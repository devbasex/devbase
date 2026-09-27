"""Snapshot command implementation"""

import sys
from pathlib import Path

from devbase.errors import DevbaseError, SnapshotError
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


#: 引数の誤り (``--group`` が無い・使えない名前) の終了コード
EXIT_USAGE = 2


def _create_group(devbase_root: Path, group):
    """``snapshot create`` の対象のグループ。``(グループ, None)`` か ``(None, 終了コード)``。

    ``--group`` があればその名前、無ければ実行時のプロジェクトの宣言 (#315 決定 3)。
    プロジェクトの外で ``--group`` が無ければ 2、宣言が読めなければ 1。既定の値は無いため、
    旧既定のボリュームの系列は作られない (I10)。
    """
    from devbase.env import groups as _groups
    from devbase.env import runtime as _runtime
    from devbase.volume.manager import validate_account_group

    if group is not None:
        try:
            return validate_account_group(group), None
        except DevbaseError as e:
            logger.error("--group に使えない名前です: %s", e)
            return None, EXIT_USAGE
    project = _runtime.current_project_name(devbase_root)
    if project is None:
        logger.error("プロジェクトの外では対象のグループを決められません。"
                     "--group <名前> を付けてください (例: --group nyle)")
        return None, EXIT_USAGE
    try:
        return _groups.declare(devbase_root, project).name, None
    except DevbaseError as e:
        logger.error("%s", e)
        return None, 1


def cmd_snapshot(devbase_root: Path, args) -> int:
    """snapshotサブコマンドの振り分け"""
    subcmd = getattr(args, 'subcommand', None)
    group = None
    if subcmd == 'create':
        group, rc = _create_group(devbase_root, getattr(args, 'group', None))
        if group is None:
            return rc
    mgr = SnapshotManager(devbase_root, group=group)

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
        # TUI の dispatch_group は keep だけを持つ引数を渡すため getattr で受ける
        'rotate':  lambda: _snapshot_rotate(mgr, keep=getattr(args, 'keep', 3),
                                            max_total=getattr(args, 'max_total', None)),
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
    print(f"{'名前':<24} {'作成日時':<24} {'差分数':>6} {'サイズ':>10}  対象ボリューム")
    print("-" * 90)
    for s in snapshots:
        # 対象ボリュームは PLAN39 以降に記録される。旧世代は共通ボリュームのみ。
        volumes = ', '.join((s.get('volumes') or {}).values()) or 'devbase_home_ubuntu'
        print(
            f"{s['name']:<24} "
            f"{s.get('created_at', 'N/A')[:19]:<24} "
            f"{s.get('incremental_count', 0):>6} "
            f"{_format_size(s.get('size_bytes', 0)):>10}  "
            f"{volumes}"
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


def _snapshot_rotate(mgr, keep=3, max_total=None) -> int:
    deleted = mgr.rotate(keep=keep, max_total=max_total)
    if deleted == 0:
        logger.info("ローテーション不要です")
    return 0
