"""OpenBao (KV v2 + AppRole) を保存先にする ``SecretBackend`` 実装 (PLAN51)

REST を標準ライブラリの ``urllib.request`` で叩く (常時依存を増やさない)。
使う経路は仕様「OpenBao との契約」の 4 つだけ:

- ``POST /v1/auth/approle/login`` — ``role_id`` / ``secret_id`` を token へ交換
- ``GET /v1/<mount>/data/<path>`` — 参照 (パス) ごとの取得。内容と版が返る
- ``POST /v1/<mount>/data/<path>`` — 版を指定した丸ごとの置き換え (check-and-set)
- ``DELETE /v1/<mount>/metadata/<path>`` — 参照ごとの削除 (全版)

token はプロセス内にだけ持ち、ディスクへ書かない。実行のたびに取り直し、期限の少し前に
取り直す。応答の状態による再認証は置かず、認証以外の経路の 403 は権限の不足として確定
する。

``save`` は参照の内容**全体**を受け取り、**この ``SecretStore`` でその参照を最後に読んだ
ときの版**を ``options.cas`` に付けて丸ごと置き換える。書き込みの直前に取り直した版を
使うと、読んでから書くまでの間の他人の更新に CAS が通ってしまい黙って上書きするため。
控えから読んだ参照は版を持たず、書き戻せない。

失敗の種類は例外の型で区別し、キャッシュ (:mod:`devbase.env.cache`) の扱いを決める:

- :class:`SecretUnreachableError` — 通信できない・応答を解釈できない。取得ならキャッシュを
  **使う**。結果が分からない書き込みでは、その参照の控えを**消す**
- :class:`SecretAuthError` — ログインの拒否と権限の不足。キャッシュを**使わない**
- :class:`SecretConflictError` — 版の不一致 (読んでから書くまでの間に他人が書いた)。
  その参照の控えを**消す**

後の 2 つは :class:`SecretRefusedError` で、サーバが拒んだと確定している (サーバは
変わっていない)。巻き戻しはこの型で落ちた参照を対象から外す。
"""

from __future__ import annotations

import http.client
import json
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as _urlerror
from urllib import request as _urlrequest
from urllib.parse import quote, urlsplit

from devbase.env import bootstrap as _bootstrap
from devbase.env.secret_store import SecretRef, SecretStore, SecretStoreError
from devbase.env.store import EnvFile
from devbase.log import get_logger

logger = get_logger(__name__)

BACKEND_NAME = 'openbao'

LOGIN_PATH = '/v1/auth/approle/login'

#: check-and-set の不一致を表す、応答の ``errors`` に含まれる語
_CAS_MISMATCH = 'check-and-set'


class SecretUnreachableError(SecretStoreError):
    """サーバへ到達できない、または応答を解釈できない (キャッシュへ落ちてよい)"""


class SecretRefusedError(SecretStoreError):
    """サーバが拒んだと確定した失敗。**サーバは変わっていない**。

    書き込みの巻き戻し (``import`` / ``migrate``) は、この型の失敗で落ちた参照を
    対象から外す。巻き戻すと、読み直した版で控えた値を書き戻し、CAS が守った他の
    利用者の更新を消してしまう。
    """


class SecretAuthError(SecretRefusedError):
    """認証の拒否、または権限の不足 (キャッシュへ落ちてはいけない)"""


class SecretConflictError(SecretRefusedError):
    """版の不一致。読んでから書くまでの間に他の誰かが書いた"""


class _HttpStatus(Exception):
    """HTTP の失敗応答 (内部用)"""

    def __init__(self, status: int, body: bytes):
        super().__init__(f'HTTP {status}')
        self.status = status
        self.body = body

    def errors(self) -> List[str]:
        """応答の ``errors`` (解釈できなければ空)"""
        try:
            data = json.loads(self.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return []
        errors = data.get('errors') if isinstance(data, dict) else None
        return [str(e) for e in errors] if isinstance(errors, list) else []


@dataclass(frozen=True)
class _Basis:
    """この ``SecretStore`` で参照を最後に読んだ・書いたときの内容と版。

    ``version`` が ``None`` なのは控えから読んだときで、その参照は書き戻せない。
    """

    secrets: Dict[str, str]
    version: Optional[int]


class OpenBaoBackend:
    """OpenBao の KV v2 を叩く backend"""

    name = BACKEND_NAME
    direct_edit = False
    has_user_refs = True

    def __init__(self, store: SecretStore):
        self._store = store
        self._root = Path(store.root)
        settings = store.config.openbao
        if settings is None:
            raise SecretStoreError("backend: openbao の設定がありません")
        self._settings = settings
        self._creds: Optional[_bootstrap.Credentials] = None
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        #: この backend インスタンスで最後に取得・書き込みした内容と版 (参照ごと)。
        #: ``exists`` → ``load`` の並びで同じ参照を 2 度取りに行かないための控えであり、
        #: 同時に次の書き込みの CAS の基準でもある。
        self._seen: Dict[SecretRef, _Basis] = {}
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

    def path_of(self, ref: SecretRef) -> str:
        """KV v2 のパス (``<mount>`` を含まない)"""
        return self._settings.path_of(ref)

    def display_path(self, ref: SecretRef) -> str:
        """表示用の ``<mount>/<パス>``"""
        return self._settings.display_path(ref)

    def path(self, ref: SecretRef) -> Path:
        """``<mount>/<パス>`` を ``Path`` にしたもの。**表示と由来の記録のための値**で、
        ローカルには存在しない (開いて読み書きしてはいけない)。"""
        return Path(self.display_path(ref))

    def cache_scope(self, ref: SecretRef) -> str:
        """キャッシュの取得元を表す指紋の材料 (接続先 URL・mount・パス・role_id)"""
        from devbase.env.cache import scope_of

        return scope_of(self.url, self._settings.mount, self.path_of(ref), self.role_id)

    # -- 認証 -----------------------------------------------------------------

    def _credentials(self) -> _bootstrap.Credentials:
        if self._creds is None:
            creds = _bootstrap.load(self._root)
            if creds is None:
                raise SecretStoreError(
                    f"OpenBao の接続資格情報がありません ({_bootstrap.path(self._root)})。\n"
                    "  `devbase env backend use openbao --role-id ID "
                    "--secret-id-stdin` で設定してください")
            self._creds = creds
        return self._creds

    @property
    def role_id(self) -> str:
        return self._credentials().role_id

    def login(self) -> None:
        """``role_id`` / ``secret_id`` を token へ交換する (プロセス内にだけ持つ)"""
        creds = self._credentials()
        try:
            data = self._http('POST', LOGIN_PATH,
                              body={'role_id': creds.role_id, 'secret_id': creds.secret_id},
                              auth=False)
        except _HttpStatus as e:
            # 400 (invalid role or secret) は secret_id の失効・書き間違い、403 は利用者
            # (entity) の無効化。どちらも資格の取り消しで、キャッシュへ落ちてはいけない
            if e.status in (400, 403):
                raise SecretAuthError(
                    f"OpenBao の資格を確認できません (認証が拒まれました: HTTP {e.status})\n"
                    f"  接続先: {self.url}\n"
                    "  この端末の secret_id が失効していないか、利用者が無効化されていないか"
                    "確認してください") from None
            raise SecretUnreachableError(
                f"OpenBao へ到達できません (HTTP {e.status})\n  接続先: {self.url}") from None
        auth = data.get('auth') if isinstance(data, dict) else None
        token = auth.get('client_token') if isinstance(auth, dict) else None
        if not isinstance(token, str) or not token:
            raise SecretUnreachableError(
                f"OpenBao の認証応答を解釈できません\n  接続先: {self.url}")
        lease = auth.get('lease_duration')
        ttl = float(lease) if isinstance(lease, (int, float)) and lease > 0 else 3600.0
        self._token = token
        # 期限の少し前に取り直す。エディタを長く開いた env edit の書き戻しで、期限切れの
        # token を送らないため
        self._token_expires_at = time.monotonic() + max(ttl - 30.0, 1.0)

    def _ensure_token(self) -> str:
        if self._token is None or time.monotonic() >= self._token_expires_at:
            self.login()
        assert self._token is not None
        return self._token

    def issue_token(self) -> str:
        """起動中のコンテナの ``bao`` へ渡す token を返す (PLAN54)。

        読み書きに使っている token と同じもので、無いか期限が近ければログインし直す。
        同じ ``SecretStore`` で注入した直後に呼べば、ログインは増えない。token は
        控えない (控えから起動したときに書き戻せないのと同じく、サーバの答えが要る)。
        """
        return self._ensure_token()

    # -- HTTP -----------------------------------------------------------------

    def _kv_path(self, kind: str, ref: SecretRef) -> str:
        """``/v1/<mount>/<kind>/<path>``。各要素を符号化する (``/`` は区切りとして残す)"""
        mount = quote(self._settings.mount, safe='')
        path = '/'.join(quote(part, safe='') for part in self.path_of(ref).split('/'))
        return f'/v1/{mount}/{kind}/{path}'

    def _http(self, method: str, path: str, *, body: Optional[Dict[str, Any]] = None,
              auth: bool = True) -> Any:
        url = self.url.rstrip('/') + path
        headers = {'Accept': 'application/json'}
        payload: Optional[bytes] = None
        if body is not None:
            payload = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if auth:
            headers['X-Vault-Token'] = self._ensure_token()
        req = _urlrequest.Request(url, data=payload, method=method, headers=headers)
        try:
            with _urlrequest.urlopen(req, timeout=self._settings.timeout_seconds) as resp:
                raw = resp.read()
        except _urlerror.HTTPError as e:
            # 失敗応答の本文も最後まで受け取れないことがある (5xx の途中切れ)。
            # 状態コードだけで拒否と確定させず、不達として扱う
            try:
                body = e.read() or b''
            except (OSError, http.client.HTTPException) as read_error:
                raise SecretUnreachableError(
                    f"OpenBao へ到達できません (HTTP {e.code} の応答を最後まで受け取れません: "
                    f"{read_error})\n  接続先: {self.url}") from None
            raise _HttpStatus(e.code, body) from None
        except (_urlerror.URLError, socket.timeout, ConnectionError, OSError,
                http.client.HTTPException) as e:
            # 本文の途中で切られたとき (IncompleteRead) も「応答を最後まで受け取れなかった」
            # 不達として扱う
            raise SecretUnreachableError(
                f"OpenBao へ到達できません ({e.reason if hasattr(e, 'reason') else e})\n"
                f"  接続先: {self.url}") from None
        if not raw:
            return {}
        try:
            return json.loads(raw.decode('utf-8'))
        except ValueError:
            raise SecretUnreachableError(
                f"OpenBao の応答を JSON として読めません ({method} {path})\n"
                f"  接続先: {self.url}") from None

    def _unreachable(self, status: int, ref: SecretRef) -> SecretUnreachableError:
        return SecretUnreachableError(
            f"OpenBao へ到達できません ({ref.label()}: HTTP {status})\n  接続先: {self.url}")

    def _unreadable(self, ref: SecretRef, why: str) -> SecretUnreachableError:
        return SecretUnreachableError(
            f"OpenBao の応答を解釈できません ({ref.label()}: {why})\n  接続先: {self.url}")

    def _forbidden(self, ref: SecretRef, action: str, *,
                   hint: str = '', show_path: bool = True) -> SecretAuthError:
        """HTTP 403 の共通の封筒 (label・HTTP 403・接続先) を組む。

        ``action`` は「参照」に続く操作固有の語 (``を読む`` / ``への書き込み`` /
        ``を削除する``)。``show_path`` が真ならパス行を足す。``hint`` があれば
        末尾へ足す (削除は付けない)。
        """
        message = (
            f"OpenBao でこの参照{action}権限がありません ({ref.label()}: HTTP 403)\n"
            f"  接続先: {self.url}")
        if show_path:
            message += f"\n  パス: {self.display_path(ref)}"
        if hint:
            message += f"\n{hint}"
        return SecretAuthError(message)

    # -- 取得 -----------------------------------------------------------------

    @staticmethod
    def _version_of(data: Any) -> Optional[int]:
        """応答の ``data.metadata.version`` (無ければ ``None``)"""
        inner = data.get('data') if isinstance(data, dict) else None
        metadata = inner.get('metadata') if isinstance(inner, dict) else None
        version = metadata.get('version') if isinstance(metadata, dict) else None
        return version if isinstance(version, int) and not isinstance(version, bool) else None

    def _parse_secrets(self, ref: SecretRef, data: Any) -> Dict[str, str]:
        inner = data.get('data') if isinstance(data, dict) else None
        values = inner.get('data') if isinstance(inner, dict) else None
        if not isinstance(values, dict):
            raise self._unreadable(ref, 'data.data が辞書ではありません')
        result: Dict[str, str] = {}
        for key, value in values.items():
            if not isinstance(key, str) or not key:
                raise self._unreadable(ref, 'キー名が文字列ではありません')
            # devbase と WebUI は文字列しか書かない。それ以外の形式は解釈できない応答として
            # 扱い、str() で読み替えて別の値をコンテナへ渡さない
            if not isinstance(value, str):
                raise self._unreadable(ref, f'値が文字列ではありません: {key}')
            result[key] = value
        return result

    def fetch(self, ref: SecretRef) -> Dict[str, str]:
        """サーバから取り直す (キャッシュへは落ちない)。

        成功したら (0 件・404 でも) キャッシュを取得した内容で置き換え、この参照の
        基準 (内容と版) を更新する。
        """
        try:
            data = self._http('GET', self._kv_path('data', ref))
        except _HttpStatus as e:
            if e.status == 404:
                # 未作成、または最新版が論理削除されている。後者は本文に現在の版が入る
                # (版 0 を送ると不一致になる)
                try:
                    body = json.loads(e.body.decode('utf-8')) if e.body else {}
                except (ValueError, UnicodeDecodeError):
                    body = {}
                version = self._version_of(body)
                self._remember(ref, {}, version if version is not None else 0)
                return {}
            if e.status == 403:
                raise self._forbidden(
                    ref, 'を読む',
                    hint=f"  backend.yml の openbao.user (現在: {self._settings.user}) が"
                         "本人の識別子と違う可能性があります") from None
            raise self._unreachable(e.status, ref) from None
        secrets = self._parse_secrets(ref, data)
        version = self._version_of(data)
        if version is None:
            raise self._unreadable(ref, 'data.metadata.version がありません')
        self._remember(ref, secrets, version)
        return secrets

    def _remember(self, ref: SecretRef, data: Dict[str, str], version: int,
                  *, after_write: bool = False) -> None:
        self._seen[ref] = _Basis(dict(data), version)
        if self._cache is not None:
            self._cache.store(ref, data, self, after_write=after_write)

    def _forget(self, ref: SecretRef) -> None:
        """基準を捨て、その参照の控えを消す (現物と違うと分かった・結果が分からない)"""
        self._seen.pop(ref, None)
        if self._cache is not None:
            self._cache.discard(ref)

    def load(self, ref: SecretRef) -> Dict[str, str]:
        """取得する。通信できないときだけキャッシュへ落ちる。"""
        if ref in self._seen:
            return dict(self._seen[ref].secrets)
        try:
            return self.fetch(ref)
        except SecretUnreachableError as e:
            if self._cache is None:
                raise
            cached = self._cache.read(ref, self)
            if cached is None:
                raise SecretUnreachableError(
                    f"{e}\n  キャッシュもありません ({ref.label()})") from None
            logger.warning("OpenBao へ到達できないため、%s のキャッシュを使います "
                           "(最終取得 %s)", ref.label(), cached.fetched_at)
            # 控えには版が無い。この参照はこの SecretStore では書き戻せない
            self._seen[ref] = _Basis(dict(cached.secrets), None)
            return dict(cached.secrets)

    def exists(self, ref: SecretRef) -> bool:
        return bool(self.load(ref))

    def load_bytes(self, ref: SecretRef) -> bytes:
        return EnvFile.dump_bytes(self.load(ref))

    # -- 書き込み -------------------------------------------------------------

    def _basis_for_write(self, ref: SecretRef) -> int:
        """CAS に使う版。読んでいなければ取り直し、控えから読んでいれば拒む"""
        basis = self._seen.get(ref)
        if basis is None:
            self.fetch(ref)
            basis = self._seen[ref]
        if basis.version is None:
            raise SecretUnreachableError(
                f"OpenBao へ到達できないため、{ref.label()}へ書き込めません\n"
                f"  接続先: {self.url}\n"
                "  控えから読んだ内容は書き戻しません (不達の間の他の利用者の更新を"
                "上書きしないため)。サーバへ届く状態でやり直してください")
        return basis.version

    def save(self, ref: SecretRef, data: Dict[str, str]) -> Path:
        """参照の内容全体を ``data`` に置き換える (読んだときの版を指定する)"""
        version = self._basis_for_write(ref)
        body = {'options': {'cas': version}, 'data': dict(data)}
        try:
            response = self._http('POST', self._kv_path('data', ref), body=body)
        except _HttpStatus as e:
            if e.status == 400 and any(_CAS_MISMATCH in msg for msg in e.errors()):
                # 読んだ内容がもう現物と違う。控えを消して、やり直しを促す
                self._forget(ref)
                raise SecretConflictError(
                    f"{ref.label()}は読んでから書くまでの間に他の誰かが書き換えました "
                    f"(版 {version} が現在の版と一致しません)\n"
                    f"  パス: {self.display_path(ref)}\n"
                    "  もう一度読み直してから同じ操作をやり直してください") from None
            if e.status == 403:
                # ログインは通っている。資格の取り消しではなく、このパスへ書く権限が無い
                raise self._forbidden(
                    ref, 'への書き込み',
                    hint="  チーム単位の置き場へ書けるのは、書き込み権限を付けられた"
                         "利用者だけです") from None
            if 400 <= e.status < 500:
                # サーバが拒んだと確定した。サーバは変わっておらず、控えはそのまま正しい
                raise SecretRefusedError(
                    f"OpenBao が書き込みを拒みました ({ref.label()}: HTTP {e.status})\n"
                    f"  接続先: {self.url}\n"
                    f"  パス: {self.display_path(ref)}") from None
            # 5xx はサーバが更新を確定した後で応答だけが失われた可能性がある
            self._forget(ref)
            raise self._unreachable(e.status, ref) from None
        except SecretUnreachableError:
            # 送った後の接続断・タイムアウト・途中切れ・解釈できない応答。結果が分からない
            self._forget(ref)
            raise
        inner = response.get('data') if isinstance(response, dict) else None
        new_version = inner.get('version') if isinstance(inner, dict) else None
        if not isinstance(new_version, int) or isinstance(new_version, bool):
            self._forget(ref)
            raise self._unreadable(ref, '保存の応答に data.version がありません')
        # 応答の版が次の書き込みの基準になる (同じ実行で同じ参照を再び書くとき、
        # 自分の保存で進んだ版と食い違わない)
        self._remember(ref, data, new_version, after_write=True)
        return self.path(ref)

    def save_bytes(self, ref: SecretRef, data: bytes) -> Path:
        try:
            parsed = EnvFile.parse_bytes(data)
        except UnicodeDecodeError as e:
            raise SecretStoreError(f"{ref.label()}の内容を UTF-8 として読めません: {e}") from e
        return self.save(ref, parsed)

    def remove(self, ref: SecretRef) -> bool:
        """参照ごと消す (全版)。CLI のコマンドはこの経路を使わない"""
        if not self.fetch(ref) and self._seen[ref].version == 0:
            return False
        try:
            self._http('DELETE', self._kv_path('metadata', ref))
        except _HttpStatus as e:
            if e.status == 403:
                raise self._forbidden(ref, 'を削除する', show_path=False) from None
            self._forget(ref)
            raise self._unreachable(e.status, ref) from None
        except SecretUnreachableError:
            self._forget(ref)
            raise
        self._remember(ref, {}, 0, after_write=True)
        return True

    # -- 疎通確認 -------------------------------------------------------------

    def probe(self, refs: List[SecretRef]) -> List[Tuple[SecretRef, int]]:
        """認証して各参照を読み、(参照, 件数) を返す (キャッシュへは落ちない)"""
        self.login()
        return [(ref, len(self.fetch(ref))) for ref in refs]


def url_host(url: str) -> str:
    return urlsplit(url).hostname or url
