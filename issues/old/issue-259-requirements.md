# #259: ci: containers/base の entrypoint.sh・ai-cli-aliases.sh・shellrc-dir.sh が ShellCheck の対象外で、既存の指摘が 10 件ある

正は課題の本文（#259）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

CI の ShellCheck ジョブ（`.github/workflows/ci.yml` の `shellcheck`）が検査するのは `bin/` と `install.sh` と `containers/base/tmux-first` / `tmux-clean` / `tmux-session` だけで、`containers/base/` のほかのシェルスクリプトはどこでも検査されていない。base イメージの shellcheck 0.11.0（`devbase-base:latest`、2026-09-26）で走らせると次のとおりだった。

| ファイル | 指摘（既定の severity） | うち error |
| --- | --- | --- |
| `containers/base/entrypoint.sh` | 7（SC2046 ×4・SC2034・SC2317・SC2009） | 0 |
| `containers/base/ai-cli-aliases.sh` | 1（SC2148: shebang も `shell` 指示も無い） | 1 |
| `containers/base/shellrc-dir.sh` | 2（SC2148・SC1090） | 1 |
| `containers/base/dind` | 0 | 0 |
| `containers/base/tmux-first` / `tmux-clean` / `tmux-session` | 0 | 0 |

SC2046 は `entrypoint.sh` の 631・652 行目で、引用の無いコマンド置換が単語分割される。

## どこで見つけたか

`.github/workflows/ci.yml` の `shellcheck` ジョブ。devbasex/devbase#234 の設計で、新しく作る `containers/base/tmux-session` が検査の対象になるかを確かめたとき。

## なぜこの変更の範囲外なのか

#234 の設計（`issues/old/PLAN69_tmux-named-session-design.md` の決定 6）は、CI の ShellCheck に `containers/base/tmux-first` / `tmux-clean` / `tmux-session` の 3 つだけを足す。`entrypoint.sh`・`ai-cli-aliases.sh`・`shellrc-dir.sh` は tmux の変更と関係が無く、指摘の片付けを伴う。`bin/devbase` の既存の指摘は #247 が扱っており、同じ形の片付けになる。

## 直さないと何が起きるか

コンテナの起動を担う `entrypoint.sh` の引用漏れのような指摘が、変更のたびに検査されずに入る。`ai-cli-aliases.sh` と `shellrc-dir.sh` は shebang が無いため、言語サーバ（bash-language-server）が対象のシェルを判定できず、診断が不正確になる。

## 由来

issue #234

## 依頼（原文）

> CI の ShellCheck ジョブ（`.github/workflows/ci.yml` の `shellcheck`）が検査するのは `bin/` と `install.sh` と `containers/base/tmux-first` / `tmux-clean` / `tmux-session` だけで、`containers/base/` のほかのシェルスクリプトはどこでも検査されていない。

（上の「何を見つけたか」から。本文に直し方の案は無く、表題の「対象外で、既存の指摘が 10 件ある」の解消を依頼と読む）

## 目的

- `containers/base/` のシェルスクリプトをすべて CI の ShellCheck の対象にし、既定の水準で 0 件にする。以後、対象から漏れたスクリプトが増えたら気づける

## 前提

- 前提 1: 指摘は shellcheck 0.11.0 の既定の水準で `entrypoint.sh` 7 件（SC2317・SC2046 ×4・SC2034・SC2009）、`ai-cli-aliases.sh` 1 件（SC2148）、`shellrc-dir.sh` 2 件（SC2148・SC1090）、`dind` と `tmux-*` は 0 件（2026-09-26、`main` = `5edc75f`、`devbase-base:latest`）
- 前提 2: `ai-cli-aliases.sh` と `shellrc-dir.sh` は `~/.bashrc` から bash で source されるファイルで、`0644` で置かれ実行されない（`containers/base/Dockerfile:236-249`）。shebang を足して実行できるように見せるかは設計で決める
- 前提 3: `entrypoint.sh` は `set -e` で動く。振る舞いを変えない。直すと振る舞いが変わりうる指摘（SC2046 の引用の追加で単語分割がなくなる箇所など）は、変わらないことをテストで確かめてから直すか、抑止の注記にする
- 前提 4: shellcheck の版と CI の版の揃え方は #247 に従う（#247 の前提 5）
- 前提 5: 「シェルスクリプト」は、`containers/base/` の直下の通常のファイルのうち、先頭行が `sh` か `bash` を指す shebang か、拡張子が `.sh` のものを指す。2026-09-26 時点で `entrypoint.sh`・`ai-cli-aliases.sh`・`shellrc-dir.sh`・`dind`・`tmux-first`・`tmux-clean`・`tmux-session` の 7 本

## 対象範囲

含む:
- `containers/base/entrypoint.sh`・`ai-cli-aliases.sh`・`shellrc-dir.sh`・`dind` を CI の ShellCheck に足すこと
- 10 件の指摘を 1 件ずつ直すか抑止の注記にすること
- `containers/base/` に置いたシェルスクリプトが CI の対象から漏れていたら落ちるテスト

含まない:
- `containers/` の他のディレクトリ（`containers/base/` 以外）のスクリプト
- `bin/devbase` の指摘（#247）
- base イメージの再ビルドを要する変更の実機での確かめ（振る舞いを変えないため）
- 型・永続データ・API・画面の追加と変更。シェルスクリプトの注記と引用・CI の設定・テストだけで作るため、設計にクラス図・データ構造・入出力の契約の節を持たない
- 非機能の条件。実行時の振る舞いを変えず、CI の ShellCheck ジョブに 1 回の呼び出しが増えるだけのため、非機能設計表を持たない

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | `containers/base/` のシェルを変える Pull Request を出した | 開発者 | — | — |
| E2 | ShellCheck ジョブが `containers/base/` のシェルを検査した | E1 | 指摘があればジョブが失敗する | 既存の 10 件が片付いている |
| E3 | `containers/base/` に新しいシェルスクリプトを足した | 開発者 | CI の対象に足し忘れる → pytest が落ちる | — |

## 用語

| 用語 | 意味 |
| --- | --- |
| 指摘 | shellcheck が出す 1 件（SC の番号・水準・行） |
| 抑止の注記 | 指摘を抑える `# shellcheck` の指示と、抑える理由のコメントの組 |

## 受け入れ条件

- [ ] shellcheck 0.11.0 の既定の水準で `containers/base/` の `entrypoint.sh`・`ai-cli-aliases.sh`・`shellrc-dir.sh`・`dind`・`tmux-first`・`tmux-clean`・`tmux-session` が 0 件（exit 0）
- [ ] CI の ShellCheck ジョブが上の 7 本を既定の水準で検査する（ジョブのログに 7 本のパスが出るか、7 本を並べた 1 つの呼び出しがある）
- [ ] `containers/base/` の直下に、前提 5 のシェルスクリプトで CI の ShellCheck の対象に無いものがあると、pytest が落ち、落ちた理由にそのファイル名が出る
- [ ] 上のテストは、シェルスクリプトでないファイル（`Dockerfile`・`tmux.conf`・`fonts-local.conf`）を対象に数えない
- [ ] 抑止の注記を置いた行には、抑える理由が同じ行か直前の行に書いてある
- [ ] 振る舞いが変わらない: `entrypoint.sh` を `DEVBASE_ENTRYPOINT_LIB_ONLY` で source するテスト（`tests/containers/test_entrypoint_*.py`）・`test_ai_cli_aliases.py`・`test_shellrc_dir.py` が、件数を減らさずすべて通る
- [ ] `uv run --locked pytest tests/ -q` がすべて通る

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない |
| 既存の振る舞い | 変わらない。base イメージの中身はコメント・指示・引用の追加だけ変わる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 静的解析 | `docker run --rm -v "$PWD":/w -w /w --entrypoint shellcheck devbase-base:latest containers/base/entrypoint.sh containers/base/ai-cli-aliases.sh containers/base/shellrc-dir.sh containers/base/dind containers/base/tmux-first containers/base/tmux-clean containers/base/tmux-session` |
| テスト | `uv run --locked pytest tests/ -q` |
| 手動確認 | 実装 Pull Request の ShellCheck ジョブが成功する。漏れの検出は、手元で `containers/base/` に空のシェルスクリプトを 1 本置いてテストが落ちることを見て消す |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 漏れを見るテストは `tests/containers/` の下 |
| コーディング規約 | `CONTRIBUTING.md`。シェルは shellcheck の既定の水準 |
| テスト戦略 | 振る舞いは既存の source するテストで守る。CI の対象の一覧は `ci.yml` を読むテストで固定する |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 指摘ごとの判断（直す / 抑止の注記）を設計の決定に残す |
| 確認してから行う | 振る舞いが変わりうる書き換え（行わずに抑止の注記にする） |
| 行わない | 指摘の無い行の整形 |
