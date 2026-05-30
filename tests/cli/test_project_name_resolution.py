"""PLAN06 Task 2: プロジェクト名解決 (wrapper cd + Python フォールバック) のテスト。

検証対象:
  - Python `container._resolve_project_name`: $DEVBASE_ROOT/projects/<name> への
    chdir + COMPOSE_PROJECT_NAME 上書き、存在しない name のエラー + 候補提示。
  - wrapper (bin/devbase): project/container サブコマンド及びトップレベルシノニムで
    実在するプロジェクト名のみ cd + argv strip し、login <index> / build <image> /
    scale <N> の既存 positional と曖昧にならないこと (存在性ベースの判定)。

wrapper テストは実際の `uv run` を避けるため run_python / cmd_build をスタブに
差し替え、DEVBASE_ROOT を一時ディレクトリへ向けた薄いハーネスで dispatch のみ
実行する (wrapper 末尾の DEVBASE_ROOT 自動解決行も sed で除去する)。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from devbase.commands import container

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "bin" / "devbase"


# ===========================================================================
# Python: _resolve_project_name
# ===========================================================================

@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """projects/carmo を持つ一時 DEVBASE_ROOT を用意し、CWD/環境を復元する。"""
    (tmp_path / "projects" / "carmo").mkdir(parents=True)
    (tmp_path / "projects" / "shop").mkdir(parents=True)
    monkeypatch.setenv("DEVBASE_ROOT", str(tmp_path))
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    origin = Path.cwd()
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    os.chdir(origin)


def test_resolve_chdirs_into_project(fake_root):
    assert container._resolve_project_name("carmo") is True
    assert Path.cwd().resolve() == (fake_root / "projects" / "carmo").resolve()
    assert os.environ["COMPOSE_PROJECT_NAME"] == "carmo"


def test_resolve_unknown_name_errors_with_candidates(fake_root, caplog):
    with caplog.at_level(logging.ERROR, logger="devbase.commands.container"):
        assert container._resolve_project_name("nope") is False
    messages = " ".join(r.message for r in caplog.records)
    assert "nope" in messages
    # 候補一覧に既存プロジェクトが提示される
    assert "carmo" in messages and "shop" in messages


def test_resolve_without_devbase_root(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("DEVBASE_ROOT", raising=False)
    with caplog.at_level(logging.ERROR, logger="devbase.commands.container"):
        assert container._resolve_project_name("carmo") is False
    assert any("DEVBASE_ROOT" in r.message for r in caplog.records)


def test_resolve_noop_when_already_in_target(fake_root, monkeypatch):
    """wrapper が既に cd 済みなら chdir を呼ばない (冪等)。"""
    target = fake_root / "projects" / "carmo"
    monkeypatch.chdir(target)

    called = []
    monkeypatch.setattr(container.os, "chdir", lambda p: called.append(p))
    assert container._resolve_project_name("carmo") is True
    assert called == [], "既に対象ディレクトリにいる場合 chdir は呼ばれない"
    assert os.environ["COMPOSE_PROJECT_NAME"] == "carmo"


# ===========================================================================
# wrapper: cd + argv strip + 存在性ベースの曖昧性回避
# ===========================================================================

def _run_wrapper(args, devbase_root):
    """run_python / cmd_build をスタブ化し wrapper の dispatch だけを実行する。

    - run_python  -> "PWD:<cwd>" と "PYTHON:<args>" を出力
    - cmd_build   -> "PWD:<cwd>" と "BUILD:<args>" を出力
    実際の wrapper が DEVBASE_ROOT を自身のパスから再計算してしまうため、その
    代入行 (`DEVBASE_ROOT=...`) も sed で除去し、環境変数で渡した値を使わせる。
    """
    harness = (
        'run_python() { echo "PWD:$PWD"; echo "PYTHON:$*"; exit 0; }\n'
        'cmd_build() { echo "PWD:$PWD"; echo "BUILD:$*"; exit 0; }\n'
        'ensure_uv() { :; }\n'
        'eval "$(sed -e \'/^run_python()/,/^}/d\' '
        '            -e \'/^ensure_uv()/,/^}/d\' '
        '            -e \'/^cmd_build()/,/^}/d\' '
        '            -e \'/^DEVBASE_ROOT=/d\' "$WRAPPER_PATH")"\n'
    )
    env = {
        **os.environ,
        "DEVBASE_ROOT": str(devbase_root),
        "WRAPPER_PATH": str(WRAPPER),
    }
    return subprocess.run(
        ["bash", "-c", harness, "devbase", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


@pytest.fixture
def wrapper_root(tmp_path):
    (tmp_path / "projects" / "carmo").mkdir(parents=True)
    return tmp_path


def _pwd(result):
    for line in result.stdout.splitlines():
        if line.startswith("PWD:"):
            return line[len("PWD:"):]
    return None


def _python_args(result):
    for line in result.stdout.splitlines():
        if line.startswith("PYTHON:"):
            return line[len("PYTHON:"):]
    return None


def _build_args(result):
    for line in result.stdout.splitlines():
        if line.startswith("BUILD:"):
            return line[len("BUILD:"):]
    return None


def test_wrapper_project_up_name_cds_and_strips(wrapper_root):
    r = _run_wrapper(["project", "up", "carmo"], wrapper_root)
    assert "unknown command" not in r.stderr.lower(), r.stderr
    assert _pwd(r).endswith("/projects/carmo"), r.stdout
    # name は strip され Python へは渡らない
    assert _python_args(r) == "project up", r.stdout


def test_wrapper_shortcut_up_name_cds_and_strips(wrapper_root):
    r = _run_wrapper(["up", "carmo"], wrapper_root)
    assert _pwd(r).endswith("/projects/carmo"), r.stdout
    assert _python_args(r) == "up", r.stdout


def test_wrapper_unknown_name_not_stripped_no_cd(wrapper_root):
    """存在しない name は cd せず素通し (Python 側でエラー処理させる)。"""
    r = _run_wrapper(["up", "bogus"], wrapper_root)
    assert not _pwd(r).endswith("/projects/bogus"), r.stdout
    assert _python_args(r) == "up bogus", r.stdout


def test_wrapper_build_name_cds_via_shell(wrapper_root):
    """build は shell cmd_build 経路。wrapper cd で対象プロジェクトへ移動する。"""
    r = _run_wrapper(["build", "carmo"], wrapper_root)
    assert _pwd(r).endswith("/projects/carmo"), r.stdout
    assert _build_args(r) == "", r.stdout  # name は strip


def test_wrapper_build_flag_not_treated_as_name(wrapper_root):
    """`build --no-cache` のフラグは name とみなさず CWD でビルド。"""
    r = _run_wrapper(["build", "--no-cache"], wrapper_root)
    assert not _pwd(r).endswith("/projects/"), r.stdout
    assert _build_args(r) == "--no-cache", r.stdout


def test_wrapper_scale_name_disambiguation(wrapper_root):
    """`scale carmo 3` は name+N、`scale 3` は N のみ (存在性で判定)。"""
    r1 = _run_wrapper(["scale", "carmo", "3"], wrapper_root)
    assert _pwd(r1).endswith("/projects/carmo"), r1.stdout
    assert _python_args(r1) == "scale 3", r1.stdout

    r2 = _run_wrapper(["scale", "3"], wrapper_root)
    assert not _pwd(r2).endswith("/projects/3"), r2.stdout
    assert _python_args(r2) == "scale 3", r2.stdout


def test_wrapper_login_index_not_treated_as_name(wrapper_root):
    """`login 2` の 2 は index。projects/2 が無いので cd せず素通し。"""
    r = _run_wrapper(["login", "2"], wrapper_root)
    assert _python_args(r) == "login 2", r.stdout

    # 一方 `login carmo` は実在プロジェクトなので cd + strip (index=1 既定)
    r2 = _run_wrapper(["login", "carmo"], wrapper_root)
    assert _pwd(r2).endswith("/projects/carmo"), r2.stdout
    assert _python_args(r2) == "login", r2.stdout


def test_wrapper_project_scale_name_strips_keeps_subcommand(wrapper_root):
    r = _run_wrapper(["project", "scale", "carmo", "3"], wrapper_root)
    assert _pwd(r).endswith("/projects/carmo"), r.stdout
    assert _python_args(r) == "project scale 3", r.stdout


def test_wrapper_no_name_uses_cwd(wrapper_root):
    """name を渡さなければ cd せず従来通り (引数素通し)。"""
    r = _run_wrapper(["project", "up"], wrapper_root)
    assert _python_args(r) == "project up", r.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
