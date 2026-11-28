"""`devbase init` の最後の userns の手順 (#533 の受け入れ条件 5・17) のテスト

実の sudo・sysctl・docker は起動しない。``userns.apply`` を差し替えるか、tmp の paths と
:class:`FakeHost` を既定へ置いて通す。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from devbase.commands import init as init_mod
from devbase.utils import userns
from tests.utils.userns_fake import FakeHost, make_paths


@pytest.fixture
def devbase_root(tmp_path, monkeypatch):
    root = tmp_path / 'devbase'
    (root / 'bin').mkdir(parents=True)
    (root / 'etc').mkdir()
    (root / 'etc' / 'devbase-completion.bash').write_text('# completion\n')
    monkeypatch.setenv('DEVBASE_ROOT', str(root))
    monkeypatch.setenv('SHELL', '/bin/bash')
    return root


def _rc_text() -> str:
    return (Path.home() / '.bashrc').read_text()


def _fake_apply(monkeypatch, result):
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        if isinstance(result, BaseException) or isinstance(result, type):
            raise result
        return result
    monkeypatch.setattr(userns, 'apply', fake)
    return calls


def test_apply_called_once(devbase_root, monkeypatch):
    monkeypatch.delenv('DEVBASE_SKIP_USERNS')
    calls = _fake_apply(monkeypatch, userns.Report(ok=True))
    assert init_mod.cmd_init(devbase_root) == 0
    assert len(calls) == 1


@pytest.mark.parametrize('result', [userns.Report(ok=False), RuntimeError('boom'), KeyboardInterrupt])
def test_ac5_init_succeeds_and_keeps_rc_when_apply_fails(devbase_root, monkeypatch, result):
    monkeypatch.delenv('DEVBASE_SKIP_USERNS')
    _fake_apply(monkeypatch, result)
    assert init_mod.cmd_init(devbase_root) == 0
    rc = _rc_text()
    assert 'export DEVBASE_ROOT=' in rc
    assert 'devbase-completion.bash' in rc


def test_ac5_sudo_v_interrupt_through_real_apply(devbase_root, monkeypatch, tmp_path):
    """``sudo -v`` で Ctrl-C を受けても、apply は ok 偽で返り、init は 0 で終わる"""
    monkeypatch.delenv('DEVBASE_SKIP_USERNS')
    paths = make_paths(tmp_path / 'host', value=1, tty=True)
    host = FakeHost(paths, sudo_v=KeyboardInterrupt)
    monkeypatch.setattr(userns, 'DEFAULT_PATHS', paths)
    monkeypatch.setattr(userns, 'DEFAULT_RUNNER', host)
    seen = []
    real_apply = userns.apply

    def spy(*args, **kwargs):
        report = real_apply(*args, **kwargs)
        seen.append(report)
        return report
    monkeypatch.setattr(userns, 'apply', spy)
    assert init_mod.cmd_init(devbase_root) == 0
    assert seen and not seen[0].ok
    assert not paths.devbase_file.exists()
    assert 'devbase-completion.bash' in _rc_text()


def test_ac17_skip_env_does_not_call_apply(devbase_root, monkeypatch, caplog):
    monkeypatch.setenv('DEVBASE_SKIP_USERNS', '1')
    calls = _fake_apply(monkeypatch, userns.Report(ok=True))
    with caplog.at_level(logging.INFO, logger='devbase'):
        assert init_mod.cmd_init(devbase_root) == 0
    assert calls == []
    skipped = [r for r in caplog.records if 'DEVBASE_SKIP_USERNS' in r.getMessage()]
    assert len(skipped) == 1


def test_ac17_empty_skip_env_still_calls_apply(devbase_root, monkeypatch):
    monkeypatch.setenv('DEVBASE_SKIP_USERNS', '')
    calls = _fake_apply(monkeypatch, userns.Report(ok=True))
    assert init_mod.cmd_init(devbase_root) == 0
    assert len(calls) == 1
