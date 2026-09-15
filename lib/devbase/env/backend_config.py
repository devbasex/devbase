"""backend の選択と非機密設定 — ``$DEVBASE_ROOT/secrets/backend.yml``

どの backend を使うかと、その接続先のような **機密ではない** 設定をここに置く
(PLAN51 設計 1)。値そのもの (secret_id など) はブートストラップ
(:mod:`devbase.env.bootstrap`) が age で暗号化して持ち、このファイルには入らない。

ファイルが無ければ ``auto`` = 現行のファイル存在による自動判定であり、設定を
持たない端末の挙動は変わらない (前提 3)。

**対応していない値は既定へ落とさず、設定エラーとして拒む。** ``path_team_global: /team/global``
を黙って ``team/global`` へ読み替えると、書いたとおりに動いていないことに気づく手段が無くなる。
欠けている項目と同じく、受け付けない値もキー名と受け付ける値を述べて止める。

**版とレイアウトは 1 対 1 である** (PLAN56 決定 1)。``version: 1`` はパスにグループを含まない
``flat`` (``team/global`` など)、``version: 2`` はアカウントグループごとに分かれた ``group``
(``team/<g>/global`` など) で、``version: 2`` では ``openbao.layout: group`` を必須にする。
古い devbase は ``version`` が 1 以外の設定を拒むため、グループ別の置き場を選んだ設定を
配布の行き渡っていない端末が読んでも、従来のパスを黙って読まずに止まる。

``openbao.group_aliases`` はグループ名を置き場の上だけ別の名前へ読み替える (決定 4)。
``DEVBASE_ACCOUNT_GROUP`` の既定値とボリューム名 ``devbase_home_<group>`` は変えず、
``default`` を ``nyle`` の置き場へ向けるような対応を端末の設定にだけ書く。
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

#: パスの並び。``flat`` は ``version: 1``、``group`` は ``version: 2`` (決定 1)
LAYOUT_FLAT = 'flat'
LAYOUT_GROUP = 'group'

#: 受け付ける ``version`` の値と、それぞれのレイアウト
SUPPORTED_VERSIONS = {1: LAYOUT_FLAT, 2: LAYOUT_GROUP}

#: ``version: 2`` にだけ置けるキー / ``version: 1`` にだけ置けるキー
_GROUP_ONLY_KEYS = ('layout', 'path_team_prefix', 'group_aliases')
_FLAT_ONLY_KEYS = ('path_team_global', 'path_team_project_prefix')

#: 置き場のグループ名に使えない名前。``version: 2`` のパス ``team/<g>/…`` が
#: ``version: 1`` の ``team/global`` / ``team/projects/<name>`` と重なる
RESERVED_STORAGE_GROUPS = ('global', 'projects')

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


def _validate_group_name(value: Any, what: str) -> str:
    """グループ名を ``DEVBASE_ACCOUNT_GROUP`` と同じ規則で検証する。

    規則はボリューム名の検証 (:func:`devbase.volume.manager.resolve_account_group`) を
    そのまま使い、ここへ写さない。空は ``default`` へ読み替えずに拒む
    (``resolve_account_group`` は空を既定へ解決するが、置き場の名前では誤りである)。
    """
    from devbase.volume.manager import resolve_account_group

    if not isinstance(value, str) or not value.strip():
        raise BackendConfigError(f"{what} が空です: {value!r}")
    try:
        return resolve_account_group(value)
    except DevbaseError as e:
        raise BackendConfigError(f"{what}: {e}") from None


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
    #: パスの並び (``flat`` / ``group``)。``BackendConfig.version`` と 1 対 1
    layout: str = LAYOUT_FLAT
    #: ``layout: group`` のチーム単位の親 (``<prefix>/<g>/global`` など)
    path_team_prefix: str = 'team'
    #: グループ名 → 置き場のグループ名。``layout: group`` でだけ使う
    group_aliases: Dict[str, str] = field(default_factory=dict, hash=False)

    @property
    def grouped(self) -> bool:
        return self.layout == LAYOUT_GROUP

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
        if self.layout not in (LAYOUT_FLAT, LAYOUT_GROUP):
            raise BackendConfigError(
                f"openbao.layout の値 {self.layout!r} には対応していません "
                f"(受け付ける値: {LAYOUT_GROUP})")
        if self.grouped:
            keys = ('path_team_prefix', 'path_user_prefix')
        else:
            keys = ('path_team_global', 'path_team_project_prefix', 'path_user_prefix')
            if self.group_aliases:
                raise BackendConfigError(
                    "openbao.group_aliases は version: 2 (openbao.layout: group) でだけ"
                    "置けます (現在: version 1)")
        for key in keys:
            _validate_relative_path(getattr(self, key), key)
        for source, target in self.group_aliases.items():
            _validate_group_name(source, f"openbao.group_aliases のキー {source!r}")
            _validate_group_name(target, f"openbao.group_aliases.{source} の値")
            self._check_reserved(target, f"openbao.group_aliases.{source} の値")
        return self

    @staticmethod
    def _check_reserved(name: str, what: str) -> None:
        if name in RESERVED_STORAGE_GROUPS:
            raise BackendConfigError(
                f"{what} {name!r} は置き場のグループ名に使えません "
                f"({' / '.join(RESERVED_STORAGE_GROUPS)} は version: 1 のパスと重なります)")

    def storage_group(self, group: Optional[str]) -> str:
        """グループ名を置き場のグループ名へ写す (``group_aliases`` の読み替えと予約語の検査)。

        名前の検証は ``DEVBASE_ACCOUNT_GROUP`` と同じ規則で行い、読み替えた**後**の名前が
        ``global`` / ``projects`` なら拒む。
        """
        name = _validate_group_name(group, 'グループ名')
        mapped = self.group_aliases.get(name, name)
        if mapped != name:
            self._check_reserved(mapped, f"グループ {name} の読み替え先")
        else:
            self._check_reserved(mapped, 'グループ名')
        return mapped

    def _group_of(self, ref) -> str:
        if not ref.group:
            raise BackendConfigError(
                f"layout: group の置き場ではグループの無い参照を組み立てられません "
                f"({ref.label()})")
        return self.storage_group(ref.group)

    def path_of(self, ref) -> str:
        """参照に対応する KV v2 のパス (仕様の対応表。``<mount>`` は含まない)。

        ``<name>`` と ``<user>`` はいずれもパスを跨がない検査を通っているため、
        組み立てた結果が設定した親の外へ出ることはない。``layout: group`` の ``<g>`` は
        :meth:`storage_group` の検証を通った名前 (区切り文字を含まない) である。
        """
        if self.grouped:
            group = self._group_of(ref)
            if ref.owner == 'user':
                base = f"{self.path_user_prefix}/{self.user}/{group}"
            else:
                base = f"{self.path_team_prefix}/{group}"
            if ref.kind == 'global':
                return f'{base}/global'
            return f'{base}/projects/{ref.name}'
        if ref.owner == 'user':
            base = f"{self.path_user_prefix}/{self.user}"
            if ref.kind == 'global':
                return f'{base}/global'
            return f'{base}/projects/{ref.name}'
        if ref.kind == 'global':
            return self.path_team_global
        return f"{self.path_team_project_prefix}/{ref.name}"

    def cache_relpath(self, ref) -> str:
        """``secrets/cache/`` からの控えの位置 (``team/global.env.age`` / ``team/<g>/global.env.age``)"""
        parts = [ref.owner]
        if self.grouped:
            parts.append(self._group_of(ref))
        if ref.kind == 'global':
            parts.append('global.env.age')
        else:
            parts.extend(['projects', f'{ref.name}.env.age'])
        return '/'.join(parts)

    def cache_key(self, ref) -> str:
        """``index.json`` のキー (``team:global`` / ``team:<g>:project:<name>``)"""
        parts = [ref.owner]
        if self.grouped:
            parts.append(self._group_of(ref))
        if ref.kind == 'global':
            parts.append('global')
        else:
            parts.extend(['project', ref.name])
        return ':'.join(parts)

    def display_path(self, ref) -> str:
        """表示用の ``<mount>/<パス>``"""
        return f"{self.mount}/{self.path_of(ref)}"

    def to_dict(self) -> Dict[str, Any]:
        """書き出す形。``version: 1`` は今と同じキーだけ、``version: 2`` は置けるキーだけを書く"""
        data: Dict[str, Any] = {'url': self.url, 'mount': self.mount, 'user': self.user}
        if self.grouped:
            data['layout'] = self.layout
            data['path_team_prefix'] = self.path_team_prefix
            data['path_user_prefix'] = self.path_user_prefix
            if self.group_aliases:
                data['group_aliases'] = dict(self.group_aliases)
        else:
            data['path_team_global'] = self.path_team_global
            data['path_team_project_prefix'] = self.path_team_project_prefix
            data['path_user_prefix'] = self.path_user_prefix
        data['timeout_seconds'] = self.timeout_seconds
        return data


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
        if self.backend == BACKEND_OPENBAO and self.openbao is None:
            raise BackendConfigError(
                "backend: openbao に必要な設定が欠けています: "
                "openbao.url, openbao.user")
        layout = SUPPORTED_VERSIONS.get(self.version)
        if layout is None:
            raise BackendConfigError(
                f"version の値 {self.version!r} には対応していません "
                f"(受け付ける値: {', '.join(str(v) for v in SUPPORTED_VERSIONS)})")
        if layout == LAYOUT_GROUP and self.openbao is None:
            raise BackendConfigError(
                "version: 2 には openbao.layout: group が必要です "
                "(従来の置き場を使うなら version: 1 にしてください)")
        if self.openbao is not None:
            if self.openbao.layout != layout:
                raise BackendConfigError(
                    f"version: {self.version} と openbao.layout: {self.openbao.layout} は"
                    f"組み合わせられません (version: 1 は flat、version: 2 は group)")
            if self.backend == BACKEND_OPENBAO:
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


def _layout_from_dict(raw: Dict[str, Any], version: int) -> str:
    """版に置けないキーを拒み、レイアウトを返す (決定 1)。

    ``version: 2`` の ``layout`` は必須で ``group`` だけを受け付ける。``version: 1`` では
    ``layout`` 自体を置けない (``flat`` として動く)。
    """
    if version == 1:
        for key in _GROUP_ONLY_KEYS:
            if key in raw:
                raise BackendConfigError(
                    f"openbao.{key} は version: 2 でだけ置けます (現在: version 1)")
        return LAYOUT_FLAT
    for key in _FLAT_ONLY_KEYS:
        if key in raw:
            raise BackendConfigError(
                f"openbao.{key} は version: 2 では置けません "
                "(チーム単位の親は openbao.path_team_prefix で指定します)")
    layout = _str(raw.get('layout'))
    if not layout:
        raise BackendConfigError(
            "version: 2 には openbao.layout が必要です (受け付ける値: group。"
            "従来の置き場を使うなら version: 1 にしてください)")
    if layout != LAYOUT_GROUP:
        raise BackendConfigError(
            f"openbao.layout の値 {layout!r} には version: 2 では対応していません "
            f"(受け付ける値: {LAYOUT_GROUP})")
    return layout


def _aliases_from_dict(raw: Any) -> Dict[str, str]:
    if raw in (None, ''):
        return {}
    if not isinstance(raw, dict):
        raise BackendConfigError(
            f"openbao.group_aliases はグループ名の対応 (マッピング) で指定してください: {raw!r}")
    # 値の検証は OpenBaoSettings.validate が行う。ここでは型だけを揃える
    return {str(k): ('' if v is None else str(v)) for k, v in raw.items()}


def _openbao_from_dict(raw: Any, backend: str, version: int = 1) -> Optional[OpenBaoSettings]:
    """``openbao:`` 節を解釈する。節が無く backend も openbao でなければ ``None``。

    backend が openbao なのに節が無いときは空の設定を返し、欠けている項目は
    :meth:`OpenBaoSettings.validate` がキー名を挙げて拒む。``version: 2`` では節が無くても
    ``layout`` の欠落として拒む。
    """
    if not (isinstance(raw, dict) or backend == BACKEND_OPENBAO or version != 1):
        return None
    raw = raw if isinstance(raw, dict) else {}
    layout = _layout_from_dict(raw, version)
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
        layout=layout,
        path_team_prefix=_str(raw.get('path_team_prefix'), defaults.path_team_prefix),
        group_aliases=_aliases_from_dict(raw.get('group_aliases')),
    )


def _from_dict(data: Dict[str, Any], source: Optional[Path]) -> BackendConfig:
    if not isinstance(data, dict):
        raise BackendConfigError(f"{source} の内容がマッピングではありません")

    backend = _str(data.get('backend'), BACKEND_AUTO)

    raw_cache = data.get('cache')
    cache_enabled = True
    if isinstance(raw_cache, dict) and raw_cache.get('enabled') is not None:
        cache_enabled = bool(raw_cache.get('enabled'))

    raw_version = data.get('version')
    accepted = ', '.join(str(v) for v in SUPPORTED_VERSIONS)
    if raw_version in (None, ''):
        version = 1
    else:
        # 他の不正値と同じく、キー名と受け付ける値を添えて拒む (生の ValueError を漏らさない)
        try:
            version = int(str(raw_version).strip())
        except ValueError:
            raise BackendConfigError(
                f"version の値 {raw_version!r} には対応していません "
                f"(受け付ける値: {accepted})") from None
        if version not in SUPPORTED_VERSIONS:
            raise BackendConfigError(
                f"version の値 {raw_version!r} には対応していません (受け付ける値: {accepted})")

    # 置けるキーは版で決まるため、版を先に確かめてから openbao 節を読む
    openbao = _openbao_from_dict(data.get('openbao'), backend, version)

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
