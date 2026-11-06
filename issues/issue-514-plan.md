# #514: リポジトリ・プラグイン・プロジェクトの新規追加コマンドと TUI の操作

## 関連リンク

- 課題: #514（写しは `issues/issue-514-requirements.md`）
- 設計: `issues/issue-514-design.md`（決定 1〜11、不変条件 I1〜I9、「経路ごとの反映と登録」、「テスト設計」）

## モード

`standard`（設計の「当たるモード」のとおり。公開の CLI と TUI が増え、GitHub へ書く）

## 目的と非目的

達成したい状態:

- 引数 1 つのコマンドか `devbase list` の TUI で、権限の確認からマージ、手元の取り込みと登録までが終わる

やらないこと:

- `devbase up` の実行、`env` の値の登録、フックや `Dockerfile` の雛形の出し分け
- プラグインの `description` の書き足し、GitHub 以外のホスト、Organization の作成、`gh` の導入の代行
- #516（大文字小文字の違う URL の二重登録）

## 前提

- 前提 1: テストは実の GitHub に触らない。偽の `gh` と手元の bare リポジトリ（`url.<base>.insteadOf`）で通す
- 前提 2: 手元の `plugins.yml`・`repos/`・`projects/` の実物に触らない。テストは一時のルートで組む

## 受け入れ条件

`issues/issue-514-requirements.md` の 1〜22 をそのまま使う。検証手段は「テスト設計」の表の行で、下の各タスクへ割り当てる。

## 代替案と採否

設計の決定 1〜11 のとおり。実装の計画で新たに選んだものは次の 2 つである。

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 作成フローのテストは実の `git` と偽の `gh` の実行ファイルで通す | 採用 | `gh` と `git` の引数と順序を実物の push・マージの結果で縛れる |
| B | `subprocess.run` をモックする | 不採用 | 手元の作業ツリーへ書かないこと（I4）や push の順序を結果で確かめられない |
| C | 既定ブランチの `registry.yml` と既存のパスは `gh api` の contents で読む | 採用 | 計画の確定の前に手元へ何も作らない（I1）。手元の clone が古くても重複を見つけられる |
| D | 計画の段階で一時の clone を作って読む | 不採用 | 確認の前に時間のかかる clone が要る |

## 不変条件

設計の I1〜I9 をそのまま使う。

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 公開インタフェース（コマンド） | `plugin repo new`・`plugin new`・`project new` | 追加のみ |
| TUI | プラグインのメニューの階層 | 変わる（受け入れ条件 19） |
| データスキーマ | `plugins.yml` | 変えない |

## 修正対象

- 新規: `lib/devbase/plugin/{github,scaffold,publish,creator}.py`
- 変更: `lib/devbase/plugin/repo_manager.py`、`lib/devbase/commands/{plugin,project}.py`、`lib/devbase/cli.py`、`lib/devbase/tui/actions_plugin.py`、`etc/_devbase`、`etc/devbase-completion.bash`
- 文書: `docs/user/cli-reference/`、`docs/plugin-dev/`、`docs/glossary/glossary.json`、`docs/glossary.md`、`CHANGELOG.md`
- テスト: `tests/plugin/`（作成フロー）、`tests/cli/`（解決・補完）、`tests/cli/tui/test_actions_plugin.py`

## タスク分解

### Task 1: 引数の解決と `gh` の呼び出し

- **対象ファイル:** `lib/devbase/plugin/github.py`、`tests/plugin/test_github.py`
- **変更内容:** `GitHubRef.parse`、`GhClient`（認証・権限・作成の可否・読み取り・作成・PR・マージ）
- **満たす受け入れ条件:** 5、14
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 2: 雛形と `registry.yml` の追記

- **対象ファイル:** `lib/devbase/plugin/scaffold.py`、`tests/plugin/test_scaffold.py`
- **変更内容:** 3 種類の雛形の文字列、書式を保つ 1 項目の追記と読み直しの検証
- **満たす受け入れ条件:** 6、9、I3
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 3: 登録の照合と早送りの取り込み

- **対象ファイル:** `lib/devbase/plugin/repo_manager.py`、`tests/plugin/test_repo_manager_creation.py`
- **変更内容:** `_find_registered`・`_pull_ff_only` の追加。既存の関数は変えない
- **満たす受け入れ条件:** 16、I4、I8、決定 9
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 4: 一時の clone から既定ブランチへの反映

- **対象ファイル:** `lib/devbase/plugin/publish.py`、`tests/plugin/creation_harness.py`、`tests/plugin/conftest.py`、`tests/plugin/test_publish.py`
- **変更内容:** 最初のコミットの直接の push、ブランチ・PR・マージ。偽の `gh` と bare リポジトリの土台
- **満たす受け入れ条件:** 1、2、16、17、I7
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 5: `devbase plugin repo new`

- **対象ファイル:** `lib/devbase/plugin/creator.py`、`lib/devbase/commands/plugin.py`、`lib/devbase/cli.py`、`tests/plugin/test_create_repo.py`
- **変更内容:** `plan_repo`・`execute`・計画の確認と `--yes`、パーサと `SUBCMD_MAP`・`repo_subcmds`
- **満たす受け入れ条件:** 1〜5、14、15、17、I1、I2、I5、I7
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 6: `devbase plugin new`

- **対象ファイル:** `lib/devbase/plugin/creator.py`、`lib/devbase/commands/plugin.py`、`lib/devbase/cli.py`、`tests/plugin/test_create_plugin.py`
- **変更内容:** `plan_plugin`、未登録の 4 状態、登録済みの経路、登録の clone の確かめ
- **満たす受け入れ条件:** 6、7、8、16、17、I2、I8、I9、決定 8、決定 9
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 7: `devbase project new`

- **対象ファイル:** `lib/devbase/plugin/creator.py`、`lib/devbase/commands/project.py`、`lib/devbase/cli.py`、`tests/plugin/test_create_project.py`、`tests/cli/test_project_new_dispatch.py`
- **変更内容:** 入れ先の決定、グループの決定、`plan_project`、`_NO_SECRET_INJECTION`
- **満たす受け入れ条件:** 9〜13、22、I6、I9
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 8: シェル補完

- **対象ファイル:** `etc/_devbase`、`etc/devbase-completion.bash`、`tests/cli/test_completion.py`
- **変更内容:** 3 つのコマンドとオプションの候補
- **満たす受け入れ条件:** 目的の「シェル補完」
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 9: TUI のメニューの組み直しと 3 つの入口

- **対象ファイル:** `lib/devbase/tui/actions_plugin.py`、`tests/cli/tui/test_actions_plugin.py`
- **変更内容:** 第 1 階層を用途で分け、新規追加の入力の順を設計のとおりに置く
- **満たす受け入れ条件:** 18、19、20
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 10: 文書

- **対象ファイル:** `docs/user/cli-reference/`、`docs/plugin-dev/`、`docs/glossary/glossary.json`、`docs/glossary.md`、`CHANGELOG.md`
- **変更内容:** コマンドのリファレンス、プラグインリポジトリの定義、用語集の出所、変更履歴
- **満たす受け入れ条件:** 影響の「公開インタフェース」
- **進め方:** テスト駆動を適用しない（文書だけ。`glossary.py check` と固有の語の検査で確かめる）

## 影響範囲

- `devbase plugin`・`devbase plugin repo`・`devbase project` の接頭辞の解決と補完
- `devbase list` のプラグインのメニュー

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `SUBCMD_MAP` と補完の一致テストに新しい値が当たる | タスクごとにテストを通す |
| `actions_plugin.py` のメニューの変更で既存のテストの前提が変わる | タスクごとにテストを通す。`_run_operation`・`_run_repo_operation` の契約は残す |
| `repo_manager.py` の既存の経路を壊す | 足すだけにして既存の関数を変えない |

## 切り戻し手順

- 追加のコマンドは該当のコミットを revert すれば消える。`plugins.yml` の形は変わらないので、データの戻しは要らない

## 完了の定義

- [ ] 受け入れ条件 1〜22 と I1〜I9 が「テスト設計」の行のテストに対応している
- [ ] `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q`、`uvx ruff check --select=E9,F63,F7,F82 lib`、`python3 .github/scripts/proper_term_check.py` が通る
- [ ] 実の GitHub での完走は、リリース後テストで利用者が確かめる（テストからは書かない）
