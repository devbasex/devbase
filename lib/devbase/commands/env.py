"""devbase env コマンド実装"""

import os
import subprocess
from pathlib import Path
from typing import List, Optional

import yaml

from devbase.errors import DevbaseError
from devbase.log import get_logger
from devbase.env import keys
from devbase.env.store import EnvFile, safe_input
from devbase.env.sources import SourcesManager, file_hash, dir_hash
from devbase.env.collector import CollectorRegistry

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 保存先の解決
# ---------------------------------------------------------------------------
#
# 設定の実体は秘密ストア (devbase.env.secret_store) が持ち、平文か暗号化かは
# ファイルの存在から自動判定される。以下のヘルパは「どの参照を扱うか」だけを決め、
# 各コマンドは保存形式を意識せず EnvFile 互換の操作で読み書きする。

def _secret_store(devbase_root: Path):
    from devbase.env.secret_store import SecretStore

    return SecretStore(devbase_root)


def _owner(user: bool) -> str:
    """``--user`` の有無を参照の持ち主へ写す (PLAN51 決定 14)"""
    return 'user' if user else 'team'


def _global_env(devbase_root: Path, user: bool = False, store=None, *, fresh: bool = False):
    """共通設定のビューを返す (``user`` なら個人共通。``fresh`` なら控えへ落ちない)"""
    from devbase.env.secret_store import SecretRef
    from devbase.env.secret_view import SecretEnvFile

    store = store if store is not None else _secret_store(devbase_root)
    return SecretEnvFile(store, SecretRef.for_global(owner=_owner(user)), fresh=fresh)


def _current_project_name(devbase_root: Path, cwd: Optional[Path] = None) -> Optional[str]:
    """CWD からプロジェクト名を解決する (実体は :mod:`devbase.env.runtime`)。

    同じ判定をコンテナ起動側 (機密の合成) でも使うため、実装は 1 箇所に置く。
    """
    from devbase.env import runtime as _runtime

    return _runtime.current_project_name(devbase_root, cwd)


def _project_env(devbase_root: Path, cwd: Optional[Path] = None,
                 user: bool = False, store=None, *, fresh: bool = False):
    """CWD のプロジェクト設定のビューを返す (projects/ 配下でなければ ``None``)"""
    from devbase.env.secret_store import SecretRef
    from devbase.env.secret_view import SecretEnvFile

    name = _current_project_name(devbase_root, cwd)
    if name is None:
        return None
    store = store if store is not None else _secret_store(devbase_root)
    return SecretEnvFile(store, SecretRef.for_project(name, owner=_owner(user)), fresh=fresh)


def _target_env(devbase_root: Path, project: bool, user: bool = False):
    """``--project`` / ``--user`` から操作対象の設定ビューを返す (解決できなければ ``None``)。

    ``-p`` は適用範囲の軸を、``--user`` は持ち主の軸を選び、片方の指定がもう片方の軸を
    動かさない (PLAN51 決定 14)。指定の無い軸は既定 (共通 / チーム単位) を採る。

    ``projects/<name>`` 配下でない場所での ``--project`` は、どのプロジェクトの
    設定を指しているのか決められない。従来は CWD に ``.env`` を作っていたが、
    コンテナが読む先とは限らないため明示的に断る。

    set / delete / edit の 3 つが同じ判断とエラー文言を持つ必要があるので、
    ここへ集約して振る舞いがずれないようにする。

    3 つとも書き込みを伴うため、読み出しは控えへ落ちない (``fresh``)。控えから
    読んだ内容を元に書き戻すと、不達の間の他の利用者の更新を上書きする。
    """
    if not project:
        env_file = _global_env(devbase_root, user=user, fresh=True)
    else:
        env_file = _project_env(devbase_root, user=user, fresh=True)
        if env_file is None:
            logger.error(
                "--project は $DEVBASE_ROOT/projects/<name> 配下で実行してください")
            return None

    # ファイル backend は個人単位の参照を持たない。backend の path() はチーム単位の
    # ファイルを指すため、ここで止めないと `edit --user` がチームの .env を開き、
    # 個人の資格情報が全員から見える場所へ入る (PLAN51 設計 2)。
    if user and not env_file.has_user_refs():
        logger.error(
            "%s backend は個人単位の機密を扱えません (--user)。"
            "個人単位の機密を置くにはサーバ backend を設定してください: "
            "devbase env backend use openbao ...", env_file.mode_name())
        return None
    return env_file


def cmd_env(devbase_root: Path, args) -> int:
    """envサブコマンドの振り分け"""
    subcmd = getattr(args, 'subcommand', None)

    handlers = {
        'init':    lambda: cmd_env_init(devbase_root, reset=getattr(args, 'reset', False)),
        'sync':    lambda: cmd_env_sync(devbase_root),
        'list':    lambda: cmd_env_list(devbase_root,
                                        global_only=getattr(args, 'global_only', False),
                                        project_only=getattr(args, 'project_only', False),
                                        reveal=getattr(args, 'reveal', False),
                                        keys_only=getattr(args, 'keys_only', False),
                                        user=getattr(args, 'user', False)),
        'set':     lambda: cmd_env_set(devbase_root, getattr(args, 'assignment', ''),
                                       project=getattr(args, 'project', False),
                                       user=getattr(args, 'user', False)),
        'get':     lambda: cmd_env_get(devbase_root, getattr(args, 'key', ''),
                                       user=getattr(args, 'user', False)),
        'delete':  lambda: cmd_env_delete(devbase_root, getattr(args, 'key', ''),
                                          project=getattr(args, 'project', False),
                                          user=getattr(args, 'user', False)),
        'edit':    lambda: cmd_env_edit(devbase_root,
                                        project=getattr(args, 'project', False),
                                        user=getattr(args, 'user', False)),
        'project': lambda: cmd_env_project(devbase_root),
        'export':  lambda: cmd_env_export(devbase_root, args),
        'import':  lambda: cmd_env_import(devbase_root, args),
        'token':   lambda: cmd_env_token(devbase_root,
                                         print_only=getattr(args, 'print_only', False),
                                         context=getattr(args, 'context', None)),
        'exec':    lambda: cmd_env_exec(devbase_root,
                                        list(getattr(args, 'argv', []) or []),
                                        context=getattr(args, 'context', None)),
        'encrypt': lambda: _migrate(args).cmd_env_encrypt(
            devbase_root,
            dry_run=getattr(args, 'dry_run', False),
            assume_yes=getattr(args, 'assume_yes', False),
            projects=list(getattr(args, 'projects', []) or []) or None),
        'decrypt': lambda: _migrate(args).cmd_env_decrypt(
            devbase_root,
            dry_run=getattr(args, 'dry_run', False),
            assume_yes=getattr(args, 'assume_yes', False),
            projects=list(getattr(args, 'projects', []) or []) or None),
        'rekey':   lambda: _ops().cmd_env_rekey(
            devbase_root,
            add=list(getattr(args, 'add_recipients', []) or []),
            remove=list(getattr(args, 'remove_recipients', []) or []),
            dry_run=getattr(args, 'dry_run', False),
            assume_yes=getattr(args, 'assume_yes', False)),
        'doctor':  lambda: _ops().cmd_env_doctor(devbase_root),
        'backend': lambda: _backend().cmd_env_backend(devbase_root, args),
        'keygen':  lambda: cmd_env_keygen(devbase_root,
                                          force=getattr(args, 'force', False),
                                          assume_yes=getattr(args, 'assume_yes', False)),
    }

    handler = handlers.get(subcmd)
    if handler:
        return handler()

    logger.error("サブコマンドを指定してください: %s", ', '.join(handlers))
    return 1


def _ops():
    """受信者更新 / 点検の実装モジュール (import を遅延させる)"""
    from devbase.commands import env_ops

    return env_ops


def _backend():
    """保存先の選択・移行の実装モジュール (import を遅延させる)"""
    from devbase.commands import env_backend

    return env_backend


def _migrate(_args=None):
    """移行コマンドの実装モジュール (import を遅延させる)"""
    from devbase.commands import env_migrate

    return env_migrate


def cmd_env_exec(devbase_root: Path, argv, context: Optional[str] = None) -> int:
    """機密を環境変数として渡した状態でコマンドを実行する。

    起動ラッパーは共通の機密ファイルを読み込まなくなったため、ホスト側で動く
    処理のうち値を必要とするもの (Docker Compose の変数展開など) は、この
    コマンドを通して実行する (plan35 §4.4)。復号結果は子プロセスの環境変数
    としてのみ渡り、ファイルには書き出さない。

    docker context (PLAN52) もここで子プロセスへ載せる。カレントディレクトリの
    ``project.local.yml`` と env、引数 ``--context`` から解決し、機密を載せた**後**の
    辞書へ反映するので、``.env`` の同名キーに負けない。gid・home・リモート判定は
    行わない (docker を呼ばない)。
    """
    from devbase.env import runtime as _runtime
    from devbase.project.local_config import load_project_local_config
    from devbase.utils import docker_context

    # argparse.REMAINDER は区切りの `--` も残すため、先頭のものだけ取り除く。
    # 2 つ目以降はコマンド自身への引数なのでそのまま渡す。
    if argv and argv[0] == '--':
        argv = argv[1:]

    if not argv:
        logger.error("実行するコマンドを指定してください: devbase env exec -- CMD [ARGS...]")
        return 1

    project = _runtime.current_project_name(devbase_root)
    env = _runtime.child_env(devbase_root, project)
    # project.local.yml は機密と同じくプロジェクト直下から読む。projects/<name>/sub から
    # 実行しても、機密の対象 (current_project_name) と設定の対象が食い違わない。
    project_dir = (Path(devbase_root) / 'projects' / project) if project else Path.cwd()
    settings = load_project_local_config(project_dir).docker
    docker_context.apply(docker_context.choose_context(settings, cli_context=context,
                                                       environ=env), env, track=False)
    try:
        return subprocess.run(argv, env=env).returncode
    except FileNotFoundError:
        logger.error("コマンドが見つかりません: %s", argv[0])
        return 127
    except OSError as e:
        logger.error("コマンドを実行できませんでした (%s): %s", argv[0], e)
        return 1


def _running_dev_containers(project: str, dev_service_name: str, runner) -> Optional[List[str]]:
    """起動中の dev コンテナの名前を ``<dev>-<n>`` の番号順に返す。docker を呼べなければ ``None``。

    ``up`` の構成は dev の各インスタンスをサービス ``<dev>-<n>`` として定義する
    (``volume/compose.py``)。プロジェクトのラベルだけで絞ると DB や snapshot にも届く。
    """
    import re

    try:
        result = runner(
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
    return [name for _index, name in sorted(found)]


def cmd_env_token(devbase_root: Path, print_only: bool = False,
                  context: Optional[str] = None, runner=None) -> int:
    """起動中の dev コンテナの ``~/.vault-token`` を新しい token で置き換える (PLAN54)。

    コンテナの中の ``bao`` が使う token は 1 時間で切れる。コンテナに資格情報は置かず、
    ホストの資格情報でログインし直して届ける。処理の順は「backend の判定 → プロジェクト
    → dev サービス名 → 接続先 → 対象のコンテナ → ログイン → 書き込み」で、ログインを
    対象が見つかった後に置く (届け先が無いのに token を発行させない)。``--print`` は
    backend の判定の後すぐログインし、token だけを標準出力へ出す。
    """
    from devbase.commands.container import _load_project_env
    from devbase.env import container_token
    from devbase.env import runtime as _runtime
    from devbase.env.secret_store import SecretRef, SecretStoreError
    from devbase.errors import DevbaseError
    from devbase.project.local_config import load_project_local_config
    from devbase.utils import docker_context
    from devbase.volume.compose import get_dev_service_name

    run = runner or subprocess.run
    store = _runtime.store_for(devbase_root)
    try:
        backend_name = store.backend_name
    except SecretStoreError as e:
        logger.error("%s", e)
        return 1
    if backend_name != 'openbao':
        logger.error("backend が openbao ではありません (現在: %s)。コンテナの bao へ渡す "
                     "token はサーバ backend でだけ発行できます", backend_name)
        return 1
    backend = store.backend_for(SecretRef.for_global())

    if print_only:
        try:
            print(backend.issue_token())
        except DevbaseError as e:
            logger.error("%s", e)
            return 1
        return 0

    project = _current_project_name(devbase_root)
    if not project:
        logger.error("プロジェクトのディレクトリ ($DEVBASE_ROOT/projects/<name>) で実行してください")
        return 1
    project_dir = Path(devbase_root) / 'projects' / project

    # 起動ラッパーが source するのは実行時のディレクトリの env だけ。下位ディレクトリから
    # 打っても dev サービス名 (DEV_SERVICE_NAME) を取れるよう、プロジェクト直下の env を載せる
    _load_project_env(project_dir / 'env')
    dev_service_name = get_dev_service_name()

    settings = load_project_local_config(project_dir).docker
    docker_context.apply(docker_context.choose_context(settings, cli_context=context),
                         track=False)

    names = _running_dev_containers(project, dev_service_name, run)
    if names is None:
        return 1
    if not names:
        logger.error("起動中の dev コンテナがありません: %s", project)
        return 1

    try:
        token = backend.issue_token()
    except DevbaseError as e:
        logger.error("%s", e)
        return 1

    written = container_token.push(names, token, runner=runner)
    for name in written:
        print(name)
    failed = [name for name in names if name not in written]
    for name in failed:
        logger.error("token を書けませんでした: %s", name)
    return 1 if failed else 0


def cmd_env_init(devbase_root: Path, reset: bool = False) -> int:
    """全体環境の初期セットアップ（対話式）"""
    env_file = _global_env(devbase_root)
    env_file.load()

    if env_file.count() > 0 and not reset:
        print(f"環境は既にセットアップ済みです ({env_file.count()}変数)")
        print("  更新: devbase env sync")
        print("  やり直し: devbase env init --reset")
        return 0

    if reset and env_file.file_exists():
        from devbase.errors import DevbaseError

        try:
            env_file.backup()
        except DevbaseError as e:
            # サーバ backend では退避を作れないまま消すことになる。退避が無ければ
            # 削除へ進まない (PLAN51 設計 2)
            logger.error("退避を作れないため、既存の設定を消さずに中止します: %s", e)
            return 1
        logger.info("既存の設定をバックアップしました")
        for key in list(env_file.get_all().keys()):
            env_file.delete(key)

    print("\n" + "=" * 42)
    print("devbase 環境セットアップ")
    print("=" * 42)

    registry = CollectorRegistry()
    registry.discover()

    for i, collector in enumerate(registry.collectors, 1):
        print(f"\n[{i}/{len(registry.collectors)}] {collector.display_name}")
        collector.collect_fn(env_file)

    env_file.save()

    _update_source_metadata(devbase_root, env_file)

    logger.info("セットアップ完了: %s (%d変数)", env_file.path, env_file.count())
    return 0


def cmd_env_sync(devbase_root: Path) -> int:
    """ソースファイルから認証情報を再同期する"""
    env_file = _global_env(devbase_root)
    env_file.load()

    sources = SourcesManager(devbase_root)
    sources.load()

    updated = 0

    # AWS
    def _encode_aws():
        from devbase.env.collectors.aws import _encode_aws_config_files
        return _encode_aws_config_files()

    updated += _sync_source(sources, env_file, 'aws', 'AWS認証', _encode_aws)

    # Git
    def _encode_git():
        import base64
        cred_path = Path.home() / '.git-credentials'
        if cred_path.exists():
            content = cred_path.read_text(encoding='utf-8')
            return base64.b64encode(content.encode('utf-8')).decode('ascii')
        return None

    updated += _sync_source(sources, env_file, 'git_credentials', 'Git認証', _encode_git)

    # GCP（プロファイル管理があるため個別処理）
    updated += _sync_gcp(sources, env_file)

    # Host 接続情報（ソースファイルを持たないため hash 比較せず欠落キーを補完）
    updated += _sync_host(env_file)

    if updated > 0:
        env_file.save()
        _update_source_metadata(devbase_root, env_file)
        logger.info("同期完了 (%d件更新)", updated)
    else:
        if not any(sources.get_source(n) for n in ('aws', 'git_credentials', 'gcp')):
            logger.info("ソース情報がありません。先に devbase env init を実行してください")
        else:
            logger.info("同期完了 (変更なし)")

    return 0


def _sync_host(env_file):
    """ホスト接続情報の同期。更新件数を返す。

    ホスト情報はソースファイルを持たないため hash 比較は使わず、**欠落キーのみ既定値で
    補完**する。既存値 (WSL2 等での手動上書き) は尊重して上書きしない。これにより本機能
    導入前の ``.env`` への後付け backfill として機能する。
    """
    from devbase.env.collectors.host import _default_host_user, DEFAULT_HOST_SSH_HOST

    updated = 0
    if not env_file.get(keys.HOST_SSH_USER):
        user = _default_host_user()
        if user:
            env_file.set(keys.HOST_SSH_USER, user)
            logger.info("%s: %s を設定", keys.HOST_SSH_USER, user)
            updated += 1
    if not env_file.get(keys.HOST_SSH_HOST):
        env_file.set(keys.HOST_SSH_HOST, DEFAULT_HOST_SSH_HOST)
        logger.info("%s: %s を設定", keys.HOST_SSH_HOST, DEFAULT_HOST_SSH_HOST)
        updated += 1
    return updated


def _sync_source(sources, env_file, name, label, encode_fn):
    """AWS/Gitなどの単一ソース同期の共通処理。更新件数(0 or 1)を返す。"""
    source = sources.get_source(name)
    if not source:
        return 0

    changed = sources.check_changed(name)
    if changed:
        encoded = encode_fn()
        if encoded:
            env_file.set(source['env_key'], encoded)
            logger.info("%s: 更新しました", label)
            return 1
        else:
            logger.warning("%s: エンコードに失敗", label)
    elif changed is False:
        logger.info("%s: 変更なし", label)
    else:
        logger.info("%s: ソース未登録", label)
    return 0


def _sync_gcp(sources, env_file):
    """GCPプロファイルの同期処理"""
    gcp_source = sources.get_source('gcp')
    if not gcp_source:
        return 0

    updated = 0
    gcp_changes = sources.check_gcp_changed()
    for profile_name, changed in gcp_changes.items():
        if changed:
            profile_info = gcp_source.get('profiles', {}).get(profile_name, {})
            file_str = profile_info.get('file', '')
            if not file_str:
                continue
            file_path = Path(file_str).expanduser()
            if file_path.exists():
                import base64
                encoded = base64.b64encode(file_path.read_bytes()).decode('ascii')
                env_file.set(keys.gcp_credentials_key(profile_name), encoded)
                updated += 1
                logger.info("GCP認証 (%s): 更新しました", profile_name)
        else:
            logger.info("GCP認証 (%s): 変更なし", profile_name)

    return updated


def _print_env_vars(vars_dict, keys_only, reveal):
    """変数一覧の表示（重複排除用共通関数）"""
    if not vars_dict:
        print("  (変数なし)")
        return
    fmt = (lambda k: f"  {k}") if keys_only else (lambda k: f"  {k:<35} {_format_value(k, vars_dict[k], reveal)}")
    print('\n'.join(fmt(k) for k in sorted(vars_dict)))


def cmd_env_list(devbase_root: Path, global_only: bool = False,
                 project_only: bool = False, reveal: bool = False,
                 keys_only: bool = False, user: bool = False) -> int:
    """設定済み変数の一覧表示

    適用範囲の軸は ``-g`` / ``-p`` で、持ち主の軸は ``--user`` で絞る。指定の無い軸は
    絞らない。チーム共通の節は変数が 0 件でも出し、それ以外は存在する参照だけ出す
    (個人単位の参照を持たない backend では、出力は従来と同じになる)。
    """
    store = _secret_store(devbase_root)
    owners = (True,) if user else (False, True)

    if not project_only:
        for as_user in owners:
            env_file = _global_env(devbase_root, user=as_user, store=store)
            if as_user and not env_file.file_exists():
                continue
            all_vars = env_file.get_all()
            label = '個人のグローバル' if as_user else 'グローバル'

            print(f"\n=== {label} ({env_file.path}{_mode_suffix(env_file)}) ===")
            _print_env_vars(all_vars, keys_only, reveal)
            print(f"\n{label}: {len(all_vars)}変数")

    if not global_only:
        for as_user in owners:
            proj_env = _project_env(devbase_root, user=as_user, store=store)
            if proj_env is not None and proj_env.file_exists():
                proj_vars = proj_env.get_all()
                label = '個人のプロジェクト' if as_user else 'プロジェクト'

                print(f"\n=== {label}: {proj_env.ref.name} "
                      f"({proj_env.path}{_mode_suffix(proj_env)}) ===")
                _print_env_vars(proj_vars, keys_only, reveal)
                print(f"\n{label}: {len(proj_vars)}変数")

    return 0


def _mode_suffix(env_file) -> str:
    """一覧表示で保存形式を示す接尾辞。平文のときは何も足さない。

    age は従来どおり ``[暗号化]``、サーバ backend は backend 名を示す。
    """
    from devbase.env.secret_store import MODE_ABSENT, MODE_AGE, MODE_PLAINTEXT

    mode = env_file.mode()
    if mode == MODE_AGE:
        return ' [暗号化]'
    if mode in (MODE_PLAINTEXT, MODE_ABSENT):
        return ''
    return f' [{mode}]'


def _format_value(key: str, value: str, reveal: bool) -> str:
    """表示用に値をフォーマットする"""
    sensitive_patterns = ('KEY', 'SECRET', 'TOKEN', 'PASSWORD', 'CREDENTIALS', 'BASE64')
    is_sensitive = any(p in key.upper() for p in sensitive_patterns)

    if is_sensitive and not reveal:
        return f"██████ ({len(value)}文字)" if len(value) > 100 else "██████"
    return f"{value[:57]}..." if len(value) > 60 else value


def cmd_env_set(devbase_root: Path, assignment: str, project: bool = False,
                user: bool = False) -> int:
    """変数を設定する"""
    if '=' not in assignment:
        logger.error("形式: devbase env set KEY=VALUE")
        return 1

    key, _, value = assignment.partition('=')
    key = key.strip()
    value = value.strip()

    if not key:
        logger.error("キー名が空です")
        return 1

    env_file = _target_env(devbase_root, project, user=user)
    if env_file is None:
        return 1

    try:
        env_file.set(key, value)
        env_file.save()
    except DevbaseError as e:
        logger.error("%s", e)
        return 1

    logger.info("%s を設定しました (%s)", key, env_file.path)
    return 0


def cmd_env_get(devbase_root: Path, key: str, user: bool = False) -> int:
    """変数の値を取得する

    探索順は 個人共通 → チーム共通 → 個人のプロジェクト → チームのプロジェクト。
    適用範囲の順は現行のまま共通が先で、同じ適用範囲では個人単位を先に見る
    (PLAN51 設計 2)。``--user`` を付けると個人単位だけを探す。
    """
    store = _secret_store(devbase_root)
    owners = (True,) if user else (True, False)

    for as_user in owners:
        env_file = _global_env(devbase_root, user=as_user, store=store)
        if as_user and not env_file.file_exists():
            continue
        value = env_file.get(key)
        if value is not None:
            print(value)
            return 0

    for as_user in owners:
        proj_env = _project_env(devbase_root, user=as_user, store=store)
        if proj_env is not None and proj_env.file_exists():
            value = proj_env.get(key)
            if value is not None:
                print(value)
                return 0

    logger.error("変数 '%s' は設定されていません", key)
    return 1


def cmd_env_delete(devbase_root: Path, key: str, project: bool = False,
                   user: bool = False) -> int:
    """変数を削除する

    ``--project`` を受けるのは、暗号化された設定は利用者がエディタで直接開いて
    不要なキーを消せないため。CLI からプロジェクト設定を掃除する手段が要る。
    """
    env_file = _target_env(devbase_root, project, user=user)
    if env_file is None:
        return 1

    try:
        if env_file.delete(key):
            env_file.save()
            logger.info("%s を削除しました (%s)", key, env_file.path)
            return 0
    except DevbaseError as e:
        logger.error("%s", e)
        return 1

    logger.error("変数 '%s' は存在しません", key)
    return 1


def cmd_env_edit(devbase_root: Path, project: bool = False, user: bool = False) -> int:
    """エディタで.envを開く

    ``--project`` を受けるのは delete と同じ理由。保存先をファイルとして直接開いて
    よいのは平文の backend だけで (``direct_edit``)、それ以外は
    ``_edit_via_tempfile`` 経由で 読み出し → 編集 → 書き戻し する。判定を
    「暗号化されているか」ではなく「直接編集できるか」にするのは、サーバ backend の
    ``path()`` が ``<mount>/<パス>`` を ``Path`` にしただけの値で、開いても機密は無いため。
    """
    env_file = _target_env(devbase_root, project, user=user)
    if env_file is None:
        return 1

    editor = os.environ.get('EDITOR', 'vi')

    try:
        direct = env_file.direct_edit()
    except DevbaseError as e:
        logger.error("%s", e)
        return 1
    if direct:
        return subprocess.call([editor, str(env_file.path)])

    return _edit_via_tempfile(env_file, editor)


def _edit_via_tempfile(env_file, editor: str) -> int:
    """直接開けない保存先 (暗号化・サーバ) を、平文を残さずにエディタで編集する。

    エディタは平文のファイルしか開けないため、読み出した内容を一時ファイルへ書いて
    編集させ、保存後に保存先へ書き戻してから消す。一時ファイルは自分専用の
    ``0700`` ディレクトリに ``0600`` で作り、正常終了でも異常終了でも
    ``finally`` で必ず削除する。

    ここだけは平文が一瞬ディスクに載る。エディタの外部プロセスに値を渡す方法が
    他に無いためで、恒久的な平文ファイルを作らないという方針の例外として扱う
    (plan35 §7 の「守れないもの」に対応する)。
    """
    import shutil
    import tempfile

    from devbase.env import io_common as _io_common
    from devbase.errors import DevbaseError

    try:
        # 辞書ではなく原文のバイト列を取り出す。辞書経由だとコメント・空行・
        # ``export`` 表記が落ち、編集しただけで利用者の書いた内容が消えてしまう。
        original = env_file.load_bytes()
    except DevbaseError as e:
        logger.error("%s", e)
        return 1

    workdir = Path(tempfile.mkdtemp(prefix='devbase-env-'))
    tmp_path = workdir / '.env'
    try:
        _io_common.write_secure_bytes(tmp_path, original)
        before = tmp_path.read_bytes()

        rc = subprocess.call([editor, str(tmp_path)])
        if rc != 0:
            logger.error("エディタが異常終了したため保存しません (exit=%d)", rc)
            return rc

        after = tmp_path.read_bytes()
        if after == before:
            logger.info("変更はありません")
            return 0

        try:
            # 保存前の妥当性確認と、件数表示のためだけに解析する。
            # 保存自体は編集後の原文をそのまま書き戻す。
            edited = EnvFile.parse_bytes(after)
        except UnicodeDecodeError as e:
            logger.error("編集結果を UTF-8 として読めませんでした: %s", e)
            return 1

        env_file.save_bytes(after)
        logger.info("保存しました: %s (%d変数)", env_file.path, len(edited))
        return 0
    except DevbaseError as e:
        logger.error("%s", e)
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def cmd_env_project(devbase_root: Path) -> int:
    """プロジェクト固有変数の設定（対話式）"""
    env_file = _project_env(devbase_root)
    if env_file is None:
        logger.error("projects/ 配下で実行してください")
        return 1

    project_name = env_file.ref.name
    env_yml_path = Path(devbase_root) / 'projects' / project_name / 'env.yml'
    env_file.load()

    print(f"\n=== {project_name} プロジェクト環境変数 ===")

    if env_yml_path.exists():
        with open(env_yml_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}

        variables = config.get('variables', [])
        for var in variables:
            name = var.get('name', '')
            prompt = var.get('prompt', name)
            default = var.get('default', '')
            required = var.get('required', False)
            generate = var.get('generate', '')

            existing = env_file.get(name)
            if existing:
                print(f"{name}: 設定済み")
                continue

            if generate:
                import secrets
                length = 64
                if ':' in generate:
                    _, length_str = generate.split(':', 1)
                    length = int(length_str)
                value = secrets.token_hex(length // 2)
                env_file.set(name, value)
                print(f"{name}: (自動生成)")
            else:
                suffix = f" (デフォルト: {default})" if default else ""
                suffix += " (必須)" if required else " (空でスキップ)"
                value = safe_input(f"{prompt}{suffix}: ", default)
                if value:
                    env_file.set(name, value)
                elif required:
                    logger.error("必須変数 '%s' が設定されていません", name)
                    return 1
    else:
        print("env.yml が見つかりません。手動で変数を追加してください。")
        print("(Ctrl+Dで終了)")
        try:
            while True:
                line = safe_input("\nKEY=VALUE (空で終了): ")
                if not line:
                    break
                if '=' in line:
                    key, _, value = line.partition('=')
                    env_file.set(key.strip(), value.strip())
                else:
                    print("形式: KEY=VALUE")
        except EOFError:
            pass

    env_file.save()
    logger.info("保存完了: %s (%d変数)", env_file.path, env_file.count())
    return 0


def cmd_env_export(devbase_root: Path, args) -> int:
    """devbase env export"""
    from devbase.env.io_export import ExportOptions, export

    opts = ExportOptions(
        dest=getattr(args, 'dest', None),
        include_global=not getattr(args, 'no_global', False),
        include_metadata=not getattr(args, 'no_metadata', False),
        include_projects=getattr(args, 'include_projects', None),
        exclude_projects=list(getattr(args, 'exclude_projects', []) or []),
        recipients=list(getattr(args, 'recipients', []) or []),
        passphrase_env=getattr(args, 'passphrase_env', None),
        passphrase_stdin=getattr(args, 'passphrase_stdin', False),
        force_unencrypted=getattr(args, 'force_unencrypted', False),
        unsafe_allow_unencrypted_bucket=getattr(
            args, 'unsafe_allow_unencrypted_bucket', False
        ),
    )
    return export(devbase_root, opts)


def cmd_env_import(devbase_root: Path, args) -> int:
    """devbase env import"""
    from devbase.env.io_import import ImportOptions, import_bundle

    replace_keys_arg = getattr(args, 'replace_keys', '') or ''
    replace_keys = [k.strip() for k in replace_keys_arg.split(',') if k.strip()]

    opts = ImportOptions(
        source=getattr(args, 'source'),
        merge=getattr(args, 'merge', 'keep-existing'),
        replace_keys=replace_keys,
        replace=getattr(args, 'replace', False),
        dry_run=getattr(args, 'dry_run', False),
        identities=list(getattr(args, 'identities', []) or []),
        passphrase_env=getattr(args, 'passphrase_env', None),
        passphrase_stdin=getattr(args, 'passphrase_stdin', False),
        include_projects=getattr(args, 'include_projects', None),
        exclude_projects=list(getattr(args, 'exclude_projects', []) or []),
        include_global=not getattr(args, 'no_global', False),
        include_metadata=not getattr(args, 'no_metadata', False),
        merge_metadata=getattr(args, 'merge_metadata', False),
        backup_dir=getattr(args, 'backup_dir', None),
        keep_last=getattr(args, 'keep_last', 10),
    )
    return import_bundle(devbase_root, opts)


def _has_encrypted_secrets(devbase_root: Path) -> bool:
    """暗号化済みの機密が 1 つでも存在するか"""
    from devbase.env.secret_store import SecretStore, SecretRef

    store = SecretStore(devbase_root)
    if store.age.exists(SecretRef.for_global()):
        return True
    return bool(store.project_names())


def _print_key_backup_notice(path, public: str) -> None:
    print()
    print("=" * 60)
    print("鍵のバックアップを必ず取ってください")
    print("=" * 60)
    print(f"  鍵ファイル: {path}")
    print(f"  公開鍵    : {public}")
    print()
    print("  この鍵を失うと、暗号化した機密は誰にも復号できません。")
    print("  パスワード管理ツールなど、端末とは別の場所へ複製を保管してください。")
    print("=" * 60)


def cmd_env_keygen(devbase_root: Path, force: bool = False,
                   assume_yes: bool = False) -> int:
    """devbase 専用の age 鍵を生成する

    生成先は必ず ``agekeys.key_file_path()`` (= ``DEVBASE_AGE_KEY_FILE`` があれば
    それ、無ければ ``~/.config/devbase/age/keys.txt``) にする。生成先を CLI 引数で
    自由に選べるようにすると、復号側の ``agekeys.resolve_identities()`` はそのパスを
    探索しないため「生成した鍵で保存した機密を復号できない」状態を作れてしまう。
    場所を変えたい場合は ``DEVBASE_AGE_KEY_FILE`` を設定してから実行してもらい、
    生成先と探索先が構造的に一致する契約を保つ。
    """
    from devbase.env import agekeys
    from devbase.errors import DevbaseError

    path = agekeys.key_file_path()

    if path.exists() and not force:
        try:
            public = agekeys.read_public_key(path)
        except DevbaseError as e:
            logger.error("%s", e)
            return 1
        print(f"鍵は既に存在します: {path}")
        print(f"  公開鍵: {public}")
        print("  作り直す場合: devbase env keygen --force")
        return 0

    # keygen はワークスペース固有の受信者リスト (secrets/recipients.txt) を触らない。
    # 鍵はグローバル (~/.config/devbase/age/keys.txt) なのに受信者リストは
    # ワークスペースごとに存在するため、ここで書き込むと別ワークスペースには旧公開鍵が
    # 取り残され、既に失われた秘密鍵に対応する公開鍵で暗号化してしまう。
    # agekeys.resolve_recipients() は recipients.txt が無ければ鍵ファイルの公開鍵へ
    # フォールバックするので、単独利用ではリストを作る必要がない。チーム運用で明示的に
    # 受信者を足す経路 (rekey) だけが recipients.txt を作る。
    #
    # 書き込みの原子性は agekeys.generate_key_file →
    # io_common.write_secure_bytes_atomic (一時ファイル + fsync + os.replace) が
    # 担保しており、生成が途中で失敗しても既存の鍵ファイルは元のまま残る。
    # したがってこの層で「内容をメモリへ退避して書き戻す」手動ロールバックは重ねない。
    # 重ねてもリストア自体が失敗しうるぶん壊れ方の種類が増えるだけで、守れるものが
    # 増えないため。将来ここへロールバックを足したくなったら、まず io 層の原子性が
    # 破れていないかを疑うこと。
    #
    # 一方で「既存鍵が在るのに読めない」ときに中止するガードは、原子性とは別の目的で
    # 残す。読めないだけなら権限を直せば回収できる可能性があるのに、生成が成功すると
    # 旧鍵は上書きで確実に消えるため。判定は確認プロンプトより前に置き、
    # 「同意させてから中止する」空振りを避ける。
    if path.exists() and not os.access(path, os.R_OK):
        logger.error(
            "既存の鍵ファイルを読めないため、上書きを中止しました: %s", path)
        logger.error(
            "権限を確認するか、不要と判断できる場合は手動で退避してから"
            "再実行してください")
        return 1

    # ここへ来るのは「鍵が無い」か「--force で作り直す」場合だけ。後者は既存鍵を
    # 捨てる操作なので、常に明示的な同意を取る。
    #
    # 鍵は ~/.config/devbase/age/keys.txt = 全ワークスペース共通のグローバル資産
    # なのに対し、暗号化された機密はワークスペースごとに散らばっている。同意の要否を
    # カレントの DEVBASE_ROOT に機密があるか (_has_encrypted_secrets) で決めると、
    # まだ機密の無い別プロジェクトで --force した瞬間に無警告で鍵が消え、他プロジェクトの
    # 機密が復旧不能になる。カレントの状況は「文言をどれだけ強くするか」にだけ使う。
    if path.exists() and not assume_yes:
        print("鍵ファイルを作り直します。この鍵は全プロジェクト共通です。")
        print(f"  鍵ファイル: {path}")
        if _has_encrypted_secrets(devbase_root):
            print("  このワークスペースには暗号化済みの機密があり、"
                  "旧鍵でしか復号できないものは失われます。")
        print("  他のワークスペースで暗号化した機密も、"
              "旧鍵を失うと復号できなくなります。")
        print("  続行前に旧鍵のバックアップがあるか確認してください。")
        answer = safe_input("続行しますか? (yes と入力): ")
        if answer != 'yes':
            print("中止しました")
            return 1

    # force はコマンドの --force をそのまま渡す。ここで無条件に force=True に
    # すると、上の path.exists() 判定から実際の書き込みまでの隙間に他プロセスが
    # 鍵を作っていた場合、利用者が上書きを要求していないのにその鍵を消してしまう
    # (TOCTOU)。force=False なら agekeys 側が O_CREAT|O_EXCL で作るため、隙間に
    # 現れた鍵は上書きされずエラーで止まる。
    try:
        path, public = agekeys.generate_key_file(path, force=force)
    except (DevbaseError, OSError) as e:
        # 新規生成は排他作成、--force の差し替えは atomic なので、いずれの失敗でも
        # 既に在る鍵はそのまま残っている。
        logger.error("%s", e)
        return 1

    logger.info("鍵を生成しました: %s", path)
    _print_key_backup_notice(path, public)
    return 0


def _update_source_metadata(devbase_root: Path, env_file: EnvFile) -> None:
    """ソースメタデータを更新する"""
    sources = SourcesManager(devbase_root)
    sources.load()

    # AWS
    if env_file.get(keys.AWS_CONFIG_BASE64):
        aws_dir = Path.home() / '.aws'
        files = ["~/.aws/config", "~/.aws/credentials"]
        filenames = ['config', 'credentials']
        h = dir_hash(aws_dir, filenames)
        if h:
            sources.set_source('aws', 'tar_base64', files,
                              keys.AWS_CONFIG_BASE64, h)

    # Git
    if env_file.get(keys.GIT_CREDENTIALS_BASE64):
        cred_path = Path.home() / '.git-credentials'
        h = file_hash(cred_path)
        if h:
            sources.set_source('git_credentials', 'file_base64',
                              ["~/.git-credentials"],
                              keys.GIT_CREDENTIALS_BASE64, h)

    # GCP (プロファイルごと)
    from devbase.env.collectors.google import GCP_CREDENTIALS_DIR, LEGACY_CREDENTIALS_FILE
    all_vars = env_file.get_all()
    prefix = keys.GCP_CREDENTIALS_BASE64_PREFIX

    # 正規化名→実ファイルの逆引きマップを構築
    _gcp_file_map = {}
    if GCP_CREDENTIALS_DIR.is_dir():
        from devbase.env.collectors.google import _safe_profile_name
        _gcp_file_map = {
            _safe_profile_name(f.stem): f
            for f in GCP_CREDENTIALS_DIR.iterdir()
            if f.suffix == '.json' and f.is_file()
        }

    def _resolve_gcp_path(profile_name: str):
        mapped = _gcp_file_map.get(profile_name)
        if mapped and mapped.exists():
            return mapped
        if profile_name == 'default' and LEGACY_CREDENTIALS_FILE.exists():
            return LEGACY_CREDENTIALS_FILE
        return None

    gcp_profiles = {
        name: {'file': str(path), 'hash': file_hash(path)}
        for key in all_vars if key.startswith(prefix)
        for name in [key[len(prefix):]]
        for path in [_resolve_gcp_path(name)]
        if path and path.exists()
    }

    if gcp_profiles:
        active = env_file.get(keys.GCP_ACTIVE_PROFILE, "default")
        sources.set_gcp_source(gcp_profiles, active)

    sources.save()
