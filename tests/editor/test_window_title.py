"""window_title.py: VS Code ウィンドウタイトルのコンテナ名固定 (実 docker 不要)。"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional

import pytest

from devbase.editor import window_title


@dataclass
class _Proc:
    """subprocess.run 互換の軽量スタブ (returncode / stdout のみ)。"""
    returncode: int = 0
    stdout: str = ""


@dataclass
class _Runner:
    """``docker exec`` 呼び出しを記録するスタブ runner。

    1 回目 (read) は :attr:`existing` を stdout として返し、2 回目 (write) は
    渡された ``input`` を :attr:`written` へ控える。
    """
    existing: str = ""
    read_rc: int = 0
    write_rc: int = 0
    calls: list = field(default_factory=list)
    written: Optional[str] = None

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if "-i" in args:
            self.written = kwargs.get("input")
            return _Proc(returncode=self.write_rc)
        return _Proc(returncode=self.read_rc, stdout=self.existing)


# ---------------------------------------------------------------------------
# resolve_template
# ---------------------------------------------------------------------------

def test_resolve_template_defaults_when_unset():
    assert window_title.resolve_template(environ={}) == window_title.DEFAULT_TEMPLATE


def test_resolve_template_disabled_by_falsy_values():
    for value in ("0", "false", "OFF", "no", "", "  "):
        assert window_title.resolve_template(
            environ={"DEVBASE_WINDOW_TITLE": value}) is None


def test_resolve_template_custom():
    env = {"DEVBASE_WINDOW_TITLE": "{container}"}
    assert window_title.resolve_template(environ=env) == "{container}"


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def test_render_substitutes_container_only():
    out = window_title.render(window_title.DEFAULT_TEMPLATE, "nyle-dx-dev-1")
    # コンテナ名は埋まり、VS Code のタイトル変数はそのまま残る。
    assert out.startswith("nyle-dx-dev-1")
    assert "${activeEditorShort}" in out
    assert "{container}" not in out


# ---------------------------------------------------------------------------
# merge_settings
# ---------------------------------------------------------------------------

def test_merge_settings_into_empty_file():
    merged = window_title.merge_settings("", "nyle-dx-dev-1")
    assert json.loads(merged) == {"window.title": "nyle-dx-dev-1"}


def test_merge_settings_preserves_existing_keys():
    current = json.dumps({"editor.fontSize": 14})
    merged = window_title.merge_settings(current, "nyle-dx-dev-1")
    assert json.loads(merged) == {"editor.fontSize": 14,
                                  "window.title": "nyle-dx-dev-1"}


def test_merge_settings_noop_when_already_set():
    current = json.dumps({"window.title": "nyle-dx-dev-1"})
    assert window_title.merge_settings(current, "nyle-dx-dev-1") is None


def test_merge_settings_skips_unparsable_file():
    """JSONC としても読めない (編集途中で壊れた) 設定は上書きせず諦める。"""
    assert window_title.merge_settings('{"a": ', "x") is None
    assert window_title.merge_settings("not json at all", "x") is None


def test_merge_settings_skips_non_object_json():
    assert window_title.merge_settings("[1, 2]", "x") is None


# ---------------------------------------------------------------------------
# merge_settings: JSONC (コメント / 末尾カンマ) — VS Code の settings.json は
# JSONC なので、これらを含む「正常な」設定でも機能が止まってはいけない。
# ---------------------------------------------------------------------------

def test_merge_settings_replaces_title_in_jsonc_keeping_comments():
    """コメント付き設定は原文を保ったまま値だけ差し替える (コメントを失わない)。"""
    current = (
        "{\n"
        "\t// 自分で書いたメモ\n"
        '\t"window.title": "old",\n'
        '\t"editor.fontSize": 14, // 末尾カンマ\n'
        "}\n"
    )
    merged = window_title.merge_settings(current, "nyle-dx-dev-1")
    assert "// 自分で書いたメモ" in merged
    assert "// 末尾カンマ" in merged
    assert json.loads(window_title.strip_jsonc(merged)) == {
        "window.title": "nyle-dx-dev-1", "editor.fontSize": 14}


def test_merge_settings_inserts_title_into_jsonc_without_key():
    current = "{\n\t/* 既存 */\n\t\"editor.fontSize\": 14,\n}\n"
    merged = window_title.merge_settings(current, "nyle-dx-dev-1")
    assert "/* 既存 */" in merged
    assert json.loads(window_title.strip_jsonc(merged)) == {
        "window.title": "nyle-dx-dev-1", "editor.fontSize": 14}


def test_merge_settings_inserts_title_into_comment_only_jsonc():
    merged = window_title.merge_settings("{ // メモだけ\n}", "nyle-dx-dev-1")
    assert "// メモだけ" in merged
    assert json.loads(window_title.strip_jsonc(merged)) == {
        "window.title": "nyle-dx-dev-1"}


def test_merge_settings_noop_when_already_set_in_jsonc():
    """コメント付きでも同値なら書かない (mtime を無駄に変えない)。"""
    current = '{\n\t// メモ\n\t"window.title": "nyle-dx-dev-1",\n}\n'
    assert window_title.merge_settings(current, "nyle-dx-dev-1") is None


def test_strip_jsonc_keeps_offsets_and_ignores_string_contents():
    text = '{"url": "http://x/*y*/", /* c */ "n": 1,}'
    stripped = window_title.strip_jsonc(text)
    assert len(stripped) == len(text)          # 位置が原文と一対一
    assert json.loads(stripped) == {"url": "http://x/*y*/", "n": 1}


# ---------------------------------------------------------------------------
# apply_to_container
# ---------------------------------------------------------------------------

def test_apply_writes_merged_settings():
    runner = _Runner(existing=json.dumps({"editor.fontSize": 14}))
    ok = window_title.apply_to_container("nyle-dx-dev-1", environ={}, runner=runner)
    assert ok is True
    written = json.loads(runner.written)
    assert written["editor.fontSize"] == 14
    assert written["window.title"].startswith("nyle-dx-dev-1")
    # read → write の 2 回、いずれも対象コンテナへの docker exec。
    assert len(runner.calls) == 2
    for args, _ in runner.calls:
        assert args[:2] == ["docker", "exec"]
        assert "nyle-dx-dev-1" in args


def test_apply_creates_settings_when_file_missing():
    """VS Code が一度も繋がっていないコンテナ (settings.json 不在) でも書ける。"""
    runner = _Runner(existing="")
    ok = window_title.apply_to_container("nyle-dx-dev-1", environ={}, runner=runner)
    assert ok is True
    assert json.loads(runner.written)["window.title"].startswith("nyle-dx-dev-1")
    # 読み取りは cat の rc=1 (ファイル不在) を exec 失敗と取り違えないよう握り潰す。
    read_args = runner.calls[0][0]
    assert read_args[-1].endswith("|| true")
    # 書き込みはディレクトリごと作る。
    assert "mkdir -p" in runner.calls[1][0][-1]


def test_apply_skips_when_disabled():
    runner = _Runner()
    ok = window_title.apply_to_container(
        "nyle-dx-dev-1", environ={"DEVBASE_WINDOW_TITLE": "0"}, runner=runner)
    assert ok is False
    assert runner.calls == []


def test_apply_skips_write_when_unchanged():
    title = window_title.render(window_title.DEFAULT_TEMPLATE, "nyle-dx-dev-1")
    runner = _Runner(existing=json.dumps({"window.title": title}))
    ok = window_title.apply_to_container("nyle-dx-dev-1", environ={}, runner=runner)
    assert ok is False
    assert runner.written is None
    assert len(runner.calls) == 1  # read のみ


def test_apply_returns_false_when_exec_fails():
    runner = _Runner(read_rc=1)
    ok = window_title.apply_to_container("nyle-dx-dev-1", environ={}, runner=runner)
    assert ok is False
    assert runner.written is None


def test_apply_returns_false_when_write_fails():
    runner = _Runner(write_rc=1)
    ok = window_title.apply_to_container("nyle-dx-dev-1", environ={}, runner=runner)
    assert ok is False


def test_apply_survives_runner_exception():
    def boom(args, **kwargs):
        raise OSError("docker not found")

    assert window_title.apply_to_container(
        "nyle-dx-dev-1", environ={}, runner=boom) is False


# ---------------------------------------------------------------------------
# 書き込みの原子性 (一時ファイル + mv)
# ---------------------------------------------------------------------------

def _run_write_command(home, payload):
    """コンテナ内で走るのと同じ書き込みコマンドをローカル sh で実行する。"""
    return subprocess.run(
        ["sh", "-c", window_title._write_command()],
        input=payload, text=True, capture_output=True, check=False,
        env={"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )


def test_apply_write_command_replaces_atomically():
    runner = _Runner(existing=json.dumps({"editor.fontSize": 14}))
    window_title.apply_to_container("nyle-dx-dev-1", environ={}, runner=runner)
    command = runner.calls[1][0][-1]
    # 直接 truncate せず、同一ディレクトリの一時ファイルへ書いてから mv する。
    assert 'cat > "$HOME/.vscode-server/data/Machine/.settings.json.devbase.$$"' in command
    assert "mv -f" in command
    assert 'cat > "$HOME/.vscode-server/data/Machine/settings.json"' not in command
    # 失敗時は一時ファイルを片付けて非 0 で終わる。
    assert "rm -f" in command and "exit 1" in command


def test_write_command_creates_file_without_leftover_tmp(tmp_path):
    result = _run_write_command(tmp_path, '{\n\t"window.title": "x"\n}\n')
    assert result.returncode == 0, result.stderr
    machine = tmp_path / ".vscode-server" / "data" / "Machine"
    assert json.loads((machine / "settings.json").read_text()) == {"window.title": "x"}
    assert [p.name for p in machine.iterdir()] == ["settings.json"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX シェルが前提")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root はディレクトリのパーミッションを無視するため")
def test_write_command_keeps_existing_file_when_write_fails(tmp_path):
    """書き込みに失敗しても既存の settings.json は元のまま残る。"""
    machine = tmp_path / ".vscode-server" / "data" / "Machine"
    machine.mkdir(parents=True)
    settings = machine / "settings.json"
    settings.write_text('{"editor.fontSize": 14}')
    machine.chmod(0o500)  # 新規ファイルを作れない = 一時ファイルの作成が失敗する
    try:
        result = _run_write_command(tmp_path, '{"window.title": "x"}\n')
        assert result.returncode != 0
        assert settings.read_text() == '{"editor.fontSize": 14}'
    finally:
        machine.chmod(0o700)
