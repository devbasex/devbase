"""devbase env backend migrate (PLAN51 決定 7)"""

from __future__ import annotations

import logging

import pytest

from devbase.commands import env as env_cmd
from devbase.commands import env_backend
from devbase.env import backend_config as bc, bootstrap, cache
from devbase.env.secret_store import SecretRef, SecretStore


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')
TEAM_GLOBAL = 'team/global'
TEAM_WEB = 'team/projects/web'


def migrate(root, to, **kw):
    return env_backend.cmd_env_backend_migrate(root, to=to, assume_yes=True, **kw)


def errors(caplog) -> str:
    return '\n'.join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


@pytest.fixture
def age_root(openbao_root, openbao):
    """age ストアに機密を持ち、OpenBao の接続設定はあるが backend は age のまま"""
    store = SecretStore(openbao_root, config=bc.BackendConfig())
    store.age.save(GLOBAL, {'A': 'a-value', 'SHARED': 'from-age'})
    store.age.save(WEB, {'W': 'w-value'})
    config = bc.load(openbao_root)
    bc.save(openbao_root, bc.BackendConfig(backend='age', openbao=config.openbao))
    return openbao_root


# ---------------------------------------------------------------------------
# age → openbao
# ---------------------------------------------------------------------------

def test_dry_run_lists_key_names_only_and_writes_nothing(age_root, openbao, capsys):
    assert migrate(age_root, 'openbao', dry_run=True) == 0

    out = capsys.readouterr().out
    assert 'A' in out and 'SHARED' in out and 'W' in out
    assert 'a-value' not in out and 'from-age' not in out
    assert not any(r.kv_path for r in openbao.requests_of('POST'))
    assert bc.load(age_root).backend == 'age'


def test_conflicting_keys_stop_before_any_write(age_root, openbao, capsys):
    openbao.put(TEAM_GLOBAL, {'SHARED': 'on-server'})

    assert migrate(age_root, 'openbao') == 2

    assert 'SHARED' in capsys.readouterr().out
    assert not any(r.kv_path for r in openbao.requests_of('POST'))
    assert openbao.get(TEAM_GLOBAL) == {'SHARED': 'on-server'}
    assert bc.load(age_root).backend == 'age'


def test_migration_moves_secrets_and_switches_the_backend(age_root, openbao, capsys):
    openbao.put(TEAM_GLOBAL, {'B': 'pre-existing'})

    assert migrate(age_root, 'openbao') == 0

    assert openbao.get(TEAM_GLOBAL) == {'A': 'a-value', 'SHARED': 'from-age', 'B': 'pre-existing'}
    assert openbao.get(TEAM_WEB) == {'W': 'w-value'}
    assert not [r for r in openbao.requests_of('DELETE')]
    assert bc.load(age_root).backend == 'openbao'
    # 元の age ファイルは退避され、元の場所から消える
    assert not (age_root / 'secrets' / 'global.env.age').exists()
    moved = list((age_root / 'backups' / 'env-backend-migrate').rglob('*.age'))
    assert {p.name for p in moved} == {'global.env.age', 'web.env.age'}
    out = capsys.readouterr().out
    assert 'env-backend-migrate' in out and 'a-value' not in out
    # 新しい backend 越しに同じ値が取れる
    assert SecretStore(age_root).load(GLOBAL)['A'] == 'a-value'
    assert cache.cached_files(age_root)


def test_user_references_are_never_touched(age_root, openbao):
    assert migrate(age_root, 'openbao') == 0

    assert not [r for r in openbao.received if (r.kv_path or '').startswith('users/')]


def test_readback_mismatch_rolls_back_created_keys_only(age_root, openbao, caplog):
    """作成したキーのうち値が保存したままのものだけを消す。

    偽サーバの ``readback_tamper`` は「A を他の利用者が書き換えた」を再現する。
    A は作成したキーだが値が変わっているので残し、SHARED だけを消す。
    """
    openbao.put(TEAM_GLOBAL, {'B': 'pre-existing'})
    openbao.readback_tamper[TEAM_GLOBAL] = {'A': 'corrupted'}

    assert migrate(age_root, 'openbao') == 1

    assert openbao.get(TEAM_GLOBAL) == {'A': 'corrupted', 'B': 'pre-existing'}
    assert openbao.get(TEAM_WEB) == {}
    assert bc.load(age_root).backend == 'age'
    assert (age_root / 'secrets' / 'global.env.age').exists()
    assert SecretStore(age_root).load(GLOBAL)['A'] == 'a-value'
    assert '一致' in errors(caplog)
    assert 'a-value' not in caplog.text


def test_readback_checks_pre_existing_keys_too(age_root, openbao):
    openbao.put(TEAM_GLOBAL, {'B': 'pre-existing'})
    openbao.readback_tamper[TEAM_GLOBAL] = {'B': 'changed-by-someone'}

    assert migrate(age_root, 'openbao') == 1

    # B は消えず、この実行で作成した A / SHARED だけが消える
    assert set(openbao.get(TEAM_GLOBAL)) == {'B'}
    assert bc.load(age_root).backend == 'age'


def test_write_failure_leaves_the_source_backend(age_root, openbao):
    openbao.fail_write_attempts = [2]

    assert migrate(age_root, 'openbao') == 1

    assert bc.load(age_root).backend == 'age'
    assert SecretStore(age_root).load(GLOBAL)['A'] == 'a-value'


def test_missing_write_permission_stops_with_the_permission_message(age_root, openbao, caplog):
    openbao.team_writable = False

    assert migrate(age_root, 'openbao') == 1

    assert '書き込み権限' in errors(caplog)
    assert 'rollback' not in errors(caplog)      # 何も書けていないので巻き戻しも無い
    assert bc.load(age_root).backend == 'age'
    assert openbao.get(TEAM_GLOBAL) == {}
    assert SecretStore(age_root).load(GLOBAL)['A'] == 'a-value'


def test_migrate_requires_openbao_settings(tmp_path, caplog, monkeypatch):
    from devbase.env import agekeys

    (tmp_path / 'projects').mkdir()
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    agekeys.generate_key_file()

    assert migrate(tmp_path, 'openbao') == 2
    assert 'backend use openbao' in errors(caplog)


def test_unknown_destination_is_rejected(age_root, caplog):
    assert migrate(age_root, 'vaultwarden') == 2
    assert 'vaultwarden' in errors(caplog)


# ---------------------------------------------------------------------------
# openbao → age
# ---------------------------------------------------------------------------

def test_migration_back_to_age_keeps_the_server_and_drops_the_cache(openbao_root, openbao,
                                                                   capsys):
    openbao.put(TEAM_GLOBAL, {'A': 'a-value'})
    openbao.put(TEAM_WEB, {'W': 'w-value'})
    openbao.put('users/member01/global', {'MINE': 'x'})
    SecretStore(openbao_root).load(GLOBAL)          # キャッシュを作る
    assert cache.cached_files(openbao_root)

    assert migrate(openbao_root, 'age') == 0

    assert bc.load(openbao_root).backend == 'age'
    assert openbao.requests_of('DELETE') == []
    assert openbao.get(TEAM_GLOBAL) == {'A': 'a-value'}
    assert cache.cached_files(openbao_root) == []
    assert bootstrap.exists(openbao_root)
    store = SecretStore(openbao_root)
    assert store.backend_name == 'age'
    assert store.load(GLOBAL) == {'A': 'a-value'}
    assert store.load(WEB) == {'W': 'w-value'}
    out = capsys.readouterr().out
    assert openbao.url in out and TEAM_GLOBAL in out and 'a-value' not in out
    assert not [r for r in openbao.received if (r.kv_path or '').startswith('users/')]


def test_migration_back_to_age_rejects_conflicts(openbao_root, openbao):
    openbao.put(TEAM_GLOBAL, {'A': 'server'})
    SecretStore(openbao_root, config=bc.BackendConfig()).age.save(GLOBAL, {'A': 'local'})

    assert migrate(openbao_root, 'age') == 2

    assert bc.load(openbao_root).backend == 'openbao'
    assert SecretStore(openbao_root, config=bc.BackendConfig()).age.load(GLOBAL) == {'A': 'local'}


def test_migration_back_to_age_rolls_back_on_readback_failure(openbao_root, openbao,
                                                              monkeypatch):
    from devbase.env import secret_store

    openbao.put(TEAM_GLOBAL, {'A': 'a-value'})
    local = SecretStore(openbao_root, config=bc.BackendConfig())
    local.age.save(GLOBAL, {'B': 'keep'})
    real_load = secret_store.AgeBackend.load

    def tampered(self, ref):
        data = real_load(self, ref)
        if 'A' in data:
            data['A'] = 'corrupted'
        return data

    monkeypatch.setattr(secret_store.AgeBackend, 'load', tampered)

    assert migrate(openbao_root, 'age') == 1

    monkeypatch.setattr(secret_store.AgeBackend, 'load', real_load)
    assert bc.load(openbao_root).backend == 'openbao'
    assert local.age.load(GLOBAL) == {'B': 'keep'}
    assert not (openbao_root / 'secrets' / 'projects' / 'web.env.age').exists()


def test_env_get_returns_the_same_value_before_and_after_a_failed_migration(age_root, openbao,
                                                                            capsys):
    openbao.fail_write_attempts = [1]

    assert env_cmd.cmd_env_get(age_root, 'A') == 0
    assert capsys.readouterr().out.strip() == 'a-value'
    assert migrate(age_root, 'openbao') == 1
    capsys.readouterr()
    assert env_cmd.cmd_env_get(age_root, 'A') == 0
    assert capsys.readouterr().out.strip() == 'a-value'


def test_declining_confirmation_keeps_destination_and_config(age_root, openbao,
                                                             monkeypatch, capsys):
    """確認で no を入力した場合の中止と保存内容の不変を固定する。"""
    openbao.put(TEAM_GLOBAL, {'B': 'pre-existing'})
    openbao.put(TEAM_WEB, {'EXISTING': 'keep'})
    config_path = age_root / 'secrets' / 'backend.yml'
    config_before = config_path.read_bytes()
    prompts = []

    def decline(prompt):
        prompts.append(prompt)
        return 'no'

    monkeypatch.setattr('devbase.env.store.safe_input', decline)

    assert env_backend.cmd_env_backend_migrate(
        age_root, to='openbao', assume_yes=False,
    ) == 1

    assert prompts == ['続行しますか? (yes と入力): ']
    assert '中止しました' in capsys.readouterr().out
    assert openbao.get(TEAM_GLOBAL) == {'B': 'pre-existing'}
    assert openbao.get(TEAM_WEB) == {'EXISTING': 'keep'}
    assert not any(r.kv_path for r in openbao.requests_of('POST'))
    assert config_path.read_bytes() == config_before
    assert SecretStore(age_root).load(GLOBAL) == {'A': 'a-value', 'SHARED': 'from-age'}
    assert SecretStore(age_root).load(WEB) == {'W': 'w-value'}


def test_conflict_check_does_not_fall_back_to_the_cache(age_root, openbao):
    """衝突の検査はサーバの現物で行い、取得できなければ書き込み前に止まる"""
    # キャッシュには B だけを残し、その後サーバへ A (移行元と同名) が足される
    openbao.put(TEAM_GLOBAL, {'B': 'pre-existing'})
    SecretStore(age_root, config=bc.BackendConfig(
        backend='openbao', openbao=bc.load(age_root).openbao)).load(GLOBAL)
    openbao.put(TEAM_GLOBAL, {'A': 'server', 'B': 'pre-existing'})
    openbao.fail_get_attempts = [openbao.get_attempts + 1]   # 移行準備の取得だけを落とす

    assert migrate(age_root, 'openbao') == 1

    assert not any(r.kv_path for r in openbao.requests_of('POST'))
    assert openbao.get(TEAM_GLOBAL) == {'A': 'server', 'B': 'pre-existing'}
    assert bc.load(age_root).backend == 'age'


def test_config_save_failure_keeps_the_source_files_in_place(age_root, openbao, monkeypatch):
    real_save = bc.save

    def failing_save(root, config):
        if config.backend == 'openbao':
            raise bc.BackendConfigError('disk full')
        return real_save(root, config)

    monkeypatch.setattr(bc, 'save', failing_save)

    assert migrate(age_root, 'openbao') == 1

    assert (age_root / 'secrets' / 'global.env.age').exists()
    assert (age_root / 'secrets' / 'projects' / 'web.env.age').exists()
    assert not (age_root / 'backups' / 'env-backend-migrate').exists()
    assert bc.load(age_root).backend == 'age'
    assert SecretStore(age_root).load(GLOBAL)['A'] == 'a-value'


# ---------------------------------------------------------------------------
# グループ別の置き場と --exclude-project (PLAN56 受け入れ条件 12・14)
# ---------------------------------------------------------------------------

CSC = SecretRef.for_project('csc')
API = SecretRef.for_project('api')


def kv_paths(openbao):
    return {r.kv_path for r in openbao.received if r.kv_path}


def _grouped_settings(root, openbao):
    from tests.conftest import configure_openbao

    return configure_openbao(root, openbao, layout='group',
                             group_aliases={'default': 'nyle'}).openbao


@pytest.fixture
def grouped_age_root(openbao_root, openbao):
    """age に共通・web (with)・csc の機密を持ち、version 2 の OpenBao 設定で backend は age"""
    (openbao_root / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=with\n')
    (openbao_root / 'projects' / 'csc').mkdir()
    store = SecretStore(openbao_root, config=bc.BackendConfig())
    store.age.save(GLOBAL, {'A': 'a-value'})
    store.age.save(WEB, {'W': 'w-value'})
    store.age.save(CSC, {'C': 'c-value'})
    settings = _grouped_settings(openbao_root, openbao)
    bc.save(openbao_root, bc.BackendConfig(backend='age', openbao=settings, version=2))
    return openbao_root


def test_grouped_migration_writes_each_projects_group_and_skips_excluded(
        grouped_age_root, openbao, capsys, monkeypatch):
    """受け入れ条件 12: 共通は team/nyle/global、web は team/with/projects/web、csc は触らない"""
    # 実行時のディレクトリ (web) にグループが左右されない
    monkeypatch.setenv('PWD', str(grouped_age_root / 'projects' / 'web'))

    assert migrate(grouped_age_root, 'openbao', exclude_projects=['csc']) == 0

    assert openbao.get('team/nyle/global') == {'A': 'a-value'}
    assert openbao.get('team/with/projects/web') == {'W': 'w-value'}
    assert not [p for p in kv_paths(openbao) if 'csc' in p]
    assert kv_paths(openbao) <= {'team/nyle/global', 'team/with/projects/web'}
    config = bc.load(grouped_age_root)
    assert config.backend == 'openbao' and config.version == 2
    # csc は退避されずに元の位置に残る
    assert (grouped_age_root / 'secrets' / 'projects' / 'csc.env.age').exists()
    assert not (grouped_age_root / 'secrets' / 'projects' / 'web.env.age').exists()
    moved = list((grouped_age_root / 'backups' / 'env-backend-migrate').rglob('*.age'))
    assert {p.name for p in moved} == {'global.env.age', 'web.env.age'}
    out = capsys.readouterr().out
    assert 'a-value' not in out and 'w-value' not in out and 'c-value' not in out


def test_grouped_dry_run_shows_the_paths_and_conflicting_key_names_only(
        grouped_age_root, openbao, capsys):
    """受け入れ条件 12・14: --dry-run は <mount>/<パス> と衝突したキー名だけ"""
    openbao.put('team/with/projects/web', {'W': 'on-server-value'})

    rc = migrate(grouped_age_root, 'openbao', dry_run=True, exclude_projects=['csc'])

    assert rc == 2                               # 衝突は --dry-run でも今どおり 2
    out = capsys.readouterr().out
    assert 'devbase/team/nyle/global' in out
    assert 'devbase/team/with/projects/web' in out
    assert 'W' in out and 'A' in out
    assert 'csc' not in out
    for value in ('a-value', 'w-value', 'c-value', 'on-server-value', 's3cret'):
        assert value not in out
    assert not any(r.kv_path for r in openbao.requests_of('POST'))
    assert bc.load(grouped_age_root).backend == 'age'


def test_grouped_dry_run_without_conflicts_writes_nothing(grouped_age_root, openbao, capsys):
    assert migrate(grouped_age_root, 'openbao', dry_run=True) == 0

    out = capsys.readouterr().out
    assert 'devbase/team/nyle/projects/csc' in out
    assert not any(r.kv_path for r in openbao.requests_of('POST'))


@pytest.mark.parametrize('to', ['openbao', 'age'])
def test_excluding_an_unknown_project_is_a_usage_error(grouped_age_root, openbao, caplog, to):
    before = (grouped_age_root / 'secrets' / 'backend.yml').read_bytes()

    assert migrate(grouped_age_root, to, exclude_projects=['csc', 'nosuch']) == 2

    assert 'nosuch' in errors(caplog)
    assert openbao.received == []
    assert (grouped_age_root / 'secrets' / 'backend.yml').read_bytes() == before


def test_flat_migration_also_honours_exclude_project(age_root, openbao):
    (age_root / 'projects' / 'csc').mkdir()
    SecretStore(age_root, config=bc.BackendConfig()).age.save(CSC, {'C': 'c-value'})

    assert migrate(age_root, 'openbao', exclude_projects=['csc']) == 0

    assert openbao.get(TEAM_WEB) == {'W': 'w-value'}
    assert openbao.requests_to('team/projects/csc') == []
    assert (age_root / 'secrets' / 'projects' / 'csc.env.age').exists()


@pytest.fixture
def grouped_openbao_root(openbao_root, openbao):
    """version 2 の OpenBao。web は with、api は宣言なし (default → nyle)"""
    _grouped_settings(openbao_root, openbao)
    (openbao_root / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=with\n')
    (openbao_root / 'projects' / 'api').mkdir()
    openbao.put('team/nyle/global', {'A': 'a-value'})
    openbao.put('team/with/global', {'WITH_ONLY': 'with-value'})
    openbao.put('team/with/projects/web', {'W': 'w-value'})
    openbao.put('team/nyle/projects/api', {'P': 'p-value'})
    return openbao_root


def test_grouped_migration_back_to_age_leaves_other_groups_common_on_the_server(
        grouped_openbao_root, openbao, capsys, monkeypatch):
    """受け入れ条件 12 (逆向き): root のグループの共通と各プロジェクトのグループの参照を移し、
    他のグループの共通は要求せずにパスを表示する"""
    monkeypatch.setenv('PWD', str(grouped_openbao_root / 'projects' / 'web'))

    assert migrate(grouped_openbao_root, 'age') == 0

    local = SecretStore(grouped_openbao_root, config=bc.BackendConfig())
    assert local.age.load(GLOBAL) == {'A': 'a-value'}
    assert local.age.load(WEB) == {'W': 'w-value'}
    assert local.age.load(API) == {'P': 'p-value'}
    assert openbao.requests_to('team/with/global') == []
    assert not [p for p in kv_paths(openbao) if p.startswith('users/')]
    config = bc.load(grouped_openbao_root)
    assert config.backend == 'age' and config.version == 2
    out = capsys.readouterr().out
    assert 'devbase/team/with/global' in out
    assert 'with' in out
    for value in ('a-value', 'with-value', 'w-value', 'p-value', 's3cret'):
        assert value not in out


def test_grouped_migration_back_to_age_dry_run_names_the_other_groups(
        grouped_openbao_root, openbao, capsys):
    assert migrate(grouped_openbao_root, 'age', dry_run=True) == 0

    out = capsys.readouterr().out
    assert 'devbase/team/with/global' in out
    assert 'devbase/team/nyle/global' in out and 'devbase/team/with/projects/web' in out
    assert openbao.requests_to('team/with/global') == []
    assert not (grouped_openbao_root / 'secrets' / 'global.env.age').exists()


def test_grouped_migration_back_to_age_excluding_the_other_group(grouped_openbao_root, openbao,
                                                                 capsys):
    assert migrate(grouped_openbao_root, 'age', exclude_projects=['web']) == 0

    assert not [p for p in kv_paths(openbao) if p.startswith('team/with/')]
    local = SecretStore(grouped_openbao_root, config=bc.BackendConfig())
    assert not local.age.exists(WEB)
    assert local.age.load(API) == {'P': 'p-value'}
    assert 'devbase/team/with/global' not in capsys.readouterr().out


def test_migrate_parser_accepts_repeated_exclude_project():
    import argparse

    from devbase import cli

    parser = argparse.ArgumentParser()
    cli._add_env_parser(parser.add_subparsers(dest='command'))
    ns = parser.parse_args(['env', 'backend', 'migrate', '--to', 'openbao'])
    assert ns.exclude_projects == []
    ns = parser.parse_args(['env', 'backend', 'migrate', '--to', 'openbao',
                            '--exclude-project', 'csc', '--exclude-project', 'web'])
    assert ns.exclude_projects == ['csc', 'web']


def test_backend_dispatch_passes_exclude_projects(monkeypatch, tmp_path):
    from types import SimpleNamespace

    seen = {}

    def fake(root, **kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr(env_backend, 'cmd_env_backend_migrate', fake)
    args = SimpleNamespace(backend_action='migrate', to='openbao', dry_run=False,
                           assume_yes=True, exclude_projects=['csc'])

    assert env_backend.cmd_env_backend(tmp_path, args) == 0
    assert seen['exclude_projects'] == ['csc']
