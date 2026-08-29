"""起動ログの 1 行出力 (PLAN39 Task 7 / AC10)

「どのグループで、どのアカウントとして動いているか」を起動時に 1 行出す。
entrypoint は ``set -e`` で動くため、未ログインや gcloud 不在で**起動が落ちない**
ことが要点になる。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "containers" / "base" / "entrypoint.sh"


# gcloud を含まない最小の PATH。`/nonexistent` にすると bash 自体も見つからない。
MINIMAL_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def run(script: str, cwd: Path, path: str | None = None):
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("DEVBASE_", "GIT_", "CLOUDSDK_"))}
    if path is not None:
        base["PATH"] = path
    full = f'set -e\nDEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n{script}\n'
    return subprocess.run(["bash", "-c", full], cwd=cwd,
                          env=base, capture_output=True, text=True)


@pytest.fixture
def fake_bin(tmp_path: Path):
    """``gcloud`` を差し替えるための PATH を組み立てる。"""
    d = tmp_path / "bin"
    d.mkdir()

    def install(script: str):
        path = d / "gcloud"
        path.write_text(f"#!/bin/bash\n{script}\n")
        path.chmod(0o755)
        return f"{d}:{os.environ['PATH']}"

    return install


def test_group_and_account_are_reported(tmp_path, fake_bin):
    path = fake_bin('echo "someone@example.com"')

    result = run('CLOUDSDK_CONFIG=/persistent/group/gcloud '
                 'devbase_log_account_group "kkg"', tmp_path, path)

    assert result.returncode == 0, result.stderr
    assert "Account group: kkg" in result.stdout
    assert "gcloud account: someone@example.com" in result.stdout
    assert "CLOUDSDK_CONFIG: /persistent/group/gcloud" in result.stdout


def test_unauthenticated_gcloud_does_not_stop_startup(tmp_path, fake_bin):
    """未ログインだと gcloud は非 0 を返す。set -e で起動を落とさない。"""
    path = fake_bin('echo "ERROR: unset" >&2; exit 1')

    result = run('devbase_log_account_group "default"', tmp_path, path)

    assert result.returncode == 0, result.stderr
    assert "gcloud account: unset" in result.stdout


def test_empty_account_is_reported_as_unset(tmp_path, fake_bin):
    """`gcloud config get account` は未設定でも終了コード 0 で空を返すことがある。"""
    path = fake_bin('exit 0')

    result = run('devbase_log_account_group "default"', tmp_path, path)

    assert result.returncode == 0, result.stderr
    assert "gcloud account: unset" in result.stdout


def test_missing_gcloud_is_reported(tmp_path):
    """gcloud を含まないイメージでも落ちない。"""
    result = run('devbase_log_account_group "default"', tmp_path, path=MINIMAL_PATH)

    assert result.returncode == 0, result.stderr
    assert "gcloud not installed" in result.stdout


def test_group_defaults_when_omitted(tmp_path):
    result = run('devbase_log_account_group', tmp_path, path=MINIMAL_PATH)

    assert result.returncode == 0, result.stderr
    assert "Account group: default" in result.stdout
    assert "CLOUDSDK_CONFIG: unset" in result.stdout
