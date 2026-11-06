"""一時の clone から既定ブランチへ反映する処理のテスト (#514)。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from devbase.errors import RepositoryError
from devbase.plugin import publish
from devbase.plugin.github import GhClient, GitHubRef

REF = GitHubRef("owner", "repo", "https://github.com/owner/repo.git")
FULL = "owner/repo"
REGISTRY = {"registry.yml": "name: repo\nplugins: []\n"}


@pytest.fixture
def gh(github):
    return GhClient()


def _temp_leftovers(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return lambda: [p.name for p in Path(tmp_path).iterdir() if p.name.startswith("devbase-")]


def test_push_initial_goes_directly_to_the_default_branch(github, tmp_path, monkeypatch):
    leftovers = _temp_leftovers(tmp_path, monkeypatch)
    github.add_repo(FULL)
    publish.push_initial(REF, {"registry.yml": "name: repo\n"}, title="devbase: repo repo を足す")
    assert github.file(FULL, "registry.yml") == "name: repo\n"
    assert github.pushed_refs(FULL) == ["refs/heads/main"]
    assert github.prs() == []
    assert leftovers() == []


def test_publish_change_opens_and_merges_a_pr(github, gh, tmp_path, monkeypatch):
    leftovers = _temp_leftovers(tmp_path, monkeypatch)
    github.add_repo(FULL, REGISTRY)
    url = publish.publish_change(gh, REF, kind="plugin", name="team-a",
                                 files={"team-a/plugin.yml": "name: team-a\n"},
                                 registry_entry="team-a")
    pr = github.prs()[0]
    assert pr["url"] == url
    assert pr["head"] == "devbase/new-plugin-team-a"
    assert pr["base"] == "main"
    assert pr["title"] == "devbase: plugin team-a を足す"
    assert pr["state"] == "MERGED"
    assert github.pushed_refs(FULL) == ["refs/heads/devbase/new-plugin-team-a"], \
        "既定ブランチへ直接 push しない (I7)"
    assert github.branches(FULL) == ["main"], "作業用のブランチが残らない"
    assert leftovers() == [], "作業場所が残らない"
    assert github.file(FULL, "team-a/plugin.yml") == "name: team-a\n"
    assert [e["name"] for e in yaml.safe_load(github.file(FULL, "registry.yml"))["plugins"]] \
        == ["team-a"]


def test_publish_change_refuses_to_overwrite(github, gh):
    github.add_repo(FULL, {**REGISTRY, "team-a/plugin.yml": "mine\n"})
    with pytest.raises(RepositoryError, match="team-a/plugin.yml"):
        publish.publish_change(gh, REF, kind="plugin", name="team-a",
                               files={"team-a/plugin.yml": "new\n"})
    assert github.write_calls() == []
    assert github.pushed_refs(FULL) == []


def test_publish_change_stops_at_an_existing_branch_with_pr(github, gh):
    github.add_repo(FULL, REGISTRY)
    publish_branch = "devbase/new-plugin-team-a"
    github.update_repo(FULL, merge_error="blocked")
    with pytest.raises(publish.MergeFailed):
        publish.publish_change(gh, REF, kind="plugin", name="team-a",
                               files={"team-a/plugin.yml": "x\n"})
    assert publish_branch in github.branches(FULL)
    url = github.prs()[0]["url"]

    with pytest.raises(RepositoryError, match=url):
        publish.publish_change(gh, REF, kind="plugin", name="team-a",
                               files={"team-a/plugin.yml": "x\n"})
    assert len(github.prs()) == 1


def test_publish_change_merge_failure_keeps_pr_url(github, gh):
    github.add_repo(FULL, REGISTRY)
    github.update_repo(FULL, merge_error="base branch policy prohibits the merge")
    with pytest.raises(publish.MergeFailed) as e:
        publish.publish_change(gh, REF, kind="plugin", name="team-a",
                               files={"team-a/plugin.yml": "x\n"})
    assert e.value.pr_url == github.prs()[0]["url"]
    assert "base branch policy" in str(e.value)
