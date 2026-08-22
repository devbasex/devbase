"""project.yml の読み込み・正規化・検証と clone プランの wire format"""

from __future__ import annotations

import base64

import pytest

from devbase.errors import ConfigError
from devbase.project.config import (
    decode_repo_plan,
    encode_repo_plan,
    load_project_config,
    parse_project_config,
)


def write_project_yml(tmp_path, text: str):
    (tmp_path / "project.yml").write_text(text)
    return tmp_path


# ---------------------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------------------

def test_single_repo_defaults():
    """最小構成: host は github.com、dir は repo 名、init は有効、先頭が primary"""
    config = parse_project_config({
        "version": 1,
        "repos": [{"owner": "volareinc", "repo": "carmo"}],
    }, source="project.yml")

    (repo,) = config.repos
    assert repo.host == "github.com"
    assert repo.owner == "volareinc"
    assert repo.repo == "carmo"
    assert repo.dir == "carmo"
    assert repo.branch is None
    assert repo.init is True
    assert repo.primary is True
    assert repo.url == "https://github.com/volareinc/carmo.git"
    assert config.primary is repo


def test_defaults_are_inherited_and_overridable():
    config = parse_project_config({
        "version": 1,
        "defaults": {"host": "github.com", "owner": "uttaro-dev2"},
        "repos": [
            {"repo": "uttarov2", "host": "gitlab.com", "owner": "uttaro_dev", "dir": "system"},
            {"repo": "uttarov2-doc"},
            {"repo": "uttarov2migration", "branch": "develop", "init": False},
        ],
    }, source="project.yml")

    system, doc, migration = config.repos
    assert system.url == "https://gitlab.com/uttaro_dev/uttarov2.git"
    assert system.dir == "system"
    assert doc.url == "https://github.com/uttaro-dev2/uttarov2-doc.git"
    assert doc.dir == "uttarov2-doc"
    assert migration.branch == "develop"
    assert migration.init is False


def test_primary_can_be_chosen_explicitly():
    config = parse_project_config({
        "version": 1,
        "defaults": {"owner": "volareinc"},
        "repos": [{"repo": "carmo-doc"}, {"repo": "carmo", "primary": True}],
    }, source="project.yml")

    assert config.primary.repo == "carmo"
    assert [r.primary for r in config.repos] == [False, True]


def test_optional_settings_are_read():
    config = parse_project_config({
        "version": 1,
        "scale": 3,
        "open_editor": False,
        "work_dir": "/work/carmo/app",
        "repos": [{"owner": "volareinc", "repo": "carmo"}],
    }, source="project.yml")

    assert config.scale == 3
    assert config.open_editor is False
    assert config.work_dir == "/work/carmo/app"


def test_optional_settings_default_to_none():
    """未指定の設定は None。既定値の解釈は呼び出し側 (env / 既定値) に委ねる"""
    config = parse_project_config({
        "version": 1,
        "repos": [{"owner": "volareinc", "repo": "carmo"}],
    }, source="project.yml")

    assert config.scale is None
    assert config.open_editor is None
    assert config.work_dir is None


def test_work_dir_defaults_to_primary_repo_dir():
    config = parse_project_config({
        "version": 1,
        "defaults": {"owner": "volareinc"},
        "repos": [{"repo": "carmo-doc"}, {"repo": "carmo", "primary": True}],
    }, source="project.yml")

    assert config.resolved_work_dir() == "/work/carmo"


def test_resolved_work_dir_prefers_explicit_value():
    config = parse_project_config({
        "version": 1,
        "work_dir": "/work/carmo/app",
        "repos": [{"owner": "volareinc", "repo": "carmo"}],
    }, source="project.yml")

    assert config.resolved_work_dir() == "/work/carmo/app"


def test_load_project_config_reads_file(tmp_path):
    write_project_yml(tmp_path, """
version: 1
scale: 1
defaults:
  owner: KK-Generation
repos:
  - repo: project-trygroup-prd
  - repo: project-trygroup-prd-customer
""")

    config = load_project_config(tmp_path)

    assert config.scale == 1
    assert [r.dir for r in config.repos] == [
        "project-trygroup-prd", "project-trygroup-prd-customer"]


# ---------------------------------------------------------------------------
# 異常系 (後方互換は無いので、曖昧な設定は黙って通さない)
# ---------------------------------------------------------------------------

def test_missing_file_is_an_error_with_migration_hint(tmp_path):
    (tmp_path / "env").write_text("GIT_USER=volareinc\nGIT_REPO=carmo\n")

    with pytest.raises(ConfigError) as excinfo:
        load_project_config(tmp_path)

    message = str(excinfo.value)
    assert "project.yml" in message
    assert "migrate-config" in message


def test_missing_owner_is_an_error():
    with pytest.raises(ConfigError, match="owner"):
        parse_project_config({"version": 1, "repos": [{"repo": "carmo"}]},
                             source="project.yml")


def test_missing_repo_is_an_error():
    with pytest.raises(ConfigError, match="repo"):
        parse_project_config({"version": 1, "repos": [{"owner": "volareinc"}]},
                             source="project.yml")


def test_duplicated_dir_is_an_error():
    with pytest.raises(ConfigError, match="dir"):
        parse_project_config({
            "version": 1,
            "defaults": {"owner": "volareinc"},
            "repos": [{"repo": "carmo"}, {"repo": "carmo-batch", "dir": "carmo"}],
        }, source="project.yml")


def test_multiple_primary_is_an_error():
    with pytest.raises(ConfigError, match="primary"):
        parse_project_config({
            "version": 1,
            "defaults": {"owner": "volareinc"},
            "repos": [{"repo": "carmo", "primary": True},
                      {"repo": "carmo-batch", "primary": True}],
        }, source="project.yml")


def test_empty_repos_is_an_error():
    with pytest.raises(ConfigError, match="repos"):
        parse_project_config({"version": 1, "repos": []}, source="project.yml")


def test_unknown_key_is_an_error():
    """typo を黙って無視しない"""
    with pytest.raises(ConfigError, match="brunch"):
        parse_project_config({
            "version": 1,
            "repos": [{"owner": "volareinc", "repo": "carmo", "brunch": "main"}],
        }, source="project.yml")


def test_unknown_top_level_key_is_an_error():
    with pytest.raises(ConfigError, match="container_scale"):
        parse_project_config({
            "version": 1,
            "container_scale": 2,
            "repos": [{"owner": "volareinc", "repo": "carmo"}],
        }, source="project.yml")


def test_unsupported_version_is_an_error():
    with pytest.raises(ConfigError, match="version"):
        parse_project_config({
            "version": 2,
            "repos": [{"owner": "volareinc", "repo": "carmo"}],
        }, source="project.yml")


def test_missing_version_is_an_error():
    with pytest.raises(ConfigError, match="version"):
        parse_project_config({
            "repos": [{"owner": "volareinc", "repo": "carmo"}],
        }, source="project.yml")


@pytest.mark.parametrize("bad_dir", ["../escape", "nested/dir", ".", "..", "/abs"])
def test_dir_must_stay_directly_under_work(bad_dir):
    """/work の外へ抜ける dir を許すと clone 先が予測できなくなる"""
    with pytest.raises(ConfigError, match="dir"):
        parse_project_config({
            "version": 1,
            "repos": [{"owner": "volareinc", "repo": "carmo", "dir": bad_dir}],
        }, source="project.yml")


@pytest.mark.parametrize("field,value", [
    ("owner", "vola reinc"),
    ("repo", "car\tmo"),
    ("host", "github.com/extra"),
    ("branch", "main\nrm -rf"),
])
def test_fields_reject_whitespace_and_separators(field, value):
    """wire format (タブ区切り) と URL 組み立てを壊す値を弾く"""
    spec = {"owner": "volareinc", "repo": "carmo"}
    spec[field] = value
    with pytest.raises(ConfigError, match=field):
        parse_project_config({"version": 1, "repos": [spec]}, source="project.yml")


def test_scale_must_be_a_positive_integer():
    with pytest.raises(ConfigError, match="scale"):
        parse_project_config({
            "version": 1, "scale": 0,
            "repos": [{"owner": "volareinc", "repo": "carmo"}],
        }, source="project.yml")


def test_open_editor_must_be_boolean():
    with pytest.raises(ConfigError, match="open_editor"):
        parse_project_config({
            "version": 1, "open_editor": "yes",
            "repos": [{"owner": "volareinc", "repo": "carmo"}],
        }, source="project.yml")


def test_broken_yaml_is_an_error(tmp_path):
    write_project_yml(tmp_path, "version: 1\nrepos: [")

    with pytest.raises(ConfigError, match="project.yml"):
        load_project_config(tmp_path)


def test_yaml_root_must_be_a_mapping(tmp_path):
    write_project_yml(tmp_path, "- repo: carmo")

    with pytest.raises(ConfigError, match="project.yml"):
        load_project_config(tmp_path)


# ---------------------------------------------------------------------------
# wire format (entrypoint との契約)
# ---------------------------------------------------------------------------

def test_encode_repo_plan_is_base64_tsv():
    config = parse_project_config({
        "version": 1,
        "defaults": {"owner": "uttaro-dev2"},
        "repos": [
            {"repo": "uttarov2", "host": "gitlab.com", "owner": "uttaro_dev", "dir": "system"},
            {"repo": "uttarov2-doc", "branch": "develop", "init": False},
        ],
    }, source="project.yml")

    encoded = encode_repo_plan(config.repos)
    decoded = base64.b64decode(encoded).decode()

    assert decoded.splitlines() == [
        "https://gitlab.com/uttaro_dev/uttarov2.git\tsystem\t\t1",
        "https://github.com/uttaro-dev2/uttarov2-doc.git\tuttarov2-doc\tdevelop\t0",
    ]


def test_repo_plan_round_trips():
    config = parse_project_config({
        "version": 1,
        "defaults": {"owner": "volareinc"},
        "repos": [{"repo": "carmo"}, {"repo": "carmo-batch", "branch": "main"}],
    }, source="project.yml")

    restored = decode_repo_plan(encode_repo_plan(config.repos))

    assert [(e.url, e.dir, e.branch, e.init) for e in restored] == [
        ("https://github.com/volareinc/carmo.git", "carmo", None, True),
        ("https://github.com/volareinc/carmo-batch.git", "carmo-batch", "main", True),
    ]


def test_encoded_plan_has_no_shell_or_compose_hazards():
    """compose の変数展開・改行で壊れないこと (base64 なので英数と = のみ)"""
    config = parse_project_config({
        "version": 1,
        "repos": [{"owner": "volareinc", "repo": "carmo"}],
    }, source="project.yml")

    encoded = encode_repo_plan(config.repos)

    assert encoded.strip() == encoded
    assert all(c.isalnum() or c in "+/=" for c in encoded)
