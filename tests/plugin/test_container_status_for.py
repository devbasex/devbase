"""Regression tests for `_container_status_for` の compose project scope。

`bin/devbase` は常に `COMPOSE_PROJECT_NAME` を export する (cwd basename ないし
解決済みプロジェクト名)。`devbase list` の python プロセスはこれを継承するため、
`docker compose ps` を明示的に `--project-name <entry.name>` で scope しないと、
docker compose が継承 env を優先して全プロジェクトで「同じ (カレント) プロジェクト」
の状態を返してしまう (全項目が同一の running / コンテナ数になる回帰)。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from devbase.commands.status import _container_status_for


def _make_entry(tmp_path: Path, name: str) -> Path:
    entry = tmp_path / "projects" / name
    entry.mkdir(parents=True)
    (entry / "compose.yml").write_text("services:\n  dev:\n    image: busybox\n")
    return entry


def test_scopes_query_to_entry_name(tmp_path, monkeypatch):
    """継承 COMPOSE_PROJECT_NAME に左右されず entry.name で scope する。"""
    entry = _make_entry(tmp_path, "myproj")

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class R:
            returncode = 0
            stdout = '{"State":"running"}\n{"State":"running"}\n'

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    # 別プロジェクトに居る状態 (wrapper が export 済み) を再現
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "some-other-project")

    result = _container_status_for(entry)

    assert result == {"name": "myproj", "status": "running (2 containers)", "count": 2}

    cmd = captured["cmd"]
    assert "--project-name" in cmd, f"--project-name で scope していない: {cmd}"
    idx = cmd.index("--project-name")
    assert cmd[idx + 1] == "myproj", f"entry.name で scope していない: {cmd}"
    # global flag は subcommand より前に置くこと
    assert cmd.index("--project-name") < cmd.index("ps")


def test_distinct_projects_get_distinct_scope(tmp_path, monkeypatch):
    """異なる entry は異なる project 名でクエリされる (一律化しない)。"""
    a = _make_entry(tmp_path, "proj-a")
    b = _make_entry(tmp_path, "proj-b")

    seen: list[str] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd[cmd.index("--project-name") + 1])

        class R:
            returncode = 0
            stdout = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "current")

    _container_status_for(a)
    _container_status_for(b)

    assert seen == ["proj-a", "proj-b"]
