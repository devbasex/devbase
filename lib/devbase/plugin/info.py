"""Plugin info - handles plugin information display"""

from devbase.errors import PluginError

from .registry import PluginRegistry
from .syncer import load_plugin_info


def show_plugin_info(registry: PluginRegistry, name: str) -> None:
    """Display detailed information about an installed plugin.

    Raises PluginError if not installed.
    """
    plugin = registry.get(name)
    if not plugin:
        raise PluginError(f"Plugin '{name}' is not installed")

    plugin_dir = registry.devbase_root / plugin.path
    info = load_plugin_info(plugin_dir) if plugin_dir.is_dir() else None

    print(f"Plugin: {plugin.name}")
    print(f"  Version:      {plugin.version}")
    print(f"  Source:        {plugin.source}")
    print(f"  Installed at:  {plugin.installed_at}")
    print(f"  Linked:        {'Yes' if plugin.linked else 'No'}")
    print(f"  Path:          {plugin.path}")

    if info and info.description:
        print(f"  Description:   {info.description}")
    if info and info.priority:
        print(f"  Priority:      {info.priority}")

    # List projects
    projects_dir = plugin_dir / 'projects' if plugin_dir.is_dir() else None
    if projects_dir and projects_dir.is_dir():
        projects = sorted([d.name for d in projects_dir.iterdir() if d.is_dir()])
        print(f"  Projects ({len(projects)}):")
        for proj in projects:
            print(f"    - {proj}")


def show_available_plugins(registry: PluginRegistry) -> None:
    """Display available plugins from registered repositories (no clone needed).

    Raises PluginError if no repositories registered.
    """
    repos = registry.list_repositories()

    if not repos:
        raise PluginError(
            "No repositories registered.\n"
            "Use 'devbase plugin repo add <url>' to register a repository first."
        )

    installed_names = {p.name for p in registry.list_installed()}

    print(f"  {'NAME':<20} {'DESCRIPTION':<40} {'REPOSITORY':<20} {'INSTALLED'}")
    print(f"  {'-'*20} {'-'*40} {'-'*20} {'-'*9}")
    for repo in repos:
        for plugin in repo.plugins:
            installed = "Yes" if plugin.name in installed_names else "No"
            print(f"  {plugin.name:<20} {plugin.description:<40} {repo.name:<20} {installed}")

    print(f"\nTotal: {sum(len(r.plugins) for r in repos)} plugin(s) from {len(repos)} repository(ies)")
