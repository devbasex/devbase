"""プロジェクトの Dockerfile の場所の決め方 (#415) と直の親の読み方 (#404)。

場所の決め方 (:func:`project_dockerfile_path`) は、compose の構成の開発サービスの ``build`` から
プロジェクトの Dockerfile のパスを決める規則を 1 か所で持つ。通常のビルド (``bin/devbase`` が
``devbase.commands.project_dockerfile`` を通して呼ぶ) と ``--expires`` の判定
(``container._get_base_image_ref``) の両方がここを使う。

直の親の読み方は、Dockerfile の本文から、最初に ``devbase-*`` を指す ``FROM`` の行が名指すイメージ
(直の親イメージ) を読む規則を 1 か所で決める。``--expires`` の判定がここを使う。

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
from pathlib import Path
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


def project_dockerfile_path(dev_service: dict) -> Optional[Path]:
    """開発サービスの定義 (compose の構成の 1 サービス) からプロジェクトの Dockerfile のパスを返す。

    ``build`` が無い・空なら None (プロジェクトの Dockerfile は無い)。文字列の形は ``context`` とし、
    ``dockerfile`` の既定は ``Dockerfile``、``context`` の既定は ``.``。``dockerfile`` が絶対パスなら
    そのまま返し、相対なら ``context`` に連結する。ファイルを読まず、副作用を持たない。
    """
    build = dev_service.get('build')
    if not build:
        return None
    if isinstance(build, str):
        context, dockerfile = build, 'Dockerfile'
    else:
        context = build.get('context', '.')
        dockerfile = build.get('dockerfile', 'Dockerfile')
    path = Path(dockerfile)
    if path.is_absolute():
        return path
    return Path(context) / dockerfile
