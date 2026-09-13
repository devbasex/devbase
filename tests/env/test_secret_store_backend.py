"""secret_store.py: 参照の持ち主と、設定による backend の選択 (PLAN51)"""

from __future__ import annotations

import pyrage
import pytest

from devbase.env import backend_config as bc
from devbase.env import backends
from devbase.env.secret_store import (
    MODE_ABSENT,
    MODE_AGE,
    MODE_PLAINTEXT,
    SecretRef,
    SecretStore,
    SecretStoreError,
)


@pytest.fixture
def keypair():
    identity = pyrage.x25519.Identity.generate()
    return str(identity.to_public()), str(identity)


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    return tmp_path


@pytest.fixture
def store(root, keypair):
    public, secret = keypair
    id_path = root / 'identity.key'
    id_path.write_text(secret)
    return SecretStore(root, recipients=[public], identities=[str(id_path)])


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')


# ---------------------------------------------------------------------------
# 参照の持ち主
# ---------------------------------------------------------------------------

def test_refs_default_to_the_team_owner():
    assert GLOBAL.owner == 'team'
    assert WEB.owner == 'team'
    assert SecretRef.for_global() == SecretRef(kind='global')


def test_user_refs_are_distinct_from_team_refs():
    user_global = SecretRef.for_global(owner='user')
    user_web = SecretRef.for_project('web', owner='user')

    assert user_global != GLOBAL
    assert user_web != WEB
    assert user_global.owner == 'user' and user_web.name == 'web'
    assert len({GLOBAL, WEB, user_global, user_web}) == 4


def test_unknown_owner_is_rejected():
    with pytest.raises(SecretStoreError):
        SecretRef.for_global(owner='group')


def test_team_labels_are_unchanged_and_user_labels_are_prefixed():
    assert GLOBAL.label() == 'グローバル'
    assert WEB.label() == "プロジェクト 'web'"
    assert SecretRef.for_global(owner='user').label() == '個人のグローバル'
    assert SecretRef.for_project('web', owner='user').label() == "個人のプロジェクト 'web'"


# ---------------------------------------------------------------------------
# ファイル backend は個人単位の参照を持たない
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('backend_attr', ['age', 'plaintext'])
def test_file_backends_have_no_user_refs(store, backend_attr):
    backend = getattr(store, backend_attr)
    ref = SecretRef.for_global(owner='user')

    assert backend.exists(ref) is False
    assert backend.load(ref) == {}
    assert backend.load_bytes(ref) == b''
    with pytest.raises(SecretStoreError) as exc:
        backend.save(ref, {'A': '1'})
    assert '個人単位' in str(exc.value)
    with pytest.raises(SecretStoreError):
        backend.save_bytes(ref, b'A=1\n')


def test_store_reports_user_refs_absent_without_config(store):
    ref = SecretRef.for_project('web', owner='user')

    assert store.mode(ref) == MODE_ABSENT
    assert store.exists(ref) is False
    assert store.load(ref) == {}


# ---------------------------------------------------------------------------
# 登録簿
# ---------------------------------------------------------------------------

def test_registry_knows_the_documented_names():
    assert backends.BACKEND_NAMES == ('auto', 'plaintext', 'age', 'infisical')


def test_registry_rejects_unknown_names_with_the_list():
    with pytest.raises(SecretStoreError) as exc:
        backends.require_known('vaultwarden')
    message = str(exc.value)
    assert 'vaultwarden' in message and 'infisical' in message and 'age' in message


# ---------------------------------------------------------------------------
# 設定による選択
# ---------------------------------------------------------------------------

def test_auto_keeps_the_existence_based_choice(store):
    store.plaintext.save(GLOBAL, {'A': '1'})

    assert store.backend_for(GLOBAL) is store.plaintext
    assert store.mode(GLOBAL) == MODE_PLAINTEXT
    assert store.direct_edit(GLOBAL) is True


def test_explicit_age_selects_age_even_when_only_plaintext_exists(root, store):
    bc.save(root, bc.BackendConfig(backend='age'))
    store.plaintext.save(GLOBAL, {'A': '1'})

    assert store.backend_for(GLOBAL) is store.age
    assert store.mode(GLOBAL) == MODE_ABSENT
    assert store.exists(GLOBAL) is False
    assert store.direct_edit(GLOBAL) is False


def test_explicit_plaintext_selects_plaintext(root, store):
    bc.save(root, bc.BackendConfig(backend='plaintext'))
    store.age.save(GLOBAL, {'A': '1'})

    assert store.backend_for(GLOBAL) is store.plaintext
    assert store.mode(GLOBAL) == MODE_ABSENT
    assert store.path(GLOBAL) == root / '.env'


def test_explicit_age_does_not_reject_coexistence(root, store):
    """明示的に選んでいるときは、どちらが正かは設定が答えている"""
    bc.save(root, bc.BackendConfig(backend='age'))
    store.plaintext.save(GLOBAL, {'A': 'plain'})
    store.age.save(GLOBAL, {'A': 'age'})

    assert store.load(GLOBAL) == {'A': 'age'}
    assert store.mode(GLOBAL) == MODE_AGE


def test_auto_still_rejects_coexistence(store):
    store.plaintext.save(GLOBAL, {'A': 'plain'})
    store.age.save(GLOBAL, {'A': 'age'})

    with pytest.raises(SecretStoreError):
        store.backend_for(GLOBAL)


def test_broken_config_is_reported_as_a_store_error(root, store):
    path = root / 'secrets' / 'backend.yml'
    path.parent.mkdir(exist_ok=True)
    path.write_text('backend: vaultwarden\n')

    with pytest.raises(SecretStoreError) as exc:
        store.backend_for(GLOBAL)
    assert 'vaultwarden' in str(exc.value)


def test_config_is_read_once_per_store(root, store):
    """設定は生成時ではなく最初の解決時に読み、その後は読み直さない"""
    store.plaintext.save(GLOBAL, {'A': '1'})
    assert store.backend_for(GLOBAL) is store.plaintext

    bc.save(root, bc.BackendConfig(backend='age'))

    assert store.backend_for(GLOBAL) is store.plaintext
    assert SecretStore(root).backend_for(GLOBAL).name == 'age'


def test_config_property_exposes_the_loaded_choice(root, store):
    bc.save(root, bc.BackendConfig(backend='age'))

    assert store.config.backend == 'age'
    assert store.backend_name == 'age'


def test_backend_name_is_auto_without_config(store):
    assert store.backend_name == 'auto'
