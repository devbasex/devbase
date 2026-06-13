"""ホスト接続情報 (SSH) コレクター

コンテナからホストへ SSH してホスト側 GUI アプリ (例: Chrome をリモートデバッグ
モードで起動) を起動するワークフロー向けに、ホストのログインユーザー名 / SSH 先
ホスト名を収集する。``devbase env init`` はホスト上で実行されるため、ホストの
ユーザー名を ``getpass.getuser()`` で確実に取得できる。
"""

import getpass

from devbase.log import get_logger
from devbase.env import keys
from devbase.env.store import EnvFile, safe_input
from devbase.env.collector import Collector

logger = get_logger(__name__)

DEFAULT_HOST_SSH_HOST = "host.docker.internal"


def _default_host_user() -> str:
    """ホストのログインユーザー名を返す。

    ``getpass.getuser()`` は HOME/USER/LOGNAME 等が全て無い環境で例外を投げうるため、
    その場合は空文字を返して呼び出し側で安全にスキップできるようにする。
    """
    try:
        return getpass.getuser()
    except Exception:
        return ""


def collect_host_info(env_file: EnvFile) -> None:
    """ホスト接続情報 (SSH) を対話的に収集する"""
    print("\n=== ホスト接続情報 (SSH) ===")

    # HOST_SSH_USER: 既存値 > getpass.getuser() を既定として提示し、上書き可能にする
    default_user = env_file.get(keys.HOST_SSH_USER) or _default_host_user()
    user = safe_input(f"{keys.HOST_SSH_USER} [{default_user}]: ", default_user)
    if user:
        env_file.set(keys.HOST_SSH_USER, user)
    else:
        logger.info("%s: 既定値が取得できずスキップ", keys.HOST_SSH_USER)

    # HOST_SSH_HOST: 任意。既定 host.docker.internal (WSL2/Windows では上書き可)
    default_host = env_file.get(keys.HOST_SSH_HOST) or DEFAULT_HOST_SSH_HOST
    host = safe_input(f"{keys.HOST_SSH_HOST} [{default_host}]: ", default_host)
    if host:
        env_file.set(keys.HOST_SSH_HOST, host)


COLLECTOR = Collector(
    name="host",
    display_name="ホスト接続情報 (SSH)",
    collect_fn=collect_host_info,
)
