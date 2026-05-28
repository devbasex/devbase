"""Plugin migrator - migrates legacy plugins/ copy installs to repos/ clones"""

import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from devbase.errors import PluginError
from devbase.log import get_logger

from .models import AvailablePlugin, InstalledPlugin, RegisteredRepository
from .registry import PluginRegistry

if TYPE_CHECKING:
    from .models import RegistryInfo

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


def _build_persisted_repo(
    repo: RegisteredRepository,
    dir_name: str,
    reg_info: Optional["RegistryInfo"],
) -> RegisteredRepository:
    """Build a repo row with local_path = repos/<dir_name> + a refreshed plugin
    list, WITHOUT saving.

    Used after a fresh clone *and* when reusing a healthy clone left by an
    earlier run (a pre-persistent-clone registration with no local_path), so the
    plugins.yml entry is repaired identically in both cases and future runs take
    the local_path fast path.

    `reg_info` is the clone's already-parsed registry.yml (the caller parses it
    exactly once and threads it through here and on to migrate()), so this no
    longer re-reads/re-parses the file itself.  When it is None the repo's prior
    plugin list is kept.

    The caller is responsible for persisting the returned row: during migration
    every repo update is accumulated and flushed in a single plugins.yml save
    (see migrate()), so this no longer writes per repo.
    """
    plugins = [
        AvailablePlugin(name=e.name, description=e.description, path=e.path)
        for e in reg_info.plugins
    ] if reg_info else list(repo.plugins)
    return RegisteredRepository(
        name=repo.name, url=repo.url, added_at=repo.added_at,
        local_path=f"repos/{dir_name}", plugins=plugins,
    )


def _ensure_repo_cloned(
    registry: PluginRegistry,
    repo: RegisteredRepository,
    pending_repos: list[RegisteredRepository],
) -> tuple[Path, RegisteredRepository, Optional["RegistryInfo"]]:
    """Return the repos/ clone dir for a repo, cloning it if necessary.

    If the repo was registered before persistent-clone support (no local_path),
    the clone dir is missing, or the existing clone is broken (missing .git /
    registry.yml), perform a full clone and stage local_path + a refreshed
    plugin list for persistence.  A healthy repos/<derived> clone left by an
    earlier run is reused (and its missing local_path staged) rather than
    re-cloned or protected.

    Returns ``(clone_dir, repo, reg_info)`` where ``reg_info`` is the clone's
    parsed registry.yml whenever this function already had to parse it (the
    derived-reuse and fresh-clone paths), so migrate() can reuse it instead of
    re-reading the same file.  It is None only on the local_path fast path,
    where no parse happened; migrate() parses lazily there.

    Any repo row that needs (re)persisting is appended to `pending_repos`
    instead of being saved here; migrate() flushes them in a single
    plugins.yml save before any destructive cleanup, so the registry still
    durably points at the clone before old copies are retired, but the save
    count stays O(1) rather than one save per cloned repo.
    """
    from .installer import git_clone, parse_registry_yml
    from .repo_manager import _url_to_repos_dirname

    if repo.local_path:
        clone_dir = registry.devbase_root / repo.local_path
        if _clone_is_healthy(clone_dir):
            return clone_dir, repo, None
        _reclaim_or_protect_existing(clone_dir)

    dir_name = _url_to_repos_dirname(repo.url)
    repos_dir = registry.get_repos_dir()
    repos_dir.mkdir(exist_ok=True)
    clone_dir = repos_dir / dir_name

    # A pre-persistent-clone registration (no local_path) may already have a
    # healthy repos/<derived> clone from an earlier run; reuse it instead of
    # protecting (raising) on its .git, just like the local_path branch above —
    # only stage the missing local_path so future runs take the fast path.
    if _clone_is_healthy(clone_dir):
        reg_info = parse_registry_yml(clone_dir)
        updated = _build_persisted_repo(repo, dir_name, reg_info)
        pending_repos.append(updated)
        return clone_dir, updated, reg_info

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

    updated = _build_persisted_repo(repo, dir_name, reg_info)
    pending_repos.append(updated)
    logger.info("Repository '%s' cloned to %s", repo.name, updated.local_path)
    return clone_dir, updated, reg_info


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
    #     cloned-repo rows in `pending_repos`, the repos/ path rewrites in
    #     `pending`, and the retire actions in `retire`.  Cloning stages the
    #     repo row in `pending_repos` rather than saving per clone, so the save
    #     count stays O(1) regardless of how many repos are cloned.
    #   Persist: write every repo row + path rewrite in a single plugins.yml
    #     save (save_migration), flushing the staged clones BEFORE any cleanup.
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
    pending_repos: list[RegisteredRepository] = []  # cloned-repo rows to persist
    retire: list[tuple[str, Path, Path]] = []  # (plugin_name, old_dir, repo_dir)

    # Index every registered repo by URL once.  registry.get_repository_by_url
    # re-reads (and re-parses) plugins.yml on every call, so calling it per
    # legacy plugin is O(N) disk reads over the loop; a single up-front read
    # collapses that to O(1).  Last-wins on duplicate URLs mirrors the name-keyed
    # upsert model used elsewhere in the registry.
    repos_by_url = {
        repo.url: repo for repo in registry.list_repositories() if repo.url
    }

    # Cache the lazily-parsed registry.yml per repo URL. On the healthy
    # local_path fast path _ensure_repo_cloned returns reg_info=None, so a repo
    # with multiple legacy plugins would otherwise re-parse the same registry.yml
    # once per plugin here; the cache collapses that to one parse per repo.
    reg_info_by_url: dict[str, Optional["RegistryInfo"]] = {}

    for plugin in legacy:
        try:
            repo = repos_by_url.get(plugin.source) if plugin.source else None
            if not repo:
                result.skipped.append(plugin.name)
                result.errors.append(
                    f"{plugin.name}: source repository not registered "
                    f"({plugin.source or 'no source URL'})"
                )
                continue

            clone_dir, repo, reg_info = _ensure_repo_cloned(
                registry, repo, pending_repos,
            )

            # _ensure_repo_cloned may hand back an updated repo row (local_path
            # now staged after a clone/reuse). Write it back so subsequent
            # legacy plugins of the same repo take the local_path fast path
            # instead of re-entering the clone-reuse branch, which would
            # re-parse registry.yml and append a duplicate pending_repos row.
            repos_by_url[repo.url] = repo
            # Cache the parse from the clone/reuse path too, so the fast path
            # for sibling plugins reuses it instead of re-reading registry.yml.
            if reg_info is not None:
                reg_info_by_url[repo.url] = reg_info

            # _ensure_repo_cloned already parsed registry.yml on the clone/reuse
            # paths; only the healthy local_path fast path returns None. Parse
            # lazily there, but reuse a cached parse for subsequent plugins of
            # the same repo instead of re-reading the same file each iteration.
            if reg_info is None:
                if repo.url not in reg_info_by_url:
                    reg_info_by_url[repo.url] = parse_registry_yml(clone_dir)
                reg_info = reg_info_by_url[repo.url]
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

    # Persist every staged cloned-repo row AND validated path rewrite in a
    # single save BEFORE retiring any copy.  This both (a) keeps the registry
    # durably pointing at the repos/ clones before destructive cleanup — the
    # two-phase atomicity invariant — and (b) collapses what used to be one save
    # per cloned repo plus the path-rewrite save into a single O(1) write.  A
    # failure here aborts with the copies untouched (recoverable).
    registry.save_migration(pending_repos, pending)

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
