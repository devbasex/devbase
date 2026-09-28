"""CHANGELOG の検査 (#335)。判定は GitHub へ繋がずにパスの一覧で、ジョブの形は ci.yml を読んで確かめる。"""

from __future__ import annotations

import importlib.util
import subprocess

import pytest

from tests.ci._workflow import CI_YML, REPO_ROOT, load_workflow

SCRIPT = REPO_ROOT / ".github" / "scripts" / "changelog_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("changelog_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cc = _load()

# 見張るパスそれぞれの下のファイルの例
WATCHED_EXAMPLES = {
    "lib/": "lib/devbase/cli.py",
    "bin/": "bin/devbase",
    "containers/": "containers/base/entrypoint.sh",
    "etc/": "etc/completion.bash",
    "install.sh": "install.sh",
}


def test_watched_paths_are_the_five():
    assert set(cc.WATCHED_PATHS) == set(WATCHED_EXAMPLES)


@pytest.mark.parametrize("watched,path", sorted(WATCHED_EXAMPLES.items()))
def test_each_watched_path_without_changelog_warns(watched, path):
    assert cc.unrecorded_changes([path, "docs/x.md"]) == [path]


def test_watched_without_changelog_lists_only_watched_files():
    """AC4: 見張るパスの変更だけを示し、警告の文は [Unreleased] の更新を求める。"""
    files = cc.unrecorded_changes(["tests/a.py", "lib/devbase/b.py", "bin/devbase", "docs/c.md"])
    assert files == ["bin/devbase", "lib/devbase/b.py"]
    msg = cc.warning_message(files)
    assert "CHANGELOG.md" in msg and "[Unreleased]" in msg
    assert "bin/devbase" in msg and "lib/devbase/b.py" in msg
    assert "\n" not in msg


def test_watched_with_changelog_does_not_warn():
    """AC6"""
    assert cc.unrecorded_changes(["lib/devbase/b.py", "CHANGELOG.md"]) == []


def test_no_watched_path_does_not_warn():
    """AC7"""
    assert cc.unrecorded_changes(["docs/user/a.md", "tests/ci/test_x.py", ".github/workflows/ci.yml"]) == []


@pytest.mark.parametrize("path", ["libs/x.py", "binary/x", "install.sh.bak", "docs/lib/x.md", "etcetera/x"])
def test_lookalike_paths_are_not_watched(path):
    assert cc.unrecorded_changes([path]) == []


# main(): 警告を出しても成功、差分を得られなければ失敗 (AC5・AC9)


def _fake_run(outputs):
    def run(cmd, **_):
        rc, out, err = outputs[cmd[1]]
        return subprocess.CompletedProcess(cmd, rc, out, err)

    return run


@pytest.fixture
def env(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("BASE_REF", "main")
    monkeypatch.setenv("HEAD_SHA", "abc123")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return summary


def test_main_warns_once_and_succeeds(monkeypatch, capsys, env):
    monkeypatch.setattr(
        cc.subprocess, "run", _fake_run({"fetch": (0, "", ""), "diff": (0, "lib/a.py\ndocs/b.md\n", "")})
    )
    assert cc.main() == 0
    out = capsys.readouterr().out
    assert out.count("::warning") == 1
    assert "lib/a.py" in out
    assert "lib/a.py" in env.read_text()


def test_main_without_warning_succeeds(monkeypatch, capsys, env):
    monkeypatch.setattr(
        cc.subprocess, "run", _fake_run({"fetch": (0, "", ""), "diff": (0, "lib/a.py\nCHANGELOG.md\n", "")})
    )
    assert cc.main() == 0
    assert "::warning" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "outputs",
    [
        {"fetch": (128, "", "fatal: couldn't find remote ref"), "diff": (0, "", "")},
        {"fetch": (0, "", ""), "diff": (128, "", "fatal: bad revision")},
    ],
)
def test_main_fails_with_reason_when_diff_unavailable(monkeypatch, capsys, env, outputs):
    monkeypatch.setattr(cc.subprocess, "run", _fake_run(outputs))
    assert cc.main() == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "fatal:" in out
    assert "::warning" not in out


def test_main_fails_without_base_ref(monkeypatch, capsys, env):
    monkeypatch.delenv("BASE_REF")
    assert cc.main() == 1
    assert "::error::" in capsys.readouterr().out


# ci.yml のジョブの形 (AC8・AC11・AC12・非機能の条件)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return load_workflow()


@pytest.fixture(scope="module")
def job(workflow) -> dict:
    return workflow["jobs"]["changelog"]


def test_job_runs_only_for_pull_requests_to_main(job):
    cond = job["if"]
    assert "github.event_name == 'pull_request'" in cond
    assert "github.base_ref == 'main'" in cond


def test_job_permissions_are_read_only(job):
    assert job["permissions"] == {"contents": "read"}


def test_job_runs_the_script_without_docker(job):
    runs = [s.get("run") or "" for s in job["steps"]]
    assert any(".github/scripts/changelog_check.py" in r for r in runs)
    assert not any("docker" in r for r in runs)
    assert all("continue-on-error" not in s for s in job["steps"])
    checkout = next(s for s in job["steps"] if (s.get("uses") or "").startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0


def test_job_passes_base_ref_and_head_sha(job):
    step = next(s for s in job["steps"] if "changelog_check.py" in (s.get("run") or ""))
    assert step["env"]["BASE_REF"] == "${{ github.base_ref }}"
    assert step["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"


def test_watched_paths_defined_only_in_script():
    """AC11: ci.yml は見張るパスを持たず、スクリプトに任せる。"""
    job_text = CI_YML.read_text().split("  changelog:", 1)[1]
    run_lines = [ln for ln in job_text.splitlines() if "run:" in ln]
    assert run_lines and all("lib/" not in ln and "containers/" not in ln for ln in run_lines)
