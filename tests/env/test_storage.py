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


def test_resolve_rejects_gs_scheme_dropped():
    """PLAN03-1 PR4 廃案により gs:// は対応しない (S3 と紛らわしいので明示メッセージ)"""
    with pytest.raises(storage.StorageError, match="廃案"):
        storage.resolve("gs://bucket/object")


def test_resolve_returns_s3_backend():
    """s3:// は S3Backend を返し、S3Options を引き渡せる"""
    opts = storage.S3Options(unsafe_allow_unencrypted_bucket=True, sse='AES256')
    backend = storage.resolve("s3://bucket/key", s3_options=opts)
    assert isinstance(backend, storage.S3Backend)
    assert backend._options is opts


def test_resolve_returns_s3_backend_without_opts(monkeypatch):
    """s3_options 省略時は from_env で組み立てられる"""
    monkeypatch.delenv("DEVBASE_S3_SSE", raising=False)
    monkeypatch.delenv("DEVBASE_S3_SSE_KMS_KEY_ID", raising=False)
    monkeypatch.delenv("DEVBASE_S3_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("DEVBASE_S3_REGION", raising=False)
    backend = storage.resolve("s3://bucket/key")
    assert isinstance(backend, storage.S3Backend)
    assert backend._options.sse == 'aws:kms'
    assert backend._options.unsafe_allow_unencrypted_bucket is False


def test_resolve_rejects_unknown_scheme():
    with pytest.raises(storage.StorageError, match="未対応"):
        storage.resolve("ftp://host/x")


def test_is_s3():
    assert storage.is_s3("s3://bucket/key")
    assert not storage.is_s3("/tmp/foo")
    assert not storage.is_s3("-")
    assert not storage.is_s3("file:///tmp/foo")


@pytest.mark.parametrize("uri", [
    r"C:\Users\foo\bundle.tar.gz",
    r"c:\tmp\out.bin",
    "D:/data/out.bin",
])
def test_resolve_windows_drive_letter_falls_back_to_local(uri):
    """Windows のドライブレター付きパスは urlparse が scheme と誤認するが
    LocalBackend にフォールバックされる"""
    assert isinstance(storage.resolve(uri), storage.LocalBackend)


def test_local_backend_file_uri_roundtrip(tmp_path):
    backend = storage.LocalBackend()
    dest = tmp_path / "via-uri.bin"
    uri = f"file://{dest}"
    backend.write_bytes(uri, b"xyz")
    assert dest.read_bytes() == b"xyz"
    assert backend.read_bytes(uri) == b"xyz"

    # localhost も許容
    uri_localhost = f"file://localhost{dest}"
    assert backend.read_bytes(uri_localhost) == b"xyz"


def test_local_backend_file_uri_rejects_remote_host(tmp_path):
    backend = storage.LocalBackend()
    with pytest.raises(storage.StorageError, match="ホスト指定"):
        backend.read_bytes("file://other-host/tmp/x")
    with pytest.raises(storage.StorageError, match="ホスト指定"):
        backend.write_bytes("file://other-host/tmp/x", b"data")


def test_stdio_backend_writes_to_stdout(monkeypatch):
    buf = io.BytesIO()

    class FakeStdout:
        buffer = buf

    monkeypatch.setattr(sys, "stdout", FakeStdout())
    storage.StdioBackend().write_bytes("-", b"hello")
    assert buf.getvalue() == b"hello"


def test_local_backend_write_creates_with_0600_no_toctou(tmp_path, monkeypatch):
    """`os.open` の mode 引数 (0o600) が確実に渡され、umask に依存せず作成時点から
    0600 になることを検証する"""
    backend = storage.LocalBackend()
    dest = tmp_path / "secure.bin"

    captured = {}
    real_os_open = storage.os.open

    def spy_open(path, flags, mode=0o777):
        captured['mode'] = mode
        captured['flags'] = flags
        return real_os_open(path, flags, mode)

    monkeypatch.setattr(storage.os, "open", spy_open)
    backend.write_bytes(str(dest), b"secret")
    assert captured['mode'] == 0o600
    # O_CREAT|O_TRUNC|O_WRONLY が含まれていること
    import os as _os
    assert captured['flags'] & _os.O_CREAT
    assert captured['flags'] & _os.O_TRUNC
    assert dest.stat().st_mode & 0o777 == 0o600


def test_local_backend_overwrite_existing_file_keeps_0600(tmp_path):
    """既存ファイル (0644) に上書きしても 0600 まで権限を絞れる"""
    backend = storage.LocalBackend()
    dest = tmp_path / "exists.bin"
    dest.write_bytes(b"old")
    dest.chmod(0o644)

    backend.write_bytes(str(dest), b"new")
    assert dest.read_bytes() == b"new"
    assert dest.stat().st_mode & 0o777 == 0o600


def test_local_backend_write_wraps_oserror_as_storage_error(tmp_path):
    """書き込み時の OSError は StorageError にラップされる"""
    backend = storage.LocalBackend()
    # 書き込み不可能なパス (存在しないルートを起点) — mkdir も失敗する状況を作る
    # FileExistsError をテストするため、parent をファイルにして mkdir を阻む
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"x")
    dest = blocker / "child" / "out.bin"
    with pytest.raises(storage.StorageError):
        backend.write_bytes(str(dest), b"data")


def test_local_backend_read_wraps_oserror_as_storage_error(tmp_path):
    """read 時の OSError (例: ディレクトリを read) は StorageError にラップされる"""
    backend = storage.LocalBackend()
    # ディレクトリを read_bytes すると IsADirectoryError
    with pytest.raises(storage.StorageError):
        backend.read_bytes(str(tmp_path))


# ---------------------------------------------------------------------------
# S3Backend
# ---------------------------------------------------------------------------


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


class _FakeS3Client:
    """boto3 client のスタブ。呼び出しを記録し、振る舞いをカスタマイズできる"""

    def __init__(
        self,
        *,
        get_encryption_error: Exception | None = None,
        put_error: Exception | None = None,
        get_object_error: Exception | None = None,
        object_payload: bytes = b'',
    ):
        self.get_encryption_error = get_encryption_error
        self.put_error = put_error
        self.get_object_error = get_object_error
        self.object_payload = object_payload
        self.calls: list[tuple[str, dict]] = []

    def get_bucket_encryption(self, **kwargs):
        self.calls.append(('get_bucket_encryption', kwargs))
        if self.get_encryption_error:
            raise self.get_encryption_error
        return {'ServerSideEncryptionConfiguration': {'Rules': []}}

    def put_object(self, **kwargs):
        self.calls.append(('put_object', kwargs))
        if self.put_error:
            raise self.put_error
        return {'ETag': '"deadbeef"'}

    def get_object(self, **kwargs):
        self.calls.append(('get_object', kwargs))
        if self.get_object_error:
            raise self.get_object_error
        return {'Body': _FakeBody(self.object_payload)}


def _make_aws_error(code: str) -> Exception:
    """botocore.exceptions.ClientError 相当のダックタイプエラーを作る
    (boto3 を実依存に入れず、S3Backend._error_code が response[Error][Code] を
    見るだけなので最小限の構造で再現できる)"""
    err = Exception(f"AWS error: {code}")
    err.response = {'Error': {'Code': code, 'Message': 'simulated'}}
    return err


def test_parse_s3_uri_valid():
    assert storage._parse_s3_uri("s3://bucket/key") == ("bucket", "key")
    assert storage._parse_s3_uri("s3://bucket/path/to/key.tar.gz") == (
        "bucket", "path/to/key.tar.gz"
    )


def test_parse_s3_uri_invalid():
    with pytest.raises(storage.StorageError, match="バケット名が空"):
        storage._parse_s3_uri("s3:///key")
    with pytest.raises(storage.StorageError, match="キー"):
        storage._parse_s3_uri("s3://bucket")
    with pytest.raises(storage.StorageError, match="キー"):
        storage._parse_s3_uri("s3://bucket/")
    with pytest.raises(storage.StorageError, match="S3 URI"):
        storage._parse_s3_uri("/tmp/foo")


def test_parse_s3_uri_preserves_query_and_fragment_in_key():
    """S3 のキー名は `?` / `#` を含めることができる。urlparse 由来の query/fragment
    切り落としに退行していないことを検証する (AWS CLI と同じ挙動)"""
    assert storage._parse_s3_uri("s3://bucket/key?with=query") == (
        "bucket", "key?with=query"
    )
    assert storage._parse_s3_uri("s3://bucket/path/to#hash") == (
        "bucket", "path/to#hash"
    )
    assert storage._parse_s3_uri("s3://bucket/a?b#c/d") == (
        "bucket", "a?b#c/d"
    )


def test_s3_options_from_env_defaults(monkeypatch):
    for var in ('DEVBASE_S3_SSE', 'DEVBASE_S3_SSE_KMS_KEY_ID',
                'DEVBASE_S3_ENDPOINT_URL', 'DEVBASE_S3_REGION'):
        monkeypatch.delenv(var, raising=False)
    opts = storage.S3Options.from_env()
    assert opts.sse == 'aws:kms'
    assert opts.sse_kms_key_id is None
    assert opts.endpoint_url is None
    assert opts.region is None
    assert opts.unsafe_allow_unencrypted_bucket is False


def test_s3_options_from_env_reads_overrides(monkeypatch):
    monkeypatch.setenv('DEVBASE_S3_SSE', 'AES256')
    monkeypatch.setenv('DEVBASE_S3_SSE_KMS_KEY_ID', 'alias/devbase')
    monkeypatch.setenv('DEVBASE_S3_ENDPOINT_URL', 'http://minio:9000')
    monkeypatch.setenv('DEVBASE_S3_REGION', 'ap-northeast-1')
    opts = storage.S3Options.from_env(unsafe_allow_unencrypted_bucket=True)
    assert opts.sse == 'AES256'
    assert opts.sse_kms_key_id == 'alias/devbase'
    assert opts.endpoint_url == 'http://minio:9000'
    assert opts.region == 'ap-northeast-1'
    assert opts.unsafe_allow_unencrypted_bucket is True


def test_s3_options_from_env_rejects_invalid_sse(monkeypatch):
    monkeypatch.setenv('DEVBASE_S3_SSE', 'rot13')
    with pytest.raises(storage.StorageError, match="DEVBASE_S3_SSE"):
        storage.S3Options.from_env()


def _attach_fake_client(backend, fake):
    """S3Backend に _get_client をモック付与する"""
    backend._client = fake
    return fake


def test_s3_backend_write_calls_put_object_with_sse():
    backend = storage.S3Backend(storage.S3Options(sse='aws:kms'))
    fake = _attach_fake_client(backend, _FakeS3Client())

    backend.write_bytes("s3://bucket/path/key.bin", b"payload")

    assert ('get_bucket_encryption', {'Bucket': 'bucket'}) in fake.calls
    put_calls = [args for name, args in fake.calls if name == 'put_object']
    assert len(put_calls) == 1
    args = put_calls[0]
    assert args['Bucket'] == 'bucket'
    assert args['Key'] == 'path/key.bin'
    assert args['Body'] == b"payload"
    assert args['ServerSideEncryption'] == 'aws:kms'
    assert 'SSEKMSKeyId' not in args


def test_s3_backend_write_passes_kms_key_id_when_specified():
    backend = storage.S3Backend(storage.S3Options(
        sse='aws:kms', sse_kms_key_id='alias/devbase',
    ))
    fake = _attach_fake_client(backend, _FakeS3Client())
    backend.write_bytes("s3://bucket/k", b"x")
    args = [a for n, a in fake.calls if n == 'put_object'][0]
    assert args['SSEKMSKeyId'] == 'alias/devbase'


def test_s3_backend_write_with_aes256_omits_kms_key_id():
    backend = storage.S3Backend(storage.S3Options(
        sse='AES256', sse_kms_key_id='alias/should-be-ignored',
    ))
    fake = _attach_fake_client(backend, _FakeS3Client())
    backend.write_bytes("s3://bucket/k", b"x")
    args = [a for n, a in fake.calls if n == 'put_object'][0]
    assert args['ServerSideEncryption'] == 'AES256'
    assert 'SSEKMSKeyId' not in args


def test_s3_backend_write_rejects_unencrypted_bucket():
    backend = storage.S3Backend(storage.S3Options())
    fake = _attach_fake_client(backend, _FakeS3Client(
        get_encryption_error=_make_aws_error(
            'ServerSideEncryptionConfigurationNotFoundError'
        ),
    ))
    with pytest.raises(storage.StorageError, match="デフォルト暗号化が未設定"):
        backend.write_bytes("s3://bucket/k", b"x")
    # PutObject まで到達していない
    assert not any(name == 'put_object' for name, _ in fake.calls)


def test_s3_backend_write_allows_unencrypted_bucket_with_unsafe_flag(caplog):
    backend = storage.S3Backend(storage.S3Options(
        unsafe_allow_unencrypted_bucket=True,
    ))
    fake = _attach_fake_client(backend, _FakeS3Client(
        get_encryption_error=_make_aws_error(
            'ServerSideEncryptionConfigurationNotFoundError'
        ),
    ))
    with caplog.at_level('WARNING'):
        backend.write_bytes("s3://bucket/k", b"x")
    assert any('unsafe' in r.message for r in caplog.records)
    assert any(name == 'put_object' for name, _ in fake.calls)


def test_s3_backend_write_rejects_access_denied_on_encryption_check():
    backend = storage.S3Backend(storage.S3Options())
    fake = _attach_fake_client(backend, _FakeS3Client(
        get_encryption_error=_make_aws_error('AccessDenied'),
    ))
    with pytest.raises(storage.StorageError, match="GetBucketEncryption"):
        backend.write_bytes("s3://bucket/k", b"x")


def test_s3_backend_write_allows_access_denied_with_unsafe_flag():
    backend = storage.S3Backend(storage.S3Options(
        unsafe_allow_unencrypted_bucket=True,
    ))
    fake = _attach_fake_client(backend, _FakeS3Client(
        get_encryption_error=_make_aws_error('AccessDenied'),
    ))
    backend.write_bytes("s3://bucket/k", b"x")
    assert any(name == 'put_object' for name, _ in fake.calls)


def test_s3_backend_write_rejects_unknown_encryption_check_error():
    """未知の GetBucketEncryption エラーは、unsafe フラグ無しでは中止する"""
    backend = storage.S3Backend(storage.S3Options())
    _attach_fake_client(backend, _FakeS3Client(
        get_encryption_error=_make_aws_error('NotImplemented'),
    ))
    with pytest.raises(storage.StorageError, match="バケット暗号化設定の確認に失敗"):
        backend.write_bytes("s3://bucket/k", b"x")


def test_s3_backend_write_allows_unknown_encryption_error_with_unsafe_flag(caplog):
    """S3 互換ストレージ (MinIO 等) で GetBucketEncryption が NotImplemented を
    返すケース: unsafe フラグ指定時は警告のみで PutObject へ進む"""
    backend = storage.S3Backend(storage.S3Options(
        unsafe_allow_unencrypted_bucket=True,
    ))
    fake = _attach_fake_client(backend, _FakeS3Client(
        get_encryption_error=_make_aws_error('NotImplemented'),
    ))
    with caplog.at_level('WARNING'):
        backend.write_bytes("s3://bucket/k", b"x")
    assert any('unsafe' in r.message for r in caplog.records)
    assert any(name == 'put_object' for name, _ in fake.calls)


def test_s3_backend_write_wraps_put_error():
    backend = storage.S3Backend(storage.S3Options())
    _attach_fake_client(backend, _FakeS3Client(
        put_error=_make_aws_error('InternalError'),
    ))
    with pytest.raises(storage.StorageError, match="書き込みに失敗"):
        backend.write_bytes("s3://bucket/k", b"x")


def test_s3_backend_read_calls_get_object():
    backend = storage.S3Backend()
    fake = _attach_fake_client(backend, _FakeS3Client(object_payload=b"hello"))
    data = backend.read_bytes("s3://bucket/path/key")
    assert data == b"hello"
    args = [a for n, a in fake.calls if n == 'get_object'][0]
    assert args == {'Bucket': 'bucket', 'Key': 'path/key'}


def test_s3_backend_read_raises_for_missing_object():
    backend = storage.S3Backend()
    _attach_fake_client(backend, _FakeS3Client(
        get_object_error=_make_aws_error('NoSuchKey'),
    ))
    with pytest.raises(storage.StorageError, match="見つかりません"):
        backend.read_bytes("s3://bucket/no-such")


def test_s3_backend_read_wraps_unknown_error():
    backend = storage.S3Backend()
    _attach_fake_client(backend, _FakeS3Client(
        get_object_error=_make_aws_error('SlowDown'),
    ))
    with pytest.raises(storage.StorageError, match="読み込みに失敗"):
        backend.read_bytes("s3://bucket/k")


def test_s3_backend_get_client_passes_endpoint_and_region(monkeypatch):
    """S3Options.endpoint_url / region が boto3.client へ正しく渡る"""
    backend = storage.S3Backend(storage.S3Options(
        endpoint_url='http://minio:9000',
        region='ap-northeast-1',
    ))

    captured_kwargs = {}

    def fake_client(service, **kwargs):
        captured_kwargs['service'] = service
        captured_kwargs.update(kwargs)
        return _FakeS3Client()

    fake_boto3 = type(sys)('boto3')
    fake_boto3.client = fake_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, 'boto3', fake_boto3)

    backend._get_client()

    assert captured_kwargs['service'] == 's3'
    assert captured_kwargs['endpoint_url'] == 'http://minio:9000'
    assert captured_kwargs['region_name'] == 'ap-northeast-1'
