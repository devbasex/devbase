"""Container lifecycle commands (up, down, ps, login, logs, scale, build)"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from devbase.errors import DevbaseError
from devbase.log import get_logger
from devbase.volume.manager import ensure_volumes
from devbase.volume.compose import (
    generate_scaled_compose,
    get_dev_service_name,
)
from devbase.utils.docker import (
    docker_compose,
    docker_compose_down,
    docker_compose_up,
    wait_for_containers_ready,
    ensure_network
)
from devbase.utils.config import get_project_name
from devbase.project import runtime as project_runtime

logger = get_logger(__name__)

_SCALE_COMPOSE_FILE = Path('.docker-compose.scale.yml')


# ---------------------------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------------------------

def _devbase_root() -> Optional[Path]:
    root = os.environ.get('DEVBASE_ROOT')
    return Path(root) if root else None


def _inject_secrets(*, required: bool):
    """機密を復号して自プロセスの環境変数へ載せ、載せた内容を返す。

    ``docker compose`` は自分を起動したプロセスの環境変数から値を解決するため、
    Compose を呼ぶ前にここを通す。生成する構成には変数名しか書かないので、
    暗号文も平文ファイルも Compose には渡らない (plan35 §4.3)。

    戻り値を変数名の一覧ではなく :class:`~devbase.env.runtime.SecretEnv` に
    しているのは、構成生成側が**由来 (共通 / プロジェクト) ごとの内訳**を必要
    とするため。サービスが元々参照していなかった由来の機密まで渡さないための
    材料になる。

    ``required=False`` の経路 (down / ps / logs など) では、鍵が無い・復号に
    失敗したというだけでコンテナを止められなくなるのは困るため、警告に留めて
    続行する。値が要るのは主に起動時の変数展開であり、停止や状態確認には
    要らない。

    載せ直す前に :func:`~devbase.env.runtime.clear_injected` を通すのは、
    プロジェクト切替の残留対策。``cli._load_secret_env`` は dispatch の**前**に
    「現在地のプロジェクト」の機密を載せるが、TUI や
    ``python -m devbase.cli project up <other>`` の直接起動ではその後
    ``_resolve_project_name`` が対象プロジェクトへ切り替わる。ここは切替・chdir
    の**後**に呼ばれるので、載せ直しで上書きできる。ただし**上書きだけでは
    足りない**: 切替先に同名のキーが無い機密 (切替元プロジェクト固有のもの) は
    上書きされず残り、Compose や子プロセスへ引き継がれてしまうため、先に
    取り除く。非機密設定 (``env``) 側の ``_CALLER_ENV_KEYS`` /
    :func:`_resolve_project_name` と同じ扱いを機密にも与えることになる。
    """
    from devbase.env import runtime as _runtime
    from devbase.errors import DevbaseError

    root = _devbase_root()
    if root is None:
        return _runtime.SecretEnv()
    _runtime.clear_injected()
    try:
        return _runtime.inject(root, _runtime.current_project_name(root))
    except DevbaseError as e:
        if required:
            raise
        logger.warning("機密を読み込めませんでした (続行します): %s", e)
        return _runtime.SecretEnv()


def _generate_compose_for(scale: int, secrets, dev_environment=None) -> Path:
    """機密の内訳と devbase 由来の環境変数を渡してスケール構成を生成する。

    ``dev_environment`` は ``project.yml`` から作った clone プラン等
    (:func:`devbase.project.runtime.container_env`)。dev サービスへ載せることで、
    entrypoint がコンテナ内で複数リポジトリを clone できる。
    """
    return generate_scaled_compose(
        scale,
        secret_env_names=secrets.names,
        global_env_names=secrets.global_names,
        project_env_names=secrets.project_names,
        dev_environment=dev_environment,
    )


@contextmanager
def _previous_scale_compose():
    """生成前の override compose を退避し、``down`` へ渡すパスとして貸し出す。

    起動時の構成生成 (= 機密の復号) は既存コンテナの停止より**前**に済ませたい。
    停止してから復号に失敗すると、起動できないだけでなく稼働中の開発環境まで
    止まったままになるため。一方で
    :func:`~devbase.volume.compose.generate_scaled_compose` は
    ``.docker-compose.scale.yml`` を上書きするので、停止には**旧**構成が要る。
    新構成で停止すると、スケールを縮める起動で新構成に無いインスタンスが
    取り残されるため。

    退避が無い (初回起動) 場合は ``None`` を返し、呼び出し側は素の
    ``docker compose down`` へ委ねる。ブロック内で例外が起きたときは旧構成を
    書き戻す。生成が途中で失敗しても ``down`` / ``ps`` が参照する構成を壊さない
    ため。
    """
    original = _SCALE_COMPOSE_FILE.read_bytes() if _SCALE_COMPOSE_FILE.exists() else None
    if original is None:
        yield None
        return

    backup = Path(f'{_SCALE_COMPOSE_FILE}.prev')
    backup.write_bytes(original)
    try:
        yield backup
    except BaseException:
        _SCALE_COMPOSE_FILE.write_bytes(original)
        raise
    finally:
        backup.unlink(missing_ok=True)


def _compose_run(subcommand: str, *extra_args: str) -> int:
    """docker compose コマンドを実行する共通関数"""
    _inject_secrets(required=False)
    cmd = ['docker', 'compose']
    if _SCALE_COMPOSE_FILE.exists():
        cmd.extend(['-f', str(_SCALE_COMPOSE_FILE)])
    cmd.append(subcommand)
    cmd.extend(extra_args)
    return subprocess.run(cmd).returncode


def _run_deploy_script_for_instances(deploy_script: Path, indices,
                                     config=None) -> None:
    """デプロイスクリプトをスケールされた各インスタンスに対して実行する。

    ``config`` (``project.yml``) を渡すと、clone 先やリポジトリ URL をフックへ
    環境変数で伝える (:func:`devbase.project.runtime.hook_env`)。
    """
    hook_vars = project_runtime.hook_env(config) if config is not None else {}
    for i in indices:
        logger.info("[Bonus] Running deploy script for instance %d...", i)
        env = {**os.environ, **hook_vars, 'DEVBASE_INSTANCE_INDEX': str(i)}
        try:
            subprocess.run(['bash', str(deploy_script)], check=True, env=env)
            logger.info("Deploy script completed for instance %d", i)
        except subprocess.CalledProcessError as e:
            logger.warning("Deploy script failed for instance %d (exit code %d)", i, e.returncode)


def _run_pre_up_hook(config=None) -> bool:
    """`./pre-up` フックがあればコンテナ起動前に実行する。

    ビルドコンテキスト用のリポジトリ clone など、`docker compose up` より前に
    完了しておく必要のある準備処理をプロジェクト側で記述するためのフック。

    ``config`` (``project.yml``) を渡すと、clone 先やリポジトリ URL をフックへ
    環境変数で伝える (:func:`devbase.project.runtime.hook_env`)。フックは以前
    ``source ./env`` で ``GIT_REPO`` / ``WORK_DIR`` を読んでいたが、これらは
    ``project.yml`` へ移ったため devbase 側から明示的に渡す。

    Returns:
        True: フックが存在しなかった、または成功した
        False: フックが失敗した（呼び出し側で `cmd_up` を中断する）
    """
    pre_up_script = Path('./pre-up')
    if not (pre_up_script.exists() and pre_up_script.is_file()):
        return True

    logger.info("Running pre-up hook: %s", pre_up_script)
    hook_vars = project_runtime.hook_env(config) if config is not None else {}
    try:
        subprocess.run(['bash', str(pre_up_script)], check=True,
                       env={**os.environ, **hook_vars})
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
    変数欠落 (例: project 固有の ``ENABLE_SSH``) を防ぐ。

    env は環境変数定義のみを想定したファイル (bin/devbase 冒頭コメント参照) の
    ため、ここでは ``export`` 接頭辞付き / 無しの単純な ``KEY=VALUE`` 行のみを
    解釈する。``#`` コメント・空行は無視し、値の前後のクォートは除去する。

    変数参照 (``$VAR`` / ``${VAR}``) は shell ``source ./env`` (wrapper 経路) と
    同様に展開する。``FOO=$BAR/baz`` のように同一ファイル内で先に定義した変数を
    参照する書き方を wrapper 経路と揃えるため (行は file 順に ``os.environ`` へ
    載せるので、参照時には先行行の値が解決済み)。単一引用符 ``'...'`` の値は
    shell 同様リテラル扱いで展開しない。

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
        # shell `source ./env` 相当の変数展開 ($VAR / ${VAR}) を行う。同一ファイル内で
        # 先に定義した変数を参照する書き方 (`FOO=$BAR/baz`) を wrapper 経路と揃える
        # ため (行順に os.environ へ載せるため参照時には解決済み)。
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
        # ``PWD`` も併せて切り替える。機密の解決 (
        # :func:`devbase.env.runtime.current_project_name`) は wrapper の cd を前提に
        # ``os.environ['PWD']`` を先に見るため、os.chdir だけだと切替前の PWD が残り、
        # 切替先ではなく呼び出し元プロジェクトの機密を読んでしまう
        # (TUI の ``_run_in_project`` が PWD を差し替えているのと同じ理由)。
        os.environ['PWD'] = str(target)
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
    効かなくなるため。build は name positional を持たないため、この Python
    フォールバックの対象外である。compose ビルドは wrapper の shell 実装で CWD
    実行され、`<image>` 指定の単体ビルドは CWD に依存しない (PLAN49)。
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


def _apply_window_titles(project_name: str, scale: int, dev_service_name: str,
                         compose_file=None) -> None:
    """各 dev コンテナの VS Code ウィンドウタイトルをコンテナ名始まりにする。

    既定のタイトルは編集中ファイル名が先頭に来るため、複数プロジェクトの窓を
    並べるとどれがどれか分からなくなる。コンテナ内の Remote settings へ
    ``window.title`` を書いて、``nyle-dx-dev-1 - ファイル名`` の形に固定する
    (詳細と方式の選定理由は :mod:`devbase.editor.window_title`)。

    エディタ自動オープンの有無 (``open_editor``) とは独立に行う。手で
    「コンテナにアタッチ」した窓にも同じタイトルが要るため。

    失敗しても ``up`` は倒さない (タイトルは付随的な体験改善のため)。
    """
    from devbase.editor import opener, window_title

    template = window_title.resolve_template()
    if template is None:
        return
    for index in range(1, scale + 1):
        try:
            container_name = opener.resolve_container_name(
                dev_service_name, project_name, index, compose_file=compose_file)
            window_title.apply_to_container(container_name, template=template)
        except Exception as e:  # noqa: BLE001 - 付随処理で up を倒さない
            logger.debug("window.title の設定をスキップしました (index=%d): %s", index, e)


def _maybe_open_editor(project_name: str, open_flag: Optional[bool],
                       open_index: Optional[int], scale: int,
                       config, compose_file=None) -> None:
    """`up` 完了後に dev コンテナへ接続したエディタを開く ([6/6])。

    有効判定は ``open_flag`` (CLI ``--open``/``--no-open``) が優先、None なら
    ``project.yml`` の ``open_editor``、それも無ければ env ``DEVBASE_OPEN_EDITOR``。
    エディタ起動の成否は ``up`` の戻り値に影響させない。

    開く対象は ``config`` (``project.yml``) から決める。repo が 1 件なら primary の
    フォルダ、2 件以上なら entrypoint が書き出した ``*.code-workspace``。

    ``open_index`` は起動済みインスタンス範囲 ``1..scale`` 内である必要がある。
    0・負数・``scale`` 超過は存在しないコンテナ URI になり原因不明な起動失敗を招くため、
    警告を出して既定 (1) へフォールバックする。

    ``compose_file`` は実コンテナ名問い合わせ用の override compose。``up`` 起動時と
    同じファイルを渡さないと ``{dev}-{index}`` サービスが見えず実名取得に失敗する。
    未指定なら ``.docker-compose.scale.yml`` が存在すればそれ、無ければ None。
    """
    from devbase.editor import opener

    enabled = (open_flag if open_flag is not None
               else opener.is_open_enabled(config=config))
    if not enabled:
        return

    open_index = _resolve_open_index(open_index, scale)

    # 実コンテナ名問い合わせ用の compose file: 明示指定がなければ override が
    # 存在すればそれを使う (起動時と同じ file を docker compose ps へ渡す)。
    if compose_file is None and _SCALE_COMPOSE_FILE.exists():
        compose_file = _SCALE_COMPOSE_FILE

    dev_service_name = get_dev_service_name()
    workdir = config.resolved_work_dir()
    # repo が 2 件以上なら multi-root workspace を開く (entrypoint が同じパスへ
    # ファイルを書き出している)。1 件なら従来どおりフォルダを開く。
    workspace = (project_runtime.workspace_path(project_name)
                 if len(config.repos) > 1 else None)
    logger.info("[6/6] Opening editor attached to the dev container...")
    try:
        opener.open_editor(
            project_name=project_name,
            dev_service_name=dev_service_name,
            workdir=workdir,
            workspace=workspace,
            index=open_index,
            compose_file=compose_file,
        )
    except Exception as e:  # noqa: BLE001 - エディタ起動で up を倒さない
        logger.warning("エディタの自動オープンに失敗しましたがデプロイは成功しています: %s", e)


def _report_missing_repos(config, scale: int, dev_service_name: str,
                          project_name: str,
                          compose_file: Optional[Path] = None) -> None:
    """``project.yml`` に書いたのに ``/work`` へ無いリポジトリを警告する (PLAN37)。

    clone の失敗は entrypoint 側で warning に留めてコンテナ起動を続ける
    (``containers/base/entrypoint.sh`` の ``devbase_clone_repos``)。その warning は
    ``docker logs`` にしか出ないため、``up`` の画面だけを見ていると「成功した」と
    読めてしまう。ここで ``/work`` の実体を見て不足を伝える。

    ログを grep せず実体を見るのは、ログがコンテナ再起動をまたいで積み上がり
    「いつの失敗か」を判別できないため。「今 ``/work`` に有るか」の方が真実に近い。

    問い合わせ自体の失敗 (コンテナが既に落ちている等) では何も言わない。``up`` は
    ここまでで成功しており、付随情報のために倒す価値はない。
    """
    for index in range(1, scale + 1):
        service = f"{dev_service_name}-{index}"
        try:
            result = docker_compose(
                ['exec', '-T', service, 'ls', '-A1', '/work'],
                compose_file=compose_file, check=True,
                capture_output=True, silent_error=True)
        except (subprocess.CalledProcessError, OSError):
            continue

        # dir には空白・制御文字が入らない (project.yml のローダが弾く) ので
        # ls の 1 行 = 1 エントリ名として扱える。
        present = set(result.stdout.split())
        missing = [repo for repo in config.repos if repo.dir not in present]
        if not missing:
            continue

        logger.warning("Repositories missing in /work of %s (clone may have failed):",
                       service)
        for repo in missing:
            logger.warning("  - %s (%s)", repo.dir, repo.url)
        logger.warning("  Details: devbase project logs %s | grep Warning",
                       project_name)


def cmd_up(project_name: str = None, scale: int = None,
           open_editor: Optional[bool] = None,
           open_index: Optional[int] = None) -> int:
    """Deploy containers with specified scale"""
    if project_name is None:
        project_name = get_project_name()

    # project.yml が唯一の正 (PLAN32)。読めなければ移行手順を案内して止まる。
    config = project_runtime.current_project_config()

    if scale is None:
        scale = config.scale if config.scale is not None else project_runtime.DEFAULT_SCALE

    dev_service_name = get_dev_service_name()

    logger.info("Deploying project '%s' with scale=%d (dev service: %s)",
                project_name, scale, dev_service_name)

    # Pre-check 1: Ensure .env file exists with content
    if not _ensure_env_files():
        logger.error("Failed to create .env file. Please run 'devbase env init' manually.")
        return 1

    # Pre-step: Run ./pre-up hook (e.g. clone source repos used as build contexts)
    if not _run_pre_up_hook(config):
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

        # 復号と構成生成は既存コンテナを止める**前**に済ませる。鍵の紛失・権限
        # 不備・暗号文の破損でここが失敗しても、稼働中の開発環境を落としたまま
        # にしないため。
        with _previous_scale_compose() as down_compose_file:
            logger.info("[2/6] Generating scaled compose file...")
            override_file = _generate_compose_for(
                scale, _inject_secrets(required=True),
                dev_environment=project_runtime.container_env(config, project_name))
            logger.info("Generated: %s", override_file)

            logger.info("[3/6] Stopping existing containers...")
            docker_compose_down(compose_file=down_compose_file)

        logger.info("[4/6] Starting containers...")
        docker_compose_up(compose_file=override_file, detach=True)

        logger.info("[5/6] Waiting for containers to be ready...")
        wait_for_containers_ready(
            container_prefix=dev_service_name,
            scale=scale,
            compose_file=override_file,
            timeout=60
        )

        # clone できなかった repo があれば伝える (揃っていれば何も出さない)。
        _report_missing_repos(config, scale, dev_service_name, project_name,
                              compose_file=override_file)

        # Run project-specific deploy script for each scaled instance
        deploy_script = Path('./deploy')
        if deploy_script.exists() and deploy_script.is_file():
            _run_deploy_script_for_instances(deploy_script, range(1, scale + 1),
                                             config)

        # VS Code のウィンドウタイトルをコンテナ名始まりに固定する
        # (自動オープンの有無に関わらず、手動アタッチにも効かせるため up 側で行う)。
        _apply_window_titles(project_name, scale, dev_service_name,
                             compose_file=override_file)

        _maybe_open_editor(project_name, open_editor, open_index, scale,
                           config, compose_file=override_file)

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
    _inject_secrets(required=False)
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
    _inject_secrets(required=False)
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

    config = project_runtime.current_project_config()
    dev_service_name = get_dev_service_name()
    current_scale = (config.scale if config.scale is not None
                     else project_runtime.DEFAULT_SCALE)

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
        logger.info("[1/5] Updating %s: scale=%d -> %d...",
                    project_runtime.PROJECT_CONFIG_FILENAME, current_scale, new_scale)
        project_runtime.write_scale(Path.cwd(), new_scale)

        logger.info("[2/5] Ensuring volumes exist for scale=%d...", new_scale)
        ensure_volumes(new_scale, project_name)

        logger.info("[2.5/5] Ensuring network exists...")
        ensure_network('devbase_net')

        logger.info("[3/5] Generating scaled compose file...")
        override_file = _generate_compose_for(
            new_scale, _inject_secrets(required=True),
            dev_environment=project_runtime.container_env(config, project_name))
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
            _run_deploy_script_for_instances(
                deploy_script, range(current_scale + 1, new_scale + 1), config)

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

# 単体ビルドで受け付けるイメージ名。`containers/` 配下の 1 ディレクトリ名であることを
# 保証するため、英数字始まりで英数字・ハイフン・アンダースコア・ピリオドのみを許可する。
_IMAGE_NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')


def _build_single_image(image: str, no_cache: bool = False) -> int:
    """``$DEVBASE_ROOT/containers/<image>`` を単体ビルドする (PLAN49 / #139)。

    ``devbase build <image>`` (bin/devbase の dispatch が振り分け) と
    ``devbase project build <image>`` / ``devbase container build <image>`` の
    共通の実装。compose ビルドは巻き込まない。

    Returns:
        ``docker`` の終了コード。事前条件を満たさない場合は 1。
    """
    devbase_root = os.environ.get('DEVBASE_ROOT', '')
    if not devbase_root:
        logger.error("DEVBASE_ROOT not set")
        return 1

    # `image` はパスの一部として連結し、そのままタグにもなる。`/` `\` `..` などを
    # 通すと $DEVBASE_ROOT の外を指せてしまい、Docker タグとして不正な名前も作れるため、
    # ディレクトリ名 1 つとして妥当な文字だけを許可し、それ以外はここで弾く。
    if not _IMAGE_NAME_RE.fullmatch(image):
        logger.error(
            "Invalid image name: %r (must be a single directory name under "
            "containers/: alphanumeric start, then letters, digits, '.', '-', '_')",
            image)
        return 1

    image_dir = Path(devbase_root) / 'containers' / image
    if not image_dir.is_dir():
        logger.error("Image directory not found: %s", image_dir)
        return 1

    dockerfile = image_dir / 'Dockerfile'
    if not dockerfile.exists():
        logger.error("Dockerfile not found: %s", dockerfile)
        return 1

    # タグは `devbase-` + ディレクトリ名。`containers/` 配下のイメージはすべてこの規約で
    # 参照されており (compose.yml の image: / 他 Dockerfile の `FROM devbase-base:latest` /
    # snapshot の SNAPSHOT_IMAGE)、接頭辞なしでは `FROM devbase-*` から解決できない。
    # 接頭辞は剥がさない。剥がすと `containers/xxx` と `containers/devbase-xxx` が同じ
    # タグを取り合い、ディレクトリが別なのに互いのイメージを上書きしてしまう。
    # `devbase build devbase-base` のように接頭辞込みで渡した場合は、上の存在確認で
    # `containers/devbase-base` を探して見つからず、探したパスを示して終了する。
    tag = f"devbase-{image}:latest"

    # `docker build` ではなく `docker buildx build --load` を使う。shell 側の
    # build_base_image が同じイメージを buildx で作っており、ビルダが分かれると
    # 同じイメージを 2 通りの方法で作ることになる。`--load` が無いと buildx の
    # 既定ビルダでは生成物がローカルのイメージ一覧へ現れない。
    logger.info("Building image '%s' from %s ...", tag, image_dir)
    cmd = ['docker', 'buildx', 'build', '--load', '-t', tag, str(image_dir)]
    if no_cache:
        cmd.append('--no-cache')
    result = subprocess.run(cmd, check=False)
    return result.returncode


def cmd_build(image: Optional[str] = None, no_cache: bool = False,
              expires: Optional[int] = None) -> int:
    """Build container images.

    引数の意味 (i07 の 3 モード):
      - ``image`` 指定: ``$DEVBASE_ROOT/containers/<image>`` を
        ``docker buildx build --load -t devbase-<image>:latest`` で作る単体ビルド
        (``--no-cache`` のみ反映、``--expires`` は対象外)。
      - ``image`` なし + フラグなし: 通常のキャッシュビルド。
      - ``image`` なし + ``--no-cache``: base / project とも無条件 no-cache。
      - ``image`` なし + ``--expires=N``: project の作成日で期限判定し、N 日以上なら
        no-cache (base は独立判定)、N 日未満なら再ビルドしない (既存イメージを使用)。

    フラグなしの compose ビルドも、devbase-base の 2 段ビルドを行う shell
    ``cmd_build`` (``bin/devbase``) 経由 (:func:`_build_resolved` → :func:`_run_build`)
    に統一する。``image`` 指定の単体ビルドはここが唯一の実装で、shell 側の dispatch
    (``devbase build <image>``) もここへ振り分けられる (PLAN49)。
    """
    if image is not None:
        # 単体ビルド (image 指定) では期限判定を行わないため --expires は無視される。
        # 誤併用に気付けるよう警告を出す。
        if expires is not None:
            logger.warning(
                "--expires is ignored when building a single image ('%s')", image)
        return _build_single_image(image, no_cache=no_cache)

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

    # 機密が暗号化されていれば平文の .env は存在しない。ファイルの有無ではなく
    # 秘密ストアに設定があるかで判定しないと、移行済みの環境で毎回 env init が
    # 走ってしまう。
    from devbase.env import runtime as _runtime
    from devbase.env.secret_store import SecretRef, SecretStore

    store = SecretStore(devbase_root)
    has_global = store.exists(SecretRef.for_global())

    project_name = _runtime.current_project_name(devbase_root)
    has_project = project_env.exists()
    if not has_project and project_name:
        has_project = store.exists(SecretRef.for_project(project_name))

    if has_project and has_global:
        return True

    missing_files = []
    if not has_project:
        missing_files.append("project .env")
    if not has_global:
        missing_files.append(f"devbase root .env ({devbase_root_env})")

    logger.info("Missing: %s", ', '.join(missing_files))
    logger.info("Running 'devbase env init' to create them...")

    success = True
    child_env = {**os.environ, 'PYTHONPATH': str(devbase_root / 'lib')}

    if not has_global:
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

    if not has_project:
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
