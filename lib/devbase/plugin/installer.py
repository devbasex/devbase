"""Plugin installer - handles install/uninstall operations"""

import os
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
    plugins = []
    for p_data in data.get('plugins', []):
        plugins.append(RegistryEntry(
            name=p_data.get('name', ''),
            path=p_data.get('path', ''),
            description=p_data.get('description', ''),
        ))
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
    if repo.startswith('http://') or repo.startswith('https://') or repo.startswith('git@'):
        return repo
    if repo.startswith('/') or repo.startswith('.'):
        return repo  # local path
    # GitHub shorthand: user/repo
    return f"https://github.com/{repo}.git"


def install_plugin(
    registry: PluginRegistry,
    source_str: str,
    link: bool = False,
    install_all: bool = False,
) -> None:
    """Install a plugin from a source string.

    Raises PluginError on failure.
    """
    source = PluginSource.parse(source_str, link=link)
    plugins_dir = registry.get_plugins_dir()

    if not source.repo and source.plugin_name:
        result = registry.find_plugin_in_repos(source.plugin_name)
        if result:
            repo, avail_plugin = result
            repo_source = PluginSource(
                repo=repo.url, plugin_name=source.plugin_name,
                ref=source.ref, linked=False,
            )
            _install_from_repo(
                registry, repo_source, install_all=False,
            )
            return
        raise PluginError(
            f"Plugin '{source.plugin_name}' not found in registered repositories.\n"
            "Use 'devbase plugin repo add <url>' to register a repository first.\n"
            "Use 'devbase plugin repo list' to see registered repositories and available plugins."
        )

    repo_url = resolve_repo_url(source.repo)

    if link and (Path(source.repo).is_dir()):
        plugins_dir.mkdir(exist_ok=True)
        _install_from_local(registry, source, plugins_dir)
        return

    # Auto-register the repository if not already registered, so that
    # `devbase plugin install user/repo:plugin-name` keeps working without
    # a prior `repo add`.
    if not registry.get_repository_by_url(repo_url):
        if source.ref:
            raise PluginError(
                f"Cannot use @{source.ref} with unregistered repository '{repo_url}'.\n"
                "Permanent clones track the default branch and do not support pinned refs.\n"
                "Register the repository first, then install without @ref:\n"
                f"  devbase plugin repo add {repo_url}\n"
                f"  devbase plugin install {repo_url}:{source.plugin_name}"
            )
        from .repo_manager import add_repository
        try:
            add_repository(registry, repo_url)
        except Exception as e:
            raise PluginError(
                f"Repository '{repo_url}' is not registered and auto-registration failed: {e}\n"
                "Use 'devbase plugin repo add <url>' to register manually."
            )

    # Reject @ref on already-registered repos — the permanent clone tracks
    # the default branch and does not support pinned refs.  This matches the
    # validation for *unregistered* repos (line 126-133 above).
    if source.ref:
        raise PluginError(
            f"Cannot use @{source.ref} with registered repository '{repo_url}'.\n"
            "Permanent clones track the default branch and do not support pinned refs.\n"
            f"Install without @ref:\n"
            f"  devbase plugin install {repo_url}:{source.plugin_name}"
        )

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
    local_path = Path(source.repo)

    if source.plugin_name:
        plugin_path = local_path / source.plugin_name
        if not plugin_path.is_dir():
            reg_info = parse_registry_yml(local_path)
            if reg_info:
                for entry in reg_info.plugins:
                    if entry.name == source.plugin_name:
                        plugin_path = local_path / entry.path.rstrip('/')
                        break
            if not plugin_path.is_dir():
                raise PluginError(f"Plugin '{source.plugin_name}' not found in {local_path}")

        _link_plugin(registry, source.plugin_name, plugin_path, source.repo, plugins_dir)
    else:
        raise PluginError("Plugin name is required for local install (use /path:plugin-name)")


def _link_plugin(
    registry: PluginRegistry,
    name: str,
    plugin_path: Path,
    source_display: str,
    plugins_dir: Path,
) -> None:
    """Create a symlink for a local plugin (--link install only)"""
    dest = plugins_dir / name
    if dest.exists() or dest.is_symlink():
        logger.warning("Removing existing plugin '%s'", name)
        if dest.is_symlink():
            dest.unlink()
        else:
            shutil.rmtree(dest)

    dest.symlink_to(plugin_path.resolve())

    info = load_plugin_info(plugin_path)
    version = info.version if info else '0.1.0'

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
        # Legacy repository registered before persistent-clone support.
        # Auto-migrate by creating a persistent clone in repos/.
        logger.info(
            "Migrating repository '%s' to persistent clone...", repo_reg.name,
        )
        from .repo_manager import _url_to_repos_dirname
        dir_name = _url_to_repos_dirname(repo_reg.url)
        repos_dir = registry.get_repos_dir()
        repos_dir.mkdir(exist_ok=True)
        clone_dir = repos_dir / dir_name

        if not clone_dir.is_dir():
            try:
                git_clone(repo_reg.url, clone_dir, shallow=False)
            except PluginError as e:
                raise PluginError(
                    f"Failed to create persistent clone for '{repo_reg.name}': {e}\n"
                    "Remove and re-add the repository:\n"
                    f"  devbase plugin repo remove {repo_reg.name}\n"
                    f"  devbase plugin repo add {repo_reg.url}"
                )

        # Validate the clone by parsing registry.yml BEFORE saving to
        # plugins.yml.  This prevents a broken clone from polluting the
        # persisted state.  If parsing fails, the old repository entry
        # (without local_path) is kept so the user can retry.
        reg_info = parse_registry_yml(clone_dir)
        if not reg_info:
            raise PluginError(
                f"No registry.yml found in cloned repository '{repo_reg.name}'.\n"
                "Remove and re-add the repository:\n"
                f"  devbase plugin repo remove {repo_reg.name}\n"
                f"  devbase plugin repo add {repo_reg.url}"
            )

        from .models import RegisteredRepository, AvailablePlugin
        local_path = f"repos/{dir_name}"
        # Build an up-to-date plugin list from the freshly cloned
        # registry.yml instead of carrying over stale metadata.
        migrated_plugins = [
            AvailablePlugin(
                name=e.name,
                description=e.description,
                path=e.path,
            )
            for e in reg_info.plugins
        ]
        updated_repo = RegisteredRepository(
            name=repo_reg.name,
            url=repo_reg.url,
            added_at=repo_reg.added_at,
            local_path=local_path,
            plugins=migrated_plugins,
        )
        registry.add_repository(updated_repo)
        repo_reg = updated_repo
        logger.info("Repository '%s' migrated to %s", repo_reg.name, local_path)

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
        errors = []
        for entry in reg_info.plugins:
            try:
                _register_repo_plugin(
                    registry, entry.name,
                    clone_dir / entry.path.rstrip('/'),
                    source.repo, repo_reg.local_path,
                )
            except PluginError as e:
                errors.append(str(e))
        sync_projects(registry)
        if errors:
            raise PluginError(
                "Some plugins failed to install:\n" + "\n".join(errors)
            )
        return

    if source.plugin_name:
        target_entry = None
        for entry in reg_info.plugins:
            if entry.name == source.plugin_name:
                target_entry = entry
                break

        if not target_entry:
            available = "\n".join(
                f"  - {e.name}: {e.description}" for e in reg_info.plugins
            )
            raise PluginError(
                f"Plugin '{source.plugin_name}' not found in repository\n"
                f"Available plugins:\n{available}"
            )

        _register_repo_plugin(
            registry, target_entry.name,
            clone_dir / target_entry.path.rstrip('/'),
            source.repo, repo_reg.local_path,
        )
        sync_projects(registry)
    else:
        logger.info("Available plugins in %s:", source.repo)
        for entry in reg_info.plugins:
            installed = registry.get(entry.name)
            status = " (installed)" if installed else ""
            logger.info("  %s: %s%s", entry.name, entry.description, status)
        logger.info(
            "Use 'devbase plugin install %s:PLUGIN_NAME' to install",
            source.repo,
        )
        raise PluginError("No plugin name specified")


def _register_repo_plugin(
    registry: PluginRegistry,
    name: str,
    plugin_path: Path,
    source_url: str,
    repo_local_path: str,
) -> None:
    """Register a plugin from repos/ (no file copy, just metadata)."""
    if not plugin_path.is_dir():
        raise PluginError(f"Plugin directory not found: {plugin_path}")

    info = load_plugin_info(plugin_path)
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
