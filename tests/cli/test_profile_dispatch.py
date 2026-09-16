"""PLAN58 決定 6: `project profile` / `container profile` の引数の受け口

`project` は既存の `scale` と同じ `[name] <値>` の並びでプロジェクト名を受ける。
`container` / `ct` は現在地で動く既存の規約に従い、プロジェクト名を受けない。
入れ子の subparser は `dest='profile_subcommand'` を使う。`subcommand` を再利用すると
`devbase project profile list` が `project list` (プロジェクト一覧) へ流れる。
"""

from __future__ import annotations

import logging
import sys

import pytest

from devbase import cli
from devbase.commands import container


def parse(*argv):
    return cli._create_parser().parse_args(list(argv))


@pytest.fixture
def recorded(monkeypatch):
    """profile の 3 つの入口と名前解決を記録に差し替える。"""
    calls = []
    monkeypatch.setattr(container, '_resolve_project_name',
                        lambda name: calls.append(('resolve', name)) or True)
    monkeypatch.setattr(container, '_inject_secrets', lambda *, required: None)
    for op in ('up', 'down'):
        monkeypatch.setattr(container, f'cmd_profile_{op}',
                            lambda profile, op=op, **kw: calls.append((op, profile, kw)) or 0)
    monkeypatch.setattr(container, 'cmd_profile_list',
                        lambda **kw: calls.append(('list', kw)) or 0)
    return calls


@pytest.mark.parametrize('op', ['up', 'down'])
def test_project_profile_takes_profile_only(op):
    args = parse('project', 'profile', op, 'test')
    assert (args.subcommand, args.profile_subcommand) == ('profile', op)
    assert args.name is None
    assert args.profile == 'test'


@pytest.mark.parametrize('op', ['up', 'down'])
def test_project_profile_takes_name_then_profile(op):
    args = parse('project', 'profile', op, 'carmo', 'test')
    assert args.name == 'carmo'
    assert args.profile == 'test'


def test_project_profile_list_takes_optional_name():
    assert parse('project', 'profile', 'list').name is None
    assert parse('project', 'profile', 'list', 'carmo').name == 'carmo'


@pytest.mark.parametrize('group', ['container', 'ct'])
def test_container_profile_rejects_project_name(group, capsys):
    args = parse(group, 'profile', 'up', 'test')
    assert args.profile == 'test'
    assert not hasattr(args, 'name')
    with pytest.raises(SystemExit):
        parse(group, 'profile', 'up', 'carmo', 'test')


def test_project_profile_list_is_not_project_list(monkeypatch, recorded):
    listed = []
    import devbase.commands.project as project_commands
    monkeypatch.setattr(project_commands, 'cmd_project_list',
                        lambda root, args: listed.append(args) or 0)

    assert cli._dispatch('project', parse('project', 'profile', 'list')) == 0

    assert listed == []
    assert recorded == [('list', {})]


def test_named_profile_up_resolves_project_first(recorded):
    args = parse('project', 'profile', 'up', 'carmo', 'test', '--context', 'remote')

    assert cli._dispatch('project', args) == 0

    assert recorded == [('resolve', 'carmo'), ('up', 'test', {'context': 'remote'})]


def test_profile_without_name_runs_in_current_project(recorded):
    assert cli._dispatch('project', parse('project', 'profile', 'down', 'test')) == 0

    assert recorded == [('down', 'test', {})]


@pytest.mark.parametrize('group', ['container', 'ct'])
def test_container_profile_matches_project_and_warns_once(group, recorded, caplog):
    with caplog.at_level(logging.WARNING, logger='devbase.commands.container'):
        assert cli._dispatch(group, parse(group, 'profile', 'up', 'test')) == 0

    assert recorded == [('up', 'test', {})]
    assert len([r for r in caplog.records if '非推奨' in r.message]) == 1


def test_profile_without_operation_fails(recorded):
    assert cli._dispatch('project', parse('project', 'profile')) == 1
    assert recorded == []


@pytest.mark.parametrize('group', ['project', 'container'])
def test_prefix_p_still_means_ps(monkeypatch, group):
    """`profile` を足しても、従来一意だった `p` は `ps` のまま解決する。"""
    monkeypatch.setattr(sys, 'argv', ['devbase', group, 'p'])
    cli._expand_argv()
    assert sys.argv[2] == 'ps'


def test_prefix_pr_resolves_profile(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['devbase', 'project', 'pr', 'list'])
    cli._expand_argv()
    assert sys.argv[2] == 'profile'
