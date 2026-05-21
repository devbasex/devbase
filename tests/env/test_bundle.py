"""bundle.py: tar.gz パック/アンパックと manifest 検証"""

from __future__ import annotations

import pytest

from devbase.env import bundle


def _entry(arcname: str, data: bytes, origin: str = "") -> bundle.BundleEntry:
    return bundle.BundleEntry(arcname=arcname, origin=origin or arcname, data=data)


def test_pack_unpack_roundtrip_preserves_contents():
    entries = [
        _entry("env/global.env", b"FOO=bar\nBAZ=qux\n"),
        _entry("env/projects/p1/.env", b"API_KEY=abc\n"),
    ]
    blob = bundle.pack(entries, devbase_version="test")
    manifest, members = bundle.unpack(blob)

    assert manifest["version"] == bundle.SUPPORTED_MANIFEST_VERSION
    assert manifest["devbase_version"] == "test"
    assert {e["path"] for e in manifest["files"]} == {e.arcname for e in entries}
    assert members["env/global.env"] == b"FOO=bar\nBAZ=qux\n"
    assert members["env/projects/p1/.env"] == b"API_KEY=abc\n"


def test_unpack_rejects_corrupted_sha256():
    entries = [_entry("env/global.env", b"FOO=bar\n")]
    blob = bundle.pack(entries)

    # 同じ tar に対し manifest の sha256 を意図的に壊した tar を作る
    import io, tarfile, yaml
    src = io.BytesIO(blob)
    out = io.BytesIO()
    with tarfile.open(fileobj=src, mode="r:gz") as tin, \
         tarfile.open(fileobj=out, mode="w:gz") as tout:
        for info in tin.getmembers():
            data = tin.extractfile(info).read()
            if info.name == bundle.MANIFEST_NAME:
                m = yaml.safe_load(data)
                m["files"][0]["sha256"] = "0" * 64
                data = yaml.safe_dump(m).encode("utf-8")
                info.size = len(data)
            tout.addfile(info, io.BytesIO(data))

    with pytest.raises(bundle.BundleError, match="sha256"):
        bundle.unpack(out.getvalue())


def test_unpack_rejects_unknown_version():
    entries = [_entry("env/global.env", b"FOO=bar\n")]
    blob = bundle.pack(entries)

    import io, tarfile, yaml
    src = io.BytesIO(blob)
    out = io.BytesIO()
    with tarfile.open(fileobj=src, mode="r:gz") as tin, \
         tarfile.open(fileobj=out, mode="w:gz") as tout:
        for info in tin.getmembers():
            data = tin.extractfile(info).read()
            if info.name == bundle.MANIFEST_NAME:
                m = yaml.safe_load(data)
                m["version"] = bundle.SUPPORTED_MANIFEST_VERSION + 1
                data = yaml.safe_dump(m).encode("utf-8")
                info.size = len(data)
            tout.addfile(info, io.BytesIO(data))

    with pytest.raises(bundle.BundleError, match="version"):
        bundle.unpack(out.getvalue())


def test_make_entries_from_disk(tmp_path):
    root = tmp_path
    (root / ".env").write_text("GLOBAL=1\n")
    (root / ".env.sources.yml").write_text("sources: {}\n")
    proj_a = root / "projects" / "a"
    proj_a.mkdir(parents=True)
    (proj_a / ".env").write_text("A=1\n")
    proj_b = root / "projects" / "b"
    proj_b.mkdir(parents=True)
    (proj_b / ".env").write_text("B=1\n")

    entries = bundle.make_entries_from_disk(root)
    arcnames = {e.arcname for e in entries}
    assert arcnames == {
        "env/global.env",
        "env/sources.yml",
        "env/projects/a/.env",
        "env/projects/b/.env",
    }

    only_a = bundle.make_entries_from_disk(root, include_projects=["a"],
                                           include_metadata=False)
    assert {e.arcname for e in only_a} == {"env/global.env", "env/projects/a/.env"}

    no_global = bundle.make_entries_from_disk(root, include_global=False,
                                              exclude_projects=["b"])
    assert "env/global.env" not in {e.arcname for e in no_global}
    assert "env/projects/b/.env" not in {e.arcname for e in no_global}


def test_unpack_rejects_traversal_paths():
    import io, tarfile
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tout:
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = 3
        tout.addfile(info, io.BytesIO(b"BAD"))
    with pytest.raises(bundle.BundleError, match="不正なパス"):
        bundle.unpack(out.getvalue())


def _rewrite_manifest(blob: bytes, new_manifest_obj) -> bytes:
    """blob 内の manifest.yml を new_manifest_obj に置き換えた tar.gz を返す"""
    import io, tarfile, yaml
    src = io.BytesIO(blob)
    out = io.BytesIO()
    with tarfile.open(fileobj=src, mode="r:gz") as tin, \
         tarfile.open(fileobj=out, mode="w:gz") as tout:
        for info in tin.getmembers():
            data = tin.extractfile(info).read()
            if info.name == bundle.MANIFEST_NAME:
                data = yaml.safe_dump(new_manifest_obj).encode("utf-8")
                info.size = len(data)
            tout.addfile(info, io.BytesIO(data))
    return out.getvalue()


def test_unpack_rejects_files_not_list():
    blob = bundle.pack([_entry("env/global.env", b"FOO=bar\n")])
    bad = _rewrite_manifest(blob, {
        "version": bundle.SUPPORTED_MANIFEST_VERSION,
        "files": "not-a-list",
    })
    with pytest.raises(bundle.BundleError, match="files が list"):
        bundle.unpack(bad)


def test_unpack_rejects_files_entry_not_dict():
    blob = bundle.pack([_entry("env/global.env", b"FOO=bar\n")])
    bad = _rewrite_manifest(blob, {
        "version": bundle.SUPPORTED_MANIFEST_VERSION,
        "files": ["not-a-dict"],
    })
    with pytest.raises(bundle.BundleError, match="dict ではありません"):
        bundle.unpack(bad)


def test_unpack_rejects_invalid_path_field():
    blob = bundle.pack([_entry("env/global.env", b"FOO=bar\n")])
    bad = _rewrite_manifest(blob, {
        "version": bundle.SUPPORTED_MANIFEST_VERSION,
        "files": [{"path": 123, "sha256": "x" * 64}],
    })
    with pytest.raises(bundle.BundleError, match="path が不正"):
        bundle.unpack(bad)


def test_unpack_rejects_invalid_sha256_field():
    blob = bundle.pack([_entry("env/global.env", b"FOO=bar\n")])
    bad = _rewrite_manifest(blob, {
        "version": bundle.SUPPORTED_MANIFEST_VERSION,
        "files": [{"path": "env/global.env", "sha256": 12345}],
    })
    with pytest.raises(bundle.BundleError, match="sha256 が不正"):
        bundle.unpack(bad)


def test_unpack_rejects_missing_sha256_field():
    """sha256 が欠落 (None) している manifest は BundleError"""
    blob = bundle.pack([_entry("env/global.env", b"FOO=bar\n")])
    bad = _rewrite_manifest(blob, {
        "version": bundle.SUPPORTED_MANIFEST_VERSION,
        "files": [{"path": "env/global.env"}],  # sha256 欠落
    })
    with pytest.raises(bundle.BundleError, match="sha256 が不正"):
        bundle.unpack(bad)


def test_unpack_rejects_sha256_none():
    """sha256 が明示的に None でも BundleError (完全性チェック迂回防止)"""
    blob = bundle.pack([_entry("env/global.env", b"FOO=bar\n")])
    bad = _rewrite_manifest(blob, {
        "version": bundle.SUPPORTED_MANIFEST_VERSION,
        "files": [{"path": "env/global.env", "sha256": None}],
    })
    with pytest.raises(bundle.BundleError, match="sha256 が不正"):
        bundle.unpack(bad)


def test_unpack_rejects_sha256_wrong_length():
    """sha256 が 64 文字でない場合は BundleError"""
    blob = bundle.pack([_entry("env/global.env", b"FOO=bar\n")])
    bad = _rewrite_manifest(blob, {
        "version": bundle.SUPPORTED_MANIFEST_VERSION,
        "files": [{"path": "env/global.env", "sha256": "abc123"}],
    })
    with pytest.raises(bundle.BundleError, match="sha256 が不正"):
        bundle.unpack(bad)


def test_unpack_rejects_sha256_non_hex():
    """sha256 が 64 文字でも 16 進でないなら BundleError"""
    blob = bundle.pack([_entry("env/global.env", b"FOO=bar\n")])
    bad = _rewrite_manifest(blob, {
        "version": bundle.SUPPORTED_MANIFEST_VERSION,
        "files": [{"path": "env/global.env", "sha256": "z" * 64}],
    })
    with pytest.raises(bundle.BundleError, match="sha256 が不正"):
        bundle.unpack(bad)


def test_unpack_rejects_duplicate_tar_entries():
    import io, tarfile, yaml
    out = io.BytesIO()
    manifest = {
        "version": bundle.SUPPORTED_MANIFEST_VERSION,
        "files": [{"path": "env/global.env",
                   "sha256": bundle._sha256(b"FOO=bar\n")}],
    }
    manifest_bytes = yaml.safe_dump(manifest).encode("utf-8")
    with tarfile.open(fileobj=out, mode="w:gz") as tout:
        m = tarfile.TarInfo(name=bundle.MANIFEST_NAME)
        m.size = len(manifest_bytes)
        tout.addfile(m, io.BytesIO(manifest_bytes))
        # 同名エントリを 2 回追加
        for payload in (b"FOO=bar\n", b"FOO=other\n"):
            info = tarfile.TarInfo(name="env/global.env")
            info.size = len(payload)
            tout.addfile(info, io.BytesIO(payload))
    with pytest.raises(bundle.BundleError, match="重複エントリ"):
        bundle.unpack(out.getvalue())


@pytest.mark.parametrize("payload", [b"- a\n- b\n", b"just a string\n", b"42\n"])
def test_unpack_rejects_non_mapping_manifest(payload):
    """manifest.yaml の top-level が dict でない場合 BundleError"""
    import io, tarfile
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tout:
        m = tarfile.TarInfo(name=bundle.MANIFEST_NAME)
        m.size = len(payload)
        tout.addfile(m, io.BytesIO(payload))
    with pytest.raises(bundle.BundleError, match="mapping ではありません"):
        bundle.unpack(out.getvalue())


def test_pack_is_deterministic():
    """同一入力に対し pack() の出力バイト列が完全に一致 (gzip mtime=0 が効いている)"""
    entries = [
        _entry("env/global.env", b"FOO=bar\n"),
        _entry("env/projects/p1/.env", b"X=1\n"),
    ]
    blob1 = bundle.pack(entries, devbase_version="test",
                        created_at="2024-01-01T00:00:00+00:00")
    blob2 = bundle.pack(entries, devbase_version="test",
                        created_at="2024-01-01T00:00:00+00:00")
    assert blob1 == blob2
    # gzip マジックで始まる
    assert blob1[:2] == b"\x1f\x8b"


def test_unpack_rejects_duplicate_manifest_paths():
    """manifest.files に同じ path が複数回現れたら BundleError"""
    blob = bundle.pack([_entry("env/global.env", b"FOO=bar\n")])
    bad = _rewrite_manifest(blob, {
        "version": bundle.SUPPORTED_MANIFEST_VERSION,
        "files": [
            {"path": "env/global.env",
             "sha256": bundle._sha256(b"FOO=bar\n")},
            {"path": "env/global.env",
             "sha256": bundle._sha256(b"FOO=bar\n")},
        ],
    })
    with pytest.raises(bundle.BundleError, match="path が重複"):
        bundle.unpack(bad)


def test_unpack_rejects_broken_tar_with_bundle_error():
    """壊れた tar.gz は BundleError として送出される (tarfile.TarError を漏らさない)"""
    # gzip ヘッダだけ正しいが中身が壊れているバイト列
    broken = b"\x1f\x8b\x08\x00" + b"\x00" * 32
    with pytest.raises(bundle.BundleError):
        bundle.unpack(broken)


def test_make_entries_from_disk_ignores_directory_named_env(tmp_path):
    """対象パスがディレクトリの場合は is_file() で除外され、例外にならない"""
    root = tmp_path
    # .env がディレクトリだったケース
    (root / ".env").mkdir()
    # 通常の sources.yml
    (root / ".env.sources.yml").write_text("sources: {}\n")
    entries = bundle.make_entries_from_disk(root)
    arcnames = {e.arcname for e in entries}
    assert "env/global.env" not in arcnames
    assert "env/sources.yml" in arcnames


def test_unpack_rejects_unknown_tar_entries():
    """manifest に記載のないファイルが tar に紛れ込んでいたら BundleError"""
    import io, tarfile, yaml
    out = io.BytesIO()
    manifest = {
        "version": bundle.SUPPORTED_MANIFEST_VERSION,
        "files": [{"path": "env/global.env",
                   "sha256": bundle._sha256(b"FOO=bar\n")}],
    }
    manifest_bytes = yaml.safe_dump(manifest).encode("utf-8")
    with tarfile.open(fileobj=out, mode="w:gz") as tout:
        m = tarfile.TarInfo(name=bundle.MANIFEST_NAME)
        m.size = len(manifest_bytes)
        tout.addfile(m, io.BytesIO(manifest_bytes))
        # manifest に記載があるファイル
        legit = tarfile.TarInfo(name="env/global.env")
        legit.size = len(b"FOO=bar\n")
        tout.addfile(legit, io.BytesIO(b"FOO=bar\n"))
        # manifest に記載のないファイル
        stowaway = tarfile.TarInfo(name="env/stowaway.env")
        stowaway.size = len(b"EVIL=1\n")
        tout.addfile(stowaway, io.BytesIO(b"EVIL=1\n"))
    with pytest.raises(bundle.BundleError, match="manifest に記載のないファイル"):
        bundle.unpack(out.getvalue())
