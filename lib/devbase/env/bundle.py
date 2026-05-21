"""env export/import バンドル (tar.gz + manifest.yml) の構築・展開"""

from __future__ import annotations

import hashlib
import io
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

from devbase.errors import DevbaseError

try:
    from devbase import __version__ as _DEVBASE_VERSION
except ImportError:
    _DEVBASE_VERSION = "unknown"

MANIFEST_NAME = "manifest.yml"
SUPPORTED_MANIFEST_VERSION = 1


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
    # mtime=0 で再現性を確保
    with tarfile.open(fileobj=buf, mode='w:gz', format=tarfile.PAX_FORMAT) as tf:
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

    members: Dict[str, bytes] = {}
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
    version = manifest.get('version')
    if not isinstance(version, int):
        raise BundleError("manifest.version が不正です")
    if version > SUPPORTED_MANIFEST_VERSION:
        raise BundleError(
            f"manifest.version={version} はこの devbase ではサポートされていません "
            f"(対応最大={SUPPORTED_MANIFEST_VERSION})。devbase 本体を更新してください"
        )

    files = manifest.get('files') or []
    if not isinstance(files, list):
        raise BundleError("manifest.files が list ではありません")
    for entry in files:
        if not isinstance(entry, dict):
            raise BundleError(f"manifest.files の要素が dict ではありません: {type(entry).__name__}")
        path = entry.get('path')
        expected = entry.get('sha256')
        if not isinstance(path, str) or not path:
            raise BundleError(f"manifest.files の path が不正です: {path!r}")
        if expected is not None and not isinstance(expected, str):
            raise BundleError(f"manifest.files の sha256 が不正です (path={path}): {expected!r}")
        if path not in members:
            raise BundleError(f"manifest に記載されたファイルが見つかりません: {path}")
        actual = _sha256(members[path])
        if expected and expected != actual:
            raise BundleError(
                f"sha256 が一致しません (path={path}, expected={expected[:12]}..., "
                f"actual={actual[:12]}...)"
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
        if global_env.exists():
            entries.append(BundleEntry(
                arcname='env/global.env',
                origin='$DEVBASE_ROOT/.env',
                data=global_env.read_bytes(),
            ))

    if include_metadata:
        sources_yml = devbase_root / '.env.sources.yml'
        if sources_yml.exists():
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
            env_path = proj_dir / '.env'
            if env_path.exists():
                entries.append(BundleEntry(
                    arcname=f'env/projects/{name}/.env',
                    origin=f'$DEVBASE_ROOT/projects/{name}/.env',
                    data=env_path.read_bytes(),
                ))

    return entries
