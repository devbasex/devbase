"""``VolumeManager.ensure_volumes`` の現状固定テスト

共通のホーム・グループ・VS Code Server・作業ボリュームを「あれば使い、無ければ作る」
振る舞いを、作る順・確かめる順・出す文言・作れなかったときの例外まで固定する。
Docker は ``_volume_exists`` と ``create_volume`` を置き換えて触らない。
"""

from __future__ import annotations

import logging

import pytest

from devbase.errors import DevbaseError, DockerError
from devbase.volume import manager


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "acme")


def _fake_docker(monkeypatch, existing=frozenset(), failing=frozenset()):
    """確かめた名前と作った名前を呼ばれた順に記録する。"""
    calls: list[tuple[str, str]] = []
    created: set[str] = set()

    def volume_exists(self, name):
        calls.append(("exists", name))
        return name in existing or name in created

    def create_volume(self, name):
        calls.append(("create", name))
        if name in failing:
            return False
        created.add(name)
        return True

    monkeypatch.setattr(manager.VolumeManager, "_volume_exists", volume_exists)
    monkeypatch.setattr(manager.VolumeManager, "create_volume", create_volume)
    return calls


def _messages(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == manager.logger.name]


def test_creates_all_volumes_in_order_when_none_exist(monkeypatch, caplog):
    calls = _fake_docker(monkeypatch)

    with caplog.at_level(logging.INFO, logger=manager.logger.name):
        manager.VolumeManager().ensure_volumes(2)

    assert calls == [
        ("exists", "devbase_home_ubuntu"),
        ("create", "devbase_home_ubuntu"),
        ("exists", "devbase_home_acme"),
        ("create", "devbase_home_acme"),
        ("exists", "devbase_vscode-server-bin"),
        ("create", "devbase_vscode-server-bin"),
        ("exists", "devbase_work_1"),
        ("create", "devbase_work_1"),
        ("exists", "devbase_work_2"),
        ("create", "devbase_work_2"),
    ]
    assert _messages(caplog) == [
        "Ensuring volumes for 2 container(s)",
        "  Creating devbase_home_ubuntu (shared home)...",
        "  Creating devbase_home_acme (account group)...",
        "  Creating devbase_vscode-server-bin (VS Code Server)...",
        "  Creating devbase_work_1...",
        "  Creating devbase_work_2...",
    ]


def test_existing_volumes_are_only_checked(monkeypatch, caplog):
    existing = {"devbase_home_ubuntu", "devbase_home_team-b",
                "devbase_vscode-server-bin", "devbase_work_1"}
    calls = _fake_docker(monkeypatch, existing=existing)

    with caplog.at_level(logging.INFO, logger=manager.logger.name):
        manager.VolumeManager().ensure_volumes(1, group="team-b")

    assert calls == [
        ("exists", "devbase_home_ubuntu"),
        ("exists", "devbase_home_team-b"),
        ("exists", "devbase_vscode-server-bin"),
        ("exists", "devbase_work_1"),
    ]
    assert _messages(caplog) == [
        "Ensuring volumes for 1 container(s)",
        "  devbase_home_ubuntu (shared home, exists)",
        "  devbase_home_team-b (account group, exists)",
        "  devbase_vscode-server-bin (VS Code Server, exists)",
        "  devbase_work_1 (exists)",
    ]


def test_mixed_existing_and_missing(monkeypatch, caplog):
    existing = {"devbase_home_acme", "devbase_work_2"}
    calls = _fake_docker(monkeypatch, existing=existing)

    with caplog.at_level(logging.INFO, logger=manager.logger.name):
        manager.VolumeManager().ensure_volumes(3)

    assert [name for kind, name in calls if kind == "create"] == [
        "devbase_home_ubuntu",
        "devbase_vscode-server-bin",
        "devbase_work_1",
        "devbase_work_3",
    ]
    assert _messages(caplog) == [
        "Ensuring volumes for 3 container(s)",
        "  Creating devbase_home_ubuntu (shared home)...",
        "  devbase_home_acme (account group, exists)",
        "  Creating devbase_vscode-server-bin (VS Code Server)...",
        "  Creating devbase_work_1...",
        "  devbase_work_2 (exists)",
        "  Creating devbase_work_3...",
    ]


def test_scale_zero_creates_only_shared_volumes(monkeypatch):
    calls = _fake_docker(monkeypatch)

    manager.VolumeManager().ensure_volumes(0)

    assert [name for kind, name in calls if kind == "create"] == [
        "devbase_home_ubuntu",
        "devbase_home_acme",
        "devbase_vscode-server-bin",
    ]


@pytest.mark.parametrize("failing, created_before", [
    ("devbase_home_ubuntu", []),
    ("devbase_home_acme", ["devbase_home_ubuntu"]),
    ("devbase_vscode-server-bin", ["devbase_home_ubuntu", "devbase_home_acme"]),
    ("devbase_work_1", ["devbase_home_ubuntu", "devbase_home_acme",
                        "devbase_vscode-server-bin"]),
    ("devbase_work_2", ["devbase_home_ubuntu", "devbase_home_acme",
                        "devbase_vscode-server-bin", "devbase_work_1"]),
])
def test_failed_creation_raises_and_stops(monkeypatch, failing, created_before):
    calls = _fake_docker(monkeypatch, failing={failing})

    with pytest.raises(DockerError) as excinfo:
        manager.VolumeManager().ensure_volumes(3)

    assert str(excinfo.value) == f"Failed to create volume {failing}"
    assert calls[-1] == ("create", failing)
    assert [name for kind, name in calls[:-1] if kind == "create"] == created_before


def test_invalid_group_fails_before_touching_docker(monkeypatch):
    calls = _fake_docker(monkeypatch)

    with pytest.raises(DevbaseError):
        manager.VolumeManager().ensure_volumes(1, group="bad/name")

    assert calls == []


def test_module_function_delegates_with_group(monkeypatch):
    calls = _fake_docker(monkeypatch)

    manager.ensure_volumes(1, group="team-b")

    assert [name for kind, name in calls if kind == "create"] == [
        "devbase_home_ubuntu",
        "devbase_home_team-b",
        "devbase_vscode-server-bin",
        "devbase_work_1",
    ]
