"""bootstrap.py: サーバ接続に使う機密 (secrets/bootstrap.env.age)"""

from __future__ import annotations

import stat

import pytest

from devbase.env import agekeys, bootstrap
from devbase.errors import DevbaseError


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / 'projects').mkdir()
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    return tmp_path


@pytest.fixture
def with_key(root):
    _, public = agekeys.generate_key_file()
    return public


CREDS = bootstrap.Credentials(role_id='rid-1', secret_id='very-secret')


def test_roundtrip_is_encrypted_and_0600(root, with_key):
    path = bootstrap.save(root, CREDS)

    assert path == root / 'secrets' / 'bootstrap.env.age'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = path.read_bytes()
    assert raw.startswith(b'age-encryption.org/')
    assert b'very-secret' not in raw and b'cid-1' not in raw
    assert bootstrap.load(root) == CREDS


def test_load_without_a_file_returns_none(root, with_key):
    assert bootstrap.load(root) is None
    assert bootstrap.exists(root) is False


def test_save_without_a_key_does_not_fall_back_to_plaintext(root):
    with pytest.raises(DevbaseError) as exc:
        bootstrap.save(root, CREDS)

    assert 'keygen' in str(exc.value)
    assert not (root / 'secrets').exists() or not list((root / 'secrets').iterdir())


def test_load_without_a_key_fails_instead_of_reading_plaintext(root, with_key, monkeypatch):
    bootstrap.save(root, CREDS)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(root / 'age' / 'missing.txt'))
    monkeypatch.setenv('HOME', str(root / 'nohome'))   # ~/.ssh の鍵にも落ちない

    with pytest.raises(DevbaseError) as exc:
        bootstrap.load(root)
    assert 'keygen' in str(exc.value)


def test_load_reports_missing_keys(root, with_key):
    from devbase.env.secret_store import AgeBackend
    from devbase.env import io_common

    blob = AgeBackend(root).encrypt_bytes(b'DEVBASE_OPENBAO_ROLE_ID=only-id\n')
    io_common.write_secure_bytes_atomic(bootstrap.path(root), blob)

    with pytest.raises(DevbaseError) as exc:
        bootstrap.load(root)
    assert 'DEVBASE_OPENBAO_SECRET_ID' in str(exc.value)
    assert 'only-id' not in str(exc.value)
