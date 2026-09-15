"""devbase env backend: status / use (PLAN51)"""

from __future__ import annotations

import io
import logging
from types import SimpleNamespace

import pytest

from devbase.commands import env_backend
from devbase.env import agekeys, backend_config as bc, bootstrap
from devbase.env.secret_store import SecretRef, SecretStore


GLOBAL = SecretRef.for_global()


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def with_key(root):
    _, public = agekeys.generate_key_file()
    return public


def use_args(name, **kw):
    base = dict(name=name, url=None, mount=None, user=None, role_id=None,
                secret_id_stdin=False, cache=None, layout=None, group_aliases=None)
    base.update(kw)
    return SimpleNamespace(**base)


def errors(caplog) -> str:
    return '\n'.join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


def use_openbao(root, monkeypatch, **kw):
    """secret_id は stdin から渡す (argv には載せない)"""
    monkeypatch.setattr('sys.stdin', io.StringIO('s3cret\n'))
    args = use_args('openbao', url='https://openbao.example.com',
                    user='member01', role_id='rid', secret_id_stdin=True)
    for key, value in kw.items():
        setattr(args, key, value)
    return env_backend.cmd_env_backend_use(root, args)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_status_without_config_reports_plaintext_and_path(root, capsys):
    assert env_backend.cmd_env_backend_status(root) == 0

    out = capsys.readouterr().out
    assert 'plaintext' in out
    assert str(root / '.env') in out


def test_status_without_config_reports_age_when_encrypted(root, with_key, capsys):
    SecretStore(root).age.save(GLOBAL, {'A': '1'})

    assert env_backend.cmd_env_backend_status(root) == 0

    out = capsys.readouterr().out
    assert 'age' in out
    assert str(root / 'secrets' / 'global.env.age') in out


def test_status_reports_a_broken_config(root, caplog):
    path = root / 'secrets' / 'backend.yml'
    path.parent.mkdir()
    path.write_text('backend: vaultwarden\n')

    assert env_backend.cmd_env_backend_status(root) == 1
    assert 'vaultwarden' in errors(caplog)


def test_status_shows_openbao_paths_and_user(root, with_key, monkeypatch, capsys):
    # 既存の設定が無い端末の use は version: 2 で書く (PLAN56 決定 1)。従来の置き場の
    # 表示を確かめるため、レイアウトを明示する
    assert use_openbao(root, monkeypatch, layout='flat') == 0
    capsys.readouterr()

    assert env_backend.cmd_env_backend_status(root) == 0

    out = capsys.readouterr().out
    assert 'openbao' in out
    assert 'https://openbao.example.com' in out
    assert 'devbase/team/global' in out and 'devbase/team/projects' in out
    assert 'devbase/users/member01/global' in out and 'devbase/users/member01/projects' in out
    assert 'member01' in out
    assert 's3cret' not in out and 'rid' in out


# ---------------------------------------------------------------------------
# use
# ---------------------------------------------------------------------------

def test_use_openbao_saves_config_and_bootstrap(root, with_key, monkeypatch, capsys):
    assert use_openbao(root, monkeypatch) == 0

    config = bc.load(root)
    assert config.backend == 'openbao'
    assert config.openbao.url == 'https://openbao.example.com'
    assert config.openbao.user == 'member01'
    assert config.openbao.mount == 'devbase'
    assert config.cache_enabled is True
    assert bootstrap.load(root) == bootstrap.Credentials('rid', 's3cret')
    out = capsys.readouterr().out
    assert 's3cret' not in out
    # 平文の機密が増えていない
    for path in (root / 'secrets').rglob('*'):
        if path.is_file():
            assert b's3cret' not in path.read_bytes()


def test_use_openbao_with_no_cache(root, with_key, monkeypatch):
    assert use_openbao(root, monkeypatch, cache=False) == 0
    assert bc.load(root).cache_enabled is False


def test_use_inherits_the_cache_setting_and_cache_turns_it_back_on(root, with_key, monkeypatch):
    assert use_openbao(root, monkeypatch, cache=False) == 0

    assert env_backend.cmd_env_backend_use(root, use_args('openbao')) == 0
    assert bc.load(root).cache_enabled is False          # 指定が無ければ引き継ぐ

    assert env_backend.cmd_env_backend_use(root, use_args('openbao', cache=True)) == 0
    assert bc.load(root).cache_enabled is True


def test_use_parser_cache_flags_are_exclusive_and_default_to_none():
    import argparse

    from devbase import cli

    parser = argparse.ArgumentParser()
    cli._add_env_parser(parser.add_subparsers(dest='command'))
    assert parser.parse_args(['env', 'backend', 'use', 'age']).cache is None
    assert parser.parse_args(['env', 'backend', 'use', 'age', '--cache']).cache is True
    assert parser.parse_args(['env', 'backend', 'use', 'age', '--no-cache']).cache is False
    with pytest.raises(SystemExit):
        parser.parse_args(['env', 'backend', 'use', 'age', '--cache', '--no-cache'])


def test_use_rejects_unknown_backend_without_writing(root, caplog):
    assert env_backend.cmd_env_backend_use(root, use_args('vaultwarden')) == 2

    assert not (root / 'secrets' / 'backend.yml').exists()
    err = errors(caplog)
    assert 'vaultwarden' in err and 'openbao' in err and 'age' in err


def test_use_openbao_reports_missing_settings_without_writing(root, with_key, caplog):
    args = use_args('openbao', url='https://openbao.example.com')

    assert env_backend.cmd_env_backend_use(root, args) == 2

    assert not (root / 'secrets' / 'backend.yml').exists()
    assert 'openbao.user' in errors(caplog)


def test_use_openbao_rejects_remote_http(root, with_key, monkeypatch):
    assert use_openbao(root, monkeypatch, url='http://openbao.example.com') == 2
    assert not (root / 'secrets' / 'backend.yml').exists()


def test_use_openbao_requires_credentials_when_none_stored(root, with_key, caplog):
    args = use_args('openbao', url='https://openbao.example.com', user='member01')

    assert env_backend.cmd_env_backend_use(root, args) == 2

    assert not (root / 'secrets' / 'backend.yml').exists()
    assert 'role-id' in errors(caplog)


def test_use_openbao_without_a_key_does_not_write_plaintext(root, monkeypatch, caplog):
    assert use_openbao(root, monkeypatch) == 1

    assert not (root / 'secrets' / 'backend.yml').exists()
    assert not (root / 'secrets' / 'bootstrap.env.age').exists()
    assert 'keygen' in errors(caplog)


def test_use_openbao_keeps_stored_credentials(root, with_key, monkeypatch):
    assert use_openbao(root, monkeypatch) == 0

    args = use_args('openbao', user='member02', mount='kv')
    assert env_backend.cmd_env_backend_use(root, args) == 0

    config = bc.load(root)
    assert config.openbao.user == 'member02'
    assert config.openbao.mount == 'kv'
    assert config.openbao.url == 'https://openbao.example.com'
    assert bootstrap.load(root) == bootstrap.Credentials('rid', 's3cret')


def test_use_age_switches_back_and_keeps_the_age_store(root, with_key, monkeypatch, capsys):
    SecretStore(root).age.save(GLOBAL, {'KEEP': 'me'})
    assert use_openbao(root, monkeypatch) == 0

    assert env_backend.cmd_env_backend_use(root, use_args('age')) == 0

    config = bc.load(root)
    assert config.backend == 'age'
    assert config.openbao.url == 'https://openbao.example.com'   # 設定は残る
    assert SecretStore(root).load(GLOBAL) == {'KEEP': 'me'}
    assert bootstrap.exists(root)
    capsys.readouterr()
    assert env_backend.cmd_env_backend_status(root) == 0
    assert 'age' in capsys.readouterr().out


def test_use_parser_has_no_secret_id_option():
    """secret_id を argv で受ける経路が無い (ps から読めない)"""
    import argparse

    from devbase import cli

    parser = argparse.ArgumentParser()
    cli._add_env_parser(parser.add_subparsers(dest='command'))
    with pytest.raises(SystemExit):
        parser.parse_args(['env', 'backend', 'use', 'openbao', '--secret-id', 'x'])
    ns = parser.parse_args(['env', 'backend', 'use', 'openbao', '--secret-id-stdin',
                            '--mount', 'kv', '--role-id', 'r'])
    assert ns.secret_id_stdin is True
    assert ns.mount == 'kv' and ns.role_id == 'r'
    assert not hasattr(ns, 'secret_id')
    assert not hasattr(ns, 'project_id') and not hasattr(ns, 'client_id')


@pytest.mark.parametrize('action', [None, 'unknown'])
def test_backend_missing_or_unknown_action_reports_usage(root, caplog, action):
    """未指定・未知のアクションの現在の終了コードと案内を固定する。"""
    args = SimpleNamespace(backend_action=action)

    with caplog.at_level(logging.ERROR, logger='devbase.commands.env_backend'):
        assert env_backend.cmd_env_backend(root, args) == 2

    assert any(
        record.levelno == logging.ERROR
        and record.getMessage() ==
        'サブコマンドを指定してください: status, use, test, migrate'
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# グループ別の置き場 (PLAN56 受け入れ条件 11・14・17、決定 1)
# ---------------------------------------------------------------------------

SECRET_VALUE = 'do-not-print-this-value'


@pytest.fixture
def grouped(openbao_root, openbao):
    """``version: 2`` (``default`` → ``nyle``)。``web`` と ``mobile`` は ``with``、``api`` は宣言なし"""
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout='group', group_aliases={'default': 'nyle'})
    (openbao_root / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=with\n')
    (openbao_root / 'projects' / 'mobile').mkdir()
    (openbao_root / 'projects' / 'mobile' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=with\n')
    (openbao_root / 'projects' / 'api').mkdir()
    return openbao_root


def at(monkeypatch, root, rel=''):
    monkeypatch.setenv('PWD', str(root / rel) if rel else str(root))


def kv_paths(openbao):
    return {r.kv_path for r in openbao.received if r.kv_path}


def config_bytes(root):
    return (root / 'secrets' / 'backend.yml').read_bytes()


# -- status ------------------------------------------------------------------

def test_grouped_status_in_an_undeclared_project_shows_the_alias_and_its_paths(
        grouped, openbao, monkeypatch, capsys):
    """受け入れ条件 11: projects/api (宣言なし) で default → nyle と 4 パス"""
    at(monkeypatch, grouped, 'projects/api')

    assert env_backend.cmd_env_backend_status(grouped) == 0

    out = capsys.readouterr().out
    assert 'レイアウト: group (version 2)' in out
    assert 'default → nyle (projects/api/env にも $DEVBASE_ROOT/env にも宣言なし)' in out
    assert 'devbase/team/nyle/global' in out
    assert 'devbase/team/nyle/projects/api' in out
    assert 'devbase/users/member01/nyle/global' in out
    assert 'devbase/users/member01/nyle/projects/api' in out
    assert 'devbase/team/global' not in out and 'devbase/team/projects/' not in out
    assert openbao.received == []                      # 表示のためにサーバへ要求しない
    assert 's3cret' not in out


def test_grouped_status_in_a_declaring_project_shows_its_file(grouped, monkeypatch, capsys):
    """受け入れ条件 11: projects/web (with) の下位ディレクトリでも web の置き場"""
    at(monkeypatch, grouped, 'projects/web/src')

    assert env_backend.cmd_env_backend_status(grouped) == 0

    out = capsys.readouterr().out
    assert 'with (projects/web/env)' in out
    assert 'devbase/team/with/global' in out
    assert 'devbase/team/with/projects/web' in out
    assert 'devbase/users/member01/with/global' in out
    assert 'devbase/users/member01/with/projects/web' in out
    assert 'nyle' not in out.split('置き場')[1].split('キャッシュ')[0]


def test_grouped_status_outside_projects_uses_the_root_env(grouped, monkeypatch, capsys):
    """受け入れ条件 11: $DEVBASE_ROOT では $DEVBASE_ROOT/env のグループ"""
    (grouped / 'env').write_text('DEVBASE_ACCOUNT_GROUP=kkg\n')
    at(monkeypatch, grouped)

    assert env_backend.cmd_env_backend_status(grouped) == 0

    out = capsys.readouterr().out
    assert 'kkg ($DEVBASE_ROOT/env)' in out
    assert 'devbase/team/kkg/global' in out
    assert 'devbase/team/kkg/projects/<name>' in out
    assert 'devbase/users/member01/kkg/global' in out
    assert 'devbase/users/member01/kkg/projects/<name>' in out


def test_flat_status_has_no_layout_or_group_lines(openbao_root, capsys):
    """受け入れ条件 9: version: 1 の status は今と同じ (グループの行を足さない)"""
    assert env_backend.cmd_env_backend_status(openbao_root) == 0

    out = capsys.readouterr().out
    assert 'レイアウト' not in out and 'グループ' not in out
    assert 'devbase/team/global' in out


# -- use ---------------------------------------------------------------------

def test_use_without_existing_settings_writes_the_grouped_layout(root, with_key, monkeypatch):
    """決定 1: --layout なし・既存の設定なしは group (version: 2)"""
    assert use_openbao(root, monkeypatch) == 0

    config = bc.load(root)
    assert config.version == 2
    assert config.openbao.layout == 'group'
    assert config.openbao.group_aliases == {}


def test_use_without_layout_inherits_version_2_and_its_aliases(grouped, monkeypatch):
    """決定 1: version: 2 の設定は、--layout なしの use で version: 1 へ戻らない"""
    args = use_args('openbao', user='member02')

    assert env_backend.cmd_env_backend_use(grouped, args) == 0

    config = bc.load(grouped)
    assert config.version == 2 and config.openbao.layout == 'group'
    assert config.openbao.user == 'member02'
    assert config.openbao.group_aliases == {'default': 'nyle'}


def test_use_without_layout_inherits_version_1(openbao_root):
    assert env_backend.cmd_env_backend_use(openbao_root, use_args('openbao', user='member02')) == 0

    config = bc.load(openbao_root)
    assert config.version == 1 and config.openbao.layout == 'flat'
    assert config.openbao.user == 'member02'


def test_use_age_keeps_the_version_2_openbao_settings(grouped):
    """version を渡さずに組むと version: 2 の openbao 節と食い違って書けなかった"""
    assert env_backend.cmd_env_backend_use(grouped, use_args('age')) == 0

    config = bc.load(grouped)
    assert config.backend == 'age'
    assert config.version == 2
    assert config.openbao.group_aliases == {'default': 'nyle'}


def test_use_layout_group_drops_the_flat_paths(openbao_root, openbao, capsys):
    import yaml

    from tests.conftest import configure_openbao

    config = configure_openbao(openbao_root, openbao)
    bc.save(openbao_root, bc.BackendConfig(
        backend='openbao', version=1,
        openbao=replace_settings(config.openbao, path_team_global='kv/common')))

    args = use_args('openbao', layout='group', group_aliases=['default=nyle'])
    assert env_backend.cmd_env_backend_use(openbao_root, args) == 0

    raw = yaml.safe_load(config_bytes(openbao_root))
    assert raw['version'] == 2
    assert raw['openbao']['layout'] == 'group'
    assert 'path_team_global' not in raw['openbao']
    assert 'path_team_project_prefix' not in raw['openbao']
    assert raw['openbao']['group_aliases'] == {'default': 'nyle'}
    loaded = bc.load(openbao_root)
    assert loaded.openbao.display_path(SecretRef.for_global(group='default')) == \
        'devbase/team/nyle/global'


def replace_settings(settings, **kw):
    from dataclasses import replace

    return replace(settings, **kw)


def test_use_layout_flat_writes_version_1_and_reports_dropped_aliases(grouped, capsys):
    assert env_backend.cmd_env_backend_use(grouped, use_args('openbao', layout='flat')) == 0

    config = bc.load(grouped)
    assert config.version == 1 and config.openbao.layout == 'flat'
    assert config.openbao.group_aliases == {}
    out = capsys.readouterr().out
    assert 'group_aliases' in out and 'default → nyle' in out


def test_use_group_alias_replaces_the_existing_aliases(grouped):
    args = use_args('openbao', group_aliases=['kkg=kkg-main', 'with=with-main'])

    assert env_backend.cmd_env_backend_use(grouped, args) == 0

    assert bc.load(grouped).openbao.group_aliases == {'kkg': 'kkg-main', 'with': 'with-main'}


@pytest.mark.parametrize('alias', [
    'default=global', 'default=projects', 'ubuntu=nyle', 'default=ubuntu', '1=nyle',
    'default=1', 'bad name=nyle', 'a/b=nyle', 'default=a/b', 'default', 'default=', '=nyle',
])
def test_use_rejects_an_unusable_group_alias_without_writing(grouped, caplog, alias):
    """決定 1: FROM / TO は DEVBASE_ACCOUNT_GROUP と同じ検証、TO の global / projects も 2"""
    before = config_bytes(grouped)

    args = use_args('openbao', group_aliases=[alias])
    assert env_backend.cmd_env_backend_use(grouped, args) == 2

    assert config_bytes(grouped) == before
    assert '--group-alias' in errors(caplog)


def test_use_accepts_global_as_the_alias_source(grouped):
    """FROM の global は拒まない (global という名前のグループを別の置き場へ向ける)"""
    args = use_args('openbao', group_aliases=['global=nyle'])

    assert env_backend.cmd_env_backend_use(grouped, args) == 0
    assert bc.load(grouped).openbao.group_aliases == {'global': 'nyle'}


def test_use_rejects_conflicting_aliases_for_the_same_group(grouped, caplog):
    before = config_bytes(grouped)

    args = use_args('openbao', group_aliases=['default=nyle', 'default=with'])
    assert env_backend.cmd_env_backend_use(grouped, args) == 2

    assert config_bytes(grouped) == before
    assert 'default' in errors(caplog)


def test_use_rejects_group_alias_with_the_flat_layout(grouped, openbao_root, caplog):
    before = config_bytes(grouped)

    args = use_args('openbao', layout='flat', group_aliases=['default=nyle'])
    assert env_backend.cmd_env_backend_use(grouped, args) == 2

    assert config_bytes(grouped) == before
    assert '--group-alias' in errors(caplog)


def test_use_rejects_group_alias_when_the_existing_config_is_version_1(openbao_root, caplog):
    before = config_bytes(openbao_root)

    args = use_args('openbao', group_aliases=['default=nyle'])
    assert env_backend.cmd_env_backend_use(openbao_root, args) == 2

    assert config_bytes(openbao_root) == before
    assert '--group-alias' in errors(caplog)


@pytest.mark.parametrize('kw', [{'layout': 'group'}, {'group_aliases': ['default=nyle']}])
def test_use_rejects_layout_options_for_a_file_backend(grouped, caplog, kw):
    before = config_bytes(grouped)

    assert env_backend.cmd_env_backend_use(grouped, use_args('age', **kw)) == 2

    assert config_bytes(grouped) == before


def _plant_cache(root):
    path = root / 'secrets' / 'cache' / 'team' / 'global.env.age'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'ciphertext')
    return path


@pytest.mark.parametrize('layout, fixture', [('group', 'openbao_root'), ('flat', 'grouped')])
def test_use_purges_the_cache_when_the_layout_changes(request, layout, fixture, capsys):
    from devbase.env import cache

    root = request.getfixturevalue(fixture)
    _plant_cache(root)

    assert env_backend.cmd_env_backend_use(root, use_args('openbao', layout=layout)) == 0

    assert cache.cached_files(root) == []
    assert 'キャッシュ' in capsys.readouterr().out
    assert bc.load(root).openbao.layout == layout


@pytest.mark.parametrize('kw', [{}, {'layout': 'flat'}])
def test_use_keeps_the_cache_when_the_layout_stays(openbao_root, capsys, kw):
    from devbase.env import cache

    planted = _plant_cache(openbao_root)

    assert env_backend.cmd_env_backend_use(openbao_root, use_args('openbao', **kw)) == 0

    assert cache.cached_files(openbao_root) == [planted]


def test_use_parser_accepts_layout_and_repeated_group_aliases():
    import argparse

    from devbase import cli

    parser = argparse.ArgumentParser()
    cli._add_env_parser(parser.add_subparsers(dest='command'))
    ns = parser.parse_args(['env', 'backend', 'use', 'openbao'])
    assert ns.layout is None and ns.group_aliases is None
    ns = parser.parse_args(['env', 'backend', 'use', 'openbao', '--layout', 'group',
                            '--group-alias', 'default=nyle', '--group-alias', 'kkg=k'])
    assert ns.layout == 'group'
    assert ns.group_aliases == ['default=nyle', 'kkg=k']
    with pytest.raises(SystemExit):
        parser.parse_args(['env', 'backend', 'use', 'openbao', '--layout', 'nested'])


# -- test --------------------------------------------------------------------

def test_grouped_backend_test_only_probes_the_projects_group(grouped, openbao, monkeypatch,
                                                            capsys):
    """受け入れ条件 17: web (with) では with の置き場と with のプロジェクトだけ"""
    openbao.put('team/with/global', {'A': SECRET_VALUE})
    at(monkeypatch, grouped, 'projects/web')

    assert env_backend.cmd_env_backend_test(grouped) == 0

    assert kv_paths(openbao) == {
        'team/with/global', 'users/member01/with/global',
        'team/with/projects/mobile', 'users/member01/with/projects/mobile',
        'team/with/projects/web', 'users/member01/with/projects/web',
    }
    assert openbao.logins == 1
    out = capsys.readouterr().out
    assert 'api' in out and SECRET_VALUE not in out and 's3cret' not in out


def test_grouped_backend_test_outside_projects_uses_the_root_group(grouped, openbao,
                                                                  monkeypatch):
    """受け入れ条件 17: $DEVBASE_ROOT (宣言なし → default → nyle) では api だけ"""
    at(monkeypatch, grouped)

    assert env_backend.cmd_env_backend_test(grouped) == 0

    assert kv_paths(openbao) == {
        'team/nyle/global', 'users/member01/nyle/global',
        'team/nyle/projects/api', 'users/member01/nyle/projects/api',
    }
