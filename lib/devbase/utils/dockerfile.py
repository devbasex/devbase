"""直の親の読み方 (#404)。

Dockerfile の本文から、最初に ``devbase-*`` を指す ``FROM`` の行が名指すイメージ (直の親イメージ) を読む
規則を 1 か所で決める。``--expires`` の判定 (``container._get_base_image_ref``) がここを使う。

同期注意: ``bin/devbase`` の ``_DEVBASE_FROM_RE`` は同じ正規表現を文字列で持つ (通常のビルドの
``read_devbase_parent`` が使う。Bash から Python を呼ぶと偽の ``uv`` のテストで連なりが決まらず、
機密の注入も走るため)。片方を変えたらもう片方も変える。値の一致と振る舞いの一致は
``tests/cli/test_devbase_from_contract.py`` が見る。

正規表現は POSIX の ERE と Python の ``re`` のどちらでも同じ意味になる部分だけで書く。``\\s``・
``(?:...)``・``[[:space:]]``・``re.IGNORECASE`` を使わない。``FROM`` の語だけ大文字小文字を区別せず、
イメージの名前は小文字の ``devbase-`` だけを読む。2 番目の組がイメージの参照である。

``re`` だけに依存し、副作用を持たない。
"""

from __future__ import annotations

import re
from typing import Optional

#: 直の親の読み方の正本。空白はスペースとタブ (実際のタブ文字) だけを数える。
DEVBASE_FROM_PATTERN = '^[ \t]*[Ff][Rr][Oo][Mm][ \t]+(--platform=[^ \t]+[ \t]+)?(devbase-[^ \t]+)'

_DEVBASE_FROM_RE = re.compile(DEVBASE_FROM_PATTERN)


def devbase_parent_ref(text: str) -> Optional[str]:
    """Dockerfile の本文から直の親イメージの参照を返す。

    例: ``FROM devbase-base:latest`` -> ``devbase-base:latest``
        ``FROM devbase-base``        -> ``devbase-base:latest`` (tag 補完)
    当たる行が無ければ None。
    """
    for line in text.split('\n'):
        m = _DEVBASE_FROM_RE.match(line.rstrip('\r'))
        if m:
            ref = m.group(2)
            if ':' not in ref:
                ref += ':latest'
            return ref
    return None
