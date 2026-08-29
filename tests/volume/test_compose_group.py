"""グループボリュームのマウント・宣言・環境変数 (PLAN39 Task 2)

生成 compose に対して次の 3 点を固定する。

1. dev インスタンスへ ``/persistent/group`` が `devbase_home_<group>` としてマウントされる
2. そのボリュームが ``volumes:`` セクションへ ``external: true`` で宣言される
3. dev サービスの ``environment`` へ ``DEVBASE_ACCOUNT_GROUP`` が載る
   (entrypoint が初回シードの判定に使う)
"""

from __future__ import annotations

import pytest
import yaml

from devbase.errors import DevbaseError
from devbase.volume import compose


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)
    monkeypatch.delenv("DEVBASE_ACCOUNT_GROUP", raising=False)
    return tmp_path


def _write_compose(tmp_path, services: dict, volumes: dict | None = None) -> None:
    document = {"services": services}
    if volumes is not None:
        document["volumes"] = volumes
    (tmp_path / "compose.yml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _load_scaled(tmp_path) -> dict:
    return yaml.safe_load((tmp_path / ".docker-compose.scale.yml").read_text())


def _mount_source(service: dict, target: str) -> str | None:
    for vol in service.get("volumes", []):
        if isinstance(vol, str):
            parts = vol.split(":")
            if len(parts) >= 2 and parts[1] == target:
                return parts[0]
        elif isinstance(vol, dict) and vol.get("target") == target:
            return vol.get("source")
    return None


def _env_value(service: dict, name: str):
    env = service.get("environment")
    if isinstance(env, dict):
        return env.get(name)
    if isinstance(env, list):
        for entry in env:
            if isinstance(entry, str) and entry.split("=", 1)[0] == name:
                return entry.split("=", 1)[1] if "=" in entry else None
    return None


# ---------------------------------------------------------------------------
# マウント (AC3 / AC5)
# ---------------------------------------------------------------------------

def test_group_mount_is_added_when_absent(in_tmp_cwd):
    """プロジェクト compose が宣言していなくても自動で足される (前提 4)。"""
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1)
    dev = _load_scaled(in_tmp_cwd)["services"]["dev-1"]

    assert _mount_source(dev, "/persistent/group") == "devbase_home_default"


def test_group_mount_follows_account_group(in_tmp_cwd, monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1)
    dev = _load_scaled(in_tmp_cwd)["services"]["dev-1"]

    assert _mount_source(dev, "/persistent/group") == "devbase_home_kkg"


def test_declared_group_mount_is_rewritten(in_tmp_cwd, monkeypatch):
    """プロジェクトが別のソースで宣言していても devbase 側の名前へ差し替える。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    _write_compose(
        in_tmp_cwd,
        {"dev": {"image": "dev:latest",
                 "volumes": ["someone_elses_volume:/persistent/group:rw"]}},
        volumes={"someone_elses_volume": {}},
    )

    compose.generate_scaled_compose(scale=1)
    dev = _load_scaled(in_tmp_cwd)["services"]["dev-1"]

    assert "devbase_home_kkg:/persistent/group:rw" in dev["volumes"]


def test_every_instance_gets_the_same_group_volume(in_tmp_cwd, monkeypatch):
    """グループはインスタンス番号に依存しない (同グループ内で共有する)。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=3)
    services = _load_scaled(in_tmp_cwd)["services"]

    for i in (1, 2, 3):
        assert _mount_source(services[f"dev-{i}"], "/persistent/group") == "devbase_home_kkg"


def test_shared_ai_mount_is_unchanged(in_tmp_cwd, monkeypatch):
    """共通ボリュームは分離の影響を受けない (分類 A は全グループ同一実体 / AC4)。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1)
    dev = _load_scaled(in_tmp_cwd)["services"]["dev-1"]

    assert _mount_source(dev, "/persistent/ai") == "devbase_home_ubuntu"


# ---------------------------------------------------------------------------
# ボリューム宣言
# ---------------------------------------------------------------------------

def test_group_volume_is_declared_external(in_tmp_cwd, monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1)
    volumes = _load_scaled(in_tmp_cwd)["volumes"]

    assert volumes["devbase_home_kkg"] == {"external": True}
    assert volumes["devbase_home_ubuntu"] == {"external": True}


# ---------------------------------------------------------------------------
# 環境変数
# ---------------------------------------------------------------------------

def test_account_group_is_exposed_to_dev_service(in_tmp_cwd, monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1)
    dev = _load_scaled(in_tmp_cwd)["services"]["dev-1"]

    assert _env_value(dev, "DEVBASE_ACCOUNT_GROUP") == "kkg"


def test_default_group_is_exposed_when_unset(in_tmp_cwd):
    """未設定でも解決結果を明示的に渡す (コンテナ側で再解決させない)。"""
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1)
    dev = _load_scaled(in_tmp_cwd)["services"]["dev-1"]

    assert _env_value(dev, "DEVBASE_ACCOUNT_GROUP") == "default"


def test_account_group_is_added_to_list_form_environment(in_tmp_cwd, monkeypatch):
    """既存の list 形式 environment を壊さない。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    _write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest", "environment": ["FEATURE_FLAG=enabled"]},
    })

    compose.generate_scaled_compose(scale=1)
    dev = _load_scaled(in_tmp_cwd)["services"]["dev-1"]

    assert _env_value(dev, "FEATURE_FLAG") == "enabled"
    assert _env_value(dev, "DEVBASE_ACCOUNT_GROUP") == "kkg"


def test_caller_supplied_dev_environment_is_preserved(in_tmp_cwd, monkeypatch):
    """clone プラン等 (PLAN32) と共存する。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(
        scale=1, dev_environment={"DEVBASE_PRIMARY_DIR": "app"})
    dev = _load_scaled(in_tmp_cwd)["services"]["dev-1"]

    assert _env_value(dev, "DEVBASE_PRIMARY_DIR") == "app"
    assert _env_value(dev, "DEVBASE_ACCOUNT_GROUP") == "kkg"


# ---------------------------------------------------------------------------
# 検証 (AC7)
# ---------------------------------------------------------------------------

def test_invalid_group_stops_compose_generation(in_tmp_cwd, monkeypatch):
    """不正なグループ名は構成生成の時点で弾く (コンテナを起動させない)。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "ubuntu")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    with pytest.raises(DevbaseError):
        compose.generate_scaled_compose(scale=1)
