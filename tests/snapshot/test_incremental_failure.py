"""差分の作成の失敗 (#466)

既存の世代へ差分を積むとき、世代の snar を写した仮の snar と仮の名前の差分へ控え、成功してから
差分 → snar の順に改名で置く。控え・写し・改名のどこで失敗しても、世代は「打つ前のまま」
「新しい差分が置かれ snar は打つ前のまま」「新しい差分が置かれ snar が進んだ」のどれかで残る。
``DEVBASE_ROOT`` を使わず ``tmp_path`` に閉じ、``_run_docker_tar`` を差し替えて Docker を起動しない。
実の GNU tar と zstd を使うテストは、どちらかが無ければ飛ぶ。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import types
from pathlib import Path

import pytest
import yaml

from devbase.commands import container
from devbase.commands.snapshot import cmd_snapshot
from devbase.errors import SnapshotCommandError, SnapshotError
from devbase.snapshot import manager as manager_mod
from devbase.snapshot.manager import (
    ARCHIVE_MOUNT,
    FULL_ARCHIVE,
    INCR_ARCHIVE_GLOB,
    INCR_ARCHIVE_TMP,
    SNAR_FILE,
    SNAR_FILE_TMP,
    SnapshotManager,
    incr_archive_name,
    is_archive_file,
)

from .test_backup_real_tar import LocalTarManager, _has_gnu_tar, _members

_ARCHIVE_RE = re.compile(r"-o /backup/([^\s;]+)")
_SNAR_RE = re.compile(r"--listed-incremental=/backup/([^\s;]+)")

real_tar = pytest.mark.skipif(
    not (_has_gnu_tar() and shutil.which("zstd")), reason="GNU tar と zstd が要る")


@pytest.fixture(autouse=True)
def _group_env(monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "acme")


class FakeManager(SnapshotManager):
    """``docker run`` を実行せず、控えのコマンドが書くはずのファイルを回ごとに違う中身で置く。

    ``exit_codes`` に (tar, zstd) の終了コードを入れると、仮のファイルを書いた後に控えの
    パイプをその終了コードの組に差し替えて bash で打ち、失敗なら ``SnapshotCommandError`` にする。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list = []
        self.runs = 0
        self.exit_codes = (0, 0)

    def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
        self.calls.append({"mode": mode, "command": command})
        if mode != "backup":
            return None
        self.runs += 1
        (snap_dir / _ARCHIVE_RE.search(command).group(1)).write_text(f"archive-{self.runs}")
        (snap_dir / _SNAR_RE.search(command).group(1)).write_text(f"snar-{self.runs}")
        tar, zstd = self.exit_codes
        # 控えのパイプだけを差し替える。前の形 (``cp … .bak && `` を前に付ける) でも同じに打つ
        pipeline = re.sub(r"^(?:cp \S+ \S+ && )?tar .*? \| zstd [^;]*",
                          f"(exit {tar}) | (exit {zstd})", command)
        result = subprocess.run(["bash", "-c", pipeline], capture_output=True, text=True)
        if result.returncode != 0:
            raise SnapshotCommandError(
                f"Dockerでのtar操作に失敗しました: tar={tar} zstd={zstd}",
                stderr=f"tar={tar} zstd={zstd}")
        return None


def _snap_dir(root: Path) -> Path:
    return root / "backups" / "g"


def _snapshot(snap_dir: Path) -> dict:
    return {p.name: p.read_bytes() for p in snap_dir.iterdir()}


def _state(root: Path) -> bytes:
    return (root / "backups" / "snapshot.yml").read_bytes()


def _meta(snap_dir: Path) -> dict:
    return yaml.safe_load((snap_dir / "meta.yml").read_text())


def _with_incrementals(root: Path, count: int) -> FakeManager:
    """世代 ``g`` を full と ``count`` 本の差分で作る。"""
    mgr = FakeManager(root)
    mgr.create(name="g")
    for _ in range(count):
        mgr.create(name="g")
    mgr.calls.clear()
    return mgr


def _fail_replace_to(monkeypatch, name: str) -> None:
    """``os.replace`` のうち、``name`` へ改名する 1 回を失敗させる。"""
    real = os.replace

    def fake(src, dst):
        if Path(dst).name == name:
            raise OSError("injected")
        return real(src, dst)

    monkeypatch.setattr(manager_mod.os, "replace", fake)


# ---------------------------------------------------------------------------
# 控えの失敗 (受け入れ条件 1・3・I1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("count", [0, 2])
@pytest.mark.parametrize("exit_codes", [(2, 0), (0, 1), (141, 153)])
def test_failed_backup_keeps_the_generation_byte_for_byte(tmp_path, count, exit_codes):
    mgr = _with_incrementals(tmp_path, count)
    snap_dir = _snap_dir(tmp_path)
    before = _snapshot(snap_dir)
    state_before = _state(tmp_path)

    mgr.exit_codes = exit_codes
    with pytest.raises(SnapshotError, match=f"tar={exit_codes[0]} zstd={exit_codes[1]}"):
        mgr.create(name="g")

    assert _snapshot(snap_dir) == before
    assert _state(tmp_path) == state_before


def test_tar_exit_1_is_still_a_success(tmp_path):
    """tar の 1 (読む間にファイルが変わった) は今と同じく通す。"""
    mgr = _with_incrementals(tmp_path, 1)
    snap_dir = _snap_dir(tmp_path)
    mgr.exit_codes = (1, 0)
    mgr.create(name="g")
    assert (snap_dir / incr_archive_name(2)).read_text() == f"archive-{mgr.runs}"
    assert (snap_dir / SNAR_FILE).read_text() == f"snar-{mgr.runs}"


def test_failed_snar_copy_keeps_the_generation(tmp_path, monkeypatch):
    mgr = _with_incrementals(tmp_path, 1)
    snap_dir = _snap_dir(tmp_path)
    before = _snapshot(snap_dir)
    state_before = _state(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("injected")

    monkeypatch.setattr(manager_mod.shutil, "copyfile", fail)
    with pytest.raises(SnapshotError, match="世代 'g' に差分を作れませんでした: injected"):
        mgr.create(name="g")

    assert mgr.calls == []
    assert _snapshot(snap_dir) == before
    assert _state(tmp_path) == state_before


# ---------------------------------------------------------------------------
# 置く途中の失敗 (受け入れ条件 4・I2)
# ---------------------------------------------------------------------------

def test_failed_incremental_rename_leaves_the_generation_as_before(tmp_path, monkeypatch):
    """(a): 差分の改名で止まると、snar も差分の列も打つ前のまま。"""
    mgr = _with_incrementals(tmp_path, 1)
    snap_dir = _snap_dir(tmp_path)
    before = _snapshot(snap_dir)
    state_before = _state(tmp_path)

    _fail_replace_to(monkeypatch, incr_archive_name(2))
    with pytest.raises(SnapshotError, match="差分を作れませんでした"):
        mgr.create(name="g")

    assert _snapshot(snap_dir) == before
    assert _state(tmp_path) == state_before


def test_failed_snar_rename_leaves_the_new_incremental_and_the_old_snar(tmp_path, monkeypatch):
    """(b): snar の改名で止まると、新しい差分が置かれ snar は打つ前のまま。"""
    mgr = _with_incrementals(tmp_path, 1)
    snap_dir = _snap_dir(tmp_path)
    before = _snapshot(snap_dir)

    _fail_replace_to(monkeypatch, SNAR_FILE)
    with pytest.raises(SnapshotError, match="差分を作れませんでした"):
        mgr.create(name="g")

    after = _snapshot(snap_dir)
    assert set(after) == set(before) | {incr_archive_name(2)}
    assert after[incr_archive_name(2)] == f"archive-{mgr.runs}".encode()
    assert {k: v for k, v in after.items() if k != incr_archive_name(2)} == before


def test_next_incremental_after_a_stop_gets_the_next_number(tmp_path, monkeypatch):
    """(b) の世代へ次の差分を作ると、番号は重ならず snar が進む (受け入れ条件 5 のファイルの側)。"""
    mgr = _with_incrementals(tmp_path, 1)
    snap_dir = _snap_dir(tmp_path)
    with monkeypatch.context() as m:
        _fail_replace_to(m, SNAR_FILE)
        with pytest.raises(SnapshotError):
            mgr.create(name="g")

    mgr.create(name="g")
    assert sorted(p.name for p in snap_dir.glob(INCR_ARCHIVE_GLOB)) == [
        incr_archive_name(1), incr_archive_name(2), incr_archive_name(3)]
    assert (snap_dir / incr_archive_name(3)).read_text() == f"archive-{mgr.runs}"
    assert (snap_dir / SNAR_FILE).read_text() == f"snar-{mgr.runs}"
    assert not (snap_dir / INCR_ARCHIVE_TMP).exists()
    assert not (snap_dir / SNAR_FILE_TMP).exists()


# ---------------------------------------------------------------------------
# 仮の名前のファイル (受け入れ条件 6・I3)
# ---------------------------------------------------------------------------

def test_temp_names_are_not_archives(tmp_path):
    for tmp in (INCR_ARCHIVE_TMP, SNAR_FILE_TMP):
        assert not is_archive_file(tmp)
        (tmp_path / tmp).write_text("stale")
    assert not list(tmp_path.glob(INCR_ARCHIVE_GLOB))
    assert not SnapshotManager._has_previous_files(tmp_path)


def test_leftover_temps_are_discarded_before_the_backup(tmp_path):
    mgr = _with_incrementals(tmp_path, 1)
    snap_dir = _snap_dir(tmp_path)
    for tmp in (INCR_ARCHIVE_TMP, SNAR_FILE_TMP):
        (snap_dir / tmp).write_text("stale")
    future = (snap_dir / incr_archive_name(1)).stat().st_mtime + 1000
    os.utime(snap_dir / INCR_ARCHIVE_TMP, (future, future))
    assert mgr.last_snapshot_time().timestamp() < future

    seen = {}
    original = FakeManager._run_docker_tar

    def spy(self, snap_dir_, mode, command, volumes=None):
        if mode == "backup":
            seen["temps"] = {t for t in (INCR_ARCHIVE_TMP, SNAR_FILE_TMP)
                             if (snap_dir_ / t).exists()
                             and (snap_dir_ / t).read_bytes() == b"stale"}
        return original(self, snap_dir_, mode, command, volumes)

    mgr._run_docker_tar = spy.__get__(mgr)
    mgr.create(name="g")
    assert seen["temps"] == set()
    assert sorted(p.name for p in snap_dir.glob(INCR_ARCHIVE_GLOB)) == [
        incr_archive_name(1), incr_archive_name(2)]


def test_restore_does_not_pick_up_leftover_temps(tmp_path):
    mgr = _with_incrementals(tmp_path, 1)
    (_snap_dir(tmp_path) / INCR_ARCHIVE_TMP).write_text("stale")
    mgr.restore("g")
    restored = [c["command"] for c in mgr.calls if c["mode"] == "restore"]
    assert not any(INCR_ARCHIVE_TMP in c for c in restored)
    assert any(incr_archive_name(1) in c for c in restored)


# ---------------------------------------------------------------------------
# 成功の結果は今と同じ (受け入れ条件 7・I4)
# ---------------------------------------------------------------------------

def test_successful_incremental_leaves_the_same_files_without_bak(tmp_path):
    mgr = _with_incrementals(tmp_path, 0)
    snap_dir = _snap_dir(tmp_path)
    mgr.create(name="g")
    assert {p.name for p in snap_dir.iterdir()} == {
        FULL_ARCHIVE, incr_archive_name(1), SNAR_FILE, "meta.yml"}
    assert (snap_dir / SNAR_FILE).read_text() == f"snar-{mgr.runs}"
    meta = _meta(snap_dir)
    assert meta["type"] == "incremental"
    assert meta["files"] == [FULL_ARCHIVE, incr_archive_name(1)]
    assert meta["incremental_count"] == 1
    [entry] = yaml.safe_load(_state(tmp_path))["snapshots"]
    assert entry["incremental_count"] == 1


def test_existing_bak_is_left_alone(tmp_path):
    mgr = _with_incrementals(tmp_path, 1)
    snap_dir = _snap_dir(tmp_path)
    (snap_dir / f"{SNAR_FILE}.bak").write_text("old bak")
    mgr.create(name="g")
    assert (snap_dir / f"{SNAR_FILE}.bak").read_text() == "old bak"


def test_backup_command_reads_the_copied_snar(tmp_path):
    """控えに渡す仮の snar は、世代の snar の写しから始まる。"""
    mgr = _with_incrementals(tmp_path, 1)
    snap_dir = _snap_dir(tmp_path)
    expected = (snap_dir / SNAR_FILE).read_bytes()
    seen = {}

    def spy(self, snap_dir_, mode, command, volumes=None):
        seen["snar"] = (snap_dir_ / SNAR_FILE_TMP).read_bytes()
        assert f"--listed-incremental={ARCHIVE_MOUNT}/{SNAR_FILE_TMP} " in command
        return FakeManager._run_docker_tar(self, snap_dir_, mode, command, volumes)

    mgr._run_docker_tar = spy.__get__(mgr)
    mgr.create(name="g")
    assert seen["snar"] == expected


# ---------------------------------------------------------------------------
# 利用者への知らせ方は今と同じ (受け入れ条件 8・I5)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stop", ["backup", "copy", "rename"])
def test_cli_exits_1_when_the_incremental_fails(tmp_path, monkeypatch, stop):
    mgr = _with_incrementals(tmp_path, 1)
    if stop == "backup":
        mgr.exit_codes = (2, 0)
    elif stop == "copy":
        monkeypatch.setattr(manager_mod.shutil, "copyfile",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("injected")))
    else:
        _fail_replace_to(monkeypatch, SNAR_FILE)
    monkeypatch.setattr("devbase.commands.snapshot.SnapshotManager", lambda *a, **k: mgr)
    args = types.SimpleNamespace(subcommand="create", name="g", full=False, group="acme")
    assert cmd_snapshot(tmp_path, args) == 1


def test_auto_snapshot_warns_once_and_keeps_the_generation(tmp_path, monkeypatch, caplog):
    _with_incrementals(tmp_path, 1)
    snap_dir = _snap_dir(tmp_path)
    before = _snapshot(snap_dir)
    monkeypatch.setenv("DEVBASE_ROOT", str(tmp_path))
    monkeypatch.setenv("DEVBASE_SNAPSHOT_MIN_INTERVAL_MINUTES", "0")

    def fail(self, snap_dir_, mode, command, volumes=None):
        if mode == "backup":
            (snap_dir_ / INCR_ARCHIVE_TMP).write_text("partial")
            (snap_dir_ / SNAR_FILE_TMP).write_text("advanced")
            raise SnapshotCommandError("Dockerでのtar操作に失敗しました: boom", stderr="boom")

    monkeypatch.setattr(SnapshotManager, "_run_docker_tar", fail)
    with caplog.at_level(logging.INFO, logger="devbase"):
        container._auto_snapshot()

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "デプロイは続行します" in warnings[0]
    assert any("差分更新中: g" in r.getMessage() for r in caplog.records)
    assert _snapshot(snap_dir) == before


# ---------------------------------------------------------------------------
# 実の tar と zstd (受け入れ条件 2・5)
# ---------------------------------------------------------------------------

VOLUMES = ("ai", "group")


class FailingTarManager(LocalTarManager):
    """``fail`` に ``"tar"``・``"zstd"`` を入れると、次の控えをその側で失敗させる。

    ``"tar"`` は無いメンバーを足して tar を 2 で止める (zstd は 0)。``"zstd"`` は zstd の代わりに
    パイプを読み切って 1 で終わる (tar は snar を進め終える。実測 R3 の形)。
    """

    fail = None

    def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
        if mode == "backup" and self.fail == "tar":
            command = command.replace(" ai group |", " ai group missing |")
        elif mode == "backup" and self.fail == "zstd":
            command = re.sub(r"zstd -1 -T0 -o \S+", "{ cat >/dev/null; exit 1; }", command)
        self.fail = None
        return super()._run_docker_tar(snap_dir, mode, command, volumes)


def _real_setup(tmp_path: Path) -> tuple:
    source, target = tmp_path / "source", tmp_path / "target"
    for sub in VOLUMES:
        (source / sub).mkdir(parents=True)
        (target / sub).mkdir(parents=True)
    ai = source / "ai"
    (ai / "a").write_text("a1")
    (ai / "b").write_text("b1")
    mgr = FailingTarManager(tmp_path, source, target)
    mgr.create(name="g")
    (ai / "b").write_text("b2 changed")
    mgr.create(name="g")
    return mgr, ai


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


@real_tar
@pytest.mark.parametrize("fail", ["tar", "zstd"])
def test_next_incremental_holds_changes_from_before_the_failure(tmp_path, fail):
    """受け入れ条件 1・2: 失敗の前に足した ``c`` と後に足した ``e`` の両方が次の差分に入る。"""
    mgr, ai = _real_setup(tmp_path)
    snap_dir = _snap_dir(tmp_path)
    (ai / "c").write_text("c1")
    before = _snapshot(snap_dir)

    mgr.fail = fail
    with pytest.raises(SnapshotError):
        mgr.create(name="g")
    assert _snapshot(snap_dir) == before

    (ai / "e").write_text("e1")
    mgr.create(name="g")
    assert _members(snap_dir / incr_archive_name(2)) == ["ai/c", "ai/e"]


@real_tar
def test_restore_after_a_stop_between_renames_has_the_latest_tree(tmp_path, monkeypatch):
    """受け入れ条件 5: (b) の世代へ次の差分を積んで復元すると、最後の差分の時点になる。"""
    mgr, ai = _real_setup(tmp_path)
    snap_dir = _snap_dir(tmp_path)
    (ai / "f").write_text("f1")
    (ai / "a").unlink()
    with monkeypatch.context() as m:
        _fail_replace_to(m, SNAR_FILE)
        with pytest.raises(SnapshotError):
            mgr.create(name="g")
    assert _members(snap_dir / incr_archive_name(2)) == ["ai/f"]

    (ai / "h").write_text("h1")
    (ai / "f").write_text("f2 rewritten")
    mgr.create(name="g")
    assert _members(snap_dir / incr_archive_name(3)) == ["ai/f", "ai/h"]

    mgr.restore("g")
    assert _tree(tmp_path / "target") == _tree(tmp_path / "source")
