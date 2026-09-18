"""`devbase open` の CLI 登録と入口のシェル (PLAN59 / #197)

parser・ショートカット・前方一致・`bin/devbase` の name 解決の 4 か所に `open` が揃っていることを
固定する。いずれかが欠けると、`devbase open` が unknown command になるか、`project open <name>` の
name が Python へ渡って別の意味に読まれる。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from devbase import cli

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "bin" / "devbase"


def _parse(*argv):
    return cli._create_parser().parse_args(list(argv))


# --- parser ------------------------------------------------------------------

@pytest.mark.parametrize('argv', [['open'], ['project', 'open']])
def test_open_takes_name_index_and_context(argv):
    ns = _parse(*argv, 'carmo', '--open-index', '2', '--context', 'remote')
    assert ns.name == 'carmo'
    assert ns.open_index == 2
    assert ns.context == 'remote'


def test_container_open_takes_no_name():
    ns = _parse('container', 'open', '--open-index', '2')
    assert ns.subcommand == 'open'
    assert ns.open_index == 2
    assert not hasattr(ns, 'name')
    with pytest.raises(SystemExit):
        _parse('container', 'open', 'carmo')


@pytest.mark.parametrize('flag', ['--open', '--no-open'])
@pytest.mark.parametrize('argv', [['open'], ['project', 'open'], ['container', 'open']])
def test_open_rejects_the_auto_open_flags(argv, flag, capsys):
    """受け入れ条件 11"""
    with pytest.raises(SystemExit) as e:
        _parse(*argv, flag)
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
    ns = _parse('open', 'carmo', '--open-index', '2')
    cli._dispatch('open', ns)
    assert seen['entered'] == 'carmo'
    assert seen['open'] == {'project_name': 'carmo', 'open_index': 2}


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

def _run_wrapper(args, devbase_root):
    harness = (
        'run_python() { echo "PWD:$PWD"; echo "PYTHON:$*"; exit 0; }\n'
        'cmd_build() { echo "BUILD:$*"; exit 0; }\n'
        'ensure_uv() { :; }\n'
        'eval "$(sed -e \'/^run_python()/,/^}/d\' '
        '            -e \'/^ensure_uv()/,/^}/d\' '
        '            -e \'/^cmd_build()/,/^}/d\' '
        '            -e \'/^DEVBASE_ROOT=/d\' "$WRAPPER_PATH")"\n'
    )
    env = {**os.environ, "DEVBASE_ROOT": str(devbase_root), "WRAPPER_PATH": str(WRAPPER)}
    return subprocess.run(["bash", "-c", harness, "devbase", *args],
                          capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))


def _field(result, prefix):
    for line in result.stdout.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


@pytest.fixture
def wrapper_root(tmp_path):
    (tmp_path / "projects" / "carmo").mkdir(parents=True)
    return tmp_path


def test_wrapper_top_level_open_name_cds_and_strips(wrapper_root):
    r = _run_wrapper(["open", "carmo", "--open-index", "2"], wrapper_root)
    assert "unknown command" not in r.stderr.lower(), r.stderr
    assert _field(r, "PWD:").endswith("/projects/carmo"), r.stdout
    assert _field(r, "PYTHON:") == "open --open-index 2", r.stdout


def test_wrapper_project_open_name_cds_and_strips(wrapper_root):
    r = _run_wrapper(["project", "open", "carmo"], wrapper_root)
    assert _field(r, "PWD:").endswith("/projects/carmo"), r.stdout
    assert _field(r, "PYTHON:") == "project open", r.stdout


def test_wrapper_open_prefix_resolves(wrapper_root):
    r = _run_wrapper(["o"], wrapper_root)
    assert _field(r, "PYTHON:") == "open", (r.stdout, r.stderr)
