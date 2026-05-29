"""PLAN06 Task 1: `project` サブコマンド group / 共有ハンドラ / `container` 非推奨委譲のテスト

PR1 の範囲は Python レベルのリネーム + 委譲のみ（wrapper の cd / name 解決は PR2）。
ここでは parser の構造・prefix 解決・dispatch ルーティング・非推奨 warning を検証する。
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

from devbase import cli


# ---------------------------------------------------------------------------
# parser: project サブコマンド群と [name] positional
# ---------------------------------------------------------------------------

LIFECYCLE_SUBCMDS = ['up', 'down', 'ps', 'login', 'logs', 'scale', 'build']


@pytest.mark.parametrize('sub', LIFECYCLE_SUBCMDS)
def test_create_parser_accepts_project_subcommands(sub):
    parser = cli._create_parser()
    argv = ['project', sub]
    if sub == 'scale':
        argv.append('1')  # scale は new_scale (必須) を要求する
    args = parser.parse_args(argv)
    assert args.command == 'project'
    assert args.subcommand == sub


def test_project_up_accepts_optional_name():
    parser = cli._create_parser()
    with_name = parser.parse_args(['project', 'up', 'carmo'])
    assert with_name.subcommand == 'up'
    assert with_name.name == 'carmo'

    without_name = parser.parse_args(['project', 'up'])
    assert without_name.name is None


def test_project_scale_positional_is_unambiguous():
    """`[name]` optional + `new_scale` 必須 int の組合せが曖昧にならない。"""
    parser = cli._create_parser()

    only_scale = parser.parse_args(['project', 'scale', '3'])
    assert only_scale.name is None
    assert only_scale.new_scale == 3

    name_and_scale = parser.parse_args(['project', 'scale', 'carmo', '3'])
    assert name_and_scale.name == 'carmo'
    assert name_and_scale.new_scale == 3


# ---------------------------------------------------------------------------
# prefix 解決: project を 3 箇所同期した結果の検証
# ---------------------------------------------------------------------------

def test_expand_argv_resolves_project_command_prefix(monkeypatch):
    """`devbase pr ...` は一意なので `project` に解決される。"""
    monkeypatch.setattr(sys, 'argv', ['devbase', 'pr', 'up'])
    cli._expand_argv()
    assert sys.argv[1] == 'project'


def test_expand_argv_resolves_project_subcommand_prefix(monkeypatch):
    """`devbase project u` は `up` に解決される (SUBCMD_MAP に project を追加した結果)。"""
    monkeypatch.setattr(sys, 'argv', ['devbase', 'project', 'u'])
    cli._expand_argv()
    assert sys.argv == ['devbase', 'project', 'up']


# ---------------------------------------------------------------------------
# container.py: 共有 lifecycle dispatcher と非推奨委譲
# ---------------------------------------------------------------------------

def _args(**kwargs):
    return types.SimpleNamespace(**kwargs)


def test_cmd_project_delegates_to_lifecycle(monkeypatch):
    from devbase.commands import container
    captured = {}

    def fake_lifecycle(args):
        captured['args'] = args
        return 0

    monkeypatch.setattr(container, '_dispatch_lifecycle', fake_lifecycle)
    args = _args(subcommand='ps')
    assert container.cmd_project(args) == 0
    assert captured['args'] is args


def test_cmd_container_warns_and_delegates(monkeypatch, caplog):
    from devbase.commands import container
    monkeypatch.setattr(container, '_dispatch_lifecycle', lambda args: 0)
    args = _args(subcommand='ps')
    with caplog.at_level(logging.WARNING, logger='devbase.commands.container'):
        assert container.cmd_container(args) == 0
    assert any('非推奨' in r.message for r in caplog.records), \
        '`container` は非推奨 warning を出さなければならない'


def test_cmd_project_does_not_warn(monkeypatch, caplog):
    from devbase.commands import container
    monkeypatch.setattr(container, '_dispatch_lifecycle', lambda args: 0)
    args = _args(subcommand='ps')
    with caplog.at_level(logging.WARNING, logger='devbase.commands.container'):
        container.cmd_project(args)
    assert not any('非推奨' in r.message for r in caplog.records), \
        '`project` は非推奨 warning を出してはならない'


def test_lifecycle_passes_name_to_cmd_up(monkeypatch):
    """`project up <name>` の name は project_name として up に伝播する。"""
    from devbase.commands import container
    captured = {}
    monkeypatch.setattr(container, 'cmd_up',
                        lambda project_name=None, scale=None:
                        captured.update(project_name=project_name) or 0)
    args = _args(subcommand='up', name='carmo', scale=None)
    assert container._dispatch_lifecycle(args) == 0
    assert captured['project_name'] == 'carmo'


def test_lifecycle_container_path_has_no_name(monkeypatch):
    """container 経路には name 属性が無く、従来通り project_name=None になる。"""
    from devbase.commands import container
    captured = {}
    monkeypatch.setattr(container, 'cmd_up',
                        lambda project_name=None, scale=None:
                        captured.update(project_name=project_name) or 0)
    args = _args(subcommand='up', scale=None)  # name 属性なし
    assert container._dispatch_lifecycle(args) == 0
    assert captured['project_name'] is None


# ---------------------------------------------------------------------------
# _dispatch_lifecycle: name 未実装 warning
# (PR1 では up/scale も含め全サブコマンドが CWD の compose に作用するため、
#  name 指定時はサブコマンドに関わらず警告する)
# ---------------------------------------------------------------------------

def test_lifecycle_warns_for_up_with_name(monkeypatch, caplog):
    """`project up <name>` は name 指定時に未実装 warning を出す。"""
    from devbase.commands import container
    monkeypatch.setattr(container, 'cmd_up', lambda project_name=None, scale=None: 0)
    args = _args(subcommand='up', name='carmo', scale=None)
    with caplog.at_level(logging.WARNING, logger='devbase.commands.container'):
        assert container._dispatch_lifecycle(args) == 0
    assert any('未実装' in r.message for r in caplog.records), \
        'up でも name 指定時は警告しなければならない'


def test_lifecycle_warns_for_scale_with_name(monkeypatch, caplog):
    """`project scale <name> N` も name 指定時に未実装 warning を出す。"""
    from devbase.commands import container
    monkeypatch.setattr(container, 'cmd_scale',
                        lambda new_scale=None, project_name=None: 0)
    args = _args(subcommand='scale', name='carmo', new_scale=3)
    with caplog.at_level(logging.WARNING, logger='devbase.commands.container'):
        assert container._dispatch_lifecycle(args) == 0
    assert any('未実装' in r.message for r in caplog.records), \
        'scale でも name 指定時は警告しなければならない'


def test_lifecycle_no_warning_without_name(monkeypatch, caplog):
    """name 未指定なら警告を出さない。"""
    from devbase.commands import container
    monkeypatch.setattr(container, 'cmd_up', lambda project_name=None, scale=None: 0)
    args = _args(subcommand='up', scale=None)  # name 属性なし
    with caplog.at_level(logging.WARNING, logger='devbase.commands.container'):
        assert container._dispatch_lifecycle(args) == 0
    assert not any('未実装' in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# cli._dispatch: ルーティング
# ---------------------------------------------------------------------------

def test_dispatch_project_routes_to_cmd_project(monkeypatch):
    from devbase.commands import container
    calls = []
    monkeypatch.setattr(container, 'cmd_project', lambda args: calls.append('project') or 0)
    args = _args(command='project', subcommand='ps')
    assert cli._dispatch('project', args) == 0
    assert calls == ['project']


def test_dispatch_container_routes_to_cmd_container(monkeypatch):
    from devbase.commands import container
    calls = []
    monkeypatch.setattr(container, 'cmd_container', lambda args: calls.append('container') or 0)
    args = _args(command='container', subcommand='ps')
    assert cli._dispatch('container', args) == 0
    assert calls == ['container']


def test_dispatch_shortcut_routes_to_cmd_project_not_container(monkeypatch):
    """トップレベルショートカット (up 等) は非推奨の container ではなく project へ。"""
    from devbase.commands import container
    calls = []
    monkeypatch.setattr(container, 'cmd_project', lambda args: calls.append('project') or 0)
    monkeypatch.setattr(container, 'cmd_container', lambda args: calls.append('container') or 0)
    args = _args(command='up')
    assert cli._dispatch('up', args) == 0
    assert calls == ['project']
