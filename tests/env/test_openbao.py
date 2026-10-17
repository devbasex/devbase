"""openbao.py: 偽サーバに対する結合テスト (仕様「OpenBao との契約」)"""

from __future__ import annotations

import io
import json
from pathlib import Path
from urllib import request
from urllib.parse import urlsplit

import pytest

from devbase.env import runtime
from devbase.env.openbao import (
    OpenBaoBackend,
    SecretAuthError,
    SecretConflictError,
    SecretRefusedError,
    SecretUnreachableError,
)
from devbase.env.secret_store import SecretRef, SecretStore, SecretStoreError


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')
USER_GLOBAL = SecretRef.for_global(owner='user')
USER_WEB = SecretRef.for_project('web', owner='user')

TEAM_GLOBAL_PATH = 'team/global'
TEAM_WEB_PATH = 'team/projects/web'
USER_GLOBAL_PATH = 'users/member01/global'
USER_WEB_PATH = 'users/member01/projects/web'


def _kv_posts(openbao):
    """ログインの POST を除いた、KV への POST"""
    return [r for r in openbao.requests_of('POST') if r.kv_path]


@pytest.mark.parametrize('body', [
    [], {}, {'auth': None}, {'auth': {}},
    {'auth': {'client_token': 123}}, {'auth': {'client_token': ''}},
], ids=['array', 'missing-auth', 'null-auth', 'missing-token', 'numeric-token', 'empty-token'])
def test_issue_token_recovers_after_an_unreadable_login_response(tmp_path, monkeypatch, body):
    """現状固定: HTTP 成功でも不正な認証応答は拒み、次の発行で再試行する。"""
    from devbase.env import backend_config, bootstrap

    backend_config.save(tmp_path, backend_config.BackendConfig(
        backend='openbao', cache_enabled=False,
        openbao=backend_config.OpenBaoSettings(url='https://openbao.invalid', user='member01'),
    ))
    credentials = bootstrap.Credentials('fake-role-id', 'fake-secret-id')
    monkeypatch.setattr(bootstrap, 'load', lambda root: credentials)

    def urlopen(req, **kwargs):
        response = io.BytesIO(json.dumps(body).encode('utf-8'))
        response.status = 200
        return response

    monkeypatch.setattr(request, 'urlopen', urlopen)
    backend = OpenBaoBackend(SecretStore(tmp_path))

    with pytest.raises(SecretUnreachableError):
        backend.issue_token()

    body = {'auth': {'client_token': 's.fake-recovered-token', 'lease_duration': 3600}}
    assert backend.issue_token() == 's.fake-recovered-token'


@pytest.fixture
def store(openbao_root):
    return SecretStore(openbao_root)


# ---------------------------------------------------------------------------
# 選択と読み書き
# ---------------------------------------------------------------------------

def test_store_selects_the_openbao_backend(store):
    backend = store.backend_for(GLOBAL)

    assert isinstance(backend, OpenBaoBackend)
    assert backend.name == 'openbao'
    assert backend.direct_edit is False
    assert backend.has_user_refs is True
    assert store.path(GLOBAL) == Path('devbase') / TEAM_GLOBAL_PATH


def test_set_then_get_returns_the_same_value(store, openbao):
    store.save(GLOBAL, {'TOKEN': 'abc'})

    assert store.load(GLOBAL) == {'TOKEN': 'abc'}
    assert openbao.get(TEAM_GLOBAL_PATH) == {'TOKEN': 'abc'}
    assert store.mode(GLOBAL) == 'openbao'
    assert store.exists(GLOBAL) is True


def test_empty_reference_is_absent(store):
    assert store.exists(GLOBAL) is False
    assert store.mode(GLOBAL) == 'absent'
    assert store.load(GLOBAL) == {}
    assert store.load_bytes(GLOBAL) == b''


def test_each_reference_maps_to_its_own_path(store, openbao):
    store.save(GLOBAL, {'A': '1'})
    store.save(WEB, {'B': '2'})
    store.save(USER_GLOBAL, {'C': '3'})
    store.save(USER_WEB, {'D': '4'})

    assert openbao.get(TEAM_GLOBAL_PATH) == {'A': '1'}
    assert openbao.get(TEAM_WEB_PATH) == {'B': '2'}
    assert openbao.get(USER_GLOBAL_PATH) == {'C': '3'}
    assert openbao.get(USER_WEB_PATH) == {'D': '4'}
    assert store.load(SecretRef.for_project('api')) == {}


def test_requests_carry_the_token_header_and_the_mount(store, openbao):
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})

    assert store.load(GLOBAL) == {'A': '1'}

    gets = openbao.requests_of('GET')
    assert gets
    for rec in gets:
        assert rec.path == f'/v1/devbase/data/{TEAM_GLOBAL_PATH}'
        assert rec.headers['X-Vault-Token'] == openbao.token
        assert 'Authorization' not in rec.headers


def test_reference_syntax_survives_a_roundtrip(store, openbao):
    store.save(GLOBAL, {'BASE': 'x', 'REF': '${BASE}/y'})

    assert store.load(GLOBAL)['REF'] == '${BASE}/y'
    assert openbao.get(TEAM_GLOBAL_PATH)['REF'] == '${BASE}/y'


def test_non_string_values_are_an_unreadable_response(store, openbao):
    openbao.get_body = b'{"data": {"data": {"A": 1}, "metadata": {"version": 1}}}'

    with pytest.raises(SecretUnreachableError) as exc:
        store.backend_for(GLOBAL).fetch(GLOBAL)
    assert 'A' in str(exc.value)


# ---------------------------------------------------------------------------
# 版を指定した丸ごとの書き込み
# ---------------------------------------------------------------------------

def test_save_sends_the_whole_dictionary_in_one_post_with_the_read_version(store, openbao):
    openbao.put(TEAM_GLOBAL_PATH, {'SAME': '1', 'CHANGED': 'old', 'GONE': 'x'})
    assert store.load(GLOBAL)['CHANGED'] == 'old'

    store.save(GLOBAL, {'SAME': '1', 'CHANGED': 'new', 'ADDED': 'a'})

    posts = _kv_posts(openbao)
    assert len(posts) == 1
    assert posts[0].cas == 1
    assert posts[0].body['data'] == {'SAME': '1', 'CHANGED': 'new', 'ADDED': 'a'}
    assert openbao.get(TEAM_GLOBAL_PATH) == {'SAME': '1', 'CHANGED': 'new', 'ADDED': 'a'}
    assert openbao.version_of(TEAM_GLOBAL_PATH) == 2


def test_save_without_a_prior_read_fetches_the_version_first(store, openbao):
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    openbao.put(TEAM_GLOBAL_PATH, {'A': '2'})

    store.save(GLOBAL, {'A': '3'})

    assert len(openbao.requests_of('GET')) == 1
    post = _kv_posts(openbao)[0]
    assert post.cas == 2


def test_save_to_an_unwritten_path_uses_version_zero(store, openbao):
    store.save(GLOBAL, {'A': '1'})

    post = _kv_posts(openbao)[0]
    assert post.cas == 0
    assert openbao.version_of(TEAM_GLOBAL_PATH) == 1


def test_save_after_a_soft_delete_uses_the_current_version_not_zero(store, openbao):
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    openbao.put(TEAM_GLOBAL_PATH, {'A': '2'})
    openbao.soft_delete(TEAM_GLOBAL_PATH)

    assert store.load(GLOBAL) == {}
    store.save(GLOBAL, {'B': '1'})

    post = _kv_posts(openbao)[0]
    assert post.cas == 2
    assert openbao.get(TEAM_GLOBAL_PATH) == {'B': '1'}


def test_a_concurrent_write_between_read_and_save_is_a_conflict(store, openbao):
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    assert store.load(GLOBAL) == {'A': '1'}
    openbao.put(TEAM_GLOBAL_PATH, {'A': 'theirs'})     # 他の利用者が先に書いた

    with pytest.raises(SecretConflictError) as exc:
        store.save(GLOBAL, {'A': 'mine'})

    assert '先に' in str(exc.value) or '書き換え' in str(exc.value)
    assert openbao.get(TEAM_GLOBAL_PATH) == {'A': 'theirs'}    # 黙って上書きしない
    assert 'mine' not in str(exc.value) and 'theirs' not in str(exc.value)


def test_a_retry_after_a_conflict_reads_the_new_version_and_succeeds(store, openbao):
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    store.load(GLOBAL)
    openbao.put(TEAM_GLOBAL_PATH, {'A': 'theirs'})
    with pytest.raises(SecretConflictError):
        store.save(GLOBAL, {'A': 'mine'})

    fresh = SecretStore(store.root)
    fresh.load(GLOBAL)
    fresh.save(GLOBAL, {'A': 'mine'})

    assert openbao.get(TEAM_GLOBAL_PATH) == {'A': 'mine'}


def test_a_second_save_in_the_same_store_uses_the_version_returned_by_the_first(store, openbao):
    """保存の応答の data.version が次の基準になる (import の巻き戻しが通る)"""
    store.save(GLOBAL, {'A': '1'})
    store.save(GLOBAL, {'A': '2'})

    posts = _kv_posts(openbao)
    assert [p.cas for p in posts] == [0, 1]
    assert openbao.get(TEAM_GLOBAL_PATH) == {'A': '2'}
    assert len(openbao.requests_of('GET')) == 1     # 1 度目の保存前だけ


def test_save_bytes_goes_through_the_same_write(store, openbao):
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1', 'B': '2'})

    store.save_bytes(GLOBAL, b'# comment\nA=1\nC=3\n')

    assert openbao.get(TEAM_GLOBAL_PATH) == {'A': '1', 'C': '3'}
    assert store.load_bytes(GLOBAL) == b'A=1\nC=3\n'


def test_remove_deletes_all_versions(store, openbao):
    openbao.put(USER_GLOBAL_PATH, {'A': '1'})
    backend = store.backend_for(USER_GLOBAL)

    assert backend.remove(USER_GLOBAL) is True
    assert backend.remove(USER_GLOBAL) is False

    deletes = openbao.requests_of('DELETE')
    assert len(deletes) == 1
    assert deletes[0].path == f'/v1/devbase/metadata/{USER_GLOBAL_PATH}'
    assert openbao.version_of(USER_GLOBAL_PATH) == 0


def test_path_segments_are_percent_encoded_in_the_url(openbao_root, openbao):
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, user='member 01')
    store = SecretStore(openbao_root)

    store.save(USER_GLOBAL, {'A': '1'})

    post = _kv_posts(openbao)[0]
    assert '%20' in post.path
    assert post.kv_path == 'users/member 01/global'


# ---------------------------------------------------------------------------
# 往復回数
# ---------------------------------------------------------------------------

def test_resolve_with_a_project_is_one_login_and_four_gets(store, openbao):
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    openbao.put(USER_WEB_PATH, {'D': '4'})

    resolved = runtime.resolve(store.root, 'web', store=store)

    assert resolved.values == {'A': '1', 'D': '4'}
    assert openbao.logins == 1
    assert len(openbao.requests_of('GET')) == 4
    assert sorted(r.kv_path for r in openbao.requests_of('GET')) == sorted([
        TEAM_GLOBAL_PATH, TEAM_WEB_PATH, USER_GLOBAL_PATH, USER_WEB_PATH])


def test_resolve_without_a_project_is_one_login_and_two_gets(store, openbao):
    runtime.resolve(store.root, None, store=store)

    assert openbao.logins == 1
    assert sorted(r.kv_path for r in openbao.requests_of('GET')) == sorted([
        TEAM_GLOBAL_PATH, USER_GLOBAL_PATH])


def test_load_is_fetched_once_per_store_instance(store, openbao):
    """exists → load の並びで同じ参照を 2 度取りに行かない"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})

    assert store.exists(GLOBAL) is True
    assert store.load(GLOBAL) == {'A': '1'}

    assert len(openbao.requests_of('GET')) == 1


# ---------------------------------------------------------------------------
# 認証と権限
# ---------------------------------------------------------------------------

def test_login_400_is_an_auth_error_without_retry(store, openbao, caplog):
    openbao.reject_login = True

    with pytest.raises(SecretAuthError) as exc:
        store.load(GLOBAL)

    assert openbao.logins == 1
    assert openbao.requests_of('GET') == []
    message = str(exc.value)
    assert openbao.url in message and 'secret_id' in message
    assert 's3cret' not in message and 's3cret' not in caplog.text


def test_login_403_is_an_auth_error(store, openbao):
    openbao.disable_entity = True

    with pytest.raises(SecretAuthError):
        store.load(GLOBAL)
    assert openbao.requests_of('GET') == []


def test_get_403_names_the_read_permission_and_the_user_setting(store, openbao):
    openbao.forbidden_prefixes = ['users/']

    with pytest.raises(SecretAuthError) as exc:
        store.load(USER_GLOBAL)

    message = str(exc.value)
    assert openbao.logins == 1
    assert '読む権限' in message and 'openbao.user' in message and 'member01' in message
    assert '書き込み権限' not in message


def test_write_403_names_the_write_permission_not_the_credentials(store, openbao):
    openbao.team_writable = False
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})

    with pytest.raises(SecretAuthError) as exc:
        store.save(GLOBAL, {'A': '2'})

    message = str(exc.value)
    assert '書き込み権限' in message
    assert '資格' not in message and '読む権限' not in message
    assert openbao.get(TEAM_GLOBAL_PATH) == {'A': '1'}


@pytest.mark.parametrize('status', [400, 422, 429])
def test_other_4xx_on_write_is_a_refusal_not_an_outage(store, openbao, status):
    """サーバが拒んだと確定した応答。巻き戻しの対象から外れ、控えは残る"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    store.load(GLOBAL)
    openbao.write_status = status

    with pytest.raises(SecretRefusedError) as exc:
        store.save(GLOBAL, {'A': '2'})

    assert not isinstance(exc.value, (SecretAuthError, SecretConflictError))
    assert not isinstance(exc.value, SecretUnreachableError)
    assert '拒み' in str(exc.value) and str(status) in str(exc.value)
    assert openbao.get(TEAM_GLOBAL_PATH) == {'A': '1'}


def _write_fails_with(monkeypatch, status, body):
    """KV への POST だけを ``status`` と本文 ``body`` の失敗応答にする"""
    from urllib import error

    real = request.urlopen

    def urlopen(req, *args, **kwargs):
        if req.get_method() == 'POST' and '/data/' in urlsplit(req.full_url).path:
            raise error.HTTPError(req.full_url, status, 'error', {}, io.BytesIO(body))
        return real(req, *args, **kwargs)

    monkeypatch.setattr(request, 'urlopen', urlopen)


@pytest.mark.parametrize('body', [
    b'', b'{not json', b'\xff\xfe', b'[]', b'{"errors": "check-and-set"}',
], ids=['empty', 'malformed', 'undecodable', 'array', 'errors-not-a-list'])
def test_write_400_without_readable_errors_is_a_refusal(store, openbao, monkeypatch, body):
    """現状固定: 400 の本文から errors を読めなければ版の不一致ではなく拒否。"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    store.load(GLOBAL)
    _write_fails_with(monkeypatch, 400, body)

    with pytest.raises(SecretRefusedError) as exc:
        store.save(GLOBAL, {'A': '2'})

    assert not isinstance(exc.value, (SecretAuthError, SecretConflictError))


def test_write_400_errors_are_read_as_strings(store, openbao, monkeypatch):
    """現状固定: errors の要素は文字列にしてから版の不一致の語を探す。"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    store.load(GLOBAL)
    _write_fails_with(monkeypatch, 400, b'{"errors": [1, "check-and-set mismatch"]}')

    with pytest.raises(SecretConflictError):
        store.save(GLOBAL, {'A': '2'})


def test_an_expiring_token_is_renewed_before_the_request(store, openbao, monkeypatch):
    import time

    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    backend = store.backend_for(GLOBAL)
    assert backend.fetch(GLOBAL) == {'A': '1'}
    base = time.monotonic()
    monkeypatch.setattr(time, 'monotonic', lambda: base + 3600.0)

    assert backend.fetch(GLOBAL) == {'A': '1'}

    assert openbao.logins == 2
    assert len(openbao.requests_of('GET')) == 2


def test_another_users_path_is_not_readable(openbao_root, openbao):
    from tests.conftest import configure_openbao

    openbao.put('users/member01/global', {'MINE': 'x'})
    openbao.forbidden_prefixes = ['users/member02/']
    configure_openbao(openbao_root, openbao, user='member02')

    with pytest.raises(SecretAuthError):
        SecretStore(openbao_root).load(USER_GLOBAL)


# ---------------------------------------------------------------------------
# 不達
# ---------------------------------------------------------------------------

def test_unreachable_server_is_reported_with_the_url(store, openbao):
    url = openbao.url
    openbao.stop()

    with pytest.raises(SecretUnreachableError) as exc:
        store.load(GLOBAL)
    assert url in str(exc.value)


@pytest.mark.parametrize('status', [500, 502])
def test_server_errors_are_unreachable(store, openbao, status):
    openbao.get_status = status

    with pytest.raises(SecretUnreachableError):
        store.load(GLOBAL)


def test_malformed_json_is_unreachable(store, openbao):
    openbao.get_body = b'{not json'

    with pytest.raises(SecretUnreachableError):
        store.load(GLOBAL)


def test_non_dict_data_is_unreachable(store, openbao):
    openbao.get_body = b'{"data": {"data": "nope", "metadata": {"version": 1}}}'

    with pytest.raises(SecretUnreachableError):
        store.load(GLOBAL)


def test_write_does_not_touch_the_server_when_the_read_fails(store, openbao):
    openbao.get_status = 500

    with pytest.raises(SecretStoreError):
        store.save(GLOBAL, {'A': '1'})

    assert not _kv_posts(openbao)


# ---------------------------------------------------------------------------
# fetch の分岐 (現状固定)
# ---------------------------------------------------------------------------

def _cached_after_outage(openbao_root, openbao, ref):
    """サーバを落とし、新しい SecretStore がキャッシュから読む内容"""
    openbao.stop()
    return SecretStore(openbao_root).load(ref)


def test_fetch_returns_the_secrets_and_replaces_the_cache(openbao_root, store, openbao):
    """現状固定: 成功した fetch は値を返し、キャッシュをその内容で置き換える。"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    backend = store.backend_for(GLOBAL)

    assert backend.fetch(GLOBAL) == {'A': '1'}
    assert _cached_after_outage(openbao_root, openbao, GLOBAL) == {'A': '1'}


def test_fetch_reads_the_server_again_even_after_a_load(store, openbao):
    """現状固定: fetch は控え (_seen) を使わず、毎回 GET する。"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    backend = store.backend_for(GLOBAL)
    assert backend.load(GLOBAL) == {'A': '1'}
    openbao.put(TEAM_GLOBAL_PATH, {'A': '2'})

    assert backend.fetch(GLOBAL) == {'A': '2'}
    assert backend.load(GLOBAL) == {'A': '2'}
    assert len([r for r in openbao.requests_of('GET') if r.kv_path]) == 2


def test_fetch_refreshes_the_version_used_by_the_next_save(store, openbao):
    """現状固定: fetch で更新した版が次の書き込みの CAS になる。"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    backend = store.backend_for(GLOBAL)
    backend.fetch(GLOBAL)
    openbao.put(TEAM_GLOBAL_PATH, {'A': '2'})
    backend.fetch(GLOBAL)

    store.save(GLOBAL, {'A': '3'})

    post = _kv_posts(openbao)[0]
    assert post.cas == 2


def test_fetch_of_an_unwritten_path_is_empty_and_caches_empty(openbao_root, store, openbao):
    """現状固定: 404 (未作成) は空を返し、キャッシュも空で置き換える。"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    backend = store.backend_for(GLOBAL)
    assert backend.fetch(GLOBAL) == {'A': '1'}
    openbao.get_status = 404                # 消えた (版の無い 404)

    assert backend.fetch(GLOBAL) == {}
    openbao.get_status = None
    assert _cached_after_outage(openbao_root, openbao, GLOBAL) == {}


def test_fetch_of_a_soft_deleted_path_keeps_the_current_version(store, openbao):
    """現状固定: 論理削除の 404 は空を返し、本文の版を基準にする。"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    openbao.soft_delete(TEAM_GLOBAL_PATH)

    assert store.backend_for(GLOBAL).fetch(GLOBAL) == {}
    store.save(GLOBAL, {'B': '1'})

    post = _kv_posts(openbao)[0]
    assert post.cas == 1


@pytest.mark.parametrize('body', [
    b'', b'{not json', b'\xff\xfe', b'[]', b'{"errors": []}',
    b'{"data": {"metadata": {"version": "3"}}}',
    b'{"data": {"metadata": {"version": true}}}',
], ids=['empty', 'malformed', 'undecodable', 'array', 'no-data', 'string-version',
        'bool-version'])
def test_fetch_404_without_a_usable_version_uses_version_zero(store, openbao, body):
    """現状固定: 404 の本文から版を読めなければ空を返し、版 0 を基準にする。"""
    openbao.get_status = 404
    openbao.get_body = body

    assert store.backend_for(GLOBAL).fetch(GLOBAL) == {}
    openbao.get_status = None
    openbao.get_body = None
    store.save(GLOBAL, {'A': '1'})

    post = _kv_posts(openbao)[0]
    assert post.cas == 0


def test_fetch_403_is_an_auth_error_with_the_user_hint(store, openbao):
    """現状固定: 403 は読む権限の SecretAuthError。パスと openbao.user の手掛かりを含む。"""
    openbao.forbidden_prefixes = ['team/']

    with pytest.raises(SecretAuthError) as exc:
        store.backend_for(GLOBAL).fetch(GLOBAL)

    message = str(exc.value)
    assert message.startswith('OpenBao でこの参照を読む権限がありません')
    assert 'HTTP 403' in message
    assert f'接続先: {openbao.url}' in message
    assert 'パス: ' in message
    assert 'backend.yml の openbao.user (現在: member01) が本人の識別子と違う可能性があります' \
        in message
    assert exc.value.__cause__ is None and exc.value.__suppress_context__ is True


@pytest.mark.parametrize('status', [400, 401, 405, 500, 503])
def test_fetch_other_statuses_are_unreachable_with_the_status(store, openbao, status):
    """現状固定: 404・403 以外の失敗応答は HTTP の番号付きの SecretUnreachableError。"""
    openbao.get_status = status

    with pytest.raises(SecretUnreachableError) as exc:
        store.backend_for(GLOBAL).fetch(GLOBAL)

    message = str(exc.value)
    assert message.startswith('OpenBao へ到達できません (')
    assert f'HTTP {status})' in message
    assert f'接続先: {openbao.url}' in message
    assert exc.value.__suppress_context__ is True


def test_fetch_failure_does_not_touch_the_basis_or_the_cache(openbao_root, store, openbao):
    """現状固定: 失敗した fetch は前の基準とキャッシュを残す。"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    backend = store.backend_for(GLOBAL)
    backend.fetch(GLOBAL)
    openbao.get_status = 500

    with pytest.raises(SecretUnreachableError):
        backend.fetch(GLOBAL)

    assert backend.load(GLOBAL) == {'A': '1'}
    assert _cached_after_outage(openbao_root, openbao, GLOBAL) == {'A': '1'}


@pytest.mark.parametrize('metadata', [
    '', ', "metadata": {}', ', "metadata": {"version": "1"}', ', "metadata": {"version": true}',
    ', "metadata": []',
], ids=['no-metadata', 'no-version', 'string-version', 'bool-version', 'array-metadata'])
def test_fetch_without_a_version_is_unreadable(store, openbao, metadata):
    """現状固定: 200 で data.metadata.version (整数) が無ければ解釈できない応答。"""
    openbao.get_body = ('{"data": {"data": {"A": "1"}%s}}' % metadata).encode()

    with pytest.raises(SecretUnreachableError) as exc:
        store.backend_for(GLOBAL).fetch(GLOBAL)

    message = str(exc.value)
    assert message.startswith('OpenBao の応答を解釈できません (')
    assert 'data.metadata.version がありません' in message


def test_fetch_checks_the_secrets_before_the_version(store, openbao):
    """現状固定: data.data と版の両方が不正なら、data.data の不正を先に報じる。"""
    openbao.get_body = b'{"data": {"data": "nope"}}'

    with pytest.raises(SecretUnreachableError) as exc:
        store.backend_for(GLOBAL).fetch(GLOBAL)

    assert 'data.data が辞書ではありません' in str(exc.value)


# ---------------------------------------------------------------------------
# backend test
# ---------------------------------------------------------------------------

def test_backend_test_reports_url_and_count(openbao_root, openbao, capsys):
    from devbase.commands import env_backend

    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})

    assert env_backend.cmd_env_backend_test(openbao_root) == 0

    out = capsys.readouterr().out
    assert openbao.url in out
    assert '4' in out    # チーム共通 + 個人共通 + web のチーム/個人
    assert 'devbase/team/global' in out


def test_backend_test_fails_when_unreachable(openbao_root, openbao, caplog):
    from devbase.commands import env_backend

    openbao.stop()

    assert env_backend.cmd_env_backend_test(openbao_root) == 1
    assert '到達' in caplog.text


def test_backend_test_fails_without_a_server_backend(tmp_path, caplog):
    from devbase.commands import env_backend

    assert env_backend.cmd_env_backend_test(tmp_path) == 1
    assert 'openbao' in caplog.text


# ---------------------------------------------------------------------------
# token の発行 (PLAN54: コンテナの ~/.vault-token へ届ける)
# ---------------------------------------------------------------------------

def test_issue_token_returns_the_login_token(store, openbao):
    backend = store.backend_for(GLOBAL)

    token = backend.issue_token()

    assert token == openbao.token
    assert openbao.logins == 1


def test_issue_token_reuses_the_token_obtained_for_reads(store, openbao):
    """注入で読んだ同じ SecretStore からなら、ログインし直さない (up の往復を増やさない)"""
    store.load(GLOBAL)
    backend = store.backend_for(GLOBAL)

    assert backend.issue_token() == openbao.token
    assert openbao.logins == 1


def test_issue_token_logs_in_again_near_expiry(store, openbao, monkeypatch):
    backend = store.backend_for(GLOBAL)
    backend.issue_token()
    monkeypatch.setattr(backend, '_token_expires_at', 0.0)

    backend.issue_token()

    assert openbao.logins == 2


def test_issue_token_raises_auth_error_on_rejected_login(store, openbao):
    openbao.reject_login = True

    with pytest.raises(SecretAuthError):
        store.backend_for(GLOBAL).issue_token()


# ---------------------------------------------------------------------------
# グループ別の置き場 (PLAN56)
# ---------------------------------------------------------------------------

def test_group_layout_refuses_a_reference_without_a_group(openbao_root, openbao):
    """呼び出しの誤りを従来のパスへ落とさない。サーバへ要求もしない"""
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout='group')
    openbao.put(TEAM_GLOBAL_PATH, {'A': 'flat'})
    store = SecretStore(openbao_root)

    for call in (lambda: store.load(GLOBAL), lambda: store.fetch(WEB),
                 lambda: store.save(USER_GLOBAL, {'A': '1'})):
        with pytest.raises(SecretStoreError) as exc:
            call()
        assert not isinstance(exc.value, SecretUnreachableError)
        assert 'グループ' in str(exc.value)
    assert openbao.received == []


def test_flat_layout_refuses_a_reference_with_a_group(store, openbao):
    """``version: 1`` の参照は常にグループを持たない (決定 5)。持っていれば黙って無視しない"""
    with pytest.raises(SecretStoreError):
        store.load(SecretRef.for_global(group='initech'))
    assert openbao.received == []


def test_group_layout_reads_and_writes_the_group_paths(openbao_root, openbao):
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout='group', group_aliases={'umbrella': 'acme'})
    store = SecretStore(openbao_root)

    store.save(SecretRef.for_project('web', group='initech'), {'A': '1'})
    store.save(SecretRef.for_global(owner='user', group='umbrella'), {'B': '2'})

    assert openbao.get('team/initech/projects/web') == {'A': '1'}
    assert openbao.get('users/member01/acme/global') == {'B': '2'}
    assert {r.kv_path for r in openbao.received if r.kv_path} == {
        'team/initech/projects/web', 'users/member01/acme/global'}


# ---------------------------------------------------------------------------
# TLS の検証 (#497)。OpenBao への HTTP は OS の信頼ストアで検証する
# ---------------------------------------------------------------------------

@pytest.fixture
def recorded_urlopen(monkeypatch):
    """``urlopen`` を包み、経路と ``context`` 引数を控えてから本物へ渡す"""
    from devbase.env import openbao as openbao_module

    openbao_module._tls_context.cache_clear()
    calls = []
    real = request.urlopen

    def urlopen(req, *args, **kwargs):
        calls.append((req.get_method(), urlsplit(req.full_url).path, kwargs.get('context')))
        return real(req, *args, **kwargs)

    monkeypatch.setattr(request, 'urlopen', urlopen)
    yield calls
    openbao_module._tls_context.cache_clear()


def _exercise_every_route(store):
    """認証・取得・保存・削除の 4 経路をすべて通す"""
    store.save(GLOBAL, {'TOKEN': 'abc'})
    store.backend_for(GLOBAL).remove(GLOBAL)


def test_every_openbao_route_uses_the_truststore_context(store, openbao, recorded_urlopen):
    import truststore

    _exercise_every_route(store)

    routes = {(method, path.split('/')[2] if path != '/v1/auth/approle/login' else 'login')
              for method, path, _ in recorded_urlopen}
    assert routes == {('POST', 'login'), ('GET', 'devbase'), ('POST', 'devbase'),
                      ('DELETE', 'devbase')}
    contexts = {id(context) for _, _, context in recorded_urlopen}
    assert len(contexts) == 1
    context = recorded_urlopen[0][2]
    assert isinstance(context, truststore.SSLContext)
    # 検証は緩めない
    import ssl
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_truststore_is_not_injected_into_the_process(store, openbao, recorded_urlopen):
    """boto3 など他の通信の検証を変えない (``inject_into_ssl`` を使わない)"""
    import ssl

    original = ssl.SSLContext
    _exercise_every_route(store)

    assert ssl.SSLContext is original
    assert ssl.create_default_context().__class__ is original


def test_missing_truststore_falls_back_to_the_default_context(store, openbao,
                                                              recorded_urlopen, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, 'truststore', None)  # import が ImportError になる

    _exercise_every_route(store)

    assert recorded_urlopen
    assert all(context is None for _, _, context in recorded_urlopen)
    assert store.load(GLOBAL) == {}


def test_certificate_verification_failure_is_unreachable(store, monkeypatch):
    """証明書の検証の失敗は、これまでどおり到達できない失敗として扱う"""
    import ssl
    from urllib import error

    def urlopen(req, *args, **kwargs):
        raise error.URLError(ssl.SSLCertVerificationError(
            1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed'))

    monkeypatch.setattr(request, 'urlopen', urlopen)

    with pytest.raises(SecretUnreachableError) as exc:
        store.load(GLOBAL)
    message = str(exc.value)
    assert message.startswith('OpenBao へ到達できません (')
    assert 'CERTIFICATE_VERIFY_FAILED' in message
    assert '\n  接続先: ' in message


def _refers_to_urlopen(function) -> bool:
    """関数の本体 (入れ子の関数を含む) が ``urlopen`` を属性か名前で参照するか"""
    import ast

    for inner in ast.walk(function):
        if isinstance(inner, ast.Attribute) and inner.attr == 'urlopen':
            return True
        if isinstance(inner, ast.Name) and inner.id == 'urlopen':
            return True
    return False


def test_refers_to_urlopen_sees_attributes_names_and_nested_functions():
    import ast

    def function_of(source):
        return ast.parse(source).body[0]

    assert _refers_to_urlopen(function_of('def f():\n    request.urlopen(x)\n'))
    assert _refers_to_urlopen(function_of('def f():\n    urlopen(x)\n'))
    assert _refers_to_urlopen(function_of('def f():\n    def g():\n        urlopen(x)\n'))
    assert not _refers_to_urlopen(function_of('def f():\n    request.urlopen_later(x)\n'))


def test_urlopen_is_called_only_from_the_openbao_http_helper():
    """``urlopen`` を呼ぶ経路は ``OpenBaoBackend._http`` だけ (増えたら同じ context を使わせる)"""
    import ast

    lib = Path(__file__).resolve().parents[2] / 'lib' / 'devbase'
    callers = []
    for source in sorted(lib.rglob('*.py')):
        tree = ast.parse(source.read_text(encoding='utf-8'))
        callers += [(source.relative_to(lib).as_posix(), node.name)
                    for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and _refers_to_urlopen(node)]
    assert sorted(set(callers)) == [('env/openbao.py', '_http')]
