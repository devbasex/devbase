"""生成 compose への GCP 認証モードの反映 (PLAN39 Task 5)

`adc` では鍵モード専用の 2 変数を ``environment:`` の列挙から**外す**。名前が
載らなければ Compose はその変数をコンテナへ渡さないため、``docker exec`` の
シェルから見ても未設定になる。entrypoint の ``unset`` は PID 1 の子孫にしか
効かないので、ここで外すことが AC12 の要になる。
"""

from __future__ import annotations

import pytest
import yaml

from devbase.volume.compose import generate_scaled_compose


COMPOSE = """services:
  dev:
    image: alpine
    volumes:
      - x:/work
volumes:
  x: {}
"""

# 機密として列挙される名前 (実際の devbase env と同じ並び)
SECRET_NAMES = [
    "ANTHROPIC_API_KEY",
    "GCP_CREDENTIALS_BASE64__default",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "BIGQUERY_KEY_FILE",
]


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "compose.yml").write_text(COMPOSE)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEVBASE_ACCOUNT_GROUP", raising=False)
    monkeypatch.delenv("GCP_AUTH_MODE", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS_BASE64", raising=False)
    for name in list(dict.fromkeys(SECRET_NAMES)):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def env_names(project) -> set[str]:
    config = yaml.safe_load((project / ".docker-compose.scale.yml").read_text())
    environment = config["services"]["dev-1"].get("environment")
    if isinstance(environment, dict):
        return set(environment)
    return {item.split("=", 1)[0] for item in (environment or [])}


def env_map(project) -> dict:
    config = yaml.safe_load((project / ".docker-compose.scale.yml").read_text())
    environment = config["services"]["dev-1"].get("environment")
    if isinstance(environment, dict):
        return environment
    return dict(
        item.split("=", 1) if "=" in item else (item, None)
        for item in (environment or [])
    )


# ---------------------------------------------------------------------------
# 設定ディレクトリ (AC1 / AC2)
# ---------------------------------------------------------------------------

def test_config_dirs_are_passed_to_the_container(project):
    generate_scaled_compose(1)

    env = env_map(project)
    assert env["CLOUDSDK_CONFIG"] == "/persistent/group/gcloud"
    assert env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] == "/persistent/group/gws"


def test_config_dirs_are_passed_to_every_instance(project):
    generate_scaled_compose(3)

    config = yaml.safe_load((project / ".docker-compose.scale.yml").read_text())
    for index in (1, 2, 3):
        environment = config["services"][f"dev-{index}"]["environment"]
        assert environment["CLOUDSDK_CONFIG"] == "/persistent/group/gcloud"


# ---------------------------------------------------------------------------
# 認証モード (AC12)
# ---------------------------------------------------------------------------

def test_adc_is_the_default_without_a_key(project):
    generate_scaled_compose(1, secret_env_names=["ANTHROPIC_API_KEY"])

    assert env_map(project)["GCP_AUTH_MODE"] == "adc"


def test_key_mode_is_auto_detected(project, monkeypatch):
    """既存プロジェクト (鍵あり) は現行どおり鍵モードで動く。"""
    monkeypatch.setenv("GCP_CREDENTIALS_BASE64__default", "eyJ9")

    generate_scaled_compose(1, secret_env_names=SECRET_NAMES)

    assert env_map(project)["GCP_AUTH_MODE"] == "key"


def test_adc_drops_the_key_only_variables(project, monkeypatch):
    """AC12 (1): 2 変数がコンテナへ渡らない。"""
    monkeypatch.setenv("GCP_AUTH_MODE", "adc")
    monkeypatch.setenv("GCP_CREDENTIALS_BASE64__default", "eyJ9")

    generate_scaled_compose(1, secret_env_names=SECRET_NAMES)

    names = env_names(project)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in names
    assert "BIGQUERY_KEY_FILE" not in names
    # 鍵そのものと他の機密は従来どおり渡す
    assert "GCP_CREDENTIALS_BASE64__default" in names
    assert "ANTHROPIC_API_KEY" in names


def test_key_mode_keeps_the_key_only_variables(project, monkeypatch):
    """AC12 (2): 鍵モードでは従来どおり 2 変数を渡す。"""
    monkeypatch.setenv("GCP_AUTH_MODE", "key")
    monkeypatch.setenv("GCP_CREDENTIALS_BASE64__default", "eyJ9")

    generate_scaled_compose(1, secret_env_names=SECRET_NAMES)

    names = env_names(project)
    assert "GOOGLE_APPLICATION_CREDENTIALS" in names
    assert "BIGQUERY_KEY_FILE" in names


def test_switching_back_to_adc_removes_them_again(project, monkeypatch):
    """AC12 (3): key → adc へ戻すと 2 変数が消える。最も壊れやすい方向。"""
    monkeypatch.setenv("GCP_AUTH_MODE", "key")
    monkeypatch.setenv("GCP_CREDENTIALS_BASE64__default", "eyJ9")
    generate_scaled_compose(1, secret_env_names=SECRET_NAMES)
    assert "GOOGLE_APPLICATION_CREDENTIALS" in env_names(project)

    monkeypatch.setenv("GCP_AUTH_MODE", "adc")
    generate_scaled_compose(1, secret_env_names=SECRET_NAMES)

    names = env_names(project)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in names
    assert "BIGQUERY_KEY_FILE" not in names


def test_adc_also_drops_them_from_the_origin_split(project, monkeypatch):
    """由来別の列挙 (共通 / プロジェクト) からも外す。"""
    monkeypatch.setenv("GCP_AUTH_MODE", "adc")

    generate_scaled_compose(
        1,
        secret_env_names=SECRET_NAMES,
        global_env_names=["ANTHROPIC_API_KEY"],
        project_env_names=["GOOGLE_APPLICATION_CREDENTIALS", "BIGQUERY_KEY_FILE"],
    )

    names = env_names(project)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in names
    assert "BIGQUERY_KEY_FILE" not in names
