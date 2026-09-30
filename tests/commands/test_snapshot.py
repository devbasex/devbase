"""復元の確認分岐を、疑似ボリュームの最終状態で現状固定する。"""

from types import SimpleNamespace

import pytest

from devbase.commands import snapshot


@pytest.mark.parametrize(
    "is_tty,answer,expected_content",
    [
        (True, "", "original"),
        (True, "n", "original"),
        (True, "y", "saved at point 2"),
        (True, "YES", "saved at point 2"),
        (False, None, "saved at point 2"),
    ],
)
def test_restore_confirmation(tmp_path, monkeypatch, is_tty, answer, expected_content):
    volume = {"content": "original"}
    saved = {("daily", 2): "saved at point 2"}

    class FakeSnapshotManager:
        def __init__(self, devbase_root, group=None):
            pass

        def restore(self, name, point=None):
            volume["content"] = saved[name, point]

    def respond(prompt):
        if not is_tty:
            pytest.fail("Non-TTY restore must not prompt for input")
        return answer

    monkeypatch.setattr(snapshot, "SnapshotManager", FakeSnapshotManager)
    monkeypatch.setattr(snapshot.sys, "stdin", SimpleNamespace(isatty=lambda: is_tty))
    monkeypatch.setattr("builtins.input", respond)
    args = SimpleNamespace(subcommand="restore", name="daily", point=2)

    assert snapshot.cmd_snapshot(tmp_path, args) == 0
    assert volume["content"] == expected_content


# ---------------------------------------------------------------------------
# snapshot create の対象のグループ (#315 決定 3・I10)
# ---------------------------------------------------------------------------

@pytest.fixture
def recorded(monkeypatch):
    seen = {}

    class FakeSnapshotManager:
        def __init__(self, devbase_root, group=None):
            seen['group'] = group

        def create(self, name=None, full=False):
            seen['created'] = True
            return 'snap'

    monkeypatch.setattr(snapshot, "SnapshotManager", FakeSnapshotManager)
    return seen


def _create(root, **kw):
    return snapshot.cmd_snapshot(root, SimpleNamespace(subcommand="create", name=None,
                                                       full=False, **kw))


def test_create_outside_a_project_needs_the_group(tmp_path, monkeypatch, recorded, caplog):
    monkeypatch.setenv("PWD", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    assert _create(tmp_path, group=None) == 2
    assert recorded == {}
    assert "--group" in caplog.text


def test_create_with_the_group_option_uses_it(tmp_path, monkeypatch, recorded):
    monkeypatch.setenv("PWD", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    assert _create(tmp_path, group="personal") == 0
    assert recorded == {"group": "personal", "created": True}


def test_create_refuses_the_reserved_default(tmp_path, monkeypatch, recorded):
    """旧既定のボリュームの系列に新しい世代を作らない"""
    monkeypatch.setenv("PWD", str(tmp_path))

    assert _create(tmp_path, group="default") == 2
    assert recorded == {}


def test_create_inside_a_project_uses_its_declaration(tmp_path, monkeypatch, recorded):
    project = tmp_path / "projects" / "web"
    project.mkdir(parents=True)
    (project / "env").write_text("DEVBASE_ACCOUNT_GROUP=initech\n")
    monkeypatch.setenv("PWD", str(project))
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "globex")      # プロセスの値は見ない

    assert _create(tmp_path, group=None) == 0
    assert recorded["group"] == "initech"


def test_create_inside_an_undeclared_project_stops(tmp_path, monkeypatch, recorded, caplog):
    project = tmp_path / "projects" / "web"
    project.mkdir(parents=True)
    monkeypatch.setenv("PWD", str(project))

    assert _create(tmp_path, group=None) == 1
    assert recorded == {}
    assert "projects/web/env" in caplog.text


@pytest.mark.parametrize("argv", [
    ["env", "export", "--group", "acme"],
    ["env", "import", "bundle.dbenv", "--group", "acme"],
    ["env", "backend", "test", "--group", "acme"],
    ["env", "backend", "migrate", "--to", "openbao", "--group", "acme"],
    ["snapshot", "create", "--group", "acme"],
])
def test_parsers_accept_the_group_option(argv):
    from devbase import cli

    assert cli._create_parser().parse_args(argv).group == "acme"
