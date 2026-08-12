"""agekeys.py: devbase 専用 age 鍵と受信者リストの管理"""

from __future__ import annotations

import os
import stat

import pyrage
import pytest

from devbase.env import agekeys


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """鍵の既定パスを tmp_path 配下へ閉じ込める"""
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.delenv(agekeys.KEY_FILE_ENV, raising=False)
    monkeypatch.setattr(agekeys.Path, 'home', staticmethod(lambda: tmp_path / 'home'))
    return tmp_path


# ---------------------------------------------------------------------------
# パス解決
# ---------------------------------------------------------------------------

def test_key_file_path_uses_xdg_config_home(isolated_home):
    assert agekeys.key_file_path() == isolated_home / 'config' / 'devbase' / 'age' / 'keys.txt'


def test_key_file_path_env_override_wins(isolated_home, monkeypatch):
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(isolated_home / 'custom' / 'k.txt'))
    assert agekeys.key_file_path() == isolated_home / 'custom' / 'k.txt'


def test_key_file_path_falls_back_to_home_config(isolated_home, monkeypatch):
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    assert agekeys.key_file_path() == isolated_home / 'home' / '.config' / 'devbase' / 'age' / 'keys.txt'


# ---------------------------------------------------------------------------
# 鍵の生成
# ---------------------------------------------------------------------------

def test_generate_key_file_writes_private_key_with_0600(isolated_home):
    path, public = agekeys.generate_key_file()

    assert path.exists()
    assert public.startswith('age1')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert 'AGE-SECRET-KEY-1' in path.read_text()


def test_generate_key_file_creates_dir_with_0700(isolated_home):
    path, _ = agekeys.generate_key_file()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_generate_key_file_refuses_overwrite_without_force(isolated_home):
    path, _ = agekeys.generate_key_file()
    before = path.read_bytes()

    with pytest.raises(agekeys.AgeKeyError, match='既に存在'):
        agekeys.generate_key_file()

    assert path.read_bytes() == before


def test_generate_key_file_force_replaces_key(isolated_home):
    path, first = agekeys.generate_key_file()
    _, second = agekeys.generate_key_file(force=True)
    assert first != second
    assert agekeys.read_public_key(path) == second


def test_generated_key_can_decrypt_what_its_public_key_encrypted(isolated_home):
    from devbase.env import cipher

    path, public = agekeys.generate_key_file()
    blob = cipher.encrypt(b'payload', recipients=[public])
    assert cipher.decrypt(blob, identities=[str(path)]) == b'payload'


# ---------------------------------------------------------------------------
# 公開鍵の読み取り
# ---------------------------------------------------------------------------

def test_read_public_key_derives_from_secret_not_comment(isolated_home):
    """コメント行が嘘でも、秘密鍵から導出した公開鍵を返す"""
    path, public = agekeys.generate_key_file()
    tampered = path.read_text().replace(f'# public key: {public}',
                                        '# public key: age1deadbeef')
    path.write_text(tampered)

    assert agekeys.read_public_key(path) == public


def test_read_public_key_missing_file(isolated_home):
    with pytest.raises(agekeys.AgeKeyError, match='見つかりません'):
        agekeys.read_public_key(isolated_home / 'nope.txt')


def test_read_public_key_rejects_non_age_key(isolated_home):
    path = isolated_home / 'ssh_like.txt'
    path.write_text('-----BEGIN OPENSSH PRIVATE KEY-----\nzzz\n')
    with pytest.raises(agekeys.AgeKeyError, match='AGE-SECRET-KEY-1'):
        agekeys.read_public_key(path)


# ---------------------------------------------------------------------------
# 受信者リスト
# ---------------------------------------------------------------------------

def test_recipients_roundtrip(tmp_path):
    pub = str(pyrage.x25519.Identity.generate().to_public())

    assert agekeys.load_recipients(tmp_path) == []
    assert agekeys.add_recipient(tmp_path, pub) is True
    assert agekeys.load_recipients(tmp_path) == [pub]

    # 重複登録は no-op
    assert agekeys.add_recipient(tmp_path, pub) is False
    assert agekeys.load_recipients(tmp_path) == [pub]

    assert agekeys.remove_recipient(tmp_path, pub) is True
    assert agekeys.load_recipients(tmp_path) == []
    assert agekeys.remove_recipient(tmp_path, pub) is False


def test_recipients_file_is_0600(tmp_path):
    pub = str(pyrage.x25519.Identity.generate().to_public())
    agekeys.add_recipient(tmp_path, pub)
    path = agekeys.recipients_file(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_add_recipient_rejects_malformed_key(tmp_path):
    from devbase.env.cipher import CipherError

    with pytest.raises(CipherError):
        agekeys.add_recipient(tmp_path, 'not-a-key')
    assert not agekeys.recipients_file(tmp_path).exists()


def test_load_recipients_skips_comments_and_blanks(tmp_path):
    pub = str(pyrage.x25519.Identity.generate().to_public())
    path = agekeys.recipients_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(f"# header\n\n{pub}\n   \n")
    assert agekeys.load_recipients(tmp_path) == [pub]


# ---------------------------------------------------------------------------
# 鍵の解決
# ---------------------------------------------------------------------------

def test_resolve_recipients_prefers_registered_list(isolated_home, tmp_path):
    _, own = agekeys.generate_key_file()
    other = str(pyrage.x25519.Identity.generate().to_public())
    agekeys.add_recipient(tmp_path, other)

    assert agekeys.resolve_recipients(tmp_path) == [other]
    assert own not in agekeys.resolve_recipients(tmp_path)


def test_resolve_recipients_falls_back_to_own_public_key(isolated_home, tmp_path):
    _, own = agekeys.generate_key_file()
    assert agekeys.resolve_recipients(tmp_path) == [own]


def test_resolve_recipients_without_any_key_raises(isolated_home, tmp_path):
    with pytest.raises(agekeys.AgeKeyError, match='公開鍵がありません'):
        agekeys.resolve_recipients(tmp_path)


def test_resolve_identities_puts_devbase_key_first(isolated_home, monkeypatch):
    ssh_key = isolated_home / 'id_ed25519'
    ssh_key.write_text('dummy')
    monkeypatch.setattr(agekeys._cipher, 'default_identity_paths',
                        lambda: [ssh_key])

    path, _ = agekeys.generate_key_file()
    assert agekeys.resolve_identities() == [str(path), str(ssh_key)]


def test_resolve_identities_empty_when_nothing_exists(isolated_home, monkeypatch):
    monkeypatch.setattr(agekeys._cipher, 'default_identity_paths', lambda: [])
    assert agekeys.resolve_identities() == []


def test_save_recipients_is_idempotent_for_content(tmp_path):
    pubs = [str(pyrage.x25519.Identity.generate().to_public()) for _ in range(2)]
    agekeys.save_recipients(tmp_path, pubs)
    first = agekeys.recipients_file(tmp_path).read_text()
    agekeys.save_recipients(tmp_path, pubs)
    assert agekeys.recipients_file(tmp_path).read_text() == first
    assert agekeys.load_recipients(tmp_path) == pubs


def test_umask_does_not_widen_key_permissions(isolated_home):
    """umask 0 でも鍵が 0600 で作られる (作成時点から権限を絞る)"""
    old = os.umask(0)
    try:
        path, _ = agekeys.generate_key_file()
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
