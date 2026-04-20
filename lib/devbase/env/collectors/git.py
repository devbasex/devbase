"""Git認証情報コレクター"""

import base64
import re
import subprocess
from pathlib import Path
from typing import Optional

from devbase.log import get_logger
from devbase.env import keys
from devbase.env.store import EnvFile, safe_input, collect_key
from devbase.env.collector import Collector

logger = get_logger(__name__)


def _get_git_config(key: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ['git', 'config', '--global', key],
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass
    return None


def _extract_github_token() -> Optional[str]:
    credentials_path = Path.home() / '.git-credentials'
    if not credentials_path.exists():
        return None
    try:
        with open(credentials_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or 'github.com' not in line:
                    continue
                match = re.search(r'https://(?:[^:]+:)?([^@]+)@github\.com', line)
                if match:
                    return match.group(1)
    except Exception as e:
        logger.warning(".git-credentialsの読み込みに失敗: %s", e)
    return None


def _read_git_credentials_base64() -> Optional[str]:
    credentials_path = Path.home() / '.git-credentials'
    if not credentials_path.exists():
        return None
    try:
        content = credentials_path.read_text(encoding='utf-8')
        return base64.b64encode(content.encode('utf-8')).decode('ascii')
    except Exception as e:
        logger.warning(".git-credentialsの読み込みに失敗: %s", e)
        return None


def collect_git_credentials(env_file: EnvFile) -> None:
    """Git認証情報を対話的に収集する"""
    print("\n=== Git認証情報 ===")

    collect_key(env_file, keys.GIT_USER_NAME, auto_value=_get_git_config('user.name'), mask_after=0)
    collect_key(env_file, keys.GIT_USER_EMAIL, auto_value=_get_git_config('user.email'), mask_after=0)
    collect_key(env_file, keys.GIT_CREDENTIAL_HELPER, auto_value=_get_git_config('credential.helper'), mask_after=0)
    collect_key(env_file, keys.GIT_CREDENTIALS_BASE64, auto_value=_read_git_credentials_base64())

    # GITHUB_PERSONAL_ACCESS_TOKEN / GH_TOKEN: 2キー同時設定のため個別処理
    existing = env_file.get(keys.GITHUB_PERSONAL_ACCESS_TOKEN)
    if existing:
        logger.info("%s: 設定済み (%s...)", keys.GITHUB_PERSONAL_ACCESS_TOKEN, existing[:4])
        logger.info("%s: 設定済み", keys.GH_TOKEN)
    else:
        github_token = _extract_github_token()
        if github_token:
            env_file.set(keys.GITHUB_PERSONAL_ACCESS_TOKEN, github_token)
            env_file.set(keys.GH_TOKEN, github_token)
            logger.info("%s: 自動取得完了 (%s...)", keys.GITHUB_PERSONAL_ACCESS_TOKEN, github_token[:4])
            logger.info("%s: 自動取得完了", keys.GH_TOKEN)
        else:
            github_token = safe_input(f"{keys.GITHUB_PERSONAL_ACCESS_TOKEN} (空でスキップ): ")
            if github_token:
                env_file.set(keys.GITHUB_PERSONAL_ACCESS_TOKEN, github_token)
                env_file.set(keys.GH_TOKEN, github_token)


COLLECTOR = Collector(
    name="git",
    display_name="Git認証",
    collect_fn=collect_git_credentials,
    source_files=["~/.git-credentials"],
    source_type="file_base64",
)
