# PLAN63: base イメージの日本語の描画と、文書を扱う軽量の道具 の実装

## 関連リンク

- 対象 issue: devbasex/devbase#161, devbasex/devbase#160
- 要求と受け入れ条件: [PLAN63_base-image-rendering.md](PLAN63_base-image-rendering.md)
- 設計: [PLAN63_base-image-rendering-design.md](PLAN63_base-image-rendering-design.md)
- 設計 Pull Request: #221（マージ済み） / release Pull Request: #212
- 範囲外として起票済み: #219（`containers/docs` と LibreOffice）、#220（arm64 の Chromium）

## モード

`standard`。base イメージの本番の振る舞い（総称ファミリの解決先と同梱するパッケージ）を変え、
すべての派生イメージとプロジェクトへ届くため（要求の「ワークフローモード」の根拠のとおり）。

## 目的と非目的

達成したい状態:

- base コンテナで日本語を描いたとき、日本語のフェイス（Noto CJK の JP）で描かれる
- 欧文（`Arial` / `Times New Roman` / `Courier New`）と、中国語・韓国語を**明示した**指定は壊れない
- PDF を画像にする・調べる、OOXML を壊さずに読み書きする、欧文の字幅を正しく測ることが
  base だけでできる

やらないこと（今回はやらない、の意味で）:

- LibreOffice の追加（要求の前提 3）
- `containers/docs` の新設（前提 4 / #219）
- `fonts-wqy-zenhei` の削除（前提 2）
- `ENV LANG` の設定（前提 7）
- `pip` の追加（前提 8）
- Playwright のブラウザの導入方法と arm64 の Chromium（前提 5 / #220）
- 派生イメージ（`containers/general` ほか）と `containers/lfm` の Dockerfile の変更

## 前提

- 前提 1: 設定は `/etc/fonts/local.conf` に置く。`conf.d/99-*.conf` では `<alias><prefer>` が
  効かない（設計の決定 1 の実測）
- 前提 2: 中国語・韓国語の規則は**総称ファミリを名指ししたときだけ**効かせる。`lang` だけを
  条件にすると `Arial:lang=zh-cn` から Liberation Sans を奪う（設計の決定 2 の実測）
- 前提 3: `release/v3.7.0` を base にした Pull Request では CI が 1 件も動かない（#216）。
  証跡は手元で採って Pull Request 本文へ載せる
- 前提 4: この変更は `devbase build base --no-cache` を建てるまで手元に反映されない

## 受け入れ条件

要求の 16 件をそのまま引き継ぐ。ここでは検証手段の対応だけを書く。

- [ ] 1〜6（フォントの解決先）— `tests/containers/test_base_image_font_matching.py` の解決先の表
- [ ] 7・8（置き場所と、動かせない理由のコメント）— `tests/containers/test_base_dockerfile_fonts.py`
- [ ] 9・10・12（道具の有無）— 同じ `docker run` の中の `command -v` と `python3 -c "import …"`
- [ ] 11（`Calibri` → Carlito / `Cambria` → Caladea）— 解決先の表の欧文の行
- [ ] 13（イメージの増分が 40 MB 以下）— ビルドの前後の `docker images`。手で測る
- [ ] 14（`uv run --locked pytest tests/ -q` が 0）
- [ ] 15（`devbase build base --no-cache` が arm64 で成功する）
- [ ] 16（派生イメージ 1 つで同じ解決先になる）— 手で確かめる

## 代替案と採否

設計の「決定の記録」（決定 1〜9）が持つ。ここでは写さない。

## 不変条件

- `fc-match Arial` / `Times New Roman` / `Courier New` は Liberation の 3 つのままである
- `fc-match <中国語・韓国語を明示した総称ファミリ>` は、その言語の、**同じ様式**のフェイスを返す
- `soffice` / `libreoffice` / `pip` / `pip3` は `PATH` に無い
- Dockerfile に `fonts-wqy-zenhei` を消す命令が無い

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 公開インタフェース（CLI の引数・環境変数・コマンド） | 無し | 変えない |
| データ | 無し | 変えない |
| base コンテナの中で描かれる文字のフェイス | **変わる** | 建て直すまで変わらない。CHANGELOG で `devbase build base --no-cache` が要ることを知らせる |

## 修正対象

- `containers/base/fonts-local.conf`（新設）
- `containers/base/Dockerfile`（1 つ目の `RUN` の 1 回目の `apt-get install` / 末尾の `COPY` 群）
- `tests/containers/test_base_dockerfile_fonts.py`（新設）
- `tests/containers/test_base_image_font_matching.py`（新設。Docker が要る）
- `docs/user/container-operations.md`
- `CHANGELOG.md`
- `issues/PLAN63_base-image-rendering-impl.md`（この文書）

## タスク分解

### Task 1: フォントの設定ファイルと、その形の回帰テスト

- **対象ファイル:** `containers/base/fonts-local.conf`（新設）、
  `tests/containers/test_base_dockerfile_fonts.py`（新設）
- **変更内容:** 4 つの `<alias>`（`sans-serif` / `sans` / `serif` / `monospace` → Noto CJK JP）、
  未導入の書体の受け皿 1 つ（`append` / `binding="weak"`）、総称ファミリ 4 つ × 言語 2 つ =
  8 つの `<match>`（`<test name="family">` と `<test name="lang">` の両方を持つ）。
  先頭のコメントに、`/etc/fonts/local.conf` から動かせない理由（`51-local.conf` のスロット、
  `conf.d` の番号順、`99` での実測）を残す
- **満たす受け入れ条件:** 8（コメント）と、1〜6 の土台
- **進め方:** 先に `test_base_dockerfile_fonts.py` の XML の形の検査（整形式・`<alias>` 4 つ・
  `<match>` 9 つ・`lang` の `<match>` が `family` の `<test>` を必ず持つ・コメントの語）を書いて
  落とし、`fonts-local.conf` を足して通す

### Task 2: Dockerfile の 6 パッケージと `COPY` / `fc-cache`

- **対象ファイル:** `containers/base/Dockerfile`、`tests/containers/test_base_dockerfile_fonts.py`
- **変更内容:** 1 つ目の `RUN` の**1 回目**の `apt-get install` の一覧へ
  `fonts-crosextra-carlito fonts-crosextra-caladea poppler-utils python3-pil
  python3-defusedxml python3-lxml` を足す（新しい `RUN` を立てない）。末尾の `COPY` 群
  （`tmux.conf` と同じ区画、`USER ubuntu` より後）へ
  `COPY --chmod=0644 fonts-local.conf /etc/fonts/local.conf` と `RUN sudo fc-cache -f` を足す
- **満たす受け入れ条件:** 7、および 9〜13 の土台
- **進め方:** 先に Dockerfile の文字列検査（6 つが 1 回目の一覧にあること、`COPY` の宛先が
  `/etc/fonts/local.conf` で `conf.d/` を宛先にする `COPY` が無いこと、`fc-cache -f` が 1 度だけで
  `COPY` より後かつ Playwright の `RUN` より後にあること、`libreoffice` / `soffice` / `pip` を
  入れていないこと、`fonts-wqy-zenhei` を消す命令が無いこと）を書いて落とし、Dockerfile を直す

### Task 3: 建てたイメージの中の解決先を固定するテスト

- **対象ファイル:** `tests/containers/test_base_image_font_matching.py`（新設）
- **変更内容:** `scope="session"` の fixture が 1 回の `docker run` で `fc-match` の全行・
  `fc-match -s sans-serif:lang=ja` の 1 件目・`command -v` の結果・`python3 -c "import …"` の
  終了コードをまとめて採り、辞書で返す。skip は 4 段
  （`shutil.which('docker')` → `docker info` → `docker image inspect devbase-base:latest` →
  イメージの中に `/etc/fonts/local.conf` があるか）。skip の文言に
  `devbase build base --no-cache` を書く
- **満たす受け入れ条件:** 1〜6・9・10・11・12
- **進め方:** テストを書き、建て直す前は skip になることを確かめる。Task 4 でイメージを
  建て直した後に、実際に通ることを確かめる

### Task 4: イメージを建て直して実測する

- **対象ファイル:** 無し（証跡の採取）
- **変更内容:** `devbase build base --no-cache` を作業ツリーの `containers/base` で実行し、
  前後の `docker images` を記録する。解決先の表の 26 行を `fc-match` で採り、Pull Request
  本文へ載せる。派生イメージ 1 つを建て直して同じ解決先になることを確かめる
- **満たす受け入れ条件:** 1〜6・9〜13・15・16
- **進め方:** テスト駆動を適用しない（測定のため）。結果は Pull Request 本文の Test plan へ

### Task 5: 利用者向け文書と CHANGELOG

- **対象ファイル:** `docs/user/container-operations.md`、`CHANGELOG.md`
- **変更内容:** イメージの詳細の表の base の行へ、日本語のフェイスと文書の道具を足す。
  CHANGELOG の `[Unreleased]` に `### Added`（6 パッケージ）と `### Fixed`（日本語が中国語
  フォントで描画される）を書き、**イメージを建て直すまで反映されない**ことを添える
- **満たす受け入れ条件:** 要求の「対象範囲」の文書の行
- **進め方:** テスト駆動を適用しない（文書のため）

## 影響範囲

- base から派生するすべてのイメージ（`general` / `go` / `php` / `php85` / `bi-tools` /
  `latex` / `trygroup`）。いずれも `FROM devbase-base:latest` のため、建て直せば効く
- `containers/lfm` は base 由来ではないため影響しない（要求の「未確認のまま残ること」）
- 稼働中のコンテナは、イメージを建て直しただけでは入れ替わらない

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `lang` の条件が広すぎて欧文の指定から書体を奪う（設計の決定 2 の壊れ方） | Task 1 の XML の形の検査で、`lang` の `<match>` が `family` の `<test>` を必ず持つことを固定する。Task 3 で `Arial:lang=zh-cn` の行を実測で固定する |
| ビルドキャッシュが壊れた層を配り 0 バイトのファイルを作る | `--no-cache` を必ず付ける |
| 建て直す前は Docker のテストが赤くなる | skip の 4 段目（`/etc/fonts/local.conf` の有無）で skip にする |
| 1 つ目の `RUN` の文字列が変わり、巨大な層が建て直される | 設計の決定 4 のとおり避けない。`--no-cache` でどのみち全部建て直る |
| 触る対象の構造 | Dockerfile と `tests/containers/` は既に薄く、先に整える必要は無い |

## 切り戻し手順

データの移行は無い。戻すには 4 つが要る（要求の「切り戻し手順」のとおり）。

1. ブランチの revert
2. `devbase build base --no-cache`
3. 使っている派生イメージの建て直し
4. 稼働中のコンテナの作り直し（`devbase down` → `devbase up`）。**`devbase rebuild` は使えない**

## 完了の定義

- [ ] 受け入れ条件 16 件すべてに検証手段と結果が対応している
- [ ] `uv run --locked pytest tests/ -q` が `exit=0`
- [ ] `devbase build base --no-cache` が成功し、増分が 40 MB 以下
- [ ] 解決先の表の 26 行が Pull Request 本文に載っている
- [ ] 実装を載せた Draft の Pull Request がある（#238）
