"""project.yml の読み込み・正規化・検証と clone プランの wire format"""

from __future__ import annotations

import base64
import shutil
import subprocess

import pytest

from devbase.errors import ConfigError
from devbase.project.config import (
    decode_repo_plan,
    encode_repo_plan,
    load_project_config,
    parse_project_config,
)


def write_project_yml(tmp_path, text: str):
    (tmp_path / "project.yml").write_text(text, encoding="utf-8")
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


@pytest.mark.parametrize("bad_version", [True, False, 1.0, "1"])
def test_non_integer_version_is_an_error(bad_version):
    """YAML の ``true`` / ``1.0`` は ``== 1`` を満たすため型で明示的に弾く"""
    with pytest.raises(ConfigError, match="version"):
        parse_project_config({
            "version": bad_version,
            "repos": [{"owner": "volareinc", "repo": "carmo"}],
        }, source="project.yml")


@pytest.mark.parametrize("bad_defaults", [[], False, 0, "host", ["host"]])
def test_non_mapping_defaults_is_an_error(bad_defaults):
    """falsy な非マッピングを空マッピング扱いで黙って受理しない"""
    with pytest.raises(ConfigError, match="defaults"):
        parse_project_config({
            "version": 1,
            "defaults": bad_defaults,
            "repos": [{"owner": "volareinc", "repo": "carmo"}],
        }, source="project.yml")


def test_null_defaults_is_treated_as_empty():
    """``defaults:`` と書いただけ (null) は未指定と同じ扱い"""
    config = parse_project_config({
        "version": 1,
        "defaults": None,
        "repos": [{"owner": "volareinc", "repo": "carmo"}],
    }, source="project.yml")

    assert config.repos[0].host == "github.com"


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
    ("repo", "car\x1fmo"),  # US — wire format の列区切りそのもの
])
def test_fields_reject_whitespace_and_separators(field, value):
    """wire format (US 区切り・LF 行区切り) と URL 組み立てを壊す値を弾く"""
    spec = {"owner": "volareinc", "repo": "carmo"}
    spec[field] = value
    with pytest.raises(ConfigError, match=field):
        parse_project_config({"version": 1, "repos": [spec]}, source="project.yml")


@pytest.mark.parametrize("value", [
    "car\x00mo",   # NUL — bash の read / git のどちらにとっても異物
    "car\x07mo",   # BEL
    "car\x7fmo",   # DEL
    "car\u200bmo",  # ゼロ幅空白 — 目視できないまま URL に混ざる
])
def test_fields_reject_non_whitespace_control_characters(value):
    """``isspace()`` ではすり抜ける制御文字・ゼロ幅空白も弾く

    :func:`encode_repo_plan` の docstring が「制御文字を一切含まない」と
    宣言している以上、空白判定だけでは契約を満たせない。
    """
    with pytest.raises(ConfigError, match="repo"):
        parse_project_config({
            "version": 1,
            "repos": [{"owner": "volareinc", "repo": value}],
        }, source="project.yml")


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


def test_non_utf8_file_is_an_error_with_encoding_hint(tmp_path):
    """Shift-JIS 等で保存されたファイルは UTF-8 での保存を案内する"""
    (tmp_path / "project.yml").write_bytes(
        "version: 1  # 日本語コメント\n".encode("cp932"))

    with pytest.raises(ConfigError, match="UTF-8"):
        load_project_config(tmp_path)


def test_yaml_root_must_be_a_mapping(tmp_path):
    write_project_yml(tmp_path, "- repo: carmo")

    with pytest.raises(ConfigError, match="project.yml"):
        load_project_config(tmp_path)


# ---------------------------------------------------------------------------
# wire format (entrypoint との契約)
# ---------------------------------------------------------------------------

#: wire format のフィールド区切り (unit separator)。
US = "\x1f"

#: entrypoint (bash) が想定する読み取り方をそのまま再現する consumer。
#: 素朴な ``while read`` で 4 列が欠けずに読めることを、実際の bash で確かめる。
_BASH_CONSUMER = r"""
set -eu
printf '%s' "$PLAN" | base64 -d |
  while IFS=$'\x1f' read -r url dir branch init; do
    printf '[%s][%s][%s][%s]\n' "$url" "$dir" "$branch" "$init"
  done
"""


def run_bash_consumer(encoded: str) -> list:
    """符号化済み clone プランを bash の ``while read`` で読ませて行を返す。"""
    result = subprocess.run(
        ["bash", "-c", _BASH_CONSUMER],
        env={"PLAN": encoded, "PATH": "/usr/bin:/bin:/usr/local/bin"},
        capture_output=True, text=True, check=True,
    )
    return result.stdout.splitlines()


requires_bash = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash が無い環境ではスキップ")


def test_encode_repo_plan_is_base64_unit_separated():
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

    assert decoded == (
        f"https://gitlab.com/uttaro_dev/uttarov2.git{US}system{US}{US}1\n"
        f"https://github.com/uttaro-dev2/uttarov2-doc.git{US}uttarov2-doc"
        f"{US}develop{US}0\n"
    )


def test_encoded_plan_ends_with_newline():
    """末尾 LF が無いと素朴な ``while read`` consumer が最後の行を落とす"""
    config = parse_project_config({
        "version": 1,
        "repos": [{"owner": "volareinc", "repo": "carmo"}],
    }, source="project.yml")

    decoded = base64.b64decode(encode_repo_plan(config.repos)).decode()

    assert decoded.endswith("\n")


@requires_bash
def test_bash_consumer_reads_every_column_including_empty_branch():
    """branch 未指定 (空フィールド) でも init が branch にずれ込まないこと

    区切りをタブにすると bash の IFS 空白扱いで連続区切りが 1 つに畳まれ、
    ``1`` が branch に入って init が空になる。US (\x1f) なら空フィールドが残る。
    """
    config = parse_project_config({
        "version": 1,
        "defaults": {"owner": "volareinc"},
        "repos": [
            {"repo": "carmo"},
            {"repo": "carmo-batch", "branch": "develop", "init": False},
        ],
    }, source="project.yml")

    lines = run_bash_consumer(encode_repo_plan(config.repos))

    assert lines == [
        "[https://github.com/volareinc/carmo.git][carmo][][1]",
        "[https://github.com/volareinc/carmo-batch.git][carmo-batch][develop][0]",
    ]


@requires_bash
def test_bash_consumer_does_not_drop_the_last_line_for_a_single_repo():
    """末尾 LF が無いと 1 repo 構成では唯一の行がループ本体に入らない"""
    config = parse_project_config({
        "version": 1,
        "repos": [{"owner": "volareinc", "repo": "carmo"}],
    }, source="project.yml")

    lines = run_bash_consumer(encode_repo_plan(config.repos))

    assert lines == ["[https://github.com/volareinc/carmo.git][carmo][][1]"]


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


@pytest.mark.parametrize("bad_init", ["", "2", "true", "0 "])
def test_decode_rejects_init_column_outside_one_and_zero(bad_init):
    """壊れた値・将来の未知値を「init しない」として黙って通さない"""
    line = f"https://github.com/volareinc/carmo.git{US}carmo{US}{US}{bad_init}"
    encoded = base64.b64encode(line.encode()).decode()

    with pytest.raises(ConfigError, match="init"):
        decode_repo_plan(encoded)


def test_numeric_repo_name_reports_a_type_error_not_a_missing_field():
    """YAML が int として読む ``repo: 123`` に「必須です」と言わない

    「指定したのに必須と言われる」を避けるため、未指定と型不一致を書き分ける。
    """
    with pytest.raises(ConfigError, match="repo は文字列で指定してください"):
        parse_project_config({
            "version": 1,
            "repos": [{"owner": "volareinc", "repo": 123}],
        }, source="project.yml")


def test_unspecified_repo_reports_a_missing_field():
    with pytest.raises(ConfigError, match="repo は必須です"):
        parse_project_config({
            "version": 1,
            "repos": [{"owner": "volareinc", "repo": None}],
        }, source="project.yml")


def test_empty_repo_reports_an_empty_value_not_a_missing_field():
    """``repo: ""`` は「指定はされている」ので「必須です」とは言わない"""
    with pytest.raises(ConfigError, match="repo に空文字は指定できません"):
        parse_project_config({
            "version": 1,
            "repos": [{"owner": "volareinc", "repo": ""}],
        }, source="project.yml")


def test_empty_optional_branch_is_rejected_as_empty_not_as_missing():
    """branch は省略可能なので ``branch: ""`` に「必須です」と返すと矛盾する"""
    with pytest.raises(ConfigError, match="branch に空文字は指定できません"):
        parse_project_config({
            "version": 1,
            "repos": [{"owner": "volareinc", "repo": "carmo", "branch": ""}],
        }, source="project.yml")


def test_encoded_plan_has_no_shell_or_compose_hazards():
    """compose の変数展開・改行で壊れないこと (base64 なので英数と = のみ)"""
    config = parse_project_config({
        "version": 1,
        "repos": [{"owner": "volareinc", "repo": "carmo"}],
    }, source="project.yml")

    encoded = encode_repo_plan(config.repos)

    assert encoded.strip() == encoded
    assert all(c.isalnum() or c in "+/=" for c in encoded)
