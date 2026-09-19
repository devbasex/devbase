# PLAN61: `bin/devbase` の位置引数の解決を、名前の形・衝突・ヘルプ・グループで正す

対象 issue: devbasex/devbase#146, devbasex/devbase#142, devbasex/devbase#196, devbasex/devbase#200

- ワークフローモード: `standard`
  - 根拠: CLI の公開インタフェース（位置引数の解釈・`build --help`・`container <sub> <name>` の受け付け）を
    変える不具合修正で、`bin/devbase`・`lib/devbase/cli.py`・`lib/devbase/commands/container.py` にまたがる
- 4 件を 1 本にする理由: どれも `bin/devbase` の name 解決と `build)` の分岐（同じ数十行）を触る。
  分けると同じ箇所で競合し、レビューした差分と入る差分が変わる

## 依頼（原文）

#146:

> `bin/devbase` の `maybe_cd_project()` は、name 候補を `$DEVBASE_ROOT/projects/<name>` へそのまま連結してディレクトリの存在を見ます。弾いているのは `-` 始まりと空文字だけで、`..` を含む値がそのまま通ります。
>
> - `maybe_cd_project` の入口で、name を 1 セグメントに限定する（`/` `\` `..` `.` を弾く）。`_build_single_image` に入れたのと同じ形の許可リストが使えます
> - あわせて `_resolve_project_name`（`lib/devbase/commands/container.py`）も同じ検証を持つ。Python 側の chdir フォールバックも同じ連結を行うため
> - `cli._named_lifecycle_project`（`lib/devbase/cli.py`）も同じ検証を持つ。

#146 のコメント（#139 の振り返り）:

> 1. **`bin/devbase` のテストに name 解決を含む経路を 1 本通す。**
> 2. **`_resolve_project_name`（`lib/devbase/commands/container.py`）も同じ検証を持たせる。**

#142:

> `devbase build <image>` の `<image>` が `$DEVBASE_ROOT/projects/` に実在する名前と一致すると、`bin/devbase` の先頭にある name 解決がその引数をプロジェクト名として消費します。イメージ指定が消え、そのプロジェクトの compose ビルドへ化けます。
>
> 対処の候補:
>
> - 衝突時に警告を出し、どちらとして解釈したかを表示する
> - `containers/<image>` が実在する場合は `build` を name 解決の対象から外す
> - `--image` のような明示フラグを足す

#196:

> `devbase build --help` / `-h` が、ビルドを 1 つも起こさずに `build` の使い方（`--no-cache` / `--project-no-cache` / `--expires[=N]` / `--context NAME` / `<image>` 指定）を出して終了する。
>
> 検査は `tests/cli/` に、`devbase build --help` の終了コードが 0 で、出力に `=== Building devbase images ===` が現れないことを見るものを 1 件足せば足りる。

#200:

> | 論点 | 案 |
> | --- | --- |
> | `container` を名前解決から外すか | 外すと `container <sub> <name>` は実在性によらず usage エラーになる。`container` は非推奨なので、`project` へ誘導する形で揃えられる |
> | 今の動きを仕様として残すか | 残すなら `container` の parser に `[name]` を足して、wrapper と parser を一致させる |

## 目的

- 位置引数として渡した名前が、`$DEVBASE_ROOT/projects/` の直下の 1 つのプロジェクトか、利用者が意図した
  別の意味（イメージ名など）のどちらかにだけ解釈され、黙って別の対象を操作しないようにする
- `devbase build --help` でビルドが始まらないようにする

## 前提

- 前提 1: プロジェクト名として受け付ける形は `[A-Za-z0-9][A-Za-z0-9._-]*` とする（`_build_single_image` の
  イメージ名と同じ許可リスト）。先頭が英数字なので `.` と `..` はこの形に当たらない。
  現在の `projects/` の 38 件はすべてこの形に収まる（2026-09-18 に `ls projects/` で確認）
- 前提 2: 形に合わない値は、`bin/devbase` の name 解決では**名前として扱わず**（cd しない・取り除かない）、
  そのまま下流の Python へ渡す。下流は位置引数の意味に応じて扱う
  （`[name]` を取るコマンドは Python 側の名前の検証で終了コード 1、`build <image>` はイメージ名の検証で終了コード 1、
  `scale <値>` は argparse の `new_scale` の型エラーで終了コード 2、`login <値>` は今と同じく index として渡る）
- 前提 3: トップレベル `build <x>` で、`containers/<x>` と `projects/<x>` の両方が実在するときは、
  **イメージ `<x>` のビルドとして扱い**、プロジェクトとしても解釈できたことを 1 行知らせる。
  知らせる文にはプロジェクトをビルドする方法（そのディレクトリで `devbase build`）を含める。
  理由: `build <image>` はイメージを明示した指定で、プロジェクトのビルドは通常その場所で引数なしに行う。
  逆（プロジェクト優先）にすると、`containers/<x>` をトップレベルからビルドする手段が無いままになる
- 前提 4: `container`（`ct`）グループのサブコマンドは name 解決の対象から外す。`container <sub> <name>` は
  parser が `[name]` を受け付けないため、実在するプロジェクト名でも usage エラー（終了コード 2）になる。
  `container` は非推奨で、名前の指定は `project <sub> <name>` が持つ
- 前提 5: `build --help` / `-h` の使い方は `bin/devbase` の側で出す。トップレベル `build` は shell 実装
  （`cmd_build`）で動くコマンドで、Python の `project build` の parser とは受け付ける引数が違う
  （`--project-no-cache` は shell にだけある）ため
- 前提 6: `login <index>` / `scale <N>` が実在するプロジェクト名と衝突する挙動（数字だけのプロジェクト名）は
  変えない。数字だけのプロジェクト名は現在無く、衝突は偶発に限られる

## 対象範囲

含む:

- `bin/devbase` の `maybe_cd_project` の名前の検証（#146）
- `lib/devbase/commands/container.py` の `_resolve_project_name` と、`lib/devbase/cli.py` の
  `_named_lifecycle_project` の名前の検証（#146）
- トップレベル `build <x>` の、イメージとプロジェクトの衝突時の扱い（#142）
- `devbase build --help` / `-h`（#196）
- `container` / `ct` グループの name 解決の扱い（#200）
- `bin/devbase` を実プロセスで起動し、name 解決（`maybe_cd_project`）を通る経路のテスト（#146 のコメント 1）
- 利用者向けの CLI リファレンスと CHANGELOG

含まない:

- `login <index>` / `scale <N>` の衝突（前提 6）
- `container` グループそのものの削除
- `--image` のような新しいフラグの追加
- `project build <image>` / `project login <index>` の挙動（name 解決の対象外のまま）
- シェル補完の変更（候補の出し方は変わらない）
- 新しい型・永続データ・画面の追加（そのためクラス図・ER 図・画面遷移図を作らない）
- 他の名前の検証（`env/bundle.py` の `is_valid_project_name` など）を寄せること

## 受け入れ条件

名前の形（#146）:

- [x] 1. 前提: `$DEVBASE_ROOT/etc` が実在する
      操作: `devbase build ../etc` を実行する
      結果: `$DEVBASE_ROOT/etc` へ cd せず、そこの `env` を読まない。ビルドは 1 つも始まらず、終了コードは 0 以外
      検証: `tests/cli/test_build_image_argument.py::test_wrapper_build_traversal_does_not_cd_or_read_outside_env`（wrapper）、
      `::test_cli_project_build_rejects_traversal_image`（Python が 1 で終わる）、`::test_single_build_rejects_invalid_image_name[../etc]`
- [x] 2. トップレベルの `up` `down` `ps` `scale` `login` `rebuild` `open` と、`project` の
      `up` `down` `ps` `logs` `scale` `rebuild` `open` に `..` や `/` を含む名前（`../etc`、`a/b`、`.`、`..`）を渡すと、
      `projects/` の外のディレクトリへ cd せず、`projects/` の外の `env` を読まない
      検証: `tests/cli/test_project_name_resolution.py::test_wrapper_malformed_name_stays_put_and_reads_no_outside_env`（14 コマンド × 4 名前）、
      `::test_resolve_rejects_malformed_name_without_chdir`（単体）
- [x] 3. `python -m devbase.cli project up ../etc`（wrapper を経ない直接起動）は、chdir せず、
      プロジェクト名に使えない形である旨を出して終了コード 1
      検証: `tests/cli/test_project_name_resolution.py::test_cli_project_up_rejects_malformed_name`
- [x] 4. 名前を指定したライフサイクル操作の dispatch 前の注入（`_named_lifecycle_project`）は、形に合わない名前で
      `projects/` の外の `env` を読まず、`None` を返す
      検証: `tests/cli/test_secret_injection.py::test_malformed_name_is_not_a_project_and_reads_nothing`
- [x] 5. 形に合う実在のプロジェクト名（`carmo`、`github_work_time`、`carmo-ai` の形）は、今と同じく cd して
      取り除かれる
      検証: `tests/cli/test_project_name_resolution.py::test_wrapper_well_formed_existing_name_cds_and_strips`、
      `tests/utils/test_names.py::test_accepts_real_project_names`

イメージとの衝突（#142）:

- [x] 6. 前提: `containers/bi-tools` と `projects/bi-tools` が実在する
      操作: 任意のディレクトリで `devbase build bi-tools --no-cache` を実行する
      結果: `containers/bi-tools` の単体ビルド（Python の `project build bi-tools --no-cache`）へ届き、
      `projects/bi-tools` へ cd しない。プロジェクトとしても解釈できたことと、プロジェクトをビルドする方法を
      stderr に 1 回出す
      検証: `tests/cli/test_build_image_argument.py::test_wrapper_build_image_wins_over_same_named_project_and_notes`
- [x] 7. 前提: `projects/carmo` だけが実在し、`containers/carmo` は無い
      操作: `devbase build carmo` を実行する
      結果: 今と同じく `projects/carmo` へ cd してプロジェクトのビルド（`cmd_build`）へ進む
      検証: `tests/cli/test_build_image_argument.py::test_wrapper_build_project_only_name_cds_and_builds_project`
- [x] 8. 前提: `containers/go` だけが実在し、`projects/go` は無い
      操作: `devbase build go` を実行する
      結果: 今と同じく `go` の単体ビルドへ届く。衝突の知らせは出ない
      検証: `tests/cli/test_build_image_argument.py::test_wrapper_build_container_only_name_has_no_note`

ヘルプ（#196）:

- [x] 9. `devbase build --help` と `devbase build -h` は、終了コード 0 で `build` の使い方を出し、
      `=== Building devbase images ===` を出さない。`cmd_build`・`docker`・Python の `project build` の
      いずれも呼ばれない
      検証: `tests/cli/test_build_image_argument.py::test_wrapper_build_help_prints_usage_without_building[--help|-h]`
- [x] 10. 使い方には `--no-cache` / `--project-no-cache` / `--expires[=DAYS]` / `--context NAME` / `<image>` の指定が載る
      （`--project-no-cache` は 2026-09-18 のドキュメントレビューで追加）
      検証: 9 と同じテストの `_assert_build_usage`（`BUILD_USAGE_TOKENS` の 5 語）
- [x] 11. `devbase build carmo --help`（実在するプロジェクト名の後ろの `--help`）も、ビルドせず使い方を出して
      終了コード 0
      検証: `tests/cli/test_build_image_argument.py::test_wrapper_build_name_help_does_not_cd_or_read_env[--help|-h]`

`container` グループ（#200）:

- [x] 12. 前提: `projects/carmo` が実在する
      操作: `devbase container up carmo`（`down` / `ps` / `logs` / `scale` / `rebuild` / `open`、`ct` でも同じ）を実行する
      結果: `projects/carmo` へ cd せず、argparse の usage エラーで終了コード 2
      検証: `tests/cli/test_project_name_resolution.py::test_wrapper_container_group_does_not_resolve_names`（container / ct × 7 サブコマンド）、
      `tests/cli/test_project_dispatch.py::test_container_subcommands_reject_name_positional`（SystemExit(2)）
- [x] 13. `devbase container up`（名前なし）は今と同じく現在のディレクトリのプロジェクトで動き、非推奨の警告を出す
      検証: `tests/cli/test_project_name_resolution.py::test_wrapper_container_up_without_name_uses_cwd`、
      `tests/cli/test_project_dispatch.py::test_cmd_container_warns_and_delegates`（既存）

退行しないこと:

- [x] 14. `devbase project up <name>` などの名前指定、`devbase project build <image>`、`devbase build <image>`
      （衝突しない名前）、`devbase build --context NAME` の既存テストが変更なしで通る
      検証: `tests/cli/test_project_name_resolution.py`（`test_wrapper_ct_up_name_cds_and_strips` を 12 のテストへ置き換えた以外は変更なし）、
      `tests/cli/test_build_image_argument.py`・`test_wrapper_build_context.py`・`test_open_command.py`・`test_project_dispatch.py` の既存テストは変更なしで通過
- [x] 15. name 解決を含む経路のテストが、`bin/devbase` を実プロセスで起動し、`maybe_cd_project` を
      スタブせずに通る（dispatch の先だけを差し替える）
      検証: `tests/cli/conftest.py` の `exec_wrapper`（`bin/devbase` を tmp へ複製し `uv` だけを PATH で差し替える）を使う 1・2・5〜13 のテスト
- [x] 16. 全体テスト（`uv run pytest tests/`）が通る
      検証: `uv run --locked pytest -q tests/` → `2813 passed`（exit 0、2026-09-19）

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる: 形に合わない名前は名前として扱われない（#146）。`build <x>` の衝突はイメージが勝つ（#142）。`build --help` が使い方を出す（#196）。`container <sub> <name>` が usage エラーになる（#200）。CHANGELOG では #146・#142・#200 を Changed、#196 を Fixed に書く |
| データ | 変わらない |
| 既存の振る舞い | `bin/devbase` の name 解決と `build)` の分岐、Python 側の 2 つの名前の検証 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest tests/ -q` |
| 静的解析 | `shellcheck --severity=error bin/devbase`、`ruff check --select=E9,F63,F7,F82 lib` |
| 手動確認 | `devbase build --help`・`devbase build ../etc`・`devbase container up carmo` を実機で打ち、出力と終了コードを見る（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 入口の振り分けは `bin/devbase`、parser は `lib/devbase/cli.py`、名前の切り替えは `lib/devbase/commands/container.py`。名前の形の規則は Python 側で 1 か所に置き、shell 側は同じ正規表現を持つ（両方にある理由をコメントで対にする） |
| コーディング規約 | `bin/devbase` は bash 3.2 で動くこと（macOS 既定）。`set -u` は使っていない |
| テスト戦略 | wrapper の振る舞いは `tests/cli/` で `bin/devbase` を実プロセスで起動し、`run_python` / `cmd_build` / `compose_with_secrets` の先だけを差し替える。Python 側の検証は単体テスト |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 手元で全体テスト、`shellcheck` |
| 確認してから行う | 前提 3・4 の挙動の決定（設計 Pull Request の承認で確かめる） |
| 行わない | `container` グループの削除、新しいフラグの追加、補完の変更 |

## 実装計画

設計は [PLAN61_name-resolution-design.md](PLAN61_name-resolution-design.md)。タスクは設計の「構成要素」と「テスト設計」から導き、
機能単位（F1〜F4）で分ける。各タスクは失敗するテスト → 通す最小実装 → 整理の順に進める。

### 修正対象

- `lib/devbase/utils/names.py`（新設）、`lib/devbase/commands/container.py`、`lib/devbase/cli.py`
- `bin/devbase`
- `tests/cli/conftest.py`（新設）、`tests/utils/test_names.py`（新設）、`tests/cli/test_project_name_resolution.py`、
  `tests/cli/test_build_image_argument.py`、`tests/cli/test_project_dispatch.py`、`tests/cli/test_secret_injection.py`
- `docs/user/cli-reference/02-project.md`、`docs/user/cli-reference/README.md`、`docs/developer/architecture.md`、
  `docs/specifications/editor-open.md`、`CHANGELOG.md`

### Task 1 [x]: 名前の形の規則（Python）と Python 側 3 入口の検証（F1）

- **対象ファイル:** `lib/devbase/utils/names.py`（新設）、`lib/devbase/commands/container.py`、`lib/devbase/cli.py`、
  `tests/utils/test_names.py`（新設）、`tests/cli/test_project_name_resolution.py`、`tests/cli/test_build_image_argument.py`、
  `tests/cli/test_secret_injection.py`
- **変更内容:** `SINGLE_SEGMENT_NAME_PATTERN` と `is_single_segment_name` を新設。`_build_single_image` の `_IMAGE_NAME_RE` を
  これへ寄せる。`_resolve_project_name` の入口で形を見て、合わなければ error ログを出して `False`（chdir・env・候補なし）。
  `_named_lifecycle_project` は形に合わなければ実在と `store_for` を見ずに `None`
- **満たす受け入れ条件:** 1（単体）、2（単体）、3、4、5（単体）、決定 4（単体）
- **進め方:** テスト駆動

### Task 2 [x]: wrapper のハーネス `exec_wrapper` と shell 側の名前の形（F1）

- **対象ファイル:** `tests/cli/conftest.py`（新設）、`bin/devbase`、`tests/cli/test_project_name_resolution.py`
- **変更内容:** `bin/devbase` を tmp へ複製し `fakebin/uv` で dispatch の先だけを差し替える fixture。`bin/devbase` に
  `_SINGLE_SEGMENT_NAME_RE` と `is_single_segment_name`（`local LC_ALL=C`）を足し、`maybe_cd_project` の入口を置き換える。
  同期テスト（wrapper の正規表現 = `'^' + SINGLE_SEGMENT_NAME_PATTERN + '$'`）
- **満たす受け入れ条件:** 2（wrapper）、5（wrapper）、15、決定 2、決定 4（wrapper）
- **進め方:** テスト駆動

### Task 3 [x]: `build --help` / `-h`（F3）

- **対象ファイル:** `bin/devbase`、`tests/cli/test_build_image_argument.py`
- **変更内容:** `build_usage` を新設し、name 解決の case より前で `build` の引数に `-h` / `--help` があれば使い方を出して 0 で終わる。
  `--context=--help` / `--context=-h` は使い方にしない
- **満たす受け入れ条件:** 9、10、11、決定 8
- **進め方:** テスト駆動

### Task 4 [x]: `build <x>` のイメージとプロジェクトの衝突（F2）

- **対象ファイル:** `bin/devbase`、`tests/cli/test_build_image_argument.py`
- **変更内容:** name 解決の case に `build` の分岐を足す。`$2` が形に合い `containers/$2` が実在すれば name 解決を通さず、
  `projects/$2` もあれば stderr に 1 行。`build)` 分岐の PLAN49 の注記も合わせる
- **満たす受け入れ条件:** 1（wrapper）、6、7、8
- **進め方:** テスト駆動

### Task 5 [x]: `container` / `ct` を name 解決から外す（F4）

- **対象ファイル:** `bin/devbase`、`lib/devbase/cli.py`（コメント）、`tests/cli/test_project_name_resolution.py`、
  `tests/cli/test_project_dispatch.py`
- **変更内容:** name 解決の case を `project` だけにする。`test_wrapper_ct_up_name_cds_and_strips` を受け入れ条件 12 のテストへ
  置き換える。name 解決の説明コメントを変更後の規則に書き替え、`cli.py` の同期注意を `project` だけに直す
- **満たす受け入れ条件:** 12、13、14
- **進め方:** テスト駆動

### Task 6 [x]: 文書と CHANGELOG

- **対象ファイル:** `docs/user/cli-reference/02-project.md`、`docs/user/cli-reference/README.md`、`docs/developer/architecture.md`、
  `docs/specifications/editor-open.md`、`CHANGELOG.md`
- **変更内容:** 設計の「文書とテスト」の表のとおり
- **満たす受け入れ条件:** （文書。受け入れ条件には対応しない）
- **進め方:** テスト駆動を適用しない（文書のみ）

### Task 7 [x]: 全体の検証

- **変更内容:** `uv run --locked pytest -q tests/`、`shellcheck --severity=error bin/devbase`、`ruff check --select=E9,F63,F7,F82 lib`、
  macOS の `/bin/bash`（3.2）で `tests/cli`
- **満たす受け入れ条件:** 16

### リスクと対処

| リスク | 対処 |
| --- | --- |
| `bin/devbase` は 1 ファイルの shell で、name 解決と `build)` の分岐を 4 タスクが触る | タスクごとにテストを通す。既存の `sed` ハーネスのテストを退行の検出に使う（受け入れ条件 14） |
| pytest が実環境の `DEVBASE_ROOT` を継承する | `exec_wrapper` は wrapper の複製から `DEVBASE_ROOT` を tmp に決めさせる（決定 11） |

### 切り戻し手順

- コードの変更のみ（データ移行なし）。ブランチの revert で戻せる

### 完了の定義

- [x] 受け入れ条件 16 件に検証手段（テスト名）が対応している
- [x] `uv run --locked pytest -q tests/` が exit 0（2813 passed）、`shellcheck --severity=error bin/devbase`（0.11.0、exit 0）と `ruff check --select=E9,F63,F7,F82 lib`（exit 0）が exit 0。macOS の `/bin/bash` 3.2.57 で `tests/cli` 824 passed
