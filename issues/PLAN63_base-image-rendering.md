# PLAN63: base イメージの日本語の描画と、文書を扱う軽量の道具

対象 issue: devbasex/devbase#161, devbasex/devbase#160

- ワークフローモード: `standard`
  - 根拠: base イメージの本番の振る舞い（総称ファミリの解決先と、同梱するパッケージ）を変える。
    base はすべての派生イメージとプロジェクトの土台で、変更は再ビルドした全員に届く
- 2 件を 1 本にする理由: どちらも `containers/base/Dockerfile` の同じ apt / COPY の区画を触る。
  分けると同じ箇所で競合し、レビューした差分と入る差分が変わる。#160 の本文も
  「#161 を先に入れるか、1 本の PR にまとめる」と書いている
- ベースブランチ: `release/v3.7.0`（release Pull Request は #212）

## 目的

- **base コンテナで日本語を描いたとき、日本語のフェイスで描かれる。** 総称ファミリ
  （`sans-serif` / `serif` / `monospace`）と、日本語環境でよく指定される書体名
  （`Meiryo` / `Yu Gothic` / `MS PGothic` / `Noto Sans JP`）と、イメージに無い書体名のすべてが、
  Noto CJK の JP フェイスへ解決される
- **欧文と、中国語・韓国語を明示した指定は壊さない。** `Arial` / `Times New Roman` / `Courier New`
  は metric 互換のまま、`lang=zh-cn` / `lang=ko` はそれぞれの言語のフェイスのままにする
- **PDF を画像にする・調べる、OOXML を壊さずに読み書きする、欧文の字幅を正しく測る**が、
  base だけで（派生イメージも外部のサービスも使わずに）できる

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（CLI の引数・環境変数・コマンドは増減しない） |
| データ | 変わらない |
| 既存の振る舞い | **変わる。** base コンテナの中で描かれる文字のフェイスが変わる。イメージを建て直すまでは変わらない。CHANGELOG では #161 を Fixed、#160 を Added に書く |
| イメージのサイズ | +約 24 MB（7.09GB に対して +0.34%）。`fonts-local.conf` 自体は 0 |
| 利用者の操作 | **`devbase build base --no-cache` が要る。** `devbase up` では反映されない |

## 前提

- **前提 1: base の既定は日本語にする。** 利用者は日本語話者で、扱う文書も日本語が多い。
  中国語・韓国語は `lang` を明示したときだけそのフェイスを保つ形にする（言語を明示しない
  中国語の文書は日本語の字形で描かれる。これは意図した振る舞いで、変更前の「日本語の文書が
  中国語の字形で描かれる」を裏返したものである）
- **前提 2: `fonts-wqy-zenhei` は削除しない。** #161 が実測付きで結論している。削除しても
  OS 既定の `65-nonlatin.conf` が `sans-serif` の prefer 一覧に `WenQuanYi Zen Hei` を含むため
  日本語にはならず（タイ語の Loma か IPAPGothic へ落ちる）、中国語のページを豆腐にするだけになる
- **前提 3: LibreOffice は base に入れない。** #160 の決定。展開 372〜459 MB は base の規律に
  見合わない
- **前提 4: `containers/docs` は新設しない。** #160 の表題の範囲は base への追加であり、
  派生イメージの新設はそれを超える。#219 として起票済み（派生イメージ・使い捨てコンテナ・
  Google Slides の 3 つの比べ方と、LibreOffice の実測表を引き写してある）
- **前提 5: Playwright のブラウザと arm64 の Chromium には手を付けない。** #161 の「決めること」は
  #220 として起票済み。`playwright-kit` が実行時に `uv sync` + `playwright install chromium` を
  自前で行う設計であることを確認した（`devbasex/ai-plugins` の
  `plugins/playwright-kit/skills/playwright-kit-ops/templates/run.sh` ほか）
- **前提 6: CI はこの変更を検査しない。** `.github/workflows/ci.yml` にイメージを建てるジョブは
  1 つも無く、`on.pull_request.branches` は `main` だけである。**`release/v3.7.0` を base にした
  Pull Request では検査ジョブが 1 件も動かない**（#216）。そのため、イメージの中でしか確かめ
  られない受け入れ条件の証跡は、**手元で建てたイメージから採って Pull Request 本文へ載せる**
- **前提 7: `ENV LANG` は設定しない。** 言語のヒントに頼らず、総称ファミリの解決先そのものを
  日本語にする（`fc-match sans-serif` は `lang` を明示しなくても日本語になる）。`LANG` を
  `ja_JP.UTF-8` にすると、コンテナの中のすべてのコマンドの出力・ソート順・日付の書式が変わり、
  影響がフォントの外へ出る
- **前提 8: 追加するのは 6 パッケージだけで、`pip` は足さない。** Python パッケージが要るときは
  既にある `uv` / `uvx` で賄う（`markitdown` は `uvx` のままにする。焼き込むと展開で 200 MB 前後）

## 対象範囲

含む:

- `containers/base/fonts-local.conf`（新設）と、それを `/etc/fonts/local.conf` へ置く `COPY`、
  および `fc-cache -f` の実行（#161）
- `containers/base/Dockerfile` への 6 パッケージの追加（#160）
  （`poppler-utils` / `python3-pil` / `python3-defusedxml` / `python3-lxml` /
  `fonts-crosextra-carlito` / `fonts-crosextra-caladea`）
- 回帰テスト（`tests/containers/`）
- 利用者向け文書と CHANGELOG

含まない:

- LibreOffice の追加（前提 3）
- `containers/docs` の新設（前提 4、#219）
- Playwright のブラウザの導入方法と arm64 の Chromium（前提 5、#220）
- `fonts-wqy-zenhei` の削除（前提 2）
- `ENV LANG` の設定（前提 7）
- `pip` の追加（前提 8）
- 派生イメージ（`containers/general` / `go` / `php` / `php85` / `bi-tools` / `latex` / `trygroup`）の
  Dockerfile の変更。いずれも `FROM devbase-base:latest` なので、base を建て直せば自動的に効く
- `containers/lfm` の変更。`FROM nvidia/cuda:...` で base 由来ではなく、`fonts-noto-cjk` を
  自前で入れている。同じ問題を抱えるかは未調査（下の「未確認のまま残ること」）
- 新しい型・永続データ・画面の追加（そのためクラス図・ER 図・画面遷移図を作らない）

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | base イメージの構成物は `containers/base/` の直下に置く。ビルドコンテキストはこのディレクトリそのもの（`lib/devbase/commands/container.py` の `_build_single_image` が `str(image_dir)` を渡す）なので、`COPY` したいファイルはここに置けば届く |
| コーディング規約 | `containers/base/Dockerfile` の既存の書き方に合わせる。`COPY --chmod=` で権限を明示し、なぜそうするかを直前のコメントに書く |
| テスト戦略 | `tests/containers/` の既存の流儀に合わせる。既定は Docker に依存しない検査（Dockerfile とスクリプトの文字列・関数の呼び出し）。Docker が要る検査は `tests/snapshot/test_restore_incremental.py` の先例（fixture の中で `shutil.which` → `docker info` → `docker image inspect` の 3 段を見て `pytest.skip`）に合わせる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 手元で全体テスト、`devbase build base --no-cache` と解決先の表の採取 |
| 確認してから行う | 前提 1（日本語を既定にする）と受け入れ条件 6 の表の確定（設計 Pull Request の承認で確かめる） |
| 行わない | LibreOffice の追加、`containers/docs` の新設、`fonts-wqy-zenhei` の削除、`ENV LANG` の設定、Playwright まわりの変更 |

## 実装計画

設計は [PLAN63_base-image-rendering-design.md](PLAN63_base-image-rendering-design.md)。
**タスクへの分解は実装の持ち場で `/ndf:implementation-plan` が行う。** ここでは触る対象だけを挙げる。

### 修正対象

- `containers/base/fonts-local.conf`（新設）
- `containers/base/Dockerfile`
- `tests/containers/test_base_dockerfile_fonts.py`（新設）、
  `tests/containers/test_base_image_font_matching.py`（新設。Docker が要る）
- `docs/user/container-operations.md`（base の説明に文書の道具とフォントの行を足す）
- `CHANGELOG.md`

### 切り戻し手順

- イメージの中身の変更のみ（データ移行なし）。ブランチの revert と `devbase build base --no-cache` で戻せる
- **建て直すまで戻らない。** 利用者の手元のイメージは、建て直した時点で変わり、建て直さなければ変わらない

## 受け入れ条件

**実測の基準**: 以下の「現状」はすべて、2026-09-22 に手元の `devbase-base:latest`
（Ubuntu 26.04 / arm64 / fontconfig 2.17.1 / 7.09GB）で採った。イメージを建て直さずに
確かめられるものは、稼働中のコンテナへ設定とパッケージを入れて確かめてある。

### フォントの解決先（#161）

- [ ] 1. 建てた base イメージの中で `fc-match sans-serif` が `Noto Sans CJK JP` を返す
      （現状: `WenQuanYi Zen Hei`）
- [ ] 2. `fc-match sans-serif:lang=ja` も `Noto Sans CJK JP` を返す（現状: `WenQuanYi Zen Hei`）。
      `fc-match -s sans-serif:lang=ja` の 1 件目も `Noto Sans CJK JP` になる
      （現状: `WenQuanYi Zen Hei` → `IPAPGothic` → `Loma` の順）
- [ ] 3. `fc-match serif` が `Noto Serif CJK JP`、`fc-match monospace` が `Noto Sans Mono CJK JP` を返す
      （現状: `WenQuanYi Zen Hei` / `WenQuanYi Zen Hei Mono`）
- [ ] 4. `Noto Sans JP` / `Meiryo` / `Yu Gothic` / `MS PGothic` と、イメージに無い書体名
      （`Zen Kaku Gothic New`）が、いずれも `Noto Sans CJK JP` を返す（現状: すべて `WenQuanYi Zen Hei`）
- [ ] 5. 欧文が壊れない。`Arial` → `Liberation Sans`、`Times New Roman` → `Liberation Serif`、
      `Courier New` → `Liberation Mono`、`Calibri` → `Carlito`、`Cambria` → `Caladea`
- [ ] 6. **他言語が壊れない。`lang` を明示した指定は、その言語の、しかも同じ様式（sans / serif /
      等幅）のフェイスを返す。**

      | 指定 | 返すもの |
      | --- | --- |
      | `sans-serif:lang=zh-cn` | `Noto Sans CJK SC` |
      | `serif:lang=zh-cn` | `Noto Serif CJK SC` |
      | `monospace:lang=zh-cn` | `Noto Sans Mono CJK SC` |
      | `sans-serif:lang=ko` | `Noto Sans CJK KR` |
      | `serif:lang=ko` | `Noto Serif CJK KR` |
      | `Arial:lang=zh-cn` | `Liberation Sans`（欧文の指定は言語で変わらない） |
      | `Times New Roman:lang=zh-cn` | `Liberation Serif` |
      | `WenQuanYi Zen Hei`（名指し） | `WenQuanYi Zen Hei`（残る） |

- [ ] 7. 設定は `/etc/fonts/local.conf` に置かれ、`/etc/fonts/conf.d/` には 1 つも置かれない。
      ビルドの中で `fc-cache -f` が 1 度走る
      検証: `tests/containers/` の Dockerfile の文字列検査
- [ ] 8. `/etc/fonts/local.conf` から動かさない理由（`conf.d/99-*.conf` では効かないこと、および
      その実測）が、`containers/base/fonts-local.conf` の先頭のコメントと設計文書の
      「決定の記録」の**両方**にある
      検証: `tests/containers/` の文字列検査（コメントの有無）と、設計文書の目視

### 文書を扱う道具（#160）

- [ ] 9. `pdftoppm` / `pdfinfo` / `pdffonts` / `pdftocairo` が `PATH` にある
- [ ] 10. `python3 -c "import PIL, defusedxml, lxml"` が終了コード 0
- [ ] 11. `fc-match Calibri` が `Carlito`、`fc-match Cambria` が `Caladea` を返す（受け入れ条件 5 と同じ行）
- [ ] 12. `soffice` / `libreoffice` / `pip` / `pip3` のいずれも `PATH` に無い（前提 3・8 のまま）。
       `uv` はある
- [ ] 13. 追加するのは 6 パッケージの指定だけで、依存を含めて新規に入るのは 24 パッケージ、
       イメージの増分は **約 24 MB**（7.09GB に対して +0.34%）に収まる
       検証: `devbase build base --no-cache` の前後の `docker images` の差

### 退行しないこと

- [ ] 14. `uv run --locked pytest tests/ -q` が終了コード 0（既存の `tests/containers/` の 8 ファイルを含む）
- [ ] 15. `devbase build base --no-cache` が arm64 で成功する。6 パッケージは Ubuntu 26.04 の
       arm64 にすべて存在する（版は下の表）
- [ ] 16. base を建て直した後、派生イメージ（`containers/general` など）を建て直すと、同じ
       解決先になる（`FROM devbase-base:latest` のため）。1 つで確かめる

| パッケージ | 版（2026-09-22 / arm64） |
| --- | --- |
| `poppler-utils` | 26.01.0-2ubuntu0.1 |
| `python3-pil` | 12.1.1-2ubuntu1.3 |
| `python3-defusedxml` | 0.7.1-3build1 |
| `python3-lxml` | 6.0.2-1build1 |
| `fonts-crosextra-carlito` | 20230309-2 |
| `fonts-crosextra-caladea` | 20200211-2 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --locked pytest tests/ -q` |
| イメージの中の解決先 | `devbase build base --no-cache` の後、`docker run --rm --entrypoint /bin/bash devbase-base:latest -c 'fc-match ...'` の表（受け入れ条件 1〜6・9〜12）。出力を Pull Request 本文へ貼る |
| イメージのサイズ | ビルドの前後の `docker images` |
| CI | **動かない**（前提 6、#216）。`gh pr checks` の `no checks reported` と `mergeStateStatus: CLEAN` は「通った」ことを意味しない |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| LibreOffice と Chromium での実際の描画 | どちらも base に無いため実機未検証。確かめたのは `fc-match` の水準まで（#161 と同じ）。LibreOffice は fontconfig とは別の照合も持つ |
| `containers/lfm` | `FROM nvidia/cuda:...` で base 由来ではなく、`fonts-noto-cjk` を自前で入れている（`containers/lfm/Dockerfile:23`）。同じ問題を抱えるかは未調査。抱えていれば別途起票する |
| amd64 での再現 | 手元は arm64 のみ。amd64 では `google-chrome-stable` が追加で入るため、フォントの顔ぶれが違いうる |
## 依頼（原文）

#161:

> **base コンテナでは、日本語が中国語のフォントで描画される。** 総称ファミリの `sans-serif` そのものが中国語フォントへ解決される。
>
> **影響は Office 文書の描画に限らない。** fontconfig は Chromium / Playwright のスクリーンショット、PDF の生成、画像の生成すべてが参照する。**日本語を含むページを撮ると、中国語の字形で写る。**
>
> `/etc/fonts/local.conf` を1つ置く。（…）Dockerfile へは `COPY --chmod=0644 fonts-local.conf /etc/fonts/local.conf` を足し、`fc-cache -f` を1度走らせる。**追加パッケージは要らない（サイズは 0）。**
>
> **`conf.d/99-*.conf` へ置くと効かない。** 同じ内容で実測した。
>
> **`fonts-wqy-zenhei` を外す案は採らない。** rdepends が空なので削除自体はできるが、上の実測のとおり `65-nonlatin.conf` が残るため `sans-serif` はタイ語や IPAPGothic へ落ちるだけで、日本語にはならない。

#160:

> **base コンテナに、Office 文書を画像へ描画する経路が1つも無い。**（…）
>
> **LibreOffice は base に入れない（決定）。** 展開 372〜459 MB は base の規律に見合わない。
>
> ```
> poppler-utils
> python3-pil python3-defusedxml python3-lxml
> fonts-crosextra-carlito fonts-crosextra-caladea
> ```
>
> **展開 約24 MB / ダウンロード 約7 MB、7.09GB の base に対して +0.34%。**
>
> **`pip` は足さない。`uv` があるため、必要な Python パッケージはそちらで賄う。**
>
> #161 と本件は `containers/base/Dockerfile` の同じ apt / COPY の区画を触る。**#161 を先に入れるか、1 本の PR にまとめる。**

