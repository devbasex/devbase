"""snapshot カテゴリの TUI 操作フロー (PLAN31_2 PR5)。

サブコマンド選択メニュー → 引数収集 → ``dispatch_group(cmd_snapshot, ...)`` で
既存ハンドラへ委譲する。属性契約は plan 2.3 の表 (cli.py ``_add_snapshot_parser``
と同期済みを確認):

- create:  ``name`` (None=タイムスタンプ自動命名), ``full`` (False)
- list:    追加属性なし
- restore: ``name``, ``point`` (None=全差分適用 / manager は 1 以上のみ受理)
- copy:    ``name``, ``new_name``
- delete:  ``name``
- rotate:  ``keep`` (3)

破壊的な restore / delete は実行前に確認する (plan 3.4)。restore は
``cmd_snapshot`` 側にも TTY 時の input() 確認が残るが、TUI の規約として
メニュー段階でも確認する (多重確認になっても安全側に倒す)。

restore / copy / delete の対象 ``name`` は ``SnapshotManager.list()`` の既存一覧
から選択させる (タイプミス防止)。一覧の取得に失敗した場合のみ自由入力へ縮退する。
中止系の伝搬 (Ctrl-C / Esc / ``_ARG_CANCEL``) は ``tui.flow`` のナビ規約に従う。
"""

from __future__ import annotations

from pathlib import Path

from devbase.commands.snapshot import cmd_snapshot
from devbase.log import get_logger
from devbase.snapshot.manager import SnapshotManager
from devbase.tui import flow, menu
from devbase.tui.dispatch import dispatch_group

logger = get_logger(__name__)

# snapshot カテゴリで選べる操作 (表示順 = ハイライト既定順)。閲覧のみで安全な
# list を先頭に置き、Enter 連打では破壊的操作に到達しないようにする。
# 各 value は cmd_snapshot のサブコマンド名。
_SNAPSHOT_OPS: list[tuple[str, str]] = [
    ("一覧表示 (list)", "list"),
    ("作成 (create)", "create"),
    ("復元 (restore)", "restore"),
    ("複製 (copy)", "copy"),
    ("削除 (delete)", "delete"),
    ("ローテーション (rotate)", "rotate"),
]

# 中止系番兵は flow と同一オブジェクトを再公開する (呼び出し側・テストの契約)。
_ARG_CANCEL = flow.ARG_CANCEL
_ABORT = flow.ABORT


def _select_operation():
    """snapshot の操作を選ぶサブメニュー。

    戻り値: サブコマンド文字列 / ``MENU_BACK`` (Esc・← → トップへ戻る) / ``None`` (Ctrl-C 中止)。
    """
    return menu.select(f"スナップショット操作を選択 {menu.HINT_BACK}:",
                       list(_SNAPSHOT_OPS), back=True, search=False)


def _select_snapshot_name(devbase_root: Path, message: str):
    """restore/copy/delete の対象スナップショット名を既存一覧から選ばせる。

    戻り値: スナップショット名 (``str``) / ``None`` (Ctrl-C → 全体中止を呼び出し元へ
    伝搬) / ``_ARG_CANCEL`` (Esc → 操作メニューへ戻る、または対象が 1 件もない)。
    一覧の取得に失敗した場合は自由入力へ縮退する (存在チェックは委譲先の
    ``SnapshotManager`` が行う。text 入力も Esc=戻る / Ctrl-C=全体中止を区別する)。
    """
    try:
        snapshots = SnapshotManager(Path(devbase_root)).list()
    except Exception:
        logger.debug("スナップショット一覧の取得に失敗しました", exc_info=True)
        snapshots = None

    if snapshots is None:
        # 一覧が取れない環境では名前を直接入力させる。
        name = menu.text(message, allow_empty=False)
        if name is None:
            return None                # Ctrl-C → 全体中止 (ナビ規約)
        if name is menu.MENU_BACK:
            return _ARG_CANCEL         # Esc → 操作メニューを再表示
        return name

    if not snapshots:
        logger.info("スナップショットがありません。先に作成 (create) してください。")
        return _ARG_CANCEL

    # 作成日時を添えて選びやすくする (値は名前のみ)。件数が多い場合に備え
    # 文字入力での絞り込み (search=True) を有効化。search 有効時の戻る操作は
    # Esc のみ (menu.select が ← バインドを外す)。
    choices = [
        (f"{s.get('name', '?')}  ({str(s.get('created_at') or 'N/A')[:19]})",
         s.get("name"))
        for s in snapshots
    ]
    sel = menu.select(f"{message} {menu.HINT_SEARCH}:", choices,
                      back=True, search=True)
    if sel is None:
        return None                    # Ctrl-C → 全体中止 (ナビ規約)
    if sel is menu.MENU_BACK:
        return _ARG_CANCEL             # Esc → 操作メニューを再表示
    return sel


def _optional_point(message: str):
    """restore の ``--point`` を収集する (空入力 = 全差分適用 = None)。

    ``flow.optional_int`` の再公開 (戻り値の番兵契約はそちらを参照)。
    ``SnapshotManager.restore`` は point に正の整数のみ受理するため 1 以上を要求する。
    """
    return flow.optional_int(message, min_value=1)


# ---------------------------------------------------------------------------
# 各操作の引数収集 + dispatch (plan 2.3 契約)
# ---------------------------------------------------------------------------

def _op_create(devbase_root: Path):
    name = flow.need(menu.text("スナップショット名 (空でタイムスタンプ自動命名)",
                               allow_empty=True))
    full = flow.need(menu.confirm("フルバックアップを強制しますか (--full)?",
                                  default=False))
    # 空入力は CLI の --name 省略と同じ None (自動命名) に正規化する。
    return dispatch_group(cmd_snapshot, devbase_root, "create",
                          name=name or None, full=full)


def _op_restore(devbase_root: Path):
    name = flow.need(_select_snapshot_name(
        devbase_root, "復元するスナップショットを選択"))
    point = flow.need_optional(_optional_point(
        "適用する差分番号 incr-N の上限 (--point / 空で全差分適用)"))
    point_msg = f" (incr-{point:03d} まで)" if point is not None else ""
    flow.confirm_or_back(
        f"'{name}'{point_msg} から復元しますか? 現在のボリュームデータは上書きされます。")
    return dispatch_group(cmd_snapshot, devbase_root, "restore",
                          name=name, point=point)


def _op_copy(devbase_root: Path):
    name = flow.need(_select_snapshot_name(
        devbase_root, "複製元のスナップショットを選択"))
    new_name = flow.need(menu.text("複製先のスナップショット名", allow_empty=False))
    return dispatch_group(cmd_snapshot, devbase_root, "copy",
                          name=name, new_name=new_name)


def _op_delete(devbase_root: Path):
    name = flow.need(_select_snapshot_name(
        devbase_root, "削除するスナップショットを選択"))
    flow.confirm_or_back(f"スナップショット '{name}' を削除しますか?")
    return dispatch_group(cmd_snapshot, devbase_root, "delete", name=name)


def _op_rotate(devbase_root: Path):
    # keep=0 は manager 実装上 no-op (空スライス) のため 1 以上を要求する。
    keep = flow.need(menu.integer("保持する世代数 (--keep)", default=3, min_value=1))
    return dispatch_group(cmd_snapshot, devbase_root, "rotate", keep=keep)


_OP_HANDLERS = {
    "list": lambda root: dispatch_group(cmd_snapshot, root, "list"),
    "create": _op_create,
    "restore": _op_restore,
    "copy": _op_copy,
    "delete": _op_delete,
    "rotate": _op_rotate,
}


@flow.collect_args
def _run_operation(devbase_root: Path, op: str):
    """選択された操作の引数を収集して ``dispatch_group`` で ``cmd_snapshot`` へ委譲する。

    戻り値: dispatch の rc (``int``) / ``_ARG_CANCEL`` (Esc・確認拒否で引数収集を
    中止 = サブメニューへ戻る) / ``None`` (選択・入力中の Ctrl-C → 全体中止)。
    破壊的な restore / delete は実行前に確認し、拒否時は実行しない (plan 3.4)。
    """
    handler = _OP_HANDLERS.get(op)
    if handler is None:
        # 到達しない (メニュー値は _SNAPSHOT_OPS に限定される)。保守的に no-op。
        logger.error("未知の操作です: %s", op)
        raise flow.BackOut
    return handler(devbase_root)


def run(devbase_root: Path):
    """スナップショット操作カテゴリ。操作選択 → 引数収集 → 実行。

    戻り値プロトコル (``flow.menu_loop``。トップループが ``is`` 同一性で判定する):
    - **操作を実行した場合**: ``dispatch_group`` の rc (``int``) を返す。
      「実行したのでトップへ戻る、rc は呼び出し側が記憶」の意味。
    - ``menu.MENU_BACK``: 操作メニューで Esc/← (操作なしでトップへ)。
    - ``None``: Ctrl-C による全体中止。

    引数収集を中止 (``_ARG_CANCEL``) した場合は操作メニューを再表示する。
    操作完了後はトップメニューへ復帰する (plan 3.5 状態遷移: Exec → Top)。
    """
    return flow.menu_loop(_select_operation,
                          lambda op: _run_operation(devbase_root, op))
