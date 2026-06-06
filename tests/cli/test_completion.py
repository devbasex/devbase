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


@pytest.fixture
def fake_root_with_bad_links(tmp_path):
    """壊れた symlink / ファイルへの symlink を含む projects/ を作る。

    ディレクトリ / ディレクトリへの symlink のみが補完候補に出ることを検証するため。
    """
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "web").mkdir()
    # ディレクトリへの symlink (候補に出るべき)
    (tmp_path / "target").mkdir()
    (projects / "linked").symlink_to(tmp_path / "target")
    # ファイルへの symlink (候補に出ない)
    a_file = tmp_path / "afile"
    a_file.write_text("x")
    (projects / "file_link").symlink_to(a_file)
    # 壊れた symlink (候補に出ない)
    (projects / "broken_link").symlink_to(tmp_path / "does_not_exist")
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


def test_bash_top_level_ps_name_completion(fake_root):
    """`devbase ps <TAB>` (フラグなし) はプロジェクト名を補完する。"""
    out = _bash_complete("devbase ps ''", 2, fake_root)
    assert sorted(out) == ["api", "linked", "web"]


def test_bash_top_level_ps_flag_completion(fake_root):
    """`devbase ps -<TAB>` は -a / --all を補完する (project ps と対称)。"""
    out = _bash_complete("devbase ps '-'", 2, fake_root)
    assert set(out) == {"--all", "-a"}


def test_bash_project_name_excludes_bad_symlinks(fake_root_with_bad_links):
    """壊れた symlink / ファイルへの symlink は name 補完候補に出ない。

    実ディレクトリ (web) とディレクトリへの symlink (linked) のみ。zsh 側と整合。
    """
    out = _bash_complete("devbase project up ''", 3, fake_root_with_bad_links)
    assert sorted(out) == ["linked", "web"]


def test_bash_project_ps_flag_after_name(fake_root):
    """`devbase project ps web -<TAB>` (cword 4) で -a / --all を補完する。"""
    out = _bash_complete("devbase project ps web '-'", 4, fake_root)
    assert set(out) == {"--all", "-a"}


def test_bash_project_logs_flag_after_name(fake_root):
    """`devbase project logs web -<TAB>` (cword 4) で -f/--follow/--tail を補完する。"""
    out = _bash_complete("devbase project logs web '-'", 4, fake_root)
    assert set(out) == {"--follow", "-f", "--tail"}


def test_bash_top_level_ps_flag_after_name(fake_root):
    """`devbase ps web -<TAB>` (cword 3) で -a / --all を補完する (project ps と対称)。"""
    out = _bash_complete("devbase ps web '-'", 3, fake_root)
    assert set(out) == {"--all", "-a"}


def test_bash_project_list_flags(fake_root):
    out = _bash_complete("devbase project list '-'", 3, fake_root)
    assert set(out) == {"--no-interactive", "--plain", "-P", "--interactive", "-i"}


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
