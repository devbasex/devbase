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
| `containers/base/tmux.conf` | 変える | `prefix S` の行を、キーを押した pane の ID を渡して `run-shell` で `tmux-menu` を呼ぶ形に変える（決定 2・決定 4） |
| `containers/base/Dockerfile` の「tmux セッションの整理コマンド」の節 | 変える | symlink の `RUN` に `tmux-menu` を 1 つ足す |
| `tests/containers/test_tmux_session.py` | 変える | F1・F2・F3 と、`menu -c 端末 <セッション>` の形が変わらないことを確かめる。`SHORT_NAMES` に `tmux-menu` を足す（下の「テスト基盤の更新」） |
| `tests/containers/test_tmux_conf.py` | 変える | 既存の `test_prefix_s_opens_session_chooser` を書き換え、`prefix S` の割り当てが `TMUX_PANE=#{pane_id}` を付けて `tmux-menu` を呼ぶことを `list-keys` で確かめる |
| `docs/user/environment-variables.md` の「セッションを名指しで扱う」 | 変える | `tmux-menu` の使い方を足す。ホストの手順の symlink を 5 つにし、`~/.tmux.conf` の行を新しい割り当てへ差し替える |
| `docs/specifications/tmux-named-session.md` | 変える（確定仕様化で） | `menu` の 2 つの形と `tmux-menu`、`prefix S` の新しい行 |
| `issues/PLAN71_tmux-menu-measure.py` | 足す（この設計で） | 「実測」の再現用スクリプト。確定仕様化のときに消す |
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

### 決定 2: 一覧を開く定義は `tmux-session` だけに持ち、`prefix S` は `run-shell` で `tmux-menu` を呼ぶ

`prefix S` と `tmux-menu` が同じ一覧を開くことを、定義を 1 つにして保つ。2 か所に同じ template を
持つと、片方だけを直したときに 2 つの入口の動きが分かれる。実測のとおり、template を
`run-shell` の文字列へ直接書く形は `#{…}` の先の展開で壊れる。スクリプトの中に置けば
`run-shell` の展開を通らない。ホストの手順も `~/.tmux.conf` の 1 行が短くなる。

`prefix S` の行を今のまま残し、`tmux-session` にも同じ template を書く案は採らない。一致を
テストで縛ることはできるが、同じ文字列を 2 か所で持つこと自体が残る。

### 決定 3: tmux の外では attach 先を引数で取らない

名前の決まったセッションへ行くなら `tmux-go` がある。`tmux-menu` は「まず一覧を見る」入口で、
attach 先は tmux の既定（**繋がっている端末の無いセッションを優先し、その中で直近に使ったもの**。
すべてに端末が繋がっていれば全体で直近に使ったもの）で足りる。一覧からどのセッションへも移れる。
セッションを引数で取ると、決定 1 の「セッションも `-c` も受け取らない形だけが一覧を開く」規則が崩れる。

### 決定 4: `prefix S` はキーを押した pane の ID を `TMUX_PANE` で渡し、`choose-tree` の `-t` に使う

`run-shell` から起動したシェルには `TMUX_PANE` が無い。`-t` の無い `choose-tree` は、呼び出し元の
pane を継がず、全セッションから直近に操作されたセッションを選び直す。`prefix S` を押してから
`choose-tree` が走るまでの間に別のセッションの端末が操作されると、一覧がその端末に出る（実測の 5）。
`run-shell` は渡された文字列の `#{…}` を先に展開するため、`TMUX_PANE=#{pane_id} tmux-menu` と
書けば押した pane の ID（`%3` の形。引用の要らない文字だけ）が渡る（実測の 6）。

一覧を出す pane を決めるのは `TMUX_PANE` そのものである。tmux は、端末を持たないクライアントから
来たコマンドの現在の pane を、そのクライアントの環境の `TMUX_PANE` で決める（tmux の `cmd-find.c`
の `cmd_find_inside_pane`）。`-t` の無い `choose-tree` でも、`TMUX_PANE` があればその pane に出る
（実測の 7a）。それでも `tmux-menu` は `TMUX_PANE` を `-t` にも渡す。どの pane に出すかをコマンドの
行に書いて意図を読めるようにし、tmux が環境から現在の pane を引く規則に頼らないためである。
`-t` は保険であり、`-t` の有無で振る舞いは変わらない（実測の 7a・7b）。

`tmux-menu` に `-t` のオプションを足して ID を渡す案は採らない。`menu` の受け付ける形が増え、
tmux の中でコマンドを打つ形（`TMUX_PANE` がシェルにある）と同じ入口に揃わない。

## 実測（2026-09-24、ホストの tmux 3.7b）

再現用のスクリプトは [PLAN71_tmux-menu-measure.py](PLAN71_tmux-menu-measure.py) にある
（`python3 issues/PLAN71_tmux-menu-measure.py`。一時ディレクトリのソケットでサーバを立て、利用者の
サーバに触れない）。`python3` の `pty` で端末を繋ぎ、`tmux-session` の代わりに引数を書き出すだけの
偽物を置く。一覧を開くスクリプト `open-list` は `tmux-menu` の代わりで、`TMUX_PANE` があれば
`-t "$TMUX_PANE"` を付けて次の `<template>` で `choose-tree -Zs -O name` を実行する。`open-list-no-t`
は `open-list` から `-t` を落としたもので、`TMUX_PANE` があっても `-t` に使わない。`open-list-unset` は
`TMUX_PANE` を `unset` してから `-t` を付けずに開き、`open-list-unset-t` は `unset` する前の値を `-t` に使う。セッションは
`a`・`it's`（`'` を含む）・`b` の 3 つで、端末 `/dev/ttys024` が `a` に、`/dev/ttys025` が `b` に
繋がっている（端末の名前は実行ごとに変わる）。一覧では 1 つ上を選んで Enter を押した。

| # | 開き方 | 一覧が出た pane | `menu` へ渡った引数（ID → 名前） | 判定 |
| --- | --- | --- | --- | --- |
| 1 | `a` の端末のプロンプトで `open-list` を打つ | `a` | `menu -c /dev/ttys024 $1` → `it's` | 正しい |
| 2 | tmux の外で `tmux attach \; choose-tree -Zs -O name '<template>'` | 新しい端末 `/dev/ttys026` が `it's`（端末の無いセッション）へ繋がる | `menu -c /dev/ttys026 $2` → `b` | 正しい |
| 3 | `bind-key S run-shell "tmux choose-tree -Zs -O name '<template>'"`（template を `run-shell` の文字列へ直接埋め込む）を `a` の端末で押す | `a` | `menu -c /dev/ttys024 sh`。選ぶ前に押した端末のセッションの ID（`$0`）へ展開され、それをシェルが変数として `sh` に読んだと見られる | **誤り** |
| 4 | `bind-key S run-shell open-list` を `a` の端末で押す | `a` | `menu -c /dev/ttys024 $1` → `it's` | 正しい |
| 5 | 4 の形（1 秒待ってから開く `open-list-slow`）で、押した直後に `b` の端末へ 1 文字打つ | **`b`** | （選んでいない） | **誤り** |
| 6 | `bind-key S run-shell "TMUX_PANE=#{pane_id} open-list-slow"` で 5 と同じ操作。続けて選んで Enter | `a` | `menu -c /dev/ttys024 $1` → `it's` | 正しい |
| 7a | `b` の端末へ 1 文字打って直近を `b` にしてから、端末を持たないプロセスで `a` の pane の `TMUX`・`TMUX_PANE` を渡して `-t` を付けない `open-list-no-t` を実行する（テストの `tm.run("tmux-menu", env=tm.inside_env(home))` と同じ形） | `a` | （選んでいない） | 正しい（`-t` が無くても `TMUX_PANE` の pane に出る） |
| 7b | 7a と同じ操作で、`-t "$TMUX_PANE"` を付ける `open-list` を実行する | `a` | （選んでいない） | 正しい |
| 7c | 7a と同じ操作で、`TMUX_PANE` を渡さずに `open-list-no-t` を実行する | **`b`** | （選んでいない） | 直近の端末に出る |
| 7d | 7a と同じ操作で、`TMUX_PANE` を渡して `open-list-unset` を実行する | **`b`** | （選んでいない） | 直近の端末に出る（7c と同じ） |
| 7e | 7a と同じ操作で、`TMUX_PANE` を渡して `open-list-unset-t` を実行する | `a` | （選んでいない） | 正しい（消しても `-t` に値があれば出る） |
| 8a | `a` の pane へ `send-keys -t =a: <open-list の絶対パス> Enter` でコマンド行を打って一覧を開き、`send-keys -t =a: Up` で 1 つ上へ動かす。`b` の端末へ 1 文字打ってから `send-keys -t =a: Enter` | `a` | `menu -c /dev/ttys025 $1` → `it's`（端末は **`b`** のもの） | Enter を `send-keys` で送ると、`#{client_name}` は直近に操作された端末になる |
| 8b | 8a と同じ操作で、Enter だけを `a` の端末から送る | `a` | `menu -c /dev/ttys024 $1` → `it's` | 正しい（`send-keys` で開いた一覧でも、`send-keys` の `Up` は効く） |

スクリプトの出力（抜粋。`panes` は `セッション:pane:モード`）:

```text
## 2 outside: attach ; choose-tree
  clients: ['/dev/ttys024=a', '/dev/ttys025=b', "/dev/ttys026=it's"]
  menu args: 'menu -c /dev/ttys026 $2' -> 'menu -c /dev/ttys026 b'
## 3 bind: run-shell with inline template
  menu args: 'menu -c /dev/ttys024 sh' -> 'menu -c /dev/ttys024 sh'
## 5 bind: run-shell (no -t), other client typed during start
  panes: ['a:%0:', 'b:%2:tree-mode', "it's:%1:"]
## 6 bind: run-shell TMUX_PANE=#{pane_id}, other client typed during start
  panes: ['a:%0:tree-mode', 'b:%2:', "it's:%1:"]
## 6b same binding, pick and Enter
  menu args: 'menu -c /dev/ttys024 $1' -> "menu -c /dev/ttys024 it's"
## 7 no tty, b typed last, TMUX_PANE=%0: 7a open-list-no-t, TMUX_PANE=a's pane
  panes: ['a:%0:tree-mode', 'b:%2:', "it's:%1:"]
## 7 no tty, b typed last, TMUX_PANE=%0: 7b open-list (-t), TMUX_PANE=a's pane
  panes: ['a:%0:tree-mode', 'b:%2:', "it's:%1:"]
## 7 no tty, b typed last, TMUX_PANE=None: 7c open-list-no-t, no TMUX_PANE
  panes: ['a:%0:', 'b:%2:tree-mode', "it's:%1:"]
## 7 no tty, b typed last, TMUX_PANE=%0: 7d open-list-unset, TMUX_PANE=a's pane
  panes: ['a:%0:', 'b:%2:tree-mode', "it's:%1:"]
## 7 no tty, b typed last, TMUX_PANE=%0: 7e open-list-unset-t, TMUX_PANE=a's pane
  panes: ['a:%0:tree-mode', 'b:%2:', "it's:%1:"]
## 8a Enter by send-keys (open by send-keys, b typed last)
  menu args: 'menu -c /dev/ttys025 $1' -> "menu -c /dev/ttys025 it's"
## 8b Enter by a's terminal (open by send-keys, b typed last)
  menu args: 'menu -c /dev/ttys024 $1' -> "menu -c /dev/ttys024 it's"
```

`<template>` は今の `prefix S` の行と同じ次の文字列である。

```tmux
run-shell -t "%%%" "tmux-session menu -c #{q:client_name} #{q:session_id}"
```

- **`run-shell` は、渡された文字列の `#{…}` をシェルへ渡す前に展開する。** 3 で選ぶ前の ID が
  入ったのはこのためである。4 ではスクリプトの本文が `run-shell` の展開を通らないため、
  template は `choose-tree` へそのまま届く。6 はこの展開を使って押した pane の ID を渡す
- `run-shell` から起動したシェルでは `TMUX` はあり、`TMUX_PANE` は無い。`-t` の無い
  `choose-tree` は直近に操作されたセッションの pane に出る。4 で正しく見えたのは、押した端末が
  直近だったからにすぎない（5）
- 端末を持たないクライアントでは、`-t` が無くても環境の `TMUX_PANE` が現在の pane を決める（7a）。
  `TMUX_PANE` が無いときだけ直近の端末に出る（7c）。`-t "$TMUX_PANE"` を付けても付けなくても
  結果は同じである（7a・7b）。実装の中で `TMUX_PANE` を消しても、消す前の値を `-t` に使えば
  出る pane は変わらない（7d・7e）
- `send-keys` で pane へ送ったキーは、その pane の一覧を動かす。ただし一覧の Enter で展開される
  `#{client_name}` は、`send-keys` では pane に繋がった端末ではなく直近に操作された端末になる
  （8a）。押した端末の名前を確かめるには、Enter をその端末から送る（8b）
- tmux の外からの `attach` は、端末の繋がっていないセッションを優先する（2）

## 入出力の契約

### コマンドの形

```text
tmux-session menu                        一覧を開く（新しい形）
tmux-session menu -c 端末 <セッション>   メニューを出す（今の形。変えない）
tmux-menu                                = tmux-session menu
共通: -h / --help で使い方を出して終了コード 0
```

`menu` の 2 つの形は、**セッションも `-c` も受け取らないときに限り一覧を開く**。どちらか一方でも
あれば今の形として読み、足りなければ今と同じく終了コード 2 で終わる。

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
| tmux の中（`TMUX` がある） | `TMUX_PANE` があれば `-t "$TMUX_PANE"` を付けて `choose-tree` を実行する。`prefix S` は `TMUX_PANE=#{pane_id}` を渡す（決定 4）。無ければ `-t` を付けない（`run-shell` から素で呼ばれた場合。直近に操作されたセッションに出る） | `choose-tree` の終了コード |
| tmux の外 | `exec tmux attach-session \; choose-tree …`。attach 先は tmux の既定（端末の繋がっていないセッションを優先し、その中で直近のもの） | `tmux` の終了コード |

`choose-tree` の引数は今の `prefix S` と同じにする。

```sh
choose-tree -Zs -O name "run-shell -t \"%%%\" \"tmux-session menu -c #{q:client_name} #{q:session_id}\""
```

## `/etc/tmux.conf` の割り当てと互換性

### 割り当て

```tmux
# prefix S: セッションを選び、移る・中身を見る・落とすのメニューを出す (#234 / #270)。
# 一覧の定義は tmux-session にある (tmux-menu と同じ)。run-shell は渡した文字列の #{…} を
# 先に展開するため、template をここへ書かずにコマンドを呼ぶ。run-shell の中には TMUX_PANE が
# 無いため、押した pane の ID を渡す (無いと直近に操作された別の端末に出ることがある)。
bind-key S run-shell "TMUX_PANE=#{pane_id} tmux-menu"
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
        T->>C: run-shell "TMUX_PANE=#{pane_id} tmux-menu"
        C->>T: choose-tree -t 押した pane …
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

### テスト基盤の更新

`tests/containers/test_tmux_session.py` の `SHORT_NAMES`（38 行目）へ `tmux-menu` を足す。この
タプルは 2 つの経路に効く。

| 経路 | どこで使うか | 足した結果 |
| --- | --- | --- |
| Dockerfile の検査 | `test_dockerfile_links_short_names` | `ln -sf tmux-session /usr/local/bin/tmux-menu` を要求する（条件 10） |
| テスト用の `bin` | `TmuxEnv.__init__` の `for name in ("tmux-session", *SHORT_NAMES)`（346 行目） | `bin/tmux-menu` が本物の `tmux-session` を指す symlink になる。`tm.run("tmux-menu", …)` と、`prefix S` の `run-shell` が `PATH` から引く `tmux-menu` はこれを使う |

`fake_tm` フィクスチャ（943 行目）は、`TmuxEnv` の `bin` とは別の `fake/` を `PATH` の先頭に置き、
そこへ引数を書き出すだけの `tmux-session` を置く。`fake_tm` も `TmuxEnv` を作るため、`bin/tmux-menu`
は `SHORT_NAMES` の追加だけで `fake_tm` にもできる。**`fake/` には `tmux-menu` を置かない。** `prefix S`
の `tmux-menu` は本物で一覧を開き、Enter の後に template が呼ぶ `tmux-session menu -c …` だけを
`fake/` の偽物が受ける必要がある。`fake/` に偽の `tmux-session` を指す `tmux-menu` を置くと、
`prefix S` の `tmux-menu` は偽物になり、空の引数を書き出すだけで一覧を開かない。
`test_prefix_s_passes_selected_id_and_client` は `_open_tree` が `tree-mode` を待ち切れずに落ちる。

`SHORT_NAMES` を足さないままでは `bin` に `tmux-menu` が無く、`prefix S` の `run-shell` がコマンドを
見つけられない。`prefix S` から一覧を開く既存テストは、中身を変えずに、この基盤更新で通る。

| 基盤更新で通る既存テスト（`prefix S` を `_open_tree` / `_open_menu` で送る） | フィクスチャ |
| --- | --- |
| `test_prefix_s_passes_selected_id_and_client` | `fake_tm` |
| `test_menu_go_detaches_others_and_switches` | `ui_tm` |
| `test_menu_kill_asks_then_kills` | `ui_tm` |
| `test_menu_kill_own_session` | `ui_tm` |
| `test_menu_peek_opens_popup` | `ui_tm` |

`test_menu_notifies_client_on_failure` は `prefix S` を通らず `run-shell` から `tmux-go` を呼ぶため、
この更新に関係なく通る。

### 条件ごとの確かめ方

表の行を読む前提として、一覧の開き方・選び方と、端末の置き方を先に決める。

**一覧の開き方（tmux の中）。** pane のプロンプトへコマンド行を打つときは、繋いだ端末（`me.send`）
ではなく pane へ直接送る。`tm.tmux("send-keys", "-t", home, f"{tm.bin}/tmux-menu", "Enter")`
（`tmux-session` の側は `f"{tm.bin}/tmux-session menu"`）。`_open_tree` のコメントのとおり、繋いだ
直後の端末は入力を捨てることがあり、コマンド行の一部が落ちると送り直しても残骸が行に残る。
`send-keys` は端末を通らずに pane へ届き、pane のシェルは `home` の pane の `TMUX_PANE` を持つ。
コマンドは名前ではなく `tm.bin` の絶対パスで打つ。pane のシェルはログインシェルで、`PATH` は
テストの環境の `PATH`（`fake_tm` では `fake/` が先頭）が元になる。名前で打つと、`fake_tm` では
`tmux-session` が `fake/` の偽物に解決されて一覧が開かず、`/etc/profile` が `PATH` を並べ替える
環境では解決先がさらに変わりうる。一覧が開いたことは `tm.tmux("display-message", "-p", "-t", home,
"#{pane_mode}")` が `tree-mode` になるのを `_wait` で待って確かめる。

**一覧の開き方（tmux の外）。** `me = tm.spawn([str(tm.bin / "tmux-menu")])`。attach 先は tmux の
既定で、端末の繋がっていないセッションを優先し、同じ条件の中では直近に使ったものになる（実測の 2）。
行 4・5 は `target = tm.new(<名前>)`・`home = tm.new("zz-home")`・`tm.attach(target)` の順に作り、
`zz-home` を端末の無いただ 1 つのセッションにする。`me` は `zz-home` に繋がり
（`_wait(lambda: tm.all_clients().get(me.tty) == home)`）、その pane が `tree-mode` になる。

**1 つ上を選ぶ（テストに足す補助 `_pick_above(tm, me, sid)`）。** 一覧は名前順（`-O name`）で、
カーソルは開いた pane のセッション `zz-home` にある。どのテストでも選ぶセッションの名前は
`zz-home` より前に並ぶため、1 つ上が選ぶセッションになる。

1. `tm.tmux("send-keys", "-t", sid, "Up")` で 1 つ上へ動かす。`send-keys` のキーは端末を通らずに一覧を
   動かす（実測の 8b）
2. Enter は `me.send("\r")` で `me` の端末から送る。一覧の template の `#{client_name}` は Enter を押した
   端末に展開されるが、`send-keys` で送った Enter では直近に操作された端末になり（実測の 8a）、押した
   端末を確かめられない
3. `me` は繋いだ直後で Enter を捨てることがある。`sid` の pane が `tree-mode` のままなら、2 秒待つごとに
   Enter を送り直す（`_open_tree` と同じ回数）。`tree-mode` を抜けたら送らない。抜けたことは `me` が入力を
   受け付けている証拠になり、その後に `me` へ送る `a` / `p` / `k` は捨てられない

| 受け入れ条件 | 何で確かめるか |
| --- | --- |
| 1 | `ui_tm` で `home = tm.new("zz-home")`・`tm.attach(home)` を作り、上の「tmux の中」の形で `tmux-menu` を打つ。`home` の pane の `#{pane_mode}` が `tree-mode` になることを見る。一覧が `TMUX_PANE` の pane に出る（直近に操作された別の端末には出ない）ことは行 11 の足すテストで縛る |
| 2 | `fake_tm` で `SPECIAL_NAMES`（`'` を含む）を parametrize し、`target = tm.new(name)`・`home = tm.new("zz-home")`・`me = tm.attach(home)` を作る。1 と同じ形で開き、`_pick_above(tm, me, home)` で選び、`tm.record` が `["menu", "-c", me.tty, target]` になることを見る（`test_prefix_s_passes_selected_id_and_client` と同じ期待値）。打った絶対パスの `tmux-menu` は本物のスクリプトで一覧を開き、Enter の後に `run-shell` がサーバの `PATH`（`fake/` が先頭）から引く `tmux-session` だけが偽物になる |
| 3 | `ui_tm`（本物の `tmux-session`）で `test_menu_go_detaches_others_and_switches` と同じく `target = tm.new(name)`・`home = tm.new("zz-home")`・`me = tm.attach(home)`・`stale = tm.attach(target)` を作る。1 と同じ形で開き、`_pick_above(tm, me, home)` の後に `_wait(lambda: "移る" in me.output())` を待って `me.send("a")` を送る。`tm.clients_of(target) == {me.tty}` になり、`stale.tty` が `tm.all_clients()` から消えることを見る |
| 4 | `ui_tm` で上の「tmux の外」の形（`target = tm.new("devbase-3")`・`home = tm.new("zz-home")`・`tm.attach(target)` の後に `me = tm.spawn([str(tm.bin / "tmux-menu")])`）を作る。`me` が `home` に繋がり、`home` の pane の `#{pane_mode}` が `tree-mode` になることを見る |
| 5 | `fake_tm` で `SPECIAL_NAMES` を parametrize し、4 と同じ組み方で `target = tm.new(name)` として開く。`me` が `home` に繋がり一覧が開くのを待ち、`_pick_above(tm, me, home)` で選ぶ。カーソルは `zz-home` にあるため 1 つ上は `target` で、`tm.record` が `["menu", "-c", me.tty, target]` になることを見る |
| 6 | `tm` のサーバの無い環境で `tm.run("tmux-menu")` を実行し（`tm.run` は `bin` の絶対パスで起動する）、終了コード 1・標準エラーに「サーバ」を含むこと・実行後も `tm.sessions() == {}` であることを見る |
| 7 | 既存の `menu` のテスト（`test_menu_*`）が中身を変えずに通る。`prefix S` を通るものは「テスト基盤の更新」の後に通る |
| 8 | 2 つの形をどちらも両方の名前で走らせる。**一覧を開く形:** `tmux-menu` と `tmux-session menu` で 1・6 を走らせる（parametrize。1 は `send-keys` で `f"{tm.bin}/tmux-menu"` / `f"{tm.bin}/tmux-session menu"` を打ち、6 は `tm.run("tmux-menu")` / `tm.run("tmux-session", "menu")` で起動する）。**メニューを出す形:** `ui_tm` で `target = tm.new("devbase-3")` と `me = tm.attach(tm.new("zz-home"))` を作り、`cmd` = `f"tmux-menu -c {me.tty} {shlex.quote(target)}"` / `f"tmux-session menu -c {me.tty} {shlex.quote(target)}"` で parametrize し、`test_menu_notifies_client_on_failure` と同じく `tm.tmux("run-shell", "-b", cmd)` で呼ぶ。`run-shell` はサーバの環境の `PATH`（`bin` を含む）でコマンドを引く。`tm.new` はセッションの ID を返し、`ui_tm` で最初に作る `devbase-3` の ID は `$0` になる。`run-shell` は文字列を `sh -c` へ渡すため、引用しないと `$0` が `sh` に展開され（実測の 3 と同じ現象）、`tmux-session` は「セッションがありません: sh」で終了コード 1 になる（専用ソケットで `run-shell 'printf "<%s>\n" $0'` が `<sh>`、`'$0'` と引用すると `<$0>` になることを確かめた）。テストのファイルに `import shlex` を足す。`-b` で背景に回すため、`display-menu` が閉じるまでテストは止まらない。どちらの名前でも `me` の端末に同じメニュー（`test_menu_peek_opens_popup` と同じく `中身を見る` の項目）が出ることを `_wait(lambda: "中身を見る" in me.output())` で見る。`fake_tm` の record では確かめられない。偽物に置き換わるのは `tmux-session` だけで、`bin` の `tmux-menu` は本物のスクリプトへの symlink のまま `display-menu` を出し、`tmux-session` を呼ばないため |
| 9 | `test_usage_errors_exit_two` の parametrize へ `("tmux-menu", "-c", "/dev/pts/1")`（`-c` だけ）・`("tmux-menu", "a")`（`-c` が無い）・`("tmux-menu", "-c", "/dev/pts/1", "a", "b")`（余分な引数）・`("tmux-menu", "-x")`（知らないオプション）を足し、終了コード 2 と標準エラーの理由を見る。`test_help_exits_zero` へ `("tmux-menu", "-h")` を足し、`tm.run("tmux-session", "-h")` の標準出力に `tmux-menu` が含まれることを見る |
| 10 | Dockerfile の symlink の `RUN` に `tmux-menu` が含まれることを固定する（`SHORT_NAMES` を足した `test_dockerfile_links_short_names`）。建てたイメージで `command -v tmux-menu` を見る |
| 11 | **書き換える既存テスト:** `test_prefix_s_opens_session_chooser`（`tests/containers/test_tmux_conf.py`）は割り当てに `choose-tree` と `tmux-session menu` が含まれることを見ており、決定 2 で落ちる。割り当てがちょうど 1 つで、`run-shell` が `TMUX_PANE=#{pane_id} tmux-menu` を呼ぶことを見る形へ改める。**基盤更新で通る既存テスト:** `test_prefix_s_passes_selected_id_and_client` と、`prefix S` から開く `test_menu_*` 4 件（「テスト基盤の更新」の表）。中身は変えない。**足すテスト:** 時間の競合に頼らず、`TMUX_PANE` の pane に一覧が出ることを見る。`tm` で `home` と `other` の 2 つのセッションを作り、`tm.attach(home)` の後に `tm.attach(other)` で繋ぐ。後に繋いだ `other` の端末が直近に操作された端末になり、端末へ打たなくてもこの順だけで決まる（手元の 3.7b の専用ソケットで、打たずに `TMUX_PANE` を消して開くと `other` に出ることを確かめた）。そのうえで `tm.run("tmux-menu", env=tm.inside_env(home))` を実行し、`home` の pane の `#{pane_mode}` が `tree-mode` になり、`other` の pane はならないことを見る。このテストが縛るのは「一覧が `TMUX_PANE` の pane に出る」という振る舞いであり、`-t "$TMUX_PANE"` の分岐ではない。`inside_env` は `TMUX_PANE` を必ず渡し、tmux は `-t` が無くても環境の `TMUX_PANE` で現在の pane を決めるため、実装が `-t` を落としても、`TMUX_PANE` を読まなくても通る（実測の 7a・7b）。落ちるのは、実装が `TMUX_PANE` を消し（`unset TMUX_PANE` や `env -u TMUX_PANE`）、かつその値を `-t` にも使わずに tmux を呼ぶ壊し方だけである（実測の 7d。消す前の値を `-t` に使えば通る、7e）。`prefix S` が `TMUX_PANE=#{pane_id}` を渡すことは、書き換える静的テストが縛る。実測の 5・6 のような、押した直後に別の端末へ打つテストは採らない。実測では 1 秒待つ `open-list-slow` で押してから `choose-tree` までの間を作ったが、本物の `tmux-menu` にはその待ちが無く、その間に別の端末を操作できる保証が無いため、割り当てが `TMUX_PANE` を渡さなくても通りうる。`prefix s` は既定のまま |
| 12 | `git diff --stat main` に `tmux-first` / `tmux-clean` が現れない |
| 13 | `shellcheck containers/base/tmux-*` と全体の pytest |
| 14 | 文書の差分をレビューで見る |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| コンテナの tmux 3.6 での 4 つの開き方 | 実測はホストの 3.7b だけ。`attach \; choose-tree` と `run-shell` からの `choose-tree` は 3.6 でも使える機能だが、建てたイメージで確かめるのは実装の後 |
| 人が実際の端末で操作する確認 | 条件 1〜5 の人手の確認。リリース後テストへ回す |
