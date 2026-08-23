"""Plugin installer - handles install/uninstall operations"""

import shutil
import subprocess
import yaml
from pathlib import Path
from typing import Optional

from devbase.errors import PluginError
from devbase.log import get_logger

from .models import (
    PluginSource, InstalledPlugin,
    RegistryInfo, RegistryEntry,
)
from .registry import PluginRegistry
from .requirements import (
    check_devbase_requirement,
    warn_unmet_devbase_requirement,
)
from .syncer import sync_projects, load_plugin_info

logger = get_logger("devbase.plugin.installer")


def parse_registry_yml(path: Path) -> Optional[RegistryInfo]:
    """Parse a registry.yml file"""
    yml_path = path / 'registry.yml'
    if not yml_path.exists():
        return None
    try:
        with open(yml_path) as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise PluginError(f"Failed to parse {yml_path}: {e}")
    plugins = [
        RegistryEntry(
            name=p_data.get('name', ''),
            path=p_data.get('path', ''),
            description=p_data.get('description', ''),
        )
        for p_data in data.get('plugins', [])
    ]
    return RegistryInfo(
        name=data.get('name', ''),
        description=data.get('description', ''),
        maintainer=data.get('maintainer', ''),
        official=data.get('official', False),
        plugins=plugins,
    )


def git_clone(
    url: str,
    dest: Path,
    ref: Optional[str] = None,
    shallow: bool = True,
) -> None:
    """Clone a git repository.

    Raises PluginError on failure.
    """
    cmd = ['git', 'clone']
    if shallow:
        cmd.extend(['--depth', '1'])
    if ref:
        cmd.extend(['--branch', ref])
    cmd.extend([url, str(dest)])
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            cmd, check=True, capture_output=True, text=True, cwd=str(dest.parent),
        )
    except subprocess.CalledProcessError as e:
        raise PluginError(f"git clone failed for {url}: {e.stderr.strip()}")


def resolve_repo_url(repo: str) -> str:
    """Resolve a repo string to a git URL"""
    if repo.startswith(('http://', 'https://', 'git@')):
        return repo
    if repo.startswith(('/', '.')):
        return repo  # local path
    # GitHub shorthand: user/repo
    return f"https://github.com/{repo}.git"


def _auto_migrate(registry: PluginRegistry) -> None:
    """Migrate any legacy plugins/ copy installs to repos/ before proceeding.

    Triggered on the first install/update after upgrading to repos/-based
    plugin management so users do not have to run `devbase plugin migrate`
    manually.  No-op when nothing legacy remains.
    """
    from .migrator import migrate, needs_migration
    if not needs_migration(registry):
        return
    logger.info("Legacy plugins/ installs detected — migrating to repos/...")
    result = migrate(registry)
    if result.migrated:
        logger.info("  Migrated: %s", ", ".join(result.migrated))
    # preserved/skipped recur on every install/update until the user
    # reconciles, so avoid re-emitting a loud per-plugin WARNING each time:
    # surface a single concise hint pointing at the explicit command, which
    # prints the full per-plugin detail when run.
    if result.preserved or result.skipped:
        pending = len(result.preserved) + len(result.skipped)
        logger.info(
            "  %d plugin(s) still need attention — run 'devbase plugin migrate' "
            "for details.", pending,
        )


def _pinned_ref_error(ref: str, subject: str, hint: str) -> PluginError:
    """@ref 指定を拒否する共通エラーを組み立てる。

    permanent clone は default branch を追跡するため pinned ref は非サポート。
    `subject` は対象 (plugin / repository) の説明、`hint` は復旧コマンド例。
    """
    return PluginError(
        f"Cannot use @{ref} with {subject}.\n"
        "Permanent clones track the default branch and do not support pinned refs.\n"
        f"{hint}"
    )


def _install_by_name(registry: PluginRegistry, source: PluginSource) -> None:
    """name-only インストール: 登録済みリポジトリ群から plugin を検索して導入する。"""
    # Reject @ref on name-only installs too — without this guard,
    # `devbase plugin install myplugin@v1` would silently drop the ref in
    # _install_from_repo() and install the default branch instead.
    # This matches the validation for unregistered/registered repos.
    if source.ref:
        raise _pinned_ref_error(
            source.ref, f"plugin '{source.plugin_name}'",
            f"Install without @ref:\n"
            f"  devbase plugin install {source.plugin_name}",
        )
    result = registry.find_plugin_in_repos(source.plugin_name)
    if not result:
        raise PluginError(
            f"Plugin '{source.plugin_name}' not found in registered repositories.\n"
            "Use 'devbase plugin repo add <url>' to register a repository first.\n"
            "Use 'devbase plugin repo list' to see registered repositories and available plugins."
        )
    repo, _avail_plugin = result
    repo_source = PluginSource(
        repo=repo.url, plugin_name=source.plugin_name,
        ref=None, linked=False,
    )
    _install_from_repo(registry, repo_source, install_all=False)


def _ensure_repo_registered(
    registry: PluginRegistry, repo_url: str, source: PluginSource,
) -> None:
    """インストール対象リポジトリの登録を保証し、@ref 指定を拒否する。

    Auto-register the repository if not already registered, so that
    `devbase plugin install user/repo:plugin-name` keeps working without
    a prior `repo add`.
    """
    if not registry.get_repository_by_url(repo_url):
        if source.ref:
            raise _pinned_ref_error(
                source.ref, f"unregistered repository '{repo_url}'",
                "Register the repository first, then install without @ref:\n"
                f"  devbase plugin repo add {repo_url}\n"
                f"  devbase plugin install {repo_url}:{source.plugin_name}",
            )
        from .repo_manager import add_repository
        try:
            add_repository(registry, repo_url)
        except Exception as e:
            raise PluginError(
                f"Repository '{repo_url}' is not registered and auto-registration failed: {e}\n"
                "Use 'devbase plugin repo add <url>' to register manually."
            )
        return

    # Reject @ref on already-registered repos too (same rationale as above).
    if source.ref:
        raise _pinned_ref_error(
            source.ref, f"registered repository '{repo_url}'",
            f"Install without @ref:\n"
            f"  devbase plugin install {repo_url}:{source.plugin_name}",
        )


def install_plugin(
    registry: PluginRegistry,
    source_str: str,
    link: bool = False,
    install_all: bool = False,
) -> None:
    """Install a plugin from a source string.

    Raises PluginError on failure.
    """
    _auto_migrate(registry)

    source = PluginSource.parse(source_str, link=link)

    if not source.repo and source.plugin_name:
        _install_by_name(registry, source)
        return

    repo_url = resolve_repo_url(source.repo)

    if link and Path(source.repo).is_dir():
        plugins_dir = registry.get_plugins_dir()
        plugins_dir.mkdir(exist_ok=True)
        _install_from_local(registry, source, plugins_dir)
        return

    _ensure_repo_registered(registry, repo_url, source)

    _install_from_repo(
        registry, PluginSource(
            repo=repo_url, plugin_name=source.plugin_name, ref=None, linked=False,
        ),
        install_all=install_all,
    )


def _install_from_local(
    registry: PluginRegistry,
    source: PluginSource,
    plugins_dir: Path,
) -> None:
    """Install plugin from a local path using symlink.

    Raises PluginError on failure.
    """
    if not source.plugin_name:
        raise PluginError("Plugin name is required for local install (use /path:plugin-name)")

    local_path = Path(source.repo)
    plugin_path = local_path / source.plugin_name
    if not plugin_path.is_dir():
        reg_info = parse_registry_yml(local_path)
        entry = reg_info.find_plugin(source.plugin_name) if reg_info else None
        if entry:
            plugin_path = local_path / entry.path.rstrip('/')
        if not plugin_path.is_dir():
            raise PluginError(f"Plugin '{source.plugin_name}' not found in {local_path}")

    _link_plugin(registry, source.plugin_name, plugin_path, source.repo, plugins_dir)


def _link_plugin(
    registry: PluginRegistry,
    name: str,
    plugin_path: Path,
    source_display: str,
    plugins_dir: Path,
) -> None:
    """Create a symlink for a local plugin (--link install only)"""
    # 互換性は既存インストールに触れる前に確かめる。後から落とすと、入れ替えの
    # ために消した既存プラグインが戻らないまま失敗する。
    info = load_plugin_info(plugin_path)
    check_devbase_requirement(info)
    version = info.version if info else '0.1.0'

    dest = plugins_dir / name
    if dest.exists() or dest.is_symlink():
        logger.warning("Removing existing plugin '%s'", name)
        if dest.is_symlink():
            dest.unlink()
        else:
            shutil.rmtree(dest)

    dest.symlink_to(plugin_path.resolve())

    registry.add(InstalledPlugin(
        name=name,
        version=version,
        source=source_display,
        installed_at=registry.now_iso(),
        path=f"plugins/{name}",
        linked=True,
    ))

    logger.info("Linked plugin '%s' from %s", name, plugin_path)
    sync_projects(registry)


def _migrate_repo_to_persistent_clone(registry: PluginRegistry, repo_reg):
    """local_path を持たない legacy リポジトリ登録を repos/ の永続 clone へ移行する。

    Legacy repository registered before persistent-clone support.
    Auto-migrate by creating a persistent clone in repos/.
    Returns the updated RegisteredRepository.
    """
    logger.info(
        "Migrating repository '%s' to persistent clone...", repo_reg.name,
    )
    from .repo_manager import _url_to_repos_dirname
    dir_name = _url_to_repos_dirname(repo_reg.url)
    repos_dir = registry.get_repos_dir()
    repos_dir.mkdir(exist_ok=True)
    clone_dir = repos_dir / dir_name

    readd_hint = (
        "Remove and re-add the repository:\n"
        f"  devbase plugin repo remove {repo_reg.name}\n"
        f"  devbase plugin repo add {repo_reg.url}"
    )

    if not clone_dir.is_dir():
        try:
            git_clone(repo_reg.url, clone_dir, shallow=False)
        except PluginError as e:
            raise PluginError(
                f"Failed to create persistent clone for '{repo_reg.name}': {e}\n"
                + readd_hint
            )

    # Validate the clone by parsing registry.yml BEFORE saving to
    # plugins.yml.  This prevents a broken clone from polluting the
    # persisted state.  If parsing fails, the old repository entry
    # (without local_path) is kept so the user can retry.
    reg_info = parse_registry_yml(clone_dir)
    if not reg_info:
        raise PluginError(
            f"No registry.yml found in cloned repository '{repo_reg.name}'.\n"
            + readd_hint
        )

    from dataclasses import replace
    local_path = f"repos/{dir_name}"
    # Build an up-to-date plugin list from the freshly cloned
    # registry.yml instead of carrying over stale metadata.
    updated_repo = replace(
        repo_reg, local_path=local_path, plugins=reg_info.available_plugins(),
    )
    registry.add_repository(updated_repo)
    logger.info("Repository '%s' migrated to %s", updated_repo.name, local_path)
    return updated_repo


def _install_all_from_repo(
    registry: PluginRegistry, reg_info, clone_dir: Path, source_repo: str,
    repo_local_path: str,
) -> None:
    """registry.yml の全 plugin を導入する (--all)。失敗分はまとめて報告する。"""
    errors = []
    for entry in reg_info.plugins:
        try:
            _register_repo_plugin(
                registry, entry.name,
                clone_dir / entry.path.rstrip('/'),
                source_repo, repo_local_path,
            )
        except PluginError as e:
            errors.append(str(e))
    sync_projects(registry)
    if errors:
        raise PluginError(
            "Some plugins failed to install:\n" + "\n".join(errors)
        )


def _install_named_from_repo(
    registry: PluginRegistry, reg_info, clone_dir: Path, source_repo: str,
    repo_local_path: str, plugin_name: str,
) -> None:
    """registry.yml から指定名の plugin を 1 つ導入する。"""
    target_entry = reg_info.find_plugin(plugin_name)
    if not target_entry:
        available = "\n".join(
            f"  - {e.name}: {e.description}" for e in reg_info.plugins
        )
        raise PluginError(
            f"Plugin '{plugin_name}' not found in repository\n"
            f"Available plugins:\n{available}"
        )

    _register_repo_plugin(
        registry, target_entry.name,
        clone_dir / target_entry.path.rstrip('/'),
        source_repo, repo_local_path,
    )
    sync_projects(registry)


def _install_from_repo(
    registry: PluginRegistry,
    source: PluginSource,
    install_all: bool = False,
) -> None:
    """Install plugin(s) from a registered repository via symlink to repos/.

    Raises PluginError on failure.
    """
    repo_reg = registry.get_repository_by_url(source.repo)
    if not repo_reg:
        raise PluginError(
            f"Repository '{source.repo}' is not registered.\n"
            "Use 'devbase plugin repo add <url>' first."
        )

    if not repo_reg.local_path:
        repo_reg = _migrate_repo_to_persistent_clone(registry, repo_reg)

    clone_dir = registry.devbase_root / repo_reg.local_path
    if not clone_dir.is_dir():
        raise PluginError(
            f"Clone directory not found: {clone_dir}\n"
            "Use 'devbase plugin repo remove' and 'repo add' to re-clone."
        )

    reg_info = parse_registry_yml(clone_dir)
    if not reg_info:
        raise PluginError("No registry.yml found in repository")

    if install_all:
        _install_all_from_repo(
            registry, reg_info, clone_dir, source.repo, repo_reg.local_path,
        )
        return

    if not source.plugin_name:
        # plugin 名未指定はインストールせず、候補一覧の表示のみ行いエラー終了する。
        logger.info("Available plugins in %s:", source.repo)
        for entry in reg_info.plugins:
            status = " (installed)" if registry.get(entry.name) else ""
            logger.info("  %s: %s%s", entry.name, entry.description, status)
        logger.info(
            "Use 'devbase plugin install %s:PLUGIN_NAME' to install",
            source.repo,
        )
        raise PluginError("No plugin name specified")

    _install_named_from_repo(
        registry, reg_info, clone_dir, source.repo, repo_reg.local_path,
        source.plugin_name,
    )


def _register_repo_plugin(
    registry: PluginRegistry,
    name: str,
    plugin_path: Path,
    source_url: str,
    repo_local_path: str,
    enforce_requirements: bool = True,
) -> None:
    """Register a plugin from repos/ (no file copy, just metadata).

    ``enforce_requirements=False`` にすると ``requires.devbase`` 違反を警告に
    留める。update 由来の呼び出し (プラグイン分割の移行) は旧登録を削除した
    あとに呼ばれるため、ここで例外にすると移行先が登録されないまま旧登録も
    失われる。git pull は済んでおり止めても整合は取れないので、警告で知らせて
    登録は進める。
    """
    if not plugin_path.is_dir():
        raise PluginError(f"Plugin directory not found: {plugin_path}")

    info = load_plugin_info(plugin_path)
    if enforce_requirements:
        check_devbase_requirement(info)
    else:
        warn_unmet_devbase_requirement(info)
    version = info.version if info else '0.1.0'

    # Use the actual plugin_path relative to devbase_root so that
    # subdirectory plugins (registry.yml path != name) resolve correctly.
    rel_path = str(plugin_path.relative_to(registry.devbase_root))

    registry.add(InstalledPlugin(
        name=name,
        version=version,
        source=source_url,
        installed_at=registry.now_iso(),
        path=rel_path,
        linked=False,
    ))

    logger.info("Installed plugin '%s' (v%s) from repos/", name, version)


def uninstall_plugin(registry: PluginRegistry, name: str) -> None:
    """Uninstall a plugin.

    For repos/-based plugins: removes registry entry and syncs symlinks only.
    For --link plugins: removes the symlink in plugins/.

    Raises PluginError if not installed.
    """
    plugin = registry.get(name)
    if not plugin:
        raise PluginError(f"Plugin '{name}' is not installed")

    if plugin.linked:
        plugin_dir = registry.devbase_root / plugin.path
        if plugin_dir.is_symlink():
            plugin_dir.unlink()
        elif plugin_dir.is_dir():
            shutil.rmtree(plugin_dir)

    registry.remove(name)
    logger.info("Uninstalled plugin '%s'", name)
    sync_projects(registry)
