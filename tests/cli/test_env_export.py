"""devbase env export の統合テスト (擬似 DEVBASE_ROOT)"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pyrage
import pytest

from devbase.env import bundle, cipher
from devbase.env.io_export import ExportOptions, ExportError, export


@pytest.fixture
def fake_root(tmp_path):
    root = tmp_path / "devbase-root"
    (root / "projects" / "alpha").mkdir(parents=True)
    (root / "projects" / "beta").mkdir(parents=True)
    (root / ".env").write_text("AWS_CONFIG_BASE64=AAAA\nGLOBAL=1\n")
    (root / ".env.sources.yml").write_text("sources: {}\n")
    (root / "projects" / "alpha" / ".env").write_text("ALPHA_API_KEY=xyz\n")
    (root / "projects" / "beta" / ".env").write_text("BETA_DB_PASSWORD=p\n")
    return root


@pytest.fixture
def age_keys(tmp_path):
    identity = pyrage.x25519.Identity.generate()
    pub_file = tmp_path / "age.pub"
    pub_file.write_text(str(identity.to_public()) + "\n")
    id_file = tmp_path / "age.key"
    id_file.write_text(str(identity))
    return pub_file, id_file


def test_export_local_with_recipient_roundtrips(fake_root, age_keys, tmp_path):
    pub_file, id_file = age_keys
    dest = tmp_path / "out.dbenv"

    rc = export(fake_root, ExportOptions(
        dest=str(dest),
        recipients=[f"@{pub_file}"],
    ))
    assert rc == 0
    assert dest.exists()
    assert dest.stat().st_mode & 0o777 == 0o600

    decrypted = cipher.decrypt(dest.read_bytes(), identities=[str(id_file)])
    manifest, members = bundle.unpack(decrypted)

    assert {e["path"] for e in manifest["files"]} == {
        "env/global.env",
        "env/sources.yml",
        "env/projects/alpha/.env",
        "env/projects/beta/.env",
    }
    assert members["env/global.env"] == b"AWS_CONFIG_BASE64=AAAA\nGLOBAL=1\n"
    assert members["env/projects/alpha/.env"] == b"ALPHA_API_KEY=xyz\n"


def test_export_rejects_unencrypted_by_default(fake_root, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "no-ssh"))
    dest = tmp_path / "out.dbenv"

    with pytest.raises(ExportError, match="暗号化キー"):
        export(fake_root, ExportOptions(dest=str(dest)))


def test_export_force_unencrypted_writes_plaintext_tar_gz(fake_root, tmp_path, caplog):
    dest = tmp_path / "out.dbenv.tar.gz"
    caplog.set_level("WARNING")
    rc = export(fake_root, ExportOptions(dest=str(dest), force_unencrypted=True))
    assert rc == 0

    # 機密キーが検知されて警告が出ること
    assert any("機密キー" in r.message for r in caplog.records)

    manifest, members = bundle.unpack(dest.read_bytes())
    assert "env/global.env" in members
    assert dest.stat().st_mode & 0o777 == 0o600


def test_export_rejects_stdout_with_passphrase_stdin(fake_root):
    with pytest.raises(ExportError, match="DEST='-'"):
        export(fake_root, ExportOptions(dest="-", passphrase_stdin=True))


def test_export_rejects_both_passphrase_env_and_stdin(fake_root):
    with pytest.raises(ExportError, match="--passphrase-env"):
        export(fake_root, ExportOptions(
            dest="/dev/null", passphrase_env="X", passphrase_stdin=True))


def test_export_with_passphrase_env(fake_root, tmp_path, monkeypatch):
    dest = tmp_path / "out.dbenv"
    monkeypatch.setenv("DEVBASE_TEST_PASS", "s3cr3t")
    rc = export(fake_root, ExportOptions(
        dest=str(dest), passphrase_env="DEVBASE_TEST_PASS"))
    assert rc == 0
    decrypted = cipher.decrypt(dest.read_bytes(), passphrase="s3cr3t")
    bundle.unpack(decrypted)


def test_export_include_exclude_projects(fake_root, age_keys, tmp_path):
    pub_file, id_file = age_keys
    dest = tmp_path / "out.dbenv"
    export(fake_root, ExportOptions(
        dest=str(dest),
        recipients=[f"@{pub_file}"],
        include_projects=["alpha"],
    ))
    decrypted = cipher.decrypt(dest.read_bytes(), identities=[str(id_file)])
    _, members = bundle.unpack(decrypted)
    assert "env/projects/alpha/.env" in members
    assert "env/projects/beta/.env" not in members


def test_export_stdout_with_recipient(fake_root, age_keys, capsysbinary):
    pub_file, id_file = age_keys
    rc = export(fake_root, ExportOptions(dest="-", recipients=[f"@{pub_file}"]))
    assert rc == 0
    out = capsysbinary.readouterr().out
    decrypted = cipher.decrypt(out, identities=[str(id_file)])
    bundle.unpack(decrypted)


def test_export_uses_default_recipient_if_present(fake_root, tmp_path, monkeypatch, age_keys):
    pub_file, id_file = age_keys
    fake_home = tmp_path / "fake-home"
    (fake_home / ".ssh").mkdir(parents=True)
    (fake_home / ".ssh" / "id_rsa.pub").write_text(pub_file.read_text())
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    dest = tmp_path / "out.dbenv"
    rc = export(fake_root, ExportOptions(dest=str(dest)))
    assert rc == 0
    decrypted = cipher.decrypt(dest.read_bytes(), identities=[str(id_file)])
    bundle.unpack(decrypted)
