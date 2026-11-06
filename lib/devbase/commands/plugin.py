"""Plugin management CLI commands"""

import sys
from pathlib import Path

from devbase.errors import DevbaseError
from devbase.log import get_logger
from devbase.plugin import creator
from devbase.plugin.github import GhClient
from devbase.plugin.registry import PluginRegistry
from devbase.plugin.installer import install_plugin, uninstall_plugin
from devbase.plugin.updater import update_plugin
from devbase.plugin.info import show_plugin_info, show_available_plugins
from devbase.plugin.syncer import discover_projects, sync_projects
from devbase.plugin.migrator import migrate
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
        'migrate':   lambda: cmd_plugin_migrate(devbase_root),
        'repo':      lambda: cmd_repo(devbase_root, args),
        'create':    lambda: cmd_plugin_create(devbase_root, args),
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
            # plugin info・status と同じ数え方 (#276)
            proj_count = len(discover_projects(devbase_root / p.path))
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


def cmd_plugin_migrate(devbase_root: Path) -> int:
    """Migrate legacy plugins/ copy installs to repos/ persistent clones"""
    registry = PluginRegistry(devbase_root)
    try:
        result = migrate(registry)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1

    if not (result.migrated or result.preserved or result.skipped):
        logger.info("No legacy plugins/ installs to migrate.")
        return 0

    if result.migrated:
        logger.info("Migrated %d plugin(s) to repos/: %s",
                    len(result.migrated), ", ".join(result.migrated))
    if result.preserved:
        logger.warning(
            "Preserved %d plugin(s) with local changes as plugins/<name>.bak "
            "(reconcile manually): %s",
            len(result.preserved), ", ".join(result.preserved))
    if result.skipped:
        logger.warning("Could not migrate %d plugin(s): %s",
                       len(result.skipped), ", ".join(result.skipped))
        for err in result.errors:
            logger.warning("  %s", err)
        return 1
    return 0


def run_creation(registry, gh, plan, yes: bool) -> int:
    """作成の計画を示して確認を取り、実行する (#514 の E4 以降)"""
    if plan.noop_reason:
        logger.info("%s", plan.noop_reason)
        return 0

    print(plan.describe())
    if not yes and not _confirmed():
        return 1
    creator.execute(registry, gh, plan)
    return 0


def _confirmed() -> bool:
    """TTY で確認を取る。TTY が無ければ ``--yes`` を求めて断る"""
    if not sys.stdin.isatty():
        logger.error("確認できる端末がありません。この計画で進めるなら --yes を付けて打ってください")
        return False
    try:
        answer = input("この計画で進めますか? [y/N]: ")
    except (EOFError, KeyboardInterrupt):
        answer = ''
    if answer.strip().lower() in ('y', 'yes'):
        return True
    logger.info("中止しました。何も変えていません")
    return False


def cmd_plugin_create(devbase_root: Path, args) -> int:
    """`plugin create`: リポジトリへプラグインを足して導入する"""
    registry = PluginRegistry(devbase_root)
    gh = GhClient()
    try:
        plan = creator.plan_plugin(registry, gh, args.repo,
                                   name=getattr(args, 'name', None),
                                   public=getattr(args, 'public', False))
        return run_creation(registry, gh, plan, getattr(args, 'yes', False))
    except DevbaseError as e:
        logger.error("%s", e)
        return 1


def cmd_repo_create(devbase_root: Path, args) -> int:
    """`plugin repo create`: プラグインリポジトリを用意して登録する"""
    registry = PluginRegistry(devbase_root)
    gh = GhClient()
    try:
        plan = creator.plan_repo(registry, gh, args.repo,
                                 public=getattr(args, 'public', False))
        return run_creation(registry, gh, plan, getattr(args, 'yes', False))
    except DevbaseError as e:
        logger.error("%s", e)
        return 1


def cmd_repo(devbase_root: Path, args) -> int:
    """Dispatch repo subcommands"""
    registry = PluginRegistry(devbase_root)
    repo_cmd = getattr(args, 'repo_command', None)

    if not repo_cmd:
        logger.error("Usage: devbase plugin repo <create|add|remove|list|refresh>")
        return 1

    if repo_cmd == 'create':
        return cmd_repo_create(devbase_root, args)

    handlers = {
        'add':     lambda: add_repository(registry, args.url, name=args.name),
        'remove':  lambda: remove_repository(registry, args.name,
                                               force=getattr(args, 'force', False)),
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
            refresh_repository(registry, repo.name, sync=False)
        except DevbaseError as e:
            logger.error("%s", e)
            errors.append(str(e))

    # Sync once after all repos are refreshed (instead of per-repo)
    sync_projects(registry)

    if errors:
        raise DevbaseError(f"{len(errors)} repository refresh(es) failed")
