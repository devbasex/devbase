"""`devbase up` が起動中のコンテナの `bao` へ接続先と token を渡す (PLAN54 / #169)

- backend が ``openbao`` のとき、dev サービスの ``environment`` に ``BAO_ADDR`` を足し、
  [5/6] の後に各 dev コンテナの ``~/.vault-token`` へ token を書く
- それ以外の backend では何も足さず、``docker exec`` を呼ばない (生成する構成は変わらない)
- token を書けなくても ``up`` は倒さない
"""

from __future__ import annotations

import os
import types
from pathlib import Path

import pytest

from devbase import cli
from devbase.commands import container
from devbase.env import container_token, runtime
from devbase.utils import docker_context

LOCAL = docker_context.DockerTarget(context=None, source='none', remote=False, home=None, gid=None)


def _project(root: Path, name: str = 'web') -> Path:
    project = root / 'projects' / name
    project.mkdir(parents=True, exist_ok=True)
    (project / 'project.yml').write_text(
        "version: 1\nscale: 2\nrepos:\n  - owner: volareinc\n    repo: carmo\n")
    (project / 'env').write_text("")
    return project


def _harness(monkeypatch, root: Path, seen: dict):
    monkeypatch.setenv('DEVBASE_ROOT', str(root))
    for name in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DEVBASE_DOCKER_CONTEXT',
                 'COMPOSE_PROJECT_NAME', 'DEV_SERVICE_NAME', 'BAO_ADDR'):
        monkeypatch.delenv(name, raising=False)
    docker_context.reset()
    runtime.clear_injected()
    monkeypatch.setattr(container, '_resolve_docker_target', lambda cli_context=None: LOCAL)
    for name in ('_run_pre_up_hook', '_ensure_images', '_ensure_env_files'):
        monkeypatch.setattr(container, name, lambda *a, **k: True)
    for name in ('_auto_snapshot', 'ensure_volumes', 'ensure_network', 'docker_compose_down',
                 'docker_compose_up', 'wait_for_containers_ready', '_apply_window_titles',
                 '_report_missing_repos', '_maybe_open_editor'):
        monkeypatch.setattr(container, name, lambda *a, **k: None)
    monkeypatch.setattr(container, 'default_services', lambda *a, **k: ['dev-1'])
    # compose ps を叩かず、決定的な名前へ落とす
    monkeypatch.setattr('devbase.editor.opener._query_container_name', lambda *a, **k: None)

    def fake_generate(scale, secrets, dev_environment=None, **kw):
        seen['dev_environment'] = dict(dev_environment or {})
        compose = Path.cwd() / '.docker-compose.scale.yml'
        compose.write_text("services:\n  dev-1: {}\n")
        return compose

    monkeypatch.setattr(container, '_generate_compose_for', fake_generate)

    def fake_push(names, token, runner=None):
        seen.setdefault('pushes', []).append((list(names), token))
        return list(names)

    monkeypatch.setattr(container_token, 'push', fake_push)


def _up(project_dir: Path, monkeypatch) -> int:
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv('PWD', str(project_dir))
    cli._load_secret_env('project', 'up')
    ns = types.SimpleNamespace(subcommand='up', name=None, scale=None,
                               open_editor=False, open_index=None, context=None)
    return container.cmd_project(ns)


def test_openbao_up_adds_bao_addr_and_pushes_the_token(openbao_root, openbao, monkeypatch):
    seen: dict = {}
    web = _project(openbao_root)
    openbao.put('team/global', {'A': '1'})
    _harness(monkeypatch, openbao_root, seen)

    assert _up(web, monkeypatch) == 0

    assert seen['dev_environment']['BAO_ADDR'] == openbao.url
    (names, token), = seen['pushes']
    assert names == ['web-dev-1', 'web-dev-2']
    assert token == openbao.token
    # 注入と同じ SecretStore の token なので、ログインは 1 回のまま
    assert openbao.logins == 1


def test_bao_addr_is_the_only_bao_value_in_the_environment(openbao_root, openbao, monkeypatch):
    """token は compose の environment に載せない (docker inspect に残る)"""
    seen: dict = {}
    web = _project(openbao_root)
    _harness(monkeypatch, openbao_root, seen)

    assert _up(web, monkeypatch) == 0

    assert [k for k in seen['dev_environment'] if 'BAO' in k or 'TOKEN' in k] == ['BAO_ADDR']
    assert openbao.token not in seen['dev_environment'].values()


def test_age_up_adds_nothing_and_does_not_exec(tmp_path, monkeypatch):
    from devbase.env import agekeys

    seen: dict = {}
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    agekeys.generate_key_file()
    web = _project(tmp_path)
    _harness(monkeypatch, tmp_path, seen)

    assert _up(web, monkeypatch) == 0

    assert 'BAO_ADDR' not in seen['dev_environment']
    assert 'pushes' not in seen


def test_token_failure_does_not_fail_up(openbao_root, openbao, monkeypatch):
    seen: dict = {}
    web = _project(openbao_root)
    _harness(monkeypatch, openbao_root, seen)

    def broken_push(names, token, runner=None):
        raise RuntimeError('docker is gone')

    monkeypatch.setattr(container_token, 'push', broken_push)

    assert _up(web, monkeypatch) == 0


def test_push_bao_token_starts_from_the_added_instance(openbao_root, openbao, monkeypatch):
    """scale で増やしたインスタンスだけへ書く (既存のものは up で書いてある)"""
    seen: dict = {}
    _project(openbao_root)
    _harness(monkeypatch, openbao_root, seen)
    monkeypatch.chdir(openbao_root / 'projects' / 'web')

    container._push_bao_token('web', 3, 'dev', start=3)

    (names, _token), = seen['pushes']
    assert names == ['web-dev-3']
