"""entrypoint.sh の複数リポジトリ clone と workspace 書き出し (PLAN32)

``containers/base/entrypoint.sh`` を ``DEVBASE_ENTRYPOINT_LIB_ONLY=1`` で source し、
関数だけを読み込んで bash から直接呼び出す。clone 元にはローカルの bare リポジトリ
(``file://``) を使うため、ネットワークにも Docker にも依存しない。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "containers" / "base" / "entrypoint.sh"


def encode_plan(rows) -> str:
    """host 側が渡す wire format (base64 TSV) をテスト側で組み立てる。

    ``rows`` は ``(url, dir, branch, init)`` のタプル列。init は ``"1"`` / ``"0"``。
    """
    text = "\n".join("\t".join(row) for row in rows)
    return base64.b64encode(text.encode()).decode()


def run_entrypoint_fn(script: str, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    """entrypoint.sh の関数だけを読み込んで ``script`` を実行する。

    ``PATH`` を固定すると Homebrew の git しか無い環境で落ちるため、実行環境を
    引き継ぐ。ただし呼び出し側の DEVBASE_* / GIT_* が紛れ込むとテストの前提が
    崩れるので、そこだけ落としてから ``env`` を重ねる。
    """
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("DEVBASE_", "GIT_"))}
    full = f'set -e\nDEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n{script}\n'
    return subprocess.run(
        ["bash", "-c", full], cwd=cwd, env={**base, **env},
        capture_output=True, text=True,
    )


def current_branch(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def make_origin(tmp_path: Path, name: str, *, branches=(), files=None) -> str:
    """clone 元の bare リポジトリを作り ``file://`` URL を返す。"""
    work = tmp_path / "origins" / name
    work.mkdir(parents=True)
    git = ["git", "-C", str(work)]
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    subprocess.run([*git, "config", "user.email", "t@example.com"], check=True)
    subprocess.run([*git, "config", "user.name", "t"], check=True)
    for rel, content in (files or {"README.md": name}).items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if rel.endswith(".sh"):
            path.chmod(0o755)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    for branch in branches:
        subprocess.run([*git, "branch", branch], check=True)

    bare = tmp_path / "origins" / f"{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return f"file://{bare}"


@pytest.fixture
def work(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# clone
# ---------------------------------------------------------------------------

def test_clones_every_repository_in_the_plan(tmp_path, work):
    plan = encode_plan([
        (make_origin(tmp_path, "app"), "app", "", "0"),
        (make_origin(tmp_path, "docs"), "docs", "", "0"),
        (make_origin(tmp_path, "infra"), "cdk", "", "0"),
    ])

    result = run_entrypoint_fn(f'devbase_clone_repos "{work}"',
                               {"DEVBASE_REPOS": plan}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert (work / "app" / "README.md").read_text() == "app"
    assert (work / "docs" / "README.md").read_text() == "docs"
    assert (work / "cdk" / "README.md").read_text() == "infra"


def test_checks_out_the_requested_branch(tmp_path, work):
    plan = encode_plan([
        (make_origin(tmp_path, "app", branches=("develop",)), "app", "develop", "0"),
    ])

    result = run_entrypoint_fn(f'devbase_clone_repos "{work}"',
                               {"DEVBASE_REPOS": plan}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert current_branch(work / "app") == "develop"


def test_runs_init_script_only_when_enabled(tmp_path, work):
    files = {"README.md": "app", "init.sh": "#!/bin/bash\ntouch ./init-was-run\n"}
    plan = encode_plan([
        (make_origin(tmp_path, "with-init", files=files), "with-init", "", "1"),
        (make_origin(tmp_path, "no-init", files=files), "no-init", "", "0"),
    ])

    result = run_entrypoint_fn(f'devbase_clone_repos "{work}"',
                               {"DEVBASE_REPOS": plan}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert (work / "with-init" / "init-was-run").exists()
    assert not (work / "no-init" / "init-was-run").exists()


def test_a_failing_clone_does_not_stop_the_others(tmp_path, work):
    plan = encode_plan([
        (f"file://{tmp_path}/does-not-exist.git", "missing", "", "0"),
        (make_origin(tmp_path, "app"), "app", "", "0"),
    ])

    result = run_entrypoint_fn(f'devbase_clone_repos "{work}"',
                               {"DEVBASE_REPOS": plan}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert (work / "app" / "README.md").exists()
    assert not (work / "missing").exists()
    assert "Warning" in result.stdout + result.stderr


def test_a_failing_checkout_does_not_stop_the_others(tmp_path, work):
    plan = encode_plan([
        (make_origin(tmp_path, "app"), "app", "no-such-branch", "0"),
        (make_origin(tmp_path, "docs"), "docs", "", "0"),
    ])

    result = run_entrypoint_fn(f'devbase_clone_repos "{work}"',
                               {"DEVBASE_REPOS": plan}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert (work / "docs" / "README.md").exists()


def test_a_failing_checkout_skips_the_init_script(tmp_path, work):
    """checkout に失敗した repo は打ち切る (意図しない branch で init.sh を走らせない)。"""
    files = {"README.md": "app", "init.sh": "#!/bin/bash\ntouch ./init-was-run\n"}
    plan = encode_plan([
        (make_origin(tmp_path, "app", files=files), "app", "no-such-branch", "1"),
    ])

    result = run_entrypoint_fn(f'devbase_clone_repos "{work}"',
                               {"DEVBASE_REPOS": plan}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (work / "app" / "init-was-run").exists()


def test_existing_clone_keeps_the_branch_the_user_switched_to(tmp_path, work):
    """再起動のたびに設定 branch へ引き戻さない (checkout は clone 直後のみ)。"""
    url = make_origin(tmp_path, "app", branches=("develop", "feature-A"))
    plan = encode_plan([(url, "app", "develop", "0")])
    run_entrypoint_fn(f'devbase_clone_repos "{work}"', {"DEVBASE_REPOS": plan}, tmp_path)
    assert current_branch(work / "app") == "develop"
    subprocess.run(["git", "-C", str(work / "app"), "checkout", "-q", "feature-A"], check=True)

    result = run_entrypoint_fn(f'devbase_clone_repos "{work}"',
                               {"DEVBASE_REPOS": plan}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert current_branch(work / "app") == "feature-A"


def test_existing_clone_is_kept(tmp_path, work):
    url = make_origin(tmp_path, "app")
    plan = encode_plan([(url, "app", "", "0")])
    run_entrypoint_fn(f'devbase_clone_repos "{work}"', {"DEVBASE_REPOS": plan}, tmp_path)
    (work / "app" / "local-change.txt").write_text("keep me")

    result = run_entrypoint_fn(f'devbase_clone_repos "{work}"',
                               {"DEVBASE_REPOS": plan}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert (work / "app" / "local-change.txt").read_text() == "keep me"


def test_no_plan_is_not_an_error(tmp_path, work):
    result = run_entrypoint_fn(f'devbase_clone_repos "{work}"', {}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert list(work.iterdir()) == []


def test_broken_plan_is_reported_without_failing_startup(tmp_path, work):
    result = run_entrypoint_fn(f'devbase_clone_repos "{work}"',
                               {"DEVBASE_REPOS": "not-base64!!"}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Warning" in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# workspace
# ---------------------------------------------------------------------------

def test_writes_the_workspace_file(tmp_path, work):
    document = {"folders": [{"path": "/work/app"}, {"path": "/work/docs"}]}
    encoded = base64.b64encode(json.dumps(document).encode()).decode()
    dest = work / "sample.code-workspace"

    result = run_entrypoint_fn("devbase_write_workspace", {
        "DEVBASE_WORKSPACE": str(dest),
        "DEVBASE_WORKSPACE_B64": encoded,
    }, tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(dest.read_text()) == document


def test_workspace_is_skipped_when_not_configured(tmp_path, work):
    result = run_entrypoint_fn("devbase_write_workspace", {}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert list(work.iterdir()) == []


# ---------------------------------------------------------------------------
# primary への cd
# ---------------------------------------------------------------------------

def test_primary_dir_is_the_landing_directory(tmp_path, work):
    (work / "app").mkdir()

    result = run_entrypoint_fn(f'devbase_enter_primary_dir "{work}"; pwd',
                               {"DEVBASE_PRIMARY_DIR": "app"}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("/work/app")


def test_missing_primary_dir_does_not_fail_startup(tmp_path, work):
    result = run_entrypoint_fn(f'devbase_enter_primary_dir "{work}"',
                               {"DEVBASE_PRIMARY_DIR": "app"}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Warning" in result.stdout + result.stderr


def test_old_git_repo_env_is_no_longer_honoured(tmp_path, work):
    """PLAN32 は後方互換を持たない。GIT_USER/GIT_REPO では clone しない。"""
    result = run_entrypoint_fn(f'devbase_clone_repos "{work}"',
                               {"GIT_USER": "volareinc", "GIT_REPO": "carmo"}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert list(work.iterdir()) == []
