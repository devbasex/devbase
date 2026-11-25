"""コメントと並び順を残したまま YAML の一部だけを書き換える。

``project.yml`` / ``project.local.yml`` は人が書くファイルで、コメントや空行にも意味がある。
``yaml.safe_dump`` で書き戻すとそれらが失われるため、PyYAML が持つ各ノードの位置
(``start_mark`` / ``end_mark``) から書き換える範囲だけを求めて、文字列のまま差し替える。

書き換えの結果は毎回読み直し、求めた中身と一致することを確かめる。アンカーやブロックスカラー
など位置の計算が合わない書き方では、誤った内容を返さずに :class:`ConfigError` を送る。
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Mapping, Sequence, Tuple, Union

import yaml

from devbase.errors import ConfigError

Path = Tuple[Union[str, int], ...]

_PLAIN = re.compile(r'^[A-Za-z0-9_./~][A-Za-z0-9_./~-]*$')


class YamlDocument:
    """書き換え中の YAML 1 ファイル分。``text`` が文面、``data`` がその中身。"""

    def __init__(self, text: str, source: str):
        self.source = source
        self.text = text
        self.data = self._load(text)

    # -- 操作 ---------------------------------------------------------------

    def set(self, path: Path, value: Any, *, after: Sequence[str] = ()) -> None:
        """``path`` の値を書き換える。キーが無ければ足す (親のマッピングも 1 段だけ作る)。

        ``after`` は足す位置の候補で、同じマッピングにある最後のもののすぐ下へ置く。
        どれも無ければマッピングの末尾へ置く。
        """
        *parents, key = path
        expected = _unshared(self.data)
        target = expected
        for part in parents:
            if isinstance(target, dict) and target.get(part) is None:
                target[part] = {}
            target = target[part]
        target[key] = value

        parent = self._find(parents)
        if _is_null(parent):
            # 最上位が空か、親のキーが無い・値が空 (``docker:`` だけ)
            text = self._create_parent(parents, f'{key}: {_scalar(value)}')
        else:
            pair = _pair(parent, key)
            if pair is None:
                text = self._insert_key(parent, f'{key}: {_scalar(value)}', after)
            else:
                text = self._replace(pair[1], _scalar(value))
        self._commit(text, expected)

    def delete(self, path: Path) -> None:
        """``path`` のキーか要素を消す。無ければ何もしない。

        入れ子のマッピングの最後の 1 キーを消すときは、空の節を残さず親のキーごと消す。
        """
        *parents, key = path
        parent = self._find(parents)
        if isinstance(parent, yaml.MappingNode):
            index = next((i for i, (k, _v) in enumerate(parent.value)
                          if k.value == key), None)
            if index is None:
                if key in self._value(parents):      # マージキー (``<<``) で継いだ値
                    raise self._unsupported()
                return
            if len(parent.value) == 1 and parents and isinstance(parents[-1], str):
                self.delete(tuple(parents))
                return
            spans = [(k.start_mark.index, self._leaf_end(v)) for k, v in parent.value]
        elif isinstance(parent, yaml.SequenceNode):
            index = key
            spans = [(item.start_mark.index, self._leaf_end(item))
                     for item in parent.value]
        else:
            return

        expected = _unshared(self.data)
        target = expected
        for part in parents:
            target = target[part]
        del target[key]
        self._commit(self._cut(parent, spans, index), expected)

    def append(self, path: Path, item: Mapping[str, Any]) -> None:
        """``path`` の並びの末尾へマッピングを 1 件足す。"""
        node = self._find(path)
        if not isinstance(node, yaml.SequenceNode) or not node.value:
            raise self._unsupported()
        expected = _unshared(self.data)
        target = expected
        for part in path:
            target = target[part]
        target.append(dict(item))

        fields = [f'{k}: {_scalar(v)}' for k, v in item.items()]
        last = self._leaf_end(node.value[-1])
        if node.flow_style:
            text = _splice(self.text, last, last, ', {' + ', '.join(fields) + '}')
        else:
            indent = ' ' * node.start_mark.column
            lines = [f'{indent}- {fields[0]}'] + [f'{indent}  {f}' for f in fields[1:]]
            text = self._insert_lines(last, lines)
        self._commit(text, expected)

    # -- 文面の差し替え -----------------------------------------------------

    def _commit(self, text: str, expected: Any) -> None:
        try:
            actual = self._load(text)
        except ConfigError:
            raise self._unsupported() from None
        if actual != expected:
            raise self._unsupported()
        self.text, self.data = text, actual

    def _unsupported(self) -> ConfigError:
        return ConfigError(
            f"{self.source} のこの書き方は自動で書き換えられません。"
            "エディタで直接編集してください。")

    def _load(self, text: str) -> Any:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ConfigError(f"{self.source} の YAML を解釈できません: {e}") from e
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ConfigError(
                f"{self.source} の最上位はマッピングである必要があります。")
        return data

    def _value(self, path: Sequence[Union[str, int]]) -> Any:
        value = self.data
        for part in path:
            value = value[part]
        return value

    def _find(self, path: Sequence[Union[str, int]]):
        """``path`` のノード。途中のキーが無ければ ``None``"""
        node = yaml.compose(self.text, Loader=yaml.SafeLoader)
        for part in path:
            if isinstance(node, yaml.MappingNode):
                pair = _pair(node, part)
                node = None if pair is None else pair[1]
            elif isinstance(node, yaml.SequenceNode):
                node = node.value[part]
            else:
                return None
        return node

    def _leaf_end(self, node) -> int:
        """ノードの最後の値の終わり。

        ブロックの並び・マッピングの ``end_mark`` は次のトークンの位置 (間の空行とコメントを
        含む) になるため、末尾の値までたどる。ブロックスカラーの終わりの改行は含めない。
        """
        if not isinstance(node, yaml.ScalarNode) and not node.flow_style and node.value:
            last = node.value[-1]
            return self._leaf_end(last[1] if isinstance(last, tuple) else last)
        end = node.end_mark.index
        while end > node.start_mark.index and self.text[end - 1] in '\r\n':
            end -= 1
        return end

    def _replace(self, node, scalar: str) -> str:
        start, end = node.start_mark.index, node.end_mark.index
        if start == end and self.text[start - 1:start] == ':':
            scalar = f' {scalar}'
        return _splice(self.text, start, end, scalar)

    def _insert_key(self, parent, field: str, after: Sequence[str]) -> str:
        if not isinstance(parent, yaml.MappingNode):
            raise self._unsupported()
        if parent.flow_style:
            if not parent.value:
                at = parent.end_mark.index - 1
                return _splice(self.text, at, at, field)
            at = self._leaf_end(parent.value[-1][1])
            return _splice(self.text, at, at, f', {field}')
        anchors = [value for key, value in parent.value if key.value in after]
        anchor = anchors[-1] if anchors else parent.value[-1][1]
        indent = ' ' * parent.start_mark.column
        return self._insert_lines(self._leaf_end(anchor), [indent + field])

    def _create_parent(self, parents: Sequence[Union[str, int]], field: str) -> str:
        if not parents:
            return self._append_lines([field])
        if len(parents) != 1:
            raise self._unsupported()
        key = parents[0]
        root = self._find(())
        if root is None:
            return self._append_lines([f'{key}:', f'  {field}'])
        pair = _pair(root, key)
        if pair is None:
            # リポジトリの並びより前に置く (defaults は repos の前が読みやすい)
            anchor = [k for k, _v in root.value if k.value != 'repos']
            return self._insert_lines(
                self._leaf_end(_pair(root, anchor[-1].value)[1]
                               if anchor else root.value[-1][1]),
                [f'{key}:', f'  {field}'])
        if pair[1].start_mark.index != pair[1].end_mark.index:
            raise self._unsupported()      # ``docker: null`` / ``docker: ~``
        return self._insert_lines(pair[1].end_mark.index, [f'  {field}'])

    def _insert_lines(self, position: int, lines: List[str]) -> str:
        """``position`` を含む行のすぐ下へ ``lines`` を足す (行末のコメントは元の行に残す)"""
        eol = self.text.find('\n', position)
        if eol < 0:
            eol = len(self.text)
        elif eol > 0 and self.text[eol - 1] == '\r':
            eol -= 1
        newline = self._newline()
        return _splice(self.text, eol, eol, ''.join(newline + line for line in lines))

    def _append_lines(self, lines: List[str]) -> str:
        newline = self._newline()
        head = self.text
        if head and not head.endswith('\n'):
            head += newline
        return head + ''.join(line + newline for line in lines)

    def _cut(self, parent, spans: List[Tuple[int, int]], index: int) -> str:
        start, end = spans[index]
        following = spans[index + 1][0] if index + 1 < len(spans) else None
        if parent.flow_style:
            if following is not None:
                return _splice(self.text, start, following, '')
            if index > 0:
                start = spans[index - 1][1]
            return _splice(self.text, start, end, '')

        line_start = self.text.rfind('\n', 0, start) + 1
        lead = self.text[line_start:start].strip()
        is_item = isinstance(parent, yaml.SequenceNode)
        if lead and not (is_item and lead == '-'):
            # ``- owner: x`` のように行頭が ``- `` のキー。次のキーを ``- `` の後ろへ詰める
            if following is None:
                raise self._unsupported()
            return _splice(self.text, start, following, '')
        line_end = self.text.find('\n', end)
        line_end = len(self.text) if line_end < 0 else line_end + 1
        return _splice(self.text, line_start, line_end, '')

    def _newline(self) -> str:
        return '\r\n' if '\r\n' in self.text else '\n'


def _splice(text: str, start: int, end: int, replacement: str) -> str:
    return text[:start] + replacement + text[end:]


def _unshared(data: Any) -> Any:
    """写し。``copy.deepcopy`` と違い、エイリアスで共有された部分も別々の実体にする。

    共有したままだと 1 か所の書き換えが別の場所へも及び、書き換えの結果と一致してしまう。
    """
    if isinstance(data, dict):
        return {k: _unshared(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_unshared(v) for v in data]
    return data


def _pair(node, key):
    return next(((k, v) for k, v in node.value if k.value == key), None)


def _is_null(node) -> bool:
    return node is None or (isinstance(node, yaml.ScalarNode)
                            and node.tag == 'tag:yaml.org,2002:null')


def _scalar(value: Any) -> str:
    """スカラーの文面。ブロックでもフロー (``{...}``) でも同じ値に読める形で書く。"""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if _PLAIN.match(value) and yaml.safe_load(value) == value:
        return value
    return json.dumps(value, ensure_ascii=False)


__all__ = ["YamlDocument"]
