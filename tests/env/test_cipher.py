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


def test_resolve_identity_wraps_oserror(tmp_path, monkeypatch):
    """identity ファイルの read_bytes が OSError を投げた場合 CipherError に包んで送出"""
    id_path = tmp_path / "identity.key"
    id_path.write_text("dummy")

    from pathlib import Path as _Path

    original_read_bytes = _Path.read_bytes

    def fake_read_bytes(self):
        if self == id_path:
            raise OSError("simulated I/O error")
        return original_read_bytes(self)

    monkeypatch.setattr(_Path, "read_bytes", fake_read_bytes)
    with pytest.raises(cipher.CipherError, match="読み込みに失敗"):
        cipher.decrypt(b"x", identities=[str(id_path)])


def test_resolve_recipient_at_path_skips_comments_and_blank_lines(tmp_path, x25519_keypair):
    """@PATH ファイル中のコメント行 / 空行をスキップして最初の有効な recipient を採用"""
    pub_path = tmp_path / "rcpt.pub"
    pub_path.write_text(
        "# this is a comment\n"
        "\n"
        f"{x25519_keypair[0]}\n"
        "# trailing comment\n"
    )
    ciphertext = cipher.encrypt(b"hello", recipients=[f"@{pub_path}"])
    # 復号できれば有効な recipient として解釈されている
    id_path = tmp_path / "id.key"
    id_path.write_text(x25519_keypair[1])
    plain = cipher.decrypt(ciphertext, identities=[str(id_path)])
    assert plain == b"hello"


def test_resolve_recipient_at_path_rejects_only_comments(tmp_path):
    """@PATH ファイルがコメント・空行のみだと CipherError"""
    pub_path = tmp_path / "empty.pub"
    pub_path.write_text("# only comments\n\n# nothing else\n")
    with pytest.raises(cipher.CipherError, match="有効な行がありません"):
        cipher.encrypt(b"x", recipients=[f"@{pub_path}"])


def test_resolve_recipient_at_path_rejects_multiple_keys(tmp_path, x25519_keypair):
    """@PATH ファイルに複数の鍵を列挙したら CipherError で明示的に拒否される。

    暗黙に最初の 1 行だけ採用すると、`team_keys.txt` のような複数公開鍵ファイル
    を渡したケースで「最初の 1 人」だけにしか暗号化されず、他メンバーの復号が
    壊れる。誤運用を防ぐため明確にエラーを返す (PR #13 gemini 指摘)。
    """
    pub_a, _ = x25519_keypair
    # 2 つ目の鍵を別途生成
    pub_b = str(pyrage.x25519.Identity.generate().to_public())

    team_keys = tmp_path / "team_keys.txt"
    team_keys.write_text(
        f"# alice\n{pub_a}\n# bob\n{pub_b}\n"
    )
    with pytest.raises(cipher.CipherError, match="複数行の鍵|1 鍵で指定"):
        cipher.encrypt(b"x", recipients=[f"@{team_keys}"])


def test_resolve_recipient_at_path_wraps_oserror(tmp_path, monkeypatch):
    """@PATH の read_text が OSError を投げた場合 CipherError に包んで送出"""
    rcpt_path = tmp_path / "rcpt.pub"
    rcpt_path.write_text("dummy")

    from pathlib import Path as _Path

    original_read_text = _Path.read_text

    def fake_read_text(self, *args, **kwargs):
        if self == rcpt_path:
            raise PermissionError("simulated permission denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "read_text", fake_read_text)
    with pytest.raises(cipher.CipherError, match="読み込みに失敗"):
        cipher.encrypt(b"x", recipients=[f"@{rcpt_path}"])


def test_resolve_identity_prefers_openssh_header(tmp_path):
    """OpenSSH ヘッダで始まる秘密鍵は age 鍵判定より先に SSH として処理される"""
    # 中身は不正でも、OpenSSH ヘッダで判別された後 pyrage 側エラーになる
    # ことを確認 (= age 経路ではなく SSH 経路に入った証拠)
    id_path = tmp_path / "id.key"
    id_path.write_bytes(
        b"-----BEGIN OPENSSH PRIVATE KEY-----\n"
        b"not-a-valid-key\n"
        b"-----END OPENSSH PRIVATE KEY-----\n"
    )
    with pytest.raises(cipher.CipherError, match="OpenSSH 秘密鍵の解釈"):
        cipher.decrypt(b"x", identities=[str(id_path)])


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


def test_resolve_identity_accepts_age_keygen_output_with_comments(
        tmp_path, x25519_keypair):
    """``age-keygen`` が生成する秘密鍵ファイル (先頭に ``# created`` / ``# public key``
    のコメント行) を age 鍵として正しく検出して復号できること (PR #13 gemini 指摘)。
    """
    pub, priv_str = x25519_keypair

    # age-keygen の出力フォーマットを再現
    keygen_output = (
        f"# created: 2024-01-01T00:00:00Z\n"
        f"# public key: {pub}\n"
        f"{priv_str}\n"
    )
    id_path = tmp_path / "age-keygen.key"
    id_path.write_text(keygen_output)

    blob = cipher.encrypt(b"payload", recipients=[pub])
    assert cipher.decrypt(blob, identities=[str(id_path)]) == b"payload"
