# i30 (plan30): devbase list TUI 改善 (questionary 移行) + `devbase rebuild` 追加

## 関連リンク
- 先行: [i29 list TUI 化](i29_list-tui-plan.md) / [simple-term-menu 採用](i29_list-tui-simple-term-menu.md)
- 関連 PR: #42 (i29 の TUI 化を main へ統合済み)

## 概要
`devbase list` の対話選択 (TUI) を **simple-term-menu から questionary へ移行**しつつ 3 点改善し、
併せて `docker compose build --no-cache` 相当の新コマンド `devbase rebuild` を追加する。

1. **番号選択 (`[1]`〜`[9]`) を廃し、名前絞り込み + 番号ラベルへ刷新**
2. **↑/↓ 長押し時のスクロール遅延を解消**（questionary 移行で構造的に解消）
3. **running 状態の行を選択したときに「再起動 / 再ビルド / 停止」を選べるサブメニューを表示**
4. (3 の前提) **`devbase rebuild` コマンドを新設**

## 問題・背景

### 1. 番号選択のカバレッジが低い → 名前絞り込みへ刷新
現行の `[1]`〜`[9]` ショートカットは先頭 9 件しかカバーできず、最大 38 件規模のリストでは
カバレッジが低く実質無意味。

**採用 (確定): questionary の `use_search_filter=True` による名前絞り込み**。各行に番号を
**目印として表示**しつつ、絞り込みは**プロジェクト名等の部分一致**で行う。文字をタイプして
全 N 件のどれにも到達でき、誤ヒットもない。

> ⚠ 補足: リリース版 questionary 2.1.1 には `search_matcher` (カスタム一致関数) が無く
> (main ブランチのみ・未リリース)、絞り込みは「表示テキスト全体への大文字小文字無視の部分一致」
> 固定。そのため「数字を打って行番号で選ぶ」方式はステータス文字列 (`running (1 containers)`)
> や名前中の数字に誤ヒットするため**採用しない**。番号は視認用ラベルに留める。

### 2. ↑長押しのスクロールが遅い (原因特定済み → ライブラリ移行で解消)
simple-term-menu の `_read_next_key` は `os.read(tty, 80)` で一括読みするため、↑長押しの連結
エスケープシーケンスがキー名照合に失敗して**まるごと無視**され、長押し入力の大半がドロップする
（公式 issue [#99](https://github.com/IngoMeyer441/simple-term-menu/issues/99) /
未マージ修正 PR [#100](https://github.com/IngoMeyer441/simple-term-menu/pull/100) で裏取り済み）。

調査の結果、**questionary (prompt_toolkit ベース) へ移行**する方針に決定。prompt_toolkit の
`Vt100Parser` がバイトストリームを 1 バイトずつ食ってキーを離散化するため、「連結シーケンスを
取りこぼす」という simple-term-menu 固有の障害が**構造的に発生しない**。併せて以下も得られる:
- `use_search_filter=True` による組み込みインクリメンタル絞り込み (現行 `/` 検索の代替)
- 活発なメンテナンス (v2.1.x / 2025) ・クロスプラットフォーム対応
- キャンセル (Ctrl-C) が `.ask()` の戻り値 None で取れる (中止判定が素直)

代替候補だった pick (検索が組み込みでない) / survey (更新停滞・中止が例外ベース) ではなく
questionary を採用する。

### 3. running 行は up/down/rebuild を選びたい
現状は選択行を一律 `project up` していた。running なプロジェクトでは「再起動・再ビルド・停止」を
選べると操作が完結する。

### 4. `devbase rebuild` が無い
`docker compose build --no-cache` 相当の単独コマンドが無い (シェルラッパー `bin/devbase` の
`cmd_build` は `--no-cache` 対応だが devbase-base の 2 段ビルドを伴う別物)。3 の「再ビルド」
選択肢の実体として、Python 側に簡潔な `cmd_rebuild` を新設する。

## 修正対象
- `pyproject.toml` / `uv.lock` — `simple-term-menu` を `questionary>=2.1` へ置換
- `lib/devbase/commands/project.py` — TUI を questionary へ移行 + 3 点改善
- `lib/devbase/commands/container.py` — `cmd_rebuild` 追加 + `_dispatch_lifecycle` への登録
- `lib/devbase/cli.py` — `project rebuild [name]` サブパーサ + トップレベル `rebuild` ショートカット
- `bin/devbase` — `resolve_command` のコマンド一覧 + name 解決対象に `rebuild` 追加
- `tests/cli/test_project_list.py` — simple-term-menu 前提のテストを questionary seam ベースへ更新
- `tests/cli/` (container/cli 系) — `cmd_rebuild` / `rebuild` パーサのテスト
- `CHANGELOG.md` / `README.md` — questionary 移行・`devbase rebuild`・操作説明の更新

## 確定仕様

### A. ライブラリ移行 (questionary)
- `pyproject.toml` の `dependencies` から `simple-term-menu>=1.6` を削除し `questionary>=2.1` を追加。
  `uv.lock` を更新 (prompt_toolkit を間接依存として取り込む)。
- `project.py` の import を `try: import questionary except ImportError: questionary=None;
  _HAVE_QUESTIONARY=False` パターンへ変更 (optional import + fallback 維持の方針は踏襲)。

### B. メニュー実装 (`_show_menu`) — 名前絞り込み + 番号ラベル
- `_build_menu_entries(rows)` は各行に**番号ラベル** (`{i+1:>{w}}`、w=桁数) を付けた整列 body
  (` 1  NAME  PLUGIN  STATUS`) の**プレーン文字列**を返す (番号は視認用。色付けは現状どおり無効、
  将来 questionary style で別途検討)。
- `_show_menu(rows) -> int | None`:
  ```python
  choices = [questionary.Choice(title=entry, value=i) for i, entry in enumerate(entries)]
  return questionary.select(
      "起動するプロジェクトを選択 (↑↓ 移動 / 名前で絞り込み / Enter 決定 / Ctrl-C 中止):",
      choices=choices,
      use_arrow_keys=True,
      use_jk_keys=False,        # use_search_filter と併用不可のため False
      use_search_filter=True,   # 文字入力で名前等を部分一致絞り込み (/ 検索の代替)
      use_shortcuts=False,      # 単一キーショートカットは使わない
  ).ask()                       # 選択 index / キャンセル時 None
  ```
- テスト容易性のため questionary 呼び出しは `_show_menu` / `_show_action_menu` に閉じ込め、テストは
  これらを monkeypatch する (現行も `_show_menu` を monkeypatch する設計を踏襲)。

### C. running 行サブメニュー
- 選択 index 確定後、`rows[idx]["status"]` が `"running"` で始まるか判定:
  - **running**: `_show_action_menu() -> "up"|"rebuild"|"down"|None` を表示。
    - `再起動 (up)`               → `cmd_project(subcommand="up",      name=...)`
    - `再ビルド (rebuild --no-cache)` → `cmd_project(subcommand="rebuild", name=...)`
    - `停止 (down)`               → `cmd_project(subcommand="down",    name=...)`
    - None (Ctrl-C) → 何もせず `return 0`
  - **非 running** (stopped / unknown 等): 従来どおり直接 `up`。
- ディスパッチ用ヘルパ `_start_project_up/down/rebuild(name)` を `cmd_project` 経由で呼ぶ
  (down/rebuild は引数なしハンドラだが `_dispatch_lifecycle` が name で chdir してから実行する既存設計に乗る)。

### D. `devbase rebuild` (案1 = ご指示どおり)
- `container.py`:
  ```python
  def cmd_rebuild() -> int:
      """Rebuild images without cache (docker compose build --no-cache)."""
      # compose.yml 存在チェック → subprocess: docker compose build --no-cache
  ```
- `_dispatch_lifecycle` の handlers に `'rebuild': lambda: cmd_rebuild()` を追加。
- `cli.py`: `project` に `rebuild [name]` サブパーサ追加 + トップレベル `rebuild` を
  `('project',)` subcommand リスト / SHORTCUTS に登録。
- `bin/devbase`: `resolve_command` の `commands` に `rebuild` 追加 + name 解決対象
  (`up`/`down`/`ps`/`logs`/`scale` 同列) に含める。
- **注意点 (既知の差分)**: `docker compose build --no-cache` は dev (プロジェクト) イメージのみを
  no-cache 再ビルドし、`devbase-base` は作り直さない。base まで作り直すには従来どおり
  `devbase build --no-cache` を使う。この差は本プランでは仕様として許容 (ご指示「docker compose
  build --no-cache 相当」に準拠)。
- **TUI からの rebuild は再ビルドのみ** (自動 up はしない / D2 確定)。

### E. fallback (維持)
- questionary 未導入時 (`not _HAVE_QUESTIONARY`) は現行 `_fallback_select_and_up` (stdlib `input()`
  番号入力) を維持。非 TTY 時のテーブル表示フォールバックも不変。
  - 補足: running サブメニューは TUI 専用。fallback (番号入力) 経路では従来どおり `up` のみとする
    (番号入力でさらに up/rebuild/down を多段で聞くと UX が煩雑になるため)。

## タスク分解

### Task 1: 依存差し替え (questionary)
- **対象:** `pyproject.toml`, `uv.lock`
- **変更:** `simple-term-menu` 削除・`questionary>=2.1` 追加・lock 更新。

### Task 2: `devbase rebuild` コマンド追加
- **対象:** `lib/devbase/commands/container.py`, `lib/devbase/cli.py`, `bin/devbase`
- **変更:** `cmd_rebuild` 実装 + ディスパッチ登録 + パーサ/ショートカット/wrapper 登録。

### Task 3: TUI を questionary へ移行 + 名前絞り込み + 番号ラベル
- **対象:** `lib/devbase/commands/project.py`, `tests/cli/test_project_list.py`
- **変更:** import/`_show_menu`/`_build_menu_entries` を questionary 化・番号ラベルを全項目へ付与・
  既存テストを questionary seam ベースへ更新。

### Task 4: running 行サブメニュー
- **対象:** `lib/devbase/commands/project.py`, `tests/cli/test_project_list.py`
- **変更:** running 判定 + `_show_action_menu` + up/rebuild/down 分岐 + 中止処理 + 分岐テスト追加。

### Task 5: ドキュメント
- **対象:** `CHANGELOG.md`, `README.md`
- **変更:** questionary 移行・`devbase rebuild`・list TUI 操作説明 (番号削除・絞り込み・running 操作) の更新。

## 影響範囲
- `devbase list` / `devbase project list` の対話 UI (ライブラリ変更・番号無効化・スクロール改善・
  running 行の操作分岐・`/` 検索 → 文字入力絞り込み)。
- 依存関係: `simple-term-menu` (Unix 専用) → `questionary` + 間接 `prompt_toolkit`。
- `devbase project`/トップレベルのサブコマンド集合に `rebuild` が増える (wrapper/cli/補完)。
- questionary 未導入時の fallback (番号入力) / 非 TTY のテーブル表示は不変。

## テスト計画
- [ ] `_build_menu_entries` が全行に右寄せ番号ラベル付きの整列 body を返す
- [ ] `_show_menu` (questionary) を monkeypatch した選択で対応プロジェクトに対し up が起動する
- [ ] running 行選択 → `_show_action_menu` で up/rebuild/down を選ぶと対応 subcommand で
      `cmd_project` が呼ばれる / None (中止) で何も起動しない
- [ ] 非 running 行選択は従来どおり直接 up
- [ ] questionary 未導入時は番号入力 fallback が従来どおり動く / 非 TTY はテーブル表示
- [ ] `cmd_rebuild` が compose.yml 不在時にエラー終了 / 存在時に `docker compose build --no-cache` を起動
- [ ] `project rebuild` / トップレベル `rebuild` パーサが subcommand=rebuild を解決
- [ ] pytest 全通過 (既存 list / fallback / 非 TTY テストにリグレッションなし)
