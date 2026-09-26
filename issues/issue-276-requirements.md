# #276: プロジェクトとして数える名前の述語と説明の文言を utils/names.py に 1 つ置き、各所の独自の規則を寄せる

正は課題の本文（#276）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> プロジェクトとして数える名前の述語と説明の文言を utils/names.py に 1 つ置き、各所の独自の規則を寄せる
>
> ## 修正レイヤー
>
> `lib/devbase/utils/names.py`。「プロジェクトとして数える名前か」を決める述語と、その形を説明する文言の正本をここに 1 つ置く。今は `syncer.discover_projects`・`secret_store._validate_project_name`・各所の `iterdir()` の走査・`commands/container.py` のインラインの文言が、それぞれ独自の規則を持つ。
>
> ## 採る手
>
> | 手 | 内容 |
> | --- | --- |
> | 統合 | `.` 始まりの除外と名前の形の検証を `utils/names.py` の述語へ寄せ、消費側はそれを呼ぶ |
>
> ## 子 issue
>
> | 番号 | 現象レイヤーと観測 |
> | --- | --- |
> | #226 | `plugin/info.py`・`commands/status.py` などの走査。`.` 始まりのプロジェクトを数え、プラグインの同期と食い違う |
> | #229 | `commands/container.py`。`NAME_FORM_HINT` と同じ文言をインラインで持つ |
> | #245 | `env/secret_store.py`。`_validate_project_name` と `is_single_segment_name` の 2 つの規則が並ぶ |
>
> ## 完了条件
>
> - プロジェクトとして数える述語と説明の文言が `utils/names.py` に 1 つずつある
> - 子 issue に挙がった消費側がすべてそれを呼んでいる
> - 規則を変えたときに消費側の食い違いを検出するテストがある

## 目的

- 「`projects/` の直下のどの名前をプロジェクトとして数えるか」を `utils/names.py` の述語 1 つで決め、
  プラグインの同期・一覧・状態・機密の操作が同じプロジェクトの集合を見る状態にする
- 名前の形を利用者へ説明する文言を `NAME_FORM_HINT` 1 つにし、場所によって説明が違う状態を無くす
- 機密の保存先（`env/secret_store.py`）が名前の形を `utils/names.py` の規則で見るようにし、
  名前の規則が 2 つ並んだまま食い違いに気づけない状態を無くす

## 前提

- 前提 1: プロジェクトとして数える名前の述語は「`.` で始まらない」だけを見る。名前の形
  （`is_single_segment_name`）に合わない名前（`_foo`・`-x`・`café` など）は、これまでどおり数え、
  プラグインの同期の知らせで伝える（PLAN66 の「弾かずに知らせる」方針を変えない）
- 前提 2: 寄せる対象の走査は、#226 の表に挙がった 12 か所に、既に `.` 始まりを除外している
  `plugin/syncer.py` の 2 か所（`discover_projects`・実ディレクトリの知らせ）を足した 14 か所である
  （`grep -rn "iterdir()" lib/ | grep project`、`main` = `71a81a3`、2026-09-26 で数えた）。
  `tui/actions_project.py:111` は `containers/` の走査なので対象に入れない
- 前提 3: 各走査が持つディレクトリの条件（`is_dir()`・壊れた symlink を拾う `is_symlink()`・
  `compose.yml` の有無）は走査ごとに違う用途を持つので、寄せない。寄せるのは名前の条件だけである
- 前提 4: #245 は #245 の本文の案 A で扱う。`_validate_project_name` の拒否（空・パス区切り・`.`・`..`）は
  パスを跨がせない最低限の安全の検査として残し、**機密の保存先が受け付ける名前は変えない**。
  名前の形に合わない名前へ機密を書き込むときに、`NAME_FORM_HINT` を含む知らせを出す。
  案 B（受け付ける名前を狭める）は、いま読み書きできている機密が例外になるため採らない
- 前提 5: `env/bundle.py` の `is_valid_project_name`（書庫の中の名前。先頭の `_` を許す）は、
  export と import の往復の互換を持つため寄せない。ただし `_collect_projects` は、この検査の前に
  プロジェクトとして数える述語で `.` 始まりを外す
- 前提 6: `bin/devbase`（shell）は `projects/` を一覧していない。`grep -n "projects" bin/devbase` の当たりは
  `projects/<name>` の実在を確かめる箇所（`:377`・`:500`）とコメントだけである。shell 側の変更は要らない

## 対象範囲

含む:
- `utils/names.py` にプロジェクトとして数える名前の述語を 1 つ足す
- 前提 2 の 14 か所の走査が、名前の条件としてその述語を呼ぶ
- `commands/container.py` のインラインの文言を `NAME_FORM_HINT` へ差し替える（#229）
- `env/secret_store.py` の書き込みの経路で、名前の形に合わないときに知らせを出す（#245 の案 A）
- 述語を差し替えたときに消費側の食い違いを検出するテスト
- 確定仕様 `docs/specifications/cli-argument-resolution.md` の「寄せていない 2 つ」の記述と、
  `CHANGELOG.md` の `[Unreleased]` の更新

含まない:
- 名前の形（`SINGLE_SEGMENT_NAME_PATTERN`）そのものの変更
- `_validate_project_name` が受け付ける名前を狭めること（#245 の案 B）と、書き込みの入口を
  1 つにまとめること（#245 の案 C）
- `env/bundle.py` の `is_valid_project_name` と `env/backend_config.py` の `_validate_segment` を寄せること
- `containers/` の走査（イメージ名）
- 走査ごとのディレクトリの条件（前提 3）の統一

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | `projects/` の直下の名前を列挙した | 同期・一覧・状態・機密の操作・移行の各コマンド | `projects/` が無ければ 0 件として続ける（今と同じ） | — |
| E2 | 列挙した名前をプロジェクトとして数えるかを決めた | E1 | 述語は例外を出さない（真偽を返すだけ） | E1 |
| E3 | 数えた名前が名前の形に合わないことを利用者へ知らせた | 同期（既存）と、機密の書き込み（今回足す） | 知らせは標準エラーの出力で、失敗しても処理を止めない | E2 |
| E4 | 機密をプロジェクトの保存先へ書き込んだ | `env set --project` / `env edit --project` / `env import` など | 空・パス区切り・`.`・`..` は今と同じく `SecretStoreError` で止まる | E3 の知らせは E4 の前に出る |
| E5 | 名前の形の説明を利用者へ示した | 名前の形に合わない名前での拒否（`container.py`）と知らせ（E3） | — | — |

## 用語

| 用語 | 意味 |
| --- | --- |
| プロジェクトとして数える名前 | `projects/` の直下の名前のうち、同期・一覧・状態・機密の操作がプロジェクトとして扱うもの。`.` で始まらない名前 |
| 名前の形の説明 | 名前の形を利用者へ説明する文（`NAME_FORM_HINT`） |

## 受け入れ条件

述語と文言の正本:

- [ ] `utils/names.py` にプロジェクトとして数える名前の述語が 1 つあり、`foo`・`_foo`・`-x`・`carmo-ai` で真、
      `.vscode`・`.`・`..`・空文字で偽を返す（`tests/utils/test_names.py`）
- [ ] `grep -rn "startswith('.')" lib/` の当たりに、プロジェクトの名前を見る箇所が `utils/names.py` の外に無い
- [ ] `grep -rn "英数字で始まり、英数字" lib/` の当たりが `lib/devbase/utils/names.py` の 1 か所だけである

#226（`.` 始まりの扱いを揃える）:

- [ ] 前提: プラグインの `projects/` に `bar` と `.foo` のディレクトリがある
      操作: `devbase plugin info <plugin>` と `devbase status` を実行する
      結果: `plugin info` の一覧は `bar` だけで `Projects (1)`、`status` のそのプラグインのプロジェクト数は 1 であり、
      `discover_projects` が返す名前と一致する
- [ ] 前提: `$DEVBASE_ROOT/projects/` に `bar` と `.vscode` のディレクトリがある
      操作: `devbase project list`、存在しない名前での `devbase up`（候補の表示）、`devbase project migrate-config`、
      `devbase status` を実行する
      結果: どの出力にも `.vscode` が出ず、`.vscode` の中のファイルは書き換わらない
- [ ] 前提: `$DEVBASE_ROOT/projects/.vscode/.env` と `projects/bar/.env` がある
      操作: `env export`・`env encrypt`・`env decrypt`・`env doctor`・`env backend status` を実行する
      結果: `.vscode` は対象にも報告にも出ず、`env export` は `.vscode` についての警告を出さない。`bar` の扱いは変更前と同じである
- [ ] `projects/a b` のように `.` で始まらず書庫に入れられない名前は、`env export` がこれまでどおり警告を出して外す

#229（文言を定数へ寄せる）:

- [ ] `devbase up '../etc'` の相当（`container._resolve_project_name` に形に合わない名前を渡す）が出すエラーの文字列は、
      変更前と 1 文字も変わらない

#245（機密の保存先の規則）:

- [ ] 前提: プロジェクト名が `_foo` のディレクトリで
      操作: 機密をプロジェクトの保存先へ書き込む（平文・age のどちらの backend でも）
      結果: 書き込みは成功し、標準エラーへ `_foo` と `NAME_FORM_HINT` を含む知らせが 1 回出る
- [ ] 名前の形に合う名前（`bar`）への書き込みと、どの名前でも読み取りでは、知らせを出さない
- [ ] 空・`a/b`・`.`・`..` はこれまでどおり `SecretStoreError` で止まり、ファイルを書かない

食い違いの検出:

- [ ] プロジェクトとして数える名前の述語を「`bar` も偽を返す」ものへ差し替えるテストで、前提 2 の 14 か所の走査の
      どれもが `bar` を数えない。どれか 1 か所が独自の規則を持つとこのテストが落ちる
- [ ] 名前の形の述語 `is_single_segment_name` を差し替えると、機密の書き込みの知らせの有無がそれに従う

退行しないこと:

- [ ] `uv run --locked pytest tests/ -q` がすべて通る
- [ ] 名前の形（`SINGLE_SEGMENT_NAME_PATTERN`）と `bin/devbase` の `_SINGLE_SEGMENT_NAME_RE` の同期テストが通り、
      パターンの文字列は変わらない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 名前の規則を変えるときに直す場所が `utils/names.py` の 1 ファイルで済み、消費側の取り残しはテストが落ちて分かる |
| 移行性 | `projects/.<名前>/.env` に置いた機密は `env encrypt` / `env decrypt` / `env backend` の移行の対象から外れる。この変更を `CHANGELOG.md` の Changed に書く |
| セキュリティ | 機密の保存先がパスを跨がせない検査（空・パス区切り・`.`・`..` の拒否）は弱めない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | コマンドの引数は変わらない。`utils/names.py` に関数が 1 つ増える |
| データ | 無し。ファイルの置き場とスキーマは変わらない |
| 既存の振る舞い | `.` 始まりのディレクトリが `plugin info`・`status`・`project list`・候補の表示・`project migrate-config`・`env` の各コマンドから消える。`env export` の `.` 始まりの警告が消える。名前の形に合わない名前へ機密を書き込むと知らせが出る |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --locked pytest tests/ -q` |
| 静的解析 | `uv run ruff check lib tests`（CI の Ruff lint と同じ） |
| 文言と規則の所在 | `grep -rn "英数字で始まり、英数字" lib/`・`grep -rn "startswith('.')" lib/` |
| 手動確認 | 無し（受け入れ条件はすべて pytest の一時ディレクトリで確かめられる） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 名前の規則は `lib/devbase/utils/names.py` に置く。`cli.py` の起動を軽く保つため、この module は `re` だけに依存し、ログを出さない（module の docstring と PLAN66 決定 7）。知らせを出すのは呼び出し側 |
| コーディング規約 | CI の Ruff lint（`.github/workflows/ci.yml`） |
| テスト戦略 | 述語は単体（`tests/utils/test_names.py`）。消費側は一時ディレクトリの `projects/` を使う結合の階層で、述語を差し替えて食い違いを見る。実環境の `DEVBASE_ROOT` を継承しないよう、既存の環境の隔離の fixture を使う |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、Ruff の適用、確定仕様と `CHANGELOG.md` の更新 |
| 確認してから行う | 機密の保存先が受け付ける名前を変えること、名前の形のパターンの変更 |
| 行わない | 前提 2 の外の走査の書き換え、ディレクトリの条件の統一、`bundle.is_valid_project_name` の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| `.` 始まりのディレクトリ（例: `projects/.foo`）の中で `env set --project` を打ったとき、知らせだけにするか拒否するか。この仕様は知らせだけ（前提 4）とした | 設計 PR のレビューで利用者が決める | 設計 PR のマージまで |
| 機密の書き込みの知らせを出す位置（`SecretStore` の書き込みの入口か、各 backend の `path()` を書き込みから呼ぶ経路か） | `design` の工程 | 設計 PR まで |
