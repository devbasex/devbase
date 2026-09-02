"""AI 設定の永続化 (共通 / アカウントグループの 2 層) — PLAN39 Task 3・4

``containers/base/entrypoint.sh`` を ``DEVBASE_ENTRYPOINT_LIB_ONLY=1`` で source し、
一時ディレクトリを ``/persistent/ai`` / ``/persistent/group`` / ``$HOME`` に見立てて
関数を直接呼ぶ。Docker には依存しない。

固定する契約:

- 分類 A (共通) は ``/persistent/ai``、分類 B (グループ) は ``/persistent/group`` を指す
- ``~/.claude`` の既定はグループ側で、共通資産だけがその配下から共通側へ張られる
- 入れ子パスでも symlink が壊れない (親ディレクトリの作成 / ファイルとディレクトリの判別)
- 初回シードは ``default`` グループだけで、共通資産はコピーせず、2 回目は何もしない
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "containers" / "base" / "entrypoint.sh"


def run_entrypoint_fn(script: str, cwd: Path, env: dict | None = None):
    """entrypoint.sh の関数だけを読み込んで ``script`` を実行する。"""
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("DEVBASE_", "GIT_"))}
    full = f'set -e\nDEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n{script}\n'
    return subprocess.run(
        ["bash", "-c", full], cwd=cwd, env={**base, **(env or {})},
        capture_output=True, text=True,
    )


@pytest.fixture
def roots(tmp_path: Path):
    """home / persistent(ai) / persistent(group) の 3 つ組を作る。"""
    home = tmp_path / "home"
    ai = tmp_path / "persistent" / "ai"
    group = tmp_path / "persistent" / "group"
    home.mkdir(parents=True)
    return home, ai, group


def setup(roots, group_name: str = "default", cwd: Path | None = None):
    home, ai, grp = roots
    result = run_entrypoint_fn(
        f'devbase_setup_ai_settings "{home}" "{ai}" "{grp}" "{group_name}"',
        cwd or home,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result


# ---------------------------------------------------------------------------
# 2 系統の振り分け (AC3 / AC4)
# ---------------------------------------------------------------------------

def test_shared_entries_point_at_the_shared_volume(roots):
    home, ai, _ = roots
    setup(roots)

    for entry in (".codex", ".serena", ".ssh", ".kiro", "share"):
        link = home / entry
        assert link.is_symlink(), f"{entry} が symlink ではない"
        assert link.resolve() == (ai / entry).resolve()


def test_group_entries_point_at_the_group_volume(roots):
    home, _, grp = roots
    setup(roots, "kkg")

    for entry in (".claude.json", ".claude", ".gemini"):
        link = home / entry
        assert link.is_symlink(), f"{entry} が symlink ではない"
        assert link.resolve() == (grp / entry).resolve()


def test_claude_defaults_to_the_group_volume(roots):
    """``~/.claude`` 配下の既定はグループ側。

    Claude Code は ``projects`` / ``sessions`` / ``tasks`` のようなディレクトリを
    随時作る。列挙したものだけを永続化すると列挙漏れが黙って揮発するため、
    既定をグループ側に倒して共通にしたいものだけを名指しする。
    """
    home, _, grp = roots
    setup(roots, "kkg")

    (home / ".claude" / "projects").mkdir(parents=True)
    assert (grp / ".claude" / "projects").is_dir()


def test_shared_assets_under_claude_point_at_the_shared_volume(roots):
    """AC4: どのグループから見ても共通資産は同一実体を指す。"""
    home, ai, _ = roots
    setup(roots, "kkg")

    for entry in ("plugins", "skills", "commands", "CLAUDE.md", "settings.json"):
        path = home / ".claude" / entry
        assert path.resolve() == (ai / ".claude" / entry).resolve(), entry


def test_two_groups_share_assets_but_not_credentials(roots, tmp_path):
    """AC3 / AC4 をまとめて: 共通資産は同一、グループ別データは互いに見えない。"""
    home_a, ai, group_a = roots
    home_b = tmp_path / "home-b"
    home_b.mkdir()
    group_b = tmp_path / "persistent" / "kkg"

    setup((home_a, ai, group_a), "default")
    setup((home_b, ai, group_b), "kkg")

    # 共通資産は同一実体
    assert (home_a / ".claude" / "plugins").resolve() == \
        (home_b / ".claude" / "plugins").resolve()

    # グループ別データは互いに到達できない
    (home_a / ".claude" / ".credentials.json").write_text("default-secret")
    assert not (home_b / ".claude" / ".credentials.json").exists()


# ---------------------------------------------------------------------------
# 入れ子パス (AC6 / 前提 5)
# ---------------------------------------------------------------------------

def test_nested_file_entries_are_created_as_files(roots):
    """``CLAUDE.md`` / ``settings.json`` はファイル。ディレクトリにすると書けない。"""
    home, ai, _ = roots
    setup(roots)

    for entry in ("CLAUDE.md", "settings.json"):
        target = ai / ".claude" / entry
        assert target.is_file(), f"{entry} がファイルとして作られていない"
        assert not target.is_dir()


def test_nested_directory_entries_are_created_as_directories(roots):
    home, ai, _ = roots
    setup(roots)

    for entry in ("plugins", "skills", "commands"):
        assert (ai / ".claude" / entry).is_dir(), entry


def test_nested_links_are_not_broken(roots):
    """親ディレクトリが無くても壊れた symlink を残さない (前提 5)。"""
    home, _, _ = roots
    setup(roots)

    for entry in ("plugins", "CLAUDE.md"):
        link = home / ".claude" / entry
        assert link.is_symlink()
        assert link.exists(), f"{entry} が壊れた symlink になっている"


def test_jsonl_entries_are_not_turned_into_directories(roots):
    """``history.jsonl`` は ``*.json`` にマッチしないためディレクトリ化していた。"""
    home, _, grp = roots
    setup(roots)

    path = grp / ".claude" / "history.jsonl"
    # 実体は Claude Code が作るので存在しないのが正常。存在するならファイルであること。
    assert not path.is_dir()

    # entrypoint がプレースホルダを作る経路でもディレクトリにしない
    result = run_entrypoint_fn(
        f'devbase_ensure_entry "{grp}/.claude/history.jsonl"', home)
    assert result.returncode == 0, result.stderr
    assert path.is_file(), "history.jsonl がファイルとして作られていない"


def test_credentials_json_is_reachable(roots):
    """AC6: ``~/.claude/.credentials.json`` の親が無くても書き込める。"""
    home, _, grp = roots
    setup(roots)

    path = home / ".claude" / ".credentials.json"
    path.write_text('{"ok": true}')
    assert (grp / ".claude" / ".credentials.json").read_text() == '{"ok": true}'


# ---------------------------------------------------------------------------
# 既存状態からの張り替え
# ---------------------------------------------------------------------------

def test_existing_wrong_symlink_is_replaced(roots):
    """PLAN39 以前の ``~/.claude -> /persistent/ai/.claude`` を張り替える。"""
    home, ai, grp = roots
    (ai / ".claude").mkdir(parents=True)
    (home / ".claude").symlink_to(ai / ".claude")

    setup(roots, "kkg")

    assert (home / ".claude").resolve() == (grp / ".claude").resolve()


def test_existing_real_directory_in_home_is_replaced(roots):
    home, _, grp = roots
    (home / ".gemini").mkdir()
    (home / ".gemini" / "leftover").write_text("x")

    setup(roots)

    assert (home / ".gemini").is_symlink()
    assert (home / ".gemini").resolve() == (grp / ".gemini").resolve()


def test_broken_symlink_in_home_is_replaced(roots):
    home, _, grp = roots
    (home / ".codex").symlink_to(home / "does-not-exist")

    setup(roots)

    assert (home / ".codex").exists()


def test_setup_is_idempotent(roots):
    home, ai, grp = roots
    setup(roots)
    (home / ".claude" / "projects").mkdir(parents=True)
    (home / ".claude" / "projects" / "keep.txt").write_text("keep")

    setup(roots)

    assert (home / ".claude" / "projects" / "keep.txt").read_text() == "keep"
    assert (home / ".claude" / "plugins").resolve() == (ai / ".claude" / "plugins").resolve()


# ---------------------------------------------------------------------------
# 初回シード (AC8)
# ---------------------------------------------------------------------------

def _seed_source(ai: Path) -> None:
    """現行 ``/persistent/ai`` に実体がある分類 B のデータを用意する。"""
    (ai / ".claude").mkdir(parents=True)
    (ai / ".claude" / ".credentials.json").write_text("token")
    (ai / ".claude" / "history.jsonl").write_text('{"line": 1}\n')
    (ai / ".claude" / "projects").mkdir()
    (ai / ".claude" / "projects" / "a.jsonl").write_text("session")
    (ai / ".claude" / "plugins").mkdir()
    (ai / ".claude" / "plugins" / "big").write_text("x" * 100)
    (ai / ".claude.json").write_text('{"oauthAccount": {}}')
    (ai / ".gemini").mkdir()
    (ai / ".gemini" / "settings.json").write_text('{"auth": "vertex-ai"}')


def test_default_group_is_seeded_from_the_shared_volume(roots):
    """AC8: ``default`` は再ログインなしで移行できる。"""
    home, ai, grp = roots
    _seed_source(ai)

    setup(roots, "default")

    assert (grp / ".claude" / ".credentials.json").read_text() == "token"
    assert (grp / ".claude" / "history.jsonl").read_text() == '{"line": 1}\n'
    assert (grp / ".claude" / "projects" / "a.jsonl").read_text() == "session"
    assert (grp / ".claude.json").read_text() == '{"oauthAccount": {}}'
    assert (grp / ".gemini" / "settings.json").read_text() == '{"auth": "vertex-ai"}'


def test_seed_does_not_copy_shared_assets(roots):
    """共通資産はグループ数だけ重複させない (238MB の plugins をコピーしない)。"""
    home, ai, grp = roots
    _seed_source(ai)

    setup(roots, "default")

    assert (grp / ".claude" / "plugins").is_symlink()
    assert (grp / ".claude" / "plugins").resolve() == (ai / ".claude" / "plugins").resolve()


def test_seed_is_a_copy_not_a_move(roots):
    """切り戻しの余地を残すため move ではなく copy にする。"""
    home, ai, grp = roots
    _seed_source(ai)

    setup(roots, "default")

    assert (ai / ".claude" / ".credentials.json").read_text() == "token"
    assert (ai / ".claude.json").exists()


def test_non_default_groups_are_not_seeded(roots):
    """AC3: 分離の意味が失われるため非 default ではシードしない。"""
    home, ai, grp = roots
    _seed_source(ai)

    setup(roots, "kkg")

    assert not (grp / ".claude" / ".credentials.json").exists()
    assert not (grp / ".claude" / "projects").exists()
    assert not (grp / ".gemini" / "settings.json").exists()
    # プレースホルダは作られるが、シード元の中身は入らない。
    # 期待値を "" から "{}" へ変えたのは issue #136 の修正による。空ファイルは
    # 不正な JSON で Claude Code が起動できないため、プレースホルダを {} にした。
    # 「シードされていない」ことの確認という本来の意図は変わらない。
    assert (grp / ".claude.json").read_text() == "{}"


def test_seed_runs_only_once(roots):
    """2 回目は何もしない (稼働後のデータをシード時点へ巻き戻さない)。"""
    home, ai, grp = roots
    _seed_source(ai)

    setup(roots, "default")
    (grp / ".claude" / ".credentials.json").write_text("refreshed")
    (ai / ".claude" / ".credentials.json").write_text("stale")

    setup(roots, "default")

    assert (grp / ".claude" / ".credentials.json").read_text() == "refreshed"


def test_seed_skips_entries_without_a_source(roots):
    """シード元が無いエントリ (gcloud / gws) があっても止まらない (AC8)。"""
    home, ai, grp = roots
    (ai / ".claude").mkdir(parents=True)
    (ai / ".claude" / "history.jsonl").write_text("only-this\n")

    setup(roots, "default")

    assert (grp / ".claude" / "history.jsonl").read_text() == "only-this\n"
    # .claude.json / .gemini はシード元が無いので空のプレースホルダのまま
    assert (grp / ".claude.json").is_file()
    assert (grp / ".gemini").is_dir()


def test_seed_copies_dotfiles(roots):
    """``.credentials.json`` のような隠しファイルを取りこぼさない。"""
    home, ai, grp = roots
    (ai / ".claude").mkdir(parents=True)
    (ai / ".claude" / ".last-cleanup").write_text("ts")

    setup(roots, "default")

    assert (grp / ".claude" / ".last-cleanup").read_text() == "ts"


# ---------------------------------------------------------------------------
# イメージ同梱の ~/.claude/settings.json の退避
# ---------------------------------------------------------------------------

HOOKS = '{"hooks":{"SessionStart":[]}}'


def test_image_claude_settings_are_kept_on_first_run(roots):
    """Dockerfile が焼いた ``~/.claude/settings.json`` を空ファイルで潰さない。

    symlink 張り替えは ``~/.claude`` を ``rm -rf`` するため、退避しないと
    hooks 設定が初回起動で消えて共通側に空ファイルだけが残る。
    """
    home, ai, grp = roots
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(HOOKS)

    setup(roots)

    assert (ai / ".claude" / "settings.json").read_text() == HOOKS
    # グループ側 -> 共通側の symlink 経由でも読める
    assert (home / ".claude" / "settings.json").read_text() == HOOKS


def test_image_claude_settings_do_not_overwrite_the_shared_volume(roots):
    """永続側に既存の設定があればイメージ側で上書きしない。"""
    home, ai, _ = roots
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(HOOKS)
    (ai / ".claude").mkdir(parents=True)
    (ai / ".claude" / "settings.json").write_text('{"user": true}')

    setup(roots)

    assert (ai / ".claude" / "settings.json").read_text() == '{"user": true}'


def test_second_run_does_not_seed_through_the_symlink(roots):
    """2 回目以降 (``~/.claude`` が symlink) は退避を走らせない。"""
    home, ai, _ = roots
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(HOOKS)

    setup(roots)
    (ai / ".claude" / "settings.json").write_text('{"edited": true}')
    setup(roots)

    assert (ai / ".claude" / "settings.json").read_text() == '{"edited": true}'
    assert (home / ".claude").is_symlink()


# ---------------------------------------------------------------------------
# プレースホルダの初期内容 (issue #136)
# ---------------------------------------------------------------------------

def test_new_group_gets_a_parsable_claude_json(roots):
    """非 default グループの初回起動で ``.claude.json`` が妥当な JSON になる。

    空ファイルは JSON として不正で、Claude Code が
    ``The configuration file at ~/.claude.json contains invalid JSON.``
    で起動を拒否する。``default`` はシードで実体が入るため踏まないが、
    非 default はシードを飛ばすので必ずプレースホルダになる。
    """
    home, _, grp = roots
    setup(roots, group_name="with")

    assert (grp / ".claude.json").read_text() == "{}"
    assert json.loads((home / ".claude.json").read_text()) == {}


def test_shared_settings_json_is_parsable(roots):
    """共通側の ``settings.json`` も空では作らない。

    共通ボリュームを新規に作った場合、同じ経路で 0 バイトになる。
    """
    _, ai, _ = roots
    setup(roots, group_name="with")

    assert json.loads((ai / ".claude" / "settings.json").read_text()) == {}


def test_non_json_entries_stay_empty(roots):
    """``CLAUDE.md`` は空のままでよい。Markdown は空で妥当。"""
    _, ai, _ = roots
    setup(roots, group_name="with")

    assert (ai / ".claude" / "CLAUDE.md").read_text() == ""


def test_history_jsonl_is_not_pre_created(roots):
    """``history.jsonl`` はプレースホルダとして作られない。

    ``~/.claude`` はディレクトリごとグループ側へ張られるため、その配下は
    ``devbase_ensure_entry`` を通らない。Claude Code が書くまで存在しない。
    ``DEVBASE_FILE_ENTRIES`` に載っているのは、将来この経路を通ったときに
    ディレクトリとして作られないようにするための型ヒントである。
    """
    _, _, grp = roots
    setup(roots, group_name="with")

    assert not (grp / ".claude" / "history.jsonl").exists()


def test_existing_file_content_is_not_overwritten(roots):
    """実体があるファイルの中身は書き換えない (冪等性)。

    2 回目以降の起動で利用者の設定が ``{}`` へ巻き戻ると、認証も MCP 接続も消える。
    """
    _, _, grp = roots
    grp.mkdir(parents=True, exist_ok=True)
    (grp / ".claude.json").write_text('{"oauthAccount": "keep me"}')

    setup(roots, group_name="with")
    setup(roots, group_name="with")

    assert json.loads((grp / ".claude.json").read_text()) == {
        "oauthAccount": "keep me"}


def test_deliberately_emptied_file_is_left_alone(roots):
    """利用者が空にしたファイルは書き換えない。

    ``devbase_ensure_entry`` の契約は「実体が無ければ作る」であって、
    「中身を直す」ではない。壊れたボリュームの自動修復はこの関数の責務ではない。
    """
    _, _, grp = roots
    grp.mkdir(parents=True, exist_ok=True)
    (grp / ".claude.json").write_text("")

    setup(roots, group_name="with")

    assert (grp / ".claude.json").read_text() == ""
