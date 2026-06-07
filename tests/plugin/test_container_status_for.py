"""Regression tests for コンテナ状態取得 (status.py)。

`devbase list` / `status` の状態取得は当初プロジェクト数ぶん `docker compose ps`
をサブプロセス起動しており、(1) `bin/devbase` が常に export する
`COMPOSE_PROJECT_NAME` を継承して全プロジェクトが同一状態になる回帰、
(2) N サブプロセス起動の重さ、の二点があった。

現在は `_running_counts_by_project()` が単一の `docker ps` で全 running コンテナ
を `com.docker.compose.project` ラベルごとに集計し、`_container_status_for()` は
その counts マップを参照するだけ。docker ps はラベルで識別するため
`COMPOSE_PROJECT_NAME` の継承に一切影響されない。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from devbase.commands import status as status_mod
from devbase.commands.status import (
    _container_status_for,
    _running_counts_by_project,
)


def _make_entry(tmp_path: Path, name: str) -> Path:
    entry = tmp_path / "projects" / name
    entry.mkdir(parents=True)
    (entry / "compose.yml").write_text("services:\n  dev:\n    image: busybox\n")
    return entry


# --- _running_counts_by_project ------------------------------------------


def test_counts_aggregates_single_docker_ps(monkeypatch):
    """docker ps 1 回でラベルごとの running 数を集計する (N 回起動しない)。"""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class R:
            returncode = 0
            # 9 個のうち carmo-system-console が複数 (複数コンテナ project)
            stdout = "carmo-ai\ncarmo-system-console\ncarmo-system-console\n"

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    # 継承 COMPOSE_PROJECT_NAME があっても集計に影響しないこと
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "some-other-project")

    counts = _running_counts_by_project()

    assert counts == {"carmo-ai": 1, "carmo-system-console": 2}
    # 1 回しか docker を起動していない
    assert len(calls) == 1
    # docker compose ps ではなく docker ps をラベル filter で叩いている
    assert calls[0][:2] == ["docker", "ps"]
    assert any("label=com.docker.compose.project" in a for a in calls[0])


def test_counts_none_when_docker_unavailable(monkeypatch):
    """docker コマンドが無い (OSError) なら None。"""

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _running_counts_by_project() is None


# --- _container_status_for -----------------------------------------------


def test_status_uses_counts_and_ignores_env(tmp_path, monkeypatch):
    """counts マップを参照し、継承 COMPOSE_PROJECT_NAME に影響されない。"""
    entry = _make_entry(tmp_path, "myproj")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "some-other-project")

    result = _container_status_for(entry, {"myproj": 3, "other": 9})

    assert result == {"name": "myproj", "status": "running (3 containers)", "count": 3}


def test_status_stopped_when_not_in_counts(tmp_path):
    """counts に居なければ stopped (count=0)。"""
    entry = _make_entry(tmp_path, "myproj")

    result = _container_status_for(entry, {"other": 1})

    assert result == {"name": "myproj", "status": "stopped", "count": 0}


def test_status_none_without_compose(tmp_path):
    """compose.yml が無ければ対象外 (None)。"""
    entry = tmp_path / "projects" / "noncompose"
    entry.mkdir(parents=True)

    assert _container_status_for(entry, {"noncompose": 1}) is None


def test_status_none_when_counts_none(tmp_path, monkeypatch):
    """docker 不在 (counts=None) を明示的に渡したら None (再集計しない)。"""
    entry = _make_entry(tmp_path, "myproj")

    def fake_run(cmd, **kwargs):  # 呼ばれてはいけない
        raise AssertionError("counts=None なら再集計してはならない")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _container_status_for(entry, None) is None


def test_distinct_projects_distinct_counts(tmp_path):
    """同じ counts から各 entry が自分の名前ぶんだけ拾う。"""
    a = _make_entry(tmp_path, "proj-a")
    b = _make_entry(tmp_path, "proj-b")
    counts = {"proj-a": 2, "proj-b": 5}

    assert _container_status_for(a, counts)["count"] == 2
    assert _container_status_for(b, counts)["count"] == 5


def test_get_container_status_runs_docker_ps_once(tmp_path, monkeypatch):
    """_get_container_status は docker ps を 1 回だけ実行し全 entry に使い回す。"""
    projects_dir = tmp_path / "projects"
    for name in ("a", "b", "c"):
        d = projects_dir / name
        d.mkdir(parents=True)
        (d / "compose.yml").write_text("services:\n  dev:\n    image: busybox\n")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = "a\nb\nb\n"

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)

    results = status_mod._get_container_status(projects_dir)
    by_name = {r["name"]: r for r in results}

    assert by_name["a"]["status"] == "running (1 containers)"
    assert by_name["b"]["status"] == "running (2 containers)"
    assert by_name["c"]["status"] == "stopped"
    # entry が 3 つでも docker 起動は 1 回
    assert len(calls) == 1
