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
from devbase.env.host_import import HostImport

logger = get_logger(__name__)

STEP = "Git認証"


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


def collect_git_credentials(env_file: EnvFile, *, host: HostImport) -> None:
    """Git認証情報を対話的に収集する

    ホストの Git の設定 (``git config --global``・``~/.git-credentials``) から取れる値は、
    1 回の確認で取り込むかを決める (#314 前提 7)。断れば Git の値を 1 つも書かず、
    手入力へも進まない。取れる値が 1 つも無ければ今の手入力の経路。
    """
    print("\n=== Git認証情報 ===")

    auto = {
        keys.GIT_USER_NAME: _get_git_config('user.name'),
        keys.GIT_USER_EMAIL: _get_git_config('user.email'),
        keys.GIT_CREDENTIAL_HELPER: _get_git_config('credential.helper'),
        keys.GIT_CREDENTIALS_BASE64: _read_git_credentials_base64(),
    }
    github_token = _extract_github_token()
    importable = [k for k, v in auto.items() if v is not None and not env_file.get(k)]
    if github_token and not env_file.get(keys.GITHUB_PERSONAL_ACCESS_TOKEN):
        importable += [keys.GITHUB_PERSONAL_ACCESS_TOKEN, keys.GH_TOKEN]

    if importable and not host.importing:
        if host.asking:
            print(f"ホストの Git の設定から取り込めるキー: {', '.join(importable)}")
        if not host.confirm(STEP, "取り込みますか?"):
            host.declined(STEP)
            return

    collect_key(env_file, keys.GIT_USER_NAME, auto_value=auto[keys.GIT_USER_NAME], mask_after=0)
    collect_key(env_file, keys.GIT_USER_EMAIL, auto_value=auto[keys.GIT_USER_EMAIL], mask_after=0)
    collect_key(env_file, keys.GIT_CREDENTIAL_HELPER,
                auto_value=auto[keys.GIT_CREDENTIAL_HELPER], mask_after=0)
    collect_key(env_file, keys.GIT_CREDENTIALS_BASE64, auto_value=auto[keys.GIT_CREDENTIALS_BASE64])

    # GITHUB_PERSONAL_ACCESS_TOKEN / GH_TOKEN: 2キー同時設定のため個別処理
    existing = env_file.get(keys.GITHUB_PERSONAL_ACCESS_TOKEN)
    if existing:
        logger.info("%s: 設定済み (%s...)", keys.GITHUB_PERSONAL_ACCESS_TOKEN, existing[:4])
        logger.info("%s: 設定済み", keys.GH_TOKEN)
    else:
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
    host.record('git', importable)


COLLECTOR = Collector(
    name="git",
    display_name="Git認証",
    collect_fn=collect_git_credentials,
    source_files=["~/.git-credentials"],
    source_type="file_base64",
    host_import=True,
)
