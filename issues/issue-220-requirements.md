# #220: chore: base が Playwright のブラウザを取得直後に捨てている。arm64 には動く Chromium が入らない

正は課題の本文（#220）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

起票時の本文をそのまま写す。続けて、決定に関わる利用者のコメント（2026-09-22）を写す。

> ## 何を見つけたか
>
> **base イメージのビルドは Playwright のブラウザを取得した直後に捨てており、残るのは副作用のフォントだけになっている。** `containers/base/Dockerfile` の利用者側の `RUN` は次の順で動く。
>
> ```
> npx playwright install --with-deps chromium;   # :225 ブラウザを ~/.cache/ms-playwright へ入れる
> sudo rm -rf /tmp/* ~/.cache ...                 # :229-230 直後に消す
> ```
>
> Playwright の既定の置き場は `~/.cache/ms-playwright` なので、**入れたブラウザはイメージに残らない。** 残るのは `--with-deps` が入れた 86 パッケージのほうで、その中に `fonts-wqy-zenhei`（中国語フォント）が含まれる。これが #161 の原因である（`apt-cache rdepends --installed fonts-wqy-zenhei` の Reverse Depends は空で、依存としては誰からも要求されていない）。
>
> **arm64 では、動作する Chromium がそもそも入らない。** `google-chrome-stable` は `amd64` のときだけ足され（`containers/base/Dockerfile:56-64` の `if [ "$arch" = "amd64" ]`）、両アーキで入る `chromium-browser`（`:71`）は Ubuntu の snap スタブで、コンテナの中では起動しない。
>
> **Google の apt は arm64 の `google-chrome-stable` も配っている。** `http://dl.google.com/linux/chrome/deb/dists/stable/Release` は `Architectures: amd64 arm64` を返し、`binary-arm64/Packages` に `google-chrome-stable 154.0.8037.57-1` がある（2026-09-29 に確認）。`amd64` に限る分岐は、arm64 版が無かった時点の前提のまま残っている。`containers/lfm/Dockerfile:46` は同じ apt を `$(dpkg --print-architecture)` で足しており、アーキを限っていない。
>
> **`main` = `cc71c3c` で建てた base イメージ（arm64）でも、ブラウザは残っていない（2026-10-01 に確認）。** `~/.cache/ms-playwright`・`/opt/ms-playwright` はどちらも無く、`google-chrome-stable` も無い。`/usr/bin/chromium-browser` は snap スタブ（"requires the chromium snap to be installed"）である。
>
> **`playwright-kit` は実行時に自前で入れる設計である（確認済み）。** `devbasex/ai-plugins` の `plugins/playwright-kit/skills/playwright-kit-ops/templates/run.sh` と `scripts/init_project.sh` は、`.venv` が無ければ `uv sync` + `playwright install chromium` を初回に実行する。`templates/runtime-README.md` も「初回は `uv sync` と `playwright install chromium` が自動実行されます (数分)」と書いている。**したがって、消されているブラウザのキャッシュは `playwright-kit` の利用者には影響しない。**
>
> ## どこで見つけたか
>
> - `containers/base/Dockerfile`（利用者側の `RUN` の `npx playwright install --with-deps chromium`（:225）と、その直後の `sudo rm -rf ~/.cache`（:229-230））
> - `devbasex/ai-plugins` の `plugins/playwright-kit/skills/playwright-kit-ops/`（実行時導入の確認）
> - #161 の「決めること」
>
> ## 決めること
>
> | 論点 | 案 |
> | --- | --- |
> | ビルド時の `npx playwright install` を残すか | **消すなら `--with-deps` の扱いに注意が要る。** `--with-deps` が入れた `fonts-liberation`（Arial / Times New Roman の metric 互換）と `libnss3` などの共有ライブラリは、#161 の直し方（`Arial` → Liberation Sans）と Chromium の動作が前提にしている。ブラウザの取得だけをやめ、依存パッケージは明示で apt に足す形が要る |
> | ブラウザをイメージに残すか | 残すなら `PLAYWRIGHT_BROWSERS_PATH` を `~/.cache` の外（例: `/opt/ms-playwright`）へ向けて、クリーンアップで消えないようにする。イメージは約 150 MB 増える |
> | arm64 の Chromium | `chromium-browser`（snap スタブ）を入れるのをやめ、arm64 では Playwright の Chromium か、`chromium` の deb を使う形にする。Google の apt の `google-chrome-stable` が arm64 でも取れるため、`arch=amd64` の制限を外して両アーキで Chrome を入れる案もある |
>
> ## 決定
>
> **ブラウザはイメージに残す。** `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` にしてクリーンアップの対象外とし、arm64 でも Playwright の Chromium を使う。`chromium-browser`（snap スタブ）は apt から外す。
>
> 着手の時点で、`google-chrome-stable` の `arch=amd64` の制限を外す案（上の表の 3 行目）と合わせるかを確かめる。システムの Chrome を使う道具（スクリーンショット・PDF 生成）が arm64 でも動くかは、この選択で決まる。
>
> ## 影響する検査と仕様
>
> ブラウザの置き場を変える、または `--with-deps` を明示の apt に分けるときは、次も合わせて直す。どれも「Playwright の `RUN` が `--with-deps` でフォントを入れ、その後に `fc-cache -f` を走らせる」順序を前提にしている。
>
> | 場所 | 前提にしていること |
> | --- | --- |
> | `tests/containers/test_base_dockerfile_fonts.py:175-178`（`test_fc_cache_runs_after_playwright_installs_its_fonts`） | Dockerfile の本文で `npx playwright install` が `fc-cache -f` より前にある |
> | `docs/specifications/base-image-rendering.md:192-195`（決定 5） | `fc-cache -f` を Playwright がフォントを入れる `RUN` より後に置く |
> | `containers/base/Dockerfile:293-297`（コメントと `RUN sudo fc-cache -f`） | 同じ順序の理由 |
> | `containers/lfm/Dockerfile`（`ENV` の宣言と `:154` の `npx playwright install --with-deps chromium`） | lfm は base の `/opt` を `COPY --from` で取り込む（#275）。ブラウザを `/opt/ms-playwright` に置くと lfm にも届くが、`ENV` は運べないため `PLAYWRIGHT_BROWSERS_PATH` を lfm にも同じ値で宣言する。宣言が無いと `tests/containers/test_lfm_base_settings.py` が `ENV PLAYWRIGHT_BROWSERS_PATH` の名前を挙げて落ちる。lfm 自身の `npx playwright install` を残すかも合わせて決める |
> | `tests/containers/test_lfm_base_settings.py:336-345`（`test_fc_cache_runs_once_after_the_fonts_conf_and_playwright`） | lfm で `npx playwright install --with-deps` の後に `fc-cache -f` が 1 回だけ走る |
> | `docs/specifications/lfm-base-settings.md:158` | lfm には `chromium-browser` が入っていない。Chromium は Playwright のものを `~/.cache` に持つ。`google-chrome-stable` はアーキを限らずに入れる（`containers/lfm/Dockerfile:46,60`） |
> | `docs/specifications/base-image-shellcheck.md:43` | 2 回目の apt の一覧に `chromium-browser` を挙げる |
> | `docs/specifications/base-image-rendering.md:259` | amd64 では `google-chrome-stable` が追加で入る |
>
> 参考: `containers/lfm/Dockerfile:154` の `npx playwright install --with-deps chromium` は後片付けをしないため、lfm ではブラウザが `~/.cache/ms-playwright` に残る。
>
> ## なぜこの変更の範囲外なのか
>
> PLAN63（#161 / #160）の受け入れ条件は、fontconfig の解決先と 6 パッケージの追加だけを対象にしている。現象レイヤー（ブラウザが起動しない・ダウンロードが無駄になる）も修正レイヤー（ブラウザの導入方法とクリーンアップの範囲）もフォントとは別である。#161 の本文も「本 issue には含めない」と明記している。
>
> **`fonts-wqy-zenhei` の削除は、この課題でも行わない。** #161 が実測付きで結論している。削除しても OS 既定の `65-nonlatin.conf` が残るため `sans-serif` は日本語にならず（タイ語の Loma か IPAPGothic へ落ちる）、中国語のページを豆腐にするだけになる。
>
> ## 直さないと何が起きるか
>
> - base のビルドが毎回 100 MB 超のブラウザを取得して捨てる。ビルド時間と帯域が無駄になる
> - arm64（Apple Silicon）のコンテナでは、システムの Chromium を使う道具が動かないままになる。`playwright-kit` は実行時に自前で入れるため影響を受けないが、それ以外の経路（スクリーンショット・PDF 生成）は使えない
> - `--with-deps` の副作用で入るフォントが増え続ける余地が残る
>
> ## 由来
>
> issue #161 の「決めること」 / PLAN63（`issues/old/PLAN63_base-image-rendering.md`）

利用者のコメント（2026-09-22）:

> Playwright の Chromiumを入れる。

## 目的

- base イメージのビルドで取得した Playwright の Chromium をイメージに残し、取得して捨てるだけのビルドをやめる
- arm64（Apple Silicon）のコンテナでも、建て直した直後から Chromium でスクリーンショットと PDF を作れるようにする（今は arm64 に起動できる Chromium が無い）
- #161 のリリース後テストで取れなかった「base の中で PDF を作り、埋め込まれた書体を見る」確認を、base の中で行えるようにする

## 現状（2026-10-03 に調べたこと）

手元の `devbase-base:latest`（arm64、2026-10-02 に建てたもの）で確かめた。

| 調べたこと | 結果 |
| --- | --- |
| Playwright の版 | `playwright --version` は `1.63.0`。npm のグローバルの `@playwright/test@1.63.0` |
| `/opt` の中身 | `google-cloud-sdk` だけ。`ms-playwright` は無い（所有者は `root`） |
| `chromium-browser` | `2:1snap1-0ubuntu4`。「Transitional package - chromium-browser -> chromium snap」 |
| ブラウザの依存パッケージ | `fonts-liberation`・`fonts-ipafont-gothic`・`fonts-wqy-zenhei`・`libnss3` が入っている |
| ホームの置き場 | `/home/ubuntu` はボリュームではない（`lib/devbase/volume/compose.py` は `/home/ubuntu` のマウントを廃止扱いで飛ばす）。変更前の `~/.cache/ms-playwright` も、コンテナを作り直すと消える場所である |
| ブラウザを参照するテスト | `chromium-browser` / `BROWSER_PKG` を参照するテストは無い（`grep -rn 'chromium-browser\|BROWSER_PKG' tests` が 0 件）。`npx playwright install` と `fc-cache -f` の順序を見るテストが 2 件ある（本文の「影響する検査と仕様」） |

## 前提

- 前提 1: ブラウザの置き場は `/opt/ms-playwright` とし、`ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` で示す（本文の「決定」）。base の `ENV` は `docker exec` の非対話の処理にも届く
- 前提 2: base のビルドの `npx playwright install` は `--with-deps` を付けたまま残す。ブラウザをイメージに残すため、ブラウザの依存パッケージもそのまま要る。依存パッケージを明示の apt に分ける案（本文の「決めること」の 1 行目）は採らない。したがって `fonts-wqy-zenhei` も今のまま入り、`fc-cache -f` を Playwright の `RUN` より後に置く順序も変わらない
- 前提 3: base の `chromium-browser`（snap スタブ）は apt から外す。arm64 と amd64 のどちらでも Playwright の Chromium を使う（本文の「決定」）
- 前提 4: システムの Chrome（`google-chrome-stable`）は、今のまま amd64 だけに入れる。arm64 へ広げるかは未決 U1 とし、この変更では広げない。base に入れる道具を変えることはプロジェクトの「必ず人の承認が要る操作」の P1 に当たり、利用者のコメントが決めたのは Playwright の Chromium までだからである
- 前提 5: ブラウザの置き場は、コンテナの利用者（`ubuntu`）が書き込めるようにする。`playwright-kit` などが実行時に別の版の Playwright で `playwright install chromium` を打つと、`PLAYWRIGHT_BROWSERS_PATH` に従ってブラウザの置き場へ取得するためである。実行時に取得したブラウザはコンテナを作り直すと消える（変更前の `~/.cache/ms-playwright` と同じ。現状の表の「ホームの置き場」）
- 前提 6: lfm は base の `/opt` を `COPY --from` で取り込む（#275）ため、ブラウザの置き場も lfm に届く。lfm は同じ値の `ENV PLAYWRIGHT_BROWSERS_PATH` を宣言し、自前の `npx playwright install --with-deps chromium` を残す。lfm は base を `FROM` で継がないため、ブラウザの依存パッケージは lfm でも自前で入れる必要があり、ブラウザ自体は取り込んだものと版が同じなら取得し直さない
- 前提 7: 実機の確認は手元の arm64 で行う。amd64 での確認は #242（v3.7.0 の変更の amd64 での確認）に含めて行う。lfm の実機のビルドは CUDA のイメージを要するため、マージの前には行わず、リリース後テストで確かめる
- 前提 8: 確かめるためのビルドは、利用者の環境が使う `devbase-base:latest` を上書きしない別のタグで行う（P3 に当たる `devbase up` は打たない）

## 対象範囲

含む:
- base のブラウザの置き場を `/opt/ms-playwright` にし、クリーンアップで消さないこと
- base の apt から `chromium-browser` を外すこと
- lfm に同じ値の `ENV PLAYWRIGHT_BROWSERS_PATH` を宣言し、取り込んだブラウザの置き場を lfm でも使えるようにすること
- 本文の「影響する検査と仕様」の表の検査と仕様を、変更後の Dockerfile に合わせること
- `CHANGELOG.md` の `[Unreleased]` に、利用者に見える変更として書くこと

含まない:
- システムの Chrome を arm64 へ広げること（前提 4・未決 U1）
- ブラウザの依存パッケージを明示の apt に分けること（前提 2）
- `fonts-wqy-zenhei` を外すこと（本文の「なぜこの変更の範囲外なのか」）
- `playwright-kit`（`devbasex/ai-plugins`）の実行時の導入の仕組みを変えること
- 派生イメージ（`general` / `go` / `php` など）の Dockerfile の変更。base を `FROM` で継ぐため `ENV` とブラウザの置き場はそのまま届く
- 実行時に取得したブラウザを作り直しの後に残すこと（前提 5）
- amd64 での実機の確認（前提 7・#242）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | base のビルドがブラウザの置き場を用意した | base のビルド（`devbase build` / `docker build`） | 作れなければビルドが非 0 で止まる | Playwright の CLI を入れた後、E2 の前 |
| E2 | base のビルドがブラウザの依存パッケージを入れ、Playwright の Chromium をブラウザの置き場へ取得した | E1 | 取得や apt が失敗すればビルドが非 0 で止まる（今のまま） | E1 |
| E3 | base のビルドが一時ファイルを片付けた | E2 | — | E2。**ブラウザの置き場を片付けの対象に含めないと仮定している** → 受け入れ条件 2 |
| E4 | base のビルドがフォントのキャッシュを作り直した | フォントの規則の `COPY` | — | E2 の後（今のまま） → 受け入れ条件 11 |
| E5 | lfm のビルドが base の `/opt` を取り込み、ブラウザの置き場を受け取った | lfm のビルド | base に無ければ `COPY --from` が非 0 で止まる | 最新の base のビルドの後 |
| E6 | lfm のビルドがブラウザの依存パッケージを入れた。同じ版のブラウザは取得し直さなかった | E5 | apt が失敗すればビルドが非 0 で止まる | E5。**lfm の `ENV` が E6 より前にあると仮定している** → 受け入れ条件 10 |
| E7 | コンテナの利用者が Playwright の Chromium を起動し、ページを PDF やスクリーンショットにした | 利用者か道具の操作 | 起動できなければ Playwright がエラーを返す | E3（base）か E6（lfm） |
| E8 | 実行時の道具が、別の版の Playwright の Chromium をブラウザの置き場へ取得した | `playwright-kit` の初回の実行など | 書き込めなければ取得が失敗する → 前提 5・受け入れ条件 6 | E7 と独立 |
| E9 | 利用者がシステムの Chrome を起動した | Playwright を介さない道具 | arm64 には無い（前提 4・未決 U1） | — |

## 用語

| 用語 | 意味 |
| --- | --- |
| ブラウザの置き場 | Playwright がブラウザを取得して置くディレクトリ。`PLAYWRIGHT_BROWSERS_PATH` が指す。この変更で `/opt/ms-playwright` にする |
| Playwright の Chromium | `playwright install chromium` がブラウザの置き場へ取得する Chromium |
| システムの Chrome | apt で入る `google-chrome-stable` |
| snap スタブ | Ubuntu の `chromium-browser` パッケージ。コンテナの中では Chromium として起動しない |
| ブラウザの依存パッケージ | `--with-deps` が apt で入れる共有ライブラリとフォント |

## 受け入れ条件

「建てた base」は、変更後の `containers/base` から別のタグ（前提 8）で建てた arm64 のイメージを指す。

Dockerfile の形（Docker を起動しないテストで確かめる）:

- [ ] 1. `containers/base/Dockerfile` に `ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` が 1 つあり、`npx playwright install` を含む `RUN` より前にある
- [ ] 2. base の `npx playwright install` を含む `RUN` の片付けの `rm -rf` が、`/opt/ms-playwright` もその親の `/opt` も対象にしていない
- [ ] 3. `containers/base/Dockerfile` の `apt-get install` に `chromium-browser` が無い
- [ ] 4. `containers/base/Dockerfile` の `google-chrome-stable` の apt の取得元は、今のまま `arch=amd64` に限られている（前提 4）

建てた base（arm64）:

- [ ] 5. 建てた base を `--network none` で起動し、利用者 `ubuntu` の非対話の `bash -c` で Playwright の Chromium を headless で起動して、`<html lang="ja">` の日本語のページを PDF にできる（終了コード 0、PDF が 1 バイト以上）。取得は起きない
- [ ] 6. 建てた base で、利用者 `ubuntu` が `/opt/ms-playwright` の直下にファイルを作れる（`touch` が終了コード 0）
- [ ] 7. 受け入れ条件 5 の PDF を `pdffonts` で見ると、埋め込まれた書体の名前に `NotoSansCJKjp` を含むものがあり、`WenQuanYi` を含むものが無い（`fc-match 'sans-serif:lang=ja'` の解決先は `NotoSansCJKjp-Regular`）（#161 の描画の既定が Chromium の PDF でも効く）
- [ ] 8. 建てた base で `dpkg -s chromium-browser` が非 0 で終わる。`fonts-liberation`・`fonts-ipafont-gothic`・`fonts-wqy-zenhei`・`libnss3` は `dpkg -s` が 0 で終わる（ブラウザの依存パッケージは減らない）
- [ ] 9. 建てた base の `env` に `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` があり、`~/.cache/ms-playwright` は無い

lfm:

- [ ] 10. `containers/lfm/Dockerfile` に `ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` があり、`npx playwright install --with-deps chromium` を含む `RUN` より前にある。`tests/containers/test_lfm_base_settings.py` が base と lfm の `ENV` の一致を確かめて通る
- [ ] 11. 本文の「影響する検査と仕様」の 2 つのテスト（`test_fc_cache_runs_after_playwright_installs_its_fonts`・`test_fc_cache_runs_once_after_the_fonts_conf_and_playwright`）が、テストを書き換えずに通る（前提 2 で順序は変わらない）

文書と全体:

- [ ] 12. `docs/` の下に、base に `chromium-browser` が入ると書いた行と、Playwright の Chromium の置き場を `~/.cache` と書いた行が無い。本文の「影響する検査と仕様」の表の 4 つの文書（`base-image-rendering.md` の決定 5 と 259 行目・`lfm-base-settings.md` の 158 行目・`base-image-shellcheck.md` の 43 行目）が、変更後の置き場と apt の一覧を書いている
- [ ] 13. `CHANGELOG.md` の `[Unreleased]` に、base がブラウザの置き場を `/opt/ms-playwright` に持つこと、`chromium-browser` を外したことが書かれている
- [ ] 14. `.ndf/project.json` の `test.suites` の pytest のコマンドが通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 建てた base のイメージの大きさ（`docker image inspect` の `Size`）の増分を、変更前の同じ手順で建てたイメージとの差として記録する。増分と `du -sb /opt/ms-playwright` の差は 50 MB 以内である（増えるのはブラウザの分だけ） |
| 移行性 | 利用者の移行の操作は無い。base を建て直した後に作ったコンテナから効く。派生イメージと lfm は、base の後に建て直すと届く |
| システム環境 | arm64 と amd64 の両方で建てられる。arm64 の実機は手元、amd64 は #242 で確かめる（前提 7） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | コンテナの中の環境変数 `PLAYWRIGHT_BROWSERS_PATH` が増える。`/usr/bin/chromium-browser` が無くなる（snap スタブで、起動はできなかった） |
| データ | 利用者のデータの変更も移行も無い |
| 既存の振る舞い | base と派生イメージと lfm で、Playwright の Chromium がイメージに入った状態で使える。実行時に取得するブラウザの置き場が `~/.cache/ms-playwright` から `/opt/ms-playwright` に変わる。利用者に見える変更のため `CHANGELOG.md` に書く |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/containers/ -q`（全体は `.ndf/project.json` の `test.suites` のコマンド） |
| 起動（建てた base） | `docker build -t devbase-base:issue-220 containers/base` の後、`docker run --rm --network none --entrypoint bash devbase-base:issue-220 -c '...'` で受け入れ条件 5〜9 を確かめる |
| 手動確認（マージ前） | 受け入れ条件 5〜9 を手元の arm64 で確かめ、出力を PR に残す |
| 手動確認 | lfm を建て直し、受け入れ条件 5 と同じ PDF を lfm の中で作れること。amd64 で受け入れ条件 5〜9 が成り立つこと（#242）。リリース後テストで確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | イメージの定義は `containers/<名前>/Dockerfile`。Docker を起動しない形の検査は `tests/containers/`。確定仕様は `docs/specifications/`。base の外へ置く設定は lfm に同じ宣言を足す（`containers/base/Dockerfile` のコメントと `tests/containers/test_lfm_base_settings.py`） |
| コーディング規約 | Dockerfile の `RUN` は `set -eux` で始め、片付けを同じ `RUN` で行う（今の書き方）。base のシェルは ShellCheck の検査の対象（`docs/specifications/base-image-shellcheck.md`） |
| テスト戦略 | Dockerfile の形は `tests/containers/` の単体テストで固定する。ブラウザが起動すること・PDF の書体は、建てたイメージで確かめる（受け入れ条件 5〜9） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | `tests/containers/` の実行、変更した Dockerfile の ShellCheck の検査、確定仕様と `CHANGELOG.md` の更新 |
| 確認してから行う | base に入れる道具を変えること（P1。この変更は `chromium-browser` を外し、Playwright の Chromium を残す。設計の承認で人が確かめる）。システムの Chrome を arm64 へ広げること（未決 U1） |
| 行わない | `devbase-base:latest` の上書きと、実際の利用者の環境での `devbase up`（P3）。タグと GitHub Release（P2）。`fonts-wqy-zenhei` の削除。`playwright-kit` の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| U1: システムの Chrome（`google-chrome-stable`）を arm64 でも入れるか。入れると Playwright を介さずに Chrome を呼ぶ道具が arm64 でも動き、イメージが大きくなる。この変更では入れない（前提 4） | 利用者（P1 に当たるため） | 設計 PR の承認まで。入れると決めたら受け入れ条件 4 を書き換える |
