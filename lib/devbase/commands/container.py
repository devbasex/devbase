"""Container lifecycle commands (up, down, ps, login, logs, scale, build)"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from devbase.errors import DevbaseError
from devbase.log import get_logger
from devbase.volume.manager import ensure_volumes
from devbase.volume.compose import generate_scaled_compose, get_dev_service_name
from devbase.utils.docker import (
    docker_compose_down,
    docker_compose_up,
    wait_for_containers_ready,
    ensure_network
)
from devbase.utils.config import get_project_name, get_container_scale

logger = get_logger(__name__)

_SCALE_COMPOSE_FILE = Path('.docker-compose.scale.yml')


# ---------------------------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------------------------

def _compose_run(subcommand: str, *extra_args: str) -> int:
    """docker compose コマンドを実行する共通関数"""
    cmd = ['docker', 'compose']
    if _SCALE_COMPOSE_FILE.exists():
        cmd.extend(['-f', str(_SCALE_COMPOSE_FILE)])
    cmd.append(subcommand)
    cmd.extend(extra_args)
    return subprocess.run(cmd).returncode


def _run_deploy_script_for_instances(deploy_script: Path, indices) -> None:
    """デプロイスクリプトをスケールされた各インスタンスに対して実行する"""
    for i in indices:
        logger.info("[Bonus] Running deploy script for instance %d...", i)
        env = {**os.environ, 'DEVBASE_INSTANCE_INDEX': str(i)}
        try:
            subprocess.run(['bash', str(deploy_script)], check=True, env=env)
            logger.info("Deploy script completed for instance %d", i)
        except subprocess.CalledProcessError as e:
            logger.warning("Deploy script failed for instance %d (exit code %d)", i, e.returncode)


# ---------------------------------------------------------------------------
# ディスパッチャ
# ---------------------------------------------------------------------------

def cmd_container(args) -> int:
    """サブコマンドディスパッチャ"""
    subcmd = getattr(args, 'subcommand', None)

    handlers = {
        'up':    lambda: cmd_up(project_name=getattr(args, 'project_name', None),
                                scale=getattr(args, 'scale', None)),
        'down':  lambda: cmd_down(),
        'login': lambda: cmd_login(index=getattr(args, 'index', '1')),
        'ps':    lambda: cmd_ps(all_containers=getattr(args, 'all', False)),
        'logs':  lambda: cmd_logs(follow=getattr(args, 'follow', False),
                                  tail=getattr(args, 'tail', None)),
        'scale': lambda: cmd_scale(new_scale=getattr(args, 'new_scale', None),
                                   project_name=getattr(args, 'project_name', None)),
        'build': lambda: cmd_build(image=getattr(args, 'image', None)),
    }

    handler = handlers.get(subcmd)
    if handler:
        return handler()

    logger.error("サブコマンドを指定してください: %s", ', '.join(handlers))
    return 1


# ---------------------------------------------------------------------------
# cmd_up  (deploy.py の cmd_deploy を移植)
# ---------------------------------------------------------------------------

def cmd_up(project_name: str = None, scale: int = None) -> int:
    """Deploy containers with specified scale"""
    if project_name is None:
        project_name = get_project_name()

    if scale is None:
        scale = get_container_scale()

    dev_service_name = get_dev_service_name()

    logger.info("Deploying project '%s' with scale=%d (dev service: %s)",
                project_name, scale, dev_service_name)

    # Pre-check 1: Ensure .env file exists with content
    if not _ensure_env_files():
        logger.error("Failed to create .env file. Please run 'devbase env init' manually.")
        return 1

    # Pre-check 2: Ensure container images exist
    if not _ensure_images():
        logger.error(
            "Failed to ensure container images. "
            "Run 'devbase container build' for build-based services, "
            "or 'docker pull <image>' for image-only services."
        )
        return 1

    # Pre-step: Auto snapshot（差分世代数ベース世代管理）
    devbase_root_env = os.environ.get('DEVBASE_ROOT')
    if devbase_root_env:
        try:
            from devbase.snapshot.manager import SnapshotManager
            mgr = SnapshotManager(Path(devbase_root_env))
            if mgr.should_start_new_generation():
                logger.info("[0/6] 新しいスナップショット世代を作成中...")
                mgr.create()
            else:
                latest = mgr.list()[-1]['name']
                logger.info("[0/6] スナップショットを差分更新中: %s", latest)
                mgr.create(name=latest, full=False)
            mgr.rotate()
        except Exception as e:
            logger.warning("スナップショットの自動作成に失敗しましたがデプロイは続行します: %s", e)

    try:
        logger.info("[1/6] Ensuring volumes exist...")
        ensure_volumes(scale, project_name)

        logger.info("[1.5/6] Ensuring network exists...")
        ensure_network('devbase_net')

        logger.info("[2/6] Stopping existing containers...")
        if _SCALE_COMPOSE_FILE.exists():
            docker_compose_down(compose_file=_SCALE_COMPOSE_FILE)
        else:
            docker_compose_down()

        logger.info("[3/6] Generating scaled compose file...")
        override_file = generate_scaled_compose(scale, project_name)
        logger.info("Generated: %s", override_file)

        logger.info("[4/6] Starting containers...")
        docker_compose_up(compose_file=override_file, detach=True)

        logger.info("[5/6] Waiting for containers to be ready...")
        wait_for_containers_ready(
            container_prefix=dev_service_name,
            scale=scale,
            compose_file=override_file,
            timeout=60
        )

        # Run project-specific deploy script for each scaled instance
        deploy_script = Path('./deploy')
        if deploy_script.exists() and deploy_script.is_file():
            _run_deploy_script_for_instances(deploy_script, range(1, scale + 1))

        logger.info("=== Deploy completed successfully ===")
        return 0

    except DevbaseError as e:
        logger.error("Deploy failed: %s", e)
        return 1
    except subprocess.CalledProcessError as e:
        logger.error("Deploy failed: %s", e)
        return 1


# ---------------------------------------------------------------------------
# cmd_down
# ---------------------------------------------------------------------------

def cmd_down() -> int:
    """Stop and remove containers"""
    compose_file = _SCALE_COMPOSE_FILE if _SCALE_COMPOSE_FILE.exists() else None
    docker_compose_down(compose_file=compose_file)

    devbase_root = os.environ.get('DEVBASE_ROOT')
    if devbase_root:
        try:
            from devbase.snapshot.manager import SnapshotManager
            mgr = SnapshotManager(Path(devbase_root))
            mgr.rotate()
        except Exception as e:
            logger.warning("スナップショットのローテーションに失敗: %s", e)

    return 0


# ---------------------------------------------------------------------------
# cmd_login
# ---------------------------------------------------------------------------

def cmd_login(index: str = '1') -> int:
    """Login to container"""
    dev_service = get_dev_service_name()

    if _SCALE_COMPOSE_FILE.exists():
        cmd = ['docker', 'compose', '-f', str(_SCALE_COMPOSE_FILE),
               'exec', f'{dev_service}-{index}', 'bash']
    else:
        cmd = ['docker', 'compose', 'exec', f'--index={index}',
               dev_service, 'bash']

    return subprocess.run(cmd).returncode


# ---------------------------------------------------------------------------
# cmd_ps
# ---------------------------------------------------------------------------

def cmd_ps(all_containers: bool = False) -> int:
    """Show container status via docker compose ps"""
    extra = ['--all'] if all_containers else []
    return _compose_run('ps', *extra)


# ---------------------------------------------------------------------------
# cmd_logs
# ---------------------------------------------------------------------------

def cmd_logs(follow: bool = False, tail: Optional[int] = None) -> int:
    """Show container logs via docker compose logs"""
    extra = []
    if follow:
        extra.append('--follow')
    if tail is not None:
        extra.extend(['--tail', str(tail)])
    return _compose_run('logs', *extra)


# ---------------------------------------------------------------------------
# cmd_scale
# ---------------------------------------------------------------------------

def cmd_scale(new_scale: int, project_name: str = None) -> int:
    """Scale containers online without restarting existing ones"""
    if project_name is None:
        project_name = get_project_name()

    dev_service_name = get_dev_service_name()
    current_scale = _get_current_scale()

    logger.info("Scaling project '%s' from %d to %d containers (dev service: %s)",
                project_name, current_scale, new_scale, dev_service_name)

    if new_scale < 1:
        logger.error("Scale must be at least 1")
        return 1

    if new_scale <= current_scale:
        logger.warning("New scale (%d) is not greater than current scale (%d)", new_scale, current_scale)
        logger.info("To scale down, use 'devbase container down' first, then 'devbase container up' with desired scale")
        return 1

    try:
        logger.info("[1/5] Updating env file: CONTAINER_SCALE=%d -> %d...", current_scale, new_scale)
        if not _update_scale_in_env(new_scale):
            return 1

        logger.info("[2/5] Ensuring volumes exist for scale=%d...", new_scale)
        ensure_volumes(new_scale, project_name)

        logger.info("[2.5/5] Ensuring network exists...")
        ensure_network('devbase_net')

        logger.info("[3/5] Generating scaled compose file...")
        override_file = generate_scaled_compose(new_scale, project_name)
        logger.info("Generated: %s", override_file)

        logger.info("[4/5] Starting new containers (%d..%d)...", current_scale + 1, new_scale)
        logger.info("Using --no-recreate to avoid restarting existing containers...")

        result = subprocess.run(
            ['docker', 'compose', '-f', str(override_file), 'up', '-d', '--no-recreate'],
            check=False
        )

        if result.returncode != 0:
            logger.error("Failed to start new containers")
            return 1

        logger.info("[5/5] Waiting for new containers to be ready...")
        wait_for_containers_ready(
            container_prefix=dev_service_name,
            scale=new_scale,
            compose_file=override_file,
            timeout=60
        )

        # Run project-specific deploy script for newly added instances
        deploy_script = Path('./deploy')
        if deploy_script.exists() and deploy_script.is_file():
            _run_deploy_script_for_instances(deploy_script, range(current_scale + 1, new_scale + 1))

        logger.info("=== Scale completed successfully ===")
        logger.info("Container scale: %d -> %d", current_scale, new_scale)
        logger.info("You can now login to the new containers:")
        for i in range(current_scale + 1, new_scale + 1):
            logger.info("  devbase login %d", i)

        return 0

    except DevbaseError as e:
        logger.error("Scale failed: %s", e)
        return 1


# ---------------------------------------------------------------------------
# cmd_build
# ---------------------------------------------------------------------------

def cmd_build(image: str = None) -> int:
    """Build container images"""
    if image is not None:
        devbase_root = os.environ.get('DEVBASE_ROOT', '')
        if not devbase_root:
            logger.error("DEVBASE_ROOT not set")
            return 1

        image_dir = Path(devbase_root) / 'containers' / image
        if not image_dir.is_dir():
            logger.error("Image directory not found: %s", image_dir)
            return 1

        dockerfile = image_dir / 'Dockerfile'
        if not dockerfile.exists():
            logger.error("Dockerfile not found: %s", dockerfile)
            return 1

        logger.info("Building image '%s' from %s ...", image, image_dir)
        result = subprocess.run(
            ['docker', 'build', '-t', image, str(image_dir)],
            check=False
        )
        return result.returncode

    compose_file = Path('compose.yml')
    if not compose_file.exists():
        logger.error("compose.yml not found in current directory")
        return 1

    logger.info("Building images from compose.yml ...")
    result = subprocess.run(
        ['docker', 'compose', 'build'],
        check=False
    )
    return result.returncode


# ---------------------------------------------------------------------------
# 内部関数
# ---------------------------------------------------------------------------

def _ensure_env_files() -> bool:
    """Check if .env files exist. If not, run env init command."""
    project_env = Path('.env')
    devbase_root = Path(os.environ.get('DEVBASE_ROOT', ''))
    if not devbase_root.is_dir():
        logger.error("DEVBASE_ROOT not set")
        return False
    devbase_root_env = devbase_root / '.env'

    if project_env.exists() and devbase_root_env.exists():
        return True

    missing_files = []
    if not project_env.exists():
        missing_files.append("project .env")
    if not devbase_root_env.exists():
        missing_files.append(f"devbase root .env ({devbase_root_env})")

    logger.info("Missing: %s", ', '.join(missing_files))
    logger.info("Running 'devbase env init' to create them...")

    success = True
    child_env = {**os.environ, 'PYTHONPATH': str(devbase_root / 'lib')}

    if not devbase_root_env.exists():
        logger.info("Creating devbase root .env...")
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'devbase.cli', 'env', 'init'],
                env=child_env,
                cwd=str(devbase_root),
                check=False
            )
            if result.returncode != 0:
                success = False
                logger.error("Failed to create devbase root .env")
        except Exception as e:
            logger.error("Running env init for devbase root: %s", e)
            success = False

    if not project_env.exists():
        logger.info("Creating project .env...")
        try:
            project_env.touch()
            logger.info("Created empty project .env: %s", project_env)
        except Exception as e:
            logger.error("Failed to create project .env: %s", e)
            success = False

    return success


_IMAGE_MAX_AGE_DAYS_DEFAULT = 7


def _image_max_age_days() -> int:
    """Threshold for triggering an image rebuild/pull.

    Override via the DEVBASE_IMAGE_MAX_AGE_DAYS environment variable.
    Falls back to the default on missing or malformed values.
    """
    raw = os.environ.get('DEVBASE_IMAGE_MAX_AGE_DAYS')
    if not raw:
        return _IMAGE_MAX_AGE_DAYS_DEFAULT
    try:
        value = int(raw)
        if value < 0:
            raise ValueError
        return value
    except ValueError:
        logger.warning(
            "Invalid DEVBASE_IMAGE_MAX_AGE_DAYS=%r, using default %d",
            raw, _IMAGE_MAX_AGE_DAYS_DEFAULT
        )
        return _IMAGE_MAX_AGE_DAYS_DEFAULT


def _ensure_images() -> bool:
    """Check that required container images exist and are fresh.

    Behavior (threshold = DEVBASE_IMAGE_MAX_AGE_DAYS or 7):
      - Image missing + has build: → run `devbase build`
      - Image missing + image-only (no build:) → run `docker pull`
      - Image present and >= threshold days old + has build:
        → rebuild with `--no-cache`
      - Image present + image-only → nothing to do
        (Created reflects upstream build time, not local pull time, so we
        cannot derive a meaningful freshness signal here. Users who want
        public images refreshed should run `docker pull` explicitly.)
      - Otherwise: nothing to do

    Returns True on success or no-op, False on failure.
    """
    compose_file = Path('compose.yml')
    if not compose_file.exists():
        logger.warning("compose.yml not found, skipping image check")
        return True

    dev_service_name = get_dev_service_name()

    try:
        result = subprocess.run(
            ['docker', 'compose', 'config', '--format', 'json'],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            logger.info("Unable to check image status")
            logger.info("Running 'devbase container build' to ensure images exist...")
            return _run_build()

        config = json.loads(result.stdout)
        services = config.get('services', {})
        dev_service = services.get(dev_service_name, {})
        image_name = dev_service.get('image', '')
        has_build = bool(dev_service.get('build'))

        if not image_name:
            logger.warning("No image specified for %s service", dev_service_name)
            return True

        inspect = subprocess.run(
            ['docker', 'image', 'inspect', image_name],
            capture_output=True,
            text=True,
            check=False
        )

        if inspect.returncode != 0:
            if has_build:
                logger.info("Container image '%s' not found", image_name)
                logger.info("Running 'devbase container build' to create it...")
                return _run_build()
            logger.info("Container image '%s' not found, pulling...", image_name)
            return _run_pull(image_name)

        # Image-only services: 'Created' reflects upstream build time, not
        # local pull time, so age-based re-pull is not meaningful. Skip.
        if not has_build:
            return True

        max_age = _image_max_age_days()
        age_days = _get_image_age_days(inspect.stdout)
        if age_days is None or age_days < max_age:
            return True

        logger.info(
            "Container image '%s' is %d days old (>= %d days threshold)",
            image_name, age_days, max_age
        )
        logger.info("Rebuilding with --no-cache...")
        return _run_build(no_cache=True)

    except Exception as e:
        logger.warning("Error checking image: %s", e)
        logger.info("Attempting to build anyway...")
        return _run_build()


def _get_image_age_days(inspect_json: str) -> Optional[int]:
    """Return age of the inspected image in days, or None on failure."""
    try:
        data = json.loads(inspect_json)
        if not data:
            return None
        created = data[0].get('Created', '')
        if not created:
            return None
        # Docker's 'Created' is RFC3339 with nanoseconds, e.g.
        # '2024-01-15T10:30:00.123456789Z'. Python 3.10's fromisoformat does
        # not accept nanoseconds, so trim fractional seconds to 6 digits and
        # normalize 'Z' to '+00:00'.
        ts = re.sub(r'(\.\d{6})\d+', r'\1', created.replace('Z', '+00:00'))
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
        return delta.days
    except Exception as e:
        logger.warning("Could not parse image creation date: %s", e)
        return None


def _run_build(no_cache: bool = False) -> bool:
    """Run the build command (optionally with --no-cache)."""
    devbase_root = Path(os.environ.get('DEVBASE_ROOT', ''))
    if not devbase_root.exists():
        logger.error("DEVBASE_ROOT not set")
        return False

    devbase_bin = devbase_root / 'bin' / 'devbase'
    if not devbase_bin.exists():
        logger.error("devbase command not found at %s", devbase_bin)
        return False

    cmd = ['bash', str(devbase_bin), 'build']
    if no_cache:
        cmd.append('--no-cache')

    try:
        result = subprocess.run(cmd, check=False)
        return result.returncode == 0
    except Exception as e:
        logger.error("Running build: %s", e)
        return False


def _run_pull(image_name: str) -> bool:
    """docker pull the specified public image."""
    try:
        result = subprocess.run(
            ['docker', 'pull', image_name],
            check=False
        )
        return result.returncode == 0
    except Exception as e:
        logger.error("Pulling image '%s': %s", image_name, e)
        return False


def _update_scale_in_env(new_scale: int) -> bool:
    """Update CONTAINER_SCALE value in env file"""
    env_file = Path('./env')

    if not env_file.exists():
        logger.error("env file not found: %s", env_file)
        return False

    try:
        with open(env_file, 'r') as f:
            lines = f.readlines()

        updated = False
        new_lines = []
        for line in lines:
            if line.strip().startswith('CONTAINER_SCALE='):
                new_lines.append(f'CONTAINER_SCALE={new_scale}\n')
                updated = True
            else:
                new_lines.append(line)

        if not updated:
            new_lines.append(f'\n# Added by devbase scale command\nCONTAINER_SCALE={new_scale}\n')

        with open(env_file, 'w') as f:
            f.writelines(new_lines)

        return True

    except Exception as e:
        logger.error("Updating env file: %s", e)
        return False


def _get_current_scale() -> int:
    """Get current CONTAINER_SCALE from env file"""
    env_file = Path('./env')

    if not env_file.exists():
        return 0

    try:
        with open(env_file, 'r') as f:
            for line in f:
                if line.strip().startswith('CONTAINER_SCALE='):
                    value = line.split('=', 1)[1].strip()
                    return int(value)
    except Exception:
        pass

    return 0
