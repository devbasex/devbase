"""env バンドルの入出力先 (local / stdio / s3) を抽象化する"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Tuple
from urllib.parse import urlparse

from devbase.errors import DevbaseError
from devbase.log import get_logger

logger = get_logger(__name__)


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


@dataclass
class S3Options:
    """S3Backend の挙動パラメータ。

    `unsafe_allow_unencrypted_bucket` は **export 専用**: True にすると
    バケット側のデフォルト暗号化未設定でも export を許可する。
    オブジェクト個別の SSE は `sse` / `sse_kms_key_id` で常に強制される。
    """
    unsafe_allow_unencrypted_bucket: bool = False
    sse: str = 'aws:kms'           # 'aws:kms' or 'AES256'
    sse_kms_key_id: Optional[str] = None
    endpoint_url: Optional[str] = None
    region: Optional[str] = None

    @classmethod
    def from_env(
        cls,
        *,
        unsafe_allow_unencrypted_bucket: bool = False,
    ) -> 'S3Options':
        """環境変数から既定値を読み取って組み立てる。

        env vars (任意):
            DEVBASE_S3_SSE              -> sse (既定: aws:kms)
            DEVBASE_S3_SSE_KMS_KEY_ID   -> sse_kms_key_id
            DEVBASE_S3_ENDPOINT_URL     -> endpoint_url (MinIO/LocalStack 用)
            DEVBASE_S3_REGION           -> region

        boto3 が認識する AWS_PROFILE / AWS_REGION / AWS_ENDPOINT_URL[_S3] /
        AWS_ACCESS_KEY_ID 等はそのまま尊重される。
        """
        sse = os.environ.get('DEVBASE_S3_SSE', 'aws:kms')
        if sse not in ('aws:kms', 'AES256'):
            raise StorageError(
                f"DEVBASE_S3_SSE は 'aws:kms' か 'AES256' を指定してください: {sse!r}"
            )
        return cls(
            unsafe_allow_unencrypted_bucket=unsafe_allow_unencrypted_bucket,
            sse=sse,
            sse_kms_key_id=os.environ.get('DEVBASE_S3_SSE_KMS_KEY_ID'),
            endpoint_url=os.environ.get('DEVBASE_S3_ENDPOINT_URL'),
            region=os.environ.get('DEVBASE_S3_REGION'),
        )


def _parse_s3_uri(uri: str) -> Tuple[str, str]:
    """s3://bucket/key/path を (bucket, key) に分解する"""
    parsed = urlparse(uri)
    if parsed.scheme.lower() != 's3':
        raise StorageError(f"S3 URI が期待されますが: {uri!r}")
    bucket = parsed.netloc
    key = parsed.path.lstrip('/')
    if not bucket:
        raise StorageError(
            f"S3 URI のバケット名が空です: {uri!r} "
            "(s3://bucket/key の形式で指定してください)"
        )
    if not key:
        raise StorageError(
            f"S3 URI のキーが空です: {uri!r} "
            "(s3://bucket/key の形式で指定してください)"
        )
    return bucket, key


class S3Backend:
    """AWS S3 / S3 互換ストレージ (MinIO 等)。boto3 を optional dep として遅延 import する。

    - write_bytes: PutObject 時に ServerSideEncryption を常に付与し、
      `unsafe_allow_unencrypted_bucket=False` のときは
      GetBucketEncryption で**バケット側の既定暗号化**も事前確認する。
    - read_bytes: GetObject (暗号化はバケット/オブジェクト側設定に従う)。
    """

    def __init__(self, options: Optional[S3Options] = None):
        self._options = options or S3Options()
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore
        except ImportError as e:
            raise StorageError(
                "S3 backend を使うには boto3 が必要です "
                "(`pip install boto3` または `uv add boto3` を実行してください)"
            ) from e

        kwargs = {}
        if self._options.endpoint_url:
            kwargs['endpoint_url'] = self._options.endpoint_url
        if self._options.region:
            kwargs['region_name'] = self._options.region
        try:
            self._client = boto3.client('s3', **kwargs)
        except Exception as e:
            raise StorageError(f"S3 クライアントの生成に失敗しました: {e}") from e
        return self._client

    @staticmethod
    def _error_code(exc: BaseException) -> Optional[str]:
        """botocore.exceptions.ClientError から AWS error code を取り出す"""
        resp = getattr(exc, 'response', None)
        if isinstance(resp, dict):
            return resp.get('Error', {}).get('Code')
        return None

    def _verify_bucket_encryption(self, client, bucket: str) -> None:
        """バケットレベルの既定暗号化を確認。

        - 暗号化が設定済み: OK
        - 暗号化が未設定 (ServerSideEncryptionConfigurationNotFoundError):
          unsafe フラグがあれば警告のみ、無ければ StorageError
        - AccessDenied 等で確認できなかった場合は事故防止のため拒否
          (`--unsafe-allow-unencrypted-bucket` でのみバイパス可)
        """
        try:
            client.get_bucket_encryption(Bucket=bucket)
            return
        except Exception as e:
            code = self._error_code(e)
            if code == 'ServerSideEncryptionConfigurationNotFoundError':
                msg = (
                    f"S3 バケット '{bucket}' のデフォルト暗号化が未設定です。"
                    "バケットポリシーで SSE-KMS or SSE-S3 を有効化するか、"
                    "明示的に '--unsafe-allow-unencrypted-bucket' を指定してください "
                    "(オブジェクト単位の SSE はこのオプションに関係なく常に付与されます)"
                )
                if self._options.unsafe_allow_unencrypted_bucket:
                    logger.warning("%s (unsafe フラグにより続行)", msg)
                    return
                raise StorageError(msg) from e
            if code in ('AccessDenied', 'AccessDeniedException'):
                msg = (
                    f"S3 バケット '{bucket}' の暗号化設定を確認できません "
                    "(GetBucketEncryption 権限がありません)。"
                    "バケットポリシーの確認が取れないため export を中止します。"
                    "権限を付与するか、'--unsafe-allow-unencrypted-bucket' を明示してください"
                )
                if self._options.unsafe_allow_unencrypted_bucket:
                    logger.warning("%s (unsafe フラグにより続行)", msg)
                    return
                raise StorageError(msg) from e
            raise StorageError(
                f"バケット暗号化設定の確認に失敗しました ({bucket}): {e}"
            ) from e

    def write_bytes(self, dest: str, data: bytes) -> None:
        bucket, key = _parse_s3_uri(dest)
        client = self._get_client()
        self._verify_bucket_encryption(client, bucket)

        put_kwargs = {
            'Bucket': bucket,
            'Key': key,
            'Body': data,
            'ServerSideEncryption': self._options.sse,
        }
        if self._options.sse == 'aws:kms' and self._options.sse_kms_key_id:
            put_kwargs['SSEKMSKeyId'] = self._options.sse_kms_key_id

        try:
            client.put_object(**put_kwargs)
        except Exception as e:
            raise StorageError(
                f"S3 への書き込みに失敗しました ({dest}): {e}"
            ) from e
        logger.info("S3 へ書き込みました: %s (sse=%s)", dest, self._options.sse)

    def read_bytes(self, source: str) -> bytes:
        bucket, key = _parse_s3_uri(source)
        client = self._get_client()
        try:
            response = client.get_object(Bucket=bucket, Key=key)
            body = response['Body']
        except Exception as e:
            code = self._error_code(e)
            if code in ('NoSuchKey', 'NoSuchBucket', '404'):
                raise StorageError(
                    f"S3 オブジェクトが見つかりません: {source}"
                ) from e
            raise StorageError(
                f"S3 からの読み込みに失敗しました ({source}): {e}"
            ) from e
        try:
            return body.read()
        except Exception as e:
            raise StorageError(
                f"S3 レスポンスボディの読み取りに失敗しました ({source}): {e}"
            ) from e


def resolve(uri: str, *, s3_options: Optional[S3Options] = None) -> StorageBackend:
    """URI スキームから対応する backend を返す。

    s3:// は `s3_options` を受け取れる (省略時は S3Options.from_env())。
    `gs://` は PLAN03-1 PR4 廃案により対応しない。
    """
    if uri == '-':
        return StdioBackend()

    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()

    if scheme in ('', 'file'):
        return LocalBackend()

    if scheme == 's3':
        return S3Backend(s3_options if s3_options is not None else S3Options.from_env())

    if scheme == 'gs':
        raise StorageError(
            "スキーム 'gs://' (GCS) は PLAN03-1 PR4 廃案により対応していません。"
            "必要な場合は s3:// 経由 (S3 互換ゲートウェイ) を検討するか、"
            "ローカルファイルを介して転送してください"
        )

    # Windows のドライブレター付きパス (例: C:\path, d:/path) は
    # urlparse が scheme='c' / 'd' と誤認するため、1 文字アルファベットで
    # かつ `://` を伴わないものは LocalBackend にフォールバックする
    if len(scheme) == 1 and scheme.isalpha() and '://' not in uri:
        return LocalBackend()

    raise StorageError(f"未対応のスキームです: {scheme}://")


def is_stdio(uri: str) -> bool:
    return uri == '-'


def is_s3(uri: str) -> bool:
    return urlparse(uri).scheme.lower() == 's3'
