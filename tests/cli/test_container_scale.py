"""現状固定テスト: cmd_scale の振る舞い。

long_method リファクタリング (R2-004) において、
引数検証、設定更新、パイプライン実行、後続処理が振る舞いを変えずに
動作することを固定する。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devbase.commands import container
from devbase.errors import DevbaseError
from devbase.project import runtime as project_runtime
from devbase.utils import docker_context


PROJECT_YML = "version: 1\nscale: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n"


def test_scale_rejects_non_positive_scale(monkeypatch, tmp_path):
    """現状固定: 1 未満のスケールはエラーとなり 1 を返す。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'project.yml').write_text(PROJECT_YML)
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))

    assert container.cmd_scale(0) == 1
    assert container.cmd_scale(-1) == 1


def test_scale_rejects_scale_down_or_equal(monkeypatch, tmp_path):
    """現状固定: 現在値以下のスケールは警告を出して 1 を返す。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'project.yml').write_text(
        "version: 1\nscale: 2\nrepos:\n  - owner: volareinc\n    repo: carmo\n")
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))

    assert container.cmd_scale(2) == 1
    assert container.cmd_scale(1) == 1


def test_scale_target_resolution_failure(monkeypatch, tmp_path):
    """現状固定: docker context 解決に失敗したときはエラーとなり 1 を返す。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'project.yml').write_text(PROJECT_YML)
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))

    def _fail_resolve(_):
        raise DevbaseError("Target resolution failed")

    monkeypatch.setattr(container, '_resolve_docker_target', _fail_resolve)
    assert container.cmd_scale(2) == 1


def test_scale_happy_path(monkeypatch, tmp_path):
    """現状固定: 正常系で project.yml の更新、パイプラインの各ステップ実行、成功ログまで完了する。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'project.yml').write_text(PROJECT_YML)
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))

    calls = []
    monkeypatch.setattr(container, 'get_project_name', lambda: 'myproj')
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'mydev')
    monkeypatch.setattr(container, '_resolve_docker_target',
                        lambda ctx: docker_context.DockerTarget(
                            context=None, source='default', remote=False, home=None, gid=None))
    monkeypatch.setattr(container, 'ensure_volumes', lambda scale, proj: calls.append(('volumes', scale, proj)))
    monkeypatch.setattr(container, 'ensure_network', lambda net: calls.append(('network', net)))
    monkeypatch.setattr(container, '_inject_secrets', lambda *, required: None)

    dummy_compose = tmp_path / 'dummy-compose.yml'
    dummy_compose.write_text("services: {}")
    monkeypatch.setattr(container, '_generate_compose_for', lambda *a, **k: dummy_compose)
    monkeypatch.setattr(container.subprocess, 'run',
                        lambda cmd, **k: calls.append(('run', cmd)) or subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(container, 'wait_for_containers_ready', lambda **k: calls.append(('wait', k)))

    ret = container.cmd_scale(3)
    assert ret == 0

    # project.yml が更新されていること
    cfg = project_runtime.current_project_config()
    assert cfg.scale == 3

    assert ('volumes', 3, 'myproj') in calls
    assert ('network', 'devbase_net') in calls
    assert any(c[0] == 'run' and 'up' in c[1] and '--no-recreate' in c[1] for c in calls)
    assert any(c[0] == 'wait' and c[1]['scale'] == 3 for c in calls)


def test_scale_pipeline_docker_up_fails(monkeypatch, tmp_path):
    """現状固定: docker compose up が失敗した場合は 1 を返す。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'project.yml').write_text(PROJECT_YML)
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))

    monkeypatch.setattr(container, 'get_project_name', lambda: 'myproj')
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'mydev')
    monkeypatch.setattr(container, '_resolve_docker_target',
                        lambda ctx: docker_context.DockerTarget(
                            context=None, source='default', remote=False, home=None, gid=None))
    monkeypatch.setattr(container, 'ensure_volumes', lambda *a, **k: None)
    monkeypatch.setattr(container, 'ensure_network', lambda *a, **k: None)
    monkeypatch.setattr(container, '_inject_secrets', lambda *, required: None)
    dummy_compose = tmp_path / 'dummy-compose.yml'
    dummy_compose.write_text("services: {}")
    monkeypatch.setattr(container, '_generate_compose_for', lambda *a, **k: dummy_compose)
    monkeypatch.setattr(container.subprocess, 'run',
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1))

    ret = container.cmd_scale(2)
    assert ret == 1


def test_scale_runs_deploy_script_for_new_instances(monkeypatch, tmp_path):
    """現状固定: ./deploy が存在する場合、追加されたインスタンスに対して実行される。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'project.yml').write_text(PROJECT_YML)
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))

    deploy_file = tmp_path / 'deploy'
    deploy_file.write_text("#!/bin/sh\n")

    deploy_calls = []
    monkeypatch.setattr(container, 'get_project_name', lambda: 'myproj')
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'mydev')
    monkeypatch.setattr(container, '_resolve_docker_target',
                        lambda ctx: docker_context.DockerTarget(
                            context=None, source='default', remote=False, home=None, gid=None))
    monkeypatch.setattr(container, 'ensure_volumes', lambda *a, **k: None)
    monkeypatch.setattr(container, 'ensure_network', lambda *a, **k: None)
    monkeypatch.setattr(container, '_inject_secrets', lambda *, required: None)
    dummy_compose = tmp_path / 'dummy-compose.yml'
    dummy_compose.write_text("services: {}")
    monkeypatch.setattr(container, '_generate_compose_for', lambda *a, **k: dummy_compose)
    monkeypatch.setattr(container.subprocess, 'run',
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(container, 'wait_for_containers_ready', lambda **k: None)
    monkeypatch.setattr(container, '_run_deploy_script_for_instances',
                        lambda script, instances, cfg: deploy_calls.append((script, list(instances))))

    ret = container.cmd_scale(3)
    assert ret == 0
    assert len(deploy_calls) == 1
    assert deploy_calls[0][1] == [2, 3]
