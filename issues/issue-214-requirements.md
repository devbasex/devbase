# #214: 列挙の正本を一元化し、argparse と文書の一致をテストで固定する（[name] / --context）

正は課題の本文（#214）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

課題の起票の本文（PR #213 の範囲外として起こしたもの）をそのまま写す。

> ## 何を見つけたか
>
> `[name]` 引数と `--context` を取るサブコマンドの集合が、**5 か所に手で複製されている**。
>
> | 複製先 | 実体 |
> | --- | --- |
> | `bin/devbase` | `_PROJECT_NAME_SUBCOMMANDS` / `_NAME_RESOLVABLE_SHORTCUTS`（`:583-584`） |
> | `lib/devbase/cli.py` | `_add_project_parser` などでの `name` positional と `--context` の付与 |
> | 文書 7 ファイル | `docs/user/cli-reference/02-project.md` / `docs/user/cli-reference/README.md` / `docs/user/container-operations.md` / `docs/user/environment-variables.md` / `docs/specifications/compose-profiles.md` / `docs/specifications/remote-docker-context.md` / `docs/specifications/cli-argument-resolution.md` |
> | 補完 2 ファイル | `etc/devbase-completion.bash` / `etc/_devbase` |
>
> 同期はコメントの申し合わせ（`bin/devbase:573-582`、`lib/devbase/cli.py:32-37` と `:287` 付近）だけで担保されている。**集合の一致を固定するテストは無い。** `tests/cli/test_rebuild.py:152-153` が `bin/devbase` の 2 つのリストに `rebuild` が含まれることを見ているだけで、`_PROJECT_NAME_SUBCOMMANDS` と argparse の一致も、文書・補完との一致も固定されていない。
>
> `lib/devbase/cli.py` の `_create_parser()` を走査すると、集合は次のとおりである（d433e55）。
>
> | 対象 | サブコマンド |
> | --- | --- |
> | `project` で `name` positional を持つ | `up` `down` `ps` `logs` `scale` `rebuild` `open` `post-start` |
> | `project` で `--context` を持つ | `up` `down` `ps` `logs` `login` `scale` `build` `rebuild` `open` `post-start` |
> | `container` で `--context` を持つ | `up` `down` `ps` `logs` `login` `scale` `build` `rebuild` `open` |
> | `container` で `name` positional を持つ | なし |
>
> `profile` は `[name]` と `--context` を入れ子の parser（`profile up` / `profile down` / `profile list`）で持つため、上の走査では拾えない。文書は `profile` も `[name]` / `--context` を取るものとして並べている。
>
> `bin/devbase` の 2 つのリスト・補完 2 ファイル・`docs/specifications/cli-argument-resolution.md` の表は argparse と一致している。一方、`docs/specifications/remote-docker-context.md:42-44` の「`--context` を受け付けるもの」の列挙には `post-start` が無い。
>
> PLAN49（`rebuild` の追加）・PLAN59（`open` の追加）・#371（`post-start` の追加）の 3 回とも、コードと補完は更新されたが文書のどこかが取り残された。同じ漏れが 3 度起きている。
>
> ## 現象レイヤー
>
> 文書 7 ファイルの列挙、`bin/devbase` の 2 つのリスト、補完 2 ファイル。どれも argparse の定義を手で写したもので、写し漏れが起きる。
>
> ## 修正レイヤー
>
> 列挙の**正本は argparse（`lib/devbase/cli.py` の `_create_parser()`）**に置く。定義そのものがそこにあり、他の複製はすべてそこから派生する。
>
> 1. `tests/cli/` 配下に一致テストを足し、`_create_parser()` を走査して得た集合に次を合わせる
>    - `bin/devbase` の `_PROJECT_NAME_SUBCOMMANDS` / `_NAME_RESOLVABLE_SHORTCUTS`（`login` / `build` を意図的に除く・含める規則はコメントのとおり）
>    - 補完 2 ファイル（`etc/devbase-completion.bash` / `etc/_devbase`）
>    - `docs/specifications/cli-argument-resolution.md` の表
>    - 入れ子の `profile` も走査の対象に含める
> 2. 他の文書は列挙を持たず、`docs/specifications/cli-argument-resolution.md` へのリンクにする。`docs/specifications/remote-docker-context.md:42-44` の列挙（`post-start` が無い）もここでリンクに置き換える
>
> 走査の実装は PR #213 で使った、`_create_parser()` から `name` positional と `--context` を持つ parser を集める形がそのまま使える。
>
> ## どこで見つけたか
>
> #208 の本文「修正レイヤー」。PR #213 は現象レイヤー（文書への追記）だけを直した。
>
> ## なぜこの変更の範囲外なのか
>
> #208 の本文が「恒久策（正本の一元化と一致テスト）は、この issue の範囲を超えるため着手の時点で分ける」と明示している。PR #213 の受け入れ条件は文書の列挙が argparse と一致することまでで、正本の移動とテストの追加は含まない。また PR #213 は `docs/` 配下だけに閉じる `light` の変更で、`tests/` を触らない。
>
> ## 直さないと何が起きるか
>
> `[name]` または `--context` を取るサブコマンドを次に増やしたとき、同じ取り残しが起きる。文書だけが古いため CI も補完も気づかず、利用者が「そのコマンドは名前を取れない」と読んで遠回りする。影響の範囲は文書の読み手全体で、コードの振る舞いには及ばない。
>
> ## 由来
>
> PR #213（#208 / #195 の現象レイヤーの修正）
>
> ## 直す場所
>
> | 項目 | 内容 |
> | --- | --- |
> | 現れている場所 | 文書 7 ファイルの列挙（`remote-docker-context.md:42-44` は `post-start` が欠けている）・bin/devbase の _PROJECT_NAME_SUBCOMMANDS / _NAME_RESOLVABLE_SHORTCUTS・補完 2 ファイル（etc/devbase-completion.bash / etc/_devbase） |
> | 直す場所 | lib/devbase/cli.py の _create_parser() を正本にし、tests/cli に一致テストを新設（手: 統合 consolidate_duplication + 新設）。他の文書は cli-argument-resolution.md へのリンクにする |
>

## 目的

- `[name]`（プロジェクト名）と `--context` を受け付けるサブコマンドの集合について、正本を argparse（`lib/devbase/cli.py` の `_create_parser()`）の 1 か所にする。サブコマンドを足し引きして写しが取り残されたら、CI のテストが落ちる状態にする
- 文書は列挙を持たず、確定仕様 `docs/specifications/cli-argument-resolution.md` の表へのリンクで済ませる。表は一致テストが argparse と比べる
- PLAN49（`rebuild`）・PLAN59（`open`）・#371（`post-start`）で 3 度起きた文書の取り残しを、4 度目に起こさない

## 前提

- 前提 1: 一致を見る基準は、この課題の着手の時点の `main`（a120d15）で `_create_parser()` を走査した集合である。走査の結果は次の「現状の集合」の節のとおりで、課題の本文の表（d433e55）と比べて `env exec` / `env token` の `--context` と、入れ子の `profile up` / `profile down` / `profile list` が増えている
- 前提 2: 「`[name]` を取る」は、`dest` が `name` の位置引数のうち**プロジェクト名を表すもの**に限る。対象は `project` グループ（入れ子の `profile` を含む）とトップレベルの parser である。`plugin` / `pl`・`snapshot` / `ss`・`env backend use` の `name` はプラグイン・スナップショット・backend の名前で、この課題の集合に入れない
- 前提 3: 「`--context` を取る」は、`--context` を持つすべての parser である（`project` / `container` / `ct` の配下・入れ子の `profile`・トップレベル・`env exec` / `env token`）。argparse に parser の無いトップレベルの `build`（シェルの `cmd_build` が `--context` を抜き取る）は、ラッパーの実装を正本とする唯一の例外として、一致テストが名指しで足す
- 前提 4: ラッパーの 2 つのリストは、argparse の集合から次の規則で導ける形のまま残す。`_PROJECT_NAME_SUBCOMMANDS` は「`project` の直下の parser で `[name]` を取るもの」（入れ子の `profile` は 3 番目の引数が `up` / `down` / `list` になるため除く。`docs/specifications/compose-profiles.md` の既存の決定）。`_NAME_RESOLVABLE_SHORTCUTS` は「`cli.SHORTCUTS` のキー」と「`build`」の和（`login` は parser が `[name]` を持たないが、ラッパーの name 解決だけが名前の指定の手段として含める。`cli-argument-resolution.md` の既存の決定）。リストをシェルから消して Python から生成する形には変えない（name 解決のたびに `uv run` を起こさないため。同じ理由で名前の形の正規表現も文字列で写している）
- 前提 5: 補完の一致は「`[name]` を取る位置でプロジェクト名を補完すること」と「`[name]` か `--context` を取るサブコマンドが、`project` / `container` の補完が出すサブコマンドの一覧に含まれること」の 2 つで見る（argparse の `project` には補完に無い `migrate-config` / `migrate-volume` があり、一覧の全体の一致はこの課題の集合の外である）。`--context` を補完するかどうかは一致の対象に入れない（現状は `open` と `post-start` だけが補完し、揃えると補完の振る舞いが広く変わるため。下の「未決」）
- 前提 6: 入れ子の `profile` の `[name]` は、補完の一致の対象から名指しで外す。`project profile up` の 1 つ目の位置引数は、値が 1 個ならプロファイル名、2 個ならプロジェクト名になり、補完の時点ではどちらか決まらないためである。一致テストは外す理由を例外の一覧に持つ
- 前提 7: 「列挙」は、2 つ以上のサブコマンドを並べて「これらが `[name]` / `--context` を取る」と述べる文・箇条・表の行を指す。1 つのコマンドの使い方の行（`devbase project up [name] [--context NAME]`）、1 行 1 コマンドのショートカットの対応表、コマンドの地図（`docs/user/cli-reference/README.md` の mermaid の図）は列挙に当たらず、そのまま残す
- 前提 8: 一致テストの置き場は `tests/cli/` とする。bash の補完は既存の `tests/cli/test_completion.py` と同じく実際に source して確かめ、zsh の補完は CI のランナーに依らないよう内容（`etc/_devbase` の分岐）を静的に見る
- 前提 9: 利用者に見える振る舞いは変わらない（テストの追加と文書の列挙の置き換えだけ）。`CHANGELOG.md` の `[Unreleased]` には書かない。文書の誤り（`remote-docker-context.md` の `post-start` の欠け）の訂正も、文書の変更として書かない

## 現状の集合（a120d15 で `_create_parser()` を走査した結果）

| 対象 | `[name]` を取る | `--context` を取る |
| --- | --- | --- |
| `project` の直下 | `up` `down` `ps` `logs` `scale` `rebuild` `open` `post-start` | `up` `down` `ps` `logs` `login` `scale` `build` `rebuild` `open` `post-start` |
| `project profile` | `up` `down` `list` | `up` `down` `list` |
| `container` / `ct` の直下 | なし | `up` `down` `ps` `logs` `login` `scale` `build` `rebuild` `open` |
| `container profile` / `ct profile` | なし | `up` `down` `list` |
| トップレベル（argparse） | `up` `down` `ps` `scale` `rebuild` `open` | `up` `down` `ps` `login` `scale` `rebuild` `open` |
| トップレベル（ラッパー） | — | `build`（シェルの `cmd_build`） |
| `env` | なし | `exec` `token` |

| 写し | 現状 |
| --- | --- |
| `bin/devbase` の 2 つのリスト | 前提 4 の規則と一致している |
| `etc/devbase-completion.bash` | `[name]` を取る位置（`profile` を除く）でプロジェクト名を補完する。`--context` は `open` / `post-start` だけ補完する |
| `etc/_devbase` | 同上（静的に見た範囲） |
| `docs/specifications/cli-argument-resolution.md` | `[name]` の表はある。`--context` の表は無い |
| `docs/specifications/remote-docker-context.md`（「context の解決」） | `--context` の列挙に `post-start` が無い |
| `docs/user/environment-variables.md`（`DEVBASE_DOCKER_CONTEXT` の行） | `--context` の列挙に `post-start` と `profile` が無い |
| ほかの列挙 | `docs/user/cli-reference/02-project.md`（`profile` の注記と「`--context NAME`（共通オプション）」）・`docs/user/container-operations.md` の冒頭の注記・`docs/specifications/compose-profiles.md`（`--context` の一文と `_PROJECT_NAME_SUBCOMMANDS` の写し）。いずれも現状は argparse と一致している |

## 対象範囲

含む:
- argparse を走査して `[name]` の集合と `--context` の集合を得る一致テストを `tests/cli/` に足す（入れ子の `profile` を含む）
- 一致テストが比べる写し: `bin/devbase` の `_PROJECT_NAME_SUBCOMMANDS` / `_NAME_RESOLVABLE_SHORTCUTS`、補完 2 ファイル（前提 5・6 の範囲）、`docs/specifications/cli-argument-resolution.md` の表
- `docs/specifications/cli-argument-resolution.md` に `--context` を受け付けるサブコマンドの表を足す（`[name]` の表と同じ節か隣の節）
- ほかの文書の列挙（前提 7 の意味）を、`cli-argument-resolution.md` の表へのリンクに置き換える。`remote-docker-context.md` の `post-start` の欠けは、この置き換えで消える
- `bin/devbase` と `lib/devbase/cli.py` の「同期注意」のコメントと、`cli-argument-resolution.md` の「運用」の該当の箇条を、一致テストを指す形に直す
- 既存の `tests/cli/test_rebuild.py` の `test_wrapper_rebuild_in_name_resolvable` は、一致テストが包むなら外してよい（外すかは設計で決める）

含まない:
- argparse の定義そのもの（どのサブコマンドが `[name]` / `--context` を取るか）を変えること
- ラッパーのリストを Python から生成する形に変えること（前提 4）
- 補完が `--context` を補完する範囲を広げること（前提 5。下の「未決」）
- `profile` の `[name]` を補完すること（前提 6）
- `plugin` / `snapshot` / `env backend use` の `name`（前提 2）
- 1 コマンドの使い方の行・ショートカットの対応表・コマンドの地図の書き換え（前提 7）
- `container` / `ct` に `[name]` を持たせること（PLAN61 決定 10 のまま）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 開発者が argparse のサブコマンド・`name` の位置引数・`--context` を足した（または外した） | 新しいサブコマンドの追加（PLAN49・PLAN59・#371 と同じ形） | — | — |
| E2 | 一致テストが `_create_parser()` を走査して `[name]` の集合と `--context` の集合を得た | pytest の実行（手元と CI） | parser の構造が変わって走査が集合を取れない（例: 入れ子の parser を拾えない）。テストは空の集合で通らず、集合が空であることで落ちる | E1 |
| E3 | 一致テストがラッパーの 2 つのリストを `bin/devbase` から読み、前提 4 の規則で導いた集合と比べた | E2 の後 | リストの行が見つからない・形が変わった。テストは読めなかったことで落ちる | E2 |
| E4 | 一致テストが bash の補完を実行し、zsh の補完を静的に読んで、前提 5・6 の範囲で比べた | E2 の後 | bash が無い・補完の関数が終了コード 0 以外を返した。テストは落ちる | E2 |
| E5 | 一致テストが `cli-argument-resolution.md` の 2 つの表を読み、argparse の集合と比べた | E2 の後 | 表の見出しが見つからない・表の形が変わった。テストは読めなかったことで落ちる | E2 |
| E6 | 一致テストが、欠けたサブコマンドと余分なサブコマンドを写しの場所ごとに挙げて落ちた | E3〜E5 のどれかで差が出た | — | E3〜E5 |
| E7 | 開発者が写しを直し、一致テストが通った | E6 | — | E6 |

E3〜E5 は互いの順序を仮定しない（どれか 1 つが落ちてもほかの比べ方は結果を出す）。

## 用語

| 用語 | 意味 |
| --- | --- |
| 列挙の正本 | `[name]` と `--context` を受け付けるサブコマンドの集合を決める唯一の場所。この課題では argparse の `_create_parser()`（トップレベルの `build` の `--context` だけはラッパー） |
| 一致テスト | 列挙の正本を走査して得た集合と、写し（ラッパーのリスト・補完・確定仕様の表）を比べ、差があれば写しの場所と差を挙げて落ちるテスト |

## 受け入れ条件

- [ ] AC1: 一致テストが `_create_parser()` を走査して得る `[name]` の集合が、前提 1 の表の「`[name]` を取る」列と一致する（`project` の直下 8 つ・`project profile` の 3 つ・トップレベル 6 つ）。`plugin` / `snapshot` / `env backend use` の `name` を含まない
- [ ] AC2: 一致テストが得る `--context` の集合が、前提 1 の表の「`--context` を取る」列と一致する（`env exec` / `env token`・入れ子の `profile`・トップレベルの `build` を含む）
- [ ] AC3: `bin/devbase` の `_PROJECT_NAME_SUBCOMMANDS` が「`project` の直下で `[name]` を取る parser」の集合と一致しなければ、一致テストが落ちる。確かめ方: 作業用の写しで `_PROJECT_NAME_SUBCOMMANDS` から `post-start` を消すと落ち、余分に `login` を足しても落ちる
- [ ] AC4: `bin/devbase` の `_NAME_RESOLVABLE_SHORTCUTS` が「`cli.SHORTCUTS` のキー ∪ {`build`}」と一致しなければ、一致テストが落ちる。確かめ方: `open` を消すと落ち、`logs` を足しても落ちる
- [ ] AC5: argparse の `project` の直下に `[name]` を取るサブコマンドを 1 つ足し、ラッパー・補完・表のどれも直さないとき、一致テストが落ちる。失敗の出力に、足したサブコマンドの名前と、取り残した写しの場所（ファイル）がそれぞれ出る
- [ ] AC6: bash の補完（`etc/devbase-completion.bash`）で、`project` の直下で `[name]` を取る 8 つと、トップレベルで `[name]` を取る 6 つの名前の位置で、`projects/` のプロジェクト名が候補に出る。どれか 1 つの分岐からプロジェクト名の補完を外すと一致テストが落ちる
- [ ] AC7: zsh の補完（`etc/_devbase`）で、AC6 と同じサブコマンドの分岐がプロジェクト名の補完（`_devbase_project_names`）を持つ。どれか 1 つから外すと一致テストが落ちる
- [ ] AC8: argparse の `project` / `container` の直下で `[name]` か `--context` を取るサブコマンド（入れ子の `profile` は親の `profile` として数える）が、bash と zsh の補完が出す `project` / `container` のサブコマンドの一覧にすべて含まれる。どちらかから 1 つ消すと一致テストが落ちる（`migrate-config` / `migrate-volume` のように、どちらも取らないサブコマンドが補完に無いことは問わない）
- [ ] AC9: 入れ子の `profile` の `[name]` を補完の一致から外していることが、一致テストの例外の一覧に理由つきで書かれている。例外の一覧に無いサブコマンドを補完が欠いたら落ちる（例外は黙って広がらない）
- [ ] AC10: `docs/specifications/cli-argument-resolution.md` に `[name]` を受け付けるサブコマンドの表と、`--context` を受け付けるサブコマンドの表がある。一致テストがこの 2 つの表を読んで argparse の集合（AC1・AC2）と比べ、表から 1 つ消すか余分に足すと落ちる
- [ ] AC11: `docs/` の下で、前提 7 の意味の列挙が `cli-argument-resolution.md` の外に残っていない。対象は少なくとも `docs/specifications/remote-docker-context.md` の「context の解決」・`docs/user/environment-variables.md` の `DEVBASE_DOCKER_CONTEXT` の行・`docs/user/cli-reference/02-project.md` の `profile` の注記と「`--context NAME`（共通オプション）」・`docs/user/container-operations.md` の冒頭の注記・`docs/specifications/compose-profiles.md` の `--context` の一文と `_PROJECT_NAME_SUBCOMMANDS` の写し。それぞれ、`cli-argument-resolution.md` の該当の表へのリンクになっている
- [ ] AC12: 置き換えた文書のリンクが、`cli-argument-resolution.md` の実在する見出しの位置を指す（リンクを辿って表に着く）
- [ ] AC13: `bin/devbase` と `lib/devbase/cli.py` の「同期注意」のコメント、`cli-argument-resolution.md` の「運用」の箇条が、手での同期の申し合わせではなく一致テストのファイルを指している
- [ ] AC14: 退行しない: `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` が全件通る。argparse の定義・ラッパーの 2 つのリストの中身・補完の振る舞いは変わらない（`git diff` で `lib/devbase/cli.py` の parser の定義、`bin/devbase` の 2 つのリストの値、`etc/` の補完の分岐に差が無い。コメントの差は許す）
- [ ] AC15: 一致テストは実の `DEVBASE_ROOT`・実の docker・実のプロジェクトに触らない（補完の確かめは一時ディレクトリの `projects/` で行う）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 一致テストの追加で `tests/cli` の実行時間が手元で 10 秒を超えて延びない（bash の補完の起動はサブコマンドごとに 1 回で、数十回に収まる） |
| 運用・保守性 | サブコマンドを足した開発者が、失敗の出力だけで直す場所（ファイル）と足りないサブコマンドの名前を知れる（AC5） |
| システム環境 | 一致テストは CI（ubuntu-latest）と macOS の手元の両方で通る。zsh が無い環境でも落ちない（zsh は静的に読む） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（CLI の引数・補完の振る舞い・終了コード） |
| データ | 無し |
| 既存の振る舞い | 変わらない。変わるのは文書の列挙（リンクへ）とテストの追加 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/cli -q`、全体は `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` |
| 静的解析・型検査 | `uvx ruff check --select=E9,F63,F7,F82 lib`、`python3 .github/scripts/proper_term_check.py` |
| テストが落ちることの確認（AC3〜AC10） | 作業用の写しで写しを 1 つずつ崩し、一致テストが落ちて差と場所を出すことを確かめる（記録は PR 本文に残す。コミットには崩した状態を残さない） |
| 文書のリンク（AC11・AC12） | `docs/` を `post-start`・`rebuild` / `open`・`--context` で検索し、列挙が `cli-argument-resolution.md` の外に無いことと、リンク先の見出しが実在することを確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「このリポジトリ」。テストは `tests/cli/`、確定仕様は `docs/specifications/`。`projects/*` と `repos/` は触らない |
| コーディング規約 | 周りのテスト（`tests/cli/test_completion.py`・`tests/cli/test_project_name_resolution.py`）の書き方に合わせる。検査は `AGENTS.md` の「検査」 |
| テスト戦略 | 単体の階層で argparse を走査し、写しを読んで比べる。bash の補完は実行して確かめ、zsh は内容を読む。ラッパーの name 解決の振る舞いは既存の `tests/cli/test_project_name_resolution.py` が担い、この課題では足さない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | `env -u DEVBASE_ROOT` を付けた全体のテスト、CI と同じ lint と固有の語の検査、作業は `.worktrees/` の worktree で行う |
| 確認してから行う | argparse の定義・ラッパーのリストの値・補完の振る舞いを変えること（この課題の範囲の外。必要が出たら止めて人へ戻す） |
| 行わない | 範囲外の文書の書き換え（使い方の行・対応表・地図）、`plugin` / `snapshot` の `name` の扱いの変更、`projects/*` の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 補完が `--context` を補完する範囲（いまは `open` / `post-start` だけ）を、`--context` を取るすべてのサブコマンドへ揃えるか。揃えるなら補完の振る舞いが変わるため、別の課題として起こすかを決める | 利用者（課題の棚卸し） | この課題のリリースまで |
| 既存の `tests/cli/test_rebuild.py` の `test_wrapper_rebuild_in_name_resolvable` を一致テストへ寄せて消すか残すか | 設計（`design`） | 設計 PR |
| `cli-argument-resolution.md` の `--context` の表を置く節（`[name]` の表と同じ節か、別の節か）と、表の機械の読み方（見出しで探すか、印を置くか） | 設計（`design`） | 設計 PR |
