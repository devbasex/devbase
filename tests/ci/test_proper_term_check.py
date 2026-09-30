"""固有の語の検査のスクリプト (#353 I1〜I8・AC1〜AC6・AC8・AC10)。

一時ディレクトリの git リポジトリと架空の語で打つ。スクリプトは環境を組み直した別のプロセスとして起動し、
手元の既定の置き場の一覧や、runner の GITHUB_ACTIONS・GITHUB_STEP_SUMMARY を継承しない。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "proper_term_check.py"

TERM = "zzfixtureword"
OTHER = "qqmockname"
# 当たった行の本文に含める目印。出力に出てはいけない
BODY_MARK = "bodymarkerxyz"


def _load():
    spec = importlib.util.spec_from_file_location("proper_term_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # dataclass はモジュールを sys.modules から引くため、読み込む前に登録する
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ptc = _load()


@dataclass
class Run:
    code: int
    stdout: str
    stderr: str
    summary: str | None

    @property
    def text(self) -> str:
        return self.stdout + self.stderr + (self.summary or "")


class Env:
    """テストごとの隔離した環境。HOME と XDG_CONFIG_HOME は一時ディレクトリへ向ける。"""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.home = tmp / "home"
        self.config = tmp / "xdg"
        self.home.mkdir()
        self.config.mkdir()
        self.repo = tmp / "repo"
        self.repo.mkdir()
        self._git("init", "-q")

    def _git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.repo, env=self.base_env(), check=True, capture_output=True)

    def base_env(self) -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }

    def write(self, rel: str, content: str | bytes, track: bool = True) -> Path:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        if track:
            self._git("add", "-f", rel)
        return path

    def default_list(self, content: str | bytes) -> Path:
        path = self.config / "devbase" / "proper-terms.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def run(self, *args: str, env: dict[str, str] | None = None, actions: bool = False, cwd: Path | None = None) -> Run:
        full = self.base_env()
        full.update(env or {})
        summary = self.tmp / "summary.md"
        if actions:
            full["GITHUB_ACTIONS"] = "true"
            full["GITHUB_STEP_SUMMARY"] = str(summary)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=cwd or self.repo,
            env=full,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        text = None
        if actions:
            assert summary.exists(), "サマリーを書いていない"
            text = summary.read_text(encoding="utf-8")
        return Run(proc.returncode, proc.stdout, proc.stderr, text)


@pytest.fixture
def env(tmp_path) -> Env:
    return Env(tmp_path)


def _terms_file(env: Env, content: str | bytes) -> Path:
    path = env.tmp / "terms.txt"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _assert_no_leak(run: Run, *words: str) -> None:
    folded = run.text.casefold()
    for word in (*words, BODY_MARK):
        assert word.casefold() not in folded, f"出力に {word} が出た"


# --- AC1・AC2・I3 -------------------------------------------------------


def test_hit_prints_path_and_line_and_exits_1(env):
    """AC1"""
    env.write("docs/a.md", f"first\nsecond {TERM} {BODY_MARK}\nthird\n")
    env.write("b.txt", "nothing\n")
    run = env.run("--terms", str(_terms_file(env, f"{TERM}\n")))
    assert run.code == 1
    lines = run.stdout.splitlines()
    assert lines[0] == "docs/a.md:2"
    assert "当たり 1 件（1 ファイル）" in lines[-1]
    _assert_no_leak(run, TERM)


def test_no_hit_exits_0(env):
    """AC2"""
    env.write("a.txt", "clean\n")
    run = env.run("--terms", str(_terms_file(env, f"{TERM}\n")))
    assert run.code == 0
    assert "当たり 0 件（0 ファイル）" in run.stdout
    assert "語 1 語・対象 1 ファイル・外したファイル 0 件" in run.stdout


def test_hits_are_sorted_by_path_and_line(env):
    env.write("z.txt", f"{TERM}\n")
    env.write("a.txt", f"x\n{OTHER}\n{TERM}\n")
    run = env.run("--terms", str(_terms_file(env, f"{TERM}\n{OTHER}\n")))
    assert run.code == 1
    assert run.stdout.splitlines()[:3] == ["a.txt:2", "a.txt:3", "z.txt:1"]
    assert "当たり 3 件（2 ファイル）" in run.stdout


def test_crlf_line_numbers(env):
    env.write("a.txt", f"x\r\ny\r\n{TERM}\r\n")
    run = env.run("--terms", str(_terms_file(env, TERM)))
    assert run.stdout.splitlines()[0] == "a.txt:3"


# --- AC3・I5 ------------------------------------------------------------


def test_case_insensitive(env):
    """AC3"""
    env.write("a.txt", "see ZzFixtureWord here\n")
    run = env.run("--terms", str(_terms_file(env, f"{TERM}\n")))
    assert run.code == 1
    assert run.stdout.splitlines()[0] == "a.txt:1"


def test_list_case_is_ignored_too(env):
    env.write("a.txt", f"{TERM}\n")
    run = env.run("--terms", str(_terms_file(env, "ZZFIXTUREWORD\n")))
    assert run.code == 1


def test_term_is_not_a_regex(env):
    """I5: `.` を含む語は `.` の位置に別の文字がある行に当たらない。"""
    env.write("a.txt", "zzfixtureXword\n")
    run = env.run("--terms", str(_terms_file(env, "zzfixture.word\n")))
    assert run.code == 0
    env.write("b.txt", "zzfixture.word\n")
    assert env.run("--terms", str(env.tmp / "terms.txt")).code == 1


def test_list_lines_are_stripped_and_comments_skipped(env):
    env.write("a.txt", f"{OTHER}\n")
    run = env.run("--terms", str(_terms_file(env, f"﻿# {OTHER}\n\n   {TERM}  \n")))
    assert run.code == 0
    assert "語 1 語" in run.stdout


# --- AC4・AC10・I2 -------------------------------------------------------


def test_skips_when_no_source(env):
    """AC4: 出所が無い。"""
    env.write("a.txt", f"{TERM}\n")
    run = env.run()
    assert run.code == 0
    assert "語の一覧が無いため飛ばした" in run.stdout
    assert str(env.config / "devbase" / "proper-terms.txt") in run.stdout
    assert "当たり" not in run.stdout


def test_skips_when_env_is_empty_string(env):
    """AC10: フォーク・未登録の secret は空の文字列に展開される。"""
    env.write("a.txt", f"{TERM}\n")
    run = env.run(env={"PROPER_TERMS": ""}, actions=True)
    assert run.code == 0
    assert "語の一覧が無いため飛ばした" in run.stdout
    assert "::notice::" in run.stdout
    assert "語の一覧が無いため飛ばした" in run.summary


@pytest.mark.parametrize("content", ["", "\n\n", "# comment\n\n#another\n", "   \n#x\n"])
def test_skips_when_list_is_empty(env, content):
    """AC4: 空・空行と `#` の行だけ。"""
    env.write("a.txt", f"{TERM}\n")
    run = env.run("--terms", str(_terms_file(env, content)))
    assert run.code == 0
    assert "語の一覧が無いため飛ばした" in run.stdout
    assert "当たり" not in run.stdout


def test_skips_when_default_list_is_empty(env):
    env.write("a.txt", f"{TERM}\n")
    env.default_list("# nothing\n")
    run = env.run()
    assert run.code == 0
    assert "語の一覧が無いため飛ばした" in run.stdout


def test_default_path_falls_back_to_home_config(env):
    env.write("a.txt", f"{TERM}\n")
    path = env.home / ".config" / "devbase" / "proper-terms.txt"
    path.parent.mkdir(parents=True)
    path.write_text(TERM, encoding="utf-8")
    run = env.run(env={"XDG_CONFIG_HOME": ""})
    assert run.code == 1


# --- 出所の順（決定 4） ---------------------------------------------------


def test_terms_option_wins(env):
    env.write("a.txt", "only_opt_zz\n")
    env.write("b.txt", "only_env_zz\n")
    env.write("c.txt", "only_default_zz\n")
    env.default_list("only_default_zz\n")
    run = env.run("--terms", str(_terms_file(env, "only_opt_zz\n")), env={"PROPER_TERMS": "only_env_zz"})
    assert run.stdout.splitlines()[0] == "a.txt:1"
    assert "当たり 1 件" in run.stdout


def test_env_wins_over_default(env):
    env.write("b.txt", "only_env_zz\n")
    env.write("c.txt", "only_default_zz\n")
    env.default_list("only_default_zz\n")
    run = env.run(env={"PROPER_TERMS": "only_env_zz\n"})
    assert run.stdout.splitlines()[0] == "b.txt:1"
    assert "当たり 1 件" in run.stdout


def test_default_used_when_env_empty(env):
    env.write("b.txt", "only_env_zz\n")
    env.write("c.txt", "only_default_zz\n")
    env.default_list("only_default_zz\n")
    run = env.run(env={"PROPER_TERMS": ""})
    assert run.stdout.splitlines()[0] == "c.txt:1"
    assert "当たり 1 件" in run.stdout


# --- AC5・I6 ------------------------------------------------------------


def test_untracked_and_ignored_files_are_not_scanned(env):
    """AC5"""
    env.write(".gitignore", "ignored.txt\n")
    env.write("ignored.txt", f"{TERM}\n", track=False)
    env.write("untracked.txt", f"{TERM}\n", track=False)
    env.write("a.txt", "clean\n")
    run = env.run("--terms", str(_terms_file(env, TERM)))
    assert run.code == 0
    assert "対象 2 ファイル" in run.stdout


def test_runs_from_subdirectory(env):
    env.write("sub/a.txt", f"{TERM}\n")
    env.write("b.txt", f"{TERM}\n")
    run = env.run("--terms", str(_terms_file(env, TERM)), cwd=env.repo / "sub")
    assert run.stdout.splitlines()[:2] == ["b.txt:1", "sub/a.txt:1"]


# --- AC6・I7 ------------------------------------------------------------


def test_exception_rules_are_the_two():
    assert {r.path for r in ptc.EXCEPTION_RULES} == {"LICENSE", ".ndf/mvv.json"}


def test_exceptions_cover_only_their_lines(env):
    """AC6"""
    env.write("LICENSE", f"MIT License\n\nCopyright (c) 2026 {TERM}\n\nby {TERM}\n")
    env.write(".ndf/mvv.json", f'{{\n  "approved_by": "{TERM}",\n  "note": "{TERM}"\n}}\n')
    run = env.run("--terms", str(_terms_file(env, TERM)))
    assert run.code == 1
    assert run.stdout.splitlines()[:2] == [".ndf/mvv.json:3", "LICENSE:5"]
    assert "当たり 2 件" in run.stdout


def test_exception_shapes_do_not_apply_to_other_paths(env):
    """I7: 別のパスの同じ形の行には効かない。"""
    env.write("docs/LICENSE", f"Copyright (c) 2026 {TERM}\n")
    env.write("mvv.json", f'  "approved_by": "{TERM}"\n')
    run = env.run("--terms", str(_terms_file(env, TERM)))
    assert run.stdout.splitlines()[:2] == ["docs/LICENSE:1", "mvv.json:1"]


# --- I8 ----------------------------------------------------------------


def test_binary_and_symlink_are_excluded(env):
    env.write("bin.dat", b"\xff\xfe" + TERM.encode() + b"\x00\x80")
    (env.repo / "real.txt").write_text("clean\n", encoding="utf-8")
    (env.repo / "link.txt").symlink_to(env.tmp / "outside.txt")
    (env.tmp / "outside.txt").write_text(f"{TERM}\n", encoding="utf-8")
    env._git("add", "real.txt", "link.txt")
    env.write("gone.txt", f"{TERM}\n").unlink()
    run = env.run("--terms", str(_terms_file(env, TERM)))
    assert run.code == 0
    assert "対象 1 ファイル・外したファイル 3 件" in run.stdout


# --- I4 ----------------------------------------------------------------


def test_missing_terms_option_file_exits_2(env):
    env.write("a.txt", f"{TERM}\n")
    run = env.run("--terms", str(env.tmp / "missing.txt"))
    assert run.code == 2
    assert "--terms で指定した語の一覧が無い" in run.stderr


def test_non_utf8_list_exits_2(env):
    env.write("a.txt", f"{TERM}\n")
    run = env.run("--terms", str(_terms_file(env, TERM.encode() + b"\n\xff" + OTHER.encode() + b"\n")))
    assert run.code == 2
    assert "UTF-8 でない" in run.stderr


def test_unreadable_default_list_exits_2(env):
    env.write("a.txt", f"{TERM}\n")
    (env.config / "devbase" / "proper-terms.txt").mkdir(parents=True)
    run = env.run()
    assert run.code == 2
    assert "語の一覧を読めない" in run.stderr


def test_outside_repository_exits_2(env):
    outside = env.tmp / "outside"
    outside.mkdir()
    run = env.run("--terms", str(_terms_file(env, TERM)), cwd=outside, env={"GIT_CEILING_DIRECTORIES": str(env.tmp)})
    assert run.code == 2
    assert "git のリポジトリの外" in run.stderr


def test_bad_argument_exits_2(env):
    assert env.run("--no-such-option").code == 2


# --- AC8・I1 ------------------------------------------------------------


def test_actions_output_on_hits_does_not_leak(env):
    """AC8: 注記とサマリーはパスと行番号だけで、語と本文を出さない。"""
    env.write("dir,x/a:b.md", f"{BODY_MARK} {TERM}\n")
    run = env.run(env={"PROPER_TERMS": f"{TERM}\n{OTHER}\n"}, actions=True)
    assert run.code == 1
    assert "::error file=dir%2Cx/a%3Ab.md,line=1,title=固有の語の検査::固有の語を含む行がある" in run.stdout
    assert "dir,x/a:b.md:1" in run.summary
    assert "当たり 1 件" in run.summary
    _assert_no_leak(run, TERM, OTHER)


def test_actions_output_without_hits_does_not_leak(env):
    env.write("a.txt", "clean\n")
    run = env.run(env={"PROPER_TERMS": f"{TERM}\n{OTHER}\n"}, actions=True)
    assert run.code == 0
    assert "当たり 0 件" in run.summary
    _assert_no_leak(run, TERM, OTHER)


def test_actions_output_on_unreadable_list_does_not_leak(env):
    env.write("a.txt", f"{TERM}\n")
    terms = _terms_file(env, TERM.encode() + b"\n\xff" + OTHER.encode() + b"\n")
    run = env.run("--terms", str(terms), actions=True)
    assert run.code == 2
    assert "::error::" in run.stdout
    assert "UTF-8 でない" in run.summary
    _assert_no_leak(run, TERM, OTHER)


# --- 関数の単体 -----------------------------------------------------------


def test_load_terms_dedupes_and_casefolds():
    assert ptc.load_terms("A\na\n# b\n\n  c \n") == frozenset({"a", "c"})


def test_exception_rule_covers_needs_both_path_and_shape():
    rule = ptc.ExceptionRule("LICENSE", ptc.EXCEPTION_RULES[0].pattern)
    assert rule.covers("LICENSE", "Copyright (c) 2026 X")
    assert not rule.covers("LICENSE", "by X")
    assert not rule.covers("docs/LICENSE", "Copyright (c) 2026 X")
