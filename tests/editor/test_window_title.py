"""window_title.py: VS Code ウィンドウタイトルのコンテナ名固定 (実 docker 不要)。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

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
    """壊れた/コメント付き設定は上書きせず諦める (壊すより何もしない)。"""
    assert window_title.merge_settings("{ // comment\n}", "x") is None


def test_merge_settings_skips_non_object_json():
    assert window_title.merge_settings("[1, 2]", "x") is None


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
