"""認証情報ソースファイルの管理・ハッシュ比較"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml


def file_hash(path: Path) -> Optional[str]:
    """ファイルのSHA256ハッシュを返す"""
    if not path.exists():
        return None
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def dir_hash(directory: Path, filenames: List[str]) -> Optional[str]:
    """ディレクトリ内の指定ファイルの結合ハッシュを返す"""
    h = hashlib.sha256()
    found = False
    for name in sorted(filenames):
        path = directory / name
        if path.exists():
            h.update(path.read_bytes())
            found = True
    return h.hexdigest() if found else None


class SourcesManager:
    """
    .env.sources.yml の管理。
    認証情報のソースファイルとハッシュを記録し、変更検出に使う。
    """

    def __init__(self, devbase_root: Path):
        self.devbase_root = devbase_root
        self.sources_path = devbase_root / '.env.sources.yml'
        self._data: Dict = {}
        self._loaded = False

    def load(self) -> Dict:
        if self.sources_path.exists():
            with open(self.sources_path, 'r', encoding='utf-8') as f:
                self._data = yaml.safe_load(f) or {}
        else:
            self._data = {}
        self._data.setdefault('sources', {})
        self._loaded = True
        return self._data

    def save(self) -> None:
        if not self._loaded:
            self.load()
        with open(self.sources_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(self._data, f, default_flow_style=False, allow_unicode=True)

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    def get_source(self, name: str) -> Optional[Dict]:
        self._ensure_loaded()
        return self._data['sources'].get(name)

    def set_source(self, name: str, source_type: str, files: List[str],
                   env_key: str, current_hash: str, **extra) -> None:
        """ソース情報を設定・更新する"""
        self._ensure_loaded()
        self._data['sources'][name] = {
            'type': source_type,
            'files': files,
            'env_key': env_key,
            'hash': current_hash,
            'synced_at': datetime.now().isoformat(),
            **extra,
        }

    def set_gcp_source(self, profiles: Dict, active: str) -> None:
        """GCPのプロファイル情報を設定する"""
        self._ensure_loaded()
        self._data['sources']['gcp'] = {
            'type': 'named_profiles',
            'env_prefix': 'GCP_CREDENTIALS_BASE64',
            'active': active,
            'profiles': profiles,
            'synced_at': datetime.now().isoformat(),
        }

    def check_changed(self, name: str) -> Optional[bool]:
        """
        ソースファイルが変更されたか確認する。
        Returns: True=変更あり, False=変更なし, None=ソース未登録
        """
        self._ensure_loaded()
        source = self._data['sources'].get(name)
        if not source:
            return None

        old_hash = source.get('hash')
        if not old_hash:
            return None

        source_type = source.get('type', '')
        files = source.get('files', [])

        if source_type == 'tar_base64' and files:
            # ディレクトリ内の複数ファイル
            first_file = Path(files[0]).expanduser()
            directory = first_file.parent
            filenames = [Path(f).expanduser().name for f in files]
            current = dir_hash(directory, filenames)
        elif source_type == 'file_base64' and files:
            current = file_hash(Path(files[0]).expanduser())
        else:
            return None

        if current is None:
            return None

        return current != old_hash

    def check_gcp_changed(self) -> Dict[str, bool]:
        """GCPプロファイルごとの変更チェック"""
        self._ensure_loaded()
        gcp = self._data['sources'].get('gcp', {})
        profiles = gcp.get('profiles', {})
        result = {}
        for name, info in profiles.items():
            old_hash = info.get('hash')
            file_path = Path(info.get('file', '')).expanduser()
            if old_hash and file_path.exists():
                current = file_hash(file_path)
                result[name] = (current != old_hash) if current else False
            else:
                result[name] = False
        return result
