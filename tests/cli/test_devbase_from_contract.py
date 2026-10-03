"""直の親の読み方を、通常のビルドと `--expires` の判定が同じに持つ (#404 受け入れ条件 7)。

同じ Dockerfile を、通常のビルドの経路 (`bin/devbase build` を実プロセスで起動し、偽の `uv` が受けた
`docker buildx build --load -t <イメージ>` を見る) と `_get_base_image_ref` の両方に通し、表の答えと
一致することを見る。あわせて `bin/devbase` の `_DEVBASE_FROM_RE` と `DEVBASE_FROM_PATTERN` の値の一致を見る。
"""

from __future__ import annotations

import re

import pytest

from devbase.commands import container
from devbase.utils.dockerfile import DEVBASE_FROM_PATTERN
from tests.cli.conftest import WRAPPER

# (Dockerfile の本文, 読むイメージ。None は読まない)
CONTRACT = [
    ("FROM devbase-php:latest\n", "devbase-php:latest"),
    ("FROM devbase-php\n", "devbase-php:latest"),
    ("FROM --platform=linux/amd64 devbase-base:latest\n", "devbase-base:latest"),
    ("from devbase-base:latest\n", "devbase-base:latest"),
    ("FROM devbase-base:latest AS builder\n", "devbase-base:latest"),
    ("  FROM devbase-base:latest\n", "devbase-base:latest"),
    ("# FROM devbase-base:latest\n", None),
    ("COPY --from=devbase-base:latest /a /b\n", None),
    ("FROM ubuntu:26.04\n", None),
    # 入出力の契約で足した行
    ("FROM devbase-php\r\n", "devbase-php:latest"),
    ("FROM ubuntu:26.04 AS x\nFROM devbase-base:latest\n", "devbase-base:latest"),
    ("FROM DEVBASE-base:latest\n", None),
]

_IDS = [
    "plain", "no-tag", "platform", "lowercase", "as-stage", "leading-space",
    "comment", "copy-from", "ubuntu-only", "crlf", "second-from", "uppercase-name",
]


def _uv_lines(result):
    return [line[len("UV:"):] for line in result.stdout.splitlines() if line.startswith("UV:")]


def _read_by_normal_build(exec_wrapper, text):
    """通常のビルドが直の親として建てるイメージ。`devbase-base` の有無の分岐へ進んだら None。"""
    for name in ("base", "php"):
        path = exec_wrapper.root / "containers" / name
        path.mkdir(parents=True)
        (path / "Dockerfile").write_text("FROM ubuntu:26.04\n")
    project = exec_wrapper.work / "myproj"
    project.mkdir()
    (project / "Dockerfile").write_bytes(text.encode())

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    built = [line.split("docker buildx build --load -t ", 1)[1].split()[0]
             for line in _uv_lines(result) if "docker buildx build --load -t " in line]
    if not built:
        # 偽の uv は image inspect に 0 を返す (base がある) ため、何も建てずに進む
        assert "devbase-base already exists" in result.stdout, result.stdout
        return None
    assert len(built) == 1, built
    return built[0]


def _read_by_expires(tmp_path, text):
    (tmp_path / "Dockerfile").write_bytes(text.encode())
    return container._get_base_image_ref({"build": {"context": str(tmp_path)}})


@pytest.mark.parametrize("text,expected", CONTRACT, ids=_IDS)
def test_normal_build_and_expires_read_the_same_parent(exec_wrapper, tmp_path, text, expected):
    expires_dir = tmp_path / "expires"
    expires_dir.mkdir()

    assert _read_by_normal_build(exec_wrapper, text) == expected
    assert _read_by_expires(expires_dir, text) == expected


def test_wrapper_from_regex_is_synced_with_python():
    """決定 1: bin/devbase の `_DEVBASE_FROM_RE` は Python の正本と同じ正規表現。"""
    found = re.findall(r"^_DEVBASE_FROM_RE=\$'([^']*)'$", WRAPPER.read_text(), re.M)
    assert found, "bin/devbase から _DEVBASE_FROM_RE を抜き出せない"
    assert [value.replace("\\t", "\t") for value in found] == [DEVBASE_FROM_PATTERN]
