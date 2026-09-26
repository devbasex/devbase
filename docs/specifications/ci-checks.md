# CI の検査（トリガーと ShellCheck）

## 概要

devbase の継続的インテグレーションは `.github/workflows/ci.yml`（ワークフロー名 `CI`）の 1 本で、
Python の構文・Ruff・ShellCheck・pytest の 4 種の検査ジョブを持つ。この仕様は次の 2 つを定める。

- **いつ走るか（トリガー）。** Pull Request は宛先を問わず走る。push は `main` と統合ブランチ
  （`release/**`・`mission/**`）でだけ走る
- **ShellCheck が何をどう検査するか。** 基準の版の shellcheck を公式の配布物から SHA-256 を照合して入れ、
  `bin/*`・`install.sh`・`containers/base/` のシェルスクリプトのすべてを既定の水準（style まで）で検査する。
  指摘が 1 件でもあればジョブが失敗する

ShellCheck のジョブが成功していても水準が絞られていれば検査は破れており、抑止の指示さえあれば shellcheck は
0 件を返す。そのため、検査ジョブの形・検査の対象の漏れ・抑止の理由は pytest が `ci.yml` と本文を読んで固定する。

手元で CI と同じ検査を打つ手順は
[開発者ガイド: CI が実行するもの](../developer/contributing.md#ci-が実行するもの) にある。

### コンテキスト

| コンテキスト | この仕様で 1 つの意味に決まるもの |
| --- | --- |
| 継続的インテグレーション（`ci`） | 検査ジョブとトリガー、ShellCheck の検査の対象・水準・基準の版、指摘と抑止の注記 |

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
にある。使う語: 検査ジョブ・トリガー・統合ブランチ・積み重ねた Pull Request・ShellCheck の検査ジョブ・
基準の版・指摘・抑止の注記・shellcheck の指示・base のシェルスクリプト・検査の対象。ラッパー（`bin/devbase`）は
[コマンドの入口（`cli`）](../glossary.md#コマンドの入口cli) の語である。

## 対象範囲

- `.github/workflows/ci.yml` の `on:`（トリガー）と、検査ジョブの `name`（チェックの名前）
- `ci.yml` の `shellcheck` ジョブ（shellcheck の導入・版の確認・3 つの検査の手順）
- ShellCheck の検査の対象（`bin/*`・`install.sh`・base のシェルスクリプト）が守る規則: 指摘 0 件と、
  抑止の注記の書き方
- 上の形を固定する pytest（`tests/ci/`・`tests/containers/test_base_shellcheck_ci.py`・
  `tests/containers/test_base_dockerfile_shellcheck.py` の版の突き合わせ）

含まないもの:

- `.github/workflows/pages.yml`（install.sh の配信。[installer-hosting](../developer/installer-hosting.md)）
- `main` の保護設定（必須チェックの一覧）そのもの。GitHub の設定で、`ci.yml` からは変えない
- `containers/base/` 以外の `containers/*` のスクリプト。ShellCheck の対象に入っていない
- ShellCheck 以外の静的解析の規則の中身（Ruff の `--select` など）
- base イメージへの shellcheck の導入（[base-image-shellcheck.md](base-image-shellcheck.md)）

## 仕様

### 集約

| 集約 | 書き換えてよいもの | 根 | エンティティ | 値オブジェクト |
| --- | --- | --- | --- | --- |
| CI のワークフロー | `.github/workflows/ci.yml` | ワークフロー（`name: CI`） | 検査ジョブ（`name` で識別する） | トリガー（イベントと `branches` の絞り込み） |
| ShellCheck の検査ジョブ | `ci.yml` の `shellcheck` ジョブ | ジョブ | 手順（step） | 基準の版（`SHELLCHECK_VERSION`）・配布物の SHA-256（`SHELLCHECK_SHA256`）・検査の対象のパス |
| 検査の対象 | `bin/*`・`install.sh`・base のシェルスクリプトの本文 | 1 本のファイル | 指摘 | 抑止の注記（指示と理由の組）・`shell=` の指示 |

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

1 回の起動で走るチェックは 7 件になる: `Python syntax check (3.10)` / `(3.11)` / `(3.12)`・`Ruff lint`・
`ShellCheck`・`Pytest (Python 3.10)`・`Pytest (Python 3.13)`。

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

### 常に成り立つ条件

| 条件 | 破れたとき何が止めるか |
| --- | --- |
| `pull_request` のトリガーは `branches` / `branches-ignore` を持たない | 止める pytest は無い。`main` 宛ての Pull Request では検査が走り続けるため、CI の結果からも分からない。`ci.yml` の変更のレビューで見る |
| `push` のトリガーの `branches` は `main`・`release/**`・`mission/**` の 3 つとちょうど一致する | 同上 |
| 検査ジョブのチェックの名前は上の 7 件のまま変わらない | 止める pytest は無い。変えると `main` 宛ての Pull Request で必須チェックが「待ち」のまま残り、マージできない |
| `shellcheck` ジョブの `SHELLCHECK_VERSION` は `v0.11.0`、`SHELLCHECK_SHA256` は 64 桁の 16 進 | `tests/ci/test_shellcheck_job.py` |
| 導入の手順は 1 つで、`sha256sum -c` を展開（`tar`）より前に打ち、`GITHUB_PATH` へ書く | 照合が合わなければ導入の手順でジョブが失敗する。形が崩れれば `tests/ci/test_shellcheck_job.py` |
| 導入の後の手順に `grep -Fx "version: ${SHELLCHECK_VERSION#v}"` の版の確認がある | 版が違えば `Check ShellCheck version` でジョブが失敗する。確認が消えれば `tests/ci/test_shellcheck_job.py` |
| shellcheck を打つ手順はすべて導入より後にあり、`shellcheck --version` から始まる（版の確認・`bin/`・`install.sh`・`containers/base/` の 4 手順以上） | `tests/ci/test_shellcheck_job.py` |
| どの手順も水準を指定せず、`ludeeus/action-shellcheck` を使わない | `tests/ci/test_shellcheck_job.py`。`containers/base/` を検査する呼び出しは `tests/containers/test_base_shellcheck_ci.py` も見る |
| 基準の版の既定の水準で、検査の対象の指摘が 0 件 | ShellCheck の検査ジョブが失敗する |
| base のシェルスクリプトはすべて、`containers/base/` を検査する `shellcheck` の呼び出しの引数にある | `tests/containers/test_base_shellcheck_ci.py` が、載っていないファイル名を出して失敗する |
| 検査の対象の shellcheck の指示は、同じ行か直前の行に理由を持つ | `tests/ci/test_shellcheck_job.py`（`bin/*`・`install.sh`）と `tests/containers/test_base_shellcheck_ci.py`（base のシェルスクリプト）が、ファイル名と行番号を出して失敗する |
| base の Dockerfile の版の確認は `SHELLCHECK_VERSION` と同じ版を求める | base のビルドが版の確認で止まる。2 つの食い違いは `tests/containers/test_base_dockerfile_shellcheck.py` の `test_version_check_matches_ci_pin` |

### ドメインイベント

| # | イベント | 発生元 | 受け手 |
| --- | --- | --- | --- |
| E1 | Pull Request を開いた・push で更新した | 開発者・エージェント（GitHub が `pull_request` を発行する） | `pull_request` のトリガー → 7 件の検査ジョブ |
| E2 | ブランチへ push が起きた（統合ブランチ・`main` への取り込みを含む） | 開発者・エージェントの push とマージ | `push` のトリガー。行き先が 3 系統のときだけ 7 件の検査ジョブ |
| E3 | 検査ジョブが起動し、結果が出た | トリガー | Pull Request のチェック一覧と、`main` の保護設定の照合 |
| E4 | `containers/base/` にシェルスクリプトを足した | 開発者 | Pytest ジョブの漏れの検査。`ci.yml` の一覧へ足すまで落ちる |
| E5 | base イメージの shellcheck の版が上がった | Ubuntu のアーカイブの更新と base の再ビルド | 保守者。base のビルドが版の確認で止まるため、`ci.yml` の 2 つの値と Dockerfile の版の確認を一緒に上げる |

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
| `containers/base/` を検査する呼び出しが水準を指定した | Pytest の `test_base_commands_do_not_set_severity` | `水準を絞っている: <呼び出し>` |

検査ジョブどうしは互いを待たない。漏れは Pytest のジョブが、指摘は ShellCheck のジョブが見つける。

## 運用

- **基準の版を上げるとき**は、`ci.yml` の `SHELLCHECK_VERSION` と `SHELLCHECK_SHA256`、base の Dockerfile の
  版の確認の `RUN` の 3 つを一緒に上げる。SHA-256 は配布元の Release の資産から計算する。上げた版で
  検査の対象が 0 件になることを手元の base で確かめる（手順は
  [contributing.md の CI が実行するもの](../developer/contributing.md#ci-が実行するもの)）
- **`containers/base/` の直下にシェルスクリプトを足したら**、`Run ShellCheck on containers/base/` の一覧へ
  名前の順で足す
- **`bin/` にファイルを足すと自動で検査に入る。** シェルでないファイルを `bin/` に置かない
- **検査ジョブの `name` と matrix の値は変えない。** `main` の保護の必須チェック（現在は
  `Python syntax check` の 3 版・`Ruff lint`・`ShellCheck` の 5 件。`Pytest` は必須に入っていない）が
  名前で照合するため、変えると必須チェックが「待ち」のまま残る。変えるときは保護設定を同時に直す
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

pytest で確かめないもの:

- トリガーの形と検査ジョブの名前。`ci.yml` の変更のレビューと、その Pull Request 自身の検査の起動で見る。
  宛先が `main`・`release/**`・`mission/**`・それ以外のどれでも 7 件のチェックが出ること、`main`・
  `release/**`・`mission/**` 以外への push では起動しないこと、`main` 宛てで必須チェック 5 件が pass になり
  「待ち」が残らないこと
- 指摘 0 件そのもの。ShellCheck の検査ジョブが確かめる。手元では base の shellcheck で同じ検査を打てる
- ShellCheck の検査ジョブのログに手順ごとに `version: 0.11.0` が出ること。ジョブのログで見る

## 関連リンク

- [開発者ガイド: CI が実行するもの](../developer/contributing.md#ci-が実行するもの)（手元での検査の手順）
- [用語集: 継続的インテグレーション（`ci`）](../glossary.md#継続的インテグレーションci)
- [base イメージの Bash の静的検査（shellcheck）](base-image-shellcheck.md)（base の shellcheck と版の確認）
- [tmux のセッションを名指しで扱うコマンド](tmux-named-session.md)（検査の対象の `tmux-*`）
- [位置引数の解決](cli-argument-resolution.md)（検査の対象のラッパー `bin/devbase`）
- 課題: [#216](https://github.com/devbasex/devbase/issues/216)（トリガー）・
  [#247](https://github.com/devbasex/devbase/issues/247)（基準の版と既定の水準）・
  [#259](https://github.com/devbasex/devbase/issues/259)（`containers/base/` の検査の対象）
