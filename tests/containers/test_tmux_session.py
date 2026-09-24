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
import pty
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2] / "containers" / "base"
SCRIPT = BASE_DIR / "tmux-session"
TMUX_CONF = BASE_DIR / "tmux.conf"
DOCKERFILE = BASE_DIR / "Dockerfile"
SHORT_NAMES = ("tmux-go", "tmux-peek", "tmux-kill")

needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux が無い環境")

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


def _wait(predicate, timeout: float = 10.0, interval: float = 0.05):
    """``predicate()`` が真を返すまで待ち、その値を返す。待ち切れなければ失敗にする。"""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"待ち切れなかった: {predicate}")
        time.sleep(interval)


class Client:
    """pty に繋いだ 1 つのプロセス (tmux のクライアント)。出力を読み続ける。"""

    def __init__(self, argv: list[str], env: dict[str, str]):
        self.controller, terminal = pty.openpty()
        self.tty = os.ttyname(terminal)
        self.proc = subprocess.Popen(argv, stdin=terminal, stdout=terminal,
                                     stderr=terminal, env=env)
        os.close(terminal)
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        while True:
            try:
                data = os.read(self.controller, 65536)
            except OSError:
                return
            if not data:
                return
            with self._lock:
                self._buf += data

    def output(self) -> str:
        with self._lock:
            return self._buf.decode("utf-8", "replace")

    def send(self, data: str) -> None:
        os.write(self.controller, data.encode())

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=5)
        with contextlib.suppress(OSError):
            os.close(self.controller)


class TmuxEnv:
    """テスト専用の tmux サーバーと、それを指す環境。"""

    def __init__(self, root: Path, conf: Path | None = None, fake: Path | None = None):
        self.root = root
        self.conf = conf or Path("/dev/null")
        self.bin = root / "bin"
        self.bin.mkdir()
        for name in ("tmux-session", *SHORT_NAMES):
            (self.bin / name).symlink_to(SCRIPT)
        path = f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}"
        if fake is not None:
            path = f"{fake}:{path}"
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("TMUX", "TMUX_PANE", "ENV", "BASH_ENV")}
        self.env.update(TMUX_TMPDIR=str(root), TERM="xterm-256color",
                        SHELL="/bin/sh", PATH=path, PS1="$ ", LANG="C.UTF-8")
        self.clients: list[Client] = []

    # --- tmux の操作 ---

    def tmux(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        done = subprocess.run(["tmux", *args], capture_output=True, text=True,
                              env=self.env)
        if check:
            assert done.returncode == 0, f"tmux {args} が失敗した: {done.stderr}"
        return done

    def new(self, name: str, *cmd: str) -> str:
        """セッションを作り、その ID を返す。最初の 1 つがサーバーを起動する。"""
        self.tmux("-f", str(self.conf), "new-session", "-d", "-s", name,
                  "-x", "120", "-y", "40", *cmd)
        return self.sid(name)

    def sessions(self) -> dict[str, str]:
        """``{名前: ID}``。サーバーが無ければ空。"""
        done = self.tmux("list-sessions", "-F", "#{session_id} #{session_name}",
                         check=False)
        result = {}
        for line in done.stdout.splitlines():
            sid, _, name = line.partition(" ")
            result[name] = sid
        return result

    def sid(self, name: str) -> str:
        return self.sessions()[name]

    def clients_of(self, sid: str) -> set[str]:
        done = self.tmux("list-clients", "-t", sid, "-F", "#{client_name}", check=False)
        return set(done.stdout.split())

    def all_clients(self) -> dict[str, str]:
        """``{端末名: セッション ID}``"""
        done = self.tmux("list-clients", "-F", "#{client_name} #{session_id}", check=False)
        return dict(line.split(" ", 1) for line in done.stdout.splitlines())

    def spawn(self, argv: list[str], env: dict[str, str] | None = None) -> Client:
        client = Client(argv, env or self.env)
        self.clients.append(client)
        return client

    def attach(self, sid: str) -> Client:
        """端末を 1 つ ``sid`` へ繋ぎ、繋がるまで待つ。"""
        client = self.spawn(["tmux", "attach-session", "-t", sid])
        _wait(lambda: self.all_clients().get(client.tty) == sid)
        return client

    def inside_env(self, sid: str) -> dict[str, str]:
        """``sid`` の pane の中のシェルと同じ ``TMUX`` / ``TMUX_PANE`` を持つ環境。"""
        shown = self.tmux("display-message", "-p", "-t", sid,
                          "#{socket_path},#{pid},0 #{pane_id}").stdout.strip()
        tmux_var, pane = shown.split(" ")
        return {**self.env, "TMUX": tmux_var, "TMUX_PANE": pane}

    # --- tmux-session の実行 ---

    def run(self, *argv: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        prog, *args = argv
        return subprocess.run([str(self.bin / prog), *args], capture_output=True,
                              text=True, env=env or self.env, timeout=30)

    def close(self) -> None:
        self.tmux("kill-server", check=False)
        for client in self.clients:
            client.close()
        shutil.rmtree(self.root, ignore_errors=True)


def _short_root() -> Path:
    # UNIX ソケットのパス長には OS の上限 (macOS で 104 バイト) があるため、pytest の
    # 一時ディレクトリではなく $TMPDIR 直下の短い名前を使う。
    return Path(tempfile.mkdtemp(prefix="dvb69-", dir=tempfile.gettempdir()))


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
                                  ("tmux-kill", "-h")])
def test_help_exits_zero(tm, argv):
    done = tm.run(*argv)
    assert done.returncode == 0, done.stderr
    assert "tmux-session" in done.stdout


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


def _open_menu(tm: TmuxEnv, me: Client) -> None:
    """``prefix S`` で一覧を出し、1 つ上のセッションを選ぶ。"""
    me.send("\x02S")
    _wait(lambda: tm.tmux("display-message", "-p", "-c", me.tty,
                          "#{pane_mode}").stdout.strip() == "tree-mode")
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

    me.send("\x02S")
    _wait(lambda: tm.tmux("display-message", "-p", "-c", me.tty,
                          "#{pane_mode}").stdout.strip() == "tree-mode")
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
