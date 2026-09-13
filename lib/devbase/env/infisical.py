"""Infisical を保存先にする ``SecretBackend`` 実装 (PLAN51)

REST を標準ライブラリの ``urllib.request`` で叩く (決定 3: 常時依存を増やさない)。
使う経路は設計 2「Infisical との契約」の 5 つだけ:

- ``POST /api/v1/auth/universal-auth/login`` — client ID / secret を access token へ交換
- ``GET /api/v4/secrets`` — 参照 (``secretPath``) ごとの一括取得
- ``POST`` / ``PATCH`` / ``DELETE /api/v4/secrets/{name}`` — キー単位の作成・更新・削除

access token はプロセス内にだけ持ち、ディスクへ書かない。取得が 401 を返したときは
1 度だけ取り直して同じ取得をやり直し、それでも 401 / 403 なら資格の取り消しとして
扱う (決定 9)。

``save`` は参照の内容**全体**を受け取り、サーバの現状との差分 (DELETE / PATCH / POST)
だけを送る (決定 10)。値が同じキーは送らない。途中で失敗したら残りを送らず、
どこまで反映したかをキー名で述べて止める。巻き戻しはしない。

失敗の種類は例外の型で区別し、キャッシュ (:mod:`devbase.env.cache`) の可否判定に使う:

- :class:`SecretUnreachableError` — 通信できない・応答を解釈できない。キャッシュを**使う**
- :class:`SecretAuthError` — 401 / 403。キャッシュを**使わない**
- :class:`SecretWriteError` — 書き込みの途中で失敗。その参照の控えを**消す**
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as _urlerror
from urllib import request as _urlrequest
from urllib.parse import urlencode, urlsplit

from devbase.env import bootstrap as _bootstrap
from devbase.env.secret_store import SecretRef, SecretStore, SecretStoreError
from devbase.env.store import EnvFile
from devbase.log import get_logger

logger = get_logger(__name__)

BACKEND_NAME = 'infisical'

LOGIN_PATH = '/api/v1/auth/universal-auth/login'


class SecretUnreachableError(SecretStoreError):
    """サーバへ通信できない、または応答を解釈できない"""


class SecretAuthError(SecretStoreError):
    """認証を拒まれた (401 / 403)。資格の取り消しとして扱う"""


class SecretWriteError(SecretStoreError):
    """書き込みが途中で失敗した。``applied`` に反映済みのキー名を持つ"""

    def __init__(self, message: str, applied: List[str]):
        super().__init__(message)
        self.applied = list(applied)


class _HttpStatus(Exception):
    """HTTP の失敗応答 (内部用)"""

    def __init__(self, status: int, body: bytes):
        super().__init__(f'HTTP {status}')
        self.status = status
        self.body = body


class InfisicalBackend:
    """Infisical の REST を叩く backend"""

    name = BACKEND_NAME
    direct_edit = False

    def __init__(self, store: SecretStore):
        self._store = store
        self._root = Path(store.root)
        settings = store.config.infisical
        if settings is None:
            raise SecretStoreError("backend: infisical の設定がありません")
        self._settings = settings
        self._creds: Optional[_bootstrap.Credentials] = None
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        #: この backend インスタンスで最後に取得・書き込みした内容 (参照ごと)。
        #: ``exists`` → ``load`` の並びで同じ参照を 2 度取りに行かないための控え。
        #: 書き込み前の差分計算には使わず、必ず取り直す。
        self._seen: Dict[SecretRef, Dict[str, str]] = {}
        from devbase.env import cache as _cache

        self._cache = _cache.SecretCache(store) if store.config.cache_enabled else None
        if self._cache is None:
            _cache.purge(self._root)

    # -- 参照と置き場 ---------------------------------------------------------

    @property
    def settings(self):
        return self._settings

    @property
    def url(self) -> str:
        return self._settings.url

    def secret_path(self, ref: SecretRef) -> str:
        return self._settings.secret_path(ref)

    def path(self, ref: SecretRef) -> Path:
        """``secretPath`` を ``Path`` にしたもの。**表示と由来の記録のための値**で、
        ローカルには存在しない (開いて読み書きしてはいけない)。"""
        return Path(self.secret_path(ref))

    # -- 認証 -----------------------------------------------------------------

    def _credentials(self) -> _bootstrap.Credentials:
        if self._creds is None:
            creds = _bootstrap.load(self._root)
            if creds is None:
                raise SecretStoreError(
                    f"Infisical の接続資格情報がありません ({_bootstrap.path(self._root)})。\n"
                    "  `devbase env backend use infisical --client-id ID "
                    "--client-secret-stdin` で設定してください")
            self._creds = creds
        return self._creds

    @property
    def client_id(self) -> str:
        return self._credentials().client_id

    def login(self) -> None:
        """client secret を access token へ交換する (プロセス内にだけ持つ)"""
        creds = self._credentials()
        try:
            data = self._http('POST', LOGIN_PATH,
                              body={'clientId': creds.client_id,
                                    'clientSecret': creds.client_secret},
                              auth=False)
        except _HttpStatus as e:
            if e.status in (401, 403):
                raise SecretAuthError(
                    f"Infisical の資格を確認できません (認証が拒まれました: HTTP {e.status})\n"
                    f"  接続先: {self.url}\n"
                    "  client ID / client secret が失効していないか確認してください") from None
            raise SecretUnreachableError(
                f"Infisical へ到達できません (HTTP {e.status})\n  接続先: {self.url}") from None
        token = data.get('accessToken') if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise SecretUnreachableError(
                f"Infisical の認証応答を解釈できません\n  接続先: {self.url}")
        expires = data.get('expiresIn')
        ttl = float(expires) if isinstance(expires, (int, float)) and expires > 0 else 3600.0
        self._token = token
        # 期限の少し前に取り直す。境界で 401 を踏んでも再認証 1 回で吸収できる
        self._token_expires_at = time.monotonic() + max(ttl - 30.0, 1.0)

    def _ensure_token(self) -> str:
        if self._token is None or time.monotonic() >= self._token_expires_at:
            self.login()
        assert self._token is not None
        return self._token

    # -- HTTP -----------------------------------------------------------------

    def _http(self, method: str, path: str, *, query: Optional[Dict[str, str]] = None,
              body: Optional[Dict[str, Any]] = None, auth: bool = True) -> Any:
        url = self.url.rstrip('/') + path
        if query:
            url += '?' + urlencode(query)
        headers = {'Accept': 'application/json'}
        payload: Optional[bytes] = None
        if body is not None:
            payload = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if auth:
            headers['Authorization'] = f'Bearer {self._ensure_token()}'
        req = _urlrequest.Request(url, data=payload, method=method, headers=headers)
        try:
            with _urlrequest.urlopen(req, timeout=self._settings.timeout_seconds) as resp:
                raw = resp.read()
        except _urlerror.HTTPError as e:
            raise _HttpStatus(e.code, e.read() or b'') from None
        except (_urlerror.URLError, socket.timeout, ConnectionError, OSError) as e:
            raise SecretUnreachableError(
                f"Infisical へ到達できません ({e.reason if hasattr(e, 'reason') else e})\n"
                f"  接続先: {self.url}") from None
        if not raw:
            return {}
        try:
            return json.loads(raw.decode('utf-8'))
        except ValueError:
            raise SecretUnreachableError(
                f"Infisical の応答を JSON として読めません ({method} {path})\n"
                f"  接続先: {self.url}") from None

    def _authed(self, method: str, path: str, *, query=None, body=None) -> Any:
        """認証付きで叩く。401 なら 1 度だけ再認証してやり直す (決定 9)。"""
        try:
            return self._http(method, path, query=query, body=body)
        except _HttpStatus as e:
            if e.status != 401:
                raise
        self._token = None
        self.login()
        return self._http(method, path, query=query, body=body)

    def _base_query(self, ref: SecretRef) -> Dict[str, str]:
        return {
            'projectId': self._settings.project_id,
            'environment': self._settings.environment,
            'secretPath': self.secret_path(ref),
        }

    def _auth_error(self, status: int, ref: SecretRef) -> SecretAuthError:
        return SecretAuthError(
            f"Infisical の資格を確認できません ({ref.label()}: HTTP {status})\n"
            f"  接続先: {self.url}\n"
            f"  secretPath: {self.secret_path(ref)}\n"
            "  client secret の失効、または参照の読み書き権限が外れていないか確認してください")

    def _unreachable(self, status: int, ref: SecretRef) -> SecretUnreachableError:
        return SecretUnreachableError(
            f"Infisical へ到達できません ({ref.label()}: HTTP {status})\n  接続先: {self.url}")

    # -- 取得 -----------------------------------------------------------------

    def fetch(self, ref: SecretRef) -> Dict[str, str]:
        """サーバから取り直す (キャッシュへは落ちない)。

        成功したら (0 件でも) キャッシュを取得した内容で置き換える (前提 4)。
        """
        query = dict(self._base_query(ref))
        query['expandSecretReferences'] = 'false'
        query['includePersonalOverrides'] = 'false'
        try:
            data = self._authed('GET', '/api/v4/secrets', query=query)
        except _HttpStatus as e:
            if e.status in (401, 403):
                raise self._auth_error(e.status, ref) from None
            raise self._unreachable(e.status, ref) from None
        secrets = data.get('secrets') if isinstance(data, dict) else None
        if not isinstance(secrets, list):
            raise SecretUnreachableError(
                f"Infisical の応答を解釈できません ({ref.label()}: secrets が配列ではありません)\n"
                f"  接続先: {self.url}")
        result: Dict[str, str] = {}
        for item in secrets:
            if not isinstance(item, dict):
                continue
            key = item.get('secretKey')
            if isinstance(key, str) and key:
                value = item.get('secretValue')
                result[key] = '' if value is None else str(value)
        self._remember(ref, result)
        return result

    def _remember(self, ref: SecretRef, data: Dict[str, str]) -> None:
        self._seen[ref] = dict(data)
        if self._cache is not None:
            self._cache.store(ref, data, self)

    def load(self, ref: SecretRef) -> Dict[str, str]:
        """取得する。通信できないときだけキャッシュへ落ちる (決定 9)。"""
        if ref in self._seen:
            return dict(self._seen[ref])
        try:
            return self.fetch(ref)
        except SecretUnreachableError as e:
            if self._cache is None:
                raise
            cached = self._cache.read(ref, self)
            if cached is None:
                raise SecretUnreachableError(
                    f"{e}\n  キャッシュもありません ({ref.label()})") from None
            logger.warning("Infisical へ到達できないため、%s のキャッシュを使います "
                           "(最終取得 %s)", ref.label(), cached.fetched_at)
            self._seen[ref] = dict(cached.secrets)
            return dict(cached.secrets)

    def exists(self, ref: SecretRef) -> bool:
        return bool(self.load(ref))

    def load_bytes(self, ref: SecretRef) -> bytes:
        return EnvFile.dump_bytes(self.load(ref))

    # -- 書き込み -------------------------------------------------------------

    def _write(self, method: str, ref: SecretRef, key: str,
               value: Optional[str] = None) -> None:
        body: Dict[str, Any] = dict(self._base_query(ref))
        if value is not None:
            body['secretValue'] = value
        try:
            self._authed(method, f'/api/v4/secrets/{key}', body=body)
        except _HttpStatus as e:
            if e.status in (401, 403):
                raise self._auth_error(e.status, ref) from None
            raise self._unreachable(e.status, ref) from None

    def save(self, ref: SecretRef, data: Dict[str, str]) -> Path:
        """参照の内容全体を ``data`` に揃える (差分だけ送る)"""
        current = self.fetch(ref)
        # 取得は成功しているので、この時点でキャッシュは取得内容へ進んでいる。
        # 書き込みが 1 件でも失敗したら「サーバの写し」と言える世代が無くなるので消す。
        deletes = [k for k in sorted(current) if k not in data]
        patches = [k for k in sorted(data) if k in current and current[k] != data[k]]
        posts = [k for k in sorted(data) if k not in current]
        applied: List[str] = []
        try:
            for key in deletes:
                self._write('DELETE', ref, key)
                applied.append(f'-{key}')
            for key in patches:
                self._write('PATCH', ref, key, data[key])
                applied.append(f'~{key}')
            for key in posts:
                self._write('POST', ref, key, data[key])
                applied.append(f'+{key}')
        except SecretStoreError as e:
            self._seen.pop(ref, None)
            if self._cache is not None:
                self._cache.discard(ref)
            pending = len(deletes) + len(patches) + len(posts) - len(applied)
            raise SecretWriteError(
                f"{ref.label()}の書き込みが途中で失敗しました: {e}\n"
                f"  反映済み: {', '.join(applied) if applied else '(なし)'}\n"
                f"  未反映: {pending} 件 (WebUI でサーバ側の状態を確認してください)",
                applied=applied) from None
        self._seen[ref] = dict(data)
        if self._cache is not None:
            self._cache.store(ref, data, self, after_write=True)
        return self.path(ref)

    def save_bytes(self, ref: SecretRef, data: bytes) -> Path:
        try:
            parsed = EnvFile.parse_bytes(data)
        except UnicodeDecodeError as e:
            raise SecretStoreError(f"{ref.label()}の内容を UTF-8 として読めません: {e}") from e
        return self.save(ref, parsed)

    def remove(self, ref: SecretRef) -> bool:
        existed = bool(self.fetch(ref))
        if existed:
            self.save(ref, {})
        return existed

    # -- 疎通確認 -------------------------------------------------------------

    def probe(self, refs: List[SecretRef]) -> List[Tuple[SecretRef, int]]:
        """認証して各参照を読み、(参照, 件数) を返す (キャッシュへは落ちない)"""
        self.login()
        return [(ref, len(self.fetch(ref))) for ref in refs]


def url_host(url: str) -> str:
    return urlsplit(url).hostname or url
