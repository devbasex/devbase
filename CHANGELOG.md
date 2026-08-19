# Changelog

本プロジェクトの変更履歴を [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) の形式に沿って記録します。バージョン番号は [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### Added
- **tmux 内では `VSCODE_IPC_HOOK_CLI` が古くても VS Code を自動で開く**ようにしました。
  tmux サーバーはセッション作成時の環境変数を保持し続けますが、`update-environment` に
  登録した変数は attach のたびに更新されるため、**ペインのシェルは古い値・tmux の
  セッション環境は新しい値**という状態が日常的に起きます。ソケットへ接続できなかった
  場合に `tmux show-environment` を参照して生きた値を拾い直し、起動する `code` の
  環境変数にも反映します (変数を差し替えないと `code` 自身が古いソケットへ繋ぎに行って
  失敗するため)。これまではシェル側のプロンプトフックを入れていないと毎回
  「手元で実行するコマンドの提示」へ degrade していました。tmux 外の挙動は変わりません。
- **`tmux-first` コマンドを base イメージへ追加**しました。VS Code のウィンドウが
  異常終了 (クラッシュ / ホスト再起動 / 接続断) すると、VS Code サーバー側に pty が
  取り残され、tmux クライアントだけがセッションへ繋がったまま残ります。統合ターミナルの
  起動スクリプトはそのセッションを「使用中」と判定するため、`<repo>-1` に戻れず
  `<repo>-2`, `<repo>-3` ... と新しいセッションが増え続けます。`tmux-first` は放置された
  クライアントを切断し、一番若い番号のセッションへ現在の端末を切り替えます。切断対象は
  最終操作から `TMUX_FIRST_IDLE` 秒 (未設定時のみ既定 300、0 以上の整数のみ有効) 以上経過した
  クライアントだけで、使用中の端末は残します (`-f` で全件切断)。実行元の端末を巻き添えに
  しないよう自分自身のクライアントは必ず除外し、hook 経由など実行元を特定できない起動では
  `-f` を付けていても切断・切り替えのどちらも行わず、手動で切り替えるコマンドだけを案内します
  (実行元が特定できない状態で切り替えると、別の利用者の端末を切り替えてしまうため)。
  `tmux-first -n` で切り替えずに対象を確認でき、`tmux1` の短縮コマンド (symlink) も用意しています。
- **`tmux-clean` コマンドを base イメージへ追加**しました。`tmux-first` で若い番号の
  セッションへ戻したあと、置き去りになった `<repo>-<数字>` のセッションを削除します。
  既定では安全側に倒し、keeper (tmux 内なら現在のセッション、外なら最小番号) と、
  アタッチ中のセッション、シェル以外を実行中のセッション、`&` で起動したバックグラウンド
  ジョブが残っているセッションは残します。アタッチ数や pane の状態を tmux から取得できなかった
  セッションも、実行中かどうか判断できないため残します (fail-closed)。`-n` で削除対象と除外理由を
  確認でき、`-f` ですべて削除します。削除に失敗したセッションがあれば標準エラーへ出力し、終了ステータス 1 で
  知らせます。`tmuxc` の短縮コマンド (symlink) も用意しています。
- **bi-tools プロジェクト用コンテナ (`containers/bi-tools`)** を追加しました。
  `devbase-base` に dbt-core + dbt-bigquery（1.12 系 / Python 3.11）と Lightdash CLI
  (`@lightdash/cli`) を追加し、BigQuery 上の共有 dbt プロジェクト（`dbt build` / `test`）と
  `lightdash deploy` / `preview` をホストから実行できます。Evidence / Superset は各自の
  docker-compose・プロジェクトローカル npm で動くためホスト CLI は追加していません。
- **`devbase up` 後に dev コンテナへ接続した VS Code を自動オープン**できるように
  しました (PLAN31_3)。`DEVBASE_OPEN_EDITOR=1`（既定 OFF）で有効化、`devbase up
  --open` / `--no-open` で都度上書き。`/work/$GIT_REPO` をワークスペースとして開きます。
  ローカル / WSL（Windows 側）/ VS Code Remote-SSH 統合ターミナル（手元クライアント側）
  を自動判別し、素の SSH では手元で実行するコマンドを提示します。エディタは
  `DEVBASE_EDITOR`（既定 `code`）で変更可能。詳細: `docs/user/environment-variables.md`。
- **`devbase build` に `--expires[=DAYS]` を追加**しました (i07)。イメージ作成日が
  DAYS 日（既定 7、`DEVBASE_IMAGE_MAX_AGE_DAYS` で上書き可）以上のときのみ no-cache で
  再ビルドし、未満なら再ビルドしません（既存イメージを使用）。親イメージ（`FROM devbase-*`）の
  作成日は独立して判定します。`devbase build` の `--no-cache` も明示フラグとして整理しました。
- **外部リポジトリ連携プロジェクト向けドキュメント (`docs/plugin-dev/repo-backed-projects.md`)**
  を追加しました。アプリ本体のリポジトリを共有 work ボリュームへ取り込み、複数コンテナで動かす
  プロジェクトのための `pre-up` populate パターン（初回のみ populate し、2 回目以降はコンテナ側の
  ソース・環境ファイルを上書きしない冪等スキップ）と、その設計意図・更新運用・チェックリストを
  解説しています。あわせて、本パターンが `CONTAINER_SCALE=1` 前提である理由（`pre-up` は
  インデックスなしで 1 回しか実行されず、scale 生成で `/work` が差し替わるのは dev サービス
  のみ）と、同一リポジトリの複数インスタンス分離が現行実装では未サポートである点、
  work ボリュームが全プロジェクト共有のグローバル external ボリュームであることを踏まえた
  安全な再 populate 手順（ボリュームごとではなく `/work/<GIT_REPO>` サブディレクトリを削除）
  も明記しています。

### Changed
- **CLI リファレンス (`docs/user/cli-reference.md`) をコマンドグループ別ディレクトリ
  (`docs/user/cli-reference/`) に分割**しました。目次 (`README.md`) とトップレベル / project /
  env / plugin / snapshot の各ファイルに再編し、1 ファイルあたりの分量を抑えて目的のコマンドへ
  辿りやすくしました。ルート `README.md`（3 箇所）を含む他ドキュメントからの参照リンクも
  新パス (`docs/user/cli-reference/README.md`) へ更新しています。
- **コンテナ操作ガイドの work ボリューム記述を実装に合わせて訂正**しました。
  `docs/user/container-operations.md` のボリューム表が `{project}_work_{index}` /
  「各コンテナ専用」となっていましたが、実際は project 接頭辞の付かない external ボリューム
  `devbase_work_{index}` で、同じ index を使う限り**別プロジェクトからも同じ実体**を参照します。
  表記を訂正し、`docker volume rm` が他プロジェクトの作業ファイルを巻き添えにする旨の注意も
  追記しました。
- **`build` / `rebuild` / `up` の再ビルド仕様を統一**しました (i07)。キャッシュの
  扱いを 3 モード（既定=キャッシュビルド / `--no-cache`=無条件 no-cache / `--expires=N`=
  期限切れ時のみ no-cache・期限内は再ビルドしない）に整理し、`devbase rebuild` を
  `devbase build --expires=7` のシノニムに、`devbase up` の自動準備をその `rebuild` 相当に
  集約しました。`devbase rebuild` は従来の素の `docker compose build --no-cache` をやめ、
  devbase-base の 2 段ビルドと期限判定（期限内はスキップ）を行うようになりました。`devbase up`
  の「7 日未満は再ビルドしない」挙動は従来どおり維持されます。
- **`devbase up` の自動再ビルドで base イメージの日付判定を分離**しました。
  プロジェクトイメージが閾値（既定 7 日）超過で `--no-cache` 再ビルドされる際、
  ベースの作成日を独立して判定し、ベースが閾値内（新しい）であればベースを no-cache で
  作り直さず、プロジェクトイメージのみ no-cache で再ビルドします。ベースが古い、または
  判定できない場合はベースも含めて no-cache で再ビルドします。
- **シェル有効化を `bin/rc` の source に統一**しました (PLAN31_1)。`devbase init` 後に
  いま開いているシェルへ devbase（PATH / 補完）を即時適用するには
  `. ~/devbase/bin/rc`（= `source ~/devbase/bin/rc`）を使います。`bin/rc` は自身の
  場所から `DEVBASE_ROOT` を解決するため、Python（uv）起動もコマンド置換 `$(...)`
  も不要になり、ワンライナーは
  `curl -fsSL https://dl.basex.jp/i | bash && . ~/devbase/bin/rc` で現在のシェルまで
  有効化できます。

### Removed
- **`devbase shell-rc` サブコマンドを廃止**しました (PLAN31_1, 破壊的変更)。rc ファイル
  パスを print して `source "$(devbase shell-rc)"` する方式は、上記の `. bin/rc` に
  置き換えました。`source "$(devbase shell-rc)"` を使っているスクリプトは
  `. <DEVBASE_ROOT>/bin/rc` に書き換えてください。

### Fixed
- **tmux 追随フックのサンプル（`docs/user/environment-variables.md`）が孤児ソケットを
  検出できない**問題を修正しました。`test -S` はファイルの種別しか見ないため、VS Code の
  異常終了で残った listen していないソケットも「生きている」と判定し、フックが
  `tmux show-environment` からの拾い直しを早期 return でスキップしていました。
  結果として `devbase up --open` が毎回「手元で実行するコマンドの提示」へ degrade します。
  `nc -U -w 1 <sock> </dev/null` による接続確認を後段に足しました（macOS の `nc` は
  `-z` を付けると Unix ドメインソケットで誤判定するため付けません）。`-S` を前段に
  残しているので、ソケットファイルが無い一般ケースでは従来どおり追加のプロセスは起きません。
- **VS Code の異常終了後に残った IPC ソケットで `devbase up --open` が
  `ECONNREFUSED` で失敗する問題を修正**しました。`VSCODE_IPC_HOOK_CLI` が指す
  ソケットの死に方には「ファイルごと消えている」ほかに「ファイルは残っているが
  listen しているプロセスが居ない」の 2 通りがあります。後者は VS Code の
  クラッシュ・強制終了・OS 再起動で後始末されなかった場合に `$TMPDIR` へ孤児として
  残り、`ls` では生きているものと区別が付きません。従来の `_ipc_socket_alive` は
  ファイルの実在だけを見ていたため後者を「生きている」と誤判定し、`code` が
  `connect ECONNREFUSED .../vscode-ipc-<uuid>.sock` で失敗していました。
  **実際に connect して**生死を判定するようにし (タイムアウト 0.5 秒)、死んでいる
  場合は従来どおり警告のうえ degrade します。
- **tmux / screen 経由のターミナルで `devbase up --open` が無言で失敗する問題を修正**
  しました。VS Code はウィンドウごとに IPC ソケット (`$TMPDIR/vscode-ipc-<uuid>.sock`)
  を作り直しますが、tmux サーバーはセッション作成時の環境変数を保持し続けるため、
  既存セッションに再アタッチした端末では `VSCODE_IPC_HOOK_CLI` が**消えたソケットを
  指したまま**になります。従来は変数の有無だけで VS Code 統合ターミナルと判定して
  いたため、`code` が死んだソケットへ接続を試みて何も起きず、`_launch` が stderr を
  捨てていたのでエラーも出ませんでした。ソケットの実在を確認したうえで判定し
  (`_ipc_socket_alive`)、古い場合は理由を warning に出して「手元で実行するコマンドの
  提示」へ degrade します。あわせて `_launch` は stderr を握り潰さないようにしました。
  tmux 側の追随設定は `docs/user/environment-variables.md` の
  「tmux / screen 経由で使う場合」を参照してください。

### Added
- **ワンライナー installer (`install.sh`) を新設**しました (PLAN31_1)。
  `curl -fsSL https://dl.basex.jp/i | bash`
  で `~/devbase` への clone（既存なら `git pull --ff-only`）と `devbase init` まで
  自動完了します（uv の自動導入・PATH/補完の登録・`plugins.yml` 生成を含む）。
  - 配置先 / clone 元 / ref を `DEVBASE_INSTALL_DIR` / `DEVBASE_INSTALL_REPO` /
    `DEVBASE_INSTALL_REF` で上書きできます。`DEVBASE_INSTALL_REF` は branch/tag 名
    として妥当な文字のみ許可し、オプション注入を防ぎます。
  - 非 TTY (`curl | bash`) で対話プロンプトを出しません。`env init` は対話必須のため
    実行せず、完了後に次の手順（`shell-rc` 再読み込み / `plugin install` / `env init`
    / `build` / `up` / `login`）を案内します。
  - 配置先が devbase 以外の非空ディレクトリの場合は誤上書きを避けて中止します。
  - CI に `install.sh` の ShellCheck (`severity=error`) を追加しました。
- **`devbase list` の対話選択を TUI 化**しました。`questionary` 導入により、↑↓ の
  矢印キーで行移動、文字入力でプロジェクト名のインクリメンタル絞り込みができます
  (全項目に通し番号を表示)。Enter で決定、Ctrl-C で中止します。
  - **選択行が起動中 (running) の場合**は「再起動 (up) / 再ビルド (rebuild --no-cache) /
    停止 (down)」を選ぶサブメニューを表示します。それ以外 (stopped / unknown) は
    従来どおり `up` を起動します。
  - 非 TTY（パイプ/CI/リダイレクト）では従来どおりプレーンな一覧表示にフォールバック
    し、`questionary` 未導入環境では番号入力方式にフォールバックします。
  - 入力ライブラリを `simple-term-menu` から `questionary` (prompt_toolkit ベース) へ
    移行し、↑長押し時にスクロールが取りこぼされて遅くなる問題を解消しました。
- **`devbase rebuild` コマンドを新設**しました。`docker compose build --no-cache` 相当で、
  キャッシュを無効化してプロジェクト (compose) イメージを作り直します。`devbase rebuild
  [name]` / `devbase project rebuild [name]` として任意のディレクトリから利用できます
  (`devbase list` の running サブメニューからも起動できます)。なお `devbase-base` まで
  作り直す 2 段ビルドは従来どおり `devbase build --no-cache` を使用してください。
- **`devbase project` サブコマンド群を新設**しました (PLAN06)。CWD に依存せずプロジェクト名でコンテナ操作ができます。
  - `devbase project up/down/ps/logs/scale [name]` で、任意のディレクトリから `$DEVBASE_ROOT/projects/<name>` を対象に操作できます。名前解決はラッパー (`bin/devbase`) が対象ディレクトリへ `cd` してから実行するため、シェル実装の `build` を含む全操作が名前指定で成立します（呼び出し元シェルの作業ディレクトリは変わりません）。存在しない名前はエラーになり候補が提示されます。
  - `devbase project list` で `$DEVBASE_ROOT/projects/` 配下を `NAME` / `PLUGIN` / `STATUS` の一覧表示します。`PLUGIN` 列はシンボリックリンク先から解決するため、PLAN04 の同名衝突 suffix（例 `carmo.takemi`）が付いていても正しいプラグイン名を表示します。**TTY ではデフォルトで対話選択**になり、一覧から番号で選んだプロジェクトを `project up` で起動します。`--no-interactive`（`--plain` / `-P`）で一覧表示のみに切り替えられ、パイプ・リダイレクト・CI などの非 TTY 環境では自動的に一覧表示へフォールバックします（`--interactive` / `-i` は後方互換として引き続き受け付けます）。
  - トップレベルシノニム `devbase up/down/ps/scale [name]` / `devbase build [image]` / `devbase login [index]` / `devbase list` を整備しました（`logs` はシノニムを持たず `devbase project logs` のみ）。
  - bash / zsh のシェル補完に `project` グループとプロジェクト名補完（`$DEVBASE_ROOT/projects/` 配下を列挙）を追加しました。
  - 利用者向けドキュメント `docs/user/cli-reference.md`（現 [`docs/user/cli-reference/`](docs/user/cli-reference/README.md)） / [`docs/user/container-operations.md`](docs/user/container-operations.md) を `project` 体系に更新しました。
- `devbase env export` / `devbase env import` で **S3 URI (`s3://bucket/key`) を入出力先として指定**できるようになりました (PLAN03-1 PR3)。
  - 既定でオブジェクト単位の SSE (`aws:kms` または `AES256`) を強制し、export 時はバケット側のデフォルト暗号化も `GetBucketEncryption` で事前確認します。
  - 暗号化が未設定のバケットへ export する場合は `--unsafe-allow-unencrypted-bucket` の明示が必要です (オブジェクト単位の SSE はこのフラグに関係なく常に付与されます)。
  - SSE 種別 (`DEVBASE_S3_SSE`) / KMS 鍵 (`DEVBASE_S3_SSE_KMS_KEY_ID`) / エンドポイント (`DEVBASE_S3_ENDPOINT_URL`) / リージョン (`DEVBASE_S3_REGION`) は環境変数で上書きできます。MinIO / LocalStack の利用も可能です。
  - `boto3` は main dependency として常に同梱されます (S3 を使わないユーザにも 25MB 程度入りますが、引数検出や lazy install の複雑さを避けるトレードオフです)。
- `devbase env export` / `devbase env import` の利用者向けドキュメント [`docs/user/env-export-import.md`](docs/user/env-export-import.md) を新設しました (PLAN03-1 PR5)。
  - バンドル構造、age 暗号化 (recipient / identity / passphrase)、入出力先 (local / stdio / S3)、merge モード比較、`.env.sources.yml` の扱い、2 フェーズ書き込みとバックアップ、典型ワークフロー、トラブルシューティングまでを網羅します。
  - README と環境変数ガイドからのリンクも追加しました。

### Changed
- **`devbase container` グループを非推奨化**しました (PLAN06)。`devbase container <sub>` は `devbase project <sub>` のエイリアスとして当面動作しますが、実行時に非推奨警告を表示します（移行期間後のリリースで削除予定）。`[name]` 指定や `list` などの新機能は `project` 側のみで提供されます。トップレベルショートカット (`devbase up` 等) の転送先も `container` から `project` へ変更しました。
- `gs://` (GCS) スキームは **PLAN03-1 PR4 廃案** により対応しません。指定すると明示的なエラーメッセージで失敗します (旧: "未実装")。
- `lib/devbase/env/` 配下の export / import モジュールをリファクタリングしました (PLAN03-1 PR5)。公開 API (`ExportOptions`, `ImportOptions`, `export`, `import_bundle`) に互換性のない変更はありません。
  - export / import で重複していた passphrase 読み取り / 既定鍵 fallback / セキュアな bytes 書き込みを `io_common.py` に集約。
  - 711 行に肥大化していた `io_import.py` を「orchestration (`io_import.py`, 209 行)」「merge 計画 (`_import_merge.py`)」「2 フェーズ atomic 書き込み + backup GC (`_import_atomic.py`)」の 3 モジュールに分割。

## [2.2.0] - 2026-04-20

OSS 化に伴う初回リリース。devbase は本バージョンより `devbasex` Organization 配下で公開されます。

### Added
- MIT License
- プラグインマーケットの概念導入。任意のレジストリを `devbase plugin repo add` で追加可能。
- 公式サンプルレジストリ `devbasex/devbase-samples`（adminer / ai-plugins / devbase を収録）。
- PHP 8.5 ベースの開発コンテナ（`containers/php85`）。
- スナップショットの差分回数ベースの世代管理。
- `GIT_HOST` 環境変数による Git ホストの切り替えサポート。

### Changed
- `DEFAULT_OFFICIAL_REGISTRY` を `devbasex/devbase-samples.git` に変更。
- README / docs 内のリポジトリ参照を `devbasex/devbase` に更新。
- ドキュメント体系を `docs/user`, `docs/plugin-dev`, `docs/developer` に再編。

### Removed
- 「公式レジストリ」固定の概念を廃止。各レジストリは対等な扱いとなる。

[Unreleased]: https://github.com/devbasex/devbase/compare/v2.2.0...HEAD
[2.2.0]: https://github.com/devbasex/devbase/releases/tag/v2.2.0
