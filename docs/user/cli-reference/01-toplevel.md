# トップレベルコマンド

[CLI リファレンス目次に戻る](README.md)

## `devbase init`

devbase の初期セットアップを実行します。

```
devbase init
```

実行内容:
- `bin/devbase` を PATH に追加（`~/.bashrc` / `~/.zshrc`）
- シェル補完スクリプトの登録
- `plugins.yml` の作成（存在しない場合）

## `devbase status`

現在の環境の状態をまとめて表示します。

```
devbase status
```

表示項目:
- コンテナの状態（起動中 / 停止中 / 未ビルド）
- インストール済みプラグイン一覧
- 環境変数の設定状況
- スナップショットの状態

## `bin/rc`（いまのシェルで有効化）

`devbase init` 後に **いま開いているシェル**で devbase（PATH / 補完）を即時有効化するための source 用スクリプトです。`devbase` のサブコマンドではなく、`bin/rc` を直接 source して使います。

```bash
./bin/devbase init
. ./bin/rc        # = source ./bin/rc （bash / zsh 共通）
```

`bin/rc` は自身の場所から `DEVBASE_ROOT` を解決し、`DEVBASE_ROOT/bin` を PATH へ追加（冪等）したうえで、シェル補完を読み込みます（`init` が rc ファイルへ追記する有効化と同じ内容）。新しく開くシェルは init が rc に追記したブロックで自動有効化されるため、この手順は不要です。
