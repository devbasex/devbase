"""Plugin updater - handles update and migration operations"""

from pathlib import Path
from typing import Optional

from devbase.errors import PluginError
from devbase.log import get_logger

from .installer import parse_registry_yml
from .models import InstalledPlugin, RegistryInfo
from .registry import PluginRegistry
from .repo_manager import _git_pull
from .syncer import sync_projects, discover_projects

logger = get_logger("devbase.plugin.updater")


def _discover_source_projects(
    clone_dir: Path, reg_info: RegistryInfo,
) -> dict[str, str]:
    """Build project_name -> plugin_name mapping from source repository."""
    project_to_plugin: dict[str, str] = {}
    for entry in reg_info.plugins:
        plugin_path = clone_dir / entry.path.rstrip('/')
        for proj_name in discover_projects(plugin_path):
            project_to_plugin[proj_name] = entry.name
    return project_to_plugin


def _migrate_removed_plugin(
    registry: PluginRegistry,
    plugin: InstalledPlugin,
    clone_dir: Path,
    reg_info: RegistryInfo,
) -> bool:
    """Migrate a plugin that no longer exists in the source.

    Detects which new plugins contain the old plugin's projects
    and replaces the old plugin with them.
    """
    old_plugin_dir = registry.devbase_root / plugin.path
    old_projects: set[str] = set()
    if old_plugin_dir.is_dir():
        old_projects = set(discover_projects(old_plugin_dir))

    if not old_projects:
        logger.info("  Plugin '%s' has no projects — removing", plugin.name)
        registry.remove(plugin.name)
        return True

    project_to_plugin = _discover_source_projects(clone_dir, reg_info)

    replacement_plugins: dict[str, list[str]] = {}
    unmapped_projects: list[str] = []
    for proj in sorted(old_projects):
        new_plugin = project_to_plugin.get(proj)
        if new_plugin:
            replacement_plugins.setdefault(new_plugin, []).append(proj)
        else:
            unmapped_projects.append(proj)

    if not replacement_plugins:
        logger.error("No replacement found for plugin '%s' projects", plugin.name)
        return False

    logger.info("  Migrating '%s' (%d projects):", plugin.name, len(old_projects))
    for new_name, projects in sorted(replacement_plugins.items()):
        logger.info("    -> '%s' (%d projects)", new_name, len(projects))
    if unmapped_projects:
        logger.warning("    %d project(s) not found in source:", len(unmapped_projects))
        for p in unmapped_projects:
            logger.warning("      - %s", p)

    registry.remove(plugin.name)

    repo_reg = registry.get_repository_by_url(plugin.source)
    repo_local_path = repo_reg.local_path if repo_reg else ""

    for new_name in sorted(replacement_plugins):
        if registry.get(new_name):
            logger.info("  Skip: '%s' already installed", new_name)
            continue
        entry = next((e for e in reg_info.plugins if e.name == new_name), None)
        if entry and repo_local_path:
            from .installer import _register_repo_plugin
            plugin_path = clone_dir / entry.path.rstrip('/')
            _register_repo_plugin(
                registry, entry.name, plugin_path,
                plugin.source, repo_local_path,
            )

    return True


def update_plugin(registry: PluginRegistry, name: Optional[str] = None) -> None:
    """Update a plugin (or all if name is None) via git pull.

    Raises PluginError on failure.
    """
    installed = registry.list_installed()
    if not installed:
        logger.info("No plugins installed")
        return

    targets = installed if name is None else [
        p for p in installed if p.name == name
    ]

    if name and not targets:
        raise PluginError(f"Plugin '{name}' is not installed")

    updated_repos: set[str] = set()
    errors = []

    for plugin in targets:
        if plugin.linked:
            logger.info("Skip: '%s' is locally linked (update manually)", plugin.name)
            continue

        if not plugin.source:
            errors.append(
                f"Plugin '{plugin.name}' has no source URL recorded. "
                "Use 'devbase plugin install <repo>:<name>' to reinstall."
            )
            continue

        repo_reg = registry.get_repository_by_url(plugin.source)
        if not repo_reg or not repo_reg.local_path:
            errors.append(
                f"Plugin '{plugin.name}': repository not found or has no local clone. "
                "Use 'devbase plugin repo add' to re-register."
            )
            continue

        clone_dir = registry.devbase_root / repo_reg.local_path
        if not clone_dir.is_dir():
            errors.append(
                f"Plugin '{plugin.name}': clone directory not found: {clone_dir}"
            )
            continue

        if repo_reg.url not in updated_repos:
            logger.info("Updating '%s' via git pull in %s...", plugin.name, clone_dir)
            try:
                _git_pull(clone_dir)
            except PluginError as e:
                errors.append(str(e))
                continue
            updated_repos.add(repo_reg.url)
        else:
            logger.info("Updating '%s' (repo already pulled)...", plugin.name)

        reg_info = parse_registry_yml(clone_dir)
        if not reg_info:
            errors.append(f"No registry.yml in source for '{plugin.name}'")
            continue

        target_entry = None
        for entry in reg_info.plugins:
            if entry.name == plugin.name:
                target_entry = entry
                break

        if not target_entry:
            logger.info("  Plugin '%s' no longer exists in source", plugin.name)
            if not _migrate_removed_plugin(
                registry, plugin, clone_dir, reg_info,
            ):
                errors.append(f"Migration failed for '{plugin.name}'")
            continue

        plugin_path = clone_dir / target_entry.path.rstrip('/')
        from .syncer import load_plugin_info
        info = load_plugin_info(plugin_path)
        version = info.version if info else '0.1.0'

        # Use actual plugin_path relative to devbase_root so that
        # subdirectory plugins (registry.yml path != name) resolve correctly.
        rel_path = str(plugin_path.relative_to(registry.devbase_root))
        registry.add(InstalledPlugin(
            name=plugin.name,
            version=version,
            source=plugin.source,
            installed_at=plugin.installed_at,
            path=rel_path,
            linked=False,
        ))
        logger.info("Updated plugin '%s' (v%s)", plugin.name, version)

    sync_projects(registry)

    if errors:
        raise PluginError(
            "Some plugins failed to update:\n" + "\n".join(f"  - {e}" for e in errors)
        )
