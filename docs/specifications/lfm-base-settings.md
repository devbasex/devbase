# lfm が base の設定を取り込む経路

## 概要

`containers/lfm` は `FROM nvidia/cuda:13.3.1-cudnn-devel-ubuntu26.04` から建ち、base を `FROM` で
継がない。base の設定は、lfm の Dockerfile が `COPY --from=devbase-base:latest` で base のイメージ
から**同じパスへ取り込む**ことで届く。取り込めない `ENV` は lfm が base と同じ値で宣言する。
この 2 つが lfm への**届く経路**である。

**base が正本で、lfm は base の設定の中身を書かない。** base の設定を変えれば、base と lfm を
建て直すだけで lfm へも同じものが入る。

base に設定を足したとき、それが lfm へ届かない形なら、Docker を起動しない**到達の検査**
（`tests/containers/test_lfm_base_settings.py`）が届かない項目の名前を挙げて落ちる。
`/etc/devbase` の下へ置く設定は、lfm の Dockerfile を変えずに届く。

利用者向けの読み方は
[コンテナ操作ガイド: 作り直しても残るシェルの設定](../user/container-operations.md#作り直しても残るシェルの設定)
にある。

## 対象範囲

- lfm の Dockerfile の取り込み（base の設定の置き場所）・`ENV` の宣言・`tmux` の導入・
  `fc-cache -f` の位置
- 到達の検査と、その除外表
- 建てた lfm のイメージを base と比べる検査（`tests/containers/test_lfm_image.py`）
- 各機能の振る舞いそのものは、それぞれの確定仕様が持つ（「関連リンク」）。この仕様は、それが lfm
  へ届く道筋だけを扱う
- 対象に含まないもの:
  - base の apt の道具（`shellcheck`・`poppler-utils`・`python3-pil`・Chromium など）。設定では
    ないため取り込まない。例外は `tmux` 本体だけである（「届かないもの」）
  - lfm の固有の中身（CUDA・cuDNN・Rust・gfortran・MeCab・NVIDIA Container Toolkit の設定・
    `/etc/docker/daemon.json`・`LD_LIBRARY_PATH`）
  - `containers/snapshot`。`FROM ubuntu:26.04` で base を継がず、base の設定も取り込まない
  - base を `FROM` で継ぐ派生イメージ（`general` / `go` / `php` / `php85` / `bi-tools` / `latex` /
    `trygroup`）。層を継ぐので取り込みを要さない

## 用語

| 用語 | 意味 |
| --- | --- |
| base の設定 | base の Dockerfile が `/etc` 配下・`ENV`・`~/.bashrc`・`~/.claude/settings.json` に置く、利用者の操作に効く設定 |
| 届く経路 | base の設定が lfm のイメージへ入る道筋。lfm の Dockerfile の取り込みと、取り込めない `ENV` を同じ値で宣言すること |
| 取り込み | lfm の Dockerfile が `COPY --from=devbase-base:latest` で base のイメージからファイルやディレクトリを同じパスへ持ち込むこと |
| 到達の検査 | base の Dockerfile から base の設定を集め、それぞれが lfm へ届くかを Docker を起動せずに判定するテスト（`tests/containers/test_lfm_base_settings.py`） |
| 除外表 | 到達の検査が、lfm へ届かなくてよいとする base の項目と、その理由の一覧（同じテストの `EXCLUDED`） |

## 構成要素

| 要素 | 置き場所 | 責務 |
| --- | --- | --- |
| base の設定 | `containers/base/Dockerfile` と、そこから `COPY` される `containers/base/` の設定ファイル | 設定の正本。書き換えてよいのは base だけ |
| lfm の取り込み | `containers/lfm/Dockerfile` の「base の設定の取り込み」の `COPY --from` と、`ENV` の宣言 | base の設定を同じパスへ持ち込み、取り込めない `ENV` を同じ値で宣言する。中身は書かない |
| 到達の検査 | `tests/containers/test_lfm_base_settings.py` | 2 つの Dockerfile の文字列を読むだけで、届かない項目の名前の一覧を返す。どちらの Dockerfile も書き換えない |
| lfm のイメージの検査 | `tests/containers/test_lfm_image.py` | 建てた `devbase-lfm:latest` と `devbase-base:latest` を `docker run` で比べる |

型（クラス）は持たない。Dockerfile の命令と、その文字列を読むテストだけで構成する。

## 仕様

### 取り込むもの

lfm は次の 5 つを base のイメージから同じパスへ取り込む。所有者は `COPY --from` が base の
ものを保つ（`--chown` を付けたものを除く）。

| 取り込み | 所有者 | 届く設定 | 振る舞いの正本 |
| --- | --- | --- | --- |
| `/etc/devbase`（ディレクトリごと） | root | AI CLI の起動定義（`ai-cli-aliases.sh`）、シェルの設定の読み込み器（`shellrc-dir.sh`） | [AI CLI alias の読み込み](ai-cli-alias-loading.md)・[shellrc-dir](shellrc-dir.md) |
| `/etc/tmux.conf` | root | `prefix S` の割り当て | [tmux-named-session](tmux-named-session.md) |
| `/etc/fonts/local.conf` | root | 総称ファミリの解決先と受け皿 | [base-image-rendering](base-image-rendering.md) |
| `/home/ubuntu/.bashrc` | ubuntu（`--chown=ubuntu:ubuntu`） | 起動定義と読み込み器を読む 2 行とその順序、`~/.local/bin` を `PATH` へ足す行 | [shellrc-dir](shellrc-dir.md) |
| `/home/ubuntu/.claude/settings.json` | ubuntu（`--chown=ubuntu:ubuntu`） | Claude Code の SessionStart フック（`unset AWS_REGION`） | — |

これとは別に、ツールの取り込み（`/usr/local`・`/usr/bin/gh`・`/usr/bin/node`・
`/usr/lib/node_modules`・`/opt`・`/home/ubuntu/.local`・`/entrypoint.sh`・`/usr/local/bin/dind`）が
ある。`tmux-first` / `tmux-clean` / `tmux-session` とその短縮名の symlink は `/usr/local` の
取り込みで届き、`entrypoint.sh` は `~/.shellrc.d` の symlink を lfm のコンテナでも張る。

**`/etc/devbase` はディレクトリごと、ほかの `/etc` の設定はファイルごとに取り込む。**
`/etc/devbase` は devbase だけが持つディレクトリで、base がその下へ足したファイルは lfm を変えずに
届く。`/etc/tmux.conf` と `/etc/fonts/local.conf` は共有のディレクトリにあるため、ファイルごとにする。

**`/etc` と `/home/ubuntu` をまるごとは取り込まない。** CUDA のイメージの `/etc`（apt の
リポジトリ・`ld.so.conf.d` の CUDA のライブラリの場所・利用者の定義）が base のものに置き換わり、
lfm が壊れる。`~/.claude` もディレクトリごとは取り込まない。base の Claude Code の導入が
`~/.claude` に置くものを lfm の設定として受け取る理由が無いためである。

### 宣言するもの

`ENV` はイメージのメタデータで、`COPY --from` では運べない。lfm は次を base と同じ値で宣言する。

| `ENV` | 値 |
| --- | --- |
| `DEBIAN_FRONTEND` | `noninteractive` |
| `NPM_CONFIG_PREFIX` | `/usr/local/share/npm-global` |
| `DEVBASE_SHELLRC_DIR` | `/home/ubuntu/.shellrc.d` |

`PATH` は lfm が cargo（`/home/ubuntu/.cargo/bin`）と `~/.local/bin` を足すため値の一致では
比べず、base の `PATH` の各要素が lfm の `PATH` に含まれることで揃える。base の `/root/.local/bin`
だけは lfm に無い（「除外表」）。

npm グループは `COPY --from` で運べないため、lfm は `ARG NPM_GID="2000"` で base と同じ GID の
グループを作り直す。取り込んだ `/usr/local/share/npm-global` の GID がこれで定義され、ubuntu が
`sudo` なしで `npm i -g` できる。

### 命令の順序

lfm の Dockerfile の最終段は次の順に並ぶ。順序の制約は到達の検査が固定する。

1. ツールの取り込み（`/usr/local` ほか）と `ENV` の宣言
2. ユーザー設定（`npm` グループ・sudoers）と `install -d -o ubuntu -g ubuntu /home/ubuntu/.claude`
3. base の設定の取り込み（上の表の 5 つ）
4. `USER ubuntu` で rustup を入れ、`~/.bashrc` へ `source $HOME/.cargo/env` を追記する
5. `npx playwright install --with-deps chromium`
6. `RUN sudo fc-cache -f`
7. `git config`、`entrypoint.sh` と `dind` の取り込み、NVIDIA の設定・`daemon.json`・
   `LD_LIBRARY_PATH`

**`~/.bashrc` の取り込みは、lfm が `~/.bashrc` へ追記する `RUN` より前に置く。** 後に置くと、
rustup と lfm の追記が base の `~/.bashrc` で上書きされて消える。

**`~/.claude` は `settings.json` の取り込みより前に ubuntu の持ち物で作る。** ファイル 1 つの
`COPY --from` は足りない親ディレクトリを root:root で作る。親が root の持ち物だと、entrypoint が
`~/.claude` をグループのボリュームへのリンクに差し替える `rm -rf` が Permission denied で止まり、
`devbase up` のコンテナが起動しない。

**`fc-cache -f` は 1 度だけ、`/etc/fonts/local.conf` の取り込みと Playwright の `RUN` より後に
走らせる。** `--with-deps` が `fonts-wqy-zenhei` などを入れた後でなければ、後から入った書体を
知らないキャッシュが残る。base の「キャッシュの作り直し」と同じ理由である。

### lfm のフォントの解決先（#243）

lfm の日本語は base と同じく `Noto Sans CJK JP` に解決される。`fc-match sans-serif`・
`fc-match sans-serif:lang=ja`・`fc-match sans` が `Noto Sans CJK JP` を返し、
`fc-match -s sans-serif:lang=ja` の 1 件目も `Noto Sans CJK JP` になる。

この解決は 3 つが揃って成り立つ。

| 要るもの | lfm での出どころ |
| --- | --- |
| 書体 `Noto Sans CJK JP` | lfm が自前の apt で入れる `fonts-noto-cjk` / `fonts-noto-cjk-extra`。base の書体は `/usr/share/fonts` にあり、取り込みの対象ではない |
| 優先順位の設定 | base から取り込む `/etc/fonts/local.conf` |
| 書体を知っているキャッシュ | Playwright の後の `fc-cache -f` |

Playwright の `--with-deps` は lfm にも `fonts-wqy-zenhei` を入れる。`/etc/fonts/local.conf` が
無いと、OS 既定の `65-nonlatin.conf` によって日本語が `WenQuanYi Zen Hei` に解決される。
`fonts-wqy-zenhei` は消さない（base と同じ理由）。

### 届かないもの

base の apt の道具は取り込まない。`/usr/bin` の実行ファイルを取り込むと共有ライブラリも一緒に
要り、どれが要るかを lfm 側で持つことになるためである。lfm に無い主なものは次のとおり。

| 道具 | base での出どころ | lfm での扱い |
| --- | --- | --- |
| `shellcheck` | base の apt | 入っていない |
| `poppler-utils`・`python3-pil` など文書を扱う道具 | base の apt | 入っていない |
| `chromium-browser` | base の apt | 入っていない。lfm は Playwright の Chromium を `~/.cache` に持つ |
| `tmux` | base の apt | **lfm の apt で入れる**（下） |

**例外は `tmux` 本体である。** `tmux` は base の設定（`/etc/tmux.conf` と
`/usr/local/bin/tmux-*`）を動かす本体のため、lfm の 1 つ目の `apt-get install` の一覧に置く。
到達の検査は、Dockerfile の命令から導けないこの依存を「要る apt のパッケージ」の表
（`REQUIRED_APT`）で持つ。

**届かないものを lfm で使うようにするには、lfm の Dockerfile の `apt-get install` の一覧へ足す。**
標準のアーカイブのパッケージなら 1 つ目の `RUN` の 1 回目の一覧、後から足したリポジトリの
パッケージなら 2 回目の一覧である。それが base の設定を動かす本体になるなら（base の設定が
そのコマンドを呼ぶなら）、到達の検査の `REQUIRED_APT` にも理由とともに足す。

### base に設定を足すときの手順

| 足す設定 | lfm への届き方 | 開発者がすること |
| --- | --- | --- |
| `/etc/devbase` の下へ `COPY` するファイル | ディレクトリごとの取り込みで届く | 無い |
| `/etc/devbase` の外へ `COPY` するファイル | 届かない。到達の検査がパスを挙げて落ちる | lfm に同じパスの取り込みを足す。`/etc` などの共有のディレクトリはファイルごとに取り込む |
| `RUN` が `~/` の下へ書くファイル（`> ~/…`・`>> ~/…`） | 取り込み済みのファイル（`~/.bashrc`・`~/.claude/settings.json`）なら届く。別のファイルなら到達の検査が落ちる | lfm に `--chown=ubuntu:ubuntu` を付けた取り込みを足す |
| `ENV` | 届かない。到達の検査が `ENV <名前>` を挙げて落ちる | lfm に同じ名前・同じ値の `ENV` を足す |
| `ENV PATH` の要素 | 届かない。到達の検査が `PATH <要素>` を挙げて落ちる | lfm の `ENV PATH` に同じ要素を足す。lfm に要らない要素なら除外表へ理由とともに足す |
| apt の道具 | 届かない。検査も落ちない | lfm で要るなら、上の「届かないもの」の手順で lfm の apt へ足す |

base の Dockerfile の `/etc/devbase` の `COPY` の前のコメントも、この規則を書いている。

### 到達の検査

到達の検査は、base と lfm の Dockerfile を文字列で読み、Docker を起動せずに判定する。
命令は行の継続（`\`）をつなぎ、コメントの行を除いて読む。`RUN` の heredoc の本文も命令に含める。
判定する関数（`missing_settings`）は 2 つの文字列を受け取り、届かない項目の名前の一覧を返す。
実物のファイルを読む処理とは分けてあり、テストは base の写しの文字列へ 1 行を足して同じ関数へ
渡せる。

base から集める項目と、届いたとみなす lfm の形は次のとおり。

| 種類 | base から集めるもの | 届いたとみなす lfm の形 | 届かないときの名前 |
| --- | --- | --- | --- |
| 置くファイル | `--from` の無い `COPY` の宛先のパス | 同じパスへの取り込みの元のパスが、宛先と同じか、宛先の上位のディレクトリ | 宛先のパス（`/etc/foo.conf`） |
| 書く利用者のファイル | `RUN` の中の `> ~/…`・`>> ~/…`（`$HOME/…` も）の書き出し先。`~` は `/home/ubuntu` | 同上 | ファイルのパス（`/home/ubuntu/.foorc`） |
| `ENV` | `PATH` 以外の `ENV` の名前と値。`${USERNAME}` などは `ARG` の既定値で展開する | lfm の `ENV` に同じ名前・同じ値がある | `ENV <名前>` |
| `PATH` の要素 | `ENV PATH` の各要素（`${PATH}` を除く） | lfm の `ENV PATH` のどれかに同じ要素がある | `PATH <要素>` |
| 要る apt のパッケージ | 検査が持つ表 `REQUIRED_APT`（`tmux`） | lfm の `apt-get install` の一覧にある | `apt <名前>` |

取り込みとして数えるのは、`--from=devbase-base:latest` で**元と宛先が同じパス**の `COPY` だけである。
base の `RUN` が作る symlink（`/usr/local/bin/tmux-go` など）は `/usr/local` の取り込みに入るため
項目として集めない。

**集める項目は Dockerfile の命令から導き、固定の一覧を持たない。** base に足した行がそのまま
検査の対象になる。例外は「要る apt のパッケージ」の表だけである。集め方が壊れて何も集め
なくなると検査が黙って通るため、既知の項目（`/etc/devbase/ai-cli-aliases.sh`・`/etc/tmux.conf`・
`ENV DEVBASE_SHELLRC_DIR` など）が集まることも縛る。

失敗のメッセージは `lfm へ届かない base の設定:` の後に、項目の名前を 1 行ずつ出す。

### 除外表

実物の 2 つの Dockerfile に当てたとき、除外している項目は 1 つだけである。

| 項目 | 理由 |
| --- | --- |
| `PATH /root/.local/bin` | base で root の uv が入る場所。lfm は `/root` を取り込まず、ubuntu の `~/.local/bin` を `PATH` に持つ |

除外表の各行は、実物の base から集めた項目に当たらなければならない。base から項目が消えた
のに除外が残ると、検査が落ちる。

### lfm の Dockerfile の形

到達の検査は項目の照合とは別に、lfm の Dockerfile の形を縛る。

| 条件 | 破れたとき |
| --- | --- |
| `~/.bashrc` へ `alias` を書き出す `RUN` が無い（起動定義は `/etc/devbase` から届く） | 起動定義が base と食い違う |
| `~/.bashrc` の取り込みが 1 つで、`~/.bashrc` へ追記する `RUN` と rustup の `RUN` より前にある | lfm の追記が消える |
| `fc-cache -f` が 1 度だけで、`/etc/fonts/local.conf` の取り込みと Playwright の `RUN` より後にある | 後から入った書体を知らないキャッシュが残る |
| 取り込みの元に `/`・`/etc`・`/home`・`/home/ubuntu` そのものが無い | CUDA のイメージの設定が base のものに置き換わる |
| `~/.claude` を ubuntu の持ち物で作る `RUN` が `settings.json` の取り込みより前にあり、取り込みに `--chown=ubuntu:ubuntu` が付く | `devbase up` のコンテナが起動しない |

## データ・設定

| 置き場所 | 中身 |
| --- | --- |
| `containers/lfm/Dockerfile` の「base の設定の取り込み」 | 5 つの `COPY --from=devbase-base:latest` |
| `containers/lfm/Dockerfile` の `ENV` | `NPM_CONFIG_PREFIX`・`PATH`・`DEVBASE_SHELLRC_DIR`（と cudabase の段の `DEBIAN_FRONTEND`） |
| `tests/containers/test_lfm_base_settings.py` の `REQUIRED_APT` | 要る apt のパッケージと理由 |
| `tests/containers/test_lfm_base_settings.py` の `EXCLUDED` | 除外表 |
| `tests/containers/test_lfm_image.py` の `UNCHANGED_SHA256` | lfm の固有の設定（`/etc/nvidia-container-runtime/config.toml`・`/etc/docker/daemon.json`）の sha256。取り込みで上書きされていないことを見る |

## 運用

- 変更は**イメージを建て直すまで反映されない**。lfm は base のイメージを取り込み元にするため、
  `devbase build base --no-cache` で base を建て直した**後に** lfm を建て直し、稼働中のコンテナは
  `devbase down` → `devbase up` で作り直す。`devbase up` だけでは反映されない
- base のイメージが無ければ lfm は建たない（ツールの取り込みも `devbase-base:latest` を要する）
- 切り戻しはコミットの revert と 2 つのイメージの建て直しで足りる。lfm は取り込みの際に
  グループのボリュームへ書かない
- 建てて確かめてあるのは arm64 である。`nvidia/cuda:13.3.1-cudnn-devel-ubuntu26.04` は arm64 と
  amd64 の両方を配っている。GPU（`--gpus all` での `nvidia-smi`）は amd64 の GPU を持つ端末で
  確かめる

## テスト観点

### 到達の検査（`tests/containers/test_lfm_base_settings.py`、Docker 不要）

- 実物の 2 つの Dockerfile で、届かない項目が無い
- 集めた項目に既知の項目（`/etc/devbase/ai-cli-aliases.sh`・`/etc/devbase/shellrc-dir.sh`・
  `/etc/tmux.conf`・`/etc/fonts/local.conf`・`~/.bashrc`・`~/.claude/settings.json`・
  `ENV DEVBASE_SHELLRC_DIR`・`/usr/local/bin/tmux-session`・`/entrypoint.sh`）が含まれる
- base の写しへ `/etc/devbase` の下の `COPY` を足しても、lfm を変えずに届かない項目が無い
- base の写しへ `ENV`・`/etc/devbase` の外の `COPY`・`~/` の別のファイルへの書き出しを足すと、
  それぞれの名前だけが出る
- lfm から 5 つの取り込み・`ENV DEVBASE_SHELLRC_DIR`・apt の `tmux` のどれか 1 つを消すと、
  その項目が出る
- `/etc/devbase` をファイルごとの取り込みに変えると、base が足したファイルが出る
- `ENV` の値は `ARG` の既定値で展開してから比べる
- `PATH` は要素ごとに比べ、`/root/.local/bin` だけが除外される
- 除外表の各行が base の項目に当たる
- 「lfm の Dockerfile の形」の 5 つの条件

### lfm のイメージの検査（`tests/containers/test_lfm_image.py`）

建てた lfm と base を `docker run` で比べる。`docker run` はイメージごとに 1 回へまとめ、
全ケースを同じ xdist のワーカーへ寄せる。

- `/etc/devbase/ai-cli-aliases.sh`・`/etc/devbase/shellrc-dir.sh`・`/etc/tmux.conf`・
  `/etc/fonts/local.conf`・`~/.claude/settings.json` の sha256 が base と一致する
- 対話シェルの `claude` / `claudb` / `gemini` / `codex` / `kiro` / `agy` の定義が base と一致し、
  `claudb` に `ANTHROPIC_MODEL` が無い。`~/.bashrc` に起動定義を直書きした行が無い
- 非対話のシェルで `DEVBASE_SHELLRC_DIR` が見え、`~/.shellrc.d` の設定が対話シェルで起動定義の
  後に読まれる
- `tmux` があり、隔離した tmux サーバで `tmux-session` が動き、`prefix S` が割り当てられている
- 日本語の 3 つの指定と `fc-match -s sans-serif:lang=ja` の 1 件目が `Noto Sans CJK JP`
- 取り込んだ利用者のファイルと `~/.claude` の所有者が ubuntu
- lfm の固有の道具（`nvcc`・`cargo`・`gfortran`・`mecab`・`docker`・`terraform`・`gh`・`node`・
  `aws`・`gcloud`）が動き、NVIDIA と docker の設定が変わっていない
- ubuntu が `sudo` なしで `npm i -g` できる

**Docker が無い・どちらかのイメージが無い・lfm がこの仕組みより前のもの
（`/etc/devbase/shellrc-dir.sh` が無い）ときは skip する。** 古いイメージを持つ人の
`pytest tests/` が全員赤くなるのを避けるためで、skip の文言に建て直しの手順を書く。
起動の失敗は skip せず失敗として知らせる。CI はイメージを建てないため、CI では常に skip になる。

### テストの外で確かめるもの

- `--gpus all` を付けた lfm のコンテナの中で `nvidia-smi` が 0 で終わること

## 関連リンク

- [AI CLI alias の読み込み](ai-cli-alias-loading.md)
- [作り直しても残るシェルの設定（`~/.shellrc.d`）](shellrc-dir.md)
- [tmux のセッションを名指しで扱うコマンド](tmux-named-session.md)
- [base イメージの文字の描画と、文書を扱う道具](base-image-rendering.md)
- [base イメージの Bash の静的検査](base-image-shellcheck.md)
- [Issue #275](https://github.com/devbasex/devbase/issues/275)
- [Issue #243](https://github.com/devbasex/devbase/issues/243)
- [Issue #261](https://github.com/devbasex/devbase/issues/261)
