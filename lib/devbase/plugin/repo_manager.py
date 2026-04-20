"""Repository management - handles repo add/remove/list/refresh operations"""

import tempfile
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


def add_repository(
    registry: PluginRegistry,
    url: str,
    name: Optional[str] = None,
) -> None:
    """Register a repository: clone -> read registry.yml -> save to plugins.yml.

    Raises RepositoryError on failure.
    """
    repo_url = resolve_repo_url(url)

    # Check if already registered by URL
    existing = registry.get_repository_by_url(repo_url)
    if existing:
        raise RepositoryError(
            f"Repository already registered: {existing.name} ({repo_url})\n"
            "Use 'devbase plugin repo refresh' to update the plugin list."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        clone_dir = Path(tmpdir) / 'repo'
        try:
            git_clone(repo_url, clone_dir)
        except PluginError as e:
            raise RepositoryError(str(e))

        try:
            reg_info = parse_registry_yml(clone_dir)
        except PluginError as e:
            raise RepositoryError(str(e))
        if not reg_info:
            raise RepositoryError(f"No registry.yml found in {repo_url}")

        # Determine repo name: explicit --name > registry.yml name > owner/repo from URL
        derived_name = _derive_repo_name(repo_url)
        candidate_name = name or reg_info.name or derived_name

        # On name collision, fall back to owner/repo format if not already used
        if registry.get_repository(candidate_name) and candidate_name != derived_name:
            candidate_name = derived_name

        repo_name = candidate_name

        # Check name collision
        if registry.get_repository(repo_name):
            raise RepositoryError(
                f"Repository name '{repo_name}' already exists.\n"
                "Use --name to specify a different name."
            )

        plugins = [
            AvailablePlugin(
                name=e.name,
                description=e.description,
                path=e.path,
            )
            for e in reg_info.plugins
        ]

        repo = RegisteredRepository(
            name=repo_name,
            url=repo_url,
            added_at=registry.now_iso(),
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


def remove_repository(registry: PluginRegistry, name: str) -> None:
    """Remove a repository registration and uninstall all plugins from it.

    Raises RepositoryError if not found.
    """
    from .installer import uninstall_plugin

    repo = registry.get_repository(name)
    if not repo:
        raise RepositoryError(f"Repository '{name}' not found.")

    # Uninstall all plugins installed from this repository
    installed = registry.list_installed()
    plugins_to_remove = [p for p in installed if p.source == repo.url]
    for plugin in plugins_to_remove:
        logger.info("Uninstalling plugin '%s' from repository '%s'...", plugin.name, name)
        uninstall_plugin(registry, plugin.name)

    registry.remove_repository(name)
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
        print(f"{repo.name} ({repo.url})")
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
    """Refresh plugin list for a registered repository (re-clone -> update cache).

    Raises RepositoryError if not found.
    """
    repo = registry.get_repository(name)
    if not repo:
        raise RepositoryError(f"Repository '{name}' not found.")

    with tempfile.TemporaryDirectory() as tmpdir:
        clone_dir = Path(tmpdir) / 'repo'
        try:
            git_clone(repo.url, clone_dir)
        except PluginError as e:
            raise RepositoryError(str(e))

        try:
            reg_info = parse_registry_yml(clone_dir)
        except PluginError as e:
            raise RepositoryError(str(e))
        if not reg_info:
            raise RepositoryError(f"No registry.yml found in {repo.url}")

        plugins = [
            AvailablePlugin(
                name=e.name,
                description=e.description,
                path=e.path,
            )
            for e in reg_info.plugins
        ]

        updated_repo = RegisteredRepository(
            name=repo.name,
            url=repo.url,
            added_at=repo.added_at,
            plugins=plugins,
        )
        registry.add_repository(updated_repo)

        logger.info("Repository refreshed: %s", repo.name)
        if plugins:
            print("Available plugins:")
            for p in plugins:
                installed = registry.get(p.name)
                status = " (installed)" if installed else ""
                print(f"  - {p.name}: {p.description}{status}")


def add_official_repository(registry: PluginRegistry) -> bool:
    """Register the official repository if not already registered.

    Called during 'devbase init'. Network failure is non-fatal.
    Returns True on success, False on failure (non-fatal).
    """
    official_url = _get_official_registry_url()

    # Already registered?
    if registry.get_repository_by_url(official_url):
        return True

    try:
        add_repository(registry, official_url)
        return True
    except Exception as e:
        logger.warning("Could not register official repository: %s", e)
        return False


def _derive_repo_name(url: str) -> str:
    """Derive a repository name from a URL using owner/repo format.

    Examples:
        https://github.com/devbasex/devbase-samples.git -> devbasex/devbase-samples
        git@github.com:user/my-repo.git -> user/my-repo
    """
    name = url.rstrip('/')
    if name.endswith('.git'):
        name = name[:-4]
    # Handle git@ SSH URLs (git@github.com:owner/repo)
    if ':' in name and '@' in name:
        return name.rsplit(':', 1)[-1]
    # HTTPS URLs: extract owner/repo from URL path
    from urllib.parse import urlparse
    path = urlparse(name).path.strip('/')
    segments = path.split('/')
    if len(segments) >= 2:
        return f"{segments[-2]}/{segments[-1]}"
    return segments[-1] if segments else name
