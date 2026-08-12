# `devbase list` 対話選択の TUI 化 設計書

- 日付: 2026-06-07
- 対象: `devbase list` / `devbase project list --interactive` の対話選択 UI
- 関連: PR #39 (対話選択をデフォルト化), PR #40 (status 集計修正)

## 1. 目的 / 背景

現在 `devbase list`（デフォルトで対話モード）は stdlib `input()` ベースで、
`[1] name (plugin, status)` の番号一覧を表示し番号入力で 1 件選択 → `project up` を起動する。
矢印キーによる行移動がなく、視認性・操作性が低い。

本変更では、CLI 用の著名ライブラリ **simple-term-menu** を導入し、以下を実現する。

- ↑↓ 矢印キーによる行移動
- 番号（先頭 9 件のショートカット）による該当行への即ジャンプ＆選択
- `/` によるインクリメンタル検索（38 件規模での実用的な絞り込み）

「自作せず著名ライブラリを使う」というユーザ方針に従う。
（現コードのコメントにある「`simple_term_menu` 等の外部依存を増やさず」という旧方針を本変更で転換する。）

## 2. 対象環境 / 制約

- macOS / Linux のみ対応（Windows ネイティブ端末は対象外）。
  simple-term-menu は Unix 系専用であり本制約と一致する。
- 実行経路: `bin/devbase` → `uv run --project "$DEVBASE_ROOT" python -m devbase.cli`。
  依存は `pyproject.toml` + `uv.lock` 管理で `.venv` に解決されるため、追加のブートストラップは不要。
- プロジェクト数は実運用で数十件（現状 38 件）。多桁番号の任意行直接ジャンプは
  どの著名ライブラリもネイティブ非対応であり、`[1-9]` ショートカット + `/` 検索で代替する。

## 3. 変更範囲

- `pyproject.toml`: `dependencies` に `simple-term-menu>=1.6` を追加。`uv lock` で `uv.lock` 更新。
- `lib/devbase/commands/project.py`: `_interactive_select_and_up` を TUI 化。
  - `list_projects` / `_print_table` / `cmd_project_list` のディスパッチ・非 TTY 判定は現状維持。
- `tests/cli/test_project_list.py`: 主経路を TUI ラッパ注入に更新 + 追加ケース。

`commands/status.py` 等の状態集計ロジックには手を入れない。

## 4. 詳細設計

### 4.1 関数構成

`commands/project.py` に以下を導入する。

- `_build_menu_entries(rows) -> tuple[list[str], list[str]]`
  桁揃えした表示文字列リストと、それに対応する name リストを返す純粋関数。
  先頭 9 件には simple-term-menu のショートカット記法 `[1]`〜`[9]` を付与する。
  STATUS は色付け（4.3 参照）。テスト容易性のため副作用なし。
- `_show_menu(rows) -> int | None`
  `TerminalMenu` を構築し `show()` の結果（選択 index / 中止時 None）を返す薄いラッパ。
  テストはこの関数を monkeypatch して選択を注入する（TerminalMenu 自体は起動しない）。
- `_fallback_select(rows) -> int | None`
  現行 `input()` 番号入力ロジックを関数として温存。選択 index / 中止 None を返す。
- `_interactive_select_and_up(rows) -> int`
  上記を統合。simple-term-menu の import 可否で経路分岐し、選択 index から
  `cmd_project up <name>` を起動（現状と同じ委譲）。

### 4.2 TerminalMenu の挙動

- 各行: `NAME  PLUGIN  STATUS`（桁揃え）。先頭 9 件は `[n]` ショートカット付き。
- 設定:
  - `cycle_cursor=True`（端で循環）
  - `clear_screen=False`（スクロールバックを汚さない）
  - 検索キー `/`、`show_search_hint=True`
  - `status_bar` に操作ヒント（矢印 / 番号 / `/` 検索 / Enter / ESC）
- キー操作:
  - ↑↓: 行移動
  - `1`〜`9`: 該当行へジャンプ（先頭 9 件）
  - `/`: 名前のインクリメンタル検索
  - Enter: 確定 → `cmd_project up <name>`
  - ESC / `q`: 中止（`show()` が None を返す → 戻り値 0）

### 4.3 STATUS 色付け

- `running (N containers)` 系 = 緑、`stopped` = 灰、`unknown` = 既定色、で視認性を上げる。
- リスク: simple-term-menu はメニュー項目内の ANSI エスケープで表示幅計算や
  ハイライトバーがずれる場合がある。
- 方針: **実装時に実機（Unix TTY）で検証**し、
  - 桁揃え・ハイライト・検索が破綻しない → 色付きで採用
  - 破綻する → STATUS はプレーンに自動デグレード（機能優先・色は諦める）
- 色付けの有無に関わらず矢印 / 番号 / 検索の核機能を最優先で保証する。
- 非 TTY フォールバックの `_print_table` は従来どおりプレーン（パイプ安全）。

### 4.4 フォールバック / 堅牢性

- 非 TTY（stdin/stdout いずれかが非 TTY）: 既存 `isatty` ガードで `_print_table` 表示（現状維持）。
- `import simple_term_menu` 失敗時: `logger.warning` の上で `_fallback_select`（現行 input 方式）へ。
  → simple-term-menu 未同期環境でも従来どおり番号入力で選択可能。
- 非対話（EOFError）/ Ctrl+C / 空入力 / 範囲外: 現行と同じ中止・再入力挙動を維持。

## 5. テスト設計

`tests/cli/test_project_list.py` を更新する。

- 主経路（TUI）:
  - `_show_menu` を monkeypatch して index を返す → `cmd_project up <name>` が正しい name で呼ばれる。
  - `_show_menu` が None → 中止（戻り値 0、`cmd_project` 未呼出）。
  - `_build_menu_entries`: 先頭 9 件に `[1-9]` 付与 / 10 件目以降は無印 / name 対応が一致。
- フォールバック経路:
  - `import simple_term_menu` を失敗させ（`monkeypatch` で ImportError 注入）、
    `builtins.input` 経由で選択 → `cmd_project up` 起動（既存 input テストをこちらに移設）。
- 非 TTY: 既存の table フォールバックテストを維持。

色付けの ANSI 有無はユニットテストでは検証しづらいため、`_build_menu_entries` は
「色付け関数を差し替え可能」または「name 抽出は色と独立」に設計し、テストは name/index の
対応とショートカット付与に集中する。色の見た目は実機検証（手動）で確認する。

## 6. 受け入れ基準

- `devbase list`（TTY）で矢印上下移動・`[1-9]` ジャンプ・`/` 検索・Enter で `up` 起動ができる。
- ESC / `q` で何も起動せず終了（戻り値 0）。
- 非 TTY（`devbase list | cat` 等）で従来どおりプレーンなテーブルが出る。
- simple-term-menu 不在でも番号入力で選択できる（フォールバック）。
- 既存・追加テストが green。

## 7. 非対象（YAGNI）

- 複数選択 / 一括 up。
- Windows ネイティブ対応。
- 多桁番号の任意行直接ジャンプ（`/` 検索で代替）。
- preview pane（将来拡張余地として残すが本変更では実装しない）。
