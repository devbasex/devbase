"""ブートストラップ機密 — ``$DEVBASE_ROOT/secrets/bootstrap.env.age``

サーバ backend が接続に使う資格情報 (machine identity の client ID / client secret)
を置く。**登録簿を経由せず、直接 age で読み書きする。** 有効な backend を通して
読もうとすると「接続するための値を、接続しないと読めない」循環になる
(PLAN51 設計 1 / 決定 2)。

**平文へは落とさない。** age の識別鍵が無い端末では、鍵の用意を促して失敗させる。
接続資格情報を age ストアに置くことは要求の側で決まっており、鍵が無いことを理由に
平文の置き場を作ると満たせなくなる。

受信者の入れ替え (``devbase env rekey``) はこのファイルも対象に含める (決定 8)。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from devbase.env import cipher as _cipher
from devbase.env import io_common as _io_common
from devbase.env.secret_store import SECRETS_DIRNAME, AgeBackend, SecretStoreError
from devbase.env.store import EnvFile

BOOTSTRAP_FILENAME = 'bootstrap.env.age'

CLIENT_ID_KEY = 'DEVBASE_INFISICAL_CLIENT_ID'
CLIENT_SECRET_KEY = 'DEVBASE_INFISICAL_CLIENT_SECRET'


class BootstrapError(SecretStoreError):
    """ブートストラップ機密の読み書きエラー"""


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str

    def __repr__(self) -> str:  # 値をログ・例外へ載せない
        return f"Credentials(client_id={self.client_id!r}, client_secret='***')"


def path(devbase_root: Path) -> Path:
    return Path(devbase_root) / SECRETS_DIRNAME / BOOTSTRAP_FILENAME


def exists(devbase_root: Path) -> bool:
    return path(devbase_root).is_file()


def _age(devbase_root: Path) -> AgeBackend:
    return AgeBackend(Path(devbase_root))


def load(devbase_root: Path) -> Optional[Credentials]:
    """復号して返す。ファイルが無ければ ``None``、鍵が無ければ失敗する。"""
    target = path(devbase_root)
    if not target.is_file():
        return None
    try:
        blob = target.read_bytes()
    except OSError as e:
        raise BootstrapError(f"ブートストラップ機密を読めませんでした ({target}): {e}") from e
    try:
        plain = _cipher.decrypt(blob, identities=_age(devbase_root).identities())
    except _cipher.CipherError as e:
        raise BootstrapError(
            f"ブートストラップ機密を復号できませんでした ({target}): {e}") from e
    try:
        data = EnvFile.parse_bytes(plain)
    except UnicodeDecodeError as e:
        raise BootstrapError(
            f"ブートストラップ機密を UTF-8 として読めませんでした ({target}): {e}") from e
    missing = [key for key in (CLIENT_ID_KEY, CLIENT_SECRET_KEY) if not data.get(key)]
    if missing:
        raise BootstrapError(
            f"ブートストラップ機密に必要なキーがありません ({target}): {', '.join(missing)}\n"
            "  `devbase env backend use infisical --client-id ID --client-secret-stdin` "
            "で入れ直してください")
    return Credentials(client_id=data[CLIENT_ID_KEY], client_secret=data[CLIENT_SECRET_KEY])


def save(devbase_root: Path, creds: Credentials) -> Path:
    """age で暗号化して書く。受信者鍵が無ければ 1 バイトも書かない。"""
    target = path(devbase_root)
    plain = EnvFile.dump_bytes({CLIENT_ID_KEY: creds.client_id,
                                CLIENT_SECRET_KEY: creds.client_secret})
    blob = _age(devbase_root).encrypt_bytes(plain)
    try:
        _io_common.write_secure_bytes_atomic(target, blob)
    except OSError as e:
        raise BootstrapError(f"ブートストラップ機密を書き込めませんでした ({target}): {e}") from e
    return target
