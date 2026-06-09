# はじめに

devbase の初回セットアップから日常的な開発ワークフローまでを解説します。

## 前提条件

devbase を利用するには、以下のソフトウェアがホストマシンにインストールされている必要があります。

| ソフトウェア | 最低バージョン | 確認コマンド |
|-------------|--------------|-------------|
| Docker Engine | 20.10 以上 | `docker --version` |
| Docker Compose | v2.x 以上 | `docker compose version` |
| Bash | 4.0 以上 | `bash --version` |
| Zsh（代替） | 5.0 以上 | `zsh --version` |
| Python | 3.10 以上 | `python3 --version` |
| Git | 最新推奨 | `git --version` |

> **Note:** Docker Desktop を使用している場合、Docker Engine と Docker Compose の両方が含まれています。Linux では Docker Engine を直接インストールし、Docker Compose プラグインを追加してください。

## クイックインストール（ワンライナー）

手順 1〜2（クローンと初期化）を 1 コマンドで自動化できます。`git` と `curl` があれば実行できます。

```bash
curl -fsSL https://dl.basex.jp/i | bash && . ~/devbase/bin/rc
```

このコマンドは次を行います。

1. `~/devbase` に devbase を clone します（既に devbase が clone 済みなら `git pull --ff-only` で更新）。
2. clone 先で `devbase init` を 1 回実行します（uv の自動導入・PATH/補完の登録・`plugins.yml` 生成を含む）。
3. 末尾の `&& . ~/devbase/bin/rc` を**いま開いている端末**で実行し、その端末で即座に `devbase`（PATH / 補完）が使える状態にします。新しく開くターミナルは init の rc 追記により自動で有効です。

`env init`（手順 7）は対話が必要なため、ワンライナーでは**実行せず案内のみ**です。完了後に手動で実行してください。配置先を `DEVBASE_INSTALL_DIR` で変えた場合は、`~/devbase/...` を同じパスに合わせてください。

環境変数で挙動を上書きできます。

| 変数 | 既定値 | 用途 |
|------|--------|------|
| `DEVBASE_INSTALL_DIR` | `$HOME/devbase` | 配置先ディレクトリ |
| `DEVBASE_INSTALL_REPO` | `https://github.com/devbasex/devbase.git` | clone 元（fork / テスト用） |
| `DEVBASE_INSTALL_REF` | `main` | チェックアウトする branch / tag |

> **既存ディレクトリの扱い**: 配置先が devbase 以外の非空ディレクトリだった場合、誤上書きを避けるためスクリプトは中止します。別の場所に入れるには `DEVBASE_INSTALL_DIR=/path/to/dir` を指定してください。

> **⚠ `curl | bash` を実行する前に**: 信頼できないスクリプトをそのままパイプ実行するのが不安な場合は、保存して内容を確認してから実行してください。
>
> ```bash
> curl -fsSL https://dl.basex.jp/i -o install.sh
> less install.sh    # 内容を確認
> bash install.sh
> ```

ワンライナーを使わず手動で進める場合は、以下の手順に従ってください。

## セットアップ手順

### 1. リポジトリのクローン

```bash
git clone https://github.com/devbasex/devbase.git
cd devbase
```

### 2. devbase の初期化

```bash
./bin/devbase init
```

`init` コマンドは以下を自動実行します。

- `bin/devbase` を PATH に追加（環境に応じて `~/.zshrc` / `~/.bashrc` / `~/.bash_profile` のいずれかに追記）
- シェル補完スクリプトの登録（Tab 補完が有効になる）
- プラグイン設定ファイル `plugins.yml` の作成

書き込み先は現在のシェル種別と OS から自動判定されます（zsh → `~/.zshrc`、bash on macOS → `~/.bash_profile`、bash on Linux → `~/.bashrc`）。

### 3. シェルで有効化

```bash
. ./bin/rc
```

`bin/rc` を source すると、`init` が rc ファイルに追記するのと同じ有効化（devbase の PATH と補完）が現在のシェルへ即時反映されます。bash / zsh のどちらでも `.`（= `source`）で読み込めます。

> **Note:** 新しいターミナルを開いた場合は `init` が rc に追記したブロックで自動的に有効化されるため、この手順は不要です。

### 4. プラグインリポジトリの登録

`devbase init` を実行すると、サンプルレジストリ [`devbasex/devbase-samples`](https://github.com/devbasex/devbase-samples) が自動登録されます。追加のレジストリは以下のように登録します。

```bash
devbase plugin repo add user/repo
```

GitHub のショートハンド（`user/repo`）形式で指定できます。完全な URL も使用可能です。

```bash
# 登録済みリポジトリの確認
devbase plugin repo list
```

詳細・追加方法は [プラグインレジストリ](plugin-registries.md) を参照してください。

### 5. プラグインのインストール

```bash
# 利用可能なプラグインの確認
devbase plugin list --available

# プラグインのインストール
devbase plugin install <name>
```

インストールされたプラグインは `projects/` ディレクトリ配下にプロジェクトとして展開されます。

### 6. プロジェクトディレクトリへの移動

```bash
cd projects/<project>
```

### 7. 環境変数の設定

```bash
devbase env init
```

対話式のウィザードが起動し、以下を順に設定します。

1. グローバル環境変数（AWS 認証、GCP 認証、Git 認証など）
2. プロジェクト固有の環境変数

詳細は [環境変数ガイド](environment-variables.md) を参照してください。

### 8. コンテナイメージのビルド

```bash
devbase build
```

初回はベースイメージのビルドに時間がかかります（ネットワーク速度に依存）。2回目以降はキャッシュが利用されるため高速です。

### 9. コンテナの起動

```bash
devbase up
```

起動時に自動スナップショットが作成されます（詳細は [スナップショットガイド](snapshot-guide.md) を参照）。

### 10. コンテナへのログイン

```bash
devbase login
```

デフォルトでは 1 番目のコンテナにログインします。複数コンテナ環境では番号を指定できます。

```bash
devbase login 2
```

## 日常ワークフロー

セットアップ完了後の日常的な開発フローは以下のとおりです。

```mermaid
graph TD
    A[devbase up] --> B[devbase login]
    B --> C[コンテナ内で作業]
    C --> D{作業終了?}
    D -->|いいえ| C
    D -->|はい| E[exit でコンテナから退出]
    E --> F[devbase down]
    F --> G[自動ローテーション実行]
```

### 作業開始

```bash
# プロジェクトディレクトリに移動
cd devbase/projects/<project>

# コンテナ起動（自動スナップショット作成）
devbase up

# コンテナにログイン
devbase login
```

### 作業中

```bash
# コンテナの状態確認（別ターミナルから）
devbase ps

# ログの確認
devbase project logs -f

# 2番目のコンテナにログイン（並行作業）
devbase login 2
```

### 作業終了

```bash
# コンテナから退出
exit

# コンテナ停止・削除（自動ローテーション実行）
devbase down
```

## 環境の状態確認

`status` コマンドで環境の全体像を確認できます。

```bash
devbase status
```

表示される情報:

- コンテナの状態（起動中 / 停止中）
- インストール済みプラグイン
- 環境変数の設定状況
- スナップショットの状態

## プロジェクト構成の概要

devbase をセットアップすると、以下のようなディレクトリ構成になります。

```
devbase/
├── bin/
│   └── devbase              # メインエントリポイント
├── projects/
│   └── <project>/           # プラグインから作成されるプロジェクト
│       ├── plugin.yml       # プラグイン定義
│       ├── compose.yml      # Docker Compose 設定
│       ├── env              # プロジェクト設定（Git 管理）
│       └── .env             # プロジェクト機密情報（gitignore）
├── backups/                 # スナップショット保存先（全プロジェクト共通）
├── .env                     # グローバル環境変数（gitignore）
└── plugins.yml              # プラグイン設定
```

## 次のステップ

- [CLI リファレンス](cli-reference.md) -- 全コマンドの詳細な使い方
- [環境変数ガイド](environment-variables.md) -- 環境変数の3レベル構造とコレクター
- [コンテナ操作ガイド](container-operations.md) -- 並行開発やボリュームの詳細
- [スナップショットガイド](snapshot-guide.md) -- バックアップと復元の仕組み
- [トラブルシューティング](troubleshooting.md) -- 問題が発生したとき
