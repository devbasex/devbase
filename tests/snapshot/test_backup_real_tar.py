"""控えのコマンドを実際の GNU tar と zstd で走らせる (Docker は起動しない)

コンテナの ``/source`` は起動のたびに作られ inode が変わる。起点が ``/source`` そのものだと
GNU tar が毎回「新しいディレクトリ」とみなし、差分がボリューム全体になる。ここでは
ボリュームのディレクトリを毎回新しい親へ移して、同じ状況を作る。GNU tar と zstd が無ければ飛ばす。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from devbase.snapshot.exclude import DEFAULT_EXCLUDES
from devbase.snapshot.manager import (
    ARCHIVE_MOUNT,
    BACKUP_ROOT,
    FULL_ARCHIVE,
    RESTORE_ROOT,
    SnapshotManager,
    incr_archive_name,
)


def _has_gnu_tar() -> bool:
    if not shutil.which("tar"):
        return False
    out = subprocess.run(["tar", "--version"], capture_output=True, text=True).stdout
    return "GNU tar" in out


pytestmark = pytest.mark.skipif(
    not (_has_gnu_tar() and shutil.which("zstd")), reason="GNU tar と zstd が要る")

VOLUMES = {"ai": "devbase_home_ubuntu", "group": "devbase_home_acme"}


def _run(command: str, source: Path, archive_dir: Path, target: Path) -> None:
    command = (command.replace(ARCHIVE_MOUNT, str(archive_dir))
               .replace(BACKUP_ROOT, str(source))
               .replace(RESTORE_ROOT, str(target)))
    subprocess.run(["bash", "-c", command], check=True, capture_output=True)


def _backup(tmp_path: Path, archive: str, run: int) -> None:
    """ボリュームを新しい親 (``source-<run>``) へ移してから控える。"""
    source = tmp_path / f"source-{run}"
    source.mkdir()
    for sub in VOLUMES:
        prev = next(tmp_path.glob(f"source-*/{sub}"), None) or tmp_path / "init" / sub
        prev.rename(source / sub)
    _run(SnapshotManager.backup_command(archive, VOLUMES), source,
         tmp_path / "archive", tmp_path / "unused")


def _members(archive: Path) -> list:
    out = subprocess.run(
        ["bash", "-c", f"zstd -dc {archive} | tar -tf -"],
        capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if not line.endswith("/")]


def test_incremental_from_a_fresh_source_root_holds_only_changes(tmp_path):
    (tmp_path / "archive").mkdir()
    for sub in VOLUMES:
        (tmp_path / "init" / sub).mkdir(parents=True)
        for i in range(20):
            (tmp_path / "init" / sub / f"f{i}.txt").write_text(f"{sub} {i}")
    _backup(tmp_path, FULL_ARCHIVE, 1)

    ai = tmp_path / "source-1" / "ai"
    (ai / "f0.txt").unlink()
    (ai / "new.txt").write_text("new")
    _backup(tmp_path, incr_archive_name(1), 2)

    assert _members(tmp_path / "archive" / incr_archive_name(1)) == ["ai/new.txt"]

    target = tmp_path / "target"
    for sub in VOLUMES:
        (target / sub).mkdir(parents=True)
    for archive in (FULL_ARCHIVE, incr_archive_name(1)):
        _run(SnapshotManager.restore_command(archive), tmp_path / "unused",
             tmp_path / "archive", target)
    assert not (target / "ai" / "f0.txt").exists()
    assert (target / "ai" / "new.txt").read_text() == "new"
    assert len(list((target / "group").iterdir())) == 20


# ---------------------------------------------------------------------------
# 除外 (#368)
# ---------------------------------------------------------------------------

EXCLUDED = [
    ".gemini/tmp/p1/logs.json", ".gemini/tmp/p1/logs/x.log",
    ".codex/logs_2.sqlite", ".codex/logs_2.sqlite-wal", ".codex/logs_2.sqlite-shm",
    ".codex/.tmp/marketplaces/m.json", ".codex/cache/c",
    ".claude/plugins/cache/p/file", ".claude/plugins/marketplaces/m/file",
    ".serena/logs/2026-10-12/log.txt",
]
KEPT = [
    ".claude/projects/p/s.jsonl", ".codex/sessions/s.jsonl",
    ".codex/thread_history_1.sqlite", ".codex/state_5.sqlite",
    ".gemini/tmp/p1/chats/session-1.jsonl", ".gemini/tmp/p1/chats/logs.json",
    ".gemini/tmp/p1/chats/logs/x", ".claude/plugins/installed_plugins.json",
    ".claude/plugins/known_marketplaces.json", ".claude/plugins/data/d/state",
    ".serena/language_servers/ls/bin", "work/.codex/cache/keep",
]


def _excludes() -> tuple:
    return tuple(sorted(DEFAULT_EXCLUDES))


def _backup_excluded(tmp_path: Path, archive: str, run: int) -> None:
    source = tmp_path / f"source-{run}"
    source.mkdir()
    for sub in VOLUMES:
        prev = next(tmp_path.glob(f"source-*/{sub}"), None) or tmp_path / "init" / sub
        prev.rename(source / sub)
    _run(SnapshotManager.backup_command(archive, VOLUMES, _excludes()), source,
         tmp_path / "archive", tmp_path / "unused")


def _populate(root: Path, paths: list, text: str = "v1") -> None:
    for rel in paths:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _init_volumes(tmp_path: Path, paths: list) -> None:
    (tmp_path / "archive").mkdir()
    for sub in VOLUMES:
        _populate(tmp_path / "init" / sub, paths)


def test_full_leaves_out_default_excludes_and_keeps_the_records(tmp_path):
    """受け入れ条件 1・2・I5・I7"""
    _init_volumes(tmp_path, EXCLUDED + KEPT)
    _backup_excluded(tmp_path, FULL_ARCHIVE, 1)

    members = set(_members(tmp_path / "archive" / FULL_ARCHIVE))
    for sub in VOLUMES:
        for rel in EXCLUDED:
            assert f"{sub}/{rel}" not in members
        for rel in KEPT:
            assert f"{sub}/{rel}" in members
    assert not any("/logs_2.sqlite" in m or "/plugins/cache/" in m for m in members)


def test_incremental_leaves_out_rewritten_excluded_files(tmp_path):
    """受け入れ条件 3"""
    _init_volumes(tmp_path, EXCLUDED + KEPT)
    _backup_excluded(tmp_path, FULL_ARCHIVE, 1)

    ai = tmp_path / "source-1" / "ai"
    (ai / ".codex/logs_2.sqlite").write_text("v2 longer")
    (ai / ".codex/state_5.sqlite").write_text("v2 longer")
    _backup_excluded(tmp_path, incr_archive_name(1), 2)

    members = _members(tmp_path / "archive" / incr_archive_name(1))
    assert "ai/.codex/state_5.sqlite" in members
    assert "ai/.codex/logs_2.sqlite" not in members


def test_restore_has_no_excluded_paths_and_latest_contents(tmp_path):
    """受け入れ条件 8: 同じ除外の full と差分を順に展開する。"""
    _init_volumes(tmp_path, EXCLUDED + KEPT)
    _backup_excluded(tmp_path, FULL_ARCHIVE, 1)
    (tmp_path / "source-1" / "ai" / ".codex/state_5.sqlite").write_text("v2 longer")
    _backup_excluded(tmp_path, incr_archive_name(1), 2)

    target = tmp_path / "target"
    for sub in VOLUMES:
        (target / sub).mkdir(parents=True)
    for archive in (FULL_ARCHIVE, incr_archive_name(1)):
        _run(SnapshotManager.restore_command(archive), tmp_path / "unused",
             tmp_path / "archive", target)
    assert (target / "ai/.codex/state_5.sqlite").read_text() == "v2 longer"
    for sub in VOLUMES:
        for rel in EXCLUDED:
            assert not (target / sub / rel).exists(), rel
        for rel in KEPT:
            assert (target / sub / rel).exists(), rel


def test_missing_excluded_paths_do_not_fail_or_warn(tmp_path):
    """受け入れ条件 13: 除外のパスが 1 つも無くても終了コード 0 で警告が無い。"""
    _init_volumes(tmp_path, ["only.txt"])
    source = tmp_path / "source-1"
    source.mkdir()
    for sub in VOLUMES:
        (tmp_path / "init" / sub).rename(source / sub)
    command = (SnapshotManager.backup_command(FULL_ARCHIVE, VOLUMES, _excludes())
               .replace(ARCHIVE_MOUNT, str(tmp_path / "archive"))
               .replace(BACKUP_ROOT, str(source)))
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    assert result.returncode == 0
    assert "tar:" not in result.stderr
