"""PLAN31_2 PR1: tui.actions_project (project カテゴリ操作) のテスト。

旧 commands/project.py の _tui_select_and_up / 番号入力フォールバックの非回帰検証を
tui.actions_project へ移送したもの。`menu.select` を monkeypatch して選択値を注入する。
"""

from __future__ import annotations

import pytest

from devbase.tui import actions_project, menu


# ---------------------------------------------------------------------------
# handle_row(): 選択行の処理 (running → 操作サブメニュー / 他は直接 up)
# ---------------------------------------------------------------------------

def _row(status):
    return {"name": "carmo", "plugin": "p", "status": status}


@pytest.mark.parametrize("action", ["up", "rebuild"])
def test_handle_row_running_shows_action_menu(monkeypatch, tmp_path, action):
    """running 行はサブメニューで操作を選び、引数不要の up/rebuild は即起動する。"""
    from devbase.commands import container as container_mod

    seen = {}
    monkeypatch.setattr(actions_project, "_select_action",
                        lambda name: seen.update(name=name) or action)
    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(
                            subcommand=args.subcommand, name=args.name) or 0)

    result = actions_project.handle_row(tmp_path, _row("running (2 containers)"))
    assert result == 0                       # 操作完了 → dispatch の rc を返す
    assert seen["name"] == "carmo"
    assert captured == {"subcommand": action, "name": "carmo"}


def test_handle_row_propagates_nonzero_dispatch_rc(monkeypatch, tmp_path):
    """dispatch が非0 (失敗) を返したら handle_row もその rc を返す (終了コード伝搬)。"""
    from devbase.commands import container as container_mod

    monkeypatch.setattr(actions_project, "_select_action", lambda name: "up")
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: 1)

    assert actions_project.handle_row(tmp_path, _row("running (1 containers)")) == 1


@pytest.mark.parametrize("status", ["stopped", "unknown"])
def test_handle_row_non_running_direct_up(monkeypatch, tmp_path, status):
    """非 running 行はサブメニューを出さず直接 up する (PR1 非回帰)。"""
    from devbase.commands import container as container_mod

    action_calls = []
    monkeypatch.setattr(actions_project, "_select_action",
                        lambda name: action_calls.append(name) or "down")
    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(
                            subcommand=args.subcommand, name=args.name) or 0)

    result = actions_project.handle_row(tmp_path, _row(status))
    assert result == 0                       # 直接 up の rc を返す
    assert action_calls == [], "非 running ではサブメニューを出さない"
    assert captured == {"subcommand": "up", "name": "carmo"}


def test_handle_row_action_menu_back_returns_menu_back(monkeypatch, tmp_path):
    """running 行のサブメニューで Esc/← (MENU_BACK) → 一覧へ戻る (何も起動しない)。"""
    from devbase.commands import container as container_mod

    monkeypatch.setattr(actions_project, "_select_action", lambda name: menu.MENU_BACK)
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)

    assert actions_project.handle_row(
        tmp_path, _row("running (1 containers)")) is menu.MENU_BACK
    assert called == []


def test_handle_row_action_menu_ctrl_c_aborts(monkeypatch, tmp_path):
    """running 行のサブメニューで Ctrl-C (None) → 全体中止 (None を返す)。"""
    from devbase.commands import container as container_mod

    monkeypatch.setattr(actions_project, "_select_action", lambda name: None)
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)

    assert actions_project.handle_row(tmp_path, _row("running (1 containers)")) is None
    assert called == []


# ---------------------------------------------------------------------------
# _select_action: menu.select への委譲
# ---------------------------------------------------------------------------

def test_select_action_lists_all_ops(monkeypatch):
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search,
                        values=[c[1] for c in choices])
        return "logs"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_project._select_action("carmo") == "logs"
    assert captured["back"] is True
    assert captured["search"] is False
    # up を先頭にしつつ全8操作を提示する (PR2)。
    assert captured["values"] == [
        "up", "down", "login", "ps", "logs", "scale", "build", "rebuild"]
    assert captured["values"][0] == "up", "Enter 連打で up に到達できる"


# ---------------------------------------------------------------------------
# _run_operation: 各操作の引数収集 + dispatch 契約 (plan 2.3)
# ---------------------------------------------------------------------------

def _capture_dispatch(monkeypatch):
    """cmd_project の呼び出し引数を全属性キャプチャするヘルパ。"""
    from devbase.commands import container as container_mod
    captured = {}

    def _spy(args):
        captured["subcommand"] = args.subcommand
        captured["name"] = args.name
        for k in ("scale", "index", "all", "follow", "tail", "new_scale", "image"):
            if hasattr(args, k):
                captured[k] = getattr(args, k)
        return 0

    monkeypatch.setattr(container_mod, "cmd_project", _spy)
    return captured


def test_run_operation_up_passes_scale_none(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    assert actions_project._run_operation(tmp_path, "carmo", "up") == 0
    assert captured["subcommand"] == "up" and captured["scale"] is None


def test_run_operation_rebuild(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    assert actions_project._run_operation(tmp_path, "carmo", "rebuild") == 0
    assert captured["subcommand"] == "rebuild" and captured["name"] == "carmo"


def test_run_operation_down_runs_without_confirm(monkeypatch, tmp_path):
    """down は確認プロンプトなしで即実行する (volume 保持・up で復旧可能)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("down で確認を求めない"))
    assert actions_project._run_operation(tmp_path, "carmo", "down") == 0
    assert captured["subcommand"] == "down"


def test_run_operation_login_collects_index(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    # menu.integer で正の整数を保証し、index は文字列契約のため str 化して渡す。
    monkeypatch.setattr(menu, "integer", lambda *a, **k: 3)
    assert actions_project._run_operation(tmp_path, "carmo", "login") == 0
    assert captured["subcommand"] == "login" and captured["index"] == "3"


@pytest.mark.parametrize("int_ret", ["BACK", None])
def test_run_operation_login_cancel(monkeypatch, tmp_path, int_ret):
    """番号入力で Esc は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    from devbase.commands import container as container_mod
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)
    ret = menu.MENU_BACK if int_ret == "BACK" else None
    monkeypatch.setattr(menu, "integer", lambda *a, **k: ret)
    expected = actions_project._ARG_CANCEL if int_ret == "BACK" else None
    assert actions_project._run_operation(tmp_path, "carmo", "login") is expected
    assert called == []


def test_run_operation_ps_runs_without_confirm(monkeypatch, tmp_path):
    """ps は確認プロンプトなしで即実行する (--all は CLI 既定の False)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("ps で確認を求めない"))
    assert actions_project._run_operation(tmp_path, "carmo", "ps") == 0
    assert captured["subcommand"] == "ps" and captured["all"] is False


def test_run_operation_logs_collects_tail_only(monkeypatch, tmp_path):
    """logs は tail のみ収集し、--follow は CLI 既定 (False) で実行する。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("logs で確認を求めない"))
    monkeypatch.setattr(actions_project, "_optional_int", lambda msg: 50)  # tail=50
    assert actions_project._run_operation(tmp_path, "carmo", "logs") == 0
    assert captured["subcommand"] == "logs"
    assert captured["follow"] is False and captured["tail"] == 50


def test_run_operation_logs_tail_empty_is_none(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_project, "_optional_int", lambda msg: None)  # 空 = 全件
    assert actions_project._run_operation(tmp_path, "carmo", "logs") == 0
    assert captured["follow"] is False and captured["tail"] is None


def test_run_operation_logs_tail_ctrl_c_aborts(monkeypatch, tmp_path):
    """tail 入力中の Ctrl-C (_ABORT) は None を伝搬して全体中止する (round4 major)。"""
    from devbase.commands import container as container_mod
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)
    monkeypatch.setattr(actions_project, "_optional_int",
                        lambda msg: actions_project._ABORT)
    assert actions_project._run_operation(tmp_path, "carmo", "logs") is None
    assert called == []


def test_run_operation_scale_collects_int(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "integer", lambda *a, **k: 4)
    assert actions_project._run_operation(tmp_path, "carmo", "scale") == 0
    assert captured["subcommand"] == "scale" and captured["new_scale"] == 4


@pytest.mark.parametrize("int_ret", ["BACK", None])
def test_run_operation_scale_cancel(monkeypatch, tmp_path, int_ret):
    """コンテナ数入力で Esc は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    from devbase.commands import container as container_mod
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)
    ret = menu.MENU_BACK if int_ret == "BACK" else None
    monkeypatch.setattr(menu, "integer", lambda *a, **k: ret)
    expected = actions_project._ARG_CANCEL if int_ret == "BACK" else None
    assert actions_project._run_operation(tmp_path, "carmo", "scale") is expected
    assert called == []


def test_run_operation_build_selects_image(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_project, "_select_build_image", lambda root: "web")
    assert actions_project._run_operation(tmp_path, "carmo", "build") == 0
    assert captured["subcommand"] == "build" and captured["image"] == "web"


def test_run_operation_build_compose_all_is_image_none(monkeypatch, tmp_path):
    """compose.yml 全体 ('') は image=None で委譲する (CLI の引数省略と同じ)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_project, "_select_build_image", lambda root: "")
    assert actions_project._run_operation(tmp_path, "carmo", "build") == 0
    assert captured["subcommand"] == "build" and captured["image"] is None


def test_run_operation_build_cancel(monkeypatch, tmp_path):
    from devbase.commands import container as container_mod
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)
    monkeypatch.setattr(actions_project, "_select_build_image",
                        lambda root: actions_project._ARG_CANCEL)
    assert actions_project._run_operation(tmp_path, "carmo", "build") is actions_project._ARG_CANCEL
    assert called == []


def test_run_operation_build_ctrl_c_aborts(monkeypatch, tmp_path):
    """イメージ選択中の Ctrl-C は None を伝搬して全体中止する (codex round2 指摘)。"""
    from devbase.commands import container as container_mod
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)
    monkeypatch.setattr(actions_project, "_select_build_image", lambda root: None)
    assert actions_project._run_operation(tmp_path, "carmo", "build") is None
    assert called == []


# ---------------------------------------------------------------------------
# _optional_int / _select_build_image
# ---------------------------------------------------------------------------

def test_optional_int_value(monkeypatch):
    monkeypatch.setattr(menu, "text", lambda *a, **k: "20")
    assert actions_project._optional_int("tail") == 20


def test_optional_int_empty_is_none(monkeypatch):
    monkeypatch.setattr(menu, "text", lambda *a, **k: "")
    assert actions_project._optional_int("tail") is None


def test_optional_int_cancel(monkeypatch):
    """Ctrl-C (None) は _ABORT、Esc (MENU_BACK) は _ARG_CANCEL を返す。

    空入力の ``None`` (= 既定動作) と Ctrl-C を区別するため、Ctrl-C は専用番兵
    ``_ABORT`` で返す (PR #55 round4 major)。
    """
    monkeypatch.setattr(menu, "text", lambda *a, **k: None)
    assert actions_project._optional_int("tail") is actions_project._ABORT

    monkeypatch.setattr(menu, "text", lambda *a, **k: menu.MENU_BACK)
    assert actions_project._optional_int("tail") is actions_project._ARG_CANCEL


def test_optional_int_reprompts_non_numeric(monkeypatch):
    vals = iter(["abc", "7"])
    monkeypatch.setattr(menu, "text", lambda *a, **k: next(vals))
    assert actions_project._optional_int("tail") == 7


def test_optional_int_reprompts_negative(monkeypatch):
    """負数 (min_value=0 未満) は弾いて再入力を促す (logs --tail への負数防止)。"""
    vals = iter(["-5", "10"])
    monkeypatch.setattr(menu, "text", lambda *a, **k: next(vals))
    assert actions_project._optional_int("tail") == 10


def test_select_build_image_lists_containers(monkeypatch, tmp_path):
    """containers/<img>/Dockerfile を列挙し、選択値をそのまま返す。"""
    for img in ("web", "db"):
        d = tmp_path / "containers" / img
        d.mkdir(parents=True)
        (d / "Dockerfile").write_text("FROM scratch\n")
    # Dockerfile 無しのディレクトリは除外される
    (tmp_path / "containers" / "nodockerfile").mkdir()

    captured = {}

    def fake_select(message, choices, *, back, search):
        captured["values"] = [c[1] for c in choices]
        return "db"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_project._select_build_image(tmp_path) == "db"
    # 先頭は compose 全体 (value="")、続いて sorted な img 名
    assert captured["values"] == ["", "db", "web"]


def test_select_build_image_compose_all_is_empty(monkeypatch, tmp_path):
    """『compose.yml 全体』(value='') を選ぶと '' を返す (呼び出し側で None へ変換)。"""
    d = tmp_path / "containers" / "web"
    d.mkdir(parents=True)
    (d / "Dockerfile").write_text("FROM scratch\n")
    monkeypatch.setattr(menu, "select", lambda *a, **k: "")
    assert actions_project._select_build_image(tmp_path) == ""


def test_select_build_image_no_containers_returns_empty(tmp_path):
    """containers/ が無ければ選択メニューを出さず compose 全体 ('')。"""
    assert actions_project._select_build_image(tmp_path) == ""


@pytest.mark.parametrize("sel", ["BACK", None])
def test_select_build_image_cancel(monkeypatch, tmp_path, sel):
    """Esc/← (MENU_BACK) は _ARG_CANCEL、Ctrl-C (None) は None (全体中止) を返す。"""
    d = tmp_path / "containers" / "web"
    d.mkdir(parents=True)
    (d / "Dockerfile").write_text("FROM scratch\n")
    ret = menu.MENU_BACK if sel == "BACK" else None
    monkeypatch.setattr(menu, "select", lambda *a, **k: ret)
    expected = actions_project._ARG_CANCEL if sel == "BACK" else None
    assert actions_project._select_build_image(tmp_path) is expected


# ---------------------------------------------------------------------------
# _operation_menu: 引数収集中止 → サブメニュー再表示
# ---------------------------------------------------------------------------

def test_operation_menu_arg_cancel_reshows_submenu(monkeypatch, tmp_path):
    """引数収集を中止 (_ARG_CANCEL) するとサブメニューを再表示し、再選択で実行する。"""
    select_calls = []
    # 1 回目: scale を選ぶ (→ 引数収集中止) / 2 回目: up を選ぶ (→ 実行)
    monkeypatch.setattr(actions_project, "_select_action",
                        lambda name: (select_calls.append(1),
                                      "scale" if len(select_calls) == 1 else "up")[1])

    run_calls = []

    def fake_run_op(root, name, op):
        run_calls.append(op)
        return actions_project._ARG_CANCEL if op == "scale" else 0

    monkeypatch.setattr(actions_project, "_run_operation", fake_run_op)

    assert actions_project._operation_menu(tmp_path, "carmo") == 0
    assert run_calls == ["scale", "up"]
    assert len(select_calls) == 2, "引数中止でサブメニューが再表示される"


# ---------------------------------------------------------------------------
# fallback_select_and_up: 番号入力 (questionary 不在) の非回帰
# ---------------------------------------------------------------------------

def test_fallback_selects_and_ups(monkeypatch):
    from devbase.commands import container as container_mod
    rows = [{"name": "alpha", "plugin": "-", "status": "stopped"},
            {"name": "beta", "plugin": "-", "status": "stopped"}]
    monkeypatch.setattr("builtins.input", lambda *a, **k: "2")

    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(
                            subcommand=args.subcommand, name=args.name) or 0)

    assert actions_project.fallback_select_and_up(rows) == 0
    assert captured == {"subcommand": "up", "name": "beta"}


def test_fallback_empty_input_aborts(monkeypatch):
    from devbase.commands import container as container_mod
    rows = [{"name": "alpha", "plugin": "-", "status": "stopped"}]
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)
    assert actions_project.fallback_select_and_up(rows) == 0
    assert called == []


def test_fallback_non_tty_eof(monkeypatch):
    from devbase.commands import container as container_mod
    rows = [{"name": "alpha", "plugin": "-", "status": "stopped"}]

    def _eof(*a, **k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)
    assert actions_project.fallback_select_and_up(rows) == 1
    assert called == []


def test_fallback_keyboard_interrupt_aborts(monkeypatch):
    from devbase.commands import container as container_mod
    rows = [{"name": "alpha", "plugin": "-", "status": "stopped"}]

    def _interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _interrupt)
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)
    assert actions_project.fallback_select_and_up(rows) == 0
    assert called == []


def test_fallback_out_of_range_then_valid(monkeypatch):
    from devbase.commands import container as container_mod
    rows = [{"name": "alpha", "plugin": "-", "status": "stopped"}]
    inputs = iter(["99", "1"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(name=args.name) or 0)
    assert actions_project.fallback_select_and_up(rows) == 0
    assert captured == {"name": "alpha"}


def test_fallback_non_numeric_then_valid(monkeypatch):
    from devbase.commands import container as container_mod
    rows = [{"name": "alpha", "plugin": "-", "status": "stopped"}]
    inputs = iter(["abc", "1"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(name=args.name) or 0)
    assert actions_project.fallback_select_and_up(rows) == 0
    assert captured == {"name": "alpha"}
