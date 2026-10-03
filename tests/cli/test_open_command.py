"""`devbase open` の CLI 登録と入口のシェル (PLAN59 / #197)

parser・ショートカット・前方一致・`bin/devbase` の name 解決の 4 か所に `open` が揃っていることを
固定する。いずれかが欠けると、`devbase open` が unknown command になるか、`project open <name>` の
name が Python へ渡って別の意味に読まれる。
"""

from __future__ import annotations

import sys

import pytest

from devbase import cli
from tests.cli.conftest import python_args, stdout_field


def _parse(*argv):
    return cli._create_parser().parse_args(list(argv))


# --- parser ------------------------------------------------------------------

@pytest.mark.parametrize('argv', [['open'], ['project', 'open']])
def test_open_takes_name_index_and_context(argv):
    ns = _parse(*argv, 'myapp', '--open-index', '2', '--context', 'remote')
    assert ns.name == 'myapp'
    assert ns.open_index == 2
    assert ns.context == 'remote'


def test_container_open_takes_no_name():
    ns = _parse('container', 'open', '--open-index', '2')
    assert ns.subcommand == 'open'
    assert ns.open_index == 2
    assert not hasattr(ns, 'name')
    with pytest.raises(SystemExit):
        _parse('container', 'open', 'myapp')


@pytest.mark.parametrize('flag', ['--open', '--no-open'])
@pytest.mark.parametrize('argv', [['open'], ['project', 'open'], ['container', 'open']])
def test_open_rejects_the_auto_open_flags(argv, flag, capsys):
    """受け入れ条件 11"""
    with pytest.raises(SystemExit) as e:
        _parse(*argv, flag)
    assert e.value.code == 2


@pytest.mark.parametrize('args', [('--open', '2'), ('--open=2',), ('--open-i', '2')])
@pytest.mark.parametrize('argv', [['open'], ['project', 'open'], ['container', 'open']])
def test_open_rejects_abbreviations_of_open_index(argv, args, capsys):
    """受け入れ条件 11: 前方一致で --open 2 が --open-index 2 と解釈されない"""
    with pytest.raises(SystemExit) as e:
        _parse(*argv, *args)
    assert e.value.code == 2


def test_open_is_a_shortcut_to_project_open():
    assert cli.SHORTCUTS['open'] == 'open'
    assert 'project open' in (cli._create_parser().epilog or '')


def test_open_is_in_the_subcommand_maps():
    assert 'open' in cli.SUBCMD_MAP[('project',)]
    assert 'open' in cli.SUBCMD_MAP[('container', 'ct')]


def test_shortcut_dispatches_to_cmd_open(monkeypatch):
    """受け入れ条件 9: トップレベルの name は project open と同じく下流へ渡る"""
    from devbase.commands import container

    seen = {}
    monkeypatch.setattr(container, '_enter_project', lambda name: seen.setdefault('entered', name))
    monkeypatch.setattr(container, 'cmd_open',
                        lambda **kw: seen.setdefault('open', kw) and 0)
    ns = _parse('open', 'myapp', '--open-index', '2')
    cli._dispatch('open', ns)
    assert seen['entered'] == 'myapp'
    assert seen['open'] == {'project_name': 'myapp', 'open_index': 2}


def test_project_open_dispatches_to_cmd_open(monkeypatch):
    """現状固定: 推奨の project open も対象名と番号を cmd_open に渡す。"""
    from devbase.commands import container

    seen = {}
    monkeypatch.setattr(container, '_enter_project', lambda name: seen.setdefault('entered', name))
    monkeypatch.setattr(container, 'cmd_open',
                        lambda **kw: seen.setdefault('open', kw) and 0)
    ns = _parse('project', 'open', 'myapp', '--open-index', '2')

    assert cli._dispatch('project', ns) == 0
    assert seen['entered'] == 'myapp'
    assert seen['open'] == {'project_name': 'myapp', 'open_index': 2}


# --- 前方一致 (受け入れ条件 16) -------------------------------------------------

@pytest.mark.parametrize('argv, expected', [
    (['devbase', 'o'], ['devbase', 'open']),
    (['devbase', 'op'], ['devbase', 'open']),
    (['devbase', 'l'], ['devbase', 'login']),
    (['devbase', 'project', 'o'], ['devbase', 'project', 'open']),
    (['devbase', 'project', 'p'], ['devbase', 'project', 'ps']),
    (['devbase', 'container', 'o'], ['devbase', 'container', 'open']),
])
def test_prefix_resolution(monkeypatch, argv, expected):
    monkeypatch.setattr(sys, 'argv', list(argv))
    cli._expand_argv()
    assert sys.argv == expected


# --- bin/devbase -------------------------------------------------------------

@pytest.fixture
def wrapper_root(exec_wrapper):
    """`exec_wrapper` (conftest.py) で本物の bin/devbase を tmp から起動する。`uv` だけを差し替える。"""
    exec_wrapper.project("myapp")
    return exec_wrapper


def test_wrapper_top_level_open_name_cds_and_strips(wrapper_root):
    r = wrapper_root(["open", "myapp", "--open-index", "2"])
    assert "unknown command" not in r.stderr.lower(), r.stderr
    assert stdout_field(r, "PWD:").endswith("/projects/myapp"), r.stdout
    assert python_args(r) == "open --open-index 2", r.stdout


def test_wrapper_project_open_name_cds_and_strips(wrapper_root):
    r = wrapper_root(["project", "open", "myapp"])
    assert stdout_field(r, "PWD:").endswith("/projects/myapp"), r.stdout
    assert python_args(r) == "project open", r.stdout


def test_wrapper_open_prefix_resolves(wrapper_root):
    r = wrapper_root(["o"])
    assert python_args(r) == "open", (r.stdout, r.stderr)
