"""Dockerfile の文字列を命令に分ける共通の部品。

Docker を起動せずに Dockerfile の形を固定するテストが使う。``dockerfile_parse.py`` は
``test_`` で始まらないため pytest は収集しない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Instruction:
    keyword: str
    args: str


def parse(text: str) -> list[Instruction]:
    """行の継続をつなぎ、コメントの行を除いて命令に分ける。RUN の heredoc の本文も命令に含める"""
    instructions: list[Instruction] = []
    lines = text.splitlines()
    buf: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            buf.append(stripped[:-1])
            continue
        buf.append(stripped)
        joined = " ".join(buf)
        buf = []
        marker = re.search(r"<<-?\s*'?\"?(\w+)'?\"?", joined)
        if marker and joined.split(None, 1)[0].upper() == "RUN":
            body: list[str] = []
            while i < len(lines) and lines[i].strip() != marker.group(1):
                body.append(lines[i])
                i += 1
            i += 1
            joined += "\n" + "\n".join(body)
        keyword, _, args = joined.partition(" ")
        instructions.append(Instruction(keyword.upper(), args.strip()))
    if buf:
        joined = " ".join(buf)
        keyword, _, args = joined.partition(" ")
        instructions.append(Instruction(keyword.upper(), args.strip()))
    return instructions


def run_blocks(text: str) -> list[str]:
    """Dockerfile を RUN ブロック単位 (行継続を含む 1 命令分) に分ける

    ``RUN`` の本文は ``\\`` の行継続で複数行にまたがる。``RUN`` で始まる**行**だけを
    見ると、拾えるのは 1 行目 (``RUN set -eux; \\``) だけでパッケージ名は 1 つも
    入らない。命令ごとに継続行まで連結してから検査する。
    """
    blocks: list[str] = []
    block: list[str] | None = None
    for line in text.splitlines():
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
