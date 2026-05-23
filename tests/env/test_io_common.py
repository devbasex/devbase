"""io_common.py: resolve_recipient_specs / resolve_identity_specs の挙動"""

from __future__ import annotations

import pyrage
import pytest

from devbase.env import cipher
from devbase.env import io_common


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """``Path.home()`` を ``tmp_path`` に差し替える"""
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_resolve_recipient_specs_returns_first_existing_default(fake_home):
    """recipient は「どの鍵で暗号化するか」を一意に決める必要があるため、
    既定鍵が複数存在しても最初に見つかったものだけ返す (ed25519 を優先)。"""
    ssh = fake_home / ".ssh"
    ssh.mkdir()
    (ssh / "id_ed25519.pub").write_text("ssh-ed25519 AAAA dummy\n")
    (ssh / "id_rsa.pub").write_text("ssh-rsa AAAA dummy\n")

    specs = io_common.resolve_recipient_specs([])
    assert len(specs) == 1
    assert specs[0].endswith("id_ed25519.pub")


def test_resolve_recipient_specs_explicit_passthrough(fake_home):
    """明示指定があれば既定鍵探索は行わない (そのまま返す)"""
    ssh = fake_home / ".ssh"
    ssh.mkdir()
    (ssh / "id_ed25519.pub").write_text("ssh-ed25519 AAAA dummy\n")

    specs = io_common.resolve_recipient_specs(["age1example"])
    assert specs == ["age1example"]


def test_resolve_recipient_specs_returns_empty_when_no_defaults(fake_home):
    """既定鍵が見つからなければ空 list"""
    assert io_common.resolve_recipient_specs([]) == []


def test_resolve_identity_specs_returns_all_existing_defaults(fake_home):
    """identity は「どの鍵で暗号化されたか」が事前に分からないため、
    存在するすべての既定鍵を返す。``pyrage.decrypt`` は複数 identity を
    受け取れる仕様なので、両方渡しておけばどちらの鍵で暗号化されたバンドル
    でも復号できる (PR #13 gemini 指摘)。"""
    ssh = fake_home / ".ssh"
    ssh.mkdir()
    (ssh / "id_ed25519").write_text("dummy ed25519 key\n")
    (ssh / "id_rsa").write_text("dummy rsa key\n")

    specs = io_common.resolve_identity_specs([])
    assert len(specs) == 2
    # ed25519 が先に来る (default_identity_paths の順序を維持)
    assert specs[0].endswith("id_ed25519")
    assert specs[1].endswith("id_rsa")


def test_resolve_identity_specs_returns_only_existing(fake_home):
    """片方しか存在しなければそれだけ返す"""
    ssh = fake_home / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("dummy\n")

    specs = io_common.resolve_identity_specs([])
    assert len(specs) == 1
    assert specs[0].endswith("id_rsa")


def test_resolve_identity_specs_explicit_passthrough(fake_home):
    """明示指定があれば既定鍵探索は行わない"""
    ssh = fake_home / ".ssh"
    ssh.mkdir()
    (ssh / "id_ed25519").write_text("dummy\n")

    specs = io_common.resolve_identity_specs(["/path/to/explicit.key"])
    assert specs == ["/path/to/explicit.key"]


def test_resolve_identity_specs_returns_empty_when_no_defaults(fake_home):
    """既定鍵が一切無ければ空"""
    assert io_common.resolve_identity_specs([]) == []


def test_decrypt_uses_correct_identity_from_multiple_defaults(tmp_path, fake_home):
    """``resolve_identity_specs`` が返した複数 identity を ``cipher.decrypt`` に
    渡すと、その中から正しい identity が選ばれて復号される。

    シナリオ: 既定 ssh 鍵が 2 つ (id_ed25519 / id_rsa) 存在する状況を模した上で、
    `id_rsa` (実体は age 鍵) で暗号化したバンドルを「両方の identity を試す」
    `cipher.decrypt(identities=[both])` で復号できることを確認する。
    `id_ed25519` 側は別 age 鍵で、こちらは復号に使われない。
    """
    # 異なる 2 つの age 鍵を用意し、ssh 既定パスに配置して
    # resolve_identity_specs から両方が返るようにする
    id1 = pyrage.x25519.Identity.generate()
    id2 = pyrage.x25519.Identity.generate()

    ssh = fake_home / ".ssh"
    ssh.mkdir()
    ed_path = ssh / "id_ed25519"
    rsa_path = ssh / "id_rsa"
    ed_path.write_text(str(id1))   # ed25519 スロットに id1
    rsa_path.write_text(str(id2))  # rsa スロットに id2 (=暗号化に使う鍵)

    # id2 の公開鍵だけで暗号化 → id1 では復号できないバンドル
    blob = cipher.encrypt(b"team-secret", recipients=[str(id2.to_public())])

    # resolve_identity_specs は両方返す
    identities = io_common.resolve_identity_specs([])
    assert len(identities) == 2

    # 両 identity を渡して復号 → pyrage が正しい鍵 (id2) を選んで復号する
    plain = cipher.decrypt(blob, identities=identities)
    assert plain == b"team-secret"
