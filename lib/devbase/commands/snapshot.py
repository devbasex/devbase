"""Snapshot command implementation"""

import sys
from pathlib import Path

from devbase.errors import SnapshotError
from devbase.log import get_logger
from devbase.snapshot.manager import SnapshotManager

logger = get_logger(__name__)


def _format_size(size_bytes: int) -> str:
    """バイト数を人間が読みやすい形式に変換"""
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def cmd_snapshot(devbase_root: Path, args) -> int:
    """snapshotサブコマンドの振り分け"""
    mgr = SnapshotManager(devbase_root)
    subcmd = getattr(args, 'subcommand', None)

    handlers = {
        'create':  lambda: _snapshot_create(mgr,
                                            name=getattr(args, 'name', None),
                                            full=getattr(args, 'full', False)),
        'list':    lambda: _snapshot_list(mgr),
        'restore': lambda: _snapshot_restore(mgr,
                                             name=getattr(args, 'name', ''),
                                             point=getattr(args, 'point', None)),
        'copy':    lambda: _snapshot_copy(mgr,
                                          name=getattr(args, 'name', ''),
                                          new_name=getattr(args, 'new_name', '')),
        'delete':  lambda: _snapshot_delete(mgr, name=getattr(args, 'name', '')),
        'rotate':  lambda: _snapshot_rotate(mgr, keep=getattr(args, 'keep', 3)),
    }

    handler = handlers.get(subcmd)
    if not handler:
        logger.error("サブコマンドを指定してください: %s", ', '.join(handlers))
        return 1

    try:
        return handler()
    except SnapshotError as e:
        logger.error("スナップショット操作に失敗: %s", e)
        return 1


def _snapshot_create(mgr, name=None, full=False) -> int:
    name = mgr.create(name=name, full=full)
    logger.info("スナップショットを作成しました: %s", name)
    return 0


def _snapshot_list(mgr) -> int:
    snapshots = mgr.list()
    if not snapshots:
        print("スナップショットはありません")
        return 0
    print(f"{'名前':<24} {'作成日時':<24} {'差分数':>6} {'サイズ':>10}")
    print("-" * 68)
    for s in snapshots:
        print(
            f"{s['name']:<24} "
            f"{s.get('created_at', 'N/A')[:19]:<24} "
            f"{s.get('incremental_count', 0):>6} "
            f"{_format_size(s.get('size_bytes', 0)):>10}"
        )
    return 0


def _snapshot_restore(mgr, name='', point=None) -> int:
    point_msg = f" (incr-{point:03d} まで)" if point is not None else ""
    if sys.stdin.isatty():
        answer = input(
            f"'{name}'{point_msg} から復元します。現在のボリュームデータは上書きされます。\n"
            f"続行しますか? [y/N]: "
        )
        if answer.lower() not in ('y', 'yes'):
            print("復元をキャンセルしました")
            return 0
    mgr.restore(name, point=point)
    return 0


def _snapshot_copy(mgr, name='', new_name='') -> int:
    mgr.copy(name, new_name)
    return 0


def _snapshot_delete(mgr, name='') -> int:
    mgr.delete(name)
    return 0


def _snapshot_rotate(mgr, keep=3) -> int:
    deleted = mgr.rotate(keep=keep)
    if deleted == 0:
        logger.info("ローテーション不要です")
    return 0
