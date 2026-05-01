# トラブルシューティング

devbase の使用中に発生する可能性のある問題と、その解決策をカテゴリ別にまとめています。

## 1. セットアップ・初期化

### `devbase` コマンドが見つからない

**症状:**

```
bash: devbase: command not found
```

**原因と解決策:**

`devbase init` が未実行、または PATH が反映されていない可能性があります。

```bash
# devbase init を再実行
cd /path/to/devbase
./bin/devbase init

# シェルを再読み込み
source ~/.bashrc   # Bash の場合
source ~/.zshrc    # Zsh の場合
```

PATH に含まれているか確認するには:

```bash
echo $PATH | tr ':' '\n' | grep devbase
```

### シェル補完が効かない

**症状:**

Tab キーを押してもコマンドが補完されない。

**原因と解決策:**

シェルの再読み込みが必要です。

```bash
# シェルを再読み込み
source ~/.bashrc   # Bash の場合
source ~/.zshrc    # Zsh の場合
```

それでも効かない場合は `devbase init` を再実行してください。

### `devbase init` でエラーが発生する

**症状:**

`devbase init` の実行中にエラーが表示される。

**原因と解決策:**

```bash
# 前提条件を確認
docker --version          # Docker Engine 20.10 以上
docker compose version    # Docker Compose v2.x 以上
python3 --version         # Python 3.10 以上
git --version             # Git がインストールされているか
```

Docker Daemon が起動しているか確認してください。

```bash
docker info
```

## 2. コンテナ関連

### コンテナが起動しない

**症状:**

`devbase up` がエラーで失敗する。

**原因と解決策:**

**Docker Daemon が停止している場合:**

```bash
# Docker の状態確認
docker info

# Docker Daemon を起動（Linux）
sudo systemctl start docker
```

**イメージが未ビルドの場合:**

```bash
# イメージをビルド
devbase build
```

**ポートが競合している場合:**

```bash
# 使用中のポートを確認
docker ps
```

### コンテナにログインできない

**症状:**

`devbase login` がエラーで失敗する。

**原因と解決策:**

```bash
# コンテナの状態を確認
devbase ps

# コンテナが起動していない場合
devbase up

# 停止中のコンテナも含めて確認
devbase ps -a
```

### コンテナ再起動後にデータが消えた

**症状:**

`devbase down` → `devbase up` の後にファイルが見つからない。

**原因と解決策:**

ボリューム名が変更されていないか確認してください。プロジェクト名やコンテナインデックスが変わると、異なるボリュームがマウントされます。

```bash
# Docker ボリュームの一覧
docker volume ls | grep devbase

# 以前のボリュームが存在するか確認
docker volume ls | grep <project>_work
```

データが `/work` ではなくコンテナ内の別のパスに保存されていた場合、コンテナの削除とともに失われます。重要なデータは必ず `/work` に配置してください。

### コンテナのログを確認したい

**症状:**

コンテナの動作がおかしいが、原因がわからない。

**原因と解決策:**

```bash
# 最新のログを表示
devbase container logs

# リアルタイムで追跡
devbase container logs -f

# 末尾50行のみ表示
devbase container logs --tail 50
```

## 3. 環境変数関連

### 環境変数が反映されない

**症状:**

`devbase env set` で設定した値がコンテナ内で参照できない。

**原因と解決策:**

環境変数は `devbase up` 実行時にコンテナに注入されます。起動中のコンテナには反映されません。

```bash
# コンテナを再起動
devbase down
devbase up
```

読み込み順序（後勝ち）も確認してください:

1. グローバル `.env`（`devbase/.env`）
2. プロジェクト設定 `env`（`projects/*/env`）
3. プロジェクト機密 `.env`（`projects/*/.env`）

同じキーがより優先度の高いファイルで別の値に設定されている可能性があります。

```bash
# 全レベルの設定を確認
devbase env list -r

# グローバルのみ確認
devbase env list -g -r

# プロジェクトのみ確認
devbase env list -p -r
```

### AWS 認証が通らない

**症状:**

コンテナ内で `aws` コマンドが認証エラーになる。

**原因と解決策:**

ホストマシンの AWS 設定が変更されたが、devbase に同期されていない可能性があります。

```bash
# ソースファイルの変更を検出して同期
devbase env sync

# コンテナを再起動して反映
devbase down
devbase up
```

SSO を使用している場合、SSO セッションの有効期限が切れている可能性があります。ホストマシンで SSO ログインを再実行してから同期してください。

### GCP 認証プロファイルを切り替えたい

**症状:**

プロジェクトごとに異なる GCP プロジェクトを使用したい。

**原因と解決策:**

プロジェクトレベルで `GCP_ACTIVE_PROFILE` を設定します。

```bash
# プロジェクトレベルに設定
devbase env set GCP_ACTIVE_PROFILE=my-project -p

# コンテナを再起動
devbase down
devbase up
```

### 環境変数を誤って設定した

**症状:**

間違った値を設定してしまった。

**原因と解決策:**

```bash
# 値を上書き
devbase env set KEY=correct-value

# または削除
devbase env delete KEY

# エディタで直接編集
devbase env edit
```

## 4. プラグイン関連

### プラグインが見つからない

**症状:**

`devbase plugin install <name>` で「プラグインが見つかりません」と表示される。

**原因と解決策:**

```bash
# リポジトリの一覧を確認
devbase plugin repo list

# プラグイン一覧をリポジトリから再取得
devbase plugin repo refresh

# 利用可能なプラグインを確認
devbase plugin list --available

# リポジトリが未登録の場合は追加
devbase plugin repo add user/repo
```

### プラグインのインストールが失敗する

**症状:**

`devbase plugin install` がエラーで中断する。

**原因と解決策:**

```bash
# プラグインの詳細情報を確認
devbase plugin info <name>

# リポジトリを最新に更新
devbase plugin repo refresh

# 再インストール
devbase plugin install <name>
```

### シンボリックリンクの不整合

**症状:**

プラグインの更新後にファイルが見つからない、または古いバージョンが参照される。

**原因と解決策:**

```bash
# シンボリックリンクを再同期
devbase plugin sync
```

## 5. スナップショット関連

### 復元後にデータがおかしい

**症状:**

`devbase snapshot restore` 後に期待した状態になっていない。

**原因と解決策:**

全差分を適用すると最新状態になります。特定時点の状態が必要な場合は `--point N` を使用します。

```bash
# スナップショットのポイント数を確認
devbase snapshot list

# 特定のポイントまで復元
devbase snapshot restore <name> --point 1
```

復元前の状態は `pre-restore-<timestamp>` として自動保存されています。復元を取り消す場合:

```bash
# 復元前の状態に戻す
devbase snapshot restore pre-restore-20260221-150000
```

### バックアップ容量が大きい

**症状:**

ディスク容量が不足している。

**原因と解決策:**

```bash
# スナップショットの一覧とサイズを確認
devbase snapshot list

# バックアップディレクトリのサイズ確認
du -sh ${DEVBASE_ROOT}/backups/

# 不要な世代を削除（2世代のみ保持）
devbase snapshot rotate --keep 2

# 個別のスナップショットを削除
devbase snapshot delete <name>
```

### スナップショットの作成が遅い

**症状:**

`devbase snapshot create` に時間がかかる。

**原因と解決策:**

フルバックアップは `/work` ボリューム全体を圧縮するため、データ量に比例して時間がかかります。

- 差分バックアップ（`--full` なし）を使用すると、変更分のみのため高速です
- 大きな一時ファイルや不要なファイルを `/work` から削除してからバックアップしてください

## 6. ボリューム関連

### ディスク容量不足

**症状:**

Docker 操作やスナップショット作成がディスク容量不足で失敗する。

**原因と解決策:**

```bash
# Docker が使用しているディスク容量を確認
docker system df

# Docker ボリュームの一覧と詳細
docker volume ls

# 未使用のボリュームを確認
docker volume ls -f dangling=true
```

> **Warning:** 以下のコマンドは不要なリソースを削除します。実行前に重要なデータがないことを確認してください。

```bash
# 未使用のボリュームを削除
docker volume prune

# 未使用のイメージ、コンテナ、ネットワークも含めて削除
docker system prune
```

### 特定のボリュームを削除したい

**症状:**

不要になったプロジェクトのボリュームを削除したい。

**原因と解決策:**

```bash
# コンテナが停止していることを確認
devbase down

# 対象のボリュームを特定
docker volume ls | grep <project>

# ボリュームを削除
docker volume rm <volume_name>
```

## 問題が解決しない場合

上記の方法で解決しない場合は、以下の情報を添えて [GitHub Issues](https://github.com/devbasex/devbase/issues) に報告してください。

- `devbase status` の出力
- 実行したコマンドとエラーメッセージ
- OS とシェルのバージョン
- Docker と Docker Compose のバージョン
- 関連するログ（`devbase container logs`）
