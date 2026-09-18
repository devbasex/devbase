# PLAN59: `devbase open` の実装計画

## 関連リンク

- issue: devbasex/devbase#197
- 要求仕様と受け入れ条件: [PLAN59_editor-open.md](PLAN59_editor-open.md)
- 設計: [PLAN59_editor-open-design.md](PLAN59_editor-open-design.md)（設計 Pull Request #198 でマージ済み）

## モード

`standard`（公開のコマンドと TUI のメニュー項目を足し、CLI・lifecycle・TUI・入口のシェル・補完にまたがる）

## 受け入れ条件

仕様の 1〜20 をそのまま使う。番号は仕様の番号を指す。

## 修正対象

- `lib/devbase/utils/docker.py`
- `lib/devbase/commands/env.py`
- `lib/devbase/commands/container.py`
- `lib/devbase/cli.py`
- `bin/devbase`
- `lib/devbase/tui/actions_project.py`
- `etc/devbase-completion.bash` / `etc/_devbase`
- `docs/user/cli-reference/02-project.md` / `CHANGELOG.md`
- テスト: `tests/utils/test_running_dev_instances.py`（新規）/ `tests/commands/test_container_open.py`（新規）/ `tests/cli/test_open_command.py`（新規）/ `tests/cli/tui/test_open_menu.py`（新規）/ `tests/cli/test_completion.py`

## タスク分解

### Task 1: 動いている dev インスタンスの列挙を共有の場所へ移す

- **対象ファイル:** `lib/devbase/utils/docker.py`、`lib/devbase/commands/env.py`、`tests/utils/test_running_dev_instances.py`
- **変更内容:** `env.py` の `_running_dev_containers` の中身を `running_dev_instances(project, dev_service_name, runner=None) -> Optional[list[tuple[int, str]]]` として `utils/docker.py` へ移す。`_running_dev_containers` はコンテナ名だけを返す包みにする（設計の決定 8）
- **満たす受け入れ条件:** 20 の前半（失敗で `None`）。`env token` の既存テストが変更なしで通ること
- **進め方:** 失敗するテスト（`{dev}-{数字}` 以外を除く・index 順・失敗で `None`）→ 移設 → `tests/commands/test_env_token.py` を通す

### Task 2: `cmd_open` 本体

- **対象ファイル:** `lib/devbase/commands/container.py`、`tests/commands/test_container_open.py`
- **変更内容:** `_maybe_open_editor` から `_open_editor_at` を切り出す（決定 3）。`cmd_open(project_name=None, open_index=None, context=None)` を足し、`_dispatch_lifecycle` の `handlers` に `'open'` を加える。処理は設計の「処理の流れ」の順
- **満たす受け入れ条件:** 1〜8、10、19、20
- **進め方:** 条件ごとに失敗するテストを書き、`running_dev_instances`・`opener.open_editor`・`cmd_up` を差し替えて分岐を確かめる → 実装 → `up` の自動オープンの既存テストが通ることを確かめる

### Task 3: CLI の登録と入口のシェル

- **対象ファイル:** `lib/devbase/cli.py`、`bin/devbase`、`tests/cli/test_open_command.py`
- **変更内容:** `_add_open_subparser(sub, *, with_name)` を足し、`project`（name あり）・`container`（name なし）・トップレベル（name あり）へ登録する。`SHORTCUTS` / `SUBCMD_MAP` / epilog に `open` を足す。`bin/devbase` の `resolve_command` の候補・Python 実装のコマンドの `case`・`_PROJECT_NAME_SUBCOMMANDS`・`_NAME_RESOLVABLE_SHORTCUTS` に `open` を足す
- **満たす受け入れ条件:** 9、11、16
- **進め方:** parse の結果・`--open` の拒否・前方一致・wrapper のリストを見る失敗するテスト → 登録 → 既存の `tests/cli/` を通す

### Task 4: TUI の起動中メニュー

- **対象ファイル:** `lib/devbase/tui/actions_project.py`、`tests/cli/tui/test_open_menu.py`
- **変更内容:** `_RUNNING_OPS` の先頭に `("エディタを開く (open)", "open")`、`_OP_HANDLERS["open"]` を足す。先頭の理由のコメントを書き替える
- **満たす受け入れ条件:** 12〜14
- **進め方:** 並び・ハンドラ・`_BACK_TO_TOP_OPS` を見る失敗するテスト → 変更 → 既存の TUI テスト（先頭が `up` を前提にしたものがあれば、先頭に `open` が入った並びへ直す）

### Task 5: 補完と文書

- **対象ファイル:** `etc/devbase-completion.bash`、`etc/_devbase`、`tests/cli/test_completion.py`、`docs/user/cli-reference/02-project.md`、`CHANGELOG.md`
- **変更内容:** bash / zsh の候補に `open` を足し、`[name]` の補完を `up` と同じにする。CLI リファレンスに `open` の節を足し、`devbase list` の起動中メニューの先頭が変わったことを書く。CHANGELOG の Unreleased に追記する
- **満たす受け入れ条件:** 17
- **進め方:** 補完の候補を見る失敗するテスト → 補完の変更。文書はテスト駆動の対象外（振る舞いを持たないため）

### Task 6: 全体の確認

- **満たす受け入れ条件:** 15、18
- **進め方:** `uv run pytest` を通す。起動中・停止中のプロジェクトで `devbase open` を手で動かし、仕様の「手動確認」を行う

## 影響範囲

- `devbase env token`: 列挙の関数が包みになる（振る舞いは変えない）
- `devbase up` の `[6/6]`: 開く処理が `_open_editor_at` を通る（振る舞いは変えない）
- `devbase list` の起動中サブメニュー: 先頭のハイライトが `open` になる

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `container.py`（2200 行超）への追加 | 触る範囲は `_maybe_open_editor` の切り出しと関数 2 つの追加に限られ、`up` の自動オープンに既存テストがある。タスクごとにテストを通す |
| `cli.py` と `bin/devbase` と補完の同期漏れ | Task 3・5 のテストで 3 か所に `open` があることを見る |
| TUI の既存テストが先頭を `up` と決め打ちしている | Task 4 で洗い出して直す |

## 切り戻し手順

- 追加のみでデータ移行を持たないため、この Pull Request を revert すれば元へ戻る

## 完了の定義

- [x] 受け入れ条件をすべて満たし、条件ごとにテストか手動確認の結果が対応している（下の表。TTY で窓が開くことはリリース後の確認へ回す）
- [x] `uv run pytest` が exit=0
- [x] 手動確認の結果を Pull Request に書いた

## 検証結果

head 3af259b に対して実行した。

| 段階 | コマンド | 対象範囲 | 実行時刻 | 結果 |
| --- | --- | --- | --- | --- |
| 限定的な検証 | `uv run pytest -q tests/commands/test_container_open.py tests/cli/test_open_command.py tests/cli/tui/test_open_menu.py tests/utils/test_running_dev_instances.py tests/cli/test_completion.py` | 追加・変更したテスト | 2026-09-18 17:48 | 101 passed / exit=0 |
| 全体テスト | `uv run pytest -q` | 全体 | 2026-09-18 17:48 | 2682 passed / exit=0 |
| 静的解析 | `uvx ruff check --select=E9,F63,F7,F82 lib`（CI と同じ条件） | `lib` | 2026-09-18 17:50 | exit=0 |
| 構文 | `python3 -m compileall -q lib bin` / `bash -n bin/devbase` / `zsh -n etc/_devbase` | 全体 | 2026-09-18 17:50 | いずれも exit=0 |
| CI | Python syntax check (3.10 / 3.11 / 3.12)・Ruff lint・ShellCheck | 全体 | 2026-09-18 | 5 件 pass |
| 実機（非 TTY） | nyle-dx（dev-1 が起動中）で `open` / `open --open-index 3` / `open --context no-such-ctx` / `open --open 2` / `project open --open-index 2` | 起動中の経路と失敗の経路 | 2026-09-18 17:50 | exit=1（非 TTY で skip）/ 1 / 1 / 2 / 1。前後で `nyle-dx-dev-1` の ID と `StartedAt` は変わらない |

カバレッジ: `pyproject.toml` に閾値の設定が無いため測っていない。

| 受け入れ条件 | 確かめたもの |
| --- | --- |
| 1 | `test_running_opens_the_editor_without_touching_containers`、実機（コンテナ不変） |
| 2 | `test_open_ignores_the_auto_open_switch` |
| 3・4 | `test_stopped_delegates_to_up_with_open` / `test_stopped_opens_even_when_auto_open_is_disabled` |
| 5 | `test_index_not_running_is_an_error`、実機（`--open-index 3`） |
| 6 | `test_index_beyond_project_yml_scale_is_accepted_when_running` |
| 7 | `test_index_below_one_is_an_error_before_docker` |
| 8 | `test_index_from_env_is_used` / `test_up_reads_the_same_env_index_as_open` |
| 9 | `test_shortcut_dispatches_to_cmd_open` / `test_project_open_dispatches_to_cmd_open` / `test_wrapper_*_open_name_cds_and_strips` |
| 10 | `test_context_reaches_the_state_query_and_the_editor` |
| 11 | `test_open_rejects_the_auto_open_flags` / `test_open_rejects_abbreviations_of_open_index`、実機（`--open 2` → exit=2） |
| 12〜14 | `tests/cli/tui/test_open_menu.py` / `test_select_action_lists_all_ops` |
| 15 | `test_up_auto_open_*` と既存の `tests/editor` / `tests/cli/test_up_roundtrips.py` |
| 16 | `test_prefix_resolution` |
| 17 | `tests/cli/test_completion.py` の `test_bash_open_*` / `test_zsh_completion_mentions_open` |
| 18 | 全体テスト |
| 19 | `test_skip_is_a_failure` / `test_print_command_is_a_success`、実機（非 TTY で exit=1） |
| 20 | `test_state_query_failure_does_not_start_up`、実機（`--context no-such-ctx`） |

- 未検証の項目: TTY の端末から `devbase open` で窓が開くこと、`devbase list` の起動中の行で先頭が `open` であること（利用者のデスクトップに窓を出すため、リリース後の確認で行う）
- 既存の失敗: なし
- 範囲外と判断したもの: `container` / `ct` のサブコマンドが wrapper 経由で `[name]` を受け付ける既存の動き（#200）
