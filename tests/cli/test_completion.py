"""PLAN06 Task 4: シェル補完 (bash / zsh) の回帰テスト。

- bash 補完を実際に source して `project` サブコマンド補完 / プロジェクト名補完 /
  トップレベルシノニム補完が機能することを検証する (test_wrapper_dispatch と同方式)。
- zsh 補完はランナー非依存にするため静的内容チェックのみ。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASH_COMPLETION = REPO_ROOT / "etc" / "devbase-completion.bash"
ZSH_COMPLETION = REPO_ROOT / "etc" / "_devbase"


def _bash_complete(words, cword, devbase_root):
    """bash 補完を source して COMPREPLY を改行区切りで返す。"""
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


@pytest.fixture
def fake_root(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "web").mkdir()
    (projects / "api").mkdir()
    # symlink プロジェクト
    (tmp_path / "target").mkdir()
    (projects / "linked").symlink_to(tmp_path / "target")
    return tmp_path


# ---------------------------------------------------------------------------
# bash: 構文 / 動作
# ---------------------------------------------------------------------------

def test_bash_completion_syntax_ok():
    proc = subprocess.run(["bash", "-n", str(BASH_COMPLETION)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_bash_project_subcommands(fake_root):
    out = _bash_complete("devbase project ''", 2, fake_root)
    assert set(out) >= {"up", "down", "ps", "login", "logs", "scale", "build", "list"}


def test_bash_project_name_completion(fake_root):
    out = _bash_complete("devbase project up ''", 3, fake_root)
    assert sorted(out) == ["api", "linked", "web"]


def test_bash_top_level_synonym_name_completion(fake_root):
    """`devbase up <TAB>` がプロジェクト名を補完する。"""
    out = _bash_complete("devbase up ''", 2, fake_root)
    assert sorted(out) == ["api", "linked", "web"]


def test_bash_project_list_flags(fake_root):
    out = _bash_complete("devbase project list '-'", 3, fake_root)
    assert set(out) == {"--interactive", "-i"}


def test_bash_top_level_commands_include_project_and_list(fake_root):
    out = _bash_complete("devbase ''", 1, fake_root)
    assert "project" in out
    assert "list" in out
    # 後方互換: container も補完候補に残る
    assert "container" in out


# ---------------------------------------------------------------------------
# 静的内容チェック (zsh は実行環境非依存にするため内容のみ確認)
# ---------------------------------------------------------------------------

def test_zsh_completion_mentions_project_and_list():
    text = ZSH_COMPLETION.read_text()
    assert "'project:Manage projects" in text
    assert "_devbase_project_names" in text
    assert "list:List projects" in text


def test_zsh_completion_marks_container_deprecated():
    text = ZSH_COMPLETION.read_text()
    assert "deprecated" in text.lower()


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh 未インストール")
def test_zsh_completion_syntax_ok():
    proc = subprocess.run(["zsh", "-n", str(ZSH_COMPLETION)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
