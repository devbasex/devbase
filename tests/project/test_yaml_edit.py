"""コメントと並びを残した YAML の書き換え (devbase.project.yaml_edit)"""

from __future__ import annotations

import pytest

from devbase.errors import ConfigError
from devbase.project.yaml_edit import YamlDocument

BASE = """\
# 先頭のコメント
version: 1
scale: 1  # 並列数
open_editor: true

defaults:
  owner: example-org  # 既定の所有者

# リポジトリ
repos:
  - repo: myapp  # 本体
    branch: develop
  # 2 件目の説明
  - owner: other
    repo: sub

# 末尾のコメント
"""


def _doc(text=BASE):
    return YamlDocument(text, "project.yml")


def test_set_replaces_only_the_value():
    doc = _doc()
    doc.set(("open_editor",), False)
    assert doc.text == BASE.replace("open_editor: true", "open_editor: false")


def test_set_keeps_inline_comment():
    doc = _doc()
    doc.set(("defaults", "owner"), "new-org")
    assert "  owner: new-org  # 既定の所有者\n" in doc.text


def test_set_inserts_below_the_last_anchor():
    doc = _doc()
    doc.set(("work_dir",), "/work/x", after=("version", "scale"))
    assert "scale: 1  # 並列数\nwork_dir: /work/x\nopen_editor" in doc.text


def test_set_adds_key_to_sequence_item_without_moving_comments():
    doc = _doc()
    doc.set(("repos", 0, "init"), False)
    assert ("    branch: develop\n    init: false\n  # 2 件目の説明\n"
            in doc.text)


def test_set_creates_missing_parent_before_repos():
    text = "version: 1\nrepos:\n  - owner: a\n    repo: b\n"
    doc = _doc(text)
    doc.set(("defaults", "branch"), "develop")
    assert doc.text == ("version: 1\ndefaults:\n  branch: develop\n"
                        "repos:\n  - owner: a\n    repo: b\n")


@pytest.mark.parametrize("text,expected", [
    ("", "docker:\n  context: x\n"),
    ("# メモ\n", "# メモ\ndocker:\n  context: x\n"),
    ("# メモ", "# メモ\ndocker:\n  context: x\n"),
    ("docker:  # 空\n", "docker:  # 空\n  context: x\n"),
    ("docker: {}\n", "docker: {context: x}\n"),
    ("docker: {gid: 1}\n", "docker: {gid: 1, context: x}\n"),
    ("docker:\n  gid: 1", "docker:\n  gid: 1\n  context: x"),
])
def test_set_in_small_documents(text, expected):
    doc = _doc(text)
    doc.set(("docker", "context"), "x")
    assert doc.text == expected


def test_set_fills_an_empty_value():
    doc = _doc("version: 1\nwork_dir:\nrepos: []\n")
    doc.set(("work_dir",), "/work/a")
    assert "work_dir: /work/a\n" in doc.text


@pytest.mark.parametrize("value", [
    "true", "123", "a,b", "a: b", "# x", "日本語", 'q"uote', "-x", "null", "~", "1.5", "{a}",
])
@pytest.mark.parametrize("text", ["version: 1\nrepos:\n  - repo: a\n",
                                  "version: 1\nrepos: [{repo: a}]\n"])
def test_strings_stay_strings(text, value):
    doc = _doc(text)
    doc.set(("repos", 0, "branch"), value)
    assert doc.data["repos"][0]["branch"] == value


def test_delete_removes_the_line_only():
    doc = _doc()
    doc.delete(("open_editor",))
    assert doc.text == BASE.replace("open_editor: true\n", "")


def test_delete_first_key_of_sequence_item():
    doc = _doc()
    doc.delete(("repos", 1, "owner"))
    assert "  # 2 件目の説明\n  - repo: sub\n" in doc.text


def test_delete_last_key_removes_the_parent():
    doc = _doc()
    doc.delete(("defaults", "owner"))
    assert "defaults" not in doc.data
    assert "# リポジトリ\nrepos:" in doc.text


def test_delete_missing_key_is_noop():
    doc = _doc()
    doc.delete(("work_dir",))
    doc.delete(("docker", "gid"))
    assert doc.text == BASE


@pytest.mark.parametrize("index,left", [(0, "sub"), (1, "myapp")])
def test_delete_sequence_item(index, left):
    doc = _doc()
    doc.delete(("repos", index))
    assert [r["repo"] for r in doc.data["repos"]] == [left]
    assert "# 先頭のコメント" in doc.text and "# 末尾のコメント" in doc.text


@pytest.mark.parametrize("index", [0, 1, 2])
def test_delete_flow_sequence_item(index):
    doc = _doc("repos: [{repo: a}, {repo: b}, {repo: c}]\n")
    doc.delete(("repos", index))
    assert len(doc.data["repos"]) == 2


@pytest.mark.parametrize("key", ["a", "b"])
def test_delete_flow_mapping_key(key):
    doc = _doc("m: {a: 1, b: 2}\n")
    doc.delete(("m", key))
    assert list(doc.data["m"]) == [k for k in "ab" if k != key]


@pytest.mark.parametrize("text", [
    "repos:\n  - repo: a\n    owner: x\n\n# 末尾\n",
    "repos:\n- repo: a\n  owner: x\n",
    "repos: [{repo: a}]\n",
    "repos:\n  - {repo: a}  # フロー\n",
    "repos:\n  - repo: a",
])
def test_append(text):
    doc = _doc(text)
    doc.append(("repos",), {"repo": "b", "owner": "y"})
    assert doc.data["repos"][-1] == {"repo": "b", "owner": "y"}


def test_append_goes_before_trailing_comment():
    doc = _doc()
    doc.append(("repos",), {"repo": "z"})
    assert "    repo: sub\n  - repo: z\n\n# 末尾のコメント\n" in doc.text


def test_crlf_is_kept():
    doc = _doc(BASE.replace("\n", "\r\n"))
    doc.set(("work_dir",), "/work/x", after=("open_editor",))
    doc.append(("repos",), {"repo": "z", "owner": "y"})
    doc.delete(("repos", 1, "owner"))
    doc.delete(("scale",))
    assert doc.text.count("\n") == doc.text.count("\r\n")


@pytest.mark.parametrize("text,edit", [
    ("a: &x\n  k: 1\nb: *x\n", lambda d: d.set(("b", "k"), 2)),
    ("a: &x\n  k: 1\nb: *x\n", lambda d: d.set(("a", "k"), 2)),
    ("a: &x\n  k: 1\nb:\n  <<: *x\n  j: 1\n", lambda d: d.delete(("b", "k"))),
    ("docker: null\n", lambda d: d.set(("docker", "gid"), 1)),
], ids=["alias", "anchor", "merge-key", "explicit-null"])
def test_unsupported_notation_is_refused_without_change(text, edit):
    doc = _doc(text)
    with pytest.raises(ConfigError, match="直接編集"):
        edit(doc)
    assert doc.text == text
