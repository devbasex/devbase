"""名前の形の規則 (PLAN61)。

``projects/`` と ``containers/`` の直下の 1 つの名前として受け付ける形を 1 か所で決める。
名前を親ディレクトリへ連結する入口 (``container._resolve_project_name``、
``cli._named_lifecycle_project``、``container._build_single_image``) は、連結の前にここで
弾く。``..`` や ``/`` を通すと ``$DEVBASE_ROOT`` の外を指せてしまうため。

``re`` だけに依存する。``cli.py`` は起動を軽く保つためコマンドのモジュールを dispatch まで
読まないので、規則はコマンドのモジュールではなくここに置く (設計の決定 2)。

同期注意: ``bin/devbase`` の ``_SINGLE_SEGMENT_NAME_RE`` は同じ正規表現を文字列で持つ
(Python を呼ぶと name 解決のたびに ``uv run`` の起動が増えるため)。片方を変えたら
もう片方も変える。一致は ``tests/cli/test_project_name_resolution.py`` の同期テストで見る。
"""

from __future__ import annotations

import re

#: 英数字で始まり、英数字・``.``・``-``・``_`` だけからなる。先頭が英数字なので ``.`` と
#: ``..`` と ``-x`` と空は当たらない。``/`` ``\`` 空白 非 ASCII は含められない。
SINGLE_SEGMENT_NAME_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._-]*'

_SINGLE_SEGMENT_NAME_RE = re.compile(SINGLE_SEGMENT_NAME_PATTERN)


def is_single_segment_name(value: str) -> bool:
    """``value`` が親ディレクトリの直下の 1 つの名前の形か (``re.fullmatch``)。

    ``fullmatch`` で見るため、末尾の改行も不一致になる。
    """
    return _SINGLE_SEGMENT_NAME_RE.fullmatch(value) is not None
