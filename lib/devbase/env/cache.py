"""サーバ backend の機密の控え — ``$DEVBASE_ROOT/secrets/cache/`` (PLAN51)

サーバから取得できた (または devbase 自身が書いた) 機密を age で暗号化して控え、
サーバへ到達できないときにそこから読む。控えは**サーバの内容の写し**であって
記録ではなく、最後にサーバと一致すると確かめられた 1 世代だけを持つ (決定 5)。

配置 (参照 1 つにつき 1 ファイル):

- ``cache/team/global.env.age``、``cache/team/projects/<name>.env.age``
- ``cache/user/global.env.age``、``cache/user/projects/<name>.env.age``
- ``cache/index.json`` — 参照ごとの最終取得時刻と接続先ホスト。**表示のためだけ**に
  置き、キャッシュを使えるかの判定には使わない。キー名も値も入れない

**1 つの参照のキャッシュは 1 ファイルに収める。** 控えた機密と、取得元を表す ``scope``
(接続先 URL・project・environment・secretPath・client ID の SHA-256) を同じ age 暗号文の
中へ入れ、``write_secure_bytes_atomic`` で 1 回の置き換えとして書く。復号すると両方が
必ず同じ取得の結果として出るため、機密と ``scope`` が食い違った組み合わせは作れない。

書き直す条件と使ってよい条件は同じではない (設計 1 のキャッシュの節の表):

- 取得の成功 (0 件を含む) と書き込みの成功: 置き換える
- 書き込みの途中失敗、新しい世代を書けない: 消す
- 通信できない・応答を解釈できない: 残して**使う**
- 認証拒否 (401 / 403): 残して**使わない** (この判定は :mod:`infisical` 側が行う)

``cache.enabled`` が偽のときは、控えを作らないだけでなく既存の控えを消す (:func:`purge`)。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from devbase.env import cipher as _cipher
from devbase.env import io_common as _io_common
from devbase.env.secret_store import SECRETS_DIRNAME, SecretRef, SecretStore, SecretStoreError
from devbase.env.store import EnvFile
from devbase.log import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from devbase.env.infisical import InfisicalBackend

logger = get_logger(__name__)

CACHE_DIRNAME = 'cache'
INDEX_FILENAME = 'index.json'
FORMAT_VERSION = 1


class CacheError(SecretStoreError):
    """キャッシュの操作エラー"""


def cache_dir(devbase_root: Path) -> Path:
    return Path(devbase_root) / SECRETS_DIRNAME / CACHE_DIRNAME


def index_path(devbase_root: Path) -> Path:
    return cache_dir(devbase_root) / INDEX_FILENAME


def entry_path(devbase_root: Path, ref: SecretRef) -> Path:
    base = cache_dir(devbase_root) / ref.owner
    if ref.kind == 'global':
        return base / 'global.env.age'
    return base / 'projects' / f'{ref.name}.env.age'


def entry_key(ref: SecretRef) -> str:
    """``index.json`` のキー (``team:global`` / ``user:project:<name>``)"""
    if ref.kind == 'global':
        return f'{ref.owner}:global'
    return f'{ref.owner}:project:{ref.name}'


def cached_files(devbase_root: Path) -> List[Path]:
    """存在する控え (age 暗号文) の一覧。``index.json`` は含まない。"""
    base = cache_dir(devbase_root)
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob('*.env.age') if p.is_file())


def read_index(devbase_root: Path) -> Dict[str, Dict[str, Any]]:
    """``index.json`` の ``entries`` を返す (壊れていても空として扱う)"""
    path = index_path(devbase_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    entries = data.get('entries') if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}


def _write_index(devbase_root: Path, entries: Dict[str, Dict[str, Any]]) -> None:
    payload = json.dumps({'version': FORMAT_VERSION, 'entries': entries},
                         ensure_ascii=False, indent=2, sort_keys=True)
    try:
        _io_common.write_secure_bytes_atomic(index_path(devbase_root), payload.encode('utf-8'))
    except OSError as e:
        # 表示用の索引が書けなくても控えの可否は変わらない
        logger.debug("キャッシュの索引を更新できませんでした: %s", e)


def purge(devbase_root: Path) -> None:
    """``cache/`` 配下の控えをすべて消す。消せなければ残ったパスを挙げて失敗する。"""
    remaining: List[str] = []
    for path in cached_files(devbase_root):
        try:
            path.unlink()
        except OSError:
            remaining.append(str(path))
    index = index_path(devbase_root)
    if index.exists():
        try:
            index.unlink()
        except OSError:
            remaining.append(str(index))
    if remaining:
        raise CacheError(
            "キャッシュが無効なのに控えを消せませんでした。残っている控え:\n  "
            + '\n  '.join(remaining))


def scope_of(url: str, project_id: str, environment: str, secret_path: str,
             client_id: str) -> str:
    joined = '\n'.join((url, project_id, environment, secret_path, client_id))
    return 'sha256:' + hashlib.sha256(joined.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class CachedEntry:
    secrets: Dict[str, str]
    fetched_at: str
    scope: str
    backend: str


class SecretCache:
    """1 つの ``SecretStore`` に紐づく控えの読み書き"""

    def __init__(self, store: SecretStore):
        self._store = store
        self._root = Path(store.root)

    def _scope(self, ref: SecretRef, backend: 'InfisicalBackend') -> str:
        s = backend.settings
        return scope_of(s.url, s.project_id, s.environment,
                        backend.secret_path(ref), backend.client_id)

    # -- 書く -----------------------------------------------------------------

    def store(self, ref: SecretRef, secrets: Dict[str, str],
              backend: 'InfisicalBackend', *, after_write: bool = False) -> None:
        """取得・書き込みで確かめた内容で世代を置き換える。

        置き換えられなければその参照の控えを消す。前の世代を残すと、書き込みの前の
        機密が控えとして生き続けるため。サーバ側の操作は済んでいるので失敗にはせず、
        警告を出すにとどめる。
        """
        from devbase.env.infisical import url_host

        fetched_at = datetime.now().astimezone().isoformat(timespec='seconds')
        payload = {
            'version': FORMAT_VERSION,
            'scope': self._scope(ref, backend),
            'backend': backend.name,
            'fetched_at': fetched_at,
            'secrets': EnvFile.dump_bytes(secrets).decode('utf-8'),
        }
        path = entry_path(self._root, ref)
        try:
            blob = self._store.age.encrypt_bytes(
                json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            _io_common.write_secure_bytes_atomic(path, blob)
        except (SecretStoreError, OSError) as e:
            logger.warning("%sのキャッシュを更新できないため、控えを消します: %s",
                           ref.label(), e)
            self.discard(ref)
            return
        entries = read_index(self._root)
        entries[entry_key(ref)] = {
            'fetched_at': fetched_at,
            'backend': backend.name,
            'url_host': url_host(backend.url),
        }
        _write_index(self._root, entries)

    def discard(self, ref: SecretRef) -> None:
        path = entry_path(self._root, ref)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("%sのキャッシュを消せませんでした (%s): %s", ref.label(), path, e)
        entries = read_index(self._root)
        if entries.pop(entry_key(ref), None) is not None:
            _write_index(self._root, entries)

    # -- 読む -----------------------------------------------------------------

    def read(self, ref: SecretRef, backend: 'InfisicalBackend') -> Optional[CachedEntry]:
        """現在の設定と ``scope`` が一致する控えを返す (無ければ ``None``)"""
        path = entry_path(self._root, ref)
        if not path.is_file():
            return None
        try:
            blob = path.read_bytes()
            plain = _cipher.decrypt(blob, identities=self._store.age.identities())
            payload = json.loads(plain.decode('utf-8'))
        except (OSError, ValueError, UnicodeDecodeError, SecretStoreError,
                _cipher.CipherError) as e:
            logger.debug("%sのキャッシュを読めませんでした (%s): %s", ref.label(), path, e)
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get('backend') != backend.name:
            return None
        if payload.get('scope') != self._scope(ref, backend):
            logger.debug("%sのキャッシュは別の接続先のものです (使いません)", ref.label())
            return None
        raw = payload.get('secrets')
        if not isinstance(raw, str):
            return None
        try:
            secrets = EnvFile.parse_bytes(raw.encode('utf-8'))
        except UnicodeDecodeError:
            return None
        return CachedEntry(secrets=secrets, fetched_at=str(payload.get('fetched_at', '?')),
                           scope=str(payload.get('scope')), backend=str(payload.get('backend')))
