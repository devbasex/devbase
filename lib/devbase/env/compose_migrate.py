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

対応する書き方の範囲
--------------------

行単位で書き換える都合上、扱えるのは **ブロックシーケンス記法** だけである::

    env_file:
      - ${DEVBASE_ROOT}/.env
      - .env

次のインライン記法・単一文字列記法は書き換えの対象外になる::

    env_file: [ "${DEVBASE_ROOT}/.env", .env ]
    env_file: .env

これらは 1 行に複数の参照が同居するため、行ごとコメントアウトすると無関係な参照まで
巻き添えにしてしまう。対象外だが**黙って見逃すと壊れた構成のまま起動して初めて気付く**
ことになるため、:func:`warn_unsupported_env_file` で該当ファイルと行番号を警告し、
手で書き換えてもらう。

さらに、その行が**機密ファイルを指している**場合は警告では足りない。平文を退避した
あとも参照が有効なまま残り、Compose が存在しないファイルを読もうとして起動できなく
なるためである。呼び出し側が「警告で済ませてよい行」と「移行を止めるべき行」を区別
できるよう、:func:`secret_inline_env_file_lines` で後者だけを列挙する。
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from devbase.log import get_logger

logger = get_logger(__name__)

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

#: ``env_file:`` の後ろに値が続く書き方 (インライン配列・単一文字列)。
#: 行単位の書き換えでは扱えないため、検出して警告するためだけに使う。
_ENV_FILE_INLINE_RE = re.compile(r'^\s*env_file:\s*(?!#)(\S.*)$')

#: ``services:`` セクションの開始行
_SERVICES_KEY_RE = re.compile(r'^(\s*)services:\s*(#.*)?$')

#: サービス名の行 (``  dev:`` / ``  db:   # コメント``)
_SERVICE_KEY_RE = re.compile(r'^\s*([^\s#:][^:]*):\s*(#.*)?$')


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return value.strip()


def _entry_value(raw: str) -> str:
    """``- "${DEVBASE_ROOT}/.env"  # comment`` から参照先だけを取り出す"""
    return _strip_quotes(raw.split('#', 1)[0].strip())


def _inline_entries(raw: str) -> List[str]:
    """``env_file:`` の後ろに直接書かれた値から参照の一覧を取り出す。

    ``[ "${DEVBASE_ROOT}/.env", .env ]`` のようなインライン配列と、
    ``.env`` のような単一文字列の両方を受ける。書き換えはできないが、
    「機密を指しているかどうか」の判定だけはここで行う。
    """
    value = raw.split('#', 1)[0].strip()
    if value.startswith('['):
        value = value[1:]
        if value.endswith(']'):
            value = value[:-1]
        parts = value.split(',')
    else:
        parts = [value]
    return [item for item in (_strip_quotes(part.strip()) for part in parts)
            if item]


def _is_target(value: str, targets: Set[str]) -> bool:
    if TARGET_GLOBAL in targets and value in GLOBAL_ENTRIES:
        return True
    if TARGET_PROJECT in targets and value in PROJECT_ENTRIES:
        return True
    return False


def is_secret_entry(value: str,
                    targets: Iterable[str] = (TARGET_GLOBAL, TARGET_PROJECT)
                    ) -> bool:
    """``env_file`` の 1 エントリが「暗号化移行で消える既知の機密参照」かを返す。

    判定そのものは :func:`_is_target` と同じだが、あちらは private なので、
    構成生成側 (``devbase.volume.compose``) から同じ基準で判定するための公開窓口
    として置く。判定を 1 箇所に集めておかないと、移行が外す参照と生成が落とす
    参照がずれる。
    """
    if not isinstance(value, str):
        return False
    return _is_target(value.strip(), set(targets))


def _is_disabled(line: str) -> bool:
    return line.lstrip(' ').startswith(DISABLED_MARK)


def _disable_line(line: str) -> str:
    indent = ' ' * _indent_of(line)
    return f"{indent}{DISABLED_MARK}{line.strip()}"


def _enable_line(line: str) -> str:
    indent = ' ' * _indent_of(line)
    return f"{indent}{line.lstrip(' ')[len(DISABLED_MARK):]}"


def _source_line(line: str) -> str:
    """無効化されているかに関わらず、その行の「YAML としての姿」を返す。

    復元側はキー行もエントリ行もコメントアウトされている場合があるため、
    インデントや記法の判定は目印を外した姿に対して行う必要がある。
    """
    return _enable_line(line) if _is_disabled(line) else line


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
                # 空行はブロックの終わりではない。ここで打ち切ると以降の
                # エントリを無効化し損ねるうえ、「有効なエントリが 0 件」と
                # 誤判定して `env_file:` キーごと落としてしまう。
                # 終端はインデント (下の判定) が受け持つ。
                block_end += 1
                continue
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


def enable(text: str, targets: Iterable[str] = (TARGET_GLOBAL, TARGET_PROJECT)
           ) -> Tuple[str, List[str]]:
    """``disable`` が付けた目印を外し、元の行へ戻す。

    ``disable`` と同じく **種別を絞れる**。一部のプロジェクトだけを復号した
    ときに全マーカーを戻すと、まだ暗号化されたままの共通設定
    (``${DEVBASE_ROOT}/.env``) の参照まで有効になり、存在しないファイルを
    指したまま Compose の起動が失敗する。

    Returns:
        ``(書き換え後のテキスト, 復元した行の一覧)``
    """
    wanted = set(targets)
    lines = text.splitlines(keepends=True)
    restored: List[str] = []

    index = 0
    while index < len(lines):
        raw = lines[index].rstrip('\n')
        # キー行そのものが無効化されている場合があるため、目印を外した姿で判定する
        match = _ENV_FILE_KEY_RE.match(_source_line(raw))
        if not match:
            index += 1
            continue

        key_index = index
        key_disabled = _is_disabled(raw)
        key_indent = _indent_of(_source_line(raw))
        block_end = index + 1
        active_entries = 0

        while block_end < len(lines):
            line = lines[block_end].rstrip('\n')
            if not line.strip():
                block_end += 1
                continue
            source = _source_line(line)
            if _indent_of(source) <= key_indent:
                break
            item = _LIST_ITEM_RE.match(source)
            if not item:
                break
            if _is_disabled(line):
                if _is_target(_entry_value(item.group(2)), wanted):
                    lines[block_end] = _enable_line(line) + '\n'
                    restored.append(lines[block_end].strip())
                    active_entries += 1
            else:
                active_entries += 1
            block_end += 1

        # キー行は「有効なエントリが 1 つも残らない」場合に無効化されている。
        # 逆向きも同じ条件で判断し、エントリが戻ったときにだけ復元する。
        # まだ全エントリが無効なまま `env_file:` を戻すと Compose が失敗する。
        if key_disabled and active_entries > 0:
            lines[key_index] = _enable_line(raw) + '\n'
            restored.append(lines[key_index].strip())

        index = block_end

    return ''.join(lines), restored


def unsupported_env_file_lines(text: str) -> List[Tuple[int, str]]:
    """行単位では扱えない ``env_file`` 記法を列挙する。

    Returns:
        ``[(1 始まりの行番号, 行の内容)]``
    """
    found: List[Tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.rstrip()
        if _is_disabled(stripped):
            continue
        if _ENV_FILE_INLINE_RE.match(stripped):
            found.append((number, stripped.strip()))
    return found


def warn_unsupported_env_file(text: str, path: Optional[Path] = None
                              ) -> List[Tuple[int, str]]:
    """扱えない ``env_file`` 記法を見つけたら警告する。

    移行の対象から外れることを黙っていると、利用者は「移行できた」と思った
    まま起動して初めて壊れていることに気付く。どのファイルの何行目を手で
    直せばよいかまで示す。
    """
    found = unsupported_env_file_lines(text)
    for number, line in found:
        logger.warning(
            "%s:%d の env_file はインライン記法のため自動で書き換えられません"
            " (対応しているのは `env_file:` の下に `- ...` を並べる書き方だけです)。"
            " 手動で書き換えてください: %s",
            path if path is not None else '<compose.yml>', number, line)
    return found


def secret_inline_env_file_lines(
        text: str,
        targets: Iterable[str] = (TARGET_GLOBAL, TARGET_PROJECT)
) -> List[Tuple[int, str]]:
    """**機密ファイルを指している**インライン記法の行だけを列挙する。

    :func:`unsupported_env_file_lines` は「行単位で書き換えられない記法」を
    すべて返すが、そのうち ``env_file: config/app.env`` のように機密と無関係な
    ものは移行に影響しない (書き換える必要が無い)。一方 ``env_file: [.env]``
    のように機密を指しているものは、平文を退避したあとも参照が有効なまま残り、
    Compose が存在しないファイルを読もうとして起動できなくなる。**警告で流す
    のではなく移行を止める**必要があるため、その 2 つをここで区別する。

    Returns:
        ``[(1 始まりの行番号, 行の内容)]``
    """
    wanted = set(targets)
    found: List[Tuple[int, str]] = []
    for number, line in unsupported_env_file_lines(text):
        match = _ENV_FILE_INLINE_RE.match(line)
        if not match:
            continue
        if any(_is_target(value, wanted)
               for value in _inline_entries(match.group(1))):
            found.append((number, line))
    return found


def services_with_secret_env_file(
        text: str,
        targets: Iterable[str] = (TARGET_GLOBAL, TARGET_PROJECT)
) -> Set[str]:
    """機密ファイルを参照している (していた) サービス名を **生テキスト** から集める。

    移行後の ``compose.yml`` では機密の ``env_file`` 参照がコメントアウトされ、
    YAML としてパースすると見えなくなる。パース結果だけを見ると「元々その参照
    から機密を受け取っていたサービス」(例: DB パスワードを読む ``db``) を
    取りこぼし、機密が渡らないまま起動して失敗する。そこで生テキストを走査し、
    **有効なエントリとコメントアウトされたエントリの両方**を拾う。

    行単位で書き換えられないインライン記法も対象に含める。移行は止まるが、
    利用者が手で直したあとも同じ判定が使えるようにするため。
    """
    wanted = set(targets)
    found: Set[str] = set()

    services_indent: Optional[int] = None
    service_indent: Optional[int] = None
    current: Optional[str] = None
    env_file_indent: Optional[int] = None

    for raw_line in text.splitlines():
        # コメントアウト済みの行も「YAML としての姿」に戻して判定する
        line = _source_line(raw_line.rstrip())
        if not line.strip():
            continue
        indent = _indent_of(line)

        if services_indent is None:
            match = _SERVICES_KEY_RE.match(line)
            if match:
                services_indent = len(match.group(1))
                service_indent = None
                current = None
                env_file_indent = None
            continue

        if indent <= services_indent:
            # services: セクションを抜けた (volumes: / networks: など)
            services_indent = None
            service_indent = None
            current = None
            env_file_indent = None
            match = _SERVICES_KEY_RE.match(line)
            if match:
                services_indent = len(match.group(1))
            continue

        if service_indent is None:
            service_indent = indent

        if indent <= service_indent:
            match = _SERVICE_KEY_RE.match(line)
            current = match.group(1).strip() if match else None
            env_file_indent = None
            continue

        if current is None:
            continue

        if env_file_indent is not None and indent > env_file_indent:
            item = _LIST_ITEM_RE.match(line)
            if item:
                if _is_target(_entry_value(item.group(2)), wanted):
                    found.add(current)
                continue
        env_file_indent = None

        if _ENV_FILE_KEY_RE.match(line):
            env_file_indent = indent
            continue

        inline = _ENV_FILE_INLINE_RE.match(line)
        if inline and any(_is_target(value, wanted)
                          for value in _inline_entries(inline.group(1))):
            found.add(current)

    return found


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
