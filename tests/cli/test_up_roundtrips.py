"""`devbase up` 1 回のサーバ backend への往復を固定する (PLAN55 / #168)

仕様「OpenBao との契約」は `devbase up` 1 回あたり認証 1 回 + 参照ごとに取得 1 回を
想定している。CLI 全体では `cli._load_secret_env` (dispatch 前) / `_dispatch_lifecycle`
(切替後) / `_ensure_env_files` / `_run_deploy_pipeline` の 4 か所が `SecretStore` を
作り直していたため、認証 4 回・取得 12〜14 回になっていた。3 経路それぞれの往復を
偽サーバの記録で固定する。
"""

from __future__ import annotations

import os
import subprocess
import types
from pathlib import Path

import pytest

from devbase import cli
from devbase.commands import container
from devbase.env import runtime
from devbase.env.secret_store import SecretRef, SecretStore
from devbase.utils import docker_context

TEAM_GLOBAL = 'team/global'
USER_GLOBAL = 'users/member01/global'
TEAM_WEB = 'team/projects/web'
USER_WEB = 'users/member01/projects/web'
TEAM_API = 'team/projects/api'
USER_API = 'users/member01/projects/api'

WEB_REFS = [TEAM_GLOBAL, USER_GLOBAL, TEAM_WEB, USER_WEB]
API_REFS = [TEAM_GLOBAL, USER_GLOBAL, TEAM_API, USER_API]

LOCAL_TARGET = docker_context.DockerTarget(context=None, source='none', remote=False,
                                           home=None, gid=None)


def _write_project(root: Path, name: str) -> Path:
    project = root / 'projects' / name
    project.mkdir(parents=True, exist_ok=True)
    (project / 'project.yml').write_text(
        "version: 1\nscale: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n")
    (project / 'env').write_text(f"PROJECT_MARK={name}\n")
    return project


@pytest.fixture
def up_root(openbao_root, openbao, monkeypatch):
    """偽サーバに 2 プロジェクト分の機密を置き、docker の呼び出しを差し替えた DEVBASE_ROOT。

    `_ensure_env_files` と `_inject_secrets` は本物を通す (往復を数える対象)。
    """
    root = openbao_root
    _write_project(root, 'web')
    _write_project(root, 'api')
    openbao.put(TEAM_GLOBAL, {'SHARED': 'team'})
    openbao.put(USER_GLOBAL, {'MINE': 'me'})
    openbao.put(TEAM_WEB, {'WEB_ONLY': 'w'})
    openbao.put(TEAM_API, {'API_ONLY': 'a'})
    monkeypatch.setenv('DEVBASE_ROOT', str(root))
    for name in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DEVBASE_DOCKER_CONTEXT',
                 'COMPOSE_PROJECT_NAME', 'DEV_SERVICE_NAME', 'SHARED', 'MINE',
                 'WEB_ONLY', 'API_ONLY', 'PROJECT_MARK', 'INIT_KEY'):
        monkeypatch.delenv(name, raising=False)
    docker_context.reset()
    runtime.release_store()
    runtime.clear_injected()

    seen: dict = {}
    monkeypatch.setattr(container, '_resolve_docker_target', lambda cli_context=None: LOCAL_TARGET)
    monkeypatch.setattr(container, '_run_pre_up_hook', lambda config=None: True)
    monkeypatch.setattr(container, '_ensure_images', lambda: True)
    monkeypatch.setattr(container, '_auto_snapshot', lambda *a, **k: None)
    monkeypatch.setattr(container, 'ensure_volumes', lambda *a, **k: None)
    monkeypatch.setattr(container, 'ensure_network', lambda *a, **k: None)
    monkeypatch.setattr(container, 'docker_compose_down', lambda **k: None)
    monkeypatch.setattr(container, 'docker_compose_up', lambda **k: None)
    monkeypatch.setattr(container, 'wait_for_containers_ready', lambda **k: None)
    monkeypatch.setattr(container, '_apply_window_titles', lambda *a, **k: None)
    monkeypatch.setattr(container, '_report_missing_repos', lambda *a, **k: None)
    monkeypatch.setattr(container, '_maybe_open_editor', lambda *a, **k: None)

    def fake_generate(scale, secrets, dev_environment=None, **kw):
        seen['secrets'] = secrets
        seen['environ'] = dict(os.environ)
        compose = Path.cwd() / '.docker-compose.scale.yml'
        compose.write_text("services:\n  dev-1: {}\n")
        return compose

    monkeypatch.setattr(container, '_generate_compose_for', fake_generate)
    seen['root'] = root
    yield seen
    runtime.release_store()
    runtime.clear_injected()


def _run_up(name=None) -> int:
    """`devbase [project] up [name]` を CLI と同じ順で走らせる (dispatch 前の注入 → dispatch)"""
    cli._load_secret_env('project', 'up')
    ns = types.SimpleNamespace(subcommand='up', name=name, scale=None,
                               open_editor=False, open_index=None, context=None)
    return container.cmd_project(ns)


def _gets(openbao):
    return sorted(r.kv_path for r in openbao.requests_of('GET'))


@pytest.mark.parametrize(('subcommand', 'name'), [
    pytest.param('unknown', None, id='unknown-subcommand'),
    pytest.param('ps', 'missing-project', id='missing-project'),
    pytest.param('ps', None, id='handler-error'),
])
def test_cmd_project_error_discards_cached_secrets(
        openbao_root, openbao, monkeypatch, subcommand, name):
    """現状固定: 異常終了後の解決では操作前の機密を持ち越さない。"""
    root = openbao_root
    monkeypatch.setenv('DEVBASE_ROOT', str(root))
    openbao.put(TEAM_GLOBAL, {'TOKEN': 'old'})
    assert runtime.resolve(root).values['TOKEN'] == 'old'
    openbao.put(TEAM_GLOBAL, {'TOKEN': 'new'})

    def fail_ps(**kwargs):
        raise RuntimeError('ps failed')

    monkeypatch.setattr(container, 'cmd_ps', fail_ps)
    args = types.SimpleNamespace(subcommand=subcommand, name=name)
    if subcommand == 'ps' and name is None:
        with pytest.raises(RuntimeError, match='ps failed'):
            container.cmd_project(args)
    else:
        assert container.cmd_project(args) == 1

    # fixture の解除処理が走る前に、公開入口から更新値を読み直す。
    assert runtime.resolve(root).values['TOKEN'] == 'new'


def test_up_in_project(up_root, openbao, monkeypatch):
    """受け入れ条件 1: `web` の中で `up` → 認証 1 回、GET 4 回"""
    monkeypatch.chdir(up_root['root'] / 'projects' / 'web')
    monkeypatch.setenv('PWD', str(up_root['root'] / 'projects' / 'web'))

    assert _run_up() == 0

    assert openbao.logins == 1
    assert _gets(openbao) == sorted(WEB_REFS)
    assert up_root['secrets'].values['WEB_ONLY'] == 'w'
    assert up_root['environ']['SHARED'] == 'team'


def test_up_other_project(up_root, openbao, monkeypatch):
    """受け入れ条件 2: `api` の中で `up web` → 認証 1 回、GET 6 回以下、api 固有キーは残らない"""
    monkeypatch.chdir(up_root['root'] / 'projects' / 'api')
    monkeypatch.setenv('PWD', str(up_root['root'] / 'projects' / 'api'))

    assert _run_up('web') == 0

    assert openbao.logins == 1
    gets = openbao.requests_of('GET')
    assert len(gets) <= 6
    assert {r.kv_path for r in gets} == set(API_REFS) | set(WEB_REFS)
    assert 'API_ONLY' not in up_root['environ']
    assert 'API_ONLY' not in up_root['secrets'].values
    assert up_root['secrets'].values['WEB_ONLY'] == 'w'


def test_up_from_outside(up_root, openbao, monkeypatch):
    """受け入れ条件 3: `projects/` の外で `up web` → 認証 1 回、GET 4 回"""
    monkeypatch.chdir(up_root['root'])
    monkeypatch.setenv('PWD', str(up_root['root']))

    assert _run_up('web') == 0

    assert openbao.logins == 1
    assert _gets(openbao) == sorted(WEB_REFS)


def test_ensure_env_files_reads_seen(up_root, openbao, monkeypatch):
    """受け入れ条件 4: 注入の後の `_ensure_env_files` はサーバへ GET を出さない"""
    monkeypatch.chdir(up_root['root'] / 'projects' / 'web')
    monkeypatch.setenv('PWD', str(up_root['root'] / 'projects' / 'web'))
    cli._load_secret_env('project', 'up')
    before = len(openbao.requests_of('GET'))

    assert container._ensure_env_files() is True

    assert len(openbao.requests_of('GET')) == before
    assert openbao.logins == 1


def test_up_after_env_init_reads_written_values(up_root, openbao, monkeypatch):
    """受け入れ条件 8: `team/global` 未作成で `up` → `env init` が書いた値で起動する (決定 5)"""
    root = up_root['root']
    openbao.secrets.pop(TEAM_GLOBAL)
    openbao.versions.pop(TEAM_GLOBAL)
    monkeypatch.chdir(root / 'projects' / 'web')
    monkeypatch.setenv('PWD', str(root / 'projects' / 'web'))

    child: dict = {}

    def fake_env_init(argv, **kwargs):
        # 子プロセスの `env init` は別の SecretStore で書く。親の控えは更新されない。
        # 子の往復 (認証 1 + 書く前の GET) は親の数に入れないので、ここで分けて数える
        logins, gets = openbao.logins, len(openbao.requests_of('GET'))
        SecretStore(root).save(SecretRef.for_global(), {'INIT_KEY': 'value'})
        child['logins'] = openbao.logins - logins
        child['gets'] = len(openbao.requests_of('GET')) - gets
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(container.subprocess, 'run', fake_env_init)

    assert _run_up() == 0

    assert up_root['secrets'].values['INIT_KEY'] == 'value'
    assert up_root['environ']['INIT_KEY'] == 'value'
    # 親は、捨てて読み直した分 (認証 1 回 + 参照ごとに 1 回) だけ増える
    assert openbao.logins - child['logins'] == 2
    assert len(openbao.requests_of('GET')) - child['gets'] <= 8
