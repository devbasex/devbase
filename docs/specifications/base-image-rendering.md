# base イメージの文字の描画と、文書を扱う道具

## 概要

base イメージは、文字を描くときの既定を日本語にする。総称ファミリ（`sans-serif` / `sans` /
`serif` / `monospace`）と、イメージに無い書体名は Noto CJK の JP フェイスへ解決し、欧文の
metric 互換（`Arial` / `Times New Roman` / `Courier New` / `Calibri` / `Cambria`）と、中国語・
韓国語を**明示した**指定は、それぞれの書体・言語・様式のままにする。あわせて、PDF を画像に
する・調べる、OOXML を壊さずに読み書きする道具を base の中に持つ。

fontconfig は Chromium / Playwright のスクリーンショット、PDF の生成、画像の生成がすべて
参照するため、この既定は「文書を描く」以外の用途にも一様に効く。

規則は `containers/base/fonts-local.conf` の 1 ファイルで表し、イメージの中では
`/etc/fonts/local.conf` に置く。追加のパッケージを要さず、設定そのもののサイズは 0 である。

base はブラウザを持たない。base にあるのは空のブラウザの置き場 `/opt/ms-playwright`
（`ENV PLAYWRIGHT_BROWSERS_PATH`）と npm のグローバルの `@playwright/test` だけである。
Playwright の Chromium・ブラウザの依存パッケージ・追加の太さのフォント（`fonts-noto-cjk-extra`）は、
ブラウザの派生イメージ `devbase-browser`（`containers/browser`）が持つ。このイメージを選ぶと、
amd64 と arm64 のどちらでも、建て直した直後からネットワーク無しで起動してスクリーンショットや
PDF を作れる。システムの Chrome（`google-chrome-stable`）と Ubuntu の `chromium-browser`
（snap スタブ）は、どちらのイメージにも入れない。`terraform` も base に入れない。

利用者向けの読み方は
[コンテナ操作ガイド: 文字の描画と、文書を扱う道具](../user/container-operations.md#文字の描画と文書を扱う道具base-以降)
にある。

## 背景

**base にブラウザを置かないことは、以前の「base に Chromium を残す」判断を覆したものである（#402 の決定 1）。**
#220 は、ブラウザをネットワーク無しで使えるよう Playwright の Chromium を `--with-deps` のまま base に
残すと決めた。その時点では、ブラウザを使わないプロジェクトの数と依存パッケージの大きさ（arm64 で 296MB）を
測っていなかった。その後、設定の上でブラウザを求めるプロジェクトが無く、コードの見えるプロジェクトで
使うのは 1 つだけと分かり、利用者が 2026-10-03 に base からの削除を承認した（MVV の P1）。ネットワーク
無しで使えるという #220 の目的は、ブラウザの派生イメージが引き継ぐ。#220 の置き場の作り方と、検査する
イメージを環境変数で差し替える形は覆さず、置き場は base に、検査の形はブラウザの派生イメージに引き継いだ。

base に Chromium を残して依存パッケージだけを外す形は、依存パッケージが無いと Chromium が起動できない
ため採らない。Chromium だけを外して依存パッケージを残す形は、外せる量が足りないため採らない。

**システムの Chrome は両アーキとも base に入れない（#401）。** amd64 にだけあり（依存と合わせて約 540MB）、
呼ぶ処理がリポジトリとプラグインに無く、アーキで道具がそろっていなかった。

ブラウザまわりのほかの判断:

| 判断 | 理由 |
| --- | --- |
| ブラウザと追加の太さのフォントを 1 つの派生イメージ `devbase-browser` にまとめる（#402 の決定 2） | 追加の太さが効くのは、太さを指定したページをブラウザで描くときである。分けると、両方が要るプロジェクトが 1 つしか選べない。名前は役割の中心が Chromium であるため `browser` にする |
| base に `ENV PLAYWRIGHT_BROWSERS_PATH` と空の置き場を残す（#402 の決定 3） | 派生イメージが宣言し直さずに継げ、base 系のコンテナで道具が実行時に取得する先も変わらない。大きさは増えない |
| base でブラウザが無いことは Playwright 自身の失敗の出力に任せる（#402 の決定 4） | 案内の包みを足すと base へ道具を足すことになる。実行時に `--with-deps` で取得させると、作り直すたびに数百 MB を取り直し、ネットワークの無い所で動かない |
| HashiCorp の apt の取得元を base から外す（#402 の決定 5） | 残すと、base を継ぐイメージの `apt-get update` が、使う者の見当たらない道具のために外部の配布元に頼り続ける |
| `fonts-liberation`・`fonts-ipafont-gothic`・`fonts-wqy-zenhei` を 1 回目の一覧で明示する（#402 の決定 6） | 以前は `--with-deps` の副作用で入っていた。「解決先」の表を変えないため、表に現れる 3 つだけを明示する。`--with-deps` が入れていたほかのフォント（絵文字など）は残さない |
| 派生イメージとそのほかの道具を組み合わせる派生（例 `php-browser`）は devbase に足さない（#402 の決定 7） | 組み合わせの数だけ派生イメージが増える。プロジェクトの道具立てに合う道筋は、利用者向けの文書の移り方から選ぶ |
| ブラウザの派生イメージの単体ビルド（`devbase build browser`）は base を先に建てない（#402 の決定 8） | 既存の派生イメージと同じ扱いにする。base を先に建てるのは、プロジェクトの `devbase build` が Dockerfile の `FROM devbase-*` から継承の連なりをたどる経路である（[継承の連なりのビルド](base-image-chain-build.md)） |

## 対象範囲

- base イメージの fontconfig の設定の置き場所と内容、フォントキャッシュの作り直し
- base イメージに同梱する、文書を扱う道具のパッケージ
- ブラウザ（base の空の置き場と、Chromium・依存パッケージ・追加の太さのフォントを持つ
  ブラウザの派生イメージ `devbase-browser`。システムの Chrome を入れないこと）
- base から派生するイメージ（`browser` / `general` / `go` / `php` / `php85` / `bi-tools` / `latex` /
  `trygroup`）への伝播の規則
- `ENV LANG` は設定しない。LibreOffice と `pip` は同梱しない

## 用語

| 用語 | 意味 |
| --- | --- |
| 総称ファミリ | `sans-serif` / `sans` / `serif` / `monospace`。具体の書体名ではなく様式を指す指定 |
| スロット | `/etc/fonts/conf.d/` のファイル名の先頭の番号。fontconfig はこの番号順に設定を読む |
| metric 互換 | 字幅・行送りが元の書体と一致する代替の書体（`Arial` → Liberation Sans など） |
| 受け皿 | イメージに実在しない書体名を指定されたときに末尾へ足すフェイス |

ブラウザの語（ブラウザの置き場・Playwright の Chromium・システムの Chrome・snap スタブ・
ブラウザの依存パッケージ・ブラウザの派生イメージ）と、追加の太さのフォント・派生イメージの定義は
[用語集: ブラウザ（`browser`）](../glossary.md#ブラウザbrowser) にある。

## 構成要素

| 要素 | 置き場所 | 責務 |
| --- | --- | --- |
| フォントの規則 | `containers/base/fonts-local.conf` | 4 つの `<alias>`、未導入の書体の受け皿 1 つ、中国語・韓国語を明示した指定を守る 8 つの `<match>`。先頭のコメントに置き場所を動かせない理由を持つ |
| 規則の配置 | `containers/base/Dockerfile` の末尾の `COPY` 群 | `COPY --chmod=0644 fonts-local.conf /etc/fonts/local.conf`。直後の `RUN sudo fc-cache -f` |
| 道具のパッケージ | `containers/base/Dockerfile` の 1 つ目の `RUN` の 1 回目の `apt-get install` | metric 互換の 2 つと、文書を扱う 4 つ。解決先を保つ `fonts-liberation`・`fonts-ipafont-gothic`・`fonts-wqy-zenhei` |
| 形の検査 | `tests/containers/test_base_dockerfile_fonts.py` | Docker を起動せずに Dockerfile と `fonts-local.conf` の形を固定する |
| 解決先の検査 | `tests/containers/test_base_image_font_matching.py` | 建てたイメージの中の `fc-match` の解決先を固定する |
| ブラウザの置き場の宣言 | `containers/base/Dockerfile` の `ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` | 置き場の値を 1 か所で決め、以降の `RUN`・派生イメージ・`docker exec` の非対話の処理へ届ける |
| ブラウザの置き場の作成 | `containers/base/Dockerfile` の npm のグローバル領域を作る root の `RUN` | `install -d -m 2775 -o "$USERNAME" -g npm "$PLAYWRIGHT_BROWSERS_PATH"`。base では空のまま |
| ブラウザの派生イメージ | `containers/browser/Dockerfile`・`containers/browser/compose.yml` | `FROM devbase-base:latest` の上に `fonts-noto-cjk-extra` を apt で入れ、`npx playwright install --with-deps chromium` で依存パッケージと Chromium を置き場へ取得し、`fc-cache -f` でキャッシュを作り直す |
| base の形の検査 | `tests/containers/test_base_dockerfile_playwright.py` | Docker を起動せずに、base にブラウザ・追加の太さのフォント・`terraform` が無いこと、置き場の宣言と作り方を固定する |
| 派生イメージの形の検査 | `tests/containers/test_browser_dockerfile.py` | Docker を起動せずに、`containers/browser` の `FROM`・取得・片付け・`fc-cache` の順を固定する |
| base の検査 | `tests/containers/test_base_image_without_browser.py` | 建てた base に Chromium・追加の太さのフォント・`terraform` が無く、Chromium の起動が次にすることを示して止まることを固定する |
| ブラウザの検査 | `tests/containers/test_browser_image.py` | 建てたブラウザの派生イメージで Chromium が起動し、日本語のページを PDF にでき、追加の太さのフォントがあることを固定する |

型（クラス）は持たない。設定ファイルと Dockerfile の命令だけで構成する。

## 仕様

### 設定の置き場所

規則は `/etc/fonts/local.conf` に置く。`/etc/fonts/conf.d/` には 1 つも置かない。

`/etc/fonts/fonts.conf` は `<include>conf.d</include>` の 1 行しか持たず、`conf.d` を
ファイル名の番号順に読む。`local.conf` は `conf.d/51-local.conf` 経由で読まれる ── つまり
スロット 51 で、`sans-serif` を `WenQuanYi Zen Hei`（中国語）へ向けている
`64-wqy-zenhei.conf` と `65-nonlatin.conf` より**先**である。

`<prefer>` は、一致した総称ファミリの直前へ挿入する（prepend）。そのため**最も早く読まれた
`<prefer>` が先頭に残る**。この設定が効くのは順序が後だからではなく、先だからである。

同じ内容を `conf.d/99-devbase-fonts.conf` へ置くと効かない。

| 置き場所 | `fc-match sans-serif` | `fc-match sans-serif:lang=ja` |
| --- | --- | --- |
| `/etc/fonts/local.conf` | Noto Sans CJK JP | Noto Sans CJK JP |
| `/etc/fonts/conf.d/99-devbase-fonts.conf` | WenQuanYi Zen Hei | WenQuanYi Zen Hei |

**規則の種類によって順序への依存が違う。** `99-` へ置いた場合でも `<match target="pattern">`
（下の zh-cn / ko）は効いている。効かないのは `<alias><prefer>` だけである。部分的に直ったのを
見て置き場所の問題を見落とさないよう、この違いを `fonts-local.conf` の先頭のコメントにも残す。

`conf.d/00-*.conf` のように小さい番号を使う形は採らない。`local.conf` は fontconfig が
「システムの局所的な設定」のために用意した場所で、番号の付け替えで順序を取りに行くと他の
パッケージの番号と競合する。

**利用者の上書きの口は塞がない。** `conf.d/50-user.conf` が `~/.config/fontconfig/fonts.conf`
を読む。スロット 50 は `51-local.conf` より先なので、利用者が自分の `fonts.conf` で別の
`<prefer>` を書けばそちらが勝つ。この性質は `/etc/fonts/local.conf` を選んだことの帰結で、
`conf.d/00-*.conf` を使っていれば利用者の設定より先に読まれて上書きを塞いでいた。

### 総称ファミリと、未導入の書体の受け皿

4 つの `<alias>` が、総称ファミリの先頭を日本語のフェイスにする。

| 総称ファミリ | `<prefer>` するフェイス |
| --- | --- |
| `sans-serif` | `Noto Sans CJK JP` |
| `sans` | `Noto Sans CJK JP` |
| `serif` | `Noto Serif CJK JP` |
| `monospace` | `Noto Sans Mono CJK JP` |

**言語を明示しない中国語は、日本語の字形で描かれる。** `lang` を伴わない `sans-serif` は
どちらかの言語を選ばざるをえず、base の利用者は日本語話者で扱う文書も日本語が多いため、
日本語を既定に置く。中国語を明示した指定（`lang=zh-cn`）と、中国語の書体を名指しした指定
（`WenQuanYi Zen Hei`）は変わらない。Chromium は `<html lang="zh-CN">` のページで `lang` を
載せる。

未導入の書体の受け皿は、条件を付けない `<match target="pattern">` 1 つで足りる。

```xml
<match target="pattern">
  <edit name="family" mode="append" binding="weak"><string>Noto Sans CJK JP</string></edit>
</match>
```

`append` はすべてのパターンの末尾へ `Noto Sans CJK JP` を足し、**弱い結合（`binding="weak"`）
なので実在する指定を妨げない**。`Arial` は `30-metric-aliases.conf`（スロット 30、この設定より
先）の強い結合で Liberation Sans へ向くため、受け皿は末尾に付くだけで結果を変えない。

条件（`<test>`）を付けて「未導入のときだけ」足す形は採らない。fontconfig に「その書体が実在
するか」を問う `<test>` は無く、弱い結合がまさにその働きをする。

### 中国語・韓国語を明示した指定

zh-cn / ko の `<match>` は、**総称ファミリを名指ししたときだけ**効かせる。`<test name="lang">`
に加えて `<test name="family">` を必ず持つ。

| 総称ファミリ | `lang=zh-cn` で前置する | `lang=ko` で前置する |
| --- | --- | --- |
| `sans-serif` | `Noto Sans CJK SC` | `Noto Sans CJK KR` |
| `sans` | `Noto Sans CJK SC` | `Noto Sans CJK KR` |
| `serif` | `Noto Serif CJK SC` | `Noto Serif CJK KR` |
| `monospace` | `Noto Sans Mono CJK SC` | `Noto Sans Mono CJK KR` |

**`<test name="family">` を省いて `lang` だけを条件にすると壊れる。** `binding="strong"` の
前置は名指しの書体よりも強いため、`lang=zh-cn` が付いたすべてのパターン ── `Arial` や
`Times New Roman` を名指しした欧文の指定を含む ── から、指定した書体を奪う。

| 指定 | `lang` だけを条件にした場合 | `family` + `lang` を条件にした場合 |
| --- | --- | --- |
| `serif:lang=zh-cn` | Noto Sans CJK SC（様式が崩れる） | Noto Serif CJK SC |
| `monospace:lang=zh-cn` | Noto Sans CJK SC（等幅でなくなる） | Noto Sans Mono CJK SC |
| `Arial:lang=zh-cn` | Noto Sans CJK SC（欧文が奪われる） | Liberation Sans |
| `Times New Roman:lang=zh-cn` | Noto Sans CJK SC（同上） | Liberation Serif |

**ko の規則を省く形も採らない。** `zh-cn` は OS 既定の `65-nonlatin.conf` と
`70-fonts-noto-cjk.conf` が正しく扱うので規則が無くても合うが、`ko` は合わない ── 上の
`<alias>` が JP を先頭にするため、韓国語が日本語の字形になる。`ko` だけ書くと非対称で、なぜ
`zh-cn` が無いのかが後から読めないため、総称ファミリ 4 つ × 言語 2 つ = 8 つを並べて対称にする。

これらのフェイスは標準の太さで、base の `fonts-noto-cjk` に既にあり、追加の導入を要さない。

### 解決先

建てた base イメージの中で `fc-match` が返すフェイス。

| 指定 | 解決先 |
| --- | --- |
| `sans-serif` | Noto Sans CJK JP |
| `sans-serif:lang=ja` | Noto Sans CJK JP |
| `sans` | Noto Sans CJK JP |
| `serif` | Noto Serif CJK JP |
| `monospace` | Noto Sans Mono CJK JP |
| `Noto Sans JP` | Noto Sans CJK JP |
| `Meiryo` | Noto Sans CJK JP |
| `Yu Gothic` | Noto Sans CJK JP |
| `MS PGothic` | Noto Sans CJK JP |
| `Zen Kaku Gothic New`（イメージに無い） | Noto Sans CJK JP |
| `Arial` | Liberation Sans |
| `Times New Roman` | Liberation Serif |
| `Courier New` | Liberation Mono |
| `Calibri` | Carlito |
| `Cambria` | Caladea |
| `sans-serif:lang=zh-cn` | Noto Sans CJK SC |
| `sans:lang=zh-cn` | Noto Sans CJK SC |
| `serif:lang=zh-cn` | Noto Serif CJK SC |
| `monospace:lang=zh-cn` | Noto Sans Mono CJK SC |
| `sans-serif:lang=ko` | Noto Sans CJK KR |
| `sans:lang=ko` | Noto Sans CJK KR |
| `serif:lang=ko` | Noto Serif CJK KR |
| `monospace:lang=ko` | Noto Sans Mono CJK KR |
| `Arial:lang=zh-cn` | Liberation Sans |
| `Arial:lang=ko` | Liberation Sans |
| `Times New Roman:lang=zh-cn` | Liberation Serif |
| `WenQuanYi Zen Hei`（名指し） | WenQuanYi Zen Hei |
| `IPAPGothic`（名指し） | IPAPGothic |

並び順を返す `fc-match -s sans-serif:lang=ja` も、1 件目が `Noto Sans CJK JP` になる
（2 件目以降に `WenQuanYi Zen Hei` / `IPAPGothic` が続く）。

欧文を名指しした 3 行（`Arial:lang=zh-cn` / `Arial:lang=ko` /
`Times New Roman:lang=zh-cn`）と、実在する書体を名指しした 2 行（`WenQuanYi Zen Hei` /
`IPAPGothic`）は、`<test name="family">` を欠いた規則の壊れ方を捕まえる行である。

### キャッシュの作り直し

`RUN sudo fc-cache -f` は、`COPY` の直後に置く。フォントはすべて 1 つ目の `RUN` の apt で入り、
利用者の `RUN` が末尾で `~/.cache` を消すため、その後でキャッシュを作り直す。

**`fc-cache` は設定を反映するためのものではない**（設定は照合のたびに読まれる）。走らせる
のは、`~/.cache` を消した後に `/var/cache/fontconfig` を作り直し、コンテナの初回起動時の
キャッシュ生成を避けるためである。

末尾へ置くことにはキャッシュの上の利点もある。`fonts-local.conf` を書き換えたとき、無効に
なるのは末尾の数層だけで、巨大な 1 つ目の `RUN` は建て直されない。

ブラウザの派生イメージは、追加の太さのフォントと Playwright の `--with-deps` が入れるフォントを
足した後に、自分の末尾でもう一度 `RUN sudo fc-cache -f` を打つ。

### ブラウザ

**base はブラウザを持たない。** Playwright の Chromium・ブラウザの依存パッケージ・追加の太さの
フォントは、ブラウザの派生イメージ `devbase-browser`（`containers/browser`）が持つ。base は
空のブラウザの置き場と、npm のグローバルの `@playwright/test` だけを持つ。

base の命令は次の順に並ぶ。

1. `ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright`（`ENV NPM_CONFIG_PREFIX` / `ENV PATH` の後）。
   1 つだけ宣言する
2. root の `RUN` が、npm のグローバル領域の隣で、同じ形で空の置き場を作る
   （`install -d -m 2775 -o "$USERNAME" -g npm "$PLAYWRIGHT_BROWSERS_PATH"`）

Playwright の既定の置き場 `~/.cache/ms-playwright` は利用者の `RUN` の末尾の片付け
（`sudo rm -rf /tmp/* ~/.cache …`）で消えるため、片付けの対象外の `/opt` に置く。`ENV` にするのは、
`docker exec` の非対話の処理と、実行時にブラウザを取得する道具からも同じ置き場を使うためである。
置き場を base に置くため、派生イメージは `ENV` と置き場を宣言し直さずに継ぐ。

ブラウザの派生イメージの命令は次の順に並ぶ。

1. `FROM devbase-base:latest`。プロジェクトの `devbase build` が base を先に建てる経路に乗る
2. root の `RUN` が `fonts-noto-cjk-extra` を apt で入れ、同じ `RUN` で `/var/lib/apt/lists` を消す
3. 利用者 `ubuntu` の `RUN` が `npx playwright install --with-deps chromium` で、ブラウザの依存
   パッケージを apt で入れ、Chromium を置き場へ取得する。`npx` は base の `@playwright/test` の
   CLI を使う。同じ `RUN` の末尾の片付けの `rm -rf` は `/var/lib/apt/lists` を消し、
   `/opt/ms-playwright` もその親の `/opt` も対象にしない
4. `RUN sudo fc-cache -f`（「キャッシュの作り直し」）

**置き場は利用者 `ubuntu` が書き込める（`ubuntu:npm`・`2775`）。** `playwright-kit` などの道具が
実行時に別の版の Playwright で `playwright install chromium` を打つと、同じ環境変数に従って
この置き場へ版ごとのディレクトリを足すためである。npm のグローバル領域と同じ作り方にして、
所有者とパーミッションの決まりを 1 つにそろえる。

**取得の命令は `--with-deps chromium` で、headless の shell だけに絞らない。** 本体と headless の
shell の両方を置くと、本体を要する使い方（`channel: 'chromium'` の起動など）も arm64 で使える。

**base で Chromium が無いことは、Playwright 自身の失敗の出力で伝える。** base で Playwright の
Chromium を起動すると、Playwright は置き場に実行ファイルが無いことと `npx playwright install` を
出して止まる。取得した後に依存パッケージが無ければ、`install-deps` を打てと出す。base に案内の
ための包みは置かない。

**システムの Chrome（`google-chrome-stable`）はどちらのイメージにも入れない。** Google の apt の
取得元も足さない。Chromium を使う手段はブラウザの派生イメージの Playwright の Chromium で足りる。
Playwright を介さずにブラウザを呼ぶ道具には、Playwright の Chromium の実行ファイル
（`NODE_PATH="$(npm root -g)" node -e "console.log(require('@playwright/test').chromium.executablePath())"` で得るパス）を渡す。

| 場面 | base 系（base・`general`・`php` など） | ブラウザの派生イメージ |
| --- | --- | --- |
| イメージの Playwright で Chromium を起動する | 失敗する。Playwright が `npx playwright install` を打てと出す | `/opt/ms-playwright` の Chromium を起動する。取得は起きない |
| 利用者が `npx playwright install chromium` を打つ | 置き場へ取得できるが、依存パッケージが無いため起動で失敗する。Playwright が `install-deps` を出す | 同じ版なら何も起きない |
| 利用者が `npx playwright install --with-deps chromium` を打つ | 起動できるようになる。イメージの外の変更なので、コンテナを作り直すと消える | — |
| 道具が自分の版で取得する（`@playwright/mcp`・`playwright-kit`） | 取得は済むが、依存パッケージが無いため起動で失敗する | 置き場へ自分の版を足して起動する。コンテナを作り直すと足した版は消える |
| Playwright を介さずにブラウザを呼ぶ | 呼べるブラウザが無い | システムの Chrome は無い。Playwright の Chromium の実行ファイルのパスを渡す |

### 入れないもの

| 対象 | 理由 |
| --- | --- |
| LibreOffice | 展開 372〜459MB で、base の規律に見合わない |
| `pip` / `pip3` | 既にある `uv` / `uvx` で賄う |
| `ENV LANG` | 設定するとコンテナの中のすべてのコマンドの出力・ソート順・日付の書式が変わり、影響がフォントの外へ出る。総称ファミリの解決先そのものを日本語にすれば `lang` のヒントは要らない |
| Playwright の Chromium とブラウザの依存パッケージ（base） | すべての利用者は使わず、arm64 で計約 960MB ある。ブラウザの派生イメージが持つ |
| `fonts-noto-cjk-extra`（base） | 追加の太さ（Thin・Light・Medium・Black など）で、arm64 で約 215MB ある。標準の太さは `fonts-noto-cjk` にある。ブラウザの派生イメージが持つ |
| `terraform` と HashiCorp の apt の取得元 | 使う者が見当たらない。取得元を残すと、base を継ぐイメージの `apt-get update` が HashiCorp の配布元に頼る。要るプロジェクトは自分の Dockerfile かフックで入れる |
| `chromium-browser` | Ubuntu の snap スタブで、コンテナの中では Chromium として起動しない。Chromium は Playwright のものを使う |
| システムの Chrome（`google-chrome-stable`） | Chromium は Playwright のもので足り、呼ぶ処理も無い。amd64 では依存と合わせて約 540MB ある |
| `fonts-wqy-zenhei` の削除 | 削除しても OS 既定の `65-nonlatin.conf` が `sans-serif` の prefer 一覧にこの書体を含むため日本語にはならず、中国語のページを豆腐にするだけになる。1 つ目の `RUN` の 1 回目の一覧で明示して入れる |

Dockerfile は `fonts-wqy-zenhei` を対象とする `apt-get remove` / `apt-get purge` / `dpkg -r` を
持たない。

## データ・設定

同梱するパッケージは 6 つで、いずれも Ubuntu の標準のアーカイブにある。外部のリポジトリを
要さないため、1 つ目の `RUN` の**1 回目**の `apt-get install` の一覧へ、既にあるフォントの行の
隣に置く（2 回目の一覧は、後から足したリポジトリから取るもののためにある。どちらも同じ `RUN`
の中にありキャッシュの鍵は 1 つなので、置き場所でキャッシュの効き方は変わらない）。

| パッケージ | 何が使えるようになるか |
| --- | --- |
| `poppler-utils` | `pdftoppm` / `pdftocairo`（PDF を画像にする）、`pdfinfo` / `pdffonts`（ページ数・寸法・埋め込みフォントを調べる） |
| `python3-pil` | `import PIL`（画像の読み書き・変換） |
| `python3-lxml` | `import lxml`（OOXML の XML の読み書き） |
| `python3-defusedxml` | `import defusedxml`（外部実体を無効にした XML の解析） |
| `fonts-crosextra-carlito` | `Calibri` の metric 互換の実体 |
| `fonts-crosextra-caladea` | `Cambria` の metric 互換の実体 |

「解決先」の表を保つため、次の 3 つも同じ 1 回目の一覧で明示して入れる。

| パッケージ | 解決先の表の行 |
| --- | --- |
| `fonts-liberation` | `Arial` / `Times New Roman` / `Courier New` → Liberation Sans / Serif / Mono |
| `fonts-ipafont-gothic` | `IPAPGothic`（名指し） |
| `fonts-wqy-zenhei` | `WenQuanYi Zen Hei`（名指し） |

metric 互換の 2 つは、`30-metric-aliases.conf` が既に持っている対応の**実体**である。実体が
無いと `Calibri` / `Cambria` の指定は行き先を失い、中国語のフォントへ落ちる。

設定は Dockerfile のヒアドキュメントではなく独立したファイルにする。`containers/base/` は
ビルドコンテキストそのものなので、ファイルを置けば `COPY` で届く（`ai-cli-aliases.sh` /
`tmux.conf` と同じ形）。独立したファイルにすると XML として検査でき、差分が読め、置き場所の
理由のコメントを長く書ける。

## 運用

- 変更は**イメージを建て直すまで反映されない**。`devbase build base --no-cache` で base を
  建て直し、使っている派生イメージ（`browser` / `general` / `go` / `php` / `php85` / `bi-tools` / `latex` /
  `trygroup`。いずれも `FROM devbase-base:latest`）も建て直し、稼働中のコンテナは
  `devbase down` → `devbase up` で作り直す。この 3 段はいずれも省けない
- **`devbase rebuild` はここでは使えない。** `devbase build --expires=7` のシノニム
  （`lib/devbase/commands/container.py` の `cmd_rebuild`）で、イメージのビルドしか行わず
  コンテナを作り直さないうえ、期限内ならビルドそのものを飛ばす
- 既定を戻したい利用者は、コンテナの中の `~/.config/fontconfig/fonts.conf`（スロット 50）で
  上書きできる。イメージを触る必要は無い
- LibreOffice での実際の描画は未検証である。LibreOffice は base に無く、fontconfig とは別の照合も
  持つ。Chromium での描画は、建てたイメージの中で日本語のページを PDF にし、埋め込まれた書体を
  `pdffonts` で見る検査が確かめる（「テスト観点」）
- ブラウザ・追加の太さのフォントが要るプロジェクトは、dev サービスの `build.context` を
  `${DEVBASE_ROOT}/containers/browser/` にして `devbase-browser` を選ぶ。移り方は
  [コンテナ操作ガイド: ブラウザ・追加の太さのフォント・terraform を使う](../user/container-operations.md#ブラウザ追加の太さのフォントterraform-を使う)
  にある
- 実行時に道具がブラウザの置き場へ取得したブラウザは、コンテナを作り直すと消える
  （`/home/ubuntu` もボリュームではない）。ブラウザの派生イメージが持つ Chromium は消えない
- 解決先の表は arm64 で採ったものである。apt で入れるパッケージは両アーキで同じだが、同じ表になるかは
  amd64 の端末で建てるまで分からない

## テスト観点

回帰テストは 2 段に分ける。**Dockerfile の文字列検査だけでは足りない。** 固定したいのは
「`COPY` の行があること」ではなく「`fc-match sans-serif` が日本語を返すこと」で、後者は文字列
からは分からない。`<test name="family">` を欠いた壊れ方は、Dockerfile を読んでも見えない。

`tests/containers/test_base_dockerfile_fonts.py`（Docker を要さない）:

- 6 パッケージと、解決先を保つ 3 つのフォントが 1 回目の `apt-get install` の一覧にあること。
- `COPY` の宛先が `/etc/fonts/local.conf` であり、`conf.d/` を宛先にする `COPY` が無いこと。
- `fc-cache -f` が 1 度だけで、`COPY` より後にあること。
- `fonts-local.conf` が整形式の XML で、4 つの `<alias>` と 9 つの `<match>`（受け皿 1 つと、
  総称ファミリ 4 つ × 言語 2 つの 8 つ）を持つこと。`lang` の `<match>` が `family` の
  `<test>` を必ず持つこと。
- 先頭のコメントが置き場所の理由（`51-local.conf` / `conf.d` / `99`）に触れていること。
- `libreoffice` / `soffice` / `pip` を入れていないこと。`fonts-wqy-zenhei` を消す命令が無いこと。

`tests/containers/test_base_image_font_matching.py`（Docker を要する）:

- 「解決先」の表の各行。`fc-match -s sans-serif:lang=ja` の 1 件目。
- `pdftoppm` / `pdfinfo` / `pdffonts` / `pdftocairo` / `uv` が `PATH` にあり、`soffice` /
  `libreoffice` / `pip` / `pip3` が無いこと。`python3 -c "import PIL, defusedxml, lxml"` が 0 で
  終わること。
- `docker run` は**セッションで 1 回**に抑える。`scope="session"` の fixture が 1 つの
  スクリプトを走らせ、`fc-match`・`command -v`・`import` の結果をまとめて辞書で返す。

skip は 4 段で見る。`shutil.which('docker')` → `docker info` →
`docker image inspect devbase-base:latest` → イメージの中に `/etc/fonts/local.conf` があるか。
**4 段目は、この設定より前に建てたイメージを持つ人が全員 `pytest tests/` で赤くなるのを避ける
ためにある。** 古いイメージを「失敗」として知らせると、赤の意味が「壊れている」と「イメージが
古い」で混ざる。skip の文言には `devbase build base --no-cache` を書き、建て直せば検査が効く
ようにする。Docker もイメージもある状態でスクリプトが失敗したときは skip せず、失敗として
知らせる。

CI はイメージを建てるジョブを持たないため、このテストは CI では常に skip になる。解決先の
証跡は手元で建てたイメージから採る。

### ブラウザ

`tests/containers/test_base_dockerfile_playwright.py`（Docker を要さない）:

- base のどの `RUN` にも `playwright install` が無く、どの `apt-get install` にも
  `fonts-noto-cjk-extra`・`terraform` が無く、HashiCorp の apt の取得元が無いこと。
- `npm i -g` の一覧に `@playwright/test` があること。
- base に `ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` が 1 つだけあり、置き場を
  npm のグローバル領域と同じ root の `RUN` で `$USERNAME:npm`・`2775` で作り、その `RUN` が
  `ENV` より後にあること。
- base のどの `apt-get install` にも `chromium-browser` が無いこと。
- Google の apt の取得元（`dl.google.com/linux`）が無く、どの `apt-get install` にも `google-chrome` が無いこと。
- `containers/bi-tools/Dockerfile` の先頭のコメントが、base に含まれるものとして `terraform` を挙げないこと。

`tests/containers/test_browser_dockerfile.py`（Docker を要さない）:

- 最初の `FROM` が `devbase-base:latest` で、`compose.yml` の `image` が `devbase-browser:latest` であること。
- `npx playwright install --with-deps chromium` を含む `RUN` が 1 つで利用者 `ubuntu` が打ち、
  その片付けの `rm -rf` が `/var/lib/apt/lists/*` を消し、`/opt`・`/opt/ms-playwright`・
  `$PLAYWRIGHT_BROWSERS_PATH` を対象にしないこと。`ENV PLAYWRIGHT_BROWSERS_PATH` を宣言し直さないこと。
- `fonts-noto-cjk-extra` を apt で入れる `RUN` が 1 つあり、`fc-cache -f` が 1 度だけで、
  その `RUN` と Playwright の `RUN` より後にあること。

`tests/cli/test_build_browser_image.py`（Docker を要さない）:

- dev の `build.context` が `${DEVBASE_ROOT}/containers/browser/` のプロジェクトで `bin/devbase build` を
  起動すると、`devbase-base` を建ててから `docker compose build dev` を打つこと。

`tests/containers/test_base_image_without_browser.py`（Docker を要する）:

- 置き場 `/opt/ms-playwright` の下に `chromium-*`・`chromium_headless_shell-*` が無いこと。
- `dpkg -s fonts-noto-cjk-extra` と `command -v terraform` が非 0 で終わること。
- `--network none` で起動したコンテナで、利用者 `ubuntu` の非対話の `bash -c` から Playwright の
  Chromium を headless で起動すると、非 0 で終わり、出力に `playwright install` か `devbase-browser` が
  含まれること。

検査するイメージは環境変数 `DEVBASE_TEST_BASE_IMAGE` で差し替えられ、既定は `devbase-base:latest`
である。既定のイメージの置き場に Chromium があるとき（ブラウザを base から外す前に建てたイメージ）は
skip し、明示したときは skip せず落とす。

`tests/containers/test_browser_image.py`（Docker を要する）:

- 置き場 `/opt/ms-playwright` があること。
- `--network none` で起動したコンテナで、利用者 `ubuntu` の非対話の `bash -c` から Playwright の
  Chromium を headless で起動し、`<html lang="ja">` のページを PDF にできること（終了コード 0、
  1 バイト以上）。
- その PDF の `pdffonts` に `NotoSansCJKjp` を含む書体があり、`WenQuanYi` を含む書体が無いこと。
- 利用者 `ubuntu` が置き場の直下にファイルを作れること。
- `dpkg -s chromium-browser` が非 0 で、ブラウザの依存パッケージ（`fonts-liberation`・
  `fonts-ipafont-gothic`・`fonts-wqy-zenhei`・`libnss3`）は 0 で終わること。
- `env` の `PLAYWRIGHT_BROWSERS_PATH` が `/opt/ms-playwright` で、Chromium を起動した後も
  `~/.cache/ms-playwright` が無いこと。
- `dpkg -s fonts-noto-cjk-extra` が 0 で終わり、`fc-list` の `Noto Sans CJK JP` に `Thin` と `Black` の
  フェイスがあること。
- `docker run` はセッションで 1 回に抑え、並列でも同じワーカーへ寄せる（`xdist_group`）。

検査するイメージは環境変数 `DEVBASE_TEST_BROWSER_IMAGE` で差し替えられ、既定は `devbase-browser:latest`
である。マージ前は `devbase-browser:latest` を上書きしない別のタグを渡し、リリース後は既定のまま同じ
検査を走らせるためである。skip するのは、Docker が使えないとき、イメージが無いとき、既定の
イメージに置き場が無いときだけである。**`DEVBASE_TEST_BROWSER_IMAGE` を明示したときは、置き場が
無くても skip せず落とす。** 片付けで置き場を消して建てたイメージは古いイメージと見分けられず、
skip にすると壊れ方が見えなくなるためである。

### 大きさ

ブラウザ・追加の太さのフォント・`terraform` を base から外した効果は、変更の前後の base の**展開後の
ファイルの大きさ**（コンテナで `du -sxb /`）で判定する（手動。CI はイメージを建てない）。判定の物差しは
展開後の大きさで、`docker image inspect` の `.Size` ではない（#402 の決定 10）。containerd のイメージ
ストアでは `.Size` が圧縮後の大きさを返し、取得する量の目安にはなるが展開後の減りとは別の値になる。
`.Size` の値も並べて記録する。

| イメージ（arm64・2026-10-03） | `.Size`（圧縮後） | `du -sxb /`（展開後） |
| --- | ---: | ---: |
| 外す前の base | 1,709,531,737 | 4,239,699,252 |
| 外した後の base | 1,098,398,245 | 2,948,038,519 |
| 差 | 611,133,492 | 1,291,660,733 |
| `devbase-browser`（外した後の base の上） | 1,675,443,221 | 4,125,424,157 |

base の展開後の大きさの全体の推移は [base イメージの中身の絞り込み](base-image-contents.md) にある。

## 関連リンク

- [用語集: ブラウザ（`browser`）](../glossary.md#ブラウザbrowser)
- [base イメージの中身の絞り込み（文書の除外と DinD の廃止）](base-image-contents.md)
- [コンテナ操作ガイド: 文字の描画と、文書を扱う道具](../user/container-operations.md#文字の描画と文書を扱う道具base-以降)
- [AI CLI alias の読み込み](ai-cli-alias-loading.md)
- [Kiro CLI 認証永続化と tmux コピー操作](kiro-auth-persistence-and-tmux-copy.md)
- [fontconfig: Configuration file format](https://www.freedesktop.org/software/fontconfig/fontconfig-user.html)
