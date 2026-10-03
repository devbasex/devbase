# #404: 派生イメージを FROM に取る 2 段の Dockerfile で、devbase build が base を先に建てず、base の無い端末で止まる

正は課題の本文（#404）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）
課題を起こしたときの本文と、その後のコメントをそのまま引く。

本文:

> ## 何を見つけたか
>
> プロジェクトの dev サービスの Dockerfile が、base ではなく派生イメージを `FROM` に取る（2 段の継承。例 `FROM devbase-php:latest`）とき、`devbase build` は `devbase-php` を `containers/php` から建てるが、その `containers/php/Dockerfile` が継ぐ `devbase-base:latest` が手元に無くても先に建てない。`docker buildx build` は `devbase-base:latest` をレジストリから取得しようとして失敗すると見込む（`bin/devbase` を読んだ結果。実機では未再現）。
>
> `docs/user/container-operations.md:509` は、この制約と回避（先に `devbase build base` を打つ）を利用者向けに書いている。
>
> ## どこで見つけたか
>
> - `bin/devbase` の `cmd_build` の通常の経路: `check_base_image_dependency`（`bin/devbase:118`）がプロジェクトの Dockerfile の `FROM devbase-*` を 1 つだけ読み、`build_base_image` がそのイメージだけを建てる。`build_base_image` は `containers/<名前>/Dockerfile` 自身の `FROM devbase-*` を見ない
> - #402 の決定 7・決定 8（`docs/specifications/base-image-rendering.md:54-55`）。ブラウザを使う `php` のプロジェクトは、`FROM devbase-php:latest` のプロジェクトの Dockerfile で Playwright の Chromium を足すことになり、2 段の継承が実際に使われる
>
> ## 同じ判定のもう 1 つの食い違い
>
> Dockerfile から `devbase-*` への依存を読む判定は 2 つの実装にあり、読み方が違う。
>
> | 行の形 | `bin/devbase` の `check_base_image_dependency`（`grep -q "^FROM devbase-"`） | `lib/devbase/commands/container.py` の `_get_base_image_ref`（`:2370`、`--expires` の判定） |
> | --- | --- | --- |
> | `FROM --platform=linux/amd64 devbase-base:latest` | 見落とす（rc=1、2026-09-29 に関数を切り出して実行） | `devbase-base:latest` と読む |
> | 小文字の `from devbase-base:latest` | 見落とす | 読む（`:2393` のコメント） |
>
> 見落とすと、通常のビルドは `devbase-base` の存在だけを確かめる分岐へ進み、Dockerfile が実際に継ぐイメージを先に建てない。
>
> `COPY --from=devbase-*` で base を取り込むイメージ（廃止した lfm、#403）は今の標準イメージにもプラグインのプロジェクトにも無いため、この課題の対象に含めない。
>
> ## なぜ #402 の範囲外だったか
>
> #402 の受け入れ条件は、base を `FROM` で継ぐ 1 段の派生イメージ（`containers/browser`）が base を先に建てることだけを求める（受け入れ条件 10）。2 段の継承の先行ビルドは `devbase build` の振る舞いの変更で、すべての派生イメージに効くため #402 の対象に含まれない。根拠: Value 1（MVV 版 1）
>
> ## 直さないと何が起きるか
>
> base を一度も建てていない新しい端末で、2 段の継承の Dockerfile を持つプロジェクトの `devbase build`（と、それを呼ぶ `devbase up`）が止まる。利用者は先に `devbase build base` を打つ必要があることを、出力から読み取れない。今の利用者の端末には base があるため、すぐには表に出ない。
>
> ## 直す場所
>
> | 項目 | 内容 |
> | --- | --- |
> | 現れている場所 | `bin/devbase` の build（通常ビルドの base 先行の分岐）と、`container.py` の `--expires` の判定 |
> | 直す場所 | Dockerfile から `devbase-*` への依存を読む判定の契約。`check_base_image_dependency` と `_get_base_image_ref` を 1 つの判定にまとめ、`--platform` と小文字の `FROM` を同じに読み、`containers/<名前>/Dockerfile` の `FROM devbase-*` をたどって下のイメージから先に建てる |
>
> ## 受け入れ条件（案）
>
> - `FROM devbase-php:latest` の Dockerfile を持つプロジェクトで、`devbase-base:latest` が無いときに `devbase build` が base → `devbase-php` → プロジェクトの順に建てる（Docker を起動しないテストで固定する。`tests/cli/test_build_browser_image.py` と同じ形）
> - `FROM --platform=... devbase-*` と小文字の `from` を、`devbase build` と `--expires` の判定が同じイメージとして読む
> - `docs/user/container-operations.md:509` の制約の記述を、直した振る舞いに合わせる
>
> ## 由来
>
> issue #402。2 つの実装の食い違いは #319（lfm のプロジェクトの `COPY --from`）の調べで見つけた。

コメント（2026-10-03）:

> 実例が増えました。#402 の受け入れ条件 13 で出したプラグインのリポジトリの PR は、proj-a の dev を `FROM devbase-php:latest` のプロジェクトの Dockerfile に変えます。`cmd_build`（`bin/devbase` の `check_base_image_dependency` → `build_base_image`）は devbase-php を先に建てますが、その下の devbase-base はたどりません。そのため、devbase-base:latest の無い端末では devbase-php の段で止まり、`--no-cache` で建て直しても devbase-base は建て直されません。回避策は `devbase build base` を先に打つことです（その PR の本文にも書きました）。

## 目的

base を一度も建てていない端末でも、派生イメージを `FROM` に取るプロジェクトの `devbase build`（と、それを呼ぶ `devbase up`・`devbase rebuild`）が、利用者に先の手順を求めずに通る状態にする。あわせて、Dockerfile から `devbase-*` への依存を読む判定を、通常のビルドと `--expires` の判定とで同じにする。

## 前提

- 前提 1: 継承の連なりは、Dockerfile の中で最初に `devbase-*` を指す `FROM` の行だけでたどる（今の 2 つの実装と同じ）。`FROM` の 2 行目以降・`COPY --from=devbase-*`・`ARG` で名前を組み立てる `FROM ${BASE}` はたどらない
- 前提 2: `devbase-<名前>` の Dockerfile は `$DEVBASE_ROOT/containers/<名前>/Dockerfile` にあるものとする（`build_base_image` と単体ビルドの今の規約）
- 前提 3: 連なりの各段は、今の直の親イメージと同じ扱いで建てる。通常のビルドではキャッシュを使って毎回建て、`--no-cache` では全段をキャッシュなしで建て、`--project-no-cache` では全段をキャッシュを使って建てる。手元にある段を飛ばす扱いにはしない（`containers/` の変更が下の段から上の段まで届くように、今の 1 段の振る舞いをそのまま延ばす）
- 前提 4: `--expires` の鮮度の判定は、今と同じく直の親イメージ（プロジェクトの Dockerfile が `FROM` に取るイメージ）の作成日だけで行う。連なりの下の段の作成日は見ない
- 前提 5: 単体ビルド（`devbase build <image>`）の振る舞いは変えない。単体ビルドは名前を挙げた 1 つのイメージだけを建てる命令であり、継承の連なりをたどらない
- 前提 6: 実機（Docker を起動した端末）での確認は、受け入れ条件の判定に使わない。判定は Docker を起動しないテストで行う（非対話のセッションでは base を建てられないため。`AGENTS.md` の落とし穴）

## 対象範囲

含む:
- 通常のビルド・`--no-cache`・`--project-no-cache` の 3 つの経路で、プロジェクトの Dockerfile から継承の連なりをたどり、下の段から先に建てる
- 連なりの途中の段の Dockerfile が無いとき・連なりが循環するときに、プロジェクトのイメージを建てずに止まる
- Dockerfile から `devbase-*` への依存を読む判定を、通常のビルドと `--expires` の判定とで同じ読み方にする
- `docs/user/container-operations.md` の「先に `devbase build base` を打つ」という制約の記述を、直した振る舞いに合わせる

含まない:
- 単体ビルド（`devbase build <image>`）が連なりをたどること（前提 5）
- `--expires` の鮮度の判定で、連なりの下の段の作成日を見ること（前提 4）
- `COPY --from=devbase-*` で base を取り込むイメージ（廃止した lfm、#403。今の標準イメージにもプラグインのプロジェクトにも無い）
- `ARG` で名前を組み立てる `FROM`、Dockerfile の 2 つ目以降の `FROM`（前提 1）
- base や派生イメージに入れる道具とその版の変更（P1 に当たる変更をしない）
- プラグインのリポジトリ（`repos/` の下）の `compose.yml` や Dockerfile の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 利用者がプロジェクトのビルドを求めた | `devbase build`・`devbase build --no-cache`・`devbase rebuild`・`devbase up` の自動の準備 | — | — |
| E2 | プロジェクトの Dockerfile から直の親イメージを読んだ | E1 | Dockerfile が読めない・`devbase-*` の `FROM` が無い → 今と同じく `devbase-base` の有無だけを確かめる分岐へ進む | E1 |
| E3 | `containers/<名前>/Dockerfile` をたどって継承の連なりを決めた | E2 で `devbase-*` を読んだ | 途中の段の `containers/<名前>/` が無い・連なりが循環する → 止まり、0 でない終了コードを返す（受け入れ条件 5・6） | E2 |
| E4 | 連なりの下の段から順にイメージを建てた | E3 | ある段の建てが失敗する → 上の段とプロジェクトを建てずに止まる（受け入れ条件 4） | E3 |
| E5 | プロジェクトのイメージを建てた | E4 がすべての段で成功した | 今と同じく `✗ Failed to build project image` で止まる | E4 |
| E6 | `--expires` の判定で直の親イメージの作成日を読んだ | `devbase build --expires`・`devbase rebuild`・`devbase up` の自動の準備で、プロジェクトのイメージが期限を過ぎた | 直の親イメージが読めない → 今と同じく base も含めてキャッシュなしで建てる | E2 と同じ読み方で直の親を決めている（受け入れ条件 7） |

## 用語

| 用語 | 意味 |
| --- | --- |
| 派生イメージ | 用語集のとおり |
| 単体ビルド | 用語集のとおり |
| 継承の連なり | プロジェクトの Dockerfile が `FROM` に取る `devbase-*` から、`containers/<名前>/Dockerfile` の `FROM devbase-*` を順にたどり、`devbase-*` を `FROM` に取らない段（今は `devbase-base`）で終わるイメージの並び。例: プロジェクト → `devbase-php` → `devbase-base` |

## 受け入れ条件

検査の形は、Docker を起動せず外への呼び出しを偽の `uv` で受ける `tests/cli/test_build_browser_image.py` と同じとする。「建てる」は偽の `uv` が受けた `docker buildx build --load -t <イメージ>:latest` の行、「プロジェクトを建てる」は `docker compose build <dev のサービス名>` の行で判定する。

- [ ] 1. dev の Dockerfile が `FROM devbase-php:latest` のプロジェクトで、`containers/php/Dockerfile` が `FROM devbase-base:latest` のとき、`devbase build` は `devbase-base:latest` → `devbase-php:latest` → プロジェクトの順に建て、終了コード 0 を返す。`devbase-base:latest` が手元に無いと `docker image inspect` が答える状態でも同じ順に建てる
- [ ] 2. 受け入れ条件 1 のプロジェクトで `devbase build --no-cache` を打つと、`devbase-base:latest` と `devbase-php:latest` の両方を `--no-cache` を付けて建て、その後にプロジェクトを `--no-cache` で建てる
- [ ] 3. 受け入れ条件 1 のプロジェクトで `--project-no-cache` の経路（`devbase rebuild` で base が新しいときに通る）を通ると、`devbase-base:latest` と `devbase-php:latest` を `--no-cache` を付けずに建て、その後にプロジェクトを `--no-cache` で建てる
- [ ] 4. 受け入れ条件 1 のプロジェクトで `devbase-base:latest` の建てが失敗すると、`devbase-php:latest` とプロジェクトを建てずに 0 でない終了コードを返し、標準出力か標準エラーに失敗したイメージの名前 `devbase-base` が出る
- [ ] 5. 連なりの途中の段の Dockerfile が無いとき（プロジェクトが `FROM devbase-foo:latest` で `containers/foo/` が無い、または `containers/foo/Dockerfile` が `FROM devbase-bar:latest` で `containers/bar/` が無い）、プロジェクトを建てずに 0 でない終了コードを返し、出力に見つからなかった `containers/` の下のパスが出る
- [ ] 6. 連なりが循環するとき（`containers/a/Dockerfile` が `FROM devbase-b:latest`、`containers/b/Dockerfile` が `FROM devbase-a:latest`）、どのイメージも建てずに 0 でない終了コードを返して止まり、出力に循環した名前が出る。無限にたどり続けない
- [ ] 7. 次の表の行を、通常のビルドの経路と `--expires` の判定（`_get_base_image_ref` が担う判定）が同じイメージとして読む。表の全行を両方の経路に通すテストがある

  | Dockerfile の行 | 読むイメージ |
  | --- | --- |
  | `FROM devbase-php:latest` | `devbase-php:latest` |
  | `FROM devbase-php` | `devbase-php:latest` |
  | `FROM --platform=linux/amd64 devbase-base:latest` | `devbase-base:latest` |
  | `from devbase-base:latest` | `devbase-base:latest` |
  | `FROM devbase-base:latest AS builder` | `devbase-base:latest` |
  | 先頭に空白のある `  FROM devbase-base:latest` | `devbase-base:latest` |
  | `# FROM devbase-base:latest` | 読まない |
  | `COPY --from=devbase-base:latest /a /b` | 読まない |
  | `FROM ubuntu:26.04` だけの Dockerfile | 読まない（今と同じく `devbase-base` の有無だけを確かめる分岐へ進む） |
- [ ] 8. 1 段の派生イメージ（`containers/browser`）を選んだプロジェクトの既存のテスト `tests/cli/test_build_browser_image.py` が、変えずに通る（退行しない）
- [ ] 9. `devbase-*` を `FROM` に取らない Dockerfile のプロジェクトで、`devbase-base:latest` が手元にあるときは base を建てず、無いときは base を建ててからプロジェクトを建てる（今の振る舞いが退行しない）
- [ ] 10. `docs/user/container-operations.md` の「プロジェクトの Dockerfile で足す」の行から「先に `devbase build base` を打つ」という制約が消え、`devbase build` が継承の連なりを下の段から建てることが書かれている
- [ ] 11. 全体のテスト `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` と、`uvx ruff check --select=E9,F63,F7,F82 lib`・`python3 .github/scripts/proper_term_check.py` が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 連なりが 1 段（今の `browser`・`php` などを直に選ぶプロジェクト）のとき、建てるイメージの数と順は今と変わらない（受け入れ条件 8）。2 段のときに増えるのは下の段 1 つ分の建てだけである |
| 運用・保守性 | 利用者は、出力に並ぶ段の名前から、どのイメージをどの順で建てたかを読み取れる（受け入れ条件 1・4 の出力） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない。コマンドとオプションは今のまま |
| データ | 変わらない |
| 既存の振る舞い | 2 段以上の継承のプロジェクトで、`devbase build`・`devbase up`・`devbase rebuild` が下の段のイメージも建てる。`FROM --platform=...` と小文字の `from` の Dockerfile を持つプロジェクトで、通常のビルドが直の親イメージを建てるようになる（今は見落として `devbase-base` の有無だけを確かめる） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` |
| 静的解析 | `uvx ruff check --select=E9,F63,F7,F82 lib`・`python3 .github/scripts/proper_term_check.py`。`bin/devbase` を変えたときは開発参加ガイドの「CI が実行するもの」の ShellCheck |
| 手動確認 | base の無い端末で、`FROM devbase-php:latest` のプロジェクト（例: proj-a）の `devbase build` が base → `devbase-php` → プロジェクトの順に建って通ることを、利用者が見る。base の取得に対話が要るため、リリース後テストで確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「このリポジトリ」の表。変更は `bin/devbase`・`lib/devbase/`・`tests/`・`docs/user/` に置く。`projects/*`・`repos/` には置かない |
| コーディング規約 | `AGENTS.md` の「書き方」。周りのコードに合わせる。確認は上の静的解析 |
| テスト戦略 | 振る舞いは `tests/cli/` の Docker を起動しないテスト（`bin/devbase` を実プロセスで起動し、外への呼び出しを偽の `uv` で受ける形）で固定する。判定の読み方（受け入れ条件 7）は、表の行を両方の経路に通すテストで固定する |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 上の検査の実行。`CHANGELOG.md` の `[Unreleased]` に利用者に見える変更を書く。新しい語は用語集へ足す |
| 確認してから行う | 公開のコマンド・オプションの変更。単体ビルドの振る舞いの変更 |
| 行わない | base・派生イメージの道具と版の変更（P1）。実の利用者の環境での `devbase up`（P3）。プラグインのリポジトリの変更。依頼の範囲外の `cmd_build` の書き直し |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 判定を 1 つの実装にまとめるか（`bin/devbase` が Python の判定を呼ぶ・Python 側が連なりを返す など）、2 つの実装を受け入れ条件 7 の表で同じに保つか | 設計（`design`） | 設計 PR |
| 循環の検出の方法（たどった名前の記録か、段の数の上限か） | 設計（`design`） | 設計 PR |

## MVV との突き合わせ

反する疑いは無い。根拠: Value 1（入った時点で使える。base の無い端末でも先の手順なしに建つ）・Vision（誰がどの端末で立ち上げても同じ環境が再現できる）・P1（base に入れる道具と版を変えないため当たらない）・P3（実の利用者の環境での `devbase up` を受け入れ条件の判定に使わない）。
