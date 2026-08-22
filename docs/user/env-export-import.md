# 環境変数の export / import ガイド

`devbase env export` / `devbase env import` は、複数プロジェクトにまたがる `.env` 群を **暗号化したまま 1 つのバンドル**にまとめ、別マシン・別ストレージ・チーム内で再利用するためのコマンドです。

> **どんなときに使うか**
> - 新しい開発マシン / WSL / コンテナで `devbase` を再構築するときに、認証情報・API キー一式を一括移植したい
> - チームで「同じ環境」を共有したい (S3 経由でローテ済みクレデンシャルを配布する等)
> - 個別 `.env` を `scp -r` する代わりに、機密を暗号化したまま安全に転送したい

## 目次

- [概要 / 対象ファイル](#概要--対象ファイル)
- [クイックスタート](#クイックスタート)
- [バンドル構造](#バンドル構造)
- [暗号化 (age)](#暗号化-age)
- [入出力先 (ローカル / stdio / S3)](#入出力先-ローカル--stdio--s3)
- [`devbase env export` リファレンス](#devbase-env-export-リファレンス)
- [`devbase env import` リファレンス](#devbase-env-import-リファレンス)
- [`.env.sources.yml` の扱い](#envsourcesyml-の扱い)
- [バックアップとロールバック](#バックアップとロールバック)
- [典型ワークフロー](#典型ワークフロー)
- [トラブルシューティング](#トラブルシューティング)

## 概要 / 対象ファイル

`devbase env export` がバンドルに含めるのは以下の 3 種類のファイルです:

| ファイル | 役割 | 機密性 | 既定で含む |
|---|---|---|:---:|
| `$DEVBASE_ROOT/.env` | グローバル変数 (`AWS_CONFIG_BASE64` などの認証情報) | 高 | ✓ |
| `$DEVBASE_ROOT/projects/<name>/.env` | プロジェクト固有変数 (API キー / DB パスワード等) | 高 | ✓ |
| `$DEVBASE_ROOT/.env.sources.yml` | コレクターの同期メタデータ (ファイルハッシュ / 同期時刻) | 中 | ✓ |

`$DEVBASE_ROOT/projects/<name>/env` (公開可能な雛形, git 管理対象) は **対象外** です。雛形は git で配布する設計のためバンドルに含めません。

3 レベル構造の全体像は [環境変数ガイド](environment-variables.md) を参照してください。

## クイックスタート

```bash
# 既存マシンで export (~/.ssh/id_ed25519.pub があれば暗号化キー指定省略可)
devbase env export ./bundle.dbenv

# バンドルを転送 (scp / S3 / メール添付など、暗号化済みなので経路は問わない)
scp ./bundle.dbenv newhost:/tmp/

# 新しいマシンで import (~/.ssh/id_ed25519 があれば identity 指定省略可)
ssh newhost
devbase env import /tmp/bundle.dbenv
```

import は **既定で `merge=keep-existing`** です。既存の `.env` に同じキーがあれば**保持**し、新規キーだけが追加されます。確認したいときは:

```bash
devbase env import /tmp/bundle.dbenv --dry-run
```

書き込みは行わず、追加 / 上書き / スキップされるキーが一覧表示されます。

## バンドル構造

バンドルファイル ( `*.dbenv` または `*.dbenv.tar.gz` ) は内部的に **tar.gz**、外側を **age** で暗号化した 1 つのファイルです。

```
manifest.yml          # version / created_at / 各ファイルの sha256
env/global.env        # $DEVBASE_ROOT/.env をそのままコピー
env/sources.yml       # .env.sources.yml (--no-metadata で除外可)
env/projects/<name>/.env
...
```

`manifest.yml` の例:

```yaml
version: 1
created_at: '2026-05-21T10:00:00+09:00'
devbase_version: 3.0.0
files:
  - path: env/global.env
    sha256: <64 文字 hex>
    origin: $DEVBASE_ROOT/.env
  - path: env/projects/carmo/.env
    sha256: <64 文字 hex>
    origin: $DEVBASE_ROOT/projects/carmo/.env
```

import 時は以下を検証します:

- `manifest.version` が devbase 本体のサポート最大値以下であること
- 各ファイルの sha256 が manifest と一致すること
- manifest に記載のないファイルがバンドル内に存在しないこと

`version` が大きすぎる場合 (= 新しい devbase で作られたバンドルを古い devbase で開いた場合) は明示的にエラーになり、devbase 本体の更新を促すメッセージが出ます。

## 暗号化 (age)

devbase は [age](https://age-encryption.org/) (`pyrage` 同梱) でバンドルを暗号化します。鍵の渡し方は **recipient 公開鍵** / **identity 秘密鍵** / **passphrase** の 3 通りで、export と import で対称に使い分けます。

### 鍵種別

| 鍵 | recipient (export 用) | identity (import 用) | 備考 |
|---|---|---|---|
| age X25519 (`age-keygen` 生成) | `age1...` | `AGE-SECRET-KEY-1...` | age ネイティブ、最も推奨 |
| OpenSSH ed25519 (`~/.ssh/id_ed25519`) | `ssh-ed25519 AAAA...` | `~/.ssh/id_ed25519` | そのまま使える |
| OpenSSH RSA (`~/.ssh/id_rsa`) | `ssh-rsa AAAA...` | `~/.ssh/id_rsa` | そのまま使える |
| OpenSSH ECDSA / DSA | ✗ | ✗ | **age 非対応**。下記参照 |
| scrypt パスフレーズ | (鍵不要) | (鍵不要) | `--passphrase-env` / `--passphrase-stdin` |

`--recipient` には公開鍵文字列を直接渡すか、`@PATH` でファイル参照できます (例: `--recipient @~/.ssh/id_ed25519.pub`)。`--identity` は秘密鍵ファイルのパスを渡します。

### 既定鍵

- export の `--recipient` 省略時: **`~/.ssh/id_ed25519.pub` → `~/.ssh/id_rsa.pub`** を順に探し、存在する公開鍵を使用
- import の `--identity` 省略時: **`~/.ssh/id_ed25519` → `~/.ssh/id_rsa`** を順に探し、存在する秘密鍵を使用

どちらも存在しない場合はエラーになります。明示指定 (`--recipient` / `--identity`) するか、`age-keygen` で age 専用鍵を生成してください。

### ssh-ecdsa 鍵しか持っていない場合

age は ssh-ecdsa / ssh-dss に対応していません。`ssh-ed25519` をまだ持っていない場合は、いずれかの方法で鍵を用意してください:

```bash
# 方法 1: ed25519 鍵を作る (汎用、SSH と兼用可)
ssh-keygen -t ed25519

# 方法 2: age 専用鍵を作る (この用途だけに使いたい場合)
age-keygen -o ~/.config/devbase/age.key
# 公開鍵は最後の行に "Public key: age1..." と表示される
```

### passphrase ベース

CI など鍵配布が難しい環境では passphrase 方式が使えます。**コマンドラインに直接書かない** (プロセス一覧に残るため) のがルールです。

```bash
# 環境変数経由
DEVBASE_BUNDLE_PASS='change-me-strong' devbase env export ./bundle.dbenv \
  --passphrase-env DEVBASE_BUNDLE_PASS

# stdin 経由 (パイプ運用)
echo 'change-me-strong' | devbase env export ./bundle.dbenv --passphrase-stdin
```

> tty で `--passphrase-stdin` を指定した場合は `getpass` でエコー抑止された対話プロンプトに切り替わるので、パスフレーズが画面に出ません。

### 平文 export (デバッグ用途のみ)

通常は暗号化必須ですが、デバッグや構造確認のためにあえて平文で書き出したい場合は `--force-unencrypted` を明示します:

```bash
devbase env export ./bundle.dbenv.tar.gz --force-unencrypted
```

- 拡張子は意図的に `*.dbenv.tar.gz` (拡張子で暗号化有無を判別できるようにするため)
- ファイル中に `KEY` / `SECRET` / `TOKEN` / `PASSWORD` / `CREDENTIALS` / `BASE64` を含むキーが見つかると **強い警告**が出ます
- ファイルパーミッションは引き続き `0600` で書き出されます

## 入出力先 (ローカル / stdio / S3)

`DEST` / `SOURCE` には以下を指定できます:

| 形式 | 例 | 用途 |
|---|---|---|
| ローカルファイル | `./bundle.dbenv`, `/tmp/x.dbenv` | 既定。1 ファイルとして保存 |
| `file://` URI | `file:///tmp/x.dbenv`, `file://localhost/tmp/x.dbenv` | URI 形式が必要なツールとの連携 |
| stdio | `-` | パイプ運用 (gpg / age と組み合わせる、ssh 経由など) |
| S3 URI | `s3://bucket/path/to/bundle.dbenv` | チーム共有 / クラウドバックアップ |

### S3 の暗号化要件

S3 への書き込み時は以下が **常に** 適用されます (`--force-unencrypted` でも上書きできません):

- オブジェクト個別の SSE (`aws:kms` 既定、`AES256` も選択可)
- export 前にバケット側の既定暗号化を `GetBucketEncryption` で確認
  - 未設定の場合は **export を拒否** (`--unsafe-allow-unencrypted-bucket` で明示的にバイパスは可能)
  - `AccessDenied` で確認できない場合も既定では拒否 (権限を付けるか同フラグでバイパス)

S3 関連の環境変数:

| 変数 | 役割 | 既定 |
|---|---|---|
| `DEVBASE_S3_SSE` | オブジェクト単位の SSE 種別 (`aws:kms` / `AES256`) | `aws:kms` |
| `DEVBASE_S3_SSE_KMS_KEY_ID` | `aws:kms` 時の KMS 鍵 ID | (バケット既定) |
| `DEVBASE_S3_ENDPOINT_URL` | カスタムエンドポイント (MinIO / LocalStack 用) | (AWS S3) |
| `DEVBASE_S3_REGION` | リージョン上書き | (AWS SDK 設定に依存) |

`AWS_PROFILE` / `AWS_REGION` / `AWS_ACCESS_KEY_ID` 等 boto3 が認識する標準変数はそのまま尊重されます。

### stdio (パイプ運用)

`DEST='-'` / `SOURCE='-'` で stdout / stdin を使えます。GPG など別の暗号化ツールと組み合わせたい場合に便利です:

```bash
# devbase の age 暗号化を切って GPG で再暗号化したい (極めて例外的な構成)
devbase env export - --force-unencrypted | gpg --encrypt -r alice@example.com > bundle.gpg

# 逆方向
gpg --decrypt bundle.gpg | devbase env import -
```

> **制約**: `SOURCE='-'` と `--passphrase-stdin` は **併用不可** (どちらも stdin を要求するため衝突します。import 側のみエラーになります)。`DEST='-'` (export) は stdin (passphrase) と stdout (bundle) で別ストリームを使うため `--passphrase-stdin` と併用できます。

## `devbase env export` リファレンス

```
devbase env export [DEST] [options]
```

### 引数

- `DEST`: 出力先。省略時は `./devbase-env-<YYYYMMDD-HHMMSS>.dbenv` (`--force-unencrypted` 時は `.dbenv.tar.gz`)

### オプション

| オプション | 説明 |
|---|---|
| `--include-project NAME` | 対象プロジェクトを限定 (複数指定可) |
| `--exclude-project NAME` | 除外プロジェクト (複数指定可) |
| `--no-global` | グローバル `.env` を含めない |
| `--no-metadata` | `.env.sources.yml` を含めない |
| `--force-unencrypted` | 平文 tar.gz として書き出す (機密キー検知時は警告) |
| `--recipient KEY` | age 公開鍵で暗号化 (複数指定可)。`age1...` / `ssh-ed25519 ...` / `ssh-rsa ...` / `@PATH` |
| `--passphrase-env VAR` | 環境変数 VAR からパスフレーズ取得 |
| `--passphrase-stdin` | stdin の最初の行をパスフレーズとして使用 |
| `--unsafe-allow-unencrypted-bucket` | S3: バケット既定暗号化未設定でも export を許可 (オブジェクト単位の SSE は引き続き付与) |

### 使用例

```bash
# 既定鍵 (~/.ssh/id_ed25519.pub or id_rsa.pub) で暗号化
devbase env export ./bundle.dbenv

# 複数 recipient (チームメンバー全員に配布)
devbase env export ./team.dbenv \
  --recipient @~/.ssh/id_ed25519.pub \
  --recipient 'age1abc...' \
  --recipient @charlie.pub

# 特定プロジェクトのみ
devbase env export ./carmo.dbenv --include-project carmo

# S3 に直接保存 (KMS 暗号化)
DEVBASE_S3_SSE_KMS_KEY_ID=alias/devbase \
  devbase env export s3://my-bucket/envs/2026-05-23.dbenv \
  --recipient @~/.ssh/id_ed25519.pub
```

## `devbase env import` リファレンス

```
devbase env import SOURCE [options]
```

### 引数

- `SOURCE`: 入力元。ローカルパス / `s3://...` / `-` (stdin)

### オプション

| オプション | 説明 |
|---|---|
| `--merge MODE` | キー単位マージ。`keep-existing` (既定) / `prefer-incoming` |
| `--replace-keys KEY,...` | 指定キーのみバンドル値で上書き (残りは keep-existing 相当) |
| `--replace` | 既存 `.env` を丸ごと差し替え (バックアップは取る) |
| `--dry-run` | 書き込まず差分のみ表示 |
| `--identity FILE` | age / OpenSSH 秘密鍵ファイル (複数指定可) |
| `--passphrase-env VAR` | 環境変数 VAR からパスフレーズ取得 |
| `--passphrase-stdin` | stdin の最初の行をパスフレーズとして使用 |
| `--include-project NAME` | 対象プロジェクトを限定 |
| `--exclude-project NAME` | 除外プロジェクト |
| `--no-global` | グローバル `.env` を import しない |
| `--no-metadata` | バンドル内 sources.yml を完全に無視 |
| `--merge-metadata` | sources.yml で新規 source エントリのみ追加 |
| `--backup-dir DIR` | 上書き前バックアップの保存先 (既定: `$DEVBASE_ROOT/backups/env-import/<ts>`) |
| `--keep-last N` | backup-dir 内の古い backup を最新 N 個に整理 (既定 10、0 で無効) |

### merge モード比較

| モード | 既存にある同名キー | 既存に無いキー | 主な用途 |
|---|---|---|---|
| `--merge keep-existing` (既定) | **既存を残す** (skip) | 追加 | 既存環境を壊さず新規キーだけ取り込む |
| `--merge prefer-incoming` | バンドル値で上書き | 追加 | ローテ済みクレデンシャルを一斉配布 |
| `--replace-keys K1,K2,...` | K1/K2 のみ上書き、それ以外は keep-existing | 追加 | 特定キーだけピンポイント更新 |
| `--replace` | (ファイル単位で) バンドル内容で**丸ごと差し替え** | 追加 | クリーンな再同期 (バックアップ必須) |

`--replace` 以外のモードは、既存 `.env` 内の **コメント・空行・キー順** を保持したまま値だけ差し替えます。

### 使用例

```bash
# 既定: 既存を保持しつつ新規キーのみ追加
devbase env import ./bundle.dbenv

# 何が起こるか先に見たい
devbase env import ./bundle.dbenv --dry-run

# ローテ済み credentials を一斉配布
devbase env import ./bundle.dbenv --merge prefer-incoming

# 特定キーだけ更新 (例: AWS credentials のローテ)
devbase env import ./bundle.dbenv --replace-keys AWS_CONFIG_BASE64,AWS_SESSION_TOKEN

# 特定プロジェクトだけ復元
devbase env import ./bundle.dbenv --include-project carmo

# S3 から取得 + passphrase で復号
devbase env import s3://my-bucket/envs/2026-05-23.dbenv \
  --passphrase-env DEVBASE_BUNDLE_PASS
```

## `.env.sources.yml` の扱い

`.env.sources.yml` には **マシン固有の絶対パス・同期時刻・元ファイルのハッシュ** が含まれます。別マシンでそのまま上書きすると整合性が壊れるため、以下のポリシーで扱います:

- **既定**: import 時に既存 `.env.sources.yml` は **上書きしない**。バンドル内の sources.yml は `backups/env-import/<ts>/sources.yml.imported` に参照用コピーのみ残す
- `--no-metadata`: バンドル内 sources.yml を完全に無視 (既定挙動と等価だが明示用)
- `--merge-metadata`: バンドル側で新規に登場する source エントリのみ追加 (既存エントリの `origin_path` / `synced_at` などのマシン固有値は再計算されず、import 先環境の値が保持される)

## バックアップとロールバック

import は部分適用を最小化するため **2 フェーズ書き込み** + **ファイル単位 backup** で動きます。

1. **Phase 0 (backup)**: 全対象ファイルを `backups/env-import/<ts>/<relative>` にコピー (元ファイルが存在する場合のみ)
2. **Phase 1 (prepare)**: 全ファイルの新内容を `<target>.import.tmp` に 0600 で書き出し、全件成功するまで rename しない
3. **Phase 2 (commit)**: 全 tmp の書き出し成功を確認してから `os.replace` で順次差し替え

Phase 2 の途中で失敗した場合は backup から **best-effort で `_rollback()`** します。元ファイルが無かった (= 新規作成) ファイルは backup が無いので unlink で削除し、元の「不在」状態に戻します。

> **重要**: OS / FS の制約上、厳密な ACID は保証しません (途中の電源断やディスク full などは tmp 残骸を残しうる)。本来は別マシンへの初回投入のような大移動でのみ使い、稼働中の環境では `--dry-run` で確認してから実行してください。

### backup ディレクトリ

```
$DEVBASE_ROOT/backups/env-import/
  20260523-101530-123456/        # ts (microsecond + 連番付き、衝突回避)
    .env                          # 既存 global .env のコピー
    projects/alpha/.env           # 既存 project .env のコピー
    sources.yml.imported          # バンドル内 sources.yml の参照用コピー
  20260524-094210-456789/
    ...
```

ディレクトリ名はタイムスタンプ命名 (`YYYYMMDD-HHMMSS[-NNNNNN[-NN]]`)。同一秒に複数回 import しても上書きされません。

### 古い backup の GC

`--keep-last N` (既定 10) で古い backup を自動 GC します:

```bash
# 最新 5 個だけ残す
devbase env import ./bundle.dbenv --keep-last 5

# GC 無効化
devbase env import ./bundle.dbenv --keep-last 0
```

GC 対象は **devbase が生成するタイムスタンプ命名のディレクトリのみ**。`--backup-dir` で指定した親ディレクトリに無関係なファイル / サブディレクトリがあっても、それらは触らない設計です。

## 典型ワークフロー

### A. 新しいマシンへ移行

```bash
# 既存マシン
devbase env export ~/devbase-2026-05-23.dbenv

# 転送 (経路はなんでも良い、暗号化済み)
scp ~/devbase-2026-05-23.dbenv newhost:~

# 新マシン
ssh newhost
devbase env import ~/devbase-2026-05-23.dbenv
devbase env list  # 復元確認
```

### B. 単一マシンの定期バックアップ

```bash
# cron で週次バックアップ (~/.ssh/id_ed25519.pub があれば鍵指定不要)
0 3 * * 0 cd /home/me/devbase && devbase env export \
  ~/backups/devbase-$(date +\%Y\%m\%d).dbenv
```

### C. S3 経由のチーム共有

```bash
# 管理者: ローテ済みクレデンシャルを team 全員の鍵で暗号化して S3 へ
devbase env export s3://team-secrets/devbase/latest.dbenv \
  --recipient @keys/alice.pub \
  --recipient @keys/bob.pub \
  --recipient @keys/charlie.pub

# 各メンバー: 既存キーは保持しつつローテ分だけ更新
devbase env import s3://team-secrets/devbase/latest.dbenv \
  --replace-keys AWS_CONFIG_BASE64,GCP_CREDENTIALS_BASE64_default
```

### D. CI でローテキーを配布

```bash
# CI ジョブ: secret manager から passphrase を取って復号
export DEVBASE_BUNDLE_PASS=$(aws secretsmanager get-secret-value \
  --secret-id devbase/bundle-pass --query SecretString --output text)

devbase env import s3://team-secrets/devbase/latest.dbenv \
  --passphrase-env DEVBASE_BUNDLE_PASS \
  --merge prefer-incoming
```

## トラブルシューティング

### `バンドルは暗号化されていますが復号キーが指定されていません`

`~/.ssh/id_ed25519` も `~/.ssh/id_rsa` も無く、`--identity` / `--passphrase-*` も指定されていない状態です。export 時に使った鍵に対応する秘密鍵を `--identity` で渡してください。

### `OpenSSH 秘密鍵の解釈に失敗しました` / `age は ssh-ed25519 / ssh-rsa のみ対応です`

age が **ssh-ecdsa / ssh-dss に対応していない**ことが原因です。`age-keygen` で age 専用鍵を作るか、`ssh-keygen -t ed25519` で ed25519 鍵を作ってください。

### `passphrase 復号に失敗しました (パスフレーズが誤っている可能性があります)`

パスフレーズが間違っているか、バンドルが破損しています。`--passphrase-env` で渡した変数の中身に余計な改行 / 空白が無いかを確認してください。

### `manifest.version=N はこの devbase ではサポートされていません`

新しい devbase で作られたバンドルを古い devbase で開こうとしています。devbase 本体を更新してください。

### `S3 バケット 'X' のデフォルト暗号化が未設定です`

S3 バケットに既定の SSE が設定されていません。以下のいずれかで対応:

```bash
# 推奨: バケット側に SSE を有効化
aws s3api put-bucket-encryption --bucket X --server-side-encryption-configuration ...

# あるいは明示的にバイパス (オブジェクト単位の SSE は引き続き付与される)
devbase env export s3://X/key --unsafe-allow-unencrypted-bucket ...
```

### `平文 export に機密キーが含まれます`

`--force-unencrypted` で平文 tar.gz を作ろうとし、`AWS_CONFIG_BASE64` などの機密キーが検出されました。**警告であり継続します**が、保管・転送時の暗号化を強く推奨します。

### `SOURCE='-' (stdin) と --passphrase-stdin は併用できません`

stdin から同時に「バンドル本体」と「パスフレーズ」を読むことはできません。`--passphrase-env` で環境変数経由に切り替えるか、`SOURCE` をファイルパスにしてください。

### import 後に `.import.tmp` ファイルが残った

Phase 2 (commit) の途中で異常終了した可能性があります。次回 import 時に同じファイル名で書き直すので、通常は自動的にクリーンアップされます。気になる場合は `find $DEVBASE_ROOT -name '*.import.tmp' -delete` で削除できます。

## 関連ドキュメント

- [環境変数ガイド](environment-variables.md) — 3 レベル構造とコレクター
- [CLI リファレンス](cli-reference/README.md) — 全コマンド一覧
- [はじめに](getting-started.md) — 初回セットアップ
