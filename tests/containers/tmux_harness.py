"""``containers/base`` の tmux のコマンドを実物の tmux で試すための共通の harness。

利用者の tmux サーバーには触れない。``TmuxEnv`` は ``$TMPDIR`` 直下の短い名前の
ディレクトリへ ``TMUX_TMPDIR`` を向け、pytest が継承した ``TMUX`` / ``TMUX_PANE`` を消した
環境を 1 つ作る (``TMUX`` が残ると tmux は ``TMUX_TMPDIR`` を無視して利用者のサーバーへ繋ぐ)。
スクリプトは ``-S`` を受け取らず既定のソケットへ繋ぐため、テスト側の操作とスクリプトの実行を
同じ環境でそろえる。

``ScriptTmuxEnv`` は、そのうえで ``PATH`` の先頭の ``fake/`` に故障を差し込む ``tmux`` と
偽の ``date`` を置く。``tmux_harness.py`` は ``test_`` で始まらないため pytest は収集しない。
"""

from __future__ import annotations

import contextlib
import os
import pty
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2] / "containers" / "base"
SCRIPT = BASE_DIR / "tmux-session"
SHORT_NAMES = ("tmux-go", "tmux-peek", "tmux-kill", "tmux-menu")

needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux が無い環境")


def wait(predicate, timeout: float = 10.0, interval: float = 0.05):
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
        wait(lambda: self.all_clients().get(client.tty) == sid)
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
        # needs_tmux の付かないテストも tm を使う。tmux が無い環境でも後片付けを終える。
        if shutil.which("tmux", path=self.env["PATH"]) is not None:
            self.tmux("kill-server", check=False)
        for client in self.clients:
            client.close()
        shutil.rmtree(self.root, ignore_errors=True)


def short_root() -> Path:
    # UNIX ソケットのパス長には OS の上限 (macOS で 104 バイト) があるため、pytest の
    # 一時ディレクトリではなく $TMPDIR 直下の短い名前を使う。
    return Path(tempfile.mkdtemp(prefix="dvb69-", dir=tempfile.gettempdir()))



# 故障の表 (<root>/faults) の 1 行 = 1 規則。<動作>\t<前方一致の文字列>。
# 引数を空白 1 つで連結した "$*" が規則の文字列で始まる呼び出しだけに効く。
# 当たらない呼び出しは実物の tmux へ引数・環境・標準入出力をそのまま渡す。
_FAULT_WRAPPER = """#!/bin/sh
real={real}
faults={faults}
if [ -f "$faults" ]; then
	args="$*"
	tab=$(printf '\\t')
	while IFS="$tab" read -r action prefix; do
		[ -n "$action" ] || continue
		case "$args" in
		"$prefix"*) ;;
		*) continue ;;
		esac
		case "$action" in
		fail)
			echo "injected failure: $args" >&2
			exit 1
			;;
		vanish=*)
			"$real" kill-session -t "=${{action#vanish=}}" >/dev/null 2>&1 || true
			break
			;;
		esac
	done < "$faults"
fi
exec "$real" "$@"
"""

# date +%s にだけ FAKE_DATE_OFFSET 秒を足す。ほかの呼び出しは実物へ渡す。
_FAKE_DATE = """#!/bin/sh
real={real}
if [ "$#" = 1 ] && [ "$1" = "+%s" ]; then
	now=$("$real" +%s)
	echo $((now + ${{FAKE_DATE_OFFSET:-0}}))
	exit 0
fi
exec "$real" "$@"
"""


class ScriptTmuxEnv(TmuxEnv):
    """``fake/`` に故障を差し込む ``tmux`` と偽の ``date`` を置いた隔離環境。

    故障の表は、準備 (セッションと端末を作る) が終わってから書く。準備の tmux の操作も
    ラッパーを通るため、先に書くと準備そのものが失敗する。
    """

    def __init__(self, root: Path):
        fake = root / "fake"
        fake.mkdir()
        self.faults = root / "faults"
        real_tmux = shutil.which("tmux") or "/nonexistent/tmux"
        real_date = shutil.which("date") or "/bin/date"
        for name, template, real in (("tmux", _FAULT_WRAPPER, real_tmux),
                                     ("date", _FAKE_DATE, real_date)):
            path = fake / name
            path.write_text(template.format(real=shlex.quote(real),
                                            faults=shlex.quote(str(self.faults))))
            path.chmod(0o755)
        super().__init__(root, fake=fake)

    def _add_rule(self, action: str, prefix: str) -> None:
        with self.faults.open("a") as table:
            table.write(f"{action}\t{prefix}\n")

    def fail(self, prefix: str) -> None:
        """``prefix`` で始まる tmux の呼び出しを、実物を呼ばずに終了コード 1 で終える。"""
        self._add_rule("fail", prefix)

    def vanish(self, session: str, prefix: str) -> None:
        """``prefix`` で始まる tmux の呼び出しの直前に、``session`` を実物で消す。"""
        self._add_rule(f"vanish={session}", prefix)

    def sh(self, script: str, *args: str, env: dict[str, str] | None = None,
           clock: int = 0) -> subprocess.CompletedProcess:
        """``sh containers/base/<script> <args...>`` を実行する (git の上で実行権が無い)。

        ``clock`` 秒だけ ``date +%s`` を進める。``env`` の既定は tmux の外 (``self.env``)。
        """
        run_env = {**(env or self.env), "FAKE_DATE_OFFSET": str(clock)}
        return subprocess.run(["sh", str(BASE_DIR / script), *args], capture_output=True,
                              text=True, env=run_env, timeout=30)
