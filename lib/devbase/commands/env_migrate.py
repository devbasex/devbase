"""平文と暗号化構成のあいだを往復する移行コマンド

``devbase env encrypt`` は平文の設定を暗号化ストアへ移し、``devbase env decrypt``
は平文へ戻す。どちらも以下を守る (plan35 §9):

- **無言で消さない**: 元の平文はバックアップへ退避し、削除は利用者に委ねる
- **読み戻せることを確認してから消す**: 暗号化した直後に復号し、元の内容と
  一致した対象だけ平文を退避する。鍵の設定を間違えたまま平文を失うと復旧できない
- **構成ファイルの変更は差分を見せてから行う**: 利用者が独自に編集した
  ``compose.yml`` を黙って書き換えない
- **中途半端な状態で終わらない**: 移行は「機密ファイルの移動」と
  ``compose.yml`` の書き換えが噛み合って初めて意味を持つ。どちらか片方だけ
  済んだ状態は「構成ファイルが存在しないファイルを参照する」壊れた設定になる
  ため、実行した操作ごとに取り消し手続きを積み、どこで失敗しても逆順に
  巻き戻してから ``1`` を返す (:class:`_Rollback`)
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from devbase.env import agekeys, compose_migrate, io_common
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


class MigrationError(DevbaseError):
    """移行を中止して巻き戻すべき失敗"""


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
# 巻き戻し
# ---------------------------------------------------------------------------

class _Rollback:
    """実行した操作の取り消し手続きを積み、失敗時に逆順で実行する。

    移行は複数の破壊的な操作 (暗号化・平文の退避・構成ファイルの書き換え・
    暗号文の削除) が連なる。「全部検証してから全部実行する」とフェーズを
    分けるだけでは、実行フェーズの途中で失敗したぶんが中間状態として残る。
    そこで **操作を 1 つ実行するたびにその取り消し手続きを積み**、どこで
    失敗しても :meth:`unwind` で逆順に巻き戻せるようにする。
    """

    def __init__(self) -> None:
        self._undo: List[Tuple[str, Callable[[], None]]] = []

    def push(self, description: str, undo: Callable[[], None]) -> None:
        """実行済みの操作に対する取り消し手続きを積む。

        Args:
            description: 取り消しが何をするか (巻き戻しに失敗したときに
                「何が残っているか」として利用者へ見せる)
            undo: 取り消し手続き
        """
        self._undo.append((description, undo))

    def unwind(self) -> None:
        """積んだ取り消し手続きを逆順に実行する。

        後の操作は前の操作を前提にしているため、必ず逆順で戻す。巻き戻しの
        途中で失敗しても残りは試みるが、**握り潰さずに何が残っているかを
        具体的に列挙する**。ここで黙ると、利用者は壊れた状態に気付けない。
        """
        failures: List[str] = []
        for description, undo in reversed(self._undo):
            try:
                undo()
            except Exception as e:  # 1 つ失敗しても残りの巻き戻しは続ける
                failures.append(f"  - {description}: {e}")
        self._undo.clear()
        if failures:
            logger.error(
                "巻き戻しに失敗しました。次の操作が完了しておらず、"
                "手動での復旧が必要です:\n%s", "\n".join(failures))


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
        for path, (_, _, patch) in compose_changes.items():
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
    rollback = _Rollback()
    try:
        # 1. 全対象を暗号化して読み戻せることを確認する (平文にはまだ触れない)
        # 2. 平文をバックアップへ移す
        # 3. compose.yml を書き換える
        # 平文を消すのは「全対象の暗号文が読み戻せた」と分かってからにする。
        _encrypt_and_verify(store, refs, rollback)
        moved = _move_plaintext_to_backup(store, refs, backup_dir, rollback)
        _apply_compose_changes(compose_changes, rollback)
    except (DevbaseError, OSError) as e:
        logger.error("暗号化を中止し、変更を巻き戻します: %s", e)
        rollback.unwind()
        return 1

    print("\n=== 完了 ===")
    print("元の平文は次の場所へ退避しました。内容を確認したうえで削除してください:")
    for path in moved:
        print(f"  {path}")
    print("\n削除する場合:")
    print(f"  rm -rf {backup_dir}")
    return 0


def _encrypt_and_verify(store: SecretStore, refs: Sequence[SecretRef],
                        rollback: _Rollback) -> None:
    """全対象を暗号化し、読み戻して元の内容と一致することを確認する。

    ここでは平文に一切触れない。鍵の指定を誤ったまま平文を失うと、誰にも
    復号できないファイルだけが残るため、「読み戻せた」ことを全対象について
    確かめてから次のフェーズへ進む。途中で失敗しても、この実行で作った
    暗号文を消せば元の状態に戻る。
    """
    for ref in refs:
        values = store.plaintext.load(ref)
        store.age.save(ref, values)
        # 対象は MODE_PLAINTEXT で選んである = この .age はこの実行で作った
        # ものだけ。巻き戻しで既存の暗号文を巻き添えにする心配はない。
        rollback.push(
            f"{ref.label()}の暗号文 {store.age.path(ref)} を削除する",
            lambda r=ref: store.age.remove(r))

        restored = store.age.load(ref)
        if restored != values:
            raise MigrationError(
                f"{ref.label()}の暗号化結果が元の内容と一致しません")
        logger.info("%s を暗号化しました: %s", ref.label(), store.age.path(ref))


def _move_plaintext_to_backup(store: SecretStore, refs: Sequence[SecretRef],
                              backup_dir: Path,
                              rollback: _Rollback) -> List[Path]:
    """全対象の平文をバックアップへ移す (取り消し: 元の場所へ戻す)"""
    moved: List[Path] = []
    for ref in refs:
        source = store.plaintext.path(ref)
        dest = _move_to_backup(source, ref, backup_dir)
        rollback.push(
            f"{ref.label()}の平文を {source} へ戻す",
            lambda s=source, d=dest: _move_back(d, s, backup_dir))
        moved.append(dest)
    return moved


def _move_to_backup(source: Path, ref: SecretRef, backup_dir: Path) -> Path:
    """平文ファイルをバックアップへ移す (コピーではなく移動)"""
    if ref.kind == 'global':
        dest = backup_dir / 'global.env'
    else:
        dest = backup_dir / 'projects' / f'{ref.name}.env'
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    return dest


def _move_back(dest: Path, source: Path, backup_dir: Path) -> None:
    """バックアップへ移した平文を元の場所へ戻す"""
    shutil.move(str(dest), str(source))
    _prune_empty_dirs(dest.parent, backup_dir)


def _prune_empty_dirs(start: Path, stop: Path) -> None:
    """``start`` から ``stop`` まで、空になったディレクトリを畳む。

    中身を戻したのに空のバックアップディレクトリだけ残ると「まだ退避された
    ものがある」と誤解させる。見た目の掃除でしかないので、消せなくても
    巻き戻しの失敗としては扱わない (中身は既に元の場所へ戻っている)。
    """
    current = start
    while True:
        try:
            os.rmdir(current)
        except OSError:
            return
        if current == stop or current == current.parent:
            return
        current = current.parent


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
        for path, (_, _, patch) in compose_changes.items():
            print(f"\n--- {path}")
            print(patch, end='' if patch.endswith('\n') else '\n')

    if dry_run:
        print("\n(--dry-run のため変更していません)")
        return 0

    print("\n平文に戻すと、ディスク上に認証情報がそのまま置かれた状態になります。")
    if not _confirm("続行しますか? (yes と入力): ", assume_yes):
        print("中止しました")
        return 1

    rollback = _Rollback()
    try:
        # 1. 全対象の暗号文を読み込み、復号できることを確認する
        # 2. 平文を書き出す
        # 3. compose.yml を復元する
        # 4. 最後に暗号文を削除する
        #
        # 破壊的な削除を最後に置くのは、途中で失敗したときに失うものを最小に
        # するため。2 や 3 で失敗しても暗号文はまだディスク上にあり、巻き戻しは
        # 「書いた平文を消す」だけで済む。逆に先に消してしまうと、以降の失敗の
        # 巻き戻しがメモリ上の内容頼みになり、復旧の余地が狭くなる。
        loaded = _load_encrypted(store, refs)
        _write_plaintext(store, loaded, rollback)
        _apply_compose_changes(compose_changes, rollback)
        _remove_encrypted(store, loaded, rollback)
    except (DevbaseError, OSError) as e:
        logger.error("復号を中止し、変更を巻き戻します: %s", e)
        rollback.unwind()
        return 1

    print("\n=== 完了 ===")
    return 0


def _load_encrypted(store: SecretStore, refs: Sequence[SecretRef],
                    ) -> List[Tuple[SecretRef, Dict[str, str], bytes]]:
    """全対象の暗号文を読み込み、復号できることを確認する。

    生バイト列も控える。最後に削除した ``.age`` を、巻き戻しでそのまま
    書き戻せるようにするため (再暗号化すると内容が同じでもバイト列は変わり、
    「元に戻した」と言い切れなくなる)。
    """
    loaded: List[Tuple[SecretRef, Dict[str, str], bytes]] = []
    for ref in refs:
        path = store.age.path(ref)
        try:
            blob = path.read_bytes()
        except OSError as e:
            raise MigrationError(f"暗号文を読み込めませんでした ({path}): {e}") from e
        loaded.append((ref, store.age.load(ref), blob))
    return loaded


def _write_plaintext(store: SecretStore,
                     loaded: Sequence[Tuple[SecretRef, Dict[str, str], bytes]],
                     rollback: _Rollback) -> None:
    """全対象の平文を書き出す (取り消し: 書いた平文を削除)"""
    for ref, values, _ in loaded:
        store.plaintext.save(ref, values)
        # 対象は MODE_AGE で選んである = この平文はこの実行で作ったものだけ。
        rollback.push(
            f"{ref.label()}の平文 {store.plaintext.path(ref)} を削除する",
            lambda r=ref: store.plaintext.remove(r))
        logger.info("%s を平文へ戻しました: %s", ref.label(),
                    store.plaintext.path(ref))


def _remove_encrypted(store: SecretStore,
                      loaded: Sequence[Tuple[SecretRef, Dict[str, str], bytes]],
                      rollback: _Rollback) -> None:
    """暗号文を削除する (取り消し: 控えた生バイト列で復元)"""
    for ref, _, blob in loaded:
        path = store.age.path(ref)
        store.age.remove(ref)
        rollback.push(
            f"{ref.label()}の暗号文 {path} を復元する",
            lambda p=path, b=blob: io_common.write_secure_bytes_atomic(p, b))


# ---------------------------------------------------------------------------
# コンテナ構成の書き換え
# ---------------------------------------------------------------------------

def _compose_targets(path: Path, *, has_global: bool,
                     project_names: Sequence[str]) -> Set[str]:
    """この ``compose.yml`` で触ってよい参照の種別を決める。

    暗号化 (無効化) と復号 (復元) で同じ判定を使う。「一部だけ復号したのに
    全マーカーを戻す」と、まだ暗号化されたままの共通設定への参照まで有効に
    なり、存在しないファイルを指したまま Compose が起動に失敗する。
    """
    wanted: Set[str] = set()
    if has_global:
        wanted.add(compose_migrate.TARGET_GLOBAL)
    if path.parent.name in project_names:
        wanted.add(compose_migrate.TARGET_PROJECT)
    return wanted


def _plan_compose_changes(devbase_root: Path, refs: Sequence[SecretRef],
                          *, restore: bool = False):
    """``compose.yml`` の書き換え内容を組み立てる (書き込みはしない)。

    Returns:
        ``{パス: (書き換え前のテキスト, 書き換え後のテキスト, 差分)}``

    書き換え前のテキストも返すのは、適用後に別の操作が失敗したとき、
    差分を計算したのと同じ内容へ書き戻して巻き戻せるようにするため。
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

        # 行単位では書き換えられない記法 (インライン配列・単一文字列) は
        # 対象から漏れる。黙って漏らすと壊れた構成のまま起動して初めて
        # 気付くため、どのファイルの何行目かを警告しておく。
        compose_migrate.warn_unsupported_env_file(before, path)

        wanted = _compose_targets(path, has_global=has_global,
                                  project_names=project_names)
        if restore:
            after, touched = compose_migrate.enable(before, wanted)
        else:
            after, touched = compose_migrate.disable(before, wanted)

        if touched and after != before:
            changes[path] = (before, after,
                             compose_migrate.diff(before, after, path))

    return changes


def _apply_compose_changes(changes, rollback: _Rollback) -> None:
    """計画した書き換えを適用する (取り消し: 元のテキストを書き戻す)。

    1 つでも書けなければ例外で呼び出し元へ返す。ここでログだけ出して次の
    ファイルへ進むと、機密の移動・削除が済んだあとでもコマンドが成功扱いに
    なり、構成ファイルが存在しないファイルを参照したまま残ってしまう。
    書けたぶんの取り消しは既に積んであるので、呼び出し元が巻き戻せる。
    """
    for path, (before, after, _) in changes.items():
        try:
            path.write_text(after, encoding='utf-8')
        except OSError as e:
            raise MigrationError(
                f"構成ファイルを更新できませんでした ({path}): {e}") from e
        rollback.push(
            f"{path} を元の内容へ書き戻す",
            lambda p=path, t=before: p.write_text(t, encoding='utf-8'))
        logger.info("構成ファイルを更新しました: %s", path)
