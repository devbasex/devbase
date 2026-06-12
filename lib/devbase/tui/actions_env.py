"""env カテゴリの TUI 操作フロー (PLAN31_2 PR3)。

``devbase env`` の全サブコマンド (init/list/set/get/delete/edit/sync/project/
export/import) をトップ階層メニューから実行できるようにする。引数収集は
``tui.menu`` のヘルパで CLI parser (cli.py ``_add_env_parser``) と同じ属性値を
集め、``tui.dispatch.dispatch_group`` 経由で既存ハンドラ ``cmd_env`` へ委譲する
(plan 2.3 契約表 / ロジック二重実装なし)。

project スコープ依存の扱い (plan 3.3):
- ``set --project`` / ``project`` / ``list`` (プロジェクトを含む表示範囲) /
  ``get`` (プロジェクト取得) は CWD (環境変数 ``PWD``) のプロジェクト
  ディレクトリで動くため、先にプロジェクト選択メニューで対象を選ばせて
  chdir + ``PWD`` 差し替えしてからハンドラを呼び、実行後は必ず元へ復帰する
  (``_run_in_project``)。``cmd_env_*`` は ``os.environ.get('PWD', os.getcwd())``
  で現在地を判定するため、``os.chdir`` だけでなく ``PWD`` も併せて切り替える。
- ``edit`` は plan 3.3 で CWD スコープとされているが、実装 (``cmd_env_edit``) は
  常に ``$DEVBASE_ROOT/.env`` を開くグローバル操作のため、プロジェクト選択は
  行わない (plan 表と実装の乖離。parser / 実装を正とする)。

破壊的操作 ``delete`` は実行前に確認する (plan 3.4)。

export/import は引数が多いため TUI では主要引数 (``dest`` / ``source``) のみ
収集し、残りは CLI parser の既定値と同一の属性を明示的に渡す (既定値の乖離を
防ぐ。細かい制御が必要な場合は CLI を使う想定)。

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

# env カテゴリで選べる操作 (表示順 = ハイライト既定順)。参照系の list を先頭に
# 置き、Enter 連打で安全な一覧表示へ到達できるようにする。各 value は cmd_env の
# サブコマンド名。
_ENV_OPS: list[tuple[str, str]] = [
    ("変数一覧 (list)", "list"),
    ("値の取得 (get)", "get"),
    ("変数の設定 (set)", "set"),
    ("変数の削除 (delete)", "delete"),
    ("エディタで編集 (edit)", "edit"),
    ("認証情報の再同期 (sync)", "sync"),
    ("プロジェクト変数の対話設定 (project)", "project"),
    ("初期セットアップ (init)", "init"),
    ("暗号化バンドルへエクスポート (export)", "export"),
    ("バンドルからインポート (import)", "import"),
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


def _collect_assignment():
    """``env set`` の KEY=VALUE を収集する。

    形式エラー (``=`` 無し / キー名空) は ``cmd_env_set`` でも弾かれるが、TUI では
    実行前に再入力を促す。戻り値: 入力文字列 / ``MENU_BACK`` (Esc → サブメニューへ
    戻る) / ``None`` (Ctrl-C → 全体中止)。
    """
    while True:
        raw = menu.text("設定する変数 (KEY=VALUE 形式)", allow_empty=False)
        if raw is None or raw is menu.MENU_BACK:
            return raw                 # None=Ctrl-C 全体中止 / MENU_BACK=Esc 戻る
        if "=" not in raw or not raw.partition("=")[0].strip():
            logger.error("形式: KEY=VALUE (キー名は必須)")
            continue
        return raw


def _export_default_attrs() -> dict:
    """``env export`` の CLI parser 既定値 (cli.py:246-279) と同一の属性セット。

    TUI で収集しない引数も Namespace に明示的に載せ、CLI 実行と完全に同じ属性で
    ハンドラを呼ぶ (getattr 既定値とのズレを防ぐ)。list は呼び出しごとに新規生成。
    """
    return {
        "include_projects": None,
        "exclude_projects": [],
        "no_global": False,
        "no_metadata": False,
        "recipients": [],
        "passphrase_env": None,
        "passphrase_stdin": False,
        "force_unencrypted": False,
        "unsafe_allow_unencrypted_bucket": False,
    }


def _import_default_attrs() -> dict:
    """``env import`` の CLI parser 既定値 (cli.py:281-328) と同一の属性セット。"""
    return {
        "merge": "keep-existing",
        "replace_keys": "",
        "replace": False,
        "dry_run": False,
        "identities": [],
        "passphrase_env": None,
        "passphrase_stdin": False,
        "include_projects": None,
        "exclude_projects": [],
        "no_global": False,
        "no_metadata": False,
        "merge_metadata": False,
        "backup_dir": None,
        "keep_last": 10,
    }


def _select_scoped_project(devbase_root: Path, message: str, choices):
    """スコープ選択 + プロジェクトスコープなら対象プロジェクトも選ぶ共通フロー。

    list/set/get が共有する「グローバル or プロジェクトを選び、プロジェクトを
    含むスコープなら対象名も選ぶ」の前半 2 プロンプト。``(scope, name)`` を返す
    (グローバルのみのとき ``name`` は ``None``)。中止系は flow 例外で伝搬する。
    """
    scope = flow.need(menu.select(f"{message} {menu.HINT_BACK}:",
                                  choices, back=True, search=False))
    name = None
    if scope != "global":
        name = flow.need(_select_project(devbase_root))
    return scope, name


# ---------------------------------------------------------------------------
# 各操作の引数収集 + dispatch (plan 2.3 契約)
# ---------------------------------------------------------------------------

def _op_list(devbase_root: Path):
    """``env list``: 表示範囲を収集して一覧表示する。

    ハンドラ (``cmd_env_list``) は CWD (PWD) が projects/ 配下のときだけ
    プロジェクト .env を表示するため、プロジェクトを含む表示範囲
    (「グローバル + プロジェクト」「プロジェクトのみ」) は対象プロジェクトを
    選ばせて chdir + ``PWD`` 切替後に実行する (plan 3.3 / codex round3 指摘。
    TUI は通常 DEVBASE_ROOT で動くので、切替なしではプロジェクト分が表示
    されない)。「グローバルのみ」だけが切替なしで実行できる。
    """
    scope, name = _select_scoped_project(
        devbase_root, "表示範囲を選択",
        [("グローバル + プロジェクト", "both"),
         ("グローバルのみ (--global)", "global"),
         ("プロジェクトのみ (--project)", "project")])

    # --reveal / --keys は CLI 既定 (False = 機密値は伏せ字・通常表示) で実行する
    # (非破壊操作の確認プロンプト廃止)。必要な場合は CLI を使う想定。
    attrs = {"global_only": scope == "global",
             "project_only": scope == "project",
             "reveal": False, "keys_only": False}
    if name is None:
        return _dispatch(devbase_root, "list", **attrs)
    return _run_in_project(devbase_root, name,
                           lambda: _dispatch(devbase_root, "list", **attrs))


def _op_set(devbase_root: Path):
    """``env set``: 設定先 (グローバル / プロジェクト) と KEY=VALUE を収集して設定する。

    プロジェクト設定 (--project) は対象を選ばせて chdir してから実行する (plan 3.3)。
    """
    _, name = _select_scoped_project(
        devbase_root, "設定先を選択",
        [("グローバル ($DEVBASE_ROOT/.env)", "global"),
         ("プロジェクト (projects/<name>/.env, --project)", "project")])
    assignment = flow.need(_collect_assignment())

    if name is None:
        return _dispatch(devbase_root, "set", assignment=assignment, project=False)
    return _run_in_project(
        devbase_root, name,
        lambda: _dispatch(devbase_root, "set", assignment=assignment, project=True))


def _op_get(devbase_root: Path):
    """``env get``: 取得元 (グローバル / プロジェクト) と変数名を収集して値を表示する。

    ``cmd_env_get`` はグローバル .env に無いキーを CWD (PWD) のプロジェクト .env へ
    フォールバックして探すが、TUI は常に DEVBASE_ROOT で動くため、そのままでは
    プロジェクト固有キーを取得できない。list/set と同様に取得元を選ばせ、
    プロジェクト選択時は chdir + ``PWD`` 切替後に実行する (codex round2 指摘)。
    """
    _, name = _select_scoped_project(
        devbase_root, "取得元を選択",
        [("グローバル ($DEVBASE_ROOT/.env)", "global"),
         ("プロジェクト (グローバルに無ければ projects/<name>/.env)", "project")])
    key = flow.need(menu.text("取得する変数名", allow_empty=False))

    if name is None:
        return _dispatch(devbase_root, "get", key=key)
    return _run_in_project(devbase_root, name,
                           lambda: _dispatch(devbase_root, "get", key=key))


def _op_delete(devbase_root: Path):
    key = flow.need(menu.text("削除する変数名", allow_empty=False))
    # 破壊的操作のため実行前に確認する (plan 3.4)。拒否 / Esc は実行せず戻る。
    flow.confirm_or_back(f"変数 '{key}' をグローバル .env から削除しますか?")
    return _dispatch(devbase_root, "delete", key=key)


def _op_project(devbase_root: Path):
    # プロジェクト固有変数の対話設定。projects/ 配下で動く CWD スコープ操作の
    # ため、対象を選ばせて chdir してから実行する (plan 3.3)。
    name = flow.need(_select_project(devbase_root))
    return _run_in_project(devbase_root, name,
                           lambda: _dispatch(devbase_root, "project"))


def _op_export(devbase_root: Path):
    # 主要引数 dest のみ収集。空入力は parser 既定 (./devbase-env-<TS>.dbenv)。
    dest = flow.need(menu.path(
        "出力先パス (空で既定: ./devbase-env-<タイムスタンプ>.dbenv)",
        allow_empty=True))
    return _dispatch(devbase_root, "export", dest=(dest or None),
                     **_export_default_attrs())


def _op_import(devbase_root: Path):
    # 主要引数 source のみ収集 (必須 positional)。merge は parser 既定の
    # keep-existing (既存キー優先) で安全側。既存 .env はハンドラ側で
    # バックアップされる。
    source = flow.need(menu.path("インポートするバンドルのパス", allow_empty=False))
    return _dispatch(devbase_root, "import", source=source,
                     **_import_default_attrs())


_OP_HANDLERS = {
    # sync は引数なしで即実行 (ソースファイルから認証情報を再同期する)。
    # edit も引数なし。$DEVBASE_ROOT/.env を $EDITOR で開くグローバル操作のため
    # chdir しない (plan 3.3 は CWD スコープとするが実装を正とする)。
    # init は --reset なし (CLI 既定) で即実行。セットアップ済みなら
    # cmd_env_init が案内を出して安全に終了し、やり直しは CLI --reset を使う。
    "sync": lambda root: _dispatch(root, "sync"),
    "edit": lambda root: _dispatch(root, "edit"),
    "init": lambda root: _dispatch(root, "init", reset=False),
    "list": _op_list,
    "set": _op_set,
    "get": _op_get,
    "delete": _op_delete,
    "project": _op_project,
    "export": _op_export,
    "import": _op_import,
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
    - **操作を実行した場合**: dispatch の rc (``int``) を返す。「実行したのでトップへ
      戻る、rc は呼び出し側が記憶」の意味で、失敗が ``devbase list`` の終了コードへ
      伝搬する。
    - ``menu.MENU_BACK``: サブメニューで Esc/← (操作なしでトップへ)。
    - ``None``: Ctrl-C による全体中止。

    引数収集を中止 (``_ARG_CANCEL``) した場合はサブメニューを再表示する。
    """
    return flow.menu_loop(_select_action,
                          lambda op: _run_operation(devbase_root, op))
