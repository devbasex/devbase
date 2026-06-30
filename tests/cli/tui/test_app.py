"""PLAN31_2 PR1: tui.app (トップ階層メニュー & 入口 run) のテスト。

旧 commands/project.py の cmd_project_list (非 TTY / questionary 不在フォールバック)
の非回帰検証を tui.app へ移送し、トップ階層メニューの routing を追加検証する。
"""

from __future__ import annotations

import types

import pytest

from devbase.tui import actions_plugin, actions_project, app, menu


def _make_plugin_project(root, plugin_path, proj):
    target = root / plugin_path / "projects" / proj
    target.mkdir(parents=True, exist_ok=True)


def _link_project(root, link_name, plugin_path, proj):
    from pathlib import Path
    projects_dir = root / "projects"
    projects_dir.mkdir(exist_ok=True)
    (projects_dir / link_name).symlink_to(Path("..") / plugin_path / "projects" / proj)


def _seed(root, monkeypatch, status=None):
    from devbase.commands import status as status_mod
    _make_plugin_project(root, "repos/o--r/alpha", "alpha-proj")
    _link_project(root, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: status)


# ---------------------------------------------------------------------------
# run(): 非対話 / 非 TTY → table フォールバック
# ---------------------------------------------------------------------------

def test_run_non_interactive_prints_table(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch, status={"name": "x", "status": "stopped", "count": 0})
    rc = app.run(tmp_path, types.SimpleNamespace(interactive=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "alpha-proj" in out and "NAME" in out


def test_run_stdin_non_tty_falls_back_to_table(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch, status={"name": "x", "status": "stopped", "count": 0})
    monkeypatch.setattr(app.sys.stdin, "isatty", lambda: False)
    called = []
    monkeypatch.setattr(app, "_top_menu_loop", lambda root: called.append(1) or 0)

    rc = app.run(tmp_path, types.SimpleNamespace(interactive=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert called == [], "非 TTY ではトップメニューを開かない"
    assert "alpha-proj" in out


def test_run_stdout_non_tty_falls_back_to_table(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch, status={"name": "x", "status": "stopped", "count": 0})
    monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(app.sys.stdout, "isatty", lambda: False)
    called = []
    monkeypatch.setattr(app, "_top_menu_loop", lambda root: called.append(1) or 0)

    rc = app.run(tmp_path, types.SimpleNamespace(interactive=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert called == []
    assert "alpha-proj" in out


def test_run_non_interactive_empty(tmp_path, monkeypatch, capsys):
    from devbase.commands import status as status_mod
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)
    rc = app.run(tmp_path, types.SimpleNamespace(interactive=False))
    assert rc == 0


# ---------------------------------------------------------------------------
# run(): questionary 不在 → 番号入力フォールバック (project up)
# ---------------------------------------------------------------------------

def test_run_no_questionary_falls_back_to_number_input(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, status=None)
    monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(app.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)

    called = {}
    monkeypatch.setattr(actions_project, "fallback_select_and_up",
                        lambda rows: called.update(n=len(rows)) or 0)
    top_called = []
    monkeypatch.setattr(app, "_top_menu_loop", lambda root: top_called.append(1) or 0)

    rc = app.run(tmp_path, types.SimpleNamespace(interactive=True))
    assert rc == 0
    assert called == {"n": 1}, "questionary 不在時は番号入力フォールバックへ"
    assert top_called == [], "トップメニューは開かない"


def test_run_no_questionary_empty_projects(tmp_path, monkeypatch):
    from devbase.commands import status as status_mod
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)
    monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(app.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)

    called = []
    monkeypatch.setattr(actions_project, "fallback_select_and_up",
                        lambda rows: called.append(1) or 0)
    rc = app.run(tmp_path, types.SimpleNamespace(interactive=True))
    assert rc == 0
    assert called == [], "プロジェクトが無ければフォールバックも呼ばない"


# ---------------------------------------------------------------------------
# run(): questionary 利用可 → トップ階層メニュー
# ---------------------------------------------------------------------------

def test_run_interactive_opens_top_menu(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, status=None)
    monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(app.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", True)

    called = {}
    monkeypatch.setattr(app, "_top_menu_loop", lambda root: called.update(root=root) or 0)
    rc = app.run(tmp_path, types.SimpleNamespace(interactive=True))
    assert rc == 0
    assert called["root"] == tmp_path


# ---------------------------------------------------------------------------
# トップ画面: プロジェクト一覧 + カテゴリ項目
# ---------------------------------------------------------------------------

_ROWS = [{"name": "carmo", "plugin": "p", "status": "stopped"},
         {"name": "beta", "plugin": "q", "status": "running (1 containers)"}]


def _patch_loop(monkeypatch, selects, rows=None):
    """_top_menu_loop の入力 (一覧と選択値) を注入する共通ヘルパ。

    操作実行後の Enter 待ち (`_pause_for_review`) は即継続にスタブし、
    呼び出し回数を返す (pause 自体の挙動は専用テストで検証する)。
    """
    monkeypatch.setattr(app, "list_projects",
                        lambda projects_dir: list(_ROWS) if rows is None else rows)
    it = iter(selects)
    monkeypatch.setattr(app, "_select_top", lambda r, default=None: next(it))
    pauses = []
    monkeypatch.setattr(app, "_pause_for_review", lambda: pauses.append(1) or True)
    return pauses


def test_select_top_projects_in_list_categories_in_menubar(monkeypatch):
    """トップは一覧にプロジェクト行のみ、カテゴリは最下部メニューバーに並ぶ。"""
    captured = {}

    def fake_menubar(message, choices, menu_items, default=None):
        captured.update(values=[c[1] for c in choices],
                        menu_labels=[m[0] for m in menu_items],
                        menu_values=[m[1] for m in menu_items])
        return 0

    monkeypatch.setattr(menu, "select_with_menubar", fake_menubar)
    assert app._select_top(_ROWS) == 0
    assert captured["values"] == [0, 1], "一覧はプロジェクトの rows index のみ"
    assert captured["menu_values"] == ["env", "plugin", "snapshot", "status"]
    assert captured["menu_labels"] == [
        "環境変数", "プラグイン", "スナップショット", "ステータス"]


def test_select_top_empty_projects_uses_placeholder(monkeypatch):
    """プロジェクト 0 件は選択不能エラーを避けるためプレースホルダ行を 1 件置く。

    questionary の select は選択可能な choice が 0 件だと構築できない。
    """
    captured = {}

    def fake_menubar(message, choices, menu_items, default=None):
        captured.update(titles=[c[0] for c in choices],
                        values=[c[1] for c in choices])
        return captured["values"][0]

    monkeypatch.setattr(menu, "select_with_menubar", fake_menubar)
    assert app._select_top([]) is app._NO_PROJECTS
    assert captured["values"] == [app._NO_PROJECTS]
    assert "プロジェクトがありません" in captured["titles"][0]


def test_top_loop_no_projects_placeholder_redisplays(monkeypatch, tmp_path):
    """プレースホルダ行 (_NO_PROJECTS) を Enter しても何も起動せず再表示する。"""
    _patch_loop(monkeypatch, [app._NO_PROJECTS, None], rows=[])
    handled = []
    monkeypatch.setattr(actions_project, "handle_row",
                        lambda root, row: handled.append(1) or 0)

    assert app._top_menu_loop(tmp_path) == 0
    assert handled == [], "プレースホルダでは何も起動しない"


def test_top_loop_project_selection_delegates_handle_row(monkeypatch, tmp_path):
    """プロジェクト選択 (int) は actions_project.handle_row へ該当行を渡す。"""
    _patch_loop(monkeypatch, [1, None])
    handled = []
    monkeypatch.setattr(actions_project, "handle_row",
                        lambda root, row: handled.append((root, row["name"])) or 0)

    rc = app._top_menu_loop(tmp_path)
    assert rc == 0
    assert handled == [(tmp_path, "beta")], "選択 index の行が handle_row へ渡る"


def test_top_loop_restores_cursor_to_selected_project(monkeypatch, tmp_path):
    """プロジェクト選択 → サブメニューから戻ると同じ行 (index) へカーソルを復元する。"""
    monkeypatch.setattr(app, "list_projects", lambda projects_dir: list(_ROWS))
    monkeypatch.setattr(app, "_pause_for_review", lambda: True)
    # サブメニュー (handle_row) は MENU_BACK で一覧へ戻る。
    monkeypatch.setattr(actions_project, "handle_row", lambda root, row: menu.MENU_BACK)

    defaults = []
    selects = iter([1, None])    # index 1 を選択 → 戻り → トップで終了
    monkeypatch.setattr(app, "_select_top",
                        lambda r, default=None: defaults.append(default) or next(selects))

    assert app._top_menu_loop(tmp_path) == 0
    assert defaults == [None, 1], "初回は先頭、戻った後は選択した index=1 を既定にする"


def test_top_loop_propagates_executed_rc(monkeypatch, tmp_path):
    """操作実行で非0 rc が返ると、その後トップで中止しても rc がループ戻り値へ伝搬する。"""
    _patch_loop(monkeypatch, [0, None])
    monkeypatch.setattr(actions_project, "handle_row", lambda root, row: 1)

    assert app._top_menu_loop(tmp_path) == 1


def test_top_loop_back_does_not_overwrite_last_rc(monkeypatch, tmp_path):
    """実行 rc を記憶後、カテゴリが MENU_BACK を返しても last_rc は上書きされない。"""
    _patch_loop(monkeypatch, [0, "snapshot", None])
    monkeypatch.setattr(actions_project, "handle_row", lambda root, row: 1)
    from devbase.tui import actions_snapshot
    monkeypatch.setattr(actions_snapshot, "run", lambda root: menu.MENU_BACK)

    assert app._top_menu_loop(tmp_path) == 1


def test_top_loop_zero_rc_propagates(monkeypatch, tmp_path):
    """rc=0 が int として正しく扱われる (None/MENU_BACK と誤マッチしない)。"""
    _patch_loop(monkeypatch, [0, None])
    monkeypatch.setattr(actions_project, "handle_row", lambda root, row: 0)

    assert app._top_menu_loop(tmp_path) == 0


def test_top_loop_escape_exits(monkeypatch, tmp_path):
    """トップ (一覧) で Esc/Ctrl-C (None) を押すと即終了 (rc=0)。"""
    _patch_loop(monkeypatch, [None])
    handled = []
    monkeypatch.setattr(actions_project, "handle_row",
                        lambda root, row: handled.append(1) or 0)

    assert app._top_menu_loop(tmp_path) == 0
    assert handled == []


def test_top_loop_category_ctrl_c_aborts_whole_app(monkeypatch, tmp_path):
    """カテゴリ内で Ctrl-C (None) を受けたら全体中止する。"""
    _patch_loop(monkeypatch, ["env"])
    from devbase.tui import actions_env
    monkeypatch.setattr(actions_env, "run", lambda root: None)  # Ctrl-C

    assert app._top_menu_loop(tmp_path) == 0


def test_top_loop_category_back_redisplays_list(monkeypatch, tmp_path):
    """カテゴリが操作なし (MENU_BACK) で戻ったら一覧を再表示する。"""
    _patch_loop(monkeypatch, ["snapshot", None])
    from devbase.tui import actions_snapshot
    monkeypatch.setattr(actions_snapshot, "run", lambda root: menu.MENU_BACK)

    assert app._top_menu_loop(tmp_path) == 0


def test_top_loop_empty_projects_still_offers_categories(monkeypatch, tmp_path):
    """プロジェクト 0 件でも終了せず、カテゴリ操作 (status 等) が選べる。"""
    _patch_loop(monkeypatch, ["status", None], rows=[])
    from devbase.tui import actions_status
    ran = []
    monkeypatch.setattr(actions_status, "run", lambda root: ran.append(1) or 0)

    assert app._top_menu_loop(tmp_path) == 0
    assert ran == [1], "プロジェクト無しでもカテゴリへ遷移できる"


# ---------------------------------------------------------------------------
# 操作実行後の Enter 待ち (_pause_for_review): 出力が流れる前に読めるようにする
# ---------------------------------------------------------------------------

def test_top_loop_pauses_after_execution(monkeypatch, tmp_path):
    """操作を実行したら一覧の再表示前に Enter を待つ (出力を読めるようにする)。"""
    pauses = _patch_loop(monkeypatch, ["plugin", None])
    from devbase.tui import actions_plugin
    monkeypatch.setattr(actions_plugin, "run", lambda root: 0)

    assert app._top_menu_loop(tmp_path) == 0
    assert pauses == [1], "実行後は一覧再表示の前に Enter を待つ"


def test_top_loop_no_pause_on_menu_back(monkeypatch, tmp_path):
    """操作なし (MENU_BACK) で戻ったときは Enter を待たない (出力がないため)。"""
    pauses = _patch_loop(monkeypatch, ["plugin", None])
    from devbase.tui import actions_plugin
    monkeypatch.setattr(actions_plugin, "run", lambda root: menu.MENU_BACK)

    assert app._top_menu_loop(tmp_path) == 0
    assert pauses == [], "MENU_BACK では Enter を待たない"


def test_top_loop_pause_ctrl_c_exits_with_last_rc(monkeypatch, tmp_path):
    """Enter 待ちで Ctrl-C (False) を受けたら直近の実行 rc で全体中止する。"""
    _patch_loop(monkeypatch, ["plugin"])
    from devbase.tui import actions_plugin
    monkeypatch.setattr(actions_plugin, "run", lambda root: 1)
    monkeypatch.setattr(app, "_pause_for_review", lambda: False)

    assert app._top_menu_loop(tmp_path) == 1


def test_pause_for_review_enter_returns_true(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert app._pause_for_review() is True


def test_pause_for_review_ctrl_c_returns_false(monkeypatch):
    def _interrupt(*a):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _interrupt)
    assert app._pause_for_review() is False


@pytest.mark.parametrize("exc", [EOFError, OSError])
def test_pause_for_review_unreadable_stdin_returns_true(monkeypatch, exc):
    """非 TTY 等で stdin を読めない場合は待たずに一覧へ戻る (ハングしない)。"""
    def _unreadable(*a):
        raise exc

    monkeypatch.setattr("builtins.input", _unreadable)
    assert app._pause_for_review() is True


def test_route_plugin_delegates(monkeypatch, tmp_path):
    """PR4: plugin カテゴリは actions_plugin.run へ routing される。"""
    monkeypatch.setattr(actions_plugin, "run", lambda root: "RESULT")
    assert app._route("plugin", tmp_path) == "RESULT"


def test_route_env_delegates(monkeypatch, tmp_path):
    """env カテゴリは actions_env.run へ routing される (PR3)。"""
    from devbase.tui import actions_env
    monkeypatch.setattr(actions_env, "run", lambda root: "ENV_RESULT")
    assert app._route("env", tmp_path) == "ENV_RESULT"


def test_route_unknown_category_returns_menu_back(tmp_path):
    # 全カテゴリ配線済みのため、未知カテゴリへの防御的 fallback (MENU_BACK) を検証。
    assert app._route("unknown", tmp_path) is menu.MENU_BACK


def test_route_snapshot_delegates(monkeypatch, tmp_path):
    """PR5: snapshot カテゴリは actions_snapshot.run へ配線される。"""
    from devbase.tui import actions_snapshot
    monkeypatch.setattr(actions_snapshot, "run", lambda root: "SNAP")
    assert app._route("snapshot", tmp_path) == "SNAP"


def test_route_status_delegates(monkeypatch, tmp_path):
    """PR5: status カテゴリは actions_status.run へ配線される。"""
    from devbase.tui import actions_status
    monkeypatch.setattr(actions_status, "run", lambda root: "STATUS")
    assert app._route("status", tmp_path) == "STATUS"
