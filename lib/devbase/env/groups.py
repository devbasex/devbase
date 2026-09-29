"""プロジェクトのアカウントグループの宣言を読む (PLAN56 / #315)

アカウントグループ (``DEVBASE_ACCOUNT_GROUP``) は、ホームのボリューム
``devbase_home_<group>`` と、``backend.yml`` が ``version: 2`` (``openbao.layout: group``) の
ときの機密の置き場を分ける単位である。どのグループかを、**機密を読む前に非機密の
``env`` ファイルだけから**決めるのがこのモジュールである。

グループを決める出所は ``projects/<name>/env`` の空でない宣言だけである (#315)。既定の
値は持たず、``$DEVBASE_ROOT/env`` に宣言があれば (名前を変えた既定になるため) 止める。

見ないものが 2 つある (PLAN56 決定 3):

- **プロセスの環境変数。** ラッパーは実行時のディレクトリの ``env`` だけを読むため、
  プロジェクトの下位ディレクトリから打つとプロジェクトの ``env`` がプロセスに載らない。
  ファイルを直接読めば、下位ディレクトリからでも同じグループになる
- **機密の置き場。** 置き場を決める値をその置き場から読むと循環する。このため
  :func:`declare` はストアを受け取らない

名前の検証はボリューム名と同じ :func:`devbase.volume.manager.validate_account_group` に
任せ、同じ規則を別の場所へ写さない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from devbase.env import keys
from devbase.env.store import EnvFile
from devbase.errors import DevbaseError
from devbase.log import get_logger

logger = get_logger(__name__)

#: 非機密設定のファイル名 (``$DEVBASE_ROOT/env``、``projects/<name>/env``)
ENV_FILENAME = 'env'


class GroupDeclarationError(DevbaseError):
    """グループの宣言が無い・空・使えない名前・``$DEVBASE_ROOT/env`` に行がある。

    文は直すファイルと行を名指しする。
    """


class GroupRequiredError(DevbaseError):
    """グループ別の置き場で、プロジェクトの外なのにグループが渡らない"""


@dataclass(frozen=True)
class DeclaredGroup:
    """宣言されたグループと、それを書いたファイルと行"""

    name: str
    #: 宣言を書いたファイル (常に ``projects/<name>/env``)
    source: Path
    #: 宣言の行番号 (1 始まり)
    line: int


def _read_declaration(path: Path) -> Optional[Tuple[str, int]]:
    """``path`` の ``DEVBASE_ACCOUNT_GROUP`` の値と行番号。ファイルかキーが無ければ ``None``。

    空の値は ``''`` を返す (呼び出し元が「空の宣言」として止める)。
    """
    if not path.is_file():
        return None
    try:
        entries = EnvFile.parse_entries(path.read_bytes())
    except (OSError, UnicodeDecodeError) as e:
        raise DevbaseError(f"{path} を読めませんでした: {e}") from e
    # ラッパーの ``source`` と ``_load_project_env`` は ``export KEY=...`` も読み、後に書いた行が
    # 勝つ。パーサは ``export KEY`` をキーにし、辞書にすると同じキーの位置が最初の行のまま
    # 残るため、行の順に走査して接頭辞を外して比べる。パーサは 1 行を 1 項目にするため、
    # 項目の位置がそのまま行番号になる
    found = None
    for line_no, entry in enumerate(entries, start=1):
        if entry.kind != 'kv' or entry.key is None:
            continue
        key = entry.key
        name = key[len('export '):].strip() if key.startswith('export ') else key
        if name == keys.DEVBASE_ACCOUNT_GROUP:
            found = (entry.value or '', line_no)
    return found


def _root_env_line(root: Path) -> Optional[int]:
    """``$DEVBASE_ROOT/env`` の宣言の行番号。無ければ ``None``"""
    found = _read_declaration(Path(root) / ENV_FILENAME)
    return None if found is None else found[1]


def check_root_env(root: Path) -> None:
    """``$DEVBASE_ROOT/env`` にグループの宣言があれば止める (名前を変えた既定になるため)"""
    line = _root_env_line(root)
    if line is not None:
        raise GroupDeclarationError(
            f"$DEVBASE_ROOT/env:{line} の {keys.DEVBASE_ACCOUNT_GROUP} は使えません。"
            f"この行を消し、各プロジェクトの projects/<name>/env に "
            f"{keys.DEVBASE_ACCOUNT_GROUP}=<グループ> を書いてください"
        )


def declare(root: Path, project: Optional[str]) -> DeclaredGroup:
    """プロジェクトのグループの宣言を読む。

    Raises:
        GroupDeclarationError: プロジェクトが無い・宣言が無い・空・名前が使えない・
            ``$DEVBASE_ROOT/env`` に宣言がある
    """
    from devbase.volume.manager import validate_account_group

    root = Path(root)
    check_root_env(root)
    if not project:
        raise GroupDeclarationError(
            "プロジェクトの外ではアカウントグループを決められません。"
            "projects/<name>/ の下で打ってください"
        )
    path = root / 'projects' / project / ENV_FILENAME
    relative = f"projects/{project}/{ENV_FILENAME}"
    hint = f"{keys.DEVBASE_ACCOUNT_GROUP}=<グループ> (acme / personal など)"
    found = _read_declaration(path)
    if found is None:
        raise GroupDeclarationError(
            f"プロジェクト {project} はアカウントグループを宣言していません。"
            f"{relative} に {hint} を書いてください"
        )
    value, line = found
    if not value.strip():
        raise GroupDeclarationError(
            f"{relative}:{line} の {keys.DEVBASE_ACCOUNT_GROUP} が空です。"
            f"{hint} を書いてください"
        )
    try:
        name = validate_account_group(value)
    except DevbaseError as e:
        raise GroupDeclarationError(f"{e} ({relative}:{line})") from None
    return DeclaredGroup(name=name, source=path, line=line)


def _project_names(root: Path) -> List[str]:
    from devbase.utils import names

    return [p.name for p in names.project_dirs(Path(root) / 'projects')]


def declared_groups(root: Path) -> List[str]:
    """宣言済みのプロジェクトのグループ名 (重複を除き名前順)。宣言の読めないものは飛ばす"""
    names = set()
    try:
        check_root_env(root)
    except GroupDeclarationError:
        return []
    for project in _project_names(root):
        try:
            names.add(declare(root, project).name)
        except DevbaseError as e:
            logger.debug("グループの候補から外します (%s): %s", project, e)
    return sorted(names)


def undeclared_projects(root: Path, names: Iterable[str]) -> List[str]:
    """``names`` のうち、宣言の読めないプロジェクトの名前 (与えた順)。

    ``$DEVBASE_ROOT/env`` に宣言があれば、どのプロジェクトも決まらないため
    :class:`GroupDeclarationError` を送る。
    """
    check_root_env(root)
    missing = []
    for name in names:
        try:
            declare(root, name)
        except GroupDeclarationError:
            missing.append(name)
    return missing


def require_declared(root: Path, names: Iterable[str]) -> None:
    """全プロジェクトを回すコマンドの書き込みの前の検査 (#315 I11)。

    宣言の無いプロジェクトが 1 つでもあれば、名前を挙げて :class:`GroupDeclarationError`。
    """
    missing = undeclared_projects(root, names)
    if missing:
        raise GroupDeclarationError(
            "アカウントグループを宣言していないプロジェクトがあります: "
            f"{', '.join(missing)}。各 projects/<name>/env に "
            f"{keys.DEVBASE_ACCOUNT_GROUP}=<グループ> を書くか、"
            "--exclude-project で外してください"
        )


def describe_source(root: Path, declared: DeclaredGroup) -> str:
    """出所の表示 (``projects/web/env:3``)"""
    root = Path(root)
    try:
        relative = declared.source.relative_to(root)
    except ValueError:
        relative = declared.source
    return f"{relative}:{declared.line}"
