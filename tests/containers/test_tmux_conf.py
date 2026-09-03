"""base イメージへ焼き込む tmux 既定設定 (PLAN38)

``containers/base/tmux.conf`` を ``tmux -f`` で実際に読み込ませ、tmux が解釈した
実効値を ``show-options`` から読む。設定ファイルの文字列一致ではなく tmux 自身の
解釈を確認するため、書式の誤りや将来の tmux での非対応もここで落ちる。

Docker には依存しない (``test_entrypoint_repos.py`` と同じ方針)。``/etc/tmux.conf``
としての配置は Dockerfile の静的検査で担保し、実イメージでの確認は手動検証に回す
(PLAN38 Task 2)。
"""

from __future__ import annotations

import contextlib
import os
import pty
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2] / "containers" / "base"
TMUX_CONF = BASE_DIR / "tmux.conf"
DOCKERFILE = BASE_DIR / "Dockerfile"

needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux が無い環境")


def _parse_options(text: str) -> dict[str, list[str]]:
    """``show-options`` の出力を ``{名前: [値, ...]}`` にする。

    ``terminal-overrides[0] linux*:AX@`` のような配列オプションは添字を落として
    同じキーへ積む。値を持たないフラグ行は空文字列を 1 つ入れる。
    """
    options: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        name, _, value = line.partition(" ")
        name = name.split("[", 1)[0]
        options.setdefault(name, []).append(value.strip('"'))
    return options


def _effective_options(*configs: Path) -> dict[str, list[str]]:
    """``configs`` を順に読ませた tmux の session/server オプションを返す。

    専用ソケットを ``-S`` で明示し、実行中の利用者の tmux サーバーには触れない。
    UNIX ソケットのパス長には OS の上限 (macOS で 104 バイト) があるため、
    pytest の一時ディレクトリではなく ``$TMPDIR`` 直下の短い名前を使う。
    """
    socket = Path(tempfile.gettempdir()) / f"dvb38-{uuid.uuid4().hex[:8]}"
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    env["TERM"] = "xterm-256color"

    base = ["tmux", "-S", str(socket)]
    for config in configs:
        base += ["-f", str(config)]

    started = subprocess.run([*base, "new-session", "-d"],
                             capture_output=True, text=True, env=env)
    assert started.returncode == 0, f"tmux の起動に失敗した: {started.stderr}"
    try:
        options: dict[str, list[str]] = {}
        for scope in ("-g", "-s"):
            shown = subprocess.run(["tmux", "-S", str(socket), "show-options", scope],
                                   capture_output=True, text=True, env=env, check=True)
            options.update(_parse_options(shown.stdout))
        return options
    finally:
        subprocess.run(["tmux", "-S", str(socket), "kill-server"],
                       capture_output=True, text=True, env=env)
        with contextlib.suppress(FileNotFoundError):
            socket.unlink()


def _client_capabilities(config: Path) -> str:
    """``config`` を読ませた tmux へ実際に接続し、クライアント端末の能力表を返す。

    ``terminal-features`` の値は ``show-options`` から読めるが、tmux は綴りの違う
    機能名を黙って受け取る。宣言が能力として効いているかは、クライアントを繋いだ
    ときの ``show-messages -JT`` でしか確かめられない。接続には端末が要るため
    ``pty.openpty`` で用意する。
    """
    socket = Path(tempfile.gettempdir()) / f"dvb38-{uuid.uuid4().hex[:8]}"
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    env["TERM"] = "xterm-256color"
    base = ["tmux", "-S", str(socket)]

    started = subprocess.run([*base, "-f", str(config), "new-session", "-d", "-s", "p",
                              "sleep", "30"], capture_output=True, text=True, env=env)
    assert started.returncode == 0, f"tmux の起動に失敗した: {started.stderr}"

    controller, terminal = pty.openpty()
    client = subprocess.Popen([*base, "attach", "-t", "p"],
                              stdin=terminal, stdout=terminal, stderr=terminal, env=env)
    os.close(terminal)
    try:
        for _ in range(50):
            time.sleep(0.1)
            shown = subprocess.run([*base, "show-messages", "-JT"],
                                   capture_output=True, text=True, env=env)
            if "Terminal 0:" in shown.stdout:
                return shown.stdout
        raise AssertionError("クライアントが接続しなかった")
    finally:
        client.terminate()
        client.wait(timeout=5)
        os.close(controller)
        subprocess.run([*base, "kill-server"], capture_output=True, text=True, env=env)
        with contextlib.suppress(FileNotFoundError):
            socket.unlink()


@pytest.fixture(scope="module")
def options() -> dict[str, list[str]]:
    """``containers/base/tmux.conf`` だけを読ませた実効値 (利用側は needs_tmux 必須)。"""
    return _effective_options(TMUX_CONF)


@needs_tmux
def test_mouse_is_on(options):
    """条件 1: ホイールで copy-mode に入れる (素の tmux は off)。"""
    assert options["mouse"] == ["on"]


@needs_tmux
def test_history_limit_is_raised(options):
    """条件 2: 既定の 2000 行では 1 回のビルドログで流れ切る。"""
    assert options["history-limit"] == ["100000"]


@needs_tmux
def test_focus_events_is_on(options):
    """条件 3: 端末のフォーカス通知を中のアプリ (Claude Code 等) へ渡す。"""
    assert options["focus-events"] == ["on"]


@needs_tmux
def test_default_terminal_is_tmux_256color(options):
    """条件 4: tmux の版に依らず tmux-256color へ固定する。"""
    assert options["default-terminal"] == ["tmux-256color"]


@needs_tmux
def test_terminal_overrides_appends_without_dropping_defaults(options):
    """条件 5: `set -ga` で追記し、tmux 既定の linux*:AX@ を残す。"""
    overrides = options["terminal-overrides"]
    assert "xterm-256color:Tc" in overrides
    assert "linux*:AX@" in overrides


@needs_tmux
def test_user_config_wins_over_defaults(tmp_path):
    """条件 6: 個人設定が既定を上書きできる。

    tmux は ``/etc/tmux.conf`` → ``~/.tmux.conf`` の順に読み、後から読んだ設定が
    勝つ。``-f`` を指定すると既定の設定ファイルは読まれないため、ここでは同じ順序
    で 2 つ渡して「後勝ち」を確認する。``/etc`` と ``~`` の読み込み順そのものは
    実イメージでの手動確認に回す (PLAN38 Task 2)。
    """
    user_conf = tmp_path / "user.tmux.conf"
    user_conf.write_text("set -g history-limit 54321\n")

    options = _effective_options(TMUX_CONF, user_conf)

    assert options["history-limit"] == ["54321"]
    assert options["mouse"] == ["on"], "上書きしていない項目は既定が残る"


def test_dockerfile_installs_conf_as_etc_tmux_conf():
    """条件 7 の配線: base イメージが /etc/tmux.conf として配置する。

    tmux は ``~/.tmux.conf`` を後から読むので、配置先が ``/etc/tmux.conf`` から
    ずれると「個人設定で上書きできる既定値」という前提ごと崩れる。
    """
    copy_lines = [line for line in DOCKERFILE.read_text().splitlines()
                  if line.startswith("COPY") and "tmux.conf" in line]
    assert len(copy_lines) == 1, f"tmux.conf の COPY は 1 行のはず: {copy_lines}"
    assert re.fullmatch(r"COPY --chmod=0?644 tmux\.conf /etc/tmux\.conf", copy_lines[0]), \
        f"配置先かパーミッションが想定と違う: {copy_lines[0]}"


def test_conf_does_not_change_key_bindings():
    """条件 9: 既定値の配布に留め、利用者の既知の操作を壊さない。"""
    directives = [line.strip() for line in TMUX_CONF.read_text().splitlines()
                  if line.strip() and not line.strip().startswith("#")]
    assert directives, "設定が 1 行も無い"
    assert all(line.startswith("set ") for line in directives), \
        f"set 以外の指示が含まれる: {[x for x in directives if not x.startswith('set ')]}"


@needs_tmux
def test_hyperlinks_reach_the_outer_terminal():
    """OSC 8 のハイパーリンクを外側の端末へ書き出す。

    tmux は端末が ``Hls`` 能力を持つときだけ OSC 8 を出力し、持たない端末では文字列
    だけを描いてリンクを捨てる。tmux が ``xterm*`` へ既定で与える機能は
    ``clipboard/ccolour/cstyle/focus/title`` で ``hyperlinks`` を含まないため、
    宣言しないと Claude Code などが出す URL がクリックできなくなる。
    """
    capabilities = _client_capabilities(TMUX_CONF)

    hls = [line.strip() for line in capabilities.splitlines() if ": Hls:" in line]
    assert hls, "クライアントの能力表に Hls が無い"
    assert "[missing]" not in hls[0], f"Hls が定義されていない: {hls[0]}"
