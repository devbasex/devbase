"""Configuration utilities for devbase"""

import os
from pathlib import Path
from typing import Optional



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
