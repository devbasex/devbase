"""実 TTY (pty) での questionary 統合テスト。

monkeypatch 単体テストでは questionary / prompt_toolkit 統合層の描画バグを検出
できない (PR #61 の実 TTY バグ 3 件が review をすり抜けた教訓) ため、pty 上で
menu.* を実際に起動し、pyte で端末画面を再構成して描画結果を検証する。

検証対象: 回答確定後のプロンプト行 (collapse 行) が画面から消去されること。
TUI はループでメニューを再描画するため、回答済み行が残ると 1 回答ごとに
画面が下へずれていく (プロンプト残留・行ずれ不具合)。
"""

from __future__ import annotations

import os
import pty
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pyte = pytest.importorskip("pyte")

fcntl = pytest.importorskip("fcntl")
termios = pytest.importorskip("termios")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_COLS, _ROWS = 100, 30

# pty 環境では prompt_toolkit の CPR 問い合わせに端末が応答しないため、
# この警告行が 1 度だけ出る。描画検証では無視する。
_CPR_WARNING = "cursor position requests"


class _PtySession:
    """pty 上で driver スクリプトを実行し、キー送出と画面再構成を行うハーネス。"""

    def __init__(self, driver_source: str):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ,
                    struct.pack("HHHH", _ROWS, _COLS, 0, 0))
        env = dict(os.environ, TERM="xterm-256color",
                   PYTHONPATH=str(_REPO_ROOT / "lib"))
        self.proc = subprocess.Popen(
            [sys.executable, "-c", driver_source],
            stdin=slave, stdout=slave, stderr=slave, env=env,
        )
        os.close(slave)
        self._buf = bytearray()
        self._pos = 0  # wait_for の走査開始位置 (同文言の再出現を区別する)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        while True:
            try:
                data = os.read(self.master, 4096)
            except OSError:
                break
            if not data:
                break
            self._buf += data

    def wait_for(self, text: str, timeout: float = 15.0):
        """出力に text が現れるまで待つ。走査位置を進め、同文言の再出現も待てる。"""
        needle = text.encode()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            idx = bytes(self._buf).find(needle, self._pos)
            if idx >= 0:
                self._pos = idx + len(needle)
                return
            time.sleep(0.05)
        raise AssertionError(
            f"timeout: {text!r} が出力に現れない\n--- raw ---\n{bytes(self._buf)!r}")

    def send(self, data: str):
        # プロンプト文言の出現直後はキーバインド有効化前の可能性があるため一拍置く。
        time.sleep(0.1)
        os.write(self.master, data.encode())

    def finish(self, timeout: float = 15.0) -> list[str]:
        """プロセス終了を待ち、pyte で再構成した最終画面 (空行除去済み) を返す。"""
        self.proc.wait(timeout=timeout)
        time.sleep(0.2)  # 終了直前の出力フラッシュを回収
        os.close(self.master)
        self._reader.join(timeout=3)
        screen = pyte.Screen(_COLS, _ROWS)
        stream = pyte.Stream(screen)
        stream.feed(bytes(self._buf).decode("utf-8", errors="replace"))
        return [line.rstrip() for line in screen.display if line.strip()]


_DRIVER = """
from devbase.tui import menu

OPS = [("再起動 (up)", "up"), ("停止 (down)", "down")]

sel = menu.select("SELECT-PLAIN を選択:", OPS, back=True)
print(f"@SEL1={sel!r}", flush=True)

sel = menu.select("SELECT-SEARCH を選択:", OPS, back=False, search=True)
print(f"@SEL2={sel!r}", flush=True)

ok = menu.confirm("CONFIRM 停止しますか?", default=False)
print(f"@OK={ok!r}", flush=True)

txt = menu.text("TEXT 入力:")
print(f"@TXT={txt!r}", flush=True)

p = menu.path("PATH 入力:")
print(f"@PATH={p!r}", flush=True)

back = menu.select("SELECT-BACK を選択:", OPS, back=True)
print("@BACK=" + ("BACK" if back is menu.MENU_BACK else repr(back)), flush=True)

print("@END", flush=True)
"""


@pytest.fixture
def session():
    s = _PtySession(_DRIVER)
    yield s
    if s.proc.poll() is None:
        s.proc.kill()


def test_answered_prompts_are_erased(session):
    """全プロンプト (select/confirm/text/path) の回答済み行が画面に残留しないこと。

    残留すると TUI ループの再描画が 1 回答ごとに下へずれる (実 TTY のみで再現)。
    """
    session.wait_for("SELECT-PLAIN")
    session.send("\x1b[B")           # ↓ で「停止 (down)」へ
    session.send("\r")
    session.wait_for("@SEL1='down'")

    session.wait_for("SELECT-SEARCH")
    session.send("\r")               # 先頭 (再起動 up) を Enter で確定
    session.wait_for("@SEL2='up'")

    session.wait_for("CONFIRM")
    session.send("n")                # auto_enter で即確定
    session.wait_for("@OK=False")

    session.wait_for("TEXT")
    session.send("abc\r")
    session.wait_for("@TXT='abc'")

    session.wait_for("PATH")
    session.send("xyz\r")
    session.wait_for("@PATH='xyz'")

    session.wait_for("SELECT-BACK")
    session.send("\x1b")             # Esc → MENU_BACK (PR #61 の戻り消去の非回帰)
    session.wait_for("@BACK=BACK")
    session.wait_for("@END")

    lines = session.finish()

    # 結果マーカーは順序どおり画面に残る (出力自体が壊れていないことの確認)
    markers = [ln for ln in lines if ln.startswith("@")]
    assert markers == [
        "@SEL1='down'", "@SEL2='up'", "@OK=False",
        "@TXT='abc'", "@PATH='xyz'", "@BACK=BACK", "@END",
    ]

    # 回答済みプロンプト行 (「? メッセージ 回答」の collapse 行) が残留しないこと
    residue = [
        ln for ln in lines
        if not ln.startswith("@") and _CPR_WARNING not in ln
    ]
    assert residue == [], f"プロンプト行が画面に残留: {residue}"
