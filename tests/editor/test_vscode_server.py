"""起動の後の処理で VS Code Server の bin の所有者を揃える (#463)

建て直す前のイメージの entrypoint は ``~/.vscode-server`` だけを開発ユーザーへ直し、
共通のボリュームの根 ``~/.vscode-server/bin`` は root のまま残る。``prepare_bin_dir``
は ``docker exec -u 0`` を 1 回打ち、``bin`` の所有者を ``~/.vscode-server`` の所有者へ
揃える。手順は一時のディレクトリに当てて確かめる。``chown`` は差し替えて呼び出しを見る。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from devbase.editor import vscode_server


# ---------------------------------------------------------------------------
# docker exec の打ち方
# ---------------------------------------------------------------------------

def test_runs_one_docker_exec_as_root():
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert vscode_server.prepare_bin_dir("proj-a-dev-1", runner=runner) is True

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:6] == ["docker", "exec", "-u", "0", "proj-a-dev-1", "sh"]
    assert cmd[6] == "-c"
    assert "/home/ubuntu/.vscode-server/bin" in cmd[7]
    assert "/data" not in cmd[7]
    assert "-R" not in cmd[7]


def test_failure_returns_false_without_raising():
    def failing(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "no such container")

    def raising(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)

    assert vscode_server.prepare_bin_dir("x", runner=failing) is False
    assert vscode_server.prepare_bin_dir("x", runner=raising) is False


# ---------------------------------------------------------------------------
# 手順 (一時のディレクトリに当てる)
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    """呼ばれた引数を記録する chown を PATH の先頭へ置く"""
    path = tmp_path / "fakebin"
    path.mkdir()
    chown = path / "chown"
    chown.write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/chown.log"\n')
    chown.chmod(0o755)
    return path


def _run(home: Path, fake_bin: Path, stat_stub: str | None = None):
    if stat_stub is not None:
        stat = fake_bin / "stat"
        stat.write_text(stat_stub)
        stat.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}
    return subprocess.run(
        ["sh", "-c", vscode_server.bin_fix_script(str(home))],
        env=env, capture_output=True, text=True)


def _chowns(tmp_path: Path) -> list[str]:
    log = tmp_path / "chown.log"
    return log.read_text().splitlines() if log.exists() else []


def test_missing_bin_does_nothing(tmp_path: Path, fake_bin: Path):
    home = tmp_path / "home"
    (home / ".vscode-server").mkdir(parents=True)

    result = _run(home, fake_bin)

    assert result.returncode == 0, result.stderr
    assert _chowns(tmp_path) == []


def test_same_owner_does_nothing(tmp_path: Path, fake_bin: Path):
    home = tmp_path / "home"
    (home / ".vscode-server" / "bin").mkdir(parents=True)

    result = _run(home, fake_bin)

    assert result.returncode == 0, result.stderr
    assert _chowns(tmp_path) == []


def test_different_owner_is_aligned_to_the_parent(tmp_path: Path, fake_bin: Path):
    """bin が root、~/.vscode-server が開発ユーザーなら、bin 1 つだけを揃える"""
    home = tmp_path / "home"
    (home / ".vscode-server" / "bin").mkdir(parents=True)
    stub = (
        "#!/bin/sh\n"
        'for a in "$@"; do last="$a"; done\n'
        'case "$last" in */bin) echo 0:0 ;; *) echo 1000:1000 ;; esac\n'
    )

    result = _run(home, fake_bin, stat_stub=stub)

    assert result.returncode == 0, result.stderr
    assert _chowns(tmp_path) == [f"1000:1000 {home}/.vscode-server/bin"]
