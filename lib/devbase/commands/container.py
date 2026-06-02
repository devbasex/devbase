"""Container lifecycle commands (up, down, ps, login, logs, scale, build)"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
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


def _run_pre_up_hook() -> bool:
    """`./pre-up` フックがあればコンテナ起動前に実行する。

    ビルドコンテキスト用のリポジトリ clone など、`docker compose up` より前に
    完了しておく必要のある準備処理をプロジェクト側で記述するためのフック。

    Returns:
        True: フックが存在しなかった、または成功した
        False: フックが失敗した（呼び出し側で `cmd_up` を中断する）
    """
    pre_up_script = Path('./pre-up')
    if not (pre_up_script.exists() and pre_up_script.is_file()):
        return True

    logger.info("Running pre-up hook: %s", pre_up_script)
    try:
        subprocess.run(['bash', str(pre_up_script)], check=True, env=os.environ.copy())
        return True
    except subprocess.CalledProcessError as e:
        logger.error("pre-up hook failed (exit code %d)", e.returncode)
        return False


# ---------------------------------------------------------------------------
# ディスパッチャ
# ---------------------------------------------------------------------------

def _projects_dir() -> Optional[Path]:
    """$DEVBASE_ROOT/projects を返す。DEVBASE_ROOT 未設定なら None。"""
    root = os.environ.get('DEVBASE_ROOT')
    if not root:
        return None
    return Path(root) / 'projects'


# 候補一覧に表示するプロジェクト数の上限。多数プロジェクト環境で iterdir 全件を
# 出力すると 1 行が極端に長くなるため、先頭 N 件 + 「... 他 M 件」で truncate する。
_MAX_PROJECT_CANDIDATES = 20


def _report_unknown_project(name: str, projects_dir: Path) -> None:
    """存在しない project name に対するエラーと候補一覧を出力する。

    候補が多数の場合は先頭 ``_MAX_PROJECT_CANDIDATES`` 件のみ表示し、残りは
    「... 他 M 件」と省略する。
    """
    logger.error("プロジェクト '%s' が見つかりません (%s 配下に存在しません)。",
                 name, projects_dir)
    try:
        candidates = sorted(
            p.name for p in projects_dir.iterdir()
            if p.is_dir() or p.is_symlink()
        )
    except OSError:
        candidates = []
    if candidates:
        total = len(candidates)
        shown = candidates[:_MAX_PROJECT_CANDIDATES]
        listing = ', '.join(shown)
        if total > _MAX_PROJECT_CANDIDATES:
            listing += f', ... 他 {total - _MAX_PROJECT_CANDIDATES} 件'
        logger.error("利用可能なプロジェクト: %s", listing)


def _load_project_env(env_file: Path) -> None:
    """プロジェクトの ``env`` ファイルを os.environ へ反映する (wrapper 同等)。

    wrapper (bin/devbase) は cd 後に ``source ./env`` で env を読み込むため、
    Python フォールバック経路でも同じ KEY=VALUE を ``os.environ`` に載せて
    変数欠落 (例: project 固有の ``CONTAINER_SCALE``) を防ぐ。

    env は環境変数定義のみを想定したファイル (bin/devbase 冒頭コメント参照) の
    ため、ここでは ``export`` 接頭辞付き / 無しの単純な ``KEY=VALUE`` 行のみを
    解釈する。``#`` コメント・空行は無視し、値の前後のクォートは除去する。shell
    の変数展開やコマンド置換は意図的にサポートしない (安全側に倒す)。

    .. note:: shell ``source`` との仕様乖離について

       本パーサは完全な POSIX shell パーサではなく、shell ``source ./env``
       (wrapper 経路) とは以下のケースで挙動が乖離する。env は単純な
       ``KEY=VALUE`` 定義に限定する運用前提のため、これらは意図的な制約として
       受容し、ファイル側で利用しない方針とする (仕様統一ではなく制約の明示)::

         FOO=$BAR        # shell: 展開 → 本実装: リテラル文字列 "$BAR"
         FOO=$(cmd)      # shell: コマンド置換 → 本実装: リテラル "$(cmd)"
         FOO=a"b"c       # shell: クォート除去で "abc" → 本実装: 行頭/行末以外の
                         #        クォートは除去せず "a\"b\"c"
         FOO=bar # x     # shell: インラインコメント無効 (値は "bar # x") →
                         #        本実装も値は "bar # x" (行頭 # のみコメント扱い)

       いずれも wrapper を経ない直接起動 (例:
       ``python -m devbase.cli project up <name>``) のフォールバック時のみ影響し、
       通常運用の wrapper 経路では shell が env を解釈するため差異は生じない。
    """
    if not env_file.is_file():
        return
    try:
        lines = env_file.read_text().splitlines()
    except OSError as e:
        logger.warning("env ファイルを読み込めませんでした (%s): %s", env_file, e)
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ[key] = value


def _resolve_project_name(project_name: str) -> bool:
    """project name を $DEVBASE_ROOT/projects/<name> へ解決し chdir する。

    通常は wrapper (bin/devbase) が起動前に cd 済みのため、ここは

      - `python -m devbase.cli project up <name>` の直接起動
      - wrapper を経ない経路 (`_ensure_env_files` 等)

    に対する防御的フォールバックとして働く。wrapper が既に対象ディレクトリへ
    cd 済みなら chdir は no-op になる (同一パス判定)。

    chdir 後は wrapper の ``source ./env`` と同等に project の ``env`` を
    ``os.environ`` へ反映し、wrapper を経ない直接起動でも環境変数が欠落しない
    ようにする (gemini round2 minor 指摘対応)。

    Returns:
        True:  解決成功 (または既に対象ディレクトリにいる)
        False: DEVBASE_ROOT 未設定 / 対象が存在しない (呼び出し側で return 1)
    """
    projects_dir = _projects_dir()
    if projects_dir is None:
        logger.error("DEVBASE_ROOT が未設定のため project name '%s' を解決できません。",
                     project_name)
        return False

    target = projects_dir / project_name
    if not target.is_dir():
        _report_unknown_project(project_name, projects_dir)
        return False

    try:
        already_there = target.resolve() == Path.cwd().resolve()
    except OSError:
        already_there = False
    if not already_there:
        os.chdir(target)

    # wrapper の `source ./env` と同等に project env を os.environ へ反映する。
    # wrapper 経由なら既に同じ値が載っているため冪等。
    _load_project_env(Path('env'))

    # COMPOSE_PROJECT_NAME を name で上書き (wrapper が設定済みでも冪等)。
    # env 由来の COMPOSE_PROJECT_NAME より name 指定を優先するため env 反映後に行う。
    os.environ['COMPOSE_PROJECT_NAME'] = project_name
    return True


def _dispatch_lifecycle(args) -> int:
    """`project` / `container` 共有のサブコマンドディスパッチャ。

    `project <sub> [name]` の `name` を解決して project_name へ畳み込む。
    `container` 経路には `name` 属性が無いため従来通り None になる。

    name 指定時は handler 呼び出し前に一括で `$DEVBASE_ROOT/projects/<name>` へ
    chdir する (PLAN06 方針 A の Python 側フォールバック)。chdir を各 handler に
    散らさずここで実施するのは、`cmd_down()` / `cmd_login()` / `cmd_logs()` 等が
    project_name 引数を取らず、per-handler 実装では down/login/logs で名前解決が
    効かなくなるため。build は wrapper の shell 実装で CWD 実行されるため、この
    Python フォールバックの対象外 (name 属性も持たない)。
    """
    subcmd = getattr(args, 'subcommand', None)
    project_name = getattr(args, 'name', None) or getattr(args, 'project_name', None)

    # name 指定時はディレクトリを解決して chdir する。解決失敗 (DEVBASE_ROOT 未設定
    # / 存在しない name) は候補提示の上でエラー終了する。
    if project_name:
        if not _resolve_project_name(project_name):
            return 1

    handlers = {
        'up':    lambda: cmd_up(project_name=project_name,
                                scale=getattr(args, 'scale', None)),
        'down':  lambda: cmd_down(),
        'login': lambda: cmd_login(index=getattr(args, 'index', '1')),
        'ps':    lambda: cmd_ps(all_containers=getattr(args, 'all', False)),
        'logs':  lambda: cmd_logs(follow=getattr(args, 'follow', False),
                                  tail=getattr(args, 'tail', None)),
        'scale': lambda: cmd_scale(new_scale=getattr(args, 'new_scale', None),
                                   project_name=project_name),
        'build': lambda: cmd_build(image=getattr(args, 'image', None)),
    }

    handler = handlers.get(subcmd)
    if handler:
        return handler()

    logger.error("サブコマンドを指定してください: %s", ', '.join(handlers))
    return 1


def cmd_project(args) -> int:
    """`devbase project <sub> [name]` ディスパッチャ (推奨エントリ)。"""
    return _dispatch_lifecycle(args)


def cmd_container(args) -> int:
    """`devbase container <sub>` ディスパッチャ。

    非推奨: `devbase project` に移行してください (移行期間後に削除予定)。
    挙動は `cmd_project` と同一で、警告のみ追加する。
    """
    logger.warning(
        "`devbase container` は非推奨です。`devbase project` を使用してください "
        "(将来のリリースで削除されます)。"
    )
    return _dispatch_lifecycle(args)


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

    # Pre-step: Run ./pre-up hook (e.g. clone source repos used as build contexts)
    if not _run_pre_up_hook():
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
        → rebuild with `--no-cache` (uses image 'Created' timestamp)
      - Image present + image-only + last-pull >= threshold days old
        → run `docker pull` (uses local touch-file mtime, since image
        'Created' reflects upstream build time and is not a meaningful
        local-freshness signal)
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
            ok = _run_pull(image_name)
            if ok:
                _mark_pulled(image_name)
            return ok

        max_age = _image_max_age_days()

        # Image-only services: use local touch-file mtime, since image
        # 'Created' reflects upstream build time, not local pull time.
        if not has_build:
            pull_age = _pull_age_days(image_name)
            if pull_age is None:
                # Pre-existing image with no marker (e.g., upgrade from a
                # devbase version without touch-file tracking). Bootstrap a
                # marker now so future runs can apply the threshold. We do
                # not auto-pull here to avoid surprising network calls on
                # the first `up` after upgrade.
                logger.info(
                    "First time tracking image '%s'; recording marker (no pull this run)",
                    image_name
                )
                _mark_pulled(image_name)
                return True
            if pull_age < max_age:
                return True
            logger.info(
                "Image '%s' last pulled %d days ago (>= %d days threshold), re-pulling...",
                image_name, pull_age, max_age
            )
            ok = _run_pull(image_name)
            if ok:
                _mark_pulled(image_name)
            return ok

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


def _pull_marker_path(image_name: str) -> Optional[Path]:
    """Path of the touch-file recording the last pull time of `image_name`.

    Filename format: ``<sanitized>--<sha12>`` to keep the human-readable part
    while preventing collisions between distinct image references that
    sanitize to the same string (e.g., ``a/b:c`` vs ``a_b/c``).

    Returns None when DEVBASE_ROOT is not set so callers can no-op safely.
    """
    devbase_root = os.environ.get('DEVBASE_ROOT')
    if not devbase_root:
        return None
    sanitized = re.sub(r'[^A-Za-z0-9._-]', '_', image_name)[:60]
    digest = hashlib.sha256(image_name.encode('utf-8')).hexdigest()[:12]
    return Path(devbase_root) / '.cache' / 'pulls' / f'{sanitized}--{digest}'


def _pull_age_days(image_name: str) -> Optional[int]:
    """Days since the last successful pull of `image_name`. None if never.

    Negative ages (clock skew or future-dated marker) are clamped to 0 with
    a warning so they do not silently suppress refresh forever.
    """
    marker = _pull_marker_path(image_name)
    if marker is None or not marker.exists():
        return None
    delta = time.time() - marker.stat().st_mtime
    if delta < 0:
        logger.warning(
            "Pull marker for '%s' has a future mtime (clock skew?); treating as 0 days",
            image_name
        )
        return 0
    return int(delta / 86400)


def _mark_pulled(image_name: str) -> None:
    """Touch the marker file to record a successful pull."""
    marker = _pull_marker_path(image_name)
    if marker is None:
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError as e:
        logger.warning("Could not write pull marker for '%s': %s", image_name, e)


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
