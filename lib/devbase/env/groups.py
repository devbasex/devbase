"""機密の置き場のアカウントグループを決める (PLAN56)

``backend.yml`` が ``version: 2`` (``openbao.layout: group``) のとき、機密の置き場は
アカウントグループ (``DEVBASE_ACCOUNT_GROUP``) ごとに分かれる。どのグループの置き場を
読むかを、**機密を読む前に非機密の ``env`` ファイルだけから**決めるのがこのモジュールである。

決める順は ``projects/<name>/env`` → ``$DEVBASE_ROOT/env`` → ``default`` で、起動ラッパーが
``set -a`` で ``source`` する順に重なった結果と同じになる。

見ないものが 2 つある (決定 3):

- **プロセスの環境変数。** ラッパーは実行時のディレクトリの ``env`` だけを読むため、
  プロジェクトの下位ディレクトリから打つとプロジェクトの ``env`` がプロセスに載らない。
  ファイルを直接読めば、下位ディレクトリからでも同じグループになる
- **機密の置き場。** 置き場を決める値をその置き場から読むと循環する。このため
  :func:`declared_group` はストアを受け取らない

名前の検証はボリューム名と同じ :func:`devbase.volume.manager.resolve_account_group` に
任せ、同じ規則を別の場所へ写さない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from devbase.env import keys
from devbase.env.store import EnvFile
from devbase.errors import DevbaseError
from devbase.log import get_logger

logger = get_logger(__name__)

#: 非機密設定のファイル名 (``$DEVBASE_ROOT/env``、``projects/<name>/env``)
ENV_FILENAME = 'env'


@dataclass(frozen=True)
class DeclaredGroup:
    """宣言されたグループと、それを決めたファイル"""

    name: str
    #: 決めたファイル。どこにも宣言が無く ``default`` になったときは ``None``
    source: Optional[Path]


def _read_declaration(path: Path) -> Optional[str]:
    """``path`` の ``DEVBASE_ACCOUNT_GROUP`` の値。ファイルかキーが無ければ ``None``。

    空の値は ``''`` を返す。ラッパーの ``source`` では空の宣言も共通の宣言を打ち消し、
    ボリュームのグループが ``default`` になるため、ここでも「宣言あり」として扱う。
    """
    if not path.is_file():
        return None
    try:
        data = EnvFile.parse_bytes(path.read_bytes())
    except (OSError, UnicodeDecodeError) as e:
        raise DevbaseError(f"{path} を読めませんでした: {e}") from e
    return data.get(keys.DEVBASE_ACCOUNT_GROUP)


def declare(root: Path, project: Optional[str]) -> DeclaredGroup:
    """グループとその出所を返す。名前が使えなければ出所を添えて ``DevbaseError``。"""
    from devbase.volume.manager import DEFAULT_ACCOUNT_GROUP, resolve_account_group

    root = Path(root)
    candidates = []
    if project:
        candidates.append(root / 'projects' / project / ENV_FILENAME)
    candidates.append(root / ENV_FILENAME)

    for path in candidates:
        value = _read_declaration(path)
        if value is None:
            continue
        try:
            # 空文字は resolve_account_group が default へ解決する。None を渡すと
            # プロセスの環境変数を読むため、必ず文字列で渡す
            name = resolve_account_group(value)
        except DevbaseError as e:
            raise DevbaseError(f"{e} ({path})") from None
        return DeclaredGroup(name=name, source=path)
    return DeclaredGroup(name=DEFAULT_ACCOUNT_GROUP, source=None)


def declared_group(root: Path, project: Optional[str]) -> str:
    """機密の置き場のグループ名 (``projects/<project>/env`` → ``$DEVBASE_ROOT/env`` → ``default``)"""
    return declare(root, project).name


def describe_source(root: Path, declared: DeclaredGroup, project: Optional[str]) -> str:
    """出所の表示 (``projects/web/env`` / ``projects/api/env にも $DEVBASE_ROOT/env にも宣言なし``)"""
    root = Path(root)
    if declared.source is not None:
        try:
            return str(declared.source.relative_to(root))
        except ValueError:
            return str(declared.source)
    if project:
        return f"projects/{project}/env にも $DEVBASE_ROOT/env にも宣言なし"
    return "$DEVBASE_ROOT/env に宣言なし"
