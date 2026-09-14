"""backend の選択と非機密設定 — ``$DEVBASE_ROOT/secrets/backend.yml``

どの backend を使うかと、その接続先のような **機密ではない** 設定をここに置く
(PLAN51 設計 1)。値そのもの (secret_id など) はブートストラップ
(:mod:`devbase.env.bootstrap`) が age で暗号化して持ち、このファイルには入らない。

ファイルが無ければ ``auto`` = 現行のファイル存在による自動判定であり、設定を
持たない端末の挙動は変わらない (前提 3)。

**対応していない値は既定へ落とさず、設定エラーとして拒む。** ``path_team_global: /team/global``
を黙って ``team/global`` へ読み替えると、書いたとおりに動いていないことに気づく手段が無くなる。
欠けている項目と同じく、受け付けない値もキー名と受け付ける値を述べて止める。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import yaml

from devbase.env import io_common as _io_common
from devbase.errors import DevbaseError

CONFIG_FILENAME = 'backend.yml'

#: 登録簿にある backend 名。``auto`` は「ファイルの存在で判定する」現行の規則を指す。
BACKEND_NAMES = ('auto', 'plaintext', 'age', 'openbao')

BACKEND_AUTO = 'auto'
BACKEND_OPENBAO = 'openbao'

#: ``http`` を受け付けるホスト。平文が流れるのを同じ端末の中に閉じる。
_LOOPBACK_HOSTS = ('localhost', '127.0.0.1', '::1')


class BackendConfigError(DevbaseError):
    """backend 設定の読み書きエラー"""


def config_path(devbase_root: Path) -> Path:
    from devbase.env.secret_store import SECRETS_DIRNAME

    return Path(devbase_root) / SECRETS_DIRNAME / CONFIG_FILENAME


def _validate_segment(value: str, key: str) -> str:
    """パスの 1 要素として使う値がパスを跨がないことを確かめる。

    プロジェクト名と同じ検査 (``secret_store._validate_project_name``) をかける。
    ``..`` や区切り文字を許すと、組み立てたパスが設定した親の外へ出る。
    """
    if not value:
        raise BackendConfigError(f"openbao.{key} が設定されていません")
    if (value in ('.', '..') or '/' in value or '\\' in value
            or value != Path(value).name):
        raise BackendConfigError(
            f"openbao.{key} にパス区切りや '..' は使えません: {value!r}")
    return value


def _validate_relative_path(value: str, key: str) -> str:
    """置き場の相対パス。先頭・末尾の ``/`` と ``..`` を許さない。

    KV v2 の経路は ``/v1/<mount>/data/<path>`` で、``<path>`` に先頭の ``/`` を付けると
    経路が二重の ``/`` になる。既定へ読み替えず、設定エラーとして止める。
    """
    if not value:
        raise BackendConfigError(f"openbao.{key} が設定されていません")
    if value.startswith('/') or value.endswith('/'):
        raise BackendConfigError(
            f"openbao.{key} は '/' で始めず・終えずに指定してください: {value!r}")
    if any(part in ('', '.', '..') or '\\' in part for part in value.split('/')):
        raise BackendConfigError(
            f"openbao.{key} に空の要素や '..' は使えません: {value!r}")
    return value


def _validate_url(url: str) -> str:
    parts = urlsplit(url)
    scheme_ok = ((parts.scheme == 'https' and parts.hostname)
                 or (parts.scheme == 'http' and parts.hostname in _LOOPBACK_HOSTS))
    if not scheme_ok:
        raise BackendConfigError(
            f"openbao.url は https でなければなりません: {url!r}\n"
            "  http を使えるのは localhost / 127.0.0.1 / ::1 宛てだけです")
    # 経路は devbase が /v1/<mount>/... を足して組む。URL にパスが付いていると経路が
    # 二重になり、設定ミスが「機密 0 件」に見える 404 として現れる
    if parts.path.strip('/') or parts.query or parts.fragment:
        raise BackendConfigError(
            f"openbao.url はホスト (とポート) までで指定してください: {url!r}\n"
            "  パス・クエリ・フラグメントは付けられません (経路は devbase が組みます)")
    return url


@dataclass(frozen=True)
class OpenBaoSettings:
    url: str
    user: str
    mount: str = 'devbase'
    path_team_global: str = 'team/global'
    path_team_project_prefix: str = 'team/projects'
    path_user_prefix: str = 'users'
    timeout_seconds: int = 5

    def validate(self) -> 'OpenBaoSettings':
        missing = [key for key in ('url', 'user') if not getattr(self, key)]
        if missing:
            raise BackendConfigError(
                "backend: openbao に必要な設定が欠けています: "
                + ', '.join(f'openbao.{key}' for key in missing))
        _validate_url(self.url)
        _validate_segment(self.user, 'user')
        _validate_segment(self.mount, 'mount')
        if not isinstance(self.timeout_seconds, int) or self.timeout_seconds <= 0:
            raise BackendConfigError(
                f"openbao.timeout_seconds は正の整数で指定してください: "
                f"{self.timeout_seconds!r}")
        for key in ('path_team_global', 'path_team_project_prefix', 'path_user_prefix'):
            _validate_relative_path(getattr(self, key), key)
        return self

    def path_of(self, ref) -> str:
        """参照に対応する KV v2 のパス (仕様の対応表。``<mount>`` は含まない)。

        ``<name>`` と ``<user>`` はいずれもパスを跨がない検査を通っているため、
        組み立てた結果が設定した親の外へ出ることはない。
        """
        if ref.owner == 'user':
            base = f"{self.path_user_prefix}/{self.user}"
            if ref.kind == 'global':
                return f'{base}/global'
            return f'{base}/projects/{ref.name}'
        if ref.kind == 'global':
            return self.path_team_global
        return f"{self.path_team_project_prefix}/{ref.name}"

    def display_path(self, ref) -> str:
        """表示用の ``<mount>/<パス>``"""
        return f"{self.mount}/{self.path_of(ref)}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'url': self.url,
            'mount': self.mount,
            'user': self.user,
            'path_team_global': self.path_team_global,
            'path_team_project_prefix': self.path_team_project_prefix,
            'path_user_prefix': self.path_user_prefix,
            'timeout_seconds': self.timeout_seconds,
        }


@dataclass(frozen=True)
class BackendConfig:
    backend: str = BACKEND_AUTO
    openbao: Optional[OpenBaoSettings] = None
    cache_enabled: bool = True
    version: int = 1
    #: 読み込み元。ファイルが無ければ ``None`` (``auto`` の既定)
    source: Optional[Path] = field(default=None, compare=False)

    def validate(self) -> 'BackendConfig':
        if self.backend not in BACKEND_NAMES:
            raise BackendConfigError(
                f"backend の値 {self.backend!r} は登録されていません "
                f"(利用できる backend: {', '.join(BACKEND_NAMES)})")
        if self.backend == BACKEND_OPENBAO:
            if self.openbao is None:
                raise BackendConfigError(
                    "backend: openbao に必要な設定が欠けています: "
                    "openbao.url, openbao.user")
            self.openbao.validate()
        return self

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {'version': self.version, 'backend': self.backend}
        if self.openbao is not None:
            data['openbao'] = self.openbao.to_dict()
        data['cache'] = {'enabled': self.cache_enabled}
        return data


def _str(value: Any, default: str = '') -> str:
    """空・不在は「未設定」として既定へ落とす (設計 1 のデータ構造)"""
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _openbao_from_dict(raw: Any, backend: str) -> Optional[OpenBaoSettings]:
    """``openbao:`` 節を解釈する。節が無く backend も openbao でなければ ``None``。

    backend が openbao なのに節が無いときは空の設定を返し、欠けている項目は
    :meth:`OpenBaoSettings.validate` がキー名を挙げて拒む。
    """
    if not (isinstance(raw, dict) or backend == BACKEND_OPENBAO):
        return None
    raw = raw if isinstance(raw, dict) else {}
    defaults = OpenBaoSettings(url='', user='')
    timeout = raw.get('timeout_seconds')
    return OpenBaoSettings(
        url=_str(raw.get('url')),
        user=_str(raw.get('user')),
        mount=_str(raw.get('mount'), defaults.mount),
        path_team_global=_str(raw.get('path_team_global'), defaults.path_team_global),
        path_team_project_prefix=_str(raw.get('path_team_project_prefix'),
                                      defaults.path_team_project_prefix),
        path_user_prefix=_str(raw.get('path_user_prefix'), defaults.path_user_prefix),
        timeout_seconds=(defaults.timeout_seconds if timeout in (None, '')
                         else timeout),
    )


def _from_dict(data: Dict[str, Any], source: Optional[Path]) -> BackendConfig:
    if not isinstance(data, dict):
        raise BackendConfigError(f"{source} の内容がマッピングではありません")

    backend = _str(data.get('backend'), BACKEND_AUTO)
    openbao = _openbao_from_dict(data.get('openbao'), backend)

    raw_cache = data.get('cache')
    cache_enabled = True
    if isinstance(raw_cache, dict) and raw_cache.get('enabled') is not None:
        cache_enabled = bool(raw_cache.get('enabled'))

    raw_version = data.get('version')
    if raw_version in (None, ''):
        version = 1
    else:
        # 他の不正値と同じく、キー名と受け付ける値を添えて拒む (生の ValueError を漏らさない)
        try:
            version = int(str(raw_version).strip())
        except ValueError:
            raise BackendConfigError(
                f"version の値 {raw_version!r} には対応していません (受け付ける値: 1)") from None
        if version != 1:
            raise BackendConfigError(
                f"version の値 {raw_version!r} には対応していません (受け付ける値: 1)")

    return BackendConfig(backend=backend, openbao=openbao,
                         cache_enabled=cache_enabled, version=version,
                         source=source)


def load(devbase_root: Path) -> BackendConfig:
    """設定を読んで検証する。ファイルが無ければ ``auto`` の既定を返す。"""
    path = config_path(devbase_root)
    if not path.is_file():
        return BackendConfig()
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as e:
        raise BackendConfigError(f"{path} を読めませんでした: {e}") from e
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise BackendConfigError(f"{path} を YAML として読めませんでした: {e}") from e
    return _from_dict(data, path).validate()


def save(devbase_root: Path, config: BackendConfig) -> Path:
    """検証してから書く。検証に失敗したときはファイルを触らない。"""
    config.validate()
    path = config_path(devbase_root)
    text = yaml.safe_dump(config.to_dict(), allow_unicode=True, sort_keys=False)
    try:
        _io_common.write_secure_bytes_atomic(path, text.encode('utf-8'))
    except OSError as e:
        raise BackendConfigError(f"{path} へ書き込めませんでした: {e}") from e
    return path


def available_names() -> List[str]:
    return list(BACKEND_NAMES)
