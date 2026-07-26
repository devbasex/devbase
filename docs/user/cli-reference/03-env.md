# env グループ

[CLI リファレンス目次に戻る](README.md)

環境変数の管理を行うコマンド群です。詳細は [環境変数ガイド](../environment-variables.md) を参照してください。

## `devbase env init`

環境変数の対話式初期セットアップを実行します。

```
devbase env init [--reset]
```

| オプション | 説明 |
|-----------|------|
| `--reset` | 既存の設定をリセットして再設定 |

## `devbase env sync`

ソースファイル（`~/.aws/config` 等）の変更を検出し、環境変数を再同期します。

```
devbase env sync
```

## `devbase env list`

設定済みの環境変数を一覧表示します。

```
devbase env list [-g|-p] [-r] [-k]
```

| オプション | 説明 |
|-----------|------|
| `-g` | グローバル変数のみ表示 |
| `-p` | プロジェクト変数のみ表示 |
| `-r` | 値も表示（デフォルトではキーのみ） |
| `-k` | キー名でソート |

```bash
# グローバル変数のみ、値付きで表示
devbase env list -g -r

# プロジェクト変数をキー名順で表示
devbase env list -p -k
```

## `devbase env set`

環境変数を設定します。

```
devbase env set KEY=VALUE [-p]
```

| オプション | 説明 |
|-----------|------|
| `-p` | プロジェクトレベルに設定（デフォルトはグローバル） |

```bash
# グローバルに設定
devbase env set ANTHROPIC_API_KEY=sk-xxx

# プロジェクトレベルに設定
devbase env set GCP_ACTIVE_PROFILE=my-project -p
```

## `devbase env get`

環境変数の値を取得します。

```
devbase env get KEY
```

```bash
devbase env get AWS_PROFILE
```

## `devbase env delete`

環境変数を削除します。

```
devbase env delete KEY
```

## `devbase env edit`

デフォルトエディタで `.env` ファイルを開きます。

```
devbase env edit
```

## `devbase env project`

プロジェクト固有の環境変数を対話式で設定します。

```
devbase env project
```

## `devbase env export`

複数プロジェクトの `.env` 群を暗号化したまま 1 つのバンドルにまとめて書き出します。

```
devbase env export <bundle>
```

オプション（age 鍵 / passphrase / S3 入出力など）の詳細は
[環境変数の export / import ガイド](../env-export-import.md#devbase-env-export-リファレンス)を参照してください。

## `devbase env import`

`devbase env export` で作成したバンドルを復号し、環境変数を取り込みます。

```
devbase env import <bundle>
```

`--dry-run` での確認や identity 鍵指定などの詳細は
[環境変数の export / import ガイド](../env-export-import.md#devbase-env-import-リファレンス)を参照してください。
