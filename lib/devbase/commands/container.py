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
from devbase.volume.compose import (
    generate_scaled_compose,
    get_dev_service_name,
    _running_published_host_ports,
)
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


def _parse_env_assignment(raw_line: str) -> Optional[tuple[str, str]]:
    """env ファイルの 1 行を ``(key, raw_value)`` にパースする。

    パース規則 (wrapper の env_var_keys とも揃える): 行頭空白除去 → 先頭 ``#`` は
    コメント → ``export`` 接頭辞除去 → ``=`` の左辺をキーとして採用。
    コメント / 空行 / 代入でない行 / キーが空の行は None。
    """
    line = raw_line.strip()
    if not line or line.startswith('#'):
        return None
    if line.startswith('export '):
        line = line[len('export '):].lstrip()
    if '=' not in line:
        return None
    key, value = line.split('=', 1)
    key = key.strip()
    return (key, value) if key else None


def _env_var_keys(env_file: Path) -> set:
    """env ファイルが定義する変数キー名の集合を返す (値は読まない)。

    project 切替時に「呼び出し元プロジェクト固有の env キー」を unset するために
    使う。パース前提は :func:`_load_project_env` と同一 (:func:`_parse_env_assignment`
    を共有)。
    """
    if not env_file.is_file():
        return set()
    try:
        lines = env_file.read_text().splitlines()
    except OSError:
        return set()
    return {
        assignment[0]
        for assignment in map(_parse_env_assignment, lines)
        if assignment
    }


# env 値中の変数参照を shell `source ./env` 相当に展開する。
# - `$VAR` / `${VAR}` を environ から展開 (未定義は空文字 = shell source 準拠)
# - `\$` はリテラル `$` にデエスケープ (shell の `\$` と同じ。EnvFile が `$` を
#   保護するため書く `\$` 付き値を壊さない)
# - `$(...)` 等 変数名にならない `$` は素通し (コマンド置換は非対応のまま)
_ENV_VAR_REF = re.compile(r'\\\$|\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)')


def _expand_env_vars(value: str, environ) -> str:
    def _repl(m):
        if m.group(0) == '\\$':
            return '$'
        name = m.group(1) or m.group(2)
        return environ.get(name, '')
    return _ENV_VAR_REF.sub(_repl, value)


def _load_project_env(env_file: Path) -> None:
    """プロジェクトの ``env`` ファイルを os.environ へ反映する (wrapper 同等)。

    wrapper (bin/devbase) は cd 後に ``source ./env`` で env を読み込むため、
    Python フォールバック経路でも同じ KEY=VALUE を ``os.environ`` に載せて
    変数欠落 (例: project 固有の ``CONTAINER_SCALE``) を防ぐ。

    env は環境変数定義のみを想定したファイル (bin/devbase 冒頭コメント参照) の
    ため、ここでは ``export`` 接頭辞付き / 無しの単純な ``KEY=VALUE`` 行のみを
    解釈する。``#`` コメント・空行は無視し、値の前後のクォートは除去する。

    変数参照 (``$VAR`` / ``${VAR}``) は shell ``source ./env`` (wrapper 経路) と
    同様に展開する。実 env が ``WORK_DIR=/work/$GIT_REPO`` のように同一ファイル内で
    先に定義した変数を参照しており、展開しないと TUI (``list``) 経路でワークスペース
    パスが ``$GIT_REPO`` 等の未展開文字列のまま VS Code で開いてしまうため
    (行は file 順に ``os.environ`` へ載せるので、参照時には先行行の値が解決済み)。
    単一引用符 ``'...'`` の値は shell 同様リテラル扱いで展開しない。

    .. note:: shell ``source`` との仕様乖離について

       本パーサは完全な POSIX shell パーサではなく、変数展開はサポートするが
       以下のケースでは shell ``source ./env`` と挙動が乖離する。env は単純な
       ``KEY=VALUE`` 定義に限定する運用前提のため、これらは意図的な制約として
       受容する (仕様統一ではなく制約の明示)::

         FOO=$(cmd)      # shell: コマンド置換 → 本実装: リテラル "$(cmd)"
                         #        (_expand_env_vars は $(...) を変数とみなさず素通し)
         FOO=a"b"c       # shell: クォート除去で "abc" → 本実装: 行頭/行末以外の
                         #        クォートは除去せず "a\"b\"c"
         FOO=bar # x     # shell: インラインコメント有効 (値は "bar") →
                         #        本実装: 行頭 # のみコメント扱いのため値は "bar # x"

       いずれも wrapper を経ない直接起動 (例:
       ``python -m devbase.cli project up <name>`` / TUI ``list``) 経路で用いる。
       通常の wrapper 経路では shell が env を解釈する。
    """
    if not env_file.is_file():
        return
    try:
        lines = env_file.read_text().splitlines()
    except OSError as e:
        logger.warning("env ファイルを読み込めませんでした (%s): %s", env_file, e)
        return
    for assignment in map(_parse_env_assignment, lines):
        if not assignment:
            continue
        key, value = assignment
        value = value.strip()
        single_quoted = False
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            single_quoted = value[0] == "'"
            value = value[1:-1]
        # shell `source ./env` 相当の変数展開 ($VAR / ${VAR}) を行う。実 env は
        # `WORK_DIR=/work/$GIT_REPO` のように同一ファイル内で先に定義した変数を
        # 参照しており (行順に os.environ へ載せるため参照時には解決済み)、展開
        # しないと TUI (list) 経路でワークスペースパスが未展開のまま開いてしまう。
        # 単一引用符はリテラル ($BAR を展開しない) という shell 規則に合わせ、
        # `'...'` の場合のみ展開しない。展開は _expand_env_vars に委ね、`$VAR` /
        # `${VAR}` のみ展開し (未定義は空文字 = shell source 準拠)、`\$` はリテラル
        # `$` にデエスケープする (shell の `\$` と同じ。EnvFile が `$` を保護する
        # ため書く `\$` 付き値を壊さない)。`$(...)` 等 変数名にならない `$` は素通し
        # するため、コマンド置換は従来どおりリテラルのまま残る。
        if not single_quoted:
            value = _expand_env_vars(value, os.environ)
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

    # chdir 前に呼び出し元 (現 CWD) の env が定義するキーを記録しておく。
    # 別プロジェクトから `project up other` を直接起動した場合、呼び出し元 env に
    # しか無いキー (例: DEV_SERVICE_NAME) が os.environ に残留し対象へ誤って
    # 引き継がれるため、対象 env を読む前に unset してクリーンにする
    # (codex 指摘 / wrapper の _CALLER_ENV_KEYS と同等のフォールバック)。
    # already_there (= 既に対象ディレクトリ。通常 wrapper 経由) の場合は呼び出し元
    # ＝対象であり、wrapper 側で既にクリーン化済みのため何もしない。
    caller_env_keys: set = set()
    if not already_there:
        caller_env_keys = _env_var_keys(Path('env'))
        os.chdir(target)
        target_env_keys = _env_var_keys(Path('env'))
        for key in caller_env_keys - target_env_keys:
            os.environ.pop(key, None)

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
                                scale=getattr(args, 'scale', None),
                                open_editor=getattr(args, 'open_editor', None),
                                open_index=getattr(args, 'open_index', None)),
        'down':  lambda: cmd_down(),
        'login': lambda: cmd_login(index=getattr(args, 'index', '1')),
        'ps':    lambda: cmd_ps(all_containers=getattr(args, 'all', False)),
        'logs':  lambda: cmd_logs(follow=getattr(args, 'follow', False),
                                  tail=getattr(args, 'tail', None)),
        'scale': lambda: cmd_scale(new_scale=getattr(args, 'new_scale', None),
                                   project_name=project_name),
        'build': lambda: cmd_build(image=getattr(args, 'image', None),
                                   no_cache=getattr(args, 'no_cache', False),
                                   expires=getattr(args, 'expires', None)),
        'rebuild': lambda: cmd_rebuild(),
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

_SNAPSHOT_MIN_INTERVAL_MINUTES_DEFAULT = 60


def _snapshot_min_interval_minutes() -> int:
    """自動スナップショットをスキップする最小間隔 (分)。

    直近のスナップショット取得からこの分数未満なら ``_auto_snapshot`` はスキップする。
    DEVBASE_SNAPSHOT_MIN_INTERVAL_MINUTES で上書き可能 (0 で無効化＝毎回取得)。
    値が不正な場合は既定値にフォールバックする。
    """
    raw = os.environ.get('DEVBASE_SNAPSHOT_MIN_INTERVAL_MINUTES')
    if not raw:
        return _SNAPSHOT_MIN_INTERVAL_MINUTES_DEFAULT
    try:
        value = int(raw)
        if value < 0:
            raise ValueError
        return value
    except ValueError:
        logger.warning(
            "Invalid DEVBASE_SNAPSHOT_MIN_INTERVAL_MINUTES=%r, using default %d",
            raw, _SNAPSHOT_MIN_INTERVAL_MINUTES_DEFAULT
        )
        return _SNAPSHOT_MIN_INTERVAL_MINUTES_DEFAULT


def _auto_snapshot() -> None:
    """デプロイ前の自動スナップショット (差分世代数ベース世代管理)。

    失敗してもデプロイは続行する (warning のみ)。DEVBASE_ROOT 未設定なら no-op。
    """
    devbase_root = os.environ.get('DEVBASE_ROOT')
    if not devbase_root:
        return
    try:
        from datetime import datetime, timedelta, timezone

        from devbase.snapshot.manager import SnapshotManager
        mgr = SnapshotManager(Path(devbase_root))
        min_interval = _snapshot_min_interval_minutes()
        last = mgr.last_snapshot_time()
        if min_interval > 0 and last is not None:
            # 経過時間が負 (last が未来) の場合はスキップしない。システム時計の
            # ズレや他環境からのリストアで last が未来になると delta が負になり、
            # 常に閾値未満と判定されて無期限にスキップされてしまうため、
            # timedelta(0) <= delta の下限ガードを設ける。
            delta = datetime.now(timezone.utc) - last
            if timedelta(0) <= delta < timedelta(minutes=min_interval):
                logger.info(
                    "[0/6] 直近のスナップショット (%s) から%d分以内のためスキップします",
                    last.astimezone().strftime('%Y-%m-%d %H:%M:%S'), min_interval,
                )
                return
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


def _ssh_enabled() -> bool:
    """ENABLE_SSH が真値 (true/1) かどうか (compose.py の判定と揃える)。"""
    return os.environ.get('ENABLE_SSH', '').lower() in ('true', '1')


def _maybe_orca_sync() -> None:
    """up 完了後に Orca 用 SSH config を best-effort で再生成する (PLAN33)。

    ENABLE_SSH が有効なときのみ実行する (SSH 無効なら同期不要)。失敗しても
    warning のみで up の戻り値には影響させない。import は遅延させて起動コストを避ける。
    """
    if not _ssh_enabled():
        return
    try:
        from devbase.commands.orca import regenerate_config
        targets, path = regenerate_config()
        logger.info("Orca SSH config を同期しました (%d 件): %s", len(targets), path)
    except Exception as e:  # noqa: BLE001 - Orca 同期で up を倒さない
        logger.warning("Orca SSH config の同期に失敗しましたがデプロイは成功しています: %s", e)


def _maybe_orca_prune() -> None:
    """down 後に Orca 用 SSH config を best-effort で剪定する (PLAN33)。

    稼働中コンテナから再生成するだけで停止済みエントリは自然に落ちる (prune ≡
    regenerate)。ENABLE_SSH の有無に依らず実行してよい。失敗しても warning のみ。
    """
    try:
        from devbase.commands.orca import regenerate_config
        regenerate_config()
    except Exception as e:  # noqa: BLE001 - Orca 剪定で down を倒さない
        logger.warning("Orca SSH config の剪定に失敗しました: %s", e)


def _resolve_open_index(open_index: Optional[int], scale: int) -> int:
    """開く dev インスタンス番号を解決する (CLI 引数 → env ``DEVBASE_OPEN_INDEX`` → 既定 1)。

    ``1..scale`` の範囲外 (0・負数・``scale`` 超過) は存在しないコンテナを指し原因不明な
    起動失敗を招くため、警告を出して 1 へフォールバックする。:func:`_maybe_open_editor`
    で env フォールバック・範囲チェックを共有する。
    """
    if open_index is None:
        raw = os.environ.get('DEVBASE_OPEN_INDEX')
        try:
            open_index = int(raw) if raw else 1
        except ValueError:
            open_index = 1
    if not (1 <= open_index <= scale):
        logger.warning(
            "open index %d is out of range (1..%d); falling back to 1",
            open_index, scale,
        )
        open_index = 1
    return open_index


def _maybe_open_editor(project_name: str, open_flag: Optional[bool],
                       open_index: Optional[int], scale: int,
                       compose_file=None) -> None:
    """`up` 完了後に dev コンテナへ接続したエディタを開く ([6/6])。

    有効判定は ``open_flag`` (CLI ``--open``/``--no-open``) が優先、None なら env
    ``DEVBASE_OPEN_EDITOR``。エディタ起動の成否は ``up`` の戻り値に影響させない。

    ``open_index`` は起動済みインスタンス範囲 ``1..scale`` 内である必要がある。
    0・負数・``scale`` 超過は存在しないコンテナ URI になり原因不明な起動失敗を招くため、
    警告を出して既定 (1) へフォールバックする。

    ``compose_file`` は実コンテナ名問い合わせ用の override compose。``up`` 起動時と
    同じファイルを渡さないと ``{dev}-{index}`` サービスが見えず実名取得に失敗する。
    未指定なら ``.docker-compose.scale.yml`` が存在すればそれ、無ければ None。
    """
    from devbase.editor import opener

    enabled = open_flag if open_flag is not None else opener.is_open_enabled()
    if not enabled:
        return

    open_index = _resolve_open_index(open_index, scale)

    # 実コンテナ名問い合わせ用の compose file: 明示指定がなければ override が
    # 存在すればそれを使う (起動時と同じ file を docker compose ps へ渡す)。
    if compose_file is None and _SCALE_COMPOSE_FILE.exists():
        compose_file = _SCALE_COMPOSE_FILE

    dev_service_name = get_dev_service_name()
    workdir = opener.resolve_workdir(os.environ, project_name)
    logger.info("[6/6] Opening editor attached to the dev container...")
    try:
        opener.open_editor(
            project_name=project_name,
            dev_service_name=dev_service_name,
            workdir=workdir,
            index=open_index,
            compose_file=compose_file,
        )
    except Exception as e:  # noqa: BLE001 - エディタ起動で up を倒さない
        logger.warning("エディタの自動オープンに失敗しましたがデプロイは成功しています: %s", e)


def cmd_up(project_name: str = None, scale: int = None,
           open_editor: Optional[bool] = None,
           open_index: Optional[int] = None) -> int:
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
    _auto_snapshot()

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
        # 他プロジェクトが稼働 publish 済みのホストポートを best-effort でシードし、
        # SSH publish ポートの跨ぎ衝突 (bind 失敗) を回避する。
        override_file = generate_scaled_compose(
            scale, project_name,
            external_ports_provider=_running_published_host_ports,
        )
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

        _maybe_open_editor(project_name, open_editor, open_index, scale,
                           compose_file=override_file)

        # Orca 連携: SSH 有効時に隔離 SSH config を再生成する (PLAN33)。
        _maybe_orca_sync()

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

    # Orca 連携: 停止したコンテナのエントリを隔離 SSH config から剪定する (PLAN33)。
    _maybe_orca_prune()

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
        override_file = generate_scaled_compose(
            new_scale, project_name,
            external_ports_provider=_running_published_host_ports,
        )
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

def cmd_build(image: str = None, no_cache: bool = False,
              expires: Optional[int] = None) -> int:
    """Build container images.

    引数の意味 (i07 の 3 モード):
      - ``image`` 指定: ``$DEVBASE_ROOT/containers/<image>`` を直接 ``docker build``
        する単体ビルド (``--no-cache`` のみ反映、``--expires`` は対象外)。
      - ``image`` なし + フラグなし: 通常のキャッシュビルド。
      - ``image`` なし + ``--no-cache``: base / project とも無条件 no-cache。
      - ``image`` なし + ``--expires=N``: project の作成日で期限判定し、N 日以上なら
        no-cache (base は独立判定)、N 日未満なら再ビルドしない (既存イメージを使用)。

    フラグなしの compose ビルドも、devbase-base の 2 段ビルドを行う shell
    ``cmd_build`` (``bin/devbase``) 経由 (:func:`_build_resolved` → :func:`_run_build`)
    に統一する。``image`` 指定の単体ビルドのみ直接 ``docker build`` する。
    """
    if image is not None:
        # 単体ビルド (image 指定) では期限判定を行わないため --expires は無視される。
        # 誤併用に気付けるよう警告を出す。
        if expires is not None:
            logger.warning(
                "--expires is ignored when building a single image ('%s')", image)
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
        cmd = ['docker', 'build', '-t', image, str(image_dir)]
        if no_cache:
            cmd.append('--no-cache')
        result = subprocess.run(cmd, check=False)
        return result.returncode

    # `--expires` 単独 (値なし) は sentinel -1。既定日数へ解決する。
    if expires is not None and expires < 0:
        expires = _image_max_age_days()
    return _build_resolved(expires=expires, no_cache=no_cache)


# ---------------------------------------------------------------------------
# cmd_rebuild
# ---------------------------------------------------------------------------

def _resolve_dev_service() -> Optional[dict]:
    """compose config から dev サービス定義を取得する。失敗時は None。"""
    result = subprocess.run(
        ['docker', 'compose', 'config', '--format', 'json'],
        capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return None
    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return config.get('services', {}).get(get_dev_service_name(), {})


def _build_resolved(expires: Optional[int], no_cache: bool) -> int:
    """``devbase build [--expires N | --no-cache]`` / ``rebuild`` の共通エントリ。

    - ``no_cache=True``          : 無条件で base / project とも no-cache 再ビルド
    - ``expires`` 指定           : project の作成日で期限判定し、:func:`_build_with_expires`
                                   に委譲 (base は独立判定)
    - どちらも無し               : 通常のキャッシュビルド

    プロセス互換の終了コードを返す (0=成功)。
    """
    if not Path('compose.yml').exists():
        logger.error("compose.yml not found in current directory")
        return 1

    if no_cache:
        return 0 if _run_build(no_cache=True) else 1
    if expires is None:
        return 0 if _run_build() else 1

    # expires 指定: project イメージの作成日と dev サービス定義 (base 判定用) が必要。
    dev_service = _resolve_dev_service()
    if not dev_service:
        logger.info("Unable to read compose config; building with cache")
        return 0 if _run_build() else 1
    image_name = dev_service.get('image', '')
    if not image_name:
        return 0 if _run_build() else 1
    inspect = subprocess.run(
        ['docker', 'image', 'inspect', image_name],
        capture_output=True, text=True, check=False
    )
    if inspect.returncode != 0:
        # イメージ未存在 → キャッシュビルドで作成する。
        logger.info("Container image '%s' not found; building...", image_name)
        return 0 if _run_build() else 1
    return 0 if _build_with_expires(expires, image_name, inspect.stdout, dev_service) else 1


def cmd_rebuild(expires: int = None) -> int:
    """Rebuild project images honoring an expiry window (``build --expires=N`` synonym).

    ``devbase rebuild`` は ``devbase build --expires=7`` のシノニム (既定 7 日)。
    shell ラッパー (``bin/devbase`` の ``cmd_build``) を経由して devbase-base の
    2 段ビルドと期限判定を行う:

      - project が期限内 → 再ビルドしない (既存イメージを使用)
      - project が期限超過 + base 新しい → project のみ no-cache (base はキャッシュ)
      - project が期限超過 + base 古い/判定不能 → base も含めて no-cache

    ``devbase rebuild`` / ``devbase project rebuild [name]`` のエントリ。
    """
    if expires is None:
        expires = _image_max_age_days()
    logger.info("Rebuilding images (expires=%d days) from compose.yml ...", expires)
    return _build_resolved(expires=expires, no_cache=False)


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
      - Image present + has build: → run the shared expiry resolver
        (`devbase up` = `devbase rebuild` 相当, i07). Rebuild is gated by the
        project image 'Created' age:
          * younger than threshold → no rebuild (existing image kept)
          * >= threshold, base fresh → project no-cache, base cached
          * >= threshold, base stale/unknown → both no-cache
        Base image (`FROM devbase-*`) freshness is judged independently.
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
            return _fetch_missing_image(image_name, has_build)
        if not has_build:
            return _repull_if_stale(image_name)
        # build 定義あり + イメージ存在 → rebuild と同じ期限リゾルバへ委譲する
        # (devbase up = devbase rebuild 相当。i07 仕様統一)。
        return _build_with_expires(
            _image_max_age_days(), image_name, inspect.stdout, dev_service
        )

    except Exception as e:
        logger.warning("Error checking image: %s", e)
        logger.info("Attempting to build anyway...")
        return _run_build()


def _fetch_missing_image(image_name: str, has_build: bool) -> bool:
    """存在しないイメージを取得する: build 定義があればビルド、なければ pull。"""
    if has_build:
        logger.info("Container image '%s' not found", image_name)
        logger.info("Running 'devbase container build' to create it...")
        return _run_build()
    logger.info("Container image '%s' not found, pulling...", image_name)
    return _pull_and_mark(image_name)


def _repull_if_stale(image_name: str) -> bool:
    """image-only サービスの鮮度チェック: pull マーカーが閾値超過なら再 pull。

    Image-only services: use local touch-file mtime, since image 'Created'
    reflects upstream build time, not local pull time.
    """
    pull_age = _pull_age_days(image_name)
    if pull_age is None:
        # Pre-existing image with no marker (e.g., upgrade from a devbase
        # version without touch-file tracking). Bootstrap a marker now so
        # future runs can apply the threshold. We do not auto-pull here to
        # avoid surprising network calls on the first `up` after upgrade.
        logger.info(
            "First time tracking image '%s'; recording marker (no pull this run)",
            image_name
        )
        _mark_pulled(image_name)
        return True
    max_age = _image_max_age_days()
    if pull_age < max_age:
        return True
    logger.info(
        "Image '%s' last pulled %d days ago (>= %d days threshold), re-pulling...",
        image_name, pull_age, max_age
    )
    return _pull_and_mark(image_name)


def _build_with_expires(expires: int, image_name: str, inspect_json: str,
                        dev_service: dict) -> bool:
    """期限ウィンドウに従って build 定義のあるサービスを再ビルドする共通リゾルバ。

    ``devbase build --expires=N`` / ``devbase rebuild`` (= ``build --expires=7``) /
    ``devbase up`` の自動準備経路が共有する。project イメージの作成日で再ビルドの
    要否とキャッシュの扱いを切り替える:

      - project が ``expires`` 日未満 (または判定不能) → 再ビルドしない (既存
        イメージをそのまま使う)
      - project が ``expires`` 日以上 + base が閾値内 (新しい) → project のみ
        no-cache、base はキャッシュ利用 (``--project-no-cache``)
      - project が ``expires`` 日以上 + base が古い/判定不能 → base も含めて
        no-cache (``--no-cache``)

    base イメージ (``FROM devbase-*``) の作成日は project とは独立して判定する。
    """
    age_days = _get_image_age_days(inspect_json)
    if age_days is None or age_days < expires:
        if age_days is not None:
            logger.info(
                "Container image '%s' is %d days old (< %d days threshold); "
                "skipping rebuild (existing image is fresh)",
                image_name, age_days, expires
            )
        return True
    logger.info(
        "Container image '%s' is %d days old (>= %d days threshold)",
        image_name, age_days, expires
    )
    if _base_image_is_fresh(dev_service, expires):
        logger.info("Rebuilding project without cache (base is fresh)...")
        return _run_build(project_no_cache=True)
    logger.info("Rebuilding base and project without cache...")
    return _run_build(no_cache=True)


def _base_image_is_fresh(dev_service: dict, max_age: int) -> bool:
    """ベースイメージ (``FROM devbase-*``) が閾値内に作成されたものか判定する。

    True なら base は新しいため project だけ no-cache で再ビルドする。判定不能
    (ベース未検出 / inspect 失敗 / 日付解析失敗) の場合は False を返し、base も
    含めて no-cache で再ビルドする。
    """
    base_ref = _get_base_image_ref(dev_service)
    if not base_ref:
        return False
    inspect = subprocess.run(
        ['docker', 'image', 'inspect', base_ref],
        capture_output=True,
        text=True,
        check=False
    )
    if inspect.returncode != 0:
        return False
    age_days = _get_image_age_days(inspect.stdout)
    if age_days is None:
        return False
    if age_days < max_age:
        logger.info(
            "Base image '%s' is %d days old (< %d days threshold)",
            base_ref, age_days, max_age
        )
        return True
    logger.info(
        "Base image '%s' is %d days old (>= %d days threshold)",
        base_ref, age_days, max_age
    )
    return False


def _get_base_image_ref(dev_service: dict) -> Optional[str]:
    """dev サービスの Dockerfile の ``FROM devbase-*`` からベースイメージ参照を得る。

    例: ``FROM devbase-base:latest`` -> ``devbase-base:latest``
        ``FROM devbase-base``        -> ``devbase-base:latest`` (tag 補完)
    見つからない / 読めない場合は None。
    """
    build = dev_service.get('build')
    if not build:
        return None
    if isinstance(build, str):
        context, dockerfile = build, 'Dockerfile'
    else:
        context = build.get('context', '.')
        dockerfile = build.get('dockerfile', 'Dockerfile')
    df_path = Path(dockerfile)
    if not df_path.is_absolute():
        df_path = Path(context) / dockerfile
    try:
        text = df_path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    for line in text.splitlines():
        # FROM は小文字 (`from`) も許容され、`--platform=...` が前置されることがある。
        m = re.match(r'\s*FROM\s+(?:--platform=\S+\s+)?(devbase-\S+)',
                     line, re.IGNORECASE)
        if m:
            ref = m.group(1)
            if ':' not in ref:
                ref += ':latest'
            return ref
    return None


def _pull_and_mark(image_name: str) -> bool:
    """docker pull を実行し、成功時は pull マーカーを更新する。"""
    ok = _run_pull(image_name)
    if ok:
        _mark_pulled(image_name)
    return ok


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


def _run_build(no_cache: bool = False, project_no_cache: bool = False) -> bool:
    """Run the build command.

    no_cache=True rebuilds base and project without cache.
    project_no_cache=True rebuilds only the project image without cache.
    """
    devbase_root = Path(os.environ.get('DEVBASE_ROOT', ''))
    if not devbase_root.exists():
        logger.error("DEVBASE_ROOT not set")
        return False

    devbase_bin = devbase_root / 'bin' / 'devbase'
    if not devbase_bin.exists():
        logger.error("devbase command not found at %s", devbase_bin)
        return False

    cmd = ['bash', str(devbase_bin), 'build']
    if project_no_cache:
        cmd.append('--project-no-cache')
    elif no_cache:
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

    def _is_scale_line(line: str) -> bool:
        return line.strip().startswith('CONTAINER_SCALE=')

    try:
        lines = env_file.read_text().splitlines(keepends=True)
        new_lines = [
            f'CONTAINER_SCALE={new_scale}\n' if _is_scale_line(line) else line
            for line in lines
        ]
        if not any(map(_is_scale_line, lines)):
            new_lines.append(f'\n# Added by devbase scale command\nCONTAINER_SCALE={new_scale}\n')
        env_file.write_text(''.join(new_lines))
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
