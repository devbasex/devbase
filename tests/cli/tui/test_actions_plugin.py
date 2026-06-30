"""PLAN31_2 PR4: tui.actions_plugin (plugin カテゴリ操作) のテスト。

test_actions_project.py のパターンを踏襲し、`menu.*` を monkeypatch して選択値を
注入、`cmd_plugin` を mock して **plan 2.3 の契約どおりの属性を持つ Namespace** で
呼ばれることを各サブコマンド (repo 系含む) で検証する。破壊的操作 (uninstall /
repo remove) の confirm 拒否で未実行になること、Esc/←/Ctrl-C の遷移も検証する。
"""

from __future__ import annotations

import pytest
import yaml

from devbase.tui import actions_plugin, flow, menu


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    """サブメニューは実行後に留まる。各テストの一時停止 (Enter 待ち) は無効化する。"""
    monkeypatch.setattr(flow, "pause_for_review", lambda: True)


def _seq(*values):
    """呼ばれるたび values を順に返し、尽きたら最後の値を返すコールバックを作る。

    操作メニューは実行後に再表示されるため、選択スタブは末尾に MENU_BACK を置く。
    """
    box = {"i": 0}

    def _next(*_a, **_k):
        i = box["i"]
        box["i"] = min(i + 1, len(values) - 1)
        return values[i]

    return _next


def _seed_registry(root, plugins=(), repos=()):
    """plugins.yml を生成して導入済み plugin / 登録済みリポジトリを注入する。"""
    data = {
        "repositories": [
            {"name": r, "url": f"https://github.com/o/{r}",
             "added_at": "2026-01-01T00:00:00+00:00", "plugins": []}
            for r in repos
        ],
        "installed_plugins": [
            {"name": p, "version": "1.0", "source": "o--r",
             "installed_at": "2026-01-01T00:00:00+00:00",
             "path": f"repos/o--r/{p}", "linked": False}
            for p in plugins
        ],
    }
    (root / "plugins.yml").write_text(yaml.safe_dump(data, allow_unicode=True))


def _capture_dispatch(monkeypatch):
    """cmd_plugin の呼び出し引数を全属性キャプチャするヘルパ。"""
    from devbase.commands import plugin as plugin_mod
    captured = {}

    def _spy(devbase_root, args):
        captured["devbase_root"] = devbase_root
        captured["subcommand"] = args.subcommand
        for k in ("available", "source", "link", "install_all", "name",
                  "repo_command", "url", "force"):
            if hasattr(args, k):
                captured[k] = getattr(args, k)
        return 0

    monkeypatch.setattr(plugin_mod, "cmd_plugin", _spy)
    return captured


def _no_dispatch(monkeypatch):
    """cmd_plugin が呼ばれないことを検証するためのスパイ (呼び出しを記録)。"""
    from devbase.commands import plugin as plugin_mod
    called = []
    monkeypatch.setattr(plugin_mod, "cmd_plugin",
                        lambda root, args: called.append(1) or 0)
    return called


# ---------------------------------------------------------------------------
# run(): plugin メニューのループと戻り値プロトコル
# ---------------------------------------------------------------------------

def test_run_executes_then_stays_in_submenu(monkeypatch, tmp_path):
    """操作を実行 → plugin メニューに留まり、Esc/← (MENU_BACK) で初めてトップへ戻る。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_operation", _seq("sync", menu.MENU_BACK))
    assert actions_plugin.run(tmp_path) is menu.MENU_BACK
    assert captured["subcommand"] == "sync"
    assert captured["devbase_root"] == tmp_path


def test_run_executes_nonzero_then_back(monkeypatch, tmp_path):
    """非0 を返す操作でも実行後は plugin メニューに留まり、戻りは MENU_BACK。"""
    from devbase.commands import plugin as plugin_mod
    calls = []
    monkeypatch.setattr(plugin_mod, "cmd_plugin", lambda root, args: calls.append(1) or 1)
    monkeypatch.setattr(actions_plugin, "_select_operation", _seq("sync", menu.MENU_BACK))
    assert actions_plugin.run(tmp_path) is menu.MENU_BACK
    assert calls == [1], "操作は実行される (rc は終了コードへは伝搬しない)"


def test_run_back_returns_menu_back(monkeypatch, tmp_path):
    """plugin メニューで Esc/← (MENU_BACK) を押すとトップへ戻る (何も起動しない)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_operation", lambda: menu.MENU_BACK)
    assert actions_plugin.run(tmp_path) is menu.MENU_BACK
    assert called == []


def test_run_ctrl_c_aborts(monkeypatch, tmp_path):
    """plugin メニューで Ctrl-C (None) を押すと全体中止 (None を返す)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_operation", lambda: None)
    assert actions_plugin.run(tmp_path) is None
    assert called == []


def test_run_arg_cancel_reshows_menu(monkeypatch, tmp_path):
    """引数収集を中止 (_ARG_CANCEL) すると plugin メニューを再表示し、再選択で実行する。"""
    # 1 回目: install (→ 引数収集中止) / 2 回目: sync (→ 実行) / 3 回目: MENU_BACK
    select = _seq("install", "sync", menu.MENU_BACK)
    select_calls = []
    monkeypatch.setattr(actions_plugin, "_select_operation",
                        lambda: select_calls.append(1) or select())

    run_calls = []

    def fake_run_op(root, op):
        run_calls.append(op)
        return actions_plugin._ARG_CANCEL if op == "install" else 0

    monkeypatch.setattr(actions_plugin, "_run_operation", fake_run_op)

    assert actions_plugin.run(tmp_path) is menu.MENU_BACK
    assert run_calls == ["install", "sync"]
    assert len(select_calls) == 3, "引数中止と実行後に plugin メニューが再表示される"


def test_run_repo_back_reshows_plugin_menu(monkeypatch, tmp_path):
    """repo サブ階層で Esc/← (MENU_BACK) を押すと plugin メニューへ戻る。"""
    select_calls = []
    monkeypatch.setattr(actions_plugin, "_select_operation",
                        lambda: (select_calls.append(1),
                                 "repo" if len(select_calls) == 1 else menu.MENU_BACK)[1])
    monkeypatch.setattr(actions_plugin, "_repo_menu", lambda root: menu.MENU_BACK)

    assert actions_plugin.run(tmp_path) is menu.MENU_BACK
    assert len(select_calls) == 2, "repo から戻ると plugin メニューが再表示される"


def test_run_repo_back_then_plugin_back(monkeypatch, tmp_path):
    """repo サブ階層は自身で操作を完結し、戻ると plugin メニューを再表示する。

    新仕様では repo サブ階層 (``_repo_menu``) は操作実行後もそこに留まり、戻りは
    MENU_BACK のみ (rc を上位へ伝搬しない)。plugin 側はそれを受けてメニューを
    再表示し、さらに MENU_BACK でトップへ戻る。
    """
    repo_calls = []
    monkeypatch.setattr(actions_plugin, "_select_operation", _seq("repo", menu.MENU_BACK))
    monkeypatch.setattr(actions_plugin, "_repo_menu",
                        lambda root: repo_calls.append(1) or menu.MENU_BACK)
    assert actions_plugin.run(tmp_path) is menu.MENU_BACK
    assert repo_calls == [1], "repo サブ階層へ 1 度遷移してから plugin メニューへ戻る"


def test_run_repo_ctrl_c_aborts(monkeypatch, tmp_path):
    """repo サブ階層で Ctrl-C (None) を受けたら全体中止を伝搬する。"""
    monkeypatch.setattr(actions_plugin, "_select_operation", lambda: "repo")
    monkeypatch.setattr(actions_plugin, "_repo_menu", lambda root: None)
    assert actions_plugin.run(tmp_path) is None


# ---------------------------------------------------------------------------
# _select_operation / _select_repo_operation: menu.select への委譲
# ---------------------------------------------------------------------------

def test_select_operation_lists_all_ops(monkeypatch):
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search,
                        values=[c[1] for c in choices])
        return "list"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_plugin._select_operation() == "list"
    assert captured["back"] is True
    assert captured["search"] is False
    # 閲覧系の list 2 種を先頭にしつつ全 9 操作 (repo 含む) を提示する。
    # --available は y/N で聞かず独立したメニュー項目で提供する。
    assert captured["values"] == [
        "list", "list-available", "install", "uninstall", "update", "info",
        "sync", "migrate", "repo"]


def test_select_repo_operation_lists_all_ops(monkeypatch):
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, values=[c[1] for c in choices])
        return "add"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_plugin._select_repo_operation() == "add"
    assert captured["back"] is True
    assert captured["values"] == ["list", "add", "remove", "refresh"]


# ---------------------------------------------------------------------------
# _run_operation: 各操作の引数収集 + dispatch 契約 (plan 2.3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("op,available", [("list", False), ("list-available", True)])
def test_run_operation_list_available_flag(monkeypatch, tmp_path, op, available):
    """list 系は確認プロンプトなしで即実行する (--available はメニュー項目で分岐)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("list で確認を求めない"))
    assert actions_plugin._run_operation(tmp_path, op) == 0
    assert captured["subcommand"] == "list"
    assert captured["available"] is available


def test_run_operation_install_collects_source_only(monkeypatch, tmp_path):
    """install は source のみ収集し、--link/--all は CLI 既定 (False) で実行する。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "text", lambda *a, **k: "owner/repo")
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("install で確認を求めない"))
    assert actions_plugin._run_operation(tmp_path, "install") == 0
    assert captured["subcommand"] == "install"
    assert captured["source"] == "owner/repo"
    assert captured["link"] is False and captured["install_all"] is False


@pytest.mark.parametrize("text_ret", ["BACK", None])
def test_run_operation_install_source_cancel(monkeypatch, tmp_path, text_ret):
    """source 入力で Esc は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    called = _no_dispatch(monkeypatch)
    ret = menu.MENU_BACK if text_ret == "BACK" else None
    monkeypatch.setattr(menu, "text", lambda *a, **k: ret)
    expected = actions_plugin._ARG_CANCEL if text_ret == "BACK" else None
    assert actions_plugin._run_operation(tmp_path, "install") is expected
    assert called == []


def test_run_operation_uninstall_confirmed(monkeypatch, tmp_path):
    """uninstall は一覧から選んだ name で confirm=True のとき実行する (plan 3.4)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: "ndf")
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)
    assert actions_plugin._run_operation(tmp_path, "uninstall") == 0
    assert captured["subcommand"] == "uninstall" and captured["name"] == "ndf"


@pytest.mark.parametrize("confirm_ret", [False, "BACK", None])
def test_run_operation_uninstall_cancelled_does_not_dispatch(
        monkeypatch, tmp_path, confirm_ret):
    """uninstall の confirm を拒否 (False) / Esc / Ctrl-C したら実行しない。

    拒否と Esc はサブメニュー再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。
    """
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: "ndf")
    ret = menu.MENU_BACK if confirm_ret == "BACK" else confirm_ret
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: ret)
    expected = None if confirm_ret is None else actions_plugin._ARG_CANCEL
    assert actions_plugin._run_operation(tmp_path, "uninstall") is expected
    assert called == [], "確認を拒否/中止したら uninstall しない"


def test_run_operation_uninstall_name_cancel(monkeypatch, tmp_path):
    """name 選択を中止したら confirm も dispatch もしない。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: actions_plugin._ARG_CANCEL)
    confirms = []
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: confirms.append(1) or True)
    assert actions_plugin._run_operation(tmp_path, "uninstall") is actions_plugin._ARG_CANCEL
    assert called == [] and confirms == []


def test_run_operation_update_named(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: "ndf")
    assert actions_plugin._run_operation(tmp_path, "update") == 0
    assert captured["subcommand"] == "update" and captured["name"] == "ndf"


def test_run_operation_update_all_is_name_none(monkeypatch, tmp_path):
    """「全 plugin を更新」('') は name=None で委譲する (CLI の引数省略と同じ)。"""
    captured = _capture_dispatch(monkeypatch)
    seen = {}
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: seen.update(k) or "")
    assert actions_plugin._run_operation(tmp_path, "update") == 0
    assert captured["subcommand"] == "update" and captured["name"] is None
    assert seen.get("all_label"), "update では全 plugin 選択肢を提示する"


def test_run_operation_update_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: actions_plugin._ARG_CANCEL)
    assert actions_plugin._run_operation(tmp_path, "update") is actions_plugin._ARG_CANCEL
    assert called == []


def test_run_operation_update_ctrl_c_aborts(monkeypatch, tmp_path):
    """name 選択中の Ctrl-C は None を伝搬して全体中止する (codex round2 指摘)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: None)
    assert actions_plugin._run_operation(tmp_path, "update") is None
    assert called == []


def test_run_operation_info(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: "ndf")
    assert actions_plugin._run_operation(tmp_path, "info") == 0
    assert captured["subcommand"] == "info" and captured["name"] == "ndf"


def test_run_operation_info_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: actions_plugin._ARG_CANCEL)
    assert actions_plugin._run_operation(tmp_path, "info") is actions_plugin._ARG_CANCEL
    assert called == []


@pytest.mark.parametrize("op", ["sync", "migrate"])
def test_run_operation_sync_migrate_no_attrs(monkeypatch, tmp_path, op):
    """sync/migrate は引数収集なしで即委譲する (plan 2.3: 属性なし)。"""
    captured = _capture_dispatch(monkeypatch)
    assert actions_plugin._run_operation(tmp_path, op) == 0
    assert captured == {"devbase_root": tmp_path, "subcommand": op}, \
        "subcommand 以外の属性を載せない"


def test_run_operation_unknown_is_noop(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    assert actions_plugin._run_operation(tmp_path, "bogus") is actions_plugin._ARG_CANCEL
    assert called == []


# ---------------------------------------------------------------------------
# _run_repo_operation: repo 系の引数収集 + dispatch 契約 (plan 2.3)
# ---------------------------------------------------------------------------

def test_run_repo_operation_list(monkeypatch, tmp_path):
    """repo list は repo_command='list' のみで委譲する。"""
    captured = _capture_dispatch(monkeypatch)
    assert actions_plugin._run_repo_operation(tmp_path, "list") == 0
    assert captured == {"devbase_root": tmp_path, "subcommand": "repo",
                        "repo_command": "list"}


def test_run_repo_operation_add_with_custom_name(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    texts = iter(["https://github.com/o/r", "myrepo"])  # url, name
    monkeypatch.setattr(menu, "text", lambda *a, **k: next(texts))
    assert actions_plugin._run_repo_operation(tmp_path, "add") == 0
    assert captured["subcommand"] == "repo"
    assert captured["repo_command"] == "add"
    assert captured["url"] == "https://github.com/o/r"
    assert captured["name"] == "myrepo"


def test_run_repo_operation_add_empty_name_is_none(monkeypatch, tmp_path):
    """カスタム名を空にすると name=None (URL から自動命名) で委譲する。"""
    captured = _capture_dispatch(monkeypatch)
    texts = iter(["o/r", ""])                # url, name(空)
    monkeypatch.setattr(menu, "text", lambda *a, **k: next(texts))
    assert actions_plugin._run_repo_operation(tmp_path, "add") == 0
    assert captured["url"] == "o/r" and captured["name"] is None


@pytest.mark.parametrize("text_ret", ["BACK", None])
def test_run_repo_operation_add_url_cancel(monkeypatch, tmp_path, text_ret):
    """url 入力で Esc は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    called = _no_dispatch(monkeypatch)
    ret = menu.MENU_BACK if text_ret == "BACK" else None
    monkeypatch.setattr(menu, "text", lambda *a, **k: ret)
    expected = actions_plugin._ARG_CANCEL if text_ret == "BACK" else None
    assert actions_plugin._run_repo_operation(tmp_path, "add") is expected
    assert called == []


@pytest.mark.parametrize("force", [True, False])
def test_run_repo_operation_remove_confirmed(monkeypatch, tmp_path, force):
    """repo remove は confirm=True のとき force フラグ付きで実行する (plan 3.4)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: "r1")
    confirms = iter([True, force])           # 削除確認=True, force
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: next(confirms))
    assert actions_plugin._run_repo_operation(tmp_path, "remove") == 0
    assert captured["subcommand"] == "repo"
    assert captured["repo_command"] == "remove"
    assert captured["name"] == "r1" and captured["force"] is force


@pytest.mark.parametrize("confirm_ret", [False, "BACK", None])
def test_run_repo_operation_remove_cancelled_does_not_dispatch(
        monkeypatch, tmp_path, confirm_ret):
    """repo remove の confirm を拒否 (False) / Esc / Ctrl-C したら実行しない。

    拒否と Esc はサブメニュー再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。
    """
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: "r1")
    ret = menu.MENU_BACK if confirm_ret == "BACK" else confirm_ret
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: ret)
    expected = None if confirm_ret is None else actions_plugin._ARG_CANCEL
    assert actions_plugin._run_repo_operation(tmp_path, "remove") is expected
    assert called == [], "確認を拒否/中止したら remove しない"


@pytest.mark.parametrize("confirm_ret", ["BACK", None])
def test_run_repo_operation_remove_force_cancel(monkeypatch, tmp_path, confirm_ret):
    """force の確認で Esc / Ctrl-C したら実行しない。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: "r1")
    ret = menu.MENU_BACK if confirm_ret == "BACK" else None
    confirms = iter([True, ret])             # 削除確認=True, force で中止
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: next(confirms))
    expected = actions_plugin._ARG_CANCEL if confirm_ret == "BACK" else None
    assert actions_plugin._run_repo_operation(tmp_path, "remove") is expected
    assert called == []


def test_run_repo_operation_remove_name_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: actions_plugin._ARG_CANCEL)
    assert actions_plugin._run_repo_operation(tmp_path, "remove") is actions_plugin._ARG_CANCEL
    assert called == []


def test_run_repo_operation_refresh_named(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: "r1")
    assert actions_plugin._run_repo_operation(tmp_path, "refresh") == 0
    assert captured["subcommand"] == "repo"
    assert captured["repo_command"] == "refresh" and captured["name"] == "r1"


def test_run_repo_operation_refresh_all_is_name_none(monkeypatch, tmp_path):
    """「全リポジトリを更新」('') は name=None で委譲する (CLI の引数省略と同じ)。"""
    captured = _capture_dispatch(monkeypatch)
    seen = {}
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: seen.update(k) or "")
    assert actions_plugin._run_repo_operation(tmp_path, "refresh") == 0
    assert captured["repo_command"] == "refresh" and captured["name"] is None
    assert seen.get("all_label"), "refresh では全リポジトリ選択肢を提示する"


def test_run_repo_operation_refresh_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: actions_plugin._ARG_CANCEL)
    assert actions_plugin._run_repo_operation(tmp_path, "refresh") is actions_plugin._ARG_CANCEL
    assert called == []


def test_run_repo_operation_remove_ctrl_c_aborts(monkeypatch, tmp_path):
    """リポジトリ選択中の Ctrl-C は None を伝搬して全体中止する (codex round2 指摘)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: None)
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("Ctrl-C 後に確認を求めない"))
    assert actions_plugin._run_repo_operation(tmp_path, "remove") is None
    assert called == []


# ---------------------------------------------------------------------------
# _repo_menu: サブ階層メニューのループ
# ---------------------------------------------------------------------------

def test_repo_menu_back_returns_menu_back(monkeypatch, tmp_path):
    monkeypatch.setattr(actions_plugin, "_select_repo_operation",
                        lambda: menu.MENU_BACK)
    assert actions_plugin._repo_menu(tmp_path) is menu.MENU_BACK


def test_repo_menu_ctrl_c_aborts(monkeypatch, tmp_path):
    monkeypatch.setattr(actions_plugin, "_select_repo_operation", lambda: None)
    assert actions_plugin._repo_menu(tmp_path) is None


def test_repo_menu_arg_cancel_reshows_submenu(monkeypatch, tmp_path):
    """引数収集を中止 (_ARG_CANCEL) するとサブ階層メニューを再表示し、再選択で実行する。"""
    # 1 回目: add (→ 引数収集中止) / 2 回目: list (→ 実行) / 3 回目: MENU_BACK
    select = _seq("add", "list", menu.MENU_BACK)
    select_calls = []
    monkeypatch.setattr(actions_plugin, "_select_repo_operation",
                        lambda: select_calls.append(1) or select())

    run_calls = []

    def fake_run_op(root, op):
        run_calls.append(op)
        return actions_plugin._ARG_CANCEL if op == "add" else 0

    monkeypatch.setattr(actions_plugin, "_run_repo_operation", fake_run_op)

    assert actions_plugin._repo_menu(tmp_path) is menu.MENU_BACK
    assert run_calls == ["add", "list"]
    assert len(select_calls) == 3, "引数中止と実行後にサブ階層メニューが再表示される"


def test_repo_menu_executes_then_stays(monkeypatch, tmp_path):
    """repo 操作を実行 → サブ階層に留まり、Esc/← (MENU_BACK) で plugin メニューへ。"""
    monkeypatch.setattr(actions_plugin, "_select_repo_operation",
                        _seq("list", menu.MENU_BACK))
    calls = []
    monkeypatch.setattr(actions_plugin, "_run_repo_operation",
                        lambda root, op: calls.append(op) or 1)
    assert actions_plugin._repo_menu(tmp_path) is menu.MENU_BACK
    assert calls == ["list"], "操作は実行される (rc は終了コードへは伝搬しない)"


# ---------------------------------------------------------------------------
# 名前選択ヘルパ (_select_name / _select_installed_plugin / _select_repository)
# ---------------------------------------------------------------------------

def test_select_name_lists_names(monkeypatch):
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search,
                        values=[c[1] for c in choices])
        return "b"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_plugin._select_name("選択", ["a", "b"]) == "b"
    assert captured["back"] is True
    assert captured["values"] == ["a", "b"]


def test_select_name_all_label_first_and_returns_empty(monkeypatch):
    """all_label は value='' で先頭に置き、選択時は '' を返す (呼び出し側で None へ変換)。"""
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured["values"] = [c[1] for c in choices]
        return ""

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_plugin._select_name("選択", ["a"], all_label="全対象") == ""
    assert captured["values"] == ["", "a"]


@pytest.mark.parametrize("sel", [None, "BACK"])
def test_select_name_back_or_ctrl_c(monkeypatch, sel):
    """Esc/← (MENU_BACK) は _ARG_CANCEL、Ctrl-C (None) は None (全体中止) を返す。"""
    ret = menu.MENU_BACK if sel == "BACK" else None
    monkeypatch.setattr(menu, "select", lambda *a, **k: ret)
    expected = actions_plugin._ARG_CANCEL if sel == "BACK" else None
    assert actions_plugin._select_name("選択", ["a"]) is expected


def test_select_installed_plugin_reads_registry(monkeypatch, tmp_path):
    """plugins.yml の導入済み plugin が選択肢に並ぶ (registry 結合)。"""
    _seed_registry(tmp_path, plugins=("ndf", "carmo"))
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured["values"] = [c[1] for c in choices]
        return "ndf"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_plugin._select_installed_plugin(tmp_path, "選択") == "ndf"
    assert captured["values"] == ["ndf", "carmo"]


def test_select_installed_plugin_empty_is_cancel(monkeypatch, tmp_path):
    """導入済み plugin が無ければ選択メニューを出さず中止する。"""
    selects = []
    monkeypatch.setattr(menu, "select", lambda *a, **k: selects.append(1) or None)
    assert actions_plugin._select_installed_plugin(tmp_path, "選択") \
        is actions_plugin._ARG_CANCEL
    assert selects == []


def test_select_repository_reads_registry(monkeypatch, tmp_path):
    """plugins.yml の登録済みリポジトリが選択肢に並ぶ (registry 結合)。"""
    _seed_registry(tmp_path, repos=("r1", "r2"))
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured["values"] = [c[1] for c in choices]
        return "r2"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_plugin._select_repository(tmp_path, "選択") == "r2"
    assert captured["values"] == ["r1", "r2"]


def test_select_repository_empty_is_cancel(monkeypatch, tmp_path):
    selects = []
    monkeypatch.setattr(menu, "select", lambda *a, **k: selects.append(1) or None)
    assert actions_plugin._select_repository(tmp_path, "選択") \
        is actions_plugin._ARG_CANCEL
    assert selects == []
