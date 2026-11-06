"""``devbase project new`` の入口のテスト (#514 の受け入れ条件 22)。"""

from __future__ import annotations

import sys

import pytest

from devbase import cli

from .conftest import python_args, stdout_field


@pytest.mark.parametrize("target", ["owner/repo", "myapp"])
def test_wrapper_does_not_resolve_the_argument_as_a_project(exec_wrapper, target):
    """同じ名前のプロジェクトがあっても、引数をプロジェクト名として解決しない。"""
    exec_wrapper.project("myapp")
    (exec_wrapper.root / "projects" / "owner").mkdir()
    r = exec_wrapper(["project", "new", target])
    assert "/projects/" not in stdout_field(r, "PWD:"), r.stdout
    assert python_args(r) == f"project new {target}", r.stdout


def test_new_is_in_the_prefix_map():
    assert "new" in cli.SUBCMD_MAP[("project",)]
    assert "new" in cli.SUBCMD_MAP[("plugin", "pl")]


def test_prefix_resolves_to_new(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["devbase", "project", "n", "owner/repo"])
    cli._expand_argv()
    assert sys.argv == ["devbase", "project", "new", "owner/repo"]


@pytest.mark.parametrize("argv", [
    ["project", "new", "owner/repo"],
    ["plugin", "new", "owner/repo"],
    ["pl", "new", "owner/repo"],
    ["plugin", "repo", "new", "owner/repo"],
    ["pl", "repo", "new", "owner/repo"],
])
def test_creation_flows_leave_gh_to_the_users_login(argv):
    """機密の GH_TOKEN が載ると、gh は利用者のログインではなくそのトークンで動く (決定 11)。"""
    args = cli._create_parser().parse_args(argv)
    assert cli._skip_secret_injection(args.command, cli._injection_subcommand(args))


@pytest.mark.parametrize("argv", [
    ["plugin", "list"],
    ["plugin", "repo", "add", "owner/repo"],
    ["pl", "repo", "refresh", "name"],
])
def test_other_plugin_commands_still_inject(argv):
    args = cli._create_parser().parse_args(argv)
    assert not cli._skip_secret_injection(args.command, cli._injection_subcommand(args))


def test_parser_arguments():
    args = cli._create_parser().parse_args(
        ["project", "new", "owner/repo", "--plugin", "p", "--group", "g",
         "--name", "n", "--yes"])
    assert (args.repo, args.plugin, args.group, args.name, args.yes) == (
        "owner/repo", "p", "g", "n", True)


def test_dispatch_does_not_reach_the_lifecycle(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setenv("DEVBASE_ROOT", str(tmp_path))

    def _fake(root, args):
        seen["root"] = root
        seen["repo"] = args.repo
        return 0

    from devbase.commands import container, project
    monkeypatch.setattr(project, "cmd_project_new", _fake)
    monkeypatch.setattr(container, "cmd_project",
                        lambda *_a: pytest.fail("lifecycle へ流れた"))
    args = cli._create_parser().parse_args(["project", "new", "owner/repo"])
    assert cli._dispatch("project", args) == 0
    assert seen == {"root": tmp_path, "repo": "owner/repo"}
