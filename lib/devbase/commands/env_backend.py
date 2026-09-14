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
    elif config.backend == _bc.BACKEND_OPENBAO:
        _print_openbao_status(root, store, config)
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


def _print_openbao_status(root: Path, store: SecretStore, config) -> None:
    ob = config.openbao
    print(f"  backend: openbao (設定: {config.source})")
    print(f"  接続先:  {ob.url}")
    print(f"  mount:   {ob.mount}")
    print(f"  個人単位の識別子: {ob.user}")
    print("\n  置き場 (<mount>/<path>):")
    print(f"    チーム共通:           {ob.display_path(SecretRef.for_global())}")
    print(f"    チームのプロジェクト: {ob.mount}/{ob.path_team_project_prefix}/<name>")
    print(f"    個人共通:             {ob.display_path(SecretRef.for_global(owner='user'))}")
    print(f"    個人のプロジェクト:   {ob.mount}/{ob.path_user_prefix}/{ob.user}/projects/<name>")

    try:
        creds = _bootstrap.load(root)
    except DevbaseError as e:
        print(f"\n  接続資格情報: 読めません ({e})")
    else:
        if creds is None:
            print(f"\n  接続資格情報: 未設定 ({_bootstrap.path(root)})")
        else:
            print(f"\n  接続資格情報: role_id {creds.role_id} ({_bootstrap.path(root)})")

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

def _read_secret_id(from_stdin: bool) -> Optional[str]:
    """``secret_id`` を argv 以外の経路で受け取る。

    ``--secret-id-stdin`` なら標準入力の最初の行、TTY なら伏せ字入力。どちらでも
    なければ ``None`` (呼び出し側が不足として扱う)。
    """
    if from_stdin:
        line = sys.stdin.readline()
        if not line:
            raise DevbaseError("stdin から secret_id を読み取れませんでした")
        return line.rstrip('\r\n')
    if sys.stdin.isatty():
        try:
            value = getpass.getpass("secret_id: ", stream=sys.stderr)
        except EOFError as e:
            raise DevbaseError("secret_id を読み取れませんでした") from e
        return value or None
    return None


def _build_openbao_settings(current: Optional[_bc.OpenBaoSettings], args
                            ) -> _bc.OpenBaoSettings:
    """引数と既存の設定から OpenBao の設定を組み立てる (引数が優先)"""
    base = current or _bc.OpenBaoSettings(url='', user='')
    updates = {}
    for name in ('url', 'mount', 'user'):
        value = getattr(args, name, None)
        if value:
            updates[name] = value
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
        # 壊れた設定は読めないが、上書きで直せるようにする。ただし OpenBao の
        # 既存設定は引き継げないので、必須項目は改めて引数で受ける。
        logger.warning("既存の設定を読めないため、引数だけで組み立てます: %s", e)
        current = _bc.BackendConfig()

    cache_arg = getattr(args, 'cache', None)
    cache_enabled = current.cache_enabled if cache_arg is None else bool(cache_arg)
    openbao = current.openbao
    if name == _bc.BACKEND_OPENBAO:
        openbao = _build_openbao_settings(current.openbao, args)

    new_config = _bc.BackendConfig(backend=name, openbao=openbao,
                                   cache_enabled=cache_enabled)
    try:
        new_config.validate()
    except _bc.BackendConfigError as e:
        logger.error("%s", e)
        return EXIT_USAGE

    if name == _bc.BACKEND_OPENBAO:
        rc = _store_credentials(root, args)
        if rc != 0:
            return rc

    try:
        path = _bc.save(root, new_config)
    except _bc.BackendConfigError as e:
        logger.error("%s", e)
        return 1

    print(f"backend を {name} に設定しました: {path}")
    if name == _bc.BACKEND_OPENBAO:
        ob = new_config.openbao
        print(f"  接続先:  {ob.url}")
        print(f"  mount:   {ob.mount}")
        print(f"  個人単位の識別子: {ob.user}")
        print(f"  キャッシュ: {'有効' if cache_enabled else '無効'}")
        print("  接続を確かめる: devbase env backend test")
    return 0


def _store_credentials(root: Path, args) -> int:
    """ブートストラップ機密を用意する。引数が無ければ既存のものを使う。

    設定ファイルより先に書くのは、資格情報を書けない (鍵が無い) 状態で backend だけが
    ``openbao`` に切り替わると、次の実行から機密を 1 件も読めなくなるため。
    """
    role_id = getattr(args, 'role_id', None)
    from_stdin = getattr(args, 'secret_id_stdin', False)

    try:
        stored = _bootstrap.load(root)
    except DevbaseError as e:
        if not role_id:
            logger.error("%s", e)
            return 1
        stored = None

    if not role_id and not from_stdin:
        if stored is not None:
            return 0
        logger.error("接続資格情報がありません。--role-id ID と "
                     "--secret-id-stdin (または TTY での伏せ字入力) で渡してください")
        return EXIT_USAGE

    if not role_id:
        if stored is None:
            logger.error("--role-id が指定されていません")
            return EXIT_USAGE
        role_id = stored.role_id

    try:
        secret_id = _read_secret_id(from_stdin)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1
    if not secret_id:
        if stored is not None and stored.role_id == role_id:
            return 0
        logger.error("secret_id が渡されていません。--secret-id-stdin で "
                     "標準入力から渡すか、TTY で実行してください")
        return EXIT_USAGE

    try:
        _bootstrap.save(root, _bootstrap.Credentials(role_id=role_id, secret_id=secret_id))
    except DevbaseError as e:
        logger.error("%s", e)
        return 1
    return 0


# ---------------------------------------------------------------------------
# test / migrate (後続タスクで実装)
# ---------------------------------------------------------------------------

def cmd_env_backend_test(devbase_root: Path) -> int:
    """サーバへ接続し、参照ごとに読めるかを確かめる (キャッシュへは落ちない)"""
    from devbase.env.openbao import OpenBaoBackend

    root = Path(devbase_root)
    store = SecretStore(root)
    try:
        if store.backend_name != _bc.BACKEND_OPENBAO:
            logger.error("サーバ backend (openbao) が設定されていません (現在: %s)。"
                         "`devbase env backend use openbao ...` で設定してください",
                         store.backend_name)
            return 1
        backend = store.backend_for(SecretRef.for_global())
        assert isinstance(backend, OpenBaoBackend)
        refs: List[SecretRef] = []
        for ref in _team_refs(root):
            refs.append(ref)
            refs.append(SecretRef.for_global(owner='user') if ref.kind == 'global'
                        else SecretRef.for_project(ref.name, owner='user'))
        results = backend.probe(refs)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1

    print(f"\n接続先: {backend.url}")
    print(f"読めた参照: {len(results)} 件")
    for ref, count in results:
        print(f"  {ref.label():<28} {backend.display_path(ref):<40} {count} 変数")
    return 0


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------

MIGRATE_TARGETS = ('age', _bc.BACKEND_OPENBAO)


def cmd_env_backend_migrate(devbase_root: Path, *, to: Optional[str],
                            dry_run: bool = False, assume_yes: bool = False) -> int:
    """チーム単位の機密を別の backend へ写す (PLAN51 決定 7)。

    手順は両方向とも同じ: 移行先の同じ参照を読んで衝突を確かめる → 移行先の内容へ
    移行元を重ねて保存する → 読み戻して一致を確かめる → 一致しなければこの実行で
    作成したキーだけを消す → 成功したときだけ ``backend.yml`` を書き換える。
    移行元の機密は自動削除しない。
    """
    from devbase.env import cache as _cache
    from devbase.env.openbao import OpenBaoBackend

    root = Path(devbase_root)
    if to not in MIGRATE_TARGETS:
        logger.error("--to には %s のいずれかを指定してください: %r",
                     ' / '.join(MIGRATE_TARGETS), to)
        return EXIT_USAGE

    try:
        config = _bc.load(root)
    except _bc.BackendConfigError as e:
        logger.error("%s", e)
        return 1
    if config.openbao is None:
        logger.error("OpenBao の接続設定がありません。先に "
                     "`devbase env backend use openbao --url ... --user ...` "
                     "で設定してください")
        return EXIT_USAGE
    try:
        config.openbao.validate()
    except _bc.BackendConfigError as e:
        logger.error("%s", e)
        return EXIT_USAGE

    # 移行元と移行先は設定ファイルの backend とは無関係に組み立てる。移行の途中で
    # 設定を変えず、成功したときだけ書き換えるため。
    file_store = SecretStore(root, config=_bc.BackendConfig())
    server_store = SecretStore(root, config=_dc_replace(config, backend=_bc.BACKEND_OPENBAO))
    try:
        server = server_store.backend_for(SecretRef.for_global())
    except DevbaseError as e:
        logger.error("%s", e)
        return 1
    assert isinstance(server, OpenBaoBackend)

    plan = _MigrationPlan(root, file_store, server, to)
    try:
        plan.prepare()
    except DevbaseError as e:
        logger.error("%s", e)
        return 1

    if not plan.moves:
        print("移す機密はありません")
        return 0

    plan.print_summary()
    if plan.conflicts:
        print("\n移行先に同じキーがあるため、1 件も書き込まずに中止しました。"
              "移行先で消してから再実行してください")
        return EXIT_USAGE
    if dry_run:
        print("\n(--dry-run のため変更していません)")
        return 0
    if not assume_yes:
        from devbase.env.store import safe_input

        if safe_input("続行しますか? (yes と入力): ") != 'yes':
            print("中止しました")
            return 1

    try:
        plan.apply()
    except DevbaseError as e:
        logger.error("移行を中止しました: %s", e)
        return 1

    # 設定の切り替えを退避より先に行う。逆にすると、設定を書けなかったときに
    # 元のファイルだけが移動済みになり、設定が指す先から機密が読めなくなる。
    try:
        _bc.save(root, _dc_replace(config, backend=to))
    except _bc.BackendConfigError as e:
        logger.error("移行先への書き込みは済みましたが、設定を書き換えられませんでした: %s\n"
                     "  移行元の機密は元の場所に残っています。設定を直してから再実行してください", e)
        return 1

    if to == _bc.BACKEND_OPENBAO:
        backup_dir = plan.move_files_to_backup()
    else:
        _cache.purge(root)
        backup_dir = None

    print(f"\n=== 完了 === backend を {to} に切り替えました")
    if to == _bc.BACKEND_OPENBAO:
        print("元の age / 平文の機密は次の場所へ退避しました。内容を確認したうえで削除してください:")
        print(f"  {backup_dir}")
    else:
        print("サーバ上の機密はそのまま残っています (devbase は消しません):")
        print(f"  接続先: {server.url}")
        for ref, _ in plan.moves:
            print(f"  {ref.label():<24} {server.display_path(ref)}")
    return 0


class _MigrationPlan:
    """1 回の移行の計画と実行"""

    def __init__(self, root: Path, file_store: SecretStore, server, to: str):
        self.root = root
        self.file_store = file_store
        self.server = server
        self.to = to
        #: (参照, 移行元のキー名) の並び
        self.moves: List[tuple] = []
        #: 参照 → 移行先に元からあった内容
        self.existing: dict = {}
        #: 参照 → 移行元の内容
        self.source: dict = {}
        #: 参照 → 衝突したキー名
        self.conflicts: dict = {}

    def _source_backend(self):
        return self.file_store if self.to == _bc.BACKEND_OPENBAO else self.server

    def _dest_backend(self):
        return self.server if self.to == _bc.BACKEND_OPENBAO else self.file_store.age

    def _read_current(self, backend, ref: SecretRef) -> dict:
        """移行元・移行先の現物を読む。

        サーバは ``fetch`` で取り直し、キャッシュへは落ちない。控えで衝突を検査すると、
        控えの後にサーバへ足されたキーとの衝突を見逃し、後続の保存が上書きする。
        取得できなければ書き込み前にここで止まる。
        """
        if backend is self.server:
            return self.server.fetch(ref)
        return backend.load(ref) if backend.exists(ref) else {}

    def prepare(self) -> None:
        for ref in _team_refs(self.root):
            data = self._read_current(self._source_backend(), ref)
            if not data:
                continue
            if self.to == 'age' and self.file_store.plaintext.exists(ref):
                raise DevbaseError(
                    f"{ref.label()}の平文 {self.file_store.plaintext.path(ref)} が残っています。"
                    "age へ移すと暗号化・平文が同時に存在する状態になるため、"
                    "先に `devbase env encrypt` で暗号化するか退避してください")
            current = self._read_current(self._dest_backend(), ref)
            self.source[ref] = data
            self.existing[ref] = current
            self.moves.append((ref, sorted(data)))
            clash = sorted(k for k in data if k in current)
            if clash:
                self.conflicts[ref] = clash

    def print_summary(self) -> None:
        direction = ('age / 平文 → openbao' if self.to == _bc.BACKEND_OPENBAO
                     else 'openbao → age')
        print(f"\n=== 移行する機密 ({direction}) ===")
        for ref, keys in self.moves:
            print(f"  {ref.label():<24} {len(keys)} 件: {', '.join(keys)}")
        if self.conflicts:
            print("\n移行先に同じキーがあります:")
            for ref, keys in self.conflicts.items():
                print(f"  {ref.label():<24} {', '.join(keys)}")

    def apply(self) -> None:
        from devbase.env.openbao import SecretAuthError, SecretConflictError

        dest = self._dest_backend()
        created: dict = {}
        try:
            for ref, keys in self.moves:
                merged = dict(self.existing[ref])
                merged.update(self.source[ref])
                # 結果が分からない失敗に備え、書く前から巻き戻しの対象に入れる。
                # サーバが拒んだと確定した応答 (権限の不足・版の不一致) では何も
                # 書けていないので、その参照は対象から外す
                created[ref] = list(keys)
                try:
                    dest.save(ref, merged)
                except (SecretAuthError, SecretConflictError):
                    created.pop(ref, None)
                    raise
                logger.info("%s を書き込みました", ref.label())
            for ref, _ in self.moves:
                expected = dict(self.existing[ref])
                expected.update(self.source[ref])
                actual = self._read_back(ref)
                if actual != expected:
                    diff = sorted(k for k in expected if actual.get(k) != expected[k])
                    raise DevbaseError(
                        f"{ref.label()}を読み戻した内容が元と一致しません "
                        f"(一致しないキー: {', '.join(diff) or '(不明)'})")
        except DevbaseError:
            self._rollback(created)
            raise

    def _read_back(self, ref: SecretRef) -> dict:
        if self.to == _bc.BACKEND_OPENBAO:
            return self.server.fetch(ref)
        return self.file_store.age.load(ref)

    def _rollback(self, created: dict) -> None:
        """この実行で作成したキーだけを消す。移行先に元からあったキーは触らない。"""
        dest = self._dest_backend()
        for ref, keys in created.items():
            try:
                if self.to == 'age' and not self.existing[ref]:
                    self.file_store.age.remove(ref)
                    continue
                current = self._read_back(ref)
                dest.save(ref, {k: v for k, v in current.items() if k not in keys})
                logger.warning("rollback: %s に作成したキーを消しました", ref.label())
            except DevbaseError as e:
                logger.error("rollback 失敗: %s: %s", ref.label(), e)

    def move_files_to_backup(self) -> Path:
        """移行元の age / 平文ファイルを退避先へ移す (元の場所からは消える)"""
        from datetime import datetime
        import shutil

        backup_dir = (self.root / 'backups' / 'env-backend-migrate'
                      / datetime.now().strftime('%Y%m%d-%H%M%S'))
        backup_dir.mkdir(parents=True, exist_ok=True)
        for ref, _ in self.moves:
            backend = self.file_store.backend_for(ref)
            source = backend.path(ref)
            if not source.is_file():
                continue
            if ref.kind == 'global':
                target = backup_dir / source.name
            else:
                target = backup_dir / 'projects' / (
                    source.name if backend is self.file_store.age else f'{ref.name}.env')
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(source), str(target))
            except OSError as e:
                logger.warning("%s を退避できませんでした (%s): %s", ref.label(), source, e)
        return backup_dir
