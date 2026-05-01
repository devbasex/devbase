# devbase

Dockerベースの開発環境マネージャー。コンテナ化された開発環境を**Plugin**によるプロジェクト管理で提供します。

## 概要

devbaseは、Docker Composeを使った再現性の高い開発環境を提供するCLIツールです。プロジェクトはPluginとして外部リポジトリで管理され、`devbase plugin install`でインストールします。

### 主な特徴

- **Pluginベースのプロジェクト管理**: 外部リポジトリからプロジェクト設定をインストール・更新
- **コンテナ化された開発環境**: Docker Composeベースで再現性の高い環境を提供
- **豊富なツールセット**: Docker CLI、AWS CLI、gcloud SDK、Terraform、Node.js、AI CLIツールがプリインストール
- **複数コンテナの並行開発**: `devbase container scale`で既存コンテナを再起動せずにスケール可能
- **データ永続化**: 名前付きボリュームでコンテナ再起動後もデータを保持
- **スナップショット管理**: `/work` ボリュームの増分バックアップ・復元・世代管理
- **環境変数の自動収集**: `devbase env init`でAWS/Git/GCP認証情報を対話的に設定

## クイックスタート

```bash
# 1. クローンと初期化
git clone https://github.com/devbasex/devbase.git
cd devbase
./bin/devbase init
source ~/.bashrc  # または ~/.zshrc

# 2. Pluginのインストール
devbase plugin repo add user/repo    # リポジトリ登録（initで公式は自動登録済み）
devbase plugin install <name>        # Plugin名でインストール

# 3. プロジェクトの起動
cd projects/your-project
devbase env init                     # 環境変数の設定（初回のみ）
devbase build                        # コンテナイメージのビルド（初回のみ）
devbase up                           # コンテナを起動
devbase login                        # コンテナにログイン
```

詳細なセットアップ手順は [はじめに](docs/user/getting-started.md) を参照してください。

## Plugin

devbaseのプロジェクトはPluginとして管理されます。Pluginは**プラグインレジストリ**（複数の Plugin を束ねた Git リポジトリ）から `devbase plugin install` でインストールします。

### 主なプラグインレジストリ

| レジストリ | Visibility | 役割 |
|-----------|-----------|------|
| [`devbasex/devbase-samples`](https://github.com/devbasex/devbase-samples) | public | サンプルレジストリ（`devbase init` 時に自動登録） |

詳細は [プラグインレジストリ](docs/user/plugin-registries.md) を参照してください。

### 操作例

```bash
# リポジトリ登録（GitHubショートハンド対応）
devbase plugin repo add user/repo

# インストール
devbase plugin install adminer                         # 名前指定
devbase plugin install user/repo:plugin-name           # リポジトリ直接指定
devbase plugin install /path/to/local:name --link      # ローカルリンク

# 管理
devbase plugin list                  # 一覧表示
devbase plugin update                # 全Plugin更新
devbase plugin uninstall <name>      # アンインストール
```

Pluginをインストールすると、`plugins/<name>/`にリポジトリがクローンされ、内部のプロジェクトが`projects/`にシンボリックリンクとして自動作成されます。

## CLIコマンド体系

devbaseのコマンドは4つのグループにまとめられています。

| グループ | 略記 | 説明 |
|---------|------|------|
| `container` | `ct` | コンテナ管理（up / down / login / ps / logs / scale / build） |
| `env` | — | 環境変数管理（init / sync / list / set / get / delete / edit / project） |
| `plugin` | `pl` | プラグイン管理（list / install / uninstall / update / info / sync / repo） |
| `snapshot` | `ss` | スナップショット管理（create / list / restore / copy / delete / rotate） |

- **ショートカット**: `up`, `down`, `login`, `build`, `ps` はトップレベルから直接使用可能
- **プレフィックス略記**: `devbase p l` → `devbase plugin list`
- **トップレベルコマンド**: `init`, `status`

全コマンドの構文・オプション・使用例は [CLIリファレンス](docs/user/cli-reference.md) を参照してください。

## 前提条件

- Docker Engine 20.10以上
- Docker Compose v2.x以上
- Bash 4.0以上 または Zsh 5.0以上
- Python 3.10以上
- Git

## ドキュメント

詳細なドキュメントは [docs/](docs/README.md) に整備されています。

### 利用者向け

| ドキュメント | 内容 |
|-------------|------|
| [はじめに](docs/user/getting-started.md) | 前提条件、初回セットアップ、日常ワークフロー |
| [CLIリファレンス](docs/user/cli-reference.md) | 全コマンドの構文・オプション・使用例 |
| [プラグインレジストリ](docs/user/plugin-registries.md) | 公開・社内レジストリの一覧と追加方法 |
| [環境変数ガイド](docs/user/environment-variables.md) | 3レベル構造、コレクター、ソース同期 |
| [コンテナ操作ガイド](docs/user/container-operations.md) | ライフサイクル、並行開発、ボリューム構造 |
| [スナップショットガイド](docs/user/snapshot-guide.md) | 増分バックアップ、世代管理、復元手順 |
| [トラブルシューティング](docs/user/troubleshooting.md) | カテゴリ別の問題と解決策 |

### プラグイン開発者向け

| ドキュメント | 内容 |
|-------------|------|
| [クイックスタート](docs/plugin-dev/quickstart.md) | 最小構成プラグインの作成手順 |
| [plugin.ymlリファレンス](docs/plugin-dev/plugin-yml-reference.md) | プラグイン定義ファイルの全フィールド |
| [compose.ymlガイドライン](docs/plugin-dev/compose-yml-guidelines.md) | Docker Compose設定のベストプラクティス |

### devbase開発者向け

| ドキュメント | 内容 |
|-------------|------|
| [アーキテクチャ](docs/developer/architecture.md) | ディレクトリ構造、モジュール設計、データフロー |
| [コントリビューション](docs/developer/contributing.md) | 開発環境構築、コーディング規約、PRルール |
| [拡張ガイド](docs/developer/extending.md) | 新コマンド・コレクター・イメージの追加方法 |

## リンク

- **リポジトリ**: [devbasex/devbase](https://github.com/devbasex/devbase)
- **Issue報告**: [GitHub Issues](https://github.com/devbasex/devbase/issues)
