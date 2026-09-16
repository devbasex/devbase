"""PLAN58: 生成物 (.docker-compose.scale.yml) が profiles と depends_on.required を保つ

プロファイルの判定は Compose に任せる (決定 1)。そのため生成物が元の compose.yml の
``profiles:`` を落とすと、``devbase up`` がプロファイルのサービスまで起動してしまう。
``depends_on`` の ``required: false`` は、既定の ``up`` でプロファイルのサービスが
未定義になっても構成の検証を通すために要る。
"""

from __future__ import annotations

import yaml
import pytest

from devbase.volume import compose


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)
    return tmp_path


def generate(tmp_path, services: dict, scale: int = 1) -> dict:
    (tmp_path / "compose.yml").write_text(
        yaml.safe_dump({"services": services}, sort_keys=False), encoding="utf-8")
    compose.generate_scaled_compose(scale=scale)
    return yaml.safe_load((tmp_path / ".docker-compose.scale.yml").read_text())["services"]


def test_non_dev_services_keep_profiles(in_tmp_cwd):
    scaled = generate(in_tmp_cwd, {
        "dev": {"image": "dev:latest"},
        "app": {"image": "app:latest", "profiles": ["test"]},
        "mysql": {"image": "mysql:8", "profiles": ["${TEST_PROFILE:-test}", "db"]},
        "redis": {"image": "redis:7"},
    }, scale=2)

    assert scaled["app"]["profiles"] == ["test"]
    assert scaled["mysql"]["profiles"] == ["${TEST_PROFILE:-test}", "db"]
    assert "profiles" not in scaled["redis"]
    assert "profiles" not in scaled["dev-1"] and "profiles" not in scaled["dev-2"]


def test_depends_on_dev_keeps_condition_and_required_per_instance(in_tmp_cwd):
    scaled = generate(in_tmp_cwd, {
        "dev": {"image": "dev:latest"},
        "app": {
            "image": "app:latest",
            "profiles": ["test"],
            "depends_on": {"dev": {"condition": "service_started", "required": False}},
        },
    }, scale=2)

    assert scaled["app"]["depends_on"] == {
        "dev-1": {"condition": "service_started", "required": False},
        "dev-2": {"condition": "service_started", "required": False},
    }


def test_dev_depends_on_profile_service_keeps_required(in_tmp_cwd):
    scaled = generate(in_tmp_cwd, {
        "dev": {
            "image": "dev:latest",
            "depends_on": {"db": {"condition": "service_started", "required": False}},
        },
        "db": {"image": "mysql:8", "profiles": ["test"]},
    })

    assert scaled["dev-1"]["depends_on"] == {
        "db": {"condition": "service_started", "required": False}}
