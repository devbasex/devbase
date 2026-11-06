"""引数の解決 (GitHubRef) と gh の呼び出し (GhClient) のテスト (#514)。"""

from __future__ import annotations

import subprocess

import pytest

from devbase.errors import RepositoryError
from devbase.plugin import github
from devbase.plugin.github import GhClient, GitHubRef


@pytest.mark.parametrize("value, clone_url", [
    ("owner/repo", "https://github.com/owner/repo.git"),
    ("https://github.com/owner/repo", "https://github.com/owner/repo.git"),
    ("https://github.com/owner/repo/", "https://github.com/owner/repo.git"),
    ("https://github.com/owner/repo.git", "https://github.com/owner/repo.git"),
    ("git@github.com:owner/repo.git", "git@github.com:owner/repo.git"),
    ("git@github.com:owner/repo", "git@github.com:owner/repo.git"),
])
def test_parse_accepts_every_form(value, clone_url):
    ref = GitHubRef.parse(value)
    assert (ref.owner, ref.repo) == ("owner", "repo")
    assert ref.full_name == "owner/repo"
    assert ref.clone_url == clone_url


@pytest.mark.parametrize("value", [
    "",
    "repo",
    "owner/repo/extra",
    "https://gitlab.com/owner/repo",
    "https://github.com.example.com/owner/repo",
    "https://example.com/github.com/owner/repo",
    "git@gitlab.com:owner/repo.git",
    "ssh://git@github.com/owner/repo.git",
    "/local/path",
    "./owner/repo",
    "-owner/repo",
    "owner/-repo",
    "owner/..",
    "owner/.git",
    "own er/repo",
])
def test_parse_rejects_anything_else(value):
    with pytest.raises(RepositoryError):
        GitHubRef.parse(value)


def _completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


class _Script:
    """gh の引数の接頭辞ごとに結果を返す subprocess.run の代役。"""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, cmd, **_kwargs):
        assert cmd[0] == "gh"
        self.calls.append(cmd[1:])
        for prefix, result in self.table:
            if cmd[1:len(prefix) + 1] == prefix:
                if callable(result):
                    return result(cmd)
                return _completed(cmd, *result)
        raise AssertionError(f"unexpected gh call: {cmd}")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(github.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(github.time, "sleep", lambda _s: None)

    def _make(table):
        script = _Script(table)
        monkeypatch.setattr(github.subprocess, "run", script)
        return GhClient(), script
    return _make


REF = GitHubRef("owner", "repo", "https://github.com/owner/repo.git")


def test_ensure_ready_without_gh_tells_what_to_do(monkeypatch):
    monkeypatch.setattr(github.shutil, "which", lambda name: None)
    with pytest.raises(RepositoryError, match="gh"):
        GhClient().ensure_ready()


def test_ensure_ready_unauthenticated_tells_login(client):
    gh, _ = client([(["api", "user"], (1, "", "To get started, please run: gh auth login"))])
    with pytest.raises(RepositoryError, match="gh auth login"):
        gh.ensure_ready()


def test_ensure_ready_ok(client):
    gh, script = client([(["api", "user"], (0, '{"login": "me"}'))])
    gh.ensure_ready()
    assert gh.login() == "me"
    assert len(script.calls) == 1, "login は 1 回だけ問い合わせる"


def test_view_repo_missing_is_none(client):
    gh, _ = client([(["repo", "view"], (1, "", "GraphQL: Could not resolve to a Repository"))])
    assert gh.view_repo(REF) is None


def test_view_repo_other_error_stops(client):
    gh, _ = client([(["repo", "view"], (1, "", "connection refused"))])
    with pytest.raises(RepositoryError, match="connection refused"):
        gh.view_repo(REF)


@pytest.mark.parametrize("permission, can_push", [
    ("ADMIN", True), ("MAINTAIN", True), ("WRITE", True),
    ("TRIAGE", False), ("READ", False), ("", False),
])
def test_view_repo_can_push(client, permission, can_push):
    body = ('{"isEmpty": false, "defaultBranchRef": {"name": "main"}, '
            f'"viewerPermission": "{permission}"}}')
    gh, _ = client([(["repo", "view"], (0, body))])
    view = gh.view_repo(REF)
    assert view.can_push is can_push
    assert view.default_branch == "main"
    assert view.empty is False


def test_view_repo_empty_has_no_default_branch(client):
    body = '{"isEmpty": true, "defaultBranchRef": null, "viewerPermission": "ADMIN"}'
    gh, _ = client([(["repo", "view"], (0, body))])
    view = gh.view_repo(REF)
    assert view.empty is True
    assert view.default_branch == ""


@pytest.mark.parametrize("owner, membership, org, expected", [
    ("me", None, None, True),
    ("ME", None, None, True),
    ("team-a", '{"state": "active", "role": "admin"}', None, True),
    ("team-a", '{"state": "active", "role": "member"}',
     '{"members_can_create_repositories": true}', True),
    ("team-a", '{"state": "active", "role": "member"}',
     '{"members_can_create_repositories": false}', False),
    ("team-a", '{"state": "pending", "role": "admin"}', None, False),
    ("team-a", False, None, False),
])
def test_can_create_in(client, owner, membership, org, expected):
    table = [(["api", "user"], (0, '{"login": "me"}'))]
    if membership is False:
        table.insert(0, (["api", f"user/memberships/orgs/{owner}"],
                         (1, "", "gh: Not Found (HTTP 404)")))
    elif membership:
        table.insert(0, (["api", f"user/memberships/orgs/{owner}"], (0, membership)))
    if org:
        table.insert(0, (["api", f"orgs/{owner}"], (0, org)))
    gh, _ = client(table)
    assert gh.can_create_in(owner) is expected


def test_read_file_missing_is_none(client):
    gh, _ = client([(["api", "repos/owner/repo/contents/registry.yml"],
                     (1, "", "gh: Not Found (HTTP 404)"))])
    assert gh.read_file(REF, "registry.yml") is None


def test_read_file_returns_text(client):
    gh, _ = client([(["api", "repos/owner/repo/contents/registry.yml"], (0, "name: x\n"))])
    assert gh.read_file(REF, "registry.yml") == "name: x\n"


def test_read_file_other_error_stops(client):
    gh, _ = client([(["api", "repos/owner/repo/contents/registry.yml"],
                     (1, "", "gh: Server Error (HTTP 500)"))])
    with pytest.raises(RepositoryError, match="HTTP 500"):
        gh.read_file(REF, "registry.yml")


@pytest.mark.parametrize("public, flag", [(False, "--private"), (True, "--public")])
def test_create_repo_visibility(client, public, flag):
    gh, script = client([(["repo", "create"], (0,))])
    gh.create_repo(REF, public=public)
    assert script.calls == [["repo", "create", "owner/repo", flag]]


def test_create_repo_failure_carries_gh_output(client):
    gh, _ = client([(["repo", "create"], (1, "", "Name already exists on this account"))])
    with pytest.raises(RepositoryError, match="Name already exists"):
        gh.create_repo(REF, public=False)


def test_create_pr_returns_url(client):
    gh, script = client([(["pr", "create"], (0, "https://github.com/owner/repo/pull/7\n"))])
    url = gh.create_pr(REF, base="main", head="devbase/new-plugin-x", title="t", body="b")
    assert url == "https://github.com/owner/repo/pull/7"
    assert script.calls[0][:4] == ["pr", "create", "--repo", "owner/repo"]


PR = "https://github.com/owner/repo/pull/7"
ALLOWED_ALL = '{"mergeCommitAllowed": true, "squashMergeAllowed": true, "rebaseMergeAllowed": true}'


@pytest.mark.parametrize("allowed, flag", [
    (ALLOWED_ALL, "--merge"),
    ('{"mergeCommitAllowed": false, "squashMergeAllowed": true, "rebaseMergeAllowed": true}',
     "--squash"),
    ('{"mergeCommitAllowed": false, "squashMergeAllowed": false, "rebaseMergeAllowed": true}',
     "--rebase"),
])
def test_merge_pr_picks_the_allowed_method(client, allowed, flag):
    gh, script = client([
        (["pr", "view"], (0, '{"mergeable": "MERGEABLE"}')),
        (["repo", "view"], (0, allowed)),
        (["pr", "merge"], (0,)),
    ])
    gh.merge_pr(REF, PR)
    merges = [c for c in script.calls if c[:2] == ["pr", "merge"]]
    assert merges == [["pr", "merge", PR, "--repo", "owner/repo", flag, "--delete-branch"]]


def test_merge_pr_waits_for_unknown_then_merges_once(client):
    states = iter(['{"mergeable": "UNKNOWN"}', '{"mergeable": "UNKNOWN"}',
                   '{"mergeable": "MERGEABLE"}'])
    gh, script = client([
        (["pr", "view"], lambda cmd: _completed(cmd, 0, next(states))),
        (["repo", "view"], (0, ALLOWED_ALL)),
        (["pr", "merge"], (0,)),
    ])
    gh.merge_pr(REF, PR)
    assert len([c for c in script.calls if c[:2] == ["pr", "merge"]]) == 1


def test_merge_pr_never_uses_auto_or_admin(client):
    gh, script = client([
        (["pr", "view"], (0, '{"mergeable": "MERGEABLE"}')),
        (["repo", "view"], (0, ALLOWED_ALL)),
        (["pr", "merge"], (0,)),
    ])
    gh.merge_pr(REF, PR)
    used = {arg for call in script.calls for arg in call}
    assert not used & {"--auto", "--admin"}


def test_merge_pr_gives_up_when_unknown_lasts(client):
    gh, script = client([(["pr", "view"], (0, '{"mergeable": "UNKNOWN"}'))])
    with pytest.raises(RepositoryError):
        gh.merge_pr(REF, PR)
    assert not [c for c in script.calls if c[:2] == ["pr", "merge"]]


def test_merge_pr_conflict_stops_without_merging(client):
    gh, script = client([(["pr", "view"], (0, '{"mergeable": "CONFLICTING"}'))])
    with pytest.raises(RepositoryError, match="CONFLICTING"):
        gh.merge_pr(REF, PR)
    assert not [c for c in script.calls if c[:2] == ["pr", "merge"]]


def test_merge_pr_failure_is_tried_once(client):
    gh, script = client([
        (["pr", "view"], (0, '{"mergeable": "MERGEABLE"}')),
        (["repo", "view"], (0, ALLOWED_ALL)),
        (["pr", "merge"], (1, "", "base branch policy prohibits the merge")),
    ])
    with pytest.raises(RepositoryError, match="base branch policy"):
        gh.merge_pr(REF, PR)
    assert len([c for c in script.calls if c[:2] == ["pr", "merge"]]) == 1


def test_find_open_pr(client):
    gh, _ = client([(["pr", "list"], (0, f'[{{"url": "{PR}"}}]'))])
    assert gh.find_open_pr(REF, "devbase/new-plugin-x") == PR


def test_find_open_pr_none(client):
    gh, _ = client([(["pr", "list"], (0, "[]"))])
    assert gh.find_open_pr(REF, "devbase/new-plugin-x") is None
