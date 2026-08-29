# コンテナ操作ガイド

devbase のコンテナ管理機能について、ライフサイクル、並行開発、ボリューム構造、イメージ階層を解説します。

> **コマンド体系について:** コンテナ操作は `devbase project <sub>` グループ（および
> トップレベルショートカット `devbase up` 等）で行います。旧 `devbase container <sub>` は
> 非推奨となり、`project` へのエイリアスとして警告付きで当面動作します。`project` では
> `up` / `down` / `ps` / `logs` / `scale` に `[name]` を指定することで **任意のディレクトリ
> から** 対象プロジェクトを操作できます。プロジェクト一覧は `devbase project list` を参照
> してください。詳細は [CLI リファレンス: project グループ](cli-reference/02-project.md) を参照。

## コンテナライフサイクル

devbase のコンテナは以下のライフサイクルで管理されます。

```mermaid
stateDiagram-v2
    [*] --> ビルド: devbase build
    ビルド --> 停止: イメージ作成完了
    停止 --> 起動中: devbase up
    起動中 --> 起動中: devbase login
    起動中 --> 停止: devbase down
    停止 --> 起動中: devbase up

    note right of 起動中
        up 時にスナップショット自動作成
    end note
    note right of 停止
        down 時にローテーション自動実行
    end note
```

### 基本操作の流れ

```bash
# 1. コンテナイメージをビルド（初回のみ）
devbase build

# 2. コンテナを起動（自動スナップショット作成）
devbase up

# 3. コンテナにログイン
devbase login

# 4. コンテナ内で作業
# ...

# 5. コンテナから退出
exit

# 6. コンテナを停止・削除（自動ローテーション）
devbase down
```

### 自動スナップショット

コンテナのライフサイクルに連動して、スナップショットが自動管理されます。

| タイミング | 動作 | 条件 |
|-----------|------|------|
| `devbase up` | フルバックアップ or 差分追加 | 前回のフルバックアップからの経過日数で判定 |
| `devbase down` | 古い世代のローテーション | `DEFAULT_MAX_GENERATIONS` を超えた世代を削除 |

詳細は [スナップショットガイド](snapshot-guide.md) を参照してください。

## 並行開発

devbase は複数のコンテナを同時に起動し、並行開発を行うことができます。

### コンテナ数の設定

プロジェクトの [`project.yml`](project-yml.md) で `scale` を設定します。デフォルト値は `2` です。

```yaml
version: 1
scale: 2
repos:
  - owner: your-org
    repo: my-repo
```

### 動的スケーリング

起動中のコンテナを再起動せずに、コンテナ数を変更できます。

```bash
# コンテナを3台に増やす（既存コンテナは再起動しない）
devbase project scale 3

# コンテナを1台に減らす
devbase project scale 1

# 任意のディレクトリから adminer を3台に
devbase project scale adminer 3
```

### 各コンテナへのログイン

コンテナ番号を指定してログインします。

```bash
# 1番目のコンテナにログイン
devbase login 1

# 2番目のコンテナにログイン
devbase login 2

# 3番目のコンテナにログイン
devbase login 3
```

### 並行開発のユースケース

```mermaid
graph LR
    subgraph ホストマシン
        A[ターミナル 1]
        B[ターミナル 2]
    end
    subgraph devbase
        C[コンテナ 1<br/>/work 専用]
        D[コンテナ 2<br/>/work 専用]
        E[共有AI設定<br/>/persistent/ai]
    end
    A --> C
    B --> D
    C --> E
    D --> E
```

- 各コンテナは独立した `/work` ボリュームを持つ
- `/persistent/ai`（AI 設定・共有ファイル）は全コンテナで共有される（`/home/ubuntu` 直下のうち永続化されるのは symlink 対象のみ。下記「AI 設定の永続化」参照）
- 異なるブランチでの並行作業に便利

## ボリューム構造

devbase のコンテナは 2 種類のボリュームを使用します。

| ボリューム名 | マウント先 | 共有範囲 | 用途 |
|-------------|-----------|---------|------|
| `devbase_home_ubuntu` | `/persistent/ai` | 全コンテナで共有 | AI CLI 設定（`.claude` / `.codex` / `.gemini` 等）、SSH 鍵、共有ファイル置き場（`share`）。詳細は「AI 設定の永続化」参照 |
| `devbase_work_{index}` | `/work` | 同じ index のコンテナで共有（プロジェクト間も共有） | プロジェクトのソースコード、作業ファイル |

> **Note:** `devbase_home_ubuntu` は **`/persistent/ai`** にマウントされます（`/home/ubuntu` への直接マウントは廃止）。`/home/ubuntu` 直下はコンテナ層（揮発）で、永続化されるのは entrypoint が `/persistent/ai` 配下へ symlink する設定ファイルのみです。シェル履歴など symlink 対象外のファイルは再生成で失われます。

### ボリュームの永続性

- ボリュームは `devbase down` でもコンテナが削除されても保持されます
- コンテナの再起動（`devbase up`）で同じボリュームが再マウントされます
- ボリュームを明示的に削除するには `docker volume rm` を使用します

> **Warning:** `devbase_work_{index}` は `COMPOSE_PROJECT_NAME` の接頭辞が付かない **external ボリューム**です。同じ index（コンテナ 1 なら `devbase_work_1`）を使う限り **別プロジェクトからも同じ実体**を参照するため、`docker volume rm devbase_work_1` は停止中の他プロジェクトの作業ファイルまで削除します。削除前に `docker ps -a --filter volume=devbase_work_1` で利用コンテナを確認してください。

### ボリュームの確認

```bash
# Docker ボリュームの一覧
docker volume ls | grep devbase

# 特定ボリュームの詳細
docker volume inspect devbase_home_ubuntu
```

> **Warning:** `devbase_home_ubuntu` ボリューム（`/persistent/ai`、および symlink 経由でアクセスする `~/.claude` / `~/share` 等）は全プロジェクトで共有されます。ここにプロジェクト固有のファイルを置くと、他のプロジェクトにも影響します。プロジェクト固有のファイルは `/work` に配置してください。

## AI 設定の永続化

AI CLI ツールの設定や認証情報は、コンテナを再生成しても保持されるよう
`devbase_home_ubuntu` ボリューム（`/persistent/ai`）に永続化されます。

仕組みは **symlink** です。コンテナ起動時、entrypoint（`containers/base/entrypoint.sh`）が
以下の各エントリについて `/home/ubuntu/<name> -> /persistent/ai/<name>` の symlink を作成します。

| エントリ | 内容 |
|---------|------|
| `.claude.json` / `.claude` | Claude Code の設定・認証 |
| `.codex` | Codex CLI の設定 |
| `.gemini` | Gemini CLI の設定 |
| `.serena` | Serena MCP の設定 |
| `.kiro` | Kiro CLI の設定 |
| `.ssh` | SSH 鍵 |
| `share` | 全コンテナ共有のファイル置き場（任意用途） |

- `/persistent/ai` は全コンテナ共通の `devbase_home_ubuntu` ボリュームなので、**どのコンテナからも同じ実体**を参照します（例: `~/share` は全コンテナで共有）。
- symlink **対象外**のホーム配下ファイル（シェル履歴など）はコンテナ層に置かれ、再生成で失われます。永続化したいものは `/persistent/ai` 配下（= 上記 symlink 先）か `/work` に置いてください。
- `share` 配下に置いた VS Code ワークスペースファイルは `DEVBASE_WORKSPACE` で開けます（リポジトリ 1 件の構成のみ。[環境変数](environment-variables.md) 参照）。

> **Note:** symlink 対象は entrypoint にビルド時 `COPY` で焼き込まれます。エントリを増減した場合は
> イメージの再ビルドが必要です（`devbase up` 単体では反映されない場合があります。[CLI リファレンス: project グループ](cli-reference/02-project.md#devbase-project-up) の `devbase project up` の注記参照）。

### gcloud / gws の設定はどこにあるか

gcloud と gws は symlink ではなく **環境変数で設定ディレクトリごと差し替え**ています。

| 変数 | 向き先 | 入るもの |
|---|---|---|
| `CLOUDSDK_CONFIG` | `/persistent/group/gcloud` | `credentials.db` / `access_tokens.db` / `legacy_credentials/` / `configurations/` / `application_default_credentials.json`（ADC ファイル） |
| `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` | `/persistent/group/gws` | `credentials.enc` / `.encryption_key` |

`CLOUDSDK_CONFIG` は gcloud CLI 専用の仕組みではなく `google.auth` の探索経路そのものなので、
BigQuery クライアント等のライブラリも同じ場所を見ます。

> **Warning:** この差し替えにより、`~/.config/gcloud` は **gcloud の設定ディレクトリでは
> なくなりました**。鍵モード（`GCP_AUTH_MODE=key`）で書き出されるサービスアカウント鍵の
> 置き場でしかなく、コンテナ層（揮発）に残ります。したがって鍵は毎起動 `env` から書き直され、
> 永続領域には残りません。設定を見たいときは `$CLOUDSDK_CONFIG` を参照してください。

認証モードの切り替えは [環境変数ガイド](environment-variables.md) の `GCP_AUTH_MODE`、
実際の認証手順は [Google 認証ガイド](google-auth.md) を参照してください。

> **Warning:** gcloud は**並行実行を想定していません**（公式ドキュメント: "Parallel execution of
> multiple gcloud CLI commands is not supported."）。`credentials.db` は SQLite なので、
> 同じアカウントグループの複数コンテナが同時に `gcloud` を叩くと `database is locked` が
> 出ることがあります。恒久対策は取っていないので、その場合は少し待って再実行してください。

## コンテナイメージ階層

devbase のコンテナイメージは用途に応じた階層構造になっています。

```mermaid
graph TD
    A[Ubuntu 26.04] --> B[base]
    B --> C[general]
    B --> G[go]
    C --> D[php]
    C --> I[php85]
    C --> E[latex]
    C --> F[lfm]
    A --> H[snapshot]

    style A fill:#f0f0f0
    style B fill:#e8e8f4
    style C fill:#e8f4e8
    style G fill:#f4f0e8
    style H fill:#f4e8e8
```

### イメージの詳細

| イメージ | ベース | 主な内容 | 用途 |
|---------|-------|---------|------|
| **base** | Ubuntu 26.04 | Docker CLI、Python 3 | 最小限の開発環境 |
| **general** | base | AWS CLI、gcloud、Terraform、Node.js 20、AI CLI | 汎用開発環境 |
| **php** | general | PHP 8.5、Composer、MySQL Shell | PHP 8.5 系 開発 |
| **php85** | general | PHP 8.5、Composer、MySQL Shell | PHP 8.5 系 開発 |
| **latex** | general | LaTeX | 文書作成 |
| **lfm** | general | Rust、gfortran、MeCab | 数値計算・自然言語処理 |
| **go** | base | Go 開発環境 | Go 開発 |
| **snapshot** | Ubuntu 26.04 | zstd のみ（約 80MB） | スナップショット専用 |

### AI CLI エイリアス

general イメージ以降のコンテナ内では、以下の AI CLI ツールがエイリアスとして利用可能です。

| エイリアス | ツール | モード | 説明 |
|-----------|-------|--------|------|
| `claude` | Claude Code | skip-permissions | Anthropic の AI コーディングアシスタント |
| `claudb` | Claude Code (AWS Bedrock) | Opus 4.6 / us-west-2 | AWS Bedrock 経由の Claude |
| `gemini` | Gemini CLI | yolo mode | Google の AI アシスタント |
| `codex` | Codex CLI | bypass-approvals | OpenAI の AI コーディングツール |
| `kiro` | Kiro CLI | trust-all-tools | AWS の AI アシスタント |

```bash
# コンテナ内での使用例
claude "このコードをレビューして"
gemini "テストを書いて"
codex "リファクタリングして"
```

## tmux（ターミナル）の既定設定

コンテナ内の tmux には、devbase 共通の既定設定 `/etc/tmux.conf` が入っています
（実体は `containers/base/tmux.conf`）。tmux は起動すると端末の代替画面へ切り替わるため、
出力履歴は VS Code のスクロールバックではなく tmux 自身のバッファに入ります。素の tmux は
履歴 2000 行・マウス無効なので、この履歴に実質手が届きません。既定設定はそこを埋めます。

| 設定 | 値 | 理由 |
|------|-----|------|
| `mouse` | `on` | ホイールを転がすと copy-mode に入り、履歴を遡れる |
| `history-limit` | `100000` | 既定の 2000 行はビルドログ 1 回で流れ切る |
| `focus-events` | `on` | 端末のフォーカス通知を中のアプリへ渡す。Claude Code の完了通知が正しく出し分けられる |
| `default-terminal` | `tmux-256color` | 端末種別の固定 |
| `terminal-overrides` | `,xterm-256color:Tc` を追記 | VS Code の統合ターミナルへ 24bit 色を通す |

### 基本操作

| 操作 | キー |
|------|------|
| スクロール | マウスホイール（自動で copy-mode に入る） |
| コピー | ドラッグして離す（クリップボードへ直接入る） |
| copy-mode に入る | `Ctrl-b` `[` |
| 履歴内を検索 | copy-mode 中に `Ctrl-r`（上方向）／`Ctrl-s`（下方向） |
| copy-mode を抜ける | `q` |
| ペインをまたいで選択 | `Shift` + ドラッグ（VS Code のネイティブ選択に切り替わる） |

コピーは追加設定なしで動きます。tmux の `set-clipboard`（既定 `external`）により、選択して
ボタンを離した時点で OSC 52 が送出され、VS Code がクリップボードへ書き込みます。

上表の copy-mode のキーは tmux の `mode-keys` に従います。既定は `emacs` ですが、tmux は
`EDITOR` / `VISUAL` に `vi` を含む値が入っていると `vi` へ切り替えるため、その場合の検索は
`/`（下方向）と `?`（上方向）になります。

### 個人設定で上書きする

tmux は `/etc/tmux.conf` を読んでから `~/.tmux.conf` を読み、同じオプションは後から読んだ
`~/.tmux.conf` が勝ちます。既定値を変えたい場合は `~/.tmux.conf` に書いてください。

```bash
# 例: ホイールを tmux に渡さず、端末側のスクロールに戻す
echo 'set -g mouse off' >> ~/.tmux.conf
tmux source-file ~/.tmux.conf   # 実行中のセッションを保ったまま反映する
```

> **Note:** `source-file` はセッションや配下のプロセスを終了せずに設定を読み直し、`mouse` のような
> オプションは即時に反映されます。ただし `history-limit` は新しく作る pane から適用され、既存の
> pane は作成時の値を保持します。既存 pane にも効かせたい場合は、その pane を作り直してください。

> **Note:** `~/.tmux.conf` は永続化の対象外です（`/persistent/ai` へ symlink されるのは
> `~/.claude` などの AI 設定のみ）。コンテナを作り直すと消えるため、残したい設定は
> `~/share` 配下など永続領域へ置いてコピーしてください。

> **Note:** `/etc/tmux.conf` はイメージに焼き込まれています。設定を変更した場合の反映には
> `devbase container build` によるイメージの再ビルドと、コンテナの作り直しが必要です。

セッションが増えてしまったときの整理（`tmux-first` / `tmux-clean`）は
[環境変数ガイド](environment-variables.md#tmux--screen-経由で使う場合) を参照してください。

## コンテナの状態確認

### プロセス一覧

```bash
# 起動中のコンテナを表示
devbase ps

# 停止中のコンテナも含めて表示
devbase ps -a
```

### ログの確認

```bash
# 最新のログを表示
devbase project logs

# リアルタイムでログを追跡
devbase project logs -f

# 末尾100行のみ追跡
devbase project logs -f --tail 100
```

### プロジェクト一覧

```bash
# 階層メニュー TUI を起動（TTY ではこれがデフォルト）
devbase list

# 選択せず NAME / PLUGIN / STATUS の一覧表示のみ
devbase list --no-interactive   # --plain / -P も同義
```

> TTY（端末）では `devbase list` はデフォルトで階層メニュー TUI になり、
> プロジェクトを選んで起動・操作（up / down / login / ps / logs / scale /
> build / rebuild）できるほか、画面最下部の常設メニュー（環境変数 / プラグイン /
> スナップショット / ステータス）へ ←→ キーで移動して各管理操作を実行できます。
> パイプ・リダイレクト・CI などの非 TTY 環境では自動的に一覧表示のみに
> フォールバックします。画面構成とキー操作の詳細は
> [CLI リファレンス: project グループ](cli-reference/02-project.md#devbase-project-list) を参照してください。

`devbase project ps` が「対象プロジェクト 1 つのコンテナ状態」を表示するのに対し、
`devbase list` は「全プロジェクトの横断一覧」を表示します。

### 環境の全体像

```bash
# コンテナ、プラグイン、環境変数、スナップショットの状態を一括確認
devbase status
```

## ベストプラクティス

1. **プロジェクト固有のファイルは `/work` に配置する** -- `/persistent/ai`（`~/.claude` 等の symlink 先・`~/share`）は全コンテナで共有されるため
2. **`scale` は必要最小限に設定する** -- リソース消費を抑制
3. **作業終了後は `devbase down` を実行する** -- 自動ローテーションでディスク容量を管理
4. **`devbase ps` で状態を確認してからログインする** -- 異常終了したコンテナへのログイン試行を避ける
5. **イメージのビルドは初回と更新時のみ** -- 変更がない場合はキャッシュが利用される
