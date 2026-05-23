"""``devbase env import`` の merge / replace 計画

ファイル単位の操作内容 (新規作成 / マージ / 置換 / sources-merge) を
:class:`Plan` として表現し、``incoming`` と ``existing`` から差分計算する。

実書き込み (atomic rename / backup / rollback) は :mod:`_import_atomic` の役割で、
このモジュールは「何を書くか」だけを決定する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

from devbase.errors import DevbaseError
from devbase.log import get_logger

from devbase.env.store import EnvFile

logger = get_logger(__name__)

# project 名は通常のディレクトリ名のみ許容する。
#   - 先頭文字: 英数字 / `_`  (`.` を許可すると `env/projects/./.env` が
#     `$DEVBASE_ROOT/projects/.env` に正規化され、グローバル .env を上書きする
#     path traversal 系の問題になる — PR #13 codex round 3 指摘)
#   - 2文字目以降: 英数字 / `_` / `-` / `.`
#   - `.` / `..` のような特殊セグメント、空文字、`/` を含む値は弾く
# bundle._validate_manifest や tar 展開側 (`..` のみ拒否) では塞ぎきれないため、
# arcname を path に解決する側で project 名を制限する。
_PROJECT_ENV_RE = re.compile(r'^env/projects/([A-Za-z0-9_][A-Za-z0-9_.\-]*)/\.env$')

# import_bundle が許容する --merge モード一覧。CLI の choices と一致させる。
MERGE_MODES: Tuple[str, ...] = ('keep-existing', 'prefer-incoming')


class MergeError(DevbaseError):
    """merge 計画作成中のエラー (ImportError へ委譲する用途で投げる)"""


@dataclass
class Plan:
    """1 ファイル分の書き出し計画。

    ``added_keys`` / ``overwritten_keys`` / ``skipped_keys`` は dry-run およびログ表示で
    "何が起こるか" をユーザに伝えるために保持する。
    """
    target: Path
    arcname: str
    new_bytes: bytes
    added_keys: List[str] = field(default_factory=list)
    overwritten_keys: List[str] = field(default_factory=list)
    skipped_keys: List[str] = field(default_factory=list)
    op: str = 'merge'  # 'merge' | 'replace' | 'create' | 'sources-merge'


def target_for(arcname: str, devbase_root: Path) -> Path:
    """バンドル内 arcname を ``devbase_root`` 配下の書き出し先 Path に解決する"""
    if arcname == 'env/global.env':
        return devbase_root / '.env'
    if arcname == 'env/sources.yml':
        return devbase_root / '.env.sources.yml'
    m = _PROJECT_ENV_RE.match(arcname)
    if m:
        return devbase_root / 'projects' / m.group(1) / '.env'
    raise MergeError(f"未対応のバンドルエントリ: {arcname}")


def filter_members(
    members: Dict[str, bytes],
    *,
    include_global: bool,
    include_metadata: bool,
    include_projects: Optional[Sequence[str]],
    exclude_projects: Sequence[str],
) -> Dict[str, bytes]:
    """include/exclude 指定で展開済みメンバーを絞り込む"""
    included = set(include_projects) if include_projects else None
    excluded = set(exclude_projects)
    result: Dict[str, bytes] = {}

    for arcname, data in members.items():
        if arcname == 'env/global.env':
            if include_global:
                result[arcname] = data
            continue
        if arcname == 'env/sources.yml':
            if include_metadata:
                result[arcname] = data
            continue
        m = _PROJECT_ENV_RE.match(arcname)
        if not m:
            # manifest 検証 (bundle._validate_manifest) は path のパターンを制限していないため、
            # 未対応 arcname がここに来た場合は黙って捨てると "manifest と適用結果が食い違う"
            # 整合性問題になる。明示的にエラーで止める (PR #13 codex 指摘)。
            raise MergeError(
                f"バンドルに未対応の arcname が含まれています: {arcname} "
                "(対応形式: env/global.env / env/sources.yml / env/projects/<name>/.env)"
            )
        name = m.group(1)
        if name in excluded:
            continue
        if included is not None and name not in included:
            continue
        result[arcname] = data
    return result


def _merge_into_existing_bytes(existing_bytes: bytes,
                               merged: Dict[str, str]) -> bytes:
    """既存 ``.env`` のコメント / 空行 / キー順を保持したまま、``merged`` で値を差し替える。

    既存に無いキーは末尾に sorted 順で append。``merged`` から除外されたキーは
    出力からも除外する (現状の merge ロジック上発生しないが、安全側で対応)。

    値が変更されていないキーは ``raw`` 行をそのまま温存して出力する。これにより
    例えば ``PATH=$HOME/bin`` のような未クオート値が ``PATH="\\$HOME/bin"`` に
    勝手にエスケープされて source 時の意味が変わるのを防ぐ (PR #13 codex 指摘)。
    値が変わったキーと新規キーのみ ``EnvFile._format_kv_line`` でフォーマットする。

    ``EnvFile.dump_bytes`` で再シリアライズするとコメント・空行が失われるため、
    ``EnvFile.parse_entries`` ベースで再構成している (PR #15 gemini 指摘)。
    """
    seen: set[str] = set()
    out_lines: List[str] = []
    for e in EnvFile.parse_entries(existing_bytes):
        if e.kind != 'kv' or e.key is None:
            out_lines.append(e.raw + '\n')
            continue
        if e.key in merged:
            seen.add(e.key)
            new_value = merged[e.key]
            if e.value == new_value:
                # 値が変わっていないキーは元の raw 行を温存する (escape 形式や
                # クオート有無を保持して source 時の意味が変わらないように)
                out_lines.append(e.raw + '\n')
            else:
                out_lines.append(
                    EnvFile._format_kv_line(e.key, new_value)
                )
        # merged から除外されているキーは entries からも落とす
    for key in sorted(k for k in merged if k not in seen):
        out_lines.append(EnvFile._format_kv_line(key, merged[key]))
    return ''.join(out_lines).encode('utf-8')


def _plan_replace(target: Path, arcname: str, incoming: Dict[str, str],
                  existing: Dict[str, str], incoming_bytes: bytes,
                  target_exists: bool) -> Plan:
    """--replace: ファイル丸ごとを incoming で置き換える"""
    added = sorted(set(incoming) - set(existing))
    overwritten = sorted(
        k for k in incoming if k in existing and incoming[k] != existing[k]
    )
    return Plan(
        target=target,
        arcname=arcname,
        new_bytes=incoming_bytes,
        added_keys=added,
        overwritten_keys=overwritten,
        # op 判定はファイル実体の有無で行う:
        # コメントのみの既存 .env を 'create' と誤判定しないため (PR #15 round5 指摘)。
        op='replace' if target_exists else 'create',
    )


def _plan_keep_existing(incoming: Dict[str, str], existing: Dict[str, str],
                        merged: Dict[str, str], added: List[str],
                        skipped: List[str]) -> None:
    """既存キーは保持。新規キーのみ追加"""
    for key, value in incoming.items():
        if key in existing:
            skipped.append(key)
        else:
            merged[key] = value
            added.append(key)


def _plan_prefer_incoming(incoming: Dict[str, str], existing: Dict[str, str],
                          merged: Dict[str, str], added: List[str],
                          overwritten: List[str]) -> None:
    """incoming で既存キーを上書き"""
    for key, value in incoming.items():
        if key in existing:
            if existing[key] != value:
                overwritten.append(key)
        else:
            added.append(key)
        merged[key] = value


def _plan_replace_keys(incoming: Dict[str, str], existing: Dict[str, str],
                       replace_keys: Sequence[str], merged: Dict[str, str],
                       added: List[str], overwritten: List[str],
                       skipped: List[str]) -> None:
    """--replace-keys: 指定キーのみ上書き、残りは keep-existing 相当

    keep-existing 相当 = 既存にあれば残す、無ければ新規追加 (skipped は
    上書きを抑止したキーのみ)。
    """
    replace_set = set(replace_keys)
    for key, value in incoming.items():
        if key in replace_set:
            if key in existing and existing[key] != value:
                overwritten.append(key)
            elif key not in existing:
                added.append(key)
            merged[key] = value
        elif key in existing:
            if existing[key] != value:
                skipped.append(key)
        else:
            added.append(key)
            merged[key] = value


def plan_env_merge(target: Path, incoming_bytes: bytes, arcname: str, *,
                   merge: str = 'keep-existing',
                   replace: bool = False,
                   replace_keys: Sequence[str] = ()) -> Plan:
    """1 つの ``.env`` に対する merge / replace 計画を作る

    新規作成 (= target 不在) ケースでは ``incoming_bytes`` をそのまま採用する。
    ``EnvFile.dump_bytes`` で再シリアライズすると、export 側で既に escape された値が
    parse_bytes 経由でも完全に round-trip できる前提が崩れた瞬間に二重エスケープが
    発生するためである (PR #15 codex 指摘)。

    既存ファイルが存在する merge 経路では :func:`_merge_into_existing_bytes` で
    既存のコメント / 空行 / キー順を保持したまま値だけ差し替える (PR #15 gemini 指摘)。
    """
    incoming = EnvFile.parse_bytes(incoming_bytes)
    target_exists = target.exists()
    existing_bytes = target.read_bytes() if target_exists else b''
    existing = EnvFile.parse_bytes(existing_bytes) if target_exists else {}

    if replace:
        return _plan_replace(target, arcname, incoming, existing,
                             incoming_bytes, target_exists)

    merged: Dict[str, str] = dict(existing)
    added: List[str] = []
    overwritten: List[str] = []
    skipped: List[str] = []

    if replace_keys:
        _plan_replace_keys(incoming, existing, replace_keys,
                           merged, added, overwritten, skipped)
    elif merge == 'keep-existing':
        _plan_keep_existing(incoming, existing, merged, added, skipped)
    elif merge == 'prefer-incoming':
        _plan_prefer_incoming(incoming, existing, merged, added, overwritten)
    else:
        raise MergeError(f"不明な --merge モード: {merge!r}")

    new_bytes = (_merge_into_existing_bytes(existing_bytes, merged)
                 if target_exists else incoming_bytes)
    return Plan(
        target=target,
        arcname=arcname,
        new_bytes=new_bytes,
        added_keys=sorted(added),
        overwritten_keys=sorted(overwritten),
        skipped_keys=sorted(skipped),
        op='merge' if target_exists else 'create',
    )


def plan_sources(target: Path, incoming_bytes: bytes, *,
                 merge_metadata: bool) -> Optional[Plan]:
    """``.env.sources.yml`` の取り扱い計画

    既定: 上書きしないため ``None`` を返す (参照用コピーの保存は呼び出し側で実施)。
    ``merge_metadata=True``: 新規 source エントリのみ追加した内容で更新する。
    """
    if not merge_metadata:
        return None

    try:
        incoming = yaml.safe_load(incoming_bytes) or {}
    except yaml.YAMLError as e:
        raise MergeError(f"バンドルの sources.yml が壊れています: {e}") from e
    if not isinstance(incoming, dict):
        raise MergeError("バンドルの sources.yml が dict ではありません")
    incoming_sources = incoming.get('sources') or {}
    if not isinstance(incoming_sources, dict):
        raise MergeError("バンドルの sources.yml の sources が dict ではありません")

    existing: Dict = {}
    if target.exists():
        try:
            existing = yaml.safe_load(target.read_bytes()) or {}
        except yaml.YAMLError as e:
            raise MergeError(
                f"既存の {target.name} のパースに失敗しました: {e}"
            ) from e
    if not isinstance(existing, dict):
        existing = {}
    existing_sources = existing.get('sources')
    if not isinstance(existing_sources, dict):
        existing_sources = {}

    merged_sources = dict(existing_sources)
    added: List[str] = []
    for name, entry in incoming_sources.items():
        if name in merged_sources:
            continue
        merged_sources[name] = entry
        added.append(name)
    if not added:
        return None  # 変化なし

    existing['sources'] = merged_sources
    new_bytes = yaml.safe_dump(
        existing, default_flow_style=False, allow_unicode=True
    ).encode('utf-8')
    return Plan(
        target=target,
        arcname='env/sources.yml',
        new_bytes=new_bytes,
        added_keys=sorted(added),
        op='sources-merge',
    )


def log_plans(plans: Sequence[Plan], dry_run: bool) -> None:
    """dry-run / 通常実行のいずれでも plan の内容を logger.info で表示する"""
    prefix = "[dry-run] " if dry_run else ""
    for plan in plans:
        logger.info(
            "%s%s: %s (+%d add / ~%d overwrite / -%d skip)",
            prefix, plan.op, plan.target,
            len(plan.added_keys), len(plan.overwritten_keys), len(plan.skipped_keys),
        )
        if plan.added_keys:
            logger.info("  added: %s", ", ".join(plan.added_keys))
        if plan.overwritten_keys:
            logger.info("  overwrite: %s", ", ".join(plan.overwritten_keys))
        if plan.skipped_keys:
            logger.info("  skip (existing kept): %s", ", ".join(plan.skipped_keys))
