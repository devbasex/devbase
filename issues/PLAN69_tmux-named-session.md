# PLAN69: tmux のセッションを名指しで attach・調査・終了できるようにする

対象 issue: devbasex/devbase#234

- ワークフローモード: `standard`
  - 根拠: base イメージの本番の振る舞い（同梱するコマンドと `/etc/tmux.conf` のキー割り当て）を
    変える。base はすべての派生イメージの土台で、変更は建て直した全員に届く（前例: PLAN38 /
    PLAN67）
- ベースブランチ: `main`（`.ndf/worktree.json` に起点の宣言が無く、既定ブランチに落ちる）

## 目的

- **1 つの tmux セッションを名前で指して、次の 3 つの操作ができる。** 今の `tmux-first` /
  `tmux-clean` は「同じベース名のセッション群」をまとめて扱う作りで、1 つを名指しできない
  - 移る: 狙ったセッションへ移り、そのセッションに繋がっている他の端末を外す
  - 調べる: attach せずに、繋がっている端末・動いているプロセス・画面の直近を見る
  - 落とす: attach の有無・実行中かどうかに関係なく、狙ったセッションを終わらせる
- **tmux の中では、一覧から選んで同じ 3 つを呼べる。** tmux 組み込みの UI（`choose-tree` /
  `display-menu` / `display-popup`）だけで組み、追加の依存を持たない

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | **増える。** コンテナの `PATH` に `tmux-session` と短縮名 `tmux-go` / `tmux-peek` / `tmux-kill` が加わる。`/etc/tmux.conf` に `prefix S` の割り当てが加わる。devbase の CLI の引数・環境変数は増減しない |
| データ | 変わらない |
| 既存の振る舞い | `tmux-first` / `tmux-clean` は変えない。`prefix S` は tmux 3.6 / 3.7b の既定で割り当てが無い（前提 2）。利用者が `~/.tmux.conf` で `S` を割り当てていれば、後から読むそちらが勝つ |
| イメージのサイズ | シェルスクリプト 1 本と symlink 3 つ。パッケージは足さない |
| 利用者の操作 | **`devbase build base --no-cache` が要る。`devbase up` だけでは反映されない。** 派生イメージも建て直し、稼働中のコンテナは `devbase down` → `devbase up` で作り直す。ホストで使うには利用者向け文書の手順で自分で置く（前提 4） |

## 前提

- **前提 1: 対象の tmux は、コンテナの 3.6（Ubuntu 26.04 の `tmux`）とホストの 3.7b。**
  使う機能（`choose-tree` の template・`display-menu`（3.0 以降）・`display-popup`（3.2 以降）・
  書式の `q:` 修飾子）は両方にある。2026-09-24 に両方で `choose-tree` の template の置換を
  確かめた（設計文書の「実測」）
- **前提 2: `prefix S` は既定で空いている。** 2026-09-24 に `tmux -f /dev/null` で起動した
  3.6（`devbase-base:latest`）と 3.7b（ホスト）で確かめた。どちらも `list-keys -T prefix` に
  `S` が無い。`s` は既定で `choose-tree -Zs` である
- **前提 3: CI はイメージを建てない。** イメージの中でしか確かめられない受け入れ条件の証跡は、
  手元で建てたイメージから採って Pull Request 本文へ載せる（PLAN67 と同じ）。CI の runner に
  tmux が無ければ、tmux を使うテストは skip になる（`test_tmux_conf.py` と同じ扱い）
- **前提 4: ホストへ配る経路は作らない。** devbase には、ホストの `~/.local/bin` や
  `~/.tmux.conf` へ置く仕組みが無い。`install.sh` と `lib/` にそこへ書く処理が無いことを
  2026-09-24 に grep で確かめた。手順を利用者向け文書に書き、置くのは利用者が行う（設計の決定 3）
- **前提 5: tmux の外から一覧を選ぶ UI（`curses` など）は作らない。** 名指しの 3 操作は
  tmux の外からもコマンドで使える（設計の決定 4）

## 対象範囲

含む:

- 名指しの 3 操作のコマンド `containers/base/tmux-session`（新設）と、短縮名の symlink
- `containers/base/tmux.conf` への `prefix S` の割り当て
- `containers/base/Dockerfile` の tmux の整理コマンドの節への COPY と symlink の追加
- `.github/workflows/ci.yml` の ShellCheck ジョブへの `containers/base/tmux-*` の追加
- 回帰テスト（`tests/containers/`）
- 利用者向け文書（`docs/user/environment-variables.md` の tmux の節）と CHANGELOG

含まない:

- `tmux-first` / `tmux-clean` の振る舞いの変更と、実行元の端末を特定する処理の共通化
- ホストへの自動の配布（前提 4）
- tmux の外で動く一覧の UI（前提 5）
- 読み取り専用の attach（`tmux attach -r`）の操作。`choose-tree` のプレビューが同じ用途を満たす
- `containers/lfm` と `containers/snapshot`（base を継がない）
- 新しい型・永続データ・画面の追加。シェルスクリプトと tmux の設定だけで作るため、クラス図・
  ER 図・画面遷移図を作らない。呼び出される約束（コマンドの引数・出力・終了コード）は設計文書の
  「入出力の契約」に書く

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | base イメージの構成物は `containers/base/` の直下に置く。コマンドは `/usr/local/bin` へ入れる（`tmux-first` と同じ） |
| コーディング規約 | `tmux-first` / `tmux-clean` と同じく POSIX sh（`#!/bin/sh`、`set -eu`）で書く。理由をコメントに書く |
| テスト戦略 | `tests/containers/test_tmux_conf.py` の流儀に合わせ、実物の tmux を専用のソケットで起動して確かめる。利用者の tmux サーバには触れない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 手元で全体テスト、`devbase build base --no-cache` と建てたイメージでの確認、`shellcheck` |
| 確認してから行う | `prefix S` の割り当て（設計 Pull Request の承認で確かめる） |
| 行わない | `tmux-first` / `tmux-clean` の変更、ホストへの自動配布、`curses` の UI |

## 実装計画

設計は [PLAN69_tmux-named-session-design.md](PLAN69_tmux-named-session-design.md)。
**タスクへの分解は実装の持ち場で `/ndf:implementation-plan` が行う。**

### 修正対象

- `containers/base/tmux-session`（新設）
- `containers/base/tmux.conf`
- `containers/base/Dockerfile`
- `.github/workflows/ci.yml`
- `tests/containers/test_tmux_session.py`（新設）
- `tests/containers/test_tmux_conf.py`
- `docs/user/environment-variables.md`
- `CHANGELOG.md`

### 切り戻し手順

- データの移行は無い。変わるのはイメージの中身と CI の検査だけである
- 戻すには次の 4 つを順に行う
  1. ブランチの revert
  2. `devbase build base --no-cache`
  3. 使っている派生イメージの建て直し
  4. 稼働中のコンテナの作り直し（`devbase down` → `devbase up`）
- ホストへ手で置いた symlink と `~/.tmux.conf` の行は、利用者が消す

## 受け入れ条件

検証はすべて専用のソケットで起動した tmux で行い、利用者の tmux サーバに触れない。
「名前」は `devbase-1` と `devbase-10` の両方があるときに `devbase-1` だけを指すこと
（完全一致）を含む。

### 移る（`tmux-go`）

- [ ] 1. tmux の外で `tmux-go devbase-1` を実行すると、`devbase-1` に attach し、それまで
      `devbase-1` に繋がっていた端末は外れる。`devbase-10` に繋がっている端末は外れない
- [ ] 2. tmux の中で実行元の端末を特定できたとき、`tmux-go devbase-3` は `devbase-3` に
      繋がっている**他の**端末を外してから、実行元を `devbase-3` へ切り替える。実行元の端末と、
      別のセッションに繋がっている端末は外れない。特定できたときとは、プロンプトから打った場合と、
      `-c` で端末を渡した場合である
- [ ] 3. tmux の中で実行元の端末を特定できないときは、どの端末も外さず、切り替えもせず、
      手で切り替えるコマンドを標準エラーへ出して終了コード 1 で終わる

### 調べる（`tmux-peek`）

- [ ] 4. `tmux-peek devbase-2` は次の 4 つを出す
      - 繋がっている端末（端末名と最終操作の時刻）
      - pane ごとのフォアグラウンドのコマンド・pid・作業ディレクトリ
      - pane のシェルの子孫のプロセス（`&` で起動したものを含む）
      - 画面の直近の行
- [ ] 5. `tmux-peek` の前後で、対象のセッションに繋がっている端末の数と、セッションの一覧が
      変わらない

### 落とす（`tmux-kill`）

- [ ] 6. `tmux-kill devbase-3` は、端末が繋がっていても、シェル以外のコマンドが動いていても、
      `-f` なしで `devbase-3` を終わらせる。`devbase-30` は残る
- [ ] 7. `tmux-kill a b c` のうち `b` が無いとき、`a` と `c` を終わらせ、`b` が無いことを
      標準エラーへ出して終了コード 1 で終わる
- [ ] 8. tmux の中で、自分の pane があるセッションを指したときは、`-f` が無ければ終わらせず
      終了コード 1 で終わる
- [ ] 9. `tmux-kill -n devbase-3` は終わらせずに、終わらせる予定のセッションを出す

### 名前と誤り

- [ ] 10. セッション名に空白・`'`・`"`・`$`・`;` を含んでも、3 つの操作が名前どおりの
      セッションに効く
- [ ] 11. 知らないオプションでは終了コード 2、無いセッション・tmux のサーバが無いときは
      終了コード 1 で、理由を標準エラーへ出す
- [ ] 12. `tmux-session go|peek|kill|menu` と短縮名 `tmux-go` / `tmux-peek` / `tmux-kill` が
      同じ振る舞いをする

### tmux の中の UI（`prefix S`）

- [ ] 13. `containers/base/tmux.conf` を読んだ tmux の `list-keys -T prefix` に、`S` の
      割り当てがちょうど 1 つある。その割り当ては `choose-tree` を呼ぶ
- [ ] 14. `prefix S` で出る一覧で選んだセッションの ID と、操作した端末の名前が
      `tmux-session menu` へ渡る。セッション名に `'`・`"`・`$`・`;` を含んでも取り違えない
- [ ] 15. メニューに「移る」「中身を見る」「落とす」の 3 つが出て、それぞれ `tmux-session go` /
      `peek` / `kill` を呼ぶ。「落とす」は確認を挟む（建てたイメージで手で確かめる）

### 配布と検査

- [ ] 16. 建てた base イメージの `/usr/local/bin` に `tmux-session` / `tmux-go` / `tmux-peek` /
      `tmux-kill` がある。`tmux-go -h` が終了コード 0 で終わる
- [ ] 17. 建てた base イメージの `shellcheck` で `containers/base/tmux-session` を検査すると、
      指摘が 0 件で終わる。severity は既定（style まで）のままにする
- [ ] 18. CI の ShellCheck ジョブが `containers/base/tmux-first` / `tmux-clean` /
      `tmux-session` を検査する
- [ ] 19. `containers/base/tmux-first` と `containers/base/tmux-clean` に差分が無い
- [ ] 20. `uv run --locked pytest tests/ -q` が終了コード 0
- [ ] 21. 次の 2 か所に、この変更の説明がある
      - `docs/user/environment-variables.md` の tmux の節: 3 つのコマンド・`prefix S`・
        ホストで使うときの手順（symlink と `~/.tmux.conf` の 1 行）
      - `CHANGELOG.md` の `[Unreleased]` の `### Added`: 反映に `devbase build base --no-cache` が
        要ること

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --locked pytest tests/ -q` |
| 静的検査 | 建てた base イメージの中で `shellcheck /x/tmux-session`（受け入れ条件 17） |
| イメージの中 | `devbase build base --no-cache` の後に `docker run --rm --entrypoint /bin/bash devbase-base:latest -c '...'`（受け入れ条件 16）。メニューは建てたコンテナの tmux で手で確かめる（受け入れ条件 15）。出力を Pull Request 本文へ貼る |
| CI | イメージを建てない（前提 3）。ShellCheck ジョブと pytest だけが動く |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| amd64 での建て直し | 手元は arm64 のみ。足すのはシェルスクリプトだけで、アーキテクチャに依存するものは無い |
| ホストの tmux 3.7b でのメニュー | テストはホストの tmux で走るが、`display-menu` の見た目は手で確かめるまで分からない |
| CI の runner の tmux | ubuntu-latest に tmux が無ければ、tmux を使うテストは CI で skip になる。手元のテストが証跡になる |

## 依頼（原文）

#234 の「決めること」:

> - 名指しの操作を `tmux-first` / `tmux-clean` の引数として足すか、別のコマンド（`tmux-go` / `tmux-peek` / `tmux-kill` など）にするか
> - TUI のキー割り当て（`prefix S` は既定で割り当てが無いか、既存の割り当てと衝突しないか）
> - ホストの `~/.local/bin` と `~/.tmux.conf` へ配る経路を作るか
> - tmux の外からも使う要求が出たら `curses` で作るか（今は作らない）

#234 の「直し方」:

> **2 層に分ける。土台に名指しのコマンドを置き、TUI は tmux 自身の UI 機能で薄く載せる。**
>
> 土台だけでも 3 つの操作は完結する。TUI が使えない場面（tmux の外・非対話）でも土台が残る。

#234 の「日常でよく使うのに、今の道具では手間がかかる操作」:

> | 狙ったセッションへ強制 attach する（既に attach している端末は外してよい） | ... |
> | 他の端末が attach しているセッションで、何が動いているかを調べる | ... |
> | 狙ったセッションを、attach の有無に関係なく落とす | ... |
