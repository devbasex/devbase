"""Docker Compose file generation for scaled deployments"""

import copy
import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional

from devbase.errors import DockerError
from devbase.env.keys import ENABLE_SSH, DEVBASE_SSH_BIND, DEVBASE_SSH_PORT_BASE

from .manager import get_work_volume_for_index, get_ai_volume_for_index
from .ports import ssh_host_port

# 旧 /home/ubuntu マウントは非推奨のため scale 生成時に除去する
_DEPRECATED_TARGET = '/home/ubuntu'


def get_dev_service_name() -> str:
    """Get development service name from environment variable or default to 'dev'"""
    return os.environ.get('DEV_SERVICE_NAME', 'dev')


def _rewrite_depends_on(
    service_config: Dict[str, Any],
    dev_service_name: str,
    scale: int,
) -> None:
    """Rewrite `depends_on: <dev>` references to scaled instances (dev-1, ..., dev-N).

    Supports both list form (`depends_on: [dev, mysql]`) and map form
    (`depends_on: {dev: {condition: service_healthy}}`). For scale > 1 a
    single `dev` reference is expanded to every dev-i instance so that the
    dependent service waits for all of them.
    """
    deps = service_config.get('depends_on')
    if not deps:
        return

    instance_names = [f"{dev_service_name}-{i}" for i in range(1, scale + 1)]

    if isinstance(deps, list):
        service_config['depends_on'] = [
            name
            for dep in deps
            for name in (instance_names if dep == dev_service_name else [dep])
        ]
    elif isinstance(deps, dict) and dev_service_name in deps:
        condition = deps.pop(dev_service_name)
        for name in instance_names:
            deps[name] = copy.deepcopy(condition)


def _volume_target(vol: Any) -> Optional[str]:
    """Return the mount target of a volume entry (string / dict form), or None."""
    if isinstance(vol, str):
        parts = vol.split(':')
        return parts[1] if len(parts) >= 2 else None
    if isinstance(vol, dict):
        return vol.get('target')
    return None


def _replace_volumes_for_instance(
    volumes: list, ai_volume: str, work_volume: str,
) -> list:
    """Replace volume mounts in a service's volumes list for a specific instance.

    /home/ubuntu mounts are skipped (deprecated).
    /persistent/ai is mapped to ai_volume.
    /work is mapped to work_volume.
    """
    replacements = {'/persistent/ai': ai_volume, '/work': work_volume}
    replaced_targets = set()
    new_volumes = []

    for vol in volumes:
        target = _volume_target(vol)
        if target == _DEPRECATED_TARGET:
            continue
        source = replacements.get(target)
        if source is None:
            new_volumes.append(vol)
            continue
        replaced_targets.add(target)
        if isinstance(vol, str):
            # String format: "source:target" or "source:target:options"
            parts = vol.split(':')
            options = f":{parts[2]}" if len(parts) >= 3 else ""
            new_volumes.append(f"{source}:{target}{options}")
        else:
            # Dict format: {type, source, target}
            vol['source'] = source
            vol['type'] = 'volume'
            new_volumes.append(vol)

    # Add missing mounts
    new_volumes.extend(
        f"{source}:{target}"
        for target, source in replacements.items()
        if target not in replaced_targets
    )
    return new_volumes


def _build_volumes_section(config: dict, scale: int) -> dict:
    """Build the volumes section for a scaled compose file."""
    # Copy original volumes (mysql, valkey, etc.) from config
    volumes: Dict[str, Any] = {
        vol_name: copy.deepcopy(vol_config) if vol_config else {}
        for vol_name, vol_config in config.get('volumes', {}).items()
    }

    # Add shared home volume (devbase_home_ubuntu) once for all instances
    volumes[get_ai_volume_for_index(1)] = {'external': True}

    # Add work volumes for each dev instance (external)
    for i in range(1, scale + 1):
        volumes[get_work_volume_for_index(i)] = {'external': True}

    return volumes


def _build_networks_section(config: dict) -> dict:
    """Build the networks section for a scaled compose file."""
    if 'networks' in config:
        return config['networks']
    return {'net': {'driver': 'bridge'}}


def _load_compose_config(compose_file: Path) -> dict:
    """Read and parse the base compose file.

    docker compose config を使わず直接読むのは、環境変数 (secrets を含み得る) の
    展開を避けるため。
    """
    if not compose_file.exists():
        raise FileNotFoundError(f"Compose file not found: {compose_file}")
    try:
        with open(compose_file, 'r') as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise DockerError(f"Failed to parse compose file: {e}")


def _build_dev_instance(
    dev_service: dict, dev_service_name: str, index: int, project_name: str,
) -> dict:
    """Build the service definition for one scaled dev instance (dev-<index>)."""
    service = copy.deepcopy(dev_service)
    service['container_name'] = f"${{COMPOSE_PROJECT_NAME}}-{dev_service_name}-{index}"

    # Insert tini as PID 1 so orphaned children are reaped (no zombies).
    # setdefault keeps an explicit `init: false` if the project set one.
    service.setdefault('init', True)

    # Remove environment section (use env_file instead to avoid exposing secrets)
    service.pop('environment', None)

    # Update volume mounts for /persistent/ai and /work
    ai_volume = get_ai_volume_for_index(index)
    work_volume = get_work_volume_for_index(index)
    service['volumes'] = _replace_volumes_for_instance(
        service.get('volumes', []), ai_volume, work_volume,
    )

    # Publish the container's sshd (:22) to a deterministic host port so Orca
    # can attach as a plain SSH host (PLAN33). Opt-in via ENABLE_SSH.
    if os.environ.get(ENABLE_SSH, '').lower() in ('true', '1'):
        bind = os.environ.get(DEVBASE_SSH_BIND, '127.0.0.1')
        base = int(os.environ.get(DEVBASE_SSH_PORT_BASE, '2200'))
        port = ssh_host_port(project_name, index, base)
        service.setdefault('ports', []).append(f"{bind}:{port}:22")

    return service


def _build_scaled_services(
    services: dict, dev_service: dict, dev_service_name: str, scale: int,
    project_name: str,
) -> dict:
    """Build the services section: non-dev services + dev-1..dev-N instances."""
    scaled_services = {}

    # Copy non-dev services (mysql, valkey, etc.) — rewriting any
    # `depends_on: <dev>` reference to the scaled instances (dev-1..N) so
    # service_healthy chains keep working after dev is renamed.
    for service_name, service_config in services.items():
        if service_name == dev_service_name:
            continue
        copied = copy.deepcopy(service_config)
        _rewrite_depends_on(copied, dev_service_name, scale)
        # Insert tini as PID 1 so orphaned children are reaped (no zombies).
        # setdefault keeps an explicit `init: false` if the project set one.
        copied.setdefault('init', True)
        scaled_services[service_name] = copied

    # Generate a service for each instance
    for i in range(1, scale + 1):
        scaled_services[f'{dev_service_name}-{i}'] = _build_dev_instance(
            dev_service, dev_service_name, i, project_name,
        )
    return scaled_services


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
        project_name: Project name. Used for deterministic SSH port allocation
            (PLAN33) when ENABLE_SSH is set.
        compose_file: Source compose file path (default: compose.yml)
        dev_service_name: Name of the development service to scale (default: from DEV_SERVICE_NAME env or 'dev')

    Returns:
        Path to generated .docker-compose.scale.yml
    """
    compose_file = compose_file or Path("compose.yml")
    override_file = Path(".docker-compose.scale.yml")
    if dev_service_name is None:
        dev_service_name = get_dev_service_name()

    config = _load_compose_config(compose_file)

    # Extract dev service (configurable via DEV_SERVICE_NAME)
    services = config.get('services', {})
    dev_service = services.get(dev_service_name)
    if not dev_service:
        raise DockerError(f"No '{dev_service_name}' service found in compose file")

    scaled_config = {
        'services': _build_scaled_services(
            services, dev_service, dev_service_name, scale, project_name,
        ),
        'volumes': _build_volumes_section(config, scale),
        'networks': _build_networks_section(config),
    }

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
