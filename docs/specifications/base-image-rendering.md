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

利用者向けの読み方は
[コンテナ操作ガイド: 文字の描画と、文書を扱う道具](../user/container-operations.md#文字の描画と文書を扱う道具base-以降)
にある。

## 対象範囲

- base イメージの fontconfig の設定の置き場所と内容、フォントキャッシュの作り直し
- base イメージに同梱する、文書を扱う道具のパッケージ
- base から派生するイメージ（`general` / `go` / `php` / `php85` / `bi-tools` / `latex` /
  `trygroup`）への伝播の規則
- `containers/lfm` は base 由来ではないため対象に含まない
- `ENV LANG` は設定しない。LibreOffice と `pip` は同梱しない

## 用語

| 用語 | 意味 |
| --- | --- |
| 総称ファミリ | `sans-serif` / `sans` / `serif` / `monospace`。具体の書体名ではなく様式を指す指定 |
| スロット | `/etc/fonts/conf.d/` のファイル名の先頭の番号。fontconfig はこの番号順に設定を読む |
| metric 互換 | 字幅・行送りが元の書体と一致する代替の書体（`Arial` → Liberation Sans など） |
| 受け皿 | イメージに実在しない書体名を指定されたときに末尾へ足すフェイス |

## 構成要素

| 要素 | 置き場所 | 責務 |
| --- | --- | --- |
| フォントの規則 | `containers/base/fonts-local.conf` | 4 つの `<alias>`、未導入の書体の受け皿 1 つ、中国語・韓国語を明示した指定を守る 8 つの `<match>`。先頭のコメントに置き場所を動かせない理由を持つ |
| 規則の配置 | `containers/base/Dockerfile` の末尾の `COPY` 群 | `COPY --chmod=0644 fonts-local.conf /etc/fonts/local.conf`。直後の `RUN sudo fc-cache -f` |
| 道具のパッケージ | `containers/base/Dockerfile` の 1 つ目の `RUN` の 1 回目の `apt-get install` | metric 互換の 2 つと、文書を扱う 4 つ |
| 形の検査 | `tests/containers/test_base_dockerfile_fonts.py` | Docker を起動せずに Dockerfile と `fonts-local.conf` の形を固定する |
| 解決先の検査 | `tests/containers/test_base_image_font_matching.py` | 建てたイメージの中の `fc-match` の解決先を固定する |

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

これらのフェイスは `fonts-noto-cjk` / `fonts-noto-cjk-extra` に既にあり、追加の導入を要さない。

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

`RUN sudo fc-cache -f` は、`COPY` の直後かつ**Playwright がフォントを入れる `RUN` より後**に
置く。その `RUN` の `npx playwright install --with-deps chromium` が `fonts-wqy-zenhei` /
`fonts-ipafont-gothic` / `fonts-liberation` などを入れ、直後に `~/.cache` を消す。前で走らせる
と、後から入った書体を知らないキャッシュが残る。

**`fc-cache` は設定を反映するためのものではない**（設定は照合のたびに読まれる）。走らせる
のは、`~/.cache` を消した後に `/var/cache/fontconfig` を作り直し、コンテナの初回起動時の
キャッシュ生成を避けるためである。

末尾へ置くことにはキャッシュの上の利点もある。`fonts-local.conf` を書き換えたとき、無効に
なるのは末尾の数層だけで、巨大な 1 つ目の `RUN` は建て直されない。

### 入れないもの

| 対象 | 理由 |
| --- | --- |
| LibreOffice | 展開 372〜459MB で、base の規律に見合わない |
| `pip` / `pip3` | 既にある `uv` / `uvx` で賄う |
| `ENV LANG` | 設定するとコンテナの中のすべてのコマンドの出力・ソート順・日付の書式が変わり、影響がフォントの外へ出る。総称ファミリの解決先そのものを日本語にすれば `lang` のヒントは要らない |
| `fonts-wqy-zenhei` の削除 | 削除しても OS 既定の `65-nonlatin.conf` が `sans-serif` の prefer 一覧にこの書体を含むため日本語にはならず、中国語のページを豆腐にするだけになる。Dockerfile に導入の行は無く、Playwright が依存として入れる |

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

metric 互換の 2 つは、`30-metric-aliases.conf` が既に持っている対応の**実体**である。実体が
無いと `Calibri` / `Cambria` の指定は行き先を失い、中国語のフォントへ落ちる。

設定は Dockerfile のヒアドキュメントではなく独立したファイルにする。`containers/base/` は
ビルドコンテキストそのものなので、ファイルを置けば `COPY` で届く（`ai-cli-aliases.sh` /
`tmux.conf` と同じ形）。独立したファイルにすると XML として検査でき、差分が読め、置き場所の
理由のコメントを長く書ける。

## 運用

- 変更は**イメージを建て直すまで反映されない**。`devbase build base --no-cache` で base を
  建て直し、使っている派生イメージ（`general` / `go` / `php` / `php85` / `bi-tools` / `latex` /
  `trygroup`。いずれも `FROM devbase-base:latest`）も建て直し、稼働中のコンテナは
  `devbase down` → `devbase up` で作り直す。この 3 段はいずれも省けない
- **`devbase rebuild` はここでは使えない。** `devbase build --expires=7` のシノニム
  （`lib/devbase/commands/container.py` の `cmd_rebuild`）で、イメージのビルドしか行わず
  コンテナを作り直さないうえ、期限内ならビルドそのものを飛ばす
- 既定を戻したい利用者は、コンテナの中の `~/.config/fontconfig/fonts.conf`（スロット 50）で
  上書きできる。イメージを触る必要は無い
- `containers/lfm` はこの仕様の対象外である。`FROM nvidia/cuda:...` で base 由来ではなく、
  `fonts-noto-cjk` を自前で入れている（`containers/lfm/Dockerfile`）
- 確かめてあるのは `fc-match` の水準までで、LibreOffice と Chromium での実際の描画は未検証
  である。どちらも base に無く、LibreOffice は fontconfig とは別の照合も持つ
- 解決先の表は arm64 で採ったものである。amd64 では `google-chrome-stable` が追加で入るため
  `--with-deps` が入れるフォントの顔ぶれが違いうる。同じ表になるかは amd64 の端末で建てるまで
  分からない

## テスト観点

回帰テストは 2 段に分ける。**Dockerfile の文字列検査だけでは足りない。** 固定したいのは
「`COPY` の行があること」ではなく「`fc-match sans-serif` が日本語を返すこと」で、後者は文字列
からは分からない。`<test name="family">` を欠いた壊れ方は、Dockerfile を読んでも見えない。

`tests/containers/test_base_dockerfile_fonts.py`（Docker を要さない）:

- 6 パッケージが 1 回目の `apt-get install` の一覧にあること。
- `COPY` の宛先が `/etc/fonts/local.conf` であり、`conf.d/` を宛先にする `COPY` が無いこと。
- `fc-cache -f` が 1 度だけで、`COPY` より後かつ Playwright の `RUN` より後にあること。
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

## 関連リンク

- [コンテナ操作ガイド: 文字の描画と、文書を扱う道具](../user/container-operations.md#文字の描画と文書を扱う道具base-以降)
- [AI CLI alias の読み込み](ai-cli-alias-loading.md)
- [Kiro CLI 認証永続化と tmux コピー操作](kiro-auth-persistence-and-tmux-copy.md)
- [fontconfig: Configuration file format](https://www.freedesktop.org/software/fontconfig/fontconfig-user.html)
