"""Plugin migrator - migrates legacy plugins/ copy installs to repos/ clones"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from devbase.errors import PluginError
from devbase.log import get_logger

from .models import AvailablePlugin, InstalledPlugin, RegisteredRepository
from .registry import PluginRegistry

logger = get_logger("devbase.plugin.migrator")


@dataclass
class MigrationResult:
    """Outcome of a plugins/ -> repos/ migration run."""
    migrated: list[str] = field(default_factory=list)    # cleanly moved + copy deleted
    preserved: list[str] = field(default_factory=list)   # copy differed -> kept as .bak
    skipped: list[str] = field(default_factory=list)     # could not migrate
    errors: list[str] = field(default_factory=list)
    plugins_dir_cleaned: bool = False                    # plugins/ emptied to .gitkeep


def _is_legacy_plugin(plugin: InstalledPlugin) -> bool:
    """True if a plugin still uses the pre-PLAN04 plugins/<name> copy format.

    --link installs also live under plugins/ but are intentional and must not
    be migrated, so they are excluded.
    """
    if plugin.linked:
        return False
    parts = Path(plugin.path).parts
    return len(parts) >= 1 and parts[0] == 'plugins'


def needs_migration(registry: PluginRegistry) -> bool:
    """True if any installed plugin still uses the legacy plugins/ copy format."""
    return any(_is_legacy_plugin(p) for p in registry.list_installed())


def _unique_bak_path(old_dir: Path) -> Path:
    """Return a non-existing <name>.bak path, suffixing -2, -3, … if needed.

    A previous migration run may have already preserved <name>.bak awaiting
    manual reconciliation; never overwrite it, so each diverged copy lands in
    its own directory.
    """
    bak = old_dir.with_name(old_dir.name + '.bak')
    if not bak.exists():
        return bak
    n = 2
    while True:
        candidate = old_dir.with_name(f"{old_dir.name}.bak-{n}")
        if not candidate.exists():
            return candidate
        n += 1


def _entry_kind(p: Path) -> str:
    """Classify a path for diff purposes: symlink / dir / file / other.

    Symlinks are checked first (a symlink to a dir would otherwise read as a
    dir) so that a copy/clone type mismatch is always detected.
    """
    if p.is_symlink():
        return 'symlink'
    if p.is_dir():
        return 'dir'
    if p.is_file():
        return 'file'
    return 'other'


def _dirs_differ(copy_dir: Path, repo_dir: Path) -> bool:
    """True if the legacy copy differs in any way from the repos/ clone dir.

    Walks *every* entry (regular files, symlinks, and directories — including
    empty ones) and compares both the entry set and, per entry, its type plus
    file byte content / symlink target.  Any divergence (changed content, a
    user-added file/symlink/empty dir, a type mismatch, or an upstream-added
    entry) is treated as a difference so the copy is preserved rather than
    deleted — migration must never silently discard data.
    """
    def _entries(base: Path) -> set[Path]:
        return {p.relative_to(base) for p in base.rglob('*')}

    copy_entries = _entries(copy_dir)
    repo_entries = _entries(repo_dir)
    if copy_entries != repo_entries:
        return True

    for rel in copy_entries:
        fa, fb = copy_dir / rel, repo_dir / rel
        kind = _entry_kind(fa)
        if kind != _entry_kind(fb):
            return True
        if kind == 'symlink':
            if fa.readlink() != fb.readlink():
                return True
        elif kind == 'file':
            if fa.stat().st_size != fb.stat().st_size:
                return True
            if fa.read_bytes() != fb.read_bytes():
                return True
    return False


def _ensure_repo_cloned(
    registry: PluginRegistry, repo: RegisteredRepository,
) -> tuple[Path, RegisteredRepository]:
    """Return the repos/ clone dir for a repo, cloning it if necessary.

    If the repo was registered before persistent-clone support (no local_path)
    or the clone dir is missing, perform a full clone and persist local_path +
    a refreshed plugin list to plugins.yml.
    """
    from .installer import git_clone, parse_registry_yml
    from .repo_manager import _url_to_repos_dirname

    if repo.local_path:
        clone_dir = registry.devbase_root / repo.local_path
        if clone_dir.is_dir():
            return clone_dir, repo

    dir_name = _url_to_repos_dirname(repo.url)
    repos_dir = registry.get_repos_dir()
    repos_dir.mkdir(exist_ok=True)
    clone_dir = repos_dir / dir_name

    if not clone_dir.is_dir():
        git_clone(repo.url, clone_dir, shallow=False)

    reg_info = parse_registry_yml(clone_dir)
    if not reg_info:
        raise PluginError(
            f"No registry.yml found in cloned repository '{repo.name}'."
        )

    local_path = f"repos/{dir_name}"
    updated = RegisteredRepository(
        name=repo.name, url=repo.url, added_at=repo.added_at,
        local_path=local_path,
        plugins=[
            AvailablePlugin(name=e.name, description=e.description, path=e.path)
            for e in reg_info.plugins
        ],
    )
    registry.add_repository(updated)
    logger.info("Repository '%s' cloned to %s", repo.name, local_path)
    return clone_dir, updated


def _cleanup_plugins_dir(registry: PluginRegistry) -> bool:
    """Normalize plugins/ to just .gitkeep once it holds no live copy installs.

    Conservatively kept untouched when anything still depends on it:
    --link installs, skipped legacy copies still referenced by plugins.yml, or
    preserved <name>.bak directories awaiting manual reconciliation.  Returns
    True only when plugins/ was reduced to .gitkeep.
    """
    plugins_dir = registry.get_plugins_dir()
    if not plugins_dir.is_dir():
        return False

    if any(p.linked for p in registry.list_installed()):
        return False

    # A skipped legacy install still points into plugins/ — keep its files.
    if needs_migration(registry):
        return False

    entries = [e for e in plugins_dir.iterdir() if e.name != '.gitkeep']
    bak_dirs = [e for e in entries if '.bak' in e.name]
    if bak_dirs:
        logger.info(
            "plugins/ retained: %d preserved .bak dir(s) await manual reconciliation",
            len(bak_dirs),
        )
        return False

    gitkeep = plugins_dir / '.gitkeep'
    if not gitkeep.exists():
        gitkeep.touch()
    return True


def migrate(registry: PluginRegistry, *, run_sync: bool = True) -> MigrationResult:
    """Migrate legacy plugins/<name> copy installs to repos/ clones.

    For each legacy plugin: ensure its source repo is cloned to repos/, rewrite
    InstalledPlugin.path to the repos/ location, then delete the old copy when
    byte-identical or preserve it as <name>.bak when it diverged.  Finally
    re-sync project symlinks and empty plugins/ to .gitkeep when safe.
    """
    from .installer import parse_registry_yml
    from .syncer import load_plugin_info, sync_projects

    result = MigrationResult()
    legacy = [p for p in registry.list_installed() if _is_legacy_plugin(p)]
    if not legacy:
        return result

    for plugin in legacy:
        try:
            repo = registry.get_repository_by_url(plugin.source)
            if not repo:
                result.skipped.append(plugin.name)
                result.errors.append(
                    f"{plugin.name}: source repository not registered "
                    f"({plugin.source or 'no source URL'})"
                )
                continue

            clone_dir, repo = _ensure_repo_cloned(registry, repo)

            reg_info = parse_registry_yml(clone_dir)
            entry = None
            if reg_info:
                entry = next(
                    (e for e in reg_info.plugins if e.name == plugin.name), None,
                )
            if not entry:
                result.skipped.append(plugin.name)
                result.errors.append(
                    f"{plugin.name}: not found in registry.yml of '{repo.name}'"
                )
                continue

            repo_plugin_dir = clone_dir / entry.path.rstrip('/')
            if not repo_plugin_dir.is_dir():
                result.skipped.append(plugin.name)
                result.errors.append(
                    f"{plugin.name}: plugin dir missing in clone: {repo_plugin_dir}"
                )
                continue

            rel_path = str(repo_plugin_dir.relative_to(registry.devbase_root))
            info = load_plugin_info(repo_plugin_dir)
            version = info.version if info else plugin.version

            # Retire the old plugins/ copy FIRST, then update the registry.
            # Updating plugins.yml before the filesystem move means a failure
            # here would leave registry at repos/ (no longer "legacy", so never
            # retried) while the stale copy lingers — so the path rewrite must
            # only be committed once the copy has been removed or preserved.
            old_dir = registry.devbase_root / plugin.path
            if old_dir.is_dir() and not old_dir.is_symlink():
                if _dirs_differ(old_dir, repo_plugin_dir):
                    bak = _unique_bak_path(old_dir)
                    old_dir.rename(bak)
                    result.preserved.append(plugin.name)
                    logger.warning(
                        "Plugin '%s' had local changes — preserved at %s "
                        "(reconcile manually, then remove)",
                        plugin.name, bak,
                    )
                else:
                    shutil.rmtree(old_dir)
                    result.migrated.append(plugin.name)
            else:
                result.migrated.append(plugin.name)

            registry.add(InstalledPlugin(
                name=plugin.name,
                version=version,
                source=plugin.source,
                installed_at=plugin.installed_at,
                path=rel_path,
                linked=False,
            ))
        except Exception as e:
            result.skipped.append(plugin.name)
            result.errors.append(f"{plugin.name}: {e}")

    if run_sync:
        sync_projects(registry)

    result.plugins_dir_cleaned = _cleanup_plugins_dir(registry)
    return result
