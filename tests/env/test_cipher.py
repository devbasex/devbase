"""cipher.py: age 暗号化のラウンドトリップとエラー検出"""

from __future__ import annotations

import pyrage
import pytest

from devbase.env import cipher


@pytest.fixture
def x25519_keypair():
    identity = pyrage.x25519.Identity.generate()
    return str(identity.to_public()), str(identity)


def test_recipient_roundtrip_with_x25519(tmp_path, x25519_keypair):
    pub, priv_str = x25519_keypair
    id_path = tmp_path / "age_identity.key"
    id_path.write_text(priv_str)

    blob = cipher.encrypt(b"hello", recipients=[pub])
    assert blob != b"hello"
    assert cipher.decrypt(blob, identities=[str(id_path)]) == b"hello"


def test_passphrase_roundtrip():
    blob = cipher.encrypt(b"secret payload", passphrase="correct horse")
    assert cipher.decrypt(blob, passphrase="correct horse") == b"secret payload"


def test_passphrase_wrong_raises_cipher_error():
    blob = cipher.encrypt(b"x", passphrase="right")
    with pytest.raises(cipher.CipherError):
        cipher.decrypt(blob, passphrase="wrong")


def test_encrypt_requires_recipient_or_passphrase():
    with pytest.raises(cipher.CipherError):
        cipher.encrypt(b"x")


def test_encrypt_rejects_both_recipient_and_passphrase(x25519_keypair):
    pub, _ = x25519_keypair
    with pytest.raises(cipher.CipherError):
        cipher.encrypt(b"x", recipients=[pub], passphrase="p")


def test_recipient_at_file_reference(tmp_path, x25519_keypair):
    pub, priv_str = x25519_keypair
    pub_file = tmp_path / "age.pub"
    pub_file.write_text(pub + "\n")
    id_file = tmp_path / "age.key"
    id_file.write_text(priv_str)

    blob = cipher.encrypt(b"data", recipients=[f"@{pub_file}"])
    assert cipher.decrypt(blob, identities=[str(id_file)]) == b"data"


def test_recipient_rejects_unsupported_ssh_type():
    with pytest.raises(cipher.CipherError, match="ssh-ecdsa|ssh-"):
        cipher.encrypt(b"x", recipients=["ssh-ecdsa AAAA dummy"])


def test_recipient_at_file_reference_depth_limit(tmp_path):
    """@PATH の循環参照で RecursionError ではなく CipherError を返す"""
    # 互いを参照する 2 ファイル
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text(f"@{b}\n")
    b.write_text(f"@{a}\n")
    with pytest.raises(cipher.CipherError, match="深すぎ|循環"):
        cipher.encrypt(b"x", recipients=[f"@{a}"])


def test_recipient_at_file_reference_rejects_non_utf8(tmp_path):
    """@PATH ファイルが UTF-8 でない場合 CipherError に包んで送出"""
    bad = tmp_path / "bad.pub"
    # 0x80 は UTF-8 として不正な開始バイト
    bad.write_bytes(b"\x80\x81\x82\n")
    with pytest.raises(cipher.CipherError, match="UTF-8 デコード"):
        cipher.encrypt(b"x", recipients=[f"@{bad}"])


def test_default_recipient_paths_includes_ed25519():
    """ed25519 公開鍵が rsa より先に試される"""
    paths = cipher.default_recipient_paths()
    names = [p.name for p in paths]
    assert "id_ed25519.pub" in names
    assert "id_rsa.pub" in names
    # ed25519 を rsa より先に優先
    assert names.index("id_ed25519.pub") < names.index("id_rsa.pub")


def test_default_identity_paths_includes_ed25519():
    """ed25519 秘密鍵が rsa より先に試される"""
    paths = cipher.default_identity_paths()
    names = [p.name for p in paths]
    assert "id_ed25519" in names
    assert "id_rsa" in names
    assert names.index("id_ed25519") < names.index("id_rsa")
