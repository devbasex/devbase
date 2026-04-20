"""Environment variable file store"""

import os
import shutil
from pathlib import Path
from typing import Optional, Dict, Union

from devbase.log import get_logger

logger = get_logger(__name__)


def safe_input(prompt: str, default: str = "") -> str:
    """EOFを安全に処理するinput関数"""
    try:
        value = input(prompt).strip()
        return value if value else default
    except EOFError:
        return default


def collect_key(env_file, key, *, auto_value=None, prompt=None, mask_after=10):
    """collectors間で共通の「既存チェック→自動取得→手動入力→設定」パターン

    Args:
        env_file: EnvFileインスタンス
        key: 環境変数キー
        auto_value: 自動取得値（Noneでなければ自動設定）
        prompt: 手動入力用プロンプト（Noneでデフォルト）
        mask_after: 表示時のマスク文字数（0/Falseで全表示）

    Returns:
        True: 値が設定済み or 新規設定された
        False: スキップされた
    """
    existing = env_file.get(key)
    if existing:
        display = f"{existing[:mask_after]}..." if mask_after and len(existing) > mask_after else existing
        logger.info("%s: 設定済み (%s)", key, display)
        return True
    if auto_value is not None:
        env_file.set(key, auto_value)
        logger.info("%s: 自動取得完了", key)
        return True
    value = safe_input(prompt or f"{key} (空でスキップ): ")
    if value:
        env_file.set(key, value)
        return True
    return False


class EnvFile:
    """
    .envファイルの読み書き・バックアップ・バリデーションを管理する。
    """

    def __init__(self, file_path: Union[str, Path]):
        self.file_path = Path(file_path)
        self._data: Dict[str, str] = {}
        self._loaded = False

    def load(self) -> Dict[str, str]:
        """ファイルを読み込みkey=valueをパースする"""
        self._data = {}

        if not self.file_path.exists():
            self._loaded = True
            return self._data

        with open(self.file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()

                if not line or line.startswith('#'):
                    continue

                if '=' not in line:
                    continue

                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip()

                if value and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]

                self._data[key] = value

        self._loaded = True
        return self._data

    def save(self) -> None:
        """現在のデータを.envファイルに保存する"""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.file_path, 'w', encoding='utf-8') as f:
            for key, value in sorted(self._data.items()):
                if '\n' in value or any(c in value for c in (' ', '"', "'", '$', '`', '\\', '<', '>', '|', '&', ';', '(', ')', '#')):
                    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
                    f.write(f'{key}="{value}"\n')
                else:
                    f.write(f'{key}={value}\n')

        os.chmod(self.file_path, 0o600)

    def backup(self) -> Optional[Path]:
        """既存ファイルのバックアップを作成する"""
        if not self.file_path.exists():
            return None

        backup_path = Path(str(self.file_path) + '.backup')
        shutil.copy2(self.file_path, backup_path)
        return backup_path

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if not self._loaded:
            self.load()
        return self._data.get(key, default)

    def set(self, key: str, value: str) -> None:
        if not self._loaded:
            self.load()
        self._data[key] = value

    def exists(self, key: str) -> bool:
        if not self._loaded:
            self.load()
        return key in self._data

    def get_all(self) -> Dict[str, str]:
        if not self._loaded:
            self.load()
        return self._data.copy()

    def delete(self, key: str) -> bool:
        if not self._loaded:
            self.load()
        if key in self._data:
            del self._data[key]
            return True
        return False

    def count(self) -> int:
        """変数の数を返す"""
        if not self._loaded:
            self.load()
        return len(self._data)

    @property
    def path(self) -> Path:
        return self.file_path

    def __repr__(self) -> str:
        return f"EnvFile('{self.file_path}')"
