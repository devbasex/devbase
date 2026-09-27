"""``devbase status`` のアカウントグループ表示 (PLAN39 Task 7 / AC10・#315)

グループは実行時のプロジェクトの宣言 (``projects/<name>/env``) から出す。プロジェクトの外では
グループが決まらず、既定の値 (``default``) を出さない。
"""

from __future__ import annotations

import pytest

from devbase.commands import status


@pytest.fixture
def root(tmp_path, monkeypatch):
    # プロセスの環境変数は見ない。見ていれば下のテストの期待値が崩れるよう、別の値を置く
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    (tmp_path / "projects" / "web").mkdir(parents=True)
    monkeypatch.setenv("PWD", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _enter(root, monkeypatch, name="web"):
    project = root / "projects" / name
    monkeypatch.setenv("PWD", str(project))
    monkeypatch.chdir(project)
    return project


def test_outside_a_project_the_group_is_not_reported_as_default(root):
    info = status._get_account_group(root)

    assert info["group"] is None
    assert info["error"] is None
    assert "プロジェクトの外" in info["note"]


def test_declared_group_is_reported_with_its_source(root, monkeypatch):
    project = _enter(root, monkeypatch)
    (project / "env").write_text("FOO=1\nDEVBASE_ACCOUNT_GROUP=with\n")

    info = status._get_account_group(root)

    assert info["group"] == "with"
    assert info["volume"] == "devbase_home_with"
    assert info["source"] == "projects/web/env:2"


def test_undeclared_project_is_reported_without_raising(root, monkeypatch):
    """設定の誤りで status 全体を出せなくしない。"""
    _enter(root, monkeypatch)

    info = status._get_account_group(root)

    assert info["group"] is None
    assert "宣言なし" in info["error"]
    assert "projects/web/env" in info["error"]


def test_status_prints_the_account_group(root, capsys, monkeypatch):
    project = _enter(root, monkeypatch)
    (project / "env").write_text("DEVBASE_ACCOUNT_GROUP=with\n")

    status.cmd_status(root)

    out = capsys.readouterr().out
    assert "アカウントグループ" in out
    assert "with" in out
    assert "devbase_home_with" in out


def test_status_outside_a_project_does_not_print_default(root, capsys):
    status.cmd_status(root)

    out = capsys.readouterr().out
    assert "なし（プロジェクトの外）" in out
    assert "default" not in out


def test_status_prints_the_error_for_an_invalid_group(root, capsys, monkeypatch):
    project = _enter(root, monkeypatch)
    (project / "env").write_text("DEVBASE_ACCOUNT_GROUP=1\n")

    status.cmd_status(root)

    out = capsys.readouterr().out
    assert "設定エラー" in out
