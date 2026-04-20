"""Plugin management CLI commands"""

from pathlib import Path

from devbase.errors import DevbaseError
from devbase.log import get_logger
from devbase.plugin.registry import PluginRegistry
from devbase.plugin.installer import install_plugin, uninstall_plugin
from devbase.plugin.updater import update_plugin
from devbase.plugin.info import show_plugin_info, show_available_plugins
from devbase.plugin.syncer import sync_projects
from devbase.plugin.repo_manager import (
    add_repository,
    remove_repository,
    show_repositories,
    refresh_repository,
)

logger = get_logger("devbase.commands.plugin")


def cmd_plugin(devbase_root: Path, args) -> int:
    """Dispatch plugin subcommands"""
    subcmd = getattr(args, 'subcommand', None)

    handlers = {
        'list':      lambda: cmd_plugin_list(devbase_root,
                                              available=getattr(args, 'available', False)),
        'install':   lambda: cmd_plugin_install(devbase_root,
                                                getattr(args, 'source', ''),
                                                link=getattr(args, 'link', False),
                                                install_all=getattr(args, 'install_all', False)),
        'uninstall': lambda: cmd_plugin_uninstall(devbase_root, getattr(args, 'name', '')),
        'update':    lambda: cmd_plugin_update(devbase_root, getattr(args, 'name', None)),
        'info':      lambda: cmd_plugin_info(devbase_root, getattr(args, 'name', '')),
        'sync':      lambda: cmd_sync(devbase_root),
        'repo':      lambda: cmd_repo(devbase_root, args),
    }

    handler = handlers.get(subcmd)
    if handler:
        return handler()

    logger.error("サブコマンドを指定してください: %s", ', '.join(handlers))
    return 1


def cmd_plugin_list(devbase_root: Path, available: bool = False) -> int:
    """List installed or available plugins"""
    registry = PluginRegistry(devbase_root)

    try:
        if available:
            show_available_plugins(registry)
            return 0

        installed = registry.list_installed()
        if not installed:
            logger.info("No plugins installed.")
            logger.info("Use 'devbase plugin install <name>' to install plugins.")
            return 0

        total_projects = 0
        print("Installed plugins:")
        print(f"  {'NAME':<20} {'VERSION':<10} {'PROJECTS':<10} {'SOURCE'}")
        print(f"  {'-'*20} {'-'*10} {'-'*10} {'-'*30}")
        for p in installed:
            p_dir = devbase_root / p.path / 'projects'
            proj_count = 0
            if p_dir.is_dir():
                proj_count = len([d for d in p_dir.iterdir() if d.is_dir()])
            total_projects += proj_count
            source_display = p.source
            if p.linked:
                source_display += " (linked)"
            print(f"  {p.name:<20} {p.version:<10} {proj_count:<10} {source_display}")

        print(f"\nTotal: {len(installed)} plugin(s), {total_projects} project(s)")
        return 0
    except DevbaseError as e:
        logger.error("%s", e)
        return 1


def cmd_plugin_install(
    devbase_root: Path,
    source: str,
    link: bool = False,
    install_all: bool = False,
) -> int:
    """Install a plugin"""
    registry = PluginRegistry(devbase_root)
    try:
        install_plugin(registry, source, link=link, install_all=install_all)
        return 0
    except DevbaseError as e:
        logger.error("%s", e)
        return 1


def cmd_plugin_uninstall(devbase_root: Path, name: str) -> int:
    """Uninstall a plugin"""
    registry = PluginRegistry(devbase_root)
    try:
        uninstall_plugin(registry, name)
        return 0
    except DevbaseError as e:
        logger.error("%s", e)
        return 1


def cmd_plugin_update(devbase_root: Path, name: str = None) -> int:
    """Update a plugin (or all)"""
    registry = PluginRegistry(devbase_root)
    try:
        update_plugin(registry, name)
        return 0
    except DevbaseError as e:
        logger.error("%s", e)
        return 1


def cmd_plugin_info(devbase_root: Path, name: str) -> int:
    """Show detailed plugin information"""
    registry = PluginRegistry(devbase_root)
    try:
        show_plugin_info(registry, name)
        return 0
    except DevbaseError as e:
        logger.error("%s", e)
        return 1


def cmd_sync(devbase_root: Path) -> int:
    """Resync project symlinks"""
    registry = PluginRegistry(devbase_root)
    sync_projects(registry)
    return 0


def cmd_repo(devbase_root: Path, args) -> int:
    """Dispatch repo subcommands"""
    registry = PluginRegistry(devbase_root)
    repo_cmd = getattr(args, 'repo_command', None)

    if not repo_cmd:
        logger.error("Usage: devbase plugin repo <add|remove|list|refresh>")
        return 1

    handlers = {
        'add':     lambda: add_repository(registry, args.url, name=args.name),
        'remove':  lambda: remove_repository(registry, args.name),
        'list':    lambda: show_repositories(registry),
        'refresh': lambda: _repo_refresh(registry, args),
    }

    handler = handlers.get(repo_cmd)
    if not handler:
        logger.error("Unknown repo command: %s", repo_cmd)
        return 1

    try:
        handler()
        return 0
    except DevbaseError as e:
        logger.error("%s", e)
        return 1


def _repo_refresh(registry, args):
    """リポジトリのリフレッシュ処理"""
    if args.name:
        refresh_repository(registry, args.name)
        return

    repos = registry.list_repositories()
    if not repos:
        logger.info("No repositories registered.")
        return

    errors = []
    for repo in repos:
        try:
            refresh_repository(registry, repo.name)
        except DevbaseError as e:
            logger.error("%s", e)
            errors.append(str(e))
    if errors:
        raise DevbaseError(f"{len(errors)} repository refresh(es) failed")
