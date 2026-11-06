"""plugin カテゴリの TUI 操作フロー (PLAN31_2 PR4)。

``devbase plugin`` の全サブコマンド (list/install/uninstall/update/info/sync/migrate/new)、
``plugin repo`` のサブコマンド (new/add/remove/list/refresh) と ``project new`` を、
用途で分けた 2 階層のメニューから実行できるようにする (#514)。
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

# 第 1 階層は用途で分ける (表示順 = ハイライト既定順)。閲覧だけの「見る」を先頭に置き、
# Enter 連打で安全な一覧表示へ到達できるようにする。
_PLUGIN_GROUPS: list[tuple[str, str]] = [
    ("見る", "view"),
    ("新規追加", "new"),
    ("導入・更新", "install-update"),
    ("削除", "remove"),
    ("保守", "maintain"),
]

# 第 2 階層。value は ``_OP_HANDLERS`` のキー。--available は y/N で聞かず独立した
# メニュー項目にする (非破壊操作の確認プロンプト廃止)。
_GROUP_OPS: dict[str, list[tuple[str, str]]] = {
    "view": [
        ("導入済み一覧 (list)", "list"),
        ("利用可能一覧 (list --available)", "list-available"),
        ("詳細表示 (info)", "info"),
        ("リポジトリ一覧 (repo list)", "repo-list"),
    ],
    "new": [
        ("プロジェクト (project new)", "new-project"),
        ("プラグイン (plugin new)", "new-plugin"),
        ("リポジトリ (plugin repo new)", "new-repo"),
    ],
    "install-update": [
        ("インストール (install)", "install"),
        ("更新 (update)", "update"),
        ("リポジトリ登録 (repo add)", "repo-add"),
        ("リポジトリ更新 (repo refresh)", "repo-refresh"),
    ],
    "remove": [
        ("アンインストール (uninstall)", "uninstall"),
        ("リポジトリ削除 (repo remove)", "repo-remove"),
    ],
    "maintain": [
        ("プロジェクトリンク再同期 (sync)", "sync"),
        ("レガシー構成の移行 (migrate)", "migrate"),
    ],
}

_NEW_REPOSITORY_PROMPT = "GitHub のリポジトリ (URL か owner/repo)"

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
    """第 1 階層 (用途) を選ぶメニュー。

    戻り値: 用途の値 / ``MENU_BACK`` (Esc・← → トップへ戻る) / ``None`` (Ctrl-C 中止)。
    """
    return menu.select(f"plugin 操作を選択 {menu.HINT_BACK}:",
                       list(_PLUGIN_GROUPS), back=True, search=False)


def _select_group_operation(group: str):
    """第 2 階層 (用途の中の操作) を選ぶメニュー。

    戻り値: 操作の値 / ``MENU_BACK`` (Esc・← → 第 1 階層へ戻る) / ``None`` (Ctrl-C 中止)。
    """
    label = next(text for text, value in _PLUGIN_GROUPS if value == group)
    return menu.select(f"{label}: 操作を選択 {menu.HINT_BACK}:",
                       list(_GROUP_OPS[group]), back=True, search=False)


# ---------------------------------------------------------------------------
# 各操作の引数収集 + dispatch (plan 2.3 契約)
# ---------------------------------------------------------------------------

def _op_install(devbase_root: Path):
    # --link / --all は CLI 既定 (False) で実行する。指定が必要な場合は CLI
    # (`plugin install --link/--all`) を使う想定 (非破壊操作の確認プロンプト廃止)。
    source = flow.need(menu.text(
        "インストールする plugin の source (名前 / URL / パス)", allow_empty=False))
    return _dispatch(devbase_root, "install",
                     source=source, link=False, install_all=False)


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
# 新規追加: 値を集めて計画を確かめ、CLI のハンドラへ --yes 相当で渡す (#514 の決定 5)
# ---------------------------------------------------------------------------

def _creation_context(devbase_root: Path):
    from devbase.env import runtime as _runtime
    from devbase.plugin.github import GhClient
    from devbase.plugin.registry import PluginRegistry

    # TUI は起動時の注入が載ったまま動く。gh は利用者の設定で動かす (#514 の決定 11)
    _runtime.clear_injected()
    return PluginRegistry(Path(devbase_root)), GhClient()


def _confirm_plan(plan) -> None:
    """作成の計画を示して確認する。拒否 / Esc なら ``BackOut`` で戻る。"""
    print(plan.describe())
    flow.confirm_or_back("この計画で進めますか?")


def _op_new_repo(devbase_root: Path):
    from devbase.plugin import creator

    repo = flow.need(menu.text(_NEW_REPOSITORY_PROMPT, allow_empty=False))
    public = flow.need(menu.confirm(
        "新しく作るときに公開しますか (--public)?", default=False))
    registry, gh = _creation_context(devbase_root)
    try:
        plan = creator.plan_repo(registry, gh, repo, public=public)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1
    if not plan.noop_reason:
        _confirm_plan(plan)
    return _dispatch(devbase_root, "repo", repo_command="new",
                     repo=repo, public=public, yes=True)


def _op_new_plugin(devbase_root: Path):
    from devbase.plugin import creator

    repo = flow.need(menu.text(_NEW_REPOSITORY_PROMPT, allow_empty=False))
    name = flow.need(menu.text("プラグインの名前 (空でリポジトリ名)", allow_empty=True)) or None
    registry, gh = _creation_context(devbase_root)
    try:
        plan = creator.plan_plugin(registry, gh, repo, name=name)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1
    _confirm_plan(plan)
    return _dispatch(devbase_root, "new", repo=repo, name=name, public=False, yes=True)


def _select_target(candidates):
    """入れ先の候補から 1 件選ばせる。候補が 1 件なら選択を出さない。"""
    if len(candidates) == 1:
        return candidates[0].plugin
    choices = [(c.label, c.plugin) for c in candidates]
    return flow.back_as_cancel(menu.select(
        f"入れ先のプラグインを選択 {menu.HINT_BACK}:", choices, back=True, search=False))


def _op_new_project(devbase_root: Path):
    from devbase.commands.project import cmd_project_new
    from devbase.plugin import creator
    from devbase.plugin.github import GitHubRef

    repo = flow.need(menu.text(_NEW_REPOSITORY_PROMPT, allow_empty=False))
    name = flow.need(menu.text("プロジェクトの名前 (空でリポジトリ名)", allow_empty=True)) or None
    registry, gh = _creation_context(devbase_root)
    try:
        ref = GitHubRef.parse(repo)
        gh.ensure_ready()
        candidates = creator.plugin_candidates(registry, gh, ref)
        if not candidates:
            logger.error("入れ先のプラグインがありません。push できるプラグインを導入してください")
            return 1
        plugin = flow.need(_select_target(candidates))
        target = next(c for c in candidates if c.plugin == plugin)
        group = flow.need(menu.text("アカウントグループ",
                                    default=creator.suggest_group(registry, target),
                                    allow_empty=False))
        plan = creator.plan_project(registry, gh, repo, plugin=plugin, group=group, name=name)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1
    _confirm_plan(plan)
    return dispatch_group(cmd_project_new, devbase_root, "new", repo=repo, name=name,
                          plugin=plugin, group=group, yes=True)


_OP_HANDLERS = {
    # list 系は引数収集なしで即実行 (--available はメニュー項目で分岐)。
    "list": lambda root: _dispatch(root, "list", available=False),
    "list-available": lambda root: _dispatch(root, "list", available=True),
    "install": _op_install,
    "uninstall": _op_uninstall,
    "update": _op_update,
    "info": _op_info,
    # sync / migrate は引数なし (plan 2.3: 属性なし)。即実行。
    "sync": lambda root: _dispatch(root, "sync"),
    "migrate": lambda root: _dispatch(root, "migrate"),
    "new-project": _op_new_project,
    "new-plugin": _op_new_plugin,
    "new-repo": _op_new_repo,
    "repo-list": lambda root: _run_repo_operation(root, "list"),
    "repo-add": lambda root: _run_repo_operation(root, "add"),
    "repo-remove": lambda root: _run_repo_operation(root, "remove"),
    "repo-refresh": lambda root: _run_repo_operation(root, "refresh"),
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
        # 到達しない (メニュー値は _GROUP_OPS に限定される)。保守的に no-op。
        logger.error("未知の操作です: %s", op)
        raise flow.BackOut
    return handler(devbase_root)


# ---------------------------------------------------------------------------
# メニューループ
# ---------------------------------------------------------------------------

def _group_menu(devbase_root: Path, group: str):
    """用途のメニューを回す。

    戻り値 (``flow.menu_loop`` のプロトコル): ``menu.MENU_BACK`` (Esc・← で第 1 階層へ
    戻る) / ``None`` (Ctrl-C 全体中止)。操作の実行後は (出力確認の一時停止後) 用途の
    メニューに留まり、引数収集を中止 (``_ARG_CANCEL``) した場合も再表示する。
    """
    return flow.menu_loop(lambda: _select_group_operation(group),
                          lambda op: _run_operation(devbase_root, op))


def run(devbase_root: Path):
    """プラグイン操作カテゴリ。用途 → 操作 → 引数収集 → ``cmd_plugin`` へ委譲。

    戻り値プロトコル (トップループが ``is`` 同一性で判定する。actions_project と同じ):
    - ``menu.MENU_BACK``: 第 1 階層で Esc/← (トップメニューへ戻る)。
    - ``None``: Ctrl-C による全体中止。

    用途のメニューは自身で操作を完結し、Esc/← (``MENU_BACK``) で戻ったら第 1 階層を
    再表示する (``_ARG_CANCEL`` 相当に読み替える)。
    """
    def _run(group):
        rc = _group_menu(devbase_root, group)
        return _ARG_CANCEL if rc is menu.MENU_BACK else rc

    return flow.menu_loop(_select_operation, _run)
