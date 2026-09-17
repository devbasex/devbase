"""`devbase env exec [--context NAME]` が子プロセスへ DOCKER_CONTEXT を載せる (PLAN52 Task 5)。"""

from __future__ import annotations

import subprocess

import pytest

from devbase.commands import env as env_cmd
from devbase.env import runtime as secret_runtime
from devbase.utils import docker_context as dc


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DEVBASE_DOCKER_CONTEXT'):
        monkeypatch.delenv(name, raising=False)
    seen = {}

    def fake_child_env(root, project, **kw):
        # 機密ストアの値が載った後の辞書 (同名キーを含む)
        return {'PATH': '/x', **seen.get('secrets', {})}

    monkeypatch.setattr(secret_runtime, 'child_env', fake_child_env)
    monkeypatch.setattr(secret_runtime, 'current_project_name', lambda root: None)
    monkeypatch.setattr(env_cmd.subprocess, 'run',
                        lambda argv, env=None, **k: seen.__setitem__('env', dict(env))
                        or subprocess.CompletedProcess(argv, 0))
    seen['root'] = tmp_path
    return seen


def test_no_config_leaves_env(harness):
    assert env_cmd.cmd_env_exec(harness['root'], ['--', 'true']) == 0
    assert 'DOCKER_CONTEXT' not in harness['env']


def test_local_yml_context_reaches_child(harness):
    (harness['root'] / 'project.local.yml').write_text("docker:\n  context: gpu-wsl\n")
    assert env_cmd.cmd_env_exec(harness['root'], ['--', 'true']) == 0
    assert harness['env']['DOCKER_CONTEXT'] == 'gpu-wsl'


def test_cli_context_beats_secret_store(harness):
    """.env (機密) に DEVBASE_DOCKER_CONTEXT=a があっても --context b が勝つ。"""
    harness['secrets'] = {'DEVBASE_DOCKER_CONTEXT': 'a', 'DOCKER_HOST': 'tcp://x'}
    assert env_cmd.cmd_env_exec(harness['root'], ['--', 'true'], context='b') == 0
    assert harness['env']['DOCKER_CONTEXT'] == 'b'
    assert 'DOCKER_HOST' not in harness['env']


def test_env_exec_does_not_keep_module_state(harness):
    (harness['root'] / 'project.local.yml').write_text("docker:\n  context: gpu-wsl\n")
    env_cmd.cmd_env_exec(harness['root'], ['--', 'true'])
    assert dc.active_target() is None


def test_local_yml_is_read_from_project_root_when_run_in_subdir(harness, monkeypatch):
    """projects/<name>/sub から実行しても、機密と同じくプロジェクト直下の設定を読む。"""
    root = harness['root']
    proj = root / 'projects' / 'A'
    (proj / 'sub').mkdir(parents=True)
    (proj / 'project.local.yml').write_text("docker:\n  context: gpu-wsl\n")
    monkeypatch.chdir(proj / 'sub')
    monkeypatch.setattr(secret_runtime, 'current_project_name', lambda r: 'A')
    assert env_cmd.cmd_env_exec(root, ['--', 'true']) == 0
    assert harness['env']['DOCKER_CONTEXT'] == 'gpu-wsl'


@pytest.mark.parametrize('argv', [['--'], []])
def test_env_exec_empty_command_returns_one(harness, argv):
    """現状固定: 区切りだけの場合も空コマンドとして扱う。"""
    assert env_cmd.cmd_env_exec(harness['root'], argv) == 1


@pytest.mark.parametrize(('error', 'expected'), [
    (FileNotFoundError, 127),
    (OSError, 1),
])
def test_env_exec_process_error_return_code(harness, monkeypatch, error, expected):
    """現状固定: 子プロセスを起動できない場合の終了コード。"""
    def fail_run(*args, **kwargs):
        raise error('cannot execute')

    monkeypatch.setattr(env_cmd.subprocess, 'run', fail_run)
    assert env_cmd.cmd_env_exec(harness['root'], ['true']) == expected
