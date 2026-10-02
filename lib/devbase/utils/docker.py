"""Docker command utilities for devbase"""

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from devbase.errors import DockerError
from devbase.log import get_logger

logger = get_logger("devbase.utils.docker")

#: devbase 経由の Compose へ ``COMPOSE_PROFILES`` として渡す、打ち消し用のプロファイル名
#: (PLAN58 決定 7)。どのプロジェクトも定義しない名前で、利用者の端末の値とプロジェクトの
#: ``.env`` の値をどちらも無効にする。有効なプロファイルは経路ごとに ``--profile`` で決める。
NO_PROFILE = '__devbase_none__'

#: entrypoint が処理を終えたときにコンテナ内へ置く完了の印。起動の待ちと post-start が確かめる。
ENTRYPOINT_READY_FILE = '/tmp/entrypoint-ready'


def compose_env(environ: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """devbase が ``docker compose`` を呼ぶときの子プロセスの環境を返す。

    ``environ`` (既定は ``os.environ``) の複製の ``COMPOSE_PROFILES`` を
    :data:`NO_PROFILE` にする。キーを外すだけでは足りない。Compose は環境変数が
    無ければプロジェクトの ``.env`` の値を採るため、値を入れて上書きする。空文字列に
    しないのは、空の解釈 (空の一覧 / 未設定) が版に依るかを調べずに済ませるため。
    """
    env = dict(os.environ if environ is None else environ)
    env['COMPOSE_PROFILES'] = NO_PROFILE
    return env


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
            check=check,
            env=compose_env(),
        )
        return result
    except subprocess.CalledProcessError as e:
        if not silent_error and e.stderr:
            logger.error("%s", e.stderr)
        raise


def running_dev_instances(project: str, dev_service_name: str,
                          runner=None) -> Optional[List[Tuple[int, str]]]:
    """動いている dev インスタンスの ``(番号, コンテナ名)`` を番号順に返す。

    ``up`` の構成は dev の各インスタンスをサービス ``<dev>-<n>`` として定義する
    (``volume/compose.py``)。``docker ps`` はプロジェクトのラベルで絞り、それだけでは DB や
    snapshot にも届くため、サービス名 (``<dev>-<n>``) は正規表現で選別する。``-a`` を付けないので
    止まっているコンテナは出ない。

    docker を呼べない・0 以外で終わったときは error ログを出して ``None`` を返す。
    動いているものが無い ``[]`` と区別するためで、``devbase open`` はこの区別で「停止中」と
    「状態を取得できない」を分ける (PLAN59 決定 2・8)。Compose のファイルを読まないため、
    生成物の有無と構成の補間に左右されない。接続先は環境変数 ``DOCKER_CONTEXT`` に従う。
    """
    import re

    run = runner or subprocess.run
    try:
        result = run(
            ['docker', 'ps', '--filter', f'label=com.docker.compose.project={project}',
             '--format', '{{.Names}}\t{{.Label "com.docker.compose.service"}}'],
            capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        logger.error("docker ps を実行できませんでした: %s", e)
        return None
    if result.returncode != 0:
        logger.error("docker ps が失敗しました (exit=%d): %s", result.returncode,
                     (result.stderr or '').strip())
        return None
    pattern = re.compile(rf'^{re.escape(dev_service_name)}-([1-9][0-9]*)$')
    found = []
    for line in (result.stdout or '').splitlines():
        name, _, service = line.partition('\t')
        match = pattern.match(service.strip())
        if name and match:
            found.append((int(match.group(1)), name.strip()))
    return sorted(found)


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


_STARTUP_REASON_TEXT = {
    'exited': 'exited unexpectedly',
    'not_found': 'container not found',
}


@dataclass(frozen=True)
class StartupFailure:
    """起動の待ちで起動できなかった 1 インスタンス。

    ``reason`` は ``exited``・``not_found``・``timeout`` のどれかで、``logs`` は
    ``exited`` のときだけ中身を持つ (ログの末尾。取れなければ空)。
    """

    index: int
    service: str
    reason: str
    logs: str = ''
    timeout: int = 0

    def describe(self) -> str:
        """利用者へ出す 1 インスタンス分の文言 (ログの末尾を含む)。"""
        if self.reason == 'timeout':
            text = f"timeout ({self.timeout}s) waiting for the entrypoint to complete"
        else:
            text = _STARTUP_REASON_TEXT.get(self.reason, self.reason)
        lines = [f"  - {self.service}: {text}"]
        if self.reason == 'exited' and self.logs:
            lines.append("    Last logs:")
            lines.extend(f"    {line}" for line in self.logs.splitlines())
        return '\n'.join(lines)


class ContainerStartupError(DockerError):
    """起動の待ちで、起動できなかったインスタンスが 1 つ以上あった。

    ``ready`` は起動できた番号、``failures`` は起動できなかったインスタンスで、どちらも番号の
    昇順に並び、合わせると待った番号の全部になる。
    """

    def __init__(self, ready: Sequence[int], failures: Sequence[StartupFailure]):
        self.ready: Tuple[int, ...] = tuple(sorted(ready))
        self.failures: Tuple[StartupFailure, ...] = tuple(
            sorted(failures, key=lambda f: f.index))
        total = len(self.ready) + len(self.failures)
        header = (f"Container startup failed: {len(self.failures)} of {total} "
                  f"instances did not become ready.")
        super().__init__('\n'.join([header] + [f.describe() for f in self.failures]))


def _container_logs_tail(service_name: str, compose_file: Optional[Path]) -> str:
    """終了したコンテナのログの末尾 (10 行まで)。取れなければ空。"""
    try:
        result = docker_compose(
            ['logs', '--tail', '10', service_name],
            compose_file=compose_file,
            check=False,
            capture_output=True,
            silent_error=True
        )
    except (OSError, subprocess.SubprocessError):
        return ''  # ログが取れなくても、起動できなかったことは伝える
    return (result.stdout or '').strip() or (result.stderr or '').strip()


def _is_ready(service_name: str, ready_file: str, compose_file: Optional[Path]) -> bool:
    try:
        docker_compose(
            ['exec', '-T', service_name, 'test', '-f', ready_file],
            compose_file=compose_file,
            check=True,
            capture_output=True,
            silent_error=True
        )
    except subprocess.CalledProcessError:
        return False
    return True


def wait_for_containers_ready(
    container_prefix: str,
    scale: int,
    ready_file: str = ENTRYPOINT_READY_FILE,
    timeout: int = 60,
    compose_file: Optional[Path] = None
) -> bool:
    """
    Wait for all containers' entrypoint to complete

    1 から ``scale`` までの番号を巡ごとに確かめる。1 巡では、まだ決まっていない番号ごとに
    状態を 1 回、動いていれば完了の印を 1 回確かめる。決まった番号 (起動できた・終了した・
    見つからない) へは、その後の巡で問い合わせない。1 台が起動できなくても、残りの番号は
    制限まで待つ。巡の数は ``timeout`` を超えない (1 巡ごとに 1 秒休む)。

    Args:
        container_prefix: Container name prefix (e.g., "dev")
        scale: Number of containers to wait for
        ready_file: File path to check in container
        timeout: Maximum number of 1-second rounds
        compose_file: Compose file path (optional)

    Returns:
        True if all containers are ready

    Raises:
        ContainerStartupError: 起動できなかった番号が 1 つ以上あったとき。起動できた番号と、
            起動できなかった番号ごとの理由 (終了した・見つからない・時間切れ) を持つ
    """
    logger.info("Waiting for container entrypoint to complete...")

    pending = list(range(1, scale + 1))
    ready: List[int] = []
    failures: List[StartupFailure] = []
    rounds = 0
    while pending and rounds < timeout:
        still_pending = []
        for i in pending:
            service_name = f"{container_prefix}-{i}"
            status = get_container_status(service_name, compose_file)
            if status is None:
                failures.append(StartupFailure(i, service_name, 'not_found'))
                continue
            status_lower = status.lower()
            if 'exited' in status_lower or 'dead' in status_lower:
                failures.append(StartupFailure(
                    i, service_name, 'exited',
                    _container_logs_tail(service_name, compose_file)))
                continue
            if _is_ready(service_name, ready_file, compose_file):
                ready.append(i)
            else:
                still_pending.append(i)
        pending = still_pending
        if not pending:
            break
        time.sleep(1)
        rounds += 1

    failures.extend(
        StartupFailure(i, f"{container_prefix}-{i}", 'timeout', timeout=timeout)
        for i in pending)
    if failures:
        raise ContainerStartupError(ready, failures)
    logger.info("All containers ready")
    return True


def docker_compose_down(compose_file: Optional[Path] = None) -> None:
    """
    Stop and remove containers using docker compose down

    プロファイルのサービスも対象にするため ``--profile '*'`` を付ける (PLAN58 決定 4)。
    付けないと、プロファイル付きのサービスが動いたまま残り、network の削除にも失敗する。

    Args:
        compose_file: Compose file path (optional)
    """
    try:
        docker_compose(['--profile', '*', 'down', '-t0'], compose_file=compose_file, check=True)
    except subprocess.CalledProcessError as e:
        # Don't raise exception if down fails (containers might not exist)
        if e.returncode != 0:
            logger.warning("docker compose down failed (containers might not exist)")


def docker_compose_up(
    compose_file: Optional[Path] = None,
    detach: bool = True,
    services: Sequence[str] = (),
) -> None:
    """
    Start containers using docker compose up

    Args:
        compose_file: Compose file path (optional)
        detach: Run in detached mode
        services: 起動の対象。``devbase up`` は既定のサービスをすべて渡す (PLAN58 決定 7)。
            空なら従来どおりサービス名を付けない
    """
    cmd = ['up']
    if detach:
        cmd.append('-d')
    cmd.extend(services)

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
