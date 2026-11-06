"""作成フローのテストの fixture (#514)。"""

from __future__ import annotations

import builtins
import os
import sys

import pytest

from devbase import cli

from .creation_harness import FakeGitHub


@pytest.fixture
def github(tmp_path, monkeypatch):
    """偽の gh を PATH の先頭に置き、git の URL を手元の bare リポジトリへ向ける。"""
    world = FakeGitHub(tmp_path / "world")
    monkeypatch.setenv("PATH", f"{world.bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(world.gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return world


@pytest.fixture
def root(tmp_path, monkeypatch):
    """一時の devbase のルート。"""
    path = tmp_path / "root"
    (path / "projects").mkdir(parents=True)
    monkeypatch.setenv("DEVBASE_ROOT", str(path))
    return path


@pytest.fixture
def run_cli(root, github, monkeypatch):
    """devbase の CLI を同じプロセスで打つ。``tty`` と ``answers`` で端末を再現する。"""
    def _run(*argv, tty=False, answers=()):
        pending = list(answers)

        def _input(_prompt=""):
            assert tty, "TTY が無いのに入力を求めた"
            return pending.pop(0)

        monkeypatch.setattr(sys, "argv", ["devbase", *argv])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: tty, raising=False)
        monkeypatch.setattr(builtins, "input", _input)
        return cli.main()
    return _run
