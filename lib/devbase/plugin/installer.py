"""Plugin installer - handles install/uninstall operations"""

import hashlib
import os
import shutil
import subprocess
import tempfile
import yaml
from dataclasses import dataclass, field
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


def _replace_entry(path: Path) -> None:
    """Remove ``path`` (file, symlink, or directory) so it can be replaced."""
    if path.is_symlink() or not path.is_dir():
        path.unlink()
    else:
        shutil.rmtree(path)


def _hash_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a regular file's contents."""
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class _SyncReport:
    """Summary of an in-place plugin sync, surfaced to users after update."""
    added: list[Path] = field(default_factory=list)
    updated: list[Path] = field(default_factory=list)
    kept_local: list[Path] = field(default_factory=list)
    preserved_orphans: list[Path] = field(default_factory=list)


# Files at the plugin root that are upstream-owned metadata: always overwritten
# so registry version/description never desync from upstream even if a user
# happened to edit them locally.
_ALWAYS_OVERWRITE_AT_ROOT = frozenset({'plugin.yml'})


def _sync_dir(src: Path, dst: Path, report: _SyncReport, rel: Path = Path('.')) -> None:
    """Conservatively sync ``src`` → ``dst``, preserving user edits.

    Semantics (per file in src/dst):

    | src | dst | content | action |
    |---|---|---|---|
    | exists | missing |  -  | copy from src (record as ``added``) |
    | exists | exists  | same | no-op |
    | exists | exists  | differ | keep dst, write src as ``<name>.new`` (``kept_local``) |
    | missing | exists |  -  | leave dst alone (``preserved_orphans``) |

    Exception: files named in ``_ALWAYS_OVERWRITE_AT_ROOT`` at the plugin root
    are always overwritten with upstream content (treated as plugin metadata,
    not user-editable).

    Preserves the inode of ``dst`` and of subdirectories present in both — a
    user whose CWD lives inside ``dst`` (typically via a ``projects/<name>``
    symlink resolving into the plugin tree) keeps a valid CWD across updates.

    User-only files (orphans) and user-edited files are never destroyed.
    """
    dst.mkdir(parents=True, exist_ok=True)

    src_entries = {e.name: e for e in src.iterdir()}
    dst_entries = {e.name: e for e in dst.iterdir()}

    for name, dst_entry in dst_entries.items():
        if name in src_entries:
            continue
        if name.endswith('.new'):
            # `.new` is our own conflict marker — refresh, don't preserve.
            _replace_entry(dst_entry)
            continue
        report.preserved_orphans.append(rel / name)

    for name, src_entry in src_entries.items():
        dst_entry = dst / name
        sub_rel = rel / name
        if (
            rel == Path('.')
            and name in _ALWAYS_OVERWRITE_AT_ROOT
            and not src_entry.is_symlink()
            and not src_entry.is_dir()
        ):
            if dst_entry.is_symlink() or dst_entry.exists():
                _replace_entry(dst_entry)
            shutil.copy2(src_entry, dst_entry)
            report.updated.append(sub_rel)
            continue
        if src_entry.is_symlink():
            link_target = os.readlink(src_entry)
            if dst_entry.is_symlink() and os.readlink(dst_entry) == link_target:
                continue
            if dst_entry.is_symlink() or dst_entry.exists():
                # Conflict: leave user's, drop upstream alongside as `.new` symlink.
                new_dst = dst_entry.with_name(dst_entry.name + '.new')
                if new_dst.is_symlink() or new_dst.exists():
                    _replace_entry(new_dst)
                os.symlink(link_target, new_dst)
                report.kept_local.append(sub_rel)
            else:
                os.symlink(link_target, dst_entry)
                report.added.append(sub_rel)
        elif src_entry.is_dir():
            if dst_entry.is_symlink() or (dst_entry.exists() and not dst_entry.is_dir()):
                # Type mismatch: user has a file/symlink where upstream has a dir.
                # Drop upstream alongside as `<name>.new/`.
                new_dst = dst_entry.with_name(dst_entry.name + '.new')
                if new_dst.is_symlink() or (new_dst.exists() and not new_dst.is_dir()):
                    _replace_entry(new_dst)
                _sync_dir(src_entry, new_dst, report, sub_rel)
                report.kept_local.append(sub_rel)
            else:
                already_existed = dst_entry.is_dir()
                _sync_dir(src_entry, dst_entry, report, sub_rel)
                if not already_existed:
                    report.added.append(sub_rel)
        else:
            if not dst_entry.exists() and not dst_entry.is_symlink():
                shutil.copy2(src_entry, dst_entry)
                report.added.append(sub_rel)
                continue
            if dst_entry.is_symlink() or dst_entry.is_dir():
                # Type mismatch: user has a symlink/dir where upstream has a file.
                new_dst = dst_entry.with_name(dst_entry.name + '.new')
                if new_dst.is_symlink() or new_dst.exists():
                    _replace_entry(new_dst)
                shutil.copy2(src_entry, new_dst)
                report.kept_local.append(sub_rel)
                continue
            # Both are regular files — compare content.
            if _hash_file(src_entry) == _hash_file(dst_entry):
                continue
            new_dst = dst_entry.with_name(dst_entry.name + '.new')
            if new_dst.is_symlink() or new_dst.exists():
                _replace_entry(new_dst)
            shutil.copy2(src_entry, new_dst)
            report.kept_local.append(sub_rel)


def copy_plugin(
    registry: PluginRegistry,
    name: str,
    plugin_path: Path,
    source_display: str,
    plugins_dir: Path,
) -> None:
    """Install or update a plugin from a cloned repo into ``plugins/``.

    For updates, contents are synced in place (preserving directory inodes
    and user-edited files) instead of rmtree+copytree. User-edited files
    are kept as-is; the upstream version of a conflicting file is dropped
    alongside with a ``.new`` suffix for the user to diff/merge manually.
    Files present only in the user's working tree (orphans) are preserved.

    Raises PluginError on failure.
    """
    if not plugin_path.is_dir():
        raise PluginError(f"Plugin directory not found: {plugin_path}")

    dest = plugins_dir / name
    if dest.is_symlink():
        logger.warning("Removing existing plugin '%s' (symlink)", name)
        dest.unlink()
        shutil.copytree(plugin_path, dest)
    elif dest.exists():
        logger.info("Updating existing plugin '%s'", name)
        report = _SyncReport()
        _sync_dir(plugin_path, dest, report)
        if report.kept_local:
            logger.warning(
                "  %d local edit(s) kept; upstream saved as .new alongside:",
                len(report.kept_local),
            )
            for p in report.kept_local[:10]:
                logger.warning("    - %s (upstream: %s.new)", p, p.name)
            if len(report.kept_local) > 10:
                logger.warning("    ... and %d more", len(report.kept_local) - 10)
        if report.preserved_orphans:
            logger.info(
                "  %d local-only file(s) preserved (not in upstream)",
                len(report.preserved_orphans),
            )
    else:
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
