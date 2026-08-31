"""VS Code Server ディレクトリの所有者初期化 (PLAN36 Task 3)

``~/.vscode-server`` には named volume がマウントされる (PLAN36 Task 2)。空の
named volume は **root 所有**で作られるため、開発ユーザー (uid 1000) のままでは
VS Code Server をインストールできず ``Permission denied`` になる (前提 6 / AC5)。

``containers/base/entrypoint.sh`` を ``DEVBASE_ENTRYPOINT_LIB_ONLY=1`` で source し、
一時ディレクトリを ``$HOME`` に見立てて関数を直接呼ぶ。Docker には依存しない。
root 所有からの ``chown`` そのものは実機の検証手順 (AC5) で確かめる。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "containers" / "base" / "entrypoint.sh"


def run(script: str, cwd: Path) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("DEVBASE_", "GIT_"))}
    full = f'set -e\nDEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n{script}\n'
    return subprocess.run(
        ["bash", "-c", full], cwd=cwd, env=base, capture_output=True, text=True)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


def setup(home: Path) -> subprocess.CompletedProcess:
    result = run(f'devbase_setup_vscode_server_dir "{home}" "$(id -un)"', home)
    assert result.returncode == 0, result.stderr or result.stdout
    return result


def test_directory_is_prepared(home: Path):
    """マウント先が使える状態になる (AC5)。"""
    setup(home)

    assert (home / ".vscode-server").is_dir()


def test_existing_contents_are_kept(home: Path):
    """2 回目以降は何も壊さない。再ダウンロードを避ける前提そのもの (AC1)。"""
    server = home / ".vscode-server"
    (server / "bin" / "abc123").mkdir(parents=True)
    (server / "bin" / "abc123" / "marker").write_text("kept")

    setup(home)

    assert (server / "bin" / "abc123" / "marker").read_text() == "kept"


def test_is_idempotent(home: Path):
    """繰り返し呼んでも失敗しない (entrypoint は起動のたびに走る)。"""
    setup(home)
    setup(home)

    assert (home / ".vscode-server").is_dir()


def test_writable_directory_does_not_invoke_sudo(home: Path):
    """既に書けるなら sudo を呼ばない。sudo の無い環境でも起動を落とさない。"""
    (home / ".vscode-server").mkdir()

    # PATH の先頭へ「呼ばれたら失敗する sudo」を置いて、呼び出しの有無を見る
    fake_bin = home.parent / "bin"
    fake_bin.mkdir()
    sudo = fake_bin / "sudo"
    sudo.write_text("#!/bin/sh\necho 'sudo was called' >&2\nexit 1\n")
    sudo.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c",
         f'set -e\nDEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n'
         f'devbase_setup_vscode_server_dir "{home}" "$(id -un)"\n'],
        cwd=home,
        env={**{k: v for k, v in os.environ.items()
                if not k.startswith(("DEVBASE_", "GIT_"))},
             "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
        capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "sudo was called" not in result.stderr
