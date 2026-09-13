"""devbase env import の高レベル実装

責務:
  - SOURCE (file / stdio / s3) の読み込み
  - age 復号 (バンドルが暗号化されていれば)
  - tar.gz バンドルの展開と sha256 / manifest version の検証 (bundle.unpack)
  - merge / replace / replace-keys 計画の作成と適用
  - .env.sources.yml は既定で上書きせず参照用コピーのみ (--merge-metadata で
    新規 source のみ追加)
  - 2 フェーズ書き出し (prepare → commit) で部分適用を最小化
  - --backup-dir / --keep-last N で backup を GC
  - --dry-run で差分プレビュー

実装の詳細は :mod:`_import_merge` (merge 計画) と :mod:`_import_atomic`
(backup / atomic 書き込み / rollback) に分割している。
"""

from __future__ import annotations

import getpass  # noqa: F401  (tests monkey-patch devbase.env.io_import.getpass)
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from devbase.errors import DevbaseError
from devbase.log import get_logger

from devbase.env import _import_atomic as _atomic
from devbase.env import _import_merge as _merge
from devbase.env import bundle as _bundle
from devbase.env import cipher as _cipher
from devbase.env import io_common as _io_common
from devbase.env import storage as _storage

logger = get_logger(__name__)

# 暗号化済みは age テキストヘッダ "age-encryption.org/v1\n" で始まるのに対し、
# 平文 tar.gz は先頭 2 byte が gzip magic (0x1f 0x8b) となる。これで判別する。
_GZIP_MAGIC = b'\x1f\x8b'


class ImportError(DevbaseError):
    """import エラー"""


@dataclass
class ImportOptions:
    source: str
    merge: str = 'keep-existing'
    replace_keys: List[str] = field(default_factory=list)
    replace: bool = False
    dry_run: bool = False
    identities: List[str] = field(default_factory=list)
    passphrase_env: Optional[str] = None
    passphrase_stdin: bool = False
    include_projects: Optional[List[str]] = None
    exclude_projects: List[str] = field(default_factory=list)
    include_global: bool = True
    include_metadata: bool = True
    merge_metadata: bool = False
    backup_dir: Optional[str] = None
    keep_last: int = 10


def _read_passphrase(opts: ImportOptions) -> Optional[str]:
    """既存テストとの互換のために残している thin wrapper。
    実体は :mod:`devbase.env.io_common.read_passphrase`。"""
    return _io_common.read_passphrase(
        opts.passphrase_env, opts.passphrase_stdin, ImportError
    )


def _validate_options(opts: ImportOptions) -> None:
    if opts.merge not in _merge.MERGE_MODES:
        raise ImportError(
            f"--merge の値が不正です: {opts.merge!r} "
            f"(許可: {', '.join(_merge.MERGE_MODES)})"
        )
    if opts.replace and opts.replace_keys:
        raise ImportError("--replace と --replace-keys は併用できません")
    if opts.passphrase_stdin and opts.source == '-':
        raise ImportError(
            "SOURCE='-' (stdin) と --passphrase-stdin は併用できません "
            "(stdin が衝突します)"
        )
    if opts.passphrase_env and opts.passphrase_stdin:
        raise ImportError("--passphrase-env と --passphrase-stdin は併用できません")


def _decrypt_if_needed(blob: bytes, opts: ImportOptions) -> bytes:
    """先頭バイトで暗号化済みかを判定して必要なら復号する"""
    if blob[:2] == _GZIP_MAGIC:
        if opts.identities or opts.passphrase_env or opts.passphrase_stdin:
            logger.warning(
                "バンドルは平文ですが identity / passphrase が指定されています "
                "(使用されません)"
            )
        return blob

    passphrase = _read_passphrase(opts)
    if passphrase is not None:
        return _cipher.decrypt(blob, passphrase=passphrase)

    identities = _io_common.resolve_identity_specs(opts.identities)
    if not identities:
        raise ImportError(
            "バンドルは暗号化されていますが復号キーが指定されていません。\n"
            "  --identity FILE            age / OpenSSH 秘密鍵ファイル\n"
            "  --passphrase-env VAR       環境変数からパスフレーズ取得\n"
            "  --passphrase-stdin         stdin の最初の行をパスフレーズとして使用\n"
            "  ~/.ssh/id_ed25519 または ~/.ssh/id_rsa があれば "
            "--identity 省略時の既定として使用されます (ed25519 優先)"
        )
    return _cipher.decrypt(blob, identities=identities)


def _secret_ref_for(arcname: str):
    """バンドル内 arcname に対応する秘密ストアの参照 (機密でなければ ``None``)"""
    from devbase.env.secret_store import SecretRef

    if arcname == 'env/global.env':
        return SecretRef.for_global()
    match = _merge._PROJECT_ENV_RE.match(arcname)
    if match:
        return SecretRef.for_project(match.group(1))
    return None


def _is_file_backend(store, ref) -> bool:
    """参照の保存先がローカルのファイル (平文 / age) か"""
    from devbase.env.secret_store import AgeBackend, PlaintextBackend

    return isinstance(store.backend_for(ref), (AgeBackend, PlaintextBackend))


def _build_plans(
    filtered: dict, devbase_root: Path, opts: ImportOptions, store=None,
) -> Tuple[List[_merge.Plan], Optional[Tuple[Path, bytes]]]:
    """フィルタ済みメンバーから書き出し計画と sources.yml の参照用コピー対象を返す

    機密の書き出し先は秘密ストアに聞く。暗号化されている環境へ import したときに
    平文の ``.env`` を作ってしまうと、暗号化ファイルと平文が同時に存在する状態に
    なり、以後どちらが正か判断できなくなる (plan35 §9)。既存内容の読み取りと
    書き出しの両方をストア越しに行い、保存形式を維持する。

    保存先がサーバ backend の参照は、``target`` がローカルのファイルではないため
    ``Plan.ref`` に参照を入れて印を付ける。適用は :func:`_apply_via_backend` が行う。
    """
    from dataclasses import replace as _dc_replace

    from devbase.env.secret_store import SecretStore

    store = store if store is not None else SecretStore(devbase_root)
    plans: List[_merge.Plan] = []
    sources_reference: Optional[Tuple[Path, bytes]] = None
    try:
        for arcname, data in sorted(filtered.items()):
            if arcname == 'env/sources.yml':
                target = _merge.target_for(arcname, devbase_root)
                plan = _merge.plan_sources(target, data,
                                           merge_metadata=opts.merge_metadata)
                if plan is not None:
                    plans.append(plan)
                else:
                    sources_reference = (target, data)
                continue

            ref = _secret_ref_for(arcname)
            if ref is None:
                raise _merge.MergeError(f"未対応のバンドルエントリ: {arcname}")

            exists = store.exists(ref)
            plan = _merge.plan_env_merge(
                store.path(ref), data, arcname,
                merge=opts.merge,
                replace=opts.replace,
                replace_keys=opts.replace_keys,
                existing_bytes=store.load_bytes(ref) if exists else b'',
                target_exists=exists,
            )
            if not _is_file_backend(store, ref):
                plan = _dc_replace(plan, ref=ref)
            elif store.backend_for(ref) is store.age:
                # merge の結果は平文のバイト列なので、暗号化されている保存先へ
                # 書く前にここで暗号文へ変換する。以降の原子的書き込み・
                # ロールバックはバイト列とパスだけを扱うため、そのまま通せる。
                # 判定は「暗号化ファイルが存在するか」ではなく「保存先が age か」で
                # 行う。`backend: age` で保存先がまだ無い参照は前者だと平文のまま
                # `.age` へ書かれてしまう。
                plan = _dc_replace(
                    plan, new_bytes=store.age.encrypt_bytes(plan.new_bytes))
            plans.append(plan)
    except _merge.MergeError as e:
        raise ImportError(str(e)) from e
    return plans, sources_reference


def import_bundle(devbase_root: Path, opts: ImportOptions) -> int:
    """import 本体。CLI ハンドラから呼ばれる"""
    _validate_options(opts)

    backend = _storage.resolve(opts.source)
    blob = backend.read_bytes(opts.source)
    logger.debug("読み込みサイズ: %d bytes", len(blob))

    tar_blob = _decrypt_if_needed(blob, opts)
    manifest, members = _bundle.unpack(tar_blob)
    logger.info("バンドル version=%s, 生成=%s, devbase=%s",
                manifest.get('version'), manifest.get('created_at'),
                manifest.get('devbase_version'))

    filtered = _merge.filter_members(
        members,
        include_global=opts.include_global,
        include_metadata=opts.include_metadata,
        include_projects=opts.include_projects,
        exclude_projects=opts.exclude_projects,
    )
    if not filtered:
        raise ImportError(
            "import 対象がありません "
            "(--no-global / --include-project の指定とバンドル内容を確認してください)"
        )

    from devbase.env.secret_store import SecretStore

    store = SecretStore(devbase_root)
    plans, sources_reference = _build_plans(filtered, devbase_root, opts, store=store)

    _merge.log_plans(plans, opts.dry_run)
    if sources_reference is not None and not opts.merge_metadata:
        logger.info(
            "%ssources.yml は上書きしません (--merge-metadata 指定時のみ更新, "
            "参照用コピーを backup ディレクトリに残します)",
            "[dry-run] " if opts.dry_run else "",
        )

    if opts.dry_run:
        logger.info("[dry-run] 書き込みは行いません")
        return 0
    if not plans and sources_reference is None:
        logger.info("変更はありません")
        return 0

    backup_dir = _atomic.make_backup_dir(devbase_root, opts.backup_dir)
    logger.info("backup ディレクトリ: %s", backup_dir)

    local_plans = [p for p in plans if p.ref is None]
    backend_plans = [p for p in plans if p.ref is not None]

    # サーバ backend の退避は取り込みを 1 件も始める前に全件作る。受信者鍵が無い・
    # サーバから読めないといった理由で作れなければ、ここで止まる (設計 2)。
    backend_backups = _backup_via_backend(store, backend_plans, backup_dir)

    _atomic.backup_existing(local_plans, sources_reference, backup_dir, devbase_root)

    plans_and_tmps: List[Tuple[_merge.Plan, Path]] = []
    try:
        for plan in local_plans:
            tmp = _atomic.write_atomic(plan)
            plans_and_tmps.append((plan, tmp))
    except Exception:
        _atomic.cleanup_tmps(tmp for _, tmp in plans_and_tmps)
        raise

    try:
        _atomic.commit(plans_and_tmps, backup_dir, devbase_root)
    except _atomic.AtomicError as e:
        raise ImportError(str(e)) from e

    _apply_via_backend(store, backend_plans, backend_backups)
    logger.info("import 完了: %d ファイル更新", len(plans))

    _atomic.gc_backups(backup_dir, opts.keep_last)
    return 0


def _backup_name(ref) -> str:
    stem = f"{ref.owner}-{ref.kind}" + (f"-{ref.name}" if ref.name else '')
    return f'{stem}.env.age'


def _backup_via_backend(store, plans: List[_merge.Plan], backup_dir: Path
                        ) -> List[Tuple[_merge.Plan, Optional[bytes]]]:
    """サーバ backend の参照を、取り込み前の値を age 暗号化して ``backup_dir`` へ控える。

    Returns:
        ``(計画, 取り込み前の平文バイト列 (無ければ None))`` の並び。巻き戻しに使う
    """
    from devbase.errors import DevbaseError

    saved: List[Tuple[_merge.Plan, Optional[bytes]]] = []
    for plan in plans:
        ref = plan.ref
        try:
            existed = store.exists(ref)
            before = store.load_bytes(ref) if existed else None
            if before is not None:
                blob = store.age.encrypt_bytes(before)
                _io_common.write_secure_bytes_atomic(backup_dir / _backup_name(ref), blob)
        except (DevbaseError, OSError) as e:
            raise ImportError(
                f"{ref.label()}の退避を作れないため、1 件も取り込まずに中止します: {e}") from e
        saved.append((plan, before))
    return saved


def _apply_via_backend(store, plans: List[_merge.Plan],
                       backups: List[Tuple[_merge.Plan, Optional[bytes]]]) -> None:
    """参照ごとに ``save_bytes`` で適用する。失敗した参照までを順に巻き戻す。

    参照をまたぐ一括 rename が持っていた同時性はサーバ backend では作れないため、
    適用済みの参照を控えた値で書き戻す (取り込み前に無かった参照は空にする)。
    **失敗した参照も巻き戻しに含める。** サーバ backend の ``save`` はキー単位で
    反映するため、1 つの参照の途中で失敗しても一部のキーは既に書かれている。
    """
    from devbase.errors import DevbaseError

    before_of = {id(plan): before for plan, before in backups}
    applied: List[_merge.Plan] = []
    for plan in plans:
        try:
            store.save_bytes(plan.ref, plan.new_bytes)
        except DevbaseError as e:
            logger.error("%s の取り込みに失敗しました: %s", plan.ref.label(), e)
            _rollback_via_backend(store, applied + [plan], before_of)
            raise ImportError(f"{plan.ref.label()}の取り込みに失敗しました: {e}") from e
        applied.append(plan)
        logger.info("%s を取り込みました (%s)", plan.ref.label(), store.path(plan.ref))


def _rollback_via_backend(store, applied: List[_merge.Plan], before_of) -> None:
    from devbase.errors import DevbaseError

    for plan in reversed(applied):
        before = before_of.get(id(plan))
        try:
            if before is None:
                store.save(plan.ref, {})
            else:
                store.save_bytes(plan.ref, before)
            logger.warning("rollback: %s を取り込み前の内容へ戻しました", plan.ref.label())
        except DevbaseError as e:
            logger.error("rollback 失敗: %s: %s (WebUI で状態を確認してください)",
                         plan.ref.label(), e)
