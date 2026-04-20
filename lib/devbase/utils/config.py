"""Configuration utilities for devbase"""

import os
from pathlib import Path
from typing import Optional

from devbase.errors import ConfigError


def get_project_name() -> str:
    """
    Get project name from environment or current directory

    Returns:
        Project name (COMPOSE_PROJECT_NAME or current directory name)
    """
    project_name = os.environ.get('COMPOSE_PROJECT_NAME')
    if project_name:
        return project_name

    # Fallback to current directory name
    return Path.cwd().name


def get_container_scale() -> int:
    """
    Get container scale from environment

    Returns:
        Number of containers (default: 2)
    """
    scale_str = os.environ.get('CONTAINER_SCALE', '2')
    try:
        scale = int(scale_str)
        if scale < 1:
            raise ConfigError("CONTAINER_SCALE must be >= 1")
        return scale
    except ValueError as e:
        raise ConfigError(f"Invalid CONTAINER_SCALE value '{scale_str}': {e}")


def get_devbase_root() -> Optional[Path]:
    """
    Get devbase root directory from environment

    Returns:
        Path to devbase root directory, or None if not set
    """
    devbase_root = os.environ.get('DEVBASE_ROOT')
    if devbase_root:
        return Path(devbase_root)
    return None


def get_devbase_bin() -> Optional[Path]:
    """
    Get devbase bin directory

    Returns:
        Path to devbase bin directory, or None if not set
    """
    root = get_devbase_root()
    if root:
        return root / 'bin'
    return None


def get_devbase_etc() -> Optional[Path]:
    """
    Get devbase etc directory

    Returns:
        Path to devbase etc directory, or None if not set
    """
    root = get_devbase_root()
    if root:
        return root / 'etc'
    return None
