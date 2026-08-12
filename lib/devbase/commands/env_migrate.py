"""平文と暗号化構成のあいだを往復する移行コマンド

``devbase env encrypt`` は平文の設定を暗号化ストアへ移し、``devbase env decrypt``
は平文へ戻す。どちらも以下を守る (plan35 §9):

- **無言で消さない**: 元の平文はバックアップへ退避し、削除は利用者に委ねる。
  退避先は排他的に作り、既存のバックアップへは決して書き込まない
  (:func:`_create_backup_dir`)
- **原文のまま往復させる**: 機密は ``KEY=VALUE`` の辞書へ畳まず、ファイルの
  バイト列のまま暗号化する。``decrypt`` するとコメント・空行・``export``
  表記まで含めて暗号化前のファイルへ戻る。ただし原文が保たれるのは値を
  書き換えるまでで、``devbase env set`` などで更新すると内容は ``EnvFile``
  の書式へ正規化される (平文だけを使っていた頃と同じ挙動)
- **読み戻せることを確認してから消す**: 暗号化した直後に復号し、元の内容と
  一致した対象だけ平文を退避する。鍵の設定を間違えたまま平文を失うと復旧できない
- **構成ファイルの変更は差分を見せてから行う**: 利用者が独自に編集した
  ``compose.yml`` を黙って書き換えない
- **中途半端な状態で終わらない**: 移行は「機密ファイルの移動」と
  ``compose.yml`` の書き換えが噛み合って初めて意味を持つ。どちらか片方だけ
  済んだ状態は「構成ファイルが存在しないファイルを参照する」壊れた設定になる
  ため、実行した操作ごとに取り消し手続きを積み、どこで失敗しても逆順に
  巻き戻してから ``1`` を返す (:class:`devbase.env.rollback.Rollback`)
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from devbase.env import agekeys, compose_migrate, io_common
from devbase.env.rollback import Rollback
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


# 巻き戻し (:class:`devbase.env.rollback.Rollback`) は ``env rekey`` と共有する。
# 移行は複数の破壊的な操作 (暗号化・平文の退避・構成ファイルの書き換え・暗号文の
# 削除) が連なるため、操作ごとに取り消し手続きを積んで逆順に戻せるようにする。


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

    # 構成ファイルを読めない / 自動では直せない機密参照がある場合はここで中止する。
    # 平文にはまだ触れていないので、返すだけで元の状態が保たれる。
    try:
        compose_changes = _plan_compose_changes(root, refs)
    except MigrationError as e:
        logger.error("暗号化を中止しました: %s", e)
        return 1
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

    rollback = Rollback()
    try:
        # 1. 全対象を暗号化して読み戻せることを確認する (平文にはまだ触れない)
        # 2. バックアップ先を排他的に作り、平文をそこへ移す
        # 3. compose.yml を書き換える
        # 平文を消すのは「全対象の暗号文が読み戻せた」と分かってからにする。
        _encrypt_and_verify(store, refs, rollback)
        backup_dir = _create_backup_dir(
            root / 'backups' / 'env-encrypt' / _timestamp())
        # 中身を戻したあとに空のバックアップ先だけ残ると「まだ退避された
        # ものがある」と誤解させるので、作ったディレクトリも巻き戻しで畳む。
        rollback.push(
            f"空になったバックアップ先 {backup_dir} を削除する",
            lambda d=backup_dir: _prune_empty_dirs(d, d))
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
                        rollback: Rollback) -> None:
    """全対象を暗号化し、読み戻して元の内容と一致することを確認する。

    ここでは平文に一切触れない。鍵の指定を誤ったまま平文を失うと、誰にも
    復号できないファイルだけが残るため、「読み戻せた」ことを全対象について
    確かめてから次のフェーズへ進む。途中で失敗しても、この実行で作った
    暗号文を消せば元の状態に戻る。

    運ぶのは辞書ではなく **平文ファイルの生バイト列** である。``KEY=VALUE`` の
    辞書へ畳むとコメント・空行・``export KEY=...`` 表記・値のクォートが落ち、
    ``decrypt`` しても暗号化前の状態には戻らない。バイト列のまま暗号化し、
    バイト列のまま読み戻して一致を確かめる。

    なお原文が保たれるのは **値を書き換えるまで** である。``devbase env set``
    などで値を更新すると辞書経由の ``save`` が走り、内容は ``EnvFile`` の書式へ
    正規化される。平文しか無かった頃と同じ挙動であり、暗号化しても変わらない。
    """
    for ref in refs:
        original = store.plaintext.load_bytes(ref)
        store.age.save_bytes(ref, original)
        # 対象は MODE_PLAINTEXT で選んである = この .age はこの実行で作った
        # ものだけ。巻き戻しで既存の暗号文を巻き添えにする心配はない。
        rollback.push(
            f"{ref.label()}の暗号文 {store.age.path(ref)} を削除する",
            lambda r=ref: store.age.remove(r))

        restored = store.age.load_bytes(ref)
        if restored != original:
            raise MigrationError(
                f"{ref.label()}の暗号化結果が元の内容と一致しません")
        logger.info("%s を暗号化しました: %s", ref.label(), store.age.path(ref))


#: バックアップ先の名前が衝突したときに試す一意な suffix の上限。
#: ここまでぶつかるのは「同じ秒に何十回も移行している」か「先回りして名前を
#: 作られている」異常事態なので、無限に別名を探し続けずに中止して知らせる。
_BACKUP_DIR_MAX_ATTEMPTS = 100


def _create_backup_dir(preferred: Path) -> Path:
    """バックアップ先を **排他的に** 作成し、実際に作れたパスを返す。

    ディレクトリ名は秒単位の日時なので、同じ秒に 2 回移行すると衝突しうる。
    既存のディレクトリへそのまま書くと :func:`shutil.move` が同名の
    ``global.env`` やプロジェクトの env を上書きし、「削除しないはずの過去の
    平文」を失う。そこで ``exist_ok=False`` で作り、既にあれば ``-2`` ``-3`` …
    と一意な名前へ寄せて、**既存のディレクトリへは決して書き込まない**。

    Raises:
        MigrationError: 上限まで試しても空きが見つからない場合、または
            ディレクトリを作成できない場合 (どちらも平文にはまだ触れていない
            段階なので、返せば元の状態が保たれる)
    """
    # 親階層 (backups/env-encrypt) はスナップショットなど他機能とも共有するので
    # 権限は既定のまま。退避先そのものは平文の機密が置かれるため 0700 で作る。
    try:
        preferred.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise MigrationError(
            f"バックアップ先を作成できませんでした ({preferred.parent}): {e}") from e

    for attempt in range(1, _BACKUP_DIR_MAX_ATTEMPTS + 1):
        candidate = (preferred if attempt == 1
                     else preferred.with_name(f'{preferred.name}-{attempt}'))
        try:
            candidate.mkdir(mode=0o700, exist_ok=False)
        except FileExistsError:
            continue
        except OSError as e:
            raise MigrationError(
                f"バックアップ先を作成できませんでした ({candidate}): {e}") from e
        return candidate
    raise MigrationError(
        f"バックアップ先 {preferred} が既にあり、"
        f"{_BACKUP_DIR_MAX_ATTEMPTS} 回試しても空いている名前が見つかりません"
        "でした。既存のバックアップを片付けてから再実行してください")


def _move_plaintext_to_backup(store: SecretStore, refs: Sequence[SecretRef],
                              backup_dir: Path,
                              rollback: Rollback) -> List[Path]:
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
    """平文ファイルをバックアップへ移す (コピーではなく移動)。

    ``backup_dir`` は :func:`_create_backup_dir` がこの実行のために排他的に
    作ったディレクトリで、既存のバックアップとは決して重ならない。その内側の
    ``projects/`` は複数の対象で共有するので ``exist_ok=True`` で掘る
    (対象ごとにファイル名が一意なため、ここで上書きは起こらない)。
    """
    if ref.kind == 'global':
        dest = backup_dir / 'global.env'
    else:
        dest = backup_dir / 'projects' / f'{ref.name}.env'
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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

    try:
        compose_changes = _plan_compose_changes(root, refs, restore=True)
    except MigrationError as e:
        logger.error("復号を中止しました: %s", e)
        return 1
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

    rollback = Rollback()
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
                    ) -> List[Tuple[SecretRef, bytes, bytes]]:
    """全対象の暗号文を読み込み、復号できることを確認する。

    Returns:
        ``(参照, 復号した平文のバイト列, 暗号文のバイト列)`` の並び

    平文は辞書ではなくバイト列で控える。``encrypt`` が原文をそのまま暗号化して
    いるので、そのまま書き戻せばコメント・空行・``export`` 表記まで含めて
    暗号化前のファイルへ戻る。

    暗号文の生バイト列も控える。最後に削除した ``.age`` を、巻き戻しでそのまま
    書き戻せるようにするため (再暗号化すると内容が同じでもバイト列は変わり、
    「元に戻した」と言い切れなくなる)。
    """
    loaded: List[Tuple[SecretRef, bytes, bytes]] = []
    for ref in refs:
        path = store.age.path(ref)
        try:
            blob = path.read_bytes()
        except OSError as e:
            raise MigrationError(f"暗号文を読み込めませんでした ({path}): {e}") from e
        loaded.append((ref, store.age.load_bytes(ref), blob))
    return loaded


def _write_plaintext(store: SecretStore,
                     loaded: Sequence[Tuple[SecretRef, bytes, bytes]],
                     rollback: Rollback) -> None:
    """全対象の平文を書き出す (取り消し: 書いた平文を削除)"""
    for ref, plain, _ in loaded:
        store.plaintext.save_bytes(ref, plain)
        # 対象は MODE_AGE で選んである = この平文はこの実行で作ったものだけ。
        rollback.push(
            f"{ref.label()}の平文 {store.plaintext.path(ref)} を削除する",
            lambda r=ref: store.plaintext.remove(r))
        logger.info("%s を平文へ戻しました: %s", ref.label(),
                    store.plaintext.path(ref))


def _remove_encrypted(store: SecretStore,
                      loaded: Sequence[Tuple[SecretRef, bytes, bytes]],
                      rollback: Rollback) -> None:
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

    暗号化側では、書き換え内容を確定したあとに **YAML としてパースし直して**
    機密参照が残っていないことを確かめる (:func:`_verify_secrets_are_unreferenced`)。
    行ベースの走査が未知の記法を取りこぼしても、ここで必ず中止に落ちる。

    Raises:
        MigrationError: 構成ファイルを読めない場合、自動では書き換えられない
            機密参照が残っている場合、または書き換え後も機密参照が残っている
            ことを事後検証が見つけた場合 (いずれも「平文だけ退避されて構成は
            存在しないファイルを指したまま」という壊れた結果になる)
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
            # `read_text` は改行を LF へ揃えて読む (universal newlines) ため、
            # CRLF の compose.yml を書き戻すとファイル全体の改行コードが
            # 変わってしまう。書き換えた行以外は 1 バイトも動かさない。
            before = path.read_bytes().decode('utf-8')
        except (OSError, UnicodeDecodeError) as e:
            # 読めないファイルを飛ばして続けると、機密の参照が残ったまま平文
            # だけが退避され、コマンドは成功を返す。壊れた構成に気付けるのは
            # 次の起動時になるため、ここで移行全体を中止する。
            raise MigrationError(
                f"構成ファイルを読めませんでした ({path}): {e}") from e

        # 行単位では書き換えられない記法 (インライン配列・続きの行を持つ
        # long syntax など) は対象から漏れる。黙って漏らすと壊れた構成の
        # まま起動して初めて気付くため、どのファイルの何行目かを警告しておく。
        compose_migrate.warn_unsupported_env_file(before, path)

        wanted = _compose_targets(path, has_global=has_global,
                                  project_names=project_names)
        if not restore:
            # 扱えない記法のうち **機密を指しているもの** は警告では済まない。
            # 平文を退避したあとも参照が有効なまま残り、Compose が存在しない
            # ファイルを読もうとして起動できなくなる。手で直してから再実行して
            # もらう (機密と無関係なものは移行に影響しないので警告のみ)。
            #
            # 復元 (decrypt) 側では止めない。平文が戻る以上その参照は有効に
            # なるうえ、ここで失敗させると壊れた状態からの復帰手段まで
            # 塞いでしまう。
            blocking = compose_migrate.secret_unsupported_env_file_lines(
                before, wanted)
            if blocking:
                detail = '\n'.join(f"  {path}:{number}: {line}"
                                   for number, line in blocking)
                raise MigrationError(
                    "自動で書き換えられない env_file の記法が機密ファイルを"
                    "参照しています。次の行を `env_file:` の下に `- ...` を"
                    "並べる書き方へ手で直してから再実行してください:\n"
                    f"{detail}")
        if restore:
            after, touched = compose_migrate.enable(before, wanted)
        else:
            after, touched = compose_migrate.disable(before, wanted)
            # 行ベースの走査が終わったところで、書き換えた結果を YAML として
            # 読み直し、機密参照が本当に消えたことを確かめる。走査は記法の
            # 判別に頼っている以上いつでも取りこぼしうるので、記法に依らない
            # この検証を最後の砦として必ず通す (compose_migrate 冒頭
            # 「二段構えの保証」)。差分が出なかったファイルも対象にする。
            _verify_secrets_are_unreferenced(path, after, wanted)

        if touched and after != before:
            changes[path] = (before, after,
                             compose_migrate.diff(before, after, path))

    return changes


def _verify_secrets_are_unreferenced(path: Path, after: str,
                                     wanted: Set[str]) -> None:
    """書き換え後の ``compose.yml`` に機密参照が残っていないことを確かめる。

    ``compose_migrate`` の書き換えは行ベースなので、YAML の記法が想定から
    外れると (``env_file: >-`` のようなブロックスカラーなど) 参照を取りこぼす。
    取りこぼしたまま進むと「平文だけ退避され、構成は存在しないファイルを指した
    まま」でコマンドが成功してしまう。**記法の判別に依らない事後検証**をここに
    置き、取りこぼしを必ず移行の中止へ落とす。

    復元 (decrypt) 側では行わない。平文が戻る以上その参照は有効で正しく、
    残っていることが期待される状態だからである。

    Raises:
        MigrationError: 参照が残っている場合、または YAML として読めない場合
    """
    try:
        remaining = compose_migrate.remaining_secret_env_file_refs(
            after, wanted)
    except compose_migrate.ComposeParseError as e:
        # 検証できない = 参照が残っていないと言い切れない。読み取り失敗と
        # 同じ扱いで中止する (「たぶん大丈夫」で平文を消してはいけない)。
        raise MigrationError(
            f"構成ファイルを読めませんでした ({path}): {e}") from e

    if remaining:
        detail = '\n'.join(f"  {path}: サービス {service} の env_file: {ref}"
                           for service, ref in remaining)
        raise MigrationError(
            "暗号化で消える機密ファイルへの env_file 参照を自動で外せません"
            "でした。次の参照を手で削除するか、`env_file:` の下に `- ...` を "
            "1 行ずつ並べる書き方へ直してから再実行してください:\n"
            f"{detail}")


#: 既存の ``compose.yml`` の権限を読めなかったときに使う既定値。
#: 機密ではないので ``0600`` ではなく「誰でも読める」側に倒す。
_COMPOSE_FALLBACK_MODE = 0o644


def _compose_file_mode(path: Path) -> int:
    """既存の ``compose.yml`` の権限をそのまま返す。

    書き込みに使う :func:`io_common.write_secure_bytes_atomic` は機密ファイル
    向けに既定が ``0600`` になっている。``compose.yml`` は機密ではなく、他の
    利用者や CI から読めることを前提に置かれているため、**既存の権限を勝手に
    狭めない**よう元の mode を引き継ぐ。
    """
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return _COMPOSE_FALLBACK_MODE


def _write_compose(path: Path, text: str, mode: int) -> None:
    """``compose.yml`` を **原子的に** 差し替える。

    ``Path.write_text`` は既存ファイルを truncate してから書くため、途中で
    ``OSError`` が起きると部分的な ``compose.yml`` が残る。しかもこの書き込みは
    取り消し手続きを積む前に走るので、壊れた内容を巻き戻せない。一時ファイル
    → ``os.replace`` の方式なら、途中で失敗しても元の内容がそのまま残る。
    """
    io_common.write_secure_bytes_atomic(path, text.encode('utf-8'), mode=mode)


def _apply_compose_changes(changes, rollback: Rollback) -> None:
    """計画した書き換えを適用する (取り消し: 元のテキストを書き戻す)。

    1 つでも書けなければ例外で呼び出し元へ返す。ここでログだけ出して次の
    ファイルへ進むと、機密の移動・削除が済んだあとでもコマンドが成功扱いに
    なり、構成ファイルが存在しないファイルを参照したまま残ってしまう。
    書けたぶんの取り消しは既に積んであるので、呼び出し元が巻き戻せる。
    """
    for path, (before, after, _) in changes.items():
        # 元の権限は書き込み前に控える。差し替え後に読むと、こちらが付けた
        # 権限を「元の権限」と取り違える。
        mode = _compose_file_mode(path)
        try:
            _write_compose(path, after, mode)
        except OSError as e:
            raise MigrationError(
                f"構成ファイルを更新できませんでした ({path}): {e}") from e
        rollback.push(
            f"{path} を元の内容へ書き戻す",
            lambda p=path, t=before, m=mode: _write_compose(p, t, m))
        logger.info("構成ファイルを更新しました: %s", path)
