"""devbase 経由の Compose の有効なプロファイルを devbase が決める (PLAN58 決定 7)

利用者の端末の ``COMPOSE_PROFILES`` やプロジェクトの ``.env`` に書かれた値で、
devbase が起動・停止・読み取りするサービスの集合が変わらないようにする。子プロセスの
``COMPOSE_PROFILES`` へ、どのプロジェクトも定義しない打ち消し用のプロファイル名を入れる。
キーを外すだけでは Compose が ``.env`` の値を採るため足りない。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devbase.utils import docker


class FakeRun:
    """``subprocess.run`` の代わりに呼び出しを記録する。"""

    def __init__(self, returncode: int = 0, stdout: str = ''):
        self.calls: list[dict] = []
        self.returncode = returncode
        self.stdout = stdout

    def __call__(self, cmd, **kwargs):
        self.calls.append({'cmd': list(cmd), **kwargs})
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, '')


@pytest.fixture
def fake_run(monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(docker.subprocess, 'run', run)
    return run


def test_compose_env_overrides_profiles_with_reserved_name(monkeypatch):
    monkeypatch.setenv('COMPOSE_PROFILES', 'test')
    monkeypatch.setenv('DEVBASE_SAMPLE', 'kept')

    env = docker.compose_env()

    assert env['COMPOSE_PROFILES'] == '__devbase_none__'
    assert env['DEVBASE_SAMPLE'] == 'kept'


def test_compose_env_sets_reserved_name_even_when_unset(monkeypatch):
    # キーが無いと Compose はプロジェクトの .env の COMPOSE_PROFILES を採る
    monkeypatch.delenv('COMPOSE_PROFILES', raising=False)

    assert docker.compose_env()['COMPOSE_PROFILES'] == docker.NO_PROFILE


def test_compose_env_does_not_modify_process_environment(monkeypatch):
    monkeypatch.setenv('COMPOSE_PROFILES', 'test')

    docker.compose_env()

    import os
    assert os.environ['COMPOSE_PROFILES'] == 'test'


def test_docker_compose_passes_reserved_profile_to_child(monkeypatch, fake_run):
    monkeypatch.setenv('COMPOSE_PROFILES', 'test')

    docker.docker_compose(['ps'], compose_file=Path('x.yml'))

    assert fake_run.calls[0]['env']['COMPOSE_PROFILES'] == '__devbase_none__'


def test_down_targets_every_profile(monkeypatch, fake_run):
    monkeypatch.setenv('COMPOSE_PROFILES', 'test')

    docker.docker_compose_down(compose_file=Path('x.yml'))

    call = fake_run.calls[0]
    assert call['cmd'] == ['docker', 'compose', '-f', 'x.yml',
                           '--profile', '*', 'down', '-t0']
    assert call['env']['COMPOSE_PROFILES'] == '__devbase_none__'


def test_up_without_services_keeps_the_current_form(fake_run):
    docker.docker_compose_up(compose_file=Path('x.yml'))

    assert fake_run.calls[0]['cmd'] == ['docker', 'compose', '-f', 'x.yml', 'up', '-d']


def test_up_names_the_given_services_without_profile(monkeypatch, fake_run):
    monkeypatch.setenv('COMPOSE_PROFILES', 'test')

    docker.docker_compose_up(compose_file=Path('x.yml'), services=['dev-1', 'dev-2'])

    call = fake_run.calls[0]
    assert call['cmd'] == ['docker', 'compose', '-f', 'x.yml', 'up', '-d', 'dev-1', 'dev-2']
    assert '--profile' not in call['cmd']
    assert call['env']['COMPOSE_PROFILES'] == '__devbase_none__'


# ---------------------------------------------------------------------------
# docker_compose を通らずに Compose を直接呼ぶ経路 (決定 7 の棚卸しの 4 か所)
# ---------------------------------------------------------------------------

@pytest.fixture
def container_run(monkeypatch, tmp_path):
    from devbase.commands import container
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('COMPOSE_PROFILES', 'test')
    run = FakeRun(stdout='{"services": {}}')
    monkeypatch.setattr(container.subprocess, 'run', run)
    monkeypatch.setattr(container, '_prepare_compose', lambda context: None)
    return container, run


def test_ps_and_logs_pass_reserved_profile(container_run):
    container, run = container_run

    container._compose_run('ps')
    container._compose_run('logs', '--tail', '5')

    assert [c['env']['COMPOSE_PROFILES'] for c in run.calls] == [docker.NO_PROFILE] * 2


def test_compose_config_readers_pass_reserved_profile(container_run):
    container, run = container_run

    container._resolve_dev_service()
    container._read_compose_services()

    assert [c['cmd'][2] for c in run.calls] == ['config', 'config']
    assert [c['env']['COMPOSE_PROFILES'] for c in run.calls] == [docker.NO_PROFILE] * 2


def test_editor_container_name_query_passes_reserved_profile(monkeypatch):
    from devbase.editor import opener
    monkeypatch.setenv('COMPOSE_PROFILES', 'test')
    run = FakeRun(stdout='')

    opener._query_container_name('dev', 1, compose_file='x.yml', runner=run)

    assert run.calls[0]['cmd'][-4:] == ['ps', '--format', 'json', 'dev-1']
    assert run.calls[0]['env']['COMPOSE_PROFILES'] == docker.NO_PROFILE
