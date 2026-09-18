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

- [ ] 1. 前提: `$DEVBASE_ROOT/etc` が実在する
      操作: `devbase build ../etc` を実行する
      結果: `$DEVBASE_ROOT/etc` へ cd せず、そこの `env` を読まない。ビルドは 1 つも始まらず、終了コードは 0 以外
- [ ] 2. トップレベルの `up` `down` `ps` `scale` `login` `rebuild` `open` と、`project` の
      `up` `down` `ps` `logs` `scale` `rebuild` `open` に `..` や `/` を含む名前（`../etc`、`a/b`、`.`、`..`）を渡すと、
      `projects/` の外のディレクトリへ cd せず、`projects/` の外の `env` を読まない
- [ ] 3. `python -m devbase.cli project up ../etc`（wrapper を経ない直接起動）は、chdir せず、
      プロジェクト名に使えない形である旨を出して終了コード 1
- [ ] 4. 名前を指定したライフサイクル操作の dispatch 前の注入（`_named_lifecycle_project`）は、形に合わない名前で
      `projects/` の外の `env` を読まず、`None` を返す
- [ ] 5. 形に合う実在のプロジェクト名（`carmo`、`github_work_time`、`carmo-ai` の形）は、今と同じく cd して
      取り除かれる

イメージとの衝突（#142）:

- [ ] 6. 前提: `containers/bi-tools` と `projects/bi-tools` が実在する
      操作: 任意のディレクトリで `devbase build bi-tools --no-cache` を実行する
      結果: `containers/bi-tools` の単体ビルド（Python の `project build bi-tools --no-cache`）へ届き、
      `projects/bi-tools` へ cd しない。プロジェクトとしても解釈できたことと、プロジェクトをビルドする方法を
      stderr に 1 回出す
- [ ] 7. 前提: `projects/carmo` だけが実在し、`containers/carmo` は無い
      操作: `devbase build carmo` を実行する
      結果: 今と同じく `projects/carmo` へ cd してプロジェクトのビルド（`cmd_build`）へ進む
- [ ] 8. 前提: `containers/go` だけが実在し、`projects/go` は無い
      操作: `devbase build go` を実行する
      結果: 今と同じく `go` の単体ビルドへ届く。衝突の知らせは出ない

ヘルプ（#196）:

- [ ] 9. `devbase build --help` と `devbase build -h` は、終了コード 0 で `build` の使い方を出し、
      `=== Building devbase images ===` を出さない。`cmd_build`・`docker`・Python の `project build` の
      いずれも呼ばれない
- [ ] 10. 使い方には `--no-cache` / `--project-no-cache` / `--expires[=DAYS]` / `--context NAME` / `<image>` の指定が載る
      （`--project-no-cache` は 2026-09-18 のドキュメントレビューで追加）
- [ ] 11. `devbase build carmo --help`（実在するプロジェクト名の後ろの `--help`）も、ビルドせず使い方を出して
      終了コード 0

`container` グループ（#200）:

- [ ] 12. 前提: `projects/carmo` が実在する
      操作: `devbase container up carmo`（`down` / `ps` / `logs` / `scale` / `rebuild` / `open`、`ct` でも同じ）を実行する
      結果: `projects/carmo` へ cd せず、argparse の usage エラーで終了コード 2
- [ ] 13. `devbase container up`（名前なし）は今と同じく現在のディレクトリのプロジェクトで動き、非推奨の警告を出す

退行しないこと:

- [ ] 14. `devbase project up <name>` などの名前指定、`devbase project build <image>`、`devbase build <image>`
      （衝突しない名前）、`devbase build --context NAME` の既存テストが変更なしで通る
- [ ] 15. name 解決を含む経路のテストが、`bin/devbase` を実プロセスで起動し、`maybe_cd_project` を
      スタブせずに通る（dispatch の先だけを差し替える）
- [ ] 16. 全体テスト（`uv run pytest tests/`）が通る

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
