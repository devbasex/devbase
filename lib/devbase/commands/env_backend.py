"""``devbase env backend`` — 機密の保存先を選ぶ・確かめる・移す (PLAN51)

サブコマンドは 4 つ:

- ``status``: 現在の backend と保存先、参照ごとの置き場、キャッシュの状態を表示する
- ``use <name>``: backend を切り替える。設定の検証に失敗したときは 1 バイトも書かない
- ``test``: サーバ backend に接続し、参照ごとに読めるかを確かめる
- ``migrate --to <name>``: チーム単位の機密を別の backend へ写す

終了コードは、未知の名前と必須設定の欠落が 2、それ以外の失敗が 1 (設計 2)。
"""

from __future__ import annotations

import getpass
import sys
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import List, Optional

from devbase.env import backend_config as _bc
from devbase.env import bootstrap as _bootstrap
from devbase.env.secret_store import MODE_ABSENT, SecretRef, SecretStore
from devbase.errors import DevbaseError
from devbase.log import get_logger

logger = get_logger(__name__)

EXIT_USAGE = 2


def cmd_env_backend(devbase_root: Path, args) -> int:
    """``devbase env backend`` の振り分け"""
    action = getattr(args, 'backend_action', None)
    handlers = {
        'status': lambda: cmd_env_backend_status(devbase_root),
        'use': lambda: cmd_env_backend_use(devbase_root, args),
        'test': lambda: cmd_env_backend_test(devbase_root),
        'migrate': lambda: cmd_env_backend_migrate(
            devbase_root, to=getattr(args, 'to', None),
            dry_run=getattr(args, 'dry_run', False),
            assume_yes=getattr(args, 'assume_yes', False)),
    }
    handler = handlers.get(action)
    if handler is None:
        logger.error("サブコマンドを指定してください: %s", ', '.join(handlers))
        return EXIT_USAGE
    return handler()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def _team_refs(devbase_root: Path) -> List[SecretRef]:
    """チーム単位の参照 (共通 + 実在するプロジェクト)"""
    refs = [SecretRef.for_global()]
    projects_dir = Path(devbase_root) / 'projects'
    if projects_dir.is_dir():
        for entry in sorted(projects_dir.iterdir()):
            if entry.is_dir():
                try:
                    refs.append(SecretRef.for_project(entry.name))
                except DevbaseError:
                    continue
    return refs


def cmd_env_backend_status(devbase_root: Path) -> int:
    root = Path(devbase_root)
    store = SecretStore(root)
    try:
        config = store.config
    except DevbaseError as e:
        logger.error("%s", e)
        return 1

    print("\n=== 機密の保存先 ===")
    if config.backend == _bc.BACKEND_AUTO:
        _print_auto_status(store)
    elif config.backend == _bc.BACKEND_INFISICAL:
        _print_infisical_status(root, store, config)
    else:
        backend = store.backend_for(SecretRef.for_global())
        print(f"  backend: {backend.name} (設定: {config.source})")
        print(f"  保存先:  {backend.path(SecretRef.for_global())}")
    return 0


def _print_auto_status(store: SecretStore) -> None:
    global_ref = SecretRef.for_global()
    try:
        mode = store.mode(global_ref)
    except DevbaseError as e:
        logger.error("%s", e)
        mode = MODE_ABSENT
    backend = store.backend_for(global_ref)
    name = backend.name if mode == MODE_ABSENT else mode
    print(f"  backend: {name} (設定なし: ファイルの存在で自動判定)")
    print(f"  保存先:  {backend.path(global_ref)}")


def _print_infisical_status(root: Path, store: SecretStore, config) -> None:
    inf = config.infisical
    print(f"  backend: infisical (設定: {config.source})")
    print(f"  接続先:  {inf.url}")
    print(f"  project: {inf.project_id} / environment: {inf.environment}")
    print(f"  個人単位の識別子: {inf.user}")
    print("\n  置き場 (secretPath):")
    print(f"    チーム共通:           {inf.path_team_global}")
    print(f"    チームのプロジェクト: {inf.path_team_project_prefix}/<name>")
    print(f"    個人共通:             {inf.secret_path(SecretRef.for_global(owner='user'))}")
    print(f"    個人のプロジェクト:   {inf.path_user_prefix}/{inf.user}/projects/<name>")

    try:
        creds = _bootstrap.load(root)
    except DevbaseError as e:
        print(f"\n  接続資格情報: 読めません ({e})")
    else:
        if creds is None:
            print(f"\n  接続資格情報: 未設定 ({_bootstrap.path(root)})")
        else:
            print(f"\n  接続資格情報: client ID {creds.client_id} ({_bootstrap.path(root)})")

    _print_cache_status(root, store, config)


def _print_cache_status(root: Path, store: SecretStore, config) -> None:
    from devbase.env import cache as _cache

    print(f"\n  キャッシュ: {'有効' if config.cache_enabled else '無効'} ({_cache.cache_dir(root)})")
    if not config.cache_enabled:
        return
    entries = _cache.read_index(root)
    if not entries:
        print("    (控えはありません)")
        return
    for key, entry in sorted(entries.items()):
        print(f"    {key:<24} 最終取得 {entry.get('fetched_at', '?')}"
              f"  ({entry.get('url_host', '?')})")


# ---------------------------------------------------------------------------
# use
# ---------------------------------------------------------------------------

def _read_client_secret(from_stdin: bool) -> Optional[str]:
    """client secret を argv 以外の経路で受け取る。

    ``--client-secret-stdin`` なら標準入力の最初の行、TTY なら伏せ字入力。どちらでも
    なければ ``None`` (呼び出し側が不足として扱う)。
    """
    if from_stdin:
        line = sys.stdin.readline()
        if not line:
            raise DevbaseError("stdin から client secret を読み取れませんでした")
        return line.rstrip('\r\n')
    if sys.stdin.isatty():
        try:
            value = getpass.getpass("client secret: ", stream=sys.stderr)
        except EOFError as e:
            raise DevbaseError("client secret を読み取れませんでした") from e
        return value or None
    return None


def _build_infisical_settings(current: Optional[_bc.InfisicalSettings], args
                              ) -> _bc.InfisicalSettings:
    """引数と既存の設定から Infisical の設定を組み立てる (引数が優先)"""
    base = current or _bc.InfisicalSettings(url='', project_id='', user='')
    updates = {}
    for arg_name, field_name in (('url', 'url'), ('project_id', 'project_id'),
                                 ('environment', 'environment'), ('user', 'user')):
        value = getattr(args, arg_name, None)
        if value:
            updates[field_name] = value
    return _dc_replace(base, **updates)


def cmd_env_backend_use(devbase_root: Path, args) -> int:
    root = Path(devbase_root)
    name = getattr(args, 'name', None) or ''

    if name not in _bc.BACKEND_NAMES:
        logger.error("backend '%s' は登録されていません (利用できる backend: %s)",
                     name, ', '.join(_bc.BACKEND_NAMES))
        return EXIT_USAGE

    try:
        current = _bc.load(root)
    except _bc.BackendConfigError as e:
        # 壊れた設定は読めないが、上書きで直せるようにする。ただし Infisical の
        # 既存設定は引き継げないので、必須項目は改めて引数で受ける。
        logger.warning("既存の設定を読めないため、引数だけで組み立てます: %s", e)
        current = _bc.BackendConfig()

    cache_enabled = False if getattr(args, 'no_cache', False) else current.cache_enabled
    infisical = current.infisical
    if name == _bc.BACKEND_INFISICAL:
        infisical = _build_infisical_settings(current.infisical, args)

    new_config = _bc.BackendConfig(backend=name, infisical=infisical,
                                   cache_enabled=cache_enabled)
    try:
        new_config.validate()
    except _bc.BackendConfigError as e:
        logger.error("%s", e)
        return EXIT_USAGE

    if name == _bc.BACKEND_INFISICAL:
        rc = _store_credentials(root, args)
        if rc != 0:
            return rc

    try:
        path = _bc.save(root, new_config)
    except _bc.BackendConfigError as e:
        logger.error("%s", e)
        return 1

    print(f"backend を {name} に設定しました: {path}")
    if name == _bc.BACKEND_INFISICAL:
        inf = new_config.infisical
        print(f"  接続先:  {inf.url}")
        print(f"  project: {inf.project_id} / environment: {inf.environment}")
        print(f"  個人単位の識別子: {inf.user}")
        print(f"  キャッシュ: {'有効' if cache_enabled else '無効'}")
        print("  接続を確かめる: devbase env backend test")
    return 0


def _store_credentials(root: Path, args) -> int:
    """ブートストラップ機密を用意する。引数が無ければ既存のものを使う。

    設定ファイルより先に書くのは、資格情報を書けない (鍵が無い) 状態で backend だけが
    ``infisical`` に切り替わると、次の実行から機密を 1 件も読めなくなるため。
    """
    client_id = getattr(args, 'client_id', None)
    from_stdin = getattr(args, 'client_secret_stdin', False)

    try:
        stored = _bootstrap.load(root)
    except DevbaseError as e:
        if not client_id:
            logger.error("%s", e)
            return 1
        stored = None

    if not client_id and not from_stdin:
        if stored is not None:
            return 0
        logger.error("接続資格情報がありません。--client-id ID と "
                     "--client-secret-stdin (または TTY での伏せ字入力) で渡してください")
        return EXIT_USAGE

    if not client_id:
        if stored is None:
            logger.error("--client-id が指定されていません")
            return EXIT_USAGE
        client_id = stored.client_id

    try:
        secret = _read_client_secret(from_stdin)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1
    if not secret:
        if stored is not None and stored.client_id == client_id:
            return 0
        logger.error("client secret が渡されていません。--client-secret-stdin で "
                     "標準入力から渡すか、TTY で実行してください")
        return EXIT_USAGE

    try:
        _bootstrap.save(root, _bootstrap.Credentials(client_id=client_id,
                                                     client_secret=secret))
    except DevbaseError as e:
        logger.error("%s", e)
        return 1
    return 0


# ---------------------------------------------------------------------------
# test / migrate (後続タスクで実装)
# ---------------------------------------------------------------------------

def cmd_env_backend_test(devbase_root: Path) -> int:
    raise NotImplementedError


def cmd_env_backend_migrate(devbase_root: Path, *, to: Optional[str],
                            dry_run: bool = False, assume_yes: bool = False) -> int:
    raise NotImplementedError
