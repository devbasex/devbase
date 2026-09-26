"""`env sync` の書き込み先 (#273 I2・I3・I10、#268)

同期するキーは、対象のグループの個人共通とチーム共通のうち、そのキーが現にある参照へ書く。
両方にあれば個人共通、どちらにも無ければ個人共通 (ファイルの backend ではチーム共通)。
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import pytest

from devbase.commands import env as env_cmd
from devbase.env import agekeys, keys
from devbase.env.sources import SourcesManager, dir_hash, file_hash

TEAM = 'team/team-a/global'
USER = 'users/member01/team-a/global'
USER_LABEL = '個人のグローバル（グループ team-a）'
TEAM_LABEL = 'グローバル（グループ team-a）'


def b64(text: str) -> str:
    return base64.b64encode(text.encode('utf-8')).decode('ascii')


@pytest.fixture
def grouped(openbao_root, openbao):
    """``version: 2``。``$DEVBASE_ROOT/env`` のグループは ``team-a``"""
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout='group')
    (openbao_root / 'env').write_text('DEVBASE_ACCOUNT_GROUP=team-a\n')
    return openbao_root


@pytest.fixture
def home() -> Path:
    path = Path(os.environ['HOME'])
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def host_keys():
    """ホスト接続情報は両方の参照に置き、補完を起こさない"""
    return {keys.HOST_SSH_USER: 'u', keys.HOST_SSH_HOST: 'h'}


def infos(caplog) -> list:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]


def register_git(root: Path, group, path: Path, *, hash_value=None) -> None:
    sources = SourcesManager(root, group)
    sources.load()
    sources.set_source('git_credentials', 'file_base64', ['~/.git-credentials'],
                       keys.GIT_CREDENTIALS_BASE64,
                       file_hash(path) if hash_value is None else hash_value)
    sources.save()


def register_aws(root: Path, group, home: Path) -> None:
    sources = SourcesManager(root, group)
    sources.load()
    sources.set_source('aws', 'tar_base64', ['~/.aws/config', '~/.aws/credentials'],
                       keys.AWS_CONFIG_BASE64, dir_hash(home / '.aws', ['config', 'credentials']))
    sources.save()


# ---------------------------------------------------------------------------
# 宛先 (I2)
# ---------------------------------------------------------------------------

def test_a_key_only_in_the_user_global_updates_the_user_global(grouped, openbao, home,
                                                               host_keys, caplog):
    """#268: AWS_CONFIG_BASE64 が個人共通にだけあれば、個人共通が新しい値になる"""
    (home / '.aws').mkdir()
    (home / '.aws' / 'config').write_text('[default]\nregion = a\n')
    register_aws(grouped, 'team-a', home)
    openbao.put(USER, {keys.AWS_CONFIG_BASE64: 'old', **host_keys})
    openbao.put(TEAM, dict(host_keys))
    (home / '.aws' / 'config').write_text('[default]\nregion = b\n')
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped) == 0

    assert openbao.get(USER)[keys.AWS_CONFIG_BASE64] not in ('old', None)
    assert keys.AWS_CONFIG_BASE64 not in openbao.get(TEAM)
    assert 'AWS認証: 更新しました（個人共通）' in infos(caplog)


def test_a_key_only_in_the_team_global_updates_the_team_global(grouped, openbao, home,
                                                               host_keys, caplog):
    cred = home / '.git-credentials'
    cred.write_text('v1')
    register_git(grouped, 'team-a', cred)
    openbao.put(TEAM, {keys.GIT_CREDENTIALS_BASE64: b64('v1'), **host_keys})
    openbao.put(USER, dict(host_keys))
    cred.write_text('v2')
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped) == 0

    assert openbao.get(TEAM)[keys.GIT_CREDENTIALS_BASE64] == b64('v2')
    assert keys.GIT_CREDENTIALS_BASE64 not in openbao.get(USER)
    assert 'Git認証: 更新しました' in infos(caplog)


def test_a_key_in_both_updates_only_the_user_global(grouped, openbao, home, host_keys):
    cred = home / '.git-credentials'
    cred.write_text('v1')
    register_git(grouped, 'team-a', cred)
    openbao.put(TEAM, {keys.GIT_CREDENTIALS_BASE64: b64('team'), **host_keys})
    openbao.put(USER, {keys.GIT_CREDENTIALS_BASE64: b64('v1'), **host_keys})
    team_version = openbao.version_of(TEAM)
    cred.write_text('v2')

    assert env_cmd.cmd_env_sync(grouped) == 0

    assert openbao.get(USER)[keys.GIT_CREDENTIALS_BASE64] == b64('v2')
    assert openbao.get(TEAM)[keys.GIT_CREDENTIALS_BASE64] == b64('team')
    assert openbao.version_of(TEAM) == team_version


@pytest.mark.parametrize('user', [False, True])
def test_a_key_in_neither_goes_to_the_user_global(grouped, openbao, home, host_keys, user):
    """決定 12: どちらにも無いキーは --user の有無によらず個人共通へ書く"""
    cred = home / '.git-credentials'
    cred.write_text('v1')
    register_git(grouped, 'team-a', cred)
    openbao.put(TEAM, dict(host_keys))
    openbao.put(USER, dict(host_keys))
    cred.write_text('v2')

    assert env_cmd.cmd_env_sync(grouped, user=user) == 0

    assert openbao.get(USER)[keys.GIT_CREDENTIALS_BASE64] == b64('v2')
    assert keys.GIT_CREDENTIALS_BASE64 not in openbao.get(TEAM)


def test_host_keys_missing_everywhere_go_to_the_user_global(grouped, openbao):
    assert env_cmd.cmd_env_sync(grouped) == 0

    assert keys.HOST_SSH_HOST in openbao.get(USER)
    assert openbao.get(TEAM) == {}


def test_user_writes_a_team_only_key_to_the_user_global(grouped, openbao, home, host_keys):
    cred = home / '.git-credentials'
    cred.write_text('v1')
    register_git(grouped, 'team-a', cred)
    openbao.put(TEAM, {keys.GIT_CREDENTIALS_BASE64: b64('v1'), **host_keys})
    openbao.put(USER, dict(host_keys))
    team_version = openbao.version_of(TEAM)
    cred.write_text('v2')

    assert env_cmd.cmd_env_sync(grouped, user=True) == 0

    assert openbao.get(USER)[keys.GIT_CREDENTIALS_BASE64] == b64('v2')
    assert openbao.get(TEAM)[keys.GIT_CREDENTIALS_BASE64] == b64('v1')
    assert openbao.version_of(TEAM) == team_version


# ---------------------------------------------------------------------------
# 同期済みハッシュの控え (前提 5・I10)
# ---------------------------------------------------------------------------

def test_the_aws_source_is_registered_when_the_key_is_only_in_the_user_global(
        grouped, openbao, home, host_keys):
    (home / '.aws').mkdir()
    (home / '.aws' / 'config').write_text('[default]\n')
    openbao.put(USER, {keys.AWS_CONFIG_BASE64: 'old', **host_keys})
    openbao.put(TEAM, dict(host_keys))

    assert env_cmd.cmd_env_sync(grouped) == 0

    sources = SourcesManager(grouped, 'team-a')
    assert sources.get_source('aws')['env_key'] == keys.AWS_CONFIG_BASE64
    # 次の sync は控えのハッシュで「変更なし」と判定できる
    assert sources.check_changed('aws') is False


def test_an_unregistered_source_with_a_key_is_compared_and_updated(grouped, openbao, home,
                                                                   host_keys, caplog):
    cred = home / '.git-credentials'
    cred.write_text('v2')
    openbao.put(USER, {keys.GIT_CREDENTIALS_BASE64: b64('v1'), **host_keys})
    openbao.put(TEAM, dict(host_keys))
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped) == 0

    assert openbao.get(USER)[keys.GIT_CREDENTIALS_BASE64] == b64('v2')
    assert (f'Git認証: ソース未登録（{USER_LABEL}にキーがあります）。'
            '今のファイルと比べて更新しました（個人共通）') in infos(caplog)
    assert SourcesManager(grouped, 'team-a').check_changed('git_credentials') is False


def test_an_unregistered_source_with_the_same_value_reports_no_change(grouped, openbao, home,
                                                                       host_keys, caplog):
    cred = home / '.git-credentials'
    cred.write_text('v1')
    openbao.put(TEAM, {keys.GIT_CREDENTIALS_BASE64: b64('v1'), **host_keys})
    openbao.put(USER, dict(host_keys))
    team_version = openbao.version_of(TEAM)
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped) == 0

    assert openbao.version_of(TEAM) == team_version
    assert (f'Git認証: ソース未登録（{TEAM_LABEL}にキーがあります）。'
            '今のファイルと比べて変更なし') in infos(caplog)
    assert SourcesManager(grouped, 'team-a').get_source('git_credentials') is not None


def test_an_unregistered_aws_source_with_the_same_files_reports_no_change(
        grouped, openbao, home, host_keys, caplog):
    """tar の時刻が違っても、中身が同じなら変更なしと判定する"""
    from devbase.env.collectors.aws import _encode_aws_config_files

    (home / '.aws').mkdir()
    (home / '.aws' / 'config').write_text('[default]\n')
    openbao.put(USER, {keys.AWS_CONFIG_BASE64: _encode_aws_config_files(), **host_keys})
    openbao.put(TEAM, dict(host_keys))
    user_version = openbao.version_of(USER)
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped) == 0

    assert openbao.version_of(USER) == user_version
    assert (f'AWS認証: ソース未登録（{USER_LABEL}にキーがあります）。'
            '今のファイルと比べて変更なし') in infos(caplog)


def test_an_unregistered_source_without_the_file_says_so(grouped, openbao, host_keys, caplog):
    openbao.put(USER, {keys.GIT_CREDENTIALS_BASE64: b64('v1'), **host_keys})
    openbao.put(TEAM, dict(host_keys))
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped) == 0

    assert openbao.get(USER)[keys.GIT_CREDENTIALS_BASE64] == b64('v1')
    assert (f'Git認証: ソース未登録（{USER_LABEL}にキーがあります）。'
            '元のファイルがありません') in infos(caplog)


def test_a_registered_source_without_a_hash_cannot_be_compared(grouped, openbao, home,
                                                               host_keys, caplog):
    cred = home / '.git-credentials'
    cred.write_text('v1')
    register_git(grouped, 'team-a', cred, hash_value='')
    openbao.put(TEAM, {keys.GIT_CREDENTIALS_BASE64: b64('v1'), **host_keys})
    openbao.put(USER, dict(host_keys))
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped) == 0

    lines = infos(caplog)
    assert 'Git認証: 控えと比べられません（ハッシュか元のファイルがありません）' in lines
    assert not any('ソース未登録' in line for line in lines)


# ---------------------------------------------------------------------------
# 読めない・引数の誤り
# ---------------------------------------------------------------------------

def test_an_unreachable_server_writes_nothing(grouped, openbao, home, host_keys):
    cred = home / '.git-credentials'
    cred.write_text('v1')
    register_git(grouped, 'team-a', cred)
    openbao.put(TEAM, dict(host_keys))
    openbao.put(USER, dict(host_keys))
    # 控えを作っておく (fresh で読むため、控えには落ちない)
    assert env_cmd.cmd_env_list(grouped) == 0
    cred.write_text('v2')
    openbao.stop()

    assert env_cmd.cmd_env_sync(grouped) == 1

    assert openbao.writes == 0
    assert SourcesManager(grouped, 'team-a').check_changed('git_credentials') is True


def test_group_uses_the_groups_refs_and_sources(grouped, openbao, home, host_keys):
    cred = home / '.git-credentials'
    cred.write_text('v1')
    register_git(grouped, 'team-b', cred)
    openbao.put('team/team-b/global', {keys.GIT_CREDENTIALS_BASE64: b64('v1'), **host_keys})
    cred.write_text('v2')

    assert env_cmd.cmd_env_sync(grouped, group='team-b') == 0

    assert openbao.get('team/team-b/global')[keys.GIT_CREDENTIALS_BASE64] == b64('v2')
    assert not any(r.kv_path.startswith(('team/team-a', 'users/member01/team-a'))
                   for r in openbao.received if r.kv_path)
    assert (grouped / '.env.sources.team-b.yml').is_file()
    assert not (grouped / '.env.sources.team-a.yml').exists()


def test_group_without_the_grouped_layout_is_a_usage_error(openbao_root, openbao):
    assert env_cmd.cmd_env_sync(openbao_root, group='team-a') == 2
    assert openbao.writes == 0


@pytest.fixture
def file_root(tmp_path, monkeypatch):
    (tmp_path / 'projects').mkdir()
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_user_on_a_file_backend_is_a_usage_error(file_root):
    (file_root / '.env').write_text('A=1\n')

    assert env_cmd.cmd_env_sync(file_root, user=True) == 2

    assert (file_root / '.env').read_text() == 'A=1\n'


def test_a_file_backend_keeps_writing_the_team_global(file_root, home, caplog):
    cred = home / '.git-credentials'
    cred.write_text('v1')
    register_git(file_root, None, cred)
    (file_root / '.env').write_text(f'{keys.GIT_CREDENTIALS_BASE64}={b64("v1")}\n'
                                    'HOST_SSH_USER=u\nHOST_SSH_HOST=h\n')
    cred.write_text('v2')
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(file_root) == 0

    assert f'{keys.GIT_CREDENTIALS_BASE64}={b64("v2")}' in (file_root / '.env').read_text()
    assert 'Git認証: 更新しました' in infos(caplog)
    assert not any('個人共通' in line for line in infos(caplog))
