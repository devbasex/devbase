"""base イメージの OpenBao CLI (`bao`) の導入 (PLAN54 / #169)

Docker を起動せず、Dockerfile の文言で次の 3 点を固定する。実際のビルドは手で確かめる。

- 版は ``ARG BAO_VERSION`` の 1 か所で持ち、サーバと同じ 2.6 系に固定する
- amd64 / arm64 の両方の tar.gz を選べる
- 同じリリースの ``checksums.txt`` で SHA-256 を検証し、一致しなければビルドを止める
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "containers" / "base" / "Dockerfile"


def _statements() -> str:
    """コメント行を除いた Dockerfile の本文 (説明の注記に assertion が反応しないように)"""
    return "\n".join(
        line for line in DOCKERFILE.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def _bao_run_block() -> str:
    """`bao` を入れる RUN の 1 命令分 (行継続を含む) を取り出す"""
    lines = _statements().splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("ARG BAO_VERSION=")), None)
    assert start is not None, "ARG BAO_VERSION が見つからない"
    assert lines[start + 1].startswith("RUN "), "ARG BAO_VERSION の直後に RUN が無い"
    block = []
    for line in lines[start + 1:]:
        block.append(line)
        if not line.rstrip().endswith("\\"):
            break
    return "\n".join(block)


def test_version_is_pinned_once_in_an_arg():
    text = _statements()
    assert re.search(r"^ARG BAO_VERSION=2\.6\.\d+$", text, flags=re.MULTILINE)
    # 版の数字は ARG の 1 か所だけに書く (URL やファイル名は変数を参照する)
    assert len(re.findall(r"2\.6\.\d+", text)) == 1


def test_both_architectures_are_selectable():
    block = _bao_run_block()
    assert "dpkg --print-architecture" in block
    assert "amd64" in block and "arm64" in block
    assert "openbao_${BAO_VERSION}_linux_${bao_arch}.tar.gz" in block


def test_tarball_is_verified_against_the_release_checksums():
    block = _bao_run_block()
    assert 'bao_base="https://github.com/openbao/openbao/releases/download/v${BAO_VERSION}"' in block
    assert '"${bao_base}/checksums.txt"' in block
    assert "sha256sum -c" in block
    # 検証の後に展開する (検証の前に展開すると、改ざんされた物を置いてから止まる)
    assert block.index("sha256sum -c") < block.index("tar -xzf")


def test_only_the_bao_binary_is_installed_and_checked():
    block = _bao_run_block()
    assert re.search(r"tar -xzf \S+ -C /usr/local/bin bao", block)
    assert "bao version" in block
