"""Plugin registry management - handles plugins.yml"""

import os
import tempfile
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from devbase.log import get_logger

from .models import InstalledPlugin, RegisteredRepository, AvailablePlugin

logger = get_logger("devbase.plugin.registry")


class PluginRegistry:
    """Manages the plugins.yml file"""

    def __init__(self, devbase_root: Path):
        self.devbase_root = devbase_root
        self.registry_file = devbase_root / 'plugins.yml'

    def _load(self) -> dict:
        """Load plugins.yml"""
        if not self.registry_file.exists():
            return {'repositories': [], 'installed_plugins': []}
        try:
            with open(self.registry_file) as f:
                data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {'repositories': [], 'installed_plugins': []}
        except yaml.YAMLError as e:
            logger.warning("Failed to parse %s: %s", self.registry_file, e)
            return {'repositories': [], 'installed_plugins': []}

    def _save(self, data: dict) -> None:
        """Save plugins.yml (atomic write via temp file + rename)"""
        dir_path = self.registry_file.parent
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=dir_path, suffix='.tmp', prefix='.plugins-'
            )
            with os.fdopen(fd, 'w') as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True,
                          sort_keys=False)
            os.replace(tmp_path, self.registry_file)
        except OSError as e:
            # 一時ファイルが残っていれば削除
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            logger.error("plugins.ymlの保存に失敗しました: %s", e)
            raise

    def list_installed(self) -> list[InstalledPlugin]:
        """List all installed plugins"""
        data = self._load()
        return [InstalledPlugin.from_dict(p) for p in data['installed_plugins']]

    def get(self, name: str) -> Optional[InstalledPlugin]:
        """Get an installed plugin by name"""
        for p in self.list_installed():
            if p.name == name:
                return p
        return None

    def add(self, plugin: InstalledPlugin) -> None:
        """Add a plugin to the registry"""
        data = self._load()
        data['installed_plugins'] = [
            p for p in data['installed_plugins'] if p['name'] != plugin.name
        ]
        data['installed_plugins'].append(plugin.to_dict())
        self._save(data)

    def remove(self, name: str) -> bool:
        """Remove a plugin from the registry. Returns True if found."""
        data = self._load()
        original_len = len(data['installed_plugins'])
        data['installed_plugins'] = [
            p for p in data['installed_plugins'] if p['name'] != name
        ]
        if len(data['installed_plugins']) < original_len:
            self._save(data)
            return True
        return False

    def get_plugins_dir(self) -> Path:
        """Get the plugins directory path"""
        return self.devbase_root / 'plugins'

    def get_projects_dir(self) -> Path:
        """Get the projects directory path"""
        return self.devbase_root / 'projects'

    @staticmethod
    def now_iso() -> str:
        """Current time in ISO format"""
        return datetime.now(timezone.utc).isoformat()

    # ── Repository management ───────────────────────────────────

    def list_repositories(self) -> list[RegisteredRepository]:
        """List all registered repositories"""
        data = self._load()
        return [RegisteredRepository.from_dict(r) for r in data['repositories']]

    def get_repository(self, name: str) -> Optional[RegisteredRepository]:
        """Get a registered repository by name"""
        for repo in self.list_repositories():
            if repo.name == name:
                return repo
        return None

    def get_repository_by_url(self, url: str) -> Optional[RegisteredRepository]:
        """Get a registered repository by URL"""
        for repo in self.list_repositories():
            if repo.url == url:
                return repo
        return None

    def add_repository(self, repo: RegisteredRepository) -> None:
        """Add or update a repository in the registry"""
        data = self._load()
        data['repositories'] = [
            r for r in data['repositories'] if r['name'] != repo.name
        ]
        data['repositories'].append(repo.to_dict())
        self._save(data)

    def remove_repository(self, name: str) -> bool:
        """Remove a repository from the registry. Returns True if found."""
        data = self._load()
        original_len = len(data['repositories'])
        data['repositories'] = [
            r for r in data['repositories'] if r['name'] != name
        ]
        if len(data['repositories']) < original_len:
            self._save(data)
            return True
        return False

    def find_plugin_in_repos(
        self, plugin_name: str,
    ) -> Optional[tuple[RegisteredRepository, AvailablePlugin]]:
        """Find a plugin across all registered repositories"""
        for repo in self.list_repositories():
            plugin = repo.find_plugin(plugin_name)
            if plugin:
                return (repo, plugin)
        return None
