"""plugin カテゴリの TUI 操作フロー (PLAN31_2 PR4)。

``devbase plugin`` の全サブコマンド (list/install/uninstall/update/info/sync/migrate)
と ``plugin repo`` のサブ階層 (add/remove/list/refresh) を TUI から実行できるようにする。
引数は ``tui.menu`` の収集ヘルパで CLI parser と同じ属性値 (plan 2.3 契約表) を集め、
``tui.dispatch.dispatch_group`` 経由で既存ハンドラ ``cmd_plugin`` へ委譲する
(ロジック二重実装なし)。

uninstall/update/info および repo remove/refresh の ``name`` は、registry
(``plugins.yml``) から取得した導入済み plugin / 登録済みリポジトリの一覧から
選択させる (自由入力によるタイプミスを防ぐ)。破壊的な uninstall / repo remove は
``menu.confirm`` で実行前確認する (plan 3.4)。

ナビ規約 (actions_project と同一):
- Esc / ← = 1 つ前のメニューへ戻る (``menu.MENU_BACK``)
- Ctrl-C = 全体中止 (``None`` を伝搬)
- 引数収集の中止 (``_ARG_CANCEL``) = 直前のサブメニューを再表示
"""

from __future__ import annotations

from pathlib import Path

from devbase.errors import DevbaseError
from devbase.log import get_logger
from devbase.tui import menu
from devbase.tui.dispatch import dispatch_group

logger = get_logger(__name__)

# plugin サブコマンド (表示順 = ハイライト既定順)。閲覧系の list を先頭に置き、
# Enter 連打で安全な一覧表示へ到達できるようにする。value は cmd_plugin の subcommand 名
# (repo のみサブ階層メニューへの分岐)。
_PLUGIN_OPS: list[tuple[str, str]] = [
    ("一覧表示 (list)", "list"),
    ("インストール (install)", "install"),
    ("アンインストール (uninstall)", "uninstall"),
    ("更新 (update)", "update"),
    ("詳細表示 (info)", "info"),
    ("プロジェクトリンク再同期 (sync)", "sync"),
    ("レガシー構成の移行 (migrate)", "migrate"),
    ("リポジトリ管理 (repo)", "repo"),
]

# plugin repo サブ階層 (表示順 = ハイライト既定順)。value は repo_command 名。
_REPO_OPS: list[tuple[str, str]] = [
    ("リポジトリ一覧 (list)", "list"),
    ("リポジトリ登録 (add)", "add"),
    ("リポジトリ削除 (remove)", "remove"),
    ("リポジトリ更新 (refresh)", "refresh"),
]

# 引数収集を Esc/Ctrl-C で中止したことを示す番兵 (= サブメニューへ戻る)。
# dispatch の rc (int) や ``None`` (= 全体中止) と区別する (actions_project と同じ)。
_ARG_CANCEL = object()


def _dispatch(devbase_root: Path, subcommand: str, **attrs) -> int:
    """``cmd_plugin`` へ委譲する (plan 2.3 の属性契約は呼び出し側が守る)。

    import を呼び出し時まで遅延させ、テストが ``commands.plugin.cmd_plugin`` を
    monkeypatch で差し替えられるようにする (dispatch_lifecycle と同じ流儀)。
    """
    from devbase.commands.plugin import cmd_plugin

    return dispatch_group(cmd_plugin, devbase_root, subcommand, **attrs)


# ---------------------------------------------------------------------------
# 名前選択 (registry から一覧を取得して選ばせる)
# ---------------------------------------------------------------------------

def _installed_plugin_names(devbase_root: Path) -> list[str]:
    """導入済み plugin 名の一覧を registry (plugins.yml) から取得する。"""
    from devbase.plugin.registry import PluginRegistry

    try:
        return [p.name for p in PluginRegistry(Path(devbase_root)).list_installed()]
    except DevbaseError as e:
        logger.error("%s", e)
        return []


def _repository_names(devbase_root: Path) -> list[str]:
    """登録済みリポジトリ名の一覧を registry (plugins.yml) から取得する。"""
    from devbase.plugin.registry import PluginRegistry

    try:
        return [r.name for r in PluginRegistry(Path(devbase_root)).list_repositories()]
    except DevbaseError as e:
        logger.error("%s", e)
        return []


def _select_name(message: str, names: list[str], *, all_label: str | None = None):
    """名前一覧から 1 件選ばせる共通ヘルパ。

    ``all_label`` 指定時は「全対象」(value="") を先頭に置く。選択メニューの ``None``
    (Ctrl-C → 全体中止) と衝突させないため空文字を番兵にし、``None`` への変換は
    呼び出し側で行う (_select_build_image と同じ流儀)。

    戻り値: 名前 (``str``) / ``""`` (all_label 選択 = 全対象。呼び出し側で ``None``
    へ変換) / ``None`` (Ctrl-C → 全体中止を呼び出し元へ伝搬) / ``_ARG_CANCEL``
    (Esc・← → サブメニューへ戻る)。
    """
    choices: list[tuple[str, str]] = []
    if all_label is not None:
        choices.append((all_label, ""))
    choices += [(n, n) for n in names]
    sel = menu.select(
        f"{message} (↑↓ 移動 / Enter 決定 / ←・Esc 戻る / Ctrl-C 中止):",
        choices, back=True, search=False)
    if sel is None:
        return None                    # Ctrl-C → 全体中止 (ナビ規約)
    if sel is menu.MENU_BACK:
        return _ARG_CANCEL             # Esc/← → サブメニューを再表示
    return sel                         # "" = 全対象 (呼び出し側で None へ変換)


def _select_installed_plugin(devbase_root: Path, message: str, *,
                             all_label: str | None = None):
    """導入済み plugin から 1 件選ばせる。対象が無ければ案内して ``_ARG_CANCEL``。"""
    names = _installed_plugin_names(devbase_root)
    if not names:
        logger.info("導入済みの plugin がありません。"
                    "`plugin install` で導入してください。")
        return _ARG_CANCEL
    return _select_name(message, names, all_label=all_label)


def _select_repository(devbase_root: Path, message: str, *,
                       all_label: str | None = None):
    """登録済みリポジトリから 1 件選ばせる。対象が無ければ案内して ``_ARG_CANCEL``。"""
    names = _repository_names(devbase_root)
    if not names:
        logger.info("登録済みのリポジトリがありません。"
                    "`plugin repo add` で登録してください。")
        return _ARG_CANCEL
    return _select_name(message, names, all_label=all_label)


# ---------------------------------------------------------------------------
# サブメニュー
# ---------------------------------------------------------------------------

def _select_operation():
    """plugin 操作を選ぶサブメニュー。

    戻り値: サブコマンド文字列 / ``MENU_BACK`` (Esc・← → トップへ戻る) / ``None``
    (Ctrl-C 中止)。
    """
    return menu.select(
        "plugin 操作を選択 (↑↓ 移動 / Enter 決定 / ←・Esc 戻る / Ctrl-C 中止):",
        list(_PLUGIN_OPS), back=True, search=False)


def _select_repo_operation():
    """plugin repo 操作を選ぶサブ階層メニュー。

    戻り値: repo_command 文字列 / ``MENU_BACK`` (Esc・← → plugin メニューへ戻る) /
    ``None`` (Ctrl-C 中止)。
    """
    return menu.select(
        "リポジトリ操作を選択 (↑↓ 移動 / Enter 決定 / ←・Esc 戻る / Ctrl-C 中止):",
        list(_REPO_OPS), back=True, search=False)


# ---------------------------------------------------------------------------
# 各操作の引数収集 + dispatch (plan 2.3 契約)
# ---------------------------------------------------------------------------

def _run_operation(devbase_root: Path, op: str):
    """選択された plugin 操作の引数を収集して ``cmd_plugin`` へ委譲する。

    戻り値: dispatch の rc (``int``) / ``_ARG_CANCEL`` (引数収集を中止 =
    サブメニューへ戻る) / ``None`` (選択メニューで Ctrl-C → 全体中止)。
    破壊的な uninstall は ``menu.confirm`` で確認する (plan 3.4)。
    """
    if op == "list":
        # --available: 導入済み一覧の代わりに未導入の利用可能 plugin を表示する。
        available = menu.confirm(
            "未導入の利用可能 plugin を表示しますか (--available)?", default=False)
        if available is None:
            return _ARG_CANCEL
        return _dispatch(devbase_root, "list", available=available)

    if op == "install":
        source = menu.text(
            "インストールする plugin の source (名前 / URL / パス)",
            allow_empty=False)
        if source is None:
            return _ARG_CANCEL
        link = menu.confirm(
            "symlink としてインストールしますか (--link)?", default=False)
        if link is None:
            return _ARG_CANCEL
        install_all = menu.confirm(
            "リポジトリ内の全 plugin をインストールしますか (--all)?", default=False)
        if install_all is None:
            return _ARG_CANCEL
        return _dispatch(devbase_root, "install",
                         source=source, link=link, install_all=install_all)

    if op == "uninstall":
        name = _select_installed_plugin(
            devbase_root, "アンインストールする plugin を選択")
        if name is None:
            return None                # Ctrl-C → 全体中止
        if name is _ARG_CANCEL:
            return _ARG_CANCEL
        ok = menu.confirm(f"plugin '{name}' をアンインストールしますか?", default=False)
        if not ok:                     # False (拒否) / None (中止) → 実行しない
            return _ARG_CANCEL
        return _dispatch(devbase_root, "uninstall", name=name)

    if op == "update":
        # name=None で全 plugin 更新 (CLI の `plugin update` 引数省略と同じ)。
        name = _select_installed_plugin(
            devbase_root, "更新する plugin を選択",
            all_label="全 plugin を更新")
        if name is None:
            return None                # Ctrl-C → 全体中止
        if name is _ARG_CANCEL:
            return _ARG_CANCEL
        return _dispatch(devbase_root, "update", name=name or None)

    if op == "info":
        name = _select_installed_plugin(
            devbase_root, "詳細を表示する plugin を選択")
        if name is None:
            return None                # Ctrl-C → 全体中止
        if name is _ARG_CANCEL:
            return _ARG_CANCEL
        return _dispatch(devbase_root, "info", name=name)

    if op in ("sync", "migrate"):
        # 引数なし (plan 2.3: sync/migrate は属性なし)。即実行。
        return _dispatch(devbase_root, op)

    # 到達しない (メニュー値は _PLUGIN_OPS に限定される)。保守的に no-op。
    logger.error("未知の操作です: %s", op)
    return _ARG_CANCEL


def _run_repo_operation(devbase_root: Path, op: str):
    """選択された plugin repo 操作の引数を収集して ``cmd_plugin`` へ委譲する。

    repo 系は ``subcommand='repo'`` + ``repo_command=<op>`` の二段属性で
    ``cmd_repo`` へ分岐する (plan 2.3 契約)。戻り値プロトコルは ``_run_operation``
    と同じ。破壊的な remove は ``menu.confirm`` で確認する (plan 3.4)。
    """
    if op == "list":
        return _dispatch(devbase_root, "repo", repo_command="list")

    if op == "add":
        url = menu.text(
            "登録するリポジトリの URL (GitHub は owner/repo 短縮形も可)",
            allow_empty=False)
        if url is None:
            return _ARG_CANCEL
        # --name は任意 (空で URL から自動命名)。空文字は None へ変換して渡す。
        name = menu.text("カスタム名 (--name 空で自動)", allow_empty=True)
        if name is None:
            return _ARG_CANCEL
        return _dispatch(devbase_root, "repo",
                         repo_command="add", url=url, name=name or None)

    if op == "remove":
        name = _select_repository(devbase_root, "削除するリポジトリを選択")
        if name is None:
            return None                # Ctrl-C → 全体中止
        if name is _ARG_CANCEL:
            return _ARG_CANCEL
        ok = menu.confirm(f"リポジトリ '{name}' を削除しますか?", default=False)
        if not ok:                     # False (拒否) / None (中止) → 実行しない
            return _ARG_CANCEL
        force = menu.confirm(
            "未 commit / 未 push の変更があっても強制削除しますか (--force)?",
            default=False)
        if force is None:
            return _ARG_CANCEL
        return _dispatch(devbase_root, "repo",
                         repo_command="remove", name=name, force=force)

    if op == "refresh":
        # name=None で全リポジトリを refresh (CLI の引数省略と同じ)。
        name = _select_repository(
            devbase_root, "更新するリポジトリを選択",
            all_label="全リポジトリを更新")
        if name is None:
            return None                # Ctrl-C → 全体中止
        if name is _ARG_CANCEL:
            return _ARG_CANCEL
        return _dispatch(devbase_root, "repo",
                         repo_command="refresh", name=name or None)

    # 到達しない (メニュー値は _REPO_OPS に限定される)。保守的に no-op。
    logger.error("未知のリポジトリ操作です: %s", op)
    return _ARG_CANCEL


# ---------------------------------------------------------------------------
# メニューループ
# ---------------------------------------------------------------------------

def _repo_menu(devbase_root: Path):
    """plugin repo のサブ階層メニューを回す。

    戻り値プロトコル (``is`` 同一性判定):
    - dispatch の rc (``int``): 操作を実行 → 呼び出し元へ (最終的にトップへ復帰)。
    - ``menu.MENU_BACK``: Esc/← で plugin メニューへ戻る。
    - ``None``: Ctrl-C で全体中止。

    引数収集を中止 (``_ARG_CANCEL``) した場合はサブ階層メニューを再表示する。
    """
    while True:
        op = _select_repo_operation()
        if op is menu.MENU_BACK:
            return menu.MENU_BACK
        if op is None:
            return None
        rc = _run_repo_operation(devbase_root, op)
        if rc is _ARG_CANCEL:
            continue                   # 引数収集を中止 → サブ階層メニューへ戻る
        return rc                      # 実行 rc → 呼び出し元へ


def run(devbase_root: Path):
    """プラグイン操作カテゴリ。操作選択 → 引数収集 → ``cmd_plugin`` へ委譲。

    戻り値プロトコル (トップループが ``is`` 同一性で判定する。actions_project と同じ):
    - **操作を実行した場合**: dispatch の rc (``int``) を返す。失敗 (非0) は
      ``devbase list`` の終了コードへ伝搬する。
    - ``menu.MENU_BACK``: 操作なしでトップメニューへ戻る (Esc/←)。
    - ``None``: Ctrl-C による全体中止。

    repo はサブ階層メニュー (``_repo_menu``) へ分岐し、Esc/← で plugin メニューへ
    戻れる。引数収集を中止した場合は plugin メニューを再表示する。
    操作完了後はトップメニューへ復帰する (plan 3.5 状態遷移: Exec → Top)。
    """
    while True:
        op = _select_operation()
        if op is menu.MENU_BACK:
            return menu.MENU_BACK
        if op is None:
            return None

        if op == "repo":
            rc = _repo_menu(devbase_root)
            if rc is menu.MENU_BACK:
                continue              # plugin メニューへ戻る
            return rc                 # 実行 rc (int) / None (Ctrl-C) を伝搬
        rc = _run_operation(devbase_root, op)
        if rc is _ARG_CANCEL:
            continue                  # 引数収集を中止 → plugin メニューへ戻る

        # 操作完了 → トップメニューへ復帰。rc は呼び出し側 (top loop) が記憶し
        # 最終的な devbase の終了コードへ伝搬させる。
        return rc
