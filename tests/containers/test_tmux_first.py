"""``containers/base/tmux-first`` が実行元を特定できないときに何もしないこと (#239)

実行元のクライアントは、最終操作が 10 秒 (``SELF_FRESH``) 以内のときだけ特定できたとみなす。
最終操作は tmux サーバーが実時刻で持つため、``ScriptTmuxEnv`` の偽の ``date`` で
スクリプトの読む ``NOW`` だけを進め、待たずに「最終操作から 11 秒」の状態を作る。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from .tmux_harness import ScriptTmuxEnv, needs_tmux, short_root


@pytest.fixture
def tm():
    env = ScriptTmuxEnv(short_root())
    try:
        yield env
    finally:
        env.close()


def caller_and_other(tm: ScriptTmuxEnv):
    """実行元の端末を home に、他の端末を proj-1 に繋ぐ。"""
    target = tm.new("proj-1")
    home = tm.new("home")
    other = tm.attach(target)
    me = tm.attach(home)
    return target, home, me, other


@needs_tmux
@pytest.mark.parametrize("flags", [(), ("-f",)], ids=["default", "force"])
def test_unknown_caller_touches_no_client(tm, flags):
    _, home, _, _ = caller_and_other(tm)
    before = tm.all_clients()

    done = tm.sh("tmux-first", *flags, "proj", env=tm.inside_env(home), clock=11)

    assert done.returncode == 0, done.stderr
    assert "tmux-first: 実行元のクライアントを特定できないため" in done.stderr
    assert tm.all_clients() == before


@needs_tmux
def test_known_caller_detaches_others_and_switches(tm):
    """対照: 実行元を特定できるときは、他の端末を切断して実行元を切り替える。"""
    target, home, me, other = caller_and_other(tm)

    done = tm.sh("tmux-first", "-f", "proj", env=tm.inside_env(home), clock=0)

    assert done.returncode == 0, done.stderr
    assert tm.clients_of(target) == {me.tty}
    assert other.tty not in tm.all_clients()


def test_harness_env_has_no_inherited_tmux(monkeypatch):
    """pytest が利用者の tmux の中で動いていても、隔離した環境へ TMUX を持ち込まない。"""
    monkeypatch.setenv("TMUX", "/tmp/tmux-0/default,1,0")
    monkeypatch.setenv("TMUX_PANE", "%0")
    env = ScriptTmuxEnv(short_root())
    try:
        assert "TMUX" not in env.env
        assert "TMUX_PANE" not in env.env
        if shutil.which("tmux") is None:
            return
        # 中を模すときの TMUX は、隔離したサーバーのソケットを指す。
        sid = env.new("proj-1")
        socket = Path(env.inside_env(sid)["TMUX"].split(",")[0]).resolve()
        assert socket.is_relative_to(Path(env.env["TMUX_TMPDIR"]).resolve())
    finally:
        env.close()
