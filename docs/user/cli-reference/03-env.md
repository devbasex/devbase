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
devbase env delete KEY [-p]
```

| オプション | 説明 |
|-----------|------|
| `-p` | プロジェクト設定から削除（デフォルトはグローバル）。`projects/<name>` 配下で実行してください |

```bash
# グローバルから削除
devbase env delete OLD_API_KEY

# カレントプロジェクトの設定から削除
devbase env delete GCP_ACTIVE_PROFILE -p
```

## `devbase env edit`

デフォルトエディタで設定を開きます。設定が暗号化されている場合は、復号した内容を一時ファイルで編集し、保存時に再暗号化します。

```
devbase env edit [-p]
```

| オプション | 説明 |
|-----------|------|
| `-p` | カレントプロジェクトの設定を開く（デフォルトはグローバル）。`projects/<name>` 配下で実行してください |

## `devbase env project`

プロジェクト固有の環境変数を対話式で設定します。

```
devbase env project
```

## `devbase env keygen`

設定の暗号化に使う devbase 専用の age 鍵を生成します。鍵ファイルは `0600`、置き場のディレクトリは `0700` で作成されます。

```
devbase env keygen [--force] [-y|--yes]
```

| オプション | 説明 |
|-----------|------|
| `--force` | 既存の鍵を作り直す。**旧鍵でしか復号できない機密は失われます** |
| `-y`, `--yes` | `--force` 時の確認プロンプトを省略（CI 等での自動実行用） |

鍵の場所は次のとおりで、コマンドラインからは指定できません（生成先と復号時の探索先を必ず一致させるため）。別の場所に置きたい場合は `DEVBASE_AGE_KEY_FILE` を設定してから実行します。

| 指定 | 鍵ファイルのパス |
|-----|-----------------|
| 既定 | `~/.config/devbase/age/keys.txt`（`XDG_CONFIG_HOME` があればその配下） |
| `DEVBASE_AGE_KEY_FILE` | 指定したパスをそのまま使用 |

```bash
# 既定の場所に生成する（既に鍵があれば公開鍵を表示するだけで何もしない）
devbase env keygen

# 置き場を変えて生成する
DEVBASE_AGE_KEY_FILE=~/keys/devbase-age.txt devbase env keygen

# 既存の鍵を捨てて作り直す（確認プロンプトあり）
devbase env keygen --force
```

> **鍵のバックアップは必須です。** この鍵を失うと、暗号化した機密は誰にも復号できません（devbase 側にも復旧手段はありません）。生成後に表示される鍵ファイルを、パスワード管理ツールなど端末とは別の場所へ必ず複製してください。鍵は全ワークスペース共通のため、`--force` で作り直すと他のワークスペースで暗号化した機密も復号できなくなります。

## `devbase env encrypt`

平文で保存されている設定を、暗号化ストア (`$DEVBASE_ROOT/secrets/`) へ移します。事前に `devbase env keygen` で鍵を作っておく必要があります。

```
devbase env encrypt [--project NAME]... [--dry-run] [-y|--yes]
```

| オプション | 説明 |
|-----------|------|
| `--project NAME` | 対象を指定プロジェクトだけに絞る（繰り返し指定可）。指定すると共通設定は対象外になります |
| `--dry-run` | 変更内容と構成ファイルの差分を表示するだけで、何も書き換えません |
| `-y`, `--yes` | 確認プロンプトを省略 |

実行すると次の 3 つが行われます。

1. 平文の設定を暗号化して `secrets/` 配下へ保存する
2. **暗号化した内容を読み戻して元と一致することを確認**してから、元の平文を `backups/env-encrypt/<日時>/` へ退避する
3. 各プロジェクトの `compose.yml` から機密ファイルの参照をコメントアウトする（元の行はコメントとして残るため、`decrypt` で復元できます）

```bash
# 何が変わるかを先に確認する
devbase env encrypt --dry-run

# 共通設定とすべてのプロジェクトを暗号化する
devbase env encrypt

# 特定プロジェクトだけを暗号化する
devbase env encrypt --project web
```

> 退避した平文は**自動では消しません**。内容を確認したうえで、案内された `backups/env-encrypt/<日時>/` を削除してください。削除するまでは端末上に平文の認証情報が残ったままです。

## `devbase env decrypt`

暗号化された設定を平文へ戻します。`encrypt` と対になる退避コマンドです。

```
devbase env decrypt [--project NAME]... [--dry-run] [-y|--yes]
```

オプションは `encrypt` と同じです。`compose.yml` のコメントアウトも元に戻るため、暗号化前の状態へそのまま復帰します。

```bash
devbase env decrypt --dry-run
devbase env decrypt
```

## `devbase env exec`

復号した機密を環境変数として渡した状態で、任意のコマンドを実行します。値はその子プロセスの環境変数としてのみ渡り、ファイルには書き出されません。

```
devbase env exec -- CMD [ARGS...]
```

起動ラッパーは共通の機密ファイルを読み込まないため、ホスト側で機密を必要とする処理（Docker Compose の変数展開など）はこのコマンドを通します。devbase 自身の `devbase build` も内部でこれを使っています。

```bash
# コンテナに渡る値を確認する
devbase env exec -- printenv ANTHROPIC_API_KEY

# 機密を必要とする compose 操作を手で実行する
devbase env exec -- docker compose config
```

> `devbase env exec -- printenv` のように値を表示するコマンドは、画面共有や端末ログに認証情報がそのまま残ります。実行する場面に注意してください。

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
