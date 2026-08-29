"""``devbase status`` のアカウントグループ表示 (PLAN39 Task 7 / AC10)"""

from __future__ import annotations

import pytest

from devbase.commands import status


@pytest.fixture(autouse=True)
def _clean_group_env(monkeypatch):
    monkeypatch.delenv("DEVBASE_ACCOUNT_GROUP", raising=False)


def test_default_group_is_reported_as_a_fallback():
    info = status._get_account_group()

    assert info["group"] == "default"
    assert info["volume"] == "devbase_home_default"
    assert info["source"] == "既定"
    assert info["error"] is None


def test_declared_group_is_reported_as_env(monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")

    info = status._get_account_group()

    assert info["group"] == "kkg"
    assert info["volume"] == "devbase_home_kkg"
    assert info["source"] == "env"


def test_invalid_group_is_reported_without_raising(monkeypatch):
    """設定の誤りで status 全体を出せなくしない。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "ubuntu")

    info = status._get_account_group()

    assert info["group"] is None
    assert "ubuntu" in info["error"]


def test_status_prints_the_account_group(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")

    status.cmd_status(tmp_path)

    out = capsys.readouterr().out
    assert "アカウントグループ" in out
    assert "kkg" in out
    assert "devbase_home_kkg" in out


def test_status_prints_the_error_for_an_invalid_group(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "1")

    status.cmd_status(tmp_path)

    out = capsys.readouterr().out
    assert "設定エラー" in out
