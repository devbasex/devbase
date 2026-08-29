"""dev サービスへの追加環境変数の注入 (PLAN32: clone プランの受け渡し)"""

from __future__ import annotations

import os

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

REPO_ENV = {"DEVBASE_REPOS": "cGxhbg==", "DEVBASE_PRIMARY_DIR": "carmo"}

# devbase 自身が dev サービスへ常に載せる環境変数 (PLAN39)。
# 個々のテストの期待値からは除いて比較し、内容そのものは
# test_devbase_managed_environment_is_always_present で固定する。
DEVBASE_MANAGED = {
    "DEVBASE_ACCOUNT_GROUP": "default",
    "CLOUDSDK_CONFIG": "/persistent/group/gcloud",
    "GOOGLE_WORKSPACE_CLI_CONFIG_DIR": "/persistent/group/gws",
    "GCP_AUTH_MODE": "adc",
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # devbase 由来の変数は常に dev へ載る (PLAN39)。外部環境で値が変わらないよう
    # 解決の入力になるキーを落としておく。
    for name in ("DEVBASE_ACCOUNT_GROUP", "GCP_AUTH_MODE",
                 "GOOGLE_APPLICATION_CREDENTIALS_BASE64"):
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("GCP_CREDENTIALS_BASE64__"):
            monkeypatch.delenv(name, raising=False)
    return tmp_path


def generated(project):
    return yaml.safe_load((project / ".docker-compose.scale.yml").read_text())


def env_of(service) -> dict:
    environment = service.get("environment")
    if isinstance(environment, dict):
        return environment
    return dict(entry.split("=", 1) for entry in environment)


def user_env(service) -> dict:
    """devbase 自身が載せる変数を除いた environment"""
    return {k: v for k, v in env_of(service).items() if k not in DEVBASE_MANAGED}


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
    assert user_env(dev) == {
        "FEATURE_FLAG": "enabled",
        "DEVBASE_REPOS": "cGxhbg==",
        "DEVBASE_PRIMARY_DIR": "carmo",
    }


def test_environment_section_is_created_when_absent(project):
    (project / "compose.yml").write_text(COMPOSE_NO_ENV)

    generate_scaled_compose(1, dev_environment=REPO_ENV)

    assert user_env(generated(project)["services"]["dev-1"]) == REPO_ENV


def test_without_extra_environment_only_devbase_values_are_added(project):
    """呼び出し側が何も渡さなくても devbase 由来の変数は載る (PLAN39)。"""
    (project / "compose.yml").write_text(COMPOSE_NO_ENV)

    generate_scaled_compose(1)

    assert user_env(generated(project)["services"]["dev-1"]) == {}


def test_devbase_managed_environment_is_always_present(project):
    """マウント先とコンテナ側の解決結果を必ず一致させるため、ホストが明示的に渡す。

    - ``DEVBASE_ACCOUNT_GROUP``: マウントされたグループボリュームと同じ解決結果
    - ``CLOUDSDK_CONFIG`` / ``GOOGLE_WORKSPACE_CLI_CONFIG_DIR``: gcloud / gws の
      設定ディレクトリ。entrypoint の export は docker exec のシェルに届かないため
      compose で渡す必要がある
    - ``GCP_AUTH_MODE``: ホストで解決した認証モード
    """
    (project / "compose.yml").write_text(COMPOSE_NO_ENV)

    generate_scaled_compose(1)

    env = env_of(generated(project)["services"]["dev-1"])
    assert {k: env[k] for k in DEVBASE_MANAGED} == DEVBASE_MANAGED
