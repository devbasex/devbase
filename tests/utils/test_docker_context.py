"""docker context の解決・接続先の確定・環境変数への反映・gid の取得 (PLAN52)。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devbase.errors import DevbaseError
from devbase.project.local_config import DockerSettings
from devbase.utils import docker_context as dc


@pytest.fixture(autouse=True)
def _reset_state():
    dc.reset({})
    yield
    dc.reset({})


def _proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# choose_context: 優先順位 CLI > env > ファイル > 未指定
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cli, env, file, expected", [
    ("c", "b", "a", ("c", "cli")),
    (None, "b", "a", ("b", "env")),
    (None, None, "a", ("a", "file")),
    (None, None, None, (None, "default")),
    (None, "", "a", ("a", "file")),      # env の空文字は未指定
    (None, "  ", "a", ("a", "file")),
])
def test_choose_context_priority(cli, env, file, expected):
    environ = {} if env is None else {"DEVBASE_DOCKER_CONTEXT": env}
    choice = dc.choose_context(DockerSettings(context=file), cli_context=cli, environ=environ)
    assert (choice.context, choice.source) == expected


# ---------------------------------------------------------------------------
# resolve_target: リモート判定と home / gid
# ---------------------------------------------------------------------------

def test_unset_context_is_local_and_does_not_probe():
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return _proc("desktop-linux\n")

    target = dc.resolve_target(dc.ContextChoice(None, "default"), DockerSettings(),
                               environ={}, runner=runner)
    assert target.remote is False and target.context is None
    assert target.home is None and target.gid is None
    assert calls == []


def test_same_as_current_context_is_local():
    runner = lambda cmd, **kw: _proc("desktop-linux\n")  # noqa: E731
    target = dc.resolve_target(dc.ContextChoice("desktop-linux", "file"),
                               DockerSettings(context="desktop-linux", home="/h", gid=1),
                               environ={}, runner=runner)
    assert target.remote is False
    assert target.home is None and target.gid is None


def test_different_context_is_remote_with_file_values():
    runner = lambda cmd, **kw: _proc("desktop-linux\n")  # noqa: E731
    target = dc.resolve_target(dc.ContextChoice("gpu-wsl", "file"),
                               DockerSettings(context="gpu-wsl", home="/home/t", gid=999),
                               environ={}, runner=runner)
    assert target.remote is True
    assert (target.home, target.gid) == ("/home/t", 999)


def test_probe_failure_is_treated_as_remote():
    runner = lambda cmd, **kw: _proc("", returncode=1)  # noqa: E731
    target = dc.resolve_target(dc.ContextChoice("gpu-wsl", "cli"), DockerSettings(),
                               environ={}, runner=runner)
    assert target.remote is True


def test_probe_runs_without_docker_context_and_docker_host():
    """DOCKER_CONTEXT / DOCKER_HOST が残ると docker context show の答えが変わる。"""
    seen = {}

    def runner(cmd, **kw):
        seen.update(kw.get("env") or {})
        seen["cmd"] = cmd
        return _proc("desktop-linux\n")

    environ = {"DOCKER_CONTEXT": "gpu-wsl", "DOCKER_HOST": "tcp://127.0.0.1:1", "PATH": "/x"}
    target = dc.resolve_target(dc.ContextChoice("gpu-wsl", "file"),
                               DockerSettings(context="gpu-wsl"), environ=environ, runner=runner)
    assert seen["cmd"][:3] == ["docker", "context", "show"]
    assert "DOCKER_CONTEXT" not in seen and "DOCKER_HOST" not in seen
    assert seen["PATH"] == "/x"
    assert target.remote is True


def test_docker_host_only_with_same_context_is_local():
    """DOCKER_HOST だけがある環境で同名の context を指定してもローカル扱い。"""
    runner = lambda cmd, **kw: _proc("desktop-linux\n")  # noqa: E731
    target = dc.resolve_target(dc.ContextChoice("desktop-linux", "cli"), DockerSettings(),
                               environ={"DOCKER_HOST": "tcp://127.0.0.1:1"}, runner=runner)
    assert target.remote is False


def test_override_to_other_context_drops_file_home_and_gid(caplog):
    """前提 4: CLI / env で別の context へ向けたらファイルの home / gid は使わない。"""
    runner = lambda cmd, **kw: _proc("desktop-linux\n")  # noqa: E731
    with caplog.at_level("WARNING"):
        target = dc.resolve_target(dc.ContextChoice("ec2", "cli"),
                                   DockerSettings(context="gpu-wsl", home="/home/t", gid=999),
                                   environ={}, runner=runner)
    assert target.remote is True
    assert target.home is None and target.gid is None
    assert "gpu-wsl" in caplog.text and "ec2" in caplog.text


# ---------------------------------------------------------------------------
# apply / reapply / reset
# ---------------------------------------------------------------------------

def test_apply_none_touches_nothing():
    env = {"DOCKER_HOST": "tcp://x", "DOCKER_GID": "0"}
    dc.apply(dc.ContextChoice(None, "default"), env)
    assert env == {"DOCKER_HOST": "tcp://x", "DOCKER_GID": "0"}


def test_apply_choice_sets_context_and_removes_docker_host(caplog):
    env = {"DOCKER_HOST": "tcp://x", "DOCKER_GID": "0"}
    with caplog.at_level("WARNING"):
        dc.apply(dc.ContextChoice("gpu-wsl", "file"), env)
    assert env["DOCKER_CONTEXT"] == "gpu-wsl"
    assert "DOCKER_HOST" not in env
    assert env["DOCKER_GID"] == "0"          # ContextChoice は gid を触らない
    assert "DOCKER_HOST" in caplog.text


def test_apply_target_sets_gid_when_remote():
    env = {"DOCKER_GID": "0"}
    target = dc.DockerTarget(context="gpu-wsl", source="file", remote=True, home=None, gid=999)
    dc.apply(target, env)
    assert env["DOCKER_CONTEXT"] == "gpu-wsl" and env["DOCKER_GID"] == "999"


def test_apply_local_target_keeps_gid():
    env = {"DOCKER_GID": "0"}
    target = dc.DockerTarget(context="desktop-linux", source="file", remote=False,
                             home=None, gid=None)
    dc.apply(target, env)
    assert env["DOCKER_GID"] == "0"


def test_reapply_restores_after_secret_injection_overwrote():
    env = {"DOCKER_GID": "0"}
    dc.apply(dc.DockerTarget("gpu-wsl", "cli", True, None, 999), env)
    # 機密注入が同名キーを上書きした
    env.update({"DOCKER_CONTEXT": "x", "DOCKER_GID": "1", "DOCKER_HOST": "tcp://x"})
    dc.reapply(env)
    assert env["DOCKER_CONTEXT"] == "gpu-wsl" and env["DOCKER_GID"] == "999"
    assert "DOCKER_HOST" not in env


def test_reapply_without_active_target_does_nothing():
    env = {"DOCKER_CONTEXT": "x"}
    dc.reapply(env)
    assert env == {"DOCKER_CONTEXT": "x"}


def test_reset_restores_original_values():
    env = {"DOCKER_HOST": "tcp://x", "DOCKER_GID": "0"}
    dc.apply(dc.DockerTarget("gpu-wsl", "cli", True, None, 999), env)
    dc.reset(env)
    assert env == {"DOCKER_HOST": "tcp://x", "DOCKER_GID": "0"}
    dc.reapply(env)  # 控えは消えている
    assert env == {"DOCKER_HOST": "tcp://x", "DOCKER_GID": "0"}


def test_apply_is_idempotent_and_keeps_first_originals():
    env = {"DOCKER_GID": "0"}
    dc.apply(dc.DockerTarget("gpu-wsl", "cli", True, None, 999), env)
    dc.apply(dc.DockerTarget("gpu-wsl", "cli", True, None, 999), env)
    dc.reset(env)
    assert env == {"DOCKER_GID": "0"}


# ---------------------------------------------------------------------------
# ensure_remote_gid
# ---------------------------------------------------------------------------

def test_explicit_gid_is_used_without_docker_run(tmp_path):
    calls = []
    target = dc.DockerTarget("gpu-wsl", "file", True, None, 999)
    gid = dc.ensure_remote_gid(target, cache_dir=tmp_path, runner=lambda c, **k: calls.append(c))
    assert gid == 999 and calls == []


def test_gid_is_fetched_once_and_cached(tmp_path):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return _proc("999\n")

    target = dc.DockerTarget("gpu-wsl", "file", True, None, None)
    assert dc.ensure_remote_gid(target, cache_dir=tmp_path, runner=runner) == 999
    assert len(calls) == 1
    assert calls[0][:3] == ["docker", "run", "--rm"]
    assert "alpine:3" in calls[0] and "stat" in calls[0]
    assert (tmp_path / "docker-gid" / "gpu-wsl").read_text().strip() == "999"

    assert dc.ensure_remote_gid(target, cache_dir=tmp_path, runner=runner) == 999
    assert len(calls) == 1


def test_gid_probe_failure_raises_with_stderr_and_hint(tmp_path):
    runner = lambda c, **k: _proc("", returncode=1, stderr='context "gpu-wsl" does not exist')  # noqa: E731
    target = dc.DockerTarget("gpu-wsl", "file", True, None, None)
    with pytest.raises(DevbaseError) as e:
        dc.ensure_remote_gid(target, cache_dir=tmp_path, runner=runner)
    assert "does not exist" in str(e.value) and "docker.gid" in str(e.value)
    assert not (tmp_path / "docker-gid" / "gpu-wsl").exists()


def test_gid_probe_non_integer_raises(tmp_path):
    runner = lambda c, **k: _proc("abc\n")  # noqa: E731
    target = dc.DockerTarget("gpu-wsl", "file", True, None, None)
    with pytest.raises(DevbaseError):
        dc.ensure_remote_gid(target, cache_dir=tmp_path, runner=runner)


def test_gid_zero_warns_but_is_used(tmp_path, caplog):
    runner = lambda c, **k: _proc("0\n")  # noqa: E731
    target = dc.DockerTarget("gpu-wsl", "file", True, None, None)
    with caplog.at_level("WARNING"):
        assert dc.ensure_remote_gid(target, cache_dir=tmp_path, runner=runner) == 0
    assert "docker.gid" in caplog.text


def test_gid_cache_ignores_garbage(tmp_path):
    (tmp_path / "docker-gid").mkdir()
    (tmp_path / "docker-gid" / "gpu-wsl").write_text("garbage")
    runner = lambda c, **k: _proc("999\n")  # noqa: E731
    target = dc.DockerTarget("gpu-wsl", "file", True, None, None)
    assert dc.ensure_remote_gid(target, cache_dir=tmp_path, runner=runner) == 999
    assert Path(tmp_path / "docker-gid" / "gpu-wsl").read_text().strip() == "999"
