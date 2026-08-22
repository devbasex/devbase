"""ライフサイクルフックへ渡す環境変数 (PLAN32)

``pre-up`` / ``deploy`` はホスト側で動き、clone 先のパスを必要とすることがある。
旧構成では ``source ./env`` で ``GIT_REPO`` / ``WORK_DIR`` を読んでいたが、これらは
``project.yml`` へ移ったため devbase 側から明示的に渡す。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from devbase.commands import container
from devbase.project.config import parse_project_config

DUMP_SCRIPT = '''#!/bin/bash
{
  echo "DEVBASE_PRIMARY_DIR=$DEVBASE_PRIMARY_DIR"
  echo "DEVBASE_PRIMARY_URL=$DEVBASE_PRIMARY_URL"
  echo "DEVBASE_WORK_DIR=$DEVBASE_WORK_DIR"
  echo "DEVBASE_REPO_DIRS=$DEVBASE_REPO_DIRS"
  echo "DEVBASE_INSTANCE_INDEX=$DEVBASE_INSTANCE_INDEX"
} > dump.txt
'''


@pytest.fixture
def config():
    return parse_project_config({
        "version": 1,
        "defaults": {"owner": "volareinc"},
        "repos": [{"repo": "carmo"}, {"repo": "carmo-batch"}],
    }, source="project.yml")


def dumped(tmp_path: Path) -> dict:
    lines = (tmp_path / "dump.txt").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines)


def test_pre_up_hook_receives_the_repo_layout(tmp_path, monkeypatch, config):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pre-up").write_text(DUMP_SCRIPT)

    assert container._run_pre_up_hook(config) is True

    values = dumped(tmp_path)
    assert values["DEVBASE_PRIMARY_DIR"] == "carmo"
    assert values["DEVBASE_PRIMARY_URL"] == "https://github.com/volareinc/carmo.git"
    assert values["DEVBASE_WORK_DIR"] == "/work/carmo"
    assert values["DEVBASE_REPO_DIRS"] == "carmo carmo-batch"


def test_deploy_hook_receives_the_repo_layout_and_index(tmp_path, monkeypatch, config):
    monkeypatch.chdir(tmp_path)
    deploy = tmp_path / "deploy"
    deploy.write_text(DUMP_SCRIPT)

    container._run_deploy_script_for_instances(deploy, [2], config)

    values = dumped(tmp_path)
    assert values["DEVBASE_WORK_DIR"] == "/work/carmo"
    assert values["DEVBASE_INSTANCE_INDEX"] == "2"


def test_hooks_still_run_without_a_config(tmp_path, monkeypatch):
    """設定を渡さない呼び出し (旧 scale 経路) でもフック自体は動く"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEVBASE_WORK_DIR", raising=False)
    (tmp_path / "pre-up").write_text(DUMP_SCRIPT)

    assert container._run_pre_up_hook() is True

    assert dumped(tmp_path)["DEVBASE_WORK_DIR"] == ""


def test_hook_env_does_not_leak_into_the_parent_process(tmp_path, monkeypatch, config):
    monkeypatch.chdir(tmp_path)
    # 実行環境に同名の変数が居ても判定がぶれないよう、事前状態を固定する
    monkeypatch.delenv("DEVBASE_WORK_DIR", raising=False)
    (tmp_path / "pre-up").write_text(DUMP_SCRIPT)

    container._run_pre_up_hook(config)

    assert "DEVBASE_WORK_DIR" not in os.environ
