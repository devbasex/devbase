"""``devbase project create`` のテスト (#514 の受け入れ条件 9〜13)。"""

from __future__ import annotations

import subprocess

import pytest
import yaml

from devbase.commands.project import list_projects
from devbase.plugin import creator
from devbase.plugin.github import GhClient, GitHubRef
from devbase.plugin.installer import install_plugin
from devbase.plugin.models import AvailablePlugin, InstalledPlugin, RegisteredRepository
from devbase.plugin.registry import PluginRegistry
from devbase.plugin.repo_manager import add_repository
from devbase.project.config import parse_project_config

APP = "owner/app"


def _plugins_yml(root):
    path = root / "plugins.yml"
    return path.read_text() if path.exists() else None


def _plugin_repo(github, root, full, plugin="team-a", groups=(), install=True, **values):
    """プラグインを 1 つ収めたリポジトリを用意して登録・導入する。"""
    files = {
        "registry.yml": f"name: {full.split('/')[1]}\nplugins:\n"
                        f"  - name: {plugin}\n    path: {plugin}\n",
        f"{plugin}/plugin.yml": f"name: {plugin}\nversion: '1.0.0'\n",
        f"{plugin}/projects/.gitkeep": "",
    }
    for index, group in enumerate(groups):
        files[f"{plugin}/projects/p{index}/project.yml"] = "version: 1\n"
        files[f"{plugin}/projects/p{index}/env"] = f"DEVBASE_ACCOUNT_GROUP={group}\n"
    github.add_repo(full, files, **values)
    registry = PluginRegistry(root)
    url = f"https://github.com/{full}.git"
    add_repository(registry, url)
    if install:
        install_plugin(registry, f"{url}:{plugin}")
    return root / "repos" / f"github.com--{full.replace('/', '--')}"


@pytest.fixture
def app(github):
    github.add_repo(APP, {"README.md": "x\n"}, permission="READ")
    return APP


def test_name_outside_docker_rules_gets_a_valid_image(github, root, app, run_cli):
    clone = _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    assert run_cli("project", "create", APP, "--name", "app-", "--yes") == 0

    compose = yaml.safe_load((clone / "team-a/projects/app-/compose.yml").read_text())
    assert compose["services"]["dev"]["image"] == "app:latest"


@pytest.mark.parametrize("repo, project", [
    ("MyApp", "myapp"),
    ("foo.github.io", "foo-github-io"),
    ("a..b", "a-b"),
])
def test_default_name_follows_compose_rules(github, root, run_cli, repo, project):
    github.add_repo(f"owner/{repo}", {"README.md": "x\n"}, permission="READ")
    clone = _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    assert run_cli("project", "create", f"owner/{repo}", "--yes") == 0

    assert (clone / f"team-a/projects/{project}/project.yml").is_file()
    assert (root / "projects" / project).exists()
    assert not (root / "projects" / repo).exists()


@pytest.mark.parametrize("name", ["MyApp", "my.app", "app..web", "app.", "team.lock"])
def test_name_outside_compose_rules_stops_before_writing(
        github, root, app, run_cli, caplog, name):
    clone = _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    before = _plugins_yml(root)
    assert run_cli("project", "create", APP, "--name", name, "--yes") == 1

    assert "小文字の英数字で始まり" in caplog.text
    assert github.write_calls() == []
    assert not (clone / f"team-a/projects/{name}").exists()
    assert _plugins_yml(root) == before


def test_project_is_added_and_reflected(github, root, app, run_cli):
    clone = _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    assert run_cli("project", "create", APP, "--yes") == 0

    pr = github.prs()[0]
    assert pr["head"] == "devbase/create-project-app"
    assert pr["state"] == "MERGED"
    base = "team-a/projects/app"
    full = "owner/plugins"
    config = parse_project_config(
        yaml.safe_load(github.file(full, f"{base}/project.yml")), "project.yml")
    assert (config.primary.owner, config.primary.repo) == ("owner", "app")
    assert "dev" in yaml.safe_load(github.file(full, f"{base}/compose.yml"))["services"]
    assert github.file(full, f"{base}/env") == "DEVBASE_ACCOUNT_GROUP=acme\n"

    assert (clone / base / "project.yml").is_file(), "手元へ取り込む"
    assert (root / "projects" / "app" / "project.yml").is_file(), "projects/ へ反映する"
    names = [row["name"] for row in list_projects(root / "projects")]
    assert names.count("app") == 1


def test_name_option(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    assert run_cli("project", "create", APP, "--name", "web", "--yes") == 0
    assert (root / "projects" / "web").is_dir()
    assert not (root / "projects" / "app").exists()
    assert github.file("owner/plugins", "team-a/projects/web/project.yml") is not None


def test_plan_shows_target_and_group(github, root, app, run_cli, capsys):
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    assert run_cli("project", "create", APP) == 1, "TTY が無く --yes も無い"
    out = capsys.readouterr().out
    assert "team-a" in out and "acme" in out and "owner/plugins" in out and "main" in out
    assert github.write_calls() == []


# ── 入れ先の決定 ────────────────────────────────────────────────


def test_plugin_option_wins(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/one", plugin="one", groups=["acme"])
    _plugin_repo(github, root, "owner/two", plugin="two", groups=["acme"])
    assert run_cli("project", "create", APP, "--plugin", "two", "--yes") == 0
    assert github.file("owner/two", "two/projects/app/env") is not None


def test_plugin_option_requires_install(github, root, app, run_cli, caplog):
    _plugin_repo(github, root, "owner/one", plugin="one", groups=["acme"], install=False)
    assert run_cli("project", "create", APP, "--plugin", "one", "--yes") == 1
    assert "one" in caplog.text
    assert github.write_calls() == []


def test_plugin_option_requires_push(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/one", plugin="one", groups=["acme"], permission="READ")
    assert run_cli("project", "create", APP, "--plugin", "one", "--yes") == 1
    assert github.write_calls() == []


def test_same_owner_narrows_the_candidates(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/one", plugin="one", groups=["acme"])
    _plugin_repo(github, root, "other/two", plugin="two", groups=["acme"])
    assert run_cli("project", "create", APP, "--yes") == 0
    assert github.file("owner/one", "one/projects/app/env") is not None


def test_no_same_owner_keeps_all_candidates(github, root, app, run_cli, caplog):
    _plugin_repo(github, root, "other/one", plugin="one", groups=["acme"])
    _plugin_repo(github, root, "other/two", plugin="two", groups=["acme"])
    assert run_cli("project", "create", APP, "--yes") == 1
    assert "one" in caplog.text and "two" in caplog.text
    assert github.write_calls() == []


def test_unpushable_are_not_candidates(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/one", plugin="one", groups=["acme"], permission="READ")
    _plugin_repo(github, root, "other/two", plugin="two", groups=["acme"])
    assert run_cli("project", "create", APP, "--yes") == 0
    assert github.file("other/two", "two/projects/app/env") is not None


def test_multiple_with_tty_asks(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/one", plugin="one", groups=["acme"])
    _plugin_repo(github, root, "owner/two", plugin="two", groups=["acme"])
    assert run_cli("project", "create", APP, "--yes", tty=True, answers=["2"]) == 0
    assert github.file("owner/two", "two/projects/app/env") is not None


def test_multiple_with_tty_bad_answer_stops(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/one", plugin="one", groups=["acme"])
    _plugin_repo(github, root, "owner/two", plugin="two", groups=["acme"])
    assert run_cli("project", "create", APP, "--yes", tty=True, answers=["9"]) == 1
    assert github.write_calls() == []


def test_zero_candidates_stops(github, root, app, run_cli):
    assert run_cli("project", "create", APP, "--yes") == 1
    assert github.write_calls() == []


def test_linked_and_non_github_plugins_are_not_candidates(github, root, app):
    registry = PluginRegistry(root)
    registry.add_repository(RegisteredRepository(
        name="local", url="/some/path",
        plugins=[AvailablePlugin(name="here", path="here")]))
    registry.add(InstalledPlugin(name="here", version="1.0", source="/some/path",
                                 installed_at="", path="repos/local/here"))
    registry.add_repository(RegisteredRepository(
        name="gitlab", url="https://gitlab.com/owner/g.git",
        plugins=[AvailablePlugin(name="there", path="there")]))
    registry.add(InstalledPlugin(name="there", version="1.0",
                                 source="https://gitlab.com/owner/g.git",
                                 installed_at="", path="repos/g/there"))
    github.add_repo("owner/linked", {"README.md": "x\n"})
    registry.add_repository(RegisteredRepository(
        name="linked", url="https://github.com/owner/linked.git",
        plugins=[AvailablePlugin(name="dev", path="dev")]))
    registry.add(InstalledPlugin(name="dev", version="1.0",
                                 source="https://github.com/owner/linked.git",
                                 installed_at="", path="plugins/dev", linked=True))
    ref = GitHubRef.parse(APP)
    assert creator.plugin_candidates(registry, GhClient(), ref) == []


def test_gh_is_called_once_per_repository(github, root, app):
    clone = _plugin_repo(github, root, "owner/multi", plugin="one", groups=["acme"])
    assert clone.is_dir()
    registry = PluginRegistry(root)
    # 同じリポジトリの 2 つ目のプラグイン
    github.commit("owner/multi", {
        "registry.yml": "name: multi\nplugins:\n  - name: one\n    path: one\n"
                        "  - name: two\n    path: two\n",
        "two/plugin.yml": "name: two\n",
    })
    from devbase.plugin.repo_manager import refresh_repository
    refresh_repository(registry, registry.list_repositories()[0].name)
    install_plugin(registry, "https://github.com/owner/multi.git:two")
    before = len(github.calls())
    assert len(creator.plugin_candidates(registry, GhClient(), GitHubRef.parse(APP))) == 2
    views = [c for c in github.calls()[before:] if c[:2] == ["repo", "view"]]
    assert len(views) == 1


# ── グループの決定 ──────────────────────────────────────────────


@pytest.mark.parametrize("groups, expected", [
    (["acme", "acme", "other"], "acme"),
    (["solo"], "solo"),
])
def test_group_is_the_most_common(github, root, app, run_cli, groups, expected):
    _plugin_repo(github, root, "owner/plugins", groups=groups)
    assert run_cli("project", "create", APP, "--yes") == 0
    assert github.file("owner/plugins", "team-a/projects/app/env") \
        == f"DEVBASE_ACCOUNT_GROUP={expected}\n"


def test_group_option_wins(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/plugins", groups=["acme", "acme"])
    assert run_cli("project", "create", APP, "--group", "mine", "--yes") == 0
    assert github.file("owner/plugins", "team-a/projects/app/env") \
        == "DEVBASE_ACCOUNT_GROUP=mine\n"


@pytest.mark.parametrize("groups", [["a", "b"], []], ids=["tie", "none"])
def test_undecided_group_without_tty_stops(github, root, app, run_cli, groups):
    _plugin_repo(github, root, "owner/plugins", groups=groups)
    assert run_cli("project", "create", APP, "--yes") == 1
    assert github.write_calls() == []


@pytest.mark.parametrize("groups", [["a", "b"], []], ids=["tie", "none"])
def test_undecided_group_with_tty_asks(github, root, app, run_cli, groups):
    _plugin_repo(github, root, "owner/plugins", groups=groups)
    assert run_cli("project", "create", APP, "--yes", tty=True, answers=["typed"]) == 0
    assert github.file("owner/plugins", "team-a/projects/app/env") \
        == "DEVBASE_ACCOUNT_GROUP=typed\n"


@pytest.mark.parametrize("bad", ["default", "ubuntu", "123", "a b"])
def test_bad_group_from_option_stops(github, root, app, run_cli, bad):
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    assert run_cli("project", "create", APP, f"--group={bad}", "--yes") == 1
    assert github.write_calls() == []


def test_bad_group_from_most_common_stops(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/plugins", groups=["default"])
    assert run_cli("project", "create", APP, "--yes") == 1
    assert github.write_calls() == []


def test_bad_group_from_tty_stops(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/plugins")
    assert run_cli("project", "create", APP, "--yes", tty=True, answers=["default"]) == 1
    assert github.write_calls() == []


# ── 止まるとき ──────────────────────────────────────────────────


def test_unreadable_target_stops(github, root, run_cli):
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    assert run_cli("project", "create", APP, "--yes") == 1
    assert github.write_calls() == []


def test_existing_in_projects_stops(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    (root / "projects" / "app").mkdir()
    assert run_cli("project", "create", APP, "--yes") == 1
    assert github.write_calls() == []


def test_broken_symlink_in_projects_stops(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    (root / "projects" / "app").symlink_to("../gone")
    assert run_cli("project", "create", APP, "--yes") == 1
    assert github.write_calls() == []


def test_existing_in_the_plugin_stops(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    github.commit("owner/plugins", {"team-a/projects/app/project.yml": "version: 1\n"})
    assert run_cli("project", "create", APP, "--yes") == 1
    assert github.write_calls() == []


@pytest.mark.parametrize("name", ["../up", "a/b", "-x"])
def test_bad_name_stops(github, root, app, run_cli, name):
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    assert run_cli("project", "create", APP, f"--name={name}", "--yes") == 1
    assert github.write_calls() == []


@pytest.mark.parametrize("state", ["empty-path", "missing-dir"])
def test_registration_without_clone_stops(github, root, app, run_cli, caplog, state):
    import shutil

    clone = _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    registry = PluginRegistry(root)
    repo = registry.list_repositories()[0]
    if state == "empty-path":
        from dataclasses import replace
        registry.add_repository(replace(repo, local_path=""))
    else:
        shutil.rmtree(clone)
    before = _plugins_yml(root)
    assert run_cli("project", "create", APP, "--plugin", "team-a", "--yes") == 1
    assert github.write_calls() == []
    assert _plugins_yml(root) == before
    assert "plugin repo remove" in caplog.text
    assert "plugin repo add" in caplog.text


def test_merge_failure_leaves_the_local_side(github, root, app, run_cli, caplog):
    _plugin_repo(github, root, "owner/plugins", groups=["acme"],
                 merge_error="required reviews")
    before = _plugins_yml(root)
    assert run_cli("project", "create", APP, "--yes") == 1
    assert _plugins_yml(root) == before
    assert not (root / "projects" / "app").exists()
    assert github.prs()[0]["url"] in caplog.text
    assert "devbase plugin repo refresh" in caplog.text


MOVED_REGISTRY = "name: plugins\nplugins:\n  - name: team-a\n    path: moved\n"


def test_stale_plugin_path_stops_before_writing(github, root, app, run_cli, caplog):
    """既定ブランチで path が変わっていたら、古い path へマージしない。"""
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    github.commit("owner/plugins", {"registry.yml": MOVED_REGISTRY,
                                    "moved/plugin.yml": "name: team-a\nversion: '1.0.0'\n"})
    assert run_cli("project", "create", APP, "--yes") == 1
    assert github.write_calls() == []
    assert "devbase plugin repo refresh plugins" in caplog.text

    assert run_cli("plugin", "repo", "refresh", "plugins") == 0
    assert run_cli("project", "create", APP, "--group", "acme", "--yes") == 0
    assert github.file("owner/plugins", "moved/projects/app/project.yml") is not None


def test_plugin_gone_from_the_registry_stops(github, root, app, run_cli, caplog):
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    github.commit("owner/plugins", {"registry.yml": "name: plugins\nplugins: []\n"})
    assert run_cli("project", "create", APP, "--yes") == 1
    assert github.write_calls() == []
    assert "項目なし" in caplog.text


ROOT_REGISTRY = "name: plugins\nplugins:\n  - name: team-a\n    description: x\n"


def _root_plugin_repo(github, root, full="owner/plugins"):
    """registry.yml の項目に path が無い、リポジトリの直下のプラグインを導入する。"""
    github.add_repo(full, {
        "registry.yml": ROOT_REGISTRY,
        "plugin.yml": "name: team-a\nversion: '1.0.0'\n",
        "projects/.gitkeep": "",
    })
    registry = PluginRegistry(root)
    url = f"https://github.com/{full}.git"
    add_repository(registry, url)
    install_plugin(registry, f"{url}:team-a")
    return root / "repos" / f"github.com--{full.replace('/', '--')}"


def test_plugin_at_the_repository_root(github, root, app, run_cli):
    """path の無い項目は導入と同じく直下として扱い、projects/ へ現れる。"""
    clone = _root_plugin_repo(github, root)
    assert run_cli("project", "create", APP, "--group", "acme", "--yes") == 0
    assert github.file("owner/plugins", "projects/app/project.yml") is not None
    assert github.file("owner/plugins", "team-a/projects/app/project.yml") is None
    assert (clone / "projects" / "app" / "project.yml").is_file()
    assert (root / "projects" / "app" / "project.yml").is_file(), "projects/ へ反映する"


def test_group_comes_from_the_repository_root(github, root, app, run_cli):
    clone = _root_plugin_repo(github, root)
    (clone / "projects" / "p0").mkdir()
    (clone / "projects" / "p0" / "env").write_text("DEVBASE_ACCOUNT_GROUP=acme\n")
    assert run_cli("project", "create", APP, "--yes") == 0
    assert github.file("owner/plugins", "projects/app/env") == "DEVBASE_ACCOUNT_GROUP=acme\n"


def test_plugin_moved_from_the_root_stops(github, root, app, run_cli, caplog):
    _root_plugin_repo(github, root)
    github.commit("owner/plugins", {"registry.yml": MOVED_REGISTRY})
    assert run_cli("project", "create", APP, "--group", "acme", "--yes") == 1
    assert github.write_calls() == []
    assert "リポジトリの直下" in caplog.text


def test_moved_default_branch_stops_before_writing(github, root, app, run_cli, caplog):
    """既定ブランチを変えて main を残しても、古い main の clone のまま進めない。"""
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    github.set_default_branch("owner/plugins", "develop")
    before = _plugins_yml(root)
    assert run_cli("project", "create", APP, "--yes") == 1
    assert github.write_calls() == []
    assert _plugins_yml(root) == before
    assert "既定ブランチ develop" in caplog.text
    assert "switch develop" in caplog.text


def test_clone_on_the_new_default_branch_completes(github, root, app, run_cli):
    """切り替えた clone は、origin/HEAD が古いままでも進める。"""
    clone = _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    github.set_default_branch("owner/plugins", "develop")
    subprocess.run(["git", "fetch", "origin"], cwd=clone, check=True, capture_output=True)
    subprocess.run(["git", "switch", "develop"], cwd=clone, check=True, capture_output=True)
    assert run_cli("project", "create", APP, "--yes") == 0
    assert (root / "projects" / "app" / "project.yml").is_file()


def test_tty_decline_changes_nothing(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    assert run_cli("project", "create", APP, tty=True, answers=["n"]) == 1
    assert github.write_calls() == []


def test_unauthenticated(github, root, app, run_cli, caplog):
    github.update(authed=False)
    assert run_cli("project", "create", APP, "--yes") == 1
    assert "gh auth login" in caplog.text


def test_non_github_url_stops(github, root, run_cli):
    assert run_cli("project", "create", "https://gitlab.com/owner/app", "--yes") == 1
    assert github.calls() == []


def test_never_touches_docker(github, root, app, run_cli):
    _plugin_repo(github, root, "owner/plugins", groups=["acme"])
    assert run_cli("project", "create", APP, "--yes") == 0
    assert github.docker_calls() == []
