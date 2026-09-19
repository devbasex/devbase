"""PLAN61: 名前の形の規則 (`devbase.utils.names`)。

`projects/` と `containers/` の直下の 1 つの名前として受け付ける形を 1 か所で決める。
先頭が英数字なので `.` と `..` は当たらず、`/` `\\` 空白 非 ASCII は含められない。
"""

from __future__ import annotations

import pytest

from devbase.utils.names import SINGLE_SEGMENT_NAME_PATTERN, is_single_segment_name


@pytest.mark.parametrize("name", ["carmo", "github_work_time", "carmo-ai", "php85",
                                  "carmo.takemi", "9lives", "a"])
def test_accepts_real_project_names(name):
    """受け入れ条件 5: 実在するプロジェクト名の形は通る。"""
    assert is_single_segment_name(name) is True


@pytest.mark.parametrize("name", ["../etc", "a/b", ".", "..", "", "-x", "..\\etc",
                                  "a b", "carmo\n", ".hidden", "_private"])
def test_rejects_paths_flags_and_empty(name):
    """`..` `/` `\\` 空 `-` 始まり 末尾の改行 `.` 始まり `_` 始まりは名前の形ではない。"""
    assert is_single_segment_name(name) is False


def test_rejects_non_ascii():
    """決定 4: Python の `[A-Za-z0-9]` は ASCII だけに一致する。"""
    assert is_single_segment_name("café") is False


def test_pattern_is_the_image_name_allowlist():
    """決定 2: `_build_single_image` のイメージ名と同じ許可リスト。"""
    assert SINGLE_SEGMENT_NAME_PATTERN == r"[A-Za-z0-9][A-Za-z0-9._-]*"
