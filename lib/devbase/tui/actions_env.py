"""env カテゴリの TUI 操作フロー (PLAN31_2 PR3)。

``devbase env`` の全サブコマンド (init/list/set/get/delete/edit/sync/project/
export/import) をトップ階層メニューから実行できるようにする。引数収集は
``tui.menu`` のヘルパで CLI parser (cli.py ``_add_env_parser``) と同じ属性値を
集め、``tui.dispatch.dispatch_group`` 経由で既存ハンドラ ``cmd_env`` へ委譲する
(plan 2.3 契約表 / ロジック二重実装なし)。

project スコープ依存の扱い (plan 3.3):
- ``set --project`` / ``project`` / ``list --project`` は CWD (環境変数 ``PWD``)
  のプロジェクト
  ディレクトリで動くため、先にプロジェクト選択メニューで対象を選ばせて
  chdir + ``PWD`` 差し替えしてからハンドラを呼び、実行後は必ず元へ復帰する
  (``_run_in_project``)。``cmd_env_*`` は ``os.environ.get('PWD', os.getcwd())``
  で現在地を判定するため、``os.chdir`` だけでなく ``PWD`` も併せて切り替える。
- ``edit`` は plan 3.3 で CWD スコープとされているが、実装 (``cmd_env_edit``) は
  常に ``$DEVBASE_ROOT/.env`` を開くグローバル操作のため、プロジェクト選択は
  行わない (plan 表と実装の乖離。parser / 実装を正とする)。

破壊的操作 ``delete`` は実行前に ``menu.confirm`` で確認する (plan 3.4)。

export/import は引数が多いため TUI では主要引数 (``dest`` / ``source``) のみ
収集し、残りは CLI parser の既定値と同一の属性を明示的に渡す (既定値の乖離を
防ぐ。細かい制御が必要な場合は CLI を使う想定)。

ナビ規約は actions_project と同一:
- サブメニューで Esc/← → カテゴリメニューへ戻る → (呼び出し元へ ``MENU_BACK``)
- Ctrl-C → ``None`` を伝搬して全体中止
- 引数収集の中止 (``_ARG_CANCEL``) → サブメニューを再表示
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
from devbase.tui import menu
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

# 引数収集を Esc/Ctrl-C で中止したことを示す番兵 (= サブメニューへ戻る)。
# dispatch の rc (int) や ``None`` (= 全体中止) と区別する (actions_project と同じ)。
_ARG_CANCEL = object()


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
    return menu.select(
        "環境変数の操作を選択 "
        "(↑↓ 移動 / Enter 決定 / ←・Esc 戻る / Ctrl-C 中止):",
        list(_ENV_OPS), back=True, search=False)


def _select_project(devbase_root: Path):
    """project スコープ操作の対象プロジェクトを選ぶ。

    actions_project と同じ一覧取得 (``list_projects`` + ``_build_menu_entries``) を
    流用する。戻り値: プロジェクト名 (``str``) / ``_ARG_CANCEL`` (Esc・Ctrl-C 中止、
    またはプロジェクト無し)。
    """
    projects_dir = Path(devbase_root) / "projects"
    rows = list_projects(projects_dir)
    if not rows:
        logger.info("プロジェクトがありません (%s)。", projects_dir)
        return _ARG_CANCEL

    entries = _build_menu_entries(rows, colorize=_STATUS_COLOR)
    choices = [(entry, i) for i, entry in enumerate(entries)]
    idx = menu.select(
        "対象プロジェクトを選択 "
        "(↑↓ 移動 / 名前で絞り込み / Enter 決定 / Esc 戻る / Ctrl-C 中止):",
        choices, back=True, search=True)
    if idx is menu.MENU_BACK or idx is None:
        return _ARG_CANCEL
    return rows[idx]["name"]


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
    実行前に再入力を促す。戻り値: 入力文字列 / ``None`` (Esc・Ctrl-C 中止)。
    """
    while True:
        raw = menu.text("設定する変数 (KEY=VALUE 形式)", allow_empty=False)
        if raw is None:
            return None
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


def _run_list(devbase_root: Path):
    """``env list``: 表示範囲 + 表示オプションを収集して一覧表示する。

    「プロジェクトのみ」はハンドラ (``cmd_env_list``) が CWD (PWD) でプロジェクト
    .env を判定するため、対象プロジェクトを選ばせて chdir + ``PWD`` 切替後に
    実行する (plan 3.3。TUI は通常 DEVBASE_ROOT で動くので切替なしでは何も
    表示されない)。「グローバル + プロジェクト」は CLI 既定と同じ CWD 判定の
    ままとする (TUI ではグローバルのみになることが多い)。
    """
    scope = menu.select(
        "表示範囲を選択 (↑↓ 移動 / Enter 決定 / ←・Esc 戻る / Ctrl-C 中止):",
        [("グローバル + プロジェクト (既定)", "both"),
         ("グローバルのみ (--global)", "global"),
         ("プロジェクトのみ (--project)", "project")],
        back=True, search=False)
    if scope is menu.MENU_BACK or scope is None:
        return _ARG_CANCEL

    name = None
    if scope == "project":
        name = _select_project(devbase_root)
        if name is _ARG_CANCEL:
            return _ARG_CANCEL

    reveal = menu.confirm("機密値を伏せ字にせず表示しますか (--reveal)?", default=False)
    if reveal is None:
        return _ARG_CANCEL
    keys_only = menu.confirm("キー名のみ表示しますか (--keys)?", default=False)
    if keys_only is None:
        return _ARG_CANCEL

    if scope == "project":
        return _run_in_project(
            devbase_root, name,
            lambda: _dispatch(devbase_root, "list",
                              global_only=False, project_only=True,
                              reveal=reveal, keys_only=keys_only))
    return _dispatch(devbase_root, "list",
                     global_only=(scope == "global"), project_only=False,
                     reveal=reveal, keys_only=keys_only)


def _run_set(devbase_root: Path):
    """``env set``: 設定先 (グローバル / プロジェクト) と KEY=VALUE を収集して設定する。

    プロジェクト設定 (--project) は対象を選ばせて chdir してから実行する (plan 3.3)。
    """
    scope = menu.select(
        "設定先を選択 (↑↓ 移動 / Enter 決定 / ←・Esc 戻る / Ctrl-C 中止):",
        [("グローバル ($DEVBASE_ROOT/.env)", "global"),
         ("プロジェクト (projects/<name>/.env, --project)", "project")],
        back=True, search=False)
    if scope is menu.MENU_BACK or scope is None:
        return _ARG_CANCEL

    name = None
    if scope == "project":
        name = _select_project(devbase_root)
        if name is _ARG_CANCEL:
            return _ARG_CANCEL

    assignment = _collect_assignment()
    if assignment is None:
        return _ARG_CANCEL

    if scope == "project":
        return _run_in_project(
            devbase_root, name,
            lambda: _dispatch(devbase_root, "set",
                              assignment=assignment, project=True))
    return _dispatch(devbase_root, "set", assignment=assignment, project=False)


def _run_operation(devbase_root: Path, op: str):
    """選択された env 操作の引数を収集して ``cmd_env`` へ委譲する。

    戻り値: dispatch の rc (``int``) / ``_ARG_CANCEL`` (引数収集を中止 =
    サブメニューへ戻る)。属性は plan 2.3 の契約表 (cli.py parser と同期確認済み)
    に従う。
    """
    if op == "sync":
        # 引数なし。ソースファイルから認証情報を再同期する。
        return _dispatch(devbase_root, "sync")

    if op == "edit":
        # 引数なし。$DEVBASE_ROOT/.env を $EDITOR で開く (グローバル操作。
        # plan 3.3 は CWD スコープとするが実装はグローバルのため chdir しない)。
        return _dispatch(devbase_root, "edit")

    if op == "init":
        # 既存設定がある場合は --reset でやり直し (既存はバックアップされる)。
        reset = menu.confirm(
            "既存の設定をバックアップしてやり直しますか (--reset)?", default=False)
        if reset is None:
            return _ARG_CANCEL
        return _dispatch(devbase_root, "init", reset=reset)

    if op == "list":
        return _run_list(devbase_root)

    if op == "set":
        return _run_set(devbase_root)

    if op == "get":
        key = menu.text("取得する変数名", allow_empty=False)
        if key is None:
            return _ARG_CANCEL
        return _dispatch(devbase_root, "get", key=key)

    if op == "delete":
        key = menu.text("削除する変数名", allow_empty=False)
        if key is None:
            return _ARG_CANCEL
        # 破壊的操作のため実行前に確認する (plan 3.4)。拒否 (False) / 中止 (None)
        # は実行せずサブメニューへ戻る。
        ok = menu.confirm(f"変数 '{key}' をグローバル .env から削除しますか?",
                          default=False)
        if not ok:
            return _ARG_CANCEL
        return _dispatch(devbase_root, "delete", key=key)

    if op == "project":
        # プロジェクト固有変数の対話設定。projects/ 配下で動く CWD スコープ操作の
        # ため、対象を選ばせて chdir してから実行する (plan 3.3)。
        name = _select_project(devbase_root)
        if name is _ARG_CANCEL:
            return _ARG_CANCEL
        return _run_in_project(devbase_root, name,
                               lambda: _dispatch(devbase_root, "project"))

    if op == "export":
        # 主要引数 dest のみ収集。空入力は parser 既定 (./devbase-env-<TS>.dbenv)。
        dest = menu.path("出力先パス (空で既定: ./devbase-env-<タイムスタンプ>.dbenv)",
                         allow_empty=True)
        if dest is None:
            return _ARG_CANCEL
        return _dispatch(devbase_root, "export", dest=(dest or None),
                         **_export_default_attrs())

    if op == "import":
        # 主要引数 source のみ収集 (必須 positional)。merge は parser 既定の
        # keep-existing (既存キー優先) で安全側。既存 .env はハンドラ側で
        # バックアップされる。
        source = menu.path("インポートするバンドルのパス", allow_empty=False)
        if source is None:
            return _ARG_CANCEL
        return _dispatch(devbase_root, "import", source=source,
                         **_import_default_attrs())

    # 到達しない (メニュー値は _ENV_OPS に限定される)。保守的に no-op。
    logger.error("未知の操作です: %s", op)
    return _ARG_CANCEL


def run(devbase_root: Path):
    """環境変数カテゴリのエントリ。操作選択 → 引数収集 → cmd_env へ委譲。

    戻り値プロトコル (トップループが ``is`` 同一性で判定する。actions_project と同一):
    - **操作を実行した場合**: dispatch の rc (``int``) を返す。「実行したのでトップへ
      戻る、rc は呼び出し側が記憶」の意味で、失敗が ``devbase list`` の終了コードへ
      伝搬する。
    - ``menu.MENU_BACK``: サブメニューで Esc/← (操作なしでトップへ)。
    - ``None``: Ctrl-C による全体中止。

    引数収集を中止 (``_ARG_CANCEL``) した場合はサブメニューを再表示する。
    """
    while True:
        op = _select_action()
        if op is menu.MENU_BACK:
            return menu.MENU_BACK
        if op is None:
            return None
        rc = _run_operation(devbase_root, op)
        if rc is _ARG_CANCEL:
            continue                   # 引数収集を中止 → サブメニューへ戻る
        return rc                      # 実行 rc → トップへ復帰 (呼び出し側が記憶)
