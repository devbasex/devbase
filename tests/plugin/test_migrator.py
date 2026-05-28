"""Tests for PLAN04 PR2: legacy plugins/ -> repos/ migration"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml
import pytest

from devbase.errors import PluginError
from devbase.plugin.models import (
    AvailablePlugin,
    InstalledPlugin,
    RegisteredRepository,
)
from devbase.plugin.registry import PluginRegistry
from devbase.plugin.migrator import (
    _cleanup_plugins_dir,
    _clone_is_healthy,
    _dirs_differ,
    _is_bak_name,
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

    def test_same_size_different_content_differs(self, tmp_path):
        # Same byte length but different content — exercises the streamed
        # chunk comparison rather than the size fast-path.
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "plugin.yml", "name: aaaa\n")
        self._write(b, "plugin.yml", "name: bbbb\n")
        assert _dirs_differ(a, b) is True

    def test_extra_file_in_a_differs(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "plugin.yml", "name: x\n")
        self._write(a, "projects/p/.env", "SECRET=1\n")
        self._write(b, "plugin.yml", "name: x\n")
        assert _dirs_differ(a, b) is True

    def test_upstream_only_addition_does_not_differ(self, tmp_path):
        # A file present only upstream (in the clone) is not data the legacy
        # copy holds, so deleting the copy loses nothing — a routine upstream
        # addition must NOT force a manual-reconcile .bak.
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "plugin.yml", "name: x\n")
        self._write(b, "plugin.yml", "name: x\n")
        self._write(b, "README.md", "new upstream doc\n")
        assert _dirs_differ(a, b) is False

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

    def test_exec_bit_change_differs(self, tmp_path):
        # Same name, size and bytes, but the copy made the script executable.
        # Deleting the copy would lose that mode change, so it must count as
        # divergence (preserved as .bak) rather than a clean migration.
        a, b = tmp_path / "a", tmp_path / "b"
        self._write(a, "entry.sh", "#!/bin/sh\necho hi\n")
        self._write(b, "entry.sh", "#!/bin/sh\necho hi\n")
        (b / "entry.sh").chmod(0o644)
        (a / "entry.sh").chmod(0o755)
        # Sanity: bytes identical, only mode differs.
        assert (a / "entry.sh").read_bytes() == (b / "entry.sh").read_bytes()
        assert _dirs_differ(a, b) is True
        # And once the modes match, the copy is treated as identical again.
        (a / "entry.sh").chmod(0o644)
        assert _dirs_differ(a, b) is False


class TestIsBakName:
    def test_plain_bak_matches(self):
        assert _is_bak_name("carmo.bak") is True

    def test_numbered_bak_matches(self):
        assert _is_bak_name("carmo.bak-2") is True
        assert _is_bak_name("carmo.bak-17") is True

    def test_substring_bak_does_not_match(self):
        # The previous `'.bak' in name` check wrongly flagged these.
        assert _is_bak_name("my.bakery") is False
        assert _is_bak_name("notes.bak.txt") is False
        assert _is_bak_name("backup") is False


class TestCloneIsHealthy:
    def test_valid_clone_is_healthy(self, devbase_root):
        plugins = [{"name": "adminer", "path": "adminer"}]
        clone = _make_repo_clone(devbase_root, plugins)
        assert _clone_is_healthy(clone) is True

    def test_missing_git_is_unhealthy(self, devbase_root):
        plugins = [{"name": "adminer", "path": "adminer"}]
        clone = _make_repo_clone(devbase_root, plugins)
        shutil_rmtree_git(clone)
        assert _clone_is_healthy(clone) is False

    def test_missing_registry_yml_is_unhealthy(self, devbase_root):
        plugins = [{"name": "adminer", "path": "adminer"}]
        clone = _make_repo_clone(devbase_root, plugins)
        (clone / "registry.yml").unlink()
        assert _clone_is_healthy(clone) is False


def shutil_rmtree_git(clone: Path) -> None:
    import shutil
    shutil.rmtree(clone / ".git")


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


class TestCleanupPluginsDir:
    def test_unexpected_leftover_keeps_plugins_dir_uncleaned(self, registry, devbase_root):
        # plugins/ holds a stray entry that is neither .gitkeep nor a .bak
        # (e.g. left behind by an external tool); cleanup must not claim it
        # cleaned and must leave the entry in place.
        plugins_dir = devbase_root / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "stray").mkdir()

        assert _cleanup_plugins_dir(registry) is False
        assert (plugins_dir / "stray").is_dir()

    def test_bak_lookalike_is_not_treated_as_preserved(self, registry, devbase_root):
        # An entry whose name merely contains ".bak" as a substring (e.g.
        # "my.bakery") is NOT a preserved copy; it must be reported as an
        # unexpected leftover rather than silently retained as a .bak.
        plugins_dir = devbase_root / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "my.bakery").mkdir()

        assert _cleanup_plugins_dir(registry) is False
        # Still present (cleanup never deletes leftovers) and not mistaken for
        # a .bak dir.
        assert (plugins_dir / "my.bakery").is_dir()

    def test_numbered_bak_dir_is_retained(self, registry, devbase_root):
        # A real preserved copy from a prior run (carmo.bak-2) keeps plugins/
        # uncleaned just like carmo.bak does.
        plugins_dir = devbase_root / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "carmo.bak-2").mkdir()

        assert _cleanup_plugins_dir(registry) is False
        assert (plugins_dir / "carmo.bak-2").is_dir()


class TestMigratePartialCloneRecovery:
    def test_partial_clone_without_git_is_recloned(self, registry, devbase_root):
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        # Repo registered without local_path so migrate() takes the clone path.
        _register_repo(registry, plugins, local_path=None)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))
        # Leftover partial clone from a prior interrupted run: a directory with
        # no .git and no registry.yml that previously caused an infinite loop.
        partial = devbase_root / "repos" / DIRNAME
        partial.mkdir(parents=True)
        (partial / "junk.txt").write_text("partial\n")

        def fake_clone(url, dest, **kwargs):
            _make_repo_clone(dest.parent.parent, plugins)

        with patch("devbase.plugin.installer.git_clone", side_effect=fake_clone) as mock:
            result = migrate(registry)

        # The broken dir was removed and a fresh clone performed.
        mock.assert_called_once()
        assert result.migrated == ["adminer"]
        assert registry.get("adminer").path == f"repos/{DIRNAME}/adminer"
        assert not (devbase_root / "repos" / DIRNAME / "junk.txt").exists()

    def test_registered_local_path_without_git_is_recloned(self, registry, devbase_root):
        # local_path is recorded (repo migrated before) but the clone lost its
        # .git — e.g. an interrupted operation. Reusing it would leave the
        # migrated plugin pointing at an un-pullable tree, so it must re-clone.
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _register_repo(registry, plugins)  # local_path = repos/<DIRNAME>
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))
        # Existing repos/ dir that is NOT a valid clone (no .git, no registry.yml).
        broken = devbase_root / "repos" / DIRNAME
        broken.mkdir(parents=True)
        (broken / "leftover.txt").write_text("broken\n")

        def fake_clone(url, dest, **kwargs):
            _make_repo_clone(dest.parent.parent, plugins)

        with patch("devbase.plugin.installer.git_clone", side_effect=fake_clone) as mock:
            result = migrate(registry)

        mock.assert_called_once()
        assert result.migrated == ["adminer"]
        assert (devbase_root / "repos" / DIRNAME / ".git").exists()
        assert not (devbase_root / "repos" / DIRNAME / "leftover.txt").exists()


class TestMigrateBatchesRegistryWrites:
    def test_multiple_plugins_all_persisted_with_single_save(self, registry, devbase_root):
        plugins = [
            {"name": "adminer", "path": "adminer", "projects": ["adminer"]},
            {"name": "carmo", "path": "carmo", "projects": ["carmo"]},
            {"name": "redis", "path": "redis", "projects": ["redis"]},
        ]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        for p in plugins:
            _make_legacy_copy(devbase_root, p["name"], p)
            registry.add(_installed(p["name"], f"plugins/{p['name']}"))

        with patch.object(
            PluginRegistry, "_save", autospec=True, side_effect=PluginRegistry._save,
        ) as save_spy:
            result = migrate(registry)

        assert sorted(result.migrated) == ["adminer", "carmo", "redis"]
        # All three path rewrites land in a single plugins.yml save rather than
        # one save per plugin.
        assert save_spy.call_count == 1
        for p in plugins:
            assert registry.get(p["name"]).path == f"repos/{DIRNAME}/{p['name']}"


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


class TestAutoMigrateWarningSuppression:
    def test_preserved_does_not_emit_loud_per_plugin_warning(
        self, registry, devbase_root, caplog,
    ):
        # A diverged legacy copy is preserved as .bak. _auto_migrate runs on
        # every install/update; it must not re-emit a loud per-plugin WARNING
        # each time — a concise INFO hint pointing at `devbase plugin migrate`
        # is enough (the explicit command prints the full detail).
        plugins = [
            {"name": "carmo", "path": "carmo", "projects": ["carmo"]},
            {"name": "redis", "path": "redis", "projects": ["redis"]},
        ]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        # carmo diverges (user-added file) -> will be preserved, not migrated.
        _make_legacy_copy(devbase_root, "carmo", plugins[0],
                          extra={"projects/carmo/.env": "LOCAL=1\n"})
        registry.add(_installed("carmo", "plugins/carmo"))

        from devbase.plugin.installer import _auto_migrate
        import logging
        with caplog.at_level(logging.INFO, logger="devbase.plugin.installer"):
            _auto_migrate(registry)

        installer_logs = [
            r for r in caplog.records
            if r.name == "devbase.plugin.installer"
        ]
        # No WARNING-level record from the auto path.
        assert all(r.levelno < logging.WARNING for r in installer_logs)
        # The concise hint is present.
        joined = " ".join(r.getMessage() for r in installer_logs)
        assert "devbase plugin migrate" in joined


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


class TestAddManyDedup:
    def test_duplicate_names_keep_last(self, registry):
        # add_many is a public batch API; passing the same name twice must not
        # leave two conflicting entries in plugins.yml — the last wins.
        first = _installed("adminer", "repos/x/adminer")
        second = InstalledPlugin(
            name="adminer", version="2.0.0",
            source="https://github.com/testorg/testrepo.git",
            installed_at="2026-02-02T00:00:00+00:00",
            path="repos/y/adminer", linked=False,
        )
        registry.add_many([first, second])
        installed = registry.list_installed()
        assert [p.name for p in installed] == ["adminer"]
        # Last entry won.
        assert registry.get("adminer").path == "repos/y/adminer"
        assert registry.get("adminer").version == "2.0.0"

    def test_duplicate_does_not_duplicate_existing(self, registry):
        # An existing entry replaced by a batch containing duplicates of that
        # name still results in exactly one row.
        registry.add(_installed("adminer", "plugins/adminer"))
        registry.add_many([
            _installed("adminer", "repos/a/adminer"),
            _installed("adminer", "repos/b/adminer"),
        ])
        installed = [p for p in registry.list_installed() if p.name == "adminer"]
        assert len(installed) == 1
        assert installed[0].path == "repos/b/adminer"


class TestFilesEqualExecBitOnly:
    """_files_equal should only care about exec bits, not full perms."""

    def test_rw_perm_diff_does_not_differ(self, tmp_path):
        # Identical bytes, both non-executable, but differing read/write bits
        # (e.g. from a different umask) must NOT count as divergence.
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(); b.mkdir()
        (a / "f.txt").write_text("same\n")
        (b / "f.txt").write_text("same\n")
        (a / "f.txt").chmod(0o644)
        (b / "f.txt").chmod(0o640)
        assert _dirs_differ(a, b) is False

    def test_exec_bit_diff_still_differs(self, tmp_path):
        # Functionally meaningful exec-bit change is still detected.
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(); b.mkdir()
        (a / "f.sh").write_text("#!/bin/sh\n")
        (b / "f.sh").write_text("#!/bin/sh\n")
        (a / "f.sh").chmod(0o755)
        (b / "f.sh").chmod(0o644)
        assert _dirs_differ(a, b) is True


class TestEnsureRepoClonedProtectsGit:
    """A recorded local_path whose dir keeps .git must not be deleted."""

    def test_local_path_with_git_missing_registry_is_not_deleted(
        self, registry, devbase_root,
    ):
        # local_path recorded; clone has .git but registry.yml is gone. The dir
        # may hold uncommitted/unpushed local work, so migration must refuse to
        # rmtree it and raise instead of silently destroying it.
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _register_repo(registry, plugins)  # local_path = repos/<DIRNAME>
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))
        clone = devbase_root / "repos" / DIRNAME
        clone.mkdir(parents=True)
        (clone / ".git").mkdir()
        (clone / "local-work.txt").write_text("uncommitted\n")
        # No registry.yml -> _clone_is_healthy is False.

        def fake_clone(url, dest, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("re-clone must not happen when .git is present")

        with patch("devbase.plugin.installer.git_clone", side_effect=fake_clone):
            result = migrate(registry)

        # The plugin was skipped (not migrated) and the dir survives intact.
        assert "adminer" in result.skipped
        assert (clone / ".git").is_dir()
        assert (clone / "local-work.txt").read_text() == "uncommitted\n"
        # registry entry still points at the legacy path so it retries later.
        assert registry.get("adminer").path == "plugins/adminer"

    def test_local_path_without_git_is_still_recloned(self, registry, devbase_root):
        # Sanity: when .git is gone the dir is genuinely broken and re-cloning
        # (the existing behaviour) still applies.
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _register_repo(registry, plugins)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))
        broken = devbase_root / "repos" / DIRNAME
        broken.mkdir(parents=True)
        (broken / "leftover.txt").write_text("broken\n")

        def fake_clone(url, dest, **kwargs):
            _make_repo_clone(dest.parent.parent, plugins)

        with patch(
            "devbase.plugin.installer.git_clone", side_effect=fake_clone,
        ) as mock:
            result = migrate(registry)

        mock.assert_called_once()
        assert result.migrated == ["adminer"]
        assert (devbase_root / "repos" / DIRNAME / ".git").exists()
        assert not (devbase_root / "repos" / DIRNAME / "leftover.txt").exists()

    def test_derived_path_with_git_missing_registry_is_not_deleted(
        self, registry, devbase_root,
    ):
        # local_path is NOT recorded (pre-persistent-clone registration) but a
        # repos/<derived> clone already exists with .git and only registry.yml
        # missing. It may hold uncommitted/unpushed local work, so the derived
        # clone path must refuse to rmtree it just as the local_path branch does.
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _register_repo(registry, plugins, local_path=None)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))
        clone = devbase_root / "repos" / DIRNAME
        clone.mkdir(parents=True)
        (clone / ".git").mkdir()
        (clone / "local-work.txt").write_text("uncommitted\n")
        # No registry.yml -> parse_registry_yml fails on the existing dir.

        def fake_clone(url, dest, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("re-clone must not happen when .git is present")

        with patch("devbase.plugin.installer.git_clone", side_effect=fake_clone):
            result = migrate(registry)

        assert "adminer" in result.skipped
        assert (clone / ".git").is_dir()
        assert (clone / "local-work.txt").read_text() == "uncommitted\n"
        # registry entry still legacy so a later run can retry.
        assert registry.get("adminer").path == "plugins/adminer"

    def test_clone_dir_existing_as_file_is_replaced(self, registry, devbase_root):
        # repos/<derived> is squatted on by a regular *file* (not a directory).
        # git_clone would fail; the file holds no git tree so it is removed and
        # a fresh clone created in its place.
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _register_repo(registry, plugins, local_path=None)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))
        repos_dir = devbase_root / "repos"
        repos_dir.mkdir(parents=True)
        stray = repos_dir / DIRNAME
        stray.write_text("not a directory\n")
        assert stray.is_file()

        def fake_clone(url, dest, **kwargs):
            _make_repo_clone(dest.parent.parent, plugins)

        with patch(
            "devbase.plugin.installer.git_clone", side_effect=fake_clone,
        ) as mock:
            result = migrate(registry)

        mock.assert_called_once()
        assert result.migrated == ["adminer"]
        assert (devbase_root / "repos" / DIRNAME).is_dir()
        assert (devbase_root / "repos" / DIRNAME / ".git").exists()


class TestMigratePersistsRegistryBeforeRetiringCopy:
    """A registry-save failure must not leave a copy deleted with a stale path.

    Round 3 batched the path rewrites into a single add_many AFTER retiring the
    copies; if that save raised, the copies were already gone/renamed while
    plugins.yml still pointed at plugins/.  migrate() now saves the rewrites
    BEFORE any destructive filesystem op, so a save failure aborts with every
    copy intact.
    """

    def test_save_failure_leaves_copy_intact(self, registry, devbase_root):
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))

        # add_many blows up exactly when migrate() tries to persist the rewrite.
        with patch.object(
            PluginRegistry, "add_many", side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError):
                migrate(registry)

        # The copy was NOT deleted or renamed to .bak: it is still right where
        # it was, so the next run can retry cleanly.
        assert (devbase_root / "plugins" / "adminer" / "plugin.yml").is_file()
        assert not (devbase_root / "plugins" / "adminer.bak").exists()

    def test_copy_retired_only_after_registry_persisted(self, registry, devbase_root):
        # The copy delete/rename must happen strictly after add_many returns.
        plugins = [{"name": "adminer", "path": "adminer", "projects": ["adminer"]}]
        _make_repo_clone(devbase_root, plugins)
        _register_repo(registry, plugins)
        _make_legacy_copy(devbase_root, "adminer", plugins[0])
        registry.add(_installed("adminer", "plugins/adminer"))

        copy = devbase_root / "plugins" / "adminer"
        orig_add_many = PluginRegistry.add_many

        def spy_add_many(self, plugins_arg):
            # At save time the copy must still exist (not yet retired).
            assert copy.is_dir(), "copy retired before registry was persisted"
            return orig_add_many(self, plugins_arg)

        with patch.object(PluginRegistry, "add_many", autospec=True,
                          side_effect=spy_add_many):
            result = migrate(registry)

        assert result.migrated == ["adminer"]
        # After a successful save the clean copy is gone.
        assert not copy.exists()
        assert registry.get("adminer").path == f"repos/{DIRNAME}/adminer"


class TestDirsDifferOtherEntryKind:
    """An entry of kind 'other' (fifo/socket/device) can't be content-compared
    and must be treated as divergence so the copy is preserved, not deleted."""

    def test_fifo_in_copy_differs(self, tmp_path):
        import os
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(); b.mkdir()
        (a / "plugin.yml").write_text("name: x\n")
        (b / "plugin.yml").write_text("name: x\n")
        try:
            os.mkfifo(a / "pipe")
            os.mkfifo(b / "pipe")
        except (AttributeError, NotImplementedError, OSError):
            pytest.skip("mkfifo not supported on this platform")
        # Both sides hold a fifo at the same path; it can't be proven identical
        # so deleting the copy is unsafe -> divergence.
        assert _dirs_differ(a, b) is True
