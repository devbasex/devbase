"""Project symlink synchronization for plugins"""

import yaml
from pathlib import Path
from typing import Optional

from devbase.log import get_logger

from .registry import PluginRegistry
from .models import InstalledPlugin, PluginInfo

logger = get_logger("devbase.plugin.syncer")


def load_plugin_info(plugin_dir: Path) -> Optional[PluginInfo]:
    """Load plugin.yml from a plugin directory"""
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


def _extract_owner(plugin: InstalledPlugin) -> str:
    """Extract a unique suffix identifier from a plugin for collision resolution.

    For repos/-based plugins: full owner--repo dirname from repos/<owner>--<repo>/...
      This ensures uniqueness when the same owner has multiple repos.
    For --link plugins: basename of the source path
    """
    if plugin.linked:
        return Path(plugin.source).name if plugin.source else plugin.name

    parts = Path(plugin.path).parts
    if len(parts) >= 2 and parts[0] == 'repos':
        # Return full dir_name (owner--repo) to avoid collision
        # between repos from the same owner
        return parts[1]
    return plugin.name


def _collect_project_candidates(
    registry: PluginRegistry,
    installed: list[InstalledPlugin],
    verbose: bool,
) -> dict[str, list[tuple[InstalledPlugin, int]]]:
    """全 plugin のプロジェクトを project 名 -> [(plugin, priority)] へ集約する。"""
    candidates: dict[str, list[tuple[InstalledPlugin, int]]] = {}
    for plugin in installed:
        plugin_dir = registry.devbase_root / plugin.path
        if not plugin_dir.is_dir():
            if verbose:
                logger.warning("Plugin directory missing: %s", plugin.path)
            continue

        info = load_plugin_info(plugin_dir)
        priority = info.priority if info else 0

        for proj_name in discover_projects(plugin_dir):
            candidates.setdefault(proj_name, []).append((plugin, priority))
    return candidates


def _link_loser_projects(
    projects_dir: Path,
    proj_name: str,
    losers: list[tuple[InstalledPlugin, int]],
    real_projects: set,
    verbose: bool,
) -> int:
    """衝突に敗れた plugin のプロジェクトを <proj>.<owner> サフィックスで symlink する。

    Returns the number of symlinks created.
    """
    created = 0
    for loser_plugin, _ in losers:
        owner = _extract_owner(loser_plugin)
        suffix_name = f"{proj_name}.{owner}"

        if suffix_name in real_projects:
            if verbose:
                logger.info("  Skip: %s (real directory exists)", suffix_name)
            continue

        suffix_link = projects_dir / suffix_name
        if suffix_link.exists() or suffix_link.is_symlink():
            if verbose:
                logger.warning("  Skip: %s (symlink already exists)", suffix_name)
            continue
        suffix_link.symlink_to(_make_relative_target(loser_plugin, proj_name))
        created += 1
    return created


def sync_projects(registry: PluginRegistry, verbose: bool = True) -> int:
    """Synchronize project symlinks from all installed plugins.

    Creates symlinks in projects/ pointing to plugin directories:
    - repos/-based plugins: projects/<proj> -> ../repos/<owner>--<repo>/<plugin>/projects/<proj>
    - --link plugins: projects/<proj> -> ../plugins/<name>/projects/<proj>

    On name collision, the winner (highest priority) gets the bare name,
    losers get <proj>.<owner> suffix symlinks.

    Returns:
        Number of symlinks created
    """
    projects_dir = registry.get_projects_dir()
    projects_dir.mkdir(exist_ok=True)

    real_projects = {
        entry.name for entry in projects_dir.iterdir()
        if not entry.is_symlink() and entry.is_dir()
    }

    for entry in projects_dir.iterdir():
        if entry.is_symlink():
            entry.unlink()

    installed = registry.list_installed()
    if not installed:
        if verbose:
            logger.info("No plugins installed")
        return 0

    project_candidates = _collect_project_candidates(registry, installed, verbose)

    created = 0
    for proj_name, candidates in sorted(project_candidates.items()):
        if proj_name in real_projects:
            if verbose:
                logger.info("  Skip: %s (real directory exists)", proj_name)
            continue

        # 優先度降順 → plugin 名昇順。先頭が bare 名を獲得する winner。
        candidates.sort(key=lambda c: (-c[1], c[0].name))
        (winner_plugin, winner_priority), losers = candidates[0], candidates[1:]

        if losers and verbose:
            logger.warning(
                "Project '%s' exists in multiple plugins — using '%s' (priority: %d)",
                proj_name, winner_plugin.name, winner_priority,
            )
            for loser_plugin, _ in losers:
                logger.info(
                    "  Also available as: projects/%s.%s",
                    proj_name, _extract_owner(loser_plugin),
                )

        link_path = projects_dir / proj_name
        link_path.symlink_to(_make_relative_target(winner_plugin, proj_name))
        created += 1

        created += _link_loser_projects(
            projects_dir, proj_name, losers, real_projects, verbose,
        )

    if verbose:
        logger.info("Synced %d project(s) from %d plugin(s)", created, len(installed))

    return created


def _make_relative_target(plugin: InstalledPlugin, proj_name: str) -> Path:
    """Build the relative symlink target from projects/ to a plugin's project."""
    return Path('..') / plugin.path / 'projects' / proj_name
