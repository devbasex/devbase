"""Project symlink synchronization for plugins"""

import os
from pathlib import Path
from typing import Optional

from devbase.log import get_logger

from .registry import PluginRegistry
from .models import InstalledPlugin, PluginInfo

logger = get_logger("devbase.plugin.syncer")


def load_plugin_info(plugin_dir: Path) -> Optional[PluginInfo]:
    """Load plugin.yml from a plugin directory"""
    import yaml
    yml_path = plugin_dir / 'plugin.yml'
    if not yml_path.exists():
        return None
    with open(yml_path) as f:
        data = yaml.safe_load(f) or {}
    return PluginInfo(
        name=data.get('name', plugin_dir.name),
        version=data.get('version', '0.1.0'),
        description=data.get('description', ''),
        priority=data.get('priority', 0),
        requires_devbase=data.get('requires', {}).get('devbase') if isinstance(data.get('requires'), dict) else None,
    )


def discover_projects(plugin_dir: Path) -> list[str]:
    """Discover project directories within a plugin"""
    projects_dir = plugin_dir / 'projects'
    if not projects_dir.is_dir():
        return []
    return [
        d.name for d in sorted(projects_dir.iterdir())
        if d.is_dir() and not d.name.startswith('.')
    ]


def sync_projects(registry: PluginRegistry, verbose: bool = True) -> int:
    """Synchronize project symlinks from all installed plugins.

    Creates symlinks in projects/ pointing to plugins/*/projects/*

    Returns:
        Number of symlinks created
    """
    plugins_dir = registry.get_plugins_dir()
    projects_dir = registry.get_projects_dir()

    # Ensure projects/ exists
    projects_dir.mkdir(exist_ok=True)

    # Collect all existing non-symlink entries in projects/ (real directories)
    real_projects = set()
    for entry in projects_dir.iterdir():
        if not entry.is_symlink() and entry.is_dir():
            real_projects.add(entry.name)

    # Remove existing symlinks (clean slate)
    for entry in projects_dir.iterdir():
        if entry.is_symlink():
            entry.unlink()

    if not plugins_dir.is_dir():
        if verbose:
            logger.info("plugins/ directory does not exist yet")
        return 0

    # Build priority-sorted list of (project_name, plugin_name, plugin_priority, plugin_dir)
    project_candidates: dict[str, list[tuple[str, int, Path]]] = {}

    installed = registry.list_installed()
    installed_names = {p.name for p in installed}

    for plugin_entry in sorted(plugins_dir.iterdir()):
        if not plugin_entry.is_dir() or plugin_entry.name.startswith('.'):
            continue
        if plugin_entry.name not in installed_names:
            continue

        info = load_plugin_info(plugin_entry)
        priority = info.priority if info else 0

        for proj_name in discover_projects(plugin_entry):
            if proj_name not in project_candidates:
                project_candidates[proj_name] = []
            project_candidates[proj_name].append(
                (plugin_entry.name, priority, plugin_entry)
            )

    # Create symlinks with priority resolution
    created = 0
    for proj_name, candidates in sorted(project_candidates.items()):
        # Skip if real directory exists
        if proj_name in real_projects:
            if verbose:
                logger.info("  Skip: %s (real directory exists)", proj_name)
            continue

        # Sort by priority (highest first), then by name (alphabetical)
        candidates.sort(key=lambda c: (-c[1], c[0]))
        winner_plugin, winner_priority, winner_dir = candidates[0]

        if len(candidates) > 1 and verbose:
            logger.warning(
                "Project '%s' exists in multiple plugins — using '%s' (priority: %d)",
                proj_name, winner_plugin, winner_priority,
            )

        # Create relative symlink
        target = Path('..') / 'plugins' / winner_plugin / 'projects' / proj_name
        link_path = projects_dir / proj_name
        link_path.symlink_to(target)
        created += 1

    if verbose:
        logger.info("Synced %d project(s) from %d plugin(s)", created, len(installed))

    return created
