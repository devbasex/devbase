# #239: test: containers/base のシェル (entrypoint.sh / tmux-clean / tmux-first) の異常系と分岐が固定されていない

正は課題の本文（#239）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

`containers/base` のシェル 3 本に、異常系と分岐を固定するテストが無い。PR #238 の構造改善
（`/ndf:cross-refactoring`）で codex と kiro が独立に挙げた 5 件を、そのまま課題として残す。

| 対象 | 種類 | 固定されていない経路 |
| --- | --- | --- |
| `containers/base/entrypoint.sh#devbase_write_workspace`（:106） | 異常系 | `DEVBASE_WORKSPACE_FOLDERS` の base64 の復号そのものが失敗したときに、`DEVBASE_WORKSPACE_B64` の完成品へ切り替える経路。既存の `tests/containers/test_entrypoint_repos.py` は「復号できた上で中のレコードが壊れている」場合（`test_a_broken_folder_record_does_not_fail_startup`）と B64 の正常系（`test_workspace_falls_back_to_the_prebuilt_document`）しか通していない |
| `containers/base/entrypoint.sh#devbase_write_workspace_verbatim`（:152） | 異常系 | `DEVBASE_WORKSPACE_B64` が base64 として不正なときに、`.tmp` を残さず警告に留める経路。既存テストは成功経路だけを固定している |
| `containers/base/tmux-clean#main` | 分岐 | keeper と利用中セッションの保護、`-f` / `-n` による削除の可否 |
| `containers/base/tmux-clean#main` | 異常系 | 状態の取得に失敗したときの保護、走査中にセッションが消えた場合、削除に失敗したときに記録して後続を続ける経路 |
| `containers/base/tmux-first#main` | 分岐 | 実行元のクライアントを特定できない場合に、`-f` を指定しても他人の端末を切断・切り替えしない経路。`tests/containers/test_tmux_conf.py` は設定ファイルだけを検証していて、スクリプトを実行していない |

## どこで見つけたか

PR #238（PLAN63 / base イメージの描画と文書の道具）の構造改善のテスト整備ラウンドで、
codex と kiro が `containers/base` 全体を読んで挙げた。結果は
`codex-propose-rf238-r1-result.json` / `kiro-propose-rf238-r1-result.json` にある。

## なぜこの変更の範囲外なのか

PR #238 が触るのは `containers/base/Dockerfile`（apt の一覧と `COPY`）・
`containers/base/fonts-local.conf`（新設）・`tests/containers/test_base_*.py` だけで、
`entrypoint.sh` / `tmux-clean` / `tmux-first` は 1 行も変えていない。
要求（`issues/old/PLAN63_base-image-rendering.md`）の受け入れ条件 16 個はすべてフォントの
解決先・文書を扱う道具・イメージのサイズに関するもので、これらのシェルを含まない。

## 直さないと何が起きるか

`entrypoint.sh` の 2 件は、コンテナの起動時に `.code-workspace` を書く経路である。
復号に失敗したときの分岐が固定されていないため、退行すると**起動はするが作業領域の
定義が欠けた状態**になり、利用者からは「フォルダが 1 つしか見えない」形でしか気づけない。

`tmux-clean` の 2 件は削除の可否の判断である。退行すると**利用中のセッションを消す**
恐れがあり、失敗の記録が落ちると消え残りにも気づけない。

`tmux-first` の 1 件は他人の端末への切り替えの抑止で、退行すると**別の利用者の画面を
奪う**。いずれも今の挙動が壊れているという報告ではなく、変えたときに気づけないという話である。

`containers/base/tmux-session:343` は `tmux-first` と同じ規則で実行元のクライアントを特定している。
**片方の退行はもう片方にも関わる。**

## 使える道具

PLAN69 / PLAN71 で `tests/containers/test_tmux_session.py` に `TmuxEnv` ができた。
`TMUX_TMPDIR` を分け、`TMUX` を消し、`pty` で端末を用意する、実物の tmux を使う harness である。
`tmux-clean` / `tmux-first` のテストはこれを流用できる。

## 由来

PR #238

## 依頼（原文）

> `containers/base` のシェル 3 本に、異常系と分岐を固定するテストが無い。PR #238 の構造改善
> （`/ndf:cross-refactoring`）で codex と kiro が独立に挙げた 5 件を、そのまま課題として残す。

（上の「何を見つけたか」から。5 件は同じ節の表のとおり。マイルストーン 6 の conductor の補足:）

> #239 の tmux の試験は `$TMUX` を外し、別ソケット（-L/-S か TMUX_TMPDIR）で動かす前提で書く

## 目的

- `containers/base` のシェル 3 本（`entrypoint.sh` の workspace の書き出し・`tmux-clean`・`tmux-first`）の異常系と分岐を、今の振る舞いのままテストで固定する。退行したら pytest が落ちる状態にする

## 前提

- 前提 1: 今の振る舞いを正とする。テストは今の出力・終了コード・副作用を固定し、スクリプトは変えない
- 前提 2: tmux を使うテストは、テストのプロセスが継承した `TMUX` と `TMUX_PANE` を外し、専用のソケット（`-L` / `-S`、または `TMUX_TMPDIR` を専用のディレクトリへ向ける）で起動した隔離した tmux サーバで動かす。`$TMUX` が残ると tmux は `TMUX_TMPDIR` を無視して利用者のサーバへ繋ぐため、`TMUX` を外すことは省けない。`tmux-clean` / `tmux-first` は `-S` を受け取らず既定のソケットへ繋ぐため、スクリプトを呼ぶテストは `TMUX_TMPDIR` の形（既存の `TmuxEnv`）を使う。隔離したサーバの pane の中でスクリプトを動かすとき、pane の中の `TMUX` は隔離したサーバを指す
- 前提 3: 壊れた base64 の入力には `%%%%` を使う。macOS の `base64 -d` は `not-base64!!` を終了コード 0 で通す（2026-09-26 実測）。`%%%%` は macOS・base イメージ（uutils coreutils 0.8.0）・GNU coreutils のいずれでも失敗する
- 前提 4: `tmux-clean` と `tmux-first` は git の上で実行権を持たない（`100644`。イメージでは `COPY --chmod=0755` で付く）。テストは `sh <スクリプト>` で呼ぶ
- 前提 5: tmux が無い環境では tmux を使うテストを skip する（既存の `needs_tmux` と同じ）。CI（ubuntu-latest）には tmux がある
- 前提 6: `tmux-first` は、実行元のクライアントの最終操作が 10 秒以内（`SELF_FRESH=10`）のときだけ実行元を特定できたとみなし、放置の判定に `TMUX_FIRST_IDLE`（既定 300 秒）を使う。時刻は `date +%s` から取るため、テストは `PATH` の先に置いた偽の `date` で時刻を進める

## 対象範囲

含む:
- 課題本文の 5 経路のテスト（`tests/containers/` の下）
- 既存の `TmuxEnv` を、複数のテストファイルから使える場所へ移すこと

含まない:
- `entrypoint.sh`・`tmux-clean`・`tmux-first`・`tmux-session` の振る舞いの変更
- `tmux-first`（終了コード 0）と `tmux-session go`（終了コード 1）の、実行元を特定できないときの終了コードの違いを揃えること（役割の違いによる差で、直す課題にしない。理由は設計の決定の記録に残す）
- git の実行権の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | コンテナの起動で workspace の定義を書いた | `entrypoint.sh` | 復号に失敗 → 完成品（B64）へ切り替える。完成品も壊れている → 警告だけ出して起動を続ける | — |
| E2 | `tmux-clean` が同じベース名のセッションを走査した | 利用者 | 状態を取得できない → 保護する（`-f` なら消す）。走査中に消えた → `skip` | tmux サーバが動いている |
| E3 | `tmux-clean` がセッションを消した | E2 | 消せない → 記録して次へ進み、終了コード 1 | E2 |
| E4 | `tmux-first` が他の端末を切断し、自分の端末を切り替えた | 利用者 | 実行元を特定できない → 切断も切り替えもしない | 利用者が tmux の中にいる |

## 用語

| 用語 | 意味 |
| --- | --- |
| 隔離した tmux サーバ | `TMUX` を外し、専用のソケットで起動した試験用の tmux サーバ。利用者の tmux サーバに触れない |
| keeper | `tmux-clean` が必ず残すセッション。tmux の中なら今のセッション、外ならベース名に属するうち番号が最小のもの |

## 受け入れ条件

- [ ] `DEVBASE_WORKSPACE_FOLDERS` が `%%%%` で `DEVBASE_WORKSPACE_B64` が正しいとき、`Warning: Failed to decode DEVBASE_WORKSPACE_FOLDERS` が出て、書き出し先の中身が B64 の復号結果と一致し、終了コードが 0 であることをテストが固定する
- [ ] `DEVBASE_WORKSPACE_B64` が `%%%%` のとき、`Warning: Failed to write workspace file: <書き出し先>` が出て、`<書き出し先>.tmp` が残らず、既にある書き出し先が上書きされず、終了コードが 0 であることをテストが固定する
- [ ] `tmux-clean` の分岐: keeper が残る（tmux の中と外の両方）・attach 中と実行中のセッションが `-f` 無しで残る・`-f` で消える（keeper は残る）・`-n` で何も消えず `KILL <名前>  (dry-run)` が出て終了コード 0、をテストが固定する
- [ ] `tmux-clean` の異常系: 状態を取得できないセッションが `-f` 無しで残り `セッションの状態を取得できないため削除しません` が出る・走査中に消えたセッションが `skip` と出て削除の件数に数えられない・削除に失敗したセッションで `セッションを削除できませんでした` が出て後続のセッションの処理が続き終了コードが 1、をテストが固定する
- [ ] `tmux-first` が実行元のクライアントを特定できないとき（tmux の中で、実行元の最終操作が 10 秒より前）、`-f` を付けても他の端末を切断せず、実行元の端末も切り替えず、`実行元のクライアントを特定できないため` の警告が出て、終了コードが 0 であることをテストが固定する。対照として、特定できるときは切断と切り替えが起きることも確かめる
- [ ] 上のテストを、利用者の tmux の中（`TMUX` のある端末）から `uv run --locked pytest tests/containers -q` で走らせても、利用者の tmux サーバのセッション一覧とクライアントの一覧が走らせる前と変わらない
- [ ] `tests/containers/test_tmux_session.py` の件数が変わらず、すべて通る
- [ ] 新しいテストは、時刻の待ちに合計 10 秒以上の `sleep` を使わない（偽の `date` で時刻を進める）
- [ ] `uv run --locked pytest tests/ -q` がすべて通る

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない |
| 既存の振る舞い | 変わらない（テストだけを足す） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --locked pytest tests/containers -q`、`uv run --locked pytest tests/ -q` |
| 手動確認 | 実装の後、テストが確かめる行を 1 つずつ壊して（例: `rm -f "$dest.tmp"` を消す）、対応するテストが落ちることを見て戻す。利用者の tmux の中から走らせる前後で `tmux list-sessions` と `tmux list-clients` を比べる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | テストは `tests/containers/` の下。共通の harness は同じディレクトリの中に置く |
| コーディング規約 | `CONTRIBUTING.md` |
| テスト戦略 | 実物の tmux とシェルを動かす統合テストで固定する（モックの tmux は使わない） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | tmux の試験を隔離した tmux サーバで動かす |
| 確認してから行う | スクリプトの振る舞いの変更（このミッションでは行わない） |
| 行わない | `kill-server` を `TMUX` の残った環境や、ソケットを指定しない形で打つこと |
