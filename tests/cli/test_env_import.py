"""devbase env import の統合テスト (擬似 DEVBASE_ROOT で round-trip / merge / replace / dry-run)"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Tuple

import pyrage
import pytest

from devbase.env import bundle, cipher
from devbase.env.io_export import ExportOptions, export
from devbase.env.io_import import (
    ImportError as ImportBundleError,
    ImportOptions,
    _read_passphrase,
    import_bundle,
)


@pytest.fixture
def fake_root(tmp_path):
    """export 用の擬似 DEVBASE_ROOT (PR1 と同じ構造)"""
    root = tmp_path / "src-root"
    (root / "projects" / "alpha").mkdir(parents=True)
    (root / "projects" / "beta").mkdir(parents=True)
    (root / ".env").write_text("AWS_CONFIG_BASE64=AAAA\nGLOBAL=1\n")
    (root / ".env.sources.yml").write_text(
        "sources:\n  aws:\n    type: tar_base64\n    hash: deadbeef\n"
    )
    (root / "projects" / "alpha" / ".env").write_text("ALPHA_API_KEY=xyz\n")
    (root / "projects" / "beta" / ".env").write_text("BETA_DB_PASSWORD=p\n")
    return root


@pytest.fixture
def dest_root(tmp_path):
    """import 先の擬似 DEVBASE_ROOT (空 or 既存ファイルあり)"""
    root = tmp_path / "dst-root"
    (root / "projects" / "alpha").mkdir(parents=True)
    (root / "projects" / "beta").mkdir(parents=True)
    return root


@pytest.fixture
def age_keys(tmp_path):
    identity = pyrage.x25519.Identity.generate()
    pub_file = tmp_path / "age.pub"
    pub_file.write_text(str(identity.to_public()) + "\n")
    id_file = tmp_path / "age.key"
    id_file.write_text(str(identity))
    return pub_file, id_file


def _export_bundle(fake_root: Path, age_keys: Tuple[Path, Path],
                   tmp_path: Path) -> Path:
    pub_file, _ = age_keys
    dest = tmp_path / "out.dbenv"
    rc = export(fake_root, ExportOptions(
        dest=str(dest), recipients=[f"@{pub_file}"]))
    assert rc == 0
    return dest


def test_import_roundtrip_creates_files_with_0600(fake_root, dest_root, age_keys, tmp_path):
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)]))
    assert rc == 0

    # global と各 project の .env が復元されている
    assert (dest_root / ".env").read_text() == "AWS_CONFIG_BASE64=AAAA\nGLOBAL=1\n"
    assert (dest_root / "projects" / "alpha" / ".env").read_text() == "ALPHA_API_KEY=xyz\n"
    assert (dest_root / "projects" / "beta" / ".env").read_text() == "BETA_DB_PASSWORD=p\n"

    # パーミッションが 0600
    assert (dest_root / ".env").stat().st_mode & 0o777 == 0o600
    assert (dest_root / "projects" / "alpha" / ".env").stat().st_mode & 0o777 == 0o600

    # sources.yml は既定では上書きしないので存在しない
    assert not (dest_root / ".env.sources.yml").exists()
    # backup ディレクトリに参照用 sources.yml.imported が残る
    backup_root = dest_root / "backups" / "env-import"
    assert backup_root.is_dir()
    sub = next(backup_root.iterdir())
    assert (sub / "sources.yml.imported").exists()


def test_import_dry_run_does_not_modify(fake_root, dest_root, age_keys, tmp_path):
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    # 既存ファイルを置く
    (dest_root / ".env").write_text("EXISTING=keep\n")
    os.chmod(dest_root / ".env", 0o600)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)], dry_run=True))
    assert rc == 0

    # 元の .env は変更されていない
    assert (dest_root / ".env").read_text() == "EXISTING=keep\n"
    # backup も作られない
    assert not (dest_root / "backups").exists()


def test_import_keep_existing_only_adds_new_keys(fake_root, dest_root, age_keys, tmp_path):
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    (dest_root / ".env").write_text("AWS_CONFIG_BASE64=OLD\nKEEP=this\n")
    os.chmod(dest_root / ".env", 0o600)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)]))
    assert rc == 0

    text = (dest_root / ".env").read_text()
    # 既存の AWS_CONFIG_BASE64 は OLD のまま (keep-existing)
    assert "AWS_CONFIG_BASE64=OLD" in text
    # 新規キー GLOBAL=1 は追加される
    assert "GLOBAL=1" in text
    # 既存キー KEEP は残る
    assert "KEEP=this" in text


def test_import_prefer_incoming_overwrites_existing(fake_root, dest_root, age_keys, tmp_path):
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    (dest_root / ".env").write_text("AWS_CONFIG_BASE64=OLD\nKEEP=this\n")
    os.chmod(dest_root / ".env", 0o600)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)],
        merge='prefer-incoming'))
    assert rc == 0

    text = (dest_root / ".env").read_text()
    # バンドル側で上書きされる
    assert "AWS_CONFIG_BASE64=AAAA" in text
    # incoming に無い既存キーは残る
    assert "KEEP=this" in text


def test_import_replace_keys_only_overwrites_specified(fake_root, dest_root, age_keys, tmp_path):
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    (dest_root / ".env").write_text("AWS_CONFIG_BASE64=OLD\nGLOBAL=KEEP\n")
    os.chmod(dest_root / ".env", 0o600)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)],
        replace_keys=['AWS_CONFIG_BASE64']))
    assert rc == 0

    text = (dest_root / ".env").read_text()
    assert "AWS_CONFIG_BASE64=AAAA" in text   # 上書きされる
    assert "GLOBAL=KEEP" in text               # 指定外なので keep


def test_import_replace_takes_backup_and_replaces(fake_root, dest_root, age_keys, tmp_path):
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    (dest_root / ".env").write_text("OLD=value\n")
    os.chmod(dest_root / ".env", 0o600)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)], replace=True))
    assert rc == 0

    text = (dest_root / ".env").read_text()
    assert "AWS_CONFIG_BASE64=AAAA" in text
    assert "GLOBAL=1" in text
    assert "OLD=value" not in text  # 完全に置き換わる

    backup_root = dest_root / "backups" / "env-import"
    sub = next(backup_root.iterdir())
    assert (sub / ".env").read_text() == "OLD=value\n"


def test_import_rejects_replace_with_replace_keys(fake_root, dest_root, age_keys, tmp_path):
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)
    with pytest.raises(ImportBundleError, match="--replace と --replace-keys"):
        import_bundle(dest_root, ImportOptions(
            source=str(bundle_path), identities=[str(id_file)],
            replace=True, replace_keys=['A']))


def test_import_rejects_stdin_with_passphrase_stdin(dest_root):
    with pytest.raises(ImportBundleError, match="SOURCE='-'"):
        import_bundle(dest_root, ImportOptions(
            source='-', passphrase_stdin=True))


def test_import_rejects_both_passphrase_env_and_stdin(dest_root):
    with pytest.raises(ImportBundleError, match="--passphrase-env"):
        import_bundle(dest_root, ImportOptions(
            source='/dev/null', passphrase_env='X', passphrase_stdin=True))


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

    monkeypatch.setattr("devbase.env.io_import.getpass.getpass", fake_getpass)

    pw = _read_passphrase(ImportOptions(source='/dev/null', passphrase_stdin=True))
    assert pw == "hunter2"
    assert calls['prompt'] == "passphrase: "
    assert fake_stdin.read() == "should-not-be-read\n"  # stdin は消費されていない


def test_read_passphrase_falls_back_to_stdin_on_pipe(monkeypatch, capsys):
    """パイプ (非 tty) 入力時は getpass を使わず stdin.readline で読む"""
    fake_stdin = io.StringIO("piped-pass\n")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    def fail_getpass(*args, **kwargs):
        raise AssertionError("getpass.getpass should not be called for piped stdin")

    monkeypatch.setattr("devbase.env.io_import.getpass.getpass", fail_getpass)

    pw = _read_passphrase(ImportOptions(source='/dev/null', passphrase_stdin=True))
    assert pw == "piped-pass"
    assert "passphrase" not in capsys.readouterr().err


def test_read_passphrase_tty_eof_raises_import_error(monkeypatch):
    """tty で getpass が EOFError を投げた場合は ImportError に変換される"""
    fake_stdin = io.StringIO("")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    def raise_eof(*args, **kwargs):
        raise EOFError()

    monkeypatch.setattr("devbase.env.io_import.getpass.getpass", raise_eof)

    with pytest.raises(ImportBundleError, match="パスフレーズを読み取れません"):
        _read_passphrase(ImportOptions(source='/dev/null', passphrase_stdin=True))


def test_import_rejects_unknown_manifest_version(fake_root, dest_root, age_keys, tmp_path):
    """manifest.version が SUPPORTED_MANIFEST_VERSION より大きいバンドルは拒否される"""
    import gzip
    import io as _io
    import tarfile
    import yaml

    pub_file, id_file = age_keys
    # 通常 export
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)
    # 復号して中身を書き換えてから age で再暗号化する
    plain = cipher.decrypt(bundle_path.read_bytes(), identities=[str(id_file)])

    # tar.gz を再構築して manifest.version=999 にする
    buf_in = _io.BytesIO(plain)
    tin = tarfile.open(fileobj=buf_in, mode='r:gz')
    out = _io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode='wb', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT) as tout:
            for info in tin.getmembers():
                data = tin.extractfile(info).read()
                if info.name == bundle.MANIFEST_NAME:
                    manifest = yaml.safe_load(data)
                    manifest['version'] = 999
                    data = yaml.safe_dump(manifest, sort_keys=False).encode('utf-8')
                ti = tarfile.TarInfo(name=info.name)
                ti.size = len(data)
                ti.mtime = 0
                ti.mode = 0o600
                tout.addfile(ti, _io.BytesIO(data))
    tin.close()

    bad_plain = out.getvalue()
    bad = pyrage.encrypt(bad_plain,
                         [pyrage.ssh.Recipient.from_str(pub_file.read_text().strip())]
                         if pub_file.read_text().strip().startswith('ssh-')
                         else [pyrage.x25519.Recipient.from_str(pub_file.read_text().strip())])
    bad_path = tmp_path / "bad.dbenv"
    bad_path.write_bytes(bad)

    with pytest.raises(bundle.BundleError, match="サポートされていません"):
        import_bundle(dest_root, ImportOptions(
            source=str(bad_path), identities=[str(id_file)]))


def test_import_preserves_lf_line_endings(fake_root, dest_root, age_keys, tmp_path):
    _, id_file = age_keys
    # CRLF を排除した想定: export → import で LF が保持されること
    (fake_root / ".env").write_text("A=1\nB=2\n")
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)]))
    assert rc == 0
    raw = (dest_root / ".env").read_bytes()
    assert b'\r' not in raw
    assert raw.endswith(b'\n')


def test_import_keep_last_gc_removes_old_backups(fake_root, dest_root, age_keys, tmp_path):
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    backup_root = dest_root / "backups" / "env-import"
    # 既存の古い backup を 5 個事前作成する (タイムスタンプ命名規則に合わせる)
    backup_root.mkdir(parents=True)
    for i in range(5):
        (backup_root / f"20260101-00000{i}").mkdir()

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)], keep_last=3))
    assert rc == 0

    remaining = sorted(p.name for p in backup_root.iterdir())
    assert len(remaining) == 3
    # 最新 3 個に絞られる: 既存の 20260101-000003, 000004, 加えて新規 backup
    assert remaining[-1].startswith('20')


def test_import_include_project_filter(fake_root, dest_root, age_keys, tmp_path):
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)],
        include_projects=['alpha']))
    assert rc == 0
    assert (dest_root / "projects" / "alpha" / ".env").exists()
    assert not (dest_root / "projects" / "beta" / ".env").exists()


def test_import_plaintext_bundle(fake_root, dest_root, tmp_path):
    """--force-unencrypted で出力した平文 tar.gz もそのまま import できる"""
    dest = tmp_path / "out.dbenv.tar.gz"
    rc = export(fake_root, ExportOptions(dest=str(dest), force_unencrypted=True))
    assert rc == 0

    rc = import_bundle(dest_root, ImportOptions(source=str(dest)))
    assert rc == 0
    assert (dest_root / ".env").exists()


def test_import_merge_metadata_adds_only_new_sources(fake_root, dest_root, age_keys, tmp_path):
    _, id_file = age_keys
    # 既存 sources.yml を用意 (aws のみ。bundle 側も aws を持つ)
    (dest_root / ".env.sources.yml").write_text(
        "sources:\n  aws:\n    type: tar_base64\n    hash: existinghash\n"
    )
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)],
        merge_metadata=True))
    assert rc == 0

    import yaml as _yaml
    data = _yaml.safe_load((dest_root / ".env.sources.yml").read_text())
    # 既存 aws は維持される (hash=existinghash のまま)
    assert data['sources']['aws']['hash'] == 'existinghash'


def test_import_no_metadata_skips_sources_yml(fake_root, dest_root, age_keys, tmp_path):
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)],
        include_metadata=False))
    assert rc == 0
    # 参照用コピーも作られない (filter で除外されるため)
    backup_root = dest_root / "backups" / "env-import"
    sub = next(backup_root.iterdir())
    assert not (sub / "sources.yml.imported").exists()


def test_import_replace_keys_adds_unspecified_new_keys(fake_root, dest_root, age_keys, tmp_path):
    """--replace-keys 指定外でも、既存ファイルに無い incoming キーは追加される
    (CLI help 'other keys behave like keep-existing' に整合)"""
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    # 既存は AWS_CONFIG_BASE64 のみ。incoming は AWS_CONFIG_BASE64 + GLOBAL=1
    (dest_root / ".env").write_text("AWS_CONFIG_BASE64=OLD\n")
    os.chmod(dest_root / ".env", 0o600)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)],
        replace_keys=['AWS_CONFIG_BASE64']))
    assert rc == 0

    text = (dest_root / ".env").read_text()
    assert "AWS_CONFIG_BASE64=AAAA" in text  # 指定キーは上書き
    assert "GLOBAL=1" in text  # 指定外でも既存に無い新規キーは追加される (keep-existing 相当)


def test_rollback_unlinks_newly_created_files_on_commit_failure(
        fake_root, dest_root, age_keys, tmp_path, monkeypatch):
    """commit フェーズ途中失敗時、元ファイル不在で新規作成された target は unlink され、
    部分適用状態が残らないこと"""
    from devbase.env import _import_atomic as _atomic
    from devbase.env import io_import as _io_import

    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    # dest には元ファイルが一切無い (= 全 plan op='create')
    assert not (dest_root / ".env").exists()
    assert not (dest_root / "projects" / "alpha" / ".env").exists()

    # 2 つ目以降の os.replace で失敗させる
    original_replace = os.replace
    call_count = {'n': 0}

    def failing_replace(src, dst):
        call_count['n'] += 1
        if call_count['n'] >= 2:
            raise OSError("simulated commit failure")
        return original_replace(src, dst)

    monkeypatch.setattr(_atomic.os, 'replace', failing_replace)

    with pytest.raises(_io_import.ImportError, match="commit フェーズで失敗"):
        import_bundle(dest_root, ImportOptions(
            source=str(bundle_path), identities=[str(id_file)]))

    # rollback で新規作成 (op='create') の .env は削除されていること
    assert not (dest_root / ".env").exists()
    # まだ commit されていない target ももちろん存在しない
    assert not (dest_root / "projects" / "beta" / ".env").exists()


def test_gc_backups_only_removes_timestamp_dirs(fake_root, dest_root, age_keys, tmp_path):
    """--backup-dir 指定時でも、devbase が作った timestamp 形式以外のディレクトリは
    GC で削除されない"""
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    custom_backup_root = tmp_path / "user-backups"
    custom_backup_root.mkdir()
    # 関係ないディレクトリ
    unrelated = custom_backup_root / "important-user-data"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("must not be deleted")
    # 関係ないファイル
    unrelated_file = custom_backup_root / "readme.txt"
    unrelated_file.write_text("must not be deleted")
    # devbase 命名の古い backup を keep_last 超に置く
    for i in range(5):
        (custom_backup_root / f"20240101-00000{i}").mkdir()

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)],
        backup_dir=str(custom_backup_root), keep_last=3))
    assert rc == 0

    # 無関係なディレクトリ/ファイルは残る
    assert unrelated.exists()
    assert (unrelated / "keep.txt").exists()
    assert unrelated_file.exists()
    # timestamp 形式は keep_last=3 まで絞られる (新規 backup 含む)
    timestamp_dirs = sorted(
        p.name for p in custom_backup_root.iterdir()
        if p.is_dir() and p.name not in ('important-user-data',)
    )
    assert len(timestamp_dirs) == 3


def test_import_passphrase_env_roundtrip(fake_root, dest_root, tmp_path, monkeypatch):
    dest = tmp_path / "out.dbenv"
    monkeypatch.setenv("DEVBASE_TEST_PASS", "s3cr3t")
    rc = export(fake_root, ExportOptions(
        dest=str(dest), passphrase_env="DEVBASE_TEST_PASS"))
    assert rc == 0

    rc = import_bundle(dest_root, ImportOptions(
        source=str(dest), passphrase_env="DEVBASE_TEST_PASS"))
    assert rc == 0
    assert (dest_root / ".env").exists()


def test_import_preserves_escaped_values_no_double_escape(
        dest_root, age_keys, tmp_path):
    """値に backslash / quote / newline / spaces が含まれていても
    export → import で二重エスケープされないことを保証する (PR #15 codex 指摘)"""
    _, id_file = age_keys
    pub_file, _ = age_keys

    # 特殊文字を含む .env を持つ source root を構築
    src_root = tmp_path / "esc-src"
    (src_root / "projects" / "alpha").mkdir(parents=True)
    raw_env = (
        'BACKSLASH="a\\\\b"\n'              # 値: a\b (3 chars)
        'QUOTE_IN_VALUE="he said \\"hi\\""\n'  # 値: he said "hi"
        'WITH_NEWLINE="line1\\nline2"\n'    # 値: line1<newline>line2
        'WITH_SPACE="value with space"\n'   # 値: value with space
        'PLAIN=simple\n'                    # 値: simple
    )
    (src_root / ".env").write_text(raw_env)
    (src_root / "projects" / "alpha" / ".env").write_text(
        'ALPHA_BACK="a\\\\b"\n'
    )

    bundle_path = tmp_path / "esc.dbenv"
    rc = export(src_root, ExportOptions(
        dest=str(bundle_path), recipients=[f"@{pub_file}"]))
    assert rc == 0

    # 新規作成 (dest 側に既存ファイル無し)
    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)]))
    assert rc == 0

    # 新規作成時は incoming_bytes をそのまま使うので元バイト列と一致する
    assert (dest_root / ".env").read_text() == raw_env

    # EnvFile から読んだ際に escape が正しく解釈されること (parse_bytes round-trip)
    from devbase.env.store import EnvFile
    parsed = EnvFile.parse_bytes((dest_root / ".env").read_bytes())
    assert parsed['BACKSLASH'] == 'a\\b'
    assert parsed['QUOTE_IN_VALUE'] == 'he said "hi"'
    assert parsed['WITH_NEWLINE'] == 'line1\nline2'
    assert parsed['WITH_SPACE'] == 'value with space'
    assert parsed['PLAIN'] == 'simple'


def test_import_merge_round_trips_escaped_values(
        dest_root, age_keys, tmp_path):
    """既存ファイルがあって merge する場合でも、parse → format の round-trip で
    値が壊れない (二重エスケープしない)"""
    _, id_file = age_keys
    pub_file, _ = age_keys

    src_root = tmp_path / "esc-src2"
    (src_root / "projects" / "alpha").mkdir(parents=True)
    (src_root / ".env").write_text('NEW_BACK="a\\\\b"\n')

    bundle_path = tmp_path / "esc2.dbenv"
    rc = export(src_root, ExportOptions(
        dest=str(bundle_path), recipients=[f"@{pub_file}"]))
    assert rc == 0

    # dest に既存ファイルを置く (merge 経路に入る)
    (dest_root / ".env").write_text('EXISTING="x\\\\y"\n')
    os.chmod(dest_root / ".env", 0o600)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)],
        merge='prefer-incoming'))
    assert rc == 0

    from devbase.env.store import EnvFile
    parsed = EnvFile.parse_bytes((dest_root / ".env").read_bytes())
    # 二重エスケープされていないので、parse 後の値は元の 3 文字 "a\\b"
    assert parsed['NEW_BACK'] == 'a\\b'
    assert parsed['EXISTING'] == 'x\\y'


def test_rollback_unlinks_newly_created_sources_yml(
        fake_root, dest_root, age_keys, tmp_path, monkeypatch):
    """sources.yml を --merge-metadata で新規作成中に commit 失敗すると、
    ロールバックで sources.yml が削除されること (PR #15 gemini 指摘)"""
    from devbase.env import _import_atomic as _atomic
    from devbase.env import io_import as _io_import

    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    # dest には sources.yml が無い状態。--merge-metadata で新規作成パスに入る
    assert not (dest_root / ".env.sources.yml").exists()

    # commit 中に sources.yml の rename だけ失敗させる (最後のファイル)
    original_replace = os.replace

    def failing_replace(src, dst):
        if str(dst).endswith('.env.sources.yml'):
            raise OSError("simulated commit failure on sources.yml")
        return original_replace(src, dst)

    monkeypatch.setattr(_atomic.os, 'replace', failing_replace)

    with pytest.raises(_io_import.ImportError, match="commit"):
        import_bundle(dest_root, ImportOptions(
            source=str(bundle_path), identities=[str(id_file)],
            merge_metadata=True))

    # sources.yml はもともと存在しなかったので、ロールバックで unlink されているはず
    assert not (dest_root / ".env.sources.yml").exists()


def test_commit_failure_cleans_remaining_import_tmp_files(
        fake_root, dest_root, age_keys, tmp_path, monkeypatch):
    """_commit 失敗時に、まだ rename されていない .import.tmp ファイルが残らないこと
    (PR #15 gemini 指摘)"""
    from devbase.env import _import_atomic as _atomic
    from devbase.env import io_import as _io_import

    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    original_replace = os.replace
    call_count = {'n': 0}

    def failing_replace(src, dst):
        call_count['n'] += 1
        if call_count['n'] >= 2:
            raise OSError("simulated commit failure")
        return original_replace(src, dst)

    monkeypatch.setattr(_atomic.os, 'replace', failing_replace)

    with pytest.raises(_io_import.ImportError, match="commit"):
        import_bundle(dest_root, ImportOptions(
            source=str(bundle_path), identities=[str(id_file)]))

    # 残骸の .import.tmp ファイルが無いこと
    leftover = list(dest_root.rglob('*.import.tmp'))
    assert leftover == [], f"残骸の tmp が残っている: {leftover}"


def test_backup_dir_collision_avoidance(fake_root, dest_root, age_keys, tmp_path):
    """同じプロセス内で連続して import を実行しても、backup ディレクトリ名が衝突せず
    前回バックアップを上書きしないこと (PR #15 codex 指摘)"""
    _, id_file = age_keys
    bundle_path = _export_bundle(fake_root, age_keys, tmp_path)

    # 1 回目
    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)]))
    assert rc == 0
    # 2 回目 (同一プロセス内, おそらく同一秒)
    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)]))
    assert rc == 0

    backup_root = dest_root / "backups" / "env-import"
    subdirs = sorted(p.name for p in backup_root.iterdir() if p.is_dir())
    # 2 つの異なる backup ディレクトリが残っていること
    assert len(subdirs) == 2, f"backup が衝突して 1 つになっている: {subdirs}"


def test_envfile_parse_bytes_round_trip_with_escapes():
    """``EnvFile.parse_bytes`` が ``save`` が施す escape を正しく逆変換すること
    (PR #15 codex 指摘の double-escape 回避テスト)"""
    from devbase.env.store import EnvFile

    # 直接 EnvFile.save と同じ規則で encode したものを parse_bytes で復元
    raw = (
        'BACKSLASH="a\\\\b"\n'            # a\b
        'QUOTED="he said \\"hi\\""\n'     # he said "hi"
        'NL="x\\ny"\n'                    # x<newline>y
        'PLAIN=simple\n'
        'EMPTY=""\n'                      # empty string with quotes
    )
    parsed = EnvFile.parse_bytes(raw.encode('utf-8'))
    assert parsed['BACKSLASH'] == 'a\\b'
    assert parsed['QUOTED'] == 'he said "hi"'
    assert parsed['NL'] == 'x\ny'
    assert parsed['PLAIN'] == 'simple'
    assert parsed['EMPTY'] == ''

    # 「リテラル ``\\n``」(2 文字: backslash + 'n') を含む値も区別できること
    # save は ``a\\nb`` (3 chars) を ``"a\\\\nb"`` に変換するので、これを parse_bytes
    # に通せば元の 3 文字に戻る
    raw2 = 'LITERAL="a\\\\nb"\n'
    parsed2 = EnvFile.parse_bytes(raw2.encode('utf-8'))
    assert parsed2['LITERAL'] == 'a\\nb'  # backslash + 'n' + 'b' (3 chars)


def test_envfile_dollar_escape_round_trip():
    """``$`` を含む値は ``\\$`` にエスケープされ、``source`` 時に変数展開されない
    (PR #15 gemini 指摘)"""
    from devbase.env.store import EnvFile

    # dump → parse の round-trip で値が保たれる
    data = {
        'DOLLAR': '$HOME',                # 単純な変数展開を含む
        'PRICE': 'cost is $100',          # 値内の $
        'ESCAPED_LIKE': 'a\\$b',          # backslash + $ の組み合わせ
        'PLAIN_NUM': '12345',             # quote 不要なケース
    }
    dumped = EnvFile.dump_bytes(data)
    text = dumped.decode('utf-8')
    # $ が裸 (バックスラッシュ無し) で出力されていないこと
    # ($ の直前は必ず \ である or 行の終端 / 別の \\)
    for line in text.splitlines():
        if '=' in line and '"' in line:
            # ダブルクオート内に裸の $ があるかチェック
            _, _, val = line.partition('=')
            # 値部分の $ をすべて検査: 直前の文字が \\ であること
            for idx, ch in enumerate(val):
                if ch == '$':
                    assert idx > 0 and val[idx - 1] == '\\', (
                        f"unescaped $ in dump: {line!r}"
                    )
    parsed = EnvFile.parse_bytes(dumped)
    assert parsed == data


def test_env_import_merge_preserves_comments_and_blanks(
        fake_root, dest_root, age_keys, tmp_path):
    """merge 経路で既存 ``.env`` のコメントと空行が保持されること (PR #15 gemini 指摘)"""
    _, id_file = age_keys
    pub_file, _ = age_keys

    # incoming bundle: AWS_CONFIG_BASE64=AAAA + GLOBAL=1
    src_root = tmp_path / "comment-src"
    src_root.mkdir()
    (src_root / ".env").write_text("AWS_CONFIG_BASE64=AAAA\nGLOBAL=1\n")
    bundle_path = tmp_path / "comment.dbenv"
    rc = export(src_root, ExportOptions(
        dest=str(bundle_path), recipients=[f"@{pub_file}"]))
    assert rc == 0

    # 既存 dest .env にコメント・空行・既存キーを配置
    existing_text = (
        "# Top-level header comment\n"
        "\n"
        "# AWS section\n"
        "AWS_CONFIG_BASE64=OLD\n"
        "\n"
        "# user-managed key\n"
        "KEEP=this\n"
    )
    (dest_root / ".env").write_text(existing_text)
    os.chmod(dest_root / ".env", 0o600)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)],
        merge='prefer-incoming'))
    assert rc == 0

    out = (dest_root / ".env").read_text()
    # コメント・空行が保持されている
    assert "# Top-level header comment" in out
    assert "# AWS section" in out
    assert "# user-managed key" in out
    # 既存値は prefer-incoming で AAAA に書き換わる
    assert "AWS_CONFIG_BASE64=AAAA" in out
    # 既存にしか無かった KEEP は維持
    assert "KEEP=this" in out
    # incoming にしか無かった GLOBAL は末尾に追加
    assert "GLOBAL=1" in out
    # 空行も最低 1 つ残っている
    assert "\n\n" in out


def test_env_import_keep_existing_preserves_comments(
        fake_root, dest_root, age_keys, tmp_path):
    """keep-existing 経路でもコメントが保持されること"""
    _, id_file = age_keys
    pub_file, _ = age_keys

    src_root = tmp_path / "keep-src"
    src_root.mkdir()
    (src_root / ".env").write_text("INCOMING_KEY=incoming\n")
    bundle_path = tmp_path / "keep.dbenv"
    rc = export(src_root, ExportOptions(
        dest=str(bundle_path), recipients=[f"@{pub_file}"]))
    assert rc == 0

    (dest_root / ".env").write_text(
        "# This comment must survive\nKEEP_ME=v\n"
    )
    os.chmod(dest_root / ".env", 0o600)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)]))
    assert rc == 0

    out = (dest_root / ".env").read_text()
    assert "# This comment must survive" in out
    assert "KEEP_ME=v" in out
    assert "INCOMING_KEY=incoming" in out


def test_env_import_dollar_value_is_escaped_after_merge(
        fake_root, dest_root, age_keys, tmp_path):
    """``$`` を含む値が merge 後の ``.env`` 上でエスケープされていること
    (シェルで source した時の変数展開を防ぐ / PR #15 gemini 指摘)"""
    _, id_file = age_keys
    pub_file, _ = age_keys

    src_root = tmp_path / "dollar-src"
    src_root.mkdir()
    # 値に $ を含む。export 側 (EnvFile.save 形式) で書き出される
    (src_root / ".env").write_text('PRICE="cost is \\$100"\n')
    bundle_path = tmp_path / "dollar.dbenv"
    rc = export(src_root, ExportOptions(
        dest=str(bundle_path), recipients=[f"@{pub_file}"]))
    assert rc == 0

    # 既存 dest .env (merge 経路に入る)
    (dest_root / ".env").write_text("EXISTING=keep\n")
    os.chmod(dest_root / ".env", 0o600)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)],
        merge='prefer-incoming'))
    assert rc == 0

    raw_text = (dest_root / ".env").read_text()
    # 裸の $ が現れていないこと (\ の直後でなければ NG)
    for line in raw_text.splitlines():
        if 'PRICE' not in line:
            continue
        # 値の中の $ は必ず \\ の直後
        _, _, val = line.partition('=')
        for idx, ch in enumerate(val):
            if ch == '$':
                assert idx > 0 and val[idx - 1] == '\\', (
                    f"unescaped $ in merged .env: {line!r}"
                )

    from devbase.env.store import EnvFile
    parsed = EnvFile.parse_bytes((dest_root / ".env").read_bytes())
    assert parsed['PRICE'] == 'cost is $100'
    assert parsed['EXISTING'] == 'keep'


# --- PR #15 round5: コメント / 空行のみの既存 .env が create 扱いされて
# 上書きされないこと (`existing` dict が空でも target.exists() で merge に入る)。

def _setup_comment_only_dest(dest_root: Path) -> str:
    """key=value を含まずコメント / 空行のみで構成された既存 .env を作る"""
    text = (
        "# user-managed header (no kv yet)\n"
        "\n"
        "# section: aws\n"
        "\n"
    )
    (dest_root / ".env").write_text(text)
    os.chmod(dest_root / ".env", 0o600)
    return text


def _build_simple_bundle(tmp_path: Path, pub_file: Path,
                         name: str = "comment-only") -> Path:
    src_root = tmp_path / f"{name}-src"
    src_root.mkdir()
    (src_root / ".env").write_text("INCOMING_KEY=incoming\n")
    bundle_path = tmp_path / f"{name}.dbenv"
    rc = export(src_root, ExportOptions(
        dest=str(bundle_path), recipients=[f"@{pub_file}"]))
    assert rc == 0
    return bundle_path


@pytest.mark.parametrize("merge_mode", ["prefer-incoming", "keep-existing"])
def test_env_import_comment_only_existing_preserves_comments_on_merge(
        fake_root, dest_root, age_keys, tmp_path, merge_mode):
    """コメント / 空行のみの既存 .env が ``existing`` 辞書の空判定で create 扱いに
    なって上書きされ、ヘッダコメントが消失しないこと (PR #15 round5 指摘)"""
    _, id_file = age_keys
    pub_file, _ = age_keys
    bundle_path = _build_simple_bundle(tmp_path, pub_file, "comment-merge")

    _setup_comment_only_dest(dest_root)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)],
        merge=merge_mode))
    assert rc == 0

    out = (dest_root / ".env").read_text()
    assert "# user-managed header (no kv yet)" in out
    assert "# section: aws" in out
    assert "INCOMING_KEY=incoming" in out


def test_env_import_comment_only_existing_preserves_comments_on_replace_keys(
        fake_root, dest_root, age_keys, tmp_path):
    """--replace-keys 経路でも、コメントのみの既存 .env が create 扱いされず
    コメントが保持されること (PR #15 round5 指摘)"""
    _, id_file = age_keys
    pub_file, _ = age_keys
    bundle_path = _build_simple_bundle(tmp_path, pub_file, "comment-rk")

    _setup_comment_only_dest(dest_root)

    rc = import_bundle(dest_root, ImportOptions(
        source=str(bundle_path), identities=[str(id_file)],
        replace_keys=["INCOMING_KEY"]))
    assert rc == 0

    out = (dest_root / ".env").read_text()
    assert "# user-managed header (no kv yet)" in out
    assert "# section: aws" in out
    assert "INCOMING_KEY=incoming" in out


def test_env_import_comment_only_existing_replace_reports_op_replace(
        fake_root, dest_root, age_keys, tmp_path, caplog):
    """--replace 経路では incoming で完全上書きするが、その op は 'create' では
    なく 'replace' として報告されること (PR #15 round5 指摘)。

    --replace の意味論として既存内容は捨てるため、コメント保持は要件ではないが、
    ログ上 ``create`` と表示されるとロールバック挙動など他の経路 (= 新規作成は
    backup を取らない) と判別できなくなるため、op 表記の正確性を確認する。
    """
    import logging
    _, id_file = age_keys
    pub_file, _ = age_keys
    bundle_path = _build_simple_bundle(tmp_path, pub_file, "comment-replace")

    _setup_comment_only_dest(dest_root)

    # plan 表示は _import_merge.log_plans で行われるためそのモジュールの logger を捕捉する
    with caplog.at_level(logging.INFO, logger="devbase.env._import_merge"):
        rc = import_bundle(dest_root, ImportOptions(
            source=str(bundle_path), identities=[str(id_file)],
            replace=True))
    assert rc == 0

    # 'replace: <path>' のような行が出ているはず。少なくとも 'create:' 表記で
    # 出力されていないことを確認 (op_replace の正しさ)。
    log_text = "\n".join(r.message for r in caplog.records)
    assert "replace: " in log_text, log_text
    # backup には元のコメントのみの .env が記録されている (存在判定が正しければ
    # _backup_existing が target を見つけてコピーするため)
    backup_root = dest_root / "backups" / "env-import"
    assert backup_root.is_dir()
    snapshots = [p for p in backup_root.iterdir() if p.is_dir()]
    assert len(snapshots) >= 1
    backed = (snapshots[0] / ".env").read_text()
    assert "# user-managed header (no kv yet)" in backed
