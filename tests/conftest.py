"""偽の Infisical サーバ (標準ライブラリの http.server で立てる)

PLAN51 の結合テストが使う。実サーバは `carmo-cdk#312` の完了後にしか使えないため、
設計 2「Infisical との契約」の 5 経路だけを平文 HTTP で再現し、受信したリクエストを
記録して検査できるようにする。接続先が ``http://127.0.0.1:<port>`` になるのは、
``backend_config`` がループバック宛てだけ ``http`` を許すため。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import pytest


@dataclass
class Received:
    method: str
    path: str
    query: Dict[str, str]
    body: Dict[str, Any]
    headers: Dict[str, str]

    @property
    def secret_path(self) -> Optional[str]:
        return self.query.get('secretPath') or self.body.get('secretPath')

    @property
    def secret_name(self) -> Optional[str]:
        prefix = '/api/v4/secrets/'
        return self.path[len(prefix):] if self.path.startswith(prefix) else None


@dataclass
class FakeInfisical:
    client_id: str = 'cid'
    client_secret: str = 's3cret'
    project_id: str = 'pid'
    #: (environment, secretPath) → {key: value}
    secrets: Dict[Tuple[str, str], Dict[str, str]] = field(default_factory=dict)
    received: List[Received] = field(default_factory=list)
    #: ログインを拒む (401)
    reject_login: bool = False
    #: 取得に返す HTTP 状態 (None なら正常)
    get_status: Optional[int] = None
    #: 取得に返す本文を差し替える (壊れた JSON など)
    get_body: Optional[bytes] = None
    #: このパスへの読み書きを 403 で拒む (前方一致)
    forbidden_prefixes: List[str] = field(default_factory=list)
    #: 書き込み (POST/PATCH/DELETE) を N 回成功させた後は 500 を返す
    fail_writes_after: Optional[int] = None
    #: 現在有効な access token (None なら未発行)
    token: Optional[str] = None
    logins: int = 0
    writes: int = 0
    _server: Optional[ThreadingHTTPServer] = None
    _thread: Optional[threading.Thread] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- 起動と停止 ---------------------------------------------------------

    def start(self) -> 'FakeInfisical':
        server = self
        handler = type('Handler', (_Handler,), {'server_state': server})
        self._server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
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

    def put(self, path: str, data: Dict[str, str], environment: str = 'common') -> None:
        self.secrets[(environment, path)] = dict(data)

    def get(self, path: str, environment: str = 'common') -> Dict[str, str]:
        return dict(self.secrets.get((environment, path), {}))

    def expire_token(self) -> None:
        """発行済みの access token を失効させる (再ログインすれば新しく出す)"""
        self.token = None

    def requests_of(self, method: str) -> List[Received]:
        return [r for r in self.received if r.method == method]

    def requests_to(self, secret_path: str) -> List[Received]:
        return [r for r in self.received if r.secret_path == secret_path]


class _Handler(BaseHTTPRequestHandler):
    server_state: FakeInfisical

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

    def _send(self, status: int, payload: Any = None, raw: Optional[bytes] = None) -> None:
        body = raw if raw is not None else json.dumps(payload if payload is not None else {}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, method: str) -> Received:
        parts = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        rec = Received(method=method, path=parts.path, query=query,
                       body=self._read_body(),
                       headers={k: v for k, v in self.headers.items()})
        with self.server_state._lock:
            self.server_state.received.append(rec)
        return rec

    def _authorized(self, rec: Received) -> bool:
        state = self.server_state
        header = rec.headers.get('Authorization', '')
        return state.token is not None and header == f'Bearer {state.token}'

    def _forbidden(self, rec: Received) -> bool:
        path = rec.secret_path or ''
        return any(path.startswith(p) for p in self.server_state.forbidden_prefixes)

    # -- 経路 ---------------------------------------------------------------

    def do_POST(self):
        rec = self._record('POST')
        state = self.server_state
        if rec.path == '/api/v1/auth/universal-auth/login':
            state.logins += 1
            if (state.reject_login or rec.body.get('clientId') != state.client_id
                    or rec.body.get('clientSecret') != state.client_secret):
                return self._send(401, {'message': 'unauthorized'})
            state.token = f'token-{state.logins}'
            return self._send(200, {'accessToken': state.token, 'expiresIn': 3600,
                                    'tokenType': 'Bearer'})
        return self._write(rec, 'create')

    def do_PATCH(self):
        return self._write(self._record('PATCH'), 'update')

    def do_DELETE(self):
        return self._write(self._record('DELETE'), 'delete')

    def do_GET(self):
        rec = self._record('GET')
        state = self.server_state
        if rec.path != '/api/v4/secrets':
            return self._send(404, {'message': 'not found'})
        if not self._authorized(rec):
            return self._send(401, {'message': 'unauthorized'})
        if self._forbidden(rec):
            return self._send(403, {'message': 'forbidden'})
        if state.get_body is not None:
            return self._send(state.get_status or 200, raw=state.get_body)
        if state.get_status is not None:
            return self._send(state.get_status, {'message': 'error'})
        data = state.get(rec.query.get('secretPath', ''), rec.query.get('environment', 'common'))
        expand = rec.query.get('expandSecretReferences', 'true') == 'true'
        secrets = []
        for key in sorted(data):
            value = data[key]
            if expand:
                for other, other_value in data.items():
                    value = value.replace('${' + other + '}', other_value)
            secrets.append({'secretKey': key, 'secretValue': value})
        return self._send(200, {'secrets': secrets})

    def _write(self, rec: Received, op: str):
        state = self.server_state
        name = rec.secret_name
        if name is None:
            return self._send(404, {'message': 'not found'})
        if not self._authorized(rec):
            return self._send(401, {'message': 'unauthorized'})
        if self._forbidden(rec):
            return self._send(403, {'message': 'forbidden'})
        if state.fail_writes_after is not None and state.writes >= state.fail_writes_after:
            return self._send(500, {'message': 'boom'})
        key = (rec.body.get('environment', 'common'), rec.body.get('secretPath', ''))
        bucket = state.secrets.setdefault(key, {})
        if op == 'create':
            if name in bucket:
                return self._send(409, {'message': 'exists'})
            bucket[name] = rec.body.get('secretValue', '')
        elif op == 'update':
            if name not in bucket:
                return self._send(404, {'message': 'missing'})
            bucket[name] = rec.body.get('secretValue', '')
        else:
            if name not in bucket:
                return self._send(404, {'message': 'missing'})
            del bucket[name]
        state.writes += 1
        return self._send(200, {'secret': {'secretKey': name}})


@pytest.fixture
def infisical():
    """偽 Infisical サーバ。テスト終了時に落とす。"""
    server = FakeInfisical().start()
    try:
        yield server
    finally:
        server.stop()


def configure_infisical(root, server: FakeInfisical, *, user: str = 'member01',
                        cache_enabled: bool = True, environment: str = 'common',
                        with_bootstrap: bool = True):
    """DEVBASE_ROOT を偽サーバ向けの ``backend: infisical`` に設定する。

    age 鍵は呼び出し側が用意している前提 (ブートストラップとキャッシュの暗号化に使う)。
    """
    from devbase.env import backend_config as bc
    from devbase.env import bootstrap

    config = bc.BackendConfig(
        backend='infisical',
        infisical=bc.InfisicalSettings(url=server.url, project_id=server.project_id,
                                       user=user, environment=environment),
        cache_enabled=cache_enabled,
    )
    bc.save(root, config)
    if with_bootstrap:
        bootstrap.save(root, bootstrap.Credentials(server.client_id, server.client_secret))
    return config


@pytest.fixture
def infisical_root(tmp_path, monkeypatch, infisical):
    """age 鍵を持ち、偽サーバを backend にした DEVBASE_ROOT"""
    from devbase.env import agekeys

    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    agekeys.generate_key_file()
    configure_infisical(tmp_path, infisical)
    return tmp_path
