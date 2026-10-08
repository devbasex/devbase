"""volume のテストの共通の前提

``devbase up`` / ``scale`` は起動の先頭でプロジェクトの宣言を読み、``DEVBASE_ACCOUNT_GROUP``
へ置いてから構成を作る (#315)。構成の生成とボリュームの作成を単独で呼ぶテストは、その後の
状態 (グループが決まっている) を前提にする。未設定の振る舞いを見るテストは自分で消す。

compose の生成を見るテストが共有する補助 (``write_compose``・``load_scaled``・``mount_source``)
もここに置き、``from tests.volume.conftest import ...`` で取り込む。
"""

from __future__ import annotations

import pytest
import yaml


@pytest.fixture(autouse=True)
def _declared_account_group(_isolate_env, monkeypatch):
    monkeypatch.setenv('DEVBASE_ACCOUNT_GROUP', 'acme')


def write_compose(tmp_path, services: dict, volumes: dict | None = None) -> None:
    """``tmp_path`` に ``compose.yml`` を書く (``volumes`` を渡したときだけその節を持つ)"""
    document = {"services": services}
    if volumes is not None:
        document["volumes"] = volumes
    (tmp_path / "compose.yml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def load_scaled(tmp_path) -> dict:
    """スケールで作ったファイル (``.docker-compose.scale.yml``) を読む"""
    return yaml.safe_load((tmp_path / ".docker-compose.scale.yml").read_text())


def mount_source(service: dict, target: str) -> str | None:
    """サービスの ``volumes`` で ``target`` にマウントしている元 (短い記法と長い記法)"""
    for vol in service.get("volumes", []):
        if isinstance(vol, str):
            parts = vol.split(":")
            if len(parts) >= 2 and parts[1] == target:
                return parts[0]
        elif isinstance(vol, dict) and vol.get("target") == target:
            return vol.get("source")
    return None
