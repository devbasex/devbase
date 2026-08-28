# PLAN38: tmux の既定設定 (`/etc/tmux.conf`) を base イメージに焼き込む

## 関連リンク

- 発端: GitHub issue [#118](https://github.com/devbasex/devbase/issues/118)
  「feat: tmux の既定設定 (/etc/tmux.conf) を base イメージに焼き込む」
- 実装: `containers/base/tmux.conf`（新規）、`containers/base/Dockerfile`
- 既存の tmux 関連資産: `containers/base/tmux-first`、`containers/base/tmux-clean`
  （セッションの選択・掃除を行う補助スクリプト。tmux のオプションは設定していない）

## モード

`standard` — tmux の既定値（`mouse` / `history-limit` / `focus-events`）を変え、全コンテナの
実行時の振る舞いが変わる。公開コマンド・スキーマ・認可は変わらず、対象は `containers/base` に閉じる。

## 依頼（原文）

> ## 背景
>
> VS Code のターミナルから tmux 経由でコンテナを使っていると、**スクロールバーが表示されず出力履歴を遡れない**という問題に当たりました。
> （中略。tmux は alternate screen へ切り替わるため履歴は tmux 自身のバッファに入る）
>
> `history-limit 2000` / `mouse off` という tmux の素の初期値は、**どのプロジェクトのコンテナでも同様に不便**です。
>
> ## 提案: `/etc/tmux.conf` を base イメージに焼き込む
>
> tmux は `~/.tmux.conf` より先に `/etc/tmux.conf` を読み、ユーザー設定があればそちらが後勝ちになります。
> つまり `/etc/tmux.conf` に既定値を置けば、**全コンテナで即座に有効になり、かつ個人の `~/.tmux.conf` で上書きできます。**
> `containers/base/tmux.conf` を追加し、`containers/base/Dockerfile` で `COPY` するだけで完結します。
>
> ```tmux
> set -g mouse on
> set -g history-limit 100000
> set -g default-terminal "tmux-256color"
> set -ga terminal-overrides ",xterm-256color:Tc"
> set -g focus-events on
> ```
>
> （`focus-events` は Claude Code の通知判定に使われ、既定 `off` のままだと通知が意図どおり働かない。
> `mouse on` にしてもクリップボードへのコピーは OSC 52 経由でそのまま動作する。
> あわせてスクロール・コピー・copy-mode の基本操作を記載したい。
> 個人設定 `~/.tmux.conf` の永続化には踏み込まず、既定値の配布のみを対象とする）
>
> ## 補足: 本 issue のスコープ外（#116 向けの指摘）
>
> `containers/base/entrypoint.sh:423-434` は、永続領域にエントリが無いときのプレースホルダ作成で
> **拡張子が `.json` かどうかだけ**を見ています。#116 Phase 2 を実装する際は、この判定ロジックの修正が前提になります。

## 目的と非目的

達成したい状態:

- devbase のどのコンテナでも、tmux に入った直後から**マウスホイールで出力履歴を遡れる**。
- 遡れる行数が実用的（2000 行では 1 回のビルドログで流れ切る）。
- Claude Code が起動時に出す `tmux focus-events off` の警告が出ず、フォーカス連動の通知が働く。
- 上記は**既定値**であり、利用者が `~/.tmux.conf` を置けば個人設定が勝つ。

やらないこと:

- `~/.tmux.conf`（個人設定）の永続化。overlay 上の実ファイルで `AI_SETTINGS` の対象外という
  現状は変えない。issue #118 が明示的にスコープ外としている。
- キーバインドの変更（prefix の変更、`vi` 風の copy-mode バインドなど）。既定値からの逸脱は
  利用者の既知の操作を壊すため、今回は tmux 標準のまま。
- `containers/lfm` への適用。lfm は base を継承せず（`FROM nvidia/cuda:…`）、
  かつ apt で tmux を入れていないため tmux 自体が無い。設定を置く先がない。
- issue #118 が「スコープ外」として記録した `entrypoint.sh` のプレースホルダ判定の修正。
  #116 の前提であり、本 PR では触らない。

## 前提

実機で確認した事実（2026-08-28、`ubuntu:26.04` コンテナ上の tmux 3.6 / ホストの tmux 3.7b）:

- 前提 1: base イメージ (`containers/base/Dockerfile:19`) は apt で `tmux` を入れており、
  Ubuntu 26.04 の tmux は **3.6**。
- 前提 2: tmux は `/etc/tmux.conf` を読み、その後 `~/.tmux.conf` を読む。同じオプションを
  両方が設定した場合は `~/.tmux.conf` が勝つ（`history-limit` を 100000 / 54321 で実測して確認）。
- 前提 3: tmux 3.6 の素の既定値は `history-limit 2000` / `mouse off` / `focus-events off`。
  issue の調査環境（tmux 3.4）と同じで、26.04 への更新でも解消していない。
- 前提 4: tmux 3.6 の `default-terminal` の**既定値はすでに `tmux-256color`**。
  この 1 行は現状では値を変えない（明示的な固定として置く。「代替案と採否」の D 参照）。
- 前提 5: `tmux-256color` の terminfo は `ncurses-base` に含まれており、`ubuntu:26.04` で
  `infocmp tmux-256color` が成功する。別途 `ncurses-term` を入れる必要はない。
- 前提 6: `terminal-overrides` は tmux 3.6 でも有効。`set -ga` で追記すると既定の
  `linux*:AX@` を残したまま `xterm-256color:Tc` が後ろに積まれる（実測で index 0/1 を確認）。
- 前提 7: `mouse` / `history-limit` は session オプション（`show-options -g`）、
  `default-terminal` / `focus-events` / `terminal-overrides` は server オプション
  （`show-options -s`）。検証コマンドを取り違えると「設定が入っていない」ように見える。
- 前提 8: base を継承する派生イメージ（`bi-tools` / `general` / `go` / `latex` / `php` /
  `php85` / `trygroup`）は `FROM devbase-base:latest` なので `/etc/tmux.conf` を自動的に引き継ぐ。
- 前提 9: Dockerfile の変更なので、反映には base イメージの再ビルドが要る
  （`devbase container build`）。稼働中のコンテナには入らない。

## 用語

| 用語 | 意味 |
| --- | --- |
| copy-mode | tmux の履歴閲覧モード。`Ctrl-b [` またはホイール操作で入る |
| alternate screen | 端末の代替画面。tmux はここへ切り替わるため、端末側のスクロールバックに履歴が残らない |
| OSC 52 | 端末へクリップボード書き込みを依頼するエスケープシーケンス。tmux の `set-clipboard external` が使う |

## 受け入れ条件

検証は `containers/base/tmux.conf` を `-f` で読ませた tmux サーバー（テスト専用ソケット）と、
再ビルドした base イメージの実コンテナで行う。

- [x] 条件 1: `containers/base/tmux.conf` を読み込んだ tmux で `show-options -g mouse` が `on` を返す
      → 検証: `tests/containers/test_tmux_conf.py::test_mouse_is_on`
      （素の tmux は `off`）
- [x] 条件 2: 同じく `show-options -g history-limit` が `100000` を返す（素の tmux は `2000`）
      → 検証: `…::test_history_limit_is_raised`
- [x] 条件 3: 同じく `show-options -s focus-events` が `on` を返す（素の tmux は `off`）
      → 検証: `…::test_focus_events_is_on`
- [x] 条件 4: 同じく `show-options -s default-terminal` が `tmux-256color` を返す
      → 検証: `…::test_default_terminal_is_tmux_256color`
- [x] 条件 5: 同じく `show-options -s terminal-overrides` に `xterm-256color:Tc` が含まれ、
      → 検証: `…::test_terminal_overrides_appends_without_dropping_defaults`
      かつ tmux 既定の `linux*:AX@` が残っている（`set -ga` で追記され、既定を潰していない）
- [x] 条件 6: 前提: `/etc/tmux.conf` が配置されている。操作: 利用者が `~/.tmux.conf` に
      → 検証: `…::test_user_config_wins_over_defaults` + 実イメージで `~/.tmux.conf` に 54321 を置いて確認
      `set -g history-limit 54321` を置いて tmux を起動する。結果: `history-limit` は `54321` になる
      （個人設定が既定に勝つ）
- [x] 条件 7: 再ビルドした base イメージのコンテナで `/etc/tmux.conf` が存在し、
      → 検証: `…::test_dockerfile_installs_conf_as_etc_tmux_conf` + 実イメージの `/etc/tmux.conf` と `diff` して一致
      内容が `containers/base/tmux.conf` と一致する
- [x] 条件 8: 同コンテナで `tmux new-session -d` が**エラーなく起動する**
      → 検証: 実イメージで `tmux new-session -d` が成功
      （`tmux-256color` の terminfo 欠落などで起動不能にならない）
- [x] 条件 9: 起きてはいけないこと — `containers/base/tmux.conf` にキーバインドの変更
      → 検証: `…::test_conf_does_not_change_key_bindings`
      （`bind` / `unbind` / prefix 変更）が含まれない
- [x] 条件 10: 起きてはいけないこと — 既存の `tmux-first` / `tmux-clean` の動作が変わらない
      → 検証: `tmux-first` / `tmux-clean` を確認。tmux オプションは読まず `list-sessions` などの問い合わせのみ (変更なし)
      （両スクリプトは tmux オプションを読まず、`list-sessions` などの問い合わせのみを行う）
- [x] 条件 11: 利用者向けドキュメントに、スクロール・コピー・copy-mode の基本操作と
      → 検証: `docs/user/container-operations.md` の「tmux（ターミナル）の既定設定」節
      個人設定での上書き方法が載っている

## 非機能の条件

| 種類 | 条件 |
| --- | --- |
| 容量 | `history-limit 100000` は **pane あたり**の上限行数。tmux は行を実際に使った分だけ確保するため固定の先取りは無い。長大な出力を出し続ける pane で数十 MB 規模になり得る点は許容する（従来比 50 倍） |
| 権限 | `/etc/tmux.conf` は root 所有・全ユーザー読み取り可（`0644`）。コンテナ内の `ubuntu` から書き換えられる必要はない |
| 記録 | ログ出力の追加は無い |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない。devbase の CLI・`project.yml` スキーマ・環境変数は増減しない |
| データ | 変わらない。ボリューム・永続化対象に影響しない |
| 既存の振る舞い | tmux の既定値が 3 点変わる（`mouse` / `history-limit` / `focus-events`）。ホイール操作は端末のスクロールから tmux の copy-mode へ移る |

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | `containers/base/tmux.conf` を `/etc/tmux.conf` へ `COPY` | **採用** | イメージに焼くので全コンテナで即有効。個人の `~/.tmux.conf` が後勝ちで上書きできる。差分が 2 ファイルで完結する |
| B | `entrypoint.sh` が `~/.tmux.conf` を生成 | 不採用 | 利用者の個人設定を上書きしてしまう。生成しない条件分岐を入れても、一度作れば以後の既定値更新が届かない |
| C | `entrypoint.sh` から `tmux set-option -g …` を実行 | 不採用 | tmux サーバーが起動していない時点では失敗し、コンテナ起動後に利用者が始めたサーバーには効かない |
| D | `default-terminal` の行を書かない | 不採用 | tmux 3.6 の既定と同値（前提 4）だが、既定値は tmux の版で変わり得る。設定ファイル単体を読んで挙動が決まる状態を優先し、コメントで「現状は既定と同値」と明記する |
| E | `terminal-overrides` ではなく `terminal-features` を使う | 不採用 | tmux 3.2 以降の推奨は `terminal-features ",xterm-256color:RGB"` だが、`terminal-overrides` も 3.6 で有効なことを実測済み。issue の提案どおりの記述を採り、差分を最小にする |

## 不変条件

- `/etc/tmux.conf` は既定値の配布のみを行い、利用者の `~/.tmux.conf` を上書きしない。
- 設定はオプション値の変更に限り、キーバインドを変更しない（条件 9）。

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 公開インタフェース（API・イベント・コマンド） | なし | 変えない |
| データスキーマ | なし | 変えない |
| 端末操作 | ホイールが copy-mode に入るようになる | ペインをまたぐ選択は `Shift` + ドラッグで従来どおり端末側の選択になる。ドキュメントに記載する |

## 修正対象

- `containers/base/tmux.conf`（新規）
- `containers/base/Dockerfile`（`COPY` 行の追加）
- `tests/containers/test_tmux_conf.py`（新規）
- `docs/user/container-operations.md`（tmux の節を追加）
- `CHANGELOG.md`（Unreleased / Added）

## タスク分解

### Task 1: 既定設定ファイルを追加し、base イメージへ焼き込む

- **対象ファイル:** `containers/base/tmux.conf`、`containers/base/Dockerfile`、`tests/containers/test_tmux_conf.py`
- **変更内容:** issue の 5 行の設定を、意図を書いたコメント付きで `containers/base/tmux.conf` に置く。
  `Dockerfile` に `COPY --chmod=0644 tmux.conf /etc/tmux.conf` を、既存の tmux 関連 `COPY`
  （`tmux-first` / `tmux-clean`）の近くへ追加する。
- **満たす受け入れ条件:** 1〜6、9、10
- **進め方:** 先に `tests/containers/test_tmux_conf.py` を書いて落とす（設定ファイルが無い / 値が既定のまま）。
  ホストの `tmux -f <repo>/containers/base/tmux.conf` をテスト専用ソケットで起動して実効値を読むため、
  Docker に依存しない（`tests/containers/test_entrypoint_repos.py` と同じ方針）。tmux が無い環境では skip する。
  Dockerfile の `COPY` 行は静的検査（テキスト一致）で担保する。

### Task 2: 実イメージで反映を確認する

- **対象ファイル:** なし（検証のみ）
- **変更内容:** base イメージを再ビルドし、コンテナ内で `/etc/tmux.conf` の存在・内容一致と
  `tmux new-session -d` の起動可否、各オプションの実効値を確認する。
- **満たす受け入れ条件:** 7、8
- **進め方:** テスト駆動の対象外（イメージビルドを伴う手動確認）。手順と結果を PR 本文へ残す。

### Task 3: 利用者向けドキュメントと変更履歴

- **対象ファイル:** `docs/user/container-operations.md`、`CHANGELOG.md`
- **変更内容:** スクロール・コピー・copy-mode・履歴検索・`Shift` ドラッグの操作表と、
  `~/.tmux.conf` による上書き、再ビルドが要る旨を書く。CHANGELOG は Unreleased / Added に追記。
- **満たす受け入れ条件:** 11
- **進め方:** テスト駆動の対象外（文章）。記載したコマンドは Task 2 のコンテナで実行して確かめる。

## 影響範囲

- base を継承する全イメージ（前提 8）。設定ファイルが 1 つ増えるだけで、ビルド時間・イメージ容量への影響は無視できる。
- `containers/lfm` は対象外（tmux 未インストール）。
- 稼働中のコンテナには再ビルドまで反映されない（前提 9）。

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `default-terminal tmux-256color` の terminfo が無い環境で tmux が起動しない | `ubuntu:26.04` で `infocmp tmux-256color` の成功を確認済み（前提 5）。条件 8 で実イメージの起動も確認する |
| `mouse on` により、ホイールでの端末スクロールに慣れた利用者が戸惑う | ドキュメントに操作表を載せる（条件 11）。`Shift` + ドラッグで従来の端末選択に戻れることも明記する |
| `terminal-overrides` を `set -g` で書いて既定値 `linux*:AX@` を潰す | `set -ga` を使う。条件 5 で既定値が残っていることを検査する |
| 履歴 100000 行によるメモリ増 | pane あたりの上限で、実使用分しか確保しない。非機能の条件に許容範囲として記載 |

## 切り戻し手順

- `containers/base/tmux.conf` の削除と `Dockerfile` の `COPY` 行の revert、base イメージの再ビルドで完全に戻る。
- 永続データ・ボリューム・設定ファイルの移行を伴わないため、巻き戻しの制約は無い。
- 個別のコンテナだけ戻したい場合は、利用者が `~/.tmux.conf` に元の値
  （`set -g mouse off` / `set -g history-limit 2000` / `set -g focus-events off`）を書けばよい。

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest tests/containers/test_tmux_conf.py` |
| 全体テスト | `uv run pytest` |
| 静的解析 | `uv run ruff check --select=E9,F63,F7,F82 lib`（CI と同じ範囲） |
| 手動確認 | base イメージ再ビルド後のコンテナで `/etc/tmux.conf` の内容一致と `tmux new-session -d` の起動、各オプションの実効値 |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | イメージへ焼く資材は `containers/<image>/` 配下（既存の `tmux-first` / `entrypoint.sh` と同じ置き方）。ホスト側 Python (`lib/devbase/`) には置かない |
| コーディング規約 | `CONTRIBUTING.md`。設定ファイルのコメントは日本語で「なぜ」を書く（既存 Dockerfile の記述に合わせる） |
| テスト戦略 | tmux の実効値は実プロセスを起動して検査する（結合）。Dockerfile の配線は静的検査（単体）。Docker ビルドを伴う確認は自動テストに含めず手動確認とする |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、変更範囲の静的解析、実機での実効値確認 |
| 確認してから行う | 設定項目の追加・削除（issue の 5 行から増減させる場合） |
| 行わない | キーバインドの変更、`~/.tmux.conf` の永続化、`entrypoint.sh` の変更、#116 のプレースホルダ判定の修正 |

## 完了の定義

- [x] 受け入れ条件 1〜11 をすべて満たし、条件ごとに検証手段と結果が対応している
- [x] `uv run pytest` が通る（2026-08-28、1483 passed / exit=0）
- [x] base イメージの再ビルドと実コンテナでの確認結果を PR 本文に記載している
