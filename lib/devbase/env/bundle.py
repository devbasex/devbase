"""env export/import バンドル (tar.gz + manifest.yml) の構築・展開"""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

from devbase.errors import DevbaseError
from devbase.log import get_logger

try:
    from devbase import __version__ as _DEVBASE_VERSION
except ImportError:
    _DEVBASE_VERSION = "unknown"

logger = get_logger(__name__)

MANIFEST_NAME = "manifest.yml"
SUPPORTED_MANIFEST_VERSION = 1

# import/export 共通の project 名 validator。
# 詳細仕様は `_import_merge._PROJECT_ENV_RE` の docstring を参照:
#   - 先頭文字: 英数字 / `_`  (`.` 始まりは `./` / `../` 等の特殊セグメント拒否のため)
#   - 2文字目以降: 英数字 / `_` / `-` / `.`
#   - `.` / `..` / 空文字 / 空白 / `/` を含む値は弾く
# import 側 (`_import_merge.filter_members`) で `MergeError` にする一方、
# export 側 (`make_entries_from_disk`) でも同じ validator を使い、
# round-trip できない bundle を export しないようにする (PR #13 codex round 5 指摘)。
_VALID_PROJECT_NAME_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.\-]*$')


def is_valid_project_name(name: str) -> bool:
    """bundle arcname (`env/projects/<name>/.env`) に使える project 名かを判定する"""
    return bool(_VALID_PROJECT_NAME_RE.match(name))


class BundleError(DevbaseError):
    """バンドル構築・展開エラー"""


@dataclass(frozen=True)
class BundleEntry:
    """バンドル内ファイル 1 件"""
    arcname: str       # tar 内パス (例: 'env/global.env')
    origin: str        # 元ファイルの DEVBASE_ROOT 相対表記 (例: '$DEVBASE_ROOT/.env')
    data: bytes


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _local_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def build_manifest(entries: Sequence[BundleEntry],
                   devbase_version: str = _DEVBASE_VERSION,
                   created_at: Optional[str] = None) -> Dict:
    """manifest.yml の dict 表現を生成する"""
    return {
        'version': SUPPORTED_MANIFEST_VERSION,
        'created_at': created_at or _local_now_iso(),
        'devbase_version': devbase_version,
        'files': [
            {'path': e.arcname, 'sha256': _sha256(e.data), 'origin': e.origin}
            for e in entries
        ],
    }


def pack(entries: Sequence[BundleEntry],
         devbase_version: str = _DEVBASE_VERSION,
         created_at: Optional[str] = None) -> bytes:
    """エントリ群を manifest.yml 付きの tar.gz バイト列にまとめる"""
    manifest = build_manifest(entries, devbase_version=devbase_version,
                              created_at=created_at)
    manifest_bytes = yaml.safe_dump(manifest, sort_keys=False,
                                    allow_unicode=True).encode('utf-8')

    buf = io.BytesIO()
    # 再現性を確保:
    #   - tarfile の mode='w:gz' は gzip ヘッダに現在時刻を埋め込むため出力が
    #     非決定的になる。gzip.GzipFile を mtime=0 で明示的に作成し、その上に
    #     tarfile を mode='w' で書き出すことで完全に決定的なバイト列にする。
    #   - PAX_FORMAT を指定して各エントリの mtime=0 等のメタも安定させる。
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT) as tf:
            _add_member(tf, MANIFEST_NAME, manifest_bytes)
            for entry in entries:
                _add_member(tf, entry.arcname, entry.data)
    return buf.getvalue()


def _add_member(tf: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o600
    tf.addfile(info, io.BytesIO(data))


def unpack(blob: bytes) -> Tuple[Dict, Dict[str, bytes]]:
    """tar.gz バイト列から (manifest, {arcname: bytes}) を取り出す

    sha256 / version の検証も行う。
    """
    buf = io.BytesIO(blob)
    try:
        tf = tarfile.open(fileobj=buf, mode='r:gz')
    except tarfile.TarError as e:
        raise BundleError(f"tar.gz の読み込みに失敗しました: {e}") from e
    except OSError as e:
        raise BundleError(f"tar.gz の読み込みに失敗しました: {e}") from e

    members: Dict[str, bytes] = {}
    try:
        with tf:
            for info in tf.getmembers():
                if not info.isfile():
                    continue
                if info.name.startswith('/') or '..' in info.name.split('/'):
                    raise BundleError(f"不正なパスを含んでいます: {info.name}")
                if info.name in members:
                    raise BundleError(f"重複エントリを検出しました: {info.name}")
                f = tf.extractfile(info)
                if f is None:
                    continue
                members[info.name] = f.read()
    except BundleError:
        raise
    except tarfile.TarError as e:
        raise BundleError(f"tar の展開に失敗しました: {e}") from e
    except OSError as e:
        raise BundleError(f"tar の展開に失敗しました: {e}") from e

    manifest_bytes = members.pop(MANIFEST_NAME, None)
    if manifest_bytes is None:
        raise BundleError(f"{MANIFEST_NAME} がバンドルに含まれていません")

    try:
        manifest = yaml.safe_load(manifest_bytes) or {}
    except yaml.YAMLError as e:
        raise BundleError(f"{MANIFEST_NAME} のパースに失敗しました: {e}") from e

    _validate_manifest(manifest, members)
    return manifest, members


def _validate_manifest(manifest: Dict, members: Dict[str, bytes]) -> None:
    if not isinstance(manifest, dict):
        raise BundleError(
            f"{MANIFEST_NAME} の top-level が mapping ではありません "
            f"(type={type(manifest).__name__})"
        )
    version = manifest.get('version')
    if not isinstance(version, int):
        raise BundleError("manifest.version が不正です")
    if version != SUPPORTED_MANIFEST_VERSION:
        raise BundleError(
            f"manifest.version={version} はこの devbase ではサポートされていません "
            f"(対応={SUPPORTED_MANIFEST_VERSION})。devbase 本体を更新してください"
        )

    files = manifest.get('files') or []
    if not isinstance(files, list):
        raise BundleError("manifest.files が list ではありません")

    manifest_paths: set = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise BundleError(f"manifest.files の要素が dict ではありません: {type(entry).__name__}")
        path = entry.get('path')
        expected = entry.get('sha256')
        if not isinstance(path, str) or not path:
            raise BundleError(f"manifest.files の path が不正です: {path!r}")
        if path in manifest_paths:
            # 重複 path は origin/metadata の解釈が曖昧になるため拒否する
            raise BundleError(f"manifest.files に同じ path が重複しています: {path}")
        if not isinstance(expected, str) or len(expected) != 64 or not all(
            c in '0123456789abcdef' for c in expected.lower()
        ):
            raise BundleError(
                f"manifest.files の sha256 が不正です (path={path}): "
                f"64文字の16進文字列が必要です"
            )
        expected = expected.lower()
        if path not in members:
            raise BundleError(f"manifest に記載されたファイルが見つかりません: {path}")
        actual = _sha256(members[path])
        if expected != actual:
            raise BundleError(
                f"sha256 が一致しません (path={path}, expected={expected[:12]}..., "
                f"actual={actual[:12]}...)"
            )
        manifest_paths.add(path)

    # tar 内のファイルセットと manifest のファイルセットの完全一致を検証する。
    # manifest に記載のないファイルが tar に混入していても検知できるようにする
    # (バンドル内未知ファイルの混入はセキュリティ・整合性リスクのため拒否)。
    unknown = sorted(set(members) - manifest_paths)
    if unknown:
        raise BundleError(
            "manifest に記載のないファイルがバンドルに含まれています: "
            + ", ".join(unknown)
        )


def make_entries_from_disk(devbase_root,
                           include_global: bool = True,
                           include_metadata: bool = True,
                           include_projects: Optional[Sequence[str]] = None,
                           exclude_projects: Sequence[str] = ()) -> List[BundleEntry]:
    """DEVBASE_ROOT 配下から export 対象を収集して BundleEntry のリストを返す

    Args:
        devbase_root: Path
        include_global: True なら $DEVBASE_ROOT/.env を含める
        include_metadata: True なら $DEVBASE_ROOT/.env.sources.yml を含める
        include_projects: 指定があればこのプロジェクト名のみを対象
        exclude_projects: 除外するプロジェクト名
    """
    from pathlib import Path

    devbase_root = Path(devbase_root)
    entries: List[BundleEntry] = []

    if include_global:
        global_env = devbase_root / '.env'
        # is_file() でディレクトリ等を除外し、IsADirectoryError 等の例外を防ぐ
        if global_env.is_file():
            entries.append(BundleEntry(
                arcname='env/global.env',
                origin='$DEVBASE_ROOT/.env',
                data=global_env.read_bytes(),
            ))

    if include_metadata:
        sources_yml = devbase_root / '.env.sources.yml'
        if sources_yml.is_file():
            entries.append(BundleEntry(
                arcname='env/sources.yml',
                origin='$DEVBASE_ROOT/.env.sources.yml',
                data=sources_yml.read_bytes(),
            ))

    projects_dir = devbase_root / 'projects'
    if projects_dir.is_dir():
        excluded = set(exclude_projects)
        included = set(include_projects) if include_projects else None

        candidates = sorted(p for p in projects_dir.iterdir() if p.is_dir())
        for proj_dir in candidates:
            name = proj_dir.name
            if name in excluded:
                continue
            if included is not None and name not in included:
                continue
            # import 側で `_PROJECT_ENV_RE` により制限されている project 名と同じ
            # validator で fail-fast する。空白や先頭 `.` などを含むディレクトリを
            # そのまま arcname にすると export は成功しても後続の import が
            # `未対応の arcname` で失敗し、round-trip できない bundle が生成される
            # (PR #13 codex round 5 指摘)。明示エラーではなく "警告 + スキップ" 方針:
            #   - レビュー指摘の選択肢が「明示エラー or skip with warning」だったこと
            #   - 一時ディレクトリや leftover (e.g. `.git`, `.DS_Store` でも `.` 始まりで弾かれる)
            #     が混在しても valid な project だけは export を成功させたいユースケース
            # のため後者を採用。include_projects で明示指定された名前が invalid な
            # ときも warning のみで落とすことで、暗黙的に round-trip 不能なバンドル
            # を作らないようにする。
            if not is_valid_project_name(name):
                logger.warning(
                    "project '%s' は bundle に含められない名前 (空白 / 先頭 `.` / `/` 等) "
                    "のためスキップします: %s",
                    name, proj_dir,
                )
                continue
            env_path = proj_dir / '.env'
            if env_path.is_file():
                entries.append(BundleEntry(
                    arcname=f'env/projects/{name}/.env',
                    origin=f'$DEVBASE_ROOT/projects/{name}/.env',
                    data=env_path.read_bytes(),
                ))

    return entries
