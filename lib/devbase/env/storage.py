"""env バンドルの入出力先 (local / stdio / 将来 s3, gcs) を抽象化する"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from devbase.errors import DevbaseError


class StorageError(DevbaseError):
    """ストレージ操作エラー"""


class StorageBackend(Protocol):
    def write_bytes(self, dest: str, data: bytes) -> None: ...
    def read_bytes(self, source: str) -> bytes: ...


class LocalBackend:
    """ローカルファイルシステム"""

    def write_bytes(self, dest: str, data: bytes) -> None:
        path = Path(dest).expanduser()
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        # 0600 で書き出すため open(..., 'wb') 後に chmod する
        with open(path, 'wb') as f:
            f.write(data)
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Windows 等で chmod が無効でも書き込み自体は完了させる
            pass

    def read_bytes(self, source: str) -> bytes:
        path = Path(source).expanduser()
        if not path.exists():
            raise StorageError(f"ファイルが見つかりません: {path}")
        return path.read_bytes()


class StdioBackend:
    """`-` 指定での stdin/stdout 入出力 (パイプ運用向け)"""

    def write_bytes(self, dest: str, data: bytes) -> None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    def read_bytes(self, source: str) -> bytes:
        return sys.stdin.buffer.read()


def resolve(uri: str) -> StorageBackend:
    """URI スキームから対応する backend を返す"""
    if uri == '-':
        return StdioBackend()

    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()

    if scheme in ('', 'file'):
        return LocalBackend()

    if scheme in ('s3', 'gs'):
        raise StorageError(
            f"スキーム '{scheme}://' は本 PR では未実装です "
            "(後続 PR で対応予定)"
        )

    raise StorageError(f"未対応のスキームです: {scheme}://")


def is_stdio(uri: str) -> bool:
    return uri == '-'
