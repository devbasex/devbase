"""i30: `devbase rebuild` (docker compose build --no-cache 相当) のテスト。

- parser: `project rebuild [name]` / `container rebuild` / top-level `rebuild [name]`
- SHORTCUTS / SUBCMD_MAP への登録
- `_dispatch_lifecycle` が rebuild を cmd_rebuild へ振り分ける
- cmd_rebuild の振る舞い (compose.yml 不在=1 / 存在時に docker compose build --no-cache)
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


def test_cmd_rebuild_runs_no_cache_build(tmp_path, monkeypatch):
    from devbase.commands import container
    (tmp_path / 'compose.yml').write_text('services: {}\n')
    monkeypatch.chdir(tmp_path)

    captured = {}

    def fake_run(cmd, check=False):
        captured['cmd'] = cmd
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(container.subprocess, 'run', fake_run)
    assert container.cmd_rebuild() == 0
    assert captured['cmd'] == ['docker', 'compose', 'build', '--no-cache']


def test_cmd_rebuild_propagates_returncode(tmp_path, monkeypatch):
    from devbase.commands import container
    (tmp_path / 'compose.yml').write_text('services: {}\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(container.subprocess, 'run',
                        lambda cmd, check=False: types.SimpleNamespace(returncode=2))
    assert container.cmd_rebuild() == 2


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
