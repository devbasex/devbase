"""`env init` / `sync` / `project` の対象グループと、グループごとの同期済みハッシュ
(PLAN56 受け入れ条件 13・決定 13)"""

from __future__ import annotations

import base64
import os
import types
from pathlib import Path

import pytest

from devbase.commands import env as env_cmd
from devbase.env import keys


def b64(text: str) -> str:
    return base64.b64encode(text.encode('utf-8')).decode('ascii')


@pytest.fixture
def grouped(openbao_root, openbao):
    """``version: 2`` (``default`` → ``nyle``)。``web`` は ``with``、``api`` は宣言なし"""
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout='group', group_aliases={'default': 'nyle'})
    (openbao_root / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=with\n')
    (openbao_root / 'projects' / 'api').mkdir()
    return openbao_root


@pytest.fixture
def git_credentials(monkeypatch):
    """``~/.git-credentials`` を読んで登録する収集器だけにする (対話を起こさない)"""
    home = Path(os.environ['HOME'])
    home.mkdir(parents=True, exist_ok=True)
    path = home / '.git-credentials'
    path.write_text('v1')

    def collect(env_file):
        env_file.set(keys.GIT_CREDENTIALS_BASE64, b64(path.read_text()))

    class _Registry:
        collectors = [types.SimpleNamespace(display_name='git', collect_fn=collect)]

        def discover(self):
            pass

    monkeypatch.setattr(env_cmd, 'CollectorRegistry', _Registry)
    return path


def at(monkeypatch, root, rel=''):
    monkeypatch.setenv('PWD', str(root / rel) if rel else str(root))


def kv_paths(openbao):
    """チーム単位の参照のパス (sync は個人共通も読み、欠けたホスト接続情報を個人共通へ書く。#273)"""
    return {r.kv_path for r in openbao.received if r.kv_path and r.kv_path.startswith('team/')}


def test_sync_keeps_the_synced_hashes_per_storage_group(grouped, openbao, monkeypatch,
                                                         git_credentials):
    """決定 13: nyle と with を順に同期しても、ソースの更新をそれぞれが検出する"""
    for project in ('api', 'web'):
        at(monkeypatch, grouped, f'projects/{project}')
        assert env_cmd.cmd_env_init(grouped) == 0

    assert openbao.get('team/nyle/global')[keys.GIT_CREDENTIALS_BASE64] == b64('v1')
    assert openbao.get('team/with/global')[keys.GIT_CREDENTIALS_BASE64] == b64('v1')
    assert (grouped / '.env.sources.nyle.yml').is_file()
    assert (grouped / '.env.sources.with.yml').is_file()
    assert not (grouped / '.env.sources.yml').exists()

    git_credentials.write_text('v2')
    for project, path in (('api', 'team/nyle/global'), ('web', 'team/with/global')):
        at(monkeypatch, grouped, f'projects/{project}/src')
        assert env_cmd.cmd_env_sync(grouped) == 0
        assert openbao.get(path)[keys.GIT_CREDENTIALS_BASE64] == b64('v2')

    assert kv_paths(openbao) == {'team/nyle/global', 'team/with/global'}


def test_sync_outside_projects_uses_the_root_group(grouped, openbao, monkeypatch,
                                                   git_credentials):
    (grouped / 'env').write_text('DEVBASE_ACCOUNT_GROUP=kkg\n')

    assert env_cmd.cmd_env_init(grouped) == 0
    git_credentials.write_text('v2')
    assert env_cmd.cmd_env_sync(grouped) == 0

    assert openbao.get('team/kkg/global')[keys.GIT_CREDENTIALS_BASE64] == b64('v2')
    assert (grouped / '.env.sources.kkg.yml').is_file()
    assert kv_paths(openbao) == {'team/kkg/global'}


def test_sync_uses_the_single_sources_file_with_the_flat_layout(openbao_root, openbao,
                                                                git_credentials):
    """決定 13: version 1 では今の .env.sources.yml を使う"""
    assert env_cmd.cmd_env_init(openbao_root) == 0
    git_credentials.write_text('v2')
    assert env_cmd.cmd_env_sync(openbao_root) == 0

    assert openbao.get('team/global')[keys.GIT_CREDENTIALS_BASE64] == b64('v2')
    assert (openbao_root / '.env.sources.yml').is_file()
    assert list(openbao_root.glob('.env.sources.*.yml')) == []


def test_project_writes_the_projects_group(grouped, openbao, monkeypatch):
    """受け入れ条件 13: env project はプロジェクトのグループのチームのプロジェクトの参照へ書く"""
    (grouped / 'projects' / 'web' / 'env.yml').write_text(
        'variables:\n  - name: GENERATED\n    generate: "hex:16"\n')
    at(monkeypatch, grouped, 'projects/web/src')

    assert env_cmd.cmd_env_project(grouped) == 0

    assert set(openbao.get('team/with/projects/web')) == {'GENERATED'}
    assert kv_paths(openbao) == {'team/with/projects/web'}
