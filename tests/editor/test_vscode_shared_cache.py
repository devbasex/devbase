"""起動の後の処理で Dev Containers の共有の置き場を片付ける (#507)

dev インスタンスには名前 "vscode" のボリュームが /vscode に付き、Dev Containers が
拡張のキャッシュと本体の新しい版を置く。``prune_shared_cache`` は ``docker exec -u 0`` を
1 回打ち、拡張のキャッシュを拡張ごとに新しい版へ減らし、``~/.vscode-server/bin`` の
リンク切れを消す。手順は一時のディレクトリに当てて確かめる。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from devbase.editor import vscode_server

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


# ---------------------------------------------------------------------------
# docker exec の打ち方
# ---------------------------------------------------------------------------

def test_runs_one_docker_exec_as_root():
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert vscode_server.prune_shared_cache("proj-a-dev-1", runner=runner) is True

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:7] == ["docker", "exec", "-u", "0", "proj-a-dev-1", "sh", "-c"]
    assert "/vscode/vscode-server/extensionsCache" in cmd[7]
    assert "/home/ubuntu/.vscode-server/bin" in cmd[7]


def test_failure_returns_false_without_raising():
    def failing(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "no such container")

    def raising(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    assert vscode_server.prune_shared_cache("x", runner=failing) is False
    assert vscode_server.prune_shared_cache("x", runner=raising) is False


# ---------------------------------------------------------------------------
# 手順 (一時のディレクトリに当てる)
# ---------------------------------------------------------------------------

@pytest.fixture
def dirs(tmp_path: Path):
    cache = tmp_path / "vscode" / "vscode-server" / "extensionsCache"
    bin_dir = tmp_path / "home" / ".vscode-server" / "bin"
    cache.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    return cache, bin_dir


def _run(cache: Path, bin_dir: Path, keep: int = 3):
    return subprocess.run(
        ["sh", "-c", vscode_server.shared_cache_prune_script(
            str(cache), str(bin_dir), keep=keep)],
        capture_output=True, text=True)


def _touch(path: Path, age: int) -> None:
    """``age`` 秒前の更新時刻でファイルを作る (大きいほど古い)"""
    path.write_bytes(b"x")
    t = 1_700_000_000 - age
    os.utime(path, (t, t))


def test_keeps_newest_versions_per_extension(dirs):
    cache, bin_dir = dirs
    names = [f"anthropic.claude-code-2.1.3{i:02d}-linux-x64" for i in range(5)]
    for age, name in enumerate(reversed(names)):  # 末尾ほど新しい
        _touch(cache / name, age)
    _touch(cache / "ms-ceintl.vscode-language-pack-ja-1.131.2026090407", 100)
    _touch(cache / "openai.codex-audio-26.1007.21434", 50)
    _touch(cache / "openai.codex-audio-26.1021.22027", 10)

    result = _run(cache, bin_dir)

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in cache.iterdir()) == sorted([
        *names[2:],
        "ms-ceintl.vscode-language-pack-ja-1.131.2026090407",
        "openai.codex-audio-26.1007.21434",
        "openai.codex-audio-26.1021.22027",
    ])


def test_platforms_count_as_the_same_extension(dirs):
    """CPU の種類が違っても同じ拡張として数える (新しい順に keep 個)"""
    cache, bin_dir = dirs
    _touch(cache / "anthropic.claude-code-2.1.311-linux-arm64", 30)
    _touch(cache / "anthropic.claude-code-2.1.312-linux-arm64", 20)
    _touch(cache / "anthropic.claude-code-2.1.312-linux-x64", 10)

    result = _run(cache, bin_dir, keep=2)

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in cache.iterdir()) == [
        "anthropic.claude-code-2.1.312-linux-arm64",
        "anthropic.claude-code-2.1.312-linux-x64",
    ]


def test_directories_and_links_in_cache_are_left_alone(dirs, tmp_path: Path):
    """リンクとディレクトリは数えも消しもしない。リンク先も消さない"""
    cache, bin_dir = dirs
    outside = tmp_path / "outside-1.0.0"
    _touch(outside, 0)
    (cache / "foo.bar-1.0.0").symlink_to(outside)
    (cache / "foo.bar-0.9.0").mkdir()
    _touch(cache / "foo.bar-0.8.0", 10)

    result = _run(cache, bin_dir, keep=1)

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in cache.iterdir()) == [
        "foo.bar-0.8.0", "foo.bar-0.9.0", "foo.bar-1.0.0"]
    assert outside.exists()


def test_linked_cache_dir_is_not_followed(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    for i in range(3):
        _touch(real / f"foo.bar-1.0.{i}", i)
    cache = tmp_path / "extensionsCache"
    cache.symlink_to(real)
    bin_dir = tmp_path / "bin"

    result = _run(cache, bin_dir, keep=1)

    assert result.returncode == 0, result.stderr
    assert len(list(real.iterdir())) == 3


def test_missing_dirs_do_nothing(tmp_path: Path):
    result = _run(tmp_path / "no-cache", tmp_path / "no-bin")

    assert result.returncode == 0, result.stderr


def test_removes_only_dangling_commit_links_in_bin(dirs, tmp_path: Path):
    cache, bin_dir = dirs
    live = tmp_path / "vscode" / "vscode-server" / "bin" / "linux-x64" / COMMIT_A
    live.mkdir(parents=True)
    (bin_dir / COMMIT_A).symlink_to(live)                       # リンク先がある
    (bin_dir / COMMIT_B).symlink_to(tmp_path / "gone" / COMMIT_B)  # 切れている
    real = bin_dir / ("c" * 40)
    real.mkdir()                                                # リンクでない版
    (bin_dir / "not-a-commit").symlink_to(tmp_path / "gone" / "x")  # 名前が違う

    result = _run(cache, bin_dir)

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in bin_dir.iterdir()) == sorted(
        [COMMIT_A, "c" * 40, "not-a-commit"])
