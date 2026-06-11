"""PLAN31_2 PR1: tui.dispatch (ハンドラ委譲層) のテスト。"""

from __future__ import annotations

import os
from pathlib import Path

from devbase.tui import dispatch


def test_dispatch_lifecycle_builds_namespace_and_calls_cmd_project(monkeypatch):
    """dispatch_lifecycle は subcommand/name/attrs を載せた Namespace で cmd_project を呼ぶ。"""
    from devbase.commands import container as container_mod

    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(
                            subcommand=args.subcommand, name=args.name,
                            scale=getattr(args, "scale", "MISSING")) or 0)

    rc = dispatch.dispatch_lifecycle("up", "carmo", scale=None)
    assert rc == 0
    assert captured == {"subcommand": "up", "name": "carmo", "scale": None}


def test_dispatch_lifecycle_name_optional(monkeypatch):
    """name 省略時は None が載る (container 経路相当)。"""
    from devbase.commands import container as container_mod

    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(name=args.name) or 0)

    dispatch.dispatch_lifecycle("rebuild")
    assert captured == {"name": None}


def test_dispatch_lifecycle_restores_cwd_and_env(monkeypatch, tmp_path):
    """ハンドラが chdir / 環境変数変更したまま戻っても呼び出し前の状態へ復元する。

    PR #55 round1 major 回帰テスト: TUI は同一プロセスで継続するため、
    `_resolve_project_name` 相当の chdir / env 反映 / COMPOSE_PROJECT_NAME 上書きが
    トップメニュー復帰後の操作 (env get 等) へ残留してはならない。
    """
    from devbase.commands import container as container_mod

    other = tmp_path / "projects" / "carmo"
    other.mkdir(parents=True)

    def mutating_handler(args):
        os.chdir(other)                                  # chdir 残留を模擬
        os.environ["COMPOSE_PROJECT_NAME"] = "carmo"     # 上書き残留を模擬
        os.environ["DEV_SERVICE_NAME"] = "leaked"        # 新規キー残留を模擬
        os.environ.pop("DEVBASE_TEST_KEEP", None)        # 既存キー削除を模擬
        return 0

    monkeypatch.setattr(container_mod, "cmd_project", mutating_handler)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEVBASE_TEST_KEEP", "orig")
    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "before")

    rc = dispatch.dispatch_lifecycle("up", "carmo", scale=None)

    assert rc == 0
    assert Path.cwd() == tmp_path                        # CWD 復元
    assert os.environ["COMPOSE_PROJECT_NAME"] == "before"  # 上書き復元
    assert "DEV_SERVICE_NAME" not in os.environ          # 漏えいキー除去
    assert os.environ["DEVBASE_TEST_KEEP"] == "orig"     # 削除キー復元


def test_dispatch_lifecycle_restores_state_on_exception(monkeypatch, tmp_path):
    """ハンドラが例外を投げても CWD / 環境変数は復元される (try/finally 保証)。"""
    import pytest

    from devbase.commands import container as container_mod

    def raising_handler(args):
        os.chdir(tmp_path)
        os.environ["DEV_SERVICE_NAME"] = "leaked"
        raise RuntimeError("boom")

    monkeypatch.setattr(container_mod, "cmd_project", raising_handler)
    old_cwd = Path.cwd()
    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)

    with pytest.raises(RuntimeError):
        dispatch.dispatch_lifecycle("up", "carmo", scale=None)

    assert Path.cwd() == old_cwd
    assert "DEV_SERVICE_NAME" not in os.environ


def test_dispatch_group_restores_cwd_and_env(tmp_path, monkeypatch):
    """dispatch_group も lifecycle と同じ復元境界を張る (契約整合)。"""

    def mutating_handler(devbase_root, args):
        os.chdir(tmp_path)
        os.environ["DEV_SERVICE_NAME"] = "leaked"
        return 0

    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)
    old_cwd = Path.cwd()

    rc = dispatch.dispatch_group(mutating_handler, Path("/devbase"), "init")

    assert rc == 0
    assert Path.cwd() == old_cwd
    assert "DEV_SERVICE_NAME" not in os.environ


def test_dispatch_group_builds_namespace_and_calls_handler():
    """dispatch_group は (devbase_root, args) 形式のハンドラへ委譲する。"""
    captured = {}

    def handler(devbase_root, args):
        captured["root"] = devbase_root
        captured["subcommand"] = args.subcommand
        captured["reset"] = args.reset
        return 7

    rc = dispatch.dispatch_group(handler, Path("/devbase"), "init", reset=True)
    assert rc == 7
    assert captured == {"root": Path("/devbase"), "subcommand": "init", "reset": True}
