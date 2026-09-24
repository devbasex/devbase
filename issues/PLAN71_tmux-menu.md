# PLAN71: prefix S のセッションの一覧とメニューをコマンド tmux-menu からも開く

対象 issue: devbasex/devbase#270

- ワークフローモード: `standard`
  - 根拠: base イメージに新しいコマンド `tmux-menu`（公開インタフェース）が加わり、
    `tmux-session` の振る舞いが増える。変更は base を建て直した全員に届く（前例: PLAN69）
- ベースブランチ: `main`（`.ndf/worktree.json` に起点の宣言が無く、既定ブランチに落ちる）

## 依頼（原文）

> Ctrl-b S をコマンドにできませんか？

> tmux-menuというコマンドにする
> issueを起こしてください

## 目的

- **`prefix S`（`Ctrl-b S`）と同じ「セッションの一覧 → 操作のメニュー」を、コマンド
  `tmux-menu` で開ける。** キーを覚えていない人も、打てば同じ UI に辿り着ける
- **tmux の外からも開ける。** #234 の設計が弱点に挙げた「tmux の外からは呼べない」を、
  attach と同時に一覧を開くことで解く

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | **増える。** コンテナの `PATH` に短縮名 `tmux-menu` が加わる。`tmux-session` のサブコマンドの受け付ける形が 1 つ増える。既存の `tmux-session menu -c 端末 <セッション>` の形は変えない |
| データ | 変わらない |
| 既存の振る舞い | `prefix S` で開く一覧とメニューは変えない（割り当ての行は `tmux-menu` を呼ぶ形に変わる）。`prefix s`・`tmux-go` / `tmux-peek` / `tmux-kill`・`tmux-first` / `tmux-clean` は変えない |
| イメージのサイズ | symlink 1 つ。パッケージは足さない |
| 利用者の操作 | **`devbase build base --no-cache` が要る。** 派生イメージも建て直し、稼働中のコンテナは `devbase down` → `devbase up` で作り直す。ホストでは symlink を 1 つ足す（利用者向け文書の手順） |

## 前提

- **前提 1: 対象の tmux は、コンテナの 3.6 とホストの 3.7b（PLAN69 と同じ）。**
- **前提 2: 名前は `tmux-menu` で確定している**（利用者の指示、2026-09-24）。`tmux-session` の
  どのサブコマンドへ振り分けるか、既存の `menu` と意味をどう分けるかは設計で決める
- **前提 3: tmux の外から開くときの attach 先は、tmux の `attach` の既定（端末の繋がって
  いないセッションを優先し、その中で直近に使ったもの）に従う。** セッション名を引数で取るかは設計で決める
- **前提 4: CI はイメージを建てない。** 実イメージでの確認は手元で建てたイメージから採って
  Pull Request 本文へ載せる（PLAN67 / PLAN69 と同じ）

## 対象範囲

含む:

- `containers/base/tmux-session` への一覧を開く振る舞いの追加と、短縮名 `tmux-menu` の振り分け
- `containers/base/Dockerfile` への symlink `tmux-menu` の追加
- `containers/base/tmux.conf` の `prefix S` の行（一覧を開く定義を 1 か所にまとめる）
- 回帰テスト（`tests/containers/`）
- 利用者向け文書・確定仕様（`docs/specifications/tmux-named-session.md`）・CHANGELOG

含まない:

- 一覧・メニューの中身（項目・キー・動き）の変更
- `tmux-first` / `tmux-clean` の変更
- ホストへの自動の配布（PLAN69 の前提 4 のまま）
- tmux を使わない一覧の UI（`curses` など）

## 受け入れ条件

検証はすべて専用のソケットで起動した tmux で行い、利用者の tmux サーバに触れない。

### tmux の中で開く

- [ ] 1. tmux の中の pane で `tmux-menu` を実行すると、その pane にセッションの一覧
      （`choose-tree -Zs`、名前順）が出る
- [ ] 2. 1 の一覧でセッションを選んで Enter を押すと、`prefix S` から開いたときと同じ
      メニュー（移る `a` / 中身を見る `p` / 落とす `k`）が、選んだセッションを対象に出る
- [ ] 3. 2 のメニューの `a` は、Enter を押した端末を選んだセッションへ移し、その端末は外さない。
      選んだセッションに繋がっていた他の端末は外れる（`prefix S` と同じ）

### tmux の外で開く

- [ ] 4. tmux の外で、サーバが動いているときに `tmux-menu` を実行すると、attach し、
      attach した画面にすぐセッションの一覧が出る
- [ ] 5. 4 の一覧から開いたメニューの `a` / `p` / `k` が、3 と同じく選んだセッションに効く
- [ ] 6. tmux の外で、サーバが無いときは、サーバもセッションも作らず、理由を標準エラーへ出して
      終了コード 1 で終わる

### 形と互換

- [ ] 7. `tmux-session menu -c 端末 <セッション>`（`prefix S` の割り当てとホストの
      `~/.tmux.conf` の行が呼ぶ形）は、今までと同じメニューを出す
- [ ] 8. `tmux-menu` と、それに当たる `tmux-session` のサブコマンドの形が同じ振る舞いをする
- [ ] 9. 知らないオプション・余分な引数では終了コード 2 で、理由を標準エラーへ出す。
      `tmux-menu -h` は使い方を出して終了コード 0 で終わる。`tmux-session -h` の使い方に
      `tmux-menu` が載る
- [ ] 10. `containers/base/Dockerfile` が `/usr/local/bin/tmux-menu` を `tmux-session` への
      symlink として作る。建てた base イメージの `PATH` から `tmux-menu` を呼べる
- [ ] 11. `prefix S` と `prefix s` の割り当ては、変更前と同じ UI を、キーを押した端末の pane に
      開く。`prefix S` の割り当ての文字列を見る既存の静的テスト `test_prefix_s_opens_session_chooser`
      は、新しい割り当て（`run-shell` で `tmux-menu` を呼ぶ）を見る形へ書き換える。テスト基盤の
      `SHORT_NAMES` に `tmux-menu` を足したうえで、それ以外の既存のテスト（`prefix S` から開く
      `test_prefix_s_passes_selected_id_and_client` と `test_menu_*` を含む）は中身を変えずに通る
- [ ] 12. `tmux-first` / `tmux-clean` の差分が 0 行
- [ ] 13. `containers/base/tmux-*` の shellcheck が 0 件、全体の pytest が通る
- [ ] 14. 利用者向け文書に `tmux-menu` の使い方と、ホストで使うときの symlink の手順
      （5 つ目の `tmux-menu`）が載る。確定仕様と CHANGELOG に反映される

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `env -u DEVBASE_ROOT uv run pytest -q`（`tests/containers/test_tmux_session.py` ほか） |
| 静的解析 | `uvx --from shellcheck-py shellcheck containers/base/tmux-*`、`uv run ruff check` |
| 実イメージ | `devbase build base --no-cache` の後、使い捨てのコンテナで `tmux-menu` を確かめる |
| 手動確認 | 実際の端末で tmux の中と外から `tmux-menu` を打ち、一覧 → メニュー → `a` / `p` / `k` が効くことを利用者が見る（条件 2〜5 の人手の確認） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | base イメージの構成物は `containers/base/` の直下。コマンドは `/usr/local/bin`（PLAN69 と同じ） |
| コーディング規約 | POSIX sh（`#!/bin/sh`、`set -eu`）。`tmux-session` の既存の書き方に合わせる |
| テスト戦略 | `tests/containers/test_tmux_session.py` の流儀（専用のソケットの実物の tmux、pty からのキー入力）に合わせる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 手元で全体テスト、shellcheck、`devbase build base --no-cache` と建てたイメージでの確認 |
| 確認してから行う | 既存の `menu` の形を変えること（設計 Pull Request の承認で確かめる） |
| 行わない | 一覧・メニューの中身の変更、`tmux-first` / `tmux-clean` の変更、ホストへの自動配布 |

## 未決

要求の時点で未決だった 4 点は、設計（[PLAN71_tmux-menu-design.md](PLAN71_tmux-menu-design.md)）で決めた。

| 項目 | 決めたこと |
| --- | --- |
| `tmux-menu` を振り分けるサブコマンド | `menu` の短縮名にし、セッションを受け取らない形を「一覧を開く」にする（設計の決定 1） |
| tmux の外での attach 先を引数で取るか | 取らない。tmux の既定の attach 先に従う（設計の決定 3） |
| 一覧を開く定義をまとめるか | `tmux-session` だけに持ち、`prefix S` は `run-shell "TMUX_PANE=#{pane_id} tmux-menu"` で呼ぶ（設計の決定 2・決定 4） |
| シェルから開いた `choose-tree` の `#{q:client_name}` | Enter を押した端末の名前に展開される。ホストの tmux 3.7b で実測した（設計の「実測」） |
