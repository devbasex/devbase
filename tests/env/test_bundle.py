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
