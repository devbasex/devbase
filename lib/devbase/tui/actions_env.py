"""env カテゴリの TUI 操作フロー (PLAN31_2 PR3 → メニュー再構成)。

TUI では次の 4 つだけを扱い、メニュー階層を浅くする:

- 「キーの一覧と編集」(``actions_env_keys``、#273): キーの一覧と、キー単位の追加・変更・削除。
  ``set`` / ``delete`` へ委譲する。一覧・エディタでの編集・プロジェクト変数の対話設定は
  この画面と役割が重なるため TUI から外した (#312。CLI の ``env list`` / ``edit`` /
  ``project`` で実行する)
- 「認証情報の再同期 (sync)」「初期セットアップ (init)」: 引数なしで即実行する
- 「OpenBao の接続設定」(``actions_env_openbao``): 接続先とブートストラップ機密を変える
- export/import は TUI から除外する (CLI で実行)。

引数収集は ``tui.menu`` のヘルパで CLI parser (cli.py ``_add_env_parser``) と
同じ属性値を集め、``tui.dispatch.dispatch_group`` 経由で既存ハンドラ
``cmd_env`` へ委譲する (ロジック二重実装なし)。

プロジェクトの置き場への書き込みは、CWD (環境変数 ``PWD``) のプロジェクトディレクトリで
動く ``set -p`` / ``delete -p`` へ委譲するため、chdir + ``PWD`` 差し替えしてからハンドラを
呼び、実行後は必ず元へ復帰する (``_run_in_project``)。``cmd_env_*`` は
``os.environ.get('PWD', os.getcwd())`` で現在地を判定するため、``os.chdir`` だけでなく
``PWD`` も併せて切り替える。

中止系の伝搬 (Ctrl-C / Esc / ``_ARG_CANCEL``) は ``tui.flow`` のナビ規約に従う。
"""

from __future__ import annotations

import os
from pathlib import Path

from devbase.log import get_logger
from devbase.tui import flow, menu
from devbase.tui.dispatch import dispatch_group

logger = get_logger(__name__)

# env カテゴリで選べる操作 (表示順 = ハイライト既定順)。書き込む前に選び直せる
# 「キーの一覧と編集」を先頭に置き、Enter 連打で書き込みの操作へ到達しないようにする。
_ENV_OPS: list[tuple[str, str]] = [
    ("キーの一覧と編集", "keys"),
    ("認証情報の再同期 (sync)", "sync"),
    ("初期セットアップ (init)", "init"),
    ("OpenBao の接続設定", "openbao"),
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

_OP_HANDLERS = {
    # sync は引数なしで即実行 (ソースファイルから認証情報を再同期する)。
    # init は --reset なし (CLI 既定) で即実行。セットアップ済みなら
    # cmd_env_init が案内を出して安全に終了し、やり直しは CLI --reset を使う。
    "sync": lambda root: _dispatch(root, "sync"),
    "init": lambda root: _dispatch(root, "init", reset=False),
    # 2 つの画面は自分の中で引数を集め、cmd_env へ委譲する (#273)。関数内で import するのは
    # 画面のモジュールが本モジュールの _dispatch / _run_in_project を使うため (循環を避ける)。
    "keys": lambda root: _screen("actions_env_keys").run(root),
    "openbao": lambda root: _screen("actions_env_openbao").run(root),
}


def _screen(name: str):
    import importlib

    return importlib.import_module(f"devbase.tui.{name}")


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
