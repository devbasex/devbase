"""Tests for PLAN04 PR2: legacy plugins/ -> repos/ migration"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml
import pytest

from devbase.plugin.models import (
    AvailablePlugin,
    InstalledPlugin,
    RegisteredRepository,
)
from devbase.plugin.registry import PluginRegistry
from devbase.plugin.migrator import (
    _dirs_differ,
    _is_legacy_plugin,
    migrate,
    needs_migration,
)

URL = "https://github.com/testorg/testrepo.git"
DIRNAME = "github.com--testorg--testrepo"


def _make_repo_clone(devbase_root: Path, plugins: list[dict]) -> Path:
    """Create repos/<DIRNAME>/ with .git, registry.yml and plugin dirs."""
    repo_dir = devbase_root / "repos" / DIRNAME
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".git").mkdir(exist_ok=True)

    entries = []
    for p in plugins:
        pdir = repo_dir / p["path"]
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "plugin.yml").write_text(
            yaml.dump({"name": p["name"], "version": p.get("version", "1.0.0")})
        )
        for proj in p.get("projects", []):
            proj_dir = pdir / "projects" / proj
            proj_dir.mkdir(parents=True, exist_ok=True)
            (proj_dir / "compose.yml").write_text("services: {}\n")
        entries.append({"name": p["name"], "path": p["path"], "description": ""})

    (repo_dir / "registry.yml").write_text(
        yaml.dump({"name": "testrepo", "plugins": entries})
    )
    return repo_dir


def _register_repo(registry: PluginRegistry, plugins: list[dict],
                   local_path: str | None = f"repos/{DIRNAME}") -> None:
    registry.add_repository(RegisteredRepository(
        name="testrepo", url=URL, added_at=registry.now_iso(),
        local_path=local_path or "",
        plugins=[
            AvailablePlugin(name=p["name"], description="", path=p["path"])
            for p in plugins
        ],
    ))


def _make_legacy_copy(devbase_root: Path, name: str, plugin: dict,
                      extra: dict[str, str] | None = None) -> Path:
    """Create plugins/<name>/ mirroring the repo plugin dir (optionally diverged)."""
    pdir = devbase_root / "plugins" / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "plugin.yml").write_text(
        yaml.dump({"name": plugin["name"], "version": plugin.get("version", "1.0.0")})
    )
    for proj in plugin.get("projects", []):
        proj_dir = pdir / "projects" / proj
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "compose.yml").write_text("services: {}\n")
    for rel, content in (extra or {}).items():
        f = pdir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    return pdir


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def devbase_root(tmp_path):
    (tmp_path / "projects").mkdir()
    return tmp_path


@pytest.fixture
def registry(devbase_root):
    return PluginRegistry(devbase_root)


def _installed(name: str, path: str, linked: bool = False) -> InstalledPlugin:
    return InstalledPlugin(
        name=name,
        version="1.0.0",
        source="https://github.com/testorg/testrepo.git",
        installed_at="2026-01-01T00:00:00+00:00",
        path=path,
        linked=linked,
    )


# ── detection ───────────────────────────────────────────────────


class TestIsLegacyPlugin:
    def test_copy_install_under_plugins_is_legacy(self):
        assert _is_legacy_plugin(_installed("adminer", "plugins/adminer")) is True

    def test_repos_based_is_not_legacy(self):
        plugin = _installed(
            "adminer", "repos/github.com--testorg--testrepo/adminer",
        )
        assert _is_legacy_plugin(plugin) is False

    def test_linked_under_plugins_is_not_legacy(self):
        plugin = _installed("local", "plugins/local", linked=True)
        assert _is_legacy_plugin(plugin) is False


class TestDirsDiffer:
    def _write(self, base: Path, rel: str, content: str) -> None:
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def test_identical_dirs_do_not_differ(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "plugin.yml", "name: x\n")
        self._write(a, "projects/p/compose.yml", "services: {}\n")
        self._write(b, "plugin.yml", "name: x\n")
        self._write(b, "projects/p/compose.yml", "services: {}\n")
        assert _dirs_differ(a, b) is False

    def test_changed_file_content_differs(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "plugin.yml", "name: x\nversion: 9\n")
        self._write(b, "plugin.yml", "name: x\n")
        assert _dirs_differ(a, b) is True

    def test_extra_file_in_a_differs(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "plugin.yml", "name: x\n")
        self._write(a, "projects/p/.env", "SECRET=1\n")
        self._write(b, "plugin.yml", "name: x\n")
        assert _dirs_differ(a, b) is True

    def test_extra_file_in_b_differs(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "plugin.yml", "name: x\n")
        self._write(b, "plugin.yml", "name: x\n")
        self._write(b, "README.md", "new upstream doc\n")
        assert _dirs_differ(a, b) is True

    def test_extra_symlink_in_copy_differs(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "plugin.yml", "name: x\n")
        self._write(b, "plugin.yml", "name: x\n")
        # User-added symlink present only in the legacy copy
        (a / "link").symlink_to("plugin.yml")
        assert _dirs_differ(a, b) is True

    def test_empty_dir_only_in_copy_differs(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "plugin.yml", "name: x\n")
        self._write(b, "plugin.yml", "name: x\n")
        # User-added empty directory present only in the legacy copy
        (a / "logs").mkdir(parents=True)
        assert _dirs_differ(a, b) is True

    def test_symlink_target_change_differs(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "plugin.yml", "name: x\n")
        self._write(b, "plugin.yml", "name: x\n")
        (a / "link").symlink_to("a-target")
        (b / "link").symlink_to("b-target")
        assert _dirs_differ(a, b) is True

    def test_file_vs_symlink_type_mismatch_differs(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "plugin.yml", "name: x\n")
        self._write(b, "plugin.yml", "name: x\n")
        # Same name, but a regular file in the copy vs a symlink in the clone
        self._write(a, "shared", "data\n")
        (b / "shared").symlink_to("plugin.yml")
        assert _dirs_differ(a, b) is True

    def test_identical_symlinks_do_not_differ(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "plugin.yml", "name: x\n")
        self._write(b, "plugin.yml", "name: x\n")
        (a / "link").symlink_to("plugin.yml")
        (b / "link").symlink_to("plugin.yml")
        assert _dirs_differ(a, b) is False


class TestNeedsMigration:
    def test_true_when_legacy_present(self, registry):
        registry.add(_installed("adminer", "plugins/adminer"))
        assert needs_migration(registry) is True

    def test_false_when_only_repos_and_linked(self, registry):
        registry.add(_installed(
            "adminer", "repos/github.com--testorg--testrepo/adminer",
        ))
        registry.add(_installed("local", "plugins/local", linked=True))
        assert needs_migration(registry) is False

    def test_false_when_empty(self, registry):
        assert needs_migration(registry) is False


# ── migrate() ───────────────────────────────────────────────────


class TestMigrateClean:
    def test_clean_migration_updates_path_and_deletes_copy(self, registry, devbase_root):
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))

        result = migrate(registry)

        # path rewritten to repos/-based location
        migrated_plugin = registry.get("adminer")
        assert migrated_plugin.path == f"repos/{DIRNAME}/adminer"
        # old copy removed
        assert not (devbase_root / "plugins" / "adminer").exists()
        # result bookkeeping
        assert result.migrated == ["adminer"]
        assert result.preserved == []
        assert result.errors == []

    def test_clean_migration_creates_repos_symlink(self, registry, devbase_root):
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))

        migrate(registry)

        link = devbase_root / "projects" / "adminer"
        assert link.is_symlink()
        target = (link.parent / link.readlink()).resolve()
        assert target == (devbase_root / "repos" / DIRNAME / "adminer" / "projects" / "adminer").resolve()

    def test_clean_migration_empties_plugins_dir_to_gitkeep(self, registry, devbase_root):
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))

        result = migrate(registry)

        plugins_dir = devbase_root / "plugins"
        remaining = sorted(p.name for p in plugins_dir.iterdir())
        assert remaining == [".gitkeep"]
        assert result.plugins_dir_cleaned is True


class TestMigrateWithLocalChanges:
    def test_diverged_copy_preserved_as_bak(self, registry, devbase_root):
        plugins = [{"name": "carmo", "path": "carmo", "projects": ["carmo"]}]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        # User added a local .env that does not exist upstream
        _make_legacy_copy(devbase_root, "carmo", plugins[0],
                          extra={"projects/carmo/.env": "LOCAL=1\n"})
        registry.add(_installed("carmo", "plugins/carmo"))

        result = migrate(registry)

        assert result.preserved == ["carmo"]
        assert result.migrated == []
        bak = devbase_root / "plugins" / "carmo.bak"
        assert bak.is_dir()
        assert (bak / "projects" / "carmo" / ".env").read_text() == "LOCAL=1\n"
        # original copy path no longer present
        assert not (devbase_root / "plugins" / "carmo").exists()

    def test_bak_retained_plugins_dir_not_cleaned(self, registry, devbase_root):
        plugins = [{"name": "carmo", "path": "carmo", "projects": ["carmo"]}]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        _make_legacy_copy(devbase_root, "carmo", plugins[0],
                          extra={"extra.txt": "x\n"})
        registry.add(_installed("carmo", "plugins/carmo"))

        result = migrate(registry)

        assert result.plugins_dir_cleaned is False
        # path is still rewritten to repos/ even when copy is preserved
        assert registry.get("carmo").path == f"repos/{DIRNAME}/carmo"

    def test_existing_bak_is_not_overwritten(self, registry, devbase_root):
        plugins = [{"name": "carmo", "path": "carmo", "projects": ["carmo"]}]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        _make_legacy_copy(devbase_root, "carmo", plugins[0],
                          extra={"projects/carmo/.env": "LOCAL=1\n"})
        registry.add(_installed("carmo", "plugins/carmo"))
        # A previous migration run already preserved carmo.bak with its own data
        prev_bak = devbase_root / "plugins" / "carmo.bak"
        prev_bak.mkdir(parents=True)
        (prev_bak / "old.txt").write_text("PREVIOUS\n")

        result = migrate(registry)

        assert result.preserved == ["carmo"]
        # the old .bak survives untouched
        assert (prev_bak / "old.txt").read_text() == "PREVIOUS\n"
        # the new diverged copy lands in a distinct .bak-2 dir
        new_bak = devbase_root / "plugins" / "carmo.bak-2"
        assert new_bak.is_dir()
        assert (new_bak / "projects" / "carmo" / ".env").read_text() == "LOCAL=1\n"


class TestMigrateClonesMissingRepo:
    def test_repo_without_local_path_is_cloned(self, registry, devbase_root):
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        # Repo registered WITHOUT local_path (legacy registration)
        _register_repo(registry, plugins, local_path=None)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))

        def fake_clone(url, dest, **kwargs):
            _make_repo_clone(dest.parent.parent, plugins)

        with patch("devbase.plugin.installer.git_clone", side_effect=fake_clone):
            result = migrate(registry)

        assert result.migrated == ["adminer"]
        repo = registry.get_repository_by_url(URL)
        assert repo.local_path == f"repos/{DIRNAME}"
        assert registry.get("adminer").path == f"repos/{DIRNAME}/adminer"


class TestMigrateKeepsLinked:
    def test_linked_install_keeps_plugins_dir(self, registry, devbase_root):
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))
        # A separate --link install lives under plugins/ and must be preserved
        (devbase_root / "plugins" / "locallink").mkdir(parents=True)
        registry.add(_installed("locallink", "plugins/locallink", linked=True))

        result = migrate(registry)

        assert result.migrated == ["adminer"]
        assert result.plugins_dir_cleaned is False
        assert (devbase_root / "plugins" / "locallink").is_dir()


class TestMigrateSkips:
    def test_unregistered_source_is_skipped(self, registry, devbase_root):
        _make_legacy_copy(
            devbase_root, "orphan",
            {"name": "orphan", "path": "orphan", "projects": ["orphan"]},
        )
        registry.add(_installed("orphan", "plugins/orphan"))

        result = migrate(registry)

        assert result.skipped == ["orphan"]
        assert result.migrated == []
        # untouched: copy stays, path unchanged
        assert (devbase_root / "plugins" / "orphan").is_dir()
        assert registry.get("orphan").path == "plugins/orphan"


class TestCmdPluginMigrate:
    def test_command_runs_migration(self, devbase_root):
        from devbase.commands.plugin import cmd_plugin_migrate
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _make_repo_clone(devbase_root, plugins)
        registry = PluginRegistry(devbase_root)
        _register_repo(registry, plugins)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))

        rc = cmd_plugin_migrate(devbase_root)

        assert rc == 0
        assert PluginRegistry(devbase_root).get("adminer").path == f"repos/{DIRNAME}/adminer"

    def test_command_noop_when_nothing_to_migrate(self, devbase_root):
        from devbase.commands.plugin import cmd_plugin_migrate
        rc = cmd_plugin_migrate(devbase_root)
        assert rc == 0


class TestAutoMigrateOnInstall:
    def test_install_triggers_migration_of_legacy(self, registry, devbase_root):
        plugins = [
            {"name": "adminer", "path": "adminer", "projects": ["adminer"]},
            {"name": "carmo", "path": "carmo", "projects": ["carmo"]},
        ]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))

        from devbase.plugin.installer import install_plugin
        install_plugin(registry, "carmo")

        # pre-existing legacy install migrated as a side effect
        assert registry.get("adminer").path == f"repos/{DIRNAME}/adminer"
        # the explicitly requested install also succeeds
        assert registry.get("carmo").path == f"repos/{DIRNAME}/carmo"


class TestAutoMigrateOnUpdate:
    def test_update_triggers_migration_of_legacy(self, registry, devbase_root):
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))

        from devbase.plugin.updater import update_plugin
        with patch("devbase.plugin.updater._git_pull"):
            update_plugin(registry)

        assert registry.get("adminer").path == f"repos/{DIRNAME}/adminer"
        # only auto-migration removes the stale plugins/ copy; a plain pull
        # would rewrite the path but leave the old copy on disk.
        assert not (devbase_root / "plugins" / "adminer").exists()
