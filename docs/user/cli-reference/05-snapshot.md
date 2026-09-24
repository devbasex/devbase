# snapshot (ss) グループ

[CLI リファレンス目次に戻る](README.md)

スナップショットの管理を行うコマンド群です。詳細は [スナップショットガイド](../snapshot-guide.md) を参照してください。

## `devbase snapshot create`

スナップショットを作成します。

```
devbase snapshot create [--name NAME] [--full]
```

| オプション | 説明 |
|-----------|------|
| `--name NAME` | スナップショット名を指定（デフォルトはタイムスタンプ） |
| `--full` | フルバックアップを強制作成 |

```bash
# 自動命名で差分スナップショット
devbase snapshot create

# 名前付きフルバックアップ
devbase snapshot create --name before-upgrade --full
```

## `devbase snapshot list`

スナップショットの一覧を表示します。

```
devbase snapshot list
```

## `devbase snapshot restore`

スナップショットから復元します。

```
devbase snapshot restore <name> [--point N]
```

| パラメータ / オプション | 必須 | 説明 |
|----------------------|------|------|
| `<name>` | はい | 復元するスナップショット名 |
| `--point N` | いいえ | N 番目の差分まで復元（省略時は最新まで全適用） |

> **Warning:** 復元前に現在の状態が `pre-restore-<timestamp>` として自動バックアップされます。

## `devbase snapshot copy`

スナップショットをコピーします。

```
devbase snapshot copy <name> <new_name>
```

## `devbase snapshot delete`

スナップショットを削除します。

```
devbase snapshot delete <name>
```

## `devbase snapshot rotate`

古い世代のスナップショットを削除します。世代はアカウントグループ（対象ボリュームの組）ごとの
系列に分かれ、系列ごとに `--keep` 世代を残したうえで、全体の上限を超えた分を系列をまたいで
古い順に削除します。各系列の最新の世代は削除しません。

```
devbase snapshot rotate [--keep N] [--max-total M]
```

| オプション | 説明 |
|-----------|------|
| `--keep N` | アカウントグループ（系列）ごとに保持する世代数（デフォルト: `3`） |
| `--max-total M` | すべてのグループを合わせて保持する世代数の上限（デフォルト: `N × 3`）。各グループの最新の世代は上限を超えても残す |

`N` と `M` は 1 以上です。0 以下はエラーで終了コード 1 になります。

**どちらの指定も、手動で実行したその 1 回だけに効きます。** 値は保存されず、`devbase up` /
`devbase down` の自動ローテーションは既定（グループごとに 3・全体で 9）で動きます。
