"""`container up` の順序: 復号と構成生成を既存コンテナの停止より前に済ませる。

codex 指摘 (PR #90) の回帰テスト。機密の復号は鍵の紛失・権限不備・暗号文の破損で
失敗しうる。停止してから復号すると、起動できないだけでなく稼働中の開発環境まで
止まったままになるため、停止より前に構成生成まで終える必要がある。

併せて、停止に渡す compose は**生成前**の構成であることも固定する。生成は
``.docker-compose.scale.yml`` を上書きするので、新構成で停止するとスケールを縮める
起動で旧インスタンスが取り残される。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbase.commands import container
from devbase.errors import DevbaseError


OLD_COMPOSE = "services:\n  dev-1: {}\n  dev-2: {}\n"
NEW_COMPOSE = "services:\n  dev-1: {}\n"


@pytest.mark.parametrize("open_index, expected", [(0, 1), (3, 3), (4, 1)])
def test_resolve_explicit_open_index_boundaries(open_index, expected):
    """現状固定: 範囲外は 1 に戻し、scale と同じ番号は保持する。"""
    assert container._resolve_open_index(open_index, scale=3) == expected


@pytest.mark.parametrize("env_val, expected", [
    (None, 1),
    ("2", 2),
    ("abc", 1),
])
def test_resolve_open_index_env_fallback(monkeypatch, env_val, expected):
    """現状固定: open_index=None のとき DEVBASE_OPEN_INDEX の未設定・整数・非整数を解決する。"""
    if env_val is None:
        monkeypatch.delenv("DEVBASE_OPEN_INDEX", raising=False)
    else:
        monkeypatch.setenv("DEVBASE_OPEN_INDEX", env_val)
    assert container._resolve_open_index(None, scale=5) == expected


@pytest.fixture
def up_harness(tmp_path, monkeypatch):
    """cmd_up の外部作用をすべてスタブ化し、呼び出し順を記録する。"""
    monkeypatch.chdir(tmp_path)
    # PLAN52: cmd_up は docker context を解決する。外の環境変数や前のテストの残りを
    # 拾わないよう、context 関連の env とモジュール状態を空にしてから始める
    from devbase.utils import docker_context as dc
    for name in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DEVBASE_DOCKER_CONTEXT'):
        monkeypatch.delenv(name, raising=False)
    dc.reset()
    # PLAN32: cmd_up は project.yml を唯一の正として読む
    (tmp_path / 'project.yml').write_text(
        "version: 1\nscale: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n")
    calls: list = []

    monkeypatch.setattr(container, 'get_project_name', lambda: 'proj')
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    monkeypatch.setattr(container, '_ensure_env_files', lambda: True)
    monkeypatch.setattr(container, '_run_pre_up_hook', lambda config=None: True)
    monkeypatch.setattr(container, '_ensure_images', lambda: True)
    monkeypatch.setattr(container, '_auto_snapshot', lambda: None)
    monkeypatch.setattr(container, 'ensure_volumes', lambda *a, **k: None)
    monkeypatch.setattr(container, 'ensure_network', lambda *a, **k: None)
    monkeypatch.setattr(container, 'docker_compose_up', lambda **k: calls.append(('up', k)))
    monkeypatch.setattr(container, 'wait_for_containers_ready', lambda **k: None)
    monkeypatch.setattr(container, '_maybe_open_editor', lambda *a, **k: None)
    # PLAN54: 実行シェルの DEVBASE_ROOT (利用者の実環境) の backend を読まない
    monkeypatch.setattr(container, '_bao_environment', lambda: {})
    monkeypatch.setattr(container, '_push_bao_token', lambda *a, **k: None)
    # PLAN56: グループの食い違いの検査も実行シェルの DEVBASE_ROOT の backend を読む
    monkeypatch.setattr(container, '_check_group_consistency', lambda project=None: True)

    def fake_down(compose_file=None):
        # 停止時点で渡された compose の中身も記録する (旧構成であること)
        text = Path(compose_file).read_text() if compose_file else None
        calls.append(('down', text))

    monkeypatch.setattr(container, 'docker_compose_down', fake_down)
    return calls


def test_generate_precedes_down_and_uses_previous_compose(up_harness, monkeypatch):
    """生成 → 停止の順で、停止には生成前の構成が渡る。"""
    calls = up_harness
    container._SCALE_COMPOSE_FILE.write_text(OLD_COMPOSE)

    def fake_generate(scale, secrets, dev_environment=None):
        calls.append(('generate', scale))
        container._SCALE_COMPOSE_FILE.write_text(NEW_COMPOSE)
        return container._SCALE_COMPOSE_FILE

    monkeypatch.setattr(container, '_inject_secrets', lambda *, required: object())
    monkeypatch.setattr(container, '_generate_compose_for', fake_generate)

    assert container.cmd_up() == 0

    assert [c[0] for c in calls] == ['generate', 'down', 'up']
    assert calls[1][1] == OLD_COMPOSE          # 停止は旧構成で行う
    assert container._SCALE_COMPOSE_FILE.read_text() == NEW_COMPOSE
    assert not Path(f'{container._SCALE_COMPOSE_FILE}.prev').exists()


def test_decrypt_failure_keeps_containers_running(up_harness, monkeypatch):
    """復号に失敗したら停止も起動もせず、旧構成を残したまま失敗する。"""
    calls = up_harness
    container._SCALE_COMPOSE_FILE.write_text(OLD_COMPOSE)

    def boom(*, required):
        assert required is True
        raise DevbaseError('鍵が見つかりません')

    monkeypatch.setattr(container, '_inject_secrets', boom)

    assert container.cmd_up() == 1

    assert calls == []                          # down が呼ばれていない
    assert container._SCALE_COMPOSE_FILE.read_text() == OLD_COMPOSE
    assert not Path(f'{container._SCALE_COMPOSE_FILE}.prev').exists()


def test_generation_failure_restores_previous_compose(up_harness, monkeypatch):
    """生成が途中で失敗しても、down / ps が参照する旧構成は壊さない。"""
    calls = up_harness
    container._SCALE_COMPOSE_FILE.write_text(OLD_COMPOSE)

    def half_written(scale, secrets, dev_environment=None):
        container._SCALE_COMPOSE_FILE.write_text('services:\n  dev-1:')
        raise DevbaseError('compose.yml が壊れています')

    monkeypatch.setattr(container, '_inject_secrets', lambda *, required: object())
    monkeypatch.setattr(container, '_generate_compose_for', half_written)

    assert container.cmd_up() == 1

    assert calls == []
    assert container._SCALE_COMPOSE_FILE.read_text() == OLD_COMPOSE
    assert not Path(f'{container._SCALE_COMPOSE_FILE}.prev').exists()


def test_first_run_without_previous_compose(up_harness, monkeypatch):
    """初回起動 (退避なし) は素の docker compose down に委ねる。"""
    calls = up_harness
    assert not container._SCALE_COMPOSE_FILE.exists()

    def fake_generate(scale, secrets, dev_environment=None):
        container._SCALE_COMPOSE_FILE.write_text(NEW_COMPOSE)
        return container._SCALE_COMPOSE_FILE

    monkeypatch.setattr(container, '_inject_secrets', lambda *, required: object())
    monkeypatch.setattr(container, '_generate_compose_for', fake_generate)

    assert container.cmd_up() == 0

    assert [c[0] for c in calls] == ['down', 'up']
    assert calls[0][1] is None


# ---------------------------------------------------------------------------
# ボリュームと機密のグループの食い違い (PLAN56 決定 7 / 受け入れ条件 16)
# ---------------------------------------------------------------------------

GROUPED_CONFIG = """\
version: 2
backend: openbao
openbao:
  url: https://openbao.example.com
  user: member01
  layout: group
  group_aliases:
    default: nyle
"""

FLAT_CONFIG = """\
version: 1
backend: openbao
openbao:
  url: https://openbao.example.com
  user: member01
"""

PROJECT_YML = "version: 1\nscale: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n"


@pytest.fixture
def mismatch(tmp_path, monkeypatch):
    """``projects/api`` (宣言なし) で ``DEVBASE_ACCOUNT_GROUP=kkg``。副作用の呼び出しを記録する。

    ``DEVBASE_ROOT`` は tmp へ向け、実行シェルの backend を読まない。
    """
    from devbase.env import runtime
    from devbase.utils import docker_context as dc

    root = tmp_path / 'root'
    project = root / 'projects' / 'api'
    project.mkdir(parents=True)
    (project / 'project.yml').write_text(PROJECT_YML)
    (root / 'secrets').mkdir()
    monkeypatch.setenv('DEVBASE_ROOT', str(root))
    monkeypatch.setenv('PWD', str(project))
    monkeypatch.chdir(project)
    monkeypatch.setenv('DEVBASE_ACCOUNT_GROUP', 'kkg')
    for name in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DEVBASE_DOCKER_CONTEXT'):
        monkeypatch.delenv(name, raising=False)
    dc.reset()
    runtime.release_store()

    calls: list = []
    monkeypatch.setattr(container, 'get_project_name', lambda: 'api')
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    monkeypatch.setattr(container, '_resolve_docker_target', lambda context=None: dc.DockerTarget(
        context=None, source='none', remote=False, home=None, gid=None))
    monkeypatch.setattr(container.subprocess, 'run',
                        lambda argv, **k: calls.append(('subprocess', list(argv))))
    monkeypatch.setattr(container, '_run_pre_up_hook',
                        lambda config=None: calls.append(('pre-up',)) or True)
    monkeypatch.setattr(container, '_ensure_images', lambda: calls.append(('images',)) or True)
    monkeypatch.setattr(container, '_auto_snapshot', lambda *a, **k: calls.append(('snapshot',)))
    monkeypatch.setattr(container, 'ensure_volumes', lambda *a, **k: calls.append(('volumes',)))
    monkeypatch.setattr(container, 'ensure_network', lambda *a, **k: calls.append(('network',)))
    monkeypatch.setattr(container, '_inject_secrets',
                        lambda *, required: calls.append(('inject',)))
    yield {'root': root, 'project': project, 'calls': calls}
    runtime.release_store()


def _config(root, text):
    (root / 'secrets' / 'backend.yml').write_text(text)


def test_up_stops_on_a_group_mismatch_before_any_side_effect(mismatch, caplog):
    """受け入れ条件 16: 両方のグループ名と出所を述べて 1。env init・フック・スナップショットを起動しない"""
    _config(mismatch['root'], GROUPED_CONFIG)

    assert container.cmd_up() == 1

    assert mismatch['calls'] == []
    assert not (mismatch['project'] / '.env').exists()
    text = caplog.text
    assert 'kkg' in text and 'default' in text
    assert 'DEVBASE_ACCOUNT_GROUP' in text
    assert 'projects/api/env にも $DEVBASE_ROOT/env にも宣言なし' in text


def test_scale_stops_on_a_group_mismatch_without_rewriting_the_scale(mismatch, caplog):
    """受け入れ条件 16: ``scale`` は ``project.yml`` の ``scale`` を書き換えない"""
    _config(mismatch['root'], GROUPED_CONFIG)

    assert container.cmd_scale(2) == 1

    assert mismatch['calls'] == []
    assert (mismatch['project'] / 'project.yml').read_text() == PROJECT_YML
    assert 'kkg' in caplog.text and 'default' in caplog.text


def test_matching_groups_pass_the_check(mismatch):
    _config(mismatch['root'], GROUPED_CONFIG)
    (mismatch['project'] / 'env').write_text('DEVBASE_ACCOUNT_GROUP=kkg\n')

    assert container._check_group_consistency() is True


def test_flat_layout_does_not_stop_the_same_up(mismatch, monkeypatch):
    """受け入れ条件 16: 今の形の ``backend.yml`` では同じ操作で止めない"""
    _config(mismatch['root'], FLAT_CONFIG)
    monkeypatch.setattr(container, '_ensure_env_files',
                        lambda: mismatch['calls'].append(('env-files',)) or False)

    assert container.cmd_up() == 1

    assert mismatch['calls'] == [('env-files',)]


def test_flat_layout_does_not_stop_the_same_scale(mismatch, monkeypatch):
    _config(mismatch['root'], FLAT_CONFIG)

    def stop_here(*args, **kwargs):
        raise DevbaseError('ここで止める')

    monkeypatch.setattr(container, '_build_scaled_override', stop_here)

    assert container.cmd_scale(2) == 1

    assert 'scale: 2' in (mismatch['project'] / 'project.yml').read_text()
    assert ('volumes',) in mismatch['calls']
