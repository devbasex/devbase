"""compose.py: generate_scaled_compose() の `init: true` 注入挙動 (issue #28)

devbase コンテナの PID 1 は entrypoint の `tail -f /dev/null` であり orphan を reap しない。
docker の `init: true` で tini を PID 1 に挿入しゾンビ蓄積を防ぐため、生成される全サービスに
`init` を注入する。ユーザーが明示した `init: false` は setdefault で尊重する。
"""

from __future__ import annotations

import os

import yaml
import pytest

from devbase.volume import compose


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    """生成物 (.docker-compose.scale.yml) が散らからないよう CWD を tmp に移す。"""
    monkeypatch.chdir(tmp_path)
    # DEV_SERVICE_NAME が外部環境に左右されないよう既定に固定
    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)
    return tmp_path


def _write_compose(tmp_path, services: dict) -> None:
    (tmp_path / "compose.yml").write_text(
        yaml.safe_dump({"services": services}, sort_keys=False),
        encoding="utf-8",
    )


def _load_scaled(tmp_path) -> dict:
    return yaml.safe_load((tmp_path / ".docker-compose.scale.yml").read_text())


def test_dev_and_non_dev_services_get_init_true(in_tmp_cwd):
    """dev インスタンスと non-dev サービスの双方に init: true が注入される。"""
    _write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest"},
        "mysql": {"image": "mysql:8"},
    })

    compose.generate_scaled_compose(scale=1)
    scaled = _load_scaled(in_tmp_cwd)["services"]

    assert scaled["dev-1"]["init"] is True
    assert scaled["mysql"]["init"] is True


def test_init_injected_for_every_scaled_instance(in_tmp_cwd):
    """scale>1 でも各 dev-i 全てに init: true が付く。"""
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=3)
    scaled = _load_scaled(in_tmp_cwd)["services"]

    for i in (1, 2, 3):
        assert scaled[f"dev-{i}"]["init"] is True


def test_explicit_init_false_is_preserved(in_tmp_cwd):
    """明示的な init: false は setdefault により上書きされない。"""
    _write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest", "init": False},
        "mysql": {"image": "mysql:8", "init": False},
    })

    compose.generate_scaled_compose(scale=1)
    scaled = _load_scaled(in_tmp_cwd)["services"]

    assert scaled["dev-1"]["init"] is False
    assert scaled["mysql"]["init"] is False


def test_depends_on_list_rewrites_dev_to_scaled_instances(in_tmp_cwd):
    """現状固定: list 形式の depends_on は dev だけを dev-i 群へ展開する。"""
    _write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest"},
        "worker": {
            "image": "worker:latest",
            "depends_on": ["dev", "mysql"],
        },
        "mysql": {"image": "mysql:8"},
    })

    compose.generate_scaled_compose(scale=2)
    scaled = _load_scaled(in_tmp_cwd)["services"]

    assert scaled["worker"]["depends_on"] == ["dev-1", "dev-2", "mysql"]


def test_depends_on_map_rewrites_dev_to_scaled_instances(in_tmp_cwd):
    """現状固定: map 形式の depends_on は dev の条件を各 dev-i へ複製する。"""
    condition = {"condition": "service_healthy", "restart": True}
    _write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest"},
        "worker": {
            "image": "worker:latest",
            "depends_on": {
                "dev": condition,
                "mysql": {"condition": "service_started"},
            },
        },
        "mysql": {"image": "mysql:8"},
    })

    compose.generate_scaled_compose(scale=2)
    depends_on = _load_scaled(in_tmp_cwd)["services"]["worker"]["depends_on"]

    assert depends_on == {
        "mysql": {"condition": "service_started"},
        "dev-1": condition,
        "dev-2": condition,
    }
