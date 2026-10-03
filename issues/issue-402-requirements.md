# #402: Playwright のブラウザ・追加の CJK フォント・terraform を base から派生イメージへ移す

正は課題の本文（#402）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

起票時の本文をそのまま写す。

> ## 何をするか
>
> すべての利用者には要らない重い中身を、base から派生イメージへ移す。base の削減の段階 3。利用者は 2026-10-03 にこの削減を承認した（MVV の P1）。
>
> ## 対象（arm64 の実測）
>
> | 中身 | 大きさ | 移す先の案 |
> | --- | --- | --- |
> | Playwright の Chromium（`/opt/ms-playwright`。chromium 393MB・headless_shell 266MB） | 662MB | ブラウザを使うプロジェクト向けの派生イメージ |
> | `playwright install --with-deps` の apt（libllvm21・mesa・フォント・xvfb など 74 個） | 296MB | 同上 |
> | `fonts-noto-cjk-extra`（Thin・Light・Medium・Black などの追加の太さだけ。標準の太さは `fonts-noto-cjk` にある） | 215MB | 文書を作る派生イメージ |
> | `terraform` | 108MB | 外す。使う者は見当たらない（`grep -rIlE terraform repos/*/`）。要るプロジェクトは派生か `post-start` で入れる |
>
> base の `@playwright/test` は残す。利用時にブラウザを取りに行けるようにするかは設計で決める。
>
> ## 決めること（設計）
>
> - 派生イメージの切り方: ブラウザと文書のフォントを 1 つ（例 `containers/browser`）にまとめるか分けるか。名前と、`devbase build` の依存の扱い
> - ブラウザを使っているプロジェクトの洗い出しと移り方（プラグインのリポジトリの `compose.yml` の image を替える。PR はプラグインのリポジトリへ）
> - base で Playwright を使ったときに、ブラウザが無いことをどう伝えるか（案内か、置き場へ取得させるか）
> - #220 で「base に Chromium を残す」と決めた判断を覆すことの記録。lfm は自分で Playwright を入れるため影響しない見込み
> - #220 の受け入れ条件 5〜9 の検査（`tests/containers/test_base_image_browser.py`）を派生イメージへ移す
>
> ## 受け入れ条件
>
> 1. base に Playwright のブラウザ・その依存の apt・`fonts-noto-cjk-extra`・`terraform` が無い。base の展開後の大きさが arm64 で 1.2GB 以上減る（前後で測り、PR に記録する）
> 2. 派生イメージで #220 の受け入れ条件 5〜9（PDF の作成と置き場の形）が通る
> 3. 標準の太さの日本語の描画は base のまま変わらない（`tests/containers/test_base_image_font_matching.py` が通る）
> 4. ブラウザを使うプロジェクトは移る先が文書に書かれ、移すための PR がプラグインのリポジトリに出ている
> 5. 確定仕様（`base-image-rendering.md` など）、利用者向けの文書、CHANGELOG を直す
>
> ## 由来
>
> base の削減のプラン（2026-10-03）。段階 1 と段階 2 は別の課題。

## 目的

- base を、すべての利用者が使う道具だけを持つ大きさへ戻す。ブラウザ・追加の太さのフォント・`terraform` を使わないプロジェクトは、それらを取得・展開・保存しなくて済む（MVV の Value 1「base を重くしすぎず、重い道具は派生イメージに置く」）
- ブラウザと文書のフォントを要るプロジェクトは、派生イメージを選べば今と同じ描画とブラウザを、建て直した直後からネットワーク無しで使える

## 調べたこと

要求を決めるために手元で確かめた事実。設計はここから始める。

| # | 事実 | 確かめた場所 |
| --- | --- | --- |
| F1 | `--with-deps` の apt は、ブラウザの共有ライブラリのほかに `fonts-liberation`・`fonts-ipafont-gothic`・`fonts-wqy-zenhei` を入れている。base の Dockerfile にこの 3 つを入れる行は無い | `docs/specifications/base-image-rendering.md` の「キャッシュの作り直し」・`containers/base/Dockerfile` |
| F2 | `tests/containers/test_base_image_font_matching.py` は `Arial` → `Liberation Sans`、`WenQuanYi Zen Hei` の名指し → `WenQuanYi Zen Hei`、`IPAPGothic` の名指し → `IPAPGothic` を期待する。F1 の 3 つが base から消えると、この検査は落ちる | 同テストの `EXPECTED_MATCHES` |
| F3 | 中国語・韓国語を明示した指定の解決先（Noto Sans CJK SC / KR など）は標準の太さのフェイスで、`fonts-noto-cjk` にある | `base-image-rendering.md` の「中国語・韓国語を明示した指定」 |
| F4 | 派生イメージ lfm は #403 で廃止され、#403 はこの課題より先に入る（スプリント m7c の順は #403 → #400 → #401 → #402）。この課題の時点で lfm は無い | #402 の課題のコメント・#403 の本文 |
| F5 | プラグインのリポジトリの dev サービスは、`build.context: ${DEVBASE_ROOT}/containers/<名前>/` で派生イメージを選ぶ。手元の 3 リポジトリでの数は `general` 79・`php` 47・`bi-tools`・`go`・`latex`・`php85`・`trygroup` が 1 ずつ。`image:` で選ぶものは `devbase-php:latest` が 1 つ | `repos/*` の `compose.yml` |
| F6 | 手元にコードがあるプロジェクト（`projects/*/repo`）でブラウザを使うのは 1 つ（以下 `proj-a`）だけで、dev サービスは `devbase-php` を使い、`.mcp.json` で `npx chrome-devtools-mcp@latest` と `npx -y @playwright/mcp@latest` を起動する。`@playwright/mcp` は自分の版の Playwright を持ち、base の Chromium と版が合わなければ実行時に取得する。`chrome-devtools-mcp` はシステムの Chrome を探すと見込まれ、システムの Chrome は #401 で両アーキとも base から外れる | `proj-a` の `.mcp.json`・`compose.yml` |
| F7 | NDF の `layout-review` は HTML のスクリーンショットを `playwright-kit` で撮る。`playwright-kit` も実行時に自分の版の Chromium をブラウザの置き場へ取得する | NDF の `skills/layout-review/SKILL.md`・`base-image-rendering.md` の「ブラウザ」 |
| F8 | 実行時に取得したブラウザが起動できるのは、`--with-deps` の共有ライブラリが base にあるためである。依存パッケージが無いと、取得は済んでも起動で落ちる。利用者 `ubuntu` は `sudo` をパスワード無しで使えるため、実行時に `--with-deps` で入れ直すことはできる（コンテナを作り直すと消える） | `containers/base/Dockerfile` の sudoers・`base-image-rendering.md` の「運用」 |
| F9 | `terraform` の名前は、プラグインのリポジトリの設定と手元のプロジェクトのコードに現れない。`containers/bi-tools/Dockerfile` の先頭のコメントが「base に含まれるもの」として `terraform` を挙げている | `grep -rIlE terraform repos/*/ projects/*/repo`・`containers/bi-tools/Dockerfile` |
| F10 | base を `FROM` で継ぐ派生イメージは `devbase build <名前>` が base の有無と鮮度を見て先に建てる。新しい派生イメージも `FROM devbase-base:latest` で書けばこの仕組みに乗る | `bin/devbase` の `check_base_image_dependency`・`lib/devbase/commands/container.py` の `_get_base_image_ref` |

## 前提

- 前提 1: P1（base の道具の変更）の承認は、依頼の 4 つの中身を base から外すことに対して 2026-10-03 に得ている。base へ新しく道具を足すこと（前提 2 のフォントの明示を除く）は、この承認に含まれない
- 前提 2: F1 の 3 つのフォント（`fonts-liberation`・`fonts-ipafont-gothic`・`fonts-wqy-zenhei`）は base に残し、`--with-deps` に頼らず base の apt の一覧で明示して入れる。base の今の中身を保つための書き換えで、道具を足すことではない。これにより `fc-match` の解決先の表（`base-image-rendering.md` の「解決先」）は 1 行も変わらない（受け入れ条件 3）
- 前提 3: 前提 2 の 3 つのフォントを残すため、減る量は依頼の表の合計（約 1.28GB）より数十 MB 小さい。それでも 1.2GB を下回るなら、フォントを外して 1.2GB を満たすのではなく、測った値を記録して人へ戻す
- 前提 4: base の共有ライブラリのうち、Dockerfile が明示して入れている `libnss3`・`libxrandr2`・`libxss1` は残す。外すのは `--with-deps` だけが入れていたパッケージである
- 前提 5: システムの Chrome（`google-chrome-stable`）は #401 で両アーキとも base から外れている（#401 が先に入る）。この変更はシステムの Chrome を扱わない。#401 とこの変更の後は base 系のイメージにブラウザが無くなり、F6 の 2 つの MCP はどちらもそのままでは動かない。`proj-a` の移り方（ブラウザの派生イメージを選ぶか、プロジェクトの Dockerfile で足すか、MCP の起動引数でブラウザの場所を渡すか）はプラグインのリポジトリ側の Pull Request で決める
- 前提 6: 派生イメージ lfm は #403 で先に廃止されている（F4）。この変更は lfm を扱わない
- 前提 7: ブラウザを使うプロジェクトの洗い出しは、手元にある 3 つのプラグインのリポジトリと `projects/*/repo` に限る。手元に無いプロジェクトは、CHANGELOG と利用者向けの文書の移り方で知らせる
- 前提 8: 派生イメージの名前・数（ブラウザと文書のフォントを 1 つにまとめるか分けるか）・base に `ENV PLAYWRIGHT_BROWSERS_PATH` とブラウザの置き場を残すか・HashiCorp の apt の取得元を残すかは設計で決める。受け入れ条件はどの切り方でも判定できる形で書く
- 前提 9: 大きさは同じ arm64 の端末で、変更前の base と変更後の base を `docker image inspect --format '{{.Size}}'` で測り、差を PR に記録する。base を 2 本並べるため、建てる前に `docker system df` で空きを見て、作業用のタグは記録の後に消す（`AGENTS.md` の落とし穴）

## 対象範囲

含む:

- base の Dockerfile から、Playwright の Chromium の取得・`--with-deps` の apt・`fonts-noto-cjk-extra`・`terraform` を外す
- 前提 2 の 3 つのフォントを base の apt の一覧で明示する
- ブラウザと追加の太さのフォントを持つ派生イメージを `containers/` に足す（数と名前は設計）
- base で Playwright の Chromium が無いときの伝え方（設計で決める形）
- #220 の受け入れ条件 5〜9 の検査（`tests/containers/test_base_image_browser.py`）と、Dockerfile の形の検査（`tests/containers/test_base_dockerfile_playwright.py`・`test_base_dockerfile_fonts.py`）を、新しい形に合わせて移す・直す
- `containers/bi-tools/Dockerfile` の先頭のコメントの `terraform` を直す
- 確定仕様（`base-image-rendering.md`）・利用者向けの文書（`docs/user/container-operations.md` など）・用語集・`CHANGELOG.md` の `[Unreleased]`
- ブラウザを使うプロジェクト（F6 のプロジェクト `proj-a`）の移り方を文書に書き、プラグインのリポジトリへ移すための Pull Request を出す（境界の「確認してから行う」）
- #220 で「base に Chromium を残す」と決めた判断を覆すことの記録（設計文書の決定の記録）

含まない:

- 段階 1・段階 2 の削減（別の課題）
- システムの Chrome の扱い（#401。前提 5）
- lfm（#403 で先に廃止。前提 6）
- `proj-a` の移り方の選択（プラグインのリポジトリの Pull Request で決める。前提 5）
- base の `@playwright/test` を外すこと（依頼が残すと決めている）
- 手元に無いプロジェクトへの個別の Pull Request（前提 7）
- amd64 での実測（#242 と同じく、amd64 の端末で建てるまで分からない。リリース後テストへ回す）
- 実際の利用者の環境での `devbase up`（P3）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | base のビルドが、`fonts-noto-cjk-extra`・`terraform` を除き、前提 2 の 3 つのフォントを明示した apt の一覧で入れた | `devbase build base` | apt が失敗すればビルドが非 0 で止まる | — |
| E2 | base のビルドが、Chromium を取得せず `--with-deps` の apt も打たずに終わった | E1 の後の利用者の `RUN` | — | E1 → 受け入れ条件 1・2 |
| E3 | base のビルドがフォントのキャッシュを作り直した | フォントの規則の `COPY` | — | E1 の後。**Playwright の `RUN` がフォントを入れなくなっても、キャッシュが前提 2 の 3 つを知っていると仮定している** → 受け入れ条件 4 |
| E4 | 派生イメージのビルドが、base の上に Chromium・ブラウザの依存パッケージ・追加の太さのフォントを入れた | `devbase build <派生イメージ>`。base が無いか古ければ先に E1〜E3 が起きる（F10） | 取得か apt が失敗すればビルドが非 0 で止まる | E1〜E3 → 受け入れ条件 6〜9 |
| E6 | 利用者が base（または base を継ぐ派生イメージのうちブラウザを持たないもの）のコンテナで Playwright の Chromium を起動しようとし、失敗した | 利用者・道具の起動 | 失敗の出力か文書から、次にすることが辿れなければ利用者が止まる → 受け入れ条件 5 | E2 |
| E7 | 利用者が base のコンテナで、実行時にブラウザとその依存パッケージを取得した | `npx playwright install --with-deps chromium` など | ネットワークが無ければ失敗する。コンテナを作り直すと消える（F8） | E6 と独立 → 未決 U2 |
| E8 | ブラウザを使うプロジェクトが、`compose.yml` で派生イメージを選ぶように替わった | プラグインのリポジトリへの Pull Request のマージ | Pull Request が出ていなければ、そのプロジェクトは base 系のまま E6 に当たる | E4 が `main` に入った後 → 受け入れ条件 13 |

E5（lfm のビルド）は欠番にした（前提 6）。

## 用語

| 用語 | 意味 |
| --- | --- |
| 派生イメージ | `FROM devbase-base:latest` で base を継ぎ、道具を足すイメージ（`containers/` の `general`・`php` など） |
| 追加の太さのフォント | `fonts-noto-cjk-extra` が入れる Noto CJK の Thin・Light・Medium・Black などのフェイス。標準の太さ（Regular・Bold）は `fonts-noto-cjk` にある |
| ブラウザの置き場 | 用語集の定義のまま |
| Playwright の Chromium | 用語集の定義のまま |
| ブラウザの依存パッケージ | 用語集の定義のまま |
| システムの Chrome | 用語集の定義のまま |

## 受け入れ条件

「変更後の base」は、変更後の `containers/base` から作業用のタグ（前提 9）で建てた arm64 のイメージを指す。「ブラウザの派生イメージ」は、設計が決める、ブラウザを持つ派生イメージを指す（文書のフォントと同じイメージか別かは前提 8）。

Dockerfile の形（Docker を起動しないテストで確かめる）:

- [ ] 1. `containers/base/Dockerfile` に `npx playwright install` が無く、`apt-get install` の一覧に `fonts-noto-cjk-extra` と `terraform` が無い
- [ ] 2. `containers/base/Dockerfile` の 1 回目の `apt-get install` の一覧に `fonts-liberation`・`fonts-ipafont-gothic`・`fonts-wqy-zenhei` がある（前提 2）
- [ ] 3. `containers/base/Dockerfile` の `npm i -g` の一覧に `@playwright/test` が今のまま残る

変更後の base（arm64）:

- [ ] 4. `tests/containers/test_base_image_font_matching.py` が、`EXPECTED_MATCHES` を書き換えずに変更後の base で通る（標準の太さの日本語・中国語・韓国語と欧文の metric 互換の解決先が変わらない）
- [ ] 5. 変更後の base を `--network none` で起動し、利用者 `ubuntu` の非対話の `bash -c` で Playwright の Chromium を headless で起動しようとすると、終了コードが非 0 で、出力にブラウザの派生イメージの名前か取得のコマンド（`playwright install`）が含まれる
- [ ] 6. 変更後の base で `dpkg -s fonts-noto-cjk-extra` が非 0、`command -v terraform` が非 0 で終わり、`/opt/ms-playwright` の下に Chromium の版のディレクトリ（`chromium-*`・`chromium_headless_shell-*`）が無い
- [ ] 7. 変更前の base と変更後の base の `docker image inspect --format '{{.Size}}'` の差が、arm64 で 1.2GB（1,200,000,000 バイト）以上ある。両方の値と差を PR に記録する（前提 3）

ブラウザの派生イメージ（arm64）:

- [ ] 8. ブラウザの派生イメージで、#220 の受け入れ条件 5〜9（ネットワーク無しで日本語のページを PDF にできる・利用者がブラウザの置き場に書ける・PDF の書体が `NotoSansCJKjp` で `WenQuanYi` を含まない・ブラウザの依存パッケージがあり `chromium-browser` が無い・`PLAYWRIGHT_BROWSERS_PATH` が置き場を指し `~/.cache/ms-playwright` が無い）が通る。検査は `tests/containers/test_base_image_browser.py` を移したテストが、検査するイメージを環境変数で差し替えて行う
- [ ] 9. 追加の太さのフォントを持つ派生イメージで `dpkg -s fonts-noto-cjk-extra` が 0 で終わり、`fc-list` に `Noto Sans CJK JP` の `Thin` と `Black` のフェイスがある
- [ ] 10. `devbase build <ブラウザの派生イメージ>` が、base が無いときに base を先に建ててから派生イメージを建てる（F10 の仕組みに乗る）

受け入れ条件 11（lfm）は欠番にした（前提 6）。

移り方と文書:

- [ ] 12. 利用者向けの文書に、ブラウザ・追加の太さのフォント・`terraform` が要るプロジェクトの移り方（派生イメージを選ぶ `compose.yml` の書き方、または `post-start` で入れる手順）が書かれている。`CHANGELOG.md` の `[Unreleased]` に、base から外れたものと移り方の文書へのリンクがある
- [ ] 13. F6 のプロジェクト `proj-a` について、移る先（ブラウザの派生イメージを選ぶか、プロジェクトの Dockerfile で足すか、MCP の起動引数でブラウザの場所を渡すか）が文書に書かれ、移すための Pull Request がプラグインのリポジトリに出ている（境界の「確認してから行う」）
- [ ] 14. `docs/specifications/base-image-rendering.md` が、base に Chromium・追加の太さのフォント・`terraform` が無いことと、ブラウザの派生イメージの置き場を書く。#220 の「base に Chromium を残す」を覆したことが、設計文書の決定の記録に理由とともにある
- [ ] 15. `containers/bi-tools/Dockerfile` の先頭のコメントが、base に含まれるものとして `terraform` を挙げていない

退行しない:

- [ ] 16. `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q`・`uvx ruff check --select=E9,F63,F7,F82 lib`・`python3 .github/scripts/proper_term_check.py` が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | base の大きさが arm64 で 1.2GB 以上減る（受け入れ条件 7）。ブラウザの派生イメージの大きさは、変更前の base と比べて記録する（上限は置かない） |
| 移行性 | 移る作業は、プロジェクトの `compose.yml` の 1 か所（派生イメージの選択）か `post-start` の追記で済む。利用者のボリュームとデータには触れない |
| 運用・保守性 | 変更は base と派生イメージを建て直すまで効かない。建て直しの順（base → 派生イメージ → `devbase down` / `up`）を利用者向けの文書と CHANGELOG に書く |
| システム環境 | 受け入れ条件は arm64 で確かめる。amd64 で同じ条件が成り立つかはリリース後テストで確かめる |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。base（と base を継ぐ `general`・`php` など）から Chromium・ブラウザの依存パッケージ（前提 2 の 3 つのフォントを除く）・追加の太さのフォント・`terraform` が消える。互換は保たない。移り方を文書と CHANGELOG で示す |
| データ | 変わらない。利用者のボリュームには触れない |
| 既存の振る舞い | base 系のコンテナで Playwright の Chromium・`@playwright/mcp`・`playwright-kit` を使う道具は、ブラウザの派生イメージへ移るか実行時に依存パッケージごと取得するまで動かない（F6〜F8）。#401 でシステムの Chrome も外れているため、`chrome-devtools-mcp` も `proj-a` の移行の Pull Request が入るまで動かない（前提 5） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動（変更後の base） | `docker system df` で空きを見てから `docker build -t devbase-base:issue-402 containers/base` を建て、`DEVBASE_TEST_BASE_IMAGE=devbase-base:issue-402` で受け入れ条件 4〜6 の検査を打つ |
| 起動（派生イメージ） | 変更後の base を `devbase-base:latest` として派生イメージを建て、受け入れ条件 8〜10 の検査を打つ |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` |
| 静的解析 | `uvx ruff check --select=E9,F63,F7,F82 lib`・`python3 .github/scripts/proper_term_check.py` |
| 手動確認（マージ前） | 受け入れ条件 4〜10 を手元の arm64 で確かめ、受け入れ条件 7 の大きさの値とともに出力を PR に残す。作業用のタグは記録の後に消す |
| 手動確認 | amd64 で受け入れ条件 4〜10 が成り立つこと。`proj-a` が移った先で `@playwright/mcp` と `chrome-devtools-mcp` を使えること（プラグインのリポジトリの Pull Request で確かめる）。リリース後テストで確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「このリポジトリ」。イメージの定義は `containers/` に置く。`projects/*` の `compose.yml` はプラグインのリポジトリの管理で、このリポジトリでは直さない |
| コーディング規約 | `AGENTS.md` の「書き方」と `docs/developer/contributing.md`。Dockerfile の変更理由は周りと同じく行の上のコメントに課題の番号つきで書く |
| テスト戦略 | Dockerfile の形は `tests/containers/` の Docker を要さないテストで固定する（受け入れ条件 1〜3・15）。ブラウザ・書体・大きさは建てたイメージで確かめる（受け入れ条件 4〜10）。Docker を要するテストは検査するイメージを環境変数で差し替えられる形を保つ |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体のテスト・lint・固有の語の検査。base を建てる前の `docker system df`。作業用のタグを記録の後に消す |
| 確認してから行う | プラグインのリポジトリへの Pull Request（C5。`proj-a` は他の組織のリポジトリ）。base へ前提 2 の 3 つ以外の道具を足すこと（P1）。base の削減が 1.2GB に届かないときの扱い（前提 3） |
| 行わない | タグ・GitHub Release（P2）。実際の利用者の環境での `devbase up`（P3）。システムの Chrome の扱い（#401）と lfm（#403）。段階 1・2 の範囲の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| U1: 派生イメージの切り方（ブラウザと追加の太さのフォントを 1 つにまとめるか分けるか）と名前。`php` など base 以外の派生イメージを使うプロジェクト（F6）がブラウザを使うときの道筋（ブラウザの派生イメージを `FROM` に取るか、プロジェクト側で入れるか） | 設計 | 設計 PR の承認まで |
| U2: base でブラウザが無いときの伝え方。Playwright 自身の失敗の出力に任せるか、案内を足すか、実行時に `--with-deps` で取得させるか（E7。取得させるとコンテナを作り直すたびに数百 MB を取り直す） | 設計 | 設計 PR の承認まで。受け入れ条件 5 はどれでも判定できる |
| U3: base に `ENV PLAYWRIGHT_BROWSERS_PATH` とブラウザの置き場（`/opt/ms-playwright`）を残すか。残さないと、ブラウザの派生イメージが置き場を宣言し直し、base 系のコンテナで実行時に取得する先が `~/.cache/ms-playwright` に変わる | 設計 | 設計 PR の承認まで |
| U4: HashiCorp の apt の取得元を base に残すか（残すと `post-start` で `sudo apt-get install terraform` だけで入る） | 設計 | 設計 PR の承認まで |
| U5: 前提 2 で残す `fonts-ipafont-gothic`・`fonts-wqy-zenhei` を、将来 base から外すか。外すと `fc-match` の解決先の表と受け入れ条件 4 の検査が変わる | 利用者（P1 に当たるため） | この変更では外さない。外すなら別の課題 |

## 変更の記録

| 日 | 工程 | 変えたこと | 理由 |
| --- | --- | --- | --- |
| 2026-10-03 | 設計 | 前提 5 を「システムの Chrome は #401 で両アーキとも base から外れている。この変更は扱わない」へ、前提 6 を「lfm は #403 で先に廃止されている」へ改め、F4・F6・対象範囲・E5・用語・受け入れ条件 11・13・14・影響・検証手段・前提とする取り決め・境界・U3 を合わせて直した。E5 と受け入れ条件 11 は欠番 | スプリント m7c の順が #403 → #400 → #401 → #402 に決まり、この課題の時点で lfm とシステムの Chrome が base 系に無い |
