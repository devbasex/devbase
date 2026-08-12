"""受信者の更新と、端末上に残る平文の点検

``devbase env rekey`` は誰が機密を復号できるかを変え、``devbase env doctor`` は
「暗号化したつもりで平文が残っていないか」を点検する (plan35 §6)。

暗号化は「平文がどこにも残っていないこと」で初めて意味を持つ。移行の途中で
取り残されたバックアップや、除外設定の穴は黙って残り続けるため、点検する手段を
用意して繰り返し確認できるようにする。
"""

from __future__ import annotations

import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from devbase.env import agekeys
from devbase.env.secret_store import (
    MODE_AGE,
    SecretRef,
    SecretStore,
)
from devbase.env.store import safe_input
from devbase.errors import DevbaseError
from devbase.log import get_logger

logger = get_logger(__name__)


class EnvOpsError(DevbaseError):
    """受信者更新 / 点検の操作エラー"""


# ---------------------------------------------------------------------------
# rekey
# ---------------------------------------------------------------------------

def _encrypted_refs(devbase_root: Path, store: SecretStore) -> List[SecretRef]:
    """暗号化済みの参照をすべて集める"""
    refs: List[SecretRef] = []
    if store.mode(SecretRef.for_global()) == MODE_AGE:
        refs.append(SecretRef.for_global())
    for name in store.project_names():
        ref = SecretRef.for_project(name)
        if store.mode(ref) == MODE_AGE:
            refs.append(ref)
    return refs


def _own_public_key() -> Optional[str]:
    """自分の公開鍵 (専用鍵が無ければ ``None``)"""
    if not agekeys.key_file_path().exists():
        return None
    try:
        return agekeys.read_public_key()
    except DevbaseError:
        return None


def cmd_env_rekey(devbase_root: Path, *,
                  add: Sequence[str] = (),
                  remove: Sequence[str] = (),
                  dry_run: bool = False,
                  assume_yes: bool = False) -> int:
    """受信者を追加・削除し、暗号化済みの機密を新しい受信者宛に暗号化し直す"""
    root = Path(devbase_root)
    store = SecretStore(root)

    try:
        current = agekeys.load_recipients(root)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1

    own = _own_public_key()
    if not current:
        # 受信者リストが無い状態は「自分の鍵だけが受信者」という意味なので、
        # そこから始める。リストを作らずに追加だけすると、自分が受信者から
        # 外れて自分の機密を復号できなくなる。
        if own is None:
            logger.error(
                "受信者リストも devbase 専用鍵もありません。"
                "先に `devbase env keygen` を実行してください")
            return 1
        current = [own]

    updated = list(current)
    for spec in add:
        spec = spec.strip()
        if not spec:
            continue
        try:
            from devbase.env import cipher as _cipher

            _cipher.validate_recipient(spec)
        except DevbaseError as e:
            logger.error("%s", e)
            return 1
        if spec not in updated:
            updated.append(spec)

    missing = [spec for spec in remove if spec.strip() not in updated]
    if missing:
        logger.error("受信者リストに無いため削除できません: %s", ', '.join(missing))
        return 1
    for spec in remove:
        updated = [r for r in updated if r != spec.strip()]

    if not updated:
        logger.error(
            "受信者を全員削除すると、以後の機密を誰も復号できなくなります。"
            "少なくとも 1 人は残してください")
        return 1

    if updated == current:
        print("受信者に変更はありません")
        return 0

    refs = _encrypted_refs(root, store)

    print("\n=== 受信者の変更 ===")
    for spec in updated:
        mark = '+' if spec not in current else ' '
        print(f"  {mark} {spec}")
    for spec in current:
        if spec not in updated:
            print(f"  - {spec}")

    print(f"\n再暗号化する機密: {len(refs)} 件")
    for ref in refs:
        print(f"  {ref.label():<24} {store.age.path(ref)}")

    if own is not None and own not in updated:
        print("\n⚠ 自分の公開鍵が受信者から外れています。"
              "再暗号化後、この端末では機密を復号できなくなります。")

    if dry_run:
        print("\n(--dry-run のため変更していません)")
        return 0

    if not assume_yes and safe_input("続行しますか? (yes と入力): ") != 'yes':
        print("中止しました")
        return 1

    # 先に全件を復号してから書き直す。途中で復号に失敗した場合に、一部だけ
    # 新しい受信者で暗号化された状態を残さないため。
    payloads = []
    for ref in refs:
        try:
            payloads.append((ref, store.age.load_bytes(ref)))
        except DevbaseError as e:
            logger.error("%s を復号できませんでした: %s", ref.label(), e)
            logger.error("受信者リストは変更していません")
            return 1

    try:
        agekeys.save_recipients(root, updated)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1

    rewritten = SecretStore(root, recipients=updated)
    for ref, data in payloads:
        try:
            rewritten.age.save_bytes(ref, data)
        except DevbaseError as e:
            logger.error("%s の再暗号化に失敗しました: %s", ref.label(), e)
            logger.error(
                "受信者リストは更新済みです。原因を解消して "
                "`devbase env rekey` を再実行してください")
            return 1
        logger.info("%s を再暗号化しました", ref.label())

    print(f"\n=== 完了 === (受信者 {len(updated)} 名 / 機密 {len(refs)} 件)")
    return 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    """点検で見つかった問題"""

    level: str           # 'error' | 'warning'
    title: str
    detail: str = ''
    hint: str = ''


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)
    checked: List[str] = field(default_factory=list)

    def add(self, level: str, title: str, detail: str = '', hint: str = '') -> None:
        self.findings.append(Finding(level, title, detail, hint))

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.level == 'error']


#: 平文が残りやすい場所。移行や過去のバックアップで取り残される。
_PLAINTEXT_GLOBS = (
    '.env.bak*',
    '.env.backup*',
    '.env.orig',
    '.env.save',
)

#: 除外設定に必ず入っていてほしいパターン
_REQUIRED_IGNORE_PATTERNS = ('.env', 'secrets/')


def _mode_of(path: Path) -> Optional[int]:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _check_key(report: Report) -> None:
    key_file = agekeys.key_file_path()
    report.checked.append(f"鍵ファイル: {key_file}")
    if not key_file.exists():
        report.add('warning', '暗号化に使う鍵がありません',
                   f'{key_file} が存在しません',
                   '`devbase env keygen` で生成してください')
        return

    mode = _mode_of(key_file)
    if mode is not None and mode & 0o077:
        report.add('error', '鍵ファイルが他ユーザーから読めます',
                   f'{key_file} (mode {mode:04o})',
                   f'chmod 600 {key_file}')

    dir_mode = _mode_of(key_file.parent)
    if dir_mode is not None and dir_mode & 0o077:
        report.add('warning', '鍵の置き場が他ユーザーからアクセスできます',
                   f'{key_file.parent} (mode {dir_mode:04o})',
                   f'chmod 700 {key_file.parent}')


def _check_conflicts(root: Path, store: SecretStore, report: Report) -> None:
    """暗号化ファイルと平文が同時に存在していないか"""
    refs = [SecretRef.for_global()]
    projects_dir = root / 'projects'
    if projects_dir.is_dir():
        refs.extend(SecretRef.for_project(p.name)
                    for p in sorted(projects_dir.iterdir()) if p.is_dir())

    for ref in refs:
        if store.age.exists(ref) and store.plaintext.exists(ref):
            report.add('error', f'{ref.label()}の機密が暗号化・平文の両方にあります',
                       f'暗号化: {store.age.path(ref)}\n'
                       f'    平文:   {store.plaintext.path(ref)}',
                       'どちらが正しいか確認し、不要な方を削除してください')


def _check_leftovers(root: Path, store: SecretStore, report: Report) -> None:
    """移行で取り残された平文を探す"""
    encrypted = store.age.exists(SecretRef.for_global()) or bool(store.project_names())

    backups = root / 'backups'
    for name, label in (('env-encrypt', '暗号化への移行時に退避した平文'),
                        ('env-import', '取り込み時に退避した設定')):
        base = backups / name
        if not base.is_dir():
            continue
        found = [p for p in sorted(base.rglob('*')) if p.is_file()]
        plain = [p for p in found if p.suffix != '.age']
        if plain and (encrypted or name == 'env-encrypt'):
            report.add('warning', f'{label}が残っています',
                       '\n    '.join(str(p) for p in plain[:10])
                       + (f'\n    ... 他 {len(plain) - 10} 件' if len(plain) > 10 else ''),
                       f'内容を確認したうえで削除してください: rm -rf {base}')

    stale: List[Path] = []
    for pattern in _PLAINTEXT_GLOBS:
        stale.extend(sorted(root.glob(pattern)))
        projects_dir = root / 'projects'
        if projects_dir.is_dir():
            stale.extend(sorted(projects_dir.glob(f'*/{pattern}')))
    if stale:
        report.add('warning', '平文の控えファイルが残っています',
                   '\n    '.join(str(p) for p in stale),
                   '不要なら削除してください')


def _check_gitignore(root: Path, report: Report) -> None:
    path = root / '.gitignore'
    report.checked.append(f"除外設定: {path}")
    if not path.is_file():
        report.add('warning', '除外設定がありません', str(path))
        return
    try:
        lines = [line.strip() for line in path.read_text(encoding='utf-8').splitlines()]
    except (OSError, UnicodeDecodeError) as e:
        report.add('warning', '除外設定を読めませんでした', f'{path}: {e}')
        return

    missing = [p for p in _REQUIRED_IGNORE_PATTERNS if p not in lines]
    if missing:
        report.add('error', '除外設定に不足があります',
                   '不足: ' + ', '.join(missing),
                   f'{path} へ追記してください')

    # 日時付きバックアップは `.env.bak` の完全一致では弾けない。実際に
    # `.env.bak-20260807172231` のようなファイルが未追跡で検出された経緯がある。
    if not any(line.startswith('.env.bak') and line.endswith('*') for line in lines):
        report.add('warning', '日時付きの控えファイルが除外されません',
                   '`.env.bak*` のようなパターンがありません',
                   f'{path} へ `.env.bak*` を追加してください')


def cmd_env_doctor(devbase_root: Path) -> int:
    """端末上に残る平文と設定の穴を点検する"""
    root = Path(devbase_root)
    store = SecretStore(root)
    report = Report()

    _check_key(report)
    _check_conflicts(root, store, report)
    _check_leftovers(root, store, report)
    _check_gitignore(root, report)

    print("\n=== devbase env doctor ===")
    for line in report.checked:
        print(f"  確認: {line}")

    if not report.findings:
        print("\n問題は見つかりませんでした")
        return 0

    print()
    for finding in report.findings:
        marker = '✗' if finding.level == 'error' else '!'
        print(f"{marker} {finding.title}")
        if finding.detail:
            print(f"    {finding.detail}")
        if finding.hint:
            print(f"    → {finding.hint}")

    errors = len(report.errors)
    warnings = len(report.findings) - errors
    print(f"\n問題 {errors} 件 / 注意 {warnings} 件")
    # 問題があれば非ゼロで返す。定期実行して気付ける形にするため。
    return 1 if report.findings else 0
