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


@pytest.fixture
def up_harness(tmp_path, monkeypatch):
    """cmd_up の外部作用をすべてスタブ化し、呼び出し順を記録する。"""
    monkeypatch.chdir(tmp_path)
    # PLAN32: cmd_up は project.yml を唯一の正として読む
    (tmp_path / 'project.yml').write_text(
        "version: 1\nscale: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n")
    calls: list = []

    monkeypatch.setattr(container, 'get_project_name', lambda: 'proj')
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    monkeypatch.setattr(container, '_ensure_env_files', lambda: True)
    monkeypatch.setattr(container, '_run_pre_up_hook', lambda: True)
    monkeypatch.setattr(container, '_ensure_images', lambda: True)
    monkeypatch.setattr(container, '_auto_snapshot', lambda: None)
    monkeypatch.setattr(container, 'ensure_volumes', lambda *a, **k: None)
    monkeypatch.setattr(container, 'ensure_network', lambda *a, **k: None)
    monkeypatch.setattr(container, 'docker_compose_up', lambda **k: calls.append(('up', k)))
    monkeypatch.setattr(container, 'wait_for_containers_ready', lambda **k: None)
    monkeypatch.setattr(container, '_maybe_open_editor', lambda *a, **k: None)

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
