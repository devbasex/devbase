"""``devbase env import`` の 2 フェーズ atomic 書き込み + backup / GC

import_bundle が作る :class:`_import_merge.Plan` 群を、以下の手順で適用する:

  1. ``backup_existing`` — 既存 target をタイムスタンプ付き backup_dir にコピー
  2. ``write_atomic`` (per plan) — ``<target>.import.tmp`` に 0600 で書き出し
  3. ``commit`` — 全 tmp を ``os.replace`` で一括 rename。途中失敗時は backup から
     best-effort で rollback し、未 rename の tmp も後始末する

加えて ``gc_backups`` で古い backup ディレクトリを ``keep_last`` 個まで圧縮する。
モジュール内で ``os`` を直接参照しており、テストはこの ``os.replace`` を
monkeypatch することで commit 失敗パスを再現する。
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from devbase.errors import DevbaseError
from devbase.log import get_logger

from devbase.env import io_common as _io_common
from devbase.env._import_merge import Plan

logger = get_logger(__name__)

# _make_backup_dir が生成するタイムスタンプ形式のみを GC 対象にする。
# 以下のいずれかにマッチするディレクトリのみ削除する:
#   YYYYMMDD-HHMMSS                    (旧フォーマット, 後方互換)
#   YYYYMMDD-HHMMSS-NNNNNN             (microsecond 付き)
#   YYYYMMDD-HHMMSS-NNNNNN-NN          (同一マイクロ秒内の連番付き)
# これ以外のディレクトリは devbase が作ったものではないので削除しない
# (--backup-dir 親に無関係なディレクトリがあっても安全)。
_BACKUP_DIR_NAME_RE = re.compile(r'^\d{8}-\d{6}(?:-\d{6}(?:-\d+)?)?$')


class AtomicError(DevbaseError):
    """atomic 書き込み中のエラー (ImportError へ委譲する用途で投げる)"""


def make_backup_dir(devbase_root: Path, backup_dir: Optional[str]) -> Path:
    """``--backup-dir`` 指定 or ``$DEVBASE_ROOT/backups/env-import/`` 配下に
    タイムスタンプ命名の backup ディレクトリを作る。

    秒精度のみだと同一秒に 2 回 import を走らせたときに同じディレクトリを再利用して
    前回バックアップを上書きしてしまうため、microsecond + 連番を付与して衝突を回避する
    (PR #15 codex 指摘)。
    """
    base = (Path(backup_dir).expanduser() if backup_dir
            else devbase_root / 'backups' / 'env-import')
    base.mkdir(parents=True, exist_ok=True)

    stem = datetime.now().strftime('%Y%m%d-%H%M%S-%f')  # microsecond まで
    primary = base / stem
    if not primary.exists():
        primary.mkdir(parents=True)
        return primary
    # 同一マイクロ秒に複数回走った場合の安全弁: 連番を付与
    for n in range(1, 1000):
        candidate = base / f'{stem}-{n:02d}'
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
    raise AtomicError(
        f"backup ディレクトリの衝突回避に失敗しました (base={base}, stem={stem})"
    )


def _backup_relative(target: Path, devbase_root: Path) -> Path:
    """target を devbase_root 相対表現に変換。外にあるパスはファイル名のみを使う。"""
    try:
        return target.relative_to(devbase_root)
    except ValueError:
        return Path(target.name)


def backup_existing(plans: Sequence[Plan],
                    sources_copy: Optional[Tuple[Path, bytes]],
                    backup_dir: Path, devbase_root: Path) -> None:
    """phase 1 前に既存ファイルの内容を ``backup_dir`` にコピーする"""
    for plan in plans:
        if not plan.target.exists():
            continue
        dst = backup_dir / _backup_relative(plan.target, devbase_root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plan.target, dst)

    # バンドルに含まれていた sources.yml の参照用コピー (上書きしないケース)
    if sources_copy is not None:
        _, data = sources_copy
        dst = backup_dir / 'sources.yml.imported'
        dst.parent.mkdir(parents=True, exist_ok=True)
        _io_common.write_secure_bytes(dst, data)


def write_atomic(plan: Plan) -> Path:
    """phase 1: 新内容を ``<target>.import.tmp`` として 0600 で書き出す"""
    tmp = plan.target.with_suffix(plan.target.suffix + '.import.tmp')
    if tmp.exists():
        # 過去の失敗の残骸を掃除
        try:
            tmp.unlink()
        except OSError:
            pass
    _io_common.write_secure_bytes(tmp, plan.new_bytes)
    return tmp


def commit(plans_and_tmps: List[Tuple[Plan, Path]], backup_dir: Path,
           devbase_root: Path) -> List[Path]:
    """phase 2: tmp → target に rename。

    途中失敗時は best-effort で rollback したうえで、まだ rename されていない
    ``.import.tmp`` ファイルもクリーンアップする (PR #15 gemini 指摘)。
    """
    committed: List[Tuple[Plan, Path]] = []
    remaining = list(plans_and_tmps)
    try:
        while remaining:
            plan, tmp = remaining[0]
            os.replace(tmp, plan.target)
            try:
                os.chmod(plan.target, 0o600)
            except OSError:
                pass
            committed.append((plan, plan.target))
            remaining.pop(0)
    except OSError as e:
        logger.error("commit フェーズで失敗しました: %s", e)
        try:
            _rollback(committed, backup_dir, devbase_root)
        finally:
            cleanup_tmps(tmp for _, tmp in remaining)
        raise AtomicError(f"commit フェーズで失敗しました: {e}") from e
    return [t for _, t in committed]


def _rollback(committed: Sequence[Tuple[Plan, Path]], backup_dir: Path,
              devbase_root: Path) -> None:
    """best-effort ロールバック:
      - 既存上書き (backup あり) → backup から復元
      - backup が無いケース → 元ファイルが存在しなかった (= 新規作成) と
        みなして unlink し、元の「不在」状態に戻す。``op='create'`` だけでなく
        ``op='sources-merge'`` で sources.yml を新規作成したケースもここで
        unlink する (PR #15 gemini 指摘)。

    ``backup_existing`` は target が存在した場合のみ backup を作る。よって
    「backup が無い」事実は「元ファイルが存在しなかった」ことを示している。
    """
    for _, target in committed:
        src = backup_dir / _backup_relative(target, devbase_root)
        if src.exists():
            try:
                shutil.copy2(src, target)
                logger.warning("rollback: %s を %s から復元しました", target, src)
            except OSError as e:
                logger.error("rollback 失敗: %s -> %s: %s", src, target, e)
            continue
        # 元ファイル不在 → 新規作成された target を unlink して元の状態に戻す
        try:
            target.unlink()
            logger.warning("rollback: 新規作成された %s を削除しました", target)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.error("rollback unlink 失敗: %s: %s", target, e)


def cleanup_tmps(tmps) -> None:
    """``.import.tmp`` の残骸を削除する (失敗は無視)"""
    for tmp in tmps:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def gc_backups(backup_dir: Path, keep_last: int) -> None:
    """``backup_dir`` の親ディレクトリで古い backup を ``keep_last`` 個まで残して GC する。

    devbase 生成のタイムスタンプ形式 (``YYYYMMDD-HHMMSS[-NNNNNN[-NN]]``) に
    マッチするディレクトリのみが GC 対象。``--backup-dir`` 親に無関係な
    ファイル / ディレクトリがあっても、それらは触らない。
    """
    if keep_last <= 0:
        return
    parent = backup_dir.parent
    if not parent.is_dir():
        return
    candidates = [p for p in parent.iterdir()
                  if p.is_dir() and _BACKUP_DIR_NAME_RE.match(p.name)]
    if len(candidates) <= keep_last:
        return
    # 名前 (= タイムスタンプ) でソート、古いものから捨てる。keep_last は通常 10 程度なので
    # 全件ソートで十分 (heapq.nsmallest を使うほどの規模ではない)。
    candidates.sort(key=lambda p: p.name)
    for d in candidates[:-keep_last]:
        try:
            shutil.rmtree(d)
            logger.info("古い backup を削除しました: %s", d)
        except OSError as e:
            logger.warning("backup 削除に失敗 (%s): %s", d, e)
