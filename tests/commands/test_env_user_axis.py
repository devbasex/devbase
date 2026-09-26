"""devbase env の --user と、サーバ backend での edit / init --reset (PLAN51 設計 2)"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from devbase.commands import env as env_cmd
from devbase.env import agekeys
from devbase.env.secret_store import SecretRef, SecretStore


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')
USER_GLOBAL = SecretRef.for_global(owner='user')
USER_WEB = SecretRef.for_project('web', owner='user')

TEAM_GLOBAL = 'team/global'
TEAM_WEB = 'team/projects/web'
USER_GLOBAL_PATH = 'users/member01/global'
USER_WEB_PATH = 'users/member01/projects/web'


@pytest.fixture
def in_web(openbao_root, monkeypatch):
    monkeypatch.setenv('PWD', str(openbao_root / 'projects' / 'web'))
    return openbao_root


@pytest.fixture
def file_root(tmp_path, monkeypatch):
    """backend 未設定の DEVBASE_ROOT (age 鍵あり)"""
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    agekeys.generate_key_file()
    return tmp_path


def errors(caplog) -> str:
    return '\n'.join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


# ---------------------------------------------------------------------------
# set / delete の宛先
# ---------------------------------------------------------------------------

def test_set_without_flags_targets_the_team_global(openbao_root, openbao):
    assert env_cmd.cmd_env_set(openbao_root, 'A=1') == 0
    assert openbao.get(TEAM_GLOBAL) == {'A': '1'}
    assert openbao.requests_to(USER_GLOBAL_PATH) == []


def test_set_user_targets_the_user_global(openbao_root, openbao):
    assert env_cmd.cmd_env_set(openbao_root, 'A=1', user=True) == 0
    assert openbao.get(USER_GLOBAL_PATH) == {'A': '1'}
    assert openbao.get(TEAM_GLOBAL) == {}


def test_set_project_targets_the_team_project(in_web, openbao):
    assert env_cmd.cmd_env_set(in_web, 'A=1', project=True) == 0
    assert openbao.get(TEAM_WEB) == {'A': '1'}
    assert openbao.get(USER_WEB_PATH) == {}


def test_set_user_project_targets_the_user_project(in_web, openbao):
    assert env_cmd.cmd_env_set(in_web, 'A=1', project=True, user=True) == 0
    assert openbao.get(USER_WEB_PATH) == {'A': '1'}
    assert openbao.get(TEAM_WEB) == {}


def test_set_user_project_outside_projects_is_refused(openbao_root, openbao):
    assert env_cmd.cmd_env_set(openbao_root, 'A=1', project=True, user=True) == 1
    assert not any(r.kv_path for r in openbao.requests_of('POST'))


def test_delete_user_removes_from_the_user_reference_only(in_web, openbao):
    openbao.put(USER_GLOBAL_PATH, {'A': 'u', 'KEEP': '1'})
    openbao.put(TEAM_GLOBAL, {'A': 't'})

    assert env_cmd.cmd_env_delete(in_web, 'A', user=True) == 0

    assert openbao.get(USER_GLOBAL_PATH) == {'KEEP': '1'}
    assert openbao.get(TEAM_GLOBAL) == {'A': 't'}


def test_set_then_get_and_delete_on_the_server(openbao_root, openbao, capsys):
    assert env_cmd.cmd_env_set(openbao_root, 'TOKEN=abc') == 0
    assert env_cmd.cmd_env_get(openbao_root, 'TOKEN') == 0
    assert capsys.readouterr().out.strip() == 'abc'
    assert env_cmd.cmd_env_delete(openbao_root, 'TOKEN') == 0
    assert env_cmd.cmd_env_get(openbao_root, 'TOKEN') == 1
    assert 'TOKEN' not in openbao.get(TEAM_GLOBAL)


def test_set_writes_the_whole_reference_with_the_read_version(openbao_root, openbao):
    openbao.put(TEAM_GLOBAL, {'SAME': '1'})

    env_cmd.cmd_env_set(openbao_root, 'NEW=2')

    posts = [r for r in openbao.requests_of('POST') if r.kv_path]
    assert len(posts) == 1
    assert posts[0].cas == 1 and posts[0].body['data'] == {'SAME': '1', 'NEW': '2'}


# ---------------------------------------------------------------------------
# 書き込みを伴うコマンドは控えへ落ちない
# ---------------------------------------------------------------------------

def _fill_cache(root, openbao):
    openbao.put(TEAM_GLOBAL, {'A': 'cached'})
    store = SecretStore(root)
    assert store.load(GLOBAL) == {'A': 'cached'}
    assert store.load(USER_GLOBAL) == {}


def test_set_stops_when_the_server_is_unreachable_even_with_a_cache(openbao_root, openbao,
                                                                    caplog):
    _fill_cache(openbao_root, openbao)
    openbao.stop()

    assert env_cmd.cmd_env_set(openbao_root, 'B=2') == 1

    assert '到達' in errors(caplog)


def test_delete_stops_when_the_server_is_unreachable_even_with_a_cache(openbao_root, openbao,
                                                                       caplog):
    _fill_cache(openbao_root, openbao)
    openbao.stop()

    assert env_cmd.cmd_env_delete(openbao_root, 'A') == 1

    assert '到達' in errors(caplog)


def test_edit_does_not_open_the_editor_when_the_server_is_unreachable(openbao_root, openbao,
                                                                      monkeypatch, caplog):
    _fill_cache(openbao_root, openbao)
    openbao.stop()
    calls = []
    monkeypatch.setattr(env_cmd.subprocess, 'call', lambda argv: calls.append(argv) or 0)

    assert env_cmd.cmd_env_edit(openbao_root) == 1

    assert calls == []
    assert '到達' in errors(caplog)


def test_reads_still_use_the_cache_when_the_server_is_unreachable(openbao_root, openbao,
                                                                  capsys):
    _fill_cache(openbao_root, openbao)
    openbao.stop()

    assert env_cmd.cmd_env_get(openbao_root, 'A') == 0
    assert env_cmd.cmd_env_list(openbao_root) == 0
    assert 'A' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# ファイル backend では --user を扱えない
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('prepare', ['unset', 'age', 'plaintext'])
def test_user_writes_are_refused_on_file_backends(file_root, prepare, caplog):
    store = SecretStore(file_root)
    if prepare == 'age':
        store.age.save(GLOBAL, {'KEEP': '1'})
    elif prepare == 'plaintext':
        store.plaintext.save(GLOBAL, {'KEEP': '1'})

    assert env_cmd.cmd_env_set(file_root, 'A=1', user=True) == 1
    assert env_cmd.cmd_env_delete(file_root, 'KEEP', user=True) == 1
    assert '個人単位' in errors(caplog)

    expected = {} if prepare == 'unset' else {'KEEP': '1'}
    assert SecretStore(file_root).load(GLOBAL) == expected


def test_user_reads_on_file_backends_just_find_nothing(file_root, capsys):
    SecretStore(file_root).age.save(GLOBAL, {'A': 'team'})

    assert env_cmd.cmd_env_get(file_root, 'A', user=True) == 1
    assert env_cmd.cmd_env_list(file_root, user=True) == 0
    out = capsys.readouterr().out
    assert '個人' not in out


# ---------------------------------------------------------------------------
# get の探索順
# ---------------------------------------------------------------------------

def test_get_prefers_user_global_then_team_global_then_project(in_web, openbao, capsys):
    openbao.put(TEAM_GLOBAL, {'K': 'tg', 'ONLY_TG': 'x'})
    openbao.put(USER_GLOBAL_PATH, {'K': 'ug'})
    openbao.put(TEAM_WEB, {'P': 'tw'})
    openbao.put(USER_WEB_PATH, {'P': 'uw'})

    def get(key, **kw):
        assert env_cmd.cmd_env_get(in_web, key, **kw) == 0
        return capsys.readouterr().out.strip()

    assert get('K') == 'ug'
    assert get('ONLY_TG') == 'x'
    assert get('P') == 'uw'
    assert get('K', user=True) == 'ug'
    assert get('P', user=True) == 'uw'
    assert env_cmd.cmd_env_get(in_web, 'ONLY_TG', user=True) == 1


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_shows_the_backend_name_and_all_present_sections(in_web, openbao, capsys):
    openbao.put(TEAM_GLOBAL, {'A': '1'})
    openbao.put(USER_GLOBAL_PATH, {'B': '2'})
    openbao.put(TEAM_WEB, {'C': '3'})
    openbao.put(USER_WEB_PATH, {'D': '4'})

    assert env_cmd.cmd_env_list(in_web, keys_only=True) == 0

    out = capsys.readouterr().out
    assert '[openbao]' in out and '[暗号化]' not in out
    assert out.index('=== グローバル') < out.index('=== 個人のグローバル') \
        < out.index('=== プロジェクト: web') < out.index('=== 個人のプロジェクト: web')
    for key in 'ABCD':
        assert key in out


def test_list_omits_absent_user_sections(in_web, openbao, capsys):
    openbao.put(TEAM_GLOBAL, {'A': '1'})

    env_cmd.cmd_env_list(in_web, keys_only=True)

    out = capsys.readouterr().out
    assert '=== グローバル' in out
    assert '個人' not in out


@pytest.mark.parametrize('flags, expected, unexpected', [
    (dict(global_only=True), ['=== グローバル', '=== 個人のグローバル'], ['プロジェクト']),
    (dict(project_only=True), ['=== プロジェクト: web', '=== 個人のプロジェクト: web'], ['グローバル']),
    (dict(user=True), ['=== 個人のグローバル', '=== 個人のプロジェクト: web'],
     ['=== グローバル', '=== プロジェクト: web']),
    (dict(global_only=True, user=True), ['=== 個人のグローバル'],
     ['=== グローバル', 'プロジェクト']),
    (dict(project_only=True, user=True), ['=== 個人のプロジェクト: web'],
     ['グローバル', '=== プロジェクト: web']),
])
def test_list_filters_both_axes_independently(in_web, openbao, capsys, flags, expected,
                                              unexpected):
    openbao.put(TEAM_GLOBAL, {'A': '1'})
    openbao.put(USER_GLOBAL_PATH, {'B': '2'})
    openbao.put(TEAM_WEB, {'C': '3'})
    openbao.put(USER_WEB_PATH, {'D': '4'})

    env_cmd.cmd_env_list(in_web, keys_only=True, **flags)

    out = capsys.readouterr().out
    for text in expected:
        assert text in out
    for text in unexpected:
        assert text not in out


def test_list_marks_age_and_plaintext_as_before(file_root, capsys):
    SecretStore(file_root).age.save(GLOBAL, {'A': '1'})
    env_cmd.cmd_env_list(file_root, global_only=True)
    assert '[暗号化]' in capsys.readouterr().out

    SecretStore(file_root).age.remove(GLOBAL)
    SecretStore(file_root).plaintext.save(GLOBAL, {'A': '1'})
    env_cmd.cmd_env_list(file_root, global_only=True)
    out = capsys.readouterr().out
    assert '[' not in out.split('===')[1]


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------

def test_edit_on_the_server_goes_through_a_temporary_file(openbao_root, openbao,
                                                          monkeypatch):
    openbao.put(TEAM_GLOBAL, {'FOO': 'bar'})
    seen = {}

    def fake_call(argv):
        path = Path(argv[-1])
        seen['path'] = path
        assert path.exists() and path != Path(TEAM_GLOBAL)
        assert path.read_bytes() == b'FOO=bar\n'
        path.write_text('FOO=changed\nNEW=added\n')
        return 0

    monkeypatch.setattr(env_cmd.subprocess, 'call', fake_call)

    assert env_cmd.cmd_env_edit(openbao_root) == 0

    assert openbao.get(TEAM_GLOBAL) == {'FOO': 'changed', 'NEW': 'added'}
    assert not seen['path'].exists() and not seen['path'].parent.exists()


def test_edit_user_project_targets_the_user_project(in_web, openbao, monkeypatch):
    def fake_call(argv):
        Path(argv[-1]).write_text('X=1\n')
        return 0

    monkeypatch.setattr(env_cmd.subprocess, 'call', fake_call)

    assert env_cmd.cmd_env_edit(in_web, project=True, user=True) == 0

    assert openbao.get(USER_WEB_PATH) == {'X': '1'}
    assert openbao.get(TEAM_WEB) == {}


# ---------------------------------------------------------------------------
# init --reset
# ---------------------------------------------------------------------------

def test_init_reset_on_the_server_keeps_an_encrypted_backup(openbao_root, openbao,
                                                            monkeypatch):
    openbao.put(TEAM_GLOBAL, {'OLD': 'value'})
    monkeypatch.setattr(env_cmd.CollectorRegistry, 'discover', lambda self: None)

    assert env_cmd.cmd_env_init(openbao_root, reset=True) == 0

    assert openbao.get(TEAM_GLOBAL) == {}
    backups = list((openbao_root / 'backups' / 'env-init').rglob('*.age'))
    assert len(backups) == 1
    assert b'value' not in backups[0].read_bytes()
    assert not list((openbao_root / 'backups').rglob('*.env'))
    from devbase.env.secret_store import AgeBackend
    from devbase.env import cipher
    plain = cipher.decrypt(backups[0].read_bytes(), identities=AgeBackend(openbao_root).identities())
    assert plain == b'OLD=value\n'


def test_init_reset_on_the_server_refuses_when_no_backup_can_be_made(openbao_root, openbao,
                                                                     monkeypatch, caplog):
    openbao.put(TEAM_GLOBAL, {'OLD': 'value'})
    from devbase.env import secret_store
    monkeypatch.setattr(secret_store.AgeBackend, 'encrypt_bytes',
                        lambda self, data: (_ for _ in ()).throw(
                            secret_store.SecretStoreError('no recipients')))

    assert env_cmd.cmd_env_init(openbao_root, reset=True) == 1

    assert openbao.get(TEAM_GLOBAL) == {'OLD': 'value'}
    assert '退避' in errors(caplog)


# ---------------------------------------------------------------------------
# --user を受け付けないコマンド
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('argv', [
    ['env', 'init', '--user'], ['env', 'project', '--user'],
    ['env', 'export', '--user'], ['env', 'import', 'x.dbenv', '--user'],
])
def test_commands_without_the_owner_axis_reject_user(argv):
    import argparse

    from devbase import cli

    parser = argparse.ArgumentParser()
    cli._add_env_parser(parser.add_subparsers(dest='command'))
    with pytest.raises(SystemExit):
        parser.parse_args(argv)


@pytest.mark.parametrize('argv', [
    ['env', 'list', '--user'], ['env', 'get', 'K', '--user'], ['env', 'set', 'K=1', '--user', '-p'],
    ['env', 'delete', 'K', '--user'], ['env', 'edit', '--user'], ['env', 'sync', '--user'],
])
def test_commands_with_the_owner_axis_accept_user(argv):
    import argparse

    from devbase import cli

    parser = argparse.ArgumentParser()
    cli._add_env_parser(parser.add_subparsers(dest='command'))
    ns = parser.parse_args(argv)
    assert ns.user is True


@pytest.mark.parametrize('prepare', ['unset', 'age', 'plaintext'])
def test_edit_user_on_file_backends_does_not_open_the_team_file(file_root, prepare, monkeypatch,
                                                                caplog):
    store = SecretStore(file_root)
    if prepare == 'age':
        store.age.save(GLOBAL, {'KEEP': '1'})
    elif prepare == 'plaintext':
        store.plaintext.save(GLOBAL, {'KEEP': '1'})
    calls = []
    monkeypatch.setattr(env_cmd.subprocess, 'call', lambda argv: calls.append(argv) or 0)

    assert env_cmd.cmd_env_edit(file_root, user=True) == 1
    assert env_cmd.cmd_env_edit(file_root, user=True, project=True) == 1

    assert calls == []                       # エディタは 1 度も起動しない
    assert '個人単位' in errors(caplog)
    expected = {} if prepare == 'unset' else {'KEEP': '1'}
    assert SecretStore(file_root).load(GLOBAL) == expected


# ---------------------------------------------------------------------------
# グループ別の置き場での対象グループと --group (PLAN56 受け入れ条件 3・5・5a・6・14)
# ---------------------------------------------------------------------------

SECRET_VALUE = 'do-not-print-this-value'


@pytest.fixture
def grouped(openbao_root, openbao):
    """``version: 2`` (``default`` → ``nyle``)。``web`` は ``with``、``api`` は宣言なし"""
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout='group', group_aliases={'default': 'nyle'})
    (openbao_root / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=with\n')
    (openbao_root / 'projects' / 'api').mkdir()
    return openbao_root


def at(monkeypatch, root, rel=''):
    monkeypatch.setenv('PWD', str(root / rel) if rel else str(root))


def run_env(root, *argv):
    from devbase import cli

    return env_cmd.cmd_env(root, cli._create_parser().parse_args(['env', *argv]))


def kv_paths(openbao):
    return {r.kv_path for r in openbao.received if r.kv_path}


def fake_editor(monkeypatch, text='EDITED=1\n'):
    def fake_call(argv):
        Path(argv[-1]).write_text(text)
        return 0

    monkeypatch.setattr(env_cmd.subprocess, 'call', fake_call)


def test_grouped_set_from_a_subdirectory_targets_the_projects_group(grouped, openbao,
                                                                    monkeypatch):
    """受け入れ条件 3"""
    at(monkeypatch, grouped, 'projects/web/src')

    assert run_env(grouped, 'set', 'FOO=1') == 0
    assert run_env(grouped, 'set', '-p', 'FOO=2') == 0
    assert run_env(grouped, 'set', '--user', 'FOO=3') == 0

    assert openbao.get('team/with/global') == {'FOO': '1'}
    assert openbao.get('team/with/projects/web') == {'FOO': '2'}
    assert openbao.get('users/member01/with/global') == {'FOO': '3'}
    assert kv_paths(openbao) == {'team/with/global', 'team/with/projects/web',
                                 'users/member01/with/global'}


def test_grouped_commands_with_group_outside_projects_use_that_group(grouped, openbao,
                                                                    monkeypatch, capsys):
    """受け入れ条件 5: $DEVBASE_ROOT で --group kkg の 5 コマンド"""
    openbao.put('team/kkg/global', {'KEY': 'v'})
    fake_editor(monkeypatch)

    assert run_env(grouped, 'list', '--group', 'kkg', '--keys') == 0
    out = capsys.readouterr().out
    assert '=== グローバル（グループ kkg） (devbase/team/kkg/global [openbao]) ===' in out
    assert run_env(grouped, 'get', '--group', 'kkg', 'KEY') == 0
    assert capsys.readouterr().out.strip() == 'v'
    assert run_env(grouped, 'set', '--group', 'kkg', 'NEW=v') == 0
    assert run_env(grouped, 'set', '--group', 'kkg', '--user', 'MINE=u') == 0
    assert run_env(grouped, 'delete', '--group', 'kkg', 'KEY') == 0
    assert run_env(grouped, 'edit', '--group', 'kkg', '--user') == 0

    assert openbao.get('team/kkg/global') == {'NEW': 'v'}
    assert openbao.get('users/member01/kkg/global') == {'EDITED': '1'}
    assert kv_paths(openbao) == {'team/kkg/global', 'users/member01/kkg/global'}


def test_grouped_list_headings_show_the_group(grouped, openbao, monkeypatch, capsys):
    """受け入れ条件 5: 一覧の見出しにグループ名が出る"""
    openbao.put('team/with/global', {'A': '1'})
    openbao.put('users/member01/with/global', {'B': '2'})
    openbao.put('team/with/projects/web', {'C': '3'})
    openbao.put('users/member01/with/projects/web', {'D': '4'})
    at(monkeypatch, grouped, 'projects/web')

    assert run_env(grouped, 'list', '--keys') == 0

    out = capsys.readouterr().out
    for heading in ('=== グローバル（グループ with） ', '=== 個人のグローバル（グループ with） ',
                    '=== プロジェクト: web（グループ with） ',
                    '=== 個人のプロジェクト: web（グループ with） '):
        assert heading in out
    assert 'グローバル（グループ with）: 1変数' in out


def test_grouped_project_write_with_another_group_is_refused(grouped, openbao, monkeypatch,
                                                            caplog):
    """受け入れ条件 5a: web (with) で set -p --group kkg は 1、要求 0 回"""
    at(monkeypatch, grouped, 'projects/web')

    assert run_env(grouped, 'set', '-p', '--group', 'kkg', f'FOO={SECRET_VALUE}') == 1
    assert run_env(grouped, 'delete', '-p', '--group', 'kkg', 'FOO') == 1
    assert run_env(grouped, 'edit', '-p', '--group', 'kkg') == 1
    assert run_env(grouped, 'list', '-p', '--group', 'kkg') == 1

    assert openbao.received == []
    text = errors(caplog)
    assert 'kkg' in text and 'with' in text and 'projects/web/env' in text
    assert SECRET_VALUE not in caplog.text


def test_grouped_get_with_another_group_searches_only_the_common_references(
        grouped, openbao, monkeypatch, caplog, capsys):
    """受け入れ条件 5a: web で get --group kkg は kkg の共通の参照だけを探す"""
    openbao.put('team/with/projects/web', {'FOO': SECRET_VALUE})
    at(monkeypatch, grouped, 'projects/web')

    assert run_env(grouped, 'get', '--group', 'kkg', 'FOO') == 1

    assert kv_paths(openbao) == {'team/kkg/global', 'users/member01/kkg/global'}
    notices = [r.getMessage() for r in caplog.records
               if r.levelno >= logging.WARNING and 'プロジェクト' in r.getMessage()]
    assert len(notices) == 1 and 'kkg' in notices[0] and 'with' in notices[0]
    captured = capsys.readouterr()
    assert SECRET_VALUE not in captured.out + captured.err + caplog.text


def test_grouped_list_with_another_group_leaves_out_the_project(grouped, openbao, monkeypatch,
                                                               caplog, capsys):
    openbao.put('team/kkg/global', {'A': '1'})
    openbao.put('team/with/projects/web', {'C': SECRET_VALUE})
    at(monkeypatch, grouped, 'projects/web')

    assert run_env(grouped, 'list', '--group', 'kkg') == 0

    assert kv_paths(openbao) == {'team/kkg/global', 'users/member01/kkg/global'}
    out = capsys.readouterr().out
    assert 'プロジェクト' not in out
    assert SECRET_VALUE not in out + caplog.text
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


@pytest.mark.parametrize('group', ['nyle', 'default'])
def test_grouped_project_write_compares_the_aliased_names(grouped, openbao, monkeypatch, group):
    """受け入れ条件 5a: 宣言の無い api で -p --group nyle / default は team/nyle/projects/api"""
    at(monkeypatch, grouped, 'projects/api')

    assert run_env(grouped, 'set', '-p', '--group', group, 'FOO=1') == 0

    assert openbao.get('team/nyle/projects/api') == {'FOO': '1'}
    assert kv_paths(openbao) == {'team/nyle/projects/api'}


def test_grouped_mismatch_message_shows_the_names_before_and_after_the_alias(
        grouped, openbao, monkeypatch, caplog):
    at(monkeypatch, grouped, 'projects/api')

    assert run_env(grouped, 'set', '-p', '--group', 'with', 'FOO=1') == 1

    assert 'default → nyle' in errors(caplog)
    assert openbao.received == []


COMMANDS_WITH_GROUP = [
    ['list'], ['get', 'KEY'], ['set', 'KEY=1'], ['delete', 'KEY'], ['edit'],
]


@pytest.mark.parametrize('argv', COMMANDS_WITH_GROUP)
@pytest.mark.parametrize('name', ['ubuntu', '1', 'bad name', 'a/b', 'global', 'projects'])
def test_grouped_unusable_group_names_exit_2_without_requests(grouped, openbao, monkeypatch,
                                                              caplog, argv, name):
    """受け入れ条件 6"""
    fake_editor(monkeypatch)

    assert run_env(grouped, *argv, '--group', name) == 2

    assert openbao.received == []
    assert '--group' in errors(caplog)
    assert openbao.secret_id not in caplog.text


@pytest.mark.parametrize('argv', COMMANDS_WITH_GROUP)
def test_group_is_refused_with_the_flat_layout(openbao_root, openbao, monkeypatch, caplog,
                                               argv):
    """受け入れ条件 6: version 1 で --group は 2"""
    fake_editor(monkeypatch)

    assert run_env(openbao_root, *argv, '--group', 'with') == 2

    assert openbao.received == []
    assert 'グループ別の置き場' in errors(caplog)


@pytest.mark.parametrize('argv', COMMANDS_WITH_GROUP)
def test_group_is_refused_with_a_file_backend(file_root, monkeypatch, caplog, argv):
    """受け入れ条件 6: ファイル backend で --group は 2"""
    SecretStore(file_root).age.save(GLOBAL, {'KEY': 'x'})
    calls = []
    monkeypatch.setattr(env_cmd.subprocess, 'call', lambda argv: calls.append(argv) or 0)

    assert run_env(file_root, *argv, '--group', 'with') == 2

    assert calls == []
    assert SecretStore(file_root).load(GLOBAL) == {'KEY': 'x'}
    assert 'グループ別の置き場' in errors(caplog)
