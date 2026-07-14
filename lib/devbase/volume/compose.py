"""Docker Compose file generation for scaled deployments"""

import copy
import os
import re
import subprocess
import yaml
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set

from devbase.errors import DockerError
from devbase.env.keys import ENABLE_SSH, DEVBASE_SSH_BIND, DEVBASE_SSH_PORT_BASE

from .manager import get_work_volume_for_index, get_ai_volume_for_index
from .ports import allocate_ssh_host_port

# 旧 /home/ubuntu マウントは非推奨のため scale 生成時に除去する
_DEPRECATED_TARGET = '/home/ubuntu'

# devbase が SSH publish する dev コンテナを他 Compose プロジェクトと識別するための
# 専用ラベル。Orca 隔離 config 生成 (commands/orca.py `_parse_inspect`) が対象を
# 絞り込む必須条件として参照する。ENABLE_SSH 有効時に :22 publish と同時に付与する。
DEVBASE_SSH_LABEL = 'dev.devbase.ssh'

# dev インスタンス番号 (1..N) を保持する devbase 専用ラベル。
# generate_scaled_compose は各 dev-<index> を「別サービス」として展開するため、
# compose が付与する `com.docker.compose.container-number` は全インスタンスで 1 と
# なり index の識別に使えない。Orca 隔離 config 生成が Host 名の重複を避けられるよう、
# ENABLE_SSH 有効時に SSH ラベルと同時にこのラベルで実 index を明示する。
DEVBASE_INDEX_LABEL = 'dev.devbase.index'


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


def _add_ssh_label(service: dict, label: str, value: str = '1') -> None:
    """service へラベルを付与する (labels の dict / list どちらの形式にも対応)。"""
    labels = service.get('labels')
    if isinstance(labels, list):
        labels.append(f"{label}={value}")
    elif isinstance(labels, dict):
        labels[label] = value
    else:
        service['labels'] = {label: value}


def get_running_published_host_ports(exclude_project: Optional[str] = None) -> Set[int]:
    """稼働中コンテナが publish 済みのホストポート集合を best-effort で返す。

    別プロジェクトのコンテナが既に握っているホストポートとの衝突を避けるため、
    compose 生成時に docker から現況を収集して :func:`allocate_ssh_host_port` の
    ``used_ports`` に混ぜる。docker が無い / 失敗しても空集合を返して生成を止めない
    (その場合は決定的ポートにそのままフォールバックする)。

    ``exclude_project`` を指定すると、``com.docker.compose.project`` ラベルが一致する
    コンテナ (= 現在のプロジェクト自身の dev-1..N) のポートは集合から除外する。
    これがないと ``devbase scale`` で稼働中の自コンテナの決定的ポートまで「衝突」と
    誤判定されて +1 ずれ、``--no-recreate`` で残る実コンテナと生成 compose が不一致に
    なり (意図せぬ recreate / bind 失敗) を招くため、外部プロジェクトのポートだけを
    衝突回避シードにする。
    """
    try:
        result = subprocess.run(
            ['docker', 'ps', '--format',
             '{{.Label "com.docker.compose.project"}}\t{{.Ports}}'],
            capture_output=True, text=True, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()
    ports: Set[int] = set()
    # 例: "otherproj\t127.0.0.1:2231->22/tcp, 0.0.0.0:8080->80/tcp"
    for line in result.stdout.splitlines():
        project, _, ports_field = line.partition('\t')
        # 現在のプロジェクト自身の publish は衝突回避シードから除外する。
        if exclude_project is not None and project == exclude_project:
            continue
        for match in re.finditer(r":(\d+)->", ports_field):
            ports.add(int(match.group(1)))
    return ports


def _build_dev_instance(
    dev_service: dict, dev_service_name: str, index: int, project_name: str,
    used_ports: Set[int],
) -> dict:
    """Build the service definition for one scaled dev instance (dev-<index>).

    ``used_ports`` は既に割り当て済み / 使用中のホストポート集合。SSH publish の
    ポートを確保したら、この集合に追加して後続インスタンスとの衝突を防ぐ。
    """
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
        # 決定的ポートを優先しつつ、同一生成内の他インスタンスや他プロジェクトの
        # 稼働 publish と衝突する場合は空きポートへずらして bind 失敗を避ける。
        port = allocate_ssh_host_port(project_name, index, base, used_ports)
        used_ports.add(port)
        service.setdefault('ports', []).append(f"{bind}:{port}:22")
        # devbase の SSH publish コンテナを識別する専用ラベル (Orca 隔離の必須条件)。
        _add_ssh_label(service, DEVBASE_SSH_LABEL)
        # dev インスタンス番号を明示するラベル (compose の container-number は別サービス
        # 展開のため全て 1 になり index 識別に使えないので、実 index をここで持たせる)。
        _add_ssh_label(service, DEVBASE_INDEX_LABEL, str(index))

    return service


def _build_scaled_services(
    services: dict, dev_service: dict, dev_service_name: str, scale: int,
    project_name: str, used_ports: Optional[Set[int]] = None,
) -> dict:
    """Build the services section: non-dev services + dev-1..dev-N instances.

    ``used_ports`` は SSH publish のホストポート衝突回避に使う共有集合
    (省略時は空集合から開始)。dev-1..N の生成を通じて割り当て済みポートを蓄積する。
    """
    if used_ports is None:
        used_ports = set()
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
            dev_service, dev_service_name, i, project_name, used_ports,
        )
    return scaled_services


def generate_scaled_compose(
    scale: int,
    project_name: str,
    compose_file: Path = None,
    dev_service_name: str = None,
    external_ports_provider: Optional[Callable[[], Set[int]]] = None,
) -> Path:
    """
    Generate scaled docker-compose file with per-instance volumes

    Args:
        scale: Number of container instances
        project_name: Project name. Used for deterministic SSH port allocation
            (PLAN33) when ENABLE_SSH is set.
        compose_file: Source compose file path (default: compose.yml)
        dev_service_name: Name of the development service to scale (default: from DEV_SERVICE_NAME env or 'dev')
        external_ports_provider: 他プロジェクトが稼働 publish 済みのホストポート集合を
            返す関数 (SSH ポート衝突回避のシード)。None (既定) のときは外部ポートを
            シードしない (= 決定的ポートをそのまま使う。単体テストは docker 非依存)。
            実行時の up 経路は :func:`get_running_published_host_ports` を注入して
            他プロジェクトとの衝突を best-effort で回避する。

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

    # SSH publish のポート衝突回避シード: 呼び出し側 (up 経路) が他プロジェクトの
    # 稼働 publish ポートを注入した場合はそれを初期集合にする。既定 (None) では
    # シードせず、決定的ポートをそのまま使う (単体テストを docker 非依存に保つ)。
    used_ports: Set[int] = set(external_ports_provider()) if external_ports_provider else set()

    scaled_config = {
        'services': _build_scaled_services(
            services, dev_service, dev_service_name, scale, project_name, used_ports,
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
