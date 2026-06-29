"""env カテゴリの TUI 操作フロー (PLAN31_2 PR3 → メニュー再構成)。

TUI では参照・対話系の操作のみに絞り、メニュー階層を浅くする:

- 変数一覧はスコープ選択の中間プロンプトを挟まず、グローバル一覧のみを
  即実行する。プロジェクト単位の一覧は TUI から除外する (CLI で実行)。
- キー単位の get/set/delete と export/import も TUI から除外する (CLI で実行。
  値の変更は ``edit`` ($EDITOR) と ``project`` (対話設定) で代替できる)。

引数収集は ``tui.menu`` のヘルパで CLI parser (cli.py ``_add_env_parser``) と
同じ属性値を集め、``tui.dispatch.dispatch_group`` 経由で既存ハンドラ
``cmd_env`` へ委譲する (ロジック二重実装なし)。

project スコープ依存の扱い (plan 3.3):
- ``project`` (対話設定) は CWD (環境変数 ``PWD``) のプロジェクトディレクトリで
  動くため、先にプロジェクト選択メニューで対象を選ばせて chdir + ``PWD``
  差し替えしてからハンドラを呼び、実行後は必ず元へ復帰する
  (``_run_in_project``)。``cmd_env_*`` は ``os.environ.get('PWD', os.getcwd())``
  で現在地を判定するため、``os.chdir`` だけでなく ``PWD`` も併せて切り替える。
- ``edit`` は常に ``$DEVBASE_ROOT/.env`` を開くグローバル操作のため、
  プロジェクト選択は行わない。

中止系の伝搬 (Ctrl-C / Esc / ``_ARG_CANCEL``) は ``tui.flow`` のナビ規約に従う。
"""

from __future__ import annotations

import os
from pathlib import Path

from devbase.commands.project import (
    _STATUS_COLOR,
    _build_menu_entries,
    list_projects,
)
from devbase.log import get_logger
from devbase.tui import flow, menu
from devbase.tui.dispatch import dispatch_group

logger = get_logger(__name__)

# env カテゴリで選べる操作 (表示順 = ハイライト既定順)。参照系のグローバル一覧を
# 先頭に置き、Enter 連打で安全な一覧表示へ到達できるようにする (中間プロンプト
# なしで即実行)。プロジェクト単位の一覧と get/set/delete/export/import は
# TUI から除外 (CLI で実行)。
_ENV_OPS: list[tuple[str, str]] = [
    ("変数一覧 (グローバル)", "list-global"),
    ("エディタで編集 (edit)", "edit"),
    ("認証情報の再同期 (sync)", "sync"),
    ("プロジェクト変数の対話設定 (project)", "project"),
    ("初期セットアップ (init)", "init"),
]

# 中止系番兵は flow と同一オブジェクトを再公開する (呼び出し側・テストの契約)。
_ARG_CANCEL = flow.ARG_CANCEL


def _dispatch(devbase_root: Path, subcommand: str, **attrs):
    """``cmd_env`` への委譲 (dispatch_group の薄いラッパ)。

    import を関数内で行うのは actions_project (dispatch_lifecycle) と同様、
    テストで ``devbase.commands.env.cmd_env`` を monkeypatch できるようにするため。
    """
    from devbase.commands import env as env_mod

    return dispatch_group(env_mod.cmd_env, devbase_root, subcommand, **attrs)


def _select_action():
    """env 操作を選ぶサブメニュー。

    戻り値: サブコマンド文字列 / ``MENU_BACK`` (Esc・← → トップへ戻る) / ``None``
    (Ctrl-C 中止)。
    """
    return menu.select(f"環境変数の操作を選択 {menu.HINT_BACK}:",
                       list(_ENV_OPS), back=True, search=False)


def _select_project(devbase_root: Path):
    """project スコープ操作の対象プロジェクトを選ぶ。

    actions_project と同じ一覧取得 (``list_projects`` + ``_build_menu_entries``) を
    流用する。戻り値: プロジェクト名 (``str``) / ``None`` (Ctrl-C → 全体中止を呼び
    出し元へ伝搬) / ``_ARG_CANCEL`` (Esc → サブメニューへ戻る、またはプロジェクト無し)。
    """
    projects_dir = Path(devbase_root) / "projects"
    rows = list_projects(projects_dir)
    if not rows:
        logger.info("プロジェクトがありません (%s)。", projects_dir)
        return _ARG_CANCEL

    entries = _build_menu_entries(rows, colorize=_STATUS_COLOR)
    choices = [(entry, i) for i, entry in enumerate(entries)]
    idx = menu.select(f"対象プロジェクトを選択 {menu.HINT_SEARCH}:",
                      choices, back=True, search=True)
    if isinstance(idx, int):
        return rows[idx]["name"]
    return flow.back_as_cancel(idx)    # None=Ctrl-C / MENU_BACK=Esc → 再表示


def _run_in_project(devbase_root: Path, project_name: str, fn):
    """``projects/<name>`` へ chdir + ``PWD`` を切り替えて fn を実行し、必ず復帰する。

    ``cmd_env_set --project`` / ``cmd_env_project`` は
    ``os.environ.get('PWD', os.getcwd())`` で現在地を判定する (wrapper の cd を
    前提とした PLAN06 機構) ため、``os.chdir`` だけでは不十分で ``PWD`` も
    プロジェクトパスへ差し替える。``PWD`` は symlink を解決しない
    ``projects/<name>`` を指す (projects/ 配下判定を成立させるため)。

    戻り値: fn の rc / ``_ARG_CANCEL`` (対象ディレクトリへ移動できない場合)。
    """
    target = Path(devbase_root) / "projects" / project_name
    old_cwd = Path.cwd()
    old_pwd = os.environ.get("PWD")
    try:
        os.chdir(target)
    except OSError as exc:
        logger.error("プロジェクトディレクトリへ移動できません: %s (%s)", target, exc)
        return _ARG_CANCEL
    os.environ["PWD"] = str(target)
    try:
        return fn()
    finally:
        # 実行結果に関わらず必ず元の CWD / PWD へ復帰する (plan 3.3)。
        os.chdir(old_cwd)
        if old_pwd is None:
            os.environ.pop("PWD", None)
        else:
            os.environ["PWD"] = old_pwd


# ---------------------------------------------------------------------------
# 各操作の引数収集 + dispatch (plan 2.3 契約)
# ---------------------------------------------------------------------------

def _op_project(devbase_root: Path):
    # プロジェクト固有変数の対話設定。projects/ 配下で動く CWD スコープ操作の
    # ため、対象を選ばせて chdir してから実行する (plan 3.3)。
    name = flow.need(_select_project(devbase_root))
    return _run_in_project(devbase_root, name,
                           lambda: _dispatch(devbase_root, "project"))


_OP_HANDLERS = {
    # グローバル一覧は引数収集なしで即実行 (chdir 不要)。--reveal/--keys は
    # CLI 既定の False (伏せ字・通常表示)。
    # sync は引数なしで即実行 (ソースファイルから認証情報を再同期する)。
    # edit も引数なし。$DEVBASE_ROOT/.env を $EDITOR で開くグローバル操作のため
    # chdir しない (plan 3.3 は CWD スコープとするが実装を正とする)。
    # init は --reset なし (CLI 既定) で即実行。セットアップ済みなら
    # cmd_env_init が案内を出して安全に終了し、やり直しは CLI --reset を使う。
    "list-global": lambda root: _dispatch(root, "list", global_only=True,
                                          project_only=False,
                                          reveal=False, keys_only=False),
    "sync": lambda root: _dispatch(root, "sync"),
    "edit": lambda root: _dispatch(root, "edit"),
    "init": lambda root: _dispatch(root, "init", reset=False),
    "project": _op_project,
}


@flow.collect_args
def _run_operation(devbase_root: Path, op: str):
    """選択された env 操作の引数を収集して ``cmd_env`` へ委譲する。

    戻り値: dispatch の rc (``int``) / ``_ARG_CANCEL`` (Esc・確認拒否で引数収集を
    中止 = サブメニューへ戻る) / ``None`` (選択・入力中の Ctrl-C → 全体中止)。
    属性は plan 2.3 の契約表 (cli.py parser と同期確認済み) に従う。
    """
    handler = _OP_HANDLERS.get(op)
    if handler is None:
        # 到達しない (メニュー値は _ENV_OPS に限定される)。保守的に no-op。
        logger.error("未知の操作です: %s", op)
        raise flow.BackOut
    return handler(devbase_root)


def run(devbase_root: Path):
    """環境変数カテゴリのエントリ。操作選択 → 引数収集 → cmd_env へ委譲。

    戻り値プロトコル (``flow.menu_loop``。トップループが ``is`` 同一性で判定する):
    - ``menu.MENU_BACK``: サブメニューで Esc/← (トップへ戻る)。操作を実行しても
      (出力確認の一時停止後) サブメニューに留まり、Esc/← で初めてトップへ戻る。
    - ``None``: Ctrl-C による全体中止。

    操作実行後・引数収集中止 (``_ARG_CANCEL``) のいずれもサブメニューを再表示する。
    """
    return flow.menu_loop(_select_action,
                          lambda op: _run_operation(devbase_root, op))
