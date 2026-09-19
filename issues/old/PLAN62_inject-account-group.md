# PLAN62: 機密の置き場に書いた `DEVBASE_ACCOUNT_GROUP` を注入しない

対象 issue: devbasex/devbase#185

- ワークフローモード: `standard`
  - 根拠: 機密の注入（`runtime.resolve` / `inject`）の本番の振る舞いを変える。`version: 1` の挙動も変わる

## 依頼（原文）

> `runtime.inject()` は機密の置き場（`.env` / age / OpenBao）の値をそのままプロセスの環境変数へ載せる。置き場に `DEVBASE_ACCOUNT_GROUP` が入っていると、`volume.manager.resolve_account_group()`（プロセスの環境変数を読む）が決めるボリュームのグループがその値に変わる。
>
> - プロジェクトの `env` がグループを宣言していれば、ラッパーの `source` が後から上書きするので起きない
> - `$DEVBASE_ROOT/env` での宣言や、宣言が無いプロジェクトでは起きうる
> - `version: 1`（今の設定）でも同じ経路がある。PLAN56（#182 / #184）の `layout: group` では、機密の置き場のグループはファイルだけから決めるため、この場合は `up` / `scale` がボリュームとのグループの食い違いで止まる
>
> 注入の対象から `DEVBASE_ACCOUNT_GROUP` を外すかどうかは、`version: 1` の挙動を変えるため別に判断する。

## 目的

- アカウントグループ（ボリューム `devbase_home_<group>` と、`layout: group` での機密の置き場）を決める値が、
  `env` ファイル（`projects/<name>/env` / `$DEVBASE_ROOT/env`）とシェルの環境変数からだけ来るようにする。
  機密の置き場に書いた値が、どの設定（`version: 1` / `layout: group`）でもグループを変えないようにする

## 前提

- 前提 1: **注入の対象から外す**（issue の「外すかどうか」への答え）。`version: 1` でも外す。
  理由: 利用者向け文書（`docs/user/environment-variables.md` の「機密の置き場もグループで分ける（OpenBao）」と
  `docs/user/env-backend.md` の「グループの決まり方」）は PLAN56 以降
  「置き場に書いた値はグループの決定に使われない」と説明しており、`version: 1` だけ置き場の値が効く今の
  挙動は文書と食い違っている。置き場の値でグループを切り替える使い方は文書に無い
- 前提 2: 外す場所は機密の合成（`runtime.resolve`）の 1 か所にする。`inject`・`child_env`・コンテナへ
  列挙する変数名は、どれも `resolve` の結果から作られるため、1 か所で全経路に効く
- 前提 3: 置き場に書かれた値を見つけたときは、無視したことを警告で 1 回知らせ、消し方
  （`devbase env delete DEVBASE_ACCOUNT_GROUP`、置き場に応じて `-p` / `--user` / `--group`）を示す。
  値そのものは出さない
- 前提 4: `devbase env set DEVBASE_ACCOUNT_GROUP=...` は置き場へ書かずに拒否し、`env` ファイルへ書くよう案内する
  （終了コード 1）。書いても使われない値を新たに作らないため。`env import` / `env edit` / 既存の値は拒否しない
  （前提 3 の警告で知らせる）
- 前提 5: コンテナ内の `DEVBASE_ACCOUNT_GROUP` は今と同じく `volume/compose.py` が構成へ書く値
  （解決済みのグループ）で決まる。置き場の値がコンテナへ列挙されることは無くなる

## 対象範囲

含む:

- 機密の合成（`runtime.resolve`）から `DEVBASE_ACCOUNT_GROUP` を外すことと、その警告
- `devbase env set` での拒否
- 利用者向け文書（`docs/user/environment-variables.md` / `docs/user/env-backend.md` / `docs/user/cli-reference/03-env.md` の `env set`）の該当箇所と CHANGELOG

含まない:

- `DEVBASE_ACCOUNT_GROUP` 以外の「置き場に書くべきでない」キーの扱い（`DEVBASE_ROOT` など。必要なら別の issue）
- `env import` / `env edit` での拒否
- 置き場に残っている値の自動削除
- ボリュームのグループの決め方（`resolve_account_group`）と、`layout: group` の機密のグループの決め方の変更
- 新しい型・永続データ・画面の追加（そのためクラス図・ER 図・画面遷移図を作らない）。要求に非機能の条件は無い（注入の要求の回数・経路を変えない）

## 受け入れ条件

- [x] 1. 前提: チーム共通の機密（`version: 1`・`plaintext` / `age` backend）に `DEVBASE_ACCOUNT_GROUP=kkg` があり、
      プロセスの環境変数に `DEVBASE_ACCOUNT_GROUP` が無い
      操作: `runtime.inject(root, project)` を呼ぶ
      結果: プロセスの環境変数に `DEVBASE_ACCOUNT_GROUP` が載らず、`resolve_account_group()` は `default` を返す
      検証: `tests/env/test_runtime.py::test_inject_does_not_put_the_stores_account_group_into_the_environment[age|plaintext]`
- [x] 2. 前提: 1 と同じ置き場で、プロセスの環境変数に `DEVBASE_ACCOUNT_GROUP=with` がある（`env` ファイル由来）
      操作: `runtime.inject(root, project)` を呼ぶ
      結果: プロセスの環境変数は `with` のまま変わらない
      検証: `tests/env/test_runtime.py::test_inject_keeps_the_environments_account_group[age|plaintext]`、`::test_inject_keeps_the_projects_env_declaration`
- [x] 3. 個人共通・プロジェクトのチーム・プロジェクトの個人の置き場にあっても、1・2 と同じになる
      検証: `tests/env/test_runtime.py::test_every_store_layer_is_dropped_from_the_environment[4 層]`、`::test_every_store_layer_loses_to_the_environment[4 層]`
- [x] 4. 置き場にあったときは、キー名と置き場の種類・消し方を含む警告が出て、値は出ない。
      1 回の CLI 起動で同じ置き場について 2 回以上出ない
      検証: `tests/env/test_runtime.py::test_warns_once_with_the_store_and_how_to_delete[4 種]`、`::test_warning_for_a_grouped_reference_names_the_group`、`::test_warning_for_a_grouped_global_reference_still_adds_the_group`、`::test_warning_is_not_repeated_across_stores_and_release`、`::test_each_reference_warns_separately`、`::test_empty_value_still_warns_and_no_key_does_not`
- [x] 5. `resolve()` の結果（`SecretEnv.names` / `values`）に `DEVBASE_ACCOUNT_GROUP` が含まれない。
      したがって dev コンテナの `environment` へ置き場の値は列挙されない
      検証: `tests/env/test_runtime.py::test_resolve_never_lists_the_account_group`、`::test_child_env_does_not_carry_the_stores_account_group`
- [x] 6. `layout: group` で、置き場（`$DEVBASE_ROOT/env` にも `projects/<name>/env` にもグループの宣言が無い
      プロジェクトの、共通の機密）に `DEVBASE_ACCOUNT_GROUP=kkg` があっても、`devbase up` の前の
      グループの食い違いの検査で止まらない
      検証: `tests/commands/test_container_up_order.py::test_store_account_group_does_not_stop_the_group_check`
- [x] 7. `devbase env set DEVBASE_ACCOUNT_GROUP=kkg`（`-p` / `--user` / `--group` 付きも同じ）は、置き場へ書かずに
      `env` ファイルへ書くよう案内して終了コード 1
      検証: `tests/commands/test_env_account_group.py::test_set_refuses_the_account_group_without_opening_the_store[6 組]`、`::test_set_refuses_the_account_group_without_creating_the_file[global|project]`、`::test_set_refuses_the_account_group_with_surrounding_spaces`、`::test_set_of_another_key_still_writes`
- [x] 8. 他のキーの注入（重ね順・注入の履歴・`clear_injected` による巻き戻し）の既存テストが変更なしで通る
      検証: `tests/env/test_runtime.py`（既存 38 件は追記のみで本文の変更なし）・`tests/env/test_runtime_store.py`・`tests/cli/test_secret_injection.py`（変更なし）が全体テストで通過
- [x] 9. 全体テスト（`uv run pytest tests/`）が通る
      検証: `uv run --locked pytest -q tests/` → 2717 passed（exit 0）、`ruff check --select=E9,F63,F7,F82 lib` → All checks passed（exit 0）

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる: `env set DEVBASE_ACCOUNT_GROUP=...` が拒否される。CHANGELOG に「変更」として書く |
| データ | 置き場の値は消さない |
| 既存の振る舞い | `version: 1` で置き場に `DEVBASE_ACCOUNT_GROUP` を書いていた利用者は、ボリュームのグループが `env` ファイルの宣言（無ければ `default`）へ戻る。警告で知らせる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest tests/ -q` |
| 静的解析 | `ruff check --select=E9,F63,F7,F82 lib` |
| 手動確認 | 検証用のプロジェクトで置き場に値を入れ、`devbase ps` 等で警告が出ること、`devbase env set` が拒否することを見る（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 注入の規則は `lib/devbase/env/runtime.py`、キー名は `lib/devbase/env/keys.py` |
| テスト戦略 | `resolve` / `inject` は `tests/env/` の単体テストで、差し替えの `SecretStore` を使う。`env set` は `tests/cli/` または `tests/env/` の既存の形に合わせる。`DEVBASE_ROOT` は tmp へ向ける |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体テスト |
| 確認してから行う | 前提 1（`version: 1` の挙動を変えること。設計 Pull Request の承認で確かめる） |
| 行わない | 置き場の値の自動削除、他のキーの扱いの変更 |

## 実装計画

設計は [PLAN62_inject-account-group-design.md](PLAN62_inject-account-group-design.md)。タスクはその「構成要素」と「テスト設計」から導く。どのタスクも失敗するテスト → 通す最小実装 → 整理の順で進める。

### 修正対象

- `lib/devbase/env/runtime.py`（`_warned_account_group_refs` / `_without_account_group` の新設、`resolve` の変更）
- `lib/devbase/commands/env.py`（`cmd_env_set` の拒否）
- `lib/devbase/env/keys.py`（コメント）
- `tests/env/test_runtime.py`（受け入れ条件 1〜5）
- `tests/commands/test_container_up_order.py`（受け入れ条件 6）
- `tests/commands/test_env_account_group.py`（新設。受け入れ条件 7）
- `docs/user/environment-variables.md` / `docs/user/env-backend.md` / `docs/user/cli-reference/03-env.md` / `CHANGELOG.md`

### Task 1: [x] `resolve` が 4 つの置き場の `DEVBASE_ACCOUNT_GROUP` を合成しない

- **対象ファイル:** `lib/devbase/env/runtime.py`、`tests/env/test_runtime.py`
- **変更内容:** `_without_account_group(ref, data)` を新設し、`resolve` の 4 つの `store.load` の直後に通す（決定 1・6。重ね順 3 は変えない）。docstring に理由を書き足す
- **満たす受け入れ条件:** 1、2、3、5
- **進め方:** age / plaintext の `store` と `_FourLayerStore`（4 置き場を parametrize）で、`inject` 後の `os.environ` と `resolve_account_group()`、`resolve` の `names` / `global_names` / `project_names` / `values` を確かめるテストを先に書く → `resolve` を変えて通す

### Task 2: [x] 外したことを置き場ごとに 1 回だけ警告する

- **対象ファイル:** `lib/devbase/env/runtime.py`、`tests/env/test_runtime.py`
- **変更内容:** モジュール変数 `_warned_account_group_refs`（`SecretRef` の集合）を新設し、`_without_account_group` がキーを見つけた参照について 1 回だけ `logger.warning` を出す。文言は設計「警告」の表どおり（`label()`、`-p` / `--user` / `--group` の引数、プロジェクトの参照なら実行場所）。値は出さない
- **満たす受け入れ条件:** 4
- **進め方:** caplog で 4 種の参照とグループ付きの参照の文言、`release_store` と別 `store` をまたいだ重複抑止、値が出ないことを確かめるテストを先に書く → 集合と警告を足して通す

### Task 3: [x] `layout: group` で置き場の値があっても `up` の検査が止まらない

- **対象ファイル:** `tests/commands/test_container_up_order.py`
- **変更内容:** 偽の OpenBao（`layout: group`、`default → nyle`）の `team/nyle/global` に `DEVBASE_ACCOUNT_GROUP=kkg` を置き、`runtime.inject(root, 'web')` の後に `container._check_group_consistency()` が True になることを固定する（決定 8）。実装の変更は Task 1 に含まれる
- **満たす受け入れ条件:** 6
- **進め方:** Task 1 の前に書けば失敗し、Task 1 で通る。ここでは順序どおりに呼ぶテストを足して通ることを確かめる（Task 1 の実装で満たされるため追加の実装は無い）

### Task 4: [x] `env set DEVBASE_ACCOUNT_GROUP=...` を置き場を開く前に拒否する

- **対象ファイル:** `lib/devbase/commands/env.py`、`tests/commands/test_env_account_group.py`（新設）
- **変更内容:** `cmd_env_set` でキー名が `keys.DEVBASE_ACCOUNT_GROUP` なら、`_open_target_env` より前に error ログを出して 1 を返す（決定 7）。文言は設計「コマンド `env set`」の表どおり
- **満たす受け入れ条件:** 7
- **進め方:** 偽の OpenBao で `project` / `user` / `group` の各組み合わせが 1 で POST が無いこと、ファイル backend で `.env` が作られないこと、error ログに `env` ファイルの案内があることを確かめるテストを先に書く → 早期 return を足して通す

### Task 5: [x] 文書・コメント・CHANGELOG を新しい挙動に合わせる

- **対象ファイル:** `lib/devbase/env/keys.py`、`docs/user/environment-variables.md`、`docs/user/env-backend.md`、`docs/user/cli-reference/03-env.md`、`CHANGELOG.md`
- **変更内容:** 設計「構成要素」の表のとおり。置き場の値は `version: 1` でも使われず警告が出ること、`env set` で書けないこと、`up` / `scale` の食い違いの直し方から「置き場に書いていたとき」の段落を書き替える
- **満たす受け入れ条件:** （文書。受け入れ条件には無いが要求の対象範囲「含む」）
- **進め方:** 文書のためテスト駆動を適用しない

### Task 6: [x] 全体テストと静的解析

- **対象ファイル:** なし
- **変更内容:** `uv run --locked pytest -q tests/` と `ruff check --select=E9,F63,F7,F82 lib` を実行し、終了コードを記録する
- **満たす受け入れ条件:** 8、9
- **進め方:** 既存テスト（`tests/env/test_runtime.py`・`tests/env/test_runtime_store.py`・`tests/cli/test_secret_injection.py`）を変更していないことを `git diff --stat` で確かめる

### リスクと対処

| リスク | 対処 |
| --- | --- |
| `resolve` は `inject` / `child_env` / コンテナの列挙の共通経路で、外し方を誤ると他のキーの重ね順が変わる | 読み取りの直後にキーを除くだけにし、重ね順の既存テスト（Task 6）を変更なしで通す |
| 警告の集合がテスト間で漏れ、重複抑止のテストが順序に依存する | 各テストの先頭で `monkeypatch.setattr(runtime, '_warned_account_group_refs', set())` |
| テストが実環境の `DEVBASE_ROOT`（シェルが持つ）を継承し、実の secrets / OpenBao に触る | 新しいテストは必ず `DEVBASE_ROOT` を tmp へ向け、偽の OpenBao / `_FourLayerStore` を使う |

### 切り戻し手順

- `runtime.py` と `commands/env.py` の変更を戻せば元の挙動に戻る。置き場の値は消していないため、データの巻き戻しは無い
