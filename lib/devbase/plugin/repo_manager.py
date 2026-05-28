"""Repository management - handles repo add/remove/list/refresh operations"""

import subprocess
import yaml
from pathlib import Path
from typing import Optional

from devbase.errors import PluginError, RepositoryError
from devbase.log import get_logger

from .models import AvailablePlugin, RegisteredRepository
from .registry import PluginRegistry
from .installer import (
    git_clone,
    parse_registry_yml,
    resolve_repo_url,
)

logger = get_logger("devbase.plugin.repo_manager")

DEFAULT_OFFICIAL_REGISTRY = "https://github.com/devbasex/devbase-samples.git"


def _get_official_registry_url() -> str:
    """Get the official registry URL from config or default"""
    config_path = Path.home() / '.devbase' / 'config.yml'
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        url = config.get('official_registry')
        if url:
            return url
    return DEFAULT_OFFICIAL_REGISTRY


def _derive_repo_name(url: str) -> str:
    """Derive a repository name from a URL using owner/repo format.

    Examples:
        https://github.com/devbasex/devbase-samples.git -> devbasex/devbase-samples
        git@github.com:user/my-repo.git -> user/my-repo
    """
    name = url.rstrip('/')
    if name.endswith('.git'):
        name = name[:-4]
    if ':' in name and '@' in name:
        return name.rsplit(':', 1)[-1]
    from urllib.parse import urlparse
    path = urlparse(name).path.strip('/')
    segments = path.split('/')
    if len(segments) >= 2:
        return f"{segments[-2]}/{segments[-1]}"
    return segments[-1] if segments else name


def _extract_host(url: str) -> str:
    """Extract the hostname from a git URL (HTTPS or SSH).

    Examples:
        https://github.com/devbasex/repo.git -> github.com
        git@gitlab.com:user/repo.git -> gitlab.com
    """
    stripped = url.rstrip('/')
    if '@' in stripped and ':' in stripped and not stripped.startswith('http'):
        # SSH form: git@host:owner/repo.git
        after_at = stripped.split('@', 1)[1]
        return after_at.split(':', 1)[0]
    from urllib.parse import urlparse
    parsed = urlparse(stripped)
    return parsed.hostname or "unknown"


def _url_to_repos_dirname(url: str) -> str:
    """Convert a repo URL to a repos/ directory name using host--owner--repo format.

    Includes the hostname so that repos from different hosts (e.g. github.com
    vs gitlab.com) with the same owner/repo do not collide.  SSH and HTTPS
    variants of the same host produce the same dirname, enabling duplicate
    detection.

    Examples:
        https://github.com/devbasex/devbase-samples.git -> github.com--devbasex--devbase-samples
        git@github.com:user/my-repo.git -> github.com--user--my-repo
        https://gitlab.com/user/my-repo.git -> gitlab.com--user--my-repo
    """
    host = _extract_host(url)
    owner_repo = _derive_repo_name(url)
    return f"{host}--{owner_repo.replace('/', '--')}"


def _is_repo_dirty(repo_dir: Path) -> tuple[bool, str]:
    """Check if a git repository has uncommitted or unpushed changes.

    Returns (is_dirty, description).
    """
    issues = []

    try:
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True, text=True, cwd=str(repo_dir),
        )
        if result.returncode == 0 and result.stdout.strip():
            issues.append("uncommitted changes")
    except subprocess.CalledProcessError:
        pass

    try:
        # Check if upstream tracking branch exists
        upstream_check = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', '@{u}'],
            capture_output=True, text=True, cwd=str(repo_dir),
        )
        if upstream_check.returncode == 0:
            # Upstream exists — check for unpushed commits
            result = subprocess.run(
                ['git', 'log', '--oneline', '@{u}..HEAD'],
                capture_output=True, text=True, cwd=str(repo_dir),
            )
            if result.returncode == 0 and result.stdout.strip():
                issues.append("unpushed commits")
        else:
            # No upstream tracking branch — local commits may be lost
            # if deleted, so treat as dirty to be safe
            issues.append("no upstream tracking branch (local-only commits may exist)")
    except subprocess.CalledProcessError:
        pass

    if issues:
        return True, ", ".join(issues)
    return False, ""


def _git_pull(repo_dir: Path) -> None:
    """Run git pull in a repository directory.

    Raises PluginError on failure.  Detects missing upstream tracking branch
    and provides an actionable error message.
    """
    # Pre-check: verify an upstream tracking branch is set.
    # Without it, `git pull` will fail with a confusing message.
    upstream = subprocess.run(
        ['git', 'rev-parse', '--abbrev-ref', '@{u}'],
        capture_output=True, text=True, cwd=str(repo_dir),
    )
    if upstream.returncode != 0:
        # Detect current branch name
        branch_result = subprocess.run(
            ['git', 'branch', '--show-current'],
            capture_output=True, text=True, cwd=str(repo_dir),
        )
        current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""

        # Detect the first available remote (usually "origin")
        remote_result = subprocess.run(
            ['git', 'remote'],
            capture_output=True, text=True, cwd=str(repo_dir),
        )
        remote_name = ""
        if remote_result.returncode == 0 and remote_result.stdout.strip():
            remotes = remote_result.stdout.strip().splitlines()
            # Prefer "origin" when multiple remotes exist
            remote_name = "origin" if "origin" in remotes else remotes[0]

        if not current_branch:
            raise PluginError(
                f"git pull failed in {repo_dir}: HEAD is detached.\n"
                "This can happen if the branch was changed manually in repos/.\n"
                f"Check out a branch first, then retry:\n"
                f"  git -C {repo_dir} checkout main"
            )
        if not remote_name:
            raise PluginError(
                f"git pull failed in {repo_dir}: no remote configured.\n"
                f"Current branch '{current_branch}' has no remote to pull from."
            )
        raise PluginError(
            f"git pull failed in {repo_dir}: no upstream tracking branch.\n"
            f"Current branch '{current_branch}' has no remote to pull from.\n"
            "This can happen if the branch was changed manually in repos/.\n"
            f"Fix with: git -C {repo_dir} branch --set-upstream-to={remote_name}/{current_branch}"
        )

    try:
        subprocess.run(
            ['git', 'pull'],
            check=True, capture_output=True, text=True, cwd=str(repo_dir),
        )
    except subprocess.CalledProcessError as e:
        raise PluginError(
            f"git pull failed in {repo_dir}: {e.stderr.strip()}"
        )


def add_repository(
    registry: PluginRegistry,
    url: str,
    name: Optional[str] = None,
) -> None:
    """Register a repository: clone to repos/ -> read registry.yml -> save to plugins.yml.

    Raises RepositoryError on failure.
    """
    repo_url = resolve_repo_url(url)

    existing = registry.get_repository_by_url(repo_url)
    if existing:
        raise RepositoryError(
            f"Repository already registered: {existing.name} ({repo_url})\n"
            "Use 'devbase plugin repo refresh' to update the plugin list."
        )

    # Detect duplicate registration via SSH/HTTPS URL variants.
    # _url_to_repos_dirname normalizes both forms to the same dirname,
    # so we compare against existing repos to prevent redundant clones.
    new_dirname = _url_to_repos_dirname(repo_url)
    for repo in registry.list_repositories():
        if _url_to_repos_dirname(repo.url) == new_dirname and repo.url != repo_url:
            logger.warning(
                "Repository '%s' (%s) appears to be the same as '%s' "
                "(URL variant: SSH vs HTTPS). Skipping duplicate registration.",
                repo.name, repo.url, repo_url,
            )
            raise RepositoryError(
                f"Repository already registered under a different URL variant: "
                f"{repo.name} ({repo.url})\n"
                "Both SSH and HTTPS URLs resolve to the same repository.\n"
                "Use 'devbase plugin repo refresh' to update the existing entry."
            )

    repos_dir = registry.get_repos_dir()
    repos_dir.mkdir(exist_ok=True)

    dir_name = _url_to_repos_dirname(repo_url)
    clone_dir = repos_dir / dir_name

    if clone_dir.exists():
        raise RepositoryError(
            f"Directory already exists: {clone_dir}\n"
            "The repository may have been previously added. "
            "Remove the directory manually or use a different --name."
        )

    try:
        git_clone(repo_url, clone_dir, shallow=False)

        reg_info = parse_registry_yml(clone_dir)
        if not reg_info:
            raise RepositoryError(f"No registry.yml found in {repo_url}")

        derived_name = _derive_repo_name(repo_url)
        candidate_name = name or reg_info.name or derived_name

        if registry.get_repository(candidate_name) and candidate_name != derived_name:
            candidate_name = derived_name

        repo_name = candidate_name

        if registry.get_repository(repo_name):
            raise RepositoryError(
                f"Repository name '{repo_name}' already exists.\n"
                "Use --name to specify a different name."
            )
    except Exception:
        # Clean up the cloned directory so a retry won't fail with
        # "Directory already exists".  This also handles partial clones
        # (e.g. disk full, network interruption mid-clone).
        import shutil as _shutil
        if clone_dir.is_dir():
            _shutil.rmtree(clone_dir)
        raise

    plugins = [
        AvailablePlugin(
            name=e.name,
            description=e.description,
            path=e.path,
        )
        for e in reg_info.plugins
    ]

    local_path = f"repos/{dir_name}"

    repo = RegisteredRepository(
        name=repo_name,
        url=repo_url,
        added_at=registry.now_iso(),
        local_path=local_path,
        plugins=plugins,
    )
    registry.add_repository(repo)

    logger.info("Repository registered: %s (%s)", repo_name, repo_url)
    if plugins:
        logger.info("Available plugins:")
        for p in plugins:
            installed = registry.get(p.name)
            status = " (installed)" if installed else ""
            logger.info("  - %s: %s%s", p.name, p.description, status)


def remove_repository(
    registry: PluginRegistry,
    name: str,
    force: bool = False,
) -> None:
    """Remove a repository registration, uninstall plugins, and delete repos/ clone.

    Raises RepositoryError if not found or if repos/ is dirty (without --force).
    """
    import shutil
    from .installer import uninstall_plugin

    repo = registry.get_repository(name)
    if not repo:
        raise RepositoryError(f"Repository '{name}' not found.")

    repos_dir = registry.get_repos_dir()
    repo_clone_dir = registry.devbase_root / repo.local_path if repo.local_path else None

    if repo_clone_dir and repo_clone_dir.is_dir() and not force:
        is_dirty, description = _is_repo_dirty(repo_clone_dir)
        if is_dirty:
            raise RepositoryError(
                f"Repository '{name}' has {description} in {repo_clone_dir}.\n"
                "Commit/push your changes first, or use --force to delete anyway."
            )

    installed = registry.list_installed()
    plugins_to_remove = [p for p in installed if p.source == repo.url]
    for plugin in plugins_to_remove:
        logger.info("Uninstalling plugin '%s' from repository '%s'...", plugin.name, name)
        uninstall_plugin(registry, plugin.name)

    registry.remove_repository(name)

    if repo_clone_dir and repo_clone_dir.is_dir():
        shutil.rmtree(repo_clone_dir)
        logger.info("Removed clone directory: %s", repo_clone_dir)

    logger.info("Repository removed: %s", name)


def show_repositories(registry: PluginRegistry) -> None:
    """Display registered repositories and their available plugins."""
    repos = registry.list_repositories()
    if not repos:
        logger.info("No repositories registered.")
        logger.info("Use 'devbase plugin repo add <url>' to register a repository.")
        return

    installed_names = {p.name for p in registry.list_installed()}

    for repo in repos:
        local_info = f" [{repo.local_path}]" if repo.local_path else ""
        logger.info("%s (%s)%s", repo.name, repo.url, local_info)
        if repo.plugins:
            for p in repo.plugins:
                status = " [installed]" if p.name in installed_names else ""
                logger.info("  - %s: %s%s", p.name, p.description, status)
        else:
            logger.info("  (no plugins)")
        logger.info("")

    logger.info("Total: %d repository(ies)", len(repos))


def refresh_repository(
    registry: PluginRegistry,
    name: str,
    *,
    sync: bool = True,
) -> None:
    """Refresh plugin list for a registered repository (git pull + re-read registry.yml).

    Args:
        sync: If True (default), call sync_projects after updating metadata.
            Set to False when refreshing multiple repositories in a batch to
            avoid redundant sync calls — the caller should sync once at the end.

    Raises RepositoryError if not found.
    """
    repo = registry.get_repository(name)
    if not repo:
        raise RepositoryError(f"Repository '{name}' not found.")

    if not repo.local_path:
        raise RepositoryError(
            f"Repository '{name}' has no local clone path. "
            "Remove and re-add the repository to create a persistent clone."
        )

    clone_dir = registry.devbase_root / repo.local_path
    if not clone_dir.is_dir():
        raise RepositoryError(
            f"Clone directory not found: {clone_dir}\n"
            "Remove and re-add the repository to re-clone."
        )

    # Snapshot installed plugin project names BEFORE git pull so that
    # migration can still detect where old projects moved even after
    # the working tree is updated by pull.
    from .updater import _snapshot_plugin_projects
    installed = registry.list_installed()
    repo_installed = [p for p in installed if p.source == repo.url and not p.linked]
    pre_pull_projects = _snapshot_plugin_projects(registry, repo_installed)

    try:
        _git_pull(clone_dir)
    except PluginError as e:
        raise RepositoryError(str(e))

    try:
        reg_info = parse_registry_yml(clone_dir)
    except PluginError as e:
        raise RepositoryError(str(e))
    if not reg_info:
        raise RepositoryError(f"No registry.yml found in {repo.url}")

    old_plugin_names = {p.name for p in repo.plugins}

    plugins = [
        AvailablePlugin(
            name=e.name,
            description=e.description,
            path=e.path,
        )
        for e in reg_info.plugins
    ]
    new_plugin_names = {p.name for p in plugins}

    installed_names = {p.name for p in installed}
    removed_installed = (old_plugin_names - new_plugin_names) & installed_names
    if removed_installed:
        for pname in sorted(removed_installed):
            logger.warning(
                "Installed plugin '%s' no longer exists in registry.yml of '%s'",
                pname, name,
            )

    updated_repo = RegisteredRepository(
        name=repo.name,
        url=repo.url,
        added_at=repo.added_at,
        local_path=repo.local_path,
        plugins=plugins,
    )
    registry.add_repository(updated_repo)

    # After pull, update installed plugin metadata (version, path) and
    # re-sync project symlinks so that registry.yml changes (e.g. renamed
    # paths) are reflected in the installed state.
    from .updater import _update_repo_plugins
    from .syncer import sync_projects
    repo_errors = _update_repo_plugins(
        registry, repo.url, clone_dir,
        pre_pull_projects=pre_pull_projects,
    )
    if repo_errors:
        # A failed removal-migration leaves the stale plugins/ entry in
        # plugins.yml; reporting "refreshed" here would mask that broken
        # install state. Surface it as a hard error (mirrors update_plugin,
        # which raises on _update_repo_plugins errors) instead of warning and
        # exiting 0.
        for err in repo_errors:
            logger.error("  %s", err)
        raise RepositoryError(
            f"Repository '{repo.name}' refresh left plugins in a broken state:\n"
            + "\n".join(f"  - {e}" for e in repo_errors)
        )
    if sync:
        sync_projects(registry)

    logger.info("Repository refreshed: %s", repo.name)
    if plugins:
        logger.info("Available plugins:")
        for p in plugins:
            installed_p = registry.get(p.name)
            status = " (installed)" if installed_p else ""
            logger.info("  - %s: %s%s", p.name, p.description, status)


def add_official_repository(registry: PluginRegistry) -> bool:
    """Register the official repository if not already registered.

    Called during 'devbase init'. Network failure is non-fatal.
    Returns True on success, False on failure (non-fatal).
    """
    official_url = _get_official_registry_url()

    if registry.get_repository_by_url(official_url):
        return True

    try:
        add_repository(registry, official_url)
        return True
    except Exception as e:
        logger.warning("Could not register official repository: %s", e)
        return False
