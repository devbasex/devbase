# #275: containers/lfm が base を継がず、base に足した /etc 配下と ENV の設定が lfm に届かない

正は課題の本文（#275）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 修正レイヤー
>
> `containers/lfm/Dockerfile` のつくり。lfm は `FROM nvidia/cuda` から建ち、base からは `COPY --from=devbase-base /usr/local` などで一部を取り込むだけである。base の `/etc` 配下（`/etc/devbase/`・`/etc/tmux.conf`・`/etc/fonts/local.conf`）と `ENV DEVBASE_SHELLRC_DIR` は継がない。base に足した設定が lfm に届かない形の現れが、下の子 issue である。
>
> ## 採る手
>
> | 手 | 内容 |
> | --- | --- |
> | 統合 | 起動定義・シェルの設定・フォントの設定を base と lfm の 2 か所で持たず、base を正本にして lfm へ個別に `COPY` する |
> | 向きの修正 | base の `FROM` を `ARG` にし、CUDA 版の base を建てて lfm をその派生にする |
>
> どちらを採るかは着手の時点で決める。
>
> ## 子 issue
>
> | 番号 | 現象レイヤーと観測 |
> | --- | --- |
> | #261 | lfm のシェル。AI CLI の起動定義が `~/.bashrc` 直書きで古いモデルに固定され、`~/.shellrc.d` を読まない |
> | #243 | lfm の描画。`playwright install --with-deps` で `fonts-wqy-zenhei` が入り、base の `51-local.conf` が無いため日本語が WenQuanYi Zen Hei に解決される見込み |
>
> 子 issue に無い現れとして、lfm には `tmux` 本体が無い（`containers/lfm/Dockerfile:18-25` の apt の一覧に `tmux` が無く、base の `tmux` は `/usr/bin` にあるため `COPY --from=devbase-base:latest /usr/local` では届かない）。`/usr/local/bin` 経由で届く `tmux-session` / `tmux-go` / `tmux-peek` / `tmux-kill` / `tmux-menu` は動かず、`/etc/tmux.conf`（prefix S のメニュー）も入らない。
>
> ## 完了条件
>
> - base の `/etc` 配下と `ENV` に足した設定が、lfm に届く経路が 1 つに決まっている
> - 子 issue の各現象を lfm のイメージで確かめ、解消している
> - `docs/specifications/tmux-named-session.md`（:31, :328）の lfm の記述が実際と一致している

## 目的

- base に足した利用者向けの設定（シェルの起動定義・シェルの設定の読み込み器・tmux・フォントの既定）が、lfm のコンテナでも base と同じに効く
- 今後 base に同じ種類の設定を足したとき、lfm へ届けるかどうかを人が覚えていなくても、届くか、届かないことがテストで分かる

## 前提

- 前提 1: 「統合」と「向きの修正」のどちらを採るかは設計（`design`）で決める。以下の受け入れ条件は、どちらを採っても同じ文で判定できるように書く
- 前提 2: この課題で lfm へ届ける「base の設定」は、下の「base の設定の一覧」の行に限る。base の apt の道具（`shellcheck`・`poppler-utils`・`python3-pil` など）は設定ではないため対象に含めない。ただし `tmux` 本体は、base の `/usr/local/bin/tmux-*` と `/etc/tmux.conf` を動かすのに要るため対象に含める
- 前提 3: 子 issue #261 と #243 はこの課題の変更で一緒に解消する。#243 の「まず測る」は、直す前の lfm のイメージで `fc-match` を 1 度測って記録することで満たす（直した後の値だけでは、直しが効いたのかを示せない）
- 前提 4: lfm の固有の中身（CUDA・cuDNN・Rust・gfortran・MeCab・NVIDIA Container Toolkit の設定・`/etc/docker/daemon.json`・`LD_LIBRARY_PATH`）は変えない
- 前提 5: base の既定のビルド（`devbase build base`）の結果は変わらない。`FROM ubuntu:26.04` から建ち、`FROM devbase-base:latest` の派生イメージ（`bi-tools` / `general` / `go` / `latex` / `php` / `php85` / `trygroup`）は Dockerfile を変えずに建つ
- 前提 6: 建てて確かめるのは手元の arm64 とする。`nvidia/cuda:13.3.1-cudnn-devel-ubuntu26.04` が arm64 を提供しないときは、amd64 の gpu-host（WSL2 の GPU ホスト）で建てる
- 前提 7: GPU を使う実行（`--gpus all` でコンテナから `nvidia-smi` が通る）は gpu-host でしか確かめられない。マージ前に gpu-host を使えないときは、リリース後テストの項目へ回し、その旨を Pull Request に書く

### base の設定の一覧

base の Dockerfile が置く設定のうち、lfm に届いていないもの（2026-09-28 の `containers/base/Dockerfile` と `containers/lfm/Dockerfile` の突き合わせ）。

| # | 設定 | base での置き場所 | lfm の現状 |
| --- | --- | --- | --- |
| S1 | AI CLI の起動定義 | `/etc/devbase/ai-cli-aliases.sh` と `~/.bashrc` の読み込みの 1 行 | 無い。`~/.bashrc` に古い定義を直書き（`claudb` がモデルを固定、`agy` が無い） |
| S2 | シェルの設定の読み込み器 | `/etc/devbase/shellrc-dir.sh` と `~/.bashrc` の読み込みの 1 行（S1 の後） | 無い |
| S3 | 読み込み先の場所 | `ENV DEVBASE_SHELLRC_DIR=/home/ubuntu/.shellrc.d` | 無い |
| S4 | tmux の既定設定 | `/etc/tmux.conf` | 無い |
| S5 | tmux 本体 | apt の `tmux`（`/usr/bin/tmux`） | 無い |
| S6 | フォントの既定 | `/etc/fonts/local.conf` と、その後の `fc-cache -f` | 無い |
| S7 | Claude Code の SessionStart フック | `~/.claude/settings.json`（`unset AWS_REGION`） | 無い |
| S8 | `~/.local/bin` を `PATH` へ足す行 | `~/.bashrc` | `ENV PATH` で足しており効いている。一覧に載せるのは突き合わせの記録のため |

`tmux-first` / `tmux-clean` / `tmux-session` とその短縮名、`bao`、`entrypoint.sh`、`dind` は、`/usr/local` の `COPY` と個別の `COPY` で既に届いている。

## 対象範囲

含む:
- 上の一覧の S1〜S7 を lfm へ届ける
- base に設定を足したときに lfm へ届く経路を 1 つにする（前提 1 のどちらかの手）
- 経路が保たれていることを Docker を起動せずに確かめるテスト
- lfm の記述がある確定仕様の修正（`tmux-named-session.md`・`shellrc-dir.md`・`base-image-rendering.md`・`base-image-shellcheck.md`）と、base の Dockerfile のコメントのうち lfm に触れるもの

含まない:
- `containers/snapshot`（描画もシェルの設定も使わない道具のイメージ）
- base の apt の道具を lfm へそろえること（前提 2）
- lfm の CUDA の版・Ubuntu の版の変更
- AI CLI の起動定義そのものの変更（base の `ai-cli-aliases.sh` の中身は変えない）
- `devbase build` のビルド順の仕組みの変更（`COPY --from` / `FROM` の検出の改修）。設計で要ると分かったときは別の課題にする

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | base に設定を足した | 開発者が `containers/base/Dockerfile` へ `/etc` 配下の `COPY` か `ENV` を足す | lfm へ届かないまま気づかれない → 受け入れ条件 AC11 のテストが失敗する | — |
| E2 | base のイメージを建てた | `devbase build base` | ビルドが止まり、lfm も建たない | E1 |
| E3 | lfm のイメージを建てた | `devbase build lfm`（または lfm を使うプロジェクトのビルド） | ビルドが止まる。base が無ければ既存の仕組みが先に base を建てる（`bin/devbase` の devbase-base の確認） | E2 |
| E4 | lfm のコンテナを起動した | `devbase up` | entrypoint が失敗し、コンテナが止まる | E3 |
| E5 | lfm の対話シェルが設定を読んだ | 利用者が `devbase login` / `docker exec -it ... bash` | 読み込み器が失敗しても対話シェルは起動する（base と同じ。`shellrc-dir.sh` は終了状態を 0 にする） | E4（`~/.shellrc.d` の symlink は E4 で張られる） |
| E6 | 非対話の処理が読み込み先を見つけた | Claude Code の Bash などの `docker exec` | `DEVBASE_SHELLRC_DIR` が空になり、置き場所を見つけられない | E3（`ENV` はイメージが持つ） |
| E7 | lfm で tmux のセッションを扱った | `tmux-session` / `tmux-menu` / prefix S | `tmux` が無く、コマンドが失敗する | E4 |
| E8 | lfm で日本語を描いた | Playwright・Chromium・PDF の生成 | 中国語のフェイスで描かれる | E3（`fc-cache` はイメージを建てるときに走る） |

## 用語

| 用語 | 意味 |
| --- | --- |
| base の設定 | base の Dockerfile が `/etc` 配下・`ENV`・`~/.bashrc`・`~/.claude/settings.json` に置く、利用者の操作に効く設定。上の一覧の S1〜S8 |
| 届く経路 | base の設定が lfm のイメージへ入る道筋。「統合」なら lfm の Dockerfile の `COPY --from=devbase-base`、「向きの修正」なら `FROM` による継承 |

## 受け入れ条件

lfm のイメージの中での確認（`docker run --rm --entrypoint bash <lfm> -lc '...'` または `-ic`）:

- [ ] AC1: lfm の `/etc/devbase/ai-cli-aliases.sh` と `/etc/devbase/shellrc-dir.sh` が、同じ時点の base のイメージの同じファイルとバイト単位で一致する
- [ ] AC2: lfm の対話シェル（`bash -ic 'alias'`）の `claude` / `claudb` / `gemini` / `codex` / `kiro` / `agy` の定義が、base の対話シェルの定義と一致する。`claudb` の定義に `ANTHROPIC_MODEL` が含まれない
- [ ] AC3: lfm の `~/.bashrc` に、AI CLI の起動定義を直書きした `alias` の行が無い
- [ ] AC4: lfm で `bash -c 'echo "$DEVBASE_SHELLRC_DIR"'`（非対話）が `/home/ubuntu/.shellrc.d` を返す
- [ ] AC5: 前提: lfm のコンテナの `~/.shellrc.d/` に `alias zz-275='echo ok'` を書いた `a.sh` がある。操作: 対話シェルで `zz-275` を打つ。結果: `ok` が出る
- [ ] AC6: 前提: AC5 の `a.sh` が `alias claude='echo overridden'` も定義する。操作: 対話シェルで `alias claude`。結果: `echo overridden` の定義が返る（読み込み器が起動定義の後に読まれる）
- [ ] AC7: lfm で `command -v tmux` が成功し、`/etc/tmux.conf` が base の同じファイルとバイト単位で一致する
- [ ] AC8: lfm のイメージの中で、隔離した tmux サーバ（`tmux -L <名前>`）に対して `tmux-session` の一覧が 0 で終わる。`tmux -L <名前> -f /etc/tmux.conf list-keys` に prefix S の割り当てがある
- [ ] AC9: lfm で `fc-match sans-serif`・`fc-match sans-serif:lang=ja`・`fc-match sans` が `Noto Sans CJK JP` のフェイスを返し、`fc-match -s sans-serif:lang=ja` の 1 件目も `Noto Sans CJK JP` になる（`docs/specifications/base-image-rendering.md` の「解決先」の表と同じ）
- [ ] AC10: lfm の `~/.claude/settings.json` の SessionStart フックが、base の同じファイルと同じ内容になる

経路（Docker を起動しないテスト。`tests/containers/` に置く）:

- [ ] AC11: 前提: テストの中で base の Dockerfile の写しへ、`/etc/devbase/` 配下へ置く `COPY` か `ENV` を 1 行足す。操作: テストを走らせる。結果: lfm の Dockerfile を変えずにその設定が lfm へ届く形であればテストが通り、届かない形であればテストが失敗し、届いていない設定の名前を出す
- [ ] AC12: 実際の `containers/base/Dockerfile` と `containers/lfm/Dockerfile` に対して AC11 のテストが通る

退行させないこと:

- [ ] AC13: lfm で `nvcc --version`・`cargo --version`・`gfortran --version`・`mecab -v`・`docker --version`・`terraform version`・`gh --version`・`node --version`・`aws --version`・`gcloud --version` が 0 で終わる
- [ ] AC14: lfm の `/etc/nvidia-container-runtime/config.toml` と `/etc/docker/daemon.json` の中身が変更前と同じである
- [ ] AC15: lfm で ubuntu が `sudo` なしで `npm i -g <小さなパッケージ>` を実行できる（`npm` グループの GID が base と一致したまま）
- [ ] AC16: base の既定のビルドが `FROM ubuntu:26.04` から建ち、`tests/containers/` の既存のテストがすべて通る
- [ ] AC17: `FROM devbase-base:latest` の派生イメージのうち 1 つ（`general`）が、Dockerfile を変えずに建つ
- [ ] AC18: gpu-host で `--gpus all` を付けて起動した lfm のコンテナの中で `nvidia-smi` が 0 で終わる（前提 7。マージ前に確かめられないときはリリース後テストへ回す）

文書:

- [ ] AC19: `docs/specifications/` の `tmux-named-session.md`・`shellrc-dir.md`・`base-image-rendering.md`・`base-image-shellcheck.md` から、lfm について「base を継がないため対象に含まない／入らない／読まれない」と書いた記述のうち、実際と食い違うものが無くなる（`grep -n lfm docs/specifications/*.md` の各行が、建てた lfm のイメージの状態と一致する）
- [ ] AC20: 直す前の lfm のイメージでの `fc-match` の測定の結果（#243 の確かめ方の出力）が、課題 #243 か Pull Request に残っている

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | base の設定を足す開発者が、lfm へ届けるための手作業を覚えていなくてよい（AC11）。lfm へ届ける経路と、届かない物を足すときの手順が、確定仕様の 1 か所に書かれている |
| 移行性 | 反映は `devbase build base --no-cache` → lfm の建て直し → `devbase down` / `devbase up` で行う（`devbase up` だけでは反映されない）。切り戻しはコミットの revert と 2 つのイメージの建て直しで足りる |
| 性能・拡張性 | lfm のイメージの大きさの増加が、変更前の lfm に対して 300MB 以内に収まる（`docker image inspect -f '{{.Size}}'` で比べる）。超えるときは設計で理由を書く |
| システム環境 | `nvidia/cuda:13.3.1-cudnn-devel-ubuntu26.04` の上で、base と同じ Ubuntu 26.04 / glibc 2.43 のまま建つ |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `devbase build` / `up` のコマンドは変わらない。lfm の対話シェルの `claudb` の定義が変わり、`ANTHROPIC_MODEL` を固定しなくなる（利用者が選ぶモデルが Claude Code の既定へ戻る） |
| データ | 変わらない。グループのボリュームの `.shellrc.d/` はそのまま読まれるようになる |
| 既存の振る舞い | lfm の `~/.bashrc`・`/etc`・`ENV` が base とそろう。「向きの修正」を採るときは base の Dockerfile の `FROM` の形が変わる（既定値は変わらない） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `devbase build base --no-cache` → `devbase build lfm --no-cache`（または lfm の Dockerfile を直接 `docker build`）→ AC1〜AC10・AC13〜AC15 を `docker run --rm` で確かめる |
| テスト | `pytest tests/containers/`（AC11・AC12・AC16）。実環境の `DEVBASE_ROOT` を継がないようにして走らせる |
| 静的解析・型検査 | CI の ShellCheck の検査ジョブ（シェルの断片を足したとき） |
| 手動確認 | gpu-host で AC18。AC19 は差分を読んで `grep -n lfm docs/specifications/*.md` の各行を照らす |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | イメージの定義は `containers/<名前>/`、Docker を要さない検査は `tests/containers/`、確定仕様は `docs/specifications/` |
| コーディング規約 | 既存の Dockerfile のコメントの書き方（なぜそうするかを書く）に合わせる。シェルは ShellCheck を通す |
| テスト戦略 | 経路（AC11・AC12）は Docker を要さない静的テストで固定する。イメージの中の状態（AC1〜AC10・AC13〜AC15）は建てたイメージで確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存の `tests/containers/` の実行、base と派生イメージの建て直しでの確認 |
| 確認してから行う | base の Dockerfile の `FROM` の形を変えること（「向きの修正」）、`devbase build` のビルド順の仕組みへ手を入れること |
| 行わない | lfm の CUDA / Ubuntu の版の変更、`ai-cli-aliases.sh` の中身の変更、`containers/snapshot` への変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 「統合」と「向きの修正」のどちらを採るか（前提 1） | 設計（`design`）の担当 | 設計 PR |
| 「向きの修正」を採るとき、CUDA 版の base をどう名付け、`devbase build lfm` がそれを先に建てるか | 設計（`design`）の担当 | 設計 PR |
| `nvidia/cuda:13.3.1-cudnn-devel-ubuntu26.04` が arm64 を提供するか（前提 6） | 設計の担当が実際に取得して確かめる | 設計 PR |
