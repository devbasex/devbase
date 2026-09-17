"""PLAN31_2 PR1: tui.dispatch (ハンドラ委譲層) のテスト。"""

from __future__ import annotations

import os
from pathlib import Path

from devbase.tui import dispatch


def test_dispatch_lifecycle_builds_namespace_and_calls_cmd_project(monkeypatch):
    """dispatch_lifecycle は subcommand/name/attrs を載せた Namespace で cmd_project を呼ぶ。"""
    from devbase.commands import container as container_mod

    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(
                            subcommand=args.subcommand, name=args.name,
                            scale=getattr(args, "scale", "MISSING")) or 0)

    rc = dispatch.dispatch_lifecycle("up", "carmo", scale=None)
    assert rc == 0
    assert captured == {"subcommand": "up", "name": "carmo", "scale": None}


def test_dispatch_lifecycle_name_optional(monkeypatch):
    """name 省略時は None が載る (container 経路相当)。"""
    from devbase.commands import container as container_mod

    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(name=args.name) or 0)

    dispatch.dispatch_lifecycle("rebuild")
    assert captured == {"name": None}


def test_dispatch_lifecycle_restores_cwd_and_env(monkeypatch, tmp_path):
    """ハンドラが chdir / 環境変数変更したまま戻っても呼び出し前の状態へ復元する。

    PR #55 round1 major 回帰テスト: TUI は同一プロセスで継続するため、
    `_resolve_project_name` 相当の chdir / env 反映 / COMPOSE_PROJECT_NAME 上書きが
    トップメニュー復帰後の操作 (env get 等) へ残留してはならない。
    """
    from devbase.commands import container as container_mod

    other = tmp_path / "projects" / "carmo"
    other.mkdir(parents=True)

    def mutating_handler(args):
        os.chdir(other)                                  # chdir 残留を模擬
        os.environ["COMPOSE_PROJECT_NAME"] = "carmo"     # 上書き残留を模擬
        os.environ["DEV_SERVICE_NAME"] = "leaked"        # 新規キー残留を模擬
        os.environ.pop("DEVBASE_TEST_KEEP", None)        # 既存キー削除を模擬
        return 0

    monkeypatch.setattr(container_mod, "cmd_project", mutating_handler)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEVBASE_TEST_KEEP", "orig")
    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "before")

    rc = dispatch.dispatch_lifecycle("up", "carmo", scale=None)

    assert rc == 0
    assert Path.cwd() == tmp_path                        # CWD 復元
    assert os.environ["COMPOSE_PROJECT_NAME"] == "before"  # 上書き復元
    assert "DEV_SERVICE_NAME" not in os.environ          # 漏えいキー除去
    assert os.environ["DEVBASE_TEST_KEEP"] == "orig"     # 削除キー復元


def test_dispatch_lifecycle_restores_state_on_exception(monkeypatch, tmp_path):
    """ハンドラが例外を投げても CWD / 環境変数は復元される (try/finally 保証)。"""
    import pytest

    from devbase.commands import container as container_mod

    def raising_handler(args):
        os.chdir(tmp_path)
        os.environ["DEV_SERVICE_NAME"] = "leaked"
        raise RuntimeError("boom")

    monkeypatch.setattr(container_mod, "cmd_project", raising_handler)
    old_cwd = Path.cwd()
    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)

    with pytest.raises(RuntimeError):
        dispatch.dispatch_lifecycle("up", "carmo", scale=None)

    assert Path.cwd() == old_cwd
    assert "DEV_SERVICE_NAME" not in os.environ


def test_dispatch_group_restores_cwd_and_env(tmp_path, monkeypatch):
    """dispatch_group も lifecycle と同じ復元境界を張る (契約整合)。"""

    def mutating_handler(devbase_root, args):
        os.chdir(tmp_path)
        os.environ["DEV_SERVICE_NAME"] = "leaked"
        return 0

    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)
    old_cwd = Path.cwd()

    rc = dispatch.dispatch_group(mutating_handler, Path("/devbase"), "init")

    assert rc == 0
    assert Path.cwd() == old_cwd
    assert "DEV_SERVICE_NAME" not in os.environ


def test_dispatch_group_builds_namespace_and_calls_handler():
    """dispatch_group は (devbase_root, args) 形式のハンドラへ委譲する。"""
    captured = {}

    def handler(devbase_root, args):
        captured["root"] = devbase_root
        captured["subcommand"] = args.subcommand
        captured["reset"] = args.reset
        return 7

    rc = dispatch.dispatch_group(handler, Path("/devbase"), "init", reset=True)
    assert rc == 7
    assert captured == {"root": Path("/devbase"), "subcommand": "init", "reset": True}


# ---------------------------------------------------------------------------
# PLAN55: TUI は操作の入口で持ち回った SecretStore を捨てる (決定 3)
# ---------------------------------------------------------------------------

def test_preserve_cwd_env_releases_store_on_entry(tmp_path):
    """委譲の入口で控えを捨てるので、handler の中の store_for は別のインスタンスを返す。"""
    from devbase.env import runtime

    runtime.release_store()
    before = runtime.store_for(tmp_path)
    seen = {}

    def handler(devbase_root, args):
        seen["store"] = runtime.store_for(tmp_path)
        return 0

    try:
        assert dispatch.dispatch_group(handler, tmp_path, "list") == 0
        assert seen["store"] is not before
    finally:
        runtime.release_store()


def test_lifecycle_after_env_edit_reads_written_values(openbao_root, openbao, monkeypatch):
    """同じプロセスで `env edit` → `up` したとき、編集後の値で起動する (受け入れ条件 9)。"""
    import subprocess
    import types

    from devbase import cli
    from devbase.commands import container
    from devbase.commands import env as env_mod
    from devbase.env import runtime
    from devbase.utils import docker_context

    root = openbao_root
    web = root / 'projects' / 'web'
    (web / 'project.yml').write_text(
        "version: 1\nscale: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n")
    (web / 'env').write_text("")
    openbao.put('team/global', {'REVIEW_KEY': 'old'})
    openbao.put('team/projects/web', {'WEB_ONLY': 'w'})
    monkeypatch.setenv('DEVBASE_ROOT', str(root))
    for name in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DEVBASE_DOCKER_CONTEXT',
                 'COMPOSE_PROJECT_NAME', 'REVIEW_KEY', 'WEB_ONLY'):
        monkeypatch.delenv(name, raising=False)
    docker_context.reset()
    runtime.release_store()
    runtime.clear_injected()

    seen = {}
    target = docker_context.DockerTarget(context=None, source='none', remote=False,
                                         home=None, gid=None)
    monkeypatch.setattr(container, '_resolve_docker_target', lambda cli_context=None: target)
    for name in ('_run_pre_up_hook', '_ensure_images'):
        monkeypatch.setattr(container, name, lambda *a, **k: True)
    for name in ('_auto_snapshot', 'ensure_volumes', 'ensure_network', 'docker_compose_down',
                 'docker_compose_up', 'wait_for_containers_ready', '_apply_window_titles',
                 '_report_missing_repos', '_maybe_open_editor'):
        monkeypatch.setattr(container, name, lambda *a, **k: None)
    monkeypatch.setattr(container, 'default_services', lambda *a, **k: ['dev-1'])

    def fake_generate(scale, secrets, dev_environment=None, **kw):
        seen['secrets'] = secrets
        seen['environ'] = dict(os.environ)
        compose = Path.cwd() / '.docker-compose.scale.yml'
        compose.write_text("services:\n  dev-1: {}\n")
        return compose

    monkeypatch.setattr(container, '_generate_compose_for', fake_generate)
    # PLAN54: up の後処理が token を書く。docker は叩かない
    monkeypatch.setattr('devbase.editor.opener._query_container_name', lambda *a, **k: None)
    monkeypatch.setattr('devbase.env.container_token.push',
                        lambda names, token, runner=None: list(names))

    def fake_editor(argv):
        Path(argv[1]).write_text("REVIEW_KEY=new\n")
        return 0

    monkeypatch.setattr(env_mod.subprocess, 'call', fake_editor)

    try:
        # TUI の起動: dispatch 前の注入が控えを作る
        cli._load_secret_env('project', 'list')
        assert dispatch.dispatch_group(env_mod.cmd_env, root, 'edit') == 0
        assert openbao.get('team/global') == {'REVIEW_KEY': 'new'}

        assert dispatch.dispatch_lifecycle('up', 'web', scale=None, open_editor=False,
                                           open_index=None, context=None) == 0

        assert seen['secrets'].values['REVIEW_KEY'] == 'new'
        assert seen['environ']['REVIEW_KEY'] == 'new'
    finally:
        runtime.release_store()
        runtime.clear_injected()


# ---------------------------------------------------------------------------
# PR #191 round 3: 機密の注入履歴も CWD・環境変数と揃えて戻す
# ---------------------------------------------------------------------------

def test_preserve_cwd_env_restores_the_injection_history(tmp_path, monkeypatch):
    """切替先で注入し直しても、抜けた後の解除は切替元固有の機密を落とす。

    値だけを戻して履歴を戻さないと、戻った切替元の機密を次の解除が知らず、
    次に操作するプロジェクトの Compose 子プロセスへ渡ってしまう。
    """
    import pyrage

    from devbase.env import runtime
    from devbase.env.secret_store import SecretRef, SecretStore

    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    (tmp_path / 'projects' / 'api').mkdir(parents=True)
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runtime, '_injected_originals', {})
    for name in ('WEB_ONLY', 'API_ONLY'):
        # 一度設定してから消し、失敗時にも monkeypatch が未設定へ戻すようにする
        monkeypatch.setenv(name, '')
        monkeypatch.delenv(name)

    identity = pyrage.x25519.Identity.generate()
    key = tmp_path / 'id.key'
    key.write_text(str(identity))
    store = SecretStore(tmp_path, recipients=[str(identity.to_public())],
                        identities=[str(key)])
    store.age.save(SecretRef.for_project('web'), {'WEB_ONLY': 'w'})
    store.age.save(SecretRef.for_project('api'), {'API_ONLY': 'a'})

    def enter_api(devbase_root, args):
        runtime.clear_injected()
        runtime.inject(devbase_root, 'api', store=store)
        assert 'WEB_ONLY' not in os.environ
        return 0

    try:
        # 現在地 (web) の機密を載せた状態から、別プロジェクト (api) を照会する
        runtime.inject(tmp_path, 'web', store=store)
        assert dispatch.dispatch_group(enter_api, tmp_path, 'list') == 0
        assert os.environ['WEB_ONLY'] == 'w'

        # 次の操作の切替で、切替元固有の機密が落ちる
        runtime.clear_injected()
        assert 'WEB_ONLY' not in os.environ
        assert 'API_ONLY' not in os.environ
    finally:
        runtime.clear_injected()
        runtime.release_store()
