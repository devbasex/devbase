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
from devbase.env.infisical import SecretAuthError, SecretUnreachableError
from devbase.env.secret_store import SecretRef, SecretStore, SecretStoreError
from tests.conftest import configure_infisical


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')
USER_GLOBAL = SecretRef.for_global(owner='user')
USER_WEB = SecretRef.for_project('web', owner='user')
TEAM_GLOBAL_PATH = '/team/global'


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fill(infisical, store):
    """4 参照すべてに 1 件ずつ入れて取得し、控えを作る"""
    infisical.put('/team/global', {'A': 'team-global'})
    infisical.put('/team/projects/web', {'B': 'team-web'})
    infisical.put('/users/member01/global', {'C': 'user-global'})
    infisical.put('/users/member01/projects/web', {'D': 'user-web'})
    return runtime.resolve(store.root, 'web', store=store)


# ---------------------------------------------------------------------------
# 配置と形式
# ---------------------------------------------------------------------------

def test_each_reference_has_its_own_encrypted_file(infisical_root, infisical):
    store = SecretStore(infisical_root)
    fill(infisical, store)

    files = cache.cached_files(infisical_root)
    base = infisical_root / 'secrets' / 'cache'
    assert files == sorted([
        base / 'team' / 'global.env.age', base / 'team' / 'projects' / 'web.env.age',
        base / 'user' / 'global.env.age', base / 'user' / 'projects' / 'web.env.age'])
    for path in files:
        assert path.read_bytes().startswith(b'age-encryption.org/')
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert b'team-global' not in path.read_bytes()
    assert stat.S_IMODE(base.stat().st_mode) == 0o700


def test_index_has_no_keys_or_values(infisical_root, infisical):
    store = SecretStore(infisical_root)
    fill(infisical, store)

    raw = (infisical_root / 'secrets' / 'cache' / 'index.json').read_text()
    assert 'team-global' not in raw and '"A"' not in raw
    entries = cache.read_index(infisical_root)
    assert set(entries) == {'team:global', 'team:project:web', 'user:global', 'user:project:web'}
    assert entries['team:global']['url_host'] == '127.0.0.1'
    assert entries['team:global']['fetched_at']


# ---------------------------------------------------------------------------
# 不達
# ---------------------------------------------------------------------------

def test_unreachable_with_a_cache_resolves_and_warns(infisical_root, infisical, caplog):
    fill(infisical, SecretStore(infisical_root))
    fetched_at = cache.read_index(infisical_root)['team:global']['fetched_at']
    infisical.stop()

    with caplog.at_level(logging.WARNING):
        resolved = runtime.resolve(infisical_root, 'web', store=SecretStore(infisical_root))

    assert resolved.values == {'A': 'team-global', 'B': 'team-web',
                               'C': 'user-global', 'D': 'user-web'}
    assert 'キャッシュ' in caplog.text and fetched_at in caplog.text
    assert 'team-global' not in caplog.text


def test_unreachable_without_a_cache_fails_with_the_url(infisical_root, infisical):
    url = infisical.url
    infisical.stop()

    with pytest.raises(SecretUnreachableError) as exc:
        runtime.resolve(infisical_root, 'web', store=SecretStore(infisical_root))
    assert url in str(exc.value)


@pytest.mark.parametrize('how', ['down', '500', 'bad-json'])
def test_failed_fetch_leaves_the_cache_untouched(infisical_root, infisical, how):
    fill(infisical, SecretStore(infisical_root))
    path = cache.entry_path(infisical_root, GLOBAL)
    before = digest(path)

    if how == 'down':
        infisical.stop()
    elif how == '500':
        infisical.get_status = 500
    else:
        infisical.get_body = b'{oops'

    SecretStore(infisical_root).load(GLOBAL)   # キャッシュから返る

    assert digest(path) == before


def test_truncated_response_falls_back_to_the_cache(infisical_root, infisical):
    """本文が Content-Length より短く切れた応答 (IncompleteRead) は不達として扱う"""
    fill(infisical, SecretStore(infisical_root))
    infisical.truncate_get_body = True

    assert SecretStore(infisical_root).load(GLOBAL) == {'A': 'team-global'}


def test_an_empty_answer_replaces_the_cache(infisical_root, infisical):
    fill(infisical, SecretStore(infisical_root))
    infisical.put('/team/global', {})

    assert SecretStore(infisical_root).load(GLOBAL) == {}
    infisical.stop()

    assert SecretStore(infisical_root).load(GLOBAL) == {}


# ---------------------------------------------------------------------------
# 認証拒否
# ---------------------------------------------------------------------------

def test_auth_rejection_does_not_use_or_change_the_cache(infisical_root, infisical,
                                                          monkeypatch):
    fill(infisical, SecretStore(infisical_root))
    path = cache.entry_path(infisical_root, GLOBAL)
    before = digest(path)
    infisical.reject_login = True
    infisical.expire_token()
    reads = []
    original = cache.SecretCache.read
    monkeypatch.setattr(cache.SecretCache, 'read',
                        lambda self, ref, backend: reads.append(ref) or original(self, ref, backend))

    with pytest.raises(SecretAuthError):
        runtime.resolve(infisical_root, 'web', store=SecretStore(infisical_root))

    assert reads == []
    assert digest(path) == before

    # 資格が戻った後の不達では、そのキャッシュがこれまでどおり使われる
    infisical.reject_login = False
    infisical.stop()
    assert SecretStore(infisical_root).load(GLOBAL) == {'A': 'team-global'}


def test_forbidden_reference_does_not_use_the_cache(infisical_root, infisical):
    fill(infisical, SecretStore(infisical_root))
    infisical.forbidden_prefixes = ['/users/']

    with pytest.raises(SecretAuthError):
        SecretStore(infisical_root).load(USER_GLOBAL)


# ---------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('change', ['url', 'project_id', 'environment', 'user'])
def test_changed_settings_invalidate_the_cache(infisical_root, infisical, change):
    fill(infisical, SecretStore(infisical_root))
    config = bc.load(infisical_root)
    inf = config.infisical
    if change == 'url':
        # 同じホストの別ポート (= 別の接続先)。到達はできない
        new = bc.InfisicalSettings(**{**inf.to_dict(), 'url': 'http://127.0.0.1:1'})
    elif change == 'project_id':
        new = bc.InfisicalSettings(**{**inf.to_dict(), 'project_id': 'other'})
    elif change == 'environment':
        new = bc.InfisicalSettings(**{**inf.to_dict(), 'environment': 'staging'})
    else:
        new = bc.InfisicalSettings(**{**inf.to_dict(), 'user': 'member02'})
    bc.save(infisical_root, bc.BackendConfig(backend='infisical', infisical=new))
    infisical.stop()

    ref = USER_GLOBAL if change == 'user' else GLOBAL
    with pytest.raises(SecretUnreachableError):
        SecretStore(infisical_root).load(ref)


def test_changed_client_id_invalidates_the_cache(infisical_root, infisical):
    from devbase.env import bootstrap

    fill(infisical, SecretStore(infisical_root))
    bootstrap.save(infisical_root, bootstrap.Credentials('other-cid', 's3cret'))
    infisical.stop()

    with pytest.raises(SecretUnreachableError):
        SecretStore(infisical_root).load(GLOBAL)


def test_ciphertext_and_scope_travel_together(infisical_root, infisical):
    """暗号文だけを別 scope の世代へ差し替えても、scope の不一致として捨てられる"""
    store = SecretStore(infisical_root)
    fill(infisical, store)
    good = cache.entry_path(infisical_root, GLOBAL).read_bytes()

    # 別 environment で取得した世代を作り、その暗号文を元の場所へ戻す
    inf = bc.load(infisical_root).infisical
    other = bc.InfisicalSettings(**{**inf.to_dict(), 'environment': 'staging'})
    bc.save(infisical_root, bc.BackendConfig(backend='infisical', infisical=other))
    infisical.put('/team/global', {'A': 'staging-value'}, environment='staging')
    SecretStore(infisical_root).load(GLOBAL)
    staged = cache.entry_path(infisical_root, GLOBAL).read_bytes()
    assert staged != good

    bc.save(infisical_root, bc.BackendConfig(backend='infisical', infisical=inf))
    cache.entry_path(infisical_root, GLOBAL).write_bytes(staged)
    infisical.stop()

    with pytest.raises(SecretUnreachableError):
        SecretStore(infisical_root).load(GLOBAL)


# ---------------------------------------------------------------------------
# 書き込みとの同期
# ---------------------------------------------------------------------------

def test_successful_writes_advance_the_cache(infisical_root, infisical):
    fill(infisical, SecretStore(infisical_root))
    store = SecretStore(infisical_root)
    store.save(GLOBAL, {'NEW': '1'})     # A (漏れたキー) を消し、NEW を足す
    infisical.stop()

    resolved = runtime.resolve(infisical_root, 'web', store=SecretStore(infisical_root))

    assert 'A' not in resolved.values
    assert resolved.values['NEW'] == '1'


def test_partial_write_failure_discards_the_cache(infisical_root, infisical):
    fill(infisical, SecretStore(infisical_root))
    infisical.fail_writes_after = 1

    with pytest.raises(SecretStoreError):
        SecretStore(infisical_root).save(GLOBAL, {'X': '1', 'Y': '2'})

    assert not cache.entry_path(infisical_root, GLOBAL).exists()
    assert 'team:global' not in cache.read_index(infisical_root)
    infisical.stop()
    with pytest.raises(SecretUnreachableError):
        SecretStore(infisical_root).load(GLOBAL)


def test_unwritable_cache_after_a_write_is_discarded_with_a_warning(infisical_root, infisical,
                                                                    monkeypatch, caplog):
    fill(infisical, SecretStore(infisical_root))
    store = SecretStore(infisical_root)
    # 受信者鍵を外す = 新しい世代を暗号化できない
    monkeypatch.setattr(store.age, 'encrypt_bytes',
                        lambda data: (_ for _ in ()).throw(SecretStoreError('no recipients')))

    with caplog.at_level(logging.WARNING):
        store.save(GLOBAL, {'NEW': '1'})

    assert infisical.get('/team/global') == {'NEW': '1'}     # サーバへは書けている
    assert not cache.entry_path(infisical_root, GLOBAL).exists()
    assert '控えを消します' in caplog.text


# ---------------------------------------------------------------------------
# 無効化
# ---------------------------------------------------------------------------

def test_disabling_the_cache_removes_existing_entries(infisical_root, infisical):
    fill(infisical, SecretStore(infisical_root))
    assert cache.cached_files(infisical_root)
    inf = bc.load(infisical_root).infisical
    bc.save(infisical_root, bc.BackendConfig(backend='infisical', infisical=inf,
                                             cache_enabled=False))

    SecretStore(infisical_root).load(GLOBAL)

    assert cache.cached_files(infisical_root) == []
    assert not cache.index_path(infisical_root).exists()


def test_deletion_while_disabled_does_not_come_back(infisical_root, infisical):
    fill(infisical, SecretStore(infisical_root))
    inf = bc.load(infisical_root).infisical
    bc.save(infisical_root, bc.BackendConfig(backend='infisical', infisical=inf,
                                             cache_enabled=False))
    SecretStore(infisical_root).save(GLOBAL, {})     # LEAKED (= A) を消す
    bc.save(infisical_root, bc.BackendConfig(backend='infisical', infisical=inf,
                                             cache_enabled=True))
    infisical.stop()

    with pytest.raises(SecretUnreachableError):
        runtime.resolve(infisical_root, 'web', store=SecretStore(infisical_root))


def test_undeletable_entries_fail_with_their_paths(infisical_root, infisical, monkeypatch):
    fill(infisical, SecretStore(infisical_root))
    inf = bc.load(infisical_root).infisical
    bc.save(infisical_root, bc.BackendConfig(backend='infisical', infisical=inf,
                                             cache_enabled=False))
    target = cache.entry_path(infisical_root, GLOBAL)
    real_unlink = os.unlink

    def deny(path, *a, **kw):
        if str(path) == str(target):
            raise PermissionError('denied')
        return real_unlink(path, *a, **kw)

    monkeypatch.setattr(os, 'unlink', deny)

    with pytest.raises(SecretStoreError) as exc:
        SecretStore(infisical_root).load(GLOBAL)
    assert str(target) in str(exc.value)


# ---------------------------------------------------------------------------
# 原子性
# ---------------------------------------------------------------------------

def test_interrupted_update_keeps_the_previous_generation(infisical_root, infisical, monkeypatch):
    fill(infisical, SecretStore(infisical_root))
    before = cache.entry_path(infisical_root, GLOBAL).read_bytes()
    infisical.put('/team/global', {'A': 'changed'})
    real_replace = os.replace

    def interrupt(src, dst, *a, **kw):
        if str(dst).endswith('team/global.env.age'):
            raise OSError('power loss')
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, 'replace', interrupt)
    SecretStore(infisical_root).load(GLOBAL)      # 置き換えに失敗 → 控えを消す方針
    monkeypatch.setattr(os, 'replace', real_replace)

    # 途中の状態 (一時ファイル) は残らない
    leftovers = [p for p in cache.cache_dir(infisical_root).rglob('*') if p.name.endswith('.tmp')]
    assert leftovers == []
    entry = cache.entry_path(infisical_root, GLOBAL)
    assert not entry.exists() or entry.read_bytes() == before


def test_concurrent_generations_do_not_mix(infisical_root, infisical):
    """別 scope の 2 つの書き込みが交互に進んでも、残る 1 件は scope と中身が対応する"""
    store_a = SecretStore(infisical_root)
    fill(infisical, store_a)
    backend_a = store_a.backend_for(GLOBAL)
    cache_a = cache.SecretCache(store_a)

    inf = bc.load(infisical_root).infisical
    other = bc.InfisicalSettings(**{**inf.to_dict(), 'environment': 'staging'})
    bc.save(infisical_root, bc.BackendConfig(backend='infisical', infisical=other))
    store_b = SecretStore(infisical_root)
    backend_b = store_b.backend_for(GLOBAL)
    cache_b = cache.SecretCache(store_b)

    cache_b.store(GLOBAL, {'A': 'b-value'}, backend_b)
    cache_a.store(GLOBAL, {'A': 'a-value'}, backend_a)
    cache_b.store(GLOBAL, {'A': 'b-value'}, backend_b)

    assert cache_a.read(GLOBAL, backend_a) is None
    assert cache_b.read(GLOBAL, backend_b).secrets == {'A': 'b-value'}
