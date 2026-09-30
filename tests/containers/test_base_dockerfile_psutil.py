"""base イメージに psutil を apt で入れる形

Docker を起動せず、``containers/base/Dockerfile`` の文字列だけを固定する。NDF のスクリプトは
システムの ``python3`` で ``import psutil`` する。base は pip を入れない方針のため、Ubuntu の
``python3-psutil`` を 1 つ目の ``RUN`` の 1 回目の ``apt-get install`` の一覧へ置く。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "containers" / "base" / "Dockerfile"


def _statements() -> str:
    """コメント行を除いた Dockerfile の本文"""
    return "\n".join(
        line for line in DOCKERFILE.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def _first_apt_install() -> str:
    """1 つ目の ``apt-get install`` から、その一覧の終わり (``;``) までを返す"""
    body = _statements()
    start = body.index("apt-get install")
    return body[start:body.index(";", start)]


def test_python3_psutil_is_in_the_first_apt_install():
    assert re.search(r"(?<![\w-])python3-psutil(?![\w-])", _first_apt_install())


def test_psutil_is_installed_only_once():
    assert len(re.findall(r"(?<![\w-])python3-psutil(?![\w-])", _statements())) == 1
    assert "pip install psutil" not in _statements()
