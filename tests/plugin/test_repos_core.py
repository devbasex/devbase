"""Tests for PLAN04 repos/ persistent clone + direct link install"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from devbase.errors import PluginError, RepositoryError
from devbase.plugin.models import (
    AvailablePlugin,
    InstalledPlugin,
    RegisteredRepository,
)
from devbase.plugin.registry import PluginRegistry
from devbase.plugin.repo_manager import (
    _derive_repo_name,
    _is_repo_dirty,
    _url_to_repos_dirname,
    add_repository,
    refresh_repository,
    remove_repository,
)
from devbase.plugin.installer import (
    git_clone,
    install_plugin,
    uninstall_plugin,
)
from devbase.plugin.syncer import (
    _extract_owner,
    _make_relative_target,
    sync_projects,
)


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def devbase_root(tmp_path):
    """Create a minimal devbase directory structure."""
    (tmp_path / "projects").mkdir()
    return tmp_path


@pytest.fixture
def registry(devbase_root):
    return PluginRegistry(devbase_root)


def _make_repo_dir(devbase_root: Path, owner_repo: str, plugins: list[dict]) -> Path:
    """Create a fake repos/<owner>--<repo>/ with registry.yml and plugin dirs."""
    dir_name = owner_repo.replace("/", "--")
    repo_dir = devbase_root / "repos" / dir_name
    repo_dir.mkdir(parents=True, exist_ok=True)

    # Create a fake .git directory
    (repo_dir / ".git").mkdir(exist_ok=True)

    plugin_entries = []
    for p in plugins:
        pdir = repo_dir / p["path"]
        pdir.mkdir(parents=True, exist_ok=True)
        # plugin.yml
        import yaml
        with open(pdir / "plugin.yml", "w") as f:
            yaml.dump({
                "name": p["name"],
                "version": p.get("version", "1.0.0"),
                "priority": p.get("priority", 0),
            }, f)
        # projects/
        for proj in p.get("projects", []):
            (pdir / "projects" / proj).mkdir(parents=True, exist_ok=True)
        plugin_entries.append({
            "name": p["name"],
            "path": p["path"],
            "description": p.get("description", ""),
        })

    import yaml
    with open(repo_dir / "registry.yml", "w") as f:
        yaml.dump({
            "name": owner_repo.split("/")[-1],
            "plugins": plugin_entries,
        }, f)

    return repo_dir


def _register_repo(registry: PluginRegistry, owner_repo: str, url: str, plugins: list[dict]):
    """Register a repository in plugins.yml with local_path."""
    dir_name = owner_repo.replace("/", "--")
    repo = RegisteredRepository(
        name=owner_repo.split("/")[-1],
        url=url,
        added_at=registry.now_iso(),
        local_path=f"repos/{dir_name}",
        plugins=[
            AvailablePlugin(name=p["name"], description=p.get("description", ""), path=p["path"])
            for p in plugins
        ],
    )
    registry.add_repository(repo)


# ── models.py ───────────────────────────────────────────────────


class TestRegisteredRepositoryLocalPath:
    def test_to_dict_includes_local_path(self):
        repo = RegisteredRepository(
            name="test",
            url="https://github.com/test/repo.git",
            local_path="repos/test--repo",
        )
        d = repo.to_dict()
        assert d["local_path"] == "repos/test--repo"

    def test_to_dict_omits_empty_local_path(self):
        repo = RegisteredRepository(
            name="test",
            url="https://github.com/test/repo.git",
        )
        d = repo.to_dict()
        assert "local_path" not in d

    def test_from_dict_reads_local_path(self):
        repo = RegisteredRepository.from_dict({
            "name": "test",
            "url": "https://github.com/test/repo.git",
            "local_path": "repos/test--repo",
        })
        assert repo.local_path == "repos/test--repo"

    def test_from_dict_defaults_empty_local_path(self):
        repo = RegisteredRepository.from_dict({
            "name": "test",
            "url": "https://github.com/test/repo.git",
        })
        assert repo.local_path == ""


# ── registry.py ─────────────────────────────────────────────────


class TestGetReposDir:
    def test_returns_repos_path(self, registry, devbase_root):
        assert registry.get_repos_dir() == devbase_root / "repos"


# ── repo_manager.py ─────────────────────────────────────────────


class TestUrlToReposDirname:
    def test_github_https(self):
        assert _url_to_repos_dirname("https://github.com/devbasex/devbase-samples.git") == "devbasex--devbase-samples"

    def test_github_ssh(self):
        assert _url_to_repos_dirname("git@github.com:user/my-repo.git") == "user--my-repo"

    def test_owner_with_hyphens(self):
        assert _url_to_repos_dirname("https://github.com/takemi-ohama/devbase-ext.git") == "takemi-ohama--devbase-ext"


class TestDeriveRepoName:
    def test_github_https(self):
        assert _derive_repo_name("https://github.com/devbasex/devbase-samples.git") == "devbasex/devbase-samples"

    def test_github_ssh(self):
        assert _derive_repo_name("git@github.com:user/my-repo.git") == "user/my-repo"


class TestAddRepository:
    def test_add_creates_persistent_clone(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        repos_dir = devbase_root / "repos"

        with patch("devbase.plugin.repo_manager.git_clone") as mock_clone, \
             patch("devbase.plugin.repo_manager.parse_registry_yml") as mock_parse:

            def fake_clone(u, dest, **kwargs):
                dest.mkdir(parents=True, exist_ok=True)
                (dest / ".git").mkdir()

            mock_clone.side_effect = fake_clone

            from devbase.plugin.models import RegistryInfo, RegistryEntry
            mock_parse.return_value = RegistryInfo(
                name="testrepo",
                plugins=[RegistryEntry(name="myplugin", path="myplugin", description="test")],
            )

            add_repository(registry, url)

            mock_clone.assert_called_once()
            call_kwargs = mock_clone.call_args
            assert call_kwargs.kwargs.get("shallow") is False or (
                len(call_kwargs.args) >= 3 or "shallow" in str(call_kwargs)
            )

            repo = registry.get_repository("testrepo")
            assert repo is not None
            assert repo.local_path == "repos/testorg--testrepo"
            assert repo.url == url

    def test_add_duplicate_url_raises(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        _register_repo(registry, "testorg/testrepo", url, [])

        with pytest.raises(RepositoryError, match="already registered"):
            add_repository(registry, url)


class TestRemoveRepository:
    def test_remove_deletes_clone_dir(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        repo_dir = _make_repo_dir(devbase_root, "testorg/testrepo", [
            {"name": "p1", "path": "p1", "projects": ["proj1"]},
        ])
        _register_repo(registry, "testorg/testrepo", url, [
            {"name": "p1", "path": "p1"},
        ])

        remove_repository(registry, "testrepo", force=True)

        assert not repo_dir.exists()
        assert registry.get_repository("testrepo") is None

    def test_remove_dirty_repo_raises_without_force(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        _make_repo_dir(devbase_root, "testorg/testrepo", [])
        _register_repo(registry, "testorg/testrepo", url, [])

        with patch("devbase.plugin.repo_manager._is_repo_dirty", return_value=(True, "uncommitted changes")):
            with pytest.raises(RepositoryError, match="uncommitted changes"):
                remove_repository(registry, "testrepo")

    def test_remove_dirty_repo_succeeds_with_force(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        repo_dir = _make_repo_dir(devbase_root, "testorg/testrepo", [])
        _register_repo(registry, "testorg/testrepo", url, [])

        with patch("devbase.plugin.repo_manager._is_repo_dirty", return_value=(True, "uncommitted changes")):
            remove_repository(registry, "testrepo", force=True)

        assert not repo_dir.exists()

    def test_remove_uninstalls_plugins_and_syncs(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        _make_repo_dir(devbase_root, "testorg/testrepo", [
            {"name": "p1", "path": "p1", "projects": ["proj1"]},
        ])
        _register_repo(registry, "testorg/testrepo", url, [
            {"name": "p1", "path": "p1"},
        ])
        registry.add(InstalledPlugin(
            name="p1", version="1.0.0", source=url,
            installed_at=registry.now_iso(),
            path="repos/testorg--testrepo/p1",
        ))

        projects_dir = devbase_root / "projects"
        link = projects_dir / "proj1"
        link.symlink_to(Path("..") / "repos" / "testorg--testrepo" / "p1" / "projects" / "proj1")

        remove_repository(registry, "testrepo", force=True)

        assert registry.get("p1") is None
        assert not link.exists()


class TestRefreshRepository:
    def test_refresh_pulls_and_updates_metadata(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        _make_repo_dir(devbase_root, "testorg/testrepo", [
            {"name": "p1", "path": "p1"},
        ])
        _register_repo(registry, "testorg/testrepo", url, [
            {"name": "p1", "path": "p1"},
        ])

        with patch("devbase.plugin.repo_manager._git_pull"):
            refresh_repository(registry, "testrepo")

        repo = registry.get_repository("testrepo")
        assert repo is not None
        assert any(p.name == "p1" for p in repo.plugins)

    def test_refresh_warns_removed_installed_plugin(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        repo_dir = _make_repo_dir(devbase_root, "testorg/testrepo", [
            {"name": "p2", "path": "p2"},
        ])
        _register_repo(registry, "testorg/testrepo", url, [
            {"name": "p1", "path": "p1"},
            {"name": "p2", "path": "p2"},
        ])
        registry.add(InstalledPlugin(
            name="p1", version="1.0.0", source=url,
            installed_at=registry.now_iso(),
            path="repos/testorg--testrepo/p1",
        ))

        with patch("devbase.plugin.repo_manager._git_pull"), \
             patch("devbase.plugin.repo_manager.logger") as mock_logger:
            refresh_repository(registry, "testrepo")
            mock_logger.warning.assert_any_call(
                "Installed plugin '%s' no longer exists in registry.yml of '%s'",
                "p1", "testrepo",
            )


# ── installer.py ────────────────────────────────────────────────


class TestGitClone:
    def test_shallow_true_adds_depth(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            dest = Path("/tmp/test-clone")
            dest.parent.mkdir(parents=True, exist_ok=True)
            git_clone("https://example.com/repo.git", dest, shallow=True)
            cmd = mock_run.call_args[0][0]
            assert "--depth" in cmd

    def test_shallow_false_no_depth(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            dest = Path("/tmp/test-clone2")
            dest.parent.mkdir(parents=True, exist_ok=True)
            git_clone("https://example.com/repo.git", dest, shallow=False)
            cmd = mock_run.call_args[0][0]
            assert "--depth" not in cmd


class TestInstallPlugin:
    def test_install_creates_symlinks_via_repos(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        _make_repo_dir(devbase_root, "testorg/testrepo", [
            {"name": "myplugin", "path": "myplugin", "projects": ["myproj"]},
        ])
        _register_repo(registry, "testorg/testrepo", url, [
            {"name": "myplugin", "path": "myplugin"},
        ])

        install_plugin(registry, "myplugin")

        plugin = registry.get("myplugin")
        assert plugin is not None
        assert plugin.path == "repos/testorg--testrepo/myplugin"
        assert not plugin.linked

        proj_link = devbase_root / "projects" / "myproj"
        assert proj_link.is_symlink()
        target = os.readlink(str(proj_link))
        assert "repos/testorg--testrepo/myplugin/projects/myproj" in target

    def test_install_all_plugins(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        _make_repo_dir(devbase_root, "testorg/testrepo", [
            {"name": "p1", "path": "p1", "projects": ["proj1"]},
            {"name": "p2", "path": "p2", "projects": ["proj2"]},
        ])
        _register_repo(registry, "testorg/testrepo", url, [
            {"name": "p1", "path": "p1"},
            {"name": "p2", "path": "p2"},
        ])

        install_plugin(registry, url, install_all=True)

        assert registry.get("p1") is not None
        assert registry.get("p2") is not None

    def test_install_not_registered_raises(self, registry):
        with pytest.raises(PluginError, match="not found in registered"):
            install_plugin(registry, "nonexistent")

    def test_install_ref_rejected_for_unregistered_repo(self, registry):
        """@ref with unregistered repo raises PluginError (not NameError)."""
        with pytest.raises(PluginError, match="Cannot use @v1.0"):
            install_plugin(registry, "testorg/testrepo:myplugin@v1.0")

    def test_install_legacy_repo_without_local_path(self, registry, devbase_root):
        """Legacy repos (no local_path) are auto-migrated to persistent clone."""
        url = "https://github.com/testorg/testrepo.git"
        # Register a legacy repo (no local_path)
        repo = RegisteredRepository(
            name="testrepo", url=url,
            added_at=registry.now_iso(),
            local_path="",
            plugins=[AvailablePlugin(name="p1", description="test", path="p1")],
        )
        registry.add_repository(repo)

        with patch("devbase.plugin.installer.git_clone") as mock_clone:
            def fake_clone(u, dest, **kwargs):
                dest.mkdir(parents=True, exist_ok=True)
                # Create plugin dir + registry.yml in the clone
                pdir = dest / "p1"
                pdir.mkdir()
                import yaml
                (pdir / "plugin.yml").write_text("name: p1\nversion: 2.0.0\n")
                (pdir / "projects").mkdir()
                (pdir / "projects" / "proj1").mkdir()
                with open(dest / "registry.yml", "w") as f:
                    yaml.dump({
                        "name": "testrepo",
                        "plugins": [{"name": "p1", "path": "p1", "description": "test"}],
                    }, f)

            mock_clone.side_effect = fake_clone

            install_plugin(registry, "p1")

            # Repo should now have local_path set
            updated = registry.get_repository_by_url(url)
            assert updated.local_path == "repos/testorg--testrepo"

            # Plugin should be installed
            plugin = registry.get("p1")
            assert plugin is not None
            assert "repos/testorg--testrepo" in plugin.path


class TestUninstallPlugin:
    def test_uninstall_repos_plugin_preserves_files(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        repo_dir = _make_repo_dir(devbase_root, "testorg/testrepo", [
            {"name": "myplugin", "path": "myplugin", "projects": ["myproj"]},
        ])
        _register_repo(registry, "testorg/testrepo", url, [
            {"name": "myplugin", "path": "myplugin"},
        ])
        registry.add(InstalledPlugin(
            name="myplugin", version="1.0.0", source=url,
            installed_at=registry.now_iso(),
            path="repos/testorg--testrepo/myplugin",
        ))

        proj_link = devbase_root / "projects" / "myproj"
        proj_link.symlink_to(
            Path("..") / "repos" / "testorg--testrepo" / "myplugin" / "projects" / "myproj"
        )

        uninstall_plugin(registry, "myplugin")

        assert registry.get("myplugin") is None
        assert not proj_link.exists()
        assert (repo_dir / "myplugin").is_dir()

    def test_uninstall_linked_plugin_removes_symlink(self, registry, devbase_root):
        plugins_dir = devbase_root / "plugins"
        plugins_dir.mkdir()

        local_src = devbase_root / "local-repo" / "myplugin"
        local_src.mkdir(parents=True)
        (local_src / "plugin.yml").write_text("name: myplugin\nversion: 1.0.0\n")

        link_dest = plugins_dir / "myplugin"
        link_dest.symlink_to(local_src)

        registry.add(InstalledPlugin(
            name="myplugin", version="1.0.0", source=str(local_src.parent),
            installed_at=registry.now_iso(),
            path="plugins/myplugin",
            linked=True,
        ))

        uninstall_plugin(registry, "myplugin")

        assert not link_dest.exists()
        assert local_src.is_dir()

    def test_uninstall_nonexistent_raises(self, registry):
        with pytest.raises(PluginError, match="not installed"):
            uninstall_plugin(registry, "nonexistent")


# ── syncer.py ───────────────────────────────────────────────────


class TestSyncProjects:
    def test_basic_sync_creates_symlinks(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        _make_repo_dir(devbase_root, "testorg/testrepo", [
            {"name": "p1", "path": "p1", "projects": ["proj1", "proj2"]},
        ])
        _register_repo(registry, "testorg/testrepo", url, [
            {"name": "p1", "path": "p1"},
        ])
        registry.add(InstalledPlugin(
            name="p1", version="1.0.0", source=url,
            installed_at=registry.now_iso(),
            path="repos/testorg--testrepo/p1",
        ))

        count = sync_projects(registry, verbose=False)

        assert count == 2
        assert (devbase_root / "projects" / "proj1").is_symlink()
        assert (devbase_root / "projects" / "proj2").is_symlink()

    def test_collision_creates_suffix_links(self, registry, devbase_root):
        url1 = "https://github.com/orgA/repo1.git"
        url2 = "https://github.com/orgB/repo2.git"
        _make_repo_dir(devbase_root, "orgA/repo1", [
            {"name": "p1", "path": "p1", "projects": ["shared"], "priority": 10},
        ])
        _make_repo_dir(devbase_root, "orgB/repo2", [
            {"name": "p2", "path": "p2", "projects": ["shared"], "priority": 0},
        ])
        _register_repo(registry, "orgA/repo1", url1, [{"name": "p1", "path": "p1"}])
        _register_repo(registry, "orgB/repo2", url2, [{"name": "p2", "path": "p2"}])
        registry.add(InstalledPlugin(
            name="p1", version="1.0.0", source=url1,
            installed_at=registry.now_iso(),
            path="repos/orgA--repo1/p1",
        ))
        registry.add(InstalledPlugin(
            name="p2", version="1.0.0", source=url2,
            installed_at=registry.now_iso(),
            path="repos/orgB--repo2/p2",
        ))

        count = sync_projects(registry, verbose=False)

        bare_link = devbase_root / "projects" / "shared"
        assert bare_link.is_symlink()
        target = os.readlink(str(bare_link))
        assert "orgA--repo1" in target

        suffix_link = devbase_root / "projects" / "shared.orgB--repo2"
        assert suffix_link.is_symlink()
        suffix_target = os.readlink(str(suffix_link))
        assert "orgB--repo2" in suffix_target

    def test_no_collision_no_suffix(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        _make_repo_dir(devbase_root, "testorg/testrepo", [
            {"name": "p1", "path": "p1", "projects": ["proj1"]},
        ])
        _register_repo(registry, "testorg/testrepo", url, [{"name": "p1", "path": "p1"}])
        registry.add(InstalledPlugin(
            name="p1", version="1.0.0", source=url,
            installed_at=registry.now_iso(),
            path="repos/testorg--testrepo/p1",
        ))

        sync_projects(registry, verbose=False)

        assert not (devbase_root / "projects" / "proj1.testorg").exists()

    def test_winner_has_no_suffix(self, registry, devbase_root):
        url1 = "https://github.com/orgA/repo1.git"
        url2 = "https://github.com/orgB/repo2.git"
        _make_repo_dir(devbase_root, "orgA/repo1", [
            {"name": "p1", "path": "p1", "projects": ["shared"], "priority": 10},
        ])
        _make_repo_dir(devbase_root, "orgB/repo2", [
            {"name": "p2", "path": "p2", "projects": ["shared"], "priority": 0},
        ])
        _register_repo(registry, "orgA/repo1", url1, [{"name": "p1", "path": "p1"}])
        _register_repo(registry, "orgB/repo2", url2, [{"name": "p2", "path": "p2"}])
        registry.add(InstalledPlugin(
            name="p1", version="1.0.0", source=url1,
            installed_at=registry.now_iso(),
            path="repos/orgA--repo1/p1",
        ))
        registry.add(InstalledPlugin(
            name="p2", version="1.0.0", source=url2,
            installed_at=registry.now_iso(),
            path="repos/orgB--repo2/p2",
        ))

        sync_projects(registry, verbose=False)

        assert not (devbase_root / "projects" / "shared.orgA").exists()

    def test_real_directory_skipped(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        _make_repo_dir(devbase_root, "testorg/testrepo", [
            {"name": "p1", "path": "p1", "projects": ["existing"]},
        ])
        _register_repo(registry, "testorg/testrepo", url, [{"name": "p1", "path": "p1"}])
        registry.add(InstalledPlugin(
            name="p1", version="1.0.0", source=url,
            installed_at=registry.now_iso(),
            path="repos/testorg--testrepo/p1",
        ))

        (devbase_root / "projects" / "existing").mkdir()

        count = sync_projects(registry, verbose=False)
        assert count == 0
        assert not (devbase_root / "projects" / "existing").is_symlink()

    def test_missing_plugin_dir_warns(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        _register_repo(registry, "testorg/testrepo", url, [{"name": "p1", "path": "p1"}])
        registry.add(InstalledPlugin(
            name="p1", version="1.0.0", source=url,
            installed_at=registry.now_iso(),
            path="repos/testorg--testrepo/p1",
        ))

        count = sync_projects(registry, verbose=True)
        assert count == 0

    def test_link_plugin_collision_uses_source_basename(self, registry, devbase_root):
        """--link plugin と repos/ plugin の衝突時に .<source-basename> suffix"""
        url = "https://github.com/orgA/repo1.git"
        _make_repo_dir(devbase_root, "orgA/repo1", [
            {"name": "p1", "path": "p1", "projects": ["shared"], "priority": 10},
        ])
        _register_repo(registry, "orgA/repo1", url, [{"name": "p1", "path": "p1"}])
        registry.add(InstalledPlugin(
            name="p1", version="1.0.0", source=url,
            installed_at=registry.now_iso(),
            path="repos/orgA--repo1/p1",
        ))

        plugins_dir = devbase_root / "plugins"
        plugins_dir.mkdir()
        local_plugin = devbase_root / "my-local-repo" / "p2"
        local_plugin.mkdir(parents=True)
        (local_plugin / "plugin.yml").write_text("name: p2\nversion: 1.0.0\npriority: 0\n")
        (local_plugin / "projects" / "shared").mkdir(parents=True)
        link = plugins_dir / "p2"
        link.symlink_to(local_plugin)

        registry.add(InstalledPlugin(
            name="p2", version="1.0.0", source=str(devbase_root / "my-local-repo"),
            installed_at=registry.now_iso(),
            path="plugins/p2",
            linked=True,
        ))

        sync_projects(registry, verbose=False)

        suffix_link = devbase_root / "projects" / "shared.my-local-repo"
        assert suffix_link.is_symlink()


class TestExtractOwner:
    def test_repos_based(self):
        plugin = InstalledPlugin(
            name="p1", version="1.0.0", source="url",
            installed_at="", path="repos/orgA--repo1/p1",
        )
        assert _extract_owner(plugin) == "orgA--repo1"

    def test_linked(self):
        plugin = InstalledPlugin(
            name="p1", version="1.0.0", source="/path/to/my-local-repo",
            installed_at="", path="plugins/p1", linked=True,
        )
        assert _extract_owner(plugin) == "my-local-repo"


class TestMakeRelativeTarget:
    def test_repos_based(self):
        plugin = InstalledPlugin(
            name="p1", version="1.0.0", source="url",
            installed_at="", path="repos/orgA--repo1/p1",
        )
        target = _make_relative_target(plugin, "myproj")
        assert target == Path("..") / "repos" / "orgA--repo1" / "p1" / "projects" / "myproj"

    def test_linked(self):
        plugin = InstalledPlugin(
            name="p1", version="1.0.0", source="/path/to/repo",
            installed_at="", path="plugins/p1", linked=True,
        )
        target = _make_relative_target(plugin, "myproj")
        assert target == Path("..") / "plugins" / "p1" / "projects" / "myproj"


# ── updater.py ──────────────────────────────────────────────────


class TestUpdatePlugin:
    def test_update_calls_git_pull(self, registry, devbase_root):
        url = "https://github.com/testorg/testrepo.git"
        _make_repo_dir(devbase_root, "testorg/testrepo", [
            {"name": "p1", "path": "p1", "projects": ["proj1"]},
        ])
        _register_repo(registry, "testorg/testrepo", url, [
            {"name": "p1", "path": "p1"},
        ])
        registry.add(InstalledPlugin(
            name="p1", version="1.0.0", source=url,
            installed_at=registry.now_iso(),
            path="repos/testorg--testrepo/p1",
        ))

        from devbase.plugin.updater import update_plugin

        with patch("devbase.plugin.updater._git_pull") as mock_pull:
            update_plugin(registry, "p1")
            mock_pull.assert_called_once()

    def test_update_skips_linked(self, registry, devbase_root):
        registry.add(InstalledPlugin(
            name="linked-plugin", version="1.0.0", source="/local/path",
            installed_at=registry.now_iso(),
            path="plugins/linked-plugin",
            linked=True,
        ))

        from devbase.plugin.updater import update_plugin

        with patch("devbase.plugin.updater._git_pull") as mock_pull:
            update_plugin(registry, "linked-plugin")
            mock_pull.assert_not_called()

    def test_update_deduplicates_git_pull(self, registry, devbase_root):
        """Same repo pulled only once even when multiple plugins share it."""
        url = "https://github.com/testorg/testrepo.git"
        _make_repo_dir(devbase_root, "testorg/testrepo", [
            {"name": "p1", "path": "p1", "projects": ["proj1"]},
            {"name": "p2", "path": "p2", "projects": ["proj2"]},
        ])
        _register_repo(registry, "testorg/testrepo", url, [
            {"name": "p1", "path": "p1"},
            {"name": "p2", "path": "p2"},
        ])
        registry.add(InstalledPlugin(
            name="p1", version="1.0.0", source=url,
            installed_at=registry.now_iso(),
            path="repos/testorg--testrepo/p1",
        ))
        registry.add(InstalledPlugin(
            name="p2", version="1.0.0", source=url,
            installed_at=registry.now_iso(),
            path="repos/testorg--testrepo/p2",
        ))

        from devbase.plugin.updater import update_plugin

        with patch("devbase.plugin.updater._git_pull") as mock_pull:
            update_plugin(registry)
            assert mock_pull.call_count == 1
