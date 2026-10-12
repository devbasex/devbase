"""除外の一覧の組み立て (#368)

既定の除外・``backups/exclude.yml`` の読み込み・パターンの検査・tar の引数を縛る。
Docker も ``SnapshotManager`` も使わない。
"""

from __future__ import annotations

import logging
import shlex

import pytest

from devbase.snapshot.exclude import (
    DEFAULT_EXCLUDES,
    USER_CONFIG,
    normalize,
    resolve_excludes,
    tar_exclude_args,
)


def _write(backups, text: str) -> None:
    backups.mkdir(parents=True, exist_ok=True)
    (backups / USER_CONFIG).write_text(text, encoding="utf-8")


def _warnings(caplog) -> list:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


# ---------------------------------------------------------------------------
# 既定の除外
# ---------------------------------------------------------------------------

def test_default_excludes_are_the_listed_paths():
    assert set(DEFAULT_EXCLUDES) == {
        ".gemini/tmp/*/logs.json", ".gemini/tmp/*/logs",
        ".codex/logs_2.sqlite", ".codex/logs_2.sqlite-wal", ".codex/logs_2.sqlite-shm",
        ".codex/.tmp", ".codex/cache",
        ".claude/plugins/cache", ".claude/plugins/marketplaces",
        ".serena/logs",
    }


@pytest.mark.parametrize("kept", [
    ".claude/projects", ".codex/sessions", ".codex/thread_history_1.sqlite",
    ".codex/state_5.sqlite", ".gemini/tmp", ".gemini/tmp/*/chats", ".claude/plugins",
    ".claude/plugins/installed_plugins.json", ".claude/plugins/known_marketplaces.json",
    ".claude/plugins/data", ".serena/language_servers", ".serena",
])
def test_default_excludes_never_cover_records_to_keep(kept):
    """I7: 会話の記録・プラグインの状態・language_servers と、その親を外さない。"""
    for pattern in DEFAULT_EXCLUDES:
        assert pattern != kept
        assert not kept.startswith(pattern + "/"), (pattern, kept)


def test_default_excludes_are_already_normalized():
    assert all(normalize(p) == p for p in DEFAULT_EXCLUDES)


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("./a//b/", "a/b"),
    ("  .cache/pip  ", ".cache/pip"),
    ("work/*/node_modules", "work/*/node_modules"),
])
def test_normalize_accepts_relative_paths(value, expected):
    assert normalize(value) == expected


@pytest.mark.parametrize("value", [
    None, 3, ["a"], "", "   ", "/etc", "a/../b", "..", "a?", "a[b]", "a\\b", "./", ".//.",
])
def test_normalize_rejects_unusable_values(value):
    with pytest.raises(ValueError):
        normalize(value)


# ---------------------------------------------------------------------------
# resolve_excludes
# ---------------------------------------------------------------------------

def test_missing_config_uses_defaults_without_warning(tmp_path, caplog):
    """受け入れ条件 12"""
    with caplog.at_level(logging.WARNING, logger="devbase"):
        excludes, readable = resolve_excludes(tmp_path)
    assert excludes == tuple(sorted(DEFAULT_EXCLUDES))
    assert readable is True
    assert _warnings(caplog) == []


@pytest.mark.parametrize("text", ["", "exclude:\n", "exclude: []\n", "other: 1\n"])
def test_empty_config_uses_defaults_without_warning(tmp_path, caplog, text):
    """受け入れ条件 12: ``exclude`` が無い・null・空のリスト"""
    _write(tmp_path, text)
    with caplog.at_level(logging.WARNING, logger="devbase"):
        excludes, readable = resolve_excludes(tmp_path)
    assert excludes == tuple(sorted(DEFAULT_EXCLUDES))
    assert readable is True
    assert _warnings(caplog) == []


def test_user_excludes_are_added_to_defaults(tmp_path, caplog):
    """受け入れ条件 5・I6: 足した値と既定の除外の両方が効く。並べ替え・重複なし。"""
    _write(tmp_path, "exclude:\n  - ./.cache//pip/\n  - .codex/cache\n")
    with caplog.at_level(logging.WARNING, logger="devbase"):
        excludes, readable = resolve_excludes(tmp_path)
    assert readable is True
    assert ".cache/pip" in excludes
    assert set(DEFAULT_EXCLUDES) <= set(excludes)
    assert list(excludes) == sorted(set(excludes))
    assert len(excludes) == len(DEFAULT_EXCLUDES) + 1
    assert _warnings(caplog) == []


def test_unusable_values_are_dropped_one_warning_each(tmp_path, caplog):
    """受け入れ条件 11: 値ごとに WARNING 1 行。残りの値は効く。"""
    _write(tmp_path, "exclude:\n  - /abs\n  - a/../b\n  - ''\n  - null\n  - 3\n"
                     "  - 'a?b'\n  - keep/me\n")
    with caplog.at_level(logging.WARNING, logger="devbase"):
        excludes, readable = resolve_excludes(tmp_path)
    assert readable is True
    assert "keep/me" in excludes
    assert len(excludes) == len(DEFAULT_EXCLUDES) + 1
    warnings = _warnings(caplog)
    assert len(warnings) == 6
    assert all("を採りません" in w for w in warnings)


@pytest.mark.parametrize("text", ["exclude: [a\n", "- a\n", "exclude: a\n"])
def test_unreadable_config_warns_once_and_reports_unreadable(tmp_path, caplog, text):
    """受け入れ条件 11 (読めない設定): WARNING 1 行、既定の除外だけ、読めたかは偽。"""
    _write(tmp_path, text)
    with caplog.at_level(logging.WARNING, logger="devbase"):
        excludes, readable = resolve_excludes(tmp_path)
    assert excludes == tuple(sorted(DEFAULT_EXCLUDES))
    assert readable is False
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "を読めないため" in warnings[0]


# ---------------------------------------------------------------------------
# tar_exclude_args
# ---------------------------------------------------------------------------

def test_tar_args_anchor_each_pattern_to_each_member():
    """I5: メンバー名を頭に付け、頭から・1 要素の中だけに当てる。``*`` は引用する。"""
    args = shlex.split(tar_exclude_args((".codex/cache", ".gemini/tmp/*/logs"), ["ai", "group"]))
    assert args[:2] == ["--anchored", "--no-wildcards-match-slash"]
    assert args[2:] == [
        "--exclude=ai/.codex/cache", "--exclude=ai/.gemini/tmp/*/logs",
        "--exclude=group/.codex/cache", "--exclude=group/.gemini/tmp/*/logs",
    ]
    assert "'--exclude=ai/.gemini/tmp/*/logs'" in tar_exclude_args(
        (".gemini/tmp/*/logs",), ["ai"])


def test_tar_args_for_legacy_layout_start_with_dot():
    """I5 (旧レイアウト): メンバーが ``.`` なら頭は ``./``。"""
    args = shlex.split(tar_exclude_args((".codex/cache",), ["."]))
    assert args[-1] == "--exclude=./.codex/cache"


def test_tar_args_are_empty_without_excludes():
    assert tar_exclude_args((), ["ai"]) == ""
