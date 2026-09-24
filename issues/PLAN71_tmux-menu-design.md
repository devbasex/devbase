# PLAN71: prefix S のセッションの一覧とメニューをコマンド tmux-menu からも開く の設計

要求と受け入れ条件は [PLAN71_tmux-menu.md](PLAN71_tmux-menu.md) にある。この文書は
「どう作るか」だけを扱う。土台の `tmux-session` の設計は PLAN69 にある。確定仕様は
[docs/specifications/tmux-named-session.md](../docs/specifications/tmux-named-session.md) である。

## 機能一覧

| # | 機能 | 誰が使うか |
| --- | --- | --- |
| F1 | tmux の中で `tmux-menu` を打つと、今の pane にセッションの一覧を出す。選ぶと `prefix S` と同じメニューが出る | tmux の中にいる利用者 |
| F2 | tmux の外で `tmux-menu` を打つと、attach と同時に一覧を出す | tmux の外にいる利用者 |
| F3 | `prefix S` を F1 と同じ定義で開く（一覧を開く定義を 1 か所にする） | tmux の中にいる利用者 |
| F4 | F1〜F3 を base イメージへ入れ、ホストで使う手順を示す | base を建てる利用者と、ホストの tmux の利用者 |

## 構成要素

| 要素 | 変更 | 責務 |
| --- | --- | --- |
| `containers/base/tmux-session` | 変える | 呼ばれた名前 `tmux-menu` を `menu` へ振り分ける。`menu` がセッションを受け取らないときは一覧を開く（決定 1）。一覧を開く `choose-tree` の定義をこのファイルだけに持つ（決定 2） |
| `containers/base/tmux.conf` | 変える | `prefix S` の行を `run-shell` で `tmux-menu` を呼ぶ形に変える（決定 2） |
| `containers/base/Dockerfile` の「tmux セッションの整理コマンド」の節 | 変える | symlink の `RUN` に `tmux-menu` を 1 つ足す |
| `tests/containers/test_tmux_session.py` | 変える | F1・F2・F3 と、`menu -c 端末 <セッション>` の形が変わらないことを確かめる。Dockerfile の symlink の形を固定する（`SHORT_NAMES` に `tmux-menu` を足す） |
| `tests/containers/test_tmux_conf.py` | 変える | `prefix S` の割り当てが `tmux-menu` を呼ぶことを `list-keys` で確かめる |
| `docs/user/environment-variables.md` の「セッションを名指しで扱う」 | 変える | `tmux-menu` の使い方を足す。ホストの手順の symlink を 5 つにし、`~/.tmux.conf` の行を新しい割り当てへ差し替える |
| `docs/specifications/tmux-named-session.md` | 変える（確定仕様化で） | `menu` の 2 つの形と `tmux-menu`、`prefix S` の新しい行 |
| `CHANGELOG.md` | 変える | `[Unreleased]` の `### Added` に足す。反映に `devbase build base --no-cache` が要ることを書く |

次のものは変えない。

- `tmux-session` の `go` / `peek` / `kill` と、`menu -c 端末 <セッション>` が出すメニューの中身
- `containers/base/tmux-first` / `containers/base/tmux-clean`
- `prefix s`（tmux の既定の `choose-tree -Zs`）

## 決定の記録

### 決定 1: `tmux-menu` は `menu` サブコマンドの短縮名にし、セッションを受け取らない形を一覧を開く動きにする

利用者が決めた名前 `tmux-menu` と、サブコマンドの名前 `menu` がそろう。`tmux-go` = `go` と同じ
規則で読める。今の `menu -c 端末 <セッション>` はセッションを必ず受け取るため、受け取らない
形は空いている。空いた形に一覧を割り当てれば、今の形の呼び出し元（今の `/etc/tmux.conf` と
ホストへ写した行）は何も変えずに動く。

新しいサブコマンド（`ui` など）を足して `tmux-menu` をそちらへ振り分ける案は採らない。
`tmux-menu` と `menu` が別の動きになり、名前から動きが読めない。今の `menu` を別名へ改名する
案も採らない。ホストへ写した `~/.tmux.conf` の行が壊れる。

### 決定 2: 一覧を開く定義は `tmux-session` だけに持ち、`prefix S` は `run-shell "tmux-menu"` で呼ぶ

`prefix S` と `tmux-menu` が同じ一覧を開くことを、定義を 1 つにして保つ。2 か所に同じ template を
持つと、片方だけを直したときに 2 つの入口の動きが分かれる。実測のとおり、template を
`run-shell` の文字列へ直接書く形は `#{…}` の先の展開で壊れる。スクリプトの中に置けば
`run-shell` の展開を通らない。ホストの手順も `~/.tmux.conf` の 1 行が短くなる。

`prefix S` の行を今のまま残し、`tmux-session` にも同じ template を書く案は採らない。一致を
テストで縛ることはできるが、同じ文字列を 2 か所で持つこと自体が残る。

### 決定 3: tmux の外では attach 先を引数で取らない

名前の決まったセッションへ行くなら `tmux-go` がある。`tmux-menu` は「まず一覧を見る」入口で、
attach 先は tmux の既定（直近のセッション）で足りる。一覧からどのセッションへも移れる。
セッションを引数で取ると、決定 1 の「セッションを受け取るかで 2 つの形を分ける」規則が崩れる。

## 実測（2026-09-24、ホストの tmux 3.7b）

専用のソケットでサーバを立て、`python3` の `pty` で端末を attach した。`tmux-session` の代わりに
引数を書き出すだけの偽物を置き、一覧で 1 つ上のセッションを選んで Enter を押した。選んだ
セッションの名前は `it's`（`'` を含む）である。

| 開き方 | `menu` へ渡った引数 | 判定 |
| --- | --- | --- |
| tmux の中のプロンプトで `tmux choose-tree -Zs -O name '<template>'` を打つ。別のセッションにも端末を 1 つ繋いでおく | `menu -c <押した端末> <選んだ ID>` | 正しい |
| tmux の外で `tmux attach \; choose-tree -Zs -O name '<template>'` | `menu -c <押した端末> <選んだ ID>`。attach 先は直近のセッション | 正しい |
| `bind-key S run-shell "tmux choose-tree … '<template>'"`（template を `run-shell` の文字列へ直接埋め込む） | `menu -c <押した端末>` だけ。ID が消えた | **誤り** |
| `bind-key S run-shell '<スクリプト>'`。スクリプトが `tmux choose-tree -Zs -O name '<template>'` を実行する | `menu -c <押した端末> <選んだ ID>` | 正しい |

`<template>` は今の `prefix S` の行と同じ次の文字列である。

```tmux
run-shell -t "%%%" "tmux-session menu -c #{q:client_name} #{q:session_id}"
```

- **`run-shell` は、渡された文字列の `#{…}` をシェルへ渡す前に展開する。** 3 行目で ID が
  消えたのはこのためである。4 行目ではスクリプトの本文が `run-shell` の展開を通らないため、
  template は `choose-tree` へそのまま届く
- `run-shell` から起動したシェルでは `TMUX` はあり、`TMUX_PANE` は無かった。`choose-tree` は
  `-t` なしで、キーを押した端末が見ている pane に一覧を出した

## 入出力の契約

### コマンドの形

```text
tmux-session menu                        一覧を開く（新しい形）
tmux-session menu -c 端末 <セッション>   メニューを出す（今の形。変えない）
tmux-menu                                = tmux-session menu
共通: -h / --help で使い方を出して終了コード 0
```

`menu` の 2 つの形は、**セッションを受け取るかどうか**で分ける。

| 引数 | 動き |
| --- | --- |
| 無し | 一覧を開く |
| `-c 端末` と `<セッション>` 1 つ | 今と同じメニュー |
| `<セッション>` があり `-c` が無い | 終了コード 2（今と同じ「menu には -c が要ります」） |
| `-c 端末` だけでセッションが無い | 終了コード 2（「セッションを指定してください」） |
| セッションが 2 つ以上 | 終了コード 2（今と同じ） |
| 知らないオプション | 終了コード 2（今と同じ） |

`tmux-menu` は `tmux-go` などと同じく、呼ばれた名前で `menu` のサブコマンドとして動く。
`tmux-menu -c 端末 <セッション>` も今の形のメニューを出す。

### 一覧を開く形の動き

| 状況 | 動き | 終了コード |
| --- | --- | --- |
| tmux が無い | 標準エラーへ「tmux が見つかりません」 | 1 |
| サーバが無い | 標準エラーへ「tmux サーバが起動していません」。サーバもセッションも作らない | 1 |
| tmux の中（`TMUX` がある） | `TMUX_PANE` があれば `-t "$TMUX_PANE"` を付けて `choose-tree` を実行する。無ければ `-t` を付けない（`run-shell` から呼ばれた場合） | `choose-tree` の終了コード |
| tmux の外 | `exec tmux attach-session \; choose-tree …`。attach 先は tmux の既定（直近のセッション） | `tmux` の終了コード |

`choose-tree` の引数は今の `prefix S` と同じにする。

```sh
choose-tree -Zs -O name "run-shell -t \"%%%\" \"tmux-session menu -c #{q:client_name} #{q:session_id}\""
```

## `/etc/tmux.conf` の割り当てと互換性

### 割り当て

```tmux
# prefix S: セッションを選び、移る・中身を見る・落とすのメニューを出す (#234 / #270)。
# 一覧の定義は tmux-session にある (tmux-menu と同じ)。run-shell は渡した文字列の #{…} を
# 先に展開するため、template をここへ書かずにコマンドを呼ぶ。
bind-key S run-shell "tmux-menu"
```

### 互換性

- **`menu -c 端末 <セッション>` の形を変えない。** 今の `/etc/tmux.conf` の template と、
  利用者がホストの `~/.tmux.conf` へ写した今の行は、この形を呼ぶ。変えると、`git pull` した
  ホストで `prefix S` のメニューが開かなくなる
- ホストで今の行を残した利用者も、`tmux-menu` の symlink を足せば `tmux-menu` を使える。
  `prefix S` は今の行のまま動く

## 処理の流れ

```mermaid
sequenceDiagram
    actor U as 利用者
    participant C as tmux-menu（tmux-session menu）
    participant T as tmux サーバ
    participant M as tmux-session menu -c … ID
    alt tmux の中でコマンドを打つ
        U->>C: tmux-menu
        C->>T: choose-tree -t TMUX_PANE …
    else prefix S
        U->>T: prefix S
        T->>C: run-shell "tmux-menu"
        C->>T: choose-tree …（-t なし）
    else tmux の外
        U->>C: tmux-menu
        C->>T: exec tmux attach \; choose-tree …
    end
    T-->>U: セッションの一覧
    U->>T: 選んで Enter
    T->>M: run-shell -t "=名前:"<br/>menu -c 押した端末 ID
    M->>T: display-menu（移る a / 中身を見る p / 落とす k）
```

Enter の後は PLAN69 の `menu` の流れと同じである。

## テスト設計

受け入れ条件の番号は [PLAN71_tmux-menu.md](PLAN71_tmux-menu.md) のもの。テストは
`test_tmux_session.py` の流儀（専用のソケット、`pty` の端末、偽の `tmux-session`）に合わせる。

| 受け入れ条件 | 何で確かめるか |
| --- | --- |
| 1 | tmux の中の端末のプロンプトへ `tmux-menu` を打ち、その端末の `#{pane_mode}` が `tree-mode` になることを見る |
| 2 | 1 の後に 1 つ上を選んで Enter を押し、偽の `tmux-session` へ `menu -c <押した端末> <選んだ ID>` が渡ることを見る。`'` を含む名前を含める |
| 3 | 本物の `tmux-session` で 1 → 選ぶ → `a` を送り、押した端末が選んだセッションへ移り、そこに繋がっていた他の端末が外れることを `list-clients` で見る |
| 4 | tmux の外の端末で `tmux-menu` を起動し、端末がサーバに繋がり `tree-mode` になることを見る |
| 5 | 4 の後に 2 と同じく選んで Enter を押し、`menu -c <その端末> <選んだ ID>` が渡ることを見る |
| 6 | サーバの無い環境で `tmux-menu` を実行し、終了コード 1・標準エラーの理由・実行後もサーバが無いことを見る |
| 7 | 既存の `menu` のテスト（`test_menu_*`）がそのまま通る |
| 8 | `tmux-menu` と `tmux-session menu` で 1・6 を両方の名前で走らせる（parametrize） |
| 9 | `-c` だけ・余分な引数・知らないオプションで終了コード 2。`tmux-menu -h` が 0 で、`tmux-session -h` の出力に `tmux-menu` が含まれる |
| 10 | Dockerfile の symlink の `RUN` に `tmux-menu` が含まれることを固定する（`test_dockerfile_links_short_names`）。建てたイメージで `command -v tmux-menu` を見る |
| 11 | `tmux.conf` を読んだ tmux の `list-keys -T prefix` の `S` が `run-shell tmux-menu` であることを見る。既存の `test_prefix_s_passes_selected_id_and_client` と `test_menu_*`（`prefix S` から開く）がそのまま通る。`prefix s` は既定のまま |
| 12 | `git diff --stat main` に `tmux-first` / `tmux-clean` が現れない |
| 13 | `shellcheck containers/base/tmux-*` と全体の pytest |
| 14 | 文書の差分をレビューで見る |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| コンテナの tmux 3.6 での 4 つの開き方 | 実測はホストの 3.7b だけ。`attach \; choose-tree` と `run-shell` からの `choose-tree` は 3.6 でも使える機能だが、建てたイメージで確かめるのは実装の後 |
| 複数の端末が同じセッションの別々のウィンドウを見ているときの `prefix S` | `run-shell` からの `choose-tree` は `-t` なしで、そのセッションの今のウィンドウの pane に出る。1 つのセッションには今のウィンドウが 1 つしか無いため、押した端末とは別のウィンドウに一覧が出ることは無い見込み。実装でテストを 1 つ足して確かめる |
| 人が実際の端末で操作する確認 | 条件 1〜5 の人手の確認。リリース後テストへ回す |
