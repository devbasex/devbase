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
