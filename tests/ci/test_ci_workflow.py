"""`ci.yml` のトリガーと検査ジョブの名前、pytest をまとめたチェックの形 (#277・#291 I1〜I6)。

トリガーが崩れても、`main` の必須チェックの名前が変わっても、CI の結果からは気づけない
（検査は別の宛先で走り続け、必須チェックは「待ち」になるだけ）。`ci.yml` を読んで固定する。
matrix の版を変えるときは、ここの期待値も一緒に直す（保護設定は直さない）。
"""

from __future__ import annotations

import itertools
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

AGGREGATE_JOB = "pytest-all"
AGGREGATE_NAME = "Pytest"

EXPECTED_PUSH_BRANCHES = {"main", "release/**", "mission/**"}
EXPECTED_CHECK_NAMES = {
    "Python syntax check (3.10)",
    "Python syntax check (3.11)",
    "Python syntax check (3.12)",
    "Ruff lint",
    "ShellCheck",
    "Pytest (Python 3.10)",
    "Pytest (Python 3.13)",
    AGGREGATE_NAME,
    "CHANGELOG check",
}

_MATRIX_EXPR = re.compile(r"\$\{\{\s*matrix\.([\w-]+)\s*\}\}")


def _workflow() -> dict:
    return yaml.safe_load(CI_YML.read_text())


def _triggers() -> dict:
    wf = _workflow()
    # PyYAML は YAML 1.1 のため、キーの `on` を True として読む
    return wf[True] if True in wf else wf["on"]


def _check_names(job: dict) -> list[str]:
    """GitHub が検査ジョブに付けるチェックの名前を、matrix の値で展開して返す。"""
    name = job["name"]
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    axes = {k: v for k, v in matrix.items() if k not in ("include", "exclude")}
    if not axes:
        return [name]
    keys = list(axes)
    names = []
    for values in itertools.product(*(axes[k] for k in keys)):
        combo = dict(zip(keys, values))
        if _MATRIX_EXPR.search(name):
            names.append(_MATRIX_EXPR.sub(lambda m, combo=combo: str(combo[m.group(1)]), name))
        else:
            # 名前に matrix の式が無いと、GitHub は値を括弧で付け足す
            names.append(f"{name} ({', '.join(str(v) for v in values)})")
    return names


def _aggregate_job() -> dict:
    return _workflow()["jobs"][AGGREGATE_JOB]


def _aggregate_step() -> dict:
    steps = [s for s in _aggregate_job()["steps"] if "run" in s]
    assert len(steps) == 1, "まとめたチェックの判定の手順は 1 つだけ置く"
    return steps[0]


def test_pull_request_does_not_filter_branches():
    """I1: Pull Request は宛先を絞らない（積み重ねた Pull Request も検査する）。"""
    pr = _triggers()["pull_request"]
    assert pr is None or ("branches" not in pr and "branches-ignore" not in pr)


def test_push_branches_are_main_and_integration_branches():
    """I2: push は main と統合ブランチだけで走る。"""
    push = _triggers()["push"]
    assert "branches-ignore" not in push
    assert set(push["branches"]) == EXPECTED_PUSH_BRANCHES
    assert len(push["branches"]) == len(EXPECTED_PUSH_BRANCHES)


def test_check_names_are_fixed():
    """I3: 検査ジョブのチェックの名前は、既存の 7 件・まとめたチェック・CHANGELOG の検査の 9 件とちょうど一致する。"""
    names = [n for job in _workflow()["jobs"].values() for n in _check_names(job)]
    assert sorted(names) == sorted(EXPECTED_CHECK_NAMES)


def test_aggregate_name_does_not_depend_on_matrix():
    """まとめたチェックの名前は固定の文字列で、matrix を持たない（版に依存しない）。"""
    job = _aggregate_job()
    assert job["name"] == AGGREGATE_NAME
    assert "${{" not in job["name"]
    assert "strategy" not in job


def test_aggregate_waits_for_pytest():
    """I4: まとめたチェックは pytest の matrix の全版を待つ。"""
    needs = _aggregate_job().get("needs")
    needs = [needs] if isinstance(needs, str) else (needs or [])
    assert "pytest" in needs


def test_aggregate_always_runs():
    """I5: pytest が失敗・取り消しでも走る（skipped は必須チェックで合格と扱われる）。"""
    assert str(_aggregate_job().get("if", "")).strip() == "always()"


def test_aggregate_result_comes_from_pytest():
    """I6 の前提: 判定に渡す値は pytest のジョブの結論である。"""
    env = _aggregate_step().get("env") or {}
    assert re.fullmatch(r"\$\{\{\s*needs\.pytest\.result\s*\}\}", str(env.get("RESULT", "")))


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash が無い")
@pytest.mark.parametrize(
    ("result", "passes"),
    [("success", True), ("failure", False), ("cancelled", False), ("skipped", False)],
)
def test_aggregate_passes_only_on_success(result: str, passes: bool):
    """I6: 判定の手順は success のときだけ 0 で終わる。"""
    proc = subprocess.run(
        ["bash", "-e", "-c", _aggregate_step()["run"]],
        env={"RESULT": result, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        check=False,
        text=True,
    )
    assert (proc.returncode == 0) is passes, proc.stdout + proc.stderr
