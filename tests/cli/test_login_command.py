"""login が生成する Compose コマンドの現状を固定する。"""

import subprocess

import pytest

from devbase.commands import container


@pytest.mark.parametrize(('scaled', 'expected'), [
    (True, ['docker', 'compose', '-f', '.docker-compose.scale.yml',
            'exec', 'dev-2', 'bash']),
    (False, ['docker', 'compose', 'exec', '--index=2', 'dev', 'bash']),
])
def test_login_command(tmp_path, monkeypatch, scaled, expected):
    monkeypatch.chdir(tmp_path)
    if scaled:
        (tmp_path / '.docker-compose.scale.yml').write_text('services: {}\n')
    events = []
    monkeypatch.setattr(container, '_apply_context',
                        lambda context: events.append(('context', context)))
    monkeypatch.setattr(container, '_inject_secrets',
                        lambda *, required: events.append(('secrets', required)))
    monkeypatch.setattr(container, 'get_dev_service_name',
                        lambda: events.append(('service',)) or 'dev')

    def run(cmd, **kwargs):
        # 子プロセスの env (compose_env) は tests/utils/test_docker_profiles.py が固定する (PLAN65)
        events.append(('run', cmd))
        return subprocess.CompletedProcess(cmd, 7)

    monkeypatch.setattr(container.subprocess, 'run', run)

    assert container.cmd_login('2', context='remote') == 7
    assert events == [
        ('context', 'remote'), ('secrets', False), ('service',), ('run', expected),
    ]


@pytest.mark.parametrize(('scaled', 'expected'), [
    (True, ['docker', 'compose', '-f', '.docker-compose.scale.yml',
            'exec', 'dev-1', 'bash']),
    (False, ['docker', 'compose', 'exec', '--index=1', 'dev', 'bash']),
])
def test_login_command_defaults(tmp_path, monkeypatch, scaled, expected):
    """現状固定: 引数省略時は context=None でインスタンス 1 に接続する。"""
    monkeypatch.chdir(tmp_path)
    if scaled:
        (tmp_path / '.docker-compose.scale.yml').write_text('services: {}\n')
    contexts = []
    commands = []
    monkeypatch.setattr(container, '_apply_context', contexts.append)
    monkeypatch.setattr(container, '_inject_secrets', lambda *, required: None)
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')

    def run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 7)

    monkeypatch.setattr(container.subprocess, 'run', run)

    assert container.cmd_login() == 7
    assert contexts == [None]
    assert commands == [expected]
