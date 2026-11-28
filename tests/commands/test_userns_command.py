"""`devbase userns status / apply / revert` の出力と終了コード (#533) のテスト

既定の paths と runner を tmp と :class:`FakeHost` へ置き換えて通す。
"""

from __future__ import annotations

import argparse

import pytest

from devbase.commands.userns import cmd_userns
from devbase.utils import userns
from tests.utils.userns_fake import FakeHost, make_paths, write_conf


@pytest.fixture
def host(tmp_path, monkeypatch):
    def make(**kwargs):
        paths = make_paths(tmp_path / 'host', **{k: kwargs.pop(k) for k in ('value', 'tty', 'container')
                                                 if k in kwargs})
        fake = FakeHost(paths, **kwargs)
        monkeypatch.setattr(userns, 'DEFAULT_PATHS', paths)
        monkeypatch.setattr(userns, 'DEFAULT_RUNNER', fake)
        return fake
    return make


def _run(sub, root):
    return cmd_userns(root, argparse.Namespace(subcommand=sub))


def test_ac14_status_prints_three_lines_and_returns_0(host, tmp_path, capsys):
    fake = host(value=1, image=True)
    assert _run('status', tmp_path) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith('ホスト:')
    assert out[1].startswith('devbase のファイル:')
    assert out[2].startswith('コンテナが動くカーネル:')
    assert fake.non_docker() == []


def test_apply_returns_0_when_lifted(host, tmp_path, monkeypatch):
    monkeypatch.setattr(userns, '_euid', lambda: 0)
    fake = host(value=1)
    assert _run('apply', tmp_path) == 0
    assert fake.paths.devbase_file.exists()


def test_apply_returns_1_without_privilege(host, tmp_path):
    host(value=1)
    assert _run('apply', tmp_path) == 1


def test_ac12_revert_returns_0(host, tmp_path, monkeypatch):
    monkeypatch.setattr(userns, '_euid', lambda: 0)
    fake = host(value=0)
    write_conf(fake.paths, '60-devbase-userns.conf', 0, marker=True)
    assert _run('revert', tmp_path) == 0
    assert not fake.paths.devbase_file.exists()


def test_ac13_revert_unmarked_returns_1(host, tmp_path):
    fake = host(value=0)
    fake.paths.devbase_file.write_text('kernel.apparmor_restrict_unprivileged_userns = 0\n')
    assert _run('revert', tmp_path) == 1
    assert fake.paths.devbase_file.exists()


def test_ac13_revert_without_file_returns_0(host, tmp_path):
    fake = host(value=0)
    assert _run('revert', tmp_path) == 0
    assert fake.calls == []


def test_no_subcommand_returns_2(tmp_path):
    assert _run(None, tmp_path) == 2
