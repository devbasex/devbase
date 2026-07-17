# devbase

Dockerベースの開発環境マネージャー。コンテナ化された開発環境を**Plugin**によるプロジェクト管理で提供します。

## 概要

devbaseは、Docker Composeを使った再現性の高い開発環境を提供するCLIツールです。プロジェクトはPluginとして外部リポジトリで管理され、`devbase plugin install`でインストールします。

### 主な特徴

- **Pluginベースのプロジェクト管理**: 外部リポジトリからプロジェクト設定をインストール・更新
- **コンテナ化された開発環境**: Docker Composeベースで再現性の高い環境を提供
- **豊富なツールセット**: Docker CLI、AWS CLI、gcloud SDK、Terraform、Node.js、AI CLIツールがプリインストール
- **複数コンテナの並行開発**: `devbase project scale`で既存コンテナを再起動せずにスケール可能
- **データ永続化**: 名前付きボリュームでコンテナ再起動後もデータを保持
- **スナップショット管理**: 共通ボリューム `devbase_home_ubuntu`（コンテナ内 `/persistent/ai`。AI 設定・共有ファイル）の増分バックアップ・復元・世代管理
- **環境変数の自動収集**: `devbase env init`でAWS/Git/GCP認証情報を対話的に設定
- **階層メニュー TUI**: `devbase list` のプロジェクト一覧（矢印キー移動・名前絞り込み対応）から起動・操作（up / down / login / ps / logs / scale / build / rebuild）を選択。画面最下部の常設メニュー（環境変数 / プラグイン / スナップショット / ステータス）へは ←→ キーで移動できます
- **イメージ再ビルド**: `devbase build [name] --no-cache` でキャッシュ無効の完全再ビルド。`devbase rebuild [name]`（= `build --expires=7`）はイメージが既定 7 日より古いときのみ再ビルドします

## クイックスタート

devbase 本体を**インストール**し、続けて**プラグインを導入してプロジェクトを起動**します。インストールはワンライナー（推奨）か手動のどちらかを選んでください。

### 1. インストール

#### ワンライナー（推奨）

```bash
curl -fsSL https://dl.basex.jp/i | bash && . ~/devbase/bin/rc
```

`~/devbase` に clone（既存なら更新）して `devbase init` まで自動実行します（uv の自動導入・PATH/補完の登録・`plugins.yml` 生成を含む）。末尾の `. ~/devbase/bin/rc` で**いま開いている端末**でも `devbase` が即使えます（新しく開くターミナルは自動で有効）。

> 配置先を `DEVBASE_INSTALL_DIR` で変えた場合は `~/devbase/bin/rc` も同じパスに合わせてください。`. ~/devbase/bin/rc` を省いた `... | bash` だけでも導入は完了します。

環境変数で挙動を上書きできます。

| 変数 | 既定値 | 用途 |
|------|--------|------|
| `DEVBASE_INSTALL_DIR` | `$HOME/devbase` | 配置先ディレクトリ |
| `DEVBASE_INSTALL_REPO` | `https://github.com/devbasex/devbase.git` | clone 元（fork / テスト用） |
| `DEVBASE_INSTALL_REF` | `main` | チェックアウトする branch / tag |

```bash
# 例: 別ディレクトリへ特定タグを入れる（パイプではなく保存実行でも env は同様に効きます）
DEVBASE_INSTALL_DIR=~/work/devbase DEVBASE_INSTALL_REF=v1.2.3 \
  bash -c "$(curl -fsSL https://dl.basex.jp/i)"
```

#### 手動

```bash
git clone https://github.com/devbasex/devbase.git
cd devbase
./bin/devbase init
. ./bin/rc                            # いまのシェルで devbase を有効化（PATH / 補完）
```

### 2. プラグインの導入とプロジェクト起動

インストール方法を問わず、以降の手順は共通です。

```bash
# プラグインのインストール
devbase plugin repo add user/repo    # リポジトリ登録（init でサンプルレジストリ devbasex/devbase-samples は自動登録済み）
devbase plugin install <name>        # Plugin名でインストール

# プロジェクトの起動
cd ~/devbase/projects/your-project   # 手動セットアップでは clone 先 (例: ./projects/your-project)
devbase env init                     # 環境変数の設定（初回のみ）
devbase up                           # コンテナを起動（初回はイメージビルドを含むため時間がかかります）
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
| `project` | — | プロジェクト管理（up / down / login / ps / logs / scale / build / rebuild / list） |
| `env` | — | 環境変数管理（init / sync / list / set / get / delete / edit / project / export / import） |
| `plugin` | `pl` | プラグイン管理（list / install / uninstall / update / info / sync / migrate / repo） |
| `snapshot` | `ss` | スナップショット管理（create / list / restore / copy / delete / rotate） |

> **`container`（略記 `ct`）グループは非推奨です。** `devbase project <sub>` のエイリアスとして当面動作しますが、非推奨警告を表示します。新しいコマンドは `project` を使用してください。

- **ショートカット**: `up [name]`, `down [name]`, `login [index]`, `build [image]`, `ps [name]`, `scale [name] <num>`, `rebuild [name]`, `list` はトップレベルから直接使用可能（`project` グループへ自動転送。`logs` はシノニムを持ちません）。なお `build` のみ挙動が一部異なります（詳細は [CLI リファレンス](docs/user/cli-reference.md#ショートカットコマンド)）
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
| [環境変数の export/import ガイド](docs/user/env-export-import.md) | バンドル形式・age 暗号化・S3 連携・merge/replace の運用 |
| [コンテナ操作ガイド](docs/user/container-operations.md) | ライフサイクル、並行開発、ボリューム構造 |
| [スナップショットガイド](docs/user/snapshot-guide.md) | 増分バックアップ、世代管理、復元手順 |
| [トラブルシューティング](docs/user/troubleshooting.md) | カテゴリ別の問題と解決策 |
| [Orca 削除の移行ガイド](docs/user/orca-removal-migration.md) | 旧 Orca(SSH) 接続の廃止と Remote-SSH への移行手順 |

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
