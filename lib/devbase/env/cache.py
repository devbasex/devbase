"""サーバ backend の機密の控え — ``$DEVBASE_ROOT/secrets/cache/`` (PLAN51)

サーバから取得できた (または devbase 自身が書いた) 機密を age で暗号化して控え、
サーバへ到達できないときにそこから読む。控えは**サーバの内容の写し**であって
記録ではなく、最後にサーバと一致すると確かめられた 1 世代だけを持つ (決定 5)。

配置 (参照 1 つにつき 1 ファイル):

- ``cache/team/global.env.age``、``cache/team/projects/<name>.env.age``
- ``cache/user/global.env.age``、``cache/user/projects/<name>.env.age``
- ``cache/index.json`` — 参照ごとの最終取得時刻と接続先ホスト。**表示のためだけ**に
  置き、キャッシュを使えるかの判定には使わない。キー名も値も入れない

``backend.yml`` が ``version: 2`` (``openbao.layout: group``) のときは、持ち主の次に置き場の
グループ名を挟む (``cache/team/<g>/global.env.age``、``index.json`` のキーは
``team:<g>:global``。PLAN56)。グループの違うプロジェクトを順に起動しても、後の控えが
先の控えを上書きしない。位置は設定の :meth:`~devbase.env.backend_config.OpenBaoSettings.cache_relpath`
が組み、``version: 1`` では上の位置のままである。

**1 つの参照のキャッシュは 1 ファイルに収める。** 控えた機密と、取得元を表す ``scope``
(接続先 URL・mount・パス・role_id の SHA-256) を同じ age 暗号文の
中へ入れ、``write_secure_bytes_atomic`` で 1 回の置き換えとして書く。復号すると両方が
必ず同じ取得の結果として出るため、機密と ``scope`` が食い違った組み合わせは作れない。

書き直す条件と使ってよい条件は同じではない (設計 1 のキャッシュの節の表):

- 取得の成功 (0 件・404 を含む) と書き込みの成功: 置き換える
- 版の不一致、結果が分からない書き込み、新しい世代を書けない: 消す
- 取得で通信できない・応答を解釈できない: 残して**使う**
- 認証拒否・権限の不足: 残して**使わない** (この判定は :mod:`openbao` 側が行う)

**控えに版は入れない。** 控えは読み取りにだけ使い、書き戻しの基準にしない
(:mod:`openbao` が控えから読んだ参照の ``save`` を拒む)。

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
    from devbase.env.backend_config import OpenBaoSettings
    from devbase.env.openbao import OpenBaoBackend

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


def _flat_settings(ref: SecretRef) -> 'OpenBaoSettings':
    """設定を渡されなかったときの位置の組み立て役 (``version: 1`` の並び)。

    グループの付いた参照の位置は読み替えを含む設定が無いと決まらないため、ここでは拒む。
    """
    from devbase.env.backend_config import OpenBaoSettings

    if ref.group:
        raise CacheError(f"{ref.label()}の控えの位置は backend の設定なしに決まりません")
    return OpenBaoSettings(url='', user='')


def entry_path(devbase_root: Path, ref: SecretRef,
               settings: Optional['OpenBaoSettings'] = None) -> Path:
    """控えのファイルの位置。``settings`` を省くと ``version: 1`` の並びで組む"""
    settings = settings if settings is not None else _flat_settings(ref)
    return cache_dir(devbase_root).joinpath(*settings.cache_relpath(ref).split('/'))


def entry_key(ref: SecretRef, settings: Optional['OpenBaoSettings'] = None) -> str:
    """``index.json`` のキー (``team:global`` / ``user:project:<name>`` / ``team:<g>:global``)"""
    settings = settings if settings is not None else _flat_settings(ref)
    return settings.cache_key(ref)


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


def scope_of(url: str, mount: str, path: str, role_id: str) -> str:
    joined = '\n'.join((url, mount, path, role_id))
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

    def _settings(self) -> 'OpenBaoSettings':
        """控えの位置を組む設定 (``version: 1`` / ``2`` で並びが違う)"""
        settings = self._store.config.openbao
        if settings is None:
            raise CacheError("backend: openbao の設定がありません")
        return settings

    def _path(self, ref: SecretRef, backend: 'OpenBaoBackend') -> Path:
        # 参照とレイアウトの食い違いは backend が先に止める (path_of と同じ検査を通す)
        backend.path_of(ref)
        return entry_path(self._root, ref, backend.settings)

    def _scope(self, ref: SecretRef, backend: 'OpenBaoBackend') -> str:
        # 材料は backend が持つ (cache は設定の形を知らない)
        return backend.cache_scope(ref)

    # -- 書く -----------------------------------------------------------------

    def store(self, ref: SecretRef, secrets: Dict[str, str],
              backend: 'OpenBaoBackend', *, after_write: bool = False) -> None:
        """取得・書き込みで確かめた内容で世代を置き換える。

        置き換えられなければその参照の控えを消す。前の世代を残すと、書き込みの前の
        機密が控えとして生き続けるため。サーバ側の操作は済んでいるので失敗にはせず、
        警告を出すにとどめる。
        """
        from devbase.env.openbao import url_host

        fetched_at = datetime.now().astimezone().isoformat(timespec='seconds')
        payload = {
            'version': FORMAT_VERSION,
            'scope': self._scope(ref, backend),
            'backend': backend.name,
            'fetched_at': fetched_at,
            'secrets': EnvFile.dump_bytes(secrets).decode('utf-8'),
        }
        path = self._path(ref, backend)
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
        entries[entry_key(ref, backend.settings)] = {
            'fetched_at': fetched_at,
            'backend': backend.name,
            'url_host': url_host(backend.url),
        }
        _write_index(self._root, entries)

    def discard(self, ref: SecretRef) -> None:
        settings = self._settings()
        path = entry_path(self._root, ref, settings)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("%sのキャッシュを消せませんでした (%s): %s", ref.label(), path, e)
        entries = read_index(self._root)
        if entries.pop(entry_key(ref, settings), None) is not None:
            _write_index(self._root, entries)

    # -- 読む -----------------------------------------------------------------

    def read(self, ref: SecretRef, backend: 'OpenBaoBackend') -> Optional[CachedEntry]:
        """現在の設定と ``scope`` が一致する控えを返す (無ければ ``None``)"""
        path = self._path(ref, backend)
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
