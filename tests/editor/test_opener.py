"""opener.py: エディタ自動オープンの判定・URI 組み立て (実 docker/VS Code 不要)。"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from devbase.editor import opener


@dataclass
class _Proc:
    """subprocess.run 互換の軽量スタブ (returncode / stdout のみ)。"""
    returncode: int = 0
    stdout: str = ""


@pytest.fixture(autouse=True)
def _isolate_vscode_home(monkeypatch, tmp_path):
    """``~/.vscode-server`` からの ssh host 自動推測がテスト実行環境に依存しないよう
    HOME を空の tmp に隔離する (このリポジトリの dev コンテナ自体が実 .vscode-server を
    持つため、隔離しないと resolve_editor_ssh_host が実ホスト名を拾ってしまう)。"""
    monkeypatch.setenv("HOME", str(tmp_path))


def _write_history(base: str, subdir: str, content: str) -> str:
    """``<base>/data/User/History/<subdir>/entries.json`` を書いてパスを返す。"""
    path = os.path.join(base, "data", "User", "History", subdir, "entries.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# detect_context
# ---------------------------------------------------------------------------

def test_detect_context_plain_local():
    ctx = opener.detect_context(environ={}, isatty=True, system="Linux")
    assert ctx.is_tty is True
    assert ctx.in_vscode is False
    assert ctx.is_wsl is False
    assert ctx.is_ssh is False
    assert ctx.is_darwin is False


def test_detect_context_darwin():
    ctx = opener.detect_context(environ={}, isatty=True, system="Darwin")
    assert ctx.is_darwin is True


def test_detect_context_wsl_via_env():
    ctx = opener.detect_context(environ={"WSL_DISTRO_NAME": "Ubuntu"},
                                isatty=True, system="Linux")
    assert ctx.is_wsl is True


@pytest.mark.parametrize("key", ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"])
def test_detect_context_ssh(key):
    ctx = opener.detect_context(environ={key: "x"}, isatty=True, system="Linux")
    assert ctx.is_ssh is True


@pytest.fixture
def listening_ipc_socket():
    """listen 中の AF_UNIX ソケットを作り、そのパスを返す。

    macOS の sun_path は 104 バイト上限で pytest の tmp_path (/private/var/folders/...)
    だと超えうるため、短い ``/tmp`` 直下に作る。
    """
    import socket as _socket
    import tempfile
    d = tempfile.mkdtemp(dir="/tmp", prefix="dbipc")
    path = os.path.join(d, "s.sock")
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    try:
        yield path
    finally:
        srv.close()
        shutil.rmtree(d, ignore_errors=True)


def test_detect_context_in_vscode(listening_ipc_socket):
    # 接続できるソケットを指しているときだけ in_vscode が立つ
    ctx = opener.detect_context(
        environ={"VSCODE_IPC_HOOK_CLI": listening_ipc_socket},
        isatty=True, system="Linux")
    assert ctx.in_vscode is True


def test_detect_context_stale_ipc_socket_is_not_vscode(tmp_path):
    """変数だけ残りソケットが消えた状態 (tmux 再アタッチ等) は in_vscode=False。

    ここを True に倒すと code が死んだソケットへ繋ぎに行き無言で失敗する。
    """
    ctx = opener.detect_context(
        environ={"VSCODE_IPC_HOOK_CLI": str(tmp_path / "gone.sock")},
        isatty=True, system="Linux")
    assert ctx.in_vscode is False


def test_detect_context_orphan_ipc_socket_is_not_vscode():
    """ソケットファイルは残っているが listen していない場合も in_vscode=False。

    VS Code がクラッシュ/強制終了すると $TMPDIR に孤児ソケットが残る。実在確認だけ
    では生きていると誤判定し、code が ECONNREFUSED で失敗していた回帰の防止。
    """
    import socket as _socket
    import tempfile
    d = tempfile.mkdtemp(dir="/tmp", prefix="dbipc")
    path = os.path.join(d, "s.sock")
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    srv.close()          # bind したファイルは残るが listen は失われる
    try:
        assert os.path.exists(path)   # 実在確認では弾けないことを明示
        ctx = opener.detect_context(environ={"VSCODE_IPC_HOOK_CLI": path},
                                    isatty=True, system="Linux")
        assert ctx.in_vscode is False
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_detect_context_ipc_path_is_regular_file_is_not_vscode(tmp_path):
    """ソケットですらない通常ファイルを指していても in_vscode=False。"""
    f = tmp_path / "not-a-socket"
    f.write_text("")
    ctx = opener.detect_context(environ={"VSCODE_IPC_HOOK_CLI": str(f)},
                                isatty=True, system="Linux")
    assert ctx.in_vscode is False


# ---------------------------------------------------------------------------
# tmux セッション環境からの IPC ソケット拾い直し
# ---------------------------------------------------------------------------

class _FakeRun:
    """``subprocess.run`` の差し替え。tmux の出力を模す。"""

    def __init__(self, stdout="", returncode=0, exc=None):
        self.stdout, self.returncode, self.exc = stdout, returncode, exc
        self.calls = []
        self.kwargs = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        self.kwargs.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(stdout=self.stdout, returncode=self.returncode)


def test_tmux_env_returns_none_outside_tmux(monkeypatch):
    """tmux 外では tmux を起動しない (TMUX 未設定なら即 None)。"""
    run = _FakeRun()
    monkeypatch.setattr(opener.subprocess, "run", run)
    assert opener._tmux_env("VSCODE_IPC_HOOK_CLI", {}) is None
    assert run.calls == []


def test_tmux_env_reads_value(monkeypatch):
    run = _FakeRun(stdout="VSCODE_IPC_HOOK_CLI=/tmp/live.sock\n")
    monkeypatch.setattr(opener.subprocess, "run", run)
    got = opener._tmux_env("VSCODE_IPC_HOOK_CLI", {"TMUX": "/tmp/tmux-501/default,1,0"})
    assert got == "/tmp/live.sock"
    assert run.calls[0][:2] == ["tmux", "show-environment"]
    # 判定に使った environ をそのまま tmux クライアントへ渡す (別サーバーを見ない)。
    env = run.kwargs[0]["env"]
    assert env["TMUX"] == "/tmp/tmux-501/default,1,0"
    assert "PATH" in env          # os.environ 由来のキーは失わない


def test_tmux_env_treats_removed_marker_as_unset(monkeypatch):
    """未設定の変数は ``-NAME`` で返るので値として扱わない。"""
    monkeypatch.setattr(opener.subprocess, "run",
                        _FakeRun(stdout="-VSCODE_IPC_HOOK_CLI\n"))
    assert opener._tmux_env("VSCODE_IPC_HOOK_CLI", {"TMUX": "x"}) is None


def test_tmux_env_survives_tmux_failure(monkeypatch):
    """tmux が無い / 落ちても例外を投げず None を返す。"""
    monkeypatch.setattr(opener.subprocess, "run", _FakeRun(exc=OSError("no tmux")))
    assert opener._tmux_env("VSCODE_IPC_HOOK_CLI", {"TMUX": "x"}) is None
    monkeypatch.setattr(opener.subprocess, "run", _FakeRun(returncode=1))
    assert opener._tmux_env("VSCODE_IPC_HOOK_CLI", {"TMUX": "x"}) is None


def test_tmux_env_survives_non_utf8_output(monkeypatch):
    """非 UTF-8 出力で text=True が投げる UnicodeDecodeError も握り潰す。"""
    exc = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(opener.subprocess, "run", _FakeRun(exc=exc))
    assert opener._tmux_env("VSCODE_IPC_HOOK_CLI", {"TMUX": "x"}) is None


def test_resolve_ipc_socket_prefers_live_env_value(monkeypatch, listening_ipc_socket):
    """env の値が生きているなら tmux は見に行かない。"""
    run = _FakeRun()
    monkeypatch.setattr(opener.subprocess, "run", run)
    got = opener.resolve_ipc_socket({"VSCODE_IPC_HOOK_CLI": listening_ipc_socket,
                                     "TMUX": "x"})
    assert got == listening_ipc_socket
    assert run.calls == []


def test_resolve_ipc_socket_falls_back_to_tmux(monkeypatch, tmp_path,
                                               listening_ipc_socket):
    """env が死んでいて tmux 側が生きていれば拾い直す。"""
    monkeypatch.setattr(
        opener.subprocess, "run",
        _FakeRun(stdout=f"VSCODE_IPC_HOOK_CLI={listening_ipc_socket}\n"))
    got = opener.resolve_ipc_socket(
        {"VSCODE_IPC_HOOK_CLI": str(tmp_path / "gone.sock"), "TMUX": "x"})
    assert got == listening_ipc_socket


def test_resolve_ipc_socket_none_when_tmux_value_also_dead(monkeypatch, tmp_path):
    monkeypatch.setattr(
        opener.subprocess, "run",
        _FakeRun(stdout=f"VSCODE_IPC_HOOK_CLI={tmp_path / 'also-gone.sock'}\n"))
    assert opener.resolve_ipc_socket(
        {"VSCODE_IPC_HOOK_CLI": str(tmp_path / "gone.sock"), "TMUX": "x"}) is None


def test_open_editor_passes_recovered_socket_to_launcher(
        monkeypatch, tmp_path, listening_ipc_socket):
    """拾い直した値は起動する code の env にも渡す。

    ここを差し替えないと code 自身が古いソケットへ繋ぎに行って失敗する。
    """
    monkeypatch.setattr(opener.shutil, "which", lambda c: "/usr/bin/code")
    monkeypatch.setattr(opener, "resolve_container_name",
                        lambda *a, **kw: "adminer-dev-1")
    monkeypatch.setattr(
        opener.subprocess, "run",
        _FakeRun(stdout=f"VSCODE_IPC_HOOK_CLI={listening_ipc_socket}\n"))
    seen = {}
    result = opener.open_editor(
        project_name="adminer", dev_service_name="dev", workdir="/work/adminer",
        environ={"VSCODE_IPC_HOOK_CLI": str(tmp_path / "gone.sock"), "TMUX": "x"},
        isatty=True,
        launcher=lambda cmd, env: seen.update(cmd=cmd, env=env),
    )
    assert result == "launch"
    assert seen["env"]["VSCODE_IPC_HOOK_CLI"] == listening_ipc_socket


def test_open_editor_without_tmux_does_not_recover(monkeypatch, tmp_path, caplog):
    """tmux 外では拾い直さず、従来どおり警告を出す。"""
    import logging
    monkeypatch.setattr(opener.shutil, "which", lambda c: "/usr/bin/code")
    monkeypatch.setattr(opener, "resolve_container_name",
                        lambda *a, **kw: "adminer-dev-1")
    run = _FakeRun()
    monkeypatch.setattr(opener.subprocess, "run", run)
    calls = []
    with caplog.at_level(logging.INFO):
        opener.open_editor(
            project_name="adminer", dev_service_name="dev", workdir="/work/adminer",
            environ={"VSCODE_IPC_HOOK_CLI": str(tmp_path / "gone.sock")},
            isatty=True, launcher=lambda cmd, env: calls.append((cmd, env)),
        )
    assert run.calls == []          # tmux を叩いていない
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "接続できません" in text
    assert "拾い直しました" not in text
    # env の VSCODE_IPC_HOOK_CLI は書き換えられていない
    assert calls[0][1]["VSCODE_IPC_HOOK_CLI"].endswith("gone.sock")


def test_detect_context_ipc_alive_override():
    """``ipc_alive`` 明示時は実在チェックを行わない (テスト用差し替え口)。"""
    ctx = opener.detect_context(environ={"VSCODE_IPC_HOOK_CLI": "/run/x.sock"},
                                isatty=True, system="Linux", ipc_alive=True)
    assert ctx.in_vscode is True


# ---------------------------------------------------------------------------
# is_open_enabled
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, False), ("", False), ("0", False), ("false", False), ("no", False),
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
])
def test_is_open_enabled(value, expected):
    env = {} if value is None else {"DEVBASE_OPEN_EDITOR": value}
    assert opener.is_open_enabled(env) is expected


# ---------------------------------------------------------------------------
# resolve_editor_cmd
# ---------------------------------------------------------------------------

def test_resolve_editor_cmd_default_code(monkeypatch):
    monkeypatch.setattr(opener.shutil, "which",
                        lambda c: "/usr/bin/code" if c == "code" else None)
    assert opener.resolve_editor_cmd({}) == ["code"]


def test_resolve_editor_cmd_missing(monkeypatch):
    monkeypatch.setattr(opener.shutil, "which", lambda c: None)
    assert opener.resolve_editor_cmd({}) is None


def test_resolve_editor_cmd_explicit_with_args(monkeypatch):
    monkeypatch.setattr(opener.shutil, "which",
                        lambda c: "/usr/bin/cursor" if c == "cursor" else None)
    assert opener.resolve_editor_cmd({"DEVBASE_EDITOR": "cursor --reuse-window"}) \
        == ["cursor", "--reuse-window"]


def test_resolve_editor_cmd_explicit_missing(monkeypatch):
    monkeypatch.setattr(opener.shutil, "which", lambda c: None)
    assert opener.resolve_editor_cmd({"DEVBASE_EDITOR": "nope"}) is None


# ---------------------------------------------------------------------------
# resolve_editor_display (print_command 用・which 非依存)
# ---------------------------------------------------------------------------

def test_resolve_editor_display_default_code(monkeypatch):
    # ローカルに code が無くても (which=None) 既定の ["code"] を返す
    monkeypatch.setattr(opener.shutil, "which", lambda c: None)
    assert opener.resolve_editor_display({}) == ["code"]


def test_resolve_editor_display_explicit_without_which(monkeypatch):
    # DEVBASE_EDITOR があれば実在チェックなしでそのまま分割して返す
    monkeypatch.setattr(opener.shutil, "which", lambda c: None)
    assert opener.resolve_editor_display({"DEVBASE_EDITOR": "cursor --reuse-window"}) \
        == ["cursor", "--reuse-window"]


# ---------------------------------------------------------------------------
# build_attach_uri
# ---------------------------------------------------------------------------

def test_build_attach_uri_hex_payload():
    uri = opener.build_attach_uri("adminer-dev-1", "/work/adminer")
    prefix = "vscode-remote://attached-container+"
    assert uri.startswith(prefix)
    authority, _, path = uri[len(prefix):].partition("/")
    assert path == "work/adminer"
    decoded = bytes.fromhex(authority).decode("utf-8")
    assert json.loads(decoded) == {"containerName": "/adminer-dev-1"}


def test_build_attach_uri_adds_leading_slash():
    uri = opener.build_attach_uri("p-dev-1", "work/p")  # スラッシュ無し
    assert uri.endswith("/work/p")


def test_build_attach_uri_nested_ssh_remote_and_context():
    """ssh_host / docker_context 指定でネスト authority + settings.context を組む。"""
    uri = opener.build_attach_uri("adminer-dev-1", "/work/adminer",
                                  ssh_host="mac2", docker_context="desktop-linux")
    prefix = "vscode-remote://attached-container+"
    assert uri.startswith(prefix)
    authority, _, path = uri[len(prefix):].partition("/")
    assert path == "work/adminer"
    hexpart, sep, ssh = authority.partition("@")
    assert sep == "@"
    assert ssh == "ssh-remote+mac2"
    decoded = json.loads(bytes.fromhex(hexpart).decode("utf-8"))
    assert decoded == {"containerName": "/adminer-dev-1",
                       "settings": {"context": "desktop-linux"}}


def test_build_attach_uri_ssh_host_only_no_settings():
    """docker_context 無しなら settings は付けず @ssh-remote のみ付く。"""
    uri = opener.build_attach_uri("p-dev-1", "/work/p", ssh_host="mac2")
    assert "@ssh-remote+mac2/work/p" in uri
    hexpart = uri.split("attached-container+")[1].split("@")[0]
    assert json.loads(bytes.fromhex(hexpart).decode()) == {"containerName": "/p-dev-1"}


# ---------------------------------------------------------------------------
# resolve_editor_ssh_host / resolve_docker_context
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, None), ("", None), ("   ", None), ("mac2", "mac2"), (" mac2 ", "mac2"),
])
def test_resolve_editor_ssh_host_explicit_or_none(value, expected):
    # HOME は autouse fixture で空 tmp に隔離済みのため自動推測は None。
    env = {} if value is None else {"DEVBASE_EDITOR_SSH_HOST": value}
    assert opener.resolve_editor_ssh_host(env) == expected


def test_resolve_editor_ssh_host_autodetect_single(tmp_path):
    base = str(tmp_path / ".vscode-server")
    _write_history(base, "abc", json.dumps(
        {"resource": "vscode-remote://attached-container%2Bxx@ssh-remote%2Bmac2/work/x"}))
    assert opener.resolve_editor_ssh_host({}, vscode_server_dir=base) == "mac2"


def test_resolve_editor_ssh_host_autodetect_plus_form(tmp_path):
    base = str(tmp_path / ".vscode-server")
    _write_history(base, "abc", '"vscode-remote://ssh-remote+devbox/work/p"')
    assert opener.resolve_editor_ssh_host({}, vscode_server_dir=base) == "devbox"


def test_resolve_editor_ssh_host_autodetect_picks_newest(tmp_path):
    base = str(tmp_path / ".vscode-server")
    old = _write_history(base, "a", "ssh-remote%2BmacOLD/work")
    new = _write_history(base, "b", "ssh-remote%2BmacNEW/work")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    assert opener.resolve_editor_ssh_host({}, vscode_server_dir=base) == "macNEW"


def test_resolve_editor_ssh_host_autodetect_none_when_absent(tmp_path):
    assert opener.resolve_editor_ssh_host(
        {}, vscode_server_dir=str(tmp_path / "nope")) is None


def test_resolve_editor_ssh_host_auto_detect_false_skips_history(tmp_path):
    """auto_detect=False (plain SSH 相当) は history を見ず None (明示が無ければ)。"""
    base = str(tmp_path / ".vscode-server")
    _write_history(base, "a", "ssh-remote%2Bmac2/work")
    assert opener.resolve_editor_ssh_host(
        {}, vscode_server_dir=base, auto_detect=False) is None
    # auto_detect=True なら拾える (対比)
    assert opener.resolve_editor_ssh_host(
        {}, vscode_server_dir=base, auto_detect=True) == "mac2"


def test_resolve_editor_ssh_host_auto_detect_false_honors_explicit(tmp_path):
    """auto_detect=False でも明示設定は尊重する。"""
    assert opener.resolve_editor_ssh_host(
        {"DEVBASE_EDITOR_SSH_HOST": "mac2"}, auto_detect=False) == "mac2"


def test_detect_ssh_host_across_multiple_server_dirs(tmp_path):
    """cursor-server / vscode-server を横断し最新 mtime のホストを返す。"""
    vsc = str(tmp_path / ".vscode-server")
    cur = str(tmp_path / ".cursor-server")
    old = _write_history(vsc, "a", "ssh-remote%2BmacOLD/work")
    new = _write_history(cur, "b", "ssh-remote%2BmacNEW/work")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    assert opener._detect_ssh_host_from_dirs([vsc, cur]) == "macNEW"


def test_resolve_editor_ssh_host_explicit_beats_autodetect(tmp_path):
    base = str(tmp_path / ".vscode-server")
    _write_history(base, "a", "ssh-remote%2Bauto/work")
    assert opener.resolve_editor_ssh_host(
        {"DEVBASE_EDITOR_SSH_HOST": "explicit"}, vscode_server_dir=base) == "explicit"


def test_resolve_editor_ssh_host_empty_string_opts_out(tmp_path):
    """空文字は自動推測のオプトアウト (None) として扱い、history 探索しない。"""
    base = str(tmp_path / ".vscode-server")
    _write_history(base, "a", "ssh-remote%2Bauto/work")
    assert opener.resolve_editor_ssh_host(
        {"DEVBASE_EDITOR_SSH_HOST": ""}, vscode_server_dir=base) is None
    assert opener.resolve_editor_ssh_host(
        {"DEVBASE_EDITOR_SSH_HOST": "  "}, vscode_server_dir=base) is None


def test_resolve_docker_context_empty_string_opts_out():
    """空文字は settings.context を付けないオプトアウト。runner を呼ばない。"""
    def boom(cmd, **kw):
        raise AssertionError("docker context show should not run")

    assert opener.resolve_docker_context(
        {"DEVBASE_EDITOR_DOCKER_CONTEXT": ""}, runner=boom) is None


def test_resolve_docker_context_explicit_wins():
    assert opener.resolve_docker_context({"DEVBASE_EDITOR_DOCKER_CONTEXT": " desktop-linux "}) \
        == "desktop-linux"


def test_resolve_docker_context_from_docker_show():
    def runner(cmd, **kw):
        assert cmd == ["docker", "context", "show"]
        return _Proc(returncode=0, stdout="desktop-linux\n")

    assert opener.resolve_docker_context({}, runner=runner) == "desktop-linux"


def test_resolve_docker_context_none_when_docker_fails():
    assert opener.resolve_docker_context(
        {}, runner=lambda cmd, **kw: _Proc(returncode=1, stdout="")) is None


def test_resolve_docker_context_none_when_docker_absent():
    def boom(cmd, **kw):
        raise FileNotFoundError("docker")

    assert opener.resolve_docker_context({}, runner=boom) is None


# ---------------------------------------------------------------------------
# resolve_container_name / resolve_workdir
# ---------------------------------------------------------------------------

def test_resolve_container_name_deterministic():
    """docker 問い合わせが失敗 (非0) する場合は決定的名へフォールバックする。"""
    def failing_runner(cmd, **kw):
        return _Proc(returncode=1, stdout="")

    assert opener.resolve_container_name("dev", "carmo", 1, runner=failing_runner) \
        == "carmo-dev-1"
    assert opener.resolve_container_name("app", "carmo", 3, runner=failing_runner) \
        == "carmo-app-3"


def test_resolve_container_name_falls_back_when_docker_absent(monkeypatch):
    """docker 不在 (例外) でも決定的名で必ず動く。"""
    monkeypatch.setattr(opener, "_query_container_name",
                        lambda *a, **kw: None)
    assert opener.resolve_container_name("dev", "carmo", 1) == "carmo-dev-1"


def test_resolve_container_name_prefers_docker_name_ndjson():
    """docker から取得できた実 Name (NDJSON) を決定的名より優先する。"""
    def runner(cmd, **kw):
        # service token は dev-2 を指定しているはず
        assert cmd[:4] == ["docker", "compose", "ps", "--format"]
        assert cmd[-1] == "dev-2"
        return _Proc(returncode=0,
                     stdout='{"Name":"real-dev-2","Service":"dev-2"}\n')

    assert opener.resolve_container_name("dev", "carmo", 2, runner=runner) \
        == "real-dev-2"


def test_resolve_container_name_prefers_docker_name_json_array():
    """JSON 配列形式の docker compose ps 出力にも対応する。"""
    def runner(cmd, **kw):
        return _Proc(returncode=0,
                     stdout='[{"Name":"real-dev-1","Service":"dev-1"}]')

    assert opener.resolve_container_name("dev", "carmo", 1, runner=runner) \
        == "real-dev-1"


def test_query_container_name_passes_compose_file_f_flag():
    """compose_file を渡すと docker compose ps argv に -f <file> が差し込まれる。"""
    captured = {}

    def runner(cmd, **kw):
        captured["cmd"] = cmd
        return _Proc(returncode=0, stdout='{"Name":"real-dev-1"}\n')

    name = opener._query_container_name(
        "dev", 1, compose_file=".docker-compose.scale.yml", runner=runner)
    assert name == "real-dev-1"
    cmd = captured["cmd"]
    assert cmd[:2] == ["docker", "compose"]
    assert "-f" in cmd
    assert cmd[cmd.index("-f") + 1] == ".docker-compose.scale.yml"
    # service token は最後尾に置かれる
    assert cmd[-1] == "dev-1"
    # -f は ps サブコマンドより前
    assert cmd.index("-f") < cmd.index("ps")


def test_query_container_name_omits_f_flag_when_no_compose_file():
    """compose_file 未指定なら -f を付けない (従来挙動)。"""
    captured = {}

    def runner(cmd, **kw):
        captured["cmd"] = cmd
        return _Proc(returncode=0, stdout='{"Name":"real-dev-1"}\n')

    opener._query_container_name("dev", 1, runner=runner)
    assert "-f" not in captured["cmd"]


def test_resolve_container_name_forwards_compose_file():
    """resolve_container_name が compose_file を _query_container_name へ伝播する。"""
    captured = {}

    def runner(cmd, **kw):
        captured["cmd"] = cmd
        return _Proc(returncode=0, stdout='{"Name":"real-dev-2"}\n')

    name = opener.resolve_container_name(
        "dev", "carmo", 2, compose_file="override.yml", runner=runner)
    assert name == "real-dev-2"
    cmd = captured["cmd"]
    assert "-f" in cmd
    assert cmd[cmd.index("-f") + 1] == "override.yml"


def test_parse_compose_ps_name_empty_and_invalid():
    assert opener._parse_compose_ps_name("") is None
    assert opener._parse_compose_ps_name("not json") is None
    assert opener._parse_compose_ps_name("[]") is None


def test_resolve_workdir_prefers_work_dir_env():
    assert opener.resolve_workdir({"WORK_DIR": "/work/x"}, "y") == "/work/x"


def test_resolve_workdir_from_git_repo():
    assert opener.resolve_workdir({"GIT_REPO": "myrepo"}, None) == "/work/myrepo"


def test_resolve_workdir_fallback_project_name():
    assert opener.resolve_workdir({}, "proj") == "/work/proj"


def test_resolve_workspace_none_when_unset():
    assert opener.resolve_workspace({}) is None


def test_resolve_workspace_blank_is_none():
    assert opener.resolve_workspace({"DEVBASE_WORKSPACE": "   "}) is None


def test_resolve_workspace_returns_path():
    env = {"DEVBASE_WORKSPACE": "/home/ubuntu/share/work/uttarov2-doc.workspace"}
    assert opener.resolve_workspace(env) == \
        "/home/ubuntu/share/work/uttarov2-doc.workspace"


# ---------------------------------------------------------------------------
# decide_action (§2.4 マトリクス全分岐)
# ---------------------------------------------------------------------------

def _ctx(**kw):
    base = dict(is_tty=True, in_vscode=False, is_wsl=False, is_ssh=False, is_darwin=False)
    base.update(kw)
    return opener.EditorContext(**base)


def test_decide_skip_no_editor_local():
    # ローカル (launch 経路) で editor が無ければ skip
    assert opener.decide_action(_ctx(), editor_available=False).action == "skip"


def test_decide_skip_no_editor_in_vscode():
    # VS Code 統合端末でも code シムが無ければ委譲できないため skip
    plan = opener.decide_action(_ctx(in_vscode=True), editor_available=False)
    assert plan.action == "skip"


def test_decide_skip_non_tty():
    assert opener.decide_action(_ctx(is_tty=False), editor_available=True).action == "skip"


def test_decide_launch_local():
    assert opener.decide_action(_ctx(), editor_available=True).action == "launch"


def test_decide_launch_wsl():
    assert opener.decide_action(_ctx(is_wsl=True), editor_available=True).action == "launch"


def test_decide_launch_in_vscode_even_under_ssh():
    # Remote-SSH 統合端末: in_vscode が ssh より優先され launch
    plan = opener.decide_action(_ctx(in_vscode=True, is_ssh=True), editor_available=True)
    assert plan.action == "launch"


def test_decide_print_command_plain_ssh():
    plan = opener.decide_action(_ctx(is_ssh=True), editor_available=True)
    assert plan.action == "print_command"


def test_decide_print_command_plain_ssh_without_local_editor():
    # plain SSH の提示は手元で実行する前提のためローカル editor 不在でも print_command
    plan = opener.decide_action(_ctx(is_ssh=True), editor_available=False)
    assert plan.action == "print_command"


# ---------------------------------------------------------------------------
# open_editor (orchestration; launcher を差し替えて副作用を観測)
# ---------------------------------------------------------------------------

def test_open_editor_launch_invokes_launcher(monkeypatch):
    monkeypatch.setattr(opener.shutil, "which", lambda c: "/usr/bin/code")
    calls = []
    result = opener.open_editor(
        project_name="carmo", dev_service_name="dev", workdir="/work/carmo",
        environ={}, isatty=True, launcher=lambda cmd, env: calls.append(cmd),
    )
    assert result == "launch"
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "code"
    assert cmd[1] == "--folder-uri"
    assert cmd[2].startswith("vscode-remote://attached-container+")
    assert cmd[2].endswith("/work/carmo")


def test_open_editor_launch_uses_file_uri_for_workspace(monkeypatch):
    """DEVBASE_WORKSPACE 指定時は --file-uri でワークスペースファイルを開く。"""
    monkeypatch.setattr(opener.shutil, "which", lambda c: "/usr/bin/code")
    calls = []
    result = opener.open_editor(
        project_name="uttarov2-doc", dev_service_name="dev",
        workdir="/work/uttarov2-doc",
        environ={"DEVBASE_WORKSPACE":
                 "/home/ubuntu/share/work/uttarov2-doc.workspace"},
        isatty=True, launcher=lambda cmd, env: calls.append(cmd),
    )
    assert result == "launch"
    cmd = calls[0]
    assert cmd[1] == "--file-uri"
    assert cmd[2].startswith("vscode-remote://attached-container+")
    assert cmd[2].endswith("/home/ubuntu/share/work/uttarov2-doc.workspace")


def test_open_editor_print_command_file_uri_for_workspace(monkeypatch, caplog):
    """plain SSH の提示コマンドも DEVBASE_WORKSPACE 指定時は --file-uri になる。"""
    import logging
    monkeypatch.setattr(opener.shutil, "which", lambda c: "/usr/bin/code")
    with caplog.at_level(logging.INFO):
        result = opener.open_editor(
            project_name="uttarov2-doc", dev_service_name="dev",
            workdir="/work/uttarov2-doc",
            environ={"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22",
                     "DEVBASE_WORKSPACE":
                     "/home/ubuntu/share/work/uttarov2-doc.workspace"},
            isatty=True, launcher=lambda cmd, env: None,
        )
    assert result == "print_command"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "code --file-uri" in text
    assert "uttarov2-doc.workspace" in text


def test_open_editor_launch_nested_uri_under_remote_ssh(monkeypatch):
    """Remote-SSH (in_vscode + ssh) かつ DEVBASE_EDITOR_SSH_HOST 設定時はネスト URI で launch。"""
    monkeypatch.setattr(opener.shutil, "which", lambda c: "/usr/bin/code")
    # docker context show を実行させず固定値に差し替え
    monkeypatch.setattr(opener, "resolve_docker_context",
                        lambda *a, **kw: "desktop-linux")
    calls = []
    result = opener.open_editor(
        project_name="adminer", dev_service_name="dev", workdir="/work/adminer",
        environ={"VSCODE_IPC_HOOK_CLI": "/run/x.sock",
                 "SSH_CONNECTION": "192.168.1.16 5 192.168.1.201 22",
                 "DEVBASE_EDITOR_SSH_HOST": "mac2"},
        isatty=True, ipc_alive=True, launcher=lambda cmd, env: calls.append(cmd),
    )
    assert result == "launch"
    uri = calls[0][2]
    assert "@ssh-remote+mac2/work/adminer" in uri
    hexpart = uri.split("attached-container+")[1].split("@")[0]
    decoded = json.loads(bytes.fromhex(hexpart).decode())
    assert decoded["containerName"] == "/adminer-dev-1"
    assert decoded["settings"]["context"] == "desktop-linux"


def test_open_editor_flat_uri_when_ssh_host_unset(monkeypatch):
    """Remote-SSH でも DEVBASE_EDITOR_SSH_HOST 未設定なら従来のフラット URI のまま。"""
    monkeypatch.setattr(opener.shutil, "which", lambda c: "/usr/bin/code")
    calls = []
    opener.open_editor(
        project_name="adminer", dev_service_name="dev", workdir="/work/adminer",
        environ={"VSCODE_IPC_HOOK_CLI": "/run/x.sock",
                 "SSH_CONNECTION": "192.168.1.16 5 192.168.1.201 22"},
        isatty=True, ipc_alive=True, launcher=lambda cmd, env: calls.append(cmd),
    )
    assert "@ssh-remote" not in calls[0][2]


def test_open_editor_stale_ipc_under_ssh_degrades_to_print_command(
        monkeypatch, tmp_path, caplog):
    """死んだ IPC ソケット + SSH は launch せず print_command へ degrade する。

    tmux セッションを再利用した端末で `devbase up --open` が「何も起きない」
    という無言の失敗になっていた回帰の防止 (実在チェック導入前は launch していた)。
    """
    import logging
    monkeypatch.setattr(opener.shutil, "which", lambda c: "/usr/bin/code")
    calls = []
    with caplog.at_level(logging.INFO):
        result = opener.open_editor(
            project_name="adminer", dev_service_name="dev",
            workdir="/work/adminer",
            environ={"VSCODE_IPC_HOOK_CLI": str(tmp_path / "gone.sock"),
                     "SSH_CONNECTION": "192.168.1.16 5 192.168.1.201 22"},
            isatty=True, launcher=lambda cmd, env: calls.append(cmd),
        )
    assert result == "print_command"
    assert calls == []
    text = "\n".join(r.getMessage() for r in caplog.records)
    # 原因が分かるログが出ていること (無言の失敗にしない)
    assert "VSCODE_IPC_HOOK_CLI" in text
    assert "code --folder-uri" in text


def test_open_editor_skip_when_no_editor(monkeypatch):
    monkeypatch.setattr(opener.shutil, "which", lambda c: None)
    calls = []
    result = opener.open_editor(
        project_name="carmo", dev_service_name="dev", workdir="/work/carmo",
        environ={}, launcher=lambda cmd, env: calls.append(cmd),
    )
    assert result == "skip"
    assert calls == []


def test_open_editor_print_command_under_plain_ssh(monkeypatch):
    monkeypatch.setattr(opener.shutil, "which", lambda c: "/usr/bin/code")
    calls = []
    result = opener.open_editor(
        project_name="carmo", dev_service_name="dev", workdir="/work/carmo",
        environ={"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"}, isatty=True,
        launcher=lambda cmd, env: calls.append(cmd),
    )
    assert result == "print_command"
    assert calls == []


def test_open_editor_print_command_without_local_editor(monkeypatch, caplog):
    """plain SSH でローカルに code が無くても print_command になり提示コマンドを出す。"""
    import logging
    monkeypatch.setattr(opener.shutil, "which", lambda c: None)  # ローカルに code なし
    calls = []
    with caplog.at_level(logging.INFO):
        result = opener.open_editor(
            project_name="carmo", dev_service_name="dev", workdir="/work/carmo",
            environ={"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"}, isatty=True,
            launcher=lambda cmd, env: calls.append(cmd),
        )
    assert result == "print_command"
    assert calls == []
    # 提示コマンドに code --folder-uri が含まれる
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "code --folder-uri" in text


def test_open_editor_print_command_uses_explicit_display_editor(monkeypatch, caplog):
    """DEVBASE_EDITOR があれば print_command でも which 非依存でそれを提示する。"""
    import logging
    monkeypatch.setattr(opener.shutil, "which", lambda c: None)
    with caplog.at_level(logging.INFO):
        result = opener.open_editor(
            project_name="carmo", dev_service_name="dev", workdir="/work/carmo",
            environ={"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22",
                     "DEVBASE_EDITOR": "cursor"},
            isatty=True, launcher=lambda cmd, env: None,
        )
    assert result == "print_command"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "cursor --folder-uri" in text


def test_open_editor_launch_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(opener.shutil, "which", lambda c: "/usr/bin/code")

    def boom(cmd, env):
        raise OSError("cannot exec")

    # 例外を握り潰し launch を返す (up を倒さない)
    result = opener.open_editor(
        project_name="carmo", dev_service_name="dev", workdir="/work/carmo",
        environ={}, isatty=True, launcher=boom,
    )
    assert result == "launch"
