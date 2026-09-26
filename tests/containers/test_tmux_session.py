"""名指しで tmux のセッションを扱うコマンド ``tmux-session`` (PLAN69 / #234)

``containers/base/tmux-session`` を実物の tmux に対して走らせ、``list-clients`` /
``list-sessions`` の変化で振る舞いを確かめる。

利用者の tmux サーバーには触れない。テストごとに ``$TMPDIR`` 直下へ短い名前の
ディレクトリを作り、``TMUX_TMPDIR`` をそこへ向けて ``TMUX`` を消した環境を 1 つ作る。
テスト側の tmux の操作も ``tmux-session`` の実行も、すべてこの環境で行う
(``tmux-session`` は ``-S`` を受け取らず既定のソケットへ繋ぐため、両者が同じサーバーを
見るには環境をそろえるしかない)。サーバーは ``-f /dev/null`` (または対象の設定) で
起動し、利用者の ``~/.tmux.conf`` を読まない。

端末は ``pty.openpty`` で用意する。端末の出力はスレッドで読み続ける (読まないと
tmux のクライアントが書き込みで止まり、表示を確かめるテストにも要る)。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .tmux_harness import (BASE_DIR, SCRIPT, SHORT_NAMES, Client, TmuxEnv, needs_tmux)
from .tmux_harness import short_root as _short_root
from .tmux_harness import wait as _wait

TMUX_CONF = BASE_DIR / "tmux.conf"
DOCKERFILE = BASE_DIR / "Dockerfile"

# 名前に含まれても取り違えないことを確かめる文字 (受け入れ条件 10・14)
SPECIAL_NAMES = ["a b", "it's", 'q"x', "d$1", "s;x"]


def test_go_from_target_session_detaches_only_other_target_clients(tmp_path):
    """現状固定: 移動先に既にいる実行元と、無関係なセッションの端末は残る。"""
    state = tmp_path / "clients.json"
    state.write_text(json.dumps({
        "/dev/pts/1": "$1", "/dev/pts/2": "$1", "/dev/pts/3": "$2",
    }))
    stub = tmp_path / "tmux"
    stub.write_text(f"#!{sys.executable}\n" + '''
import json
import sys
from pathlib import Path

state = Path(__file__).with_name("clients.json")
clients = json.loads(state.read_text())
command, *args = sys.argv[1:]

def option(flag):
    return args[args.index(flag) + 1]

if command == "list-sessions":
    print("$1 target\\n$2 unrelated")
elif command == "list-clients":
    for client, session in clients.items():
        if "-t" not in args or session == option("-t"):
            print(client)
elif command == "display-message":
    if "-p" in args:
        print(clients[option("-c")])
elif command == "detach-client":
    del clients[option("-t")]
    state.write_text(json.dumps(clients))
elif command == "switch-client":
    clients[option("-c")] = option("-t")
    state.write_text(json.dumps(clients))
else:
    raise SystemExit(f"unsupported tmux command: {command}")
''')
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in ("TMUX", "TMUX_PANE", "ENV", "BASH_ENV")}
    env["PATH"] = f"{tmp_path}:{os.environ.get('PATH', '/usr/bin:/bin')}"

    done = subprocess.run(
        [str(SCRIPT), "go", "-c", "/dev/pts/1", "target"],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
    assert json.loads(state.read_text()) == {
        "/dev/pts/1": "$1", "/dev/pts/3": "$2",
    }


@pytest.mark.parametrize("idle_seconds, expected_returncode, expected_clients", [
    (10, 0, {"/dev/pts/1": "$2", "/dev/pts/3": "$3"}),
    (11, 1, {"/dev/pts/1": "$1", "/dev/pts/2": "$2", "/dev/pts/3": "$3"}),
])
def test_go_infers_client_at_activity_boundary(
    tmp_path, idle_seconds, expected_returncode, expected_clients,
):
    """現状固定: 最終操作から10秒なら移動し、11秒なら全接続を維持する。"""
    state = tmp_path / "clients.json"
    state.write_text(json.dumps({
        "/dev/pts/1": "$1", "/dev/pts/2": "$2", "/dev/pts/3": "$3",
    }))
    (tmp_path / "activity").write_text(str(1_000 - idle_seconds))
    stub = tmp_path / "tmux"
    stub.write_text(f"#!{sys.executable}\n" + '''
import json
import sys
from pathlib import Path

state = Path(__file__).with_name("clients.json")
clients = json.loads(state.read_text())
command, *args = sys.argv[1:]

def option(flag):
    return args[args.index(flag) + 1]

if command == "list-sessions":
    print("$1 source\\n$2 target\\n$3 unrelated")
elif command == "list-clients":
    for client, session in clients.items():
        if "-t" not in args or session == option("-t"):
            print(client)
elif command == "display-message":
    if args[-1] == "#{client_activity}:#{client_name}":
        activity = Path(__file__).with_name("activity").read_text()
        print(f"{activity}:/dev/pts/1")
    elif args[-1] == "#{session_id}":
        print(clients[option("-c")] if "-c" in args else "$1")
    else:
        raise SystemExit(f"unsupported tmux format: {args[-1]}")
elif command == "detach-client":
    del clients[option("-t")]
    state.write_text(json.dumps(clients))
elif command == "switch-client":
    clients[option("-c")] = option("-t")
    state.write_text(json.dumps(clients))
else:
    raise SystemExit(f"unsupported tmux command: {command}")
''')
    stub.chmod(0o755)
    date = tmp_path / "date"
    date.write_text('#!/bin/sh\n[ "$1" = "+%s" ] || exit 1\nprintf "1000\\n"\n')
    date.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in ("TMUX", "TMUX_PANE", "ENV", "BASH_ENV")}
    env.update(PATH=f"{tmp_path}:{os.environ.get('PATH', '/usr/bin:/bin')}",
               TMUX="/unused/socket,123,0", TMUX_PANE="%1")

    done = subprocess.run(
        [str(SCRIPT), "go", "target"],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert done.returncode == expected_returncode, done.stderr
    assert json.loads(state.read_text()) == expected_clients
    if idle_seconds == 11:
        assert "実行元の端末を特定できない" in done.stderr


# go で detach-client / switch-client が失敗する経路を固定する。実物の tmux では特定の
# 操作だけを失敗させられないため、端末と接続先を JSON で持ち、fail に書いた操作だけを
# 非 0 にする tmux スタブを使う (fail は "detach-client <端末>" か "switch-client")。
# -c のときの知らせ (display-message -l) は状態行へ出すだけなので何もしない。
_GO_FAIL_STUB = f"#!{sys.executable}\n" + '''
import json
import sys
from pathlib import Path

state = Path(__file__).with_name("clients.json")
fail = Path(__file__).with_name("fail").read_text().strip()
clients = json.loads(state.read_text())  # {端末: セッション ID}
command, *args = sys.argv[1:]


def option(flag):
    return args[args.index(flag) + 1]


if command == "list-sessions":
    print("$1 source\\n$2 target\\n$3 unrelated")
elif command == "list-clients":
    for client, session in clients.items():
        if "-t" not in args or session == option("-t"):
            print(client)
elif command == "display-message":
    if "-p" in args:
        print(clients[option("-c")])
elif command == "detach-client":
    if fail == f"detach-client {option('-t')}":
        sys.exit(1)
    del clients[option("-t")]
    state.write_text(json.dumps(clients))
elif command == "switch-client":
    if fail == "switch-client":
        sys.exit(1)
    clients[option("-c")] = option("-t")
    state.write_text(json.dumps(clients))
else:
    raise SystemExit(f"unsupported tmux command: {command}")
'''


def _go_fail_env(tmp_path, fail):
    """実行元を source、2 台を target、1 台を unrelated に繋ぎ、``fail`` の操作だけ失敗させる。"""
    (tmp_path / "clients.json").write_text(json.dumps({
        "/dev/pts/1": "$1", "/dev/pts/2": "$2", "/dev/pts/3": "$2",
        "/dev/pts/4": "$3",
    }))
    (tmp_path / "fail").write_text(fail)
    stub = tmp_path / "tmux"
    stub.write_text(_GO_FAIL_STUB)
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in ("TMUX", "TMUX_PANE", "ENV", "BASH_ENV")}
    env["PATH"] = f"{tmp_path}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    return env


def test_go_detach_failure_warns_but_still_switches(tmp_path):
    """現状固定: 対象の他端末を外せなくても警告だけで続け、実行元は移り終了値は 0。"""
    env = _go_fail_env(tmp_path, "detach-client /dev/pts/2")

    done = subprocess.run(
        [str(SCRIPT), "go", "-c", "/dev/pts/1", "target"],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
    assert "/dev/pts/2" in done.stderr
    assert json.loads((tmp_path / "clients.json").read_text()) == {
        "/dev/pts/1": "$2", "/dev/pts/2": "$2", "/dev/pts/4": "$3",
    }


def test_go_detach_failure_continues_to_remaining_client_and_earlier_session(tmp_path):
    """現状固定: $1 の先頭端末を外せなくても、残りを外して $2 の実行元を移す。"""
    env = _go_fail_env(tmp_path, "detach-client /dev/pts/2")
    state = tmp_path / "clients.json"
    state.write_text(json.dumps({
        "/dev/pts/2": "$1", "/dev/pts/3": "$1", "/dev/pts/1": "$2",
    }))
    (tmp_path / "tmux").write_text(_GO_FAIL_STUB.replace(
        "$1 source\\n$2 target", "$1 target\\n$2 source",
    ))

    done = subprocess.run(
        [str(SCRIPT), "go", "-c", "/dev/pts/1", "target"],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert done.returncode == 0, done.stderr
    assert "/dev/pts/2" in done.stderr
    assert json.loads(state.read_text()) == {
        "/dev/pts/2": "$1", "/dev/pts/1": "$1",
    }


def test_go_switch_failure_exits_one_after_detaching(tmp_path):
    """現状固定: 切り替えに失敗すると終了値 1。対象の他端末は外した後で、実行元は残る。"""
    env = _go_fail_env(tmp_path, "switch-client")

    done = subprocess.run(
        [str(SCRIPT), "go", "-c", "/dev/pts/1", "target"],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert done.returncode == 1, done.stderr
    assert done.stdout == ""
    assert "target" in done.stderr
    assert json.loads((tmp_path / "clients.json").read_text()) == {
        "/dev/pts/1": "$1", "/dev/pts/4": "$3",
    }


@pytest.fixture
def tm():
    env = TmuxEnv(_short_root())
    try:
        yield env
    finally:
        env.close()


# --- 骨組み: 呼び出しの形と失敗の形 (受け入れ条件 11・12) ---


@pytest.mark.parametrize("argv", [("tmux-session", "-h"), ("tmux-session", "go", "-h"),
                                  ("tmux-go", "-h"), ("tmux-peek", "--help"),
                                  ("tmux-kill", "-h"), ("tmux-menu", "-h")])
def test_help_exits_zero(tm, argv):
    done = tm.run(*argv)
    assert done.returncode == 0, done.stderr
    assert "tmux-session" in done.stdout


def test_help_lists_tmux_menu(tm):
    """PLAN71 条件 9: ``tmux-session -h`` の使い方に ``tmux-menu`` が載る。"""
    done = tm.run("tmux-session", "-h")
    assert done.returncode == 0, done.stderr
    assert "tmux-menu" in done.stdout


@pytest.mark.parametrize("argv", [
    ("tmux-session",),                        # サブコマンドが無い
    ("tmux-session", "nope", "x"),            # 知らないサブコマンド
    ("tmux-go", "--nope", "x"),               # 知らないオプション
    ("tmux-kill", "-x", "x"),
    ("tmux-go",),                             # 引数が足りない
    ("tmux-peek", "a", "b"),                  # 引数が多い
    ("tmux-peek", "-n", "-1", "x"),           # -n が 0 以上の整数でない
    ("tmux-peek", "-n", "abc", "x"),
    ("tmux-go", "-c", "/dev/pts/1;x", "x"),   # -c の形が外れた
    ("tmux-go", "-c", "", "x"),
    ("tmux-session", "menu", "x"),            # menu に -c が無い
    ("tmux-go", "-c"),                        # -c の値が無い (末尾)
    ("tmux-peek", "-n"),                      # -n の値が無い (末尾)
    ("tmux-kill",),                           # kill にセッションが無い
    ("tmux-peek", "-c", "/dev/pts/1", "x"),   # peek は -c を受け取らない
    ("tmux-session", "menu", "-c", "/dev/pts/1", "a", "b"),  # menu にセッションが複数
    ("tmux-menu", "-c", "/dev/pts/1"),        # PLAN71 条件 9: -c だけでセッションが無い
    ("tmux-menu", "a"),                       # -c が無い
    ("tmux-menu", "-c", "/dev/pts/1", "a", "b"),  # 余分な引数
    ("tmux-menu", "-x"),                      # 知らないオプション
])
def test_usage_errors_exit_two(tm, argv):
    done = tm.run(*argv)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert done.stderr.strip(), "理由を標準エラーへ出す"


@needs_tmux
def test_no_server_exits_one(tm):
    """条件 11: tmux のサーバーが無いときは 1。"""
    for argv in (("tmux-go", "x"), ("tmux-peek", "x"), ("tmux-kill", "x")):
        done = tm.run(*argv)
        assert done.returncode == 1, (argv, done.stderr)
        assert "サーバ" in done.stderr


@needs_tmux
def test_missing_session_exits_one(tm):
    """条件 11: 無いセッションは 1。完全一致で探し、前方一致に落ちない。"""
    tm.new("devbase-10")
    for argv in (("tmux-go", "devbase-1"), ("tmux-peek", "devbase-1"),
                 ("tmux-kill", "devbase-1")):
        done = tm.run(*argv)
        assert done.returncode == 1, (argv, done.stderr)
        assert "devbase-1" in done.stderr
    assert "devbase-10" in tm.sessions()


@needs_tmux
def test_id_form_does_not_fall_back_to_name(tm):
    """``$数字`` は ID としてだけ引く。同じ文字列の名前のセッションへ落ちない。"""
    tm.new("$99")
    done = tm.run("tmux-kill", "$99")
    assert done.returncode == 1
    assert "$99" in tm.sessions()


@needs_tmux
def test_id_form_targets_session(tm):
    sid = tm.new("devbase-5")
    done = tm.run("tmux-kill", sid)
    assert done.returncode == 0, done.stderr
    assert "devbase-5" not in tm.sessions()


@needs_tmux
def test_unknown_client_exits_one(tm):
    tm.new("devbase-1")
    done = tm.run("tmux-go", "-c", "/dev/nope", "devbase-1")
    assert done.returncode == 1
    assert "/dev/nope" in done.stderr


# --- 落とす (受け入れ条件 6〜10・12) ---


@needs_tmux
@pytest.mark.parametrize("argv", [("tmux-kill",), ("tmux-session", "kill")])
def test_kill_ends_attached_busy_session_only(tm, argv):
    """条件 6・12: 端末が繋がり、シェル以外が動いていても落とす。devbase-30 は残る。"""
    sid = tm.new("devbase-3", "sleep", "300")
    tm.new("devbase-30")
    tm.attach(sid)

    done = tm.run(*argv, "devbase-3")

    assert done.returncode == 0, done.stderr
    assert "KILL devbase-3" in done.stdout
    assert set(tm.sessions()) == {"devbase-30"}


@needs_tmux
def test_kill_continues_past_missing_target(tm):
    """条件 7: 無い対象を飛ばして残りを落とし、最後に 1。"""
    for name in ("a", "c", "keep"):
        tm.new(name)

    done = tm.run("tmux-kill", "a", "b", "c")

    assert done.returncode == 1
    assert "b" in done.stderr
    assert set(tm.sessions()) == {"keep"}


@needs_tmux
def test_kill_refuses_own_session_without_force(tm):
    """条件 8: 自分の pane があるセッションは -f が無ければ落とさない。"""
    sid = tm.new("devbase-3")
    tm.new("other")
    inside = tm.inside_env(sid)

    done = tm.run("tmux-kill", "devbase-3", "other", env=inside)

    assert done.returncode == 1
    assert "-f" in done.stderr
    assert set(tm.sessions()) == {"devbase-3"}, "他の対象は続けて落とす"


@needs_tmux
def test_kill_force_ends_own_session_last(tm):
    """-f なら自分のセッションも落とす。ほかの対象を先に処理する。"""
    sid = tm.new("devbase-3")
    tm.new("other")
    tm.new("keep")
    inside = tm.inside_env(sid)

    done = tm.run("tmux-kill", "-f", "devbase-3", "other", env=inside)

    assert done.returncode == 0, done.stderr
    lines = [line for line in done.stdout.splitlines() if line.startswith("KILL")]
    assert lines == ["KILL other", "KILL devbase-3"]
    assert set(tm.sessions()) == {"keep"}


# ``kill-session`` 自体が失敗する経路を固定する。実物の tmux では特定の削除だけを
# 失敗させられないため、セッション状態を JSON で持ち、指定した ID の削除だけを非 0 に
# する tmux スタブを使う。resolve / name_of は起動時に 1 度だけ取る list-sessions の
# 出力で引くので、スタブは list-sessions・display-message・kill-session を返せばよい。
_KILL_FAIL_STUB = f"#!{sys.executable}\n" + '''
import json
import sys
from pathlib import Path

state = Path(__file__).with_name("sessions.json")
fail = Path(__file__).with_name("fail").read_text().strip()
sessions = json.loads(state.read_text())  # {id: name}
command, *args = sys.argv[1:]


def option(flag):
    return args[args.index(flag) + 1]


if command == "list-sessions":
    for sid, name in sessions.items():
        print(f"{sid} {name}")
elif command == "display-message":
    # 自分の pane があるセッションの ID (TMUX_PANE から引く)。
    print(Path(__file__).with_name("self").read_text().strip())
elif command == "kill-session":
    target = option("-t")
    if target == fail:
        sys.exit(1)
    sessions.pop(target, None)
    state.write_text(json.dumps(sessions))
else:
    raise SystemExit(f"unsupported tmux command: {command}")
'''


def _kill_fail_env(tmp_path, sessions, fail_id, self_id=""):
    """``kill-session`` が ``fail_id`` の削除だけ失敗する tmux スタブと環境を作る。"""
    (tmp_path / "sessions.json").write_text(json.dumps(sessions))
    (tmp_path / "fail").write_text(fail_id)
    (tmp_path / "self").write_text(self_id)
    stub = tmp_path / "tmux"
    stub.write_text(_KILL_FAIL_STUB)
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in ("TMUX", "TMUX_PANE", "ENV", "BASH_ENV")}
    env["PATH"] = f"{tmp_path}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    return env


def _remaining(tmp_path):
    return set(json.loads((tmp_path / "sessions.json").read_text()).values())


def test_kill_keeps_failed_target_but_kills_following_success(tmp_path):
    """現状固定: 通常対象の削除が失敗しても終了値を失わず、後続の成功対象は落とす。

    kill-session が fail を非 0 で返す do_kill の else 分岐を通す。失敗対象 (fail) は
    残り、後続の成功対象 (ok) は削除される。終了値は 1 になる。無関係な keep は残る。
    """
    env = _kill_fail_env(
        tmp_path, {"$1": "fail", "$2": "ok", "$3": "keep"}, fail_id="$1")

    done = subprocess.run(
        [str(SCRIPT), "kill", "fail", "ok"],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert done.returncode == 1, done.stderr
    assert "fail" in done.stderr
    assert _remaining(tmp_path) == {"fail", "keep"}


def test_kill_force_self_last_keeps_self_on_failure_but_kills_others(tmp_path):
    """現状固定: -f で最後に回す自己セッションの削除が失敗しても終了値を失わない。

    TMUX / TMUX_PANE を与えると self ($1) が SELF_SID に解決され、-f なので LAST へ
    回る。ほかの対象 (other) を先に落とし、最後の自己の kill-session が失敗する。
    自己は残るが other は削除され、終了値は 1 になる。無関係な keep は残る。
    """
    env = _kill_fail_env(
        tmp_path, {"$1": "self", "$2": "other", "$3": "keep"},
        fail_id="$1", self_id="$1")
    env.update(TMUX="/unused/socket,123,0", TMUX_PANE="%1")

    done = subprocess.run(
        [str(SCRIPT), "kill", "-f", "self", "other"],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert done.returncode == 1, done.stderr
    assert _remaining(tmp_path) == {"self", "keep"}


@needs_tmux
def test_kill_dry_run_force_own_session_last_keeps_sessions(tm):
    """現状固定: -n と -f を同時に与えると、自分のセッションも予定だけ出して残す。

    do_kill の LAST ブロックの DRY=1 分岐を通す。FORCE=1 で自分のセッション
    (devbase-3) は LAST に回り、DRY=1 なので落とさず ``(dry-run)`` を出す。
    ほかの対象 (other) を先に、自分を後に、どちらも予定表示だけになる。
    """
    sid = tm.new("devbase-3")
    tm.new("other")
    tm.new("keep")
    inside = tm.inside_env(sid)
    before = set(tm.sessions())

    done = tm.run("tmux-kill", "-n", "-f", "devbase-3", "other", env=inside)

    assert done.returncode == 0, done.stderr
    lines = [line for line in done.stdout.splitlines() if line.startswith("KILL")]
    assert lines == ["KILL other (dry-run)", "KILL devbase-3 (dry-run)"]
    assert set(tm.sessions()) == before


@needs_tmux
def test_kill_own_session_with_client_keeps_it_and_kills_others(tm):
    """現状固定: -c で自分の端末を渡すと、そのセッションは -f 無しでは残す。

    HAVE_CLIENT=1 のため知らせは端末の状態行へ出て標準出力は空になる。SELF_SID は
    -c の端末が見ている home に解決され、-f が無いので home は残り rc=1。自分でない
    keep は落ちる。
    """
    home = tm.new("home")
    tm.new("keep")
    me = tm.attach(home)

    done = tm.run("tmux-kill", "-c", me.tty, "home", "keep")

    assert done.returncode == 1
    assert done.stdout == ""
    assert set(tm.sessions()) == {"home"}


@needs_tmux
def test_kill_dry_run_keeps_sessions(tm):
    """条件 9: -n は落とさずに予定を出す。"""
    tm.new("devbase-3")
    before = tm.sessions()

    done = tm.run("tmux-kill", "-n", "devbase-3")

    assert done.returncode == 0, done.stderr
    assert "KILL devbase-3 (dry-run)" in done.stdout
    assert tm.sessions() == before


@needs_tmux
@pytest.mark.parametrize("name", SPECIAL_NAMES)
def test_kill_special_names(tm, name):
    """条件 10: 名前どおりのセッションだけを落とす。"""
    tm.new(name)
    tm.new("keep")
    done = tm.run("tmux-kill", name)
    assert done.returncode == 0, done.stderr
    assert set(tm.sessions()) == {"keep"}


# --- 調べる (受け入れ条件 4・5・10・12) ---


def _observe(tm: TmuxEnv) -> tuple[str, str]:
    clients = tm.tmux("list-clients", "-F", "#{client_name} #{session_id}").stdout
    sessions = tm.tmux("list-sessions", "-F", "#{session_id} #{session_name}").stdout
    return clients, sessions


@needs_tmux
@pytest.mark.parametrize("argv", [("tmux-peek",), ("tmux-session", "peek")])
def test_peek_shows_clients_panes_processes_and_screen(tm, argv):
    """条件 4・5・12: 4 節を出し、tmux の状態を変えない。"""
    sid = tm.new("devbase-2")
    client = tm.attach(sid)
    tm.tmux("send-keys", "-t", sid,
            "echo peek-marker; sleep 300 & sh -c 'sleep 301; true'", "Enter")
    _wait(lambda: "sleep 301" in subprocess.run(
        ["ps", "-A", "-o", "args="], capture_output=True, text=True).stdout)
    _wait(lambda: "peek-marker" in tm.tmux("capture-pane", "-p", "-t", sid).stdout)
    before = _observe(tm)

    done = tm.run(*argv, "devbase-2")

    assert done.returncode == 0, done.stderr
    out = done.stdout
    assert re.search(r"^session\s+devbase-2 \(\$\d+\)", out, re.M), out
    assert "clients" in out and client.tty in out
    assert "panes" in out and "pid=" in out
    assert "sleep 300" in out, "& で起動したものも子孫として出る"
    assert "sleep 301" in out, "孫のプロセスも出る"
    assert re.search(r"^screen", out, re.M) and "peek-marker" in out
    assert _observe(tm) == before


@needs_tmux
def test_peek_screen_lines(tm):
    """``-n`` で画面の行数を絞り、``-n 0`` で画面の節を出さない。"""
    sid = tm.new("devbase-2")
    tm.tmux("send-keys", "-t", sid, "for i in 1 2 3 4 5; do echo line-$i; done", "Enter")
    _wait(lambda: "line-5" in tm.tmux("capture-pane", "-p", "-t", sid).stdout)

    three = tm.run("tmux-peek", "-n", "3", "devbase-2").stdout
    screen = three.split("\nscreen", 1)[1].splitlines()[1:]
    assert len(screen) == 3, screen
    assert "line-5" in screen[1]

    none = tm.run("tmux-peek", "-n", "0", "devbase-2").stdout
    assert "\nscreen" not in none


@needs_tmux
def test_peek_without_clients_and_n_beyond_screen(tm):
    """端末が 0 件なら clients 節は (なし) の 1 行、``-n`` が画面の行数を超えたら先頭から出す。"""
    sid = tm.new("devbase-2")
    tm.tmux("send-keys", "-t", sid, "for i in 1 2; do echo line-$i; done", "Enter")
    _wait(lambda: "line-2" in tm.tmux("capture-pane", "-p", "-t", sid).stdout)

    done = tm.run("tmux-peek", "-n", "50", "devbase-2")

    assert done.returncode == 0, done.stderr
    out = done.stdout
    assert re.search(r"^session\s.*attached 0$", out, re.M), out
    clients = out.split("\nclients\n", 1)[1].split("\npanes\n", 1)[0].splitlines()
    assert len(clients) == 1, clients
    assert "/dev/" not in clients[0], clients
    screen = out.split("\nscreen", 1)[1].splitlines()[1:]
    assert any("line-1" in l for l in screen), screen
    assert any("line-2" in l for l in screen), screen
    assert screen[-1].strip(), screen


@needs_tmux
@pytest.mark.parametrize("name", SPECIAL_NAMES)
def test_peek_special_names(tm, name):
    """条件 10"""
    tm.new(name)
    tm.new("other")
    done = tm.run("tmux-peek", name)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith(f"session  {name} (")


# --- 移る (受け入れ条件 1〜3・10・12) ---


@needs_tmux
@pytest.mark.parametrize("argv", [("tmux-go",), ("tmux-session", "go")])
def test_go_outside_attaches_and_detaches_others(tm, argv):
    """条件 1・12: tmux の外。devbase-1 の端末は外れ、devbase-10 の端末は残る。"""
    one = tm.new("devbase-1")
    ten = tm.new("devbase-10")
    old = tm.attach(one)
    neighbour = tm.attach(ten)

    me = tm.spawn([str(tm.bin / argv[0]), *argv[1:], "devbase-1"])

    _wait(lambda: tm.clients_of(one) == {me.tty})
    assert tm.clients_of(ten) == {neighbour.tty}
    _wait(lambda: old.proc.poll() is not None)


@needs_tmux
def test_go_with_client_detaches_others_then_switches(tm):
    """条件 2 (-c): 対象の他の端末だけを外し、実行元を切り替える。"""
    target = tm.new("devbase-3")
    home = tm.new("home")
    other = tm.new("other")
    me = tm.attach(home)
    stale = tm.attach(target)
    bystander = tm.attach(other)

    done = tm.run("tmux-go", "-c", me.tty, "devbase-3")

    assert done.returncode == 0, done.stderr
    assert done.stdout == "", "-c のときは標準出力へ出さない"
    _wait(lambda: tm.clients_of(target) == {me.tty})
    assert tm.clients_of(other) == {bystander.tty}
    assert stale.tty not in tm.all_clients()


@needs_tmux
def test_go_from_prompt_switches_the_typing_client(tm):
    """条件 2 (プロンプト): 打ち込んだ端末を実行元と認める。"""
    target = tm.new("devbase-3")
    home = tm.new("home")
    me = tm.attach(home)
    stale = tm.attach(target)
    time.sleep(0.5)  # シェルがプロンプトを出すまで

    me.send("tmux-go devbase-3\r")

    _wait(lambda: tm.clients_of(target) == {me.tty})
    assert stale.tty not in tm.all_clients()


@needs_tmux
def test_go_inside_without_known_client_does_nothing(tm):
    """条件 3: 実行元を特定できない (最終操作が 10 秒より前) ときは何もしない。"""
    target = tm.new("devbase-3")
    home = tm.new("home")
    tm.attach(home)
    tm.attach(target)
    time.sleep(11)
    before = tm.all_clients()

    done = tm.run("tmux-go", "devbase-3", env=tm.inside_env(home))

    assert done.returncode == 1
    assert "switch-client" in done.stderr
    assert tm.all_clients() == before


@needs_tmux
@pytest.mark.parametrize("name", SPECIAL_NAMES)
def test_go_special_names(tm, name):
    """条件 10"""
    sid = tm.new(name)
    tm.new("other")
    old = tm.attach(sid)

    me = tm.spawn([str(tm.bin / "tmux-go"), name])

    _wait(lambda: tm.clients_of(sid) == {me.tty})
    _wait(lambda: old.proc.poll() is not None)


# --- tmux の中の UI (受け入れ条件 14・15) ---


def _open_tree(tm: TmuxEnv, me: Client, attempts: int = 5) -> None:
    """``prefix S`` で一覧 (tree-mode) を出す。

    繋いだ直後のクライアントは端末への問い合わせの応答を待っている間の入力を捨てる
    ことがある (CI で 10 秒待っても一覧が出なかった)。一覧が出なければ送り直す。
    """
    def in_tree() -> bool:
        return tm.tmux("display-message", "-p", "-c", me.tty,
                       "#{pane_mode}").stdout.strip() == "tree-mode"

    for _ in range(attempts - 1):
        me.send("\x02S")
        with contextlib.suppress(AssertionError):
            _wait(in_tree, timeout=2.0)
            return
    me.send("\x02S")
    _wait(in_tree)


def _open_menu(tm: TmuxEnv, me: Client) -> None:
    """``prefix S`` で一覧を出し、1 つ上のセッションを選ぶ。"""
    _open_tree(tm, me)
    me.send("\x1b[A")
    time.sleep(0.2)
    me.send("\r")


@pytest.fixture
def fake_tm():
    """引数を書き出すだけの偽の ``tmux-session`` を PATH の先頭に置いた環境。"""
    root = _short_root()
    fake = root / "fake"
    fake.mkdir()
    record = root / "args"
    stub = fake / "tmux-session"
    stub.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{record}'\n")
    stub.chmod(0o755)
    env = TmuxEnv(root, conf=TMUX_CONF, fake=fake)
    env.record = record
    try:
        yield env
    finally:
        env.close()


@needs_tmux
@pytest.mark.parametrize("name", SPECIAL_NAMES)
def test_prefix_s_passes_selected_id_and_client(fake_tm, name):
    """条件 14: 選んだセッションの ID と、押した端末の名前が menu へ渡る。

    一覧は名前順 (``-O name``) で、カーソルは今のセッション ``zz-home`` にある。
    1 つ上が選ぶセッションになる。
    """
    tm = fake_tm
    target = tm.new(name)
    home = tm.new("zz-home")
    me = tm.attach(home)

    _open_menu(tm, me)

    _wait(lambda: tm.record.exists() and tm.record.read_text())
    assert tm.record.read_text().splitlines() == ["menu", "-c", me.tty, target]


@pytest.fixture
def ui_tm():
    env = TmuxEnv(_short_root(), conf=TMUX_CONF)
    try:
        yield env
    finally:
        env.close()


@needs_tmux
@pytest.mark.parametrize("name", ["devbase-3", "it's"])
def test_menu_go_detaches_others_and_switches(ui_tm, name):
    """条件 15 (移る): メニューの ``a`` が go を呼ぶ。"""
    tm = ui_tm
    target = tm.new(name)
    home = tm.new("zz-home")
    me = tm.attach(home)
    stale = tm.attach(target)

    _open_menu(tm, me)
    _wait(lambda: "移る" in me.output())
    me.send("a")

    _wait(lambda: tm.clients_of(target) == {me.tty})
    assert stale.tty not in tm.all_clients()


@needs_tmux
@pytest.mark.parametrize("name", ["devbase-3", 'q"x;$1'])
def test_menu_kill_asks_then_kills(ui_tm, name):
    """条件 15 (落とす): 確認を挟み、y で落とす。"""
    tm = ui_tm
    tm.new(name)
    home = tm.new("zz-home")
    me = tm.attach(home)

    _open_menu(tm, me)
    _wait(lambda: "落とす" in me.output())
    me.send("k")
    _wait(lambda: "(y/n)" in me.output())
    assert name in tm.sessions(), "確認の前には落とさない"
    me.send("y")

    _wait(lambda: name not in tm.sessions())
    assert "zz-home" in tm.sessions()


@needs_tmux
def test_menu_kill_own_session(ui_tm):
    """メニューの「落とす」は -f 付きで、今いるセッションも同意で落とせる。"""
    tm = ui_tm
    tm.new("keep")
    home = tm.new("zz-home")
    me = tm.attach(home)

    _open_tree(tm, me)
    me.send("\r")  # カーソルは今のセッション
    _wait(lambda: "落とす" in me.output())
    me.send("k")
    _wait(lambda: "(y/n)" in me.output())
    me.send("y")

    _wait(lambda: "zz-home" not in tm.sessions())
    assert "keep" in tm.sessions()


@needs_tmux
def test_menu_peek_opens_popup(ui_tm):
    """条件 15 (中身を見る): popup に peek の出力が出て、Enter で閉じる。"""
    tm = ui_tm
    target = tm.new("devbase-3")
    tm.tmux("send-keys", "-t", target, "echo popup-marker", "Enter")
    home = tm.new("zz-home")
    me = tm.attach(home)

    _open_menu(tm, me)
    _wait(lambda: "中身を見る" in me.output())
    me.send("p")

    _wait(lambda: "popup-marker" in me.output() and "Enter" in me.output())
    me.send("\r")
    assert "devbase-3" in tm.sessions(), "見るだけで落とさない"


@needs_tmux
def test_menu_notifies_client_on_failure(ui_tm):
    """背景の run-shell から ``display-message -c`` で端末へ知らせが届く。"""
    tm = ui_tm
    tm.new("zz-home")
    me = tm.attach(tm.sid("zz-home"))

    tm.tmux("run-shell", "-b", f"tmux-go -c {me.tty} no-such-session")

    _wait(lambda: "no-such-session" in me.output())


# --- tmux-menu: 一覧を開く形 (PLAN71) ---
#
# 一覧は名前順 (``-O name``) で、カーソルは開いた pane のセッション ``zz-home`` にある。
# 選ぶセッションの名前はどれも ``zz-home`` より前に並ぶため、1 つ上が選ぶセッションになる。

# 一覧を開く形を打つコマンド行 (``tm.bin`` からの相対)。条件 8 で両方の名前を走らせる。
LIST_COMMANDS = ["tmux-menu", "tmux-session menu"]


def _pane_mode(tm: TmuxEnv, sid: str) -> str:
    return tm.tmux("display-message", "-p", "-t", sid, "#{pane_mode}").stdout.strip()


def _type_list_command(tm: TmuxEnv, sid: str, command: str) -> None:
    """``sid`` の pane のプロンプトへ一覧を開くコマンド行を打ち、一覧が出るまで待つ。

    繋いだ端末ではなく pane へ ``send-keys`` で直接送る (繋いだ直後の端末は入力を捨てる
    ことがある)。コマンドは ``tm.bin`` の絶対パスで打つ (``fake_tm`` では名前の
    ``tmux-session`` が ``fake/`` の偽物に解決されるため)。
    """
    tm.tmux("send-keys", "-t", sid, f"{tm.bin}/{command}", "Enter")
    _wait(lambda: _pane_mode(tm, sid) == "tree-mode")


def _pick_above(tm: TmuxEnv, me: Client, sid: str, attempts: int = 5) -> None:
    """``sid`` の pane の一覧で 1 つ上を選び、``me`` の端末から Enter を送る。

    Up は ``send-keys`` で送る (端末を通らずに一覧を動かす)。Enter は ``me`` から送る。
    ``send-keys`` の Enter では template の ``#{client_name}`` が直近に操作された端末に
    なり、押した端末を確かめられない。繋いだ直後の ``me`` は Enter を捨てることがあるため、
    一覧が残っていれば 2 秒ごとに送り直す。一覧を抜けたら ``me`` は入力を受け付けている。
    """
    tm.tmux("send-keys", "-t", sid, "Up")
    for _ in range(attempts - 1):
        me.send("\r")
        with contextlib.suppress(AssertionError):
            _wait(lambda: _pane_mode(tm, sid) != "tree-mode", timeout=2.0)
            return
    me.send("\r")
    _wait(lambda: _pane_mode(tm, sid) != "tree-mode")


@needs_tmux
@pytest.mark.parametrize("command", LIST_COMMANDS)
def test_list_inside_opens_tree_in_pane(ui_tm, command):
    """PLAN71 条件 1・8: tmux の中で打つと、その pane に一覧が出る。"""
    tm = ui_tm
    home = tm.new("zz-home")
    tm.attach(home)

    _type_list_command(tm, home, command)


@needs_tmux
@pytest.mark.parametrize("name", SPECIAL_NAMES)
def test_list_inside_passes_selected_id_and_client(fake_tm, name):
    """PLAN71 条件 2: 一覧で選ぶと、prefix S と同じ引数で menu が呼ばれる。"""
    tm = fake_tm
    target = tm.new(name)
    home = tm.new("zz-home")
    me = tm.attach(home)

    _type_list_command(tm, home, "tmux-menu")
    _pick_above(tm, me, home)

    _wait(lambda: tm.record.exists() and tm.record.read_text())
    assert tm.record.read_text().splitlines() == ["menu", "-c", me.tty, target]


@needs_tmux
@pytest.mark.parametrize("name", ["devbase-3", "it's"])
def test_list_inside_menu_go_detaches_others_and_switches(ui_tm, name):
    """PLAN71 条件 3: メニューの ``a`` で Enter を押した端末が移り、他の端末は外れる。"""
    tm = ui_tm
    target = tm.new(name)
    home = tm.new("zz-home")
    me = tm.attach(home)
    stale = tm.attach(target)

    _type_list_command(tm, home, "tmux-menu")
    _pick_above(tm, me, home)
    _wait(lambda: "移る" in me.output())
    me.send("a")

    _wait(lambda: tm.clients_of(target) == {me.tty})
    assert stale.tty not in tm.all_clients()


def _spawn_outside(tm: TmuxEnv, home: str) -> Client:
    """tmux の外で ``tmux-menu`` を起動し、``home`` に繋がって一覧が出るまで待つ。"""
    me = tm.spawn([str(tm.bin / "tmux-menu")])
    _wait(lambda: tm.all_clients().get(me.tty) == home)
    _wait(lambda: _pane_mode(tm, home) == "tree-mode")
    return me


@needs_tmux
def test_list_outside_attaches_and_opens_tree(ui_tm):
    """PLAN71 条件 4: tmux の外では、端末の無いセッションへ attach して一覧を出す。"""
    tm = ui_tm
    target = tm.new("devbase-3")
    home = tm.new("zz-home")
    tm.attach(target)

    _spawn_outside(tm, home)


@needs_tmux
@pytest.mark.parametrize("name", SPECIAL_NAMES)
def test_list_outside_passes_selected_id_and_client(fake_tm, name):
    """PLAN71 条件 5: 外から開いた一覧で選ぶと、attach した端末と選んだ ID が渡る。"""
    tm = fake_tm
    target = tm.new(name)
    home = tm.new("zz-home")
    tm.attach(target)

    me = _spawn_outside(tm, home)
    _pick_above(tm, me, home)

    _wait(lambda: tm.record.exists() and tm.record.read_text())
    assert tm.record.read_text().splitlines() == ["menu", "-c", me.tty, target]


@needs_tmux
@pytest.mark.parametrize("argv", [("tmux-menu",), ("tmux-session", "menu")])
def test_list_no_server_exits_one(tm, argv):
    """PLAN71 条件 6・8: サーバが無ければ、何も作らず 1 で終わる。"""
    done = tm.run(*argv)
    assert done.returncode == 1, (done.stdout, done.stderr)
    assert "サーバ" in done.stderr
    assert tm.sessions() == {}


@needs_tmux
@pytest.mark.parametrize("form", ["tmux-menu -c {tty} {sid}",
                                  "tmux-session menu -c {tty} {sid}"])
def test_menu_form_same_by_both_names(ui_tm, form):
    """PLAN71 条件 8: メニューを出す形は、どちらの名前でも同じメニューを出す。

    ``run-shell`` は文字列を ``sh -c`` へ渡すため、ID (``$0``) は引用して渡す。
    """
    tm = ui_tm
    target = tm.new("devbase-3")
    me = tm.attach(tm.new("zz-home"))

    tm.tmux("run-shell", "-b", form.format(tty=me.tty, sid=shlex.quote(target)))

    _wait(lambda: "中身を見る" in me.output())


@needs_tmux
def test_list_opens_in_tmux_pane_not_latest_client(tm):
    """PLAN71 条件 11: 一覧は ``TMUX_PANE`` の pane に出て、直近に操作された端末には出ない。

    後に繋いだ ``other`` の端末が直近になる (設計の実測 9a)。
    """
    home = tm.new("home")
    other = tm.new("other")
    tm.attach(home)
    tm.attach(other)

    done = tm.run("tmux-menu", env=tm.inside_env(home))

    assert done.returncode == 0, (done.stdout, done.stderr)
    _wait(lambda: _pane_mode(tm, home) == "tree-mode")
    assert _pane_mode(tm, other) == ""


@needs_tmux
def test_list_inside_without_tmux_pane_opens_tree(tm):
    """PLAN71: ``TMUX`` があり ``TMUX_PANE`` が空なら、``-t`` なしの一覧へ落ちて開く。

    端末が 1 つだけ繋がっていれば、その端末の pane に一覧が出る。
    """
    home = tm.new("home")
    tm.attach(home)
    env = {k: v for k, v in tm.inside_env(home).items() if k != "TMUX_PANE"}

    done = tm.run("tmux-menu", env=env)

    assert done.returncode == 0, (done.stdout, done.stderr)
    _wait(lambda: _pane_mode(tm, home) == "tree-mode")


# --- 配布 (受け入れ条件 16 のうちビルドの前に分かる部分) ---


def test_dockerfile_installs_tmux_session():
    lines = DOCKERFILE.read_text().splitlines()
    copies = [line for line in lines if line.startswith("COPY") and "tmux-session" in line]
    assert copies == ["COPY --chmod=0755 tmux-session /usr/local/bin/tmux-session"]


def test_dockerfile_links_short_names():
    text = DOCKERFILE.read_text()
    for name in SHORT_NAMES:
        assert re.search(rf"ln -sf tmux-session /usr/local/bin/{name}\b", text), name


def test_script_is_executable_posix_sh():
    assert SCRIPT.read_text().startswith("#!/bin/sh\n")
    assert os.access(SCRIPT, os.X_OK)
