"""``containers/base/tmux-clean`` の分岐と異常系 (#239)

実物の tmux を隔離したサーバーで動かし、セッション一覧・出力・終了コードで振る舞いを
固定する。状態の取得や削除の失敗は、``ScriptTmuxEnv`` の故障の表で 1 つの呼び出しだけに
差し込む。
"""

from __future__ import annotations

import pytest

from .tmux_harness import ScriptTmuxEnv, needs_tmux, short_root

pytestmark = needs_tmux


@pytest.fixture
def tm():
    env = ScriptTmuxEnv(short_root())
    try:
        yield env
    finally:
        env.close()


def names(tm: ScriptTmuxEnv) -> set[str]:
    return set(tm.sessions())


def clean(tm: ScriptTmuxEnv, *args: str, env: dict[str, str] | None = None):
    return tm.sh("tmux-clean", *args, env=env)


def busy_sessions(tm: ScriptTmuxEnv):
    """keeper の devbase-1、アタッチ中の devbase-2、実行中の devbase-3、空の devbase-4。"""
    tm.new("devbase-1")
    attached = tm.new("devbase-2")
    tm.new("devbase-3", "sleep", "600")
    tm.new("devbase-4")
    tm.attach(attached)


# --- 分岐: keeper ---


def test_outside_keeps_the_lowest_number(tm):
    """tmux の外では番号が最小のもの (数の昇順で 2 < 10) を残す。"""
    tm.new("devbase-2")
    tm.new("devbase-10")

    done = clean(tm, "devbase")

    assert done.returncode == 0, done.stderr
    assert "tmux-clean: base=devbase keeper=devbase-2" in done.stdout
    assert "  keep  devbase-2  (keeper)" in done.stdout
    assert names(tm) == {"devbase-2"}


def test_inside_keeps_the_current_session(tm):
    """tmux の中では今のセッションを残し、ベース名は今のセッション名から推定する。"""
    tm.new("devbase-1")
    current = tm.new("devbase-2")
    tm.new("devbase-3")

    done = clean(tm, env=tm.inside_env(current))

    assert done.returncode == 0, done.stderr
    assert "tmux-clean: base=devbase keeper=devbase-2" in done.stdout
    assert names(tm) == {"devbase-2"}


# --- 分岐: 利用中の保護と -f / -n ---


def test_attached_and_running_sessions_are_kept(tm):
    busy_sessions(tm)

    done = clean(tm, "devbase")

    assert done.returncode == 0, done.stderr
    assert "  keep  devbase-2  (アタッチ中: クライアント 1 個)" in done.stdout
    assert "  keep  devbase-3  (実行中: sleep)" in done.stdout
    assert "  KILL  devbase-4" in done.stdout
    assert names(tm) == {"devbase-1", "devbase-2", "devbase-3"}


def test_force_removes_attached_and_running_but_not_the_keeper(tm):
    busy_sessions(tm)

    done = clean(tm, "-f", "devbase")

    assert done.returncode == 0, done.stderr
    assert names(tm) == {"devbase-1"}


@pytest.mark.parametrize("flags", [("-n",), ("-n", "-f")], ids=["dry-run", "dry-run-force"])
def test_dry_run_removes_nothing(tm, flags):
    busy_sessions(tm)
    before = tm.sessions()

    done = clean(tm, *flags, "devbase")

    assert done.returncode == 0, done.stderr
    assert "  KILL  devbase-4  (dry-run)" in done.stdout
    assert "  keep  devbase-1  (keeper)" in done.stdout
    assert tm.sessions() == before


# --- 異常系 ---

UNREADABLE = {
    "panes": "list-panes -s -t =devbase-2",
    "attached": "list-sessions -F #{session_attached}",
}


@pytest.mark.parametrize("force", [False, True], ids=["default", "force"])
@pytest.mark.parametrize("call", sorted(UNREADABLE))
def test_unreadable_state_is_kept_without_force(tm, call, force):
    """状態を読めないセッションは、空の状態と読み替えずに残す (-f なら消す)。"""
    tm.new("devbase-1")
    tm.new("devbase-2")
    tm.fail(UNREADABLE[call])

    done = clean(tm, *(["-f"] if force else []), "devbase")

    assert done.returncode == 0, done.stderr
    message = "tmux-clean: セッションの状態を取得できないため削除しません: devbase-2"
    if force:
        assert message not in done.stderr
        assert names(tm) == {"devbase-1"}
    else:
        assert message in done.stderr
        assert "  keep  devbase-2  (状態を取得できませんでした)" in done.stdout
        assert names(tm) == {"devbase-1", "devbase-2"}


@pytest.mark.parametrize("prefix", ["list-panes -s -t =devbase-2", "kill-session -t =devbase-2"],
                         ids=["before-reading-panes", "before-kill"])
def test_a_session_that_vanished_is_skipped(tm, prefix):
    """走査中に消えたセッションは skip と出し、削除の件数に数えない。"""
    for name in ("devbase-1", "devbase-2", "devbase-3"):
        tm.new(name)
    tm.vanish("devbase-2", prefix)

    done = clean(tm, "devbase")

    assert done.returncode == 0, done.stderr
    assert "  skip  devbase-2  (既に終了していました)" in done.stdout
    assert "tmux-clean: 削除 1 件 / 残す 1 件 / 失敗 0 件" in done.stdout
    assert names(tm) == {"devbase-1"}


def test_a_failed_kill_is_reported_and_the_rest_continue(tm):
    for name in ("devbase-1", "devbase-2", "devbase-3"):
        tm.new(name)
    tm.fail("kill-session -t =devbase-2")

    done = clean(tm, "devbase")

    assert done.returncode == 1
    assert "tmux-clean: セッションを削除できませんでした: devbase-2" in done.stderr
    assert "  KILL  devbase-3" in done.stdout
    assert "tmux-clean: 削除 1 件 / 残す 1 件 / 失敗 1 件" in done.stdout
    assert names(tm) == {"devbase-1", "devbase-2"}
