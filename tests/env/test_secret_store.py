"""secret_store.py: 平文 / age の保存先抽象と自動判定"""

from __future__ import annotations

import stat

import pyrage
import pytest

from devbase.env.secret_store import (
    MODE_ABSENT,
    MODE_AGE,
    MODE_PLAINTEXT,
    AgeBackend,
    SecretRef,
    SecretStore,
    SecretStoreError,
)


@pytest.fixture
def keypair():
    identity = pyrage.x25519.Identity.generate()
    return str(identity.to_public()), str(identity)


@pytest.fixture
def store(tmp_path, keypair):
    """明示的な鍵を渡した SecretStore (ホームの鍵に依存しない)"""
    public, secret = keypair
    id_path = tmp_path / 'identity.key'
    id_path.write_text(secret)
    (tmp_path / 'projects').mkdir()
    return SecretStore(tmp_path, recipients=[public], identities=[str(id_path)])


GLOBAL = SecretRef.for_global()
SAMPLE = {'ANTHROPIC_API_KEY': 'sk-test', 'AWS_SECRET_ACCESS_KEY': 'secret value'}


# ---------------------------------------------------------------------------
# 参照
# ---------------------------------------------------------------------------

def test_project_ref_rejects_path_traversal():
    for bad in ('../evil', 'a/b', '.', '..'):
        with pytest.raises(SecretStoreError):
            SecretRef.for_project(bad)


def test_project_ref_rejects_empty_name():
    with pytest.raises(SecretStoreError):
        SecretRef.for_project('')


# ---------------------------------------------------------------------------
# 保存先パス
# ---------------------------------------------------------------------------

def test_paths_follow_the_documented_layout(tmp_path, store):
    proj = SecretRef.for_project('web')

    assert store.plaintext.path(GLOBAL) == tmp_path / '.env'
    assert store.plaintext.path(proj) == tmp_path / 'projects' / 'web' / '.env'
    assert store.age.path(GLOBAL) == tmp_path / 'secrets' / 'global.env.age'
    assert store.age.path(proj) == tmp_path / 'secrets' / 'projects' / 'web.env.age'


# ---------------------------------------------------------------------------
# ラウンドトリップ
# ---------------------------------------------------------------------------

def test_age_backend_roundtrip(store):
    path = store.age.save(GLOBAL, SAMPLE)

    assert path.exists()
    assert b'sk-test' not in path.read_bytes()   # 平文が残っていない
    assert store.age.load(GLOBAL) == SAMPLE


def test_age_backend_file_is_0600(store):
    path = store.age.save(GLOBAL, SAMPLE)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_plaintext_backend_roundtrip(store):
    store.plaintext.save(GLOBAL, SAMPLE)
    assert store.plaintext.load(GLOBAL) == SAMPLE


def test_project_secrets_roundtrip(store):
    proj = SecretRef.for_project('web')
    store.age.save(proj, {'DB_PASSWORD': 'p@ss word'})
    assert store.age.load(proj) == {'DB_PASSWORD': 'p@ss word'}


def test_load_of_missing_file_is_empty(store):
    assert store.age.load(GLOBAL) == {}
    assert store.plaintext.load(GLOBAL) == {}


def test_age_load_with_wrong_identity_raises(tmp_path, keypair):
    public, _ = keypair
    other = tmp_path / 'other.key'
    other.write_text(str(pyrage.x25519.Identity.generate()))

    writer = AgeBackend(tmp_path, recipients=[public])
    writer.save(GLOBAL, SAMPLE)

    reader = AgeBackend(tmp_path, identities=[str(other)])
    with pytest.raises(SecretStoreError, match='復号'):
        reader.load(GLOBAL)


# ---------------------------------------------------------------------------
# 自動判定
# ---------------------------------------------------------------------------

def test_mode_is_absent_when_nothing_exists(store):
    assert store.mode(GLOBAL) == MODE_ABSENT
    assert store.exists(GLOBAL) is False


def test_mode_is_plaintext_when_only_plain_exists(store):
    store.plaintext.save(GLOBAL, SAMPLE)
    assert store.mode(GLOBAL) == MODE_PLAINTEXT
    assert store.load(GLOBAL) == SAMPLE


def test_mode_is_age_when_only_encrypted_exists(store):
    store.age.save(GLOBAL, SAMPLE)
    assert store.mode(GLOBAL) == MODE_AGE
    assert store.is_encrypted(GLOBAL) is True
    assert store.load(GLOBAL) == SAMPLE


def test_both_present_is_an_error(store):
    store.plaintext.save(GLOBAL, SAMPLE)
    store.age.save(GLOBAL, SAMPLE)

    with pytest.raises(SecretStoreError, match='両方に存在'):
        store.load(GLOBAL)
    with pytest.raises(SecretStoreError, match='両方に存在'):
        store.mode(GLOBAL)


def test_both_present_error_names_both_paths(store):
    store.plaintext.save(GLOBAL, SAMPLE)
    store.age.save(GLOBAL, SAMPLE)
    with pytest.raises(SecretStoreError) as exc:
        store.path(GLOBAL)
    message = str(exc.value)
    assert str(store.age.path(GLOBAL)) in message
    assert str(store.plaintext.path(GLOBAL)) in message


def test_save_keeps_the_existing_format(store):
    """set / sync 相当の保存が形式を勝手に変えない"""
    store.age.save(GLOBAL, SAMPLE)
    store.save(GLOBAL, {**SAMPLE, 'NEW': '1'})

    assert store.mode(GLOBAL) == MODE_AGE
    assert store.plaintext.path(GLOBAL).exists() is False
    assert store.load(GLOBAL)['NEW'] == '1'


def test_save_defaults_to_plaintext_for_new_refs(store):
    store.save(GLOBAL, SAMPLE)
    assert store.mode(GLOBAL) == MODE_PLAINTEXT


# ---------------------------------------------------------------------------
# 削除・一覧
# ---------------------------------------------------------------------------

def test_remove_reports_whether_a_file_was_deleted(store):
    store.age.save(GLOBAL, SAMPLE)
    assert store.age.remove(GLOBAL) is True
    assert store.age.remove(GLOBAL) is False


def test_project_names_lists_encrypted_projects_only(store):
    store.age.save(SecretRef.for_project('web'), {'A': '1'})
    store.age.save(SecretRef.for_project('api'), {'B': '2'})
    store.plaintext.save(SecretRef.for_project('legacy'), {'C': '3'})

    assert store.project_names() == ['api', 'web']


def test_project_names_empty_without_secrets_dir(store):
    assert store.project_names() == []


# ---------------------------------------------------------------------------
# 鍵未整備時のエラー
# ---------------------------------------------------------------------------

def test_age_save_without_recipients_raises(tmp_path, monkeypatch):
    from devbase.env import agekeys

    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'absent' / 'keys.txt'))
    backend = AgeBackend(tmp_path)
    with pytest.raises(agekeys.AgeKeyError, match='公開鍵がありません'):
        backend.save(GLOBAL, SAMPLE)


def test_age_load_without_identities_raises(tmp_path, keypair, monkeypatch):
    from devbase.env import agekeys

    public, _ = keypair
    AgeBackend(tmp_path, recipients=[public]).save(GLOBAL, SAMPLE)

    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'absent' / 'keys.txt'))
    monkeypatch.setattr(agekeys._cipher, 'default_identity_paths', lambda: [])
    with pytest.raises(SecretStoreError, match='秘密鍵が見つかりません'):
        AgeBackend(tmp_path).load(GLOBAL)


def test_plaintext_load_of_binary_reports_a_useful_error(store):
    path = store.plaintext.path(GLOBAL)
    path.write_bytes(b'\xff\xfe\x00binary')
    with pytest.raises(SecretStoreError, match='UTF-8'):
        store.plaintext.load(GLOBAL)
