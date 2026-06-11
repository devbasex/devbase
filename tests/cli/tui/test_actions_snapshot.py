"""PLAN31_2 PR5: tui.actions_snapshot (snapshot カテゴリ操作) のテスト。

`menu.*` を monkeypatch して選択・入力値を注入し、`cmd_snapshot` を mock して
plan 2.3 の契約どおりの属性を持つ Namespace で呼ばれることを検証する
(test_actions_project.py のパターン踏襲)。
"""

from __future__ import annotations

import pytest

from devbase.tui import actions_snapshot, menu


def _capture_dispatch(monkeypatch):
    """cmd_snapshot の呼び出し引数を全属性キャプチャするヘルパ。

    actions_snapshot は ``cmd_snapshot`` をモジュール global として参照するため、
    actions_snapshot 側を monkeypatch する。
    """
    captured = {}

    def _spy(devbase_root, args):
        captured["devbase_root"] = devbase_root
        captured["subcommand"] = args.subcommand
        for k in ("name", "full", "point", "new_name", "keep"):
            if hasattr(args, k):
                captured[k] = getattr(args, k)
        return 0

    monkeypatch.setattr(actions_snapshot, "cmd_snapshot", _spy)
    return captured


def _no_dispatch(monkeypatch):
    """cmd_snapshot が呼ばれないことを検証するためのスパイ。"""
    called = []
    monkeypatch.setattr(actions_snapshot, "cmd_snapshot",
                        lambda root, args: called.append(1) or 0)
    return called


# ---------------------------------------------------------------------------
# _select_operation: menu.select への委譲
# ---------------------------------------------------------------------------

def test_select_operation_lists_all_ops(monkeypatch):
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search,
                        values=[c[1] for c in choices])
        return "create"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_snapshot._select_operation() == "create"
    assert captured["back"] is True
    assert captured["search"] is False
    # 安全な list を先頭にしつつ全 6 操作を提示する。
    assert captured["values"] == [
        "list", "create", "restore", "copy", "delete", "rotate"]
    assert captured["values"][0] == "list", "Enter 連打で破壊的操作に到達しない"


# ---------------------------------------------------------------------------
# run(): 操作メニューの遷移 (Esc/← / Ctrl-C / 引数中止の再表示 / rc 伝搬)
# ---------------------------------------------------------------------------

def test_run_back_returns_to_top(monkeypatch, tmp_path):
    """操作メニューで Esc/← (MENU_BACK) を押すとトップへ戻る (何も起動しない)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_snapshot, "_select_operation", lambda: menu.MENU_BACK)
    assert actions_snapshot.run(tmp_path) is menu.MENU_BACK
    assert called == []


def test_run_ctrl_c_aborts(monkeypatch, tmp_path):
    """操作メニューで Ctrl-C (None) を押すと全体中止 (None を返す)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_snapshot, "_select_operation", lambda: None)
    assert actions_snapshot.run(tmp_path) is None
    assert called == []


def test_run_executes_and_returns_rc(monkeypatch, tmp_path):
    """操作を実行したら dispatch の rc を返す (非0 もそのまま伝搬)。"""
    monkeypatch.setattr(actions_snapshot, "_select_operation", lambda: "list")
    monkeypatch.setattr(actions_snapshot, "_run_operation", lambda root, op: 1)
    assert actions_snapshot.run(tmp_path) == 1


def test_run_arg_cancel_reshows_menu(monkeypatch, tmp_path):
    """引数収集を中止 (_ARG_CANCEL) すると操作メニューを再表示し、再選択で実行する。"""
    select_calls = []
    # 1 回目: delete (→ 引数収集中止) / 2 回目: list (→ 実行)
    monkeypatch.setattr(actions_snapshot, "_select_operation",
                        lambda: (select_calls.append(1),
                                 "delete" if len(select_calls) == 1 else "list")[1])

    run_calls = []

    def fake_run_op(root, op):
        run_calls.append(op)
        return actions_snapshot._ARG_CANCEL if op == "delete" else 0

    monkeypatch.setattr(actions_snapshot, "_run_operation", fake_run_op)

    assert actions_snapshot.run(tmp_path) == 0
    assert run_calls == ["delete", "list"]
    assert len(select_calls) == 2, "引数中止で操作メニューが再表示される"


# ---------------------------------------------------------------------------
# _run_operation: 各操作の引数収集 + dispatch 契約 (plan 2.3)
# ---------------------------------------------------------------------------

def test_run_operation_list_no_extra_attrs(monkeypatch, tmp_path):
    """list は引数収集なしで即委譲する (追加属性なし)。"""
    captured = _capture_dispatch(monkeypatch)
    assert actions_snapshot._run_operation(tmp_path, "list") == 0
    assert captured == {"devbase_root": tmp_path, "subcommand": "list"}


def test_run_operation_create_collects_name_and_full(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "text", lambda *a, **k: "snap1")
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)   # --full
    assert actions_snapshot._run_operation(tmp_path, "create") == 0
    assert captured["subcommand"] == "create"
    assert captured["name"] == "snap1" and captured["full"] is True


def test_run_operation_create_empty_name_is_none(monkeypatch, tmp_path):
    """空入力の name は CLI の --name 省略と同じ None (自動命名) に正規化する。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "text", lambda *a, **k: "")
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: False)
    assert actions_snapshot._run_operation(tmp_path, "create") == 0
    assert captured["name"] is None and captured["full"] is False


def test_run_operation_create_name_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "text", lambda *a, **k: None)      # Esc/Ctrl-C
    assert actions_snapshot._run_operation(
        tmp_path, "create") is actions_snapshot._ARG_CANCEL
    assert called == []


def test_run_operation_create_full_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "text", lambda *a, **k: "snap1")
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: None)   # Esc/Ctrl-C
    assert actions_snapshot._run_operation(
        tmp_path, "create") is actions_snapshot._ARG_CANCEL
    assert called == []


def test_run_operation_restore_confirmed(monkeypatch, tmp_path):
    """restore は confirm=True で name/point を契約どおり渡す (plan 3.4)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_snapshot, "_select_snapshot_name",
                        lambda root, msg: "snap1")
    monkeypatch.setattr(actions_snapshot, "_optional_point", lambda msg: 2)
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)
    assert actions_snapshot._run_operation(tmp_path, "restore") == 0
    assert captured["subcommand"] == "restore"
    assert captured["name"] == "snap1" and captured["point"] == 2


def test_run_operation_restore_point_empty_is_none(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_snapshot, "_select_snapshot_name",
                        lambda root, msg: "snap1")
    monkeypatch.setattr(actions_snapshot, "_optional_point", lambda msg: None)
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)
    assert actions_snapshot._run_operation(tmp_path, "restore") == 0
    assert captured["point"] is None


@pytest.mark.parametrize("confirm_ret", [False, None])
def test_run_operation_restore_cancelled_does_not_dispatch(
        monkeypatch, tmp_path, confirm_ret):
    """restore の confirm を拒否 (False) / 中止 (None) したら実行しない (plan 3.4)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_snapshot, "_select_snapshot_name",
                        lambda root, msg: "snap1")
    monkeypatch.setattr(actions_snapshot, "_optional_point", lambda msg: None)
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: confirm_ret)
    assert actions_snapshot._run_operation(
        tmp_path, "restore") is actions_snapshot._ARG_CANCEL
    assert called == [], "確認を拒否/中止したら restore しない"


def test_run_operation_restore_name_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_snapshot, "_select_snapshot_name",
                        lambda root, msg: actions_snapshot._ARG_CANCEL)
    assert actions_snapshot._run_operation(
        tmp_path, "restore") is actions_snapshot._ARG_CANCEL
    assert called == []


def test_run_operation_restore_name_ctrl_c_aborts(monkeypatch, tmp_path):
    """対象選択中の Ctrl-C は None を伝搬して全体中止する (codex round2 指摘)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_snapshot, "_select_snapshot_name",
                        lambda root, msg: None)
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("Ctrl-C 後に確認を求めない"))
    assert actions_snapshot._run_operation(tmp_path, "restore") is None
    assert called == []


def test_run_operation_restore_point_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_snapshot, "_select_snapshot_name",
                        lambda root, msg: "snap1")
    monkeypatch.setattr(actions_snapshot, "_optional_point",
                        lambda msg: actions_snapshot._ARG_CANCEL)
    assert actions_snapshot._run_operation(
        tmp_path, "restore") is actions_snapshot._ARG_CANCEL
    assert called == []


def test_run_operation_copy_collects_new_name(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_snapshot, "_select_snapshot_name",
                        lambda root, msg: "snap1")
    monkeypatch.setattr(menu, "text", lambda *a, **k: "snap1-copy")
    assert actions_snapshot._run_operation(tmp_path, "copy") == 0
    assert captured["subcommand"] == "copy"
    assert captured["name"] == "snap1" and captured["new_name"] == "snap1-copy"


def test_run_operation_copy_new_name_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_snapshot, "_select_snapshot_name",
                        lambda root, msg: "snap1")
    monkeypatch.setattr(menu, "text", lambda *a, **k: None)      # Esc/Ctrl-C
    assert actions_snapshot._run_operation(
        tmp_path, "copy") is actions_snapshot._ARG_CANCEL
    assert called == []


def test_run_operation_delete_confirmed(monkeypatch, tmp_path):
    """delete は confirm=True で name を契約どおり渡す (plan 3.4)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_snapshot, "_select_snapshot_name",
                        lambda root, msg: "snap1")
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)
    assert actions_snapshot._run_operation(tmp_path, "delete") == 0
    assert captured["subcommand"] == "delete" and captured["name"] == "snap1"


@pytest.mark.parametrize("confirm_ret", [False, None])
def test_run_operation_delete_cancelled_does_not_dispatch(
        monkeypatch, tmp_path, confirm_ret):
    """delete の confirm を拒否 (False) / 中止 (None) したら削除しない (plan 3.4)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_snapshot, "_select_snapshot_name",
                        lambda root, msg: "snap1")
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: confirm_ret)
    assert actions_snapshot._run_operation(
        tmp_path, "delete") is actions_snapshot._ARG_CANCEL
    assert called == [], "確認を拒否/中止したら delete しない"


def test_run_operation_rotate_collects_keep(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    seen = {}

    def fake_integer(message, *, default=None, min_value=None, max_value=None):
        seen.update(default=default, min_value=min_value)
        return 5

    monkeypatch.setattr(menu, "integer", fake_integer)
    assert actions_snapshot._run_operation(tmp_path, "rotate") == 0
    assert captured["subcommand"] == "rotate" and captured["keep"] == 5
    # CLI 既定 (--keep 3) と同じ既定値を提示し、no-op な 0 以下は弾く。
    assert seen == {"default": 3, "min_value": 1}


def test_run_operation_rotate_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "integer", lambda *a, **k: None)
    assert actions_snapshot._run_operation(
        tmp_path, "rotate") is actions_snapshot._ARG_CANCEL
    assert called == []


def test_run_operation_unknown_op_is_noop(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    assert actions_snapshot._run_operation(
        tmp_path, "bogus") is actions_snapshot._ARG_CANCEL
    assert called == []


# ---------------------------------------------------------------------------
# _select_snapshot_name: 既存一覧からの選択 / 縮退
# ---------------------------------------------------------------------------

class _FakeManager:
    """SnapshotManager の list() だけを差し替える小道具。"""

    snapshots: list[dict] | Exception = []

    def __init__(self, devbase_root):
        pass

    def list(self):
        if isinstance(type(self).snapshots, Exception):
            raise type(self).snapshots
        return type(self).snapshots


def test_select_snapshot_name_lists_and_returns_value(monkeypatch, tmp_path):
    """既存一覧を (名前+作成日時) で提示し、選択値 (名前) をそのまま返す。"""
    _FakeManager.snapshots = [
        {"name": "snap1", "created_at": "2026-06-10T12:00:00.123456"},
        {"name": "snap2"},  # created_at 欠落でも落ちない
    ]
    monkeypatch.setattr(actions_snapshot, "SnapshotManager", _FakeManager)

    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search,
                        titles=[c[0] for c in choices],
                        values=[c[1] for c in choices])
        return "snap2"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_snapshot._select_snapshot_name(tmp_path, "選択") == "snap2"
    assert captured["back"] is True and captured["search"] is True
    assert captured["values"] == ["snap1", "snap2"]
    assert "2026-06-10T12:00:00" in captured["titles"][0]


@pytest.mark.parametrize("sel", ["BACK", None])
def test_select_snapshot_name_cancel(monkeypatch, tmp_path, sel):
    """Esc (MENU_BACK) は _ARG_CANCEL、Ctrl-C (None) は None (全体中止) を返す。"""
    _FakeManager.snapshots = [{"name": "snap1", "created_at": "2026-06-10"}]
    monkeypatch.setattr(actions_snapshot, "SnapshotManager", _FakeManager)
    ret = menu.MENU_BACK if sel == "BACK" else None
    monkeypatch.setattr(menu, "select", lambda *a, **k: ret)
    expected = actions_snapshot._ARG_CANCEL if sel == "BACK" else None
    assert actions_snapshot._select_snapshot_name(tmp_path, "選択") is expected


def test_select_snapshot_name_empty_list_cancels(monkeypatch, tmp_path):
    """対象が 1 件も無ければ案内を出して中止 (選択メニューは出さない)。"""
    _FakeManager.snapshots = []
    monkeypatch.setattr(actions_snapshot, "SnapshotManager", _FakeManager)
    select_calls = []
    monkeypatch.setattr(menu, "select",
                        lambda *a, **k: select_calls.append(1) or None)
    assert actions_snapshot._select_snapshot_name(
        tmp_path, "選択") is actions_snapshot._ARG_CANCEL
    assert select_calls == []


def test_select_snapshot_name_list_failure_falls_back_to_text(monkeypatch, tmp_path):
    """一覧取得に失敗したら自由入力へ縮退する (存在チェックは委譲先に任せる)。"""
    _FakeManager.snapshots = RuntimeError("boom")
    monkeypatch.setattr(actions_snapshot, "SnapshotManager", _FakeManager)
    monkeypatch.setattr(menu, "text", lambda *a, **k: "typed-name")
    assert actions_snapshot._select_snapshot_name(tmp_path, "選択") == "typed-name"


def test_select_snapshot_name_text_fallback_cancel(monkeypatch, tmp_path):
    _FakeManager.snapshots = RuntimeError("boom")
    monkeypatch.setattr(actions_snapshot, "SnapshotManager", _FakeManager)
    monkeypatch.setattr(menu, "text", lambda *a, **k: None)
    assert actions_snapshot._select_snapshot_name(
        tmp_path, "選択") is actions_snapshot._ARG_CANCEL


# ---------------------------------------------------------------------------
# _optional_point
# ---------------------------------------------------------------------------

def test_optional_point_value(monkeypatch):
    monkeypatch.setattr(menu, "text", lambda *a, **k: "3")
    assert actions_snapshot._optional_point("point") == 3


def test_optional_point_empty_is_none(monkeypatch):
    monkeypatch.setattr(menu, "text", lambda *a, **k: "")
    assert actions_snapshot._optional_point("point") is None


def test_optional_point_cancel(monkeypatch):
    monkeypatch.setattr(menu, "text", lambda *a, **k: None)
    assert actions_snapshot._optional_point(
        "point") is actions_snapshot._ARG_CANCEL


def test_optional_point_reprompts_non_numeric(monkeypatch):
    vals = iter(["abc", "7"])
    monkeypatch.setattr(menu, "text", lambda *a, **k: next(vals))
    assert actions_snapshot._optional_point("point") == 7


def test_optional_point_reprompts_non_positive(monkeypatch):
    """manager は point に正の整数のみ受理するため 0 以下は弾いて再入力を促す。"""
    vals = iter(["0", "-1", "2"])
    monkeypatch.setattr(menu, "text", lambda *a, **k: next(vals))
    assert actions_snapshot._optional_point("point") == 2
