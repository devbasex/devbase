"""i30/i07: `devbase rebuild` (= `devbase build --expires=7` のシノニム) のテスト。

- parser: `project rebuild [name]` / `container rebuild` / top-level `rebuild [name]`
- SHORTCUTS / SUBCMD_MAP への登録
- `_dispatch_lifecycle` が rebuild を cmd_rebuild へ振り分ける
- cmd_rebuild の振る舞い (compose.yml 不在=1 / 既定 expires で _build_resolved へ委譲)
- wrapper (bin/devbase) が rebuild を Python 経路へ流す (shell build 経路ではない)
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from devbase import cli


# ---------------------------------------------------------------------------
# parser / shortcuts
# ---------------------------------------------------------------------------

def test_project_rebuild_accepts_optional_name():
    parser = cli._create_parser()
    with_name = parser.parse_args(['project', 'rebuild', 'carmo'])
    assert with_name.command == 'project'
    assert with_name.subcommand == 'rebuild'
    assert with_name.name == 'carmo'

    without_name = parser.parse_args(['project', 'rebuild'])
    assert without_name.subcommand == 'rebuild'
    assert without_name.name is None


def test_container_rebuild_subcommand():
    parser = cli._create_parser()
    ns = parser.parse_args(['container', 'rebuild'])
    assert ns.command == 'container'
    assert ns.subcommand == 'rebuild'


def test_top_level_rebuild_shortcut():
    parser = cli._create_parser()
    ns = parser.parse_args(['rebuild', 'carmo'])
    assert ns.command == 'rebuild'
    assert ns.name == 'carmo'


def test_rebuild_in_shortcuts():
    # build と異なり rebuild は Python 実装なのでトップレベルショートカットに含める
    assert cli.SHORTCUTS.get('rebuild') == 'rebuild'


def test_rebuild_in_subcmd_map():
    assert 'rebuild' in cli.SUBCMD_MAP[('project',)]
    assert 'rebuild' in cli.SUBCMD_MAP[('container', 'ct')]


def test_expand_argv_resolves_rebuild_prefix(monkeypatch):
    """`devbase project re` は rebuild に一意解決される。"""
    import sys
    monkeypatch.setattr(sys, 'argv', ['devbase', 'project', 're'])
    cli._expand_argv()
    assert sys.argv == ['devbase', 'project', 'rebuild']


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def test_lifecycle_routes_rebuild_to_cmd_rebuild(monkeypatch):
    from devbase.commands import container
    called = []
    monkeypatch.setattr(container, 'cmd_rebuild', lambda: called.append(1) or 0)
    args = types.SimpleNamespace(subcommand='rebuild')
    assert container._dispatch_lifecycle(args) == 0
    assert called == [1]


def test_lifecycle_rebuild_resolves_name_first(monkeypatch):
    """`rebuild <name>` は handler 前に name 解決 (chdir) を通す。"""
    from devbase.commands import container
    order = []
    monkeypatch.setattr(container, '_resolve_project_name',
                        lambda name: order.append(('resolve', name)) or True)
    monkeypatch.setattr(container, 'cmd_rebuild',
                        lambda: order.append('rebuild') or 0)
    args = types.SimpleNamespace(subcommand='rebuild', name='carmo')
    assert container._dispatch_lifecycle(args) == 0
    assert order == [('resolve', 'carmo'), 'rebuild']


# ---------------------------------------------------------------------------
# cmd_rebuild の振る舞い
# ---------------------------------------------------------------------------

def test_cmd_rebuild_missing_compose(tmp_path, monkeypatch):
    from devbase.commands import container
    monkeypatch.chdir(tmp_path)
    assert container.cmd_rebuild() == 1


def test_cmd_rebuild_delegates_to_build_resolved_with_default_expires(monkeypatch):
    """rebuild は build --expires=<default> のシノニム: 既定日数で _build_resolved に委譲する。"""
    from devbase.commands import container
    captured = {}
    monkeypatch.setattr(container, '_image_max_age_days', lambda: 7)
    monkeypatch.setattr(container, '_build_resolved',
                        lambda expires, no_cache: captured.update(expires=expires,
                                                                  no_cache=no_cache) or 0)
    assert container.cmd_rebuild() == 0
    assert captured == {'expires': 7, 'no_cache': False}


def test_cmd_rebuild_propagates_returncode(monkeypatch):
    from devbase.commands import container
    monkeypatch.setattr(container, '_build_resolved', lambda expires, no_cache: 2)
    assert container.cmd_rebuild() == 2


# ---------------------------------------------------------------------------
# compose config の読み取り (現状固定)
# ---------------------------------------------------------------------------

def _fake_compose_config(monkeypatch, returncode: int, stdout: str):
    """`docker compose config --format json` の結果を差し替える。"""
    from devbase.commands import container

    class _R:
        pass

    _R.returncode = returncode
    _R.stdout = stdout
    calls = []
    monkeypatch.setattr(container.subprocess, 'run',
                        lambda cmd, *a, **k: calls.append(cmd) or _R())
    return calls


def test_resolve_dev_service_none_when_compose_config_fails(monkeypatch):
    from devbase.commands import container
    _fake_compose_config(monkeypatch, returncode=1, stdout='')
    assert container._resolve_dev_service() is None


def test_resolve_dev_service_none_on_invalid_json(monkeypatch):
    from devbase.commands import container
    _fake_compose_config(monkeypatch, returncode=0, stdout='not json')
    assert container._resolve_dev_service() is None


def test_resolve_dev_service_returns_dev_service(monkeypatch):
    from devbase.commands import container
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    calls = _fake_compose_config(
        monkeypatch, returncode=0,
        stdout='{"services": {"dev": {"image": "dev:latest"}}}')
    assert container._resolve_dev_service() == {'image': 'dev:latest'}
    assert calls == [['docker', 'compose', 'config', '--format', 'json']]


def test_resolve_dev_service_empty_when_service_missing(monkeypatch):
    from devbase.commands import container
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    _fake_compose_config(monkeypatch, returncode=0, stdout='{"services": {}}')
    assert container._resolve_dev_service() == {}


def test_read_compose_services_returns_rc_and_empty_on_failure(monkeypatch):
    from devbase.commands import container
    _fake_compose_config(monkeypatch, returncode=3, stdout='')
    assert container._read_compose_services() == (3, {})


def test_read_compose_services_returns_services(monkeypatch):
    from devbase.commands import container
    calls = _fake_compose_config(
        monkeypatch, returncode=0,
        stdout='{"services": {"dev": {"image": "dev:latest"}}}')
    assert container._read_compose_services() == (0, {'dev': {'image': 'dev:latest'}})
    assert calls == [['docker', 'compose', 'config', '--format', 'json']]


def test_read_compose_services_raises_on_invalid_json(monkeypatch):
    """不正な JSON は握らず伝播する (_ensure_images の except 経路が受ける)。"""
    import json as _json
    from devbase.commands import container
    _fake_compose_config(monkeypatch, returncode=0, stdout='not json')
    with pytest.raises(_json.JSONDecodeError):
        container._read_compose_services()


# ---------------------------------------------------------------------------
# wrapper routing
# ---------------------------------------------------------------------------

def test_wrapper_routes_rebuild_to_python():
    wrapper = (Path(__file__).resolve().parents[2] / 'bin' / 'devbase').read_text()
    lines = wrapper.splitlines()
    # rebuild は Python 実装。run_python 委譲ケースの case ラベル行 (直後行が
    # run_python "${_resolved_cmd}") に rebuild が含まれること。
    found = False
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ''
        if ln.strip().endswith(')') and 'run_python "${_resolved_cmd}"' in nxt \
           and 'rebuild' in ln:
            found = True
            break
    assert found, 'rebuild は wrapper の run_python ケースに含まれる必要がある'
    # shell の build) ケースに rebuild が混ざっていないこと
    for ln in lines:
        if ln.strip().startswith('build)') and 'cmd_build' in ln:
            assert 'rebuild' not in ln


def test_wrapper_rebuild_in_name_resolvable():
    wrapper = (Path(__file__).resolve().parents[2] / 'bin' / 'devbase').read_text()
    assert '_NAME_RESOLVABLE_SHORTCUTS=" up down ps scale login build rebuild "' in wrapper
    assert '_PROJECT_NAME_SUBCOMMANDS=" up down ps logs scale rebuild "' in wrapper
