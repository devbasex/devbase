"""Init command implementation"""

import shutil
import subprocess
from pathlib import Path

from devbase.errors import DevbaseError
from devbase.log import get_logger
from devbase.utils.shell import (
    get_current_shell,
    get_shell_rc_file,
    add_to_rc_file
)
from devbase.utils.config import get_devbase_root, get_devbase_bin, get_devbase_etc

logger = get_logger("devbase.commands.init")


def _init_submodules(repo_root: Path) -> bool:
    """
    Initialize git submodules if not already initialized.

    Args:
        repo_root: Path to repository root directory

    Returns:
        True if submodules were initialized, False if already initialized or no submodules
    """
    gitmodules = repo_root / '.gitmodules'

    # Check if .gitmodules exists
    if not gitmodules.exists():
        return False

    # Check if any submodule is not initialized
    # A submodule is not initialized if its directory is empty or doesn't have .git
    needs_init = False
    try:
        with open(gitmodules, 'r') as f:
            content = f.read()
            # Parse submodule paths from .gitmodules
            for line in content.split('\n'):
                if 'path = ' in line:
                    submodule_path = line.split('path = ')[1].strip()
                    submodule_dir = repo_root / submodule_path
                    submodule_git = submodule_dir / '.git'

                    # Check if submodule directory exists and has .git
                    if not submodule_dir.exists() or not submodule_git.exists():
                        needs_init = True
                        break
                    # Check if directory is empty (except .git)
                    if submodule_dir.exists():
                        contents = list(submodule_dir.iterdir())
                        if len(contents) == 0:
                            needs_init = True
                            break
    except Exception:
        return False

    if not needs_init:
        return False

    # Initialize submodules
    logger.info("Initializing git submodules in %s...", repo_root)
    try:
        result = subprocess.run(
            ['git', 'submodule', 'update', '--init', '--recursive'],
            cwd=repo_root,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            logger.info("Git submodules initialized successfully")
            return True
        else:
            logger.warning("Failed to initialize submodules: %s", result.stderr)
            return False
    except Exception as e:
        logger.warning("Failed to initialize submodules: %s", e)
        return False


def _setup_plugins_config(devbase_root: Path) -> None:
    """
    Set up plugins infrastructure:
    - Create ~/.devbase/config.yml with official registry URL if not exists
    - Ensure plugins/ directory exists
    - Ensure projects/ directory exists
    - Create plugins.yml with official repository if not exists
    """
    import yaml

    # Create ~/.devbase/config.yml
    config_dir = Path.home() / '.devbase'
    config_file = config_dir / 'config.yml'
    if not config_file.exists():
        config_dir.mkdir(exist_ok=True)
        default_config = {
            'official_registry': 'https://github.com/devbasex/devbase-samples.git',
        }
        with open(config_file, 'w') as f:
            yaml.dump(default_config, f, default_flow_style=False, allow_unicode=True)
        logger.info("Created plugins config: %s", config_file)
    else:
        logger.info("Plugins config already exists")

    # Ensure plugins/ directory exists
    plugins_dir = devbase_root / 'plugins'
    plugins_dir.mkdir(exist_ok=True)

    # Ensure projects/ directory exists
    projects_dir = devbase_root / 'projects'
    projects_dir.mkdir(exist_ok=True)

    # Create plugins.yml with official repository if not exists
    plugins_yml = devbase_root / 'plugins.yml'
    if not plugins_yml.exists():
        logger.info("Creating plugins.yml with official repository...")
        try:
            from devbase.plugin.repo_manager import add_official_repository
            from devbase.plugin.registry import PluginRegistry
            registry = PluginRegistry(devbase_root)
            add_official_repository(registry)
        except Exception as e:
            # ネットワーク失敗時は最低限の構造で作成
            logger.warning("Could not fetch official repository: %s", e)
            default_data = {'repositories': [], 'installed_plugins': []}
            with open(plugins_yml, 'w') as f:
                yaml.dump(default_data, f, default_flow_style=False,
                          allow_unicode=True, sort_keys=False)
            logger.info("Created empty plugins.yml: %s", plugins_yml)
    else:
        logger.info("plugins.yml already exists")



def _migrate_rc_devbase_block(rc_file: Path, devbase_root: Path) -> bool:
    """
    Migrate old devbase RC block to new format.
    - Removes DEVBASE_PARENT_ROOT lines
    - Updates DEVBASE_ROOT if path changed
    - Removes old hardcoded PATH/source lines that don't match current devbase_root

    Returns:
        True if migration was performed
    """
    import re

    if not rc_file.exists():
        return False

    with open(rc_file, 'r') as f:
        content = f.read()

    original = content
    devbase_root_str = str(devbase_root)

    # Remove DEVBASE_PARENT_ROOT lines
    content = re.sub(r'export DEVBASE_PARENT_ROOT="[^"]*"\n', '', content)

    # Update DEVBASE_ROOT path if it points to an old location
    old_root_match = re.search(r'export DEVBASE_ROOT="([^"]*)"', content)
    if old_root_match and old_root_match.group(1) != devbase_root_str:
        content = re.sub(
            r'export DEVBASE_ROOT="[^"]*"',
            f'export DEVBASE_ROOT="{devbase_root_str}"',
            content,
        )

    # Remove old hardcoded PATH lines that don't use ${DEVBASE_ROOT} variable
    # Keep lines using ${DEVBASE_ROOT} or matching current devbase_root
    escaped_root = re.escape(devbase_root_str)
    lines = content.split('\n')
    filtered = []
    for line in lines:
        # Match hardcoded devbase PATH entries
        if re.match(r'^export PATH=".*devbase.*/bin:\$PATH"$', line):
            # Keep if it uses ${DEVBASE_ROOT} or matches current root exactly
            if '${DEVBASE_ROOT}' in line or f'"{devbase_root_str}/bin:' in line:
                filtered.append(line)
                continue
            # Remove old hardcoded paths
            continue
        # Match hardcoded devbase completion source entries
        if re.match(r'^source ".*devbase.*/etc/devbase-completion\.bash"$', line):
            if f'"{devbase_root_str}/etc/' in line:
                filtered.append(line)
                continue
            # Remove old hardcoded paths
            continue
        filtered.append(line)
    content = '\n'.join(filtered)

    # Remove empty "# devbase" marker blocks (marker followed only by blank lines)
    content = re.sub(r'# devbase\n(\s*\n)+(?=# |\Z)', '', content)

    if content == original:
        return False

    # Clean up excessive blank lines
    content = re.sub(r'\n{4,}', '\n\n\n', content)

    # バックアップを作成してから書き込み
    backup_file = rc_file.with_suffix(rc_file.suffix + '.devbase-backup')
    shutil.copy2(rc_file, backup_file)

    with open(rc_file, 'w') as f:
        f.write(content)

    logger.info("Migrated old devbase settings in %s (backup: %s)", rc_file, backup_file)
    return True


def _register_shell_completion(
    current_shell: str, devbase_etc: Path, rc_file: Path,
) -> bool:
    """Register shell completion. Returns True if completion was added."""
    if current_shell == 'zsh':
        completion_file = devbase_etc / '_devbase'
        lines = [
            f'fpath=("{devbase_etc}" $fpath)',
            'autoload -Uz compinit && compinit'
        ]
        check_string = '# devbase completion'
        shell_label = 'zsh'
    else:
        completion_file = devbase_etc / 'devbase-completion.bash'
        lines = [f'source "{completion_file}"']
        check_string = 'devbase-completion.bash'
        shell_label = 'bash'

    if not completion_file.exists():
        logger.warning("%s completion file not found: %s", shell_label, completion_file)
        return False

    added = add_to_rc_file(
        rc_file,
        lines=lines,
        marker='# devbase completion',
        check_string=check_string,
    )
    if added:
        logger.info("Registered %s-completion in %s", shell_label, rc_file)
    else:
        logger.info("%s-completion already registered", shell_label)
    return added


def cmd_init(devbase_root: Path = None) -> int:
    """
    Initialize devbase environment (add to PATH, register shell completion,
    set up plugins config)

    Args:
        devbase_root: Path to devbase root directory (default: from environment)

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    # Get devbase directories
    if devbase_root is None:
        devbase_root = get_devbase_root()
        if devbase_root is None:
            logger.error("DEVBASE_ROOT environment variable not set")
            return 1

    devbase_bin = get_devbase_bin()
    devbase_etc = get_devbase_etc()

    if not devbase_bin or not devbase_bin.exists():
        logger.error("devbase bin directory not found: %s", devbase_bin)
        return 1

    if not devbase_etc or not devbase_etc.exists():
        logger.error("devbase etc directory not found: %s", devbase_etc)
        return 1

    try:
        # Initialize git submodules if any exist in devbase itself
        _init_submodules(devbase_root)

        # Set up plugins infrastructure
        _setup_plugins_config(devbase_root)

        # Detect shell
        current_shell = get_current_shell()
        rc_file = get_shell_rc_file(current_shell)

        logger.info("Detected shell: %s", current_shell)
        logger.info("RC file: %s", rc_file)
        logger.info("DEVBASE_ROOT: %s", devbase_root)

        # Migrate old RC block if needed (remove DEVBASE_PARENT_ROOT, fix path)
        _migrate_rc_devbase_block(rc_file, devbase_root)

        # Add DEVBASE_ROOT and PATH
        env_added = add_to_rc_file(
            rc_file,
            lines=[
                f'export DEVBASE_ROOT="{devbase_root}"',
                'export PATH="${DEVBASE_ROOT}/bin:$PATH"'
            ],
            marker='# devbase',
            check_string='export DEVBASE_ROOT='
        )

        if env_added:
            logger.info("Added devbase environment to %s", rc_file)
        else:
            logger.info("devbase environment already configured")

        # Register shell completion
        completion_added = _register_shell_completion(
            current_shell, devbase_etc, rc_file,
        )

        # Print final message
        if env_added or completion_added:
            logger.info("Run 'source %s' to apply changes", rc_file)
        else:
            logger.info("devbase is already configured")

        return 0

    except DevbaseError as e:
        logger.error("Init failed: %s", e)
        return 1
    except OSError as e:
        logger.error("Init failed: %s", e)
        return 1
