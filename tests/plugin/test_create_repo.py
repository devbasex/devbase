"""``devbase plugin repo create`` のテスト (#514 の受け入れ条件 1〜5・14・15・17)。"""

from __future__ import annotations

import os

import pytest
import yaml

from devbase.plugin.models import RegisteredRepository
from devbase.plugin.registry import PluginRegistry

FULL = "owner/repo"
REGISTRY = "name: repo\nplugins: []\n"


def _registered(root):
    return PluginRegistry(root).list_repositories()


def _plugins_yml(root):
    path = root / "plugins.yml"
    return path.read_text() if path.exists() else None


def _visibility(github):
    return github.state()["repos"][FULL]["visibility"]


# ── 分岐の表 ─────────────────────────────────────────────────────


def test_absent_repo_is_created_private_and_registered(github, root, run_cli):
    github.add_org("owner", role="admin")
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 0
    assert _visibility(github) == "private"
    assert yaml.safe_load(github.file(FULL, "registry.yml")) == {
        "name": "repo", "description": "", "plugins": []}
    assert github.pushed_refs(FULL) == ["refs/heads/main"]
    assert github.prs() == []
    repo = _registered(root)[0]
    assert repo.url == "https://github.com/owner/repo.git"
    assert (root / repo.local_path / "registry.yml").is_file()
    assert repo.plugins == [], "プラグイン 0 件でも登録する"


def test_public_flag_makes_it_public(github, root, run_cli):
    github.add_org("owner", role="admin")
    assert run_cli("plugin", "repo", "create", FULL, "--public", "--yes") == 0
    assert _visibility(github) == "public"


def test_empty_repo_gets_first_commit_directly(github, root, run_cli):
    github.add_repo(FULL)
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 0
    assert github.pushed_refs(FULL) == ["refs/heads/main"]
    assert github.prs() == []
    assert [c for c in github.write_calls() if c[:2] == ["repo", "create"]] == []
    assert len(_registered(root)) == 1


def test_repo_without_registry_goes_through_a_pr(github, root, run_cli):
    github.add_repo(FULL, {"README.md": "x\n"})
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 0
    pr = github.prs()[0]
    assert pr["head"] == "devbase/create-repo-repo"
    assert pr["state"] == "MERGED"
    assert github.pushed_refs(FULL) == ["refs/heads/devbase/create-repo-repo"]
    assert github.file(FULL, "registry.yml") is not None
    assert len(_registered(root)) == 1


def test_repo_with_registry_is_only_registered(github, root, run_cli):
    github.add_repo(FULL, {"registry.yml": REGISTRY})
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 0
    assert github.write_calls() == []
    assert github.pushed_refs(FULL) == []
    assert len(_registered(root)) == 1


@pytest.mark.parametrize("registered_url", [
    "https://github.com/owner/repo.git",
    "https://github.com/owner/repo",
    "git@github.com:owner/repo.git",
])
def test_registered_is_left_alone(github, root, run_cli, caplog, registered_url):
    github.add_repo(FULL, {"registry.yml": REGISTRY})
    PluginRegistry(root).add_repository(RegisteredRepository(name="r", url=registered_url))
    before = _plugins_yml(root)
    assert run_cli("plugin", "repo", "create", FULL) == 0, "確認も --yes も要らない"
    assert _plugins_yml(root) == before
    assert github.write_calls() == []
    assert "登録済み" in caplog.text


def test_registered_without_push_stops_with_1(github, root, run_cli):
    github.add_repo(FULL, {"registry.yml": REGISTRY}, permission="READ")
    PluginRegistry(root).add_repository(RegisteredRepository(name="r", url=f"git@github.com:{FULL}.git"))
    before = _plugins_yml(root)
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 1
    assert _plugins_yml(root) == before
    assert github.write_calls() == []


def test_registered_with_empty_local_path_still_ends_0(github, root, run_cli):
    github.add_repo(FULL, {"registry.yml": REGISTRY})
    PluginRegistry(root).add_repository(RegisteredRepository(
        name="r", url=f"https://github.com/{FULL}.git", local_path=""))
    assert run_cli("plugin", "repo", "create", FULL) == 0


def test_no_push_stops_without_writing(github, root, run_cli, caplog):
    github.add_repo(FULL, {"README.md": "x\n"}, permission="READ")
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 1
    assert github.write_calls() == []
    assert github.pushed_refs(FULL) == []
    assert _plugins_yml(root) is None
    assert not (root / "repos").exists()
    assert FULL in caplog.text


def test_absent_and_not_a_member_stops(github, root, run_cli):
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 1
    assert github.write_calls() == []
    assert not github.exists(FULL)
    assert _plugins_yml(root) is None


def test_org_forbids_creation_stops_at_create_with_gh_output(github, root, run_cli, caplog):
    """非管理者には組織の設定が返らないので、gh repo create の失敗の出力で止まる。"""
    github.add_org("owner", role="member", can_create=False)
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 1
    assert "cannot create repositories" in caplog.text
    assert not github.exists(FULL)
    assert _plugins_yml(root) is None


def test_absent_in_own_account_can_be_created(github, root, run_cli):
    assert run_cli("plugin", "repo", "create", "me/repo", "--yes") == 0
    assert github.exists("me/repo")


def test_absent_in_org_where_members_can_create(github, root, run_cli):
    github.add_org("owner", role="member", can_create=True)
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 0


def test_unreadable_private_stops_at_create_with_gh_output(github, root, run_cli, caplog):
    github.add_org("owner", role="admin")
    github.add_repo(FULL, {"README.md": "x\n"}, hidden=True)
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 1
    assert "Name already exists" in caplog.text
    assert _plugins_yml(root) is None


def test_stale_clone_directory_stops_before_writing(github, root, run_cli, caplog):
    github.add_org("owner", role="admin")
    (root / "repos" / "github.com--owner--repo").mkdir(parents=True)
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 1
    assert github.write_calls() == []
    assert "github.com--owner--repo" in caplog.text


# ── 引数の形 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("value, url", [
    ("owner/repo", "https://github.com/owner/repo.git"),
    ("https://github.com/owner/repo", "https://github.com/owner/repo.git"),
    ("https://github.com/owner/repo.git", "https://github.com/owner/repo.git"),
    ("git@github.com:owner/repo.git", "git@github.com:owner/repo.git"),
])
def test_all_forms_reach_the_same_repo(github, root, run_cli, value, url):
    github.add_repo(FULL, {"registry.yml": REGISTRY})
    assert run_cli("plugin", "repo", "create", value, "--yes") == 0
    assert _registered(root)[0].url == url


def test_non_github_url_stops(github, root, run_cli):
    assert run_cli("plugin", "repo", "create", "https://gitlab.com/owner/repo", "--yes") == 1
    assert github.calls() == [], "gh を呼ぶ前に止まる"


def test_prefix_of_create_resolves(github, root, run_cli):
    github.add_repo(FULL, {"registry.yml": REGISTRY})
    assert run_cli("pl", "repo", "c", FULL, "--yes") == 0


# ── gh の前提 ────────────────────────────────────────────────────


def test_without_gh_nothing_changes(github, root, run_cli, monkeypatch, caplog):
    git_only = github.world / "git-only"
    git_only.mkdir()
    import shutil
    (git_only / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(git_only))
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 1
    assert "cli.github.com" in caplog.text
    assert github.calls() == []
    assert _plugins_yml(root) is None


def test_unauthenticated_nothing_changes(github, root, run_cli, caplog):
    github.update(authed=False)
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 1
    assert "gh auth login" in caplog.text
    assert github.write_calls() == []
    assert _plugins_yml(root) is None


# ── 確認 ─────────────────────────────────────────────────────────


def test_no_tty_without_yes_stops_after_showing_the_plan(github, root, run_cli, capsys, caplog):
    github.add_org("owner", role="admin")
    assert run_cli("plugin", "repo", "create", FULL) == 1
    out = capsys.readouterr().out
    assert FULL in out and "private" in out
    assert "--yes" in caplog.text
    assert github.write_calls() == []
    assert _plugins_yml(root) is None


def test_tty_decline_changes_nothing(github, root, run_cli):
    github.add_org("owner", role="admin")
    assert run_cli("plugin", "repo", "create", FULL, tty=True, answers=["n"]) == 1
    assert github.write_calls() == []
    assert _plugins_yml(root) is None


def test_tty_accept_goes_on(github, root, run_cli):
    github.add_org("owner", role="admin")
    assert run_cli("plugin", "repo", "create", FULL, tty=True, answers=["y"]) == 0
    assert github.exists(FULL)
    assert len(_registered(root)) == 1


def test_nothing_is_written_before_the_confirmation(github, root, run_cli, monkeypatch):
    """確認の時点では GitHub にも手元にも書いていない (I1)。"""
    from devbase.commands import plugin as commands

    github.add_org("owner", role="admin")
    seen = {}

    def _record():
        seen.update(writes=github.write_calls(), registered=_plugins_yml(root))
        return False

    monkeypatch.setattr(commands, "_confirmed", _record)
    assert run_cli("plugin", "repo", "create", FULL) == 1
    assert seen == {"writes": [], "registered": None}


# ── マージの失敗 ─────────────────────────────────────────────────


def test_merge_failure_leaves_plugins_yml_and_shows_pr(github, root, run_cli, caplog):
    github.add_repo(FULL, {"README.md": "x\n"}, merge_error="required reviews")
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 1
    pr = github.prs()[0]
    assert pr["url"] in caplog.text
    assert "devbase plugin repo add" in caplog.text
    assert _plugins_yml(root) is None
    assert not (root / "repos").exists()


# ── コンテナに触らない ───────────────────────────────────────────


def test_never_touches_docker(github, root, run_cli):
    github.add_org("owner", role="admin")
    assert run_cli("plugin", "repo", "create", FULL, "--yes") == 0
    assert github.docker_calls() == []
    assert os.environ["PATH"].startswith(str(github.bin))
