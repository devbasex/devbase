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
    pre_pull_projects: Optional[set[str]] = None,
) -> bool:
    """Migrate a plugin that no longer exists in the source.

    Detects which new plugins contain the old plugin's projects
    and replaces the old plugin with them.

    Args:
        pre_pull_projects: Project names captured BEFORE git pull.
            If provided, used instead of reading the (now-updated) working tree
            so that renamed/moved plugin directories are still detected.
    """
    if pre_pull_projects is not None:
        old_projects = pre_pull_projects
    else:
        old_plugin_dir = registry.devbase_root / plugin.path
        old_projects = set()
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


def _snapshot_plugin_projects(
    registry: PluginRegistry,
    plugins: list[InstalledPlugin],
) -> dict[str, set[str]]:
    """Snapshot project names for each plugin BEFORE git pull.

    Returns {plugin_name: {project_names}} so migration can detect
    where old projects moved even after the working tree is updated.
    """
    result: dict[str, set[str]] = {}
    for plugin in plugins:
        plugin_dir = registry.devbase_root / plugin.path
        if plugin_dir.is_dir():
            result[plugin.name] = set(discover_projects(plugin_dir))
        else:
            result[plugin.name] = set()
    return result


def _update_repo_plugins(
    registry: PluginRegistry,
    repo_url: str,
    clone_dir: Path,
    pre_pull_projects: Optional[dict[str, set[str]]] = None,
) -> list[str]:
    """Re-read registry.yml and update ALL installed plugins from the given repo.

    After git pull updates the working tree, every installed plugin from the
    same repository must have its plugins.yml metadata (version, path) refreshed
    — not just the one the user asked for.

    Args:
        pre_pull_projects: {plugin_name: {project_names}} captured before pull.
            Passed to _migrate_removed_plugin so it can detect project moves
            even when the old directory was renamed/deleted by pull.

    Returns a list of error messages (empty on full success).
    """
    reg_info = parse_registry_yml(clone_dir)
    if not reg_info:
        return [f"No registry.yml in source for repo '{repo_url}'"]

    errors: list[str] = []
    installed = registry.list_installed()
    repo_plugins = [p for p in installed if p.source == repo_url and not p.linked]

    for plugin in repo_plugins:
        target_entry = next(
            (e for e in reg_info.plugins if e.name == plugin.name), None,
        )

        if not target_entry:
            logger.info("  Plugin '%s' no longer exists in source", plugin.name)
            snapshot = (
                pre_pull_projects.get(plugin.name)
                if pre_pull_projects else None
            )
            if not _migrate_removed_plugin(
                registry, plugin, clone_dir, reg_info,
                pre_pull_projects=snapshot,
            ):
                errors.append(f"Migration failed for '{plugin.name}'")
            continue

        plugin_path = clone_dir / target_entry.path.rstrip('/')
        if not plugin_path.is_dir():
            errors.append(
                f"Plugin directory not found for '{plugin.name}': {plugin_path}"
            )
            logger.warning(
                "Skipping '%s': registry.yml path '%s' does not exist",
                plugin.name, target_entry.path,
            )
            continue

        from .syncer import load_plugin_info
        info = load_plugin_info(plugin_path)
        version = info.version if info else '0.1.0'

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

    return errors


def update_plugin(registry: PluginRegistry, name: Optional[str] = None) -> None:
    """Update a plugin (or all if name is None) via git pull.

    Raises PluginError on failure.
    """
    from .installer import _auto_migrate
    _auto_migrate(registry)

    installed = registry.list_installed()
    if not installed:
        logger.info("No plugins installed")
        return

    targets = installed if name is None else [
        p for p in installed if p.name == name
    ]

    if name and not targets:
        raise PluginError(f"Plugin '{name}' is not installed")

    # Snapshot project lists BEFORE pull so migration can detect moves
    # even after the working tree is overwritten by git pull.
    _pre_pull_projects = _snapshot_plugin_projects(registry, installed)

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

            # After pull, update ALL installed plugins from this repo
            # (not just the named target) so metadata stays in sync.
            repo_errors = _update_repo_plugins(
                registry, repo_reg.url, clone_dir,
                pre_pull_projects=_pre_pull_projects,
            )
            errors.extend(repo_errors)
        else:
            logger.info("Skip: '%s' (repo already pulled and refreshed)", plugin.name)

    sync_projects(registry)

    if errors:
        raise PluginError(
            "Some plugins failed to update:\n" + "\n".join(f"  - {e}" for e in errors)
        )
