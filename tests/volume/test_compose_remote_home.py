"""リモート扱いの生成で bind mount の ``~`` を docker.home で展開する (PLAN52 Task 4)。"""

from __future__ import annotations

import pytest
import yaml

from devbase.volume import compose


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)
    monkeypatch.delenv("DEVBASE_ACCOUNT_GROUP", raising=False)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "carmo-ai")
    (tmp_path / "compose.yml").write_text(yaml.safe_dump({"services": {
        "dev": {"image": "dev:latest", "volumes": ["~/.aws:/home/ubuntu/.aws",
                                                   "/var/run/docker.sock:/var/run/docker.sock"]},
        "db": {"image": "db", "volumes": ["./init.sql:/init.sql"]},
    }}, sort_keys=False), encoding="utf-8")
    return tmp_path


def _sources(tmp_path, service):
    doc = yaml.safe_load((tmp_path / ".docker-compose.scale.yml").read_text())
    return [v if isinstance(v, str) else v["source"] for v in doc["services"][service]["volumes"]]


def test_local_generation_keeps_tilde(in_tmp_cwd):
    compose.generate_scaled_compose(1)
    assert "~/.aws:/home/ubuntu/.aws" in _sources(in_tmp_cwd, "dev-1")


def test_remote_with_home_rewrites_and_warns_for_relative(in_tmp_cwd, caplog):
    with caplog.at_level("WARNING"):
        compose.generate_scaled_compose(1, docker_home="/home/takemi", remote=True)
    assert "/home/takemi/.aws:/home/ubuntu/.aws" in _sources(in_tmp_cwd, "dev-1")
    assert "./init.sql:/init.sql" in _sources(in_tmp_cwd, "db")
    assert "db: ./init.sql:/init.sql" in caplog.text
    assert "~/.aws" not in caplog.text


def test_remote_without_home_warns_and_keeps(in_tmp_cwd, caplog):
    with caplog.at_level("WARNING"):
        compose.generate_scaled_compose(1, remote=True)
    assert "~/.aws:/home/ubuntu/.aws" in _sources(in_tmp_cwd, "dev-1")
    assert "dev-1: ~/.aws:/home/ubuntu/.aws" in caplog.text
    assert "docker.home" in caplog.text
    assert "docker.sock" not in caplog.text
