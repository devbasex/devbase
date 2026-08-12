"""平文と暗号化構成のあいだを往復する移行コマンド

``devbase env encrypt`` は平文の設定を暗号化ストアへ移し、``devbase env decrypt``
は平文へ戻す。どちらも以下を守る (plan35 §9):

- **無言で消さない**: 元の平文はバックアップへ退避し、削除は利用者に委ねる
- **読み戻せることを確認してから消す**: 暗号化した直後に復号し、元の内容と
  一致した対象だけ平文を退避する。鍵の設定を間違えたまま平文を失うと復旧できない
- **構成ファイルの変更は差分を見せてから行う**: 利用者が独自に編集した
  ``compose.yml`` を黙って書き換えない
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from devbase.env import agekeys, compose_migrate
from devbase.env.secret_store import (
    MODE_AGE,
    MODE_PLAINTEXT,
    SecretRef,
    SecretStore,
)
from devbase.env.store import safe_input
from devbase.errors import DevbaseError
from devbase.log import get_logger

logger = get_logger(__name__)


@dataclass
class Target:
    """移行対象の 1 参照"""

    ref: SecretRef
    values: Dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.ref.label()


def _timestamp() -> str:
    return datetime.now().strftime('%Y%m%d%H%M%S')


def _project_names(devbase_root: Path) -> List[str]:
    projects_dir = Path(devbase_root) / 'projects'
    if not projects_dir.is_dir():
        return []
    return sorted(p.name for p in projects_dir.iterdir() if p.is_dir())


def _select_refs(devbase_root: Path, store: SecretStore, wanted_mode: str,
                 projects: Optional[Sequence[str]]) -> List[SecretRef]:
    """指定された保存形式で存在する参照を集める。

    ``projects`` を指定した場合は共通設定を対象から外す。「このプロジェクトだけ」
    と言われたのに全体に効く共通設定まで動かすと、取り消しの利かない操作を
    利用者の意図より広く実行してしまう。
    """
    refs: List[SecretRef] = []
    if not projects and store.mode(SecretRef.for_global()) == wanted_mode:
        refs.append(SecretRef.for_global())

    names = list(projects) if projects else _project_names(devbase_root)
    for name in names:
        ref = SecretRef.for_project(name)
        if store.mode(ref) == wanted_mode:
            refs.append(ref)
    return refs


def _affected_projects(refs: Sequence[SecretRef]) -> List[str]:
    return [ref.name for ref in refs if ref.kind == 'project' and ref.name]


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    return safe_input(prompt) == 'yes'


# ---------------------------------------------------------------------------
# encrypt
# ---------------------------------------------------------------------------

def cmd_env_encrypt(devbase_root: Path, *, dry_run: bool = False,
                    assume_yes: bool = False,
                    projects: Optional[Sequence[str]] = None) -> int:
    """平文の設定を暗号化ストアへ移す"""
    root = Path(devbase_root)
    store = SecretStore(root)

    try:
        recipients = agekeys.resolve_recipients(root)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1

    refs = _select_refs(root, store, MODE_PLAINTEXT, projects)
    if not refs:
        print("暗号化する平文の設定はありません")
        return 0

    print("\n=== 暗号化する設定 ===")
    for ref in refs:
        print(f"  {ref.label():<24} {store.plaintext.path(ref)}"
              f"  →  {store.age.path(ref)}")
    print(f"\n受信者 ({len(recipients)} 件):")
    for spec in recipients:
        print(f"  {spec}")

    compose_changes = _plan_compose_changes(root, refs)
    if compose_changes:
        print("\n=== コンテナ構成の変更 ===")
        for path, (_, patch) in compose_changes.items():
            print(f"\n--- {path}")
            print(patch, end='' if patch.endswith('\n') else '\n')

    if dry_run:
        print("\n(--dry-run のため変更していません)")
        return 0

    print("\n" + "=" * 60)
    print("暗号化すると、この鍵を失った時点で設定は復旧できなくなります。")
    print(f"  鍵ファイル: {agekeys.key_file_path()}")
    print("  鍵のバックアップを取ってから続行してください。")
    print("=" * 60)
    if not _confirm("続行しますか? (yes と入力): ", assume_yes):
        print("中止しました")
        return 1

    backup_dir = root / 'backups' / 'env-encrypt' / _timestamp()
    moved: List[Path] = []
    for ref in refs:
        values = store.plaintext.load(ref)
        try:
            store.age.save(ref, values)
        except DevbaseError as e:
            logger.error("%s の暗号化に失敗しました: %s", ref.label(), e)
            return 1

        # 読み戻せることを確認してから平文を退避する。鍵の指定を誤ったまま
        # 平文を失うと、誰にも復号できないファイルだけが残る。
        try:
            restored = store.age.load(ref)
        except DevbaseError as e:
            logger.error("%s を暗号化しましたが読み戻せませんでした: %s", ref.label(), e)
            store.age.remove(ref)
            return 1
        if restored != values:
            logger.error("%s の暗号化結果が元の内容と一致しません。中止します", ref.label())
            store.age.remove(ref)
            return 1

        moved.append(_move_to_backup(store.plaintext.path(ref), ref, backup_dir))
        logger.info("%s を暗号化しました: %s", ref.label(), store.age.path(ref))

    _apply_compose_changes(compose_changes)

    print("\n=== 完了 ===")
    print("元の平文は次の場所へ退避しました。内容を確認したうえで削除してください:")
    for path in moved:
        print(f"  {path}")
    print("\n削除する場合:")
    print(f"  rm -rf {backup_dir}")
    return 0


def _move_to_backup(source: Path, ref: SecretRef, backup_dir: Path) -> Path:
    """平文ファイルをバックアップへ移す (コピーではなく移動)"""
    if ref.kind == 'global':
        dest = backup_dir / 'global.env'
    else:
        dest = backup_dir / 'projects' / f'{ref.name}.env'
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    return dest


# ---------------------------------------------------------------------------
# decrypt
# ---------------------------------------------------------------------------

def cmd_env_decrypt(devbase_root: Path, *, dry_run: bool = False,
                    assume_yes: bool = False,
                    projects: Optional[Sequence[str]] = None) -> int:
    """暗号化された設定を平文へ戻す"""
    root = Path(devbase_root)
    store = SecretStore(root)

    refs = _select_refs(root, store, MODE_AGE, projects)
    if not refs:
        print("平文へ戻す暗号化済みの設定はありません")
        return 0

    print("\n=== 平文へ戻す設定 ===")
    for ref in refs:
        print(f"  {ref.label():<24} {store.age.path(ref)}"
              f"  →  {store.plaintext.path(ref)}")

    compose_changes = _plan_compose_changes(root, refs, restore=True)
    if compose_changes:
        print("\n=== コンテナ構成の変更 ===")
        for path, (_, patch) in compose_changes.items():
            print(f"\n--- {path}")
            print(patch, end='' if patch.endswith('\n') else '\n')

    if dry_run:
        print("\n(--dry-run のため変更していません)")
        return 0

    print("\n平文に戻すと、ディスク上に認証情報がそのまま置かれた状態になります。")
    if not _confirm("続行しますか? (yes と入力): ", assume_yes):
        print("中止しました")
        return 1

    for ref in refs:
        try:
            values = store.age.load(ref)
        except DevbaseError as e:
            logger.error("%s を復号できませんでした: %s", ref.label(), e)
            return 1
        store.plaintext.save(ref, values)
        store.age.remove(ref)
        logger.info("%s を平文へ戻しました: %s", ref.label(), store.plaintext.path(ref))

    _apply_compose_changes(compose_changes)
    print("\n=== 完了 ===")
    return 0


# ---------------------------------------------------------------------------
# コンテナ構成の書き換え
# ---------------------------------------------------------------------------

def _plan_compose_changes(devbase_root: Path, refs: Sequence[SecretRef],
                          *, restore: bool = False):
    """``compose.yml`` の書き換え内容を組み立てる (書き込みはしない)。

    Returns:
        ``{パス: (書き換え後のテキスト, 差分)}``
    """
    root = Path(devbase_root)
    has_global = any(ref.kind == 'global' for ref in refs)
    project_names = _affected_projects(refs)

    # 共通の機密を暗号化する場合、その参照は全プロジェクトの構成に現れるため、
    # 対象プロジェクトだけでなく全プロジェクトを見る必要がある。
    targets = _project_names(root) if has_global else project_names
    changes = {}

    for path in compose_migrate.compose_files(root, targets):
        try:
            before = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("構成ファイルを読めませんでした (%s): %s", path, e)
            continue

        if restore:
            after, touched = compose_migrate.enable(before)
        else:
            wanted = set()
            if has_global:
                wanted.add(compose_migrate.TARGET_GLOBAL)
            if path.parent.name in project_names:
                wanted.add(compose_migrate.TARGET_PROJECT)
            after, touched = compose_migrate.disable(before, wanted)

        if touched and after != before:
            changes[path] = (after, compose_migrate.diff(before, after, path))

    return changes


def _apply_compose_changes(changes) -> None:
    for path, (after, _) in changes.items():
        try:
            path.write_text(after, encoding='utf-8')
        except OSError as e:
            logger.error("構成ファイルを更新できませんでした (%s): %s", path, e)
            continue
        logger.info("構成ファイルを更新しました: %s", path)
