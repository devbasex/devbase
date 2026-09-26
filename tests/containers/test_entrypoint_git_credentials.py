"""entrypoint の Git 認証情報の配置 (3.1 GIT_CREDENTIALS_BASE64 / 3.3 旧来のトークン)

``containers/base/entrypoint.sh`` の「3. Setup Git credentials」から「4. Setup AWS
configuration」の手前までを切り出し、``DEVBASE_ENTRYPOINT_LIB_ONLY=1`` で関数を読み込んだ
あとに実行する。``sudo`` は失敗する stub に差し替え、``cat`` + ``chmod`` の経路を通す。
"""

from __future__ import annotations

import base64
import os
import stat
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "containers" / "base" / "entrypoint.sh"

START = "# 3. Setup Git credentials"
END = "# 4. Setup AWS configuration"


def git_block() -> str:
    text = ENTRYPOINT.read_text()
    begin = text.index(START)
    return text[begin:text.index(END, begin)]


def run(tmp_path: Path, env: dict) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    sudo = stub / "sudo"
    sudo.write_text("#!/bin/sh\nexit 1\n")
    sudo.chmod(0o755)
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("DEVBASE_", "GIT_", "GITHUB_"))}
    script = f'DEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n{git_block()}\n'
    return subprocess.run(
        ["bash", "-c", script], cwd=tmp_path,
        env={**base, "HOME": str(home), "PATH": f"{stub}:{base['PATH']}",
             "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"), **env},
        capture_output=True, text=True,
    )


def helper(tmp_path: Path) -> str:
    return subprocess.run(
        ["git", "config", "--file", str(tmp_path / "home" / ".gitconfig"),
         "credential.helper"],
        capture_output=True, text=True,
    ).stdout.strip()


def creds(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".git-credentials"


def test_base64_restores_credentials_file(tmp_path):
    content = "https://user:secret@example.com\n"
    b64 = base64.b64encode(content.encode()).decode()
    result = run(tmp_path, {"GIT_CREDENTIALS_BASE64": b64})
    assert result.returncode == 0, result.stderr
    assert creds(tmp_path).read_text() == content
    assert stat.S_IMODE(creds(tmp_path).stat().st_mode) == 0o600
    assert "Restoring git credentials from GIT_CREDENTIALS_BASE64..." in result.stdout
    assert "Git credentials restored successfully" in result.stdout
    assert helper(tmp_path) == "store"


def test_base64_replaces_stale_file(tmp_path):
    (tmp_path / "home").mkdir()
    creds(tmp_path).write_text("stale\n")
    b64 = base64.b64encode(b"https://new@example.com\n").decode()
    result = run(tmp_path, {"GIT_CREDENTIALS_BASE64": b64})
    assert result.returncode == 0, result.stderr
    assert creds(tmp_path).read_text() == "https://new@example.com\n"


def test_base64_wins_over_legacy_token(tmp_path):
    b64 = base64.b64encode(b"https://b64@example.com\n").decode()
    result = run(tmp_path, {"GIT_CREDENTIALS_BASE64": b64,
                            "GITHUB_PERSONAL_ACCESS_TOKEN": "tok"})
    assert result.returncode == 0, result.stderr
    assert creds(tmp_path).read_text() == "https://b64@example.com\n"
    assert "legacy" not in result.stdout


def test_legacy_token_writes_github_line(tmp_path):
    result = run(tmp_path, {"GITHUB_PERSONAL_ACCESS_TOKEN": "tok123"})
    assert result.returncode == 0, result.stderr
    assert creds(tmp_path).read_text() == "https://x-access-token:tok123@github.com\n"
    assert stat.S_IMODE(creds(tmp_path).stat().st_mode) == 0o600
    assert "Using legacy GITHUB_PERSONAL_ACCESS_TOKEN..." in result.stdout
    assert helper(tmp_path) == "store"


@pytest.mark.parametrize("token", ["", None])
def test_nothing_written_without_inputs(tmp_path, token):
    env = {} if token is None else {"GITHUB_PERSONAL_ACCESS_TOKEN": token}
    result = run(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert not creds(tmp_path).exists()
    assert helper(tmp_path) == "store"


def test_custom_helper_is_kept_with_base64(tmp_path):
    b64 = base64.b64encode(b"x\n").decode()
    result = run(tmp_path, {"GIT_CREDENTIALS_BASE64": b64,
                            "GIT_CREDENTIAL_HELPER": "cache"})
    assert result.returncode == 0, result.stderr
    assert helper(tmp_path) == "cache"
    assert "Git credential helper configured: cache" in result.stdout


def test_legacy_token_overrides_custom_helper_to_store(tmp_path):
    result = run(tmp_path, {"GITHUB_PERSONAL_ACCESS_TOKEN": "t",
                            "GIT_CREDENTIAL_HELPER": "cache"})
    assert result.returncode == 0, result.stderr
    assert helper(tmp_path) == "store"
