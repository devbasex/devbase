"""env の TUI の画面が ``cmd_env`` へ委譲する部品 (``actions_env`` と各画面が使う)。

env メニュー (``actions_env``) と画面 (``actions_env_keys`` / ``actions_env_openbao``) は、
どちらもこのモジュールを通して ``cmd_env`` を呼ぶ。画面からメニューのモジュールを
読まないため、モジュールの間に循環が無い。

プロジェクトの置き場への書き込みは、CWD (環境変数 ``PWD``) のプロジェクトディレクトリで
動く ``set -p`` / ``delete -p`` へ委譲するため、chdir + ``PWD`` 差し替えしてからハンドラを
呼び、実行後は必ず元へ復帰する (:func:`run_in_project`)。
"""

from __future__ import annotations

import os
from pathlib import Path

from devbase.log import get_logger
from devbase.tui import flow
from devbase.tui.dispatch import dispatch_group

logger = get_logger(__name__)


def dispatch(devbase_root: Path, subcommand: str, **attrs):
    """``cmd_env`` への委譲 (dispatch_group の薄いラッパ)。

    import を関数内で行うのは actions_project (dispatch_lifecycle) と同様、
    テストで ``devbase.commands.env.cmd_env`` を monkeypatch できるようにするため。
    """
    from devbase.commands import env as env_mod

    return dispatch_group(env_mod.cmd_env, devbase_root, subcommand, **attrs)


def run_in_project(devbase_root: Path, project_name: str, fn):
    """``projects/<name>`` へ chdir + ``PWD`` を切り替えて fn を実行し、必ず復帰する。

    ``cmd_env_set --project`` / ``cmd_env_project`` は
    ``os.environ.get('PWD', os.getcwd())`` で現在地を判定する (wrapper の cd を
    前提とした PLAN06 機構) ため、``os.chdir`` だけでは不十分で ``PWD`` も
    プロジェクトパスへ差し替える。``PWD`` は symlink を解決しない
    ``projects/<name>`` を指す (projects/ 配下判定を成立させるため)。

    戻り値: fn の rc / ``flow.ARG_CANCEL`` (対象ディレクトリへ移動できない場合)。
    """
    target = Path(devbase_root) / "projects" / project_name
    old_cwd = Path.cwd()
    old_pwd = os.environ.get("PWD")
    try:
        os.chdir(target)
    except OSError as exc:
        logger.error("プロジェクトディレクトリへ移動できません: %s (%s)", target, exc)
        return flow.ARG_CANCEL
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
