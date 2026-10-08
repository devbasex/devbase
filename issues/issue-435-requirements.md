# #435: シェル補完が --context を open と post-start でしか出さない

正は課題の本文（#435）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何を見つけたか
>
> シェル補完が `--context` を出すのは `open` と `post-start` だけである（`etc/devbase-completion.bash` の 56・143 行、`etc/_devbase` の 169・224・229・263 行）。`--context` を取るほかの副コマンド（`up` `down` `ps` `logs` `login` `scale` `build` `rebuild`、`container` の配下、`env exec` など）では補完に出ない。2 つだけに出るのは機能を足した順の結果で、決めた範囲ではない。
>
> ## どうするか
>
> `--context` を取るすべての副コマンドで、bash と zsh の補完が `--context` を出すようにする。値（コンテキスト名）の補完はしない。#214 で入れた一致テスト（補完とコマンドの集合を比べるテスト）の比べる対象を `--context` まで広げ、食い違いの再発を防ぐ。
>
> ## 受け入れ条件
>
> - [ ] `--context` を取るすべての副コマンド（`container` の配下・`env exec` を含む）で、bash（`etc/devbase-completion.bash`）と zsh（`etc/_devbase`）の補完が `--context` を出す。値（コンテキスト名）は補完しない
> - [ ] #214 の一致テスト（`tests/cli/test_completion.py`）が `--context` を比べる対象に含み、補完から `--context` を 1 か所外すと落ちる
> - [ ] 全体のテスト（`env -u DEVBASE_ROOT uv run --locked pytest tests/ -q`）と CI と同じ lint が通る
>
> ## 由来
>
> #214 の要求の「未決」（補完が `--context` を補完する範囲）。m7e の棚卸しで、利用者が起票すると決めた（2026-10-07）。実害の報告は無い。
>
> Size: 2

## 目的

- `--context NAME` を受け付けるサブコマンドなら、どれでもシェル補完で `--context` を見つけられる状態にする。今は `open` と `post-start` だけに出るため、`devbase up myapp --context gpu-wsl`（[CLI リファレンス](docs/user/cli-reference/02-project.md)の例）のような使い方を、補完から知る手段が無い
- 補完の `--context` が [列挙の正本](docs/specifications/cli-argument-resolution.md#name-と---context-を受け付けるサブコマンド)（argparse）からずれたら、一致テストが落ちる状態にする。今の一致テストは補完の `--context` を比べない（確定仕様に「補完が `--context` の値を補完するかどうか（今は `open` と `post-start` だけ）は比べない」とある）

## 前提

- 前提 1: 原文の「#214 の一致テスト（`tests/cli/test_completion.py`）」は `tests/cli/test_name_context_consistency.py` を指す。#214 で入れた一致テストはこのファイルで、`test_completion.py` は補完の個々の振る舞いを固定するテストである（`docs/specifications/cli-argument-resolution.md` の「一致テストが比べる写し」）
- 前提 2: 「`--context` を取るすべての副コマンド」は、一致テストが列挙の正本から得る `--context` の集合（`CONTEXT_PATHS`）の 35 の道に等しい。原文の例に無い `env token` と、入れ子の `project profile up|down|list`・`container profile up|down|list` も含む。`ct` は `container` の別名として同じ扱いを受ける
- 前提 3: 補完が `--context` を出す位置は次の 2 つに限る。(a) サブコマンドの道の直後の位置。(b) `[name]` を受け付けるサブコマンド（`[name]` の集合から補完の例外を引いたもの）で、プロジェクト名の直後の位置（`devbase up web -<TAB>`・`devbase project up web -<TAB>`）。ほかの位置引数（`scale` の数・`profile` のプロファイル名・`login` の番号・`build` のイメージ名・`env exec` の `--` より後ろ）の後ろは対象にしない。zsh の `_arguments` が位置を問わず出すのは構わない
- 前提 4: bash で `--context` を出すのは、補完する語が `-` で始まるときだけとする（今の `open` / `ps` / `logs` / `post-start` と同じ「`-` で始まれば旗、そうでなければこれまでの候補」の分岐）。`-` で始まらない語の候補（プロジェクト名・`login` の `1 2`・`container scale` の `1 2 3 4 5` など）は変えない
- 前提 5: `-` で始まる語の候補は、そのサブコマンドの補完がすでに出している旗（`ps` の `--all -a`、`logs` の `--follow -f --tail`、`open` の `--open-index`）に `--context` を足したものになる。今の補完が出していないほかの旗（`up` の `--open` / `--no-open`、`build` の `--expires`、`env token` の `--print` など）は足さない
- 前提 6: `env token` は bash・zsh のどちらの補完でも `env` のサブコマンドの一覧に無い。この課題では `devbase env token -<TAB>` で `--context` を出すところまでを扱い、一覧へ `token` を足すことは扱わない（下の「対象範囲」の含まない）
- 前提 7: 一致テストの zsh の比べ方は、#214 と同じく zsh を起動せずに内容を読む（道の分岐の本体に `--context` の指定があるか）。入れ子の `profile` の道と、今は分岐の無い zsh のトップレベル `build`・`env token` には、道の分岐を足すことになる

## 対象範囲

含む:
- `etc/devbase-completion.bash` と `etc/_devbase` で、`--context` の集合のすべての道が前提 3 の位置で `--context` を出すこと
- `--context` の直後（値の位置）で、コンテキスト名を補完しないこと
- 一致テスト（`tests/cli/test_name_context_consistency.py`）で、bash（実行して）と zsh（内容を読んで）の補完が `--context` の集合のすべての道で `--context` を出すことを比べること
- 今の振る舞いを固定している `tests/cli/test_completion.py` の期待値のうち、`-` で始まる語の候補の集合（`ps` の `{"--all", "-a"}` など）を、`--context` を足した集合へ直すこと
- 確定仕様 `docs/specifications/cli-argument-resolution.md` の「一致テストが比べる写し」の表と、「補完が `--context` の値を補完するかどうか（今は `open` と `post-start` だけ）は比べない」の文を、新しい比べ方に合わせること
- `CHANGELOG.md` の `[Unreleased]` への記入（利用者に見える変更のため）

含まない:
- コンテキスト名（`docker context ls` の名前）の補完
- `--context` 以外の旗を補完へ足すこと（前提 5）
- `env` のサブコマンドの一覧へ `token` や `backend` を足すこと（前提 6。別の課題の候補）
- 前提 3 の 2 つの位置より後ろでの bash の旗の補完
- argparse の `--context` の受け付け（`lib/devbase/cli.py`）とラッパーの `cmd_build` の振る舞いの変更
- zsh を起動して補完を実行する試験

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 利用者が `--context` を受け付けるサブコマンドの後で `-` を打って補完を求めた | 利用者の Tab | — | 補完のファイルがシェルに読み込まれている |
| E2 | 補完が `--context` を含む旗の候補を出した | E1 | `--context` が候補に無い（今の `open` / `post-start` 以外の状態）。受け入れ条件 1・2 が防ぐ | E1 |
| E3 | 利用者が `--context` の後で補完を求め、補完が候補を出さなかった | `--context` の後の Tab | コンテキスト名やプロジェクト名が出る。受け入れ条件 3 が防ぐ | E2 |
| E4 | 開発者が `lib/devbase/cli.py` で `--context` を取るサブコマンドを足した | 機能の追加 | — | — |
| E5 | 一致テストが、補完の `--context` の欠けた道とファイルを挙げて落ちた | E4 の後の pytest（手元か CI） | 落ちずに通る（今の状態）。受け入れ条件 4 が防ぐ | E4 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 列挙の正本 | `[name]` と `--context` を受け付けるサブコマンドの集合の正本。argparse（`lib/devbase/cli.py` の `_create_parser()`） |
| 一致テスト | 列挙の正本を走査して得た集合と、写しを比べるテスト（`tests/cli/test_name_context_consistency.py`） |
| 写し | 列挙の正本を手で写した場所（`bin/devbase` の 2 つのリスト・補完 2 ファイル・確定仕様の表） |
| 補完の例外 | 補完がプロジェクト名を出さないことを理由つきで認める `[name]` の道（`project profile up|down|list`） |

## 受け入れ条件

- [ ] 1. bash: `--context` の集合のすべての道（35。`ct` で始めても同じ）で、`devbase <道> -` の最後の語を補完すると、候補に `--context` が含まれる
- [ ] 2. bash: `[name]` の集合から補完の例外を引いたすべての道で、`devbase <道> web -` の最後の語を補完すると、候補に `--context` が含まれる（`projects/web` のある一時の `DEVBASE_ROOT` で）
- [ ] 3. bash: `devbase up --context ''`・`devbase project up web --context ''`・`devbase container ps --context ''`・`devbase env exec --context ''` の最後の語を補完すると、候補が 0 件である（コンテキスト名もプロジェクト名も出ない）
- [ ] 4. zsh: `etc/_devbase` で `--context` の集合のすべての道の分岐の本体に、値の補完を持たない `--context` の指定（`'--context[...]:context:'` の形。`:` の後ろに補完の関数を置かない）がある
- [ ] 5. 一致テストが受け入れ条件 1・2・4 を、列挙の正本から得た集合で比べる。bash の補完から道 1 つの `--context` を外すと bash のテストが、zsh の補完から道 1 つの `--context` を外すと zsh のテストが落ち、落ちたテストの文に外したファイルと道が出る
- [ ] 6. 退行しない: `-` で始まらない語の補完の候補は変わらない。`tests/cli/test_completion.py` と一致テストのうち、`-` で始まる語の候補の集合を比べる期待値だけを `--context` を足した集合へ直し、ほかの期待値は変えずに通る
- [ ] 7. 確定仕様 `docs/specifications/cli-argument-resolution.md` の「一致テストが比べる写し」の表の bash と zsh の行が、補完の `--context` を比べることを書き、「補完が `--context` の値を補完するかどうか（今は `open` と `post-start` だけ）は比べない」の文が新しい比べ方と食い違わない
- [ ] 8. 全体のテスト（`env -u DEVBASE_ROOT uv run --locked pytest tests/ -q`）・CI と同じ lint（`uvx ruff check --select=E9,F63,F7,F82 lib`）・固有の語の検査（`python3 .github/scripts/proper_term_check.py`）が通る。補完 2 ファイルの構文の検査（`bash -n`・`zsh -n`。`tests/cli/test_completion.py` にある）も通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | `--context` を取るサブコマンドを `lib/devbase/cli.py` に足したとき、補完の写しを取り残すと一致テストが道を挙げて落ちる（受け入れ条件 5） |
| システム環境 | bash の補完は macOS の bash 3.2 と Linux の bash 5 のどちらでも動く書き方を保つ（今の補完と同じ。`bash -n` と一致テストが CI の Linux で通り、手元の macOS でも通る） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない。CLI の引数は変えない。補完の候補が増えるだけで、互換は保つ |
| データ | なし |
| 既存の振る舞い | `-` で始まる語の補完の候補に `--context` が加わる（`ps` の `--all -a` → `--all -a --context` など）。`-` で始まらない語の候補は変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q`（一致テストと `tests/cli/test_completion.py` を含む） |
| 静的解析・型検査 | `uvx ruff check --select=E9,F63,F7,F82 lib`、`python3 .github/scripts/proper_term_check.py`（`語の一覧が無いため飛ばした` と出たら検査はしていない） |
| 手動確認（マージ前） | 受け入れ条件 5 の「外すと落ちる」を確かめる。bash と zsh の補完からそれぞれ道 1 つの `--context` を一時的に外して一致テストを打ち、落ちたことと出た文を Pull Request に記録してから戻す |
| 手動確認 | リリース後テストで、手元の bash と zsh に補完を読み込み、`devbase up web -<TAB>`・`devbase env exec -<TAB>` で `--context` が出ること、`devbase up --context <TAB>` で候補が出ないことを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の表。補完は `etc/`、テストは `tests/cli/`、確定仕様は `docs/specifications/` |
| コーディング規約 | `AGENTS.md` の「書き方」と、補完 2 ファイルの今の書き方（bash は `_devbase_complete_flags` の分岐、zsh は `_arguments` の指定）に合わせる |
| テスト戦略 | bash は補完を実行して候補を比べる。zsh は内容を読む（#214 の決定 9 と同じ、道の分岐ごとに読む）。集合は列挙の正本の走査から得て、道を手で並べない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体のテスト・CI と同じ lint・固有の語の検査を push の前に打つ。`env -u DEVBASE_ROOT` を外さない |
| 確認してから行う | `--context` 以外の旗を補完へ足すこと、`env` のサブコマンドの一覧を直すこと |
| 行わない | `lib/devbase/cli.py` の引数の定義を変えること、コンテキスト名を補完すること、補完の例外を広げること |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| `env` の補完のサブコマンドの一覧に `token`・`backend` が無いことを別の課題にするか（前提 6） | 利用者（棚卸しで） | この課題の振り返りまで |
