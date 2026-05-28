"""Plugin migrator - migrates legacy plugins/ copy installs to repos/ clones"""

import re
import shutil
import stat
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


# Names produced by _unique_bak_path: "<name>.bak" or "<name>.bak-<N>".
_BAK_NAME_RE = re.compile(r'\.bak(-\d+)?$')


def _is_bak_name(name: str) -> bool:
    """True if name matches the preserved-copy convention (<name>.bak[-N]).

    A substring check like ``'.bak' in name`` would wrongly flag unrelated
    entries such as ``my.bakery`` or ``notes.bak.txt``; anchor to the suffix.
    """
    return _BAK_NAME_RE.search(name) is not None


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


_EXEC_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


def _files_equal(fa: Path, fb: Path) -> bool:
    """Compare two regular files by exec bits, size, then byte content.

    Reads in fixed-size chunks rather than slurping the whole file so a large
    plugin asset can't exhaust memory during the migration scan.  Only the
    execute bits are compared (not the full S_IMODE): a local exec-bit change
    (e.g. an entry script the user made executable) is functionally meaningful
    and must be preserved, but read/write permission differences caused by the
    environment's umask or group settings should not spuriously force a .bak.
    """
    sa, sb = fa.stat(), fb.stat()
    if (sa.st_mode & _EXEC_BITS) != (sb.st_mode & _EXEC_BITS):
        return False
    if sa.st_size != sb.st_size:
        return False
    chunk = 64 * 1024
    with fa.open('rb') as a, fb.open('rb') as b:
        while True:
            ba, bb = a.read(chunk), b.read(chunk)
            if ba != bb:
                return False
            if not ba:
                return True


def _dirs_differ(copy_dir: Path, repo_dir: Path) -> bool:
    """True if deleting the legacy copy would discard data not in the clone.

    Walks *every* entry (regular files, symlinks, and directories — including
    empty ones).  An entry is treated as divergence only when it represents
    data the copy holds but the clone does not: a copy-only entry (user-added
    file/symlink/empty dir), or a common entry whose type, symlink target, or
    file content differs.  Upstream-only additions (present in the clone but
    not the copy) are *not* a difference — deleting the copy loses nothing — so
    a routine upstream change no longer forces a manual-reconcile .bak.
    """
    def _entries(base: Path) -> set[Path]:
        return {p.relative_to(base) for p in base.rglob('*')}

    copy_entries = _entries(copy_dir)
    repo_entries = _entries(repo_dir)

    # User-added entries live only in the copy and would be lost on delete.
    if copy_entries - repo_entries:
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
            if not _files_equal(fa, fb):
                return True
        elif kind == 'other':
            # A socket / pipe / device the copy holds can't be content-compared,
            # so it can't be proven identical — treat it as divergence and fall
            # back to preserving the copy (.bak) rather than risk deleting data
            # we couldn't inspect.
            return True
    return False


def _clone_is_healthy(clone_dir: Path) -> bool:
    """True if clone_dir looks like a usable repo clone (has .git + registry.yml).

    A repos/ dir that lost its .git (or whose registry.yml went missing) points
    migrated plugins at a tree that can never be pulled/updated again, so it
    must be re-cloned rather than reused.
    """
    return (
        clone_dir.is_dir()
        and (clone_dir / '.git').exists()
        and (clone_dir / 'registry.yml').is_file()
    )


def _reclaim_or_protect_existing(clone_dir: Path) -> None:
    """Clear a reusable leftover at clone_dir, or protect a .git-bearing tree.

    Called before (re-)cloning into clone_dir.  Three cases:

    - clone_dir is a symlink (broken, to a file, or even to a dir) -> remove
      the link; it is never a real persistent clone and git_clone would fail.
    - clone_dir does not exist -> nothing to do.
    - clone_dir exists but is not a directory (a stray file squatting on the
      path) -> remove it so git_clone can create the directory; a regular file
      cannot hold a git working tree, so nothing is lost.
    - clone_dir is a directory without .git -> a broken/partial clone that can
      never be pulled; remove it so a fresh clone repairs it.
    - clone_dir is a directory *with* .git -> it may hold uncommitted or
      unpushed local work, so refuse to delete it and raise asking the user to
      repair/remove it manually rather than silently destroying their changes.
    """
    # Check the symlink first: is_dir()/exists() both follow symlinks, so a
    # symlink-to-dir would otherwise slip through as a "directory".
    if clone_dir.is_symlink():
        clone_dir.unlink()
        return
    if not clone_dir.exists():
        return
    if not clone_dir.is_dir():
        # A regular file is squatting on the path; git_clone would fail. It can
        # hold no git working tree, so removing it loses nothing.
        clone_dir.unlink()
        return
    if not (clone_dir / '.git').exists():
        shutil.rmtree(clone_dir)
        return
    raise PluginError(
        f"Existing clone '{clone_dir}' has a .git but is missing "
        f"registry.yml; refusing to delete it to avoid losing local "
        f"changes. Restore registry.yml (e.g. 'git checkout -- "
        f"registry.yml') or remove the directory manually, then retry."
    )


def _ensure_repo_cloned(
    registry: PluginRegistry, repo: RegisteredRepository,
) -> tuple[Path, RegisteredRepository]:
    """Return the repos/ clone dir for a repo, cloning it if necessary.

    If the repo was registered before persistent-clone support (no local_path),
    the clone dir is missing, or the existing clone is broken (missing .git /
    registry.yml), perform a full clone and persist local_path + a refreshed
    plugin list to plugins.yml.
    """
    from .installer import git_clone, parse_registry_yml
    from .repo_manager import _url_to_repos_dirname

    if repo.local_path:
        clone_dir = registry.devbase_root / repo.local_path
        if _clone_is_healthy(clone_dir):
            return clone_dir, repo
        _reclaim_or_protect_existing(clone_dir)

    dir_name = _url_to_repos_dirname(repo.url)
    repos_dir = registry.get_repos_dir()
    repos_dir.mkdir(exist_ok=True)
    clone_dir = repos_dir / dir_name

    # A leftover from a previously interrupted clone (e.g. disk full or network
    # drop) would otherwise be reused forever — re-cloning is skipped while
    # parse_registry_yml() keeps failing, so migration can never self-heal.
    # Remove a leftover that is *not* a valid clone (missing .git, or a stray
    # non-directory squatting on the path) so the clone below re-creates it
    # cleanly; but a dir that still has .git may hold uncommitted/unpushed work
    # and is protected (raises) rather than destroyed.
    _reclaim_or_protect_existing(clone_dir)

    freshly_cloned = False
    if not clone_dir.is_dir():
        try:
            git_clone(repo.url, clone_dir, shallow=False)
            freshly_cloned = True
        except Exception:
            # Drop a partial clone so the next run starts from a clean slate.
            if clone_dir.is_dir():
                shutil.rmtree(clone_dir)
            raise

    reg_info = parse_registry_yml(clone_dir)
    if not reg_info:
        # registry.yml is missing/invalid.  Only discard a clone we just made
        # (guaranteed to hold no local work); an existing .git-bearing dir is
        # protected so we never delete uncommitted/unpushed changes — surface a
        # recoverable error instead, mirroring the local_path branch.
        if freshly_cloned:
            shutil.rmtree(clone_dir)
            raise PluginError(
                f"No registry.yml found in cloned repository '{repo.name}'."
            )
        raise PluginError(
            f"Existing clone '{clone_dir}' has a .git but is missing "
            f"registry.yml; refusing to delete it to avoid losing local "
            f"changes. Restore registry.yml (e.g. 'git checkout -- "
            f"registry.yml') or remove the directory manually, then retry."
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
    bak_dirs = [e for e in entries if _is_bak_name(e.name)]
    if bak_dirs:
        logger.info(
            "plugins/ retained: %d preserved .bak dir(s) await manual reconciliation",
            len(bak_dirs),
        )
        return False

    # Anything left over that is neither .gitkeep nor a preserved .bak means
    # plugins/ is not actually clean; leave it untouched and report uncleaned.
    leftover = [e for e in entries if e not in bak_dirs]
    if leftover:
        logger.info(
            "plugins/ retained: %d unexpected entr(y/ies) remain (%s)",
            len(leftover), ", ".join(sorted(e.name for e in leftover)),
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

    # Two-phase migration so a registry-save failure can never leave a copy
    # deleted while plugins.yml still points at the stale plugins/ path:
    #
    #   Phase 1 (no destructive fs ops): validate the repo/clone/entry and
    #     decide each copy's fate (delete vs preserve as .bak), collecting the
    #     repos/ path rewrites in `pending` and the retire actions in `retire`.
    #   Persist: write every rewrite in a single plugins.yml save.
    #   Phase 2 (destructive): only after plugins.yml is durably at repos/ do we
    #     delete/rename the old copies.
    #
    # Ordering rationale: if the save raises, no copy has been touched yet, so
    # the registry stays legacy and the next run retries cleanly with the copies
    # intact (recoverable).  Conversely the validated repos/ clone is known good
    # before we commit, so committing the rewrite first cannot strand a plugin
    # on a missing tree; a stray copy left by a phase-2 hiccup is merely surfaced
    # by _cleanup_plugins_dir, never silent data loss.
    pending: list[InstalledPlugin] = []
    retire: list[tuple[str, Path, Path]] = []  # (plugin_name, old_dir, repo_dir)

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

            old_dir = registry.devbase_root / plugin.path
            pending.append(InstalledPlugin(
                name=plugin.name,
                version=version,
                source=plugin.source,
                installed_at=plugin.installed_at,
                path=rel_path,
                linked=False,
            ))
            retire.append((plugin.name, old_dir, repo_plugin_dir))
        except Exception as e:
            result.skipped.append(plugin.name)
            result.errors.append(f"{plugin.name}: {e}")

    # Persist every validated path rewrite in a single save BEFORE retiring any
    # copy.  A failure here aborts with the copies untouched (recoverable).
    registry.add_many(pending)

    # Now that plugins.yml durably points at repos/, retire the old copies.
    for name, old_dir, repo_plugin_dir in retire:
        try:
            if old_dir.is_dir() and not old_dir.is_symlink():
                if _dirs_differ(old_dir, repo_plugin_dir):
                    bak = _unique_bak_path(old_dir)
                    old_dir.rename(bak)
                    result.preserved.append(name)
                    logger.warning(
                        "Plugin '%s' had local changes — preserved at %s "
                        "(reconcile manually, then remove)",
                        name, bak,
                    )
                else:
                    shutil.rmtree(old_dir)
                    result.migrated.append(name)
            else:
                result.migrated.append(name)
        except Exception as e:
            # Registry is already at repos/ (valid clone), so the plugin works;
            # a copy we failed to retire just lingers under plugins/ and is
            # surfaced by _cleanup_plugins_dir rather than lost.
            result.migrated.append(name)
            result.errors.append(f"{name}: copy not retired: {e}")

    if run_sync:
        sync_projects(registry)

    result.plugins_dir_cleaned = _cleanup_plugins_dir(registry)
    return result
