"""infisical.py: 偽サーバに対する結合テスト (PLAN51 設計 2「Infisical との契約」)"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from devbase.env import runtime
from devbase.env.infisical import (
    InfisicalBackend,
    SecretAuthError,
    SecretUnreachableError,
    SecretWriteError,
)
from devbase.env.secret_store import SecretRef, SecretStore, SecretStoreError


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')
USER_GLOBAL = SecretRef.for_global(owner='user')
USER_WEB = SecretRef.for_project('web', owner='user')

TEAM_GLOBAL_PATH = '/team/global'
TEAM_WEB_PATH = '/team/projects/web'
USER_GLOBAL_PATH = '/users/member01/global'
USER_WEB_PATH = '/users/member01/projects/web'


@pytest.fixture
def store(infisical_root):
    return SecretStore(infisical_root)


# ---------------------------------------------------------------------------
# 選択と読み書き
# ---------------------------------------------------------------------------

def test_store_selects_the_infisical_backend(store):
    backend = store.backend_for(GLOBAL)

    assert isinstance(backend, InfisicalBackend)
    assert backend.name == 'infisical'
    assert backend.direct_edit is False
    assert store.path(GLOBAL) == Path(TEAM_GLOBAL_PATH)


def test_set_then_get_returns_the_same_value(store, infisical):
    store.save(GLOBAL, {'TOKEN': 'abc'})

    assert store.load(GLOBAL) == {'TOKEN': 'abc'}
    assert infisical.get(TEAM_GLOBAL_PATH) == {'TOKEN': 'abc'}
    assert store.mode(GLOBAL) == 'infisical'
    assert store.exists(GLOBAL) is True


def test_empty_reference_is_absent(store):
    assert store.exists(GLOBAL) is False
    assert store.mode(GLOBAL) == 'absent'
    assert store.load(GLOBAL) == {}
    assert store.load_bytes(GLOBAL) == b''


def test_each_reference_maps_to_its_own_secret_path(store, infisical):
    store.save(GLOBAL, {'A': '1'})
    store.save(WEB, {'B': '2'})
    store.save(USER_GLOBAL, {'C': '3'})
    store.save(USER_WEB, {'D': '4'})

    assert infisical.get(TEAM_GLOBAL_PATH) == {'A': '1'}
    assert infisical.get(TEAM_WEB_PATH) == {'B': '2'}
    assert infisical.get(USER_GLOBAL_PATH) == {'C': '3'}
    assert infisical.get(USER_WEB_PATH) == {'D': '4'}
    assert store.load(SecretRef.for_project('api')) == {}


def test_get_sends_no_expansion_and_no_personal_overrides(store, infisical):
    infisical.put(TEAM_GLOBAL_PATH, {'BASE': 'x', 'REF': '${BASE}/y'})

    assert store.load(GLOBAL) == {'BASE': 'x', 'REF': '${BASE}/y'}

    gets = infisical.requests_of('GET')
    assert gets
    for rec in gets:
        assert rec.query['expandSecretReferences'] == 'false'
        assert rec.query['includePersonalOverrides'] == 'false'
        assert rec.query['projectId'] == 'pid'
        assert rec.query['environment'] == 'common'


def test_reference_syntax_survives_a_roundtrip(store, infisical):
    store.save(GLOBAL, {'BASE': 'x', 'REF': '${BASE}/y'})

    assert store.load(GLOBAL)['REF'] == '${BASE}/y'
    assert infisical.get(TEAM_GLOBAL_PATH)['REF'] == '${BASE}/y'


# ---------------------------------------------------------------------------
# 差分適用
# ---------------------------------------------------------------------------

def test_save_sends_only_the_difference(store, infisical):
    infisical.put(TEAM_GLOBAL_PATH, {'SAME': '1', 'CHANGED': 'old', 'GONE': 'x'})

    store.save(GLOBAL, {'SAME': '1', 'CHANGED': 'new', 'ADDED': 'a'})

    names = lambda method: sorted(r.secret_name for r in infisical.requests_of(method)
                                  if r.secret_name)
    assert names('DELETE') == ['GONE']
    assert names('PATCH') == ['CHANGED']
    assert names('POST') == ['ADDED']
    assert infisical.get(TEAM_GLOBAL_PATH) == {'SAME': '1', 'CHANGED': 'new', 'ADDED': 'a'}


def test_save_with_no_change_sends_nothing(store, infisical):
    infisical.put(TEAM_GLOBAL_PATH, {'A': '1'})

    store.save(GLOBAL, {'A': '1'})

    assert not any(r.secret_name for r in infisical.received)


def test_save_bytes_goes_through_the_same_diff(store, infisical):
    infisical.put(TEAM_GLOBAL_PATH, {'A': '1', 'B': '2'})

    store.save_bytes(GLOBAL, b'# comment\nA=1\nC=3\n')

    assert infisical.get(TEAM_GLOBAL_PATH) == {'A': '1', 'C': '3'}
    assert store.load_bytes(GLOBAL) == b'A=1\nC=3\n'


def test_partial_write_failure_stops_and_names_applied_keys(store, infisical, caplog):
    infisical.put(TEAM_GLOBAL_PATH, {'OLD': 'x'})
    infisical.fail_writes_after = 1

    with pytest.raises(SecretWriteError) as exc:
        store.save(GLOBAL, {'A': '1', 'B': '2', 'C': '3'})

    message = str(exc.value)
    # DELETE OLD は成功、その次の POST で失敗する。残りは送らない
    assert 'OLD' in message
    posts = [r.secret_name for r in infisical.requests_of('POST') if r.secret_name]
    assert len(posts) == 1
    assert infisical.get(TEAM_GLOBAL_PATH) == {}
    assert '1' not in message.split('OLD')[-1] or 'A=' not in message
    assert 'x' not in caplog.text


# ---------------------------------------------------------------------------
# 往復回数
# ---------------------------------------------------------------------------

def test_resolve_with_a_project_is_one_login_and_four_gets(store, infisical):
    infisical.put(TEAM_GLOBAL_PATH, {'A': '1'})
    infisical.put(USER_WEB_PATH, {'D': '4'})

    resolved = runtime.resolve(store.root, 'web', store=store)

    assert resolved.values == {'A': '1', 'D': '4'}
    assert infisical.logins == 1
    assert len(infisical.requests_of('GET')) == 4
    assert sorted(r.secret_path for r in infisical.requests_of('GET')) == sorted([
        TEAM_GLOBAL_PATH, TEAM_WEB_PATH, USER_GLOBAL_PATH, USER_WEB_PATH])


def test_resolve_without_a_project_is_one_login_and_two_gets(store, infisical):
    runtime.resolve(store.root, None, store=store)

    assert infisical.logins == 1
    assert sorted(r.secret_path for r in infisical.requests_of('GET')) == sorted([
        TEAM_GLOBAL_PATH, USER_GLOBAL_PATH])


# ---------------------------------------------------------------------------
# 認証
# ---------------------------------------------------------------------------

def test_load_is_fetched_once_per_store_instance(store, infisical):
    """exists → load の並びで同じ参照を 2 度取りに行かない"""
    infisical.put(TEAM_GLOBAL_PATH, {'A': '1'})

    assert store.exists(GLOBAL) is True
    assert store.load(GLOBAL) == {'A': '1'}

    assert len(infisical.requests_of('GET')) == 1


def test_expired_token_is_renewed_once(store, infisical):
    infisical.put(TEAM_GLOBAL_PATH, {'A': '1'})
    backend = store.backend_for(GLOBAL)
    assert backend.fetch(GLOBAL) == {'A': '1'}
    infisical.expire_token()

    assert backend.fetch(GLOBAL) == {'A': '1'}

    assert infisical.logins == 2
    assert len(infisical.requests_of('GET')) == 3     # 1 + (401 → 再取得)


def test_get_401_after_relogin_is_an_auth_error(store, infisical, caplog):
    infisical.put(TEAM_GLOBAL_PATH, {'A': '1'})
    backend = store.backend_for(GLOBAL)
    backend.fetch(GLOBAL)
    infisical.expire_token()
    infisical.reject_login = True

    with pytest.raises(SecretAuthError) as exc:
        backend.fetch(GLOBAL)

    assert infisical.url in str(exc.value)
    assert infisical.logins == 2          # 再認証は 1 度だけ
    assert 's3cret' not in str(exc.value) and 's3cret' not in caplog.text


def test_login_401_is_an_auth_error_without_retry(store, infisical):
    infisical.reject_login = True

    with pytest.raises(SecretAuthError):
        store.load(GLOBAL)

    assert infisical.logins == 1
    assert infisical.requests_of('GET') == []


def test_403_is_not_retried(store, infisical):
    infisical.forbidden_prefixes = ['/users/']

    with pytest.raises(SecretAuthError) as exc:
        store.load(USER_GLOBAL)

    assert infisical.logins == 1
    assert '資格' in str(exc.value)


def test_another_users_path_is_not_readable(infisical_root, infisical, monkeypatch):
    from tests.conftest import configure_infisical

    infisical.put('/users/member01/global', {'MINE': 'x'})
    infisical.forbidden_prefixes = ['/users/member02/']
    configure_infisical(infisical_root, infisical, user='member02')

    with pytest.raises(SecretAuthError):
        SecretStore(infisical_root).load(USER_GLOBAL)


# ---------------------------------------------------------------------------
# 不達
# ---------------------------------------------------------------------------

def test_unreachable_server_is_reported_with_the_url(store, infisical):
    url = infisical.url
    infisical.stop()

    with pytest.raises(SecretUnreachableError) as exc:
        store.load(GLOBAL)
    assert url in str(exc.value)


@pytest.mark.parametrize('status', [500, 502])
def test_server_errors_are_unreachable(store, infisical, status):
    infisical.get_status = status

    with pytest.raises(SecretUnreachableError):
        store.load(GLOBAL)


def test_malformed_json_is_unreachable(store, infisical):
    infisical.get_body = b'{not json'

    with pytest.raises(SecretUnreachableError):
        store.load(GLOBAL)


def test_non_list_secrets_is_unreachable(store, infisical):
    infisical.get_body = b'{"secrets": "nope"}'

    with pytest.raises(SecretUnreachableError):
        store.load(GLOBAL)


def test_write_does_not_touch_the_server_when_the_read_fails(store, infisical):
    infisical.get_status = 500

    with pytest.raises(SecretStoreError):
        store.save(GLOBAL, {'A': '1'})

    assert not any(r.secret_name for r in infisical.received)


# ---------------------------------------------------------------------------
# backend test
# ---------------------------------------------------------------------------

def test_backend_test_reports_url_and_count(infisical_root, infisical, capsys):
    from devbase.commands import env_backend

    infisical.put(TEAM_GLOBAL_PATH, {'A': '1'})

    assert env_backend.cmd_env_backend_test(infisical_root) == 0

    out = capsys.readouterr().out
    assert infisical.url in out
    assert '4' in out    # チーム共通 + 個人共通 + web のチーム/個人


def test_backend_test_fails_when_unreachable(infisical_root, infisical, caplog):
    from devbase.commands import env_backend

    infisical.stop()

    assert env_backend.cmd_env_backend_test(infisical_root) == 1
    assert '到達' in caplog.text


def test_backend_test_fails_without_a_server_backend(tmp_path, caplog):
    from devbase.commands import env_backend

    assert env_backend.cmd_env_backend_test(tmp_path) == 1
    assert 'infisical' in caplog.text


def test_key_names_are_percent_encoded_in_the_url_path(store, infisical):
    """`FOO/BAR` や空白・`?` を含むキーでも別のリソースを叩かない"""
    store.save(GLOBAL, {'FOO/BAR': '1', 'WITH SPACE': '2', 'Q?A#B': '3'})

    posted = sorted(r.secret_name for r in infisical.requests_of('POST') if r.secret_name)
    assert posted == sorted(['FOO%2FBAR', 'WITH%20SPACE', 'Q%3FA%23B'])
    assert infisical.get(TEAM_GLOBAL_PATH) == {'FOO%2FBAR': '1', 'WITH%20SPACE': '2', 'Q%3FA%23B': '3'}
