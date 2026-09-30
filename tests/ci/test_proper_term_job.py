"""固有の語の検査ジョブの形 (#353 I9・I10)。

secret がほかの手順や成果物へ流れても、ジョブが宛先で絞られても、CI の結果からは気づけない。
`ci.yml` を読んで固定する。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ".github/scripts/proper_term_check.py"
JOB = "proper-terms"

_SECRET_EXPR = re.compile(r"\$\{\{\s*secrets\.PROPER_TERMS\s*\}\}")


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return CI_YML.read_text()


@pytest.fixture(scope="module")
def job(workflow_text) -> dict:
    return yaml.safe_load(workflow_text)["jobs"][JOB]


def _check_step(job: dict) -> dict:
    steps = [s for s in job["steps"] if "run" in s]
    assert len(steps) == 1, "検査の手順は 1 つだけ置く"
    return steps[0]


def test_job_name_is_fixed(job):
    assert job["name"] == "Proper term check"
    assert "strategy" not in job


def test_job_runs_on_every_trigger(job):
    """I10: 宛先で絞らない。"""
    assert "if" not in job


def test_permissions_are_contents_read_only(job):
    """I9"""
    assert job["permissions"] == {"contents": "read"}


def test_checkout_does_not_persist_credentials(job):
    checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["persist-credentials"] is False


def test_step_runs_only_the_script(job):
    run = _check_step(job)["run"].strip()
    assert run == f"python3 {SCRIPT}"
    assert (REPO_ROOT / SCRIPT).is_file()


def test_secret_goes_only_to_check_step_env(job, workflow_text):
    """I9: secret は検査の手順の env にだけ現れる。"""
    step = _check_step(job)
    assert _SECRET_EXPR.fullmatch(str(step["env"]["PROPER_TERMS"]))
    assert len(_SECRET_EXPR.findall(workflow_text)) == 1
    assert "env" not in job
    for s in job["steps"]:
        assert not _SECRET_EXPR.search(str(s.get("run", ""))), s
        assert not _SECRET_EXPR.search(str(s.get("with", ""))), s


def test_no_artifact_or_comment_steps(job):
    for s in job["steps"]:
        uses = str(s.get("uses", ""))
        assert "upload-artifact" not in uses, s
        assert "github-script" not in uses, s
        assert "comment" not in uses, s
    assert len(job["steps"]) == 2
