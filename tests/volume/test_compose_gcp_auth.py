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
    monkeypatch.delenv("GCP_ACTIVE_PROFILE", raising=False)
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


# ---------------------------------------------------------------------------
# 元の compose.yml が environment へ直書きしている場合 (AC12 (1))
# ---------------------------------------------------------------------------

INLINE_MAP_COMPOSE = """services:
  dev:
    image: alpine
    environment:
      GOOGLE_APPLICATION_CREDENTIALS: /home/ubuntu/.config/gcloud/credentials.json
      BIGQUERY_KEY_FILE: /home/ubuntu/.config/gcloud/credentials.json
      TZ: Asia/Tokyo
    volumes:
      - x:/work
  batch:
    image: alpine
    environment:
      - GOOGLE_APPLICATION_CREDENTIALS=/keys/sa.json
      - TZ=Asia/Tokyo
volumes:
  x: {}
"""

ONLY_KEYS_COMPOSE = """services:
  dev:
    image: alpine
    environment:
      GOOGLE_APPLICATION_CREDENTIALS: /home/ubuntu/.config/gcloud/credentials.json
      BIGQUERY_KEY_FILE: /home/ubuntu/.config/gcloud/credentials.json
    volumes:
      - x:/work
volumes:
  x: {}
"""


def services(project) -> dict:
    return yaml.safe_load(
        (project / ".docker-compose.scale.yml").read_text())["services"]


def test_adc_drops_inline_key_paths_from_the_original_compose(project, monkeypatch):
    """列挙を絞るだけでは残ってしまう直書きの値も消す。

    実体の無いパスが残ると ADC はユーザー認証へフォールバックせず
    DefaultCredentialsError で落ちるため、値ごと取り除く必要がある。
    """
    (project / "compose.yml").write_text(INLINE_MAP_COMPOSE)
    monkeypatch.setenv("GCP_AUTH_MODE", "adc")

    generate_scaled_compose(1, secret_env_names=SECRET_NAMES)

    env = env_map(project)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert "BIGQUERY_KEY_FILE" not in env
    # 鍵と無関係な直書きの値は残す
    assert env["TZ"] == "Asia/Tokyo"


def test_adc_keeps_inline_key_paths_of_non_dev_services(project, monkeypatch):
    """非 dev サービスの明示設定は残す。

    ``GCP_AUTH_MODE`` は **dev コンテナの認証方式**の宣言である。独自に鍵を
    マウントしている batch のようなサービスから元の ``compose.yml`` の設定まで
    消すと、そのサービスを壊してしまう。
    """
    (project / "compose.yml").write_text(INLINE_MAP_COMPOSE)
    monkeypatch.setenv("GCP_AUTH_MODE", "adc")

    generate_scaled_compose(1, secret_env_names=SECRET_NAMES)

    batch = services(project)["batch"]["environment"]
    assert batch == ["GOOGLE_APPLICATION_CREDENTIALS=/keys/sa.json", "TZ=Asia/Tokyo"]


def test_key_mode_keeps_inline_key_paths(project, monkeypatch):
    """鍵モードでは直書きのパスを尊重する (前提 11)。"""
    (project / "compose.yml").write_text(INLINE_MAP_COMPOSE)
    monkeypatch.setenv("GCP_AUTH_MODE", "key")
    monkeypatch.setenv("GCP_CREDENTIALS_BASE64__default", "eyJ9")

    generate_scaled_compose(1, secret_env_names=SECRET_NAMES)

    env = env_map(project)
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] is None  # 機密として伏せ字化
    assert services(project)["batch"]["environment"] == [
        "GOOGLE_APPLICATION_CREDENTIALS=/keys/sa.json", "TZ=Asia/Tokyo"]


def test_inline_key_paths_are_dropped_from_the_dev_instance(project, monkeypatch):
    """dev の environment に直書きされた 2 変数は消す。

    列挙を絞るだけでは元の ``compose.yml`` の直書きが生成物に残り、実在しない
    パスが ADC を ``DefaultCredentialsError`` で落とす。
    """
    (project / "compose.yml").write_text(ONLY_KEYS_COMPOSE)
    monkeypatch.setenv("GCP_AUTH_MODE", "adc")

    generate_scaled_compose(1)

    names = env_names(project)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in names
    assert "BIGQUERY_KEY_FILE" not in names


def test_environment_is_removed_when_it_becomes_empty():
    """全部消えたら environment ごと落とす (空の map を残さない)。"""
    from devbase.volume.compose import _drop_env_names
    from devbase.env import gcp_auth

    service = {"image": "alpine", "environment": {
        "GOOGLE_APPLICATION_CREDENTIALS": "/keys/sa.json",
        "BIGQUERY_KEY_FILE": "/keys/sa.json",
    }}

    _drop_env_names(service, gcp_auth.KEY_ONLY_ENV_KEYS)

    assert "environment" not in service


def test_declared_key_without_a_key_drops_them_too(project, monkeypatch):
    """GCP_AUTH_MODE=key でも鍵が無ければ adc 相当 (entrypoint と同じ)。"""
    (project / "compose.yml").write_text(INLINE_MAP_COMPOSE)
    monkeypatch.setenv("GCP_AUTH_MODE", "key")

    generate_scaled_compose(1, secret_env_names=SECRET_NAMES)

    env = env_map(project)
    assert env["GCP_AUTH_MODE"] == "adc"
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert "BIGQUERY_KEY_FILE" not in env


def test_other_profiles_key_does_not_enable_key_mode(project, monkeypatch):
    """アクティブでないプロファイルの鍵では key モードにしない。"""
    monkeypatch.setenv("GCP_ACTIVE_PROFILE", "dev")
    monkeypatch.setenv("GCP_CREDENTIALS_BASE64__prod", "eyJ9")

    generate_scaled_compose(1, secret_env_names=SECRET_NAMES)

    env = env_map(project)
    assert env["GCP_AUTH_MODE"] == "adc"
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
