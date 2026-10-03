"""entrypoint の DinD の廃止の知らせ (#400)

base は dockerd を入れなくなり、DinD を廃止した。``ENABLE_DIND`` を残した利用者には 1 行で知らせ、
起動は止めない。``containers/base/entrypoint.sh`` を ``DEVBASE_ENTRYPOINT_LIB_ONLY=1`` で source し、
``devbase_notice_dind_removed`` が docker.sock・dockerd・``docker info`` に触れないことを固定する。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "containers" / "base" / "entrypoint.sh"

# 知らせが呼んではならないコマンド。呼ばれたら名前を記録する
FORBIDDEN = ["sudo", "dockerd", "docker", "docker-init", "rm", "pgrep", "sleep"]


def run_notice(tmp_path: Path, env: dict) -> tuple[subprocess.CompletedProcess, list[str]]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    calls = tmp_path / "calls"
    for name in FORBIDDEN:
        stub = stub_dir / name
        stub.write_text(f'#!/bin/sh\necho {name} >> "{calls}"\n')
        stub.chmod(0o755)
    base = {k: v for k, v in os.environ.items()
            if not k.startswith("DEVBASE_") and k != "ENABLE_DIND"}
    base["PATH"] = f"{stub_dir}:{base.get('PATH', '')}"
    script = (
        f'set -e\nDEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n'
        "devbase_notice_dind_removed\n"
    )
    result = subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, env={**base, **env},
        capture_output=True, text=True,
    )
    called = calls.read_text().split() if calls.exists() else []
    return result, called


@pytest.mark.parametrize("value", ["true", "1"])
def test_enabled_prints_one_notice_and_touches_nothing(tmp_path, value):
    result, called = run_notice(tmp_path, {"ENABLE_DIND": value})
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    assert "ENABLE_DIND" in lines[0]
    assert "docker.sock" in lines[0]
    assert result.stderr == ""
    assert called == []


@pytest.mark.parametrize("env", [{}, {"ENABLE_DIND": "false"}, {"ENABLE_DIND": ""},
                                 {"ENABLE_DIND": "yes"}])
def test_not_enabled_prints_nothing(tmp_path, env):
    result, called = run_notice(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert called == []


def _main_lines() -> list[str]:
    """LIB_ONLY の return より後の、起動のときだけ走る本体"""
    lines = ENTRYPOINT.read_text().splitlines()
    guard = next(i for i, line in enumerate(lines) if "DEVBASE_ENTRYPOINT_LIB_ONLY:-" in line)
    return lines[guard:]


def _index(lines: list[str], pattern: str) -> int:
    for i, line in enumerate(lines):
        if re.search(pattern, line):
            return i
    raise AssertionError(f"pattern not found: {pattern}")


def test_notice_runs_between_aws_and_ai_settings():
    lines = _main_lines()
    aws = _index(lines, r'^if \[ -n "\$AWS_CONFIG_BASE64" \]')
    notice = _index(lines, r"^devbase_notice_dind_removed$")
    ai = _index(lines, r"^devbase_require_account_group ")
    assert aws < notice < ai


def test_main_does_not_start_dockerd():
    body = "\n".join(line for line in _main_lines() if not line.lstrip().startswith("#"))
    for word in ["dockerd", "/usr/local/bin/dind", "docker info", "/var/run/docker.sock"]:
        assert word not in body, f"entrypoint の本体に {word} が残っている"
