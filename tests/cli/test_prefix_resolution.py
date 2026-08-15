"""sys.argv の prefix 解決 (`devbase env e` → `edit` 等) の後方互換テスト"""

from __future__ import annotations

import sys

from devbase import cli


def test_resolve_prefix_unique_match():
    assert cli._resolve_prefix("ed", ["edit", "export"]) == "edit"
    assert cli._resolve_prefix("ex", ["edit", "export"]) == "export"


def test_resolve_prefix_ambiguous_returns_input():
    # `e` は edit / export の両方にマッチするため、デフォルトでは入力をそのまま返す
    assert cli._resolve_prefix("e", ["edit", "export"]) == "e"


def test_resolve_prefix_falls_back_to_preference_when_ambiguous():
    """ambiguous な prefix に対し preference があれば fallback で解決する"""
    candidates = ["edit", "export"]
    preferences = {"e": "edit"}
    assert cli._resolve_prefix("e", candidates, preferences) == "edit"


def test_resolve_prefix_ignores_preference_when_target_not_in_candidates():
    """preference の指す値が candidates にない場合は無視される"""
    candidates = ["edit", "export"]
    preferences = {"e": "explode"}
    assert cli._resolve_prefix("e", candidates, preferences) == "e"


def test_expand_argv_env_e_resolves_to_edit(monkeypatch):
    """`devbase env e` は引き続き `devbase env edit` に解決される (後方互換)"""
    monkeypatch.setattr(sys, "argv", ["devbase", "env", "e"])
    cli._expand_argv()
    assert sys.argv == ["devbase", "env", "edit"]


def test_expand_argv_env_ed_resolves_to_edit(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["devbase", "env", "ed"])
    cli._expand_argv()
    assert sys.argv == ["devbase", "env", "edit"]


def test_expand_argv_env_ex_resolves_to_export(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["devbase", "env", "ex"])
    cli._expand_argv()
    assert sys.argv == ["devbase", "env", "export"]


def test_expand_argv_env_i_resolves_to_init(monkeypatch):
    """`devbase env i` は `import` 追加後も `init` に解決される (後方互換 / PR #15 round5)"""
    monkeypatch.setattr(sys, "argv", ["devbase", "env", "i"])
    cli._expand_argv()
    assert sys.argv == ["devbase", "env", "init"]


def test_expand_argv_env_im_resolves_to_import(monkeypatch):
    """`devbase env im` は唯一の候補なので `import` に解決される"""
    monkeypatch.setattr(sys, "argv", ["devbase", "env", "im"])
    cli._expand_argv()
    assert sys.argv == ["devbase", "env", "import"]


def test_expand_argv_env_in_resolves_to_init(monkeypatch):
    """`devbase env in` も唯一の候補 (`init`) に解決される"""
    monkeypatch.setattr(sys, "argv", ["devbase", "env", "in"])
    cli._expand_argv()
    assert sys.argv == ["devbase", "env", "init"]


def test_expand_argv_env_k_resolves_to_keygen(monkeypatch):
    """`devbase env k` は唯一の候補 (`keygen`) に解決される (PLAN35)"""
    monkeypatch.setattr(sys, "argv", ["devbase", "env", "k"])
    cli._expand_argv()
    assert sys.argv == ["devbase", "env", "keygen"]


def test_env_subcmd_map_covers_all_registered_subcommands():
    """SUBCMD_MAP['env'] が parser 登録済みサブコマンドを漏れなく含む。

    `keygen` のように parser にだけ追加して SUBCMD_MAP を更新し忘れると、
    prefix 展開の対象から外れて短縮形が invalid choice で落ちる。
    """
    parser = cli._create_parser()
    env_parser = parser._subparsers._group_actions[0].choices["env"]
    registered = set(env_parser._subparsers._group_actions[0].choices)
    assert registered == set(cli.SUBCMD_MAP[("env",)])
