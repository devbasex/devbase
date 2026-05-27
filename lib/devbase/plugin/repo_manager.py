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


def _url_to_repos_dirname(url: str) -> str:
    """Convert a repo URL to a repos/ directory name using owner--repo format.

    Examples:
        https://github.com/devbasex/devbase-samples.git -> devbasex--devbase-samples
        git@github.com:user/my-repo.git -> user--my-repo
    """
    owner_repo = _derive_repo_name(url)
    return owner_repo.replace('/', '--')


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

    Raises PluginError on failure.
    """
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
    except PluginError as e:
        raise RepositoryError(str(e))

    try:
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
        # "Directory already exists".
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
        print("Available plugins:")
        for p in plugins:
            installed = registry.get(p.name)
            status = " (installed)" if installed else ""
            print(f"  - {p.name}: {p.description}{status}")


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
        print(f"{repo.name} ({repo.url}){local_info}")
        if repo.plugins:
            for p in repo.plugins:
                status = " [installed]" if p.name in installed_names else ""
                print(f"  - {p.name}: {p.description}{status}")
        else:
            print("  (no plugins)")
        print()

    print(f"Total: {len(repos)} repository(ies)")


def refresh_repository(
    registry: PluginRegistry,
    name: str,
) -> None:
    """Refresh plugin list for a registered repository (git pull + re-read registry.yml).

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

    installed = registry.list_installed()
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

    logger.info("Repository refreshed: %s", repo.name)
    if plugins:
        print("Available plugins:")
        for p in plugins:
            installed_p = registry.get(p.name)
            status = " (installed)" if installed_p else ""
            print(f"  - {p.name}: {p.description}{status}")


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
