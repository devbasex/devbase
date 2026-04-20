"""Shell utilities for devbase"""

import os
import platform
from pathlib import Path
from typing import Optional, List

from devbase.errors import ConfigError


def get_current_shell() -> str:
    """
    Get current shell name (bash, zsh, etc.)

    Returns:
        Shell name (e.g., "bash", "zsh")
    """
    shell = os.environ.get('SHELL', '/bin/bash')
    return Path(shell).name


def get_shell_rc_file(shell_name: Optional[str] = None) -> Path:
    """
    Get appropriate shell RC file path

    Args:
        shell_name: Shell name (e.g., "bash", "zsh"). Auto-detected if None.

    Returns:
        Path to shell RC file (~/.bashrc, ~/.zshrc, ~/.bash_profile)
    """
    if shell_name is None:
        shell_name = get_current_shell()

    home = Path.home()

    if shell_name == 'zsh':
        return home / '.zshrc'
    elif shell_name == 'bash':
        # macOS uses .bash_profile for login shells, Linux uses .bashrc
        system = platform.system()
        if system == 'Darwin':
            rc_file = home / '.bash_profile'
            # Create .bash_profile if it doesn't exist
            if not rc_file.exists():
                rc_file.touch()
            return rc_file
        else:
            return home / '.bashrc'
    else:
        # Fallback to .bashrc for unknown shells
        return home / '.bashrc'


def check_line_in_file(file_path: Path, search_string: str) -> bool:
    """
    Check if a line containing search_string exists in file

    Args:
        file_path: Path to file
        search_string: String to search for

    Returns:
        True if found, False otherwise
    """
    if not file_path.exists():
        return False

    try:
        with open(file_path, 'r') as f:
            content = f.read()
            return search_string in content
    except IOError:
        return False


def add_to_rc_file(
    rc_file: Path,
    lines: List[str],
    marker: Optional[str] = None,
    check_string: Optional[str] = None
) -> bool:
    """
    Add lines to shell RC file if not already present

    Args:
        rc_file: Path to RC file
        lines: Lines to add (without trailing newlines)
        marker: Comment marker to identify the section (e.g., "# devbase")
        check_string: String to check for existing content (default: marker)

    Returns:
        True if lines were added, False if already present
    """
    if check_string is None:
        check_string = marker if marker else lines[0]

    # Check if already present
    if check_line_in_file(rc_file, check_string):
        return False

    # Create RC file if it doesn't exist
    if not rc_file.exists():
        rc_file.touch()

    # Append lines
    try:
        with open(rc_file, 'a') as f:
            f.write('\n')  # Add blank line before
            if marker:
                f.write(f'{marker}\n')
            for line in lines:
                f.write(f'{line}\n')
        return True
    except IOError as e:
        raise ConfigError(f"Failed to write to {rc_file}: {e}")
