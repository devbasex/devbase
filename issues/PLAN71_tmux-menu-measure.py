#!/usr/bin/env python3
"""PLAN71 の実測: choose-tree の開き方ごとに、一覧が出た pane と menu へ渡った引数を見る。

使い方: python3 issues/PLAN71_tmux-menu-measure.py
一時ディレクトリのソケットでサーバを立て、利用者の tmux サーバに触れない。
"""

import os
import pty
import select
import subprocess
import tempfile
import time

TEMPLATE = 'run-shell -t "%%%" "tmux-session menu -c #{q:client_name} #{q:session_id}"'
WORK = tempfile.mkdtemp(prefix="plan71-")
SOCK = os.path.join(WORK, "sock")
LOG = os.path.join(WORK, "menu.log")
BINDIR = os.path.join(WORK, "bin")

SCRIPTS = {
    # 本物の代わりに、受け取った引数を書き出すだけの tmux-session
    "tmux-session": f'#!/bin/sh\nprintf "%s\\n" "$*" >> {LOG}\n',
    # tmux-menu の代わり。TMUX_PANE があれば -t に使う（設計の「一覧を開く形」と同じ分岐）
    "open-list": (
        "#!/bin/sh\n"
        f"exec tmux choose-tree ${{TMUX_PANE:+-t \"$TMUX_PANE\"}} -Zs -O name '{TEMPLATE}'\n"
    ),
    # open-list から -t を落としたもの（TMUX_PANE があっても -t に使わない）
    "open-list-no-t": f"#!/bin/sh\nexec tmux choose-tree -Zs -O name '{TEMPLATE}'\n",
    # TMUX_PANE を消してから -t を付けずに開く（TMUX_PANE を消す壊し方）
    "open-list-unset": (
        f"#!/bin/sh\nunset TMUX_PANE\nexec tmux choose-tree -Zs -O name '{TEMPLATE}'\n"
    ),
    # TMUX_PANE を消すが、消す前の値を -t に使う
    "open-list-unset-t": (
        "#!/bin/sh\n"
        'pane=$TMUX_PANE\nunset TMUX_PANE\n'
        f"exec tmux choose-tree -t \"$pane\" -Zs -O name '{TEMPLATE}'\n"
    ),
    # 一覧が開くまでの間に別の端末を操作するため、1 秒待ってから開く
    "open-list-slow": "#!/bin/sh\nsleep 1\nexec open-list\n",
}

os.mkdir(BINDIR)
for name, body in SCRIPTS.items():
    path = os.path.join(BINDIR, name)
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)

ENV = dict(
    os.environ, PATH=BINDIR + ":" + os.environ["PATH"], SHELL="/bin/sh", TERM="xterm"
)
ENV.pop("TMUX", None)
ENV.pop("TMUX_PANE", None)
TMUX = ["tmux", "-S", SOCK, "-f", "/dev/null"]


def t(*args):
    return subprocess.run(
        TMUX + list(args), env=ENV, capture_output=True, text=True, check=False
    ).stdout.strip()


def spawn(*args):
    """pty の端末で tmux を起動し、端末の fd を返す。"""
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe("tmux", TMUX + list(args), ENV)
    time.sleep(1)
    return fd


def drain(fd):
    while select.select([fd], [], [], 0.05)[0]:
        try:
            os.read(fd, 65536)
        except OSError:
            break


def pick(fd):
    """一覧で 1 つ上を選んで Enter を押す。"""
    time.sleep(0.8)
    os.write(fd, b"\x1b[A")
    time.sleep(0.3)
    os.write(fd, b"\r")
    time.sleep(0.8)


def state(label):
    ids = dict(
        line.split(" ", 1)
        for line in t(
            "list-sessions", "-F", "#{session_id} #{session_name}"
        ).splitlines()
    )
    clients = t("list-clients", "-F", "#{client_name}=#{session_name}")
    panes = t("list-panes", "-a", "-F", "#{session_name}:#{pane_id}:#{pane_mode}")
    got = ""
    if os.path.exists(LOG):
        with open(LOG) as f:
            got = f.read().strip()
    named = " ".join(ids.get(w, w) if w.startswith("$") else w for w in got.split())
    print(f"## {label}")
    print(f"  clients: {clients.split()}")
    print(f"  panes: {panes.split()}")
    print(f"  menu args: {got!r} -> {named!r}")


def reset():
    if os.path.exists(LOG):
        os.remove(LOG)
    for line in t("list-panes", "-a", "-F", "#{pane_id} #{pane_mode}").splitlines():
        pane, _, mode = line.partition(" ")
        if mode:
            t("send-keys", "-t", pane, "q")
    time.sleep(0.3)


def main():
    for name in ("a", "it's", "b"):
        t("new-session", "-d", "-s", name, "-x", "80", "-y", "24")
    c1 = spawn("attach", "-t", "=a")
    c2 = spawn("attach", "-t", "=b")

    # 1. 中のプロンプトで打つ（pane のシェルには TMUX_PANE がある）
    reset()
    drain(c1)
    os.write(c1, b"open-list\r")
    pick(c1)
    state("1 prompt: open-list (a's pane, TMUX_PANE set)")

    # 2. 外から attach \; choose-tree
    reset()
    c3 = spawn("attach", ";", "choose-tree", "-Zs", "-O", "name", TEMPLATE)
    pick(c3)
    state("2 outside: attach ; choose-tree")
    t("detach-client", "-t", t("list-clients", "-F", "#{client_name}").split()[-1])

    # 3. template を run-shell の文字列へ直接埋め込む
    reset()
    t("bind-key", "S", "run-shell", f"tmux choose-tree -Zs -O name '{TEMPLATE}'")
    os.write(c1, b"\x02S")
    pick(c1)
    state("3 bind: run-shell with inline template")

    # 4. run-shell からスクリプトを呼ぶ（-t なし）
    reset()
    t("bind-key", "S", "run-shell", "open-list")
    os.write(c1, b"\x02S")
    pick(c1)
    state("4 bind: run-shell open-list (no TMUX_PANE)")

    # 5. 4 の形で、一覧が開く前に別のセッションの端末が操作される
    reset()
    t("bind-key", "S", "run-shell", "open-list-slow")
    os.write(c1, b"\x02S")
    time.sleep(0.3)
    os.write(c2, b"x")
    time.sleep(1.5)
    state("5 bind: run-shell (no -t), other client typed during start")

    # 6. #{pane_id} を TMUX_PANE として渡す
    reset()
    t("bind-key", "S", "run-shell", "TMUX_PANE=#{pane_id} open-list-slow")
    os.write(c1, b"\x02S")
    time.sleep(0.3)
    os.write(c2, b"x")
    time.sleep(1.5)
    state("6 bind: run-shell TMUX_PANE=#{pane_id}, other client typed during start")
    reset()
    os.write(c1, b"\x02S")
    time.sleep(1)
    pick(c1)
    state("6b same binding, pick and Enter")

    # 7. 端末の無いプロセスから、a の pane の TMUX / TMUX_PANE を持って呼ぶ
    #    （テストの tm.run(..., env=tm.inside_env(home)) と同じ形）。先に b の端末へ打って直近を b にする
    tmux_var = t("display-message", "-p", "-t", "=a:", "#{socket_path},#{pid},0")
    a_pane = t("display-message", "-p", "-t", "=a:", "#{pane_id}")
    for label, prog, pane in (
        ("7a open-list-no-t, TMUX_PANE=a's pane", "open-list-no-t", a_pane),
        ("7b open-list (-t), TMUX_PANE=a's pane", "open-list", a_pane),
        ("7c open-list-no-t, no TMUX_PANE", "open-list-no-t", None),
        ("7d open-list-unset, TMUX_PANE=a's pane", "open-list-unset", a_pane),
        ("7e open-list-unset-t, TMUX_PANE=a's pane", "open-list-unset-t", a_pane),
    ):
        reset()
        os.write(c2, b"x")
        time.sleep(0.5)
        env = dict(ENV, TMUX=tmux_var)
        if pane:
            env["TMUX_PANE"] = pane
        subprocess.Popen(
            [prog], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        state(f"7 no tty, b typed last, TMUX_PANE={pane}: {label}")

    # 8. pane へ send-keys でコマンド行を打って一覧を開き、send-keys で 1 つ上へ動かす。
    #    直近を b の端末にしてから Enter を送り、#{client_name} がどの端末になるかを見る
    for label, enter_from_client in (
        ("8a Enter by send-keys", False),
        ("8b Enter by a's terminal", True),
    ):
        reset()
        t("send-keys", "-t", "=a:", os.path.join(BINDIR, "open-list"), "Enter")
        time.sleep(1)
        t("send-keys", "-t", "=a:", "Up")
        os.write(c2, b"x")
        time.sleep(0.5)
        if enter_from_client:
            os.write(c1, b"\r")
        else:
            t("send-keys", "-t", "=a:", "Enter")
        time.sleep(1)
        state(f"{label} (open by send-keys, b typed last)")

    t("kill-server")


if __name__ == "__main__":
    main()
