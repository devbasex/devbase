"""``devbase up`` の自動スナップショットを系列で扱う (PLAN68)

``DEVBASE_ROOT`` を ``tmp_path`` に向け、``SnapshotManager._run_docker_tar`` を
差し替える。実データの ``backups/`` と Docker に触らない。
"""

from __future__ import annotations

import logging
import os
import re
import time

import pytest
import yaml

from devbase.commands import container
from devbase.snapshot.manager import SnapshotManager

from .test_manager_series import names, write_state


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVBASE_ROOT", str(tmp_path))
    monkeypatch.setenv("DEVBASE_SNAPSHOT_MIN_INTERVAL_MINUTES", "0")
    monkeypatch.delenv("DEVBASE_ACCOUNT_GROUP", raising=False)
    def fake(self, snap_dir, mode, command, volumes=None):
        if mode == "backup":
            archive = re.search(r"/backup/(full\.tar\.zst|incr-\d+\.tar\.zst)", command)
            (snap_dir / archive.group(1)).write_text("archive")
            (snap_dir / "snapshot.snar").write_text("snar")

    monkeypatch.setattr(SnapshotManager, "_run_docker_tar", fake)
    return tmp_path


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_returning_group_appends_to_its_generation(root, caplog):
    """1・16: default → with → default で、default の世代へ incr を積む。"""
    backups = write_state(root, [("A", "default", 0), ("B", "with", 0)])

    with caplog.at_level(logging.INFO, logger="devbase"):
        container._auto_snapshot()

    assert (backups / "A" / "incr-001.tar.zst").exists()
    assert not (backups / "B" / "incr-001.tar.zst").exists()
    assert names(root) == ["A", "B"]
    messages = [r.getMessage() for r in caplog.records]
    assert not any("構成が変わった" in m for m in messages)
    assert any("差分更新中: A (グループ default)" in m for m in messages)


def test_group_without_generation_creates_new(root, monkeypatch, caplog):
    """2・16: with の世代が無ければ full の世代を作り、理由を出す。"""
    write_state(root, [("A", "default", 0)])
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "with")

    with caplog.at_level(logging.INFO, logger="devbase"):
        container._auto_snapshot()

    entries = yaml.safe_load((root / "backups" / "snapshot.yml").read_text())["snapshots"]
    assert len(entries) == 2
    new = entries[-1]
    assert new["volumes"]["group"] == "devbase_home_with"
    assert (root / "backups" / new["name"] / "full.tar.zst").exists()
    messages = [r.getMessage() for r in caplog.records]
    assert any("グループ with の世代がまだ無いため" in m for m in messages)
    assert any("新しいスナップショット世代を作成中 (グループ with)" in m for m in messages)


def test_min_interval_is_per_series(root, monkeypatch, caplog):
    """6: default は 10 分前なら飛ばし、with は 2 時間前なので積む。"""
    backups = write_state(root, [("W", "with", 0), ("D", "default", 0)])
    _age(backups / "D" / "full.tar.zst", 600)
    _age(backups / "W" / "full.tar.zst", 7200)
    monkeypatch.setenv("DEVBASE_SNAPSHOT_MIN_INTERVAL_MINUTES", "60")

    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "with")
    container._auto_snapshot()
    assert (backups / "W" / "incr-001.tar.zst").exists()

    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "default")
    with caplog.at_level(logging.INFO, logger="devbase"):
        container._auto_snapshot()
    assert not (backups / "D" / "incr-001.tar.zst").exists()
    assert any("グループ default の直近のスナップショット" in r.getMessage()
               and "スキップします" in r.getMessage() for r in caplog.records)


def test_zero_interval_never_skips(root):
    """7: 間隔 0 なら 10 分前の系列でも積む。"""
    backups = write_state(root, [("D", "default", 0)])
    _age(backups / "D" / "full.tar.zst", 600)

    container._auto_snapshot()
    assert (backups / "D" / "incr-001.tar.zst").exists()


def test_new_generation_rotates_only_its_own_series(root, monkeypatch):
    """現状固定: 作成の後の rotate() で、新世代を積んだ系列の最古だけが消える。

    ``_auto_snapshot`` は「新世代の作成 → 既定の rotate()」を続けて呼ぶ。既定の
    rotate() は系列ごとに ``max_generations`` (ここでは 3) 世代を残すため、default
    系列を 3 世代 (最新の差分数 10 で上限) と with 系列を 1 世代の状態から default
    で呼ぶと、default は新世代が積まれて 4 世代 → 最古が 1 つ落ちて 3 世代に戻り、
    with の 1 世代はそのまま残る。作成とローテーションのつなぎ目を固定する。
    """
    backups = write_state(root, [
        ("D1", "default", 0), ("D2", "default", 0), ("D3", "default", 10),
        ("W1", "with", 0)])
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "default")

    container._auto_snapshot()

    entries = yaml.safe_load((backups / "snapshot.yml").read_text())["snapshots"]
    # default 系列は 3 世代 (最古の D1 が落ち、新世代が 1 つ増えた)。
    default_series = [e["name"] for e in entries
                      if e.get("volumes", {}).get("group") == "devbase_home_default"]
    with_series = [e["name"] for e in entries
                   if e.get("volumes", {}).get("group") == "devbase_home_with"]
    assert len(default_series) == 3
    assert "D1" not in default_series
    assert {"D2", "D3"} <= set(default_series)
    new_names = set(default_series) - {"D2", "D3"}
    assert len(new_names) == 1  # 新しく積まれた 1 世代
    assert with_series == ["W1"]  # with の 1 世代はそのまま残る

    # 消えた最古 (D1) のディレクトリは無く、with (W1) のディレクトリは残る。
    assert not (backups / "D1").exists()
    assert (backups / "W1" / "full.tar.zst").exists()
    new_name = next(iter(new_names))
    assert (backups / new_name / "full.tar.zst").exists()


def test_invalid_group_warns_without_creating_metadata(root, monkeypatch, caplog):
    """現状固定: ボリューム解決の失敗は警告に落とし、呼び出し元へ戻る。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "invalid/group")

    with caplog.at_level(logging.WARNING, logger="devbase"):
        container._auto_snapshot()

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert not (root / "backups" / "snapshot.yml").exists()
