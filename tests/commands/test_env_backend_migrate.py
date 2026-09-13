"""devbase env backend migrate (PLAN51 決定 7)"""

from __future__ import annotations

import logging

import pytest

from devbase.commands import env as env_cmd
from devbase.commands import env_backend
from devbase.env import backend_config as bc, bootstrap, cache
from devbase.env.secret_store import SecretRef, SecretStore
from tests.conftest import configure_infisical


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')
TEAM_GLOBAL = '/team/global'
TEAM_WEB = '/team/projects/web'


def migrate(root, to, **kw):
    return env_backend.cmd_env_backend_migrate(root, to=to, assume_yes=True, **kw)


def errors(caplog) -> str:
    return '\n'.join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


@pytest.fixture
def age_root(infisical_root, infisical):
    """age ストアに機密を持ち、Infisical の接続設定はあるが backend は age のまま"""
    store = SecretStore(infisical_root, config=bc.BackendConfig())
    store.age.save(GLOBAL, {'A': 'a-value', 'SHARED': 'from-age'})
    store.age.save(WEB, {'W': 'w-value'})
    config = bc.load(infisical_root)
    bc.save(infisical_root, bc.BackendConfig(backend='age', infisical=config.infisical))
    return infisical_root


# ---------------------------------------------------------------------------
# age → infisical
# ---------------------------------------------------------------------------

def test_dry_run_lists_key_names_only_and_writes_nothing(age_root, infisical, capsys):
    assert migrate(age_root, 'infisical', dry_run=True) == 0

    out = capsys.readouterr().out
    assert 'A' in out and 'SHARED' in out and 'W' in out
    assert 'a-value' not in out and 'from-age' not in out
    assert not any(r.secret_name for r in infisical.received)
    assert bc.load(age_root).backend == 'age'


def test_conflicting_keys_stop_before_any_write(age_root, infisical, capsys):
    infisical.put(TEAM_GLOBAL, {'SHARED': 'on-server'})

    assert migrate(age_root, 'infisical') == 2

    assert 'SHARED' in capsys.readouterr().out
    assert not any(r.secret_name for r in infisical.received)
    assert infisical.get(TEAM_GLOBAL) == {'SHARED': 'on-server'}
    assert bc.load(age_root).backend == 'age'


def test_migration_moves_secrets_and_switches_the_backend(age_root, infisical, capsys):
    infisical.put(TEAM_GLOBAL, {'B': 'pre-existing'})

    assert migrate(age_root, 'infisical') == 0

    assert infisical.get(TEAM_GLOBAL) == {'A': 'a-value', 'SHARED': 'from-age', 'B': 'pre-existing'}
    assert infisical.get(TEAM_WEB) == {'W': 'w-value'}
    assert not [r for r in infisical.requests_of('DELETE')]
    assert bc.load(age_root).backend == 'infisical'
    # 元の age ファイルは退避され、元の場所から消える
    assert not (age_root / 'secrets' / 'global.env.age').exists()
    moved = list((age_root / 'backups' / 'env-backend-migrate').rglob('*.age'))
    assert {p.name for p in moved} == {'global.env.age', 'web.env.age'}
    out = capsys.readouterr().out
    assert 'env-backend-migrate' in out and 'a-value' not in out
    # 新しい backend 越しに同じ値が取れる
    assert SecretStore(age_root).load(GLOBAL)['A'] == 'a-value'
    assert cache.cached_files(age_root)


def test_user_references_are_never_touched(age_root, infisical):
    assert migrate(age_root, 'infisical') == 0

    assert not [r for r in infisical.received if (r.secret_path or '').startswith('/users/')]


def test_readback_mismatch_rolls_back_created_keys_only(age_root, infisical, caplog):
    infisical.put(TEAM_GLOBAL, {'B': 'pre-existing'})
    infisical.readback_tamper[('common', TEAM_GLOBAL)] = {'A': 'corrupted'}

    assert migrate(age_root, 'infisical') == 1

    assert infisical.get(TEAM_GLOBAL) == {'B': 'pre-existing'}
    assert infisical.get(TEAM_WEB) == {}
    assert bc.load(age_root).backend == 'age'
    assert (age_root / 'secrets' / 'global.env.age').exists()
    assert SecretStore(age_root).load(GLOBAL)['A'] == 'a-value'
    assert '一致' in errors(caplog)
    assert 'a-value' not in caplog.text


def test_readback_checks_pre_existing_keys_too(age_root, infisical):
    infisical.put(TEAM_GLOBAL, {'B': 'pre-existing'})
    infisical.readback_tamper[('common', TEAM_GLOBAL)] = {'B': 'changed-by-someone'}

    assert migrate(age_root, 'infisical') == 1

    # B は消えず、この実行で作成した A / SHARED だけが消える
    assert set(infisical.get(TEAM_GLOBAL)) == {'B'}
    assert bc.load(age_root).backend == 'age'


def test_write_failure_leaves_the_source_backend(age_root, infisical):
    infisical.fail_writes_after = 1

    assert migrate(age_root, 'infisical') == 1

    assert bc.load(age_root).backend == 'age'
    assert SecretStore(age_root).load(GLOBAL)['A'] == 'a-value'


def test_migrate_requires_infisical_settings(tmp_path, caplog, monkeypatch):
    from devbase.env import agekeys

    (tmp_path / 'projects').mkdir()
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    agekeys.generate_key_file()

    assert migrate(tmp_path, 'infisical') == 2
    assert 'backend use infisical' in errors(caplog)


def test_unknown_destination_is_rejected(age_root, caplog):
    assert migrate(age_root, 'vaultwarden') == 2
    assert 'vaultwarden' in errors(caplog)


# ---------------------------------------------------------------------------
# infisical → age
# ---------------------------------------------------------------------------

def test_migration_back_to_age_keeps_the_server_and_drops_the_cache(infisical_root, infisical,
                                                                   capsys):
    infisical.put(TEAM_GLOBAL, {'A': 'a-value'})
    infisical.put(TEAM_WEB, {'W': 'w-value'})
    infisical.put('/users/member01/global', {'MINE': 'x'})
    SecretStore(infisical_root).load(GLOBAL)          # キャッシュを作る
    assert cache.cached_files(infisical_root)

    assert migrate(infisical_root, 'age') == 0

    assert bc.load(infisical_root).backend == 'age'
    assert infisical.requests_of('DELETE') == []
    assert infisical.get(TEAM_GLOBAL) == {'A': 'a-value'}
    assert cache.cached_files(infisical_root) == []
    assert bootstrap.exists(infisical_root)
    store = SecretStore(infisical_root)
    assert store.backend_name == 'age'
    assert store.load(GLOBAL) == {'A': 'a-value'}
    assert store.load(WEB) == {'W': 'w-value'}
    out = capsys.readouterr().out
    assert infisical.url in out and TEAM_GLOBAL in out and 'a-value' not in out
    assert not [r for r in infisical.received if (r.secret_path or '').startswith('/users/')]


def test_migration_back_to_age_rejects_conflicts(infisical_root, infisical):
    infisical.put(TEAM_GLOBAL, {'A': 'server'})
    SecretStore(infisical_root, config=bc.BackendConfig()).age.save(GLOBAL, {'A': 'local'})

    assert migrate(infisical_root, 'age') == 2

    assert bc.load(infisical_root).backend == 'infisical'
    assert SecretStore(infisical_root, config=bc.BackendConfig()).age.load(GLOBAL) == {'A': 'local'}


def test_migration_back_to_age_rolls_back_on_readback_failure(infisical_root, infisical,
                                                              monkeypatch):
    from devbase.env import secret_store

    infisical.put(TEAM_GLOBAL, {'A': 'a-value'})
    local = SecretStore(infisical_root, config=bc.BackendConfig())
    local.age.save(GLOBAL, {'B': 'keep'})
    real_load = secret_store.AgeBackend.load

    def tampered(self, ref):
        data = real_load(self, ref)
        if 'A' in data:
            data['A'] = 'corrupted'
        return data

    monkeypatch.setattr(secret_store.AgeBackend, 'load', tampered)

    assert migrate(infisical_root, 'age') == 1

    monkeypatch.setattr(secret_store.AgeBackend, 'load', real_load)
    assert bc.load(infisical_root).backend == 'infisical'
    assert local.age.load(GLOBAL) == {'B': 'keep'}
    assert not (infisical_root / 'secrets' / 'projects' / 'web.env.age').exists()


def test_env_get_returns_the_same_value_before_and_after_a_failed_migration(age_root, infisical,
                                                                            capsys):
    infisical.fail_writes_after = 0

    assert env_cmd.cmd_env_get(age_root, 'A') == 0
    assert capsys.readouterr().out.strip() == 'a-value'
    assert migrate(age_root, 'infisical') == 1
    capsys.readouterr()
    assert env_cmd.cmd_env_get(age_root, 'A') == 0
    assert capsys.readouterr().out.strip() == 'a-value'
