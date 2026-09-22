# PLAN66 実装 1 本目: 名前の形に合わないプロジェクトを、作られた時点で知らせる

## 関連リンク

- 課題: devbasex/devbase#203
- 要求と受け入れ条件: `issues/PLAN66_project-name-validation.md`
- 設計: `issues/PLAN66_project-name-validation-design.md`（設計 PR #230。弾かずに警告に留める案を承認済み）
- release PR: #212（base は `release/v3.7.0`）
- この計画が扱うのは設計の「実装の分け方」の **1 本目（知らせ。F1・F2）だけ**である。2 本目（スナップショットの
  名前を `utils/names` の述語へ寄せる。F3・決定 5・6）はこの Pull Request のマージ後に別に出す

## モード

`standard`（要求の文書の判定のまま。出力が増えるだけで、終了コードと作られるものは変えない）。

## 目的と非目的

達成したい状態:

- `devbase plugin install` / `update` / `sync` が、名前の形に合わない名前を `projects/` に載せる直前に
  1 行知らせる（プラグインのプロジェクト・合成した別名・`projects/` 直下の実ディレクトリ）
- `devbase env import` が、名前の形に合わないプロジェクト名を取り込むとき、保存先に応じた文で 1 行知らせる
  （`--dry-run` でも出す）
- 名前の形の説明文を `utils/names.NAME_FORM_HINT` の 1 か所に置く

やらないこと:

- 名前を弾く・載せない・整える（決定 1）。終了コードと作られる symlink・ディレクトリは変えない
- `discover_projects` と `_collect_project_candidates` での検査（決定 2）・`.` 始まりの除外の変更（決定 8）
- 下流の検証（`bin/devbase`・`cli.py`・`commands/container.py`）の変更（決定 3）
- `env/secret_store.py`・`env/bundle.py`・`_PROJECT_ENV_RE` の変更（決定 4。G4 の束 #188 との重なりを避ける）
- `snapshot/manager.py` の変更と確定仕様「運用」の 2 つ目の箇条書き（2 本目）

## 受け入れ条件

要求の文書の番号をそのまま使う。この Pull Request が満たすのは 1〜9・13 の 1 つ目・14 の Added・15〜17 である。

- [ ] 1・2・3・3-2・4・5（同期）: `tests/plugin/test_repos_core.py` に新しいテストクラスを足す
- [ ] 6・7・8（import の平文）: `tests/env/test_io_import.py` へテストを足す
- [ ] 9（import の age）: `tests/cli/test_env_bundle_backend.py` へテストを足す
- [ ] 13 の 1 つ目: `docs/specifications/cli-argument-resolution.md` の「運用」の 1 つ目の箇条書き
- [ ] 14 の Added: `CHANGELOG.md` の `[Unreleased]`
- [ ] 15・16: 既存のテストを変更せずに通す
- [ ] 17: `uv run --locked pytest tests/ -q` が exit=0

## 修正対象

- `lib/devbase/utils/names.py`（`NAME_FORM_HINT` を足す）
- `lib/devbase/plugin/syncer.py`（`_warn_unusable_name` を足し、`sync_projects` の 2 か所と `_link_loser_projects` から呼ぶ）
- `lib/devbase/env/_import_merge.py`（`project_name_of` を足す）
- `lib/devbase/env/io_import.py`（`import_bundle` の `_build_plans` の直後、`--dry-run` の判定より前に知らせる）
- `tests/plugin/test_repos_core.py`・`tests/env/test_io_import.py`・`tests/cli/test_env_bundle_backend.py`
- `docs/specifications/cli-argument-resolution.md`・`CHANGELOG.md`

## タスク分解

### Task 1: 同期の知らせ（F1）

- **対象ファイル:** `lib/devbase/utils/names.py`・`lib/devbase/plugin/syncer.py`・`tests/plugin/test_repos_core.py`
- **変更内容:** `NAME_FORM_HINT` を足す。`_warn_unusable_name(name, source, base=None)` を足し、出所ごとに
  案内を選ぶ（設計「警告の文」の表の 4 行）。`sync_projects` で `sorted(real_projects)` の名前ごとと、winner の
  symlink の直前に呼ぶ。`_link_loser_projects` で別名の symlink の直前に、元のプロジェクト名を `base` として呼ぶ。
  `verbose` に依存させない
- **満たす受け入れ条件:** 1・2・3・3-2・4・5・15
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 2: import の知らせ（F2）

- **対象ファイル:** `lib/devbase/env/_import_merge.py`・`lib/devbase/env/io_import.py`・`tests/env/test_io_import.py`・`tests/cli/test_env_bundle_backend.py`
- **変更内容:** `project_name_of(arcname)` を足す。`import_bundle` で `plans` を回し、形に合わない名前に 1 行知らせる。
  保存先が `projects/<名前>/.env`（`plan.ref is None` かつ `plan.target` がそのパス）なら「この import が
  `projects/<名前>/` を作る」文、それ以外（age・サーバ backend）は保存先を名指しして `projects/` に何も作らない文
- **満たす受け入れ条件:** 6・7・8・9・16
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 3: 確定仕様と CHANGELOG

- **対象ファイル:** `docs/specifications/cli-argument-resolution.md`・`CHANGELOG.md`
- **変更内容:** 「運用」の 1 つ目に、知らせが出ること・4 つの出所・弾かないことを足す。CHANGELOG の Added に F1・F2
- **満たす受け入れ条件:** 13 の 1 つ目・14 の Added
- **進め方:** 文書のためテスト駆動を適用しない

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `sync_projects(verbose=False)` を数える用途（`updater`）でも警告が出る | 設計の決定どおり（黙る経路を作らない）。`updater` の差分計算は `discover_projects` を使い、`sync_projects` の出力には触れない |
| 警告の文が長く、`caplog` の件数が他の WARNING と混ざる | テストは名前の形の行を定型の先頭文で絞って数える |
| 触る範囲は 4 ファイルで、どれもテストが厚い | 実装の後の構造改善で足りる |

## 切り戻し手順

コードの差分は知らせの追加だけで、データ・スキーマを持たない。この Pull Request の revert で完全に戻る。

## 完了の定義

- [ ] 受け入れ条件 1〜9 がテストで確かめられ、15〜17 の全件が exit=0
- [ ] Draft の Pull Request の本文に Test plan と実行結果を載せる
