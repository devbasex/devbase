# CI の検査（トリガー・ShellCheck・CHANGELOG・固有の語）

## 概要

devbase の継続的インテグレーションは `.github/workflows/ci.yml`（ワークフロー名 `CI`）の 1 本で、
Python の構文・Ruff・ShellCheck・pytest・CHANGELOG・固有の語の 6 種の検査ジョブを持つ。この仕様は次の 4 つを定める。

- **いつ走るか（トリガー）。** Pull Request は宛先を問わず走る。push は `main` と統合ブランチ
  （`release/**`・`mission/**`）でだけ走る
- **ShellCheck が何をどう検査するか。** 基準の版の shellcheck を公式の配布物から SHA-256 を照合して入れ、
  `bin/*`・`install.sh`・`containers/base/` のシェルスクリプトのすべてを既定の水準（style まで）で検査する。
  指摘が 1 件でもあればジョブが失敗する
- **CHANGELOG の検査が何を警告するか。** `main` 宛ての Pull Request で、見張るパスを変えて `CHANGELOG.md` を
  変えていないとき、未記入の警告を出す。警告ではジョブを失敗にしない
- **固有の語の検査が何を探すか。** すべてのトリガーで、secret の語の一覧にある固有の語を追跡されたファイルから
  探し、当たりがあればジョブを失敗にする。語の一覧と当たった行の本文はログに出さない

ShellCheck のジョブが成功していても水準が絞られていれば検査は破れており、抑止の指示さえあれば shellcheck は
0 件を返す。そのため、検査ジョブの形・検査の対象の漏れ・抑止の理由は pytest が `ci.yml` と本文を読んで固定する。

手元で CI と同じ検査を打つ手順は
[開発者ガイド: CI が実行するもの](../developer/contributing.md#ci-が実行するもの) にある。

### コンテキスト

| コンテキスト | この仕様で 1 つの意味に決まるもの |
| --- | --- |
| 継続的インテグレーション（`ci`） | 検査ジョブとトリガー、ShellCheck の検査の対象・水準・基準の版、指摘と抑止の注記、見張るパスと未記入の警告、固有の語・語の一覧・当たり・例外の位置 |

隣り合うコンテキストとの関係:

- **GitHub の保護設定（外部の系）には順応者として接する。** `main` の必須チェックは、ジョブの `name` と
  matrix の値から GitHub が作るチェックの名前で照合される。照合の規則は変えられないため、`ci.yml` の側が
  名前を保つ
- **コマンドの入口（`cli`）と base イメージ（`base-image`・`tmux`）は検査の対象の本文を供給する。**
  `ci` は「基準の版の既定の水準で指摘が 0 件」を求め、本文を直すのは供給する側である。`ci` は検査の対象の
  本文を書き換えない
- **base イメージの shellcheck とは版を揃える。** base の Dockerfile の版の確認が、CI と同じ版であることを
  ビルドで止める（[base イメージの Bash の静的検査](base-image-shellcheck.md)）

## 用語

この仕様が使う語の定義は [用語集: 継続的インテグレーション（`ci`）](../glossary.md#継続的インテグレーションci)
にある。使う語: 検査ジョブ・まとめたチェック・必須チェック・トリガー・統合ブランチ・積み重ねた Pull Request・ShellCheck の検査ジョブ・
基準の版・指摘・抑止の注記・shellcheck の指示・base のシェルスクリプト・検査の対象・利用者に見える変更・
見張るパス・CHANGELOG の検査・未記入の警告・固有の語・語の一覧・固有の語の検査・当たり・例外の位置。ラッパー（`bin/devbase`）は
[コマンドの入口（`cli`）](../glossary.md#コマンドの入口cli) の語である。

## 対象範囲

- `.github/workflows/ci.yml` の `on:`（トリガー）と、検査ジョブの `name`（チェックの名前）
- `ci.yml` の `shellcheck` ジョブ（shellcheck の導入・版の確認・3 つの検査の手順）
- ShellCheck の検査の対象（`bin/*`・`install.sh`・base のシェルスクリプト）が守る規則: 指摘 0 件と、
  抑止の注記の書き方
- `ci.yml` の `changelog` ジョブと、判定を持つ `.github/scripts/changelog_check.py`
- `ci.yml` の `proper-terms` ジョブと、手元でも同じく打つ `.github/scripts/proper_term_check.py`
- 上の形を固定する pytest（`tests/ci/`・`tests/containers/test_base_shellcheck_ci.py`・
  `tests/containers/test_base_dockerfile_shellcheck.py` の版の突き合わせ）

含まないもの:

- `.github/workflows/pages.yml`（install.sh の配信。[installer-hosting](../developer/installer-hosting.md)）
- `main` の保護設定そのもの。GitHub の設定で、`ci.yml` からは変えない。必須チェックの一覧は、名前を
  `ci.yml` と揃える取り決めとして「運用」に書く
- `containers/base/` 以外の `containers/*` のスクリプト。ShellCheck の対象に入っていない
- ShellCheck 以外の静的解析の規則の中身（Ruff の `--select` など）
- base イメージへの shellcheck の導入（[base-image-shellcheck.md](base-image-shellcheck.md)）
- `CHANGELOG.md` の `[Unreleased]` の中身（書き方・分類・文の質）と、CHANGELOG の自動生成
- 語の一覧の中身と、secret `PROPER_TERMS` への登録（人が行う）。git の履歴・Pull Request と issue の本文・
  追跡されていないファイルの検査

## 仕様

### 集約

| 集約 | 書き換えてよいもの | 根 | エンティティ | 値オブジェクト |
| --- | --- | --- | --- | --- |
| CI のワークフロー | `.github/workflows/ci.yml` | ワークフロー（`name: CI`） | 検査ジョブ（`name` で識別する） | トリガー（イベントと `branches` の絞り込み） |
| ShellCheck の検査ジョブ | `ci.yml` の `shellcheck` ジョブ | ジョブ | 手順（step） | 基準の版（`SHELLCHECK_VERSION`）・配布物の SHA-256（`SHELLCHECK_SHA256`）・検査の対象のパス |
| 検査の対象 | `bin/*`・`install.sh`・base のシェルスクリプトの本文 | 1 本のファイル | 指摘 | 抑止の注記（指示と理由の組）・`shell=` の指示 |
| CHANGELOG の検査 | `ci.yml` の `changelog` ジョブと `.github/scripts/changelog_check.py` | ジョブ | — | 見張るパス（`WATCHED_PATHS`）・未記入の警告の文 |
| 固有の語の検査 | `.github/scripts/proper_term_check.py` | 検査の 1 回の実行 | 当たり（パスと行番号で識別する） | 語の一覧・例外の位置（`EXCEPTION_RULES`）・結果（当たりの並び・外したファイルの数・終了コード） |
| 固有の語の検査ジョブ | `ci.yml` の `proper-terms` ジョブ | ジョブ | 手順（step） | secret の名前（`PROPER_TERMS`）・権限（`contents: read`） |

`tests/ci/` と `tests/containers/test_base_shellcheck_ci.py` はどの集約にも属さない。集約の状態を読んで
固定するだけで、書き換えない。検査の対象と ShellCheck の検査ジョブはパスでだけつながり、2 つを揃えるのは
開発者である。揃っていないことは pytest が見つける。

### 検査ジョブ

| ジョブ ID | `name`（チェックの名前） | 中身 |
| --- | --- | --- |
| `python-syntax` | `Python syntax check`（matrix 3.10 / 3.11 / 3.12 で `Python syntax check (3.10)` などになる） | `python -m compileall -q lib bin` |
| `lint` | `Ruff lint` | `astral-sh/ruff-action@v3`、`check --select=E9,F63,F7,F82 lib` |
| `shellcheck` | `ShellCheck` | 下の「ShellCheck の検査ジョブ」 |
| `pytest` | `Pytest (Python ${{ matrix.python-version }})`（3.10 / 3.13） | `uv sync --locked` の後に `uv run --locked pytest tests/ -q`。`timeout-minutes: 15`、`fail-fast: false` |
| `pytest-all` | `Pytest`（固定の文字列。matrix を持たない） | pytest の全版をまとめたチェック。`needs: pytest` と `if: always()` で全版を待ち、`needs.pytest.result` を `env` の `RESULT` で受けて `test "${RESULT}" = success` で判定する。`timeout-minutes: 5` |
| `changelog` | `CHANGELOG check` | 下の「CHANGELOG の検査」。`main` 宛ての Pull Request でだけ走る |
| `proper-terms` | `Proper term check` | 下の「固有の語の検査」。すべてのトリガーで走る |

1 回の起動で走るチェックは 9 件になる: `Python syntax check (3.10)` / `(3.11)` / `(3.12)`・`Ruff lint`・
`ShellCheck`・`Pytest (Python 3.10)`・`Pytest (Python 3.13)`・`Pytest`・`Proper term check`。`main` 宛ての
Pull Request ではこれに `CHANGELOG check` が加わって 10 件になる。それ以外の起動では `CHANGELOG check` は skipped になる。

まとめたチェック `Pytest` の結論は次のとおり。`skipped` は必須チェックで合格と扱われるため、pytest が
成功以外のときも走って `failure` を返す。

| pytest の全版 | `needs.pytest.result` | `Pytest` の結論 |
| --- | --- | --- |
| すべて成功 | `success` | `success` |
| 1 版でも失敗・時間切れ | `failure` | `failure` |
| 取り消された（ワークフローの取り消しを含む） | `cancelled` | `failure` |
| 走らなかった | `skipped` | `failure` |

### トリガー

```yaml
on:
  push:
    branches: [main, 'release/**', 'mission/**']
  pull_request:
```

| イベント | 起動する条件 | 理由 |
| --- | --- | --- |
| `pull_request` | 宛先のブランチを問わない。値を持たないため既定の種類（`opened` / `synchronize` / `reopened`）で起動する | 宛先で絞ると、絞り込みに無い宛先の Pull Request は検査が 0 件のまま `mergeStateStatus` が `CLEAN` になり、動いていないことが画面から分からない。絞らなければ統合ブランチの規則が増えても直さずに済み、積み重ねた Pull Request も検査される |
| `push` | 行き先が `main`・`release/**`・`mission/**` のとき | 取り込み後の先端を検査したいのはこの 3 系統だけである。作業ブランチへの push は開いている Pull Request の `synchronize` で検査されるため、`push` を絞らないと同じ変更に検査が 2 回走る |

- `release/**` と `mission/**` は、`*` が `/` を越えない GitHub の glob の規則に合わせて `**` にしてある。
  `release/v9.9.9` のような 1 段でも、`mission/m6/sub` のような 2 段以上でも当たる
- 統合ブランチから `main` への Pull Request を開いたまま統合ブランチへ取り込むと、統合ブランチの `push`
  （先端を検査）と、その Pull Request の `synchronize`（`main` との merge commit を検査）の 2 回が走る。
  検査する commit が違うため、両方を走らせる
- `pull_request` の起動は、Pull Request の merge commit にある `ci.yml` で判定される

### ShellCheck の検査ジョブ

ジョブの `env` に基準の版と配布物の SHA-256 を 1 か所だけ置く。

| 変数 | 値 |
| --- | --- |
| `SHELLCHECK_VERSION` | `v0.11.0` |
| `SHELLCHECK_SHA256` | `shellcheck-v0.11.0.linux.x86_64.tar.xz` の SHA-256（64 桁の 16 進） |

手順は次の順に並ぶ。

| 手順（step の `name`） | すること |
| --- | --- |
| `actions/checkout@v4` | — |
| `Install ShellCheck ${{ env.SHELLCHECK_VERSION }}` | `https://github.com/koalaman/shellcheck/releases/download/${SHELLCHECK_VERSION}/shellcheck-${SHELLCHECK_VERSION}.linux.x86_64.tar.xz` を `$RUNNER_TEMP` へ取り、`sha256sum -c` で照合してから `tar -xJf` で展開し、`$RUNNER_TEMP/shellcheck-${SHELLCHECK_VERSION}` を `$GITHUB_PATH` へ書く。この手順の中では shellcheck を打たない |
| `Check ShellCheck version` | `shellcheck --version \| grep -Fx "version: ${SHELLCHECK_VERSION#v}"`。`GITHUB_PATH` へ書いた場所は次の手順から効くため、導入と分けてある |
| `Run ShellCheck on bin/` | `shellcheck --version \| sed -n 2p && shellcheck bin/*` |
| `Run ShellCheck on install.sh` | `shellcheck --version \| sed -n 2p && shellcheck install.sh` |
| `Run ShellCheck on containers/base/` | `shellcheck --version \| sed -n 2p && shellcheck` に base のシェルスクリプト 7 本を名前の順に並べた 1 つの呼び出し |

- **基準の版は手元の基準（`devbase-base:latest` の shellcheck）と同じ版である。** runner に入っている
  shellcheck や `apt-get` の版は選べず、版が違うと指摘の数が変わる。そのため公式の配布物を入れ、`PATH` の
  先頭へ置いて全手順で共有する。配布物は取得のたびに SHA-256 を照合し、置き換えられた物を実行しない
- **どの手順も水準を指定しない。** `--severity` / `-S` / `with.severity` を持たず、既定の style まで検査する
- **検査の手順はどれも、検査の前に使う shellcheck の版をログへ出す**（`version: 0.11.0` の行）
- **`bin/` は glob（`bin/*`）で渡す。** 足したファイルも自動で検査に入り、シェルでないファイルが入れば
  その手順が失敗して気づける。`bin/` の中身は `devbase` と `rc` の 2 本で、どちらも Bash である
- **`containers/base/` はファイル名を並べて渡す。** 何を検査したかが `ci.yml` とジョブのログだけで読める。
  `*.sh` の glob では拡張子の無い `dind` と `tmux-*` を拾えないため使わない。並べ忘れは pytest が止める
  （下の「検査の対象の漏れ」）
- `ludeeus/action-shellcheck` は使わない

### 検査の対象

| 手順 | 対象 |
| --- | --- |
| `Run ShellCheck on bin/` | `bin/devbase`・`bin/rc` |
| `Run ShellCheck on install.sh` | `install.sh` |
| `Run ShellCheck on containers/base/` | `containers/base/ai-cli-aliases.sh`・`dind`・`entrypoint.sh`・`shellrc-dir.sh`・`tmux-clean`・`tmux-first`・`tmux-session` |

base のシェルスクリプトは、`containers/base/` の直下の項目のうち次のすべてを満たすものである。

| 条件 | 判定 |
| --- | --- |
| 通常のファイル | symlink でなく、ファイルである。サブディレクトリへは降りない |
| シェルを指す | 名前が `.sh` で終わる、または先頭行が `^#!\s*(\S*/)?(env\s+)?(sh\|bash)(\s\|$)` に一致する（UTF-8 で読めない文字は置き換えて読む） |

`Dockerfile`・`fonts-local.conf`・`tmux.conf` は数えない。`ai-cli-aliases.sh` と `shellrc-dir.sh` は
`~/.bashrc` から bash が source するファイルで、`0644` で置かれ実行されないため shebang を持たない。
代わりに先頭に理由のコメントと `# shellcheck shell=bash` を置き、shellcheck に対象のシェルを教える
（`shell=` は指摘を抑えない指示である）。

### 抑止の注記

指摘は直すのが先で、直すと振る舞いが変わりうるものだけを抑止の注記にする。

- 抑える指示（`# shellcheck disable=SCxxxx` / `# shellcheck source=...`）には、抑える理由を
  **同じ行の指示の後ろ（` # 理由`）か、直前の行のコメント**に書く
- 直前の行が別の指示の行・空行・`#` だけの行なら、理由とはみなさない（base のシェルスクリプトでは
  shebang の行も理由とみなさない）。2 つの指示が要るときは、
  それぞれの直前に理由を置くか、`disable=SC1090,SC2046` のように 1 行にまとめて理由を 1 つ置く
- 抑止は行ごとに置く。`.shellcheckrc` での一括の抑止や、CI で `-e` を渡す形は使わない。前者は今後の本当に
  辿れるはずの指摘まで消し、後者は手元の結果と CI の結果を食い違わせる

現在の指示の一覧:

| ファイル | 指示 | 置いた場所 | 抑える理由 |
| --- | --- | --- | --- |
| `bin/devbase` | `disable=SC1091`（3 か所） | `${DEVBASE_ROOT}/env`・呼び出し元の `./env`・切り替え先のプロジェクトの `./env` を source する行 | env は利用者が置くファイルで、場所が実行時にしか決まらず、静的に辿れない |
| `bin/rc` | `disable=SC2206` | zsh の `fpath` への代入 | `$fpath` を要素へ展開させるため意図して引用しない |
| `bin/rc` | `source=/dev/null` | 補完の定義の読み込み | 補完の定義は `DEVBASE_ROOT` の下にあり、場所は実行時にしか決まらない |
| `containers/base/entrypoint.sh` | `disable=SC2046` | `devbase_install_git_credentials` の `sudo install -m 600 -o $(id -u) -g $(id -g) ...` | `id` が失敗して空を出したとき、引用があると uutils の `install` が空の持ち主を「変えない」と読み root の持ち主で書くため、引用しない |
| `containers/base/entrypoint.sh` | `disable=SC2317` | `DEVBASE_ENTRYPOINT_LIB_ONLY` のときの `return 0 2>/dev/null \|\| exit 0` | source したときは `return` で抜け、実行したときは `exit` へ進む。shellcheck は source を想定せず `exit` を届かないと読む |
| `containers/base/entrypoint.sh` | `disable=SC2009` | dockerd が起動しなかったときの `ps aux \| grep dockerd` | 診断として利用者と起動の引数を含む全列を出す。`pgrep` は PID（`-a` でも別の列）だけを出す |
| `containers/base/shellrc-dir.sh` | `source=/dev/null` | 置き場所のファイルを `.` で読む行 | 読むのは利用者が置くファイルで、検査の時点では存在しない |
| `containers/base/ai-cli-aliases.sh`・`shellrc-dir.sh` | `shell=bash` | 先頭 | 抑止ではない。bash が source するファイルで shebang を置かないため |

指摘を直した箇所の書き方の規則:

- `local x=$(f)` / `export X=$(f)`（SC2155）は、宣言と代入を分ける。`bin/devbase` は `set -e` で動くため、
  分けると `f` の非 0 で止まるようになる。非 0 が失敗でなく判定の結果である置換
  （`check_base_image_dependency` の「devbase-* を使わない」の 1）は、分けた代入を `|| true` で受け、
  その理由を直前の行に書く
- パイプの最後のコマンドが 0 を返す置換（`DOCKER_GID` の `grep ... | cut`）は、分けても止まる経路が
  増えないため `|| true` を付けない。`bin/devbase` は `pipefail` を使わない
- `A && B || C`（SC2015）は `if` / `else` へ書き換える
- 読まない変数（SC2034）は `_` にする（`entrypoint.sh` の `for _ in {1..30}`）

### CHANGELOG の検査

`main` へのマージがそのまま配布になるため、利用者に見える変更は `main` に入る時点で `CHANGELOG.md` の
`[Unreleased]` に載っている状態にする。書き漏れをリリースの Pull Request ではなく変更の Pull Request の
レビューで気づけるよう、`changelog` ジョブが未記入の警告を出す。

```yaml
changelog:
  name: CHANGELOG check
  if: github.event_name == 'pull_request' && github.base_ref == 'main'
  permissions:
    contents: read
```

| 項目 | 決まり |
| --- | --- |
| 対象の Pull Request | 宛先が `main` の Pull Request だけ。統合ブランチ宛て・積み重ねた Pull Request・`push` のイベントではジョブが skipped になり、警告を出さない。統合ブランチを通す変更は、統合ブランチから `main` への Pull Request の差分で `CHANGELOG.md` を変えていればよい |
| 見張るパス | `lib/`・`bin/`・`containers/`・`etc/`・`install.sh`。`.github/scripts/changelog_check.py` の `WATCHED_PATHS` にだけ置き、pytest も同じ定義を読む。`docs/`・`tests/`・`issues/`・`.github/`・`.ndf/` は見張らない |
| 差分 | `actions/checkout@v4`（`fetch-depth: 0`）の後、宛先ブランチを `origin/<宛先>` へ取り直し、`git diff --name-only origin/<宛先>...<head の commit>`（merge base から head まで）のパスを見る。head の commit は `github.event.pull_request.head.sha` |
| 判定 | 差分に見張るパスのファイルが 1 つ以上あり、`CHANGELOG.md` が無いときに未記入の警告を 1 件出す。`[Unreleased]` の節の中に足したかまでは見ない |
| 未記入の警告 | `::warning title=CHANGELOG 未記入::` の注記と `$GITHUB_STEP_SUMMARY` に、`CHANGELOG.md` の `[Unreleased]` の更新を求める文と、変えた見張るパスのファイルを出す。Pull Request へのコメントは書かない（fork からの Pull Request では書き込みの権限が無い） |
| ジョブの結論 | 未記入の警告を出しても success。見張るパスの変更にも CHANGELOG が要らないもの（テストのための変更・利用者に見えないリファクタリング）があるため、要否はレビュアーが判断する |
| 差分を得られないとき | 黙って通さず、`::error::` に理由を出してジョブを失敗にする。必須チェックではないためマージは止まらない |
| 権限と実行 | `pull_request` のイベントで動かし、`pull_request_target` を使わない。ジョブの権限は `contents: read`。Docker もビルドも使わず、runner の `python3` で標準ライブラリだけの処理を打つ |

`CHANGELOG check` は `main` の保護の必須チェックに加えない。警告であって止める検査ではないためである。

### 固有の語の検査

作成者の所属組織・顧客・社内プロダクト・個人に固有の名前（固有の語）が文書・コード・テストへ再び持ち込まれたら、
マージの前に気づけるようにする。語の一覧そのものは公開のリポジトリにも CI のログにも出さない。

```yaml
proper-terms:
  name: Proper term check
  timeout-minutes: 5
  permissions:
    contents: read
  steps:
    - uses: actions/checkout@v4
      with:
        persist-credentials: false
    - name: Check proper terms
      env:
        PROPER_TERMS: ${{ secrets.PROPER_TERMS }}
      run: python3 .github/scripts/proper_term_check.py
```

| 項目 | 決まり |
| --- | --- |
| 起動 | `if:` を持たず、`ci.yml` のすべてのトリガーで走る。積み重ねた Pull Request と統合ブランチでも語を見つけ、`main` への push でも走る |
| 語の一覧の出所 | `--terms PATH` のファイル・環境変数 `PROPER_TERMS`・既定の置き場 `${XDG_CONFIG_HOME:-$HOME/.config}/devbase/proper-terms.txt` の順で、最初に当たった 1 つだけを使う。CI は secret `PROPER_TERMS` を検査の手順の `env` にだけ渡し、ファイルへ書かない。secret の登録と更新は人が行う |
| 一覧の書式 | 1 行 1 語の UTF-8（先頭の BOM は許す）。各行の前後の空白を除き、空の行と `#` で始まる行を読み飛ばす |
| 一覧が無い・空 | 未登録の secret とフォークからの Pull Request では `PROPER_TERMS` が空の文字列になる。出所が無いか語が 0 語なら、`語の一覧が無いため飛ばした` と見た出所を出して 0 で終わる。CI では `::notice::` とサマリーにも同じ文を出す |
| 対象 | `git rev-parse --show-toplevel` のリポジトリで `git ls-files -z` が返す追跡されたファイル。中身は作業ツリーから読む。UTF-8 で読めないファイル・symlink・消えたファイルは外し、外した数だけを出す |
| 判定 | 行ごとに `casefold` し、語を正規表現でなく文字列として部分一致で探す。行は `\n` で分け、行末の `\r` を除く |
| 例外の位置 | `EXCEPTION_RULES` にだけ置く。`LICENSE` の `^Copyright \(c\) \d{4} ` の行と、`.ndf/mvv.json` の `^\s*"approved_by":\s*"` の行。パスと行の形の両方が合う行だけに効き、形は語を含まない |
| 当たりの出力 | 当たり 1 件を `<パス>:<行番号>` の 1 行でパス・行番号の順に出し、最後に `固有の語の検査: 当たり <N> 件（<M> ファイル）。語 <K> 語・対象 <F> ファイル・外したファイル <X> 件` を出す。CI では当たりごとに `::error file=<パス>,line=<行番号>,title=固有の語の検査::固有の語を含む行がある` とサマリーを足す。語と当たった行の本文はどこにも出さない |
| 終了コード | 0: 当たり 0 件か飛ばした。1: 当たりがある。2: 一覧を読めない（`--terms` のファイルが無い・権限・UTF-8 でない）、`git` が使えない・リポジトリの外、引数の誤り |
| 権限と実行 | ジョブの権限は `contents: read` だけで、checkout は `persist-credentials: false`。artifact と Pull Request へのコメントの手順を持たない。runner の `python3` で標準ライブラリだけの処理を打つ |

失敗の理由は定型文だけを出し、例外の文言（`UnicodeDecodeError` の本文など）をそのまま出さない。読めなかった
バイトが一覧の断片になりうるためである。`Proper term check` を `main` の必須チェックに加えるかは、`main` で
当たり 0 件を確かめた後に利用者が決める。

### 常に成り立つ条件

| 条件 | 破れたとき何が止めるか |
| --- | --- |
| `pull_request` のトリガーは `branches` / `branches-ignore` を持たない | `tests/ci/test_ci_workflow.py` の `test_pull_request_does_not_filter_branches` |
| `push` のトリガーの `branches` は `main`・`release/**`・`mission/**` の 3 つとちょうど一致し、`branches-ignore` を持たない | `tests/ci/test_ci_workflow.py` の `test_push_branches_are_main_and_integration_branches` |
| 検査ジョブのチェックの名前（`name` を matrix の値で展開したもの）は上の 10 件（`CHANGELOG check` と `Proper term check` を含む）とちょうど一致する | `tests/ci/test_ci_workflow.py` の `test_check_names_are_fixed`。変えたまま `main` へ入ると、必須チェックが「待ち」のまま残りマージできない |
| `pytest-all` の `name` は固定の文字列 `Pytest` で、`strategy` を持たない | `tests/ci/test_ci_workflow.py` の `test_aggregate_name_does_not_depend_on_matrix` |
| `pytest-all` は `needs` に `pytest` を持ち、`if` が `always()` である | `tests/ci/test_ci_workflow.py` の `test_aggregate_waits_for_pytest` と `test_aggregate_always_runs` |
| `pytest-all` の判定は `needs.pytest.result` を受け、`success` のときだけ 0 で終わる | `tests/ci/test_ci_workflow.py` の `test_aggregate_result_comes_from_pytest` と `test_aggregate_passes_only_on_success` |
| `shellcheck` ジョブの `SHELLCHECK_VERSION` は `v0.11.0`、`SHELLCHECK_SHA256` は 64 桁の 16 進 | `tests/ci/test_shellcheck_job.py` |
| 導入の手順は 1 つで、`sha256sum -c` を展開（`tar`）より前に打ち、`GITHUB_PATH` へ書く | 照合が合わなければ導入の手順でジョブが失敗する。形が崩れれば `tests/ci/test_shellcheck_job.py` |
| 導入の後の手順に `grep -Fx "version: ${SHELLCHECK_VERSION#v}"` の版の確認がある | 版が違えば `Check ShellCheck version` でジョブが失敗する。確認が消えれば `tests/ci/test_shellcheck_job.py` |
| shellcheck を打つ手順はすべて導入より後にあり、`shellcheck --version` から始まる（版の確認・`bin/`・`install.sh`・`containers/base/` の 4 手順以上） | `tests/ci/test_shellcheck_job.py` |
| どの手順も水準を指定せず、`ludeeus/action-shellcheck` を使わない | `tests/ci/test_shellcheck_job.py`。`containers/base/` を検査する呼び出しは `tests/containers/test_base_shellcheck_ci.py` も見る |
| 基準の版の既定の水準で、検査の対象の指摘が 0 件 | ShellCheck の検査ジョブが失敗する |
| base のシェルスクリプトはすべて、`containers/base/` を検査する `shellcheck` の呼び出しの引数にある | `tests/containers/test_base_shellcheck_ci.py` が、載っていないファイル名を出して失敗する |
| 検査の対象の shellcheck の指示は、同じ行か直前の行に理由を持つ | `tests/ci/test_shellcheck_job.py`（`bin/*`・`install.sh`）と `tests/containers/test_base_shellcheck_ci.py`（base のシェルスクリプト）が、ファイル名と行番号を出して失敗する |
| `changelog` ジョブは `main` 宛ての `pull_request` でだけ走り、権限は `contents: read`、`changelog_check.py` を打つ | `tests/ci/test_changelog_check.py` |
| 見張るパスは `WATCHED_PATHS` の 5 つだけで、`ci.yml` には書かない | `tests/ci/test_changelog_check.py` |
| `proper-terms` ジョブは `if:` を持たず、権限は `contents: read` だけで、secret `PROPER_TERMS` は検査の手順の `env` にだけ現れ、`run` はスクリプトを打つだけで、artifact とコメントの手順を持たない | `tests/ci/test_proper_term_job.py` |
| 固有の語の検査の出力は、パス・行番号・件数・定型文だけで、一覧の語と当たった行の本文を含まない | `tests/ci/test_proper_term_check.py` |
| 固有の語の検査は一覧が無い・空なら 0、当たりがあれば 1、一覧や git の失敗は 2 で終わる | `tests/ci/test_proper_term_check.py` |
| base の Dockerfile の版の確認は `SHELLCHECK_VERSION` と同じ版を求める | base のビルドが版の確認で止まる。2 つの食い違いは `tests/containers/test_base_dockerfile_shellcheck.py` の `test_version_check_matches_ci_pin` |

### ドメインイベント

| # | イベント | 発生元 | 受け手 |
| --- | --- | --- | --- |
| E1 | Pull Request を開いた・push で更新した | 開発者・エージェント（GitHub が `pull_request` を発行する） | `pull_request` のトリガー → 9 件のチェック（`main` 宛てでは `CHANGELOG check` を加えた 10 件） |
| E2 | ブランチへ push が起きた（統合ブランチ・`main` への取り込みを含む） | 開発者・エージェントの push とマージ | `push` のトリガー。行き先が 3 系統のときだけ 9 件のチェック |
| E3 | 検査ジョブが起動し、結果が出た | トリガー | Pull Request のチェック一覧と、`main` の保護設定の照合 |
| E3a | pytest の全版が終わり、まとめたチェック `Pytest` の結論が出た | pytest のジョブ（成功・失敗・取り消しのどれでも） | Pull Request のチェック一覧と、`main` の保護設定の照合 |
| E4 | `containers/base/` にシェルスクリプトを足した | 開発者 | Pytest ジョブの漏れの検査。`ci.yml` の一覧へ足すまで落ちる |
| E5 | base イメージの shellcheck の版が上がった | Ubuntu のアーカイブの更新と base の再ビルド | 保守者。base のビルドが版の確認で止まるため、`ci.yml` の 2 つの値と Dockerfile の版の確認を一緒に上げる |
| E6 | `main` 宛ての Pull Request の差分に見張るパスがあり `CHANGELOG.md` が無い | CHANGELOG の検査 | 作成者とレビュアー。作成者が `[Unreleased]` を書き足して push すると新しい実行では警告が出ない。レビュアーが利用者に見えない変更と判断すれば、そのままマージする |
| E7 | 人が語の一覧を secret `PROPER_TERMS` に登録した・更新した | リポジトリの管理者 | 固有の語の検査ジョブ。以後の起動から一覧で検査する |
| E8 | 固有の語の検査が当たりを見つけた | 固有の語の検査 | 作成者とレビュアー。`Proper term check` が失敗し、当たったパスと行番号で直す場所が分かる |

## データ・設定

| 置き場所 | 値 | 読むもの |
| --- | --- | --- |
| `ci.yml` の `jobs.shellcheck.env.SHELLCHECK_VERSION` | `v0.11.0`（先頭の `v` を含む。配布物の名前と URL の形） | 導入・版の確認の手順、`test_shellcheck_job.py`、`test_base_dockerfile_shellcheck.py` |
| `ci.yml` の `jobs.shellcheck.env.SHELLCHECK_SHA256` | 配布物 `shellcheck-v0.11.0.linux.x86_64.tar.xz` の SHA-256 | 導入の手順 |
| `containers/base/Dockerfile` の版の確認の `RUN` | `shellcheck --version \| grep -Fx "version: 0.11.0"`（`v` を含まない） | base のビルド |

リポジトリに `.shellcheckrc` は置かない。検査の設定はすべて各ファイルの指示と `ci.yml` の呼び出しにある。

## エラー処理

| 起きたこと | 止まる場所 | 出るもの |
| --- | --- | --- |
| 配布物を取れない | `Install ShellCheck ...`（`curl -f` の非 0） | curl の失敗 |
| 配布物の SHA-256 が合わない | `Install ShellCheck ...`（`sha256sum -c`）。展開しない | `FAILED` の行 |
| `PATH` の先の shellcheck が基準の版でない | `Check ShellCheck version`（`grep -Fx` の非 0） | 何も一致しない |
| 検査の対象に指摘がある | その対象の `Run ShellCheck on ...` | shellcheck の指摘。ShellCheck のチェックが赤になる |
| base のシェルスクリプトが `ci.yml` に無い | Pytest の `test_every_base_script_is_checked_by_ci` | `CI の ShellCheck の対象に無い containers/base のシェルスクリプト: <名前>, ... (.github/workflows/ci.yml の Run ShellCheck on containers/base/ へ足す)` |
| 抑止の指示に理由が無い | Pytest の `test_directive_has_reason` / `test_directives_have_reason` | `<パス>:<行>: 抑える理由が無い: <行>` / `抑える理由が無い指示: containers/base/<名前>:<行>` |
| CHANGELOG の検査で宛先ブランチや差分を得られない | `Check CHANGELOG.md is updated`（非 0） | `::error::CHANGELOG の検査: 宛先ブランチ <名前> を取れない: <git の出力>` / `... の差分を取れない: <git の出力>` |
| 固有の語の検査で当たりがある | `Check proper terms`（終了コード 1） | `<パス>:<行番号>` の行と要約、`::error file=...` の注記 |
| 固有の語の検査で一覧を読めない・`git` が使えない | `Check proper terms`（終了コード 2） | `固有の語の検査: <理由の定型文>`（CI では `::error::`） |
| `containers/base/` を検査する呼び出しが水準を指定した | Pytest の `test_base_commands_do_not_set_severity` | `水準を絞っている: <呼び出し>` |

検査ジョブどうしは互いを待たない。待つのはまとめたチェックのジョブ（`pytest-all`）だけで、pytest の全版を
待つ。漏れは Pytest のジョブが、指摘は ShellCheck のジョブが見つける。pytest が 1 版でも成功以外なら、
まとめたチェック `Pytest` が `failure` になる。

## 運用

- **基準の版を上げるとき**は、`ci.yml` の `SHELLCHECK_VERSION` と `SHELLCHECK_SHA256`、base の Dockerfile の
  版の確認の `RUN` の 3 つを一緒に上げる。SHA-256 は配布元の Release の資産から計算する。上げた版で
  検査の対象が 0 件になることを手元の base で確かめる（手順は
  [contributing.md の CI が実行するもの](../developer/contributing.md#ci-が実行するもの)）
- **`containers/base/` の直下にシェルスクリプトを足したら**、`Run ShellCheck on containers/base/` の一覧へ
  名前の順で足す
- **`bin/` にファイルを足すと自動で検査に入る。** シェルでないファイルを `bin/` に置かない
- **`main` の保護の必須チェックは 6 件**: `Python syntax check (3.10)` / `(3.11)` / `(3.12)`・`Ruff lint`・
  `ShellCheck`・`Pytest`。提供元は GitHub Actions に固定し、`strict` は `true`。pytest は版ごとの
  `Pytest (Python 3.10)` などではなく、まとめたチェック `Pytest` で照合する
- **pytest の matrix の版を足し引きしても、保護設定は直さなくてよい。** `Pytest` の名前は版に依存しない。
  `tests/ci/test_ci_workflow.py` の期待値（チェックの名前の 10 件）は一緒に直す
- **ほかの検査ジョブの `name` と matrix の値は変えない。** 必須チェックが名前で照合するため、変えると
  必須チェックが「待ち」のまま残る。変えるときは保護設定を同時に直す
- **語の一覧を変えるとき**は、人が secret `PROPER_TERMS` を更新し、手元の既定の置き場の一覧も揃える。
  一般の英単語と重なる語は入れず、その語を含むより長い固有の名前で見張る
- `pull_request` のトリガーは merge commit の `ci.yml` で判定されるため、トリガーを変えた Pull Request は
  それ自身の検査で新しい形が効く

## テスト観点

`tests/ci/test_shellcheck_job.py`（`ci.yml` を `yaml.safe_load` で読み、`bin/*` と `install.sh` の本文を読む）:

- `bin/*` と `install.sh` の `# shellcheck` で始まる行が 5 行以上あり、検査が素通りしていないこと
- それぞれの指示の行が、同じ行の後ろの `# 理由` か、直前の行の指示でないコメントを持つこと
- `shellcheck` ジョブのどの手順も `with.severity` を持たず、`run` に `--severity` / `-S` が無いこと
- どの手順も `action-shellcheck` を使わないこと
- `SHELLCHECK_VERSION` が `v0.11.0` で、`SHELLCHECK_SHA256` が 64 桁の 16 進であること
- `GITHUB_PATH` へ書く導入の手順がちょうど 1 つあり、`${SHELLCHECK_VERSION}` を使い、`sha256sum -c` を
  `tar` より前に打ち、その中で shellcheck を打たないこと
- 導入より後の手順に `grep -Fx "version: ${SHELLCHECK_VERSION#v}"` があること
- shellcheck を打つ手順が 4 つ以上あって `shellcheck bin/*` と `shellcheck install.sh` を含み、すべて導入より
  後にあり、`shellcheck --version` から始まること

`tests/ci/test_changelog_check.py`（`changelog_check.py` を読み込み、GitHub へ繋がない）:

- `WATCHED_PATHS` がちょうど 5 つで、それぞれの下のファイルを変えて `CHANGELOG.md` を変えていないと、そのファイルを示すこと
- 見張るパスと他のパスを混ぜたとき見張るパスのファイルだけを示し、警告の文が `CHANGELOG.md` と `[Unreleased]` を含むこと
- `CHANGELOG.md` も変えていれば、また見張るパスが無ければ（`docs/`・`tests/`・`.github/`）警告しないこと。
  `libs/`・`install.sh.bak` のような似た名前を見張らないこと
- `main()` が、警告のときに `::warning` を 1 件だけ出して 0 を返し要約へ書くこと、宛先ブランチや差分を
  得られないときと `BASE_REF` が無いときに `::error::` を出して 1 を返すこと（git は差し替える）
- `ci.yml` のトリガーが変わらず `pull_request_target` が無いこと、既存の 4 つのジョブの `name` が残ること
- `changelog` ジョブの `if` が `pull_request` と `main` 宛てで絞り、権限が `contents: read` だけで、
  `fetch-depth: 0` の checkout の後に `BASE_REF` と `HEAD_SHA` を渡してスクリプトを打ち、docker を使わないこと

`tests/ci/test_proper_term_check.py`（一時ディレクトリの git リポジトリと架空の語で、スクリプトを別のプロセスとして打つ。
`HOME`・`XDG_CONFIG_HOME` はテストごとの一時ディレクトリへ向け、`PROPER_TERMS`・`GITHUB_ACTIONS`・`GITHUB_STEP_SUMMARY` は
試す行でだけ渡す）:

- 当たりが `<パス>:<行番号>` でパス・行番号の順に出て 1 で終わり、当たりが無ければ 0 件の要約で 0 で終わること
- 大文字小文字だけが違う語が当たり、`.` を含む語が正規表現として扱われないこと
- 出所が無い・`PROPER_TERMS` が空・一覧が空行と `#` の行だけのときに飛ばした旨を出して 0 で終わること
- `--terms`・`PROPER_TERMS`・既定の置き場の順に 1 つだけを使うこと
- 追跡されていないファイルと `.gitignore` 済みのファイルを読まないこと
- 例外の位置の行だけが当たりにならず、同じファイルの別の行と別のパスの同じ形の行は当たること
- バイナリ・symlink・消えたファイルを外して数えること
- `--terms` のファイルが無い・一覧が UTF-8 でない・既定の置き場が読めない・リポジトリの外のときに 2 で終わること
- 当たりあり・なし・一覧が読めない実行の標準出力・標準エラー・サマリーに、一覧の語と当たった行の本文が出ないこと

`tests/ci/test_proper_term_job.py`（`ci.yml` を読む）:

- `proper-terms` の `name` が `Proper term check` で、`if` を持たず、権限が `contents: read` だけで、
  checkout が `persist-credentials: false` であること
- 検査の手順が `python3 .github/scripts/proper_term_check.py` を打つだけで、`secrets.PROPER_TERMS` が
  ワークフローの中でその手順の `env` にだけ現れること
- artifact とコメントの手順を持たないこと

`tests/containers/test_base_shellcheck_ci.py`（Docker を要さない）:

- `containers/base/` で数えた base のシェルスクリプトが 7 本を含み、`Dockerfile`・`fonts-local.conf`・
  `tmux.conf` を含まないこと
- 数えたすべてが、`containers/base/` を検査する `shellcheck` の呼び出しの引数にあること
- `containers/base/` を検査する呼び出しがあり、水準の指定を持たないこと
- 数えた各ファイルの指示（`# shellcheck <名前>=`）が理由を持つこと
- 判定の規則: `#!/bin/sh`・`#!/usr/bin/env bash`・shebang の無い `x.sh` を数え、`#!/usr/bin/python3`・
  shebang の無い拡張子の無いファイル・`#!/bin/bashful`・サブディレクトリの中の `y.sh`・`.sh` への symlink を
  数えないこと
- 漏れの検査が、足りないファイル名を文面に含めて失敗すること
- 呼び出しの集め方: 行末の `\` で続く行と `./` の付いたパスを集め、`install.sh`・`bin/*` だけの呼び出しや
  `echo containers/base/...` を採らないこと。`--severity=warning`・`-S warning`・`-Swarning`・
  `--severity warning` を水準の指定として見つけ、`install.sh` の行へ足した `containers/base/` のパスも採ること
- 理由の規則: 理由の無い指示・直前が空行・直前が shebang・指示が 2 行続いた 2 行目・直前が `#` だけの指示を
  失敗と数え、直前の行の理由・同じ行の `# 理由`・理由と指示の組が 2 つ続く形を通すこと

`tests/containers/test_base_dockerfile_shellcheck.py` の `test_version_check_matches_ci_pin`:

- base の Dockerfile の版の確認が `shellcheck --version | grep -Fx "version: <SHELLCHECK_VERSION から v を除いた値>"` で
  あること

指摘を直した箇所の振る舞い（`tests/cli/test_wrapper_shellcheck_fixes.py`、`bin/devbase` を実プロセスで起動する）:

- Dockerfile が `devbase-*` を使わないとき、`devbase build` がベースイメージの判定の非 0 で止まらず、
  プロジェクトのビルド（`docker compose build dev`）へ進むこと
- `DOCKER_GID` が Darwin で `0`、それ以外で `/etc/group` の docker の gid、docker の行が無ければ空で、
  どの場合もラッパーが終了コード 0 で進むこと。`COMPOSE_PROJECT_NAME` がカレントディレクトリの名前であること

base のシェルスクリプトの振る舞いは、`entrypoint.sh` を `DEVBASE_ENTRYPOINT_LIB_ONLY` で source する
`tests/containers/test_entrypoint_*.py` と、`test_ai_cli_aliases.py`・`test_shellrc_dir.py` が守る。

`tests/ci/test_ci_workflow.py`（`ci.yml` を `yaml.safe_load` で読む。`on` は `True` のキーになる）:

- `on.pull_request` が値を持たないか、`branches` と `branches-ignore` のどちらも持たないこと
- `on.push.branches` が `main`・`release/**`・`mission/**` とちょうど一致し、`branches-ignore` を持たないこと
- 各ジョブの `name` を matrix の値で展開した名前（式を含まない `name` には値を括弧で付け足す）が、上の 10 件と
  ちょうど一致すること
- `pytest-all` の `name` が `Pytest` で、`${{` を含まず、`strategy` を持たないこと
- `pytest-all` の `needs` が `pytest` を含み、`if` が `always()` であること
- `pytest-all` の判定の手順の `env.RESULT` が `${{ needs.pytest.result }}` で、その `run` を bash で
  `RESULT` = `success`・`failure`・`cancelled`・`skipped` の 4 通りに走らせると、`success` だけが 0 で終わること

pytest で確かめないもの:

- 実際の起動と保護設定の照合。GitHub の上で見る。宛先が `main`・`release/**`・`mission/**`・それ以外の
  どれでも 9 件のチェックが出ること、`main`・`release/**`・`mission/**` 以外への push では起動しないこと、
  pytest が落ちたとき `Pytest` が `skipped` でなく `failure` になること、`main` 宛てで必須チェック 6 件が
  pass になり「待ち」が残らないこと
- 指摘 0 件そのもの。ShellCheck の検査ジョブが確かめる。手元では base の shellcheck で同じ検査を打てる
- ShellCheck の検査ジョブのログに手順ごとに `version: 0.11.0` が出ること。ジョブのログで見る
- CHANGELOG の検査が実際の Pull Request で走り success で終わること。その Pull Request の `gh pr checks` で見る
- 固有の語の検査が secret の一覧で走ること。固有の語を 1 つ足した検証用の Draft の Pull Request でジョブが失敗し、
  ログに語と本文が無いことを 1 度見る。実際の一覧での 0 件は、手元の既定の置き場の一覧と `main` の Run で見る

## 関連リンク

- [開発者ガイド: CI が実行するもの](../developer/contributing.md#ci-が実行するもの)（手元での検査の手順）
- [用語集: 継続的インテグレーション（`ci`）](../glossary.md#継続的インテグレーションci)
- [base イメージの Bash の静的検査（shellcheck）](base-image-shellcheck.md)（base の shellcheck と版の確認）
- [tmux のセッションを名指しで扱うコマンド](tmux-named-session.md)（検査の対象の `tmux-*`）
- [位置引数の解決](cli-argument-resolution.md)（検査の対象のラッパー `bin/devbase`）
- 課題: [#216](https://github.com/devbasex/devbase/issues/216)（トリガー）・
  [#247](https://github.com/devbasex/devbase/issues/247)（基準の版と既定の水準）・
  [#259](https://github.com/devbasex/devbase/issues/259)（`containers/base/` の検査の対象）・
  [#277](https://github.com/devbasex/devbase/issues/277)（まとめたチェック `Pytest` と必須チェック）・
  [#291](https://github.com/devbasex/devbase/issues/291)（トリガーと名前を固定する回帰テスト）・
  [#335](https://github.com/devbasex/devbase/issues/335)（CHANGELOG の検査）・
  [#353](https://github.com/devbasex/devbase/issues/353)（固有の語の検査）
- [開発者ガイド: CHANGELOG の更新](../developer/contributing.md#changelog-の更新)
