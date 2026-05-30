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
    # name 解決 (chdir) は別テストで検証するためここでは no-op 化し、伝播のみ見る。
    monkeypatch.setattr(container, '_resolve_project_name', lambda name: True)
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
# _dispatch_lifecycle: name 解決 (PR2 で wrapper cd の Python フォールバックを実装)
# name 指定時は handler 呼び出し前に _resolve_project_name で chdir する。
# 解決失敗時は handler を呼ばずに 1 を返す。詳細な解決ロジックは
# test_project_name_resolution.py を参照。
# ---------------------------------------------------------------------------

def test_lifecycle_resolves_name_before_handler(monkeypatch):
    """name 指定時は handler 前に _resolve_project_name を呼ぶ。"""
    from devbase.commands import container
    order = []
    monkeypatch.setattr(container, '_resolve_project_name',
                        lambda name: order.append(('resolve', name)) or True)
    monkeypatch.setattr(container, 'cmd_up',
                        lambda project_name=None, scale=None:
                        order.append(('up', project_name)) or 0)
    args = _args(subcommand='up', name='carmo', scale=None)
    assert container._dispatch_lifecycle(args) == 0
    assert order == [('resolve', 'carmo'), ('up', 'carmo')]


def test_lifecycle_aborts_when_name_unresolved(monkeypatch):
    """name 解決に失敗したら handler を呼ばず 1 を返す。"""
    from devbase.commands import container
    called = []
    monkeypatch.setattr(container, '_resolve_project_name', lambda name: False)
    monkeypatch.setattr(container, 'cmd_up',
                        lambda project_name=None, scale=None:
                        called.append('up') or 0)
    args = _args(subcommand='up', name='bogus', scale=None)
    assert container._dispatch_lifecycle(args) == 1
    assert called == [], '解決失敗時は handler を呼んではならない'


def test_lifecycle_no_resolution_without_name(monkeypatch):
    """name 未指定なら _resolve_project_name を呼ばない。"""
    from devbase.commands import container
    resolved = []
    monkeypatch.setattr(container, '_resolve_project_name',
                        lambda name: resolved.append(name) or True)
    monkeypatch.setattr(container, 'cmd_up', lambda project_name=None, scale=None: 0)
    args = _args(subcommand='up', scale=None)  # name 属性なし
    assert container._dispatch_lifecycle(args) == 0
    assert resolved == []


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


# ---------------------------------------------------------------------------
# parser: 共通サブコマンド (login / build) の project / container 一致
# (重複定義を _add_login_subparser / _add_build_subparser に共通化した結果の検証)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('group', ['project', 'container'])
def test_login_positional_is_index_in_both_groups(group):
    """login は project / container いずれでも単一 positional を index として扱う。"""
    parser = cli._create_parser()
    args = parser.parse_args([group, 'login', '2'])
    assert args.subcommand == 'login'
    assert args.index == '2'
    # name positional は存在しない (曖昧さ回避)
    assert not hasattr(args, 'name')


@pytest.mark.parametrize('group', ['project', 'container'])
def test_login_index_defaults_in_both_groups(group):
    parser = cli._create_parser()
    args = parser.parse_args([group, 'login'])
    assert args.index == '1'


@pytest.mark.parametrize('group', ['project', 'container'])
def test_build_positional_is_image_in_both_groups(group):
    """build は project / container いずれでも単一 positional を image として扱う。"""
    parser = cli._create_parser()
    args = parser.parse_args([group, 'build', 'web'])
    assert args.subcommand == 'build'
    assert args.image == 'web'
    assert not hasattr(args, 'name')


# ---------------------------------------------------------------------------
# top-level ショートカットの [name] 受理と伝播
# (up/down/ps/scale が project サブコマンドと同様に [name] を受理し、
#  ショートカット経由でも name が _dispatch_lifecycle まで伝播する)
# ---------------------------------------------------------------------------

def test_shortcut_up_accepts_optional_name():
    parser = cli._create_parser()
    with_name = parser.parse_args(['up', 'carmo'])
    assert with_name.command == 'up'
    assert with_name.name == 'carmo'

    without_name = parser.parse_args(['up'])
    assert without_name.name is None


def test_shortcut_down_accepts_optional_name():
    parser = cli._create_parser()
    args = parser.parse_args(['down', 'carmo'])
    assert args.command == 'down'
    assert args.name == 'carmo'


def test_shortcut_ps_accepts_optional_name():
    parser = cli._create_parser()
    args = parser.parse_args(['ps', 'carmo', '--all'])
    assert args.command == 'ps'
    assert args.name == 'carmo'
    assert args.all is True


def test_shortcut_scale_positional_is_unambiguous():
    """`scale [name] <new_scale>` は project scale と同じく曖昧にならない。"""
    parser = cli._create_parser()

    only_scale = parser.parse_args(['scale', '3'])
    assert only_scale.name is None
    assert only_scale.new_scale == 3

    name_and_scale = parser.parse_args(['scale', 'carmo', '3'])
    assert name_and_scale.name == 'carmo'
    assert name_and_scale.new_scale == 3


def test_shortcut_up_propagates_name_through_dispatch(monkeypatch):
    """`devbase up <name>` の name がショートカット経由で cmd_up まで伝播する。"""
    from devbase.commands import container
    captured = {}
    monkeypatch.setattr(container, '_resolve_project_name', lambda name: True)
    monkeypatch.setattr(container, 'cmd_up',
                        lambda project_name=None, scale=None:
                        captured.update(project_name=project_name) or 0)
    # ショートカット parser が生成する namespace を再現 (name 属性を持つ)
    args = _args(command='up', name='carmo', scale=None)
    assert cli._dispatch('up', args) == 0
    assert captured['project_name'] == 'carmo'


def test_shortcut_scale_propagates_name_through_dispatch(monkeypatch):
    """`devbase scale <name> N` の name がショートカット経由で cmd_scale まで伝播する。"""
    from devbase.commands import container
    captured = {}
    monkeypatch.setattr(container, '_resolve_project_name', lambda name: True)
    monkeypatch.setattr(container, 'cmd_scale',
                        lambda new_scale=None, project_name=None:
                        captured.update(project_name=project_name, new_scale=new_scale) or 0)
    args = _args(command='scale', name='carmo', new_scale=3)
    assert cli._dispatch('scale', args) == 0
    assert captured['project_name'] == 'carmo'
    assert captured['new_scale'] == 3
