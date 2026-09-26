"""抑止の注記と ShellCheck の検査ジョブの形 (#247 I2・I3・I4a・I4b)。

shellcheck は指示の行さえあれば 0 件を返し、検査ジョブは水準を絞っても成功する。
どちらも shellcheck を実行しても確かめられないため、本文と `ci.yml` を読んで固定する。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
BASE_VERSION = "v0.11.0"

_DIRECTIVE = re.compile(r"^\s*#\s*shellcheck\s")
# 行頭か区切りの後に来る `shellcheck` のコマンド (URL やファイル名の `shellcheck-` は含めない)
_INVOKE = re.compile(r"(?:^|[\s;&|(])shellcheck(?=\s|$)")
_SEVERITY = re.compile(r"(?:^|\s)(?:--severity|-S)")


def _checked_scripts() -> list[Path]:
    return sorted(p for p in (REPO_ROOT / "bin").iterdir() if p.is_file()) + [REPO_ROOT / "install.sh"]


def _directive_lines() -> list[tuple[Path, int, str, str]]:
    found = []
    for path in _checked_scripts():
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if _DIRECTIVE.match(line):
                prev = lines[i - 1] if i > 0 else ""
                found.append((path, i + 1, line, prev))
    return found


def _has_reason(line: str, prev: str) -> bool:
    # 同じ行: 指示の後ろに `# 理由` が続く
    body = line.split("shellcheck", 1)[1]
    if re.search(r"\s#\s*\S", body):
        return True
    # 直前の行: 指示でないコメント
    return prev.lstrip().startswith("#") and not _DIRECTIVE.match(prev) and prev.strip() != "#"


def test_directives_exist():
    """対象が空で検査が素通りしていないこと (bin/devbase の 3 行と bin/rc の 2 行)。"""
    assert len(_directive_lines()) >= 5


@pytest.mark.parametrize(
    "path,lineno,line,prev",
    _directive_lines(),
    ids=lambda v: v.name if isinstance(v, Path) else str(v) if isinstance(v, int) else "",
)
def test_directive_has_reason(path, lineno, line, prev):
    assert _has_reason(line, prev), f"{path.relative_to(REPO_ROOT)}:{lineno}: 抑える理由が無い: {line.strip()}"


@pytest.fixture(scope="module")
def job() -> dict:
    return yaml.safe_load(CI_YML.read_text())["jobs"]["shellcheck"]


def _run(step: dict) -> str:
    return step.get("run") or ""


def _invokes_shellcheck(step: dict) -> bool:
    return bool(_INVOKE.search(_run(step)))


def _install_index(steps: list[dict]) -> int:
    idx = [i for i, s in enumerate(steps) if "GITHUB_PATH" in _run(s)]
    assert len(idx) == 1, "shellcheck の導入の手順が 1 つでない"
    return idx[0]


def test_no_step_sets_severity(job):
    for step in job["steps"]:
        assert "severity" not in (step.get("with") or {}), step
        assert not _SEVERITY.search(_run(step)), step


def test_does_not_use_action_shellcheck(job):
    for step in job["steps"]:
        assert "action-shellcheck" not in (step.get("uses") or ""), step


def test_job_pins_base_version_and_sha256(job):
    env = job["env"]
    assert env["SHELLCHECK_VERSION"] == BASE_VERSION
    assert re.fullmatch(r"[0-9a-f]{64}", env["SHELLCHECK_SHA256"])


def test_install_step_verifies_sha256_before_extracting(job):
    run = _run(job["steps"][_install_index(job["steps"])])
    assert "${SHELLCHECK_VERSION}" in run
    assert "sha256sum -c" in run
    assert run.index("sha256sum -c") < run.index("tar ")
    assert not _INVOKE.search(run), "導入の手順の中で shellcheck を打っている"


def test_version_is_checked_against_base_version(job):
    steps = job["steps"]
    after = steps[_install_index(steps) + 1:]
    assert any('grep -Fx "version: ${SHELLCHECK_VERSION#v}"' in _run(s) for s in after)


def test_every_shellcheck_step_follows_install_and_prints_version_first(job):
    steps = job["steps"]
    install = _install_index(steps)
    checks = [(i, s) for i, s in enumerate(steps) if _invokes_shellcheck(s)]
    # 版の確認・bin/・install.sh・containers/base/ の 4 手順
    assert len(checks) >= 4
    targets = " ".join(_run(s) for _, s in checks)
    assert "shellcheck bin/*" in targets
    assert "shellcheck install.sh" in targets
    for i, step in checks:
        assert i > install, f"導入より前に shellcheck を打つ: {step.get('name')}"
        run = _run(step).strip()
        assert run.startswith("shellcheck --version"), f"版を先に出していない: {step.get('name')}"
