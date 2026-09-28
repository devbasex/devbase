"""export / import が共通で使う passphrase の読み取り (io_common.read_passphrase) のテスト"""

from __future__ import annotations

import io

import pytest

from devbase.env import io_common
from devbase.env.io_export import ExportError
from devbase.env.io_import import ImportError as ImportBundleError

# export と import がそれぞれ渡す例外クラス。どちらでも同じ契約を保つ
ERROR_CLASSES = pytest.mark.parametrize("error_class", [ExportError, ImportBundleError])


@ERROR_CLASSES
def test_read_passphrase_uses_getpass_on_tty(monkeypatch, error_class):
    """tty 入力時は getpass.getpass を使い stdin.readline は呼ばない (エコー抑止)"""
    fake_stdin = io.StringIO("should-not-be-read\n")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    calls = {}

    def fake_getpass(prompt='', stream=None):
        calls['prompt'] = prompt
        calls['stream'] = stream
        return "hunter2"

    monkeypatch.setattr(io_common.getpass, "getpass", fake_getpass)

    pw = io_common.read_passphrase(None, True, error_class)
    assert pw == "hunter2"
    assert calls['prompt'] == "passphrase: "
    assert fake_stdin.read() == "should-not-be-read\n"  # stdin は消費されていない


@ERROR_CLASSES
def test_read_passphrase_falls_back_to_stdin_on_pipe(monkeypatch, capsys, error_class):
    """パイプ (非 tty) 入力時は getpass を使わず stdin.readline で読む"""
    fake_stdin = io.StringIO("hunter2\n")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    def fail_getpass(*args, **kwargs):
        raise AssertionError("getpass.getpass should not be called for piped stdin")

    monkeypatch.setattr(io_common.getpass, "getpass", fail_getpass)

    pw = io_common.read_passphrase(None, True, error_class)
    assert pw == "hunter2"
    assert "passphrase" not in capsys.readouterr().err


@ERROR_CLASSES
def test_read_passphrase_strips_crlf_from_pipe(monkeypatch, error_class):
    """Windows/WSL 由来の CRLF パイプ入力でも末尾 \\r が混入しないこと。

    `\\r` が残ると age 復号は無音で失敗するため、対称的に `rstrip('\\r\\n')` が必要。
    """
    fake_stdin = io.StringIO("hunter2\r\n")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    pw = io_common.read_passphrase(None, True, error_class)
    assert pw == "hunter2"


@ERROR_CLASSES
def test_read_passphrase_tty_eof_raises_given_error(monkeypatch, error_class):
    """tty で getpass が EOFError を投げた場合は呼び出し側の例外クラスに変換される"""
    fake_stdin = io.StringIO("")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdin", fake_stdin)

    def raise_eof(*args, **kwargs):
        raise EOFError()

    monkeypatch.setattr(io_common.getpass, "getpass", raise_eof)

    with pytest.raises(error_class, match="パスフレーズを読み取れません"):
        io_common.read_passphrase(None, True, error_class)
