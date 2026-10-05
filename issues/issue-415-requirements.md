# #415: devbase build が compose.yml の最初の build: から Dockerfile を決め、dev のサービスの Dockerfile と食い違いうる

正は課題の本文（#415）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何を見つけたか
>
> `devbase build`（通常の経路）は、プロジェクトの Dockerfile の場所を `compose.yml` の**最初の** `build:` ブロックの直後 3 行から決める。dev のサービスの `build:` かどうかを見ない。`build: ./dir` のような文字列の形も読まない（`dockerfile:` も `context:` も見つからず、カレントディレクトリの `Dockerfile` を読む）。
>
> 一方、`--expires` の判定（`lib/devbase/commands/container.py` の `_get_base_image_ref`）は、`docker compose config` から dev のサービスの `build` を取り、そこから Dockerfile を決める。
>
> そのため、次のプロジェクトでは 2 つの経路が別の Dockerfile を読む（`bin/devbase` を読んだ結果。実機では未再現）。
>
> - `compose.yml` の中で、dev より前に `build:` を持つ別のサービスがある
> - dev の `build:` が文字列の形（`build: ./dev`）
>
> ## どこで見つけたか
>
> - `bin/devbase` の `cmd_build` の中の `compose_build_field`（`grep -A 3 "build:" compose.yml | grep "$1:" | head -1`）と `resolve_project_dockerfile`
> - `lib/devbase/commands/container.py` の `_get_base_image_ref`（`dev_service.get('build')` の文字列の形と辞書の形を両方読む）
>
> ## なぜこの変更の範囲外なのか
>
> issue #404 の受け入れ条件 7 は、Dockerfile の**行の読み方**（`--platform`・小文字の `from` など）を 2 つの経路で揃えることだけを求める。**どの Dockerfile を読むか**の決め方は受け入れ条件に無く、#404 の境界は「依頼の範囲外の `cmd_build` の書き直し」を行わないとしている。根拠: Value 1（MVV 版 1）
>
> ## 直さないと何が起きるか
>
> 上の形のプロジェクトでは、通常のビルドが dev の Dockerfile と違うファイルから継承の連なりを決め、dev が実際に継ぐイメージを先に建てない（#404 で直す見落としと同じ結果になる）。`--expires` の判定とも別のイメージを見る。今のプラグインのプロジェクト（36 件）は、どれも dev の `build:` が最初の `build:` で、辞書の形で書かれているため、すぐには表に出ない。
>
> ## 由来
>
> issue #404 の設計中に見つけた。

## 目的

- 通常のビルド（`bin/devbase` の `cmd_build`）が、`docker compose build` で実際に建てる開発サービスの Dockerfile から継承の連なりを決めるようにする。compose の書き方（サービスの並び・`build` の形）によって、建てる段が変わらない状態にする
- 通常のビルドと `--expires` の判定が、同じ compose の構成から同じプロジェクトの Dockerfile を読む状態にする（#404 で揃えた直の親の読み方の、1 段手前を揃える）

## 前提

- 前提 1: 「dev のサービス」は開発サービス名（`DEV_SERVICE_NAME`、既定 `dev`）のサービスを指す。`cmd_build` が `docker compose build` に渡す名前（`${DEV_SERVICE_NAME:-dev}`）と、`--expires` の判定の `get_dev_service_name()` は今も同じ値を使っている
- 前提 2: プロジェクトの Dockerfile の場所は、`docker compose build <開発サービス名>` が読むのと同じ compose の構成（同じ compose ファイルの探し方・同じ環境変数の展開）から決める。`compose.yml` を行の並びとして読む方法は採らない。`--expires` の判定が使う `docker compose config` の結果に合わせる
- 前提 3: compose の構成を読む処理は docker の daemon に繋がない。`--context` で別の daemon を指すときも、プロジェクトの Dockerfile は手元のファイルとして読む（今と同じ）
- 前提 4: 開発サービスにプロファイルは付かない（開発サービスは既定のサービスである）。プロファイルを指定せずに読んだ構成に開発サービスが現れる
- 前提 5: 今の手元のプラグインのプロジェクト 36 件は、どれも開発サービスの `build:` が `compose.yml` の最初の `build:` で、辞書の形である（2026-10-05 に `projects/*/compose.yml` を数えた。2 つの `build:` を持つものが 1 件あり、その 1 件も最初は開発サービス）。`DEV_SERVICE_NAME` を `env` に書いたプロジェクトは 0 件。直した後もこの 36 件の建てる段は変わらない見込みである
- 前提 6: 開発サービスが `build` を持たないとき（`image:` だけ）、プロジェクトの Dockerfile は無いものとし、`devbase-*` を `FROM` に取らないプロジェクトと同じ扱い（確定仕様の I8）にする。今は「カレントディレクトリの `Dockerfile`」か「別のサービスの Dockerfile」を読んでおり、ここは振る舞いが変わる
- 前提 7: compose の構成を読めないとき（`docker compose config` が 0 以外で終わる。必須の環境変数が無い・YAML が壊れている など）、通常のビルドはどの段も建てずに止まる。続く `docker compose build` も同じ構成を読んで失敗するため、先に止まっても利用者が失うものは無い

## 対象範囲

含む:
- 通常のビルド（`devbase build`・`devbase build --no-cache`・`--project-no-cache` の経路。`devbase up` と `rebuild` が内部で呼ぶものを含む）が、プロジェクトの Dockerfile を開発サービスの `build` から決めること
- `build` の文字列の形（`build: ./dev`）と辞書の形（`context`・`dockerfile`。`dockerfile` の絶対パス・`context` の環境変数の展開を含む）
- 通常のビルドと `--expires` の判定が、Dockerfile の場所の決め方を共有すること（同じ入力に同じパスを返すことをテストで縛る）
- 開発サービスが `build` を持たないとき・compose の構成を読めないときの振る舞い
- 確定仕様 `docs/specifications/base-image-chain-build.md` の、Dockerfile の場所の決め方の記述の更新（「含まない」に置いた食い違いの記述を消す）
- `CHANGELOG.md` の `[Unreleased]` の記入

含まない:
- 直の親の読み方（#404 で揃えた行の規則）と、継承の連なりの解決・段の建て方の変更
- `build.dockerfile_inline`（Dockerfile を compose の中に書く形）を読むこと。今のプラグインのプロジェクトに使う例は無い。この形のときに 2 つの経路が同じ結果になることだけを求め、どう読むかは決めない
- `build.target`（多段の Dockerfile の段の指定）による直の親の選び分け。今と同じく、Dockerfile の中で最初に `devbase-*` を指す `FROM` を読む
- 開発サービス以外のサービスの Dockerfile が `devbase-*` を継ぐときに、その連なりを建てること
- `cmd_build` の、Dockerfile の場所の決め方の外の書き直し
- `projects/*` のプラグインのリポジトリの `compose.yml` の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | compose の構成を読んだ | `devbase build`（通常・`--no-cache`・`--project-no-cache`）、`devbase up`・`rebuild` が内部で呼ぶビルド、`--expires` の判定 | 構成を読めない: 通常のビルドは理由を出して終了コード 1 で止まり、どの段も建てない（前提 7）。`--expires` の判定は今と同じくキャッシュありのビルドへ進む | — |
| E2 | プロジェクトの Dockerfile の場所を決めた | E1 の構成の開発サービスの `build` | 開発サービスが無い・`build` を持たない: プロジェクトの Dockerfile は無い（前提 6） | E1 |
| E3 | 直の親イメージを読んだ | E2 で決めた Dockerfile | Dockerfile が無い・`devbase-*` を指す行が無い: `devbase-*` を使わないものとして I8 の分岐へ進む（今と同じ） | E2 |
| E4 | 継承の連なりを解決した | E3 の直の親 | 確定仕様の I3 のとおり（今と同じ） | E3 |
| E5 | 連なりの段を下から建てた | E4 の連なり | 確定仕様のとおり（今と同じ） | E4 |
| E6 | 開発サービスのイメージを建てた | E5 の完了 | 今と同じ（`✗ Failed to build project image`、終了コード 1） | E5 |

E1・E2 が新しく決める部分で、E3 から先は確定仕様 `base-image-chain-build.md` のとおりに変えない。

## 用語

| 用語 | 意味 |
| --- | --- |
| プロジェクトの Dockerfile | プロジェクトの compose の構成で、開発サービス名のサービスの `build`（`context` と `dockerfile`。文字列の形は `context` だけ）が指す Dockerfile。開発サービスが `build` を持たないときは無い |
| Dockerfile の場所の決め方 | compose の構成からプロジェクトの Dockerfile のパスを決める規則。通常のビルドと `--expires` の判定が同じ規則を使う |
| 開発サービス名 | 用語集のとおり（`DEV_SERVICE_NAME`、既定 `dev`） |
| 継承の連なり | 用語集のとおり |
| 直の親イメージ | 用語集のとおり |

## 受け入れ条件

- [ ] 1. `compose.yml` で開発サービスより前に `build:` を持つ別のサービス（`FROM devbase-other` を書いた Dockerfile）があり、開発サービスの Dockerfile が `FROM devbase-base` のとき、通常のビルドは `devbase-base` の連なりを建て、`devbase-other` を建てず、`devbase-other` の名前を出力しない
- [ ] 2. 開発サービスの `build` が文字列の形（`build: ./dev`）のとき、通常のビルドは `./dev/Dockerfile` の直の親を読み、その連なりを建てる（カレントディレクトリの `Dockerfile` を読まない）
- [ ] 3. `DEV_SERVICE_NAME` に `dev` 以外の名前を設定したとき、通常のビルドはその名前のサービスの `build` から Dockerfile を決める
- [ ] 4. 次の各形の compose の構成について、通常のビルドと `--expires` の判定が同じプロジェクトの Dockerfile のパスを決める。1 つの入力の表を両方の経路で打つテストがあり、片方の決め方だけを変えるとそのテストが落ちる
  - 文字列の形 / 辞書の形の `context` だけ / `dockerfile` だけ / 両方 / `dockerfile` が絶対パス / `context` に環境変数を含む / 開発サービスより前に `build` を持つ別のサービスがある / 開発サービスが `build` を持たない
- [ ] 5. 開発サービスが `build` を持たないとき、通常のビルドはどの Dockerfile も読まず、`devbase-*` を `FROM` に取らないプロジェクトと同じ振る舞いになる（`devbase-base:latest` があれば `[1/2] devbase-base already exists (use --no-cache to rebuild)` を出して建てず、無ければ建てる）
- [ ] 6. compose の構成を読めないとき（例: `context` が `${MISSING:?}` を含み、その変数が無い）、通常のビルドはどの段の `docker buildx build` も `docker compose build` も起動せず、理由を出して終了コード 1 で終わる
- [ ] 7. 開発サービスの `build` が `compose.yml` の最初の `build:` で辞書の形のプロジェクト（今の 36 件の形）では、建てる段・その順・出力の行が直す前と変わらない。`tests/cli/test_build_base_chain.py` の既存のテストが書き換えなしで通る
- [ ] 8. 通常のビルド 1 回で、compose の構成を読む処理（`docker compose config`）を起動するのは 1 回以下である
- [ ] 9. 確定仕様 `docs/specifications/base-image-chain-build.md` が、プロジェクトの Dockerfile の場所を開発サービスの `build` から決めると書き、「含まない」の節と関数の表から「最初の `build:`」の記述が無くなっている
- [ ] 10. `CHANGELOG.md` の `[Unreleased]` に、通常のビルドが開発サービスの Dockerfile から継承の連なりを決めるようになったことが書かれている

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | compose の構成を読む処理の起動は通常のビルド 1 回につき 1 回以下（受け入れ条件 8）。ビルドの開始までに増える時間は、設計の段で手元の 1 プロジェクトで測って記録に残す（上限の値は置かない。続くビルドが数分かかるため） |
| 運用・保守性 | Dockerfile の場所の決め方は 1 か所が持ち、もう一方の経路はそれを使うか、同じ表のテストで縛る（受け入れ条件 4） |
| 移行性 | 利用者とプラグインの作者の作業は要らない。今の 36 件の形では振る舞いが変わらない（受け入れ条件 7） |
| システム環境 | macOS（Bash 3.2 と Homebrew の Bash の両方）と Linux（CI）で同じ結果になる。テストは CI で打つ |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | コマンドと引数は変わらない。開発サービスが `build` を持たないとき・compose の構成を読めないときの振る舞いが変わる（前提 6・7） |
| データ | 無し |
| 既存の振る舞い | 開発サービスの `build` が最初の `build:` でないプロジェクト・文字列の形のプロジェクトで、建てる段が開発サービスの Dockerfile に合う段へ変わる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` |
| 静的解析 | `uvx ruff check --select=E9,F63,F7,F82 lib`、`bin/devbase` の ShellCheck（開発参加ガイドの「CI が実行するもの」） |
| 固有の語の検査 | `python3 .github/scripts/proper_term_check.py`（`語の一覧が無いため飛ばした` と出たら検査していない） |
| 受け入れ条件 1〜3・5〜8 | `tests/cli/` の、偽の `docker` を置いて `cmd_build` を打つハーネスで、起動したコマンドと出力の行を確かめる。base を実際に建てない |
| 受け入れ条件 4 | 1 つの入力の表を `bin/devbase` の経路と `_get_base_image_ref` の経路の両方で打つテスト |
| 手動確認 | リリース後、手元のプラグインのプロジェクト 1 件で `devbase build` を打ち、出力の段の行が直す前と同じであることを確かめる（受け入れ条件 7 の実機の確認。P3 に当たる `devbase up` は打たない） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「このリポジトリ」の表。実装は `bin/devbase` と `lib/devbase/`、テストは `tests/`。`projects/*` は触らない |
| コーディング規約 | `AGENTS.md` の「書き方」と「検査」。シェルは ShellCheck、Python は ruff |
| テスト戦略 | 通常のビルドの振る舞いは `tests/cli/` のハーネス（偽の `docker`・`env -u DEVBASE_ROOT`）で確かめる。2 つの経路の一致は入力の表を両方で打つテストで縛る。実の docker と実の base は使わない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存のテストの実行、ruff と ShellCheck の適用、確定仕様と `CHANGELOG.md` の更新 |
| 確認してから行う | base イメージの中身と版の変更（P1）、実際の利用者の環境での `devbase up`（P3） |
| 行わない | 直の親の読み方・継承の連なりの解決の変更、`cmd_build` の Dockerfile の場所の決め方の外の書き直し、`projects/*` の変更、base の建て直し |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 通常のビルドが compose の構成を読む手段（`docker compose config` を Bash から直に打つか、Python の決め方を呼ぶか）と、`compose_with_secrets` の環境の渡し方 | 設計の工程（`design`） | 設計 PR |
| compose の構成を読めないときに出す理由の行の文言 | 設計の工程（`design`） | 設計 PR |
