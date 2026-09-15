"""lifecycle コマンドが docker context を子プロセスへ届けること (PLAN52)。

``docker`` / ``docker compose`` は ``os.environ`` を継承して起動されるため、各偽関数は
呼ばれた時点の ``os.environ`` の値を記録する。
"""

from __future__ import annotations

import os
import subprocess
import types
from pathlib import Path

import pytest

from devbase.commands import container
from devbase.env import runtime as secret_runtime
from devbase.errors import DevbaseError
from devbase.utils import docker_context as dc

PROJECT_YML = "version: 1\nscale: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n"


def _proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _snapshot():
    return {k: os.environ.get(k) for k in ('DOCKER_CONTEXT', 'DOCKER_GID', 'DOCKER_HOST')}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DEVBASE_DOCKER_CONTEXT', 'DEVBASE_ROOT'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('DOCKER_GID', '0')
    dc.reset()
    # _resolve_project_name / _load_project_env は os.environ を直接書くので、
    # テストごとに丸ごと戻す
    saved = dict(os.environ)
    yield
    dc.reset()
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'project.yml').write_text(PROJECT_YML)
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))
    return tmp_path


@pytest.fixture
def docker_calls(monkeypatch):
    """docker への呼び出し (context show / run alpine) を記録し、現在の context を返す。"""
    calls = []

    def runner(cmd, **kw):
        calls.append((list(cmd), dict(kw.get('env') or {}), _snapshot()))
        if cmd[:3] == ['docker', 'context', 'show']:
            return _proc('desktop-linux\n')
        if cmd[:2] == ['docker', 'run']:
            return _proc('999\n')
        return _proc()

    monkeypatch.setattr(dc.subprocess, 'run', runner)
    return calls


@pytest.fixture
def up_harness(project, monkeypatch):
    """cmd_up の外部作用をスタブ化し、それぞれが見た環境変数を記録する。"""
    seen = {}

    def record(name):
        def _f(*a, **k):
            seen[name] = _snapshot()
        return _f

    monkeypatch.setattr(container, 'get_project_name', lambda: 'proj')
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    monkeypatch.setattr(container, '_ensure_env_files', lambda: True)
    monkeypatch.setattr(container, '_run_pre_up_hook', lambda config=None: True)
    monkeypatch.setattr(container, '_ensure_images', lambda: True)
    seen['_real_snapshot'] = container._auto_snapshot
    monkeypatch.setattr(container, '_auto_snapshot', record('snapshot'))
    monkeypatch.setattr(container, 'ensure_volumes', record('volumes'))
    monkeypatch.setattr(container, 'ensure_network', record('network'))
    monkeypatch.setattr(container, 'docker_compose_down', record('down'))
    monkeypatch.setattr(container, 'docker_compose_up', record('up'))
    monkeypatch.setattr(container, 'wait_for_containers_ready', record('wait'))
    monkeypatch.setattr(container, '_apply_window_titles', record('titles'))
    monkeypatch.setattr(container, '_report_missing_repos', lambda *a, **k: None)
    monkeypatch.setattr(container, '_maybe_open_editor',
                        lambda *a, **k: seen.__setitem__('editor', k))
    seen['_real_inject'] = container._inject_secrets
    monkeypatch.setattr(container, '_inject_secrets', lambda *, required: secret_runtime.SecretEnv())

    def fake_generate(scale, secrets, dev_environment=None, **kw):
        seen['generate'] = {**_snapshot(), 'kwargs': kw}
        container._SCALE_COMPOSE_FILE.write_text("services:\n  dev-1: {}\n")
        return container._SCALE_COMPOSE_FILE

    monkeypatch.setattr(container, '_generate_compose_for', fake_generate)
    return seen


# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------

def test_up_without_config_does_not_touch_env_or_probe(up_harness, docker_calls):
    assert container.cmd_up() == 0
    for step in ('volumes', 'network', 'down', 'up', 'generate', 'snapshot'):
        assert up_harness[step]['DOCKER_CONTEXT'] is None
        assert up_harness[step]['DOCKER_GID'] == '0'
    assert docker_calls == []
    assert up_harness['generate']['kwargs'] == {}
    assert up_harness['editor']['docker_context_name'] is None


def test_up_with_local_yml_propagates_context_and_gid(up_harness, docker_calls, project):
    (project / 'project.local.yml').write_text("docker:\n  context: gpu-wsl\n  gid: 42\n")
    assert container.cmd_up() == 0
    for step in ('volumes', 'network', 'down', 'up', 'generate'):
        assert up_harness[step]['DOCKER_CONTEXT'] == 'gpu-wsl'
        assert up_harness[step]['DOCKER_GID'] == '42'
    # docker context show は 1 回だけ、DOCKER_CONTEXT 抜きの環境で
    shows = [c for c in docker_calls if c[0][:3] == ['docker', 'context', 'show']]
    assert len(shows) == 1 and 'DOCKER_CONTEXT' not in shows[0][1]
    assert not any(c[0][:2] == ['docker', 'run'] for c in docker_calls)
    assert up_harness['editor']['docker_context_name'] == 'gpu-wsl'


def test_up_fetches_gid_once_when_not_configured(up_harness, docker_calls, project):
    (project / 'project.local.yml').write_text("docker:\n  context: gpu-wsl\n")
    assert container.cmd_up() == 0
    assert up_harness['up']['DOCKER_GID'] == '999'
    runs = [c for c in docker_calls if c[0][:2] == ['docker', 'run']]
    assert len(runs) == 1 and runs[0][1]['DOCKER_CONTEXT'] == 'gpu-wsl'
    assert (project / '.cache' / 'docker-gid' / 'gpu-wsl').read_text().strip() == '999'

    docker_calls.clear()
    assert container.cmd_up() == 0
    assert not any(c[0][:2] == ['docker', 'run'] for c in docker_calls)


def test_up_same_as_current_context_is_local(up_harness, docker_calls, project):
    (project / 'project.local.yml').write_text(
        "docker:\n  context: desktop-linux\n  gid: 42\n  home: /home/x\n")
    assert container.cmd_up() == 0
    assert up_harness['up']['DOCKER_CONTEXT'] == 'desktop-linux'
    assert up_harness['up']['DOCKER_GID'] == '0'            # bin/devbase の値のまま
    assert up_harness['generate']['kwargs'] == {}           # home は渡さない
    assert len(docker_calls) == 1                           # context show のみ
    assert 'snapshot' in up_harness                         # 自動スナップショットは走る


def test_up_remote_skips_auto_snapshot_and_passes_home(up_harness, docker_calls, project,
                                                        monkeypatch, caplog):
    (project / 'project.local.yml').write_text(
        "docker:\n  context: gpu-wsl\n  gid: 42\n  home: /home/takemi\n")
    # 実物の _auto_snapshot に戻す。リモート扱いなら SnapshotManager に触る前に抜ける
    monkeypatch.setattr(container, '_auto_snapshot', up_harness['_real_snapshot'])
    from devbase.snapshot import manager as snapshot_manager
    monkeypatch.setattr(snapshot_manager, 'SnapshotManager',
                        lambda *a, **k: pytest.fail('リモート扱いでスナップショットに触った'))
    with caplog.at_level('WARNING'):
        assert container.cmd_up() == 0
    assert 'スナップショット' in caplog.text
    assert up_harness['generate']['kwargs'] == {'docker_home': '/home/takemi', 'remote': True}


def test_up_gid_probe_failure_stops_before_touching_containers(up_harness, docker_calls,
                                                                project, monkeypatch, caplog):
    (project / 'project.local.yml').write_text("docker:\n  context: nope\n")

    def runner(cmd, **kw):
        if cmd[:3] == ['docker', 'context', 'show']:
            return _proc('desktop-linux\n')
        return _proc('', returncode=1, stderr='context "nope" does not exist')

    monkeypatch.setattr(dc.subprocess, 'run', runner)
    with caplog.at_level('ERROR'):
        assert container.cmd_up() == 1
    assert 'does not exist' in caplog.text and 'docker.gid' in caplog.text
    assert 'down' not in up_harness and 'up' not in up_harness


def test_up_cli_context_beats_env_and_file(up_harness, docker_calls, project, monkeypatch):
    (project / 'project.local.yml').write_text("docker:\n  context: a\n  gid: 1\n")
    monkeypatch.setenv('DEVBASE_DOCKER_CONTEXT', 'b')
    assert container.cmd_up(context='c') == 0
    assert up_harness['up']['DOCKER_CONTEXT'] == 'c'
    assert up_harness['up']['DOCKER_GID'] == '999'          # ファイルの gid は使わない


def test_up_env_context_empty_falls_back_to_file(up_harness, docker_calls, project, monkeypatch):
    (project / 'project.local.yml').write_text("docker:\n  context: a\n  gid: 1\n")
    monkeypatch.setenv('DEVBASE_DOCKER_CONTEXT', '')
    assert container.cmd_up() == 0
    assert up_harness['up']['DOCKER_CONTEXT'] == 'a'


def test_up_removes_docker_host_when_context_resolved(up_harness, docker_calls, project,
                                                       monkeypatch, caplog):
    (project / 'project.local.yml').write_text("docker:\n  context: gpu-wsl\n  gid: 1\n")
    monkeypatch.setenv('DOCKER_HOST', 'tcp://127.0.0.1:1')
    with caplog.at_level('WARNING'):
        assert container.cmd_up() == 0
    assert up_harness['up']['DOCKER_HOST'] is None
    assert 'DOCKER_HOST' in caplog.text


def test_up_keeps_docker_host_without_context(up_harness, docker_calls, project, monkeypatch):
    monkeypatch.setenv('DOCKER_HOST', 'tcp://127.0.0.1:1')
    assert container.cmd_up() == 0
    assert up_harness['up']['DOCKER_HOST'] == 'tcp://127.0.0.1:1'


def test_up_reapplies_after_secret_injection(up_harness, docker_calls, project, monkeypatch):
    """機密ストアに同名キーがあっても、注入の後に確定済みの接続先へ戻る。"""
    (project / 'project.local.yml').write_text("docker:\n  context: gpu-wsl\n  gid: 42\n")

    def fake_inject(root, project_name):
        os.environ.update({'DOCKER_CONTEXT': 'x', 'DOCKER_GID': '1', 'DOCKER_HOST': 'tcp://x'})
        return secret_runtime.SecretEnv()

    monkeypatch.setattr(secret_runtime, 'inject', fake_inject)
    monkeypatch.setattr(secret_runtime, 'clear_injected', lambda: None)
    # up_harness が差し替えた _inject_secrets を実物へ戻す (再適用は実物の中にある)
    monkeypatch.setattr(container, '_inject_secrets', up_harness['_real_inject'])

    assert container.cmd_up() == 0
    assert up_harness['down']['DOCKER_CONTEXT'] == 'gpu-wsl'
    assert up_harness['up']['DOCKER_GID'] == '42'
    assert up_harness['up']['DOCKER_HOST'] is None


# ---------------------------------------------------------------------------
# scale
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("context", [None, 'gpu-wsl'])
def test_scale_propagates_remote_settings(up_harness, docker_calls, project, context):
    """現状固定: scale もリモート接続先と home を構成生成へ届ける。"""
    (project / 'project.local.yml').write_text(
        "docker:\n  context: gpu-wsl\n  home: /home/remote\n  gid: 42\n")

    assert container.cmd_scale(2, context=context) == 0

    for step in ('volumes', 'network', 'generate', 'wait'):
        assert up_harness[step]['DOCKER_CONTEXT'] == 'gpu-wsl'
        assert up_harness[step]['DOCKER_GID'] == '42'
    assert up_harness['generate']['kwargs'] == {'docker_home': '/home/remote', 'remote': True}
    compose_envs = [env for cmd, _, env in docker_calls if cmd[:2] == ['docker', 'compose']]
    assert compose_envs
    for env in compose_envs:
        assert env['DOCKER_CONTEXT'] == 'gpu-wsl'
        assert env['DOCKER_GID'] == '42'


# ---------------------------------------------------------------------------
# down / ps / logs / login / build
# ---------------------------------------------------------------------------

@pytest.fixture
def compose_seen(project, monkeypatch):
    """down / ps / logs / login が起動する docker の呼び出しと、その時点の環境を記録する。"""
    seen = []
    monkeypatch.setattr(container, '_inject_secrets', lambda *, required: secret_runtime.SecretEnv())
    monkeypatch.setattr(container, 'docker_compose_down',
                        lambda **k: seen.append((['docker', 'compose', 'down'], _snapshot())))
    monkeypatch.setattr(subprocess, 'run',
                        lambda cmd, **k: seen.append((list(cmd), _snapshot())) or _proc())
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    return seen


@pytest.mark.parametrize("call", [
    lambda: container.cmd_down(),
    lambda: container.cmd_ps(),
    lambda: container.cmd_logs(),
    lambda: container.cmd_login('1'),
])
def test_other_commands_propagate_context(compose_seen, project, call):
    (project / 'project.local.yml').write_text("docker:\n  context: gpu-wsl\n")
    call()
    assert compose_seen and all(env['DOCKER_CONTEXT'] == 'gpu-wsl' for _, env in compose_seen)
    assert not any(cmd[:3] == ['docker', 'context', 'show'] for cmd, _ in compose_seen)


def test_other_commands_without_config_leave_env(compose_seen, project):
    container.cmd_down()
    assert compose_seen and all(env['DOCKER_CONTEXT'] is None for _, env in compose_seen)


def test_run_build_passes_context_argument(project, monkeypatch):
    seen = []
    (project / 'bin').mkdir()
    (project / 'bin' / 'devbase').write_text('')
    monkeypatch.setattr(container.subprocess, 'run', lambda cmd, **k: seen.append(cmd) or _proc())
    dc.apply(dc.DockerTarget('gpu-wsl', 'cli', True, None, 42))
    assert container._run_build(no_cache=True)
    assert seen[0][2:] == ['build', '--context', 'gpu-wsl', '--no-cache']

    dc.reset()
    seen.clear()
    assert container._run_build()
    assert seen[0][2:] == ['build']


# ---------------------------------------------------------------------------
# dispatch: 操作の間で漏れない / 切替後に解決する
# ---------------------------------------------------------------------------

def test_dispatch_resets_between_operations(compose_seen, project, monkeypatch):
    """TUI: context 付きの A の操作の後に、設定の無い B の操作が A を引きずらない。"""
    (project / 'project.local.yml').write_text("docker:\n  context: gpu-wsl\n")
    assert container._dispatch_lifecycle(types.SimpleNamespace(subcommand='down')) == 0
    assert compose_seen[-1][1]['DOCKER_CONTEXT'] == 'gpu-wsl'
    assert os.environ.get('DOCKER_CONTEXT') is None       # finally で reset

    (project / 'project.local.yml').unlink()
    assert container._dispatch_lifecycle(types.SimpleNamespace(subcommand='down')) == 0
    assert compose_seen[-1][1]['DOCKER_CONTEXT'] is None
    assert os.environ.get('DOCKER_GID') == '0'


def test_dispatch_reinjects_secrets_after_project_switch(compose_seen, project, monkeypatch):
    """A の .env の DEVBASE_DOCKER_CONTEXT が残ったまま B の context を解決しない。"""
    b = project / 'projects' / 'B'
    b.mkdir(parents=True)
    (b / 'project.yml').write_text(PROJECT_YML)
    (b / 'project.local.yml').write_text("docker:\n  context: b\n")
    os.environ['DEVBASE_DOCKER_CONTEXT'] = 'a'   # A の機密として注入されていた値
    order = []

    def fake_inject(*, required):
        order.append('inject')
        os.environ.pop('DEVBASE_DOCKER_CONTEXT', None)   # clear_injected 相当
        return secret_runtime.SecretEnv()

    monkeypatch.setattr(container, '_inject_secrets', fake_inject)
    monkeypatch.setattr(container, '_resolve_project_name',
                        lambda name: order.append('switch') or os.chdir(b) or True)
    monkeypatch.setattr(container, 'docker_compose_down',
                        lambda **k: order.append(('down', _snapshot()['DOCKER_CONTEXT'])))

    assert container._dispatch_lifecycle(types.SimpleNamespace(subcommand='down', name='B')) == 0
    assert order[:2] == ['switch', 'inject']
    assert ('down', 'b') in order


def test_dispatch_passes_cli_context_only_when_given(monkeypatch):
    calls = []
    monkeypatch.setattr(container, 'cmd_down', lambda **k: calls.append(k) or 0)
    container._dispatch_lifecycle(types.SimpleNamespace(subcommand='down'))
    container._dispatch_lifecycle(types.SimpleNamespace(subcommand='down', context='x'))
    assert calls == [{}, {'context': 'x'}]


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ['up', '--context', 'x'], ['down', '--context', 'x'], ['ps', '--context', 'x'],
    ['login', '--context', 'x'], ['scale', '3', '--context', 'x'], ['rebuild', '--context', 'x'],
    ['project', 'up', '--context', 'x'], ['project', 'logs', '--context', 'x'],
    ['project', 'build', '--context', 'x'], ['container', 'down', '--context', 'x'],
    ['env', 'exec', '--context', 'x', '--', 'true'],
])
def test_parser_accepts_context(argv):
    from devbase.cli import _create_parser
    args = _create_parser().parse_args(argv)
    assert args.context == 'x'


def test_parser_rejects_empty_context():
    from devbase.cli import _create_parser
    with pytest.raises(SystemExit):
        _create_parser().parse_args(['up', '--context', ''])


def test_parser_has_no_top_level_logs():
    from devbase.cli import _create_parser
    with pytest.raises(SystemExit):
        _create_parser().parse_args(['logs'])


def test_dispatch_clears_source_secrets_before_loading_target_env(project, monkeypatch):
    """A の機密 (DEVBASE_DOCKER_CONTEXT=a) を、B の env が載せた b を消さずに落とす。"""
    b = project / 'projects' / 'B'
    b.mkdir(parents=True)
    (b / 'project.yml').write_text(PROJECT_YML)
    (b / 'env').write_text("DEVBASE_DOCKER_CONTEXT=b\n")
    # cli.main() 相当: A の機密として注入 (注入前は未設定)
    secret_runtime.clear_injected()
    monkeypatch.setattr(secret_runtime, 'resolve',
                        lambda root, project, store=None: types.SimpleNamespace(
                            values={'DEVBASE_DOCKER_CONTEXT': 'a'}, names=['DEVBASE_DOCKER_CONTEXT']))
    secret_runtime.inject(project, None)
    assert os.environ['DEVBASE_DOCKER_CONTEXT'] == 'a'

    # 切替先 B の機密は空。_inject_secrets は実物のまま (clear_injected を通る)
    monkeypatch.setattr(secret_runtime, 'resolve',
                        lambda root, project, store=None: types.SimpleNamespace(values={}, names=[]))
    seen = []
    monkeypatch.setattr(container, 'docker_compose_down',
                        lambda **k: seen.append(_snapshot()))
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    assert container._dispatch_lifecycle(types.SimpleNamespace(subcommand='down', name='B')) == 0
    assert seen[-1]['DOCKER_CONTEXT'] == 'b'
    secret_runtime.clear_injected()


@pytest.mark.parametrize(('raw', 'expected'), [
    ('', None),
    ('# c', None),
    ('  # c', None),
    ('export FOO=bar', ('FOO', 'bar')),
    ('FOO', None),
    ('=x', None),
    ('  A = b ', ('A', ' b')),
    ('export  =y', None),
])
def test_parse_env_assignment_current_branches(raw, expected):
    """現状固定: 行全体の strip 後も、値の先頭の空白は残る。"""
    assert container._parse_env_assignment(raw) == expected
