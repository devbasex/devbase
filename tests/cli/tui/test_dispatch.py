"""PLAN31_2 PR1: tui.dispatch (ハンドラ委譲層) のテスト。"""

from __future__ import annotations

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
