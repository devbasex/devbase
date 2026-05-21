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


def _to_local_path(uri: str) -> Path:
    """ローカルパス文字列または file:// URI を Path に正規化する"""
    parsed = urlparse(uri)
    if parsed.scheme.lower() == 'file':
        # file:///tmp/x や file://localhost/tmp/x のみ許容
        # file://other-host/tmp/x はホスト情報が脱落するので拒否
        netloc = (parsed.netloc or '').lower()
        if netloc not in ('', 'localhost'):
            raise StorageError(
                f"file:// URI のホスト指定はサポートされていません "
                f"(netloc={parsed.netloc!r}, 許可: '' / 'localhost')"
            )
        from urllib.request import url2pathname
        return Path(url2pathname(parsed.path)).expanduser()
    return Path(uri).expanduser()


class LocalBackend:
    """ローカルファイルシステム"""

    def write_bytes(self, dest: str, data: bytes) -> None:
        path = _to_local_path(dest)
        try:
            if path.parent and not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            # TOCTOU 回避: open(..., 'wb') 後に chmod すると、umask が緩い環境では
            # 一瞬 0644 等で平文 export が露出する。
            # os.open に mode=0o600 を渡し、O_CREAT|O_TRUNC|O_WRONLY で作成時点
            # から 0600 を強制する。既存ファイルも書き込み前に chmod で権限を絞る。
            if path.exists():
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    # Windows 等で chmod が無効でも処理を続行
                    pass
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd = os.open(path, flags, 0o600)
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(data)
            except BaseException:
                # fdopen 失敗時は fd を明示的に閉じる (fdopen 成功時は with が close)
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            # mode 引数が無視される環境 (Windows 等) でも後追いで chmod を試みる
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except StorageError:
            raise
        except OSError as e:
            raise StorageError(f"書き込みに失敗しました ({path}): {e}") from e

    def read_bytes(self, source: str) -> bytes:
        path = _to_local_path(source)
        if not path.exists():
            raise StorageError(f"ファイルが見つかりません: {path}")
        try:
            return path.read_bytes()
        except OSError as e:
            raise StorageError(f"読み込みに失敗しました ({path}): {e}") from e


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

    # Windows のドライブレター付きパス (例: C:\path, d:/path) は
    # urlparse が scheme='c' / 'd' と誤認するため、1 文字アルファベットで
    # かつ `://` を伴わないものは LocalBackend にフォールバックする
    if len(scheme) == 1 and scheme.isalpha() and '://' not in uri:
        return LocalBackend()

    raise StorageError(f"未対応のスキームです: {scheme}://")


def is_stdio(uri: str) -> bool:
    return uri == '-'
