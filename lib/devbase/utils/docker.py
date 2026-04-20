"""Docker command utilities for devbase"""

import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

from devbase.errors import DockerError
from devbase.log import get_logger

logger = get_logger("devbase.utils.docker")


def docker_compose(
    command: List[str],
    compose_file: Optional[Path] = None,
    check: bool = True,
    capture_output: bool = False,
    silent_error: bool = False
) -> subprocess.CompletedProcess:
    """
    Execute docker compose command

    Args:
        command: Command arguments (e.g., ['up', '-d'])
        compose_file: Compose file path (optional, uses docker's default if not specified)
        check: Raise exception on non-zero exit code
        capture_output: Capture stdout/stderr
        silent_error: Suppress error output

    Returns:
        CompletedProcess instance

    Raises:
        subprocess.CalledProcessError: If check=True and command fails
    """
    cmd = ['docker', 'compose']

    if compose_file:
        cmd.extend(['-f', str(compose_file)])

    cmd.extend(command)

    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=check
        )
        return result
    except subprocess.CalledProcessError as e:
        if not silent_error and e.stderr:
            logger.error("%s", e.stderr)
        raise


def get_container_status(
    service_name: str,
    compose_file: Optional[Path] = None
) -> Optional[str]:
    """
    Get container status (running, exited, etc.)

    Args:
        service_name: Service name in compose file
        compose_file: Compose file path (optional)

    Returns:
        Container status string or None if container not found
    """
    import json
    try:
        # Use -a to include stopped/exited containers
        result = docker_compose(
            ['ps', '-a', '--format', 'json', service_name],
            compose_file=compose_file,
            check=True,
            capture_output=True,
            silent_error=True
        )
        if result.stdout.strip():
            data = json.loads(result.stdout.strip())
            # Handle both single object and array response
            if isinstance(data, list):
                if data:
                    return data[0].get('State', data[0].get('Status', ''))
            else:
                return data.get('State', data.get('Status', ''))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        pass
    return None


def check_containers_running(
    container_prefix: str,
    scale: int,
    compose_file: Optional[Path] = None
) -> Tuple[bool, Optional[str]]:
    """
    Check if all containers are running (not exited)

    Args:
        container_prefix: Container name prefix (e.g., "dev")
        scale: Number of containers to check
        compose_file: Compose file path (optional)

    Returns:
        Tuple of (all_running: bool, error_message: Optional[str])
    """
    for i in range(1, scale + 1):
        service_name = f"{container_prefix}-{i}"
        status = get_container_status(service_name, compose_file)

        if status is None:
            return False, f"Container {service_name} not found"

        # Check if container exited
        status_lower = status.lower()
        if 'exited' in status_lower or 'dead' in status_lower:
            # Get container logs for error details
            try:
                result = docker_compose(
                    ['logs', '--tail', '10', service_name],
                    compose_file=compose_file,
                    check=False,
                    capture_output=True,
                    silent_error=True
                )
                logs = result.stdout.strip() or result.stderr.strip()
                return False, f"Container {service_name} exited unexpectedly.\nLast logs:\n{logs}"
            except Exception:
                return False, f"Container {service_name} exited unexpectedly (status: {status})"

    return True, None


def wait_for_containers_ready(
    container_prefix: str,
    scale: int,
    ready_file: str = '/tmp/entrypoint-ready',
    timeout: int = 60,
    compose_file: Optional[Path] = None
) -> bool:
    """
    Wait for all containers' entrypoint to complete

    Args:
        container_prefix: Container name prefix (e.g., "dev")
        scale: Number of containers to wait for
        ready_file: File path to check in container
        timeout: Maximum wait time in seconds
        compose_file: Compose file path (optional)

    Returns:
        True if all containers are ready

    Raises:
        DockerError: If containers fail to start or timeout
    """
    logger.info("Waiting for container entrypoint to complete...")

    waited = 0
    while waited < timeout:
        # First check if containers are still running
        all_running, error_msg = check_containers_running(
            container_prefix, scale, compose_file
        )
        if not all_running:
            raise DockerError(f"Container startup failed: {error_msg}")

        all_ready = True

        for i in range(1, scale + 1):
            service_name = f"{container_prefix}-{i}"

            # Check if ready file exists in container
            try:
                cmd = ['exec', '-T', service_name, 'test', '-f', ready_file]
                docker_compose(
                    cmd,
                    compose_file=compose_file,
                    check=True,
                    capture_output=True,
                    silent_error=True
                )
            except subprocess.CalledProcessError:
                all_ready = False
                break

        if all_ready:
            logger.info("All containers ready")
            return True

        time.sleep(1)
        waited += 1

    raise DockerError(f"Timeout ({timeout}s) waiting for containers to be ready")


def docker_compose_down(compose_file: Optional[Path] = None) -> None:
    """
    Stop and remove containers using docker compose down

    Args:
        compose_file: Compose file path (optional)
    """
    try:
        docker_compose(['down', '-t0'], compose_file=compose_file, check=True)
    except subprocess.CalledProcessError as e:
        # Don't raise exception if down fails (containers might not exist)
        if e.returncode != 0:
            logger.warning("docker compose down failed (containers might not exist)")


def docker_compose_up(
    compose_file: Optional[Path] = None,
    detach: bool = True
) -> None:
    """
    Start containers using docker compose up

    Args:
        compose_file: Compose file path (optional)
        detach: Run in detached mode
    """
    cmd = ['up']
    if detach:
        cmd.append('-d')

    docker_compose(cmd, compose_file=compose_file, check=True)


def ensure_network(network_name: str = 'devbase_net') -> None:
    """
    Ensure docker network exists, create if not

    Args:
        network_name: Network name to create/ensure (default: devbase_net)
    """
    # Check if network exists
    try:
        result = subprocess.run(
            ['docker', 'network', 'inspect', network_name],
            capture_output=True,
            check=False,
            text=True
        )
        if result.returncode == 0:
            logger.info("Network '%s' already exists", network_name)
            return
    except Exception:
        pass

    # Create network if not exists
    try:
        subprocess.run(
            ['docker', 'network', 'create', network_name],
            check=True,
            capture_output=True,
            text=True
        )
        logger.info("Created network '%s'", network_name)
    except subprocess.CalledProcessError as e:
        # Check if error is "already exists" (race condition)
        if 'already exists' in (e.stderr or ''):
            logger.info("Network '%s' already exists", network_name)
        else:
            raise DockerError(f"Failed to create network '{network_name}': {e.stderr}")
