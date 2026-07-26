# plugin (pl) グループ

[CLI リファレンス目次に戻る](README.md)

プラグインの管理を行うコマンド群です。

## `devbase plugin list`

インストール済み、または利用可能なプラグインを一覧表示します。

```
devbase plugin list [--available]
```

| オプション | 説明 |
|-----------|------|
| `--available` | リポジトリから取得可能なプラグインを表示 |

## `devbase plugin install`

プラグインをインストールします。

```
devbase plugin install <source>
```

ソースの指定形式:

| 形式 | 説明 | 例 |
|------|------|----|
| 名前のみ | 登録済みリポジトリから検索 | `devbase plugin install adminer` |
| リポジトリ直接指定 | 特定リポジトリのプラグイン | `devbase plugin install user/repo:plugin-name` |
| 全プラグイン一括 | リポジトリの全プラグインをインストール | `devbase plugin install user/repo --all` |
| ローカルリンク | ローカルディレクトリからリンク | `devbase plugin install /path:plugin-name --link` |

## `devbase plugin uninstall`

プラグインをアンインストールします。

```
devbase plugin uninstall <name>
```

## `devbase plugin update`

プラグインを最新バージョンに更新します。

```
devbase plugin update [name]
```

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `name` | いいえ | 更新するプラグイン名（省略時は全プラグイン） |

## `devbase plugin info`

プラグインの詳細情報を表示します。

```
devbase plugin info <name>
```

## `devbase plugin sync`

プロジェクトのシンボリックリンクを再同期します。

```
devbase plugin sync
```

## `devbase plugin migrate`

旧形式 (`plugins/<name>` へのコピー) でインストールされたプラグインを、`repos/` 配下の永続クローンへ移行します。`install` / `update` 実行時にも自動で呼び出されるため、通常は手動実行不要です。

```
devbase plugin migrate
```

移行の挙動:

| 状況 | 動作 |
|---|---|
| コピーがクローンと一致 | 旧コピーを削除し `repos/` へ移行 (migrated) |
| コピーにローカル変更あり | 旧コピーを `plugins/<name>.bak` として保全 (preserved、手動で reconcile) |
| 移行できない (ソース未登録 等) | スキップしてエラーを表示 (skipped) |

`--link` でインストールしたプラグインは移行対象外です。

## `devbase plugin repo add`

プラグインリポジトリを登録します。

```
devbase plugin repo add <url>
```

```bash
# GitHub ショートハンド
devbase plugin repo add user/repo

# 完全な URL
devbase plugin repo add https://github.com/user/repo.git
```

## `devbase plugin repo remove`

リポジトリの登録を削除します。

```
devbase plugin repo remove <name>
```

## `devbase plugin repo list`

登録済みリポジトリの一覧を表示します。

```
devbase plugin repo list
```

## `devbase plugin repo refresh`

プラグイン一覧をリポジトリから再取得します。

```
devbase plugin repo refresh [name]
```

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `name` | いいえ | 更新するリポジトリ名（省略時は全リポジトリ） |
