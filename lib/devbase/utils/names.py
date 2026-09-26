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

述語は 2 つある (#276)。``counts_as_project`` は ``projects/`` の直下のどの名前をプロジェクトとして
数えるかを決める (``.`` 始まりと空だけを外し、名前の形は見ない)。``is_single_segment_name`` は
名前を指定した操作に使える形かを決める。形に合わない名前も数え、呼び出し側が ``NAME_FORM_HINT`` を
添えて知らせる (PLAN66 の「弾かずに知らせる」)。

消費側は ``from devbase.utils import names`` と読み込み、``names.counts_as_project(...)`` のように
module の属性として呼ぶ。規則の差し替えのテスト (``tests/utils/test_project_name_consumers.py``) が
定義元の 1 か所を差し替えて消費側の取り残しを見るため。
"""

from __future__ import annotations

import re
from pathlib import Path

#: 英数字で始まり、英数字・``.``・``-``・``_`` だけからなる。先頭が英数字なので ``.`` と
#: ``..`` と ``-x`` と空は当たらない。``/`` ``\`` 空白 非 ASCII は含められない。
SINGLE_SEGMENT_NAME_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._-]*'

_SINGLE_SEGMENT_NAME_RE = re.compile(SINGLE_SEGMENT_NAME_PATTERN)

#: 名前の形を利用者へ説明する文 (PLAN66 決定 7)。知らせの文に埋め込むため末尾に句点を
#: 置かない。この module はログを出さない (副作用を持たない契約) ので、出すのは呼び出し側。
NAME_FORM_HINT = "英数字で始まり、英数字・'.'・'-'・'_' だけからなる名前"


def is_single_segment_name(value: str) -> bool:
    """``value`` が親ディレクトリの直下の 1 つの名前の形か (``re.fullmatch``)。

    ``fullmatch`` で見るため、末尾の改行も不一致になる。
    """
    return _SINGLE_SEGMENT_NAME_RE.fullmatch(value) is not None


def counts_as_project(name: str) -> bool:
    """``projects/`` の直下の ``name`` をプロジェクトとして数えるか。

    空と ``.`` 始まり (``.vscode``・``.``・``..``) だけを偽にする。名前の形は見ない
    (``_foo``・``-x``・``a b`` も真)。例外を出さず、ログも出さない。
    """
    return bool(name) and not name.startswith('.')


def project_dirs(projects_dir: Path) -> list[Path]:
    """``projects_dir`` の直下で、プロジェクトとして数える dir (パス順)。

    ``projects_dir`` が dir でなければ空。述語は module の大域名で引くので、
    :func:`counts_as_project` の差し替えがここにも届く。
    """
    if not projects_dir.is_dir():
        return []
    return [p for p in sorted(projects_dir.iterdir())
            if counts_as_project(p.name) and p.is_dir()]
