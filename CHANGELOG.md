# Changelog

本プロジェクトの変更履歴を [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) の形式に沿って記録します。バージョン番号は [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

## [3.2.1] - 2026-09-04

Kiro CLI のログイン状態をコンテナ再作成後も保持し、tmux の履歴上で選択した文字列を
`Ctrl+C` でコピーできるようにしました。

**この版を反映するには、ベースイメージの再ビルドとコンテナの再作成が要ります。**

```bash
devbase build base --no-cache
devbase up <プロジェクト名>
```

### Fixed

- **Kiro CLI 2.x の認証状態と実行データをアカウントグループ単位で永続化しました。**
  `~/.local/share/kiro-cli` は `/persistent/group/.local/share/kiro-cli` へのシンボリックリンクに
  なります。初回適用時にホーム側へ既存データがある場合は、グループ側の保存先が空のときだけ
  コピーするため、再ログイン済みの状態を失わず、異なるAWSアカウント間でも混ざりません。
- **tmux の履歴上でドラッグ選択した文字列を `Ctrl+C` でコピーできるようにしました。**
  マウスホイールで履歴へ入り、ボタンを離しても選択を保持します。`Ctrl+C` はOSC 52経由で
  端末のクリップボードへコピーしてcopy-modeを終了します。`Ctrl+Home` はVS CodeやWindows側の
  操作と競合するため割り当てません。

## [3.2.0] - 2026-09-03

`gemini` が常に Vertex AI 経由になっていたのをやめ、認証方式を環境で選べるようにしました。

**この版を反映するには、ベースイメージの再ビルドとコンテナの再作成が要ります。**

```bash
devbase build base --no-cache
devbase up <プロジェクト名>
```

### Changed
- **`gemini` の Vertex AI 強制をやめました。** `containers/base/Dockerfile` が `.bashrc` へ
  書き込む alias が、全コンテナで `GOOGLE_GENAI_USE_VERTEXAI=true` を無条件に前置していました。
  Vertex AI を使わないプロジェクト（`GOOGLE_CLOUD_PROJECT` を空にしたもの）でも Vertex 経路へ
  倒れ、gemini が使えなくなります。

  認証方式は環境変数 `GOOGLE_GENAI_USE_VERTEXAI` で選びます。未設定・空なら
  `~/.gemini/settings.json` の `selectedType`（OAuth など）に従います。

  **`GOOGLE_CLOUD_PROJECT` の有無では判定しません。** これは gcloud や BigQuery でも使う
  プロジェクト指定であって認証方式の opt-in ではなく、OAuth を使いながら別の目的で設定して
  いる場合に意図せず Vertex へ倒れるためです。

  > **移行が要ります。** これまで Vertex AI を使っていた環境は、alias が補っていた値を
  > 環境の側へ移してください。移さないと OAuth 側へ倒れます。
  >
  > ```bash
  > devbase env set GOOGLE_GENAI_USE_VERTEXAI=true
  > ```
  >
  > Vertex AI を使わないプロジェクトは `projects/<name>/env` に
  > `GOOGLE_GENAI_USE_VERTEXAI=` を書いて共通の値を打ち消します。

- **AI CLI の起動定義を `containers/base/ai-cli-aliases.sh` へ出しました。** `~/.bashrc` へ
  直接書き出す形をやめ、`COPY` する資産にしています。Docker を起動せずに振る舞いを固定する
  テストを 37 件追加しました。定義の場所はコンテナ内の `/etc/devbase/ai-cli-aliases.sh` です。

### Fixed
- **`claudb` が `--dangerously-skip-permissions` を 2 度渡していた**のを直しました。alias の
  展開で `claude` の alias まで展開されていたためです。`command` を挟んで解消しました。
- **起動定義の `"$@"` を落としました。** alias の `"$@"` は alias の引数ではなくシェルの
  位置パラメータへ展開されるため、引数を渡す働きをしていませんでした。引数の渡り方は変わりません。

## [3.1.0] - 2026-09-02

`devbase up` でコンテナを作り直しても VS Code Server が残るようになり、
アカウントグループを分けたときに他社の GCP 鍵がコンテナへ渡らなくなりました。
`devbase build <image>` の単体ビルドも動くようになっています。

`FROM devbase-*` を使うプロジェクトは、VS Code Server の永続化と Antigravity CLI の
追加を反映するために**ベースイメージの再ビルド**が要ります。

```bash
devbase build base --no-cache
```

### Added
- **VS Code Server をコンテナ再作成をまたいで保つ**ようにしました (PLAN36)。
  `~/.vscode-server` はこれまでコンテナ層 (揮発) にあったため、`devbase up` で
  コンテナを作り直すたびに VS Code の attach で **215MB の再ダウンロード**
  (約 55 秒) と拡張機能の再インストールが走っていました。コンテナ 1 つにつき 1 本の
  named volume `devbase_vscode_<project>_<index>` を `~/.vscode-server` へ
  マウントし、本体・拡張機能・接続トークンをプロジェクトの寿命で保ちます。

  共有せずコンテナ単位にするのは、VS Code Server が「1 マシン 1 セット」の状態
  (`data/Machine/.connection-token-<commit>` など) を持つためです。名前に
  プロジェクト名とインスタンス番号を含めるので、`scale > 1` の同時 attach でも
  別プロジェクトの同時起動でも状態が混ざりません。

  反映には**ベースイメージの再ビルド**が要ります (`devbase container build --no-cache`)。
  空のボリュームは root 所有で作られるため、entrypoint が所有者を初期化します。
  VS Code 本体の更新 (`commit` ハッシュの変更) 時は従来どおり取得が走ります。
  ボリュームは `devbase down` でも残るので、使わなくなったプロジェクトの分は
  [トラブルシューティング](docs/user/troubleshooting.md#vs-code-server-のボリュームが溜まっている)
  の手順で削除してください。

- **Antigravity CLI (`agy`) をベースイメージへ追加**しました。Google の AI コーディング
  エージェントを、既存の `claude` / `gemini` / `codex` / `kiro` と同じくコンテナ内から
  すぐ使えます。エイリアス `agy` は確認プロンプトを省く
  `--dangerously-skip-permissions` 付き (Gemini CLI の `--yolo` に相当するフラグは
  Antigravity CLI には無く、この 1 本だけです)。設定と認証は
  `~/.gemini/antigravity-cli/` 配下に置かれるため、既にグループ単位で永続化されている
  `.gemini` にそのまま乗り、**コンテナを作り直しても再認証は要りません**。

  反映には**ベースイメージの再ビルド**が要ります (`devbase container build`)。
  バイナリは約 190MB です。

- **永続化ボリュームをアカウントグループ単位に分離**しました (PLAN39 / #116)。
  これまで認証情報と会話ログは全コンテナ共通の `devbase_home_ubuntu` に置かれていたため、
  nyle.co.jp で認証した Claude Code / gcloud を kk-generation.com のプロジェクトが
  そのまま引き継いでしまい、企業テナントの境界を越えていました。`DEVBASE_ACCOUNT_GROUP`
  (未設定なら `default`) で使用する Google / AWS アカウントの単位を宣言すると、
  グループごとに `devbase_home_<group>` が作られ `/persistent/group` としてマウントされます。

  | 分類 | 置き場 | 内容 |
  |---|---|---|
  | 共通 | `/persistent/ai` (`devbase_home_ubuntu`) | `~/.claude/plugins` / `skills` / `commands` / `CLAUDE.md` / `settings.json`、`.codex` / `.serena` / `.kiro` / `.ssh` / `share` |
  | グループ別 | `/persistent/group` (`devbase_home_<group>`) | `.claude.json`、`~/.claude` 本体 (認証・会話ログ)、`.gemini`、gcloud / gws の設定ディレクトリ |

  `~/.claude/plugins` (238MB) のような共通資産はグループ数だけ重複しません。
  `default` グループでは初回起動時に既存データを**コピー**してシードするため、
  Claude Code の再ログインは発生しません (gcloud / gws はシード元が無いため
  全グループで初回 1 回の認証が要ります)。使えないグループ名 (Docker のボリューム名に
  できないもの・`ubuntu`・数字だけ) は `devbase up` の前にエラーで弾きます。
  詳細は [コンテナ操作ガイド](docs/user/container-operations.md#アカウントグループ) を参照してください。

- **gcloud / gws の設定ディレクトリをアカウントグループ単位に永続化**しました。
  `CLOUDSDK_CONFIG` / `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` を `/persistent/group` 配下へ
  向けることで、`gcloud auth login` / `gws auth login` のユーザー OAuth が
  **コンテナを作り直しても保たれ**、かつグループをまたいで共有されなくなります。
  `CLOUDSDK_CONFIG` は gcloud CLI 専用ではなく `google.auth` の探索経路そのものなので、
  BigQuery クライアント等も同じ場所を見ます。あわせて `@googleworkspace/cli` (`gws`) を
  base イメージへ追加しました (これまでどのコンテナにも入っておらず、設定だけ永続化しても
  復旧しませんでした)。

- **`GCP_AUTH_MODE` を新設**しました。`adc` でサービスアカウント鍵を使わず
  `gcloud auth application-default login` によるユーザー認証 (ADC) を使い、`key` で
  従来どおり鍵を使います。未設定なら鍵の env の有無で自動判定するため、既存プロジェクトは
  これまでどおり動きます。`adc` では `GOOGLE_APPLICATION_CREDENTIALS` と `BIGQUERY_KEY_FILE` を
  **コンテナへ渡しません** (値だけ残して実体が無いと ADC はユーザー認証へフォールバックせず
  `DefaultCredentialsError` で落ちるため)。

  > **Warning:** `CLOUDSDK_CONFIG` の導入により、`~/.config/gcloud` は
  > **gcloud の設定ディレクトリではなくなりました**。鍵モードで書き出される
  > サービスアカウント鍵の置き場でしかなく、コンテナ層 (揮発) に残ります。設定を見たい
  > ときは `$CLOUDSDK_CONFIG` を参照してください。

- **`devbase status` に解決されたアカウントグループ**を表示するようにしました。
  コンテナの起動ログにも、グループ名と gcloud のアカウントが 1 行出ます。

  > **Note:** 上記のうち entrypoint と Dockerfile に関わる変更は、反映に
  > `devbase build --no-cache` によるイメージの再ビルドとコンテナの作り直しが要ります。

- **tmux の既定設定 (`/etc/tmux.conf`) を base イメージへ焼き込む**ようにしました。tmux は
  起動時に端末の代替画面へ切り替わるため、出力履歴は VS Code のスクロールバックではなく
  tmux 自身のバッファに入ります。これまでコンテナの tmux は素の初期状態 (履歴 2000 行・
  マウス無効) で、その履歴へ実質手が届きませんでした。ホイールで copy-mode に入って
  遡れるようにし (`mouse on`)、履歴を 100000 行へ広げ、端末のフォーカス通知を中のアプリへ
  転送します (`focus-events on`。既定の `off` では Claude Code が警告を出し、完了通知の
  出し分けが働きません)。あわせて `default-terminal` を `tmux-256color` に固定し、
  VS Code の統合ターミナルへ 24bit 色を通します。tmux は `/etc/tmux.conf` を読んでから
  `~/.tmux.conf` を読むため、**個人設定を置けばそちらが勝ちます**。キーバインドは変更して
  いないため、既知の操作はそのまま使えます。操作方法は
  [コンテナ操作ガイド](docs/user/container-operations.md#tmuxターミナルの既定設定) を参照してください。

  > **Note:** イメージへ焼き込むため、反映には `devbase container build` によるイメージの
  > 再ビルドとコンテナの作り直しが要ります。base を継承しない `lfm` は tmux 自体を
  > 同梱していないため対象外です。

- **VS Code のウィンドウタイトルをコンテナ名始まりに固定**するようにしました
  (例 `nyle-dx-dev-1 - main.py`)。既定のタイトルは編集中ファイル名が先頭に来るため、
  複数プロジェクトの窓を並べるとどれがどのプロジェクトか判別できませんでした。
  `devbase up` が各 dev コンテナ内の Remote settings
  (`~/.vscode-server/data/Machine/settings.json`) へ `window.title` を書きます。
  エディタ自動オープン (`DEVBASE_OPEN_EDITOR`) の有無に関わらず設定されるため、
  手動で「コンテナーにアタッチ」した窓にも効きます。既存の設定値は保持し、
  値が同じなら書き込みません。テンプレートは `DEVBASE_WINDOW_TITLE` で変更でき
  (`{container}` が実コンテナ名へ置換)、`0` で無効化できます。
  settings.json はコメントや末尾カンマを含む JSONC でも読み取り、コメント付きの設定へは
  原文を保ったまま `window.title` だけを差し替えます。書き込みは同一ディレクトリの
  一時ファイル + `mv` による原子的置換なので、稼働中の VS Code が中途半端な JSON を
  読むことも、失敗時に既存の設定を失うこともありません。
- **clone できなかったリポジトリを `devbase up` が知らせる**ようにしました。複数リポジトリ構成で
  一部のリポジトリに権限が無い (または名前が違う) 場合、これまでは entrypoint の警告が
  `docker logs` にしか出ず、`up` の画面は成功したように見えていました。コンテナ起動後に
  `/work` の実体を確認し、`project.yml` に書いたのに無いリポジトリを clone URL 付きで
  警告します。すべて揃っているときの出力は変わりません。clone の失敗が `up` を失敗させない
  点も従来どおりです。

### Changed
- **multi-root ワークスペースに、clone できたリポジトリだけを載せる**ようにしました。これまでは
  `project.yml` の内容をそのまま書き出していたため、clone に失敗したリポジトリが VS Code の
  エクスプローラに「開けないフォルダ」として並んでいました。

> **Note:** ワークスペースの変更は `entrypoint.sh` の変更を含むため、反映には
> `devbase container build` (必要に応じて `--no-cache`) によるイメージの再ビルドが要ります。
> 再ビルドしていないイメージでは、これまでどおり全フォルダを載せたワークスペースが
> 書き出されます (機能が黙って失われることはありません)。

- **スナップショットの対象が 2 ボリューム**になりました (共通 + アカウントグループ)。
  メタデータに対象ボリューム名を記録し、`devbase snapshot list` にも表示します。
  分離前に作られた既存スナップショットは**そのまま復元できます**。対象ボリュームの構成が
  変わったときは、旧世代へ壊れた差分を積まないよう新しい世代を作ります (旧世代の差分状態
  ファイルは別のレイアウトを記録しているため、そこへ差分を積むと差分が壊れます)。
- **`devbase env init` は鍵を登録したときだけ** `GOOGLE_APPLICATION_CREDENTIALS` /
  `BIGQUERY_KEY_FILE` を書くようにしました (従来は鍵の有無に関係なく書いていました)。
  実体の無いパスが `env` に残っていると ADC がユーザー認証へフォールバックできません。

### Fixed
- **`devbase build <image>` が必ず失敗する問題**を修正しました (#139)。`<image>` の位置引数が
  剥がされないまま `docker buildx build` へ渡り、PATH が 2 つになって
  `docker: 'docker buildx build' requires 1 argument` で落ちていました。CLI リファレンスに
  正式な構文として載っているにもかかわらず、**ベースイメージだけを再ビルドする手段が
  無い**状態でした。

  `bin/devbase` の dispatch が位置引数を検出し、`--expires` と同じく Python 側へ振り分けます。
  `devbase build` / `--no-cache` / `--project-no-cache` は従来どおり shell の 2 段ビルドです。

  あわせて `devbase project build <image>` / `devbase container build <image>` が作るタグを
  `<image>:latest` から **`devbase-<image>:latest`** へ直しました。旧タグは他の Dockerfile の
  `FROM devbase-base:latest` から解決できず、ビルドしても使われませんでした。ビルドコマンドも
  shell 側と同じ `docker buildx build --load` に揃えています。旧タグはリポジトリ内のどこからも
  参照されていないため、移行の手当ては要りません。

  `<image>` にはディレクトリ名を渡してください (`devbase build base`)。`devbase-base` のように
  接頭辞込みで渡すと `containers/devbase-base` を探して見つからず、終了コード 1 で終わります。
  `<image>` が `projects/` に実在する名前と一致する場合は、そのプロジェクトへの操作として
  解釈されます (#142)。この場合は `devbase project build <image>` を使ってください。

- **使われない GCP サービスアカウント鍵をコンテナへ渡さない**ようにしました (#134)。
  `GCP_AUTH_MODE=adc` が止めるのは「鍵をファイルへ書き出すこと」だけで、鍵を運ぶ
  `GCP_CREDENTIALS_BASE64__*` と `GOOGLE_APPLICATION_CREDENTIALS_BASE64` は生成 compose の
  `environment` に名前が残り、値がコンテナへ渡っていました。アカウントグループを分けても
  **他社の鍵がコンテナ内から `env` で読める**状態が続きます。

  entrypoint が読むのはアクティブプロファイルの鍵 1 本だけなので、それ以外は dev の列挙から
  外します。`adc` では 1 本も渡しません。後方互換キーは、鍵モードでアクティブプロファイルの
  鍵が無いとき **だけ** 供給源になるため、その場合に限って残します。`compose.yml` の dev へ
  **直書き**された鍵も取り除きます (列挙を絞るだけでは迂回されるため)。非 dev サービスの
  設定には触れません。

- **新規アカウントグループの初回起動で Claude Code が起動しない**不具合を直しました (#136)。
  プレースホルダとして作られる `~/.claude.json` が 0 バイトで、
  `The configuration file at ~/.claude.json contains invalid JSON.` になっていました。
  `default` グループは実体がシードされるため踏みませんが、非 default はシードを飛ばす
  (他社テナントの認証情報を持ち込まないための設計) ので必ず空になります。

  空で作ると不正になるエントリを一覧で持ち、`{}` を書くようにしました。`history.jsonl` は
  JSON Lines なので対象外です (`{}` を書くと 1 行目が履歴として読まれます)。
  反映には**ベースイメージの再ビルド**が要ります (`devbase container build --no-cache`)。

- entrypoint の symlink 生成で、**入れ子パスの親ディレクトリが作られていなかった**不具合を
  直しました。`~/.claude/.credentials.json` は永続領域側の作成が
  `No such file or directory` で落ちて壊れた symlink になり、`~/.claude/history.jsonl` は
  ファイル判定が `*.json` グロブだったため `.jsonl` にマッチせず**ディレクトリとして**
  作られ、Claude Code が追記できませんでした。ファイルとして作るエントリは拡張子ではなく
  明示の一覧で判定するようにしています。

- **`plugin.yml` の `requires.devbase` をインストール時に検証**するようにしました。要件を
  満たさない Plugin は `devbase plugin install` が中止します。これまでは値を読むだけで
  比較しておらず、`project.yml` 形式の Plugin を 2.x へ入れられてしまい、`devbase up` の
  段階で初めて失敗していました。既存のインストールに触れる前に検証するため、入れ替えに
  失敗しても既存の Plugin は壊れません。解釈できない書式は警告に留めて続行し、
  `DEVBASE_IGNORE_PLUGIN_REQUIRES=1` で検証を無効化できます。
- **`devbase plugin update` でも `requires.devbase` を確認**するようにしました。`git pull` で
  要求が上がって満たさなくなった Plugin は警告で知らせます（更新は既に済んでいるため中止はしません）。

## [3.0.0] - 2026-08-23

プロジェクト設定を `project.yml` へ移行する破壊的変更を含みます。プラグイン側の
プロジェクト定義も本バージョンに合わせた更新が必要です (`requires.devbase: ">=3.0.0"`)。

### Changed
- **1 プロジェクト = 1 コンテナ = 複数リポジトリ**に対応しました。プロジェクトが開発対象と
  するリポジトリは `projects/<name>/project.yml` の `repos` 配列で指定し、すべてが同じ
  コンテナの `/work` 配下へ clone されます。リポジトリごとに Git ホスト・オーナー・
  ブランチ・clone 先ディレクトリ名・`init.sh` の実行有無を指定できます。関連する複数
  リポジトリ (本体・ドキュメント・インフラ等) を 1 つの開発環境で横断的に扱えます。
  リポジトリが 2 件以上あるときは、全リポジトリを含む multi-root ワークスペース
  `/work/<プロジェクト名>.code-workspace` を生成してエディタで開きます。
  詳細は [project.yml リファレンス](docs/user/project-yml.md) を参照してください。
- **プロジェクト設定を `env` から `project.yml` へ移しました (破壊的変更)。**
  `GIT_USER` / `GIT_REPO` / `GIT_HOST` / `WORK_DIR` / `CONTAINER_SCALE` /
  `DEVBASE_OPEN_EDITOR` は `project.yml` の `repos` / `work_dir` / `scale` /
  `open_editor` に移行しました。`env` は「コンテナへ渡す環境変数」だけを持ちます。
  配列を表現できない環境変数では複数リポジトリを書けないためです。
  `project.yml` を持たないプロジェクトは `devbase up` が移行手順を案内して停止します
  (旧形式へ暗黙にフォールバックすると、移行漏れを検出できないため)。
  `devbase project scale N` の書き込み先も `project.yml` の `scale` になります。

> **Note:** リポジトリの clone は `entrypoint.sh` で行われ、これはイメージに焼き込まれます。
> 適用には `devbase build --no-cache` によるベースイメージの再ビルドが必要です。

### Added
- **ライフサイクルフックへ `project.yml` の値を環境変数で渡す**ようにしました。`pre-up` /
  `deploy` に `DEVBASE_PRIMARY_DIR` (primary の clone 先ディレクトリ名) /
  `DEVBASE_PRIMARY_URL` (primary の clone URL) / `DEVBASE_WORK_DIR` (コンテナ内の既定の
  作業ディレクトリ) / `DEVBASE_REPO_DIRS` (全リポジトリのディレクトリ名・宣言順の空白
  区切り) が渡ります。フックはホスト側で動くため `env` を読み込めず、`GIT_REPO` /
  `WORK_DIR` の `project.yml` 移行によって `source ./env` に依存していたフックが値を
  取れなくなるためです。値は子プロセス限定で、親プロセスの環境は汚しません。
  詳細は [フックへ渡る環境変数](docs/plugin-dev/quickstart.md#フックへ渡る環境変数)
  を参照してください。
- **`devbase project migrate-config`** を追加しました。旧 `env` 形式のプロジェクト定義を
  `project.yml` へ機械的に変換します。`--dry-run` で変換結果を確認でき、既存の
  `project.yml` は上書きしないため何度実行しても同じ状態に収束します。
  変換対象は上記 6 キーのみで、`ENABLE_SSH` などそれ以外は `env` に残ります。
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

[Unreleased]: https://github.com/devbasex/devbase/compare/v3.2.1...HEAD
[3.2.1]: https://github.com/devbasex/devbase/compare/v3.2.0...v3.2.1
[3.2.0]: https://github.com/devbasex/devbase/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/devbasex/devbase/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/devbasex/devbase/compare/v2.2.0...v3.0.0
[2.2.0]: https://github.com/devbasex/devbase/releases/tag/v2.2.0
