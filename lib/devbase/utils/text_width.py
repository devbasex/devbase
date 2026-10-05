"""端末での表示幅で桁を揃える (#244)

f-string の ``:<24`` は文字数で埋めるため、全角文字を含む見出しでは列がずれる。
ここでは ``unicodedata.east_asian_width`` が ``W`` / ``F`` を返す文字を 2 桁、
それ以外 (曖昧幅 ``A`` を含む) を 1 桁と数える。

幅を超える文字列の扱い:

- ``pad`` は文字列を切らずにそのまま返す。呼び出し側は列の間に必ず空白を 1 つ置き、
  あふれても次の列とくっつかないようにする
- 一覧の列は ``column_width`` で、最小の幅と一覧の中の最も広い値の大きい方にそろえる。
  長い値が 1 つあっても、その一覧の行どうしの桁はそろう
"""

from __future__ import annotations

import unicodedata
from typing import Iterable


def display_width(text: str) -> int:
    """端末での表示幅 (全角 ``W`` / ``F`` は 2、それ以外は 1)"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text: str, width: int) -> str:
    """表示幅が ``width`` になるまで右に空白を足す。超えるときは切らずにそのまま返す"""
    return text + " " * max(0, width - display_width(text))


def column_width(texts: Iterable[str], minimum: int = 0) -> int:
    """列の幅。``minimum`` と、``texts`` の中で最も広い表示幅の大きい方"""
    return max([minimum, *(display_width(t) for t in texts)])
