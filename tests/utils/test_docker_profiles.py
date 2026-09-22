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
#
# devbase が Compose を起動する経路は docker_compose とこの 4 つだけである (PLAN65 決定 1・2)。
# _compose_run (ps / logs)・_compose_lines (config --services / --profiles)・
# cmd_login (exec)・editor._query_container_name (ps --format json) は subprocess.run を
# 直接呼ぶため、env=compose_env() を自分で渡す。scale の起動と config --format json の
# 読み取りは docker_compose を通る (下の節と tests/commands/test_container_scale_order.py)
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




def test_compose_lines_pass_reserved_profile(container_run):
    container, run = container_run

    container._compose_lines(Path('x.yml'), ['config', '--services'])

    assert run.calls[0]['cmd'] == ['docker', 'compose', '-f', 'x.yml', 'config', '--services']
    assert run.calls[0]['env']['COMPOSE_PROFILES'] == docker.NO_PROFILE


@pytest.mark.parametrize('generated, expected_tail', [
    (True, ['exec', 'dev-2', 'bash']),
    (False, ['exec', '--index=2', 'dev', 'bash']),
])
def test_login_passes_reserved_profile(container_run, monkeypatch, generated, expected_tail):
    container, run = container_run
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    if generated:
        container._SCALE_COMPOSE_FILE.write_text('services: {}\n')

    container.cmd_login('2')

    assert run.calls[0]['cmd'][-len(expected_tail):] == expected_tail
    assert run.calls[0]['env'] is not None
    assert run.calls[0]['env']['COMPOSE_PROFILES'] == docker.NO_PROFILE
    import os
    assert os.environ['COMPOSE_PROFILES'] == 'test'


def test_editor_container_name_query_passes_reserved_profile(monkeypatch):
    from devbase.editor import opener
    monkeypatch.setenv('COMPOSE_PROFILES', 'test')
    run = FakeRun(stdout='')

    opener._query_container_name('dev', 1, compose_file='x.yml', runner=run)

    assert run.calls[0]['cmd'][-4:] == ['ps', '--format', 'json', 'dev-1']
    assert run.calls[0]['env']['COMPOSE_PROFILES'] == docker.NO_PROFILE


# ---------------------------------------------------------------------------
# config --format json の読み取り (PLAN65 決定 9)。docker_compose を通る唯一の関数に寄せる
# ---------------------------------------------------------------------------

def test_compose_config_readers_pass_reserved_profile(container_run):
    container, run = container_run

    container._resolve_dev_service()
    container._compose_config_services()

    assert [c['cmd'] for c in run.calls] == [['docker', 'compose', 'config', '--format', 'json']] * 2
    assert [c['env']['COMPOSE_PROFILES'] for c in run.calls] == [docker.NO_PROFILE] * 2


@pytest.mark.parametrize('returncode, stdout, expected', [
    (1, 'not json', (1, {})),
    (0, '{"services": {"dev": {"image": "x"}}}', (0, {'dev': {'image': 'x'}})),
])
def test_compose_config_services_contract(container_run, returncode, stdout, expected):
    container, run = container_run
    run.returncode, run.stdout = returncode, stdout

    assert container._compose_config_services() == expected


def test_compose_config_services_propagates_unreadable_json(container_run):
    import json
    container, run = container_run
    run.stdout = 'not json'

    with pytest.raises(json.JSONDecodeError):
        container._compose_config_services()


@pytest.mark.parametrize('returncode, stdout, expected', [
    (1, '{"services": {"dev": {"image": "x"}}}', None),
    (0, 'not json', None),
    (0, '{"services": {"dev": {"image": "x"}}}', {'image': 'x'}),
])
def test_resolve_dev_service_contract(container_run, monkeypatch, returncode, stdout, expected):
    container, run = container_run
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    run.returncode, run.stdout = returncode, stdout

    assert container._resolve_dev_service() == expected
