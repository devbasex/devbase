"""dev サービスへの追加環境変数の注入 (PLAN32: clone プランの受け渡し)"""

from __future__ import annotations

import pytest
import yaml

from devbase.volume.compose import generate_scaled_compose


COMPOSE_DICT_ENV = """services:
  dev:
    image: alpine
    environment:
      FEATURE_FLAG: enabled
    volumes:
      - x:/work
  db:
    image: mysql
volumes:
  x: {}
"""

COMPOSE_LIST_ENV = """services:
  dev:
    image: alpine
    environment:
      - FEATURE_FLAG=enabled
    volumes:
      - x:/work
volumes:
  x: {}
"""

COMPOSE_NO_ENV = """services:
  dev:
    image: alpine
    volumes:
      - x:/work
volumes:
  x: {}
"""

COMPOSE_UNSUPPORTED_ENV = """services:
  dev:
    image: alpine
    environment: value
    volumes:
      - x:/work
volumes:
  x: {}
"""

REPO_ENV = {"DEVBASE_REPOS": "cGxhbg==", "DEVBASE_PRIMARY_DIR": "carmo"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def generated(project):
    return yaml.safe_load((project / ".docker-compose.scale.yml").read_text())


def env_of(service) -> dict:
    environment = service.get("environment")
    if isinstance(environment, dict):
        return environment
    return dict(entry.split("=", 1) for entry in environment)


def test_dev_instances_receive_the_extra_environment(project):
    (project / "compose.yml").write_text(COMPOSE_DICT_ENV)

    generate_scaled_compose(2, dev_environment=REPO_ENV)

    services = generated(project)["services"]
    for name in ("dev-1", "dev-2"):
        assert env_of(services[name])["DEVBASE_REPOS"] == "cGxhbg=="
        assert env_of(services[name])["DEVBASE_PRIMARY_DIR"] == "carmo"
        # 元からある値は残す
        assert env_of(services[name])["FEATURE_FLAG"] == "enabled"


def test_non_dev_services_do_not_receive_it(project):
    """clone するのは dev コンテナだけ。他サービスへ余計な変数を増やさない"""
    (project / "compose.yml").write_text(COMPOSE_DICT_ENV)

    generate_scaled_compose(1, dev_environment=REPO_ENV)

    db = generated(project)["services"]["db"]
    assert "DEVBASE_REPOS" not in (db.get("environment") or {})


def test_list_form_environment_is_supported(project):
    (project / "compose.yml").write_text(COMPOSE_LIST_ENV)

    generate_scaled_compose(1, dev_environment=REPO_ENV)

    dev = generated(project)["services"]["dev-1"]
    assert env_of(dev) == {
        "FEATURE_FLAG": "enabled",
        "DEVBASE_REPOS": "cGxhbg==",
        "DEVBASE_PRIMARY_DIR": "carmo",
    }


def test_environment_section_is_created_when_absent(project):
    (project / "compose.yml").write_text(COMPOSE_NO_ENV)

    generate_scaled_compose(1, dev_environment=REPO_ENV)

    assert env_of(generated(project)["services"]["dev-1"]) == REPO_ENV


def test_without_extra_environment_nothing_is_added(project):
    (project / "compose.yml").write_text(COMPOSE_NO_ENV)

    generate_scaled_compose(1)

    assert "environment" not in generated(project)["services"]["dev-1"]


def test_unsupported_environment_is_replaced_before_dev_environment(project, caplog):
    (project / "compose.yml").write_text(COMPOSE_UNSUPPORTED_ENV)

    with caplog.at_level("WARNING"):
        generate_scaled_compose(1, dev_environment=REPO_ENV)

    assert generated(project)["services"]["dev-1"]["environment"] == [
        "DEVBASE_REPOS=cGxhbg==",
        "DEVBASE_PRIMARY_DIR=carmo",
    ]
    assert any("environment の形式 (str) を解釈できない" in r.getMessage()
               for r in caplog.records)
