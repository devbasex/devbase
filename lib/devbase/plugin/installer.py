"""Plugin installer - handles install/uninstall operations"""

import shutil
import subprocess
import tempfile
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


def git_clone(url: str, dest: Path, ref: Optional[str] = None) -> None:
    """Clone a git repository.

    Raises PluginError on failure.
    """
    cmd = ['git', 'clone', '--depth', '1']
    if ref:
        cmd.extend(['--branch', ref])
    cmd.extend([url, str(dest)])
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
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
    plugins_dir.mkdir(exist_ok=True)

    # Name-only: look up in registered repositories
    if not source.repo and source.plugin_name:
        result = registry.find_plugin_in_repos(source.plugin_name)
        if result:
            repo, avail_plugin = result
            repo_source = PluginSource(
                repo=repo.url, plugin_name=source.plugin_name,
                ref=source.ref, linked=False,
            )
            _install_from_repo(
                registry, repo_source, plugins_dir, install_all=False,
            )
            return
        raise PluginError(
            f"Plugin '{source.plugin_name}' not found in registered repositories.\n"
            "Use 'devbase plugin repo add <url>' to register a repository first.\n"
            "Use 'devbase plugin repo list' to see registered repositories and available plugins."
        )

    # Resolve repo URL
    repo_url = resolve_repo_url(source.repo)

    # Local path with --link
    if link and (Path(source.repo).is_dir()):
        _install_from_local(registry, source, plugins_dir)
        return

    # Git repository (user/repo:plugin-name or URL:plugin-name)
    _install_from_repo(
        registry, PluginSource(
            repo=repo_url, plugin_name=source.plugin_name, ref=source.ref, linked=False,
        ),
        plugins_dir,
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
        # Specific plugin within the repo
        plugin_path = local_path / source.plugin_name
        if not plugin_path.is_dir():
            # Try looking at registry.yml for path mapping
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
    """Create a symlink for a local plugin"""
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
    plugins_dir: Path,
    install_all: bool = False,
) -> None:
    """Install plugin(s) from a git repository.

    Raises PluginError on failure.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        clone_dir = Path(tmpdir) / 'repo'
        git_clone(source.repo, clone_dir, source.ref)

        reg_info = parse_registry_yml(clone_dir)
        if not reg_info:
            raise PluginError("No registry.yml found in repository")

        if install_all:
            # Install all plugins from the repo
            errors = []
            for entry in reg_info.plugins:
                try:
                    copy_plugin(
                        registry, entry.name,
                        clone_dir / entry.path.rstrip('/'),
                        source.repo, plugins_dir
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
            # Find specific plugin
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

            plugin_path = clone_dir / target_entry.path.rstrip('/')
            copy_plugin(
                registry, target_entry.name, plugin_path, source.repo, plugins_dir
            )
            sync_projects(registry)
        else:
            # No plugin specified - show available
            print(f"Available plugins in {source.repo}:")
            for entry in reg_info.plugins:
                installed = registry.get(entry.name)
                status = " (installed)" if installed else ""
                print(f"  {entry.name}: {entry.description}{status}")
            print(f"\nUse 'devbase plugin install {source.repo}:PLUGIN_NAME' to install")
            raise PluginError("No plugin name specified")


def copy_plugin(
    registry: PluginRegistry,
    name: str,
    plugin_path: Path,
    source_display: str,
    plugins_dir: Path,
) -> None:
    """Copy a plugin from cloned repo to plugins/.

    Raises PluginError on failure.
    """
    if not plugin_path.is_dir():
        raise PluginError(f"Plugin directory not found: {plugin_path}")

    dest = plugins_dir / name
    if dest.exists():
        logger.warning("Removing existing plugin '%s'", name)
        shutil.rmtree(dest)

    shutil.copytree(plugin_path, dest)

    info = load_plugin_info(dest)
    version = info.version if info else '0.1.0'

    registry.add(InstalledPlugin(
        name=name,
        version=version,
        source=source_display,
        installed_at=registry.now_iso(),
        path=f"plugins/{name}",
        linked=False,
    ))

    logger.info("Installed plugin '%s' (v%s)", name, version)


def uninstall_plugin(registry: PluginRegistry, name: str) -> None:
    """Uninstall a plugin.

    Raises PluginError if not installed.
    """
    plugin = registry.get(name)
    if not plugin:
        raise PluginError(f"Plugin '{name}' is not installed")

    plugin_dir = registry.devbase_root / plugin.path
    if plugin_dir.is_symlink():
        plugin_dir.unlink()
    elif plugin_dir.is_dir():
        shutil.rmtree(plugin_dir)

    registry.remove(name)
    logger.info("Uninstalled plugin '%s'", name)
    sync_projects(registry)
