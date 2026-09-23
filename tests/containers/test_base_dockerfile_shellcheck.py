"""base イメージの shellcheck の導入の「形」 (PLAN67 / #249)

Docker を起動せず、``containers/base/Dockerfile`` の文字列だけを固定する。イメージの中に
入っていることは、版の確認の ``RUN`` がビルドの時点で守る (設計の決定 2)。ここで固定するのは
次の 3 つである (設計の決定 4)。

- ``shellcheck`` が **1 つ目の RUN の 1 回目の** ``apt-get install`` の一覧にある (受け入れ条件 5)
- ``shellcheck`` を入れる ``RUN`` が他に無く、``apt-get update`` が 2 回のまま (受け入れ条件 5)
- 版の確認の ``RUN`` に ``shellcheck --version`` がある (受け入れ条件 4)

補助の関数は ``test_base_dockerfile_fonts.py`` から import しない。テストのファイルどうしを
依存させない (``test_base_dockerfile_bao.py`` も自前の ``_statements`` を持つ)。
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "containers" / "base" / "Dockerfile"
SHELLCHECK = re.compile(r"(?<![\w-])shellcheck(?![\w-])")


def _statements() -> str:
    """コメント行を除いた Dockerfile の本文 (説明の注記に assertion が反応しないように)"""
    return "\n".join(
        line for line in DOCKERFILE.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def _run_blocks() -> list[str]:
    """Dockerfile を RUN ブロック単位 (行継続を含む 1 命令分) に分ける

    ``RUN`` で始まる**行**だけを見ると、行継続の先にあるパッケージ名を 1 つも拾えない。
    """
    blocks: list[str] = []
    block: list[str] | None = None
    for line in _statements().splitlines():
        if block is None:
            if not line.startswith("RUN "):
                continue
            block = []
        block.append(line)
        if not line.rstrip().endswith("\\"):
            blocks.append("\n".join(block))
            block = None
    if block is not None:  # 最終行が \ で終わっていても取りこぼさない
        blocks.append("\n".join(block))
    assert blocks, "RUN が 1 つも見つからない"
    return blocks


def _first_apt_install(block: str) -> str:
    """1 つ目の RUN の**1 回目**の apt-get install の一覧だけを取り出す

    1 つ目の RUN は apt-get install を 2 回呼ぶ。RUN の本文全体で探すと、2 回目の一覧
    (後から足したリポジトリの docker-ce / gh / nodejs など) にあっても通ってしまう。
    """
    calls = [m.start() for m in re.finditer(r"apt-get install", block)]
    assert len(calls) >= 2, "1 つ目の RUN に apt-get install が 2 回無い"
    return block[calls[0]:calls[1]]


def _version_check_run() -> str:
    """gh / node / aws などの版を確かめる RUN の 1 命令分"""
    found = [b for b in _run_blocks() if "gh --version" in b and "session-manager-plugin --version" in b]
    assert len(found) == 1, "版の確認の RUN がちょうど 1 つではない"
    return found[0]


def test_shellcheck_is_in_the_first_apt_install():
    """決定 1。標準のアーカイブのパッケージなので、1 回目の一覧へ置く"""
    assert SHELLCHECK.search(_first_apt_install(_run_blocks()[0]))


def test_shellcheck_is_not_in_the_second_apt_install():
    """2 回目は後から足したリポジトリのパッケージを入れる場所で、混ぜない"""
    first = _run_blocks()[0]
    second = first[[m.start() for m in re.finditer(r"apt-get install", first)][1]:]
    assert not SHELLCHECK.search(second)


def test_no_extra_run_installs_shellcheck():
    """決定 1。新しい RUN を立てず、apt-get update をもう 1 回走らせない"""
    for block in _run_blocks()[1:]:
        assert not re.search(r"apt-get\s+install[^;&]*(?<![\w-])shellcheck(?![\w-])", block)
    assert _statements().count("apt-get update") == 2, "apt-get update の回数が変わっている"


def test_version_check_run_calls_shellcheck():
    """決定 2。入れ損ないを、版の確認の RUN で止める (無ければ終了コード 127)"""
    commands = [c.strip() for c in _version_check_run().removeprefix("RUN ").split("&&")]
    assert "shellcheck --version" in commands
