"""``devbase plugin new`` のテスト (#514 の受け入れ条件 6〜8・16・17、決定 8・9)。"""

from __future__ import annotations

import subprocess

import pytest
import yaml

from devbase.commands.plugin import cmd_plugin_list
from devbase.plugin.installer import install_plugin
from devbase.plugin.models import RegisteredRepository
from devbase.plugin.registry import PluginRegistry
from devbase.plugin.repo_manager import add_repository

FULL = "owner/repo"
URL = "https://github.com/owner/repo.git"
REGISTRY = "# 説明\nname: repo\nplugins: []\n"


def _plugins_yml(root):
    path = root / "plugins.yml"
    return path.read_text() if path.exists() else None


def _registry_names(github):
    return [e["name"] for e in yaml.safe_load(github.file(FULL, "registry.yml"))["plugins"]]


@pytest.fixture
def registered(github, root):
    github.add_repo(FULL, {"registry.yml": REGISTRY})
    add_repository(PluginRegistry(root), URL)
    return root / "repos" / "github.com--owner--repo"


def _assert_installed(root, name="team-a", source=URL):
    registry = PluginRegistry(root)
    plugin = registry.get(name)
    assert plugin is not None
    assert plugin.source == source
    assert (root / plugin.path / "plugin.yml").is_file()
    return plugin


def test_registered_repo_gets_a_plugin(github, root, registered, run_cli, capsys):
    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 0
    pr = github.prs()[0]
    assert pr["head"] == "devbase/new-plugin-team-a"
    assert pr["state"] == "MERGED"
    assert github.file(FULL, "team-a/plugin.yml") is not None
    assert github.file(FULL, "team-a/projects/.gitkeep") is not None
    text = github.file(FULL, "registry.yml")
    assert text.startswith("# 説明\n"), "registry.yml のコメントが残る"
    assert _registry_names(github) == ["team-a"]
    assert (registered / "team-a" / "plugin.yml").is_file(), "手元へ取り込む"
    _assert_installed(root)
    capsys.readouterr()
    cmd_plugin_list(root)
    assert "team-a" in capsys.readouterr().out


def test_default_name_is_the_repo_name(github, root, registered, run_cli):
    assert run_cli("plugin", "new", FULL, "--yes") == 0
    _assert_installed(root, "repo")


@pytest.mark.parametrize("prepare, first_pushes, first_titles", [
    ("absent", ["refs/heads/main"], ["devbase: repo repo を足す"]),
    ("empty", ["refs/heads/main"], ["devbase: repo repo を足す"]),
    ("no-registry", ["refs/heads/devbase/new-repo-repo"], ["seed", "devbase: repo repo を足す"]),
    ("registry", [], ["seed"]),
])
def test_unregistered_goes_through_the_repo_branch_first(
        github, root, run_cli, prepare, first_pushes, first_titles):
    github.add_org("owner", role="admin")
    if prepare == "empty":
        github.add_repo(FULL)
    elif prepare == "no-registry":
        github.add_repo(FULL, {"README.md": "x\n"})
    elif prepare == "registry":
        github.add_repo(FULL, {"registry.yml": REGISTRY})

    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 0

    assert github.pushed_refs(FULL) == [*first_pushes, "refs/heads/devbase/new-plugin-team-a"]
    assert github.subjects(FULL) == [*first_titles, "devbase: plugin team-a を足す"], \
        "リポジトリの分岐 → プラグインの PR の順に別の変更で入る (決定 8)"
    _assert_installed(root)


def test_absent_is_private_unless_public(github, root, run_cli):
    github.add_org("owner", role="admin")
    assert run_cli("plugin", "new", FULL, "--public", "--yes") == 0
    assert github.state()["repos"][FULL]["visibility"] == "public"


def test_public_does_not_change_an_existing_repo(github, root, registered, run_cli):
    assert run_cli("plugin", "new", FULL, "--public", "--yes") == 0
    assert [c for c in github.write_calls() if c[:2] == ["repo", "create"]] == []


@pytest.mark.parametrize("setup", ["no-push", "cannot-create"])
def test_repo_branch_that_ends_writes_nothing(github, root, run_cli, setup):
    if setup == "no-push":
        github.add_repo(FULL, {"README.md": "x\n"}, permission="READ")
    else:
        github.add_org("owner", role="member", state="pending")
    assert run_cli("plugin", "new", FULL, "--yes") == 1
    assert github.write_calls() == []
    assert _plugins_yml(root) is None


def test_duplicate_in_registry_changes_nothing(github, root, run_cli, caplog):
    text = "name: repo\nplugins:\n  - name: team-a\n    path: other\n"
    github.add_repo(FULL, {"registry.yml": text})
    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 1
    assert github.write_calls() == []
    assert _plugins_yml(root) is None
    assert "team-a" in caplog.text


def test_duplicate_directory_changes_nothing(github, root, run_cli):
    github.add_repo(FULL, {"registry.yml": REGISTRY, "team-a/README.md": "x\n"})
    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 1
    assert github.write_calls() == []


def test_duplicate_installed_name_changes_nothing(github, root, registered, run_cli):
    other = "other/repo"
    github.add_repo(other, {
        "registry.yml": "name: other\nplugins:\n  - name: team-a\n    path: team-a\n",
        "team-a/plugin.yml": "name: team-a\n",
    })
    registry = PluginRegistry(root)
    add_repository(registry, f"https://github.com/{other}.git")
    from devbase.plugin.installer import install_plugin
    install_plugin(registry, f"https://github.com/{other}.git:team-a")
    before = _plugins_yml(root)
    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 1
    assert _plugins_yml(root) == before
    assert github.write_calls() == []


@pytest.mark.parametrize("name", ["../up", "a/b", "-x", ".hidden", "a b"])
def test_bad_name_form_stops_before_gh_writes(github, root, registered, run_cli, name):
    assert run_cli("plugin", "new", FULL, f"--name={name}", "--yes") == 1
    assert github.write_calls() == []


@pytest.mark.parametrize("registered_url, value", [
    ("git@github.com:owner/repo.git", "owner/repo"),
    ("https://github.com/owner/repo", "git@github.com:owner/repo.git"),
    ("https://github.com/owner/repo", "https://github.com/owner/repo.git"),
])
def test_url_form_difference_uses_the_registered_path(
        github, root, run_cli, registered_url, value):
    github.add_repo(FULL, {"registry.yml": REGISTRY})
    add_repository(PluginRegistry(root), registered_url)
    assert run_cli("plugin", "new", value, "--name", "team-a", "--yes") == 0
    urls = [r.url for r in PluginRegistry(root).list_repositories()]
    assert urls == [registered_url], "plugins.yml の url は変わらない"
    _assert_installed(root, source=registered_url)
    # 同じ入力の repo new は何もしない
    calls = len(github.write_calls())
    assert run_cli("plugin", "repo", "new", value) == 0
    assert len(github.write_calls()) == calls


@pytest.mark.parametrize("state", ["empty-path", "missing-dir"])
def test_registration_without_clone_stops_before_writing(github, root, run_cli, caplog, state):
    github.add_repo(FULL, {"registry.yml": REGISTRY})
    local_path = "" if state == "empty-path" else "repos/github.com--owner--repo"
    PluginRegistry(root).add_repository(RegisteredRepository(
        name="r", url=URL, local_path=local_path))
    before = _plugins_yml(root)
    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 1
    assert github.write_calls() == []
    assert _plugins_yml(root) == before
    assert "plugin repo remove r" in caplog.text
    assert f"plugin repo add {URL}" in caplog.text
    # 同じ登録へ repo new は何もせず 0
    assert run_cli("plugin", "repo", "new", FULL) == 0


def test_uncommitted_changes_survive(github, root, registered, run_cli):
    (registered / "note.txt").write_text("mine\n")
    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 0
    assert (registered / "note.txt").read_text() == "mine\n"
    status = subprocess.run(["git", "status", "--porcelain"], cwd=registered,
                            check=True, capture_output=True, text=True).stdout
    assert status.strip() == "?? note.txt", "clone の作業ツリーを汚さない"


def test_colliding_change_stops_and_is_kept(github, root, registered, run_cli, caplog):
    edited = REGISTRY + "# 手元の編集\n"
    (registered / "registry.yml").write_text(edited)
    before = _plugins_yml(root)
    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 1
    assert (registered / "registry.yml").read_text() == edited
    assert _plugins_yml(root) == before
    assert "git -C" in caplog.text


def test_diverged_clone_stops_with_commands(github, root, registered, run_cli, caplog):
    subprocess.run(["git", "commit", "--allow-empty", "-m", "local"], cwd=registered, check=True,
                   capture_output=True)
    before = _plugins_yml(root)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=registered, check=True,
                          capture_output=True, text=True).stdout
    github.commit(FULL, {"other.txt": "x\n"})
    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 1
    assert _plugins_yml(root) == before
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=registered, check=True,
                          capture_output=True, text=True).stdout == head
    assert "git -C" in caplog.text
    assert "devbase plugin repo refresh" in caplog.text
    assert "devbase plugin install" in caplog.text


@pytest.mark.parametrize("prepare", ["registered", "unregistered"])
def test_merge_failure_keeps_plugins_yml(github, root, run_cli, caplog, prepare):
    github.add_repo(FULL, {"registry.yml": REGISTRY}, merge_error="required reviews")
    if prepare == "registered":
        add_repository(PluginRegistry(root), URL)
    before = _plugins_yml(root)
    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 1
    assert _plugins_yml(root) == before
    assert github.prs()[0]["url"] in caplog.text
    assert "devbase plugin install" in caplog.text
    assert "devbase plugin repo" in caplog.text


def test_registry_pr_merge_failure_asks_to_rerun(github, root, run_cli, caplog):
    """プラグインの PR がまだ無いので、登録と導入ではなく打ち直しを案内する。"""
    github.add_repo(FULL, {"README.md": "x\n"}, merge_error="required reviews")
    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 1
    assert [pr["head"] for pr in github.prs()] == ["devbase/new-repo-repo"]
    assert github.prs()[0]["url"] in caplog.text
    assert f"devbase plugin new {URL} --name team-a" in caplog.text
    assert "devbase plugin install" not in caplog.text
    assert "devbase plugin repo add" not in caplog.text
    assert _plugins_yml(root) is None


def test_ssh_registration_clones_and_pushes_over_ssh(github, root, run_cli):
    """一時の clone と push は入力から作った https ではなく、登録の URL で走る。"""
    url = "git@github.com:owner/repo.git"
    github.add_repo(FULL, {"registry.yml": REGISTRY})
    add_repository(PluginRegistry(root), url)
    github.ssh_only()
    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 0
    _assert_installed(root, source=url)


def test_rename_in_the_pulled_update_keeps_the_project(github, root, run_cli):
    """取り込みでプラグインが移っても、移行先を導入してプロジェクトのリンクを残す。"""
    github.add_repo(FULL, {
        "registry.yml": "name: repo\nplugins:\n  - name: old\n    path: old\n",
        "old/plugin.yml": "name: old\nversion: '1.0.0'\n",
        "old/projects/p1/compose.yml": "services: {}\n",
    })
    registry = PluginRegistry(root)
    add_repository(registry, URL)
    install_plugin(registry, f"{URL}:old")
    assert (root / "projects" / "p1").is_dir()

    github.commit(FULL, {
        "registry.yml": "name: repo\nplugins:\n  - name: moved\n    path: moved\n",
        "moved/plugin.yml": "name: moved\nversion: '1.0.0'\n",
        "moved/projects/p1/compose.yml": "services: {}\n",
    }, remove=["old"])
    assert run_cli("plugin", "new", FULL, "--name", "team-a", "--yes") == 0

    registry = PluginRegistry(root)
    assert registry.get("old") is None
    assert registry.get("moved") is not None
    assert (root / "projects" / "p1").is_dir()


def test_never_touches_docker(github, root, registered, run_cli):
    assert run_cli("plugin", "new", FULL, "--yes") == 0
    assert github.docker_calls() == []


def test_prefix_of_new_resolves(github, root, registered, run_cli):
    assert run_cli("pl", "n", FULL, "--yes") == 0


def test_no_tty_without_yes_stops(github, root, registered, run_cli):
    assert run_cli("plugin", "new", FULL) == 1
    assert github.write_calls() == []
