"""Docker Compose file generation for scaled deployments"""

import os
import yaml
from pathlib import Path
from typing import Any, Dict

from devbase.errors import DockerError

from .manager import get_work_volume_for_index, get_ai_volume_for_index


def get_dev_service_name() -> str:
    """Get development service name from environment variable or default to 'dev'"""
    return os.environ.get('DEV_SERVICE_NAME', 'dev')


def _deep_copy(obj: Any) -> Any:
    """Deep copy an object (handles dicts, lists, and primitives)"""
    if isinstance(obj, dict):
        return {k: _deep_copy(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_deep_copy(item) for item in obj]
    else:
        return obj


def _replace_volumes_for_instance(
    volumes: list, ai_volume: str, work_volume: str,
) -> list:
    """Replace volume mounts in a service's volumes list for a specific instance.

    /home/ubuntu mounts are skipped (deprecated).
    /persistent/ai is mapped to ai_volume.
    /work is mapped to work_volume.
    """
    new_volumes = []
    has_ai_mount = False
    has_work_mount = False

    for vol in volumes:
        if isinstance(vol, str):
            # String format: "source:target" or "source:target:options"
            parts = vol.split(':')
            if len(parts) >= 2:
                target = parts[1]
                if target == '/home/ubuntu':
                    continue
                elif target == '/persistent/ai':
                    new_vol = f"{ai_volume}:/persistent/ai"
                    if len(parts) >= 3:
                        new_vol += f":{parts[2]}"
                    new_volumes.append(new_vol)
                    has_ai_mount = True
                elif target == '/work':
                    new_vol = f"{work_volume}:/work"
                    if len(parts) >= 3:
                        new_vol += f":{parts[2]}"
                    new_volumes.append(new_vol)
                    has_work_mount = True
                else:
                    new_volumes.append(vol)
            else:
                new_volumes.append(vol)
        elif isinstance(vol, dict):
            # Dict format: {type, source, target}
            target = vol.get('target')
            if target == '/home/ubuntu':
                continue
            elif target == '/persistent/ai':
                vol['source'] = ai_volume
                vol['type'] = 'volume'
                has_ai_mount = True
                new_volumes.append(vol)
            elif target == '/work':
                vol['source'] = work_volume
                vol['type'] = 'volume'
                has_work_mount = True
                new_volumes.append(vol)
            else:
                new_volumes.append(vol)
        else:
            new_volumes.append(vol)

    # Add missing mounts
    if not has_ai_mount:
        new_volumes.append(f"{ai_volume}:/persistent/ai")
    if not has_work_mount:
        new_volumes.append(f"{work_volume}:/work")

    return new_volumes


def _build_volumes_section(config: dict, scale: int) -> dict:
    """Build the volumes section for a scaled compose file."""
    volumes: Dict[str, Any] = {}

    # Copy original volumes (mysql, valkey, etc.) from config
    if 'volumes' in config:
        for vol_name, vol_config in config['volumes'].items():
            volumes[vol_name] = _deep_copy(vol_config) if vol_config else {}

    # Add shared home volume (devbase_home_ubuntu) once for all instances
    home_volume = get_ai_volume_for_index(1)
    volumes[home_volume] = {'external': True}

    # Add work volumes for each dev instance (external)
    for i in range(1, scale + 1):
        work_volume = get_work_volume_for_index(i)
        volumes[work_volume] = {'external': True}

    return volumes


def _build_networks_section(config: dict) -> dict:
    """Build the networks section for a scaled compose file."""
    if 'networks' in config:
        return config['networks']
    return {'net': {'driver': 'bridge'}}


def generate_scaled_compose(
    scale: int,
    project_name: str,
    compose_file: Path = None,
    dev_service_name: str = None
) -> Path:
    """
    Generate scaled docker-compose file with per-instance volumes

    Args:
        scale: Number of container instances
        project_name: Project name (unused, kept for backward compatibility)
        compose_file: Source compose file path (default: compose.yml)
        dev_service_name: Name of the development service to scale (default: from DEV_SERVICE_NAME env or 'dev')

    Returns:
        Path to generated .docker-compose.scale.yml
    """
    compose_file = compose_file or Path("compose.yml")
    override_file = Path(".docker-compose.scale.yml")

    # Get development service name from parameter, environment variable, or default
    if dev_service_name is None:
        dev_service_name = get_dev_service_name()

    if not compose_file.exists():
        raise FileNotFoundError(f"Compose file not found: {compose_file}")

    # Read base compose configuration directly (not using docker compose config
    # to avoid expanding environment variables which may contain secrets)
    try:
        with open(compose_file, 'r') as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise DockerError(f"Failed to parse compose file: {e}")

    # Extract dev service (configurable via DEV_SERVICE_NAME)
    services = config.get('services', {})
    dev_service = services.get(dev_service_name)
    if not dev_service:
        raise DockerError(f"No '{dev_service_name}' service found in compose file")

    # Build scaled compose
    scaled_config = {'services': {}}

    # Copy non-dev services (mysql, valkey, etc.) as-is
    for service_name, service_config in services.items():
        if service_name != dev_service_name:
            scaled_config['services'][service_name] = _deep_copy(service_config)

    # Generate a service for each instance
    for i in range(1, scale + 1):
        ai_volume = get_ai_volume_for_index(i)
        work_volume = get_work_volume_for_index(i)

        # Clone dev service
        service = _deep_copy(dev_service)

        # Update container name
        service['container_name'] = f"${{COMPOSE_PROJECT_NAME}}-{dev_service_name}-{i}"

        # Remove environment section (use env_file instead to avoid exposing secrets)
        if 'environment' in service:
            del service['environment']

        # Update volume mounts for /persistent/ai and /work
        if 'volumes' in service:
            service['volumes'] = _replace_volumes_for_instance(
                service['volumes'], ai_volume, work_volume,
            )
        else:
            # No volumes section, create one
            service['volumes'] = [
                f"{ai_volume}:/persistent/ai",
                f"{work_volume}:/work"
            ]

        scaled_config['services'][f'{dev_service_name}-{i}'] = service

    # Add volumes and networks sections
    scaled_config['volumes'] = _build_volumes_section(config, scale)
    scaled_config['networks'] = _build_networks_section(config)

    # Write scaled compose file
    try:
        with open(override_file, 'w') as f:
            yaml.dump(
                scaled_config,
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True
            )
    except IOError as e:
        raise DockerError(f"Failed to write {override_file}: {e}")

    return override_file
