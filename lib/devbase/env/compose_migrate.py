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

この走査が扱う範囲 (契約)
-------------------------

行単位で書き換える以上、YAML の記法すべてを扱えるわけではない。**何を扱い、何を
扱わないか**をここに明示する。走査を直すときはこの契約と突き合わせること。

1. 書き換える記法 — 1 行がちょうど 1 エントリに対応するもの::

       env_file:
         - .env
         - "${DEVBASE_ROOT}/.env"
         - path: .env            # long syntax でも 1 行で閉じているもの

   行ごとコメントアウトしても他の指定を巻き込まないため、無効化も復元も機械的に
   できる。行末コメント・前後の空行・利用者のコメント行が混ざっていてもよい。

2. 移行を中止する記法 — 1 行に閉じていない、または 1 行に複数の指定が同居する
   もの::

       env_file: .env
       env_file: [ "${DEVBASE_ROOT}/.env", .env ]
       env_file:
         - path: .env
           required: false       # 続きの行を持つ long syntax
         - { path: .env }        # フロー記法のマッピング

   行ごとコメントアウトすると無関係な指定まで巻き添えにする (あるいは 1 エントリ
   の一部だけが残って YAML が壊れる)。**機密を指している場合は移行を止め**、
   利用者に手で直してもらう (:func:`secret_unsupported_env_file_lines`)。
   ``env_file:`` の値がシーケンスでない場合 (下がマッピングになっている等) も
   ここに含める。Compose の仕様上は不正な書き方だが、参照を見落とすよりは中止・
   警告する方が安全なため。

3. 触らない記法 — 機密と無関係な参照::

       env_file:
         - config/app.env

   移行で消えるファイルではないので書き換える必要がない。ただし 2. の記法で
   書かれている場合は、機密でなくても警告する
   (:func:`warn_unsupported_env_file`)。

**不変条件**: 扱えない記法に当たったときに黙って通してはいけない。機密を指して
いれば中止 (2.)、指していなければ警告に落ちる。黙って見逃すと、平文を退避した
あとも参照だけが残り、次の起動で初めて壊れていることに気付くことになる。
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import (Dict, Iterable, List, NamedTuple, Optional, Sequence, Set,
                    Tuple)

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

#: long syntax (``- path: .env``) の ``path`` キー。Compose はエントリを
#: マッピングでも書けるため、文字列としてだけ見ると参照を取りこぼす。
_LONG_SYNTAX_PATH_RE = re.compile(r"""^(?:path|'path'|"path")\s*:\s*(.*)$""")

#: フロー記法 (``{ path: .env, required: false }``) から ``path`` の値だけを拾う。
#: 書き換えの対象にはしないが、「機密を指しているか」の判定には要る。
_FLOW_PATH_RE = re.compile(
    r"""(?:^|[\[{,]\s*)(?:path|'path'|"path")\s*:\s*([^,}\]]*)""")

#: ``services:`` セクションの開始行
_SERVICES_KEY_RE = re.compile(r'^(\s*)services:\s*(#.*)?$')

#: サービス名の行 (``  dev:`` / ``  "db":`` / ``  db:   # コメント``)
_SERVICE_KEY_RE = re.compile(
    r"""^\s*(?:"([^"]*)"|'([^']*)'|([^\s#:][^:]*)):\s*(#.*)?$""")


class _Entry(NamedTuple):
    """``env_file`` ブロックの 1 エントリ

    Attributes:
        index: エントリが始まる行の位置。
        refs: そのエントリが指しうる参照先。続きの行に書かれた ``path`` も
            含める。移行を止めるべきかの判定に使う。
        disabled: すでにコメントアウトされているか。
        supported: **行単位で無効化・復元できるか** (モジュール冒頭の契約 1.)。
            偽なら書き換えず、警告か中止のどちらかに落とす。
    """

    index: int
    refs: Tuple[str, ...]
    disabled: bool
    supported: bool


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def _split_eol(line: str) -> Tuple[str, str]:
    """行を ``(中身, 行末)`` に分ける。

    ``rstrip('\\n')`` で行末を落として ``'\\n'`` を付け直すと、CRLF の行が
    LF になってしまう。暗号化 → 復号の往復で元の ``compose.yml`` に戻らず、
    書き換えた行だけ改行コードが混ざる。元の行末をそのまま付け直せるよう
    ここで分けておく。
    """
    for eol in ('\r\n', '\n', '\r'):
        if line.endswith(eol):
            return line[:-len(eol)], eol
    return line, ''


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return value.strip()


def _entry_value(raw: str) -> str:
    """``- "${DEVBASE_ROOT}/.env"  # comment`` から参照先だけを取り出す"""
    return _strip_quotes(raw.split('#', 1)[0].strip())


def _long_syntax_ref(text: str) -> Optional[str]:
    """``path: .env`` から参照先を取り出す (long syntax でなければ None)"""
    match = _LONG_SYNTAX_PATH_RE.match(text.strip())
    if not match:
        return None
    return _strip_quotes(match.group(1).split('#', 1)[0].strip())


def _flow_map_refs(text: str) -> List[str]:
    """フロー記法の中の ``path`` の値をすべて拾う。

    ``- { path: .env, required: false }`` や ``env_file: [{path: .env}]`` は
    書き換えの対象にしない (契約 2.) が、機密を指しているなら移行を止める
    必要があるため、判定に使う参照先だけは取り出す。
    """
    return [_strip_quotes(value.strip())
            for value in _FLOW_PATH_RE.findall(text)
            if value.strip()]


def _inline_entries(raw: str) -> List[str]:
    """``env_file:`` の後ろに直接書かれた値から参照の一覧を取り出す。

    ``[ "${DEVBASE_ROOT}/.env", .env ]`` のようなインライン配列と、
    ``.env`` のような単一文字列の両方を受ける。書き換えはできないが、
    「機密を指しているかどうか」の判定だけはここで行う。
    """
    value = raw.split('#', 1)[0].strip()
    if value.startswith('['):
        inner = value[1:]
        if inner.endswith(']'):
            inner = inner[:-1]
        parts = inner.split(',')
    else:
        parts = [value]
    found = [item for item in (_strip_quotes(part.strip()) for part in parts)
             if item]
    # 要素がフロー記法のマッピングだと上の分割では参照先にならない
    # (``{path: .env}`` がそのまま 1 要素になる)。``path`` の値も足しておく。
    found.extend(_flow_map_refs(value))
    return found


def _list_item_refs(body: str) -> Tuple[Tuple[str, ...], bool]:
    """リスト項目の中身から ``(参照先, 行単位で扱えるか)`` を返す。

    ``- .env`` のような文字列と ``- path: .env`` の long syntax はどちらも
    1 行で閉じているので書き換えられる。フロー記法のマッピングだけは 1 行に
    複数の指定が同居するため対象外にする (契約 2.)。
    """
    value = body.split('#', 1)[0].strip()
    if value.startswith('{') or value.startswith('['):
        return tuple(_flow_map_refs(value)), False
    ref = _long_syntax_ref(value)
    if ref is not None:
        return (ref,), True
    return (_strip_quotes(value),), True


def _service_name(line: str) -> Optional[str]:
    """サービス名の行から **YAML と同じ姿の** 名前を取り出す。

    ``"db":`` のようにクォートされたキーも有効な YAML で、PyYAML は ``db`` を
    返す。引用符込みで記録すると、パース済みのサービス名と照合する生成側
    (``devbase.volume.compose``) と一致せず、そのサービスへ機密が渡らない。
    ここで引用符を外して揃える (二重引用符の中のバックスラッシュ表記までは
    解釈しない。構成ファイルのサービス名には現れないため)。
    """
    match = _SERVICE_KEY_RE.match(line)
    if not match:
        return None
    double, single, bare = match.group(1), match.group(2), match.group(3)
    if double is not None:
        return double
    if single is not None:
        return single.replace("''", "'")
    return bare.strip()


def _target_of(value: str) -> Optional[str]:
    """``env_file`` の 1 エントリが**どちらの機密**を指しているかを返す。

    「機密かどうか」だけでなく由来 (共通 / プロジェクト) まで返すのは、機密の
    渡し先を決める側 (``devbase.volume.compose``) が「そのサービスが元々
    受け取っていた由来のキーだけ」を列挙できるようにするため。真偽値だけでは
    共通設定しか読んでいなかったサービスにプロジェクト固有の機密まで渡って
    しまう。
    """
    if value in GLOBAL_ENTRIES:
        return TARGET_GLOBAL
    if value in PROJECT_ENTRIES:
        return TARGET_PROJECT
    return None


def _is_target(value: str, targets: Set[str]) -> bool:
    return _target_of(value) in targets


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


def _disable_line(content: str) -> str:
    """行末は含めずに受け取り、目印を付けた姿を返す。

    末尾の空白まで含めてそのまま残すのは、復元したときに元のバイト列へ戻す
    ため (行末は :func:`_split_eol` で別に持ち回る)。
    """
    indent = ' ' * _indent_of(content)
    return f"{indent}{DISABLED_MARK}{content.lstrip(' ')}"


def _enable_line(content: str) -> str:
    indent = ' ' * _indent_of(content)
    return f"{indent}{content.lstrip(' ')[len(DISABLED_MARK):]}"


def _source_line(line: str) -> str:
    """無効化されているかに関わらず、その行の「YAML としての姿」を返す。

    復元側はキー行もエントリ行もコメントアウトされている場合があるため、
    インデントや記法の判定は目印を外した姿に対して行う必要がある。
    """
    return _enable_line(line) if _is_disabled(line) else line


def _is_skippable(line: str) -> bool:
    """空行、または利用者が書いた単独のコメント行かを返す。

    どちらも YAML としての構造を持たないので、走査の途中で出てきても
    ブロックの終わりとみなしてはいけない。ここで打ち切ると、**コメント行より
    後ろに書かれた機密参照が無効化されないまま残り**、平文を退避したあとに
    Compose が存在しないファイルを読もうとして起動できなくなる。

    無効化済みの行 (:data:`DISABLED_MARK` 付き) も見た目はコメント行だが、
    中身はエントリなので読み飛ばしてはいけない。判定の順序を間違えると
    ``enable`` が何も復元できなくなるため、先に :func:`_is_disabled` で除く。
    """
    stripped = line.strip()
    if not stripped:
        return True
    if _is_disabled(stripped):
        return False
    return stripped.startswith('#')


def _with_continuation(entry: _Entry, source: str) -> _Entry:
    """続きの行を持つエントリに「行単位では扱えない」印を付ける。

    ``- path: .env`` の下に ``required: false`` が続く形は、``- path:`` の行だけ
    コメントアウトすると ``required: false`` が宙に浮いて YAML が壊れる。行を
    またぐ範囲を安全に無効化・復元する術がないので、書き換えの対象から外して
    中止・警告へ回す (契約 2.)。続きの行に書かれた ``path`` も控えておかないと、
    ``-`` の行に参照が現れない書き方で機密を見落とす。
    """
    refs = entry.refs
    ref = _long_syntax_ref(source)
    if ref:
        refs = refs + (ref,)
    return entry._replace(refs=refs, supported=False)


def _scan_env_file_block(lines: Sequence[str], key_index: int, key_indent: int
                         ) -> Tuple[List[_Entry], int]:
    """``env_file:`` ブロックのエントリを集め、ブロックの終端を返す。

    ``disable`` と ``enable`` は向きが逆なだけで「どこからどこまでがブロックで、
    どの行がエントリか」の判定は同じである。二重に持つと片方だけ直したときに
    無効化と復元がずれるため、走査はここ 1 箇所に集める。扱えない記法の検出も
    同じ走査に相乗りさせる (:func:`_unsupported_entries`)。

    Args:
        key_index: ``env_file:`` キー行の位置。
        key_indent: キー行のインデント (目印を外した姿で数えたもの)。

    Returns:
        ``([エントリ], ブロック終端の行の位置)``
    """
    entries: List[_Entry] = []
    index = key_index + 1
    item_indent: Optional[int] = None
    while index < len(lines):
        raw = _split_eol(lines[index])[0]
        if _is_skippable(raw):
            index += 1
            continue
        # 無効化済みの行も「YAML としての姿」に戻してインデントと記法を見る
        source = _source_line(raw)
        if _indent_of(source) <= key_indent:
            break
        item = _LIST_ITEM_RE.match(source)
        if item is None:
            # `- ` で始まらないのにブロックの中にある行。long syntax の続き
            # (`required: false` など) か、そもそもシーケンスでない値である。
            # どちらも行単位では扱えないので、直前のエントリに印を付けて先へ
            # 進む。ここで走査を打ち切る方が危険で、後ろに並ぶエントリを丸ごと
            # 取りこぼし、機密の参照が有効なまま残ってしまう。
            if item_indent is None:
                # `env_file:` の直下がシーケンスでない。ブロック全体を 1 つの
                # 扱えないエントリとみなし、キーより深い行はすべて続きとして
                # 束ねる。
                entries.append(_Entry(index, (), False, False))
                item_indent = key_indent
            entries[-1] = _with_continuation(entries[-1], source)
            index += 1
            continue
        item_indent = _indent_of(source)
        refs, supported = _list_item_refs(item.group(2))
        entries.append(_Entry(index, refs, _is_disabled(raw), supported))
        index += 1
    return entries, index


def disable(text: str, targets: Iterable[str] = (TARGET_GLOBAL, TARGET_PROJECT)
            ) -> Tuple[str, List[str]]:
    """機密ファイルを指す ``env_file`` エントリをコメントアウトする。

    書き換えるのは契約 1. の記法だけで、扱えない記法には触れない。触れない分は
    :func:`secret_unsupported_env_file_lines` が中止の理由として拾う。

    Returns:
        ``(書き換え後のテキスト, 無効化した参照の一覧)``
    """
    wanted = set(targets)
    lines = text.splitlines(keepends=True)
    disabled: List[str] = []

    index = 0
    while index < len(lines):
        content, eol = _split_eol(lines[index])
        match = _ENV_FILE_KEY_RE.match(content)
        if not match:
            index += 1
            continue

        key_index = index
        key_indent = len(match.group(1))
        touched_here = False
        active_entries = 0

        entries, block_end = _scan_env_file_block(lines, key_index, key_indent)
        for entry in entries:
            if entry.disabled:
                # すでに無効化されている。有効なエントリとしても数えない
                continue
            if entry.supported and _is_target(entry.refs[0], wanted):
                entry_content, entry_eol = _split_eol(lines[entry.index])
                lines[entry.index] = _disable_line(entry_content) + entry_eol
                disabled.append(entry.refs[0])
                touched_here = True
            else:
                active_entries += 1

        # 全エントリを落とすと `env_file:` だけが残り、Compose が
        # 「env_file は文字列かリスト」で失敗する。キー行ごと無効化する。
        if touched_here and active_entries == 0:
            lines[key_index] = _disable_line(content) + eol

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
        content, eol = _split_eol(lines[index])
        # キー行そのものが無効化されている場合があるため、目印を外した姿で判定する
        match = _ENV_FILE_KEY_RE.match(_source_line(content))
        if not match:
            index += 1
            continue

        key_index = index
        key_disabled = _is_disabled(content)
        key_indent = _indent_of(_source_line(content))
        active_entries = 0

        entries, block_end = _scan_env_file_block(lines, key_index, key_indent)
        for entry in entries:
            if not entry.disabled:
                active_entries += 1
                continue
            if entry.supported and _is_target(entry.refs[0], wanted):
                entry_content, entry_eol = _split_eol(lines[entry.index])
                lines[entry.index] = _enable_line(entry_content) + entry_eol
                restored.append(lines[entry.index].strip())
                active_entries += 1

        # キー行は「有効なエントリが 1 つも残らない」場合に無効化されている。
        # 逆向きも同じ条件で判断し、エントリが戻ったときにだけ復元する。
        # まだ全エントリが無効なまま `env_file:` を戻すと Compose が失敗する。
        if key_disabled and active_entries > 0:
            lines[key_index] = _enable_line(content) + eol
            restored.append(lines[key_index].strip())

        index = block_end

    return ''.join(lines), restored


def _unsupported_entries(text: str) -> List[Tuple[int, str, Tuple[str, ...]]]:
    """行単位では扱えない ``env_file`` の記述を、指しうる参照つきで列挙する。

    契約 2. に当たるものをすべて集める。参照先まで返すのは、呼び出し側が
    「警告で済ませてよい行」と「移行を止めるべき行」を区別できるようにするため。

    Returns:
        ``[(1 始まりの行番号, 行の内容, その記述が指しうる参照)]``
    """
    lines = text.splitlines()
    found: List[Tuple[int, str, Tuple[str, ...]]] = []

    index = 0
    while index < len(lines):
        stripped = lines[index].rstrip()
        if not _is_disabled(stripped):
            inline = _ENV_FILE_INLINE_RE.match(stripped)
            if inline:
                # `env_file:` の後ろに値が続く書き方 (インライン配列・単一文字列)
                found.append((index + 1, stripped.strip(),
                              tuple(_inline_entries(inline.group(1)))))
                index += 1
                continue

        source = _source_line(stripped)
        match = _ENV_FILE_KEY_RE.match(source)
        if not match:
            index += 1
            continue

        # ブロックの中に潜む扱えない記法 (続きの行を持つ long syntax など) は
        # 行を単独で見ても分からない。無効化と同じ走査で拾う。
        entries, block_end = _scan_env_file_block(
            lines, index, len(match.group(1)))
        for entry in entries:
            if not entry.supported:
                found.append((entry.index + 1,
                              lines[entry.index].strip(), entry.refs))
        index = block_end

    return found


def unsupported_env_file_lines(text: str) -> List[Tuple[int, str]]:
    """行単位では扱えない ``env_file`` 記法を列挙する (契約 2.)。

    Returns:
        ``[(1 始まりの行番号, 行の内容)]``
    """
    return [(number, line) for number, line, _ in _unsupported_entries(text)]


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
            "%s:%d の env_file は行単位では自動で書き換えられない記法です"
            " (対応しているのは `env_file:` の下に `- ...` を 1 行ずつ並べる"
            "書き方だけです)。手動で書き換えてください: %s",
            path if path is not None else '<compose.yml>', number, line)
    return found


def secret_unsupported_env_file_lines(
        text: str,
        targets: Iterable[str] = (TARGET_GLOBAL, TARGET_PROJECT)
) -> List[Tuple[int, str]]:
    """**機密ファイルを指している**扱えない記法の行だけを列挙する。

    :func:`unsupported_env_file_lines` は契約 2. に当たるものをすべて返すが、
    そのうち ``env_file: config/app.env`` のように機密と無関係なものは移行に
    影響しない (書き換える必要が無い)。一方 ``env_file: [.env]`` や
    ``- path: .env`` + ``required: false`` のように機密を指しているものは、平文を
    退避したあとも参照が有効なまま残り、Compose が存在しないファイルを読もうと
    して起動できなくなる。**警告で流すのではなく移行を止める**必要があるため、
    その 2 つをここで区別する。

    Returns:
        ``[(1 始まりの行番号, 行の内容)]``
    """
    wanted = set(targets)
    return [(number, line) for number, line, refs in _unsupported_entries(text)
            if any(_is_target(ref, wanted) for ref in refs)]


def services_with_secret_env_file(
        text: str,
        targets: Iterable[str] = (TARGET_GLOBAL, TARGET_PROJECT)
) -> Dict[str, Set[str]]:
    """機密ファイルを参照している (していた) サービスを **生テキスト** から集める。

    移行後の ``compose.yml`` では機密の ``env_file`` 参照がコメントアウトされ、
    YAML としてパースすると見えなくなる。パース結果だけを見ると「元々その参照
    から機密を受け取っていたサービス」(例: DB パスワードを読む ``db``) を
    取りこぼし、機密が渡らないまま起動して失敗する。そこで生テキストを走査し、
    **有効なエントリとコメントアウトされたエントリの両方**を拾う。

    行単位で書き換えられない記法 (契約 2.) も対象に含める。移行は止まるが、
    利用者が手で直したあとも同じ判定が使えるようにするため。

    Returns:
        ``{サービス名: 参照していた種別の集合}``。種別は ``TARGET_GLOBAL`` /
        ``TARGET_PROJECT``。単なるサービス名の集合ではなく種別まで返すのは、
        機密を渡す側が**元々受け取っていた由来のキーだけ**へ絞れるようにする
        ため。共通設定だけを読んでいたサービスにプロジェクト固有のトークンまで
        渡すのは、元の構成より機密の範囲を広げてしまう。
    """
    wanted = set(targets)
    found: Dict[str, Set[str]] = {}

    def record(service: str, value: str) -> None:
        target = _target_of(value)
        if target in wanted:
            found.setdefault(service, set()).add(target)

    services_indent: Optional[int] = None
    service_indent: Optional[int] = None
    current: Optional[str] = None
    env_file_indent: Optional[int] = None

    for raw_line in text.splitlines():
        stripped = raw_line.rstrip()
        # 空行と利用者のコメント行は構造を持たない。ここで env_file ブロックを
        # 打ち切ると、その後ろのエントリを取りこぼして機密が渡らなくなる
        if _is_skippable(stripped):
            continue
        # コメントアウト済みの行も「YAML としての姿」に戻して判定する
        line = _source_line(stripped)
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
            # クォート付きのキーも YAML と同じ姿へ揃える。引用符込みで記録すると
            # パース済みのサービス名と照合できず、機密が渡らない
            current = _service_name(line)
            env_file_indent = None
            continue

        if current is None:
            continue

        if env_file_indent is not None and indent > env_file_indent:
            item = _LIST_ITEM_RE.match(line)
            if item:
                for ref in _list_item_refs(item.group(2))[0]:
                    record(current, ref)
                continue
            # `- ` で始まらない行は long syntax の続き (`path:` / `required:`)。
            # ブロックを抜けたことにすると後続のエントリを取りこぼす
            ref = _long_syntax_ref(line)
            if ref is not None:
                record(current, ref)
            continue
        env_file_indent = None

        if _ENV_FILE_KEY_RE.match(line):
            env_file_indent = indent
            continue

        inline = _ENV_FILE_INLINE_RE.match(line)
        if inline:
            for value in _inline_entries(inline.group(1)):
                record(current, value)

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
