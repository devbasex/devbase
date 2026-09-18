"""`devbase open`: コンテナに触らず dev コンテナへ接続した窓を開く (PLAN59 / #197)

起動中の判定 (``running_dev_instances``)・開く処理 (``opener.open_editor``)・``cmd_up`` を
差し替え、仕様の受け入れ条件の分岐を 1 つずつ固定する。``up`` のパイプラインの部品は
呼ばれたら落ちるスタブにして、起動中の経路が触らないことを見る。
"""

from __future__ import annotations

import logging
import os

import pytest

from devbase.commands import container
from devbase.editor import opener
from devbase.utils import docker_context as dc

PROJECT_YML = "version: 1\nscale: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DEVBASE_DOCKER_CONTEXT', 'DEVBASE_ROOT',
                 'DEVBASE_OPEN_EDITOR', 'DEVBASE_OPEN_INDEX'):
        monkeypatch.delenv(name, raising=False)
    dc.reset()
    saved = dict(os.environ)
    yield
    dc.reset()
    os.environ.clear()
    os.environ.update(saved)


class Harness:
    """外部作用の差し替えと記録"""

    def __init__(self, monkeypatch, running):
        self.running = running        # list[(index, name)] / None (取得できない)
        self.opened = []              # open_editor の kwargs
        self.ups = []                 # cmd_up の kwargs
        self.ps_context = []          # running_dev_instances を呼んだ時点の DOCKER_CONTEXT
        self.action = 'launch'
        self.up_rc = 0

        def fake_running(project, dev_service_name, runner=None):
            self.ps_context.append(os.environ.get('DOCKER_CONTEXT'))
            self.ps_args = (project, dev_service_name)
            return self.running

        def fake_open(**kwargs):
            self.opened.append(kwargs)
            return self.action

        def fake_up(**kwargs):
            self.ups.append(kwargs)
            return self.up_rc

        def forbidden(name):
            def _f(*a, **k):
                raise AssertionError(f'{name} は open の起動中の経路で呼ばない')
            return _f

        monkeypatch.setattr(container, 'running_dev_instances', fake_running)
        monkeypatch.setattr(opener, 'open_editor', fake_open)
        monkeypatch.setattr(container, 'cmd_up', fake_up)
        monkeypatch.setattr(container, 'get_project_name', lambda: 'proj')
        monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
        monkeypatch.setattr(container, '_inject_secrets', lambda **k: None)
        for name in ('_run_deploy_pipeline', '_run_pre_up_checks', '_auto_snapshot',
                     'ensure_volumes', 'ensure_network', 'docker_compose_up',
                     'docker_compose_down', '_run_deploy_script_for_instances'):
            monkeypatch.setattr(container, name, forbidden(name))


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'project.yml').write_text(PROJECT_YML)
    return tmp_path


@pytest.fixture
def harness(project, monkeypatch):
    return Harness(monkeypatch, running=[(1, 'proj-dev-1')])


def _open(**kw):
    return container.cmd_open(**kw)


# --- 起動中 -------------------------------------------------------------------

def test_running_opens_the_editor_without_touching_containers(harness):
    """受け入れ条件 1"""
    assert _open() == 0
    assert len(harness.opened) == 1
    assert harness.opened[0]['index'] == 1
    assert harness.opened[0]['project_name'] == 'proj'
    assert harness.ups == []
    assert harness.ps_args == ('proj', 'dev')


@pytest.mark.parametrize('disabled', ['env', 'project_yml'])
def test_open_ignores_the_auto_open_switch(harness, project, monkeypatch, disabled):
    """受け入れ条件 2"""
    if disabled == 'env':
        monkeypatch.setenv('DEVBASE_OPEN_EDITOR', '0')
    else:
        (project / 'project.yml').write_text(PROJECT_YML + "open_editor: false\n")
    assert _open() == 0
    assert len(harness.opened) == 1


def test_index_beyond_project_yml_scale_is_accepted_when_running(harness):
    """受け入れ条件 6: 上限は project.yml ではなく動いている数から決まる"""
    harness.running = [(1, 'proj-dev-1'), (2, 'proj-dev-2')]
    assert _open(open_index=2) == 0
    assert harness.opened[0]['index'] == 2


def test_index_from_env_is_used(harness, monkeypatch):
    """受け入れ条件 8"""
    harness.running = [(1, 'proj-dev-1'), (2, 'proj-dev-2')]
    monkeypatch.setenv('DEVBASE_OPEN_INDEX', '2')
    assert _open() == 0
    assert harness.opened[0]['index'] == 2


@pytest.mark.parametrize('env_index, kwargs, expected_index', [
    pytest.param('3', {'open_index': 1}, 1, id='explicit-overrides-env'),
    pytest.param('', {}, 1, id='empty-env'),
    pytest.param('invalid', {}, 1, id='non-integer-env'),
    pytest.param(None, {'open_index': 3}, 3, id='non-contiguous-running-index'),
])
def test_current_index_resolution_with_gaps(harness, monkeypatch, env_index, kwargs,
                                          expected_index):
    """現状固定: env のフォールバックと、飛び番号の起動一覧への接続。"""
    harness.running = [(1, 'proj-dev-1'), (3, 'proj-dev-3')]
    if env_index is None:
        monkeypatch.delenv('DEVBASE_OPEN_INDEX', raising=False)
    else:
        monkeypatch.setenv('DEVBASE_OPEN_INDEX', env_index)

    assert container.cmd_open(**kwargs) == 0
    assert [(opened['project_name'], opened['index']) for opened in harness.opened] == [
        ('proj', expected_index)]
    assert harness.ups == []


def test_current_missing_index_between_running_instances_is_an_error(harness, monkeypatch):
    """現状固定: 起動中の番号の間でも、存在しない番号は起動せず失敗する。"""
    harness.running = [(1, 'proj-dev-1'), (3, 'proj-dev-3')]
    monkeypatch.delenv('DEVBASE_OPEN_INDEX', raising=False)

    assert container.cmd_open(open_index=2) == 1
    assert harness.opened == []
    assert harness.ups == []


def test_index_not_running_is_an_error(harness, caplog):
    """受け入れ条件 5"""
    with caplog.at_level(logging.ERROR):
        assert _open(open_index=2) == 1
    assert harness.opened == [] and harness.ups == []
    assert 'dev-2' in caplog.text and '1' in caplog.text


@pytest.mark.parametrize('index', [0, -1])
def test_index_below_one_is_an_error_before_docker(harness, index):
    """受け入れ条件 7"""
    assert _open(open_index=index) == 1
    assert harness.ps_context == []
    assert harness.opened == [] and harness.ups == []


def test_context_reaches_the_state_query_and_the_editor(harness):
    """受け入れ条件 10"""
    assert _open(context='remote-x') == 0
    assert harness.ps_context == ['remote-x']
    assert harness.opened[0]['docker_context'] == 'remote-x'


def test_skip_is_a_failure(harness):
    """受け入れ条件 19: 開けなかったことを終了コードで返す"""
    harness.action = 'skip'
    assert _open() == 1


def test_print_command_is_a_success(harness):
    """受け入れ条件 19: SSH でコマンドを提示したときは成功"""
    harness.action = 'print_command'
    assert _open() == 0


def test_workspace_is_opened_for_multiple_repos(harness, project):
    (project / 'project.yml').write_text(
        PROJECT_YML + "  - owner: volareinc\n    repo: other\n")
    assert _open() == 0
    assert harness.opened[0]['workspace']


# --- 停止中 / 取得できない ----------------------------------------------------

def test_stopped_delegates_to_up_with_open(harness, caplog):
    """受け入れ条件 3"""
    harness.running = []
    harness.up_rc = 7
    with caplog.at_level(logging.INFO):
        assert _open(open_index=2, context='ctx') == 7
    assert harness.opened == []
    assert harness.ups == [{'project_name': 'proj', 'open_editor': True, 'open_index': 2,
                            'context': 'ctx'}]
    assert 'up' in caplog.text


def test_stopped_opens_even_when_auto_open_is_disabled(harness, monkeypatch):
    """受け入れ条件 4"""
    harness.running = []
    monkeypatch.setenv('DEVBASE_OPEN_EDITOR', '0')
    assert _open() == 0
    assert harness.ups[0]['open_editor'] is True
    assert harness.ups[0]['open_index'] is None


def test_state_query_failure_does_not_start_up(harness):
    """受け入れ条件 20"""
    harness.running = None
    assert _open() == 1
    assert harness.ups == [] and harness.opened == []


# --- dispatch ---------------------------------------------------------------

def test_dispatch_lifecycle_routes_open(harness):
    import types

    ns = types.SimpleNamespace(subcommand='open', name=None, open_index=None, context=None)
    assert container.cmd_project(ns) == 0
    assert len(harness.opened) == 1


# --- up の自動オープンは変わらない (受け入れ条件 15) -----------------------------

def test_up_auto_open_still_falls_back_to_index_one(project, monkeypatch):
    """up の [6/6] は範囲外の index を 1 へ落とす既存の振る舞いを保つ"""
    opened = []
    monkeypatch.setattr(opener, 'open_editor', lambda **k: opened.append(k) or 'launch')
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    config = container.project_runtime.current_project_config()
    container._maybe_open_editor('proj', True, 5, 1, config)
    assert opened[0]['index'] == 1


def test_up_auto_open_respects_the_switch(project, monkeypatch):
    opened = []
    monkeypatch.setattr(opener, 'open_editor', lambda **k: opened.append(k) or 'launch')
    monkeypatch.setenv('DEVBASE_OPEN_EDITOR', '0')
    config = container.project_runtime.current_project_config()
    container._maybe_open_editor('proj', None, None, 1, config)
    assert opened == []
