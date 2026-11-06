"""雛形の生成と registry.yml への追記のテスト (#514)。"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from devbase.errors import RepositoryError
from devbase.plugin import scaffold
from devbase.plugin.github import GitHubRef
from devbase.project.config import parse_project_config

REF = GitHubRef("owner", "app", "https://github.com/owner/app.git")


def test_registry_template_has_no_plugins():
    data = yaml.safe_load(scaffold.registry_yml("my-repo"))
    assert data == {"name": "my-repo", "description": "", "plugins": []}


def test_plugin_files():
    files = scaffold.plugin_files("team-a")
    assert set(files) == {"team-a/plugin.yml", "team-a/projects/.gitkeep"}
    data = yaml.safe_load(files["team-a/plugin.yml"])
    assert data == {
        "name": "team-a", "version": "1.0.0", "description": "",
        "requires": {"devbase": ">=3.0.0"},
        "projects": {"mode": "auto"}, "priority": 50,
    }


def test_project_files():
    files = scaffold.project_files("team-a/", "My-App", REF, "acme")
    assert set(files) == {
        "team-a/projects/My-App/project.yml",
        "team-a/projects/My-App/compose.yml",
        "team-a/projects/My-App/env",
    }
    config = parse_project_config(
        yaml.safe_load(files["team-a/projects/My-App/project.yml"]), "project.yml")
    assert config.scale == 1
    assert config.open_editor is True
    assert config.primary.owner == "owner"
    assert config.primary.repo == "app"

    compose = yaml.safe_load(files["team-a/projects/My-App/compose.yml"])
    assert list(compose["services"]) == ["dev"]
    dev = compose["services"]["dev"]
    assert dev["image"] == "my-app:latest"
    assert dev["build"]["context"] == "${DEVBASE_ROOT}/containers/general/"
    assert "env" in dev["env_file"]

    assert files["team-a/projects/My-App/env"] == "DEVBASE_ACCOUNT_GROUP=acme\n"


@pytest.mark.parametrize("name", ["null", "true", "1.0", "yes", "012"])
def test_templates_keep_names_as_strings(name):
    assert yaml.safe_load(scaffold.registry_yml(name))["name"] == name
    assert yaml.safe_load(scaffold.plugin_files(name)[f"{name}/plugin.yml"])["name"] == name
    added = scaffold.add_registry_entry("plugins: []\n", name)
    assert yaml.safe_load(added)["plugins"][0]["name"] == name


def _entries(text):
    return yaml.safe_load(text)["plugins"]


def test_add_to_empty_flow_list():
    text = scaffold.registry_yml("r")
    added = scaffold.add_registry_entry(text, "team-a")
    assert _entries(added) == [{"name": "team-a", "path": "team-a", "description": ""}]
    assert added.startswith('name: r\n')


COMMENTED = textwrap.dedent("""\
    # 社内の説明コメント
    name: 'quoted'   # 引用符を保つ
    plugins:
      # 先頭のプラグイン
      - name: alpha
        path: alpha/
        description: "A"

      # 二つ目
      - name: beta
        path: beta
        description: B

    maintainer: someone
    # 末尾のコメント
    """)


def test_add_keeps_comments_quotes_and_order():
    added = scaffold.add_registry_entry(COMMENTED, "gamma")
    # 既存の行はすべて同じ順で残る
    new_lines = added.splitlines()
    position = 0
    for line in COMMENTED.splitlines():
        position = new_lines.index(line, position) + 1
    assert len(new_lines) == len(COMMENTED.splitlines()) + 3
    assert [e["name"] for e in _entries(added)] == ["alpha", "beta", "gamma"]
    assert yaml.safe_load(added)["maintainer"] == "someone"
    # 項目は最後の項目の直後に入る (空行やコメントの後ろへ流れない)
    assert new_lines[new_lines.index("    description: B") + 1] == "  - name: gamma"


def test_add_with_unindented_sequence():
    text = "name: r\nplugins:\n- name: alpha\n  path: alpha\n  description: ''\n"
    added = scaffold.add_registry_entry(text, "beta")
    assert [e["name"] for e in _entries(added)] == ["alpha", "beta"]
    assert added.splitlines()[-3] == "- name: beta"


def test_add_when_key_is_empty():
    assert _entries(scaffold.add_registry_entry("name: r\nplugins:\ndescription: d\n", "x")) == [
        {"name": "x", "path": "x", "description": ""}]


def test_add_when_key_is_missing():
    added = scaffold.add_registry_entry("name: r\n", "x")
    assert yaml.safe_load(added)["name"] == "r"
    assert [e["name"] for e in _entries(added)] == ["x"]


def test_add_without_trailing_newline():
    added = scaffold.add_registry_entry("plugins:\n  - name: a\n    path: a", "b")
    assert [e["name"] for e in _entries(added)] == ["a", "b"]


def test_add_refuses_flow_list_with_items():
    text = "plugins: [{name: a, path: a}]\n"
    with pytest.raises(RepositoryError, match="registry.yml"):
        scaffold.add_registry_entry(text, "b")


def test_add_refuses_broken_yaml():
    with pytest.raises(RepositoryError, match="registry.yml"):
        scaffold.add_registry_entry("plugins: [\n", "b")


def test_add_stops_when_the_result_differs(monkeypatch):
    """読み直しの検証 (I3): 既存の項目が変わる追記は返さない。"""
    monkeypatch.setattr(scaffold, "_insert_entry",
                        lambda text, name: "plugins:\n  - name: other\n    path: other\n")
    with pytest.raises(RepositoryError, match="registry.yml"):
        scaffold.add_registry_entry("plugins:\n  - name: a\n    path: a\n", "b")
