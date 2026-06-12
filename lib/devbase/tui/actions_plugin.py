"""plugin カテゴリの TUI 操作フロー (PLAN31_2 PR4)。

``devbase plugin`` の全サブコマンド (list/install/uninstall/update/info/sync/migrate)
と ``plugin repo`` のサブ階層 (add/remove/list/refresh) を TUI から実行できるようにする。
引数は ``tui.menu`` の収集ヘルパで CLI parser と同じ属性値 (plan 2.3 契約表) を集め、
``tui.dispatch.dispatch_group`` 経由で既存ハンドラ ``cmd_plugin`` へ委譲する
(ロジック二重実装なし)。

uninstall/update/info および repo remove/refresh の ``name`` は、registry
(``plugins.yml``) から取得した導入済み plugin / 登録済みリポジトリの一覧から
選択させる (自由入力によるタイプミスを防ぐ)。破壊的な uninstall / repo remove は
実行前に確認する (plan 3.4)。

中止系の伝搬 (Ctrl-C / Esc / ``_ARG_CANCEL``) は ``tui.flow`` のナビ規約に従う。
"""

from __future__ import annotations

from pathlib import Path

from devbase.errors import DevbaseError
from devbase.log import get_logger
from devbase.tui import flow, menu
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

# 中止系番兵は flow と同一オブジェクトを再公開する (呼び出し側・テストの契約)。
_ARG_CANCEL = flow.ARG_CANCEL


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

def _registry_names(devbase_root: Path, lister: str) -> list[str]:
    """registry (plugins.yml) から名前一覧を取得する。

    ``lister`` は ``PluginRegistry`` の一覧メソッド名 (``list_installed`` /
    ``list_repositories``)。取得に失敗したら案内して空リストを返す。
    """
    from devbase.plugin.registry import PluginRegistry

    try:
        registry = PluginRegistry(Path(devbase_root))
        return [item.name for item in getattr(registry, lister)()]
    except DevbaseError as e:
        logger.error("%s", e)
        return []


def _select_name(message: str, names: list[str], *,
                 all_label: str | None = None, empty_hint: str = "対象がありません。"):
    """名前一覧から 1 件選ばせる共通ヘルパ。対象が無ければ案内して ``_ARG_CANCEL``。

    ``all_label`` 指定時は「全対象」(value="") を先頭に置く。選択メニューの ``None``
    (Ctrl-C → 全体中止) と衝突させないため空文字を番兵にし、``None`` への変換は
    呼び出し側で行う (_select_build_image と同じ流儀)。

    戻り値: 名前 (``str``) / ``""`` (all_label 選択 = 全対象。呼び出し側で ``None``
    へ変換) / ``None`` (Ctrl-C → 全体中止を呼び出し元へ伝搬) / ``_ARG_CANCEL``
    (Esc・← → サブメニューへ戻る、または対象が 1 件もない)。
    """
    if not names:
        logger.info("%s", empty_hint)
        return _ARG_CANCEL
    choices = ([(all_label, "")] if all_label is not None else [])
    choices += [(n, n) for n in names]
    return flow.back_as_cancel(menu.select(
        f"{message} {menu.HINT_BACK}:", choices, back=True, search=False))


def _select_installed_plugin(devbase_root: Path, message: str, *,
                             all_label: str | None = None):
    """導入済み plugin から 1 件選ばせる。対象が無ければ案内して ``_ARG_CANCEL``。"""
    return _select_name(
        message, _registry_names(devbase_root, "list_installed"),
        all_label=all_label,
        empty_hint="導入済みの plugin がありません。`plugin install` で導入してください。")


def _select_repository(devbase_root: Path, message: str, *,
                       all_label: str | None = None):
    """登録済みリポジトリから 1 件選ばせる。対象が無ければ案内して ``_ARG_CANCEL``。"""
    return _select_name(
        message, _registry_names(devbase_root, "list_repositories"),
        all_label=all_label,
        empty_hint="登録済みのリポジトリがありません。`plugin repo add` で登録してください。")


# ---------------------------------------------------------------------------
# サブメニュー
# ---------------------------------------------------------------------------

def _select_operation():
    """plugin 操作を選ぶサブメニュー。

    戻り値: サブコマンド文字列 / ``MENU_BACK`` (Esc・← → トップへ戻る) / ``None``
    (Ctrl-C 中止)。
    """
    return menu.select(f"plugin 操作を選択 {menu.HINT_BACK}:",
                       list(_PLUGIN_OPS), back=True, search=False)


def _select_repo_operation():
    """plugin repo 操作を選ぶサブ階層メニュー。

    戻り値: repo_command 文字列 / ``MENU_BACK`` (Esc・← → plugin メニューへ戻る) /
    ``None`` (Ctrl-C 中止)。
    """
    return menu.select(f"リポジトリ操作を選択 {menu.HINT_BACK}:",
                       list(_REPO_OPS), back=True, search=False)


# ---------------------------------------------------------------------------
# 各操作の引数収集 + dispatch (plan 2.3 契約)
# ---------------------------------------------------------------------------

def _op_list(devbase_root: Path):
    # --available: 導入済み一覧の代わりに未導入の利用可能 plugin を表示する。
    available = flow.need(menu.confirm(
        "未導入の利用可能 plugin を表示しますか (--available)?", default=False))
    return _dispatch(devbase_root, "list", available=available)


def _op_install(devbase_root: Path):
    source = flow.need(menu.text(
        "インストールする plugin の source (名前 / URL / パス)", allow_empty=False))
    link = flow.need(menu.confirm(
        "symlink としてインストールしますか (--link)?", default=False))
    install_all = flow.need(menu.confirm(
        "リポジトリ内の全 plugin をインストールしますか (--all)?", default=False))
    return _dispatch(devbase_root, "install",
                     source=source, link=link, install_all=install_all)


def _op_uninstall(devbase_root: Path):
    name = flow.need(_select_installed_plugin(
        devbase_root, "アンインストールする plugin を選択"))
    flow.confirm_or_back(f"plugin '{name}' をアンインストールしますか?")
    return _dispatch(devbase_root, "uninstall", name=name)


def _op_update(devbase_root: Path):
    # name=None で全 plugin 更新 (CLI の `plugin update` 引数省略と同じ)。
    name = flow.need(_select_installed_plugin(
        devbase_root, "更新する plugin を選択", all_label="全 plugin を更新"))
    return _dispatch(devbase_root, "update", name=name or None)


def _op_info(devbase_root: Path):
    name = flow.need(_select_installed_plugin(
        devbase_root, "詳細を表示する plugin を選択"))
    return _dispatch(devbase_root, "info", name=name)


_OP_HANDLERS = {
    "list": _op_list,
    "install": _op_install,
    "uninstall": _op_uninstall,
    "update": _op_update,
    "info": _op_info,
    # sync / migrate は引数なし (plan 2.3: 属性なし)。即実行。
    "sync": lambda root: _dispatch(root, "sync"),
    "migrate": lambda root: _dispatch(root, "migrate"),
}


@flow.collect_args
def _run_operation(devbase_root: Path, op: str):
    """選択された plugin 操作の引数を収集して ``cmd_plugin`` へ委譲する。

    戻り値: dispatch の rc (``int``) / ``_ARG_CANCEL`` (Esc・確認拒否で引数収集を
    中止 = サブメニューへ戻る) / ``None`` (選択・入力中の Ctrl-C → 全体中止)。
    破壊的な uninstall は実行前に確認する (plan 3.4)。
    """
    handler = _OP_HANDLERS.get(op)
    if handler is None:
        # 到達しない (メニュー値は _PLUGIN_OPS に限定される)。保守的に no-op。
        logger.error("未知の操作です: %s", op)
        raise flow.BackOut
    return handler(devbase_root)


def _op_repo_add(devbase_root: Path):
    url = flow.need(menu.text(
        "登録するリポジトリの URL (GitHub は owner/repo 短縮形も可)",
        allow_empty=False))
    # --name は任意 (空で URL から自動命名)。空文字は None へ変換して渡す。
    name = flow.need(menu.text("カスタム名 (--name 空で自動)", allow_empty=True))
    return _dispatch(devbase_root, "repo",
                     repo_command="add", url=url, name=name or None)


def _op_repo_remove(devbase_root: Path):
    name = flow.need(_select_repository(devbase_root, "削除するリポジトリを選択"))
    flow.confirm_or_back(f"リポジトリ '{name}' を削除しますか?")
    force = flow.need(menu.confirm(
        "未 commit / 未 push の変更があっても強制削除しますか (--force)?",
        default=False))
    return _dispatch(devbase_root, "repo",
                     repo_command="remove", name=name, force=force)


def _op_repo_refresh(devbase_root: Path):
    # name=None で全リポジトリを refresh (CLI の引数省略と同じ)。
    name = flow.need(_select_repository(
        devbase_root, "更新するリポジトリを選択", all_label="全リポジトリを更新"))
    return _dispatch(devbase_root, "repo",
                     repo_command="refresh", name=name or None)


_REPO_HANDLERS = {
    "list": lambda root: _dispatch(root, "repo", repo_command="list"),
    "add": _op_repo_add,
    "remove": _op_repo_remove,
    "refresh": _op_repo_refresh,
}


@flow.collect_args
def _run_repo_operation(devbase_root: Path, op: str):
    """選択された plugin repo 操作の引数を収集して ``cmd_plugin`` へ委譲する。

    repo 系は ``subcommand='repo'`` + ``repo_command=<op>`` の二段属性で
    ``cmd_repo`` へ分岐する (plan 2.3 契約)。戻り値プロトコルは ``_run_operation``
    と同じ。破壊的な remove は実行前に確認する (plan 3.4)。
    """
    handler = _REPO_HANDLERS.get(op)
    if handler is None:
        # 到達しない (メニュー値は _REPO_OPS に限定される)。保守的に no-op。
        logger.error("未知のリポジトリ操作です: %s", op)
        raise flow.BackOut
    return handler(devbase_root)


# ---------------------------------------------------------------------------
# メニューループ
# ---------------------------------------------------------------------------

def _repo_menu(devbase_root: Path):
    """plugin repo のサブ階層メニューを回す。

    戻り値 (``flow.menu_loop`` のプロトコル): dispatch の rc (``int``) /
    ``menu.MENU_BACK`` (Esc・← で plugin メニューへ戻る) / ``None`` (Ctrl-C 全体中止)。
    引数収集を中止 (``_ARG_CANCEL``) した場合はサブ階層メニューを再表示する。
    """
    return flow.menu_loop(_select_repo_operation,
                          lambda op: _run_repo_operation(devbase_root, op))


def run(devbase_root: Path):
    """プラグイン操作カテゴリ。操作選択 → 引数収集 → ``cmd_plugin`` へ委譲。

    戻り値プロトコル (トップループが ``is`` 同一性で判定する。actions_project と同じ):
    - **操作を実行した場合**: dispatch の rc (``int``) を返す。失敗 (非0) は
      ``devbase list`` の終了コードへ伝搬する。
    - ``menu.MENU_BACK``: 操作なしでトップメニューへ戻る (Esc/←)。
    - ``None``: Ctrl-C による全体中止。

    repo はサブ階層メニュー (``_repo_menu``) へ分岐し、Esc/← で plugin メニューへ
    戻れる (``MENU_BACK`` を ``_ARG_CANCEL`` 相当に読み替えて再表示する)。
    操作完了後はトップメニューへ復帰する (plan 3.5 状態遷移: Exec → Top)。
    """
    def _run(op):
        if op == "repo":
            rc = _repo_menu(devbase_root)
            # repo 階層から Esc で戻ったら plugin メニューを再表示する。
            return _ARG_CANCEL if rc is menu.MENU_BACK else rc
        return _run_operation(devbase_root, op)

    return flow.menu_loop(_select_operation, _run)
