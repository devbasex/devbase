"""storage.py: Local / Stdio backend + resolve()"""

from __future__ import annotations

import io
import sys

import pytest

from devbase.env import storage


def test_local_backend_roundtrip(tmp_path):
    backend = storage.LocalBackend()
    dest = tmp_path / "out" / "bundle.bin"
    backend.write_bytes(str(dest), b"abc")

    assert backend.read_bytes(str(dest)) == b"abc"
    assert dest.stat().st_mode & 0o777 == 0o600


def test_local_backend_missing_file_raises(tmp_path):
    backend = storage.LocalBackend()
    with pytest.raises(storage.StorageError):
        backend.read_bytes(str(tmp_path / "no-such"))


def test_resolve_local_for_plain_path():
    assert isinstance(storage.resolve("/tmp/foo"), storage.LocalBackend)
    assert isinstance(storage.resolve("relative/path"), storage.LocalBackend)
    assert isinstance(storage.resolve("file:///tmp/foo"), storage.LocalBackend)


def test_resolve_stdio_for_dash():
    assert isinstance(storage.resolve("-"), storage.StdioBackend)
    assert storage.is_stdio("-")
    assert not storage.is_stdio("/tmp/foo")


def test_resolve_rejects_unimplemented_schemes():
    for uri in ("s3://bucket/key", "gs://bucket/object"):
        with pytest.raises(storage.StorageError, match="未実装"):
            storage.resolve(uri)


def test_resolve_rejects_unknown_scheme():
    with pytest.raises(storage.StorageError, match="未対応"):
        storage.resolve("ftp://host/x")


def test_stdio_backend_writes_to_stdout(monkeypatch):
    buf = io.BytesIO()

    class FakeStdout:
        buffer = buf

    monkeypatch.setattr(sys, "stdout", FakeStdout())
    storage.StdioBackend().write_bytes("-", b"hello")
    assert buf.getvalue() == b"hello"
