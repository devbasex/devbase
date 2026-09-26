# base イメージの Bash の静的検査（shellcheck）

## 概要

base イメージは [ShellCheck](https://www.shellcheck.net/)（`shellcheck`）を同梱する。base と、
base を継ぐ派生イメージのコンテナの中で、Bash スクリプトの静的検査をそのまま走らせられる。
bash-language-server などの言語サーバは Bash の診断を `shellcheck` に任せており、無いとエラーも
警告も出さずに診断が空になる。base に置くことで、コンテナの中で動かす言語サーバも診断を返せる。

**shellcheck が入っていないイメージは建たない。** 入れ損ないはビルドの時点で止まる。

利用者向けの読み方は
[コンテナ操作ガイド: Bash の静的検査](../user/container-operations.md#bash-の静的検査base-以降)
にある。

## 対象範囲

- base イメージへの `shellcheck` の導入と、入れ損ないをビルドで止める仕組み
- base から派生するイメージ（`general` / `go` / `php` / `php85` / `bi-tools` / `latex` /
  `trygroup`）への伝播の規則
- `containers/lfm` と `containers/snapshot` は base を継がないため対象に含まない
- bash-language-server 自体は同梱しない
- CI の ShellCheck ジョブは base の shellcheck を使わず、base と同じ版（基準の版）の公式の配布物を
  入れて使う。入れ方は `.github/workflows/ci.yml` の `shellcheck` ジョブが持つ

## 構成要素

| 要素 | 置き場所 | 責務 |
| --- | --- | --- |
| 導入 | `containers/base/Dockerfile` の 1 つ目の `RUN` の 1 回目の `apt-get install` | `poppler-utils` などの行の後に、理由のコメントとともに `shellcheck` を置く |
| 入れ損ないの検出 | `containers/base/Dockerfile` の版の確認の `RUN` | `gh --version && ... && session-manager-plugin --version && shellcheck --version \| grep -Fx "version: 0.11.0"`。無ければ `shellcheck: command not found`（終了コード 127）、版が違えば `grep` の非 0 でビルドが止まる |
| 形の検査 | `tests/containers/test_base_dockerfile_shellcheck.py` | Docker を起動せずに、上の 2 か所の形を固定する |

型（クラス）は持たない。Dockerfile の命令だけで構成する。

## 仕様

### 置き場所

`shellcheck` は Ubuntu の標準のアーカイブ（`universe`）にあり、外部のリポジトリを要さない。
そのため 1 つ目の `RUN` の**1 回目**の `apt-get install` の一覧に置く。2 回目の一覧は後から
足したリポジトリのパッケージ（`docker-ce` / `terraform` / `gh` / `nodejs` / `chromium-browser`）
のためにあり、標準のアーカイブのパッケージを混ぜない。

shellcheck のための `RUN` は立てない。立てると派生イメージ 7 つが積む層が 1 つ増え、
`apt-get update` とクリーンアップ（`apt-get clean` / `rm -rf`）をもう 1 か所に持つことになる。
Dockerfile の `apt-get update` は 2 回である。

### 入れ損ないを止める

版の確認の `RUN` は `USER ${USERNAME}` より前にあり root で走る。`/usr/bin/shellcheck` は
`PATH` にあるため、パスを書かずに呼ぶ。

**止める役はテストではなくビルドが持つ。** `apt-get install` はパッケージが一覧から消えても
失敗しないため、一覧の編集で `shellcheck` が落ちたときに止まるのは版の確認の側だけである。
テストは、版の確認の 1 語が消えないことを固定する。

### 版

パッケージの版は `shellcheck=<版>` と書かず、base を建てた時点で Ubuntu のアーカイブが配る版を入れる
（2026-09 時点で `0.11.0-2`、`shellcheck --version` は `version: 0.11.0`）。`shellcheck=<版>` と書かないのは、
アーカイブが版を上げるとその版が消えて `apt-get install` が止まるためである。

**入った版はビルドで確かめる。** 版の確認の `RUN` は `shellcheck --version | grep -Fx "version: 0.11.0"` で、
アーカイブが別の版を配るとそこで base のビルドが止まる。この版は CI の ShellCheck ジョブの基準の版
（`ci.yml` の `SHELLCHECK_VERSION`）と同じであり、手元の base と CI で指摘の数を揃える。
2 つの一致は `tests/containers/test_base_dockerfile_shellcheck.py` の `test_version_check_matches_ci_pin` が固定する。
版を上げるときは、Dockerfile の版の確認と `ci.yml` の `SHELLCHECK_VERSION`・`SHELLCHECK_SHA256` を一緒に上げる
（[CI の検査](ci-checks.md)）。

## データ・設定

依存を含めて新しく入るパッケージは `shellcheck` と `libnuma1` の 2 つである（`libc6` /
`libffi8` / `libgmp10` は既に入っている）。置き場所は `/usr/bin/shellcheck`。

| 項目 | 値 |
| --- | --- |
| `Installed-Size`（`shellcheck`） | 24971 KB（arm64）/ 22867 KB（amd64、アーカイブの `Packages.gz` の値） |
| `/usr` の増分 | 約 25 MB（arm64 の実測） |

環境変数・設定ファイルは持たない。

## 運用

- 変更は**イメージを建て直すまで反映されない**。`devbase build base --no-cache` で base を
  建て直し、使っている派生イメージ（いずれも `FROM devbase-base:latest`）も建て直し、稼働中の
  コンテナは `devbase down` → `devbase up` で作り直す。`devbase up` だけでは反映されない
- **`devbase rebuild` はここでは使えない。** `devbase build --expires=7` のシノニム
  （`lib/devbase/commands/container.py` の `cmd_rebuild`）で、期限内ならビルドを飛ばし、
  コンテナも作り直さない
- `containers/lfm` は `FROM nvidia/cuda:...` で base を継がず、base からは `/usr/local` /
  `/usr/bin/gh` / `/usr/bin/node` / `/opt` などを選んで `COPY` するだけのため、
  `/usr/bin/shellcheck` は届かない。`containers/snapshot` は `FROM ubuntu:26.04` で base を
  継がない。どちらかで要るようになったときは、そのイメージの `apt-get install` か `COPY` の
  一覧へ足す
- 建てて確かめてあるのは arm64 である。amd64 はアーカイブに同じ版があり、依存も同じである
  ことまで確かめている

## テスト観点

`tests/containers/test_base_dockerfile_shellcheck.py`（Docker を要さない）:

- `shellcheck` が 1 つ目の `RUN` の 1 回目の `apt-get install` の一覧（最初の `;` まで）に
  あること。2 回目の一覧に無いこと
- 2 つ目以降の `RUN` に `shellcheck` を入れる `apt-get install` が無く、`apt-get update` が
  2 回のままであること
- 版の確認の `RUN` を `&&` で分けた命令に `shellcheck --version` があること
- その命令が `ci.yml` の `SHELLCHECK_VERSION` と同じ版を `grep -Fx` で求めること（`test_version_check_matches_ci_pin`）

補助の関数（コメント行を除く本文・`RUN` ブロックへの分割・1 回目の一覧の切り出し）は
このファイルに持ち、`test_base_dockerfile_fonts.py` から import しない。テストのファイル
どうしを依存させない。

イメージの中の検査は Docker のテストにしない。入っていることはビルドが守る。
`test_base_image_font_matching.py` の期待値へ足すと、この変更より前に建てた base を持つ全員の
`pytest tests/` が赤くなり、赤の意味が「壊れている」と「イメージが古い」で混ざる。
「無ければ skip」の別のテストは「無い」を検査できない。

建てたイメージで手で確かめる観点:

- `docker run --rm --entrypoint /bin/bash devbase-base:latest -c 'shellcheck --version'` が
  終了コード 0 で終わり、`version:` の行を出すこと。base を建て直した後に建てた
  `devbase-general:latest` / `devbase-php:latest` でも同じであること
- `echo $foo` の 1 行を持つ Bash スクリプトへ `shellcheck` を走らせると、出力に `SC2086` を
  含み、終了コード 1 で終わること（言語サーバが使うのはこの診断である）
- 新しく入るのが `shellcheck` と `libnuma1` の 2 パッケージで、
  `dpkg-query -W -f='${Installed-Size}\n' shellcheck libnuma1` の合計が 30720 KB 以下であること。
  `docker images` の前後の差では測らない（`--no-cache` の建て直しは他の取得物の版も入れ替える）

CI はイメージを建てるジョブを持たないため、イメージの中の観点は CI では確かめない。

## 関連リンク

- [コンテナ操作ガイド: Bash の静的検査](../user/container-operations.md#bash-の静的検査base-以降)
- [base イメージの文字の描画と、文書を扱う道具](base-image-rendering.md)
- [CI の検査（トリガーと ShellCheck）](ci-checks.md)（CI の ShellCheck の検査ジョブ）
- [ShellCheck](https://www.shellcheck.net/)
