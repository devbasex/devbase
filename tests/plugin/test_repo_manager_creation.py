"""作成フローが足した _find_registered / _pull_ff_only のテスト (#514)。"""

from __future__ import annotations

import subprocess

import pytest

from devbase.errors import RepositoryError
from devbase.plugin.models import RegisteredRepository
from devbase.plugin.registry import PluginRegistry
from devbase.plugin.repo_manager import (
    _find_registered,
    _pull_ff_only,
    _require_default_branch,
)


@pytest.fixture
def registry(tmp_path):
    return PluginRegistry(tmp_path)


@pytest.mark.parametrize("registered, given", [
    ("https://github.com/owner/repo.git", "https://github.com/owner/repo.git"),
    ("https://github.com/owner/repo", "https://github.com/owner/repo.git"),
    ("git@github.com:owner/repo.git", "https://github.com/owner/repo.git"),
    ("https://github.com/owner/repo.git", "git@github.com:owner/repo.git"),
])
def test_find_registered_ignores_the_url_form(registry, registered, given):
    registry.add_repository(RegisteredRepository(name="r", url=registered))
    found = _find_registered(registry, given)
    assert found is not None
    assert found.url == registered


def test_find_registered_none(registry):
    registry.add_repository(RegisteredRepository(
        name="r", url="https://github.com/owner/other.git"))
    assert _find_registered(registry, "https://github.com/owner/repo.git") is None
    assert _find_registered(registry, "https://gitlab.com/owner/other.git") is None


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                           *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def remote_and_clone(tmp_path):
    """origin と、その clone と、origin へ push する別の clone の組。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   check=True, capture_output=True)
    writer = tmp_path / "writer"
    subprocess.run(["git", "clone", str(origin), str(writer)], check=True, capture_output=True)
    _git(writer, "checkout", "-b", "main")
    (writer / "a.txt").write_text("1\n")
    _git(writer, "add", "-A")
    _git(writer, "commit", "-m", "one")
    _git(writer, "push", "origin", "main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], check=True, capture_output=True)
    return writer, clone


def _advance(writer, name="b.txt"):
    (writer / name).write_text("new\n")
    _git(writer, "add", "-A")
    _git(writer, "commit", "-m", "two")
    _git(writer, "push", "origin", "main")


def test_pull_ff_only_advances(remote_and_clone):
    writer, clone = remote_and_clone
    _advance(writer)
    _pull_ff_only(clone, "main")
    assert (clone / "b.txt").is_file()


def test_pull_ff_only_keeps_uncommitted_changes(remote_and_clone):
    writer, clone = remote_and_clone
    _advance(writer)
    (clone / "a.txt").write_text("edited\n")
    (clone / "note.txt").write_text("untracked\n")
    _pull_ff_only(clone, "main")
    assert (clone / "b.txt").is_file()
    assert (clone / "a.txt").read_text() == "edited\n"
    assert (clone / "note.txt").read_text() == "untracked\n"


def test_pull_ff_only_stops_when_changes_collide(remote_and_clone):
    writer, clone = remote_and_clone
    _advance(writer, "note.txt")
    (clone / "note.txt").write_text("mine\n")
    with pytest.raises(RepositoryError, match="status"):
        _pull_ff_only(clone, "main")
    assert (clone / "note.txt").read_text() == "mine\n"


def test_pull_ff_only_stops_when_diverged(remote_and_clone):
    writer, clone = remote_and_clone
    _advance(writer)
    (clone / "c.txt").write_text("local\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-m", "local")
    head = _git(clone, "rev-parse", "HEAD").stdout
    with pytest.raises(RepositoryError):
        _pull_ff_only(clone, "main")
    assert _git(clone, "rev-parse", "HEAD").stdout == head, "マージコミットを作らない"


def test_pull_ff_only_stops_on_other_branch(remote_and_clone):
    writer, clone = remote_and_clone
    _advance(writer)
    _git(clone, "checkout", "-b", "work")
    with pytest.raises(RepositoryError, match="work"):
        _pull_ff_only(clone, "main")
    assert not (clone / "b.txt").exists()


def test_pull_ff_only_stops_on_detached_head(remote_and_clone):
    _, clone = remote_and_clone
    _git(clone, "checkout", "--detach")
    with pytest.raises(RepositoryError):
        _pull_ff_only(clone, "main")


def test_pull_ff_only_stops_when_default_branch_moved(remote_and_clone):
    """origin/HEAD が古くても、GitHub の既定ブランチと比べて止める。"""
    writer, clone = remote_and_clone
    _advance(writer)
    with pytest.raises(RepositoryError, match="develop"):
        _pull_ff_only(clone, "develop")
    assert not (clone / "b.txt").exists()


def test_require_default_branch_stops_on_other_upstream(remote_and_clone):
    """同じ名前でも、別のブランチを追っていれば止める。"""
    writer, clone = remote_and_clone
    _git(writer, "push", "origin", "main:other")
    _git(clone, "fetch", "origin")
    _git(clone, "branch", "--set-upstream-to=origin/other", "main")
    with pytest.raises(RepositoryError, match="origin/other"):
        _require_default_branch(clone, "main")


def test_require_default_branch_stops_without_upstream(remote_and_clone):
    _, clone = remote_and_clone
    _git(clone, "branch", "--unset-upstream")
    with pytest.raises(RepositoryError, match="upstream"):
        _require_default_branch(clone, "main")
