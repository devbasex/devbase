"""``devbase project post-start [name]`` の登録 (#371 決定 2)。

``project`` グループだけに置き、トップレベルの短縮形と ``container`` グループには置かない。
ラッパーの ``[name]`` の解決と、bash / zsh の補完に現れる。
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
BASH_COMPLETION = REPO_ROOT / "etc" / "devbase-completion.bash"
ZSH_COMPLETION = REPO_ROOT / "etc" / "_devbase"


def _parse(*argv):
    return cli._create_parser().parse_args(list(argv))


def test_project_post_start_takes_name_and_context():
    ns = _parse('project', 'post-start', 'myapp', '--context', 'remote')
    assert ns.subcommand == 'post-start'
    assert ns.name == 'myapp'
    assert ns.context == 'remote'


def test_post_start_is_only_in_the_project_group():
    assert 'post-start' in cli.SUBCMD_MAP[('project',)]
    assert 'post-start' not in cli.SUBCMD_MAP[('container', 'ct')]
    assert 'post-start' not in cli.SHORTCUTS
    with pytest.raises(SystemExit):
        _parse('container', 'post-start')
    with pytest.raises(SystemExit):
        _parse('post-start')


def test_project_post_start_dispatches_to_cmd_post_start(monkeypatch):
    from devbase.commands import container

    seen = {}
    monkeypatch.setattr(container, '_enter_project', lambda name: seen.setdefault('entered', name))
    monkeypatch.setattr(container, 'cmd_post_start',
                        lambda **kw: seen.setdefault('post_start', kw) and 0)
    ns = _parse('project', 'post-start', 'myapp', '--context', 'remote')

    assert cli._dispatch('project', ns) == 0
    assert seen['entered'] == 'myapp'
    assert seen['post_start'] == {'project_name': 'myapp', 'context': 'remote'}


@pytest.mark.parametrize('argv, expected', [
    (['devbase', 'project', 'po'], ['devbase', 'project', 'post-start']),
    (['devbase', 'project', 'p'], ['devbase', 'project', 'ps']),
    (['devbase', 'project', 'pr'], ['devbase', 'project', 'profile']),
    (['devbase', 'project', 're'], ['devbase', 'project', 'rebuild']),
])
def test_prefix_resolution(monkeypatch, argv, expected):
    monkeypatch.setattr(sys, 'argv', list(argv))
    cli._expand_argv()
    assert sys.argv == expected


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


def test_wrapper_project_post_start_name_cds_and_strips(tmp_path):
    (tmp_path / "projects" / "myapp").mkdir(parents=True)

    r = _run_wrapper(["project", "post-start", "myapp"], tmp_path)

    lines = dict(line.split(':', 1) for line in r.stdout.splitlines() if ':' in line)
    assert lines['PWD'].endswith('/projects/myapp'), r.stdout
    assert lines['PYTHON'] == 'project post-start', r.stdout


def _bash_complete(words, cword, devbase_root):
    script = f"""
set -e
source "{BASH_COMPLETION}"
COMP_WORDS=({words})
COMP_CWORD={cword}
_devbase_completions
printf '%s\\n' "${{COMPREPLY[@]}}"
"""
    env = {**os.environ, "DEVBASE_ROOT": str(devbase_root)}
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    return [line for line in proc.stdout.splitlines() if line]


def test_bash_completion_lists_post_start_only_under_project(tmp_path):
    (tmp_path / "projects" / "web").mkdir(parents=True)

    assert "post-start" in _bash_complete("devbase project ''", 2, tmp_path)
    assert "post-start" not in _bash_complete("devbase container ''", 2, tmp_path)
    assert "post-start" not in _bash_complete("devbase ''", 1, tmp_path)
    assert _bash_complete("devbase project post-start ''", 3, tmp_path) == ["web"]
    assert _bash_complete("devbase project post-start '-'", 3, tmp_path) == ["--context"]


def test_zsh_completion_lists_post_start_once():
    text = ZSH_COMPLETION.read_text()
    assert text.count("'post-start:") == 1
    assert "                post-start)" in text
