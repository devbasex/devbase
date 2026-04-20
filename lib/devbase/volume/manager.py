"""Volume management functions for devbase"""

import subprocess
from typing import Optional

from devbase.errors import DockerError
from devbase.log import get_logger

logger = get_logger("devbase.volume.manager")

# 共有ボリューム名のプレフィックス
SHARED_VOLUME_PREFIX = "devbase_home_"
WORK_VOLUME_PREFIX = "devbase_work_"
AI_VOLUME_PREFIX = "devbase_ai_"
# 全コンテナで共有するホームディレクトリボリューム
HOME_UBUNTU_VOLUME = "devbase_home_ubuntu"


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

    def ensure_volumes(self, scale: int) -> None:
        """
        Ensure required volumes exist for the specified scale

        Creates volumes:
        - devbase_home_ubuntu: Shared home directory for all containers
        - devbase_work_{i}: Project work directory per instance

        Args:
            scale: Number of container instances
        """
        logger.info("Ensuring volumes for %d container(s)", scale)

        # Ensure shared home directory volume (once for all containers)
        if self._volume_exists(HOME_UBUNTU_VOLUME):
            logger.info("  %s (shared home, exists)", HOME_UBUNTU_VOLUME)
        else:
            logger.info("  Creating %s (shared home)...", HOME_UBUNTU_VOLUME)
            if not self._create_volume(HOME_UBUNTU_VOLUME):
                raise DockerError(f"Failed to create volume {HOME_UBUNTU_VOLUME}")

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


def ensure_volumes(scale: int, project_name: str = None) -> None:
    """
    Ensure required shared volumes exist for the specified scale

    All projects share the same volumes (devbase_home_1, devbase_home_2, ...)
    based on container index.

    Args:
        scale: Number of container instances
        project_name: Unused, kept for backward compatibility
    """
    manager = VolumeManager()
    manager.ensure_volumes(scale)


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
