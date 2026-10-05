"""`[name]` と `--context` を受け付けるサブコマンドの集合の一致テスト（#214）。

列挙の正本は argparse（`lib/devbase/cli.py` の `_create_parser()`）である。ここで parser を
走査して得た集合に、手で写した次の場所が合っていることを固定する。サブコマンドを足し引きして
写しを取り残すと、取り残した場所（ファイル）と欠けた・余分なサブコマンドを挙げて落ちる。

- `bin/devbase` の `_PROJECT_NAME_SUBCOMMANDS` / `_NAME_RESOLVABLE_SHORTCUTS`
- 補完 2 ファイル（`etc/devbase-completion.bash` は実行して、`etc/_devbase` は静的に読む）
- 確定仕様 `docs/specifications/cli-argument-resolution.md` の 2 つの表

集合の要素は「サブコマンドへの道筋」のタプルで表す（例: `("project", "profile", "up")`、
トップレベルは `("up",)`）。`ct` は `container` の別名として畳む。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

import pytest

from devbase import cli

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "bin" / "devbase"
BASH_COMPLETION = REPO_ROOT / "etc" / "devbase-completion.bash"
ZSH_COMPLETION = REPO_ROOT / "etc" / "_devbase"
SPEC = REPO_ROOT / "docs" / "specifications" / "cli-argument-resolution.md"

# `[name]` がプロジェクト名を表すのは `project` グループ（入れ子の `profile` を含む）と
# トップレベルだけ。`plugin` / `snapshot` / `env backend use` の `name` は別のものの名前。
_PROJECT_NAME_GROUPS = ("project",)

# argparse に parser が無く、ラッパーの実装（シェルの `cmd_build`）が `--context` を
# 受け付けるもの。正本をラッパーに置く唯一の例外として名指しで足す。
_WRAPPER_ONLY_CONTEXT = {("build",)}

# 補完がプロジェクト名を出さないことを認める `[name]` の例外と、その理由。
# ここに無いサブコマンドを補完が欠いたら落ちる（例外は黙って広がらない）。
_PROFILE_REASON = (
    "`project profile up|down|list` の 1 つ目の位置引数は、値が 1 個ならプロファイル名、"
    "2 個ならプロジェクト名になり、補完の時点ではどちらか決まらない"
)
COMPLETION_NAME_EXCEPTIONS = {
    ("project", "profile", "up"): _PROFILE_REASON,
    ("project", "profile", "down"): _PROFILE_REASON,
    ("project", "profile", "list"): _PROFILE_REASON,
}


# ---------------------------------------------------------------------------
# 正本: argparse の走査
# ---------------------------------------------------------------------------

def _walk(parser, path=()):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for key, sub in action.choices.items():
                yield from _walk(sub, path + (key,))
    yield path, parser


def _canonical(path):
    if path and path[0] in cli.GROUP_ALIASES:
        return (cli.GROUP_ALIASES[path[0]],) + path[1:]
    return path


def _has_name(parser):
    return any(not a.option_strings and a.dest == "name" for a in parser._actions)


def _has_context(parser):
    return any("--context" in a.option_strings for a in parser._actions)


def _scan():
    names, contexts = set(), set()
    for path, parser in _walk(cli._create_parser()):
        if not path:
            continue
        path = _canonical(path)
        is_project_name_scope = len(path) == 1 or path[0] in _PROJECT_NAME_GROUPS
        if is_project_name_scope and _has_name(parser):
            names.add(path)
        if _has_context(parser):
            contexts.add(path)
    return names, contexts | _WRAPPER_ONLY_CONTEXT


NAME_PATHS, CONTEXT_PATHS = _scan()


def _diff_message(where, expected, actual):
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    return (f"{where} が argparse（lib/devbase/cli.py の _create_parser）と一致しない: "
            f"欠けている={missing} 余分={extra}")


def test_scan_finds_both_sets():
    # 走査が parser の構造の変化で空になったら、ほかの比べ方が空のまま通らないよう先に落とす。
    assert NAME_PATHS, "argparse から `[name]` を取るサブコマンドを 1 つも拾えない"
    assert CONTEXT_PATHS - _WRAPPER_ONLY_CONTEXT, "argparse から `--context` を取るサブコマンドを 1 つも拾えない"
    # 入れ子の parser（`project profile up` など）まで降りていること
    assert any(len(p) == 3 for p in NAME_PATHS)
    assert any(len(p) == 3 for p in CONTEXT_PATHS)
    # プロジェクト名でない `name`（plugin / snapshot / env backend use）を含めないこと
    assert not any(p[0] in ("plugin", "snapshot", "env") for p in NAME_PATHS)


# ---------------------------------------------------------------------------
# bin/devbase の 2 つのリスト
# ---------------------------------------------------------------------------

def _wrapper_list(var):
    m = re.search(rf'^{var}=" (.*) "$', WRAPPER.read_text(), re.M)
    assert m, f"bin/devbase に {var}=\" ... \" の行が見つからない"
    return set(m.group(1).split())


def test_wrapper_project_name_subcommands():
    # `project` の直下で `[name]` を取る parser。入れ子の `profile` は 3 番目の引数が
    # up / down / list になるため対象外（compose-profiles.md の決定）。
    expected = {p[1] for p in NAME_PATHS if len(p) == 2 and p[0] == "project"}
    actual = _wrapper_list("_PROJECT_NAME_SUBCOMMANDS")
    assert actual == expected, _diff_message("bin/devbase の _PROJECT_NAME_SUBCOMMANDS", expected, actual)


def test_wrapper_name_resolvable_shortcuts():
    # `cli.SHORTCUTS` のキーとシェル実装の `build`。`login` は parser が `[name]` を持たないが、
    # ラッパーの name 解決だけが名前の指定の手段として含める（cli-argument-resolution.md）。
    expected = set(cli.SHORTCUTS) | {"build"}
    actual = _wrapper_list("_NAME_RESOLVABLE_SHORTCUTS")
    assert actual == expected, _diff_message("bin/devbase の _NAME_RESOLVABLE_SHORTCUTS", expected, actual)


def test_top_level_name_subcommands_are_shortcuts():
    # トップレベルで `[name]` を取る parser は、すべてショートカットとして解決の対象に載る。
    top = {p[0] for p in NAME_PATHS if len(p) == 1}
    missing = top - set(cli.SHORTCUTS)
    assert not missing, f"lib/devbase/cli.py の SHORTCUTS に無いトップレベルの `[name]`: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 補完: プロジェクト名を出す位置
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_root(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "web").mkdir()
    (projects / "api").mkdir()
    return tmp_path


def _bash_complete(words, devbase_root):
    """`devbase <words...> ""` の最後の位置で bash 補完を実行し、候補を返す。"""
    quoted = " ".join(f'"{w}"' for w in ("devbase",) + tuple(words) + ("",))
    script = f"""
set -e
source "{BASH_COMPLETION}"
COMP_WORDS=({quoted})
COMP_CWORD={len(words) + 1}
_devbase_completions
printf '%s\\n' "${{COMPREPLY[@]}}"
"""
    env = {**os.environ, "DEVBASE_ROOT": str(devbase_root)}
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"etc/devbase-completion.bash の実行に失敗: {words} {proc.stderr}"
    return {line for line in proc.stdout.splitlines() if line}


def test_completion_exceptions_are_live():
    # 例外の一覧が argparse から消えたサブコマンドを抱えたまま残らないこと
    stale = set(COMPLETION_NAME_EXCEPTIONS) - NAME_PATHS
    assert not stale, f"COMPLETION_NAME_EXCEPTIONS に `[name]` を取らないものがある: {sorted(stale)}"
    assert all(COMPLETION_NAME_EXCEPTIONS.values())


def test_bash_completion_offers_project_names(fake_root):
    missing = []
    for path in sorted(NAME_PATHS - set(COMPLETION_NAME_EXCEPTIONS)):
        if not {"web", "api"} <= _bash_complete(path, fake_root):
            missing.append(path)
    assert not missing, (f"etc/devbase-completion.bash が `[name]` の位置でプロジェクト名を補完しない: "
                         f"{missing}")


def _zsh_body(lines, start, end, label, indent):
    """lines[start:end] で indent の深さにある `label)` の分岐の本体の範囲を返す。"""
    pat = re.compile(rf"^ {{{indent}}}([A-Za-z0-9_|*-]+)\)\s*$")
    for i in range(start, end):
        m = pat.match(lines[i])
        if m and label in m.group(1).split("|"):
            j = i + 1
            while j < end and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent):
                j += 1
            return i + 1, j
    return None


def _zsh_branch(path):
    lines = ZSH_COMPLETION.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if 'case "$words[2]" in' in ln)
    # 分岐の見出しは `case` の 4 つ下、入れ子の `case` の分岐はさらに 8 つ下に置く書き方
    indent = len(lines[start]) - len(lines[start].lstrip()) + 4
    rng = (start + 1, len(lines))
    for word in path:
        rng = _zsh_body(lines, rng[0], rng[1], word, indent)
        if rng is None:
            return None
        indent += 8
    return "\n".join(lines[rng[0]:rng[1]])


def test_zsh_completion_offers_project_names():
    missing = []
    for path in sorted(NAME_PATHS - set(COMPLETION_NAME_EXCEPTIONS)):
        body = _zsh_branch(path)
        if body is None or "_devbase_project_names" not in body:
            missing.append(path)
    assert not missing, f"etc/_devbase が `[name]` の位置でプロジェクト名（_devbase_project_names）を補完しない: {missing}"


# ---------------------------------------------------------------------------
# 補完: project / container のサブコマンドの一覧
# ---------------------------------------------------------------------------

def _group_subcommands(group):
    # 入れ子の `profile` は親の `profile` として数える
    return {p[1] for p in NAME_PATHS | CONTEXT_PATHS if len(p) >= 2 and p[0] == group}


@pytest.mark.parametrize("group", ["project", "container"])
def test_bash_completion_lists_subcommands(group, fake_root):
    expected = _group_subcommands(group)
    assert expected, f"argparse の {group} に `[name]` / `--context` を取るサブコマンドが無い"
    offered = _bash_complete((group,), fake_root)
    missing = expected - offered
    assert not missing, (f"etc/devbase-completion.bash の {group} のサブコマンドの一覧に無い: "
                         f"{sorted(missing)}")


@pytest.mark.parametrize("group", ["project", "container"])
def test_zsh_completion_lists_subcommands(group):
    expected = _group_subcommands(group)
    m = re.search(rf"^\s*{group}_subcommands=\((.*?)^\s*\)", ZSH_COMPLETION.read_text(), re.M | re.S)
    assert m, f"etc/_devbase に {group}_subcommands=( ... ) が見つからない"
    offered = set(re.findall(r"'([^':]+):", m.group(1)))
    missing = expected - offered
    assert not missing, f"etc/_devbase の {group}_subcommands に無い: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 確定仕様の 2 つの表
# ---------------------------------------------------------------------------

SPEC_NAME_HEADING = "#### `[name]` を受け付けるサブコマンド"
SPEC_CONTEXT_HEADING = "#### `--context` を受け付けるサブコマンド"


def _spec_table(heading):
    """見出しの直後の表を読み、道筋のタプルの集合を返す。

    1 列目は `devbase <グループ...> <sub>` の形の入口、2 列目は `<sub>` に入るサブコマンドを
    バッククォートで並べたもの。
    """
    lines = SPEC.read_text().splitlines()
    assert heading in lines, f"{SPEC.relative_to(REPO_ROOT)} に見出し {heading!r} が無い"
    i = lines.index(heading) + 1
    while i < len(lines) and not lines[i].startswith("|"):
        i += 1
    rows = []
    while i < len(lines) and lines[i].startswith("|"):
        rows.append(lines[i])
        i += 1
    assert len(rows) > 2, f"{SPEC.relative_to(REPO_ROOT)} の {heading!r} の下に表が無い"
    paths = set()
    for row in rows[2:]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        entry = re.findall(r"`([^`]+)`", cells[0])
        assert entry, f"{SPEC.relative_to(REPO_ROOT)} の表の入口が読めない: {row}"
        words = entry[0].split()
        assert words[0] == "devbase" and words[-1] == "<sub>", f"入口の形が違う: {row}"
        prefix = _canonical(tuple(words[1:-1]))
        for sub in re.findall(r"`([^`]+)`", cells[1]):
            paths.add(prefix + (sub,))
    return paths


def test_spec_name_table():
    actual = _spec_table(SPEC_NAME_HEADING)
    assert actual == NAME_PATHS, _diff_message(
        f"{SPEC.relative_to(REPO_ROOT)} の「`[name]` を受け付けるサブコマンド」の表", NAME_PATHS, actual)


def test_spec_context_table():
    actual = _spec_table(SPEC_CONTEXT_HEADING)
    assert actual == CONTEXT_PATHS, _diff_message(
        f"{SPEC.relative_to(REPO_ROOT)} の「`--context` を受け付けるサブコマンド」の表", CONTEXT_PATHS, actual)
