# devbase list TUI化 (simple-term-menu) 実装プラン

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `devbase list`（TTY 時の対話選択）を simple-term-menu ベースの TUI 化し、矢印キー移動・`[1-9]` 番号ジャンプ・`/` インクリメンタル検索で起動プロジェクトを選べるようにする。

**Architecture:** 変更は `lib/devbase/commands/project.py` の対話選択ロジックに限定。`_interactive_select_and_up` は (a) simple-term-menu が import できれば TUI メニュー (`_tui_select_and_up`)、(b) 未導入なら現行の `input()` 番号入力 (`_fallback_select_and_up`) に分岐する。非 TTY 判定 (`cmd_project_list` の isatty ガード) と一覧テーブル出力は現状維持。

**Tech Stack:** Python 3.10+ / simple-term-menu (Unix 専用) / pytest / uv

設計書: `issues/old/i29_list-tui-simple-term-menu.md`

---

## File Structure

- `pyproject.toml` — `dependencies` に `simple-term-menu>=1.6` を追加（Modify）
- `uv.lock` — `uv lock` で再生成（Modify）
- `lib/devbase/commands/project.py` — 対話選択を TUI 化（Modify）
  - 追加: `_color_status`, `_build_menu_entries`, `_show_menu`, `_tui_select_and_up`, `_start_project_up`
  - 改名: 現 `_interactive_select_and_up` の本体 → `_fallback_select_and_up`
  - 新 `_interactive_select_and_up`: import 可否で分岐するディスパッチャ
  - モジュール先頭: `simple_term_menu` の任意 import ガード + `_STATUS_COLOR` フラグ
- `tests/cli/test_project_list.py` — 既存 input ベーステストを fallback 用に固定 + TUI テスト追加（Modify）
- `CHANGELOG.md` / `README.md` — 利用者向け記述更新（Modify）

各タスクは TDD（失敗テスト→最小実装→green→commit）で進める。`tests/cli/test_project_list.py` 冒頭の補助関数 `_make_plugin_project` / `_link_project` は既存のものを再利用する。

---

## Task 1: simple-term-menu を依存に追加

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: pyproject.toml に依存を追加**

`pyproject.toml` の `dependencies` を以下にする（`simple-term-menu>=1.6` を追記）:

```toml
dependencies = [
    "pyyaml>=6.0",
    "pyrage>=1.2",
    "boto3>=1.34",
    "simple-term-menu>=1.6",
]
```

- [ ] **Step 2: ロックファイル更新と同期**

Run:
```bash
uv lock && uv sync
```
Expected: `simple-term-menu` が解決・インストールされる（`uv.lock` に追加、`.venv` に導入）。

- [ ] **Step 3: import 可能なことを確認**

Run:
```bash
uv run python -c "from simple_term_menu import TerminalMenu; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: 既存テストが引き続き green なことを確認**

Run:
```bash
uv run pytest tests/cli/test_project_list.py -q
```
Expected: PASS（34 passed）

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): simple-term-menu を追加 (devbase list TUI化の前提)"
```

---

## Task 2: `_color_status` と `_build_menu_entries` を実装

rows を simple-term-menu 用の表示文字列へ変換する純粋関数。先頭 9 件にショートカット `[n]` を付与し、`colorize=True` で STATUS に色を付ける。

**Files:**
- Modify: `lib/devbase/commands/project.py`
- Test: `tests/cli/test_project_list.py`

- [ ] **Step 1: 失敗テストを書く**

`tests/cli/test_project_list.py` の末尾に追加:

```python
# ---------------------------------------------------------------------------
# TUI: _build_menu_entries / _color_status
# ---------------------------------------------------------------------------

def test_build_menu_entries_shortcuts_and_mapping():
    from devbase.commands.project import _build_menu_entries

    rows = [{"name": f"p{i}", "plugin": "-", "status": "stopped"} for i in range(11)]
    entries = _build_menu_entries(rows)

    assert len(entries) == 11
    # 先頭 9 件は [1]..[9] ショートカット付き (entry index と rows index は 1:1)
    for i in range(9):
        assert entries[i].startswith(f"[{i + 1}] ")
        assert f"p{i}" in entries[i]
    # 10 件目以降はショートカット無し (4 スペース始まりで桁を揃える)
    assert entries[9].startswith("    ")
    assert not entries[9].lstrip().startswith("[")
    assert "p9" in entries[9]
    assert "p10" in entries[10]


def test_build_menu_entries_colorize_wraps_status():
    from devbase.commands.project import _build_menu_entries

    rows = [
        {"name": "a", "plugin": "-", "status": "running (1 containers)"},
        {"name": "b", "plugin": "-", "status": "stopped"},
        {"name": "c", "plugin": "-", "status": "unknown"},
    ]
    entries = _build_menu_entries(rows, colorize=True)

    assert "\033[32m" in entries[0] and "\033[0m" in entries[0]   # running=緑
    assert "\033[90m" in entries[1]                                # stopped=灰
    assert "\033[" not in entries[2]                               # unknown=無装飾


def test_build_menu_entries_plain_has_no_ansi():
    from devbase.commands.project import _build_menu_entries

    rows = [{"name": "a", "plugin": "-", "status": "running (1 containers)"}]
    entries = _build_menu_entries(rows, colorize=False)

    assert "\033[" not in entries[0]
```

- [ ] **Step 2: テストが失敗することを確認**

Run:
```bash
uv run pytest tests/cli/test_project_list.py -k build_menu_entries -q
```
Expected: FAIL（`ImportError: cannot import name '_build_menu_entries'`）

- [ ] **Step 3: 最小実装を書く**

`lib/devbase/commands/project.py` の `_print_table` の直後（`_interactive_select_and_up` の前）に追加:

```python
# STATUS 色付け用 ANSI。実機で桁崩れ等が出る場合は _STATUS_COLOR を False にする。
_ANSI_GREEN = "\033[32m"
_ANSI_GREY = "\033[90m"
_ANSI_RESET = "\033[0m"


def _color_status(status: str) -> str:
    """STATUS 文字列に色を付ける。running 系=緑 / stopped=灰 / その他=無装飾。

    color 対象の文字列は status._container_status_for が返す
    ``running (N containers)`` / ``stopped`` と、project.list_projects が補う
    ``unknown`` を想定する。
    """
    if status.startswith("running"):
        return f"{_ANSI_GREEN}{status}{_ANSI_RESET}"
    if status == "stopped":
        return f"{_ANSI_GREY}{status}{_ANSI_RESET}"
    return status


def _build_menu_entries(rows: list[dict], colorize: bool = False) -> list[str]:
    """rows を simple_term_menu 用の表示文字列へ変換する。

    返り値の index は rows の index と 1:1 対応する (entry i ↔ rows[i])。
    先頭 9 件には simple_term_menu のショートカット記法 ``[n]`` (n=1..9) を付与し、
    数字キーで即ジャンプできるようにする。10 件目以降は ``[n] `` と同じ 4 文字幅
    (スペース) で字下げして桁を揃える。``colorize`` が True のとき STATUS に
    ANSI 色を付ける (検索/桁計算が崩れる端末向けに呼び出し側で False にできる)。
    """
    name_w = max(len("NAME"), *(len(r["name"]) for r in rows))
    plugin_w = max(len("PLUGIN"), *(len(r["plugin"]) for r in rows))
    entries: list[str] = []
    for i, r in enumerate(rows):
        status = _color_status(r["status"]) if colorize else r["status"]
        body = f"{r['name']:<{name_w}}  {r['plugin']:<{plugin_w}}  {status}"
        if i < 9:
            entries.append(f"[{i + 1}] {body}")
        else:
            entries.append(f"    {body}")  # "[n] " と同じ 4 文字幅で字下げ
    return entries
```

- [ ] **Step 4: テストが通ることを確認**

Run:
```bash
uv run pytest tests/cli/test_project_list.py -k "build_menu_entries or color_status" -q
```
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add lib/devbase/commands/project.py tests/cli/test_project_list.py
git commit -m "feat(list): メニュー表示文字列生成 (_build_menu_entries / _color_status) 追加"
```

---

## Task 3: TUI メニュー本体とディスパッチャを実装（fallback 温存）

simple-term-menu の任意 import、`_show_menu` / `_tui_select_and_up` / `_start_project_up` を追加し、現 `_interactive_select_and_up` を `_fallback_select_and_up` に改名。新 `_interactive_select_and_up` で import 可否により分岐する。

**Files:**
- Modify: `lib/devbase/commands/project.py`
- Test: `tests/cli/test_project_list.py`

- [ ] **Step 1: 失敗テストを書く（TUI 経路）**

`tests/cli/test_project_list.py` の末尾に追加:

```python
# ---------------------------------------------------------------------------
# TUI: _interactive_select_and_up のディスパッチ (TUI 経路)
# ---------------------------------------------------------------------------

def test_cmd_project_list_tui_selects_and_ups(tmp_path, monkeypatch):
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod
    from devbase.commands import container as container_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    _make_plugin_project(tmp_path, "plugins/beta", "beta-proj")
    _link_project(tmp_path, "beta-proj", "plugins/beta", "beta-proj")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)
    monkeypatch.setattr(project_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(project_mod.sys.stdout, "isatty", lambda: True)

    # simple_term_menu 導入済み相当にし、メニューは index=1 (beta-proj) を返すよう差し替え
    monkeypatch.setattr(project_mod, "_HAVE_TERMINAL_MENU", True)
    monkeypatch.setattr(project_mod, "_show_menu", lambda rows: 1)

    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(
                            subcommand=args.subcommand, name=args.name) or 0)

    args = types.SimpleNamespace(interactive=True)
    rc = project_mod.cmd_project_list(tmp_path, args)

    assert rc == 0
    assert captured["subcommand"] == "up"
    assert captured["name"] == "beta-proj"


def test_cmd_project_list_tui_abort_returns_zero(tmp_path, monkeypatch):
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod
    from devbase.commands import container as container_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)
    monkeypatch.setattr(project_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(project_mod.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(project_mod, "_HAVE_TERMINAL_MENU", True)
    # ESC 等での中止は _show_menu が None を返す
    monkeypatch.setattr(project_mod, "_show_menu", lambda rows: None)

    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)

    args = types.SimpleNamespace(interactive=True)
    rc = project_mod.cmd_project_list(tmp_path, args)

    assert rc == 0
    assert called == [], "中止時は up を起動しない"


def test_interactive_falls_back_when_no_terminal_menu(tmp_path, monkeypatch):
    """simple_term_menu 未導入時は input() 番号入力にフォールバックして up する。"""
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod
    from devbase.commands import container as container_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)
    monkeypatch.setattr(project_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(project_mod.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(project_mod, "_HAVE_TERMINAL_MENU", False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "1")

    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(name=args.name) or 0)

    args = types.SimpleNamespace(interactive=True)
    rc = project_mod.cmd_project_list(tmp_path, args)

    assert rc == 0
    assert captured["name"] == "alpha-proj"
```

- [ ] **Step 2: テストが失敗することを確認**

Run:
```bash
uv run pytest tests/cli/test_project_list.py -k "tui or falls_back" -q
```
Expected: FAIL（`AttributeError: ... has no attribute '_HAVE_TERMINAL_MENU'` / `_show_menu`）

- [ ] **Step 3: モジュール先頭に import ガードとフラグを追加**

`lib/devbase/commands/project.py` の `logger = get_logger(__name__)` の直後に追加:

```python
# simple_term_menu は Unix 専用の任意依存。未導入/非対応環境では番号入力に
# フォールバックするため、import 失敗を許容する。
try:
    from simple_term_menu import TerminalMenu
    _HAVE_TERMINAL_MENU = True
except ImportError:  # pragma: no cover - 未導入環境のフォールバック経路
    TerminalMenu = None
    _HAVE_TERMINAL_MENU = False

# STATUS 色付けの有効/無効。simple_term_menu の桁計算と ANSI が衝突する端末では
# False に倒してプレーン表示にする (機能 > 装飾)。
_STATUS_COLOR = True
```

- [ ] **Step 4: TUI 関数と共有ヘルパを追加**

`_build_menu_entries`（Task 2 で追加）の直後に追加:

```python
def _start_project_up(name: str) -> int:
    """``project up <name>`` を共有ハンドラ cmd_project 経由で起動する。"""
    import types

    from devbase.commands.container import cmd_project
    return cmd_project(types.SimpleNamespace(subcommand="up", name=name, scale=None))


def _show_menu(rows: list[dict]) -> int | None:
    """TerminalMenu を起動し、選択された rows の index を返す (中止時 None)。

    テストではこの関数自体を monkeypatch して TerminalMenu の実起動を避ける。
    """
    entries = _build_menu_entries(rows, colorize=_STATUS_COLOR)
    menu = TerminalMenu(
        entries,
        title=("起動するプロジェクトを選択 "
               "(↑↓ 移動 / 1-9 ジャンプ / / 検索 / Enter 決定 / Esc 中止):"),
        cycle_cursor=True,
        clear_screen=False,
        show_search_hint=True,
    )
    return menu.show()


def _tui_select_and_up(rows: list[dict]) -> int:
    """TUI メニューで 1 件選択し ``project up <name>`` を起動する。"""
    idx = _show_menu(rows)
    if idx is None:
        logger.info("中止しました。")
        return 0
    return _start_project_up(rows[idx]["name"])
```

- [ ] **Step 5: 現 `_interactive_select_and_up` を改名し、末尾の up 起動を共有ヘルパに置換**

`lib/devbase/commands/project.py` の現 `def _interactive_select_and_up(rows):` を
`def _fallback_select_and_up(rows: list[dict]) -> int:` に改名し、docstring 冒頭を
「番号入力で 1 件選択し…（simple_term_menu 未導入時のフォールバック）」に更新する。
関数末尾の次のブロック:

```python
    name = rows[idx - 1]["name"]
    # name 解決 (chdir) + up は共有ハンドラ cmd_project に委譲する。
    import types

    from devbase.commands.container import cmd_project
    return cmd_project(types.SimpleNamespace(subcommand="up", name=name, scale=None))
```

を次に置換する:

```python
    return _start_project_up(rows[idx - 1]["name"])
```

- [ ] **Step 6: 新しいディスパッチャ `_interactive_select_and_up` を追加**

`_fallback_select_and_up` の直前（`_tui_select_and_up` の後）に追加:

```python
def _interactive_select_and_up(rows: list[dict]) -> int:
    """一覧から 1 件選択して ``project up`` を起動する (TTY 専用)。

    simple_term_menu が利用可能なら矢印キー対応の TUI メニューを使う。未導入環境
    では現行の番号入力方式 (_fallback_select_and_up) にフォールバックする。
    """
    if _HAVE_TERMINAL_MENU:
        return _tui_select_and_up(rows)
    logger.warning(
        "simple_term_menu が未導入のため番号入力にフォールバックします "
        "(`uv sync` で導入すると矢印キー選択が使えます)。"
    )
    return _fallback_select_and_up(rows)
```

- [ ] **Step 7: 新規 TUI テストが通ることを確認**

Run:
```bash
uv run pytest tests/cli/test_project_list.py -k "tui or falls_back" -q
```
Expected: PASS（3 passed）

- [ ] **Step 8: Commit**

```bash
git add lib/devbase/commands/project.py tests/cli/test_project_list.py
git commit -m "feat(list): simple-term-menu による TUI 選択を追加 (fallback 温存)"
```

---

## Task 4: 既存 input ベーステストを fallback 経路に固定

Task 3 で主経路が TUI になったため、`input()` 挙動を検証する既存テスト 6 件に
`_HAVE_TERMINAL_MENU = False` の固定を追加し、fallback テストとして成立させる。

**Files:**
- Modify: `tests/cli/test_project_list.py`

- [ ] **Step 1: 6 テストに fallback 固定を追加**

以下の各テスト関数で、`project_mod.sys.stdin.isatty` を設定している行の直後に
1 行追加する:

```python
    monkeypatch.setattr(project_mod, "_HAVE_TERMINAL_MENU", False)
```

対象テスト:
- `test_cmd_project_list_interactive_selects_and_ups`
- `test_cmd_project_list_interactive_empty_input_aborts`
- `test_cmd_project_list_interactive_non_tty_eof`
- `test_cmd_project_list_interactive_keyboard_interrupt_aborts`
- `test_cmd_project_list_interactive_out_of_range_reprompts`
- `test_cmd_project_list_interactive_non_numeric_reprompts`

（いずれも `from devbase.commands import project as project_mod` を冒頭で import 済み。
未 import のものがあれば追加する。）

- [ ] **Step 2: ファイル全体のテストが通ることを確認**

Run:
```bash
uv run pytest tests/cli/test_project_list.py -q
```
Expected: PASS（既存 34 + 新規 6 = 40 passed）

- [ ] **Step 3: Commit**

```bash
git add tests/cli/test_project_list.py
git commit -m "test(list): 既存 input テストを fallback 経路 (_HAVE_TERMINAL_MENU=False) に固定"
```

---

## Task 5: 静的チェックと全テスト

**Files:** （変更なし・検証のみ）

- [ ] **Step 1: コンパイルチェック（CI 相当）**

Run:
```bash
uv run python -m compileall -q lib bin
```
Expected: エラー出力なし（終了コード 0）

- [ ] **Step 2: ruff lint（CI 相当）**

Run:
```bash
uvx ruff check --select=E9,F63,F7,F82 lib
```
Expected: `All checks passed!`

- [ ] **Step 3: 全テスト実行**

Run:
```bash
uv run pytest -q
```
Expected: PASS（全 green）

- [ ] **Step 4: 変更があればコミット（lint 修正等が出た場合のみ）**

```bash
git add -A
git commit -m "chore(list): lint/compile 対応"
```
（変更なしならスキップ）

---

## Task 6: 実機 TTY 検証（手動）と色のデグレード判断

ユニットテストでは矢印キー/検索/色の見た目は検証できないため、実端末で確認する。
**この検証は本物の TTY が必要で、ユーザーが実行する。**

- [ ] **Step 1: 実端末で対話メニューを起動**

ユーザーが本物のターミナルで実行:
```bash
./bin/devbase list
```

- [ ] **Step 2: 以下を目視確認**

  - ↑↓ で行が移動する（端で循環する）
  - `1`〜`9` で該当行へジャンプできる
  - `/` で名前のインクリメンタル検索ができる
  - Enter で選択プロジェクトが `up` される
  - Esc で何も起動せず終了する
  - STATUS の色（running=緑 / stopped=灰）で**桁ずれ・ハイライト崩れ・検索不具合が出ない**

- [ ] **Step 3: 色で表示が崩れる場合のみデグレード**

桁ずれ等が出たら `lib/devbase/commands/project.py` の `_STATUS_COLOR = True` を
`_STATUS_COLOR = False` に変更し、Step 1〜2 を再確認する。

- [ ] **Step 4: 非 TTY フォールバック確認**

Run:
```bash
./bin/devbase list | cat
```
Expected: 対話に入らず NAME/PLUGIN/STATUS のプレーンなテーブルが出力される。

- [ ] **Step 5: 変更があればコミット（デグレードした場合のみ）**

```bash
git add lib/devbase/commands/project.py
git commit -m "fix(list): 端末互換のため STATUS 色付けを無効化 (_STATUS_COLOR=False)"
```

---

## Task 7: ドキュメント更新

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`（必要に応じて `docs/user/cli-reference.md`）

- [ ] **Step 1: CHANGELOG に追記**

`CHANGELOG.md` の `## [Unreleased]` 直下の `### Added` 先頭に追加:

```markdown
- **`devbase list` の対話選択を TUI 化**しました。`simple-term-menu` 導入により、
  ↑↓ の矢印キーで行移動、先頭 9 件は `1`〜`9` の数字キーで即ジャンプ、`/` で
  名前のインクリメンタル検索ができます。Enter で選択プロジェクトを `up` 起動し、
  Esc で中止します。非 TTY（パイプ/CI/リダイレクト）では従来どおりプレーンな
  一覧表示にフォールバックし、`simple-term-menu` 未導入環境では番号入力方式に
  フォールバックします（macOS / Linux 対応）。
```

- [ ] **Step 2: README の機能一覧へ 1 行追記**

`README.md` の機能箇条書き（`- **環境変数の自動収集**:` 等が並ぶ箇所）に追加:

```markdown
- **対話的なプロジェクト選択**: `devbase list` で矢印キー・番号・`/` 検索に対応した TUI メニューから起動対象を選べます
```

- [ ] **Step 3: コミット**

```bash
git add CHANGELOG.md README.md
git commit -m "docs(list): TUI 化 (simple-term-menu) を CHANGELOG / README に反映"
```

---

## 完了条件（受け入れ基準）

- `uv run pytest -q` が全 green（test_project_list.py は既存 34 + 追加 6 件 = 40）
- `uv run python -m compileall -q lib bin` と `uvx ruff check --select=E9,F63,F7,F82 lib` が pass
- 実端末で矢印移動 / `[1-9]` ジャンプ / `/` 検索 / Enter 起動 / Esc 中止 ができる
- `./bin/devbase list | cat` が従来のプレーンテーブルを出す
- simple-term-menu 不在でも番号入力で選択できる（fallback）

## 非対象（YAGNI）

- 複数選択 / 一括 up、preview pane、Windows ネイティブ対応、多桁番号の任意行直接ジャンプ
