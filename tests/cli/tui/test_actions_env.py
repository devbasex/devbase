"""tui.actions_env (env カテゴリ操作) のテスト (PLAN31_2 PR3 → メニュー再構成)。

test_actions_project.py のパターンを踏襲し、`menu.*` を monkeypatch して選択値を
注入、`cmd_env` を mock して契約どおりの属性を持つ Namespace で呼ばれることを
検証する。TUI は参照・対話系 (グローバル一覧 / edit / sync / project / init) のみ
提供し、プロジェクト単位の一覧と get/set/delete/export/import は CLI 専用
(メニューに出さない)。project スコープ操作の chdir → 復帰、Esc/←/Ctrl-C の
遷移も検証する。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from devbase.tui import actions_env, flow, menu


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    """サブメニューは実行後に留まる。各テストの一時停止 (Enter 待ち) は無効化する。"""
    monkeypatch.setattr(flow, "pause_for_review", lambda: True)


def _seq(*values):
    """呼ばれるたび values を順に返し、尽きたら最後の値を返すコールバックを作る。

    サブメニューは操作実行後に再表示されるため、選択スタブは「操作 → … →
    MENU_BACK」のように最後に MENU_BACK を置いてループを終わらせる。
    """
    box = {"i": 0}

    def _next(*_a, **_k):
        i = box["i"]
        box["i"] = min(i + 1, len(values) - 1)
        return values[i]

    return _next


def _make_plugin_project(root, plugin_path, proj):
    target = root / plugin_path / "projects" / proj
    target.mkdir(parents=True, exist_ok=True)
    return target


def _link_project(root, link_name, plugin_path, proj):
    projects_dir = root / "projects"
    projects_dir.mkdir(exist_ok=True)
    (projects_dir / link_name).symlink_to(Path("..") / plugin_path / "projects" / proj)


def _capture_dispatch(monkeypatch):
    """cmd_env の呼び出しを (root, 全属性, 実行時 CWD/PWD) でキャプチャするヘルパ。"""
    from devbase.commands import env as env_mod
    captured = {}

    def _spy(devbase_root, args):
        captured["root"] = devbase_root
        captured["attrs"] = dict(vars(args))
        captured["cwd"] = os.getcwd()
        captured["pwd"] = os.environ.get("PWD")
        return 0

    monkeypatch.setattr(env_mod, "cmd_env", _spy)
    return captured


# ---------------------------------------------------------------------------
# run(): 操作選択 → 実行 / Esc / Ctrl-C / 引数収集中止
# ---------------------------------------------------------------------------

def test_run_executes_and_stays_in_submenu(monkeypatch, tmp_path):
    """操作を選んで実行 → サブメニューに留まり、Esc/← で初めてトップへ戻る。"""
    captured = _capture_dispatch(monkeypatch)
    # sync を実行 → サブメニュー再表示 → MENU_BACK でトップへ。
    monkeypatch.setattr(actions_env, "_select_action", _seq("sync", menu.MENU_BACK))

    assert actions_env.run(tmp_path) is menu.MENU_BACK
    assert captured["root"] == tmp_path
    assert captured["attrs"] == {"subcommand": "sync"}


def test_run_executes_then_back_runs_operation(monkeypatch, tmp_path):
    """非0 を返す操作でも実行後はサブメニューに留まり、戻りは MENU_BACK。"""
    from devbase.commands import env as env_mod
    calls = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: calls.append(1) or 1)
    monkeypatch.setattr(actions_env, "_select_action", _seq("sync", menu.MENU_BACK))

    assert actions_env.run(tmp_path) is menu.MENU_BACK
    assert calls == [1], "操作は実行される (rc は終了コードへは伝搬しない)"


def test_run_back_returns_to_top(monkeypatch, tmp_path):
    """サブメニューで Esc/← (MENU_BACK) を押すとトップへ戻る (何も実行しない)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(actions_env, "_select_action", lambda: menu.MENU_BACK)

    assert actions_env.run(tmp_path) is menu.MENU_BACK
    assert called == []


def test_run_ctrl_c_aborts(monkeypatch, tmp_path):
    """サブメニューで Ctrl-C (None) を押すと全体中止 (None を返す)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(actions_env, "_select_action", lambda: None)

    assert actions_env.run(tmp_path) is None
    assert called == []


def test_run_arg_cancel_reshows_submenu(monkeypatch, tmp_path):
    """引数収集を中止 (_ARG_CANCEL) するとサブメニューを再表示し、再選択で実行する。"""
    # 1 回目: project (→ 引数収集中止) / 2 回目: sync (→ 実行) / 3 回目: MENU_BACK
    select = _seq("project", "sync", menu.MENU_BACK)
    select_calls = []
    monkeypatch.setattr(actions_env, "_select_action",
                        lambda: select_calls.append(1) or select())

    run_calls = []

    def fake_run_op(root, op):
        run_calls.append(op)
        return actions_env._ARG_CANCEL if op == "project" else 0

    monkeypatch.setattr(actions_env, "_run_operation", fake_run_op)

    assert actions_env.run(tmp_path) is menu.MENU_BACK
    assert run_calls == ["project", "sync"]
    assert len(select_calls) == 3, "引数中止と実行後にサブメニューが再表示される"


def test_run_propagates_ctrl_c_from_operation(monkeypatch, tmp_path):
    """引数収集中の Ctrl-C (None) はサブメニューを再表示せず全体中止を伝搬する。"""
    select_calls = []
    monkeypatch.setattr(actions_env, "_select_action",
                        lambda: select_calls.append(1) or "project")
    monkeypatch.setattr(actions_env, "_run_operation", lambda root, op: None)

    assert actions_env.run(tmp_path) is None
    assert len(select_calls) == 1, "Ctrl-C でサブメニューを再表示しない"


# ---------------------------------------------------------------------------
# _select_action: menu.select への委譲 (参照・対話系のみの提示)
# ---------------------------------------------------------------------------

def test_select_action_lists_all_ops(monkeypatch):
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search,
                        values=[c[1] for c in choices])
        return "list-global"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_env._select_action() == "list-global"
    assert captured["back"] is True
    assert captured["search"] is False
    # 参照系のグローバル一覧を先頭に、参照・対話系のみを提示する (メニュー再構成)。
    # プロジェクト単位の一覧と get/set/delete/export/import は CLI 専用で
    # メニューに出さない。
    assert captured["values"] == [
        "list-global", "edit", "sync", "project", "init"]
    assert captured["values"][0] == "list-global", "Enter 連打で安全な一覧表示に到達できる"


# ---------------------------------------------------------------------------
# _run_operation: 引数なし系 (sync / edit / init)
# ---------------------------------------------------------------------------

def test_run_operation_sync_no_attrs(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    assert actions_env._run_operation(tmp_path, "sync") == 0
    assert captured["attrs"] == {"subcommand": "sync"}


def test_run_operation_edit_is_global_no_project_select(monkeypatch, tmp_path):
    """edit は $DEVBASE_ROOT/.env を開くグローバル操作。プロジェクト選択も chdir もしない。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_env, "_select_project",
                        lambda root: pytest.fail("edit でプロジェクト選択してはいけない"))

    before = os.getcwd()
    assert actions_env._run_operation(tmp_path, "edit") == 0
    assert captured["attrs"] == {"subcommand": "edit"}
    assert captured["cwd"] == before, "edit は chdir しない"


def test_run_operation_init_runs_without_confirm(monkeypatch, tmp_path):
    """init は確認プロンプトなしで reset=False (CLI 既定) のまま即実行する。

    セットアップ済みの環境では cmd_env_init が案内を出して安全に終了する。
    やり直しは CLI (`env init --reset`) を使う想定。
    """
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("init で確認を求めない"))
    assert actions_env._run_operation(tmp_path, "init") == 0
    assert captured["attrs"] == {"subcommand": "init", "reset": False}


# ---------------------------------------------------------------------------
# _run_operation: list-global (中間プロンプトなしの即実行)
# ---------------------------------------------------------------------------

def test_run_operation_list_global_no_prompts_no_chdir(monkeypatch, tmp_path):
    """「変数一覧 (グローバル)」は中間プロンプトなしで --global 相当を即実行する。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "select",
                        lambda *a, **k: pytest.fail("グローバル一覧で選択を求めない"))
    monkeypatch.setattr(actions_env, "_select_project",
                        lambda root: pytest.fail("グローバル一覧でプロジェクト選択しない"))

    before = os.getcwd()
    assert actions_env._run_operation(tmp_path, "list-global") == 0
    assert captured["attrs"] == {
        "subcommand": "list",
        "global_only": True, "project_only": False,
        "reveal": False, "keys_only": False,
    }
    assert captured["cwd"] == before, "グローバル一覧は chdir しない"


# ---------------------------------------------------------------------------
# _run_operation: project (chdir + 復帰)
# ---------------------------------------------------------------------------

def test_run_operation_project_chdirs_and_restores(monkeypatch, tmp_path):
    """env project は対象プロジェクトへ chdir + PWD 切替後に実行し、復帰する (plan 3.3)。"""
    captured = _capture_dispatch(monkeypatch)
    target = tmp_path / "projects" / "carmo"
    target.mkdir(parents=True)
    monkeypatch.setattr(actions_env, "_select_project", lambda root: "carmo")
    monkeypatch.setenv("PWD", str(tmp_path))

    before = os.getcwd()
    assert actions_env._run_operation(tmp_path, "project") == 0
    assert captured["attrs"] == {"subcommand": "project"}
    assert captured["cwd"] == str(target)
    assert captured["pwd"] == str(target)
    assert os.getcwd() == before
    assert os.environ["PWD"] == str(tmp_path)


def test_run_operation_project_select_cancel(monkeypatch, tmp_path):
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(actions_env, "_select_project",
                        lambda root: actions_env._ARG_CANCEL)
    assert actions_env._run_operation(tmp_path, "project") is actions_env._ARG_CANCEL
    assert called == []


def test_run_operation_project_select_ctrl_c_aborts(monkeypatch, tmp_path):
    """プロジェクト選択中の Ctrl-C は None を伝搬して全体中止する (codex round2 指摘)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(actions_env, "_select_project", lambda root: None)
    assert actions_env._run_operation(tmp_path, "project") is None
    assert called == []


def test_run_in_project_restores_cwd_on_exception(monkeypatch, tmp_path):
    """ハンドラが例外を投げても CWD / PWD は復帰する (try/finally)。"""
    target = tmp_path / "projects" / "carmo"
    target.mkdir(parents=True)
    monkeypatch.setenv("PWD", "/original/pwd")

    def _boom():
        raise RuntimeError("handler failed")

    before = os.getcwd()
    with pytest.raises(RuntimeError):
        actions_env._run_in_project(tmp_path, "carmo", _boom)
    assert os.getcwd() == before
    assert os.environ["PWD"] == "/original/pwd"


def test_run_in_project_restores_unset_pwd(monkeypatch, tmp_path):
    """元の環境に PWD が無い場合は復帰時に PWD を残さない。"""
    target = tmp_path / "projects" / "carmo"
    target.mkdir(parents=True)
    monkeypatch.delenv("PWD", raising=False)

    seen = {}

    def _probe():
        seen["pwd"] = os.environ.get("PWD")
        return 0

    assert actions_env._run_in_project(tmp_path, "carmo", _probe) == 0
    assert seen["pwd"] == str(target)
    assert "PWD" not in os.environ


def test_run_in_project_missing_dir_cancels(monkeypatch, tmp_path):
    """対象ディレクトリへ移動できない場合は実行せず _ARG_CANCEL (メニューへ戻る)。"""
    called = []
    result = actions_env._run_in_project(tmp_path, "ghost",
                                         lambda: called.append(1) or 0)
    assert result is actions_env._ARG_CANCEL
    assert called == []


# ---------------------------------------------------------------------------
# _select_project
# ---------------------------------------------------------------------------

def test_select_project_returns_name(monkeypatch, tmp_path):
    """一覧 (actions_project と同じ取得方法) から選んだ行の name を返す。"""
    from devbase.commands import status as status_mod
    _make_plugin_project(tmp_path, "repos/o--r/p", "carmo")
    _link_project(tmp_path, "carmo", "repos/o--r/p", "carmo")
    monkeypatch.setattr(status_mod, "_container_status_for",
                        lambda entry, counts=None: None)

    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search, n=len(choices))
        return 0

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_env._select_project(tmp_path) == "carmo"
    assert captured == {"back": True, "search": True, "n": 1}


@pytest.mark.parametrize("sel_ret", ["BACK", None])
def test_select_project_cancel(monkeypatch, tmp_path, sel_ret):
    """Esc (MENU_BACK) は _ARG_CANCEL、Ctrl-C (None) は None (全体中止) を返す。"""
    from devbase.commands import status as status_mod
    _make_plugin_project(tmp_path, "repos/o--r/p", "carmo")
    _link_project(tmp_path, "carmo", "repos/o--r/p", "carmo")
    monkeypatch.setattr(status_mod, "_container_status_for",
                        lambda entry, counts=None: None)
    ret = menu.MENU_BACK if sel_ret == "BACK" else None
    monkeypatch.setattr(menu, "select", lambda *a, **k: ret)
    expected = actions_env._ARG_CANCEL if sel_ret == "BACK" else None
    assert actions_env._select_project(tmp_path) is expected


def test_select_project_empty_cancels(monkeypatch, tmp_path):
    """プロジェクトが無いときは選択メニューを出さず _ARG_CANCEL。"""
    monkeypatch.setattr(menu, "select",
                        lambda *a, **k: pytest.fail("空一覧でメニューを出さない"))
    assert actions_env._select_project(tmp_path) is actions_env._ARG_CANCEL
