# #236: 派生イメージに言語サーバを入れ、コンテナに入った時点でコードインテリジェンスが使えるようにする

正は課題の本文（#236）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> 言語サーバは base ではなく派生イメージで入れる（P1 に当たらない）。どの派生イメージに何を入れるかと、Serena の `uv` のキャッシュの扱いは設計で決める。判断待ちを解き、マイルストーン 04 で着手する。

（書き手: 利用者、日付: 2026-10-13。課題のコメントに「2026-10-13 の棚卸しで利用者が決めた」として記録された決定）

> 前倒しせず 04 に置く。m7c で base を削った直後のため、言語サーバを足すなら base ではなく派生イメージから検討する（MVV の Value 1。base へ入れるなら P1 の承認が要る）。

（書き手: 利用者、日付: 2026-10-03。課題のコメントに「2026-10-03 の棚卸しで利用者が決めたこと」として記録された決定）

> 言語サーバは base ではなく派生イメージで入れる（P1 に当たらない形）。base の Dockerfile は変えない設計にする。どの派生イメージ（containers/ の下）に何を入れるか、Serena の uv のキャッシュ（案 (a)/(b)）の扱い、増える大きさ（展開後の大きさ、用語集の語）を設計で決める。本文の「base に入れる」前提の受け入れ条件は派生イメージの形へ書き換える。

（書き手: AI の起動指示（利用者の決定を引用したもの）、日付: 2026-10-13）

> base コンテナに **言語サーバを入れておき**、コンテナに入った時点でコードインテリジェンス（定義・参照・診断・シンボル単位の編集）がすぐ使えるようにする。Serena 本体の取得を前もって済ませておくかは「決めること」で決める。

（書き手: 起票者、日付: 2026-10-03 ごろ。課題の最初の本文。「base に入れる」は上の利用者の決定で派生イメージへ変わった。最初の本文の全文はこの課題の編集の履歴にある）

## 目的

コンテナに入った時点で、Claude Code の診断（公式 LSP プラグイン）と Serena のシンボルの把握・編集が、Python・TypeScript・PHP のコードで使える状態にする。base は重くせず（MVV の Value 1）、言語サーバは派生イメージに置く。

背景（起票の時点の調べ。2026-10-03）:

- ai-plugins 側（[devbasex/ai-plugins#818](https://github.com/devbasex/ai-plugins/issues/818)、`docs/specifications/mcp-serena-language-servers.md`）は、言語サーバの本体を利用者（マシン・コンテナ）が PATH の上に入れる前提にしている。ai-plugins の `check` は欠けを知らせるだけで入れない
- Serena の MCP は常に `uvx --from serena-agent==1.7.0 serena start-mcp-server ...` で起動する（ai-plugins の決定 4: 導入済みの Serena を優先する起動のラッパーは作らない）。`uv tool install` で PATH に置いた `serena` は使われず、効くのは `uvx` が読む uv のキャッシュだけである
- uv のキャッシュの既定の置き場は `~/.cache/uv` で、base のクリーンアップ（`containers/base/Dockerfile` のユーザー用ツールの RUN）が `~/.cache` を消す。`~/.cache` は永続化の対象でもないため、コンテナを作り直すと消える
- base の `npm i -g` が入れる `typescript` は latest の 7.0.2 で、`lib/tsserver.js` を持たない。`typescript-language-server` は typescript 5 系を要る

## 調べたこと（2026-10-13、arm64、既存の `devbase-base:latest`（e10369aa02c4）で `docker run --rm` の使い捨てのコンテナ）

| 項目 | 値 |
| --- | --- |
| base の Python / uv / `tsc` | Python 3.14.4 / uv 0.12.22 / `tsc` 7.0.2。グローバルの `typescript/lib/tsserver.js` は無い |
| npm の `typescript` の `latest` | 7.0.2（5 系の最新は 5.9.3） |
| `npm i` で入る版 | `pyright` 1.1.414、`typescript-language-server` 6.0.2、`typescript@5` 5.9.3、`intelephense` 1.18.5 |
| Python・TypeScript の言語サーバ（`pyright` + `typescript-language-server` + `typescript@5`）の大きさ | 45,519,343 バイト（依存を含む `node_modules`） |
| PHP の言語サーバ（`intelephense`）の大きさ | 126,043,484 バイト（依存を含む） |
| `uvx --from serena-agent==1.7.0 serena --help` で温めた uv のキャッシュ | 130,841,053 バイト。base の Python 3.14 で動き、uv が別の Python を取りに行かなかった。温めた後、`uvx --offline` で同じコマンドが終了コード 0 |
| `pyright-langserver --version` / `intelephense --version` | どちらも終了コード 1（`--version` を持たない。`--stdio` などの指定を求める）。`typescript-language-server --version` は 6.0.2 を返す |
| ai-plugins の `typescript_major_5` の調べ方 | `typescript-language-server` の実体のパスの親を順にたどり、最初に見つけた `node_modules/typescript/package.json` の版が 5 か。グローバルの prefix に入れると base の `typescript` 7.0.2 に当たる |

この端末の `projects/*` が dev サービスを建てる派生イメージ（数だけ。名前は書かない）: `general` 24、`php` 9、`php85` 1、`latex` 1、`bi-tools` 1、`trygroup` 1。`browser`・`go` を使うプロジェクトはこの端末に無い。

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 対象の言語は少なくとも Python・TypeScript・PHP | 最初の本文の「決めること」（ai-plugins の対応表から選んだ） | 前提 2 |
| S2 | 言語サーバは ai-plugins の対応表（`languages.json`）と同じ導入方法（`npm install -g`）で入れる | 最初の本文の作業項目 | 採らない。入れ方（prefix・グローバルか）は設計で決める。受け入れ条件 3〜5 は PATH の上に見えることと起動することだけを求める |
| S3 | `tsc --version` が 5 系を返す（グローバルの `typescript` を 5 へ固定する） | 最初の本文の受け入れ条件 2・作業項目 | 採らない。base の `typescript` は base の Dockerfile にあり変えない。派生イメージで `tsc` の版が変わるかは設計で決め、受け入れ条件 7 で扱う |
| S4 | `pyright-langserver --version` / `intelephense --version` が通る | 最初の本文の受け入れ条件 1 | 採らない。2026-10-13 の測定で両方とも `--version` を持たない。受け入れ条件 4 の起動の確認へ置き換える |
| S5 | 版を固定する | 最初の本文の作業項目 | 受け入れ条件 6 |
| S6 | 起動の確認をコンテナの検査（`tests/containers/`）に足す | 最初の本文の作業項目 | 受け入れ条件 6（静的な検査）。建てたイメージでの起動はテストで常に走らせず、検証手段の手動確認（マージ前）にする |
| S7 | amd64 でも建てて確かめる | 最初の本文の受け入れ条件 | 非機能の条件のシステム環境と、検証手段の手動確認（リリース後テスト） |
| S8 | (a) を選んだ場合、uv のキャッシュの置き場は `ubuntu` が書ける持ち主にする | 最初の本文の作業項目 | 受け入れ条件 9 |
| S9 | Serena が PATH の上の言語サーバを使うか、`~/.serena/language_servers` へ取りに行くかを確かめる | 最初の本文の作業項目 | 未決 3 |
| S10 | `~/.serena` は `/persistent/ai` に永続化されるため Serena が自分で取る言語サーバは端末ごとに初回 1 回で済む | 最初の本文の「なぜ base に入れるか」 | 未決 3 で確かめ直す（ai-plugins の `.mcp.json` は `SERENA_HOME=.serena` の相対パスを渡しており、置き場が `~/.serena` でない可能性がある） |

## 前提

- 前提 1: base の Dockerfile と `containers/base/` の下のファイルは変えない（利用者の決定。P1 に当てない）
- 前提 2: 対象の言語は Python・TypeScript・PHP の 3 つである。Go（`gopls`）など対応表のほかの言語は対象にしない
- 前提 3: 言語サーバを入れる派生イメージは、少なくとも `general` に Python と TypeScript、`php` と `php85` に PHP を含む。ほかの組み（`php` に Python・TypeScript も入れるか、`latex`・`bi-tools`・`trygroup`・`browser`・`go` に入れるか）は設計で決め、決定に理由を書く。設計がこの最小の組みから外すときも、決定に理由を書く
- 前提 4: Serena の扱いは設計で (a)（uv のキャッシュをイメージで温める）か (b)（言語サーバだけを入れ、Serena の取得は実行時の `uvx` に任せる）を選ぶ。どちらを選んでも受け入れ条件は下のとおりに分けて書く
- 前提 5: ai-plugins の Serena の版の固定は 2026-10-13 の時点で `serena-agent==1.7.0` である。(a) を選ぶ場合、イメージの版の固定はこの値に揃える
- 前提 6: 1 つの派生イメージで増える展開後の大きさは、2026-10-13 の測定（言語サーバの全部 約 172MB、uv のキャッシュ 約 131MB）から、350,000,000 バイト以下に収まる

## 対象範囲

含む:

- 派生イメージ（`containers/` の下の base 以外）の Dockerfile に、Python・TypeScript・PHP の言語サーバを足す
- `typescript-language-server` が typescript 5 系を解決するようにする
- Serena の uv のキャッシュの扱い（(a)/(b)）を決め、(a) ならイメージで温める
- 増える展開後の大きさの測定と記録
- 版の固定と、それを止める静的な検査（`tests/containers/`）
- `CHANGELOG.md` の `[Unreleased]`

含まない:

- base の Dockerfile と `containers/base/` の変更（前提 1）
- base の `npm i -g` にある `typescript`（7.0.2）の扱い
- Go・Ruby・Rust など前提 2 の外の言語
- ai-plugins 側の変更（`.mcp.json`・`languages.json`・`check` の直し）。要るなら ai-plugins へ別に起票する
- Claude Code の公式 LSP プラグインの導入（利用者の `~/.claude` の設定にあたる）
- プラグインのリポジトリ（`projects/*`）の `compose.yml` の変更（どの派生イメージを使うかは各プロジェクトが選ぶ）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 派生イメージを建てるときに言語サーバを入れた | `devbase build` / `docker compose build` | 取得に失敗するとビルドが止まる（イメージは前のまま残る） | base のイメージが手元にある |
| E2 | (a) のとき、建てるときに `serena-agent` を uv のキャッシュへ温めた | E1 と同じ建て | 取得に失敗するとビルドが止まる | uv が base にある |
| E3 | 派生イメージからコンテナを作って起動した | `devbase up`（利用者が打つ） | 既存の起動の失敗と同じ | E1（と E2） |
| E4 | Claude Code の公式 LSP プラグインが PATH の上の言語サーバを起動した | 利用者が `.py` などを開いて編集した | 言語サーバが見つからないと診断が届かない（プラグインがそう知らせる） | E3、利用者がプラグインを入れている |
| E5 | Serena の MCP が `uvx --from serena-agent==1.7.0` で起動した | Claude Code / Codex が MCP を起動した | (a) でキャッシュに当たらない（版が違う・置き場を読めない）と取得に行き、ネットワークが無いと起動しない | E3。(a) なら E2 |
| E6 | Serena が言語サーバを使い始めた | Serena が言語のコードを初めて扱った | PATH の上を使うか自分で取得するかは未決 3。取得なら初回にネットワークが要る | E5 |
| E7 | ai-plugins が `serena-agent` の版の固定を上げた | ai-plugins の配布 | (a) の温めたキャッシュは外れ、E5 の起動で新しい版を取得する。ネットワークが無いと起動しない | — |

E5・E7 の失敗の経路は受け入れ条件 10 と未決 2 で扱う。E4 の「利用者がプラグインを入れている」は対象範囲の「含まない」とした。

## 用語

| 用語 | 意味 |
| --- | --- |
| 派生イメージ | 用語集の語。`FROM devbase-base:latest` で base を継ぎ、道具を足すイメージ |
| 展開後の大きさ | 用語集の語。建てたイメージから作ったコンテナで `du -sbx /` を測ったバイト数 |
| 言語サーバ | エディタや AI の道具へ、定義・参照・診断を Language Server Protocol で返すプログラム。この課題では `pyright-langserver`・`typescript-language-server`・`intelephense` |
| uv のキャッシュ | `uv` / `uvx` が取得したパッケージと作った環境を置く場所。置き場は `UV_CACHE_DIR`、既定は `~/.cache/uv` |

## 受け入れ条件

base を変えないこと:

- [ ] 1. `git diff main -- containers/base/` が空である

言語サーバ（設計の決定で対象にした派生イメージと言語の組みごとに確かめる。前提 3 の最小の組みを含む）:

- [ ] 2. 対象の派生イメージで `docker run --rm <イメージ> bash -c 'command -v <コマンド>'`（ログインシェルでない非対話のシェル）が、言語ごとのコマンド（`pyright-langserver`・`typescript-language-server`・`intelephense`）のパスを返す
- [ ] 3. 対象の派生イメージで、言語サーバのコマンドを `--stdio` で起動し LSP の `initialize` を送ると、`result.capabilities` を持つ応答が 30 秒以内に返る
- [ ] 4. 対象の派生イメージで、`typescript-language-server` の実体のパス（`realpath`）の親を順にたどって最初に見つかる `node_modules/typescript/package.json` の版が `5.` で始まり、同じ `typescript` の下に `lib/tsserver.js` がある
- [ ] 5. 対象の派生イメージで ai-plugins の `check`（`/mcp-serena:language-servers` のインストールチェック、ランタイムは Claude Code）を打つと、Python・TypeScript・PHP のうち対象にした言語について、`binary`（本体）と `typescript_major_5`（TypeScript の版）の欠けが出ない。`plugin`（公式 LSP プラグインの導入）の欠けは利用者の設定のため問わない
- [ ] 6. `tests/containers/` の静的な検査が、足した npm のパッケージ（と (a) のときの `serena-agent`）の版が厳密な版（`@5.9.3` の形。範囲や `latest` でない）で書かれていることを止め、`env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` が通る
- [ ] 7. 対象の派生イメージの `tsc --version` が、設計の決定に書いた版を返す。base の `tsc`（7.0.2）から変わるなら、`CHANGELOG.md` の `[Unreleased]` にそのイメージと版を書く

Serena の uv のキャッシュ:

- [ ] 8. (a) を選んだ場合: 対象の派生イメージで `docker run --rm --network none <イメージ> bash -c 'uvx --from serena-agent==1.7.0 serena --help'` が終了コード 0 で終わる
- [ ] 9. (a) を選んだ場合: 対象の派生イメージで、uv のキャッシュの置き場（`UV_CACHE_DIR`）が `~/.cache` の外にあり、`ubuntu` で `test -w "$UV_CACHE_DIR"` が通る
- [ ] 10. (a) を選んだ場合: イメージで温める `serena-agent` の版が、ai-plugins の `plugins/mcp/mcp-serena/.mcp.json` の固定（前提 5）と同じ文字列であり、Dockerfile のその行のコメントが ai-plugins の `.mcp.json` を出所として名指す
- [ ] 11. (b) を選んだ場合: 設計の決定に (b) を選んだ理由と、初回の起動にネットワークが要ることが書かれ、派生イメージに `UV_CACHE_DIR` も `serena-agent` も足されていない

大きさと退行:

- [ ] 12. 実装の Pull Request の本文に、対象の各派生イメージの展開後の大きさを変更の前と後で記し、測った端末（アーキテクチャ）とイメージストアを書く。増えた分が 1 つのイメージにつき 350,000,000 バイト以下である（前提 6）
- [ ] 13. 対象にしなかった派生イメージの Dockerfile は変わっていない（`git diff main -- containers/<名前>/` が空）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 1 つの派生イメージで増える展開後の大きさは 350,000,000 バイト以下（受け入れ条件 12） |
| 運用・保守性 | 言語サーバと `serena-agent` の版は厳密に固定し、検査で止める（受け入れ条件 6）。ai-plugins が `serena-agent` の固定を上げたときの揃え方は未決 2 |
| システム環境 | arm64 と amd64 の両方で、受け入れ条件 2〜5 と (a) のときの 8 が通る（amd64 は手動確認） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない。devbase の CLI と設定の形は変えない |
| データ | 無し。ボリュームの中身は変えない |
| 既存の振る舞い | 対象の派生イメージに言語サーバのコマンドが PATH の上に増える。設計次第で、そのイメージの `tsc` の版と、(a) なら `UV_CACHE_DIR`（`uv` を使うすべての処理のキャッシュの置き場）が変わる。利用者がイメージを建て直すまでは届かない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` |
| 静的解析 | `uvx ruff check --select=E9,F63,F7,F82 lib`、`python3 .github/scripts/proper_term_check.py`、ShellCheck（開発参加ガイドの「CI が実行するもの」） |
| 手動確認（マージ前） | 対象の派生イメージを手元の `devbase-base:latest` の上に建て、受け入れ条件 2〜5・7・8・9・12 を `docker run --rm` で確かめる。`devbase up` は打たない（作業用のプロジェクトでも実のボリュームが付く。AGENTS.md の落とし穴）。base は建てない。非対話の担当は `ubuntu`・`composer:latest` などの取得がキーチェーンで止まり得るため、建てられなかったら「判断が要る」で返す（AGENTS.md の落とし穴） |
| 手動確認 | amd64 の端末で対象の派生イメージを建て、受け入れ条件 2〜5・8 を確かめる。利用者のコンテナの中の Claude Code で `.py` を編集し、公式 LSP プラグインの診断が届くことを見る（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「このリポジトリ」。変更は `containers/<派生イメージ>/`・`tests/containers/`・`CHANGELOG.md`。`containers/base/` と `projects/*` には置かない |
| コーディング規約 | `AGENTS.md` の「書き方」と「検査」。Dockerfile は周りの派生イメージ（`bi-tools`・`trygroup` の `USER ubuntu` で `npm i -g` する書き方）に合わせる |
| テスト戦略 | Dockerfile の静的な検査（`tests/containers/` の既存の `dockerfile_parse.py` を使う形）で版の固定と base の不変を止める。建てたイメージでの起動は手動確認（マージ前）で確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体のテスト・lint・固有の語の検査。展開後の大きさの記録。`CHANGELOG.md` の記入 |
| 確認してから行う | 対象の派生イメージの選び方と `tsc` の版の変化（承認ゲート 1・2 で人が見る） |
| 行わない | base の Dockerfile の変更（P1）。base を建てる。`devbase up`（P3）。ボリュームを丸ごと写す。ai-plugins の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. どの派生イメージにどの言語サーバを入れるか（前提 3 の最小の組みの外）と、共通の入れ方（各 Dockerfile に書くか、共通の断片を持つか） | 設計（承認ゲート 1 で利用者が見る） | 設計 PR |
| 2. Serena の扱い (a)/(b)。(a) なら ai-plugins の版の固定が上がったときに揃える手順（検査で気づくか、手で揃えるか） | 設計（承認ゲート 1 で利用者が見る） | 設計 PR |
| 3. Serena が PATH の上の言語サーバを使うか、自分で取得するか。取得するなら置き場（`.mcp.json` の `SERENA_HOME=.serena` が指す先）と、作り直しで残るか | 設計の調べ | 設計 PR |
| 4. 派生イメージでの `typescript` 5 の入れ方（グローバルの prefix を 5 へ上書きして `tsc` も変えるか、言語サーバ用の別の場所に置いて `tsc` は 7 のまま残すか） | 設計（承認ゲート 1 で利用者が見る） | 設計 PR |

🤖 Generated with [Claude Code](https://claude.com/claude-code)
