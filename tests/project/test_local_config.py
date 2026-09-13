"""``project.local.yml`` (個人・機材ごとの設定) の読み込みと検証 (PLAN52)。"""

from __future__ import annotations

import pytest

from devbase.errors import ConfigError
from devbase.project.local_config import (
    PROJECT_LOCAL_CONFIG_FILENAME,
    DockerSettings,
    load_project_local_config,
    parse_project_local_config,
)


def write_local(tmp_path, text: str):
    (tmp_path / PROJECT_LOCAL_CONFIG_FILENAME).write_text(text, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# 無い・空
# ---------------------------------------------------------------------------

def test_missing_file_returns_defaults(tmp_path):
    """ファイルが無ければ既定値 (docker の 3 項目とも None) で、例外にしない。"""
    config = load_project_local_config(tmp_path)
    assert config.docker == DockerSettings(context=None, home=None, gid=None)


def test_empty_file_is_same_as_missing(tmp_path):
    write_local(tmp_path, "")
    config = load_project_local_config(tmp_path)
    assert config.docker == DockerSettings(context=None, home=None, gid=None)


# ---------------------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------------------

def test_reads_docker_section(tmp_path):
    write_local(tmp_path, "docker:\n  context: gpu-wsl\n  home: /home/takemi\n  gid: 999\n")
    config = load_project_local_config(tmp_path)
    assert config.docker == DockerSettings(context="gpu-wsl", home="/home/takemi", gid=999)


def test_docker_section_all_optional():
    config = parse_project_local_config({"docker": {"context": "ec2"}}, source="x")
    assert config.docker == DockerSettings(context="ec2", home=None, gid=None)


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------

def test_unknown_top_level_key_is_rejected():
    with pytest.raises(ConfigError) as e:
        parse_project_local_config({"scale": 3}, source="local")
    assert "scale" in str(e.value)
    assert "docker" in str(e.value)  # 使えるキーを添える


def test_unknown_docker_key_is_rejected():
    with pytest.raises(ConfigError) as e:
        parse_project_local_config({"docker": {"host": "x"}}, source="local")
    assert "host" in str(e.value)
    assert "context" in str(e.value) and "home" in str(e.value) and "gid" in str(e.value)


@pytest.mark.parametrize("value", ["", "   ", "a b", 12, "tab\there"])
def test_invalid_context_is_rejected(value):
    with pytest.raises(ConfigError) as e:
        parse_project_local_config({"docker": {"context": value}}, source="local")
    assert "context" in str(e.value)


@pytest.mark.parametrize("value", [-1, True, "999", 1.5])
def test_invalid_gid_is_rejected(value):
    with pytest.raises(ConfigError) as e:
        parse_project_local_config({"docker": {"gid": value}}, source="local")
    assert "gid" in str(e.value)


@pytest.mark.parametrize("value", ["home/takemi", "~", "", 3])
def test_home_must_be_absolute(value):
    with pytest.raises(ConfigError) as e:
        parse_project_local_config({"docker": {"home": value}}, source="local")
    assert "home" in str(e.value)


def test_docker_must_be_mapping():
    with pytest.raises(ConfigError):
        parse_project_local_config({"docker": "gpu-wsl"}, source="local")


def test_broken_yaml_mentions_file(tmp_path):
    write_local(tmp_path, "docker: [\n")
    with pytest.raises(ConfigError) as e:
        load_project_local_config(tmp_path)
    assert PROJECT_LOCAL_CONFIG_FILENAME in str(e.value)


def test_top_level_must_be_mapping(tmp_path):
    write_local(tmp_path, "- a\n")
    with pytest.raises(ConfigError):
        load_project_local_config(tmp_path)
