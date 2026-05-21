"""devbase env import の高レベル実装

責務:
  - SOURCE (file / stdio) の読み込み
  - age 復号 (バンドルが暗号化されていれば)
  - tar.gz バンドルの展開と sha256 / manifest version の検証 (bundle.unpack)
  - --merge / --replace-keys / --replace のセマンティクスで .env 群を更新
  - .env.sources.yml は既定で上書きせず参照用コピーのみ (--merge-metadata で
    新規 source のみ追加)
  - 2 フェーズ書き出し (prepare → commit) で部分適用を最小化
  - --backup-dir / --keep-last N で backup を GC
  - --dry-run で差分プレビュー
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

from devbase.errors import DevbaseError
from devbase.log import get_logger

from devbase.env import bundle as _bundle
from devbase.env import cipher as _cipher
from devbase.env import storage as _storage
from devbase.env.store import EnvFile

logger = get_logger(__name__)

# gzip magic. tar.gz バンドルは先頭 2 byte が 0x1f 0x8b。age 暗号化済みは
# テキストヘッダ "age-encryption.org/v1\n" で始まるため magic で識別できる。
_GZIP_MAGIC = b'\x1f\x8b'

_MERGE_MODES = ('keep-existing', 'prefer-incoming')

# _make_backup_dir が生成するタイムスタンプ形式のみを GC 対象にする。
# 以下のいずれかにマッチするディレクトリのみ削除する:
#   YYYYMMDD-HHMMSS                    (旧フォーマット, 後方互換)
#   YYYYMMDD-HHMMSS-NNNNNN             (microsecond 付き)
#   YYYYMMDD-HHMMSS-NNNNNN-NN          (同一マイクロ秒内の連番付き)
# これ以外のディレクトリは devbase が作ったものではないので削除しない
# (--backup-dir 親に無関係なディレクトリがあっても安全)。
_BACKUP_DIR_NAME_RE = re.compile(r'^\d{8}-\d{6}(?:-\d{6}(?:-\d+)?)?$')


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


@dataclass
class _Plan:
    """1 ファイル分の書き出し計画"""
    target: Path
    arcname: str
    new_bytes: bytes
    # 差分サマリ (dry-run / ログ用)
    added_keys: List[str] = field(default_factory=list)
    overwritten_keys: List[str] = field(default_factory=list)
    skipped_keys: List[str] = field(default_factory=list)
    # ファイル単位の操作種別
    op: str = 'merge'  # 'merge' | 'replace' | 'create' | 'sources-merge'


def _read_passphrase(opts: ImportOptions) -> Optional[str]:
    if opts.passphrase_env:
        value = os.environ.get(opts.passphrase_env)
        if not value:
            raise ImportError(f"環境変数 {opts.passphrase_env} が空または未設定です")
        return value
    if opts.passphrase_stdin:
        import sys
        if sys.stdin.isatty():
            print("passphrase: ", end='', file=sys.stderr, flush=True)
        line = sys.stdin.readline()
        if not line:
            raise ImportError("stdin からパスフレーズを読み取れませんでした")
        return line.rstrip('\n')
    return None


def _resolve_identities(specs: Sequence[str]) -> List[str]:
    if specs:
        return list(specs)
    for path in _cipher.default_identity_paths():
        if path.exists():
            logger.info("identity 既定鍵を使用: %s", path)
            return [str(path)]
    return []


def _decrypt_if_needed(blob: bytes, opts: ImportOptions) -> bytes:
    """先頭バイトで暗号化済みかを判定して必要なら復号する"""
    if blob[:2] == _GZIP_MAGIC:
        # 平文 tar.gz。鍵指定があっても無視せず警告にとどめる
        if opts.identities or opts.passphrase_env or opts.passphrase_stdin:
            logger.warning(
                "バンドルは平文ですが identity / passphrase が指定されています "
                "(使用されません)"
            )
        return blob

    passphrase = _read_passphrase(opts)
    if passphrase is not None:
        return _cipher.decrypt(blob, passphrase=passphrase)

    identities = _resolve_identities(opts.identities)
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


def _filter_members(members: Dict[str, bytes],
                    opts: ImportOptions) -> Dict[str, bytes]:
    """include/exclude 指定で展開済みメンバーを絞り込む"""
    included = set(opts.include_projects) if opts.include_projects else None
    excluded = set(opts.exclude_projects)
    result: Dict[str, bytes] = {}

    proj_re = re.compile(r'^env/projects/([^/]+)/\.env$')

    for arcname, data in members.items():
        if arcname == 'env/global.env':
            if not opts.include_global:
                continue
            result[arcname] = data
            continue
        if arcname == 'env/sources.yml':
            if not opts.include_metadata:
                continue
            result[arcname] = data
            continue
        m = proj_re.match(arcname)
        if m:
            name = m.group(1)
            if name in excluded:
                continue
            if included is not None and name not in included:
                continue
            result[arcname] = data
            continue
        # 他の形式は manifest 検証で拒否されているはずだが念のため
        logger.debug("未対応の arcname を無視します: %s", arcname)
    return result


def _target_for(arcname: str, devbase_root: Path) -> Path:
    if arcname == 'env/global.env':
        return devbase_root / '.env'
    if arcname == 'env/sources.yml':
        return devbase_root / '.env.sources.yml'
    m = re.match(r'^env/projects/([^/]+)/\.env$', arcname)
    if m:
        return devbase_root / 'projects' / m.group(1) / '.env'
    raise ImportError(f"未対応のバンドルエントリ: {arcname}")


def _parse_env_bytes(data: bytes) -> Dict[str, str]:
    """EnvFile と同じ規則で bytes をパースする (一時ファイル不要)"""
    return EnvFile.parse_bytes(data)


def _format_env_bytes(data: Dict[str, str]) -> bytes:
    """EnvFile.save と同じフォーマットで dict をバイト列化する"""
    lines: List[str] = []
    for key in sorted(data):
        value = data[key]
        needs_quote = (
            '\n' in value
            or any(c in value for c in (' ', '"', "'", '$', '`', '\\',
                                        '<', '>', '|', '&', ';',
                                        '(', ')', '#'))
        )
        if needs_quote:
            quoted = (value.replace('\\', '\\\\')
                          .replace('"', '\\"')
                          .replace('\n', '\\n'))
            lines.append(f'{key}="{quoted}"\n')
        else:
            lines.append(f'{key}={value}\n')
    return ''.join(lines).encode('utf-8')


def _plan_env_merge(target: Path, incoming_bytes: bytes,
                    opts: ImportOptions, arcname: str) -> _Plan:
    """1 つの .env に対する merge / replace 計画を作る

    既存ファイルが無い (= create) ケースでは、バンドル側の ``incoming_bytes`` を
    そのまま採用する。``_format_env_bytes`` で再シリアライズすると、export 側で
    既に escape された値を parse_bytes 経由でも完全に round-trip できる前提が
    崩れた瞬間に二重エスケープが発生するためである (PR #15 codex 指摘)。
    """
    incoming = _parse_env_bytes(incoming_bytes)
    existing: Dict[str, str] = {}
    if target.exists():
        existing = _parse_env_bytes(target.read_bytes())

    if opts.replace:
        added = sorted(set(incoming) - set(existing))
        overwritten = sorted(k for k in incoming if k in existing and incoming[k] != existing[k])
        # replace は バンドル側の値で完全に置き換える
        return _Plan(
            target=target,
            arcname=arcname,
            new_bytes=incoming_bytes,
            added_keys=added,
            overwritten_keys=overwritten,
            skipped_keys=[],
            op='replace' if existing else 'create',
        )

    if opts.replace_keys:
        merged = dict(existing)
        added: List[str] = []
        overwritten: List[str] = []
        skipped: List[str] = []
        replace_set = set(opts.replace_keys)
        for key, value in incoming.items():
            if key in replace_set:
                if key in existing:
                    if existing[key] != value:
                        overwritten.append(key)
                    merged[key] = value
                else:
                    added.append(key)
                    merged[key] = value
            else:
                # --replace-keys 指定外のキーは keep-existing 相当:
                # 既存にあれば残し、無ければ新規追加 (skipped は overwrite を
                # 抑止した = 上書きしなかったキーのみ)。
                if key in existing:
                    if existing[key] != value:
                        skipped.append(key)
                else:
                    added.append(key)
                    merged[key] = value
        # 新規作成時は incoming_bytes をそのまま保持して二重エスケープを回避
        new_bytes = incoming_bytes if not existing else _format_env_bytes(merged)
        return _Plan(
            target=target,
            arcname=arcname,
            new_bytes=new_bytes,
            added_keys=sorted(added),
            overwritten_keys=sorted(overwritten),
            skipped_keys=sorted(skipped),
            op='merge' if existing else 'create',
        )

    if opts.merge == 'keep-existing':
        merged = dict(existing)
        added: List[str] = []
        skipped: List[str] = []
        for key, value in incoming.items():
            if key in existing:
                skipped.append(key)
            else:
                merged[key] = value
                added.append(key)
        new_bytes = incoming_bytes if not existing else _format_env_bytes(merged)
        return _Plan(
            target=target,
            arcname=arcname,
            new_bytes=new_bytes,
            added_keys=sorted(added),
            overwritten_keys=[],
            skipped_keys=sorted(skipped),
            op='merge' if existing else 'create',
        )

    if opts.merge == 'prefer-incoming':
        merged = dict(existing)
        added: List[str] = []
        overwritten: List[str] = []
        for key, value in incoming.items():
            if key in existing:
                if existing[key] != value:
                    overwritten.append(key)
                merged[key] = value
            else:
                merged[key] = value
                added.append(key)
        new_bytes = incoming_bytes if not existing else _format_env_bytes(merged)
        return _Plan(
            target=target,
            arcname=arcname,
            new_bytes=new_bytes,
            added_keys=sorted(added),
            overwritten_keys=sorted(overwritten),
            skipped_keys=[],
            op='merge' if existing else 'create',
        )

    raise ImportError(f"不明な --merge モード: {opts.merge!r}")


def _plan_sources(target: Path, incoming_bytes: bytes,
                  opts: ImportOptions) -> Optional[_Plan]:
    """.env.sources.yml の取り扱い計画

    既定: 上書きせず None を返す (backup_dir に参照用コピーのみ書く)。
    --merge-metadata: 新規 source エントリのみ追加した内容で更新する。
    """
    if not opts.merge_metadata:
        # 上書きしないので _Plan は返さない。参照用 copy は run() 側で処理。
        return None

    try:
        incoming = yaml.safe_load(incoming_bytes) or {}
    except yaml.YAMLError as e:
        raise ImportError(f"バンドルの sources.yml が壊れています: {e}") from e
    if not isinstance(incoming, dict):
        raise ImportError("バンドルの sources.yml が dict ではありません")
    incoming_sources = incoming.get('sources') or {}
    if not isinstance(incoming_sources, dict):
        raise ImportError("バンドルの sources.yml の sources が dict ではありません")

    existing: Dict = {}
    if target.exists():
        try:
            existing = yaml.safe_load(target.read_bytes()) or {}
        except yaml.YAMLError as e:
            raise ImportError(
                f"既存の {target.name} のパースに失敗しました: {e}"
            ) from e
    if not isinstance(existing, dict):
        existing = {}
    existing.setdefault('sources', {})
    if not isinstance(existing['sources'], dict):
        existing['sources'] = {}

    added: List[str] = []
    merged_sources = dict(existing['sources'])
    for name, entry in incoming_sources.items():
        if name in merged_sources:
            continue
        merged_sources[name] = entry
        added.append(name)

    if not added:
        return None  # 変化なし

    existing['sources'] = merged_sources
    new_bytes = yaml.safe_dump(
        existing, default_flow_style=False, allow_unicode=True
    ).encode('utf-8')
    return _Plan(
        target=target,
        arcname='env/sources.yml',
        new_bytes=new_bytes,
        added_keys=sorted(added),
        overwritten_keys=[],
        skipped_keys=[],
        op='sources-merge',
    )


def _make_backup_dir(devbase_root: Path, opts: ImportOptions) -> Path:
    """バックアップディレクトリを作成する。

    秒精度のみだと同一秒に 2 回 import を走らせたときに同じディレクトリを再利用して
    前回バックアップを上書きしてしまうため、microsecond + 連番を付与して衝突を回避する
    (PR #15 codex 指摘)。
    """
    if opts.backup_dir:
        base = Path(opts.backup_dir).expanduser()
    else:
        base = devbase_root / 'backups' / 'env-import'
    base.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    stem = now.strftime('%Y%m%d-%H%M%S-%f')  # microsecond まで
    path = base / stem
    if not path.exists():
        path.mkdir(parents=True)
        return path
    # 同一マイクロ秒に複数回走った場合の安全弁: 連番を付与
    for n in range(1, 1000):
        candidate = base / f'{stem}-{n:02d}'
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
    raise ImportError(
        f"backup ディレクトリの衝突回避に失敗しました (base={base}, stem={stem})"
    )


def _backup_existing(plans: Sequence[_Plan], sources_copy: Optional[Tuple[Path, bytes]],
                     backup_dir: Path, devbase_root: Path) -> None:
    """phase 1 前に既存ファイルの内容を backup_dir にコピーする"""
    for plan in plans:
        if not plan.target.exists():
            continue
        try:
            relative = plan.target.relative_to(devbase_root)
        except ValueError:
            relative = Path(plan.target.name)
        dst = backup_dir / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plan.target, dst)

    # バンドルに含まれていた sources.yml の参照用コピー (上書きしないケース)
    if sources_copy is not None:
        target, data = sources_copy
        dst = backup_dir / 'sources.yml.imported'
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        try:
            os.chmod(dst, 0o600)
        except OSError:
            pass


def _write_atomic(plan: _Plan) -> Path:
    """phase 1: 新内容を .import.tmp として書き出す (0600)"""
    tmp = plan.target.with_suffix(plan.target.suffix + '.import.tmp')
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        # 過去の失敗の残骸を掃除
        try:
            tmp.unlink()
        except OSError:
            pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(plan.new_bytes)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    return tmp


def _commit(plans_and_tmps: List[Tuple[_Plan, Path]], backup_dir: Path,
            devbase_root: Path) -> List[Path]:
    """phase 2: tmp → target に rename。

    途中失敗時は best-effort で rollback したうえで、まだ rename されていない
    残りの ``.import.tmp`` ファイルもクリーンアップする (PR #15 gemini 指摘)。
    """
    committed: List[Tuple[_Plan, Path]] = []
    remaining_tmps = [tmp for _, tmp in plans_and_tmps]
    try:
        for idx, (plan, tmp) in enumerate(plans_and_tmps):
            os.replace(tmp, plan.target)
            try:
                os.chmod(plan.target, 0o600)
            except OSError:
                pass
            committed.append((plan, plan.target))
            # rename 済みの tmp は残らないが、リストから除外して後続 cleanup を簡潔に
            remaining_tmps[idx] = None  # type: ignore[call-overload]
    except OSError as e:
        logger.error("commit フェーズで失敗しました: %s", e)
        try:
            _rollback(committed, backup_dir, devbase_root)
        finally:
            # rename 前で残っている .import.tmp を後始末
            _cleanup_tmps([t for t in remaining_tmps if t is not None])
        raise ImportError(f"commit フェーズで失敗しました: {e}") from e
    return [t for _, t in committed]


def _rollback(committed: Sequence[Tuple[_Plan, Path]], backup_dir: Path,
              devbase_root: Path) -> None:
    """best-effort ロールバック:
      - 既存上書き (backup あり) → backup から復元
      - backup が無いケース → 元ファイルが存在しなかった (= 新規作成) と
        みなして unlink し、元の「不在」状態に戻す。``op='create'`` だけでなく
        ``op='sources-merge'`` で sources.yml を新規作成したケースもここで
        unlink する (PR #15 gemini 指摘)。

    ``_backup_existing`` は target が存在した場合のみ backup を作る。よって
    「backup が無い」事実は「元ファイルが存在しなかった」ことを示している。
    """
    for plan, target in committed:
        try:
            relative = target.relative_to(devbase_root)
        except ValueError:
            relative = Path(target.name)
        src = backup_dir / relative
        if src.exists():
            try:
                shutil.copy2(src, target)
                logger.warning("rollback: %s を %s から復元しました", target, src)
            except OSError as e:
                logger.error("rollback 失敗: %s -> %s: %s", src, target, e)
        else:
            # 元ファイル不在 → 新規作成された target を unlink して元の状態に戻す
            try:
                target.unlink()
                logger.warning("rollback: 新規作成された %s を削除しました", target)
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.error("rollback unlink 失敗: %s: %s", target, e)


def _cleanup_tmps(tmps: Sequence[Path]) -> None:
    for tmp in tmps:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _gc_backups(backup_dir: Path, keep_last: int) -> None:
    """backup_dir の親ディレクトリ内の古い backup を keep_last 個まで残して GC する。

    安全性のため、削除対象は devbase が生成するタイムスタンプ形式
    (YYYYMMDD-HHMMSS) のディレクトリに限定する。--backup-dir で指定された
    親ディレクトリに無関係なファイル/ディレクトリがあっても、それらは触らない。
    """
    if keep_last <= 0:
        return
    parent = backup_dir.parent
    if not parent.is_dir():
        return
    siblings = sorted(
        (p for p in parent.iterdir()
         if p.is_dir() and _BACKUP_DIR_NAME_RE.match(p.name)),
        key=lambda p: p.name,
    )
    if len(siblings) <= keep_last:
        return
    to_remove = siblings[:-keep_last]
    for d in to_remove:
        try:
            shutil.rmtree(d)
            logger.info("古い backup を削除しました: %s", d)
        except OSError as e:
            logger.warning("backup 削除に失敗 (%s): %s", d, e)


def _log_plans(plans: Sequence[_Plan], dry_run: bool) -> None:
    prefix = "[dry-run] " if dry_run else ""
    for plan in plans:
        logger.info(
            "%s%s: %s (+%d add / ~%d overwrite / -%d skip)",
            prefix, plan.op, plan.target,
            len(plan.added_keys), len(plan.overwritten_keys), len(plan.skipped_keys),
        )
        if plan.added_keys:
            logger.info("  added: %s", ", ".join(plan.added_keys))
        if plan.overwritten_keys:
            logger.info("  overwrite: %s", ", ".join(plan.overwritten_keys))
        if plan.skipped_keys:
            logger.info("  skip (existing kept): %s", ", ".join(plan.skipped_keys))


def import_bundle(devbase_root: Path, opts: ImportOptions) -> int:
    """import 本体。CLI ハンドラから呼ばれる"""
    # 引数の早期検証
    if opts.merge not in _MERGE_MODES:
        raise ImportError(
            f"--merge の値が不正です: {opts.merge!r} (許可: {', '.join(_MERGE_MODES)})"
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

    # SOURCE 読み込み
    backend = _storage.resolve(opts.source)
    blob = backend.read_bytes(opts.source)
    logger.debug("読み込みサイズ: %d bytes", len(blob))

    # 復号 (必要なら) + 展開 + manifest 検証 (sha256 / version)
    tar_blob = _decrypt_if_needed(blob, opts)
    manifest, members = _bundle.unpack(tar_blob)
    logger.info("バンドル version=%s, 生成=%s, devbase=%s",
                manifest.get('version'), manifest.get('created_at'),
                manifest.get('devbase_version'))

    filtered = _filter_members(members, opts)
    if not filtered:
        raise ImportError(
            "import 対象がありません "
            "(--no-global / --include-project の指定とバンドル内容を確認してください)"
        )

    # 計画作成
    plans: List[_Plan] = []
    sources_reference: Optional[Tuple[Path, bytes]] = None
    for arcname, data in sorted(filtered.items()):
        target = _target_for(arcname, devbase_root)
        if arcname == 'env/sources.yml':
            plan = _plan_sources(target, data, opts)
            if plan is not None:
                plans.append(plan)
            else:
                # 既定動作: 上書きしないので参照用 copy のみバックアップする
                sources_reference = (target, data)
        else:
            plans.append(_plan_env_merge(target, data, opts, arcname))

    _log_plans(plans, opts.dry_run)
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

    # backup → phase 1 (tmp 書き出し) → phase 2 (rename)
    backup_dir = _make_backup_dir(devbase_root, opts)
    logger.info("backup ディレクトリ: %s", backup_dir)
    _backup_existing(plans, sources_reference, backup_dir, devbase_root)

    tmps: List[Path] = []
    plans_and_tmps: List[Tuple[_Plan, Path]] = []
    try:
        for plan in plans:
            tmp = _write_atomic(plan)
            tmps.append(tmp)
            plans_and_tmps.append((plan, tmp))
    except Exception:
        _cleanup_tmps(tmps)
        raise

    _commit(plans_and_tmps, backup_dir, devbase_root)
    logger.info("import 完了: %d ファイル更新", len(plans))

    _gc_backups(backup_dir, opts.keep_last)
    return 0
