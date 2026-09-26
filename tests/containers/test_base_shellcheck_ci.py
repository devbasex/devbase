"""containers/base のシェルスクリプトが CI の ShellCheck から漏れないこと (#259 I1・I3)。

`.github/workflows/ci.yml` の `shellcheck` ジョブは、`containers/base/` のシェルスクリプトを
ファイル名を並べて検査する。並べ忘れたスクリプトは検査されないまま入るため、直下を数えて
ci.yml の検査の対象と突き合わせる。あわせて、7 本の shellcheck の指示が理由を持つことを確かめる。
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

import pytest
import yaml

from tests.shellcheck_rules import CI_YML, REPO_ROOT, directives_without_reason

BASE_DIR = REPO_ROOT / "containers" / "base"
PREFIX = "containers/base/"

_SHEBANG = re.compile(r"^#!\s*(\S*/)?(env\s+)?(sh|bash)(\s|$)")
_OPERATORS = {"|", "||", "&&", ";", "&"}


# --- base のシェルスクリプトを数える判定 ---


def _is_shell_script(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if path.name.endswith(".sh"):
        return True
    with path.open(encoding="utf-8", errors="replace") as f:
        first = f.readline()
    return bool(_SHEBANG.match(first))


def base_shell_scripts(base_dir: Path) -> list[str]:
    return [p.name for p in sorted(base_dir.iterdir(), key=lambda p: p.name) if _is_shell_script(p)]


# --- 検査の対象を集める規則 ---


def _shellcheck_commands(run: str) -> list[list[str]]:
    """run の中の、containers/base/ を検査する shellcheck の呼び出しを語の並びで返す。"""
    commands = []
    for line in run.replace("\\\n", " ").splitlines():
        words = shlex.split(line, comments=True)
        segment: list[str] = []
        for word in words + [";"]:
            if word not in _OPERATORS:
                segment.append(word)
                continue
            if segment and os.path.basename(segment[0]) == "shellcheck":
                args = [w.removeprefix("./") for w in segment[1:] if not w.startswith("-")]
                if any(a.startswith(PREFIX) for a in args):
                    commands.append(segment)
            segment = []
    return commands


def base_commands(ci: dict) -> list[list[str]]:
    commands = []
    for step in ci["jobs"]["shellcheck"]["steps"]:
        commands.extend(_shellcheck_commands(step.get("run") or ""))
    return commands


def checked_targets(commands: list[list[str]]) -> set[str]:
    return {w.removeprefix("./") for c in commands for w in c[1:] if not w.startswith("-")}


def severity_options(command: list[str]) -> list[str]:
    return [w for w in command[1:] if w == "--severity" or w.startswith("--severity=") or w.startswith("-S")]


def missing_scripts(scripts: list[str], targets: set[str]) -> list[str]:
    return sorted(name for name in scripts if PREFIX + name not in targets)


def assert_all_checked(scripts: list[str], targets: set[str]) -> None:
    missing = missing_scripts(scripts, targets)
    assert not missing, (
        "CI の ShellCheck の対象に無い containers/base のシェルスクリプト: "
        + ", ".join(missing)
        + " (.github/workflows/ci.yml の Run ShellCheck on containers/base/ へ足す)"
    )


# --- 実物の検査 ---


@pytest.fixture(scope="module")
def ci() -> dict:
    return yaml.safe_load(CI_YML.read_text())


def test_base_scripts_are_counted():
    scripts = base_shell_scripts(BASE_DIR)
    for name in ["ai-cli-aliases.sh", "dind", "entrypoint.sh", "shellrc-dir.sh",
                 "tmux-clean", "tmux-first", "tmux-session"]:
        assert name in scripts
    for name in ["Dockerfile", "fonts-local.conf", "tmux.conf"]:
        assert name not in scripts


def test_every_base_script_is_checked_by_ci(ci):
    assert_all_checked(base_shell_scripts(BASE_DIR), checked_targets(base_commands(ci)))


def test_base_commands_do_not_set_severity(ci):
    commands = base_commands(ci)
    assert commands, "containers/base/ を検査する shellcheck の呼び出しが無い"
    for command in commands:
        assert not severity_options(command), f"水準を絞っている: {' '.join(command)}"


@pytest.mark.parametrize("name", base_shell_scripts(BASE_DIR))
def test_directives_have_reason(name):
    lines = (BASE_DIR / name).read_text(encoding="utf-8", errors="replace").splitlines()
    bad = directives_without_reason(lines)
    assert not bad, "抑える理由が無い指示: " + ", ".join(f"containers/base/{name}:{n}" for n in bad)


# --- 規則の単体テスト ---


def test_counts_shebang_and_sh_extension(tmp_path):
    (tmp_path / "a").write_text("#!/bin/sh\necho a\n")
    (tmp_path / "b").write_text("#!/usr/bin/env bash\necho b\n")
    (tmp_path / "x.sh").write_text("echo x\n")
    assert base_shell_scripts(tmp_path) == ["a", "b", "x.sh"]


def test_does_not_count_non_shell(tmp_path):
    (tmp_path / "py").write_text("#!/usr/bin/python3\nprint(1)\n")
    (tmp_path / "plain").write_text("echo plain\n")
    (tmp_path / "Dockerfile").write_text("FROM ubuntu\n")
    (tmp_path / "tmux.conf").write_text("set -g mouse on\n")
    (tmp_path / "bashful").write_text("#!/bin/bashful\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "y.sh").write_text("echo y\n")
    real = sub / "real.sh"
    real.write_text("echo r\n")
    (tmp_path / "link.sh").symlink_to(real)
    assert base_shell_scripts(tmp_path) == []


def test_missing_script_is_named():
    targets = {PREFIX + "a.sh"}
    with pytest.raises(AssertionError, match="probe.sh"):
        assert_all_checked(["a.sh", "probe.sh"], targets)
    assert_all_checked(["a.sh"], targets)


def _ci(run: str) -> dict:
    return {"jobs": {"shellcheck": {"steps": [{"uses": "actions/checkout@v4"}, {"run": run}]}}}


def test_collects_targets_from_continued_lines():
    run = "shellcheck --version | sed -n 2p && shellcheck \\\n  containers/base/a.sh \\\n  ./containers/base/b\n"
    assert checked_targets(base_commands(_ci(run))) == {PREFIX + "a.sh", PREFIX + "b"}


def test_ignores_commands_without_base():
    run = "shellcheck --severity=error install.sh\nshellcheck bin/*\necho containers/base/a.sh\n"
    assert base_commands(_ci(run)) == []


@pytest.mark.parametrize("opt", ["--severity=warning", "-S warning", "-Swarning", "--severity warning"])
def test_severity_on_base_command_is_found(opt):
    commands = base_commands(_ci(f"shellcheck {opt} containers/base/a.sh"))
    assert commands and severity_options(commands[0])


def test_base_script_added_to_install_line_is_taken():
    commands = base_commands(_ci("shellcheck --severity=error install.sh containers/base/a.sh"))
    assert commands and severity_options(commands[0])


@pytest.mark.parametrize(
    "text,bad",
    [
        ("x\n# shellcheck disable=SC2046\n", [2]),
        ("# 理由\n\n# shellcheck disable=SC2046\n", [3]),
        ("#!/bin/bash\n# shellcheck disable=SC2046\n", [2]),
        ("# 理由\n# shellcheck disable=SC2046\n# shellcheck disable=SC2009\n", [3]),
        ("#\n# shellcheck disable=SC2046\n", [2]),
        ("# 理由\n# shellcheck disable=SC2046\n", []),
        ("# shellcheck disable=SC2046 # 理由\n", []),
        ("# 理由 1\n# shellcheck disable=SC2046\n# 理由 2\n# shellcheck source=/dev/null\n", []),
    ],
)
def test_directive_reason_rule(text, bad):
    assert directives_without_reason(text.splitlines()) == bad
