"""プロジェクト構成ファイルから機密ファイルの参照を外す / 戻す

各プロジェクトの ``compose.yml`` は共通設定とプロジェクト設定を ``env_file`` で
直接参照している。機密を暗号化すると、そのファイルは平文としては存在しなくなる
ため、参照を残したままでは Docker Compose が起動時に失敗する (plan35 §2.2)。

書き換えは **行単位のコメントアウト** で行い、元の行をそのまま残す:

    env_file:
      # devbase(PLAN35) 機密は環境変数で注入: - ${DEVBASE_ROOT}/.env
      - env

こうする理由は 2 つある。1 つは、YAML として読み書きし直すと利用者が自分で書いた
コメントや整形が失われること。もう 1 つは、平文へ戻す操作 (``devbase env decrypt``)
で**元の行を機械的に復元できる**こと。行を削除してしまうと、どの位置に何を書き戻せば
よいか分からなくなる。
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Iterable, List, Sequence, Set, Tuple

#: コメントアウトした行に付ける目印。復元時はこれを取り除くだけで元に戻る。
DISABLED_MARK = '# devbase(PLAN35) 機密は環境変数で注入: '

#: 共通の機密ファイルを指す ``env_file`` エントリ
GLOBAL_ENTRIES = ('${DEVBASE_ROOT}/.env', '$DEVBASE_ROOT/.env')

#: プロジェクトの機密ファイルを指す ``env_file`` エントリ
PROJECT_ENTRIES = ('.env', './.env')

TARGET_GLOBAL = 'global'
TARGET_PROJECT = 'project'

_ENV_FILE_KEY_RE = re.compile(r'^(\s*)env_file:\s*(#.*)?$')
_LIST_ITEM_RE = re.compile(r'^(\s*)-\s*(.*?)\s*$')


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def _entry_value(raw: str) -> str:
    """``- "${DEVBASE_ROOT}/.env"  # comment`` から参照先だけを取り出す"""
    value = raw.split('#', 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return value.strip()


def _is_target(value: str, targets: Set[str]) -> bool:
    if TARGET_GLOBAL in targets and value in GLOBAL_ENTRIES:
        return True
    if TARGET_PROJECT in targets and value in PROJECT_ENTRIES:
        return True
    return False


def _is_disabled(line: str) -> bool:
    return line.lstrip(' ').startswith(DISABLED_MARK)


def _disable_line(line: str) -> str:
    indent = ' ' * _indent_of(line)
    return f"{indent}{DISABLED_MARK}{line.strip()}"


def _enable_line(line: str) -> str:
    indent = ' ' * _indent_of(line)
    return f"{indent}{line.lstrip(' ')[len(DISABLED_MARK):]}"


def disable(text: str, targets: Iterable[str] = (TARGET_GLOBAL, TARGET_PROJECT)
            ) -> Tuple[str, List[str]]:
    """機密ファイルを指す ``env_file`` エントリをコメントアウトする。

    Returns:
        ``(書き換え後のテキスト, 無効化した参照の一覧)``
    """
    wanted = set(targets)
    lines = text.splitlines(keepends=True)
    disabled: List[str] = []

    index = 0
    while index < len(lines):
        match = _ENV_FILE_KEY_RE.match(lines[index].rstrip('\n'))
        if not match:
            index += 1
            continue

        key_index = index
        key_indent = len(match.group(1))
        block_end = index + 1
        touched_here = False
        active_entries = 0

        while block_end < len(lines):
            raw = lines[block_end].rstrip('\n')
            if not raw.strip():
                break
            if _indent_of(raw) <= key_indent:
                break
            if _is_disabled(raw):
                block_end += 1
                continue
            item = _LIST_ITEM_RE.match(raw)
            if not item:
                break
            value = _entry_value(item.group(2))
            if _is_target(value, wanted):
                lines[block_end] = _disable_line(raw) + '\n'
                disabled.append(value)
                touched_here = True
            else:
                active_entries += 1
            block_end += 1

        # 全エントリを落とすと `env_file:` だけが残り、Compose が
        # 「env_file は文字列かリスト」で失敗する。キー行ごと無効化する。
        if touched_here and active_entries == 0:
            lines[key_index] = _disable_line(lines[key_index].rstrip('\n')) + '\n'

        index = block_end

    return ''.join(lines), disabled


def enable(text: str) -> Tuple[str, List[str]]:
    """``disable`` が付けた目印を外し、元の行へ戻す。

    Returns:
        ``(書き換え後のテキスト, 復元した行の一覧)``
    """
    lines = text.splitlines(keepends=True)
    restored: List[str] = []
    for i, line in enumerate(lines):
        stripped = line.rstrip('\n')
        if not _is_disabled(stripped):
            continue
        lines[i] = _enable_line(stripped) + '\n'
        restored.append(lines[i].strip())
    return ''.join(lines), restored


def find_secret_entries(text: str,
                        targets: Iterable[str] = (TARGET_GLOBAL, TARGET_PROJECT)
                        ) -> List[str]:
    """有効なままの機密ファイル参照を列挙する (書き換えはしない)"""
    _, found = disable(text, targets)
    return found


def diff(before: str, after: str, path: Path) -> str:
    """利用者へ提示するための差分を作る"""
    return ''.join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f'{path} (現在)',
        tofile=f'{path} (変更後)',
    ))


def compose_files(devbase_root: Path, projects: Sequence[str]) -> List[Path]:
    """対象プロジェクトの ``compose.yml`` のうち実在するものを返す"""
    root = Path(devbase_root)
    found = []
    for name in projects:
        path = root / 'projects' / name / 'compose.yml'
        if path.is_file():
            found.append(path)
    return found
