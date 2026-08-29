"""Volume management functions for devbase"""

import os
import re
import subprocess
from typing import Optional

from devbase.env import keys
from devbase.errors import DevbaseError, DockerError
from devbase.log import get_logger

logger = get_logger("devbase.volume.manager")

# 共有ボリューム名のプレフィックス
SHARED_VOLUME_PREFIX = "devbase_home_"
WORK_VOLUME_PREFIX = "devbase_work_"
# 全コンテナで共有するホームディレクトリボリューム
HOME_UBUNTU_VOLUME = "devbase_home_ubuntu"

# --- アカウントグループ (PLAN39) ---------------------------------------------
# アカウントグループは「使用する Google / AWS アカウントの単位」。グループごとに
# devbase_home_<group> を作り、/persistent/group としてマウントする。認証情報や
# 会話履歴のようにテナントへ紐づくデータ (分類 B) の置き場になる。
DEFAULT_ACCOUNT_GROUP = "default"
# Docker のボリューム名として使える文字種
_GROUP_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
# 数字のみは devbase_home_<index> (get_volume_for_index) と同じ名前になる
_NUMERIC_NAME_RE = re.compile(r"^[0-9]+$")
# 共通ボリューム devbase_home_ubuntu と同じ名前になる
_RESERVED_ACCOUNT_GROUPS = ("ubuntu",)


def resolve_account_group(group: Optional[str] = None) -> str:
    """アカウントグループ名を解決して検証する。

    解決順は 引数 → ``DEVBASE_ACCOUNT_GROUP`` → ``default``。環境変数には
    グローバル ``env`` とプロジェクト ``env`` を重ねた結果が入っている
    (``bin/devbase`` が ``set -a`` で source する) ので、ここで読むだけで
    3 レベルの解決結果になる。

    解決結果はそのままボリューム名 ``devbase_home_<group>`` の一部になるため、
    **起動前に**次の 3 つを弾く。(b) と (c) は (a) を通過してしまうので、
    正規表現とは別のチェックとして明示的に持つ。

    (a) Docker のボリューム名にできない文字列
    (b) 予約語 ``ubuntu`` (共通ボリューム ``devbase_home_ubuntu`` と衝突)
    (c) 数字のみ (``devbase_home_<index>`` と衝突)

    Raises:
        DevbaseError: グループ名が使えない場合
    """
    raw = group if group is not None else os.environ.get(
        keys.DEVBASE_ACCOUNT_GROUP, "")
    name = (raw or "").strip()
    if not name:
        return DEFAULT_ACCOUNT_GROUP

    if not _GROUP_NAME_RE.match(name):
        raise DevbaseError(
            f"{keys.DEVBASE_ACCOUNT_GROUP} が不正です: '{name}'。"
            "Docker のボリューム名に使える文字 (英数字・ドット・ハイフン・"
            "アンダースコア、先頭は英数字) だけを使ってください"
        )
    if name in _RESERVED_ACCOUNT_GROUPS:
        raise DevbaseError(
            f"{keys.DEVBASE_ACCOUNT_GROUP} に予約語は使えません: '{name}'。"
            f"共通ボリューム {HOME_UBUNTU_VOLUME} と同じ名前になります"
        )
    if _NUMERIC_NAME_RE.match(name):
        raise DevbaseError(
            f"{keys.DEVBASE_ACCOUNT_GROUP} に数字だけの名前は使えません: "
            f"'{name}'。インスタンス番号のボリューム "
            f"{SHARED_VOLUME_PREFIX}<index> と同じ名前になります"
        )
    return name


def get_group_volume(group: Optional[str] = None) -> str:
    """アカウントグループのボリューム名 (``devbase_home_<group>``) を返す。

    検証を迂回する経路を作らないため、名前の解決は必ず
    :func:`resolve_account_group` を通す。
    """
    return f"{SHARED_VOLUME_PREFIX}{resolve_account_group(group)}"


class VolumeManager:
    """Manages Docker volumes for devbase projects"""

    def __init__(self, project_name: str = None):
        """
        Initialize VolumeManager

        Args:
            project_name: Project name (unused, kept for backward compatibility)
        """
        self.project_name = project_name

    def _volume_exists(self, volume_name: str) -> bool:
        """Check if Docker volume exists"""
        try:
            result = subprocess.run(
                ['docker', 'volume', 'inspect', volume_name],
                capture_output=True,
                text=True,
                check=False
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning("Failed to check volume %s: %s", volume_name, e)
            return False

    def _create_volume(self, volume_name: str) -> bool:
        """Create Docker volume"""
        try:
            subprocess.run(
                ['docker', 'volume', 'create', volume_name],
                capture_output=True,
                text=True,
                check=True
            )
            return True
        except subprocess.CalledProcessError as e:
            logger.error("Failed to create volume %s: %s", volume_name, e.stderr)
            return False

    def get_volume_for_index(self, index: int) -> str:
        """
        Get shared volume name for specified index

        Args:
            index: Container index (1-based)

        Returns:
            Shared volume name (devbase_home_{index})
        """
        return f"{SHARED_VOLUME_PREFIX}{index}"

    def get_work_volume_for_index(self, index: int) -> str:
        """
        Get work volume name for specified index

        Args:
            index: Container index (1-based)

        Returns:
            Work volume name (devbase_work_{index})
        """
        return f"{WORK_VOLUME_PREFIX}{index}"

    def get_ai_volume_for_index(self, index: int) -> str:
        """
        Get AI settings volume name for specified index

        Note: All containers share the same home directory volume (devbase_home_ubuntu)
        regardless of index.

        Args:
            index: Container index (1-based, unused)

        Returns:
            Home ubuntu volume name (devbase_home_ubuntu)
        """
        return HOME_UBUNTU_VOLUME

    def ensure_volumes(self, scale: int, group: Optional[str] = None) -> None:
        """
        Ensure required volumes exist for the specified scale

        Creates volumes:
        - devbase_home_ubuntu: Shared home directory for all containers
        - devbase_home_{group}: Per-account-group directory (PLAN39)
        - devbase_work_{i}: Project work directory per instance

        Args:
            scale: Number of container instances
            group: Account group name (default: resolved from environment)
        """
        logger.info("Ensuring volumes for %d container(s)", scale)

        # グループ名の検証は Docker を触る前に済ませる。あとに置くと、名前が
        # 不正なだけの入力エラーでも共有ボリュームが作られてから失敗して
        # しまい、Docker の状態が変わってしまう。
        group_volume = get_group_volume(group)

        # Ensure shared home directory volume (once for all containers)
        if self._volume_exists(HOME_UBUNTU_VOLUME):
            logger.info("  %s (shared home, exists)", HOME_UBUNTU_VOLUME)
        else:
            logger.info("  Creating %s (shared home)...", HOME_UBUNTU_VOLUME)
            if not self._create_volume(HOME_UBUNTU_VOLUME):
                raise DockerError(f"Failed to create volume {HOME_UBUNTU_VOLUME}")

        # Ensure account group volume (shared by all containers of the group)
        if self._volume_exists(group_volume):
            logger.info("  %s (account group, exists)", group_volume)
        else:
            logger.info("  Creating %s (account group)...", group_volume)
            if not self._create_volume(group_volume):
                raise DockerError(f"Failed to create volume {group_volume}")

        # Create or verify work volumes for each instance
        for i in range(1, scale + 1):
            work_volume = self.get_work_volume_for_index(i)

            # Ensure work volume
            if self._volume_exists(work_volume):
                logger.info("  %s (exists)", work_volume)
            else:
                logger.info("  Creating %s...", work_volume)
                if not self._create_volume(work_volume):
                    raise DockerError(f"Failed to create volume {work_volume}")


def ensure_volumes(scale: int, project_name: str = None,
                   group: Optional[str] = None) -> None:
    """
    Ensure required shared volumes exist for the specified scale

    All projects share the same home volume (devbase_home_ubuntu) and the
    volume of their account group (devbase_home_<group>); work volumes are
    per container index.

    Args:
        scale: Number of container instances
        project_name: Unused, kept for backward compatibility
        group: Account group name (default: resolved from environment)
    """
    manager = VolumeManager()
    manager.ensure_volumes(scale, group)


def get_volume_for_index(index: int, project_name: str = None) -> str:
    """
    Get shared volume name for specified index

    Args:
        index: Container index (1-based)
        project_name: Unused, kept for backward compatibility

    Returns:
        Shared volume name (devbase_home_{index})
    """
    return f"{SHARED_VOLUME_PREFIX}{index}"


def get_work_volume_for_index(index: int, project_name: str = None) -> str:
    """
    Get work volume name for specified index

    Args:
        index: Container index (1-based)
        project_name: Unused, kept for backward compatibility

    Returns:
        Work volume name (devbase_work_{index})
    """
    return f"{WORK_VOLUME_PREFIX}{index}"


def get_ai_volume_for_index(index: int, project_name: str = None) -> str:
    """
    Get AI settings volume name for specified index

    Note: All containers share the same home directory volume (devbase_home_ubuntu)
    regardless of index.

    Args:
        index: Container index (1-based, unused)
        project_name: Unused, kept for backward compatibility

    Returns:
        Home ubuntu volume name (devbase_home_ubuntu)
    """
    return HOME_UBUNTU_VOLUME
