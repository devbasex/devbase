"""env メニューとその画面が共有する委譲の手段 (#273)

``cmd_env`` への委譲・対象プロジェクトの選択・プロジェクトのディレクトリでの実行を持つ。
env メニュー (``actions_env``) と 2 つの画面 (``actions_env_keys`` / ``actions_env_openbao``) は
ここを参照し、画面が親メニューの内部を参照しないようにする。
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

_ARG_CANCEL = flow.ARG_CANCEL


def dispatch(devbase_root: Path, subcommand: str, **attrs):
    """``cmd_env`` への委譲 (dispatch_group の薄いラッパ)。

    import を関数内で行うのは actions_project (dispatch_lifecycle) と同様、
    テストで ``devbase.commands.env.cmd_env`` を monkeypatch できるようにするため。
    """
    from devbase.commands import env as env_mod

    return dispatch_group(env_mod.cmd_env, devbase_root, subcommand, **attrs)


def select_project(devbase_root: Path):
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


def run_in_project(devbase_root: Path, project_name: str, fn):
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
