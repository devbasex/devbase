"""env カテゴリの TUI 操作フロー (PLAN31_2 PR3 → メニュー再構成)。

TUI では参照・対話系の操作を中心にし、メニュー階層を浅くする:

- 変数一覧はスコープ選択の中間プロンプトを挟まず、グローバル一覧のみを
  即実行する。プロジェクト単位の一覧は TUI から除外する (CLI で実行)。
- キー単位の追加・変更・削除は「キーの一覧と編集」の画面 (``actions_env_keys``、#273) で
  行い、``set`` / ``delete`` へ委譲する。OpenBao の接続先とブートストラップ機密は
  「OpenBao の接続設定」の画面 (``actions_env_openbao``) で変える。
- export/import は TUI から除外する (CLI で実行)。

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

from pathlib import Path

from devbase.log import get_logger
from devbase.tui import actions_env_keys, actions_env_openbao, flow, menu
from devbase.tui.env_dispatch import dispatch as _dispatch
from devbase.tui.env_dispatch import run_in_project as _run_in_project
from devbase.tui.env_dispatch import select_project as _select_project

logger = get_logger(__name__)

# env カテゴリで選べる操作 (表示順 = ハイライト既定順)。参照系のグローバル一覧を
# 先頭に置き、Enter 連打で安全な一覧表示へ到達できるようにする (中間プロンプト
# なしで即実行)。プロジェクト単位の一覧と export/import は TUI から除外 (CLI で実行)。
# #273 の 2 つの画面は既存の 5 つの後に置く (既存の名前と順は変えない)。
_ENV_OPS: list[tuple[str, str]] = [
    ("変数一覧 (グローバル)", "list-global"),
    ("エディタで編集 (edit)", "edit"),
    ("認証情報の再同期 (sync)", "sync"),
    ("プロジェクト変数の対話設定 (project)", "project"),
    ("初期セットアップ (init)", "init"),
    ("キーの一覧と編集", "keys"),
    ("OpenBao の接続設定", "openbao"),
]

# 中止系番兵は flow と同一オブジェクトを再公開する (呼び出し側・テストの契約)。
_ARG_CANCEL = flow.ARG_CANCEL


def _select_action():
    """env 操作を選ぶサブメニュー。

    戻り値: サブコマンド文字列 / ``MENU_BACK`` (Esc・← → トップへ戻る) / ``None``
    (Ctrl-C 中止)。
    """
    return menu.select(f"環境変数の操作を選択 {menu.HINT_BACK}:",
                       list(_ENV_OPS), back=True, search=False)


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
    # 2 つの画面は自分の中で引数を集め、cmd_env へ委譲する (#273)
    "keys": lambda root: actions_env_keys.run(root),
    "openbao": lambda root: actions_env_openbao.run(root),
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
