"""受信者の更新と、端末上に残る平文の点検

``devbase env rekey`` は誰が機密を復号できるかを変え、``devbase env doctor`` は
「暗号化したつもりで平文が残っていないか」を点検する (plan35 §6)。

暗号化は「平文がどこにも残っていないこと」で初めて意味を持つ。移行の途中で
取り残されたバックアップや、除外設定の穴は黙って残り続けるため、点検する手段を
用意して繰り返し確認できるようにする。
"""

from __future__ import annotations

import fnmatch
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from devbase.env import agekeys, io_common
from devbase.env.rollback import Rollback
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

    # 受信者リストの更新と全暗号文の差し替えは、**片方だけ済んだ状態を残さない**
    # 単一のまとまりとして扱う。中途半端に終わると旧受信者宛と新受信者宛の暗号文が
    # 混在し、しかも自分の鍵を外す操作だと残った旧暗号文をもう復号できないため、
    # `devbase env rekey` の再実行でも復旧できなくなる。
    #
    # env_migrate と同じ考え方で **破壊的な操作をできるだけ後ろへ寄せ**、実行した
    # 操作ごとに取り消し手続きを積む (Rollback)。失うものが無い準備 (復号・暗号化)
    # を先に全件済ませてからディスクへ触るので、途中で失敗しても巻き戻しは
    # 「控えたバイト列を書き戻す」だけで済む。
    rollback = Rollback()
    try:
        # 1. 全件を復号し、旧暗号文の生バイト列も控える (ディスクは触らない)
        # 2. 新しい受信者宛の暗号文を全件用意する (ここもまだ触らない)
        # 3. 受信者リストを更新する
        # 4. 各暗号文を差し替える
        prepared = _prepare_reencryption(root, store, refs, updated)
        _replace_recipients(root, updated, rollback)
        _replace_ciphertexts(store, prepared, rollback)
    except (DevbaseError, OSError) as e:
        logger.error("受信者の更新を中止し、変更を巻き戻します: %s", e)
        rollback.unwind()
        return 1

    print(f"\n=== 完了 === (受信者 {len(updated)} 名 / 機密 {len(refs)} 件)")
    return 0


def _prepare_reencryption(root: Path, store: SecretStore,
                          refs: Sequence[SecretRef],
                          updated: Sequence[str],
                          ) -> List[Tuple[SecretRef, bytes, bytes]]:
    """全件を復号し、新しい受信者宛の暗号文を用意する (ディスクは触らない)。

    Returns:
        ``(参照, 旧暗号文の生バイト列, 新受信者宛の暗号文)`` の並び

    旧暗号文は再暗号化ではなく **生バイト列のまま** 控える。巻き戻しで元の
    ファイルへ 1 バイト違わず戻せるようにするため (age は暗号化のたびに異なる
    出力になるので、作り直したものでは「元に戻した」と言い切れない)。

    ここで失敗しても、受信者リストも暗号文もまだ 1 つも書き換えていない。
    """
    rewritten = SecretStore(root, recipients=list(updated))
    prepared: List[Tuple[SecretRef, bytes, bytes]] = []
    for ref in refs:
        path = store.age.path(ref)
        try:
            old_blob = path.read_bytes()
        except OSError as e:
            raise EnvOpsError(f"暗号文を読み込めませんでした ({path}): {e}") from e
        try:
            plain = store.age.load_bytes(ref)
        except DevbaseError as e:
            raise EnvOpsError(f"{ref.label()}を復号できませんでした: {e}") from e
        try:
            new_blob = rewritten.age.encrypt_bytes(plain)
        except DevbaseError as e:
            raise EnvOpsError(
                f"{ref.label()}を新しい受信者宛に暗号化できませんでした: {e}") from e
        prepared.append((ref, old_blob, new_blob))
    return prepared


def _write_blob(path: Path, blob: bytes) -> None:
    """暗号文 / 受信者リストを atomic に差し替える。

    書き込みを 1 箇所に集約しておくと、巻き戻し側も同じ経路を通るので
    「戻したつもりで別の書き方をしていた」というずれが起きない。
    """
    io_common.write_secure_bytes_atomic(path, blob)


def _replace_recipients(root: Path, updated: Sequence[str],
                        rollback: Rollback) -> None:
    """受信者リストを差し替える (取り消し: 元の内容へ戻す / 元が無ければ削除)"""
    path = agekeys.recipients_file(root)
    try:
        before = path.read_bytes() if path.is_file() else None
    except OSError as e:
        raise EnvOpsError(f"受信者リストを読み込めませんでした ({path}): {e}") from e

    try:
        agekeys.save_recipients(root, list(updated))
    except OSError as e:
        raise EnvOpsError(f"受信者リストを更新できませんでした ({path}): {e}") from e

    if before is None:
        # 元々リストが無かった場合は「作る前」= 存在しない状態へ戻す。
        rollback.push(f"作成した受信者リスト {path} を削除する",
                      lambda p=path: p.unlink())
    else:
        rollback.push(f"受信者リスト {path} を元の内容へ戻す",
                      lambda p=path, b=before: _write_blob(p, b))


def _replace_ciphertexts(store: SecretStore,
                         prepared: Sequence[Tuple[SecretRef, bytes, bytes]],
                         rollback: Rollback) -> None:
    """用意済みの暗号文でファイルを差し替える (取り消し: 旧バイト列を書き戻す)"""
    for ref, old_blob, new_blob in prepared:
        path = store.age.path(ref)
        try:
            _write_blob(path, new_blob)
        except OSError as e:
            raise EnvOpsError(
                f"{ref.label()}の再暗号化に失敗しました ({path}): {e}") from e
        rollback.push(f"{ref.label()}の暗号文 {path} を元の内容へ戻す",
                      lambda p=path, b=old_blob: _write_blob(p, b))
        logger.info("%s を再暗号化しました", ref.label())


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

#: 日時付きの控えが除外されるかを試すサンプル名。実際に未追跡で検出された
#: ``.env.bak-20260807172231`` を代表として使う。
_BACKUP_SAMPLE_NAME = '.env.bak-20260807172231'


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


def _normalize_ignore_pattern(line: str) -> Optional[str]:
    """``.gitignore`` の 1 行を比較用に正規化する (対象外なら ``None``)。

    点検コマンドは「疑わしきは報告」でよいが、**正しく除外できている設定を毎回
    叱るのは害になる**。無視されて当然と分かる書き方は同じものとして扱う:

    - 前後の空白を落とす (``.env  `` は git も末尾空白を無視する)
    - 空行と ``#`` 始まりのコメント行は対象外
    - 行末コメント (`` #`` 以降) を落とす。gitignore の厳密な文法では ``#`` 以降も
      パターンの一部だが、実運用では ``.env    # 機密`` のように書かれるため許容する
    - 先頭の ``/`` (リポジトリルート指定) と ``**/`` (任意階層) を落とす。
      ``/.env`` は ``$DEVBASE_ROOT/.env`` を確実に除外できており、不足ではない
    - 末尾の ``/`` (ディレクトリ指定) を落とす。``secrets`` と ``secrets/`` は
      ここで見たい「secrets を除外しているか」に関しては同じ意味になる

    逆に、次は **検出漏れとして受け入れる** (判定を素朴に保つほうが利益が大きい):

    - ``!`` 始まりの再包含は「除外している根拠」にならないので対象外にするが、
      ``.env`` と ``!.env`` が両方ある矛盾した設定までは追わない
    - ``secrets/*.age`` のように配下の一部だけを除外する書き方は不足として報告する
      (実際に平文が漏れうるので、報告する側に倒す)
    - ``\\`` のエスケープや文字クラスは解釈しない
    """
    text = line.strip()
    if not text or text.startswith('#'):
        return None
    if text.startswith('!'):
        return None
    comment = text.find(' #')
    if comment >= 0:
        text = text[:comment].strip()
    if not text:
        return None
    while text.startswith('**/'):
        text = text[3:]
    text = text.lstrip('/')
    text = text.rstrip('/')
    return text or None


def _check_gitignore(root: Path, report: Report) -> None:
    path = root / '.gitignore'
    report.checked.append(f"除外設定: {path}")
    if not path.is_file():
        report.add('warning', '除外設定がありません', str(path))
        return
    try:
        raw_lines = path.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError) as e:
        report.add('warning', '除外設定を読めませんでした', f'{path}: {e}')
        return

    patterns = [p for p in (_normalize_ignore_pattern(line) for line in raw_lines)
                if p is not None]

    # 必須パターン側も同じ規則で正規化してから突き合わせる。報告に出す名前は
    # 利用者が追記しやすいよう元の表記 (`secrets/`) のままにしておく。
    missing = [p for p in _REQUIRED_IGNORE_PATTERNS
               if _normalize_ignore_pattern(p) not in patterns]
    if missing:
        report.add('error', '除外設定に不足があります',
                   '不足: ' + ', '.join(missing),
                   f'{path} へ追記してください')

    # 日時付きバックアップは `.env.bak` の完全一致では弾けない。実際に
    # `.env.bak-20260807172231` のようなファイルが未追跡で検出された経緯がある。
    # 「`.env.bak` で始まり `*` で終わる」かどうかではなく、代表的な名前に実際に
    # マッチするかで見る。`.env.bak*` だけでなく `.env*` のような広い指定も
    # ちゃんと除外できており、不足として叱る理由がないため。
    if not any(fnmatch.fnmatch(_BACKUP_SAMPLE_NAME, p) for p in patterns):
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
