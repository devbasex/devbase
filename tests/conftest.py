"""偽の OpenBao サーバ (標準ライブラリの http.server で立てる)

PLAN51 の結合テストが使う。実サーバは `carmo-cdk#312` の完了後にしか使えないため、
仕様「OpenBao との契約」の 4 経路 (AppRole のログイン、KV v2 の取得・版付き保存・
メタデータ削除) だけを平文 HTTP で再現し、受信したリクエストを記録して検査できるように
する。接続先が ``http://127.0.0.1:<port>`` になるのは、``backend_config`` がループバック
宛てだけ ``http`` を許すため。

ポリシーの代わりに ``forbidden_prefixes`` (読み書きとも 403) と ``team_writable``
(偽なら ``team/`` への保存が 403) で権限の不足を再現する。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Set
from urllib.parse import unquote, urlsplit

import pytest


@dataclass
class Received:
    method: str
    path: str
    body: Dict[str, Any]
    headers: Dict[str, str]

    def _kv(self, kind: str) -> Optional[str]:
        parts = self.path.split('/', 4)   # ['', 'v1', <mount>, <kind>, <path>]
        if len(parts) == 5 and parts[1] == 'v1' and parts[3] == kind:
            return unquote(parts[4])
        return None

    @property
    def kv_path(self) -> Optional[str]:
        """``/v1/<mount>/data/<path>`` の ``<path>`` (復号済み)"""
        return self._kv('data')

    @property
    def metadata_path(self) -> Optional[str]:
        return self._kv('metadata')

    @property
    def cas(self) -> Optional[int]:
        options = self.body.get('options')
        return options.get('cas') if isinstance(options, dict) else None


@dataclass
class FakeOpenBao:
    role_id: str = 'rid'
    secret_id: str = 's3cret'
    mount: str = 'devbase'
    #: path → {key: value} (現在の版の内容)
    secrets: Dict[str, Dict[str, str]] = field(default_factory=dict)
    #: path → 現在の版 (1 度も書かれていなければ無い)
    versions: Dict[str, int] = field(default_factory=dict)
    #: 最新版が論理削除されたパス (取得は 404 だが本文に版が入る)
    soft_deleted: Set[str] = field(default_factory=set)
    received: List[Received] = field(default_factory=list)
    #: ログインを 400 (invalid role or secret) で拒む
    reject_login: bool = False
    #: ログインを 403 で拒む (利用者の無効化)
    disable_entity: bool = False
    #: 取得に返す HTTP 状態 (None なら正常)
    get_status: Optional[int] = None
    #: 取得に返す本文を差し替える (壊れた JSON など)
    get_body: Optional[bytes] = None
    #: 何回目の取得の試みを 503 にするか (1 始まり)。それ以外は通す
    fail_get_attempts: List[int] = field(default_factory=list)
    get_attempts: int = 0
    #: 取得の本文を Content-Length より短く切って返す (IncompleteRead を起こす)
    truncate_get_body: bool = False
    #: このパスへの読み書きを 403 で拒む (前方一致)
    forbidden_prefixes: List[str] = field(default_factory=list)
    #: 偽なら ``team/`` 配下への保存を 403 で拒む (書き込みのポリシーが無い利用者)
    team_writable: bool = True
    #: 何回目の書き込みの試みを 500 にするか (1 始まり)。それ以外は通す
    fail_write_attempts: List[int] = field(default_factory=list)
    write_attempts: int = 0
    #: 書き込みを反映した後、応答を返さずに接続を切る (結果不明の失敗)
    drop_write_response: bool = False
    #: 書き込みの応答を JSON でない本文にする (解釈できない応答)
    garble_write_response: bool = False
    #: 書き込みの 500 の本文を Content-Length より短く切る (失敗応答の途中切れ)
    truncate_write_error_body: bool = False
    #: 書き込みに返す HTTP 状態 (None なら正常)。4xx の拒否を再現する
    write_status: Optional[int] = None
    #: 書き込みが 1 度でも起きた後の取得で値を差し替える (読み戻しの検証を失敗させる):
    #: path → {key: value}
    readback_tamper: Dict[str, Dict[str, str]] = field(default_factory=dict)
    #: 現在有効な token (None なら未発行)
    token: Optional[str] = None
    logins: int = 0
    writes: int = 0
    _server: Optional[ThreadingHTTPServer] = None
    _thread: Optional[threading.Thread] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- 起動と停止 ---------------------------------------------------------

    def start(self) -> 'FakeOpenBao':
        server = self
        handler = type('Handler', (_Handler,), {'server_state': server})
        self._server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.02}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """サーバを落とす (以後の接続は拒否される = 不達)"""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.port}'

    # -- 状態の操作 ---------------------------------------------------------

    def put(self, path: str, data: Dict[str, str]) -> int:
        """サーバ側で書く (版が 1 つ進む)。他の利用者の更新を再現するのに使う。"""
        with self._lock:
            self.versions[path] = self.versions.get(path, 0) + 1
            self.secrets[path] = dict(data)
            self.soft_deleted.discard(path)
            return self.versions[path]

    def get(self, path: str) -> Dict[str, str]:
        return dict(self.secrets.get(path, {}))

    def version_of(self, path: str) -> int:
        return self.versions.get(path, 0)

    def soft_delete(self, path: str) -> None:
        """最新版を論理削除する (WebUI の delete に相当)"""
        self.soft_deleted.add(path)

    def expire_token(self) -> None:
        """発行済みの token を失効させる (再ログインすれば新しく出す)"""
        self.token = None

    def requests_of(self, method: str) -> List[Received]:
        return [r for r in self.received if r.method == method]

    def requests_to(self, path: str) -> List[Received]:
        return [r for r in self.received if r.kv_path == path]


class _Handler(BaseHTTPRequestHandler):
    server_state: FakeOpenBao

    def log_message(self, *args):  # 標準エラーへ出さない
        pass

    # -- 共通 ---------------------------------------------------------------

    def _read_body(self) -> Dict[str, Any]:
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b''
        if not raw:
            return {}
        try:
            return json.loads(raw.decode('utf-8'))
        except ValueError:
            return {'_raw': raw.decode('utf-8', 'replace')}

    def _send(self, status: int, payload: Any = None, raw: Optional[bytes] = None,
              truncate: bool = False) -> None:
        body = raw if raw is not None else json.dumps(payload if payload is not None else {}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body) + (100 if truncate else 0)))
        self.end_headers()
        self.wfile.write(body[:1] if truncate else body)
        if truncate:
            self.wfile.flush()
            self.close_connection = True

    def _drop(self) -> None:
        """応答を返さずに接続を切る"""
        self.close_connection = True
        try:
            self.connection.shutdown(1)
        except OSError:
            pass

    def _record(self, method: str) -> Received:
        parts = urlsplit(self.path)
        rec = Received(method=method, path=parts.path, body=self._read_body(),
                       headers={k: v for k, v in self.headers.items()})
        with self.server_state._lock:
            self.server_state.received.append(rec)
        return rec

    def _authorized(self, rec: Received) -> bool:
        state = self.server_state
        return state.token is not None and rec.headers.get('X-Vault-Token') == state.token

    def _forbidden(self, path: str) -> bool:
        return any(path.startswith(p) for p in self.server_state.forbidden_prefixes)

    def _mount_ok(self, rec: Received) -> bool:
        parts = rec.path.split('/')
        return len(parts) > 2 and parts[1] == 'v1' and parts[2] == self.server_state.mount

    # -- 経路 ---------------------------------------------------------------

    def do_POST(self):
        rec = self._record('POST')
        state = self.server_state
        if rec.path == '/v1/auth/approle/login':
            state.logins += 1
            if state.disable_entity:
                return self._send(403, {'errors': ['permission denied']})
            if (state.reject_login or rec.body.get('role_id') != state.role_id
                    or rec.body.get('secret_id') != state.secret_id):
                return self._send(400, {'errors': ['invalid role or secret']})
            state.token = f'token-{state.logins}'
            return self._send(200, {'auth': {'client_token': state.token,
                                             'lease_duration': 3600,
                                             'renewable': True}})
        path = rec.kv_path
        if path is None or not self._mount_ok(rec):
            return self._send(404, {'errors': []})
        if not self._authorized(rec):
            return self._send(403, {'errors': ['permission denied']})
        if self._forbidden(path) or (not state.team_writable and path.startswith('team/')):
            return self._send(403, {'errors': ['1 error occurred:\n\t* permission denied\n\n']})
        state.write_attempts += 1
        if state.write_attempts in state.fail_write_attempts:
            return self._send(500, {'errors': ['boom']}, truncate=state.truncate_write_error_body)
        if state.write_status is not None:
            return self._send(state.write_status, {'errors': ['refused']})
        data = rec.body.get('data')
        if not isinstance(data, dict):
            return self._send(400, {'errors': ['data must be a map']})
        with state._lock:
            current = state.versions.get(path, 0)
            cas = rec.cas
            if cas is None or cas != current:
                return self._send(400, {'errors': [
                    'check-and-set parameter did not match the current version']})
            state.versions[path] = current + 1
            state.secrets[path] = {str(k): v for k, v in data.items()}
            state.soft_deleted.discard(path)
            state.writes += 1
            new_version = state.versions[path]
        if state.drop_write_response:
            return self._drop()
        if state.garble_write_response:
            return self._send(200, raw=b'<html>gateway</html>')
        return self._send(200, {'data': {'version': new_version, 'destroyed': False,
                                         'created_time': '2026-09-14T00:00:00Z'}})

    def do_DELETE(self):
        rec = self._record('DELETE')
        state = self.server_state
        path = rec.metadata_path
        if path is None or not self._mount_ok(rec):
            return self._send(404, {'errors': []})
        if not self._authorized(rec):
            return self._send(403, {'errors': ['permission denied']})
        if self._forbidden(path) or (not state.team_writable and path.startswith('team/')):
            return self._send(403, {'errors': ['permission denied']})
        with state._lock:
            state.versions.pop(path, None)
            state.secrets.pop(path, None)
            state.soft_deleted.discard(path)
        self.send_response(204)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        rec = self._record('GET')
        state = self.server_state
        path = rec.kv_path
        if path is None or not self._mount_ok(rec):
            return self._send(404, {'errors': []})
        if not self._authorized(rec):
            return self._send(403, {'errors': ['permission denied']})
        if self._forbidden(path):
            return self._send(403, {'errors': ['permission denied']})
        state.get_attempts += 1
        if state.get_attempts in state.fail_get_attempts:
            return self._send(503, {'errors': ['unavailable']})
        if state.get_body is not None:
            return self._send(state.get_status or 200, raw=state.get_body)
        if state.get_status is not None:
            return self._send(state.get_status, {'errors': ['error']})
        if path not in state.versions:
            return self._send(404, {'errors': []})
        version = state.versions[path]
        if path in state.soft_deleted:
            return self._send(404, {'errors': [], 'data': {
                'data': None,
                'metadata': {'version': version, 'deletion_time': '2026-09-14T00:00:00Z',
                             'destroyed': False}}})
        data = state.get(path)
        if state.writes > 0:
            for key, value in state.readback_tamper.get(path, {}).items():
                if key in data:
                    data[key] = value
        return self._send(200, {'data': {'data': data,
                                         'metadata': {'version': version, 'deletion_time': '',
                                                      'destroyed': False}}},
                          truncate=state.truncate_get_body)


@pytest.fixture
def openbao():
    """偽 OpenBao サーバ。テスト終了時に落とす。"""
    server = FakeOpenBao().start()
    try:
        yield server
    finally:
        server.stop()


def configure_openbao(root, server: FakeOpenBao, *, user: str = 'member01',
                      cache_enabled: bool = True, with_bootstrap: bool = True,
                      mount: Optional[str] = None):
    """DEVBASE_ROOT を偽サーバ向けの ``backend: openbao`` に設定する。

    age 鍵は呼び出し側が用意している前提 (ブートストラップとキャッシュの暗号化に使う)。
    """
    from devbase.env import backend_config as bc
    from devbase.env import bootstrap

    config = bc.BackendConfig(
        backend='openbao',
        openbao=bc.OpenBaoSettings(url=server.url, user=user, mount=mount or server.mount),
        cache_enabled=cache_enabled,
    )
    bc.save(root, config)
    if with_bootstrap:
        bootstrap.save(root, bootstrap.Credentials(server.role_id, server.secret_id))
    return config


@pytest.fixture
def openbao_root(tmp_path, monkeypatch, openbao):
    """age 鍵を持ち、偽サーバを backend にした DEVBASE_ROOT"""
    from devbase.env import agekeys

    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    agekeys.generate_key_file()
    configure_openbao(tmp_path, openbao)
    return tmp_path
