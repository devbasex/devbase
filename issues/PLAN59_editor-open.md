# PLAN59: 再起動なしで VS Code だけ開き直す `devbase open`

対象 issue: devbasex/devbase#197

- ワークフローモード: `standard`
  - 根拠: 公開インタフェース（CLI のコマンド `open` と `devbase list` の TUI のメニュー項目）を足す。
    `cli.py`・`commands/container.py`・`tui/actions_project.py`・`bin/devbase`・補完にまたがる

## 目的

- `devbase up` で開いた VS Code の窓を手で閉じたあと、コンテナを再作成・再起動せずに同じ窓を出し直せるようにする
- `devbase list` の TUI で起動中のプロジェクトを選んだとき、Enter 1 回目で窓を出せるようにする

## 前提

- 前提 1: コマンド名は `open` とする（issue の第 1 案。`editor` / `code` は採らない）。トップレベル `devbase open`、
  `devbase project open [name]`、`devbase container open` の 3 つの入口を持つ。トップレベルの `o` 始まりの
  既存コマンドは無いため、前方一致の短縮 `devbase o` も一意に `open` へ解決する
- 前提 2: 「起動している」の判定は、**dev サービスのインスタンスが 1 つ以上動いていること**で行う。
  判定は 2 段に分ける。

  | 動いているインスタンス | 扱い |
  | --- | --- |
  | 0 個 | 停止中として `up` へ委譲する |
  | 1 個以上 | index が動いているものに含まれるかを検査する |

  issue の案（「コンテナ名が解決できないこと」）のままでは誤る場面がある。scale 1 で動いているプロジェクトへ
  `--open-index 2` を渡すと、「停止中」と判定して `up` を走らせてしまう。2 段に分けるのは、issue の
  「index の上限は動いているコンテナの数から決める」「解決できない index はエラーにする」を両立させる解釈である
- 前提 3: 停止中のときは `devbase up --open [--open-index N]` と同じ動きにする。`up` 側の既存の index の扱い
  （範囲外は警告して 1 へ落とす）は変えない
- 前提 4: エディタを開く処理（`opener.open_editor`）の既存の判断（非 TTY はスキップ、SSH ではコマンドを
  提示、`code` が無ければスキップ）は変えない。`open` が変えるのは「開くかどうか」の判定
  （`DEVBASE_OPEN_EDITOR` / `project.yml` の `open_editor` を見ない）だけである
- 前提 5: 起動中の判定・開く処理は、`up` と同じく `--context` と `project.local.yml` の docker context に従う

## 対象範囲

含む:

- コマンド `open`（トップレベル・`project`・`container`）と、その `--open-index N` / `--context NAME`
- `project open [name]` とトップレベル `open [name]` のプロジェクト名の解決（`bin/devbase` の name 解決と
  Python 側の chdir）
- `devbase list` の TUI の起動中メニューの先頭に「エディタを開く (open)」を置くこと
- シェル補完（bash / zsh）と利用者向けの CLI リファレンス、CHANGELOG

含まない:

- `devbase up` の `--open` / `--no-open` / `--open-index` と `DEVBASE_OPEN_EDITOR` の挙動の変更
- `opener.open_editor` の起動方針（launch / print_command / skip）の変更
- 停止中の行（TUI で選ぶと直接 `up` する行）のメニュー追加。停止中の行はこれまでどおり直接 `up` する
- 複数インスタンスをまとめて開くこと（1 回に開くのは 1 つ）
- 新しい型・永続データ・画面の追加（そのためクラス図・ER 図・画面遷移図を作らない）

## 受け入れ条件

コマンド:

- [ ] 1. 前提: プロジェクトの dev が動いている
      操作: `devbase open` を実行する
      結果: `opener.open_editor` が 1 回だけ呼ばれる。compose up・compose down・ボリューム/ネットワークの作成・
      compose の再生成・`deploy` フック・pre-up チェック・自動スナップショットは、どれも呼ばれない。終了コード 0
- [ ] 2. 前提: dev が動いており、`DEVBASE_OPEN_EDITOR=0`（または `project.yml` の `open_editor: false`）
      操作: `devbase open` を実行する
      結果: `opener.open_editor` が呼ばれる（自動オープンの無効化は `open` に効かない）
- [ ] 3. 前提: dev が動いていない（動いているインスタンスが 0 個）
      操作: `devbase open` を実行する
      結果: `cmd_up` が `open_editor=True` で 1 回呼ばれ、`open` 自身は `opener.open_editor` を呼ばない。
      終了コードは `cmd_up` の戻り値
- [ ] 4. 前提: dev が動いていない、`DEVBASE_OPEN_EDITOR=0`
      操作: `devbase open` を実行する
      結果: 起動後に窓が開く（`cmd_up` へ `open_editor=True` が渡る）
- [ ] 5. 前提: dev-1 だけが動いている
      操作: `devbase open --open-index 2` を実行する
      結果: `cmd_up` も `opener.open_editor` も呼ばれず、動いている index（`1`）を示すエラーを出して終了コード 1
- [ ] 6. 前提: dev-1 と dev-2 が動いている（`project.yml` の `scale` は 1。`devbase scale` でオンライン変更した状態）
      操作: `devbase open --open-index 2` を実行する
      結果: `opener.open_editor` が `index=2` で呼ばれる（上限は `project.yml` ではなく動いている数から決まる）
- [ ] 7. `--open-index` に 0 以下を渡すと、`cmd_up` も `opener.open_editor` も呼ばれず終了コード 1
- [ ] 8. `--open-index` を省き env `DEVBASE_OPEN_INDEX=2` があるとき、起動中の経路は `index=2` を使う
- [ ] 9. `devbase project open <name>` を別ディレクトリから実行すると、`<name>` のプロジェクトを対象にする。
      解決は `devbase project up <name>` と同じ。トップレベル `devbase open <name>` も同じ
- [ ] 10. `devbase open --context NAME` は、2 か所の両方に `NAME` を使う。起動中の判定の `docker ps` と、
      `opener.open_editor` の `docker_context` である
- [ ] 11. `open` は `--open` / `--no-open` を受け付けない（argparse の usage エラー、終了コード 2）
- [ ] 19. 前提: dev が動いており、端末が非 TTY（または `code` が無い）
      操作: `devbase open` を実行する
      結果: `opener.open_editor` が `skip` を返し、終了コード 1。SSH セッションでコマンドを提示した場合（`print_command`）は 0
- [ ] 20. 前提: 起動中の判定の `docker ps` が 0 以外で終わる（daemon に届かないなど）
      操作: `devbase open` を実行する
      結果: `cmd_up` を呼ばず、状態を取得できない旨を出して終了コード 1

TUI:

- [ ] 12. 起動中の行のサブメニューの先頭の項目が `open` である
- [ ] 13. 起動中の行のサブメニューで `open` を選ぶと、`dispatch_lifecycle("open", <name>, ...)` が呼ばれる
- [ ] 14. `open` の実行後はトップ一覧へ戻らず、同じサブメニューに留まる（`_BACK_TO_TOP_OPS` に含まれない）

退行しないこと:

- [ ] 15. `devbase up` の自動オープン（`--open` / `--no-open` / `--open-index` / `DEVBASE_OPEN_EDITOR`）の既存テストが
      変更なしで通る
- [ ] 16. `devbase l` は引き続き `login` に、`devbase project p` は引き続き `ps` に解決する
- [ ] 17. bash / zsh の補完でトップレベル・`project`・`container` の候補に `open` が出る
- [ ] 18. 全体テスト（`uv run pytest`）が通る

（19・20 は設計で決めた失敗の形を条件へ戻したもの。番号は追記順）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 起動中の経路で docker を呼ぶのは、起動中の判定の `docker ps` 1 回と、`opener.open_editor` 内の既存の呼び出しだけ（`up` のパイプラインを通らない） |
| 運用・保守性 | 停止中から `up` へ委譲するときは、その旨を info ログに 1 行出す（利用者が「なぜ起動が走ったか」を読める） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 足す: `devbase open [name]` / `project open [name]` / `container open`（`--open-index N` / `--context NAME`）。既存は変えない |
| データ | 変わらない |
| 既存の振る舞い | `devbase list` の起動中サブメニューの既定のハイライトが「再起動 (up)」から「エディタを開く (open)」へ変わる。Enter 連打で再起動していた利用者は 1 つ下を選ぶことになる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest` |
| 静的解析 | `uv run ruff check lib tests`（導入されていれば） |
| 手動確認 | 起動中のプロジェクトで窓を閉じ、`devbase open` で同じ窓が開き、`docker ps` の dev の `CreatedAt` / `Status` が変わらないことを見る。停止中のプロジェクトで `devbase open` が起動から窓を開くことを見る。`devbase list` で起動中の行を選び、先頭が `open` であることを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | CLI の登録は `lib/devbase/cli.py`、処理は `lib/devbase/commands/container.py`（lifecycle のサブコマンドの置き場）、TUI は `lib/devbase/tui/actions_project.py`。`bin/devbase` の name 解決の 2 つのリストと `cli.py` の対の注記に従う |
| コーディング規約 | 既存のモジュール関数の並びに足す。コメント・ログは日本語、PLAN 番号を引く既存の書き方に合わせる |
| テスト戦略 | 単体: `cmd_open` の分岐（起動中 / 停止中 / index 範囲外）を docker と `cmd_up` を差し替えて見る。CLI: parser と name 解決と補完。TUI: メニューの並びとハンドラ |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`cli.py` と `bin/devbase` と補完の 3 か所の同期 |
| 確認してから行う | `opener.open_editor` の既存の判断の変更、`up` の挙動の変更 |
| 行わない | `up` の自動オープンの置き換え、依頼範囲外のリファクタリング |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| なし | | |

## 用語

| 用語 | 意味 |
| --- | --- |
| 窓 | dev コンテナへ Dev Containers 拡張で接続した VS Code のウィンドウ |
| 動いているインスタンス | `docker ps`（`-a` なし）に現れ、Compose のプロジェクトのラベルがこのプロジェクトで、サービスのラベルが `{dev}-{index}` のコンテナ |
| index | 開く dev インスタンスの番号（`dev-1` の `1`）。`--open-index N`、未指定なら env `DEVBASE_OPEN_INDEX`、それも無ければ 1 |

## 依頼（原文）

> /goal /ndf:development-workflow https://github.com/devbasex/devbase/issues/197

issue #197 の本文（抜粋。原文のまま）:

> **一度閉じた VS Code の窓を、コンテナを止めずに開き直す手段が無い。**

> | やること | 期待 |
> | --- | --- |
> | 起動中のプロジェクトでコマンドを打つ | コンテナに一切触らず、dev コンテナへ接続した窓を開く |
> | 停止中のプロジェクトで打つ | `up` を実行して起動し、そのまま窓を開く（`devbase up` と同じ結果になる） |
> | `devbase list` の TUI | 起動中メニューの**先頭**（再起動 / 停止 / ログインの上）から同じ操作を選べる |

> | 論点 | 案 |
> | --- | --- |
> | **既定のハイライトが変わる** | （略）**依頼どおり先頭に置くが、`:31` のコメントもあわせて書き替える** |
> | `DEVBASE_OPEN_EDITOR=0` の端末での扱い | **開く。** 明示のコマンド・明示のメニュー選択は意思表示であり、`up` のときの自動オープンの可否とは別に扱う（`opener.is_open_enabled` を見ない） |
> | index の上限 | `project.yml` の `scale` ではなく**動いているコンテナの数**から決める。（略）そこで解決できない index はエラーにする |
> | 停止中のときの動き | **`up` を実行する。** コンテナ名が解決できないことをもって「起動していない」と判定し、`cmd_up` へ委譲して起動から窓を開くところまで通す。 |

> `devbase up` の `--open` / `--no-open` / `--open-index` は残す。**`open` の新設は `up` の自動オープンを置き換えるものではない**
