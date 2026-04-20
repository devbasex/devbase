"""Data models for plugin management"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class PluginProject:
    """A project within a plugin"""
    name: str
    path: Path


@dataclass
class PluginSource:
    """Source information for a plugin"""
    repo: str                    # e.g. "devbasex/devbase-samples" or local path
    plugin_name: str             # plugin name within the repo
    ref: Optional[str] = None   # branch/tag/commit (None = default branch)
    linked: bool = False         # True for local --link installs

    @property
    def display(self) -> str:
        if self.linked:
            return f"{self.repo} (linked)"
        return self.repo

    @classmethod
    def parse(cls, source_str: str, link: bool = False) -> 'PluginSource':
        """Parse a source string into PluginSource.

        Formats:
          - "plugin-name"                   -> name-only (look up in registered repos)
          - "user/repo:plugin-name"         -> GitHub repo with specific plugin
          - "user/repo:plugin-name@ref"     -> with branch/tag
          - "https://...git:plugin-name"    -> full URL
          - "/path/to/local:plugin-name"    -> local path (with --link)
        """
        ref = None
        plugin_name = ""
        repo = ""

        # Check for @ref suffix
        if '@' in source_str and not source_str.startswith(('http', 'git@')):
            source_str, ref = source_str.rsplit('@', 1)

        # Check for :plugin-name
        if ':' in source_str:
            # Handle URL with : in protocol
            if source_str.startswith('http://') or source_str.startswith('https://'):
                # URL:plugin-name - split on last :
                parts = source_str.rsplit(':', 1)
                if len(parts) == 2 and '/' not in parts[1]:
                    repo, plugin_name = parts
                else:
                    repo = source_str
            else:
                repo, plugin_name = source_str.rsplit(':', 1)
        else:
            # No colon - either name-only or repo --all
            if '/' in source_str:
                repo = source_str
            else:
                plugin_name = source_str
                repo = ""

        return cls(repo=repo, plugin_name=plugin_name, ref=ref, linked=link)


@dataclass
class PluginInfo:
    """Metadata from plugin.yml"""
    name: str
    version: str = "0.1.0"
    description: str = ""
    priority: int = 0
    requires_devbase: Optional[str] = None


@dataclass
class RegistryEntry:
    """An entry in registry.yml (repo-level)"""
    name: str
    path: str
    description: str = ""


@dataclass
class RegistryInfo:
    """Metadata from registry.yml (repo-level)"""
    name: str
    description: str = ""
    maintainer: str = ""
    official: bool = False
    plugins: list[RegistryEntry] = field(default_factory=list)


@dataclass
class InstalledPlugin:
    """An installed plugin entry in plugins.yml"""
    name: str
    version: str
    source: str
    installed_at: str
    path: str
    linked: bool = False

    @property
    def installed_path(self) -> Path:
        return Path(self.path)

    @property
    def installed_datetime(self) -> datetime:
        return datetime.fromisoformat(self.installed_at)

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'version': self.version,
            'source': self.source,
            'installed_at': self.installed_at,
            'path': self.path,
            'linked': self.linked,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'InstalledPlugin':
        return cls(
            name=data['name'],
            version=data.get('version', '0.1.0'),
            source=data.get('source', ''),
            installed_at=data.get('installed_at', ''),
            path=data.get('path', ''),
            linked=data.get('linked', False),
        )


@dataclass
class AvailablePlugin:
    """A plugin available in a registered repository"""
    name: str
    description: str = ""
    path: str = ""


@dataclass
class RegisteredRepository:
    """A registered plugin repository in plugins.yml"""
    name: str
    url: str
    added_at: str = ""
    plugins: list[AvailablePlugin] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'url': self.url,
            'added_at': self.added_at,
            'plugins': [
                {'name': p.name, 'description': p.description, 'path': p.path}
                for p in self.plugins
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'RegisteredRepository':
        plugins = [
            AvailablePlugin(
                name=p.get('name', ''),
                description=p.get('description', ''),
                path=p.get('path', ''),
            )
            for p in data.get('plugins', [])
        ]
        return cls(
            name=data.get('name', ''),
            url=data.get('url', ''),
            added_at=data.get('added_at', ''),
            plugins=plugins,
        )

    def find_plugin(self, plugin_name: str) -> Optional[AvailablePlugin]:
        """Find a plugin by name in this repository"""
        for p in self.plugins:
            if p.name == plugin_name:
                return p
        return None
