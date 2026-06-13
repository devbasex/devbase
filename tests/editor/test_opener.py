"""opener.py: エディタ自動オープンの判定・URI 組み立て (実 docker/VS Code 不要)。"""

from __future__ import annotations

import json

import pytest

from devbase.editor import opener


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


def test_detect_context_in_vscode():
    ctx = opener.detect_context(environ={"VSCODE_IPC_HOOK_CLI": "/run/x.sock"},
                                isatty=True, system="Linux")
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


# ---------------------------------------------------------------------------
# resolve_container_name / resolve_workdir
# ---------------------------------------------------------------------------

def test_resolve_container_name_deterministic():
    assert opener.resolve_container_name("dev", "carmo", 1) == "carmo-dev-1"
    assert opener.resolve_container_name("app", "carmo", 3) == "carmo-app-3"


def test_resolve_workdir_prefers_work_dir_env():
    assert opener.resolve_workdir({"WORK_DIR": "/work/x"}, "y") == "/work/x"


def test_resolve_workdir_from_git_repo():
    assert opener.resolve_workdir({"GIT_REPO": "myrepo"}, None) == "/work/myrepo"


def test_resolve_workdir_fallback_project_name():
    assert opener.resolve_workdir({}, "proj") == "/work/proj"


# ---------------------------------------------------------------------------
# decide_action (§2.4 マトリクス全分岐)
# ---------------------------------------------------------------------------

def _ctx(**kw):
    base = dict(is_tty=True, in_vscode=False, is_wsl=False, is_ssh=False, is_darwin=False)
    base.update(kw)
    return opener.EditorContext(**base)


def test_decide_skip_no_editor():
    assert opener.decide_action(_ctx(), None).action == "skip"


def test_decide_skip_non_tty():
    assert opener.decide_action(_ctx(is_tty=False), ["code"]).action == "skip"


def test_decide_launch_local():
    assert opener.decide_action(_ctx(), ["code"]).action == "launch"


def test_decide_launch_wsl():
    assert opener.decide_action(_ctx(is_wsl=True), ["code"]).action == "launch"


def test_decide_launch_in_vscode_even_under_ssh():
    # Remote-SSH 統合端末: in_vscode が ssh より優先され launch
    plan = opener.decide_action(_ctx(in_vscode=True, is_ssh=True), ["code"])
    assert plan.action == "launch"


def test_decide_print_command_plain_ssh():
    plan = opener.decide_action(_ctx(is_ssh=True), ["code"])
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
