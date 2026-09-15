"""cache.py: サーバ不達時の控え (PLAN51 設計 1「キャッシュ」)"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat

import pytest

from devbase.env import agekeys, cache, runtime
from devbase.env import backend_config as bc
from devbase.env.openbao import SecretAuthError, SecretConflictError, SecretUnreachableError
from devbase.env.secret_store import SecretRef, SecretStore, SecretStoreError


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')
USER_GLOBAL = SecretRef.for_global(owner='user')
USER_WEB = SecretRef.for_project('web', owner='user')
TEAM_GLOBAL_PATH = 'team/global'


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fill(openbao, store):
    """4 参照すべてに 1 件ずつ入れて取得し、控えを作る"""
    openbao.put('team/global', {'A': 'team-global'})
    openbao.put('team/projects/web', {'B': 'team-web'})
    openbao.put('users/member01/global', {'C': 'user-global'})
    openbao.put('users/member01/projects/web', {'D': 'user-web'})
    return runtime.resolve(store.root, 'web', store=store)


# ---------------------------------------------------------------------------
# 配置と形式
# ---------------------------------------------------------------------------

def test_each_reference_has_its_own_encrypted_file(openbao_root, openbao):
    store = SecretStore(openbao_root)
    fill(openbao, store)

    files = cache.cached_files(openbao_root)
    base = openbao_root / 'secrets' / 'cache'
    assert files == sorted([
        base / 'team' / 'global.env.age', base / 'team' / 'projects' / 'web.env.age',
        base / 'user' / 'global.env.age', base / 'user' / 'projects' / 'web.env.age'])
    for path in files:
        assert path.read_bytes().startswith(b'age-encryption.org/')
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert b'team-global' not in path.read_bytes()
    assert stat.S_IMODE(base.stat().st_mode) == 0o700


def test_index_has_no_keys_or_values(openbao_root, openbao):
    store = SecretStore(openbao_root)
    fill(openbao, store)

    raw = (openbao_root / 'secrets' / 'cache' / 'index.json').read_text()
    assert 'team-global' not in raw and '"A"' not in raw
    entries = cache.read_index(openbao_root)
    assert set(entries) == {'team:global', 'team:project:web', 'user:global', 'user:project:web'}
    assert entries['team:global']['url_host'] == '127.0.0.1'
    assert entries['team:global']['fetched_at']


# ---------------------------------------------------------------------------
# 不達
# ---------------------------------------------------------------------------

def test_unreachable_with_a_cache_resolves_and_warns(openbao_root, openbao, caplog):
    fill(openbao, SecretStore(openbao_root))
    fetched_at = cache.read_index(openbao_root)['team:global']['fetched_at']
    openbao.stop()

    with caplog.at_level(logging.WARNING):
        resolved = runtime.resolve(openbao_root, 'web', store=SecretStore(openbao_root))

    assert resolved.values == {'A': 'team-global', 'B': 'team-web',
                               'C': 'user-global', 'D': 'user-web'}
    assert 'キャッシュ' in caplog.text and fetched_at in caplog.text
    assert 'team-global' not in caplog.text


def test_unreachable_without_a_cache_fails_with_the_url(openbao_root, openbao):
    url = openbao.url
    openbao.stop()

    with pytest.raises(SecretUnreachableError) as exc:
        runtime.resolve(openbao_root, 'web', store=SecretStore(openbao_root))
    assert url in str(exc.value)


@pytest.mark.parametrize('how', ['down', '500', 'bad-json'])
def test_failed_fetch_leaves_the_cache_untouched(openbao_root, openbao, how):
    fill(openbao, SecretStore(openbao_root))
    path = cache.entry_path(openbao_root, GLOBAL)
    before = digest(path)

    if how == 'down':
        openbao.stop()
    elif how == '500':
        openbao.get_status = 500
    else:
        openbao.get_body = b'{oops'

    SecretStore(openbao_root).load(GLOBAL)   # キャッシュから返る

    assert digest(path) == before


def test_truncated_response_falls_back_to_the_cache(openbao_root, openbao):
    """本文が Content-Length より短く切れた応答 (IncompleteRead) は不達として扱う"""
    fill(openbao, SecretStore(openbao_root))
    openbao.truncate_get_body = True

    assert SecretStore(openbao_root).load(GLOBAL) == {'A': 'team-global'}


def test_an_empty_answer_replaces_the_cache(openbao_root, openbao):
    fill(openbao, SecretStore(openbao_root))
    openbao.put('team/global', {})

    assert SecretStore(openbao_root).load(GLOBAL) == {}
    openbao.stop()

    assert SecretStore(openbao_root).load(GLOBAL) == {}


def test_a_404_replaces_the_cache_with_an_empty_generation(openbao_root, openbao):
    """WebUI で参照ごと消した (論理削除) 後の 404 は機密 0 件として控えを置き換える"""
    fill(openbao, SecretStore(openbao_root))
    openbao.soft_delete('team/global')

    assert SecretStore(openbao_root).load(GLOBAL) == {}
    openbao.stop()

    assert SecretStore(openbao_root).load(GLOBAL) == {}


# ---------------------------------------------------------------------------
# 認証拒否
# ---------------------------------------------------------------------------

def test_auth_rejection_does_not_use_or_change_the_cache(openbao_root, openbao,
                                                          monkeypatch):
    fill(openbao, SecretStore(openbao_root))
    path = cache.entry_path(openbao_root, GLOBAL)
    before = digest(path)
    openbao.reject_login = True
    openbao.expire_token()
    reads = []
    original = cache.SecretCache.read
    monkeypatch.setattr(cache.SecretCache, 'read',
                        lambda self, ref, backend: reads.append(ref) or original(self, ref, backend))

    with pytest.raises(SecretAuthError):
        runtime.resolve(openbao_root, 'web', store=SecretStore(openbao_root))

    assert reads == []
    assert digest(path) == before

    # 資格が戻った後の不達では、そのキャッシュがこれまでどおり使われる
    openbao.reject_login = False
    openbao.stop()
    assert SecretStore(openbao_root).load(GLOBAL) == {'A': 'team-global'}


def test_forbidden_reference_does_not_use_the_cache(openbao_root, openbao):
    fill(openbao, SecretStore(openbao_root))
    openbao.forbidden_prefixes = ['users/']

    with pytest.raises(SecretAuthError):
        SecretStore(openbao_root).load(USER_GLOBAL)


# ---------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('change', ['url', 'mount', 'path', 'user'])
def test_changed_settings_invalidate_the_cache(openbao_root, openbao, change):
    fill(openbao, SecretStore(openbao_root))
    ob = bc.load(openbao_root).openbao
    if change == 'url':
        # 同じホストの別ポート (= 別の接続先)。到達はできない
        new = bc.OpenBaoSettings(**{**ob.to_dict(), 'url': 'http://127.0.0.1:1'})
    elif change == 'mount':
        new = bc.OpenBaoSettings(**{**ob.to_dict(), 'mount': 'other'})
    elif change == 'path':
        new = bc.OpenBaoSettings(**{**ob.to_dict(), 'path_team_global': 'shared/global'})
    else:
        new = bc.OpenBaoSettings(**{**ob.to_dict(), 'user': 'member02'})
    bc.save(openbao_root, bc.BackendConfig(backend='openbao', openbao=new))
    openbao.stop()

    ref = USER_GLOBAL if change == 'user' else GLOBAL
    with pytest.raises(SecretUnreachableError):
        SecretStore(openbao_root).load(ref)


def test_changed_role_id_invalidates_the_cache(openbao_root, openbao):
    from devbase.env import bootstrap

    fill(openbao, SecretStore(openbao_root))
    bootstrap.save(openbao_root, bootstrap.Credentials('other-rid', 's3cret'))
    openbao.stop()

    with pytest.raises(SecretUnreachableError):
        SecretStore(openbao_root).load(GLOBAL)


def test_ciphertext_and_scope_travel_together(openbao_root, openbao):
    """暗号文だけを別 scope の世代へ差し替えても、scope の不一致として捨てられる"""
    store = SecretStore(openbao_root)
    fill(openbao, store)
    good = cache.entry_path(openbao_root, GLOBAL).read_bytes()

    # 別 mount で取得した世代を作り、その暗号文を元の場所へ戻す
    ob = bc.load(openbao_root).openbao
    other = bc.OpenBaoSettings(**{**ob.to_dict(), 'mount': 'staging'})
    bc.save(openbao_root, bc.BackendConfig(backend='openbao', openbao=other))
    openbao.mount = 'staging'
    openbao.put('team/global', {'A': 'staging-value'})
    SecretStore(openbao_root).load(GLOBAL)
    staged = cache.entry_path(openbao_root, GLOBAL).read_bytes()
    assert staged != good

    bc.save(openbao_root, bc.BackendConfig(backend='openbao', openbao=ob))
    cache.entry_path(openbao_root, GLOBAL).write_bytes(staged)
    openbao.stop()

    with pytest.raises(SecretUnreachableError):
        SecretStore(openbao_root).load(GLOBAL)


# ---------------------------------------------------------------------------
# 書き込みとの同期
# ---------------------------------------------------------------------------

def test_successful_writes_advance_the_cache(openbao_root, openbao):
    fill(openbao, SecretStore(openbao_root))
    store = SecretStore(openbao_root)
    store.save(GLOBAL, {'NEW': '1'})     # A (漏れたキー) を消し、NEW を足す
    openbao.stop()

    resolved = runtime.resolve(openbao_root, 'web', store=SecretStore(openbao_root))

    assert 'A' not in resolved.values
    assert resolved.values['NEW'] == '1'


def test_a_version_conflict_discards_the_cache(openbao_root, openbao):
    fill(openbao, SecretStore(openbao_root))
    store = SecretStore(openbao_root)
    store.load(GLOBAL)
    openbao.put('team/global', {'A': 'theirs'})

    with pytest.raises(SecretConflictError):
        store.save(GLOBAL, {'X': '1'})

    assert not cache.entry_path(openbao_root, GLOBAL).exists()
    assert 'team:global' not in cache.read_index(openbao_root)
    openbao.stop()
    with pytest.raises(SecretUnreachableError):
        SecretStore(openbao_root).load(GLOBAL)


@pytest.mark.parametrize('how', ['dropped', '500', 'garbled', '500-truncated'])
def test_a_write_with_an_unknown_result_discards_the_cache(openbao_root, openbao, how):
    """サーバが更新を確定した後で応答だけが失われた (壊れた) 可能性があるため消す"""
    fill(openbao, SecretStore(openbao_root))
    if how == 'dropped':
        openbao.drop_write_response = True
    elif how == '500':
        openbao.fail_write_attempts = [1]
    elif how == '500-truncated':
        openbao.fail_write_attempts = [1]
        openbao.truncate_write_error_body = True
    else:
        openbao.garble_write_response = True

    with pytest.raises(SecretUnreachableError):
        SecretStore(openbao_root).save(GLOBAL, {'X': '1'})

    assert not cache.entry_path(openbao_root, GLOBAL).exists()
    assert 'team:global' not in cache.read_index(openbao_root)


def test_a_write_the_server_refused_keeps_the_cache(openbao_root, openbao):
    """403 はサーバが拒んだと確定した応答で、読んだときに進んだ世代がそのまま正しい"""
    fill(openbao, SecretStore(openbao_root))
    openbao.team_writable = False

    with pytest.raises(SecretAuthError):
        SecretStore(openbao_root).save(GLOBAL, {'X': '1'})

    assert cache.entry_path(openbao_root, GLOBAL).exists()
    openbao.stop()
    assert SecretStore(openbao_root).load(GLOBAL) == {'A': 'team-global'}


def test_a_reference_read_from_the_cache_cannot_be_written_back(openbao_root, openbao):
    """控えには版が無い。復旧後に取り直した版で書くと不達の間の他人の更新を上書きする"""
    fill(openbao, SecretStore(openbao_root))
    openbao.stop()
    store = SecretStore(openbao_root)
    assert store.load(GLOBAL) == {'A': 'team-global'}     # 控えから

    with pytest.raises(SecretUnreachableError) as exc:
        store.save(GLOBAL, {'A': 'stale'})

    assert '書き込めません' in str(exc.value) or '書き戻' in str(exc.value)
    assert cache.entry_path(openbao_root, GLOBAL).exists()     # 控えは残る


def test_unwritable_cache_after_a_write_is_discarded_with_a_warning(openbao_root, openbao,
                                                                    monkeypatch, caplog):
    fill(openbao, SecretStore(openbao_root))
    store = SecretStore(openbao_root)
    # 受信者鍵を外す = 新しい世代を暗号化できない
    monkeypatch.setattr(store.age, 'encrypt_bytes',
                        lambda data: (_ for _ in ()).throw(SecretStoreError('no recipients')))

    with caplog.at_level(logging.WARNING):
        store.save(GLOBAL, {'NEW': '1'})

    assert openbao.get('team/global') == {'NEW': '1'}     # サーバへは書けている
    assert not cache.entry_path(openbao_root, GLOBAL).exists()
    assert '控えを消します' in caplog.text


# ---------------------------------------------------------------------------
# 無効化
# ---------------------------------------------------------------------------

def test_disabling_the_cache_removes_existing_entries(openbao_root, openbao):
    fill(openbao, SecretStore(openbao_root))
    assert cache.cached_files(openbao_root)
    ob = bc.load(openbao_root).openbao
    bc.save(openbao_root, bc.BackendConfig(backend='openbao', openbao=ob,
                                             cache_enabled=False))

    SecretStore(openbao_root).load(GLOBAL)

    assert cache.cached_files(openbao_root) == []
    assert not cache.index_path(openbao_root).exists()


def test_deletion_while_disabled_does_not_come_back(openbao_root, openbao):
    fill(openbao, SecretStore(openbao_root))
    ob = bc.load(openbao_root).openbao
    bc.save(openbao_root, bc.BackendConfig(backend='openbao', openbao=ob,
                                             cache_enabled=False))
    SecretStore(openbao_root).save(GLOBAL, {})     # LEAKED (= A) を消す
    bc.save(openbao_root, bc.BackendConfig(backend='openbao', openbao=ob,
                                             cache_enabled=True))
    openbao.stop()

    with pytest.raises(SecretUnreachableError):
        runtime.resolve(openbao_root, 'web', store=SecretStore(openbao_root))


def test_undeletable_entries_fail_with_their_paths(openbao_root, openbao, monkeypatch):
    fill(openbao, SecretStore(openbao_root))
    ob = bc.load(openbao_root).openbao
    bc.save(openbao_root, bc.BackendConfig(backend='openbao', openbao=ob,
                                             cache_enabled=False))
    target = cache.entry_path(openbao_root, GLOBAL)
    real_unlink = os.unlink

    def deny(path, *a, **kw):
        if str(path) == str(target):
            raise PermissionError('denied')
        return real_unlink(path, *a, **kw)

    monkeypatch.setattr(os, 'unlink', deny)

    with pytest.raises(SecretStoreError) as exc:
        SecretStore(openbao_root).load(GLOBAL)
    assert str(target) in str(exc.value)


# ---------------------------------------------------------------------------
# 原子性
# ---------------------------------------------------------------------------

def test_interrupted_update_keeps_the_previous_generation(openbao_root, openbao, monkeypatch):
    fill(openbao, SecretStore(openbao_root))
    before = cache.entry_path(openbao_root, GLOBAL).read_bytes()
    openbao.put('team/global', {'A': 'changed'})
    real_replace = os.replace

    def interrupt(src, dst, *a, **kw):
        if str(dst).endswith('team/global.env.age'):
            raise OSError('power loss')
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, 'replace', interrupt)
    SecretStore(openbao_root).load(GLOBAL)      # 置き換えに失敗 → 控えを消す方針
    monkeypatch.setattr(os, 'replace', real_replace)

    # 途中の状態 (一時ファイル) は残らない
    leftovers = [p for p in cache.cache_dir(openbao_root).rglob('*') if p.name.endswith('.tmp')]
    assert leftovers == []
    entry = cache.entry_path(openbao_root, GLOBAL)
    assert not entry.exists() or entry.read_bytes() == before


def test_concurrent_generations_do_not_mix(openbao_root, openbao):
    """別 scope の 2 つの書き込みが交互に進んでも、残る 1 件は scope と中身が対応する"""
    store_a = SecretStore(openbao_root)
    fill(openbao, store_a)
    backend_a = store_a.backend_for(GLOBAL)
    cache_a = cache.SecretCache(store_a)

    ob = bc.load(openbao_root).openbao
    other = bc.OpenBaoSettings(**{**ob.to_dict(), 'mount': 'staging'})
    bc.save(openbao_root, bc.BackendConfig(backend='openbao', openbao=other))
    store_b = SecretStore(openbao_root)
    backend_b = store_b.backend_for(GLOBAL)
    cache_b = cache.SecretCache(store_b)

    cache_b.store(GLOBAL, {'A': 'b-value'}, backend_b)
    cache_a.store(GLOBAL, {'A': 'a-value'}, backend_a)
    cache_b.store(GLOBAL, {'A': 'b-value'}, backend_b)

    assert cache_a.read(GLOBAL, backend_a) is None
    assert cache_b.read(GLOBAL, backend_b).secrets == {'A': 'b-value'}


# ---------------------------------------------------------------------------
# グループ別の置き場 (PLAN56 受け入れ条件 8)
# ---------------------------------------------------------------------------

@pytest.fixture
def grouped_root(openbao_root, openbao):
    """``version: 2`` (``default`` → ``nyle``)。``web`` は ``with``、``api`` は宣言なし"""
    from tests.conftest import configure_openbao

    root = openbao_root
    configure_openbao(root, openbao, layout='group', group_aliases={'default': 'nyle'})
    (root / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=with\n')
    (root / 'projects' / 'api').mkdir(parents=True, exist_ok=True)
    openbao.put('team/with/global', {'A': 'with-global'})
    openbao.put('team/with/projects/web', {'B': 'with-web'})
    openbao.put('team/nyle/global', {'A': 'nyle-global'})
    openbao.put('team/nyle/projects/api', {'B': 'nyle-api'})
    return root


def test_grouped_caches_are_separate_files_and_index_keys(grouped_root):
    root = grouped_root
    runtime.resolve(root, 'web', store=SecretStore(root))
    runtime.resolve(root, 'api', store=SecretStore(root))

    base = root / 'secrets' / 'cache'
    assert cache.cached_files(root) == sorted([
        base / 'team' / 'nyle' / 'global.env.age',
        base / 'team' / 'nyle' / 'projects' / 'api.env.age',
        base / 'team' / 'with' / 'global.env.age',
        base / 'team' / 'with' / 'projects' / 'web.env.age',
        base / 'user' / 'nyle' / 'global.env.age',
        base / 'user' / 'nyle' / 'projects' / 'api.env.age',
        base / 'user' / 'with' / 'global.env.age',
        base / 'user' / 'with' / 'projects' / 'web.env.age'])
    assert set(cache.read_index(root)) == {
        'team:nyle:global', 'team:nyle:project:api', 'user:nyle:global',
        'user:nyle:project:api', 'team:with:global', 'team:with:project:web',
        'user:with:global', 'user:with:project:web'}


def test_unreachable_uses_each_groups_own_cache(grouped_root, openbao):
    """受け入れ条件 8: 後から起動した方の控えが先の控えを上書きせず、取り違えない"""
    root = grouped_root
    runtime.resolve(root, 'api', store=SecretStore(root))
    runtime.resolve(root, 'web', store=SecretStore(root))
    openbao.stop()

    api = runtime.resolve(root, 'api', store=SecretStore(root))
    web = runtime.resolve(root, 'web', store=SecretStore(root))

    assert api.values == {'A': 'nyle-global', 'B': 'nyle-api'}
    assert web.values == {'A': 'with-global', 'B': 'with-web'}


def test_flat_cache_is_not_used_for_a_grouped_reference(openbao_root, openbao):
    """移行性: ``version: 1`` の控えを ``version: 2`` の参照に使わない"""
    from tests.conftest import configure_openbao

    root = openbao_root
    openbao.put('team/global', {'A': 'flat'})
    runtime.resolve(root, store=SecretStore(root))
    configure_openbao(root, openbao, layout='group', group_aliases={'default': 'nyle'})
    openbao.stop()

    with pytest.raises(SecretUnreachableError) as exc:
        runtime.resolve(root, store=SecretStore(root))
    assert 'キャッシュもありません' in str(exc.value)
