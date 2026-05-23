"""devbase env export の統合テスト (擬似 DEVBASE_ROOT)"""

from __future__ import annotations

import io
from pathlib import Path

import pyrage
import pytest

from devbase.env import bundle, cipher
from devbase.env.io_export import (
    ExportOptions, ExportError, _default_dest, _read_passphrase,
    _validate_options, export,
)


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


def test_export_allows_stdout_with_passphrase_stdin(
    fake_root, age_keys, monkeypatch, capsysbinary
):
    """DEST='-' (stdout) と --passphrase-stdin の併用は許可される。

    stdin (passphrase) と stdout (bundle) は別ストリームのため衝突しない:
        echo "pass" | devbase env export - --passphrase-stdin > out.dbenv
    """
    fake_stdin = io.StringIO("hunter2\n")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    rc = export(fake_root, ExportOptions(dest="-", passphrase_stdin=True))
    assert rc == 0

    out_bytes = capsysbinary.readouterr().out
    assert len(out_bytes) > 0
    # age 暗号化ヘッダ (passphrase mode) — `age-encryption.org/v1` を含む
    decrypted = cipher.decrypt(out_bytes, passphrase="hunter2")
    manifest, members = bundle.unpack(decrypted)
    assert "env/global.env" in members


def test_export_rejects_both_passphrase_env_and_stdin(fake_root):
    with pytest.raises(ExportError, match="--passphrase-env"):
        export(fake_root, ExportOptions(
            dest="/dev/null", passphrase_env="X", passphrase_stdin=True))


def test_export_rejects_recipient_and_passphrase_combo(
    fake_root, age_keys, tmp_path, monkeypatch
):
    """--recipient と --passphrase-* を同時指定したら ExportError を上げる。
    黙って recipients=[] に上書きしてパスフレーズだけで暗号化するのは
    ユーザの意図と異なるため明示的に拒否する (cipher.encrypt 側のチェックに
    到達する前にここで弾く)。"""
    pub_file, _ = age_keys
    monkeypatch.setenv("DEVBASE_TEST_PASS", "s3cr3t")
    dest = tmp_path / "out.dbenv"
    with pytest.raises(ExportError, match="--recipient"):
        export(fake_root, ExportOptions(
            dest=str(dest),
            recipients=[f"@{pub_file}"],
            passphrase_env="DEVBASE_TEST_PASS",
        ))
    assert not dest.exists()


def test_read_passphrase_uses_getpass_on_tty(monkeypatch):
    """tty 入力時は getpass.getpass を使い stdin.readline は呼ばない (エコー抑止)"""
    fake_stdin = io.StringIO("should-not-be-read\n")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    calls = {}

    def fake_getpass(prompt='', stream=None):
        calls['prompt'] = prompt
        calls['stream'] = stream
        return "hunter2"

    monkeypatch.setattr("devbase.env.io_export.getpass.getpass", fake_getpass)

    pw = _read_passphrase(ExportOptions(passphrase_stdin=True))
    assert pw == "hunter2"
    assert calls['prompt'] == "passphrase: "
    assert fake_stdin.read() == "should-not-be-read\n"  # stdin は消費されていない


def test_read_passphrase_falls_back_to_stdin_on_pipe(monkeypatch, capsys):
    """パイプ (非 tty) 入力時は getpass を使わず stdin.readline で読む"""
    fake_stdin = io.StringIO("hunter2\n")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    def fail_getpass(*args, **kwargs):
        raise AssertionError("getpass.getpass should not be called for piped stdin")

    monkeypatch.setattr("devbase.env.io_export.getpass.getpass", fail_getpass)

    pw = _read_passphrase(ExportOptions(passphrase_stdin=True))
    assert pw == "hunter2"
    assert "passphrase" not in capsys.readouterr().err


def test_read_passphrase_strips_crlf_from_pipe(monkeypatch):
    """Windows/WSL 由来の CRLF パイプ入力でも末尾 \\r が混入しないこと。

    `\\r` が残ると age 復号は無音で失敗するため、対称的に `rstrip('\\r\\n')` が必要。
    """
    fake_stdin = io.StringIO("hunter2\r\n")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    pw = _read_passphrase(ExportOptions(passphrase_stdin=True))
    assert pw == "hunter2"


def test_read_passphrase_tty_eof_raises_export_error(monkeypatch):
    """tty で getpass が EOFError を投げた場合は ExportError に変換される"""
    fake_stdin = io.StringIO("")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    def raise_eof(*args, **kwargs):
        raise EOFError()

    monkeypatch.setattr("devbase.env.io_export.getpass.getpass", raise_eof)

    with pytest.raises(ExportError, match="パスフレーズを読み取れません"):
        _read_passphrase(ExportOptions(passphrase_stdin=True))


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


# --- fail-fast 排他チェック (PR #22 round2 gemini 指摘) ---


def test_validate_rejects_recipient_and_passphrase_env():
    """_validate_options で --recipient + --passphrase-env が即座に弾かれること。

    ディスク I/O (make_entries_from_disk / pack) より前に ExportError になる。
    """
    with pytest.raises(ExportError, match="--recipient"):
        _validate_options(ExportOptions(
            recipients=["age1dummy"],
            passphrase_env="SOME_VAR",
        ))


def test_validate_rejects_recipient_and_passphrase_stdin():
    """_validate_options で --recipient + --passphrase-stdin が即座に弾かれること。"""
    with pytest.raises(ExportError, match="--recipient"):
        _validate_options(ExportOptions(
            recipients=["age1dummy"],
            passphrase_stdin=True,
        ))


def test_validate_rejects_force_unencrypted_with_recipient():
    """_validate_options で --force-unencrypted + --recipient が即座に弾かれること。"""
    with pytest.raises(ExportError, match="--force-unencrypted"):
        _validate_options(ExportOptions(
            force_unencrypted=True,
            recipients=["age1dummy"],
        ))


def test_validate_rejects_force_unencrypted_with_passphrase_env():
    """_validate_options で --force-unencrypted + --passphrase-env が即座に弾かれること。"""
    with pytest.raises(ExportError, match="--force-unencrypted"):
        _validate_options(ExportOptions(
            force_unencrypted=True,
            passphrase_env="SOME_VAR",
        ))


def test_validate_rejects_force_unencrypted_with_passphrase_stdin():
    """_validate_options で --force-unencrypted + --passphrase-stdin が即座に弾かれること。"""
    with pytest.raises(ExportError, match="--force-unencrypted"):
        _validate_options(ExportOptions(
            force_unencrypted=True,
            passphrase_stdin=True,
        ))


# --- default dest 衝突回避 (PR #22 codex round 3 指摘) ---


def test_default_dest_includes_microsecond():
    """既定出力名が microsecond 精度を含むこと"""
    name = _default_dest(force_unencrypted=False)
    # ./devbase-env-YYYYMMDD-HHMMSS-ffffff.dbenv
    import re
    assert re.match(r'^\./devbase-env-\d{8}-\d{6}-\d{6}\.dbenv$', name), name


def test_export_default_dest_rejects_existing_file(
        fake_root, age_keys, tmp_path, monkeypatch):
    """既定出力先に同名ファイルが既に存在する場合は ExportError を上げる"""
    pub_file, _ = age_keys
    # _default_dest を固定して衝突を再現する
    fixed_name = "./devbase-env-20240101-120000-000000.dbenv"
    monkeypatch.setattr("devbase.env.io_export._default_dest", lambda fu: fixed_name)
    # 既存ファイルを作成
    existing = tmp_path / "devbase-env-20240101-120000-000000.dbenv"
    existing.write_bytes(b"old data")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ExportError, match="既に存在します"):
        export(fake_root, ExportOptions(
            recipients=[f"@{pub_file}"],
        ))


def test_export_empty_dest_rejects_existing_file(
        fake_root, age_keys, tmp_path, monkeypatch):
    """opts.dest が空文字 "" の場合も既定名が使われ、既存ファイル上書きを拒否する。

    opts.dest="" は falsy なので `not opts.dest` で None と同様に既定名ガードが
    有効になること。(PR #22 round4 gemini 指摘)
    """
    pub_file, _ = age_keys
    fixed_name = "./devbase-env-20240101-120000-000000.dbenv"
    monkeypatch.setattr("devbase.env.io_export._default_dest", lambda fu: fixed_name)
    existing = tmp_path / "devbase-env-20240101-120000-000000.dbenv"
    existing.write_bytes(b"old data")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ExportError, match="既に存在します"):
        export(fake_root, ExportOptions(
            dest="",  # 空文字 — None と同様に既定名ガードが効くこと
            recipients=[f"@{pub_file}"],
        ))
