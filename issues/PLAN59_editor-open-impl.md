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

- [ ] 受け入れ条件 1〜20 をすべて満たし、条件ごとにテストか手動確認の結果が対応している
- [ ] `uv run pytest` が exit=0
- [ ] 手動確認（起動中で窓が開きコンテナが変わらない / 停止中で起動から開く / TUI の先頭が `open`）の結果を Pull Request に書いた
