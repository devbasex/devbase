"""backend の選択と非機密設定 — ``$DEVBASE_ROOT/secrets/backend.yml``

どの backend を使うかと、その接続先のような **機密ではない** 設定をここに置く
(PLAN51 設計 1)。値そのもの (client secret など) はブートストラップ
(:mod:`devbase.env.bootstrap`) が age で暗号化して持ち、このファイルには入らない。

ファイルが無ければ ``auto`` = 現行のファイル存在による自動判定であり、設定を
持たない端末の挙動は変わらない (前提 3)。

**対応していない値は既定へ落とさず、設定エラーとして拒む。** ``api_version: v3`` を
黙って ``v4`` へ読み替えると、書いたとおりに動いていないことに気づく手段が無くなる。
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
BACKEND_NAMES = ('auto', 'plaintext', 'age', 'infisical')

BACKEND_AUTO = 'auto'
BACKEND_INFISICAL = 'infisical'

#: 受け付ける Infisical API の版。取得・作成・更新・削除の 4 経路をすべて定義した
#: 版だけを載せる (設計 3「未確認のまま残ること」)。
SUPPORTED_API_VERSIONS = ('v4',)

#: ``http`` を受け付けるホスト。平文が流れるのを同じ端末の中に閉じる。
_LOOPBACK_HOSTS = ('localhost', '127.0.0.1', '::1')


class BackendConfigError(DevbaseError):
    """backend 設定の読み書きエラー"""


def config_path(devbase_root: Path) -> Path:
    from devbase.env.secret_store import SECRETS_DIRNAME

    return Path(devbase_root) / SECRETS_DIRNAME / CONFIG_FILENAME


def _validate_segment(value: str, key: str) -> str:
    """``secretPath`` の 1 要素として使う値がパスを跨がないことを確かめる。

    プロジェクト名と同じ検査 (``secret_store._validate_project_name``) をかける。
    ``..`` や区切り文字を許すと、組み立てた ``secretPath`` が設定した親の外へ出る。
    """
    if not value:
        raise BackendConfigError(f"infisical.{key} が設定されていません")
    if (value in ('.', '..') or '/' in value or '\\' in value
            or value != Path(value).name):
        raise BackendConfigError(
            f"infisical.{key} にパス区切りや '..' は使えません: {value!r}")
    return value


def _validate_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme == 'https' and parts.hostname:
        return url
    if parts.scheme == 'http' and parts.hostname in _LOOPBACK_HOSTS:
        return url
    raise BackendConfigError(
        f"infisical.url は https でなければなりません: {url!r}\n"
        "  http を使えるのは localhost / 127.0.0.1 / ::1 宛てだけです")


@dataclass(frozen=True)
class InfisicalSettings:
    url: str
    project_id: str
    user: str
    environment: str = 'common'
    path_team_global: str = '/team/global'
    path_team_project_prefix: str = '/team/projects'
    path_user_prefix: str = '/users'
    api_version: str = 'v4'
    timeout_seconds: int = 5

    def validate(self) -> 'InfisicalSettings':
        missing = [key for key in ('url', 'project_id', 'user')
                   if not getattr(self, key)]
        if missing:
            raise BackendConfigError(
                "backend: infisical に必要な設定が欠けています: "
                + ', '.join(f'infisical.{key}' for key in missing))
        _validate_url(self.url)
        _validate_segment(self.user, 'user')
        if self.api_version not in SUPPORTED_API_VERSIONS:
            raise BackendConfigError(
                f"infisical.api_version の値 {self.api_version!r} には対応していません "
                f"(受け付ける値: {', '.join(SUPPORTED_API_VERSIONS)})")
        if not isinstance(self.timeout_seconds, int) or self.timeout_seconds <= 0:
            raise BackendConfigError(
                f"infisical.timeout_seconds は正の整数で指定してください: "
                f"{self.timeout_seconds!r}")
        for key in ('path_team_global', 'path_team_project_prefix', 'path_user_prefix'):
            value = getattr(self, key)
            if not value.startswith('/'):
                raise BackendConfigError(
                    f"infisical.{key} は '/' で始まるパスで指定してください: {value!r}")
        return self

    def secret_path(self, ref) -> str:
        """参照に対応する Infisical の ``secretPath`` (設計 1 の対応表)。

        ``<name>`` と ``<user>`` はいずれもパスを跨がない検査を通っているため、
        組み立てた結果が設定した親の外へ出ることはない。
        """
        if ref.owner == 'user':
            base = f"{self.path_user_prefix.rstrip('/')}/{self.user}"
            if ref.kind == 'global':
                return f'{base}/global'
            return f'{base}/projects/{ref.name}'
        if ref.kind == 'global':
            return self.path_team_global
        return f"{self.path_team_project_prefix.rstrip('/')}/{ref.name}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'url': self.url,
            'project_id': self.project_id,
            'environment': self.environment,
            'user': self.user,
            'path_team_global': self.path_team_global,
            'path_team_project_prefix': self.path_team_project_prefix,
            'path_user_prefix': self.path_user_prefix,
            'api_version': self.api_version,
            'timeout_seconds': self.timeout_seconds,
        }


@dataclass(frozen=True)
class BackendConfig:
    backend: str = BACKEND_AUTO
    infisical: Optional[InfisicalSettings] = None
    cache_enabled: bool = True
    version: int = 1
    #: 読み込み元。ファイルが無ければ ``None`` (``auto`` の既定)
    source: Optional[Path] = field(default=None, compare=False)

    def validate(self) -> 'BackendConfig':
        if self.backend not in BACKEND_NAMES:
            raise BackendConfigError(
                f"backend の値 {self.backend!r} は登録されていません "
                f"(利用できる backend: {', '.join(BACKEND_NAMES)})")
        if self.backend == BACKEND_INFISICAL:
            if self.infisical is None:
                raise BackendConfigError(
                    "backend: infisical に必要な設定が欠けています: "
                    "infisical.url, infisical.project_id, infisical.user")
            self.infisical.validate()
        return self

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {'version': self.version, 'backend': self.backend}
        if self.infisical is not None:
            data['infisical'] = self.infisical.to_dict()
        data['cache'] = {'enabled': self.cache_enabled}
        return data


def _str(value: Any, default: str = '') -> str:
    """空・不在は「未設定」として既定へ落とす (設計 1 のデータ構造)"""
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _from_dict(data: Dict[str, Any], source: Optional[Path]) -> BackendConfig:
    if not isinstance(data, dict):
        raise BackendConfigError(f"{source} の内容がマッピングではありません")

    backend = _str(data.get('backend'), BACKEND_AUTO)
    raw_inf = data.get('infisical')
    infisical: Optional[InfisicalSettings] = None
    if isinstance(raw_inf, dict) or backend == BACKEND_INFISICAL:
        raw_inf = raw_inf if isinstance(raw_inf, dict) else {}
        defaults = InfisicalSettings(url='', project_id='', user='')
        timeout = raw_inf.get('timeout_seconds')
        infisical = InfisicalSettings(
            url=_str(raw_inf.get('url')),
            project_id=_str(raw_inf.get('project_id')),
            user=_str(raw_inf.get('user')),
            environment=_str(raw_inf.get('environment'), defaults.environment),
            path_team_global=_str(raw_inf.get('path_team_global'),
                                  defaults.path_team_global),
            path_team_project_prefix=_str(raw_inf.get('path_team_project_prefix'),
                                          defaults.path_team_project_prefix),
            path_user_prefix=_str(raw_inf.get('path_user_prefix'),
                                  defaults.path_user_prefix),
            api_version=_str(raw_inf.get('api_version'), defaults.api_version),
            timeout_seconds=(defaults.timeout_seconds if timeout in (None, '')
                             else timeout),
        )

    raw_cache = data.get('cache')
    cache_enabled = True
    if isinstance(raw_cache, dict) and raw_cache.get('enabled') is not None:
        cache_enabled = bool(raw_cache.get('enabled'))

    raw_version = data.get('version')
    version = 1 if raw_version in (None, '') else int(raw_version)

    return BackendConfig(backend=backend, infisical=infisical,
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
