"""TUI の一覧の元になるキーの行 (#273 collect_key_rows)"""

from __future__ import annotations

import dataclasses

import pytest

from devbase.commands import env_rows
from devbase.commands.env import GroupOptionError
from devbase.env import agekeys
from devbase.errors import DevbaseError

TEAM = 'team/team-a/global'
USER = 'users/member01/team-a/global'


@pytest.fixture
def grouped(openbao_root, openbao):
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout='group')
    (openbao_root / 'env').write_text('DEVBASE_ACCOUNT_GROUP=team-a\n')
    (openbao_root / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=team-a\n')
    return openbao_root


def shown(listing):
    return [(r.key, r.owner_label, r.scope_label, r.group_label, r.wins) for r in listing.rows]


def kv_gets(openbao):
    return [r.kv_path for r in openbao.requests_of('GET') if r.kv_path]


def test_rows_show_the_owner_scope_and_group(grouped, openbao):
    openbao.put(TEAM, {'A': 'secret-a'})
    openbao.put(USER, {'B': 'secret-b'})

    listing = env_rows.collect_key_rows(grouped, group='team-a')

    assert shown(listing) == [('A', 'チーム', '共通', 'team-a', True),
                              ('B', '個人', '共通', 'team-a', True)]
    assert listing.grouped and listing.has_user_refs
    assert listing.group == 'team-a' and listing.project is None


def test_rows_do_not_hold_values(grouped, openbao):
    openbao.put(TEAM, {'A': 'secret-a'})

    listing = env_rows.collect_key_rows(grouped, group='team-a')

    assert 'secret-a' not in repr(listing)
    assert {f.name for f in dataclasses.fields(env_rows.KeyRow)} == {
        'key', 'ref', 'owner_label', 'scope_label', 'group_label', 'wins'}


def test_the_user_row_wins_over_the_team_row(grouped, openbao):
    openbao.put(TEAM, {'K': 't'})
    openbao.put(USER, {'K': 'u'})

    listing = env_rows.collect_key_rows(grouped, group='team-a')

    assert shown(listing) == [('K', 'チーム', '共通', 'team-a', False),
                              ('K', '個人', '共通', 'team-a', True)]


def test_project_rows_follow_the_stacking_order(grouped, openbao):
    openbao.put(TEAM, {'K': '1', 'Z': '1'})
    openbao.put(USER, {'K': '2'})
    openbao.put('team/team-a/projects/web', {'K': '3', 'A': '3'})
    openbao.put('users/member01/team-a/projects/web', {'K': '4'})

    listing = env_rows.collect_key_rows(grouped, project='web')

    assert shown(listing) == [
        ('K', 'チーム', '共通', 'team-a', False),
        ('Z', 'チーム', '共通', 'team-a', True),
        ('K', '個人', '共通', 'team-a', False),
        ('A', 'チーム', 'プロジェクト web', 'team-a', True),
        ('K', 'チーム', 'プロジェクト web', 'team-a', False),
        ('K', '個人', 'プロジェクト web', 'team-a', True),
    ]
    assert listing.project == 'web'


def test_the_account_group_key_never_wins(grouped, openbao):
    openbao.put(TEAM, {'DEVBASE_ACCOUNT_GROUP': 'x'})

    listing = env_rows.collect_key_rows(grouped, group='team-a')

    assert shown(listing) == [('DEVBASE_ACCOUNT_GROUP', 'チーム', '共通', 'team-a', False)]


@pytest.mark.parametrize('project, gets', [(None, 2), ('web', 4)])
def test_one_login_and_one_get_per_ref(grouped, openbao, project, gets):
    openbao.put(TEAM, {'A': '1'})

    env_rows.collect_key_rows(grouped, project=project, group=None)

    assert openbao.logins == 1
    assert len(kv_gets(openbao)) == gets
    assert openbao.requests_of('LIST') == []


def test_the_rows_are_read_from_the_server_not_the_cache(grouped, openbao):
    openbao.put(TEAM, {'A': '1'})
    env_rows.collect_key_rows(grouped, group='team-a')
    openbao.stop()

    with pytest.raises(DevbaseError):
        env_rows.collect_key_rows(grouped, group='team-a')


def test_a_forbidden_ref_is_sent_as_a_devbase_error(grouped, openbao):
    openbao.forbidden_prefixes = ['users/']

    with pytest.raises(DevbaseError) as exc:
        env_rows.collect_key_rows(grouped, group='team-a')
    assert '個人のグローバル' in str(exc.value)


def test_a_project_uses_its_own_group(grouped, openbao):
    (grouped / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=team-b\n')

    listing = env_rows.collect_key_rows(grouped, project='web', group='team-a')

    assert listing.group is None
    assert all('team-b' in p for p in kv_gets(openbao))


def test_an_unusable_group_name_is_a_group_option_error(grouped):
    with pytest.raises(GroupOptionError):
        env_rows.collect_key_rows(grouped, group='../x')


def test_group_without_the_grouped_layout_is_refused(openbao_root):
    with pytest.raises(GroupOptionError):
        env_rows.collect_key_rows(openbao_root, group='team-a')


def test_version_one_has_no_group_column(openbao_root, openbao):
    openbao.put('team/global', {'A': '1'})

    listing = env_rows.collect_key_rows(openbao_root)

    assert shown(listing) == [('A', 'チーム', '共通', None, True)]
    assert not listing.grouped and listing.group is None


@pytest.fixture
def file_root(tmp_path, monkeypatch):
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_file_backend_has_no_user_rows(file_root):
    (file_root / '.env').write_text('A=1\n')
    (file_root / 'projects' / 'web' / '.env').write_text('A=2\n')

    listing = env_rows.collect_key_rows(file_root, project='web')

    assert shown(listing) == [('A', 'チーム', '共通', None, False),
                              ('A', 'チーム', 'プロジェクト web', None, True)]
    assert not listing.has_user_refs


def test_group_choices_dedupe_by_storage_group(grouped, openbao):
    from tests.conftest import configure_openbao

    configure_openbao(grouped, openbao, layout='group', group_aliases={'default': 'team-a'})
    (grouped / 'projects' / 'api').mkdir()
    (grouped / 'projects' / 'ops').mkdir()
    (grouped / 'projects' / 'ops' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=team-c\n')
    (grouped / 'env').unlink()

    assert env_rows.group_choices(grouped) == ['default', 'team-c']
