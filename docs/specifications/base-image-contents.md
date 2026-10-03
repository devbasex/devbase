# base イメージの中身の絞り込み（文書の除外と DinD の廃止）

## 概要

base イメージは、すべての利用者が使う道具だけを持つ。使う者の見当たらない中身と、ビルドの片付けの
漏れを持たない。重い道具は派生イメージに置く（[プロジェクト MVV](../../.ndf/mvv.md) の Value 1）。

この仕様は、base が**入れないもの**と、その規則を保つ仕組みを定める。

- dockerd と containerd を入れず、DinD（コンテナの中で dockerd を起こす形）を持たない。
  `ENABLE_DIND` は廃止を 1 行で知らせるだけで、起動は止めない
- グローバルの `aws-cdk-lib` と root の uv を入れない。`PATH` に `/root/.local/bin` を持たない
- apt の一覧（`/var/lib/apt/lists`）をイメージに残さない
- 文書の除外: apt が入れるパッケージの文書（copyright のほか）・man・info・Node.js のヘッダを展開しない。
  この設定は base を `FROM` で継ぐ派生イメージにも効く

ブラウザ（Playwright の Chromium・システムの Chrome）・追加の太さのフォント・`terraform` を base に
入れないことは [base イメージの文字の描画と、文書を扱う道具](base-image-rendering.md) が定める。

## 用語

DinD・文書の除外・展開後の大きさ・派生イメージの定義は
[用語集: イメージの継承（`image-lineage`）](../glossary.md#イメージの継承image-lineage) にある。

## 背景

base には、使う者の見当たらない中身と片付けの漏れが arm64 で約 0.5GB あった。base とその派生を
建てる・取得する・置くすべての利用者が、その分の時間とディスクを払っていた。利用者は 2026-10-03 に
base の削減を承認し（MVV の P1）、次の 4 つの課題で絞った。

| 課題 | 外したもの | 定める仕様 |
| --- | --- | --- |
| #403 | 派生イメージ lfm | この仕様（「派生イメージ」） |
| #400 | dockerd・containerd・DinD・グローバルの `aws-cdk-lib`・root の uv・apt の一覧・文書 | この仕様 |
| #401 | システムの Chrome（amd64 だけにあった） | [base-image-rendering.md](base-image-rendering.md) |
| #402 | Playwright の Chromium とブラウザの依存パッケージ・追加の太さのフォント・`terraform` | [base-image-rendering.md](base-image-rendering.md) |

**DinD は使う者の有無で廃止した（#400 の決定 1）。** `ENABLE_DIND` を設定する compose・env は、
このリポジトリ・手元のプラグイン・手元のプロジェクトに 0 件だった。プロジェクトはどれもホストの
docker.sock を mount して docker を使う。DinD を前提に作られた唯一の派生 lfm は使うプロジェクトが無く、
#403 で先に廃止した。DinD を残しても動かす先が無く、誰も通らない経路を base とすべての派生に配り続ける
ことになる。dockerd を入れた新しい派生へ DinD を移す形は、移す先を使う者がいないため採らない。

**`ENABLE_DIND` は黙って無視も拒否もせず、1 行で知らせて起動を続ける（#400 の決定 2）。** 黙って
無視すると、設定を残した利用者は DinD が起きない理由を知る手段が無い。起動を止めると、使っていない
設定の残りのためにコンテナが使えなくなる。

**lfm は使うプロジェクトが無く廃止した（#403）。** lfm は base を `COPY --from` で取り込み、base の
約 3GB を重ねて持っていた。`devbase build lfm` など lfm を名指しした操作に特別な分岐は残さず、
知らないイメージとして既存の扱い（`Image directory not found`）で止まる。

## 対象範囲

- `containers/base/Dockerfile` の apt の層・root の道具の層・`ENV PATH`・利用者の層の片付け
- 文書の除外の設定 `containers/base/dpkg-excludes`
- `containers/base/entrypoint.sh` の `ENABLE_DIND` の扱い
- base から派生するイメージ（`browser` / `general` / `go` / `php` / `php85` / `bi-tools` / `latex` /
  `trygroup`。いずれも `FROM devbase-base:latest`）への文書の除外の伝播
- `containers/snapshot` は base を継がないため対象に含まない

## 構成要素

| 要素 | 置き場所 | 責務 |
| --- | --- | --- |
| 文書の除外の設定 | `containers/base/dpkg-excludes`（イメージの中では `/etc/dpkg/dpkg.cfg.d/excludes-devbase`） | Ubuntu の `excludes` の後に読まれ、copyright を残して文書・man・info・`/usr/include/node` を外す |
| 設定の配置 | `containers/base/Dockerfile` の `FROM` の直後の `COPY --chmod=0644 dpkg-excludes /etc/dpkg/dpkg.cfg.d/excludes-devbase` | 最初の `RUN` より前に置き、base と派生のすべての `apt-get install` に効かせる |
| apt の層 | `containers/base/Dockerfile` の 1 つ目の `RUN` | 2 回目の `apt-get install` で `docker-ce-cli`・`docker-buildx-plugin`・`docker-compose-plugin` を入れ、`docker-ce`・`containerd.io` を入れない。末尾で `/usr/share/doc` の copyright でない物と `/usr/share/info` の中身と apt の一覧を消す |
| root の道具の層 | `containers/base/Dockerfile` の AWS CLI・gcloud・npm のグローバルを入れる `RUN` | `npm i -g` に `aws-cdk` を入れ、`aws-cdk-lib` を入れない。uv のインストーラを呼ばない |
| 利用者の層 | `containers/base/Dockerfile` の `USER ${USERNAME}` の後の uv・claude・agy・kiro を入れる `RUN` | uv を利用者 `ubuntu` の `~/.local/bin` に入れる。片付けで `/usr/share/doc` の copyright でない物（`sudo find`）と `/var/lib/apt/lists/*` を消す |
| DinD の知らせ | `containers/base/entrypoint.sh` の `devbase_notice_dind_removed` | `ENABLE_DIND` が `true` か `1` なら廃止を 1 行出す。どの値でも 0 を返す |
| 形の検査 | `tests/containers/test_base_dockerfile_slim.py` | Docker を起動せずに、Dockerfile と `dpkg-excludes` の形を固定する |
| 知らせの検査 | `tests/containers/test_entrypoint_dind_removed.py` | entrypoint を source して、知らせの出力と、触れてはならないコマンドを呼ばないことを固定する |

型（クラス）は持たない。Dockerfile の命令・設定ファイル・entrypoint のシェル関数だけで構成する。
Dockerfile を命令に分ける部品（`Instruction` / `parse`）は `tests/containers/dockerfile_parse.py` に
あり、Dockerfile の形を固定する検査が共通に使う。

## 仕様

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | apt で `docker-ce`・`containerd.io` を入れず、`docker-ce-cli`・`docker-buildx-plugin`・`docker-compose-plugin` を入れる | 形の検査がパッケージの名前を挙げて落ちる |
| I2 | npm のグローバルに `aws-cdk-lib` を入れず、`aws-cdk` を入れる | 同上 |
| I3 | uv のインストーラを呼ぶのは `USER ubuntu` の後の `RUN` だけで、`ENV PATH` に `/root/.local/bin` が無い | 同上 |
| I4 | apt の一覧を取得する `RUN`（`apt-get update` か `--with-deps` を含み、`/var/lib/apt` を cache mount しないもの）は、同じ `RUN` で `/var/lib/apt/lists` を消す | 同上。どの `RUN` かを挙げて落ちる |
| I5 | 文書の除外の設定を、最初の `RUN` より前に、Ubuntu の `excludes` より後に読まれる名前で置く。最初の `RUN` と利用者の層の `RUN` は、`/usr/share/doc` の下の copyright でないファイルと、ディレクトリを指さない symlink を消す | 同上 |
| I6 | `containers/base/dind` が無く、Dockerfile は `/usr/local/bin/dind` を置かない | 同上 |
| I7 | `ENABLE_DIND` が `true` か `1` のとき、DinD の廃止を 1 行出して起動を続ける。dockerd を起こさず、`/var/run/docker.sock` を消さず、`docker info` を待たない | 知らせの検査が落ちる |
| I8 | `ENABLE_DIND` が無い・`true` と `1` 以外のとき、何も出さず、起動の手順と順序は変わらない | 同上。既存の entrypoint の検査も落ちる |

I4 は「利用者の層の片付けに `/var/lib/apt/lists` がある」ではなく規則で縛る（#400 の決定 7）。
apt の一覧を取得しない `RUN` には当たらないため、`RUN` から `--with-deps` を外しても検査は意味を保つ。

### docker

base は docker の**クライアント**だけを持つ。docker CLI・`docker buildx`・`docker compose` は使える。
デーモン（dockerd）と containerd は持たず、コンテナの中で docker を使うときはホストの docker.sock を
mount する。利用者 `ubuntu` は `docker` グループに属する。

### `ENABLE_DIND`

entrypoint は起動の手順 5（AWS の設定の後、AI の設定の symlink の前）で
`devbase_notice_dind_removed` を呼ぶ。

| `ENABLE_DIND` | 振る舞い |
| --- | --- |
| `true` / `1` | `NOTICE: ENABLE_DIND は廃止しました (DinD は起動しません)。ホストの docker.sock を mount して docker を使ってください` を 1 行出し、0 を返す |
| 未設定・空・それ以外の値 | 何も出さずに 0 を返す |

どちらの場合も `sudo`・`dockerd`・`docker`・`rm` を呼ばない。関数は `DEVBASE_ENTRYPOINT_LIB_ONLY` で
entrypoint を source した検査から呼べる。

### 文書の除外

`ubuntu:26.04` は `/etc/dpkg/dpkg.cfg.d/excludes` で man・翻訳の `.mo`・doc を既に外し、copyright と
changelog だけを `path-include` で残している。dpkg は後に読んだ規則を優先するため、`excludes` より後に
並ぶ名前（`excludes-devbase`）のファイルで doc を外し直し、copyright だけを入れ直す。これで changelog も
外れる（#400 の決定 4）。

**dpkg の規則だけでは消えない物を、`RUN` の中で消す。** 規則は設定を置いた後に展開するパッケージに
しか効かず、`FROM` の層に既にある物（`NEWS.Debian.gz`・`README`・`examples/*` などのファイルと、
別のパッケージの置き場を指す symlink）は残る。そのため 1 つ目の `RUN` の末尾と利用者の層の片付けの
両方で次を打つ。

```sh
find /usr/share/doc -mindepth 1 ! -type d ! -xtype d ! -name copyright -delete
```

- `! -type d` は symlink を含み、`! -xtype d` は指す先がディレクトリの symlink を外す。
  `libgcc-s1` などのディレクトリを指す symlink は `/usr/share/doc/<パッケージ>/copyright` への経路なので残す
- 利用者の層は `USER ubuntu` の下で動き、`/usr/share/doc` は apt が root 所有で作るため `sudo find` で消す。
  `sudo` が無いと `find` が Permission denied で 0 でなく終わり、`set -eux` でビルドが止まる
- 1 つ目の `RUN` は `/usr/share/info` の中身も消す

| 採らない形 | 理由 |
| --- | --- |
| Ubuntu の `excludes` を書き換える | Ubuntu の持ち物を混ぜる |
| `RUN` の中で `printf` で設定を書く | 検査がファイルとして読めない |
| 入れた後に `rm` するだけにする | 前の層に大きさが残る |

翻訳（`/usr/share/locale`）は外さない。`.mo` は Ubuntu が既に外しており、残る `locale.alias` は glibc が読む。

### 入れないもの

| 対象 | 理由 |
| --- | --- |
| `docker-ce`（dockerd）・`containerd.io` | DinD でだけ使い、DinD を廃止した。arm64 で 106MB と 81MB |
| `containers/base/dind`（`/usr/local/bin/dind`） | 同上 |
| グローバルの `aws-cdk-lib` | Node はグローバルのモジュールを `require` で探さず、CDK のアプリは自分の `package.json` に持つ。CLI の `aws-cdk` は残す。181MB |
| root の uv | 利用者 `ubuntu` の uv と重複し、root で uv を使う処理が無い。`sudo uv` は「コマンドが無い」で失敗する。40MB |
| `PATH` の `/root/.local/bin` | root の uv を外すと入る物が無い。空の置き場を配り続けない（#400 の決定 6） |
| `/var/lib/apt/lists` | 実行時に読まない。42MB |
| `/usr/share/doc`（copyright のほか）・`/usr/share/man`・`/usr/share/info` | 実行時に読まない |
| `/usr/include/node` | ネイティブアドオンのビルドでは node-gyp がヘッダを取得する。67MB |

### 派生イメージ

base を継ぐ派生イメージは `browser` / `general` / `go` / `php` / `php85` / `bi-tools` / `latex` / `trygroup` で、
いずれも `FROM devbase-base:latest` である。派生イメージは dpkg の設定（`/etc/dpkg/dpkg.cfg.d/`）を継ぐ
ため、派生が自分で `apt-get install` するパッケージの文書も展開されない。

npm グループの GID は `ARG NPM_GID="2000"` で固定する。建て直しても、npm のグローバル領域とブラウザの
置き場の数値の所有者が変わらないようにするためである。

## データ・設定

`containers/base/dpkg-excludes` の規則:

| 規則 | 対象 |
| --- | --- |
| `path-exclude=/usr/share/doc/*` | 文書をすべて外す |
| `path-include=/usr/share/doc/*/copyright` | copyright だけを入れ直す（`path-exclude` の後に置く） |
| `path-exclude=/usr/share/man/*` | man |
| `path-exclude=/usr/share/info/*` | info |
| `path-exclude=/usr/include/node/*` | Node.js のヘッダ |

展開後の大きさ（arm64・2026-10-03。コンテナで `du -sxb /` を測ったバイト数）:

| base | 展開後の大きさ |
| --- | ---: |
| 絞り込みの前 | 4,711,162,948 |
| #400 まで | 4,241,009,996（−470,152,952） |
| #402 まで（今の base） | 2,948,038,543（−1,763,124,405） |

`docker image inspect` の `.Size` は、containerd のイメージストアでは圧縮後の大きさを返す。大きさは
展開後の大きさで比べる（#402 の決定 10）。

## 運用

- 変更は**イメージを建て直すまで反映されない**。`devbase build base --no-cache` で base を建て直し、
  使っている派生イメージも建て直し、稼働中のコンテナは `devbase down` → `devbase up` で作り直す。
  `devbase up` だけでは反映されない
- base を前後で建てて比べるときは、建てる前に `docker system df` で空きを見る。base は 1 本で数 GB あり、
  作業用のタグは記録を書いたら消す
- 大きさは同じ端末・同じ日に建てた前後の base から作ったコンテナで `du -sxb /` を測って比べる
  （apt と npm が取得する版の差を小さくする）
- uv は利用者 `ubuntu` の `~/.local/bin` にあり、`PATH` へは `~/.profile` が足す。`docker run ... bash -c` の
  ログインでないシェルでは `uv` が見つからないため、ログインシェル（`bash -lc`）で確かめる
- 派生イメージ（`latex`・`php`・`php85` など）で実行時に `/usr/share/doc` の中身を読む道具は、文書が
  展開されないため動かない。見つかったときは、その派生の Dockerfile で `path-include` を足す

## テスト観点

`tests/containers/test_base_dockerfile_slim.py`（Docker を要さない）:

- I1: apt の一覧に `docker-ce`・`containerd.io` が無く、`docker-ce-cli`・`docker-buildx-plugin`・
  `docker-compose-plugin` があること。
- I2: `npm i -g` の一覧に `aws-cdk-lib` が無く、`aws-cdk` があること。
- I3: uv のインストーラ（`astral.sh/uv/install.sh`）を呼ぶ `RUN` が `USER` の後にだけあり、`ENV PATH` に
  `/root/.local/bin` が無いこと。
- I4: cache mount の無い apt の一覧の取得を持つ `RUN` は、同じ `RUN` で `/var/lib/apt/lists` を消すこと。
- I5: 文書の除外の設定の `COPY` が最初の `RUN` より前にあり、宛先の名前が `excludes` より後に並ぶこと。
  doc の除外の後に copyright の取り込みがあり、man・info・`/usr/include/node` を外すこと。最初の `RUN` と
  利用者の層の `RUN` が `find /usr/share/doc` に `! -type d`・`! -xtype d`・`! -name copyright`・`-delete` を
  持ち、利用者の層ではそれが `sudo find` であること。
- I6: `containers/base/dind` が無く、Dockerfile に `/usr/local/bin/dind` が無いこと。

`tests/containers/test_entrypoint_dind_removed.py`（Docker を要さない）:

- I7: `ENABLE_DIND` が `true`・`1` のとき、知らせが 1 行だけ出て 0 で返り、`sudo`・`dockerd`・`docker`・
  `rm` などを呼ばないこと。
- I8: `ENABLE_DIND` が無い・`false`・空のとき、何も出さずに 0 で返ること。
- 本体の手順で、知らせの呼び出しが AWS の設定と AI の設定の間にあり、`dockerd`・`/usr/local/bin/dind`・
  `docker info`・`/var/run/docker.sock` が残っていないこと。

建てた base で確かめること（手動。CI はイメージを建てない）:

- arm64 と amd64 で、`command -v dockerd`・`command -v containerd` が失敗し、利用者 `ubuntu` のログイン
  シェルで `docker --version`・`docker buildx version`・`docker compose version`・`cdk --version`・
  `uv --version` が 0 で終わること。
- `npm ls -g aws-cdk-lib` に無く、`/root/.local/bin/uv` が無いこと。`/var/lib/apt/lists` に `lock` と
  `partial` のほかのファイルが無いこと。
- `/usr/share/man` と `/usr/include/node` にファイルが無く、`find /usr/share/doc ! -type d ! -xtype d
  ! -name copyright` が 0 件で、apt で入れたパッケージの `/usr/share/doc/<パッケージ>/copyright` が読めること。
- 変更後の base から派生イメージがすべて建つこと。
- `ENABLE_DIND=true` で起こしたコンテナの起動の出力に知らせが 1 行あり、起動が完了すること。
- 中身を外す変更では、前後の展開後の大きさの差を記録すること。

## 関連リンク

- [base イメージの文字の描画と、文書を扱う道具](base-image-rendering.md)（ブラウザ・追加の太さのフォント・`terraform`）
- [base イメージの Bash の静的検査（shellcheck）](base-image-shellcheck.md)
- [CI の検査](ci-checks.md)
- [用語集: イメージの継承（`image-lineage`）](../glossary.md#イメージの継承image-lineage)
- [コンテナ操作ガイド](../user/container-operations.md)
