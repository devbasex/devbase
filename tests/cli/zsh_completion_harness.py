"""zsh を起動して ``etc/_devbase`` の補完が実際に出す候補を集める harness（#455）。

外側の zsh が ``zsh/zpty`` で対話の zsh（``zsh -f -i``）を 1 つ起こし、``fpath`` に補完の
置き場を入れて ``compinit`` する。``compadd`` を包んで、加えた語を行ごとのファイルへ書き出す。
受け取った行を順に打って Tab で補完を求め、行ごとの候補を返す。

- 利用者の rc は読まない（``-f``）。環境は ``PATH``・``HOME``・``TERM``・``DEVBASE_ROOT`` だけを渡す
- 1 回の補完の要求（と起動・読み込み）に ``timeout`` 秒の応答が無ければ打ち切り、起動した
  プロセスを子孫まで終える。打ち切った行は「時間切れ」、それより後の行は「流していない」になる
- ``zpty`` の子は別のセッションで動くため、後始末はプロセスのグループでなく、外側の zsh の
  子孫を ``ps`` でたどって終える

``zsh_completion_harness.py`` は ``test_`` で始まらないため pytest は収集しない。
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPLETION_DIR = REPO_ROOT / "etc"

# 1 回の補完の要求につき待つ秒数（要求の受け入れ条件 8）
DEFAULT_TIMEOUT = 10.0

# 結果の状態
OK = "ok"
TIMEOUT = "時間切れ"
NOT_RUN = "流していない"

# 外側の zsh が流すスクリプト。引数は「出力の置き場・内側の zsh・fpath に入れる場所…・-- ・打つ行…」。
# 進み具合を $outdir/progress へ書き、Python の側がその止まりで時間切れを決める。
# 端末の出力は $outdir/term へ足す（読み込めないときの手がかり）。
_DRIVER = r"""
zmodload zsh/zselect; zmodload zsh/zpty || { print -r -- "zsh/zpty を読み込めない" >&2; exit 3; }
outdir=$1 inner=$2; shift 2
fp=()
while [[ $1 != -- ]]; do fp+=($1); shift; done
shift
progress() { print -r -- "$1" >> $outdir/progress; }
zpty z $inner -f -i
local buf; integer k=0
cmd() {
  (( k++ ))
  zpty -w z "$1; print __S\"\"${k}__"
  zpty -r z buf "*__S${k}__*"; print -rn -- $buf >> $outdir/term
  zpty -r z buf '*<P> *'; print -rn -- $buf >> $outdir/term
}
cmd "PS1='<''P> '; unsetopt auto_list list_ambiguous beep"
cmd "print -r -- \$\$ > $outdir/inner_pid"
cmd "fpath=(${(j: :)${(q)fp}} \$fpath); autoload -Uz compinit; compinit -u -D -d /dev/null"
cmd "compadd() { local -a __m; builtin compadd -O __m \"\$@\"; print -r -- __CALL__ >> \$PROBE_OUT; (( \$#__m )) && print -rl -- \$__m >> \$PROBE_OUT; builtin compadd \"\$@\"; }"
cmd "_probe() { zle complete-word; print -r -- __DONE__ >> \$PROBE_OUT; }; zle -N _probe; bindkey '^I' _probe"
progress ready
integer n=0
for line in "$@"; do
  (( n++ ))
  : > $outdir/$n
  cmd "PROBE_OUT=$outdir/$n"
  progress "start $n"
  zpty -w -n z "$line"$'\t'
  until [[ $(<$outdir/$n) == *__DONE__* ]]; do zselect -t 1; done
  progress "done $n"
  zpty -w -n z $'\x15'
  cmd ":"
done
zpty -d z
progress finished
"""


@dataclass
class LineResult:
    """1 行の結果。``status`` が ``OK`` のときだけ ``candidates`` と ``called`` に意味がある。"""

    line: str
    status: str
    candidates: frozenset[str] = frozenset()
    called: bool = False


@dataclass
class RunResult:
    lines: list[LineResult]
    terminal: str
    stderr: str
    # 起動したプロセス（外側と内側の zsh、その子孫）の pid。後始末の確かめに使う
    pids: set[int] = field(default_factory=set)
    # 起動・読み込みの途中で時間切れになった
    setup_timed_out: bool = False

    @property
    def loaded(self) -> bool:
        """どこかの行で包んだ ``compadd`` が呼ばれた（補完を読み込めた）。"""
        return any(r.called for r in self.lines)

    def terminal_tail(self, n: int = 2000) -> str:
        return (self.terminal + self.stderr)[-n:]


def missing_part(path: str | None = None) -> str | None:
    """zsh か ``zsh/zpty`` が欠けていれば、その名前を返す。そろっていれば ``None``。"""
    zsh = shutil.which("zsh", path=path)
    if zsh is None:
        return "zsh"
    try:
        done = subprocess.run([zsh, "-f", "-c", "zmodload zsh/zpty"], capture_output=True,
                              text=True, timeout=DEFAULT_TIMEOUT, env=_base_env(path))
    except (OSError, subprocess.TimeoutExpired):
        return "zsh/zpty"
    return None if done.returncode == 0 else "zsh/zpty"


def _base_env(path: str | None) -> dict[str, str]:
    return {"PATH": path if path is not None else os.environ.get("PATH", "")}


def _descendants(root: int) -> set[int]:
    """``root`` とその子孫の pid（``ps`` の pid / ppid の表から）。"""
    done = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True)
    children: dict[int, list[int]] = {}
    for row in done.stdout.splitlines():
        parts = row.split()
        if len(parts) == 2:
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = set(), [root]
    while stack:
        pid = stack.pop()
        if pid in found:
            continue
        found.add(pid)
        stack.extend(children.get(pid, ()))
    return found


def alive(pid: int) -> bool:
    """``pid`` のプロセスが残っている（ゾンビは残っていないと数える）。"""
    done = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    stat = done.stdout.strip()
    return bool(stat) and not stat.startswith("Z")


def _kill_all(pids: set[int]) -> None:
    for pid in pids:
        for kill in (os.killpg, os.kill):
            try:
                kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def _read_progress(outdir: Path) -> list[str]:
    try:
        return (outdir / "progress").read_text().splitlines()
    except FileNotFoundError:
        return []


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except FileNotFoundError:
        return ""


def run_completions(lines: list[str], *, devbase_root: Path, home: Path,
                    fpath: list[Path] | None = None, timeout: float = DEFAULT_TIMEOUT) -> RunResult:
    """``lines`` を 1 つの zsh のセッションで順に打ち、行ごとの補完の候補を返す。

    ``fpath`` は補完の置き場（既定は ``etc/``）。先頭ほど先に引かれる。
    """
    zsh = shutil.which("zsh")
    assert zsh is not None, "zsh が無い（missing_part で先に確かめる）"
    fpath = fpath or [COMPLETION_DIR]
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "TERM": "xterm",
           "DEVBASE_ROOT": str(devbase_root)}
    with tempfile.TemporaryDirectory(prefix="zc-") as tmp:
        outdir = Path(tmp)
        driver = outdir / "driver.zsh"
        driver.write_text(_DRIVER)
        argv = [zsh, "-f", str(driver), str(outdir), zsh, *map(str, fpath), "--", *lines]
        proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        pids: set[int] = {proc.pid}
        timed_out = False
        try:
            timed_out = _wait_until_stalled(proc, outdir, timeout)
        finally:
            stdout, stderr = _reap(proc, outdir, pids)
        progress = _read_progress(outdir)
        terminal = _read_text(outdir / "term") + stdout.decode(errors="replace")
        results = _line_results(lines, progress, outdir, timed_out)
        return RunResult(results, terminal, stderr.decode(errors="replace"), pids,
                         setup_timed_out=timed_out and "ready" not in progress)


def _wait_until_stalled(proc: subprocess.Popen, outdir: Path, timeout: float) -> bool:
    """zsh が終わるか、進みが ``timeout`` 秒止まるまで待つ。止まったら ``True``。"""
    seen, last_change = 0, time.monotonic()
    while proc.poll() is None:
        progress = _read_progress(outdir)
        if len(progress) != seen:
            seen, last_change = len(progress), time.monotonic()
        elif time.monotonic() - last_change > timeout:
            return True
        time.sleep(0.05)
    return False


def _reap(proc: subprocess.Popen, outdir: Path, pids: set[int]) -> tuple[bytes, bytes]:
    """外側の zsh と内側の zsh の子孫を終え、外側の出力を返す。終えた pid は ``pids`` へ足す。"""
    if proc.poll() is None:
        pids |= _descendants(proc.pid)
        _kill_all(pids)
    stdout, stderr = proc.communicate()
    # 内側の zsh は別のセッションで動き、外側が終わると親を失う。残っていれば子孫ごと終える
    inner = _read_text(outdir / "inner_pid").strip()
    if inner.isdigit():
        pids.add(int(inner))
        if alive(int(inner)):
            leftover = _descendants(int(inner))
            pids |= leftover
            _kill_all(leftover)
    return stdout, stderr


def _line_results(lines: list[str], progress: list[str], outdir: Path,
                  timed_out: bool) -> list[LineResult]:
    """完了の記録（``done``・``ready``）から行ごとの結果を並べる。"""
    done = {int(p.split()[1]) for p in progress if p.startswith("done ")}
    ready = "ready" in progress
    pending = [n for n in range(1, len(lines) + 1) if n not in done]
    # 時間切れの行は、読み込みを終えた後で最初に終わらなかった行
    stuck = pending[0] if timed_out and ready and pending else None
    results = []
    for n, line in enumerate(lines, start=1):
        if n in done:
            text = _read_text(outdir / str(n)).splitlines()
            words = frozenset(w for w in text if w and w not in ("__CALL__", "__DONE__"))
            results.append(LineResult(line, OK, words, "__CALL__" in text))
        elif n == stuck:
            results.append(LineResult(line, TIMEOUT))
        else:
            results.append(LineResult(line, NOT_RUN))
    return results
