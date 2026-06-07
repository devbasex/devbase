# PLAN03-1: devbase env export / import

> 元 issue: `issues/i03.md` 第1項 (devbase env export / import)
> ステータス: 着手可（未決事項すべて確定済み、2026-05-21）
> 関連 skill: `/ndf:issue-plan-strategy`, `/ndf:implementation-plan`

## 1. 背景と目的

devbase は以下の階層で環境変数を管理している:

| ファイル | 役割 | 機密性 |
|---|---|---|
| `$DEVBASE_ROOT/.env` | グローバル（AWS / GCP / Git credentials を base64 化したもの、`*_BASE64` キー群） | 高 |
| `$DEVBASE_ROOT/projects/<name>/.env` | プロジェクト固有変数（API キー、DB パスワード等） | 高 |
| `$DEVBASE_ROOT/projects/<name>/env` | プロジェクト固有変数の **公開可能な雛形**（git 管理） | 低 |
| `$DEVBASE_ROOT/.env.sources.yml` | 各 source の元ファイル・ハッシュ・同期時刻のメタデータ | 中 |

現状の課題:

- `.env` がプロジェクトごとに分散しており、新しいマシン / WSL / コンテナで devbase を再構築する際に **個別にコピーする必要**がある。
- バックアップ運用がユーザー個別であり、`devbase` 自身が責任を持って一括退避・復元できない。
- チームで「同じ環境を別マシンに移植」する手段が `scp -r` か手動コピーしかない（しかも機密が暗号化されないまま残る）。

本タスクのゴール:

- グローバル `.env` と全プロジェクトの `.env` を**1ファイルにまとめて export / import** できる CLI を追加する。
- まとめ方は複数提案し、運用要件に応じて選択できるようにする。
- 出力先 / 入力元としてローカルファイルだけでなく **外部ストレージ（S3 等）**を扱えるようにする。
- 機密情報を扱うため、**暗号化を既定**とし、誤って平文を流出させない設計にする。

## 2. 要件

### 2.1 機能要件

- `devbase env export [options]` で以下の対象をひとまとめにする:
  - `$DEVBASE_ROOT/.env`
  - `$DEVBASE_ROOT/projects/*/.env`（存在するもののみ）
  - `$DEVBASE_ROOT/.env.sources.yml`（メタデータ。任意で除外可）
- `devbase env import <source> [options]` で上記を復元する:
  - 既存 `.env` が存在する場合の merge / replace を選べる。
  - `--dry-run` で差分プレビューできる。
- 入出力先:
  - ローカルファイル（パス指定）
  - S3 URL（`s3://bucket/key`）
  - 標準入出力（パイプ運用 / GPG/age と組み合わせる用途）
  - ~~GCS URL（`gs://bucket/object`）~~（**廃案**: 利用見込みが小さく、boto3 + google-cloud-storage の依存増加に見合わないと判断したため。必要になった時点で別 PLAN として切り出す）
- 暗号化:
  - 既定で暗号化（後述、複数案）
  - 平文出力は既定で **拒否**。`--force-unencrypted` を明示した場合のみ許可し、その際も `*_BASE64` 等の機密キー検知時は **強い警告**を出す
  - 拡張子も暗号化有無で区別する: 暗号化済み `*.dbenv` / 平文 `*.dbenv.tar.gz`（ファイル名から判別可能にし、事故を防ぐ）

### 2.2 非機能要件

- バンドルファイルは **自己記述的**（バージョン・生成時刻・対象一覧をヘッダに含む）
- 復号鍵・パスフレーズは **環境変数 or ファイル経由**で渡し、コマンドラインに直接書かせない（プロセス一覧に残るため）
- 既存の `devbase env *` コマンド体系・引数命名規則と整合させる
- 失敗時の部分適用を避けるため import は 2 フェーズ方式（全ファイルの一時書き出し完了後に rename を実行）。途中失敗時は backup から best-effort で復旧する（厳密な ACID は OS / FS 制約上保証しない旨を docs に明記）

### 2.3 セキュリティ要件

- 出力ファイルのデフォルトパーミッションは `0600`
- S3 アップロード時は SSE-KMS or SSE-S3 を強制（バケット側ポリシーに依存しないよう SDK 引数でも指定）
- パスフレーズはエコー無しで対話入力させる選択肢を残す（環境変数を使えない CI 外シナリオ向け）

## 3. まとめ方の提案（複数案）

| 案 | 形式 | 暗号化 | 拡張性 | 既存ツール依存 | 推奨度 |
|---|---|---|---|---|---|
| **A: tar.gz + age 暗号化** | `*.dbenv` (実態は `.tar.gz.age`) | age（鍵 or パスフレーズ） | 高（任意のファイルを追加可） | `age` バイナリ or python `pyrage` | ★★★（既定推奨） |
| B: YAML 単一ファイル + age | `*.dbenv.yml.age` | age | 中（構造化済み、メタデータ表現が容易） | 同上 | ★★ |
| C: tar.gz + GPG | `*.dbenv.gpg` | GPG | 高 | `gpg` バイナリ | ★ |
| D: tar.gz + openssl AES-256-CBC | `*.dbenv` | openssl | 高 | `openssl` | ★ |
| E: 平文 tar.gz | `*.tar.gz` | なし | 高 | 標準 lib のみ | × (デバッグ用途のみ) |

### 採用: 案 A（tar.gz + age）

- age は OpenSSH 鍵 / X25519 鍵 / scrypt パスフレーズの 3 通りに対応し、CI でもローカルでも使いやすい
- python から `pyrage` で扱えるため外部バイナリ不要にできる
- 鍵運用が GPG より圧倒的に軽い
- 既定アルゴリズムを 1 つに固定することで実装・運用コストを抑える

#### age が受け付ける鍵種別

| 鍵 | recipient (公開鍵) | identity (秘密鍵) | 備考 |
|---|---|---|---|
| age X25519 (`age-keygen` 生成) | `age1...` | `AGE-SECRET-KEY-1...` | age ネイティブ、最も推奨 |
| OpenSSH ed25519 (`~/.ssh/id_ed25519`) | `ssh-ed25519 AAAA...` | `~/.ssh/id_ed25519` | そのまま使える |
| OpenSSH RSA (`~/.ssh/id_rsa`) | `ssh-rsa AAAA...` | `~/.ssh/id_rsa` | そのまま使える |
| OpenSSH ECDSA (`~/.ssh/id_ecdsa`) | ✗ | ✗ | **age 非対応**。age 専用鍵を用意してもらう必要あり |
| OpenSSH DSA | ✗ | ✗ | 非対応 |
| scrypt パスフレーズ | (なし) | (なし) | `--passphrase-env` / `--passphrase-stdin` で渡す |

`--recipient` には `ssh-ed25519 ...` / `ssh-rsa ...` 形式の **公開鍵文字列** を直接渡すか、`@~/.ssh/id_ed25519.pub` のようにファイル参照させる。`--identity` は秘密鍵ファイルパスをそのまま受け付ける。

ECDSA 鍵しか持たないユーザー向けに、docs に `age-keygen` での鍵作成手順を記載する。

#### 既定鍵

- export の `--recipient` 省略時: `~/.ssh/id_rsa.pub` を使用
- import の `--identity` 省略時: `~/.ssh/id_rsa` を使用
- いずれも存在しない場合はエラーとし、明示指定 or `age-keygen` での鍵生成を案内する

YAML 構造化（案 B）は **ヘッダメタデータ** にのみ採用し、ペイロード本体はファイル丸ごと tar に詰める案 A をベースとする。

### バンドル内構造（案 A）

```
manifest.yml          # version, created_at, devbase_version, files[].sha256
env/global.env        # $DEVBASE_ROOT/.env をそのままコピー
env/sources.yml       # .env.sources.yml（任意、--no-metadata で除外可）
env/projects/<name>/.env
...
```

`manifest.yml` 例:

```yaml
version: 1
created_at: '2026-05-21T10:00:00+09:00'
devbase_version: 2.2.0
files:
  - path: env/global.env
    sha256: <hex>
    origin: $DEVBASE_ROOT/.env
  - path: env/projects/carmo/.env
    sha256: <hex>
    origin: $DEVBASE_ROOT/projects/carmo/.env
```

これを tar.gz 化し、age で暗号化したものが最終バンドル。

**バージョン互換ポリシー:**

- `version` は整数で単調増加。互換性の無い変更時にのみインクリメントする
- import 側は **未知の `version`（自身がサポートする最大値より大きい）を検知したら拒否**し、devbase 本体のアップデートを促す
- 同じ major version 内では後方互換を保つ
- `sha256` は age AEAD と役割重複するが、(a) 復号後の個別ファイル検証、(b) `--force-unencrypted` 時の改ざん検知、(c) 部分展開のサポート用途で残す

## 4. CLI 仕様

### 4.1 export

```
devbase env export [DEST] [options]

DEST:
  ファイルパス（省略時は ./devbase-env-<YYYYMMDD-HHMMSS>.dbenv）
  s3://bucket/key, gs://bucket/object も指定可

options:
  --include-project NAME     対象プロジェクトを限定（複数指定可）
  --exclude-project NAME     除外プロジェクト（複数指定可）
  --no-global                グローバル .env を含めない
  --no-metadata              .env.sources.yml を含めない
  --force-unencrypted        平文 tar.gz として書き出す（既定は拒否。指定時も機密キー検知で警告）
  --recipient KEY            age 公開鍵で暗号化（複数指定可）
                              形式: 'age1...' / 'ssh-ed25519 AAAA...' / 'ssh-rsa AAAA...'
                              '@PATH' でファイル参照可（例: @~/.ssh/id_ed25519.pub）
                              ※ ssh-ecdsa は age 非対応
                              省略時の既定: ~/.ssh/id_rsa.pub（存在する場合）
  --passphrase-env VAR       環境変数 VAR からパスフレーズ取得
  --passphrase-stdin         stdin の最初の行をパスフレーズとして使用
  --format tar|yaml          バンドル形式（既定 tar）
  --print-manifest           書き出さず manifest を stdout に出す（プレビュー用）
```

> 注: `DEST='-'`（stdout）と `--passphrase-stdin` は併用不可（同様に import でも `SOURCE='-'` と `--passphrase-stdin` は併用不可）。CLI 側で明示エラーにする。

### 4.2 import

```
devbase env import SOURCE [options]

SOURCE:
  ファイルパス、s3://..., gs://..., または '-' で stdin

options:
  --merge MODE               キー単位マージ。MODE は keep-existing (既定) | prefer-incoming
                              keep-existing: 既存キーを保持、新規キーのみ追加
                              prefer-incoming: バンドル側の値で上書き（API キーのローテ配布用）
  --replace-keys KEY,...     指定キーのみバンドル値で上書き（粒度の細かい運用向け）
  --replace                  既存 .env を丸ごと差し替え（バックアップは取る）
  --dry-run                  実際には書かず差分のみ表示
  --identity FILE            age / OpenSSH 秘密鍵ファイル（複数指定可）
                              例: ~/.ssh/id_ed25519, ~/.ssh/id_rsa, age 専用鍵ファイル
                              ※ ssh-ecdsa は age 非対応
                              省略時の既定: ~/.ssh/id_rsa（存在する場合）
  --passphrase-env VAR       環境変数 VAR からパスフレーズ取得
  --passphrase-stdin         stdin の最初の行をパスフレーズとして使用
  --include-project NAME     対象プロジェクトを限定
  --exclude-project NAME     除外プロジェクト
  --no-global                グローバル .env を import しない
  --no-metadata              .env.sources.yml を import しない
  --backup-dir DIR           上書き前バックアップの保存先（既定: $DEVBASE_ROOT/backups/env-import/<ts>）
  --keep-last N              backup-dir 内の古い backup を最新 N 個に整理（既定 10、0 で無効）
```

### 4.3 既存コマンドとの整合

- `cli.py` の `SUBCMD_MAP[('env',)]` に `'export'`, `'import'` を追加
- パーサは `_add_env_parser` 内で新規 sub-subparser として登録
- 振り分けは `commands/env.py` の `handlers` dict に登録（既存パターン踏襲）

## 5. 内部設計

### 5.1 モジュール構成（追加分のみ）

```
lib/devbase/env/
  bundle.py        # Bundle 構築/展開、manifest 生成・検証、sha256
  cipher.py        # age 暗号化/復号（pyrage 経由）
  storage.py       # local / s3 / gcs バックエンドの抽象化
  io_export.py     # export 高レベル実装
  io_import.py     # import 高レベル実装（merge/replace, dry-run）
```

`commands/env.py` には薄いハンドラ（引数解釈 + 呼び出し）のみ追加し、ロジックは上記モジュールに置く。

### 5.2 storage バックエンド抽象

```python
class StorageBackend(Protocol):
    def write_bytes(self, dest: str, data: bytes) -> None: ...
    def read_bytes(self, source: str) -> bytes: ...

def resolve(uri: str) -> StorageBackend:
    # 'file://' / no scheme -> LocalBackend
    # 's3://' -> S3Backend (boto3, optional dep)
    # '-' -> StdioBackend
```

初期 PR では `Local` と `Stdio` のみ実装し（依存を増やさないため）、S3 backend は PR3 で追加する。`gs://` は廃案（PR4 中止）。

### 5.3 merge/replace のセマンティクス

- `--merge=keep-existing`（既定）:
  - 既存キーは保持、新規キーのみ追加
  - 衝突したキーは「skip」として stdout に列挙
- `--merge=prefer-incoming`:
  - バンドル側の値で既存キーを上書き（ローテ済みクレデンシャルの配布等）
  - 上書き対象キーは stdout に列挙
- `--replace-keys KEY,...`:
  - 指定キーのみバンドル値で上書き、それ以外は keep-existing 相当
- `--replace`:
  - 対象 `.env` を `backups/env-import/<ts>/<relative>` にコピーしてから差し替え
  - 差し替え対象は **バンドルに含まれていたファイル単位**（バンドル外のファイルは触らない）
- どちらも `--dry-run` で「追加されるキー / skip されるキー / 上書きされるキー」を表示

#### `.env.sources.yml` の取り扱い

`.env.sources.yml` にはマシン固有の絶対パス・同期時刻・元ファイルのハッシュが含まれるため、別マシンでそのまま上書きすると整合性が壊れる。以下のポリシーで扱う:

- 既定: import 時は **既存 `.env.sources.yml` を上書きしない**。バンドル内の sources.yml は `backups/env-import/<ts>/sources.yml.imported` として参照用にコピーするのみ。
- `--no-metadata`: バンドル内 sources.yml を完全に無視する（既定挙動と等価だが明示用）。
- `--merge-metadata`: バンドル側で新規に登場する source エントリのみ追加する（マシン固有値である `origin_path`, `synced_at` は import 先環境に合わせて再計算）。

### 5.4 バックアップとロールバック

- import は 2 フェーズ方式で部分適用を最小化する:
  1. 全対象ファイルを `backups/env-import/<ts>/` にコピー
  2. **Phase 1 (prepare)**: 全対象ファイルの新内容を `<target>.import.tmp` として書き出し、全件成功するまで rename しない
  3. **Phase 2 (commit)**: 全 tmp の書き出し成功を確認してから、各ファイルを `os.replace` で順次差し替え
  4. Phase 2 の途中で失敗した場合は backup から best-effort で `_rollback()`（OS / FS の制約上、厳密な ACID は保証しない旨を docs にも明記）
- backup 整理: `--keep-last N`（既定 10）で古い backup ディレクトリを自動 GC（無限増殖を防ぐ）
- 既存の `EnvFile.backup()` / `backups/` ディレクトリの慣習に揃える

## 6. PR 分割計画

| PR # | branch 名 | 概要 | 依存 | 並行可否 |
|---|---|---|---|---|
| 1 | `feature/PLAN03-1-export-local` | `bundle.py` / `cipher.py` / `storage.py` (Local+Stdio) の実装 + `devbase env export` サブコマンドを同時に公開。E2E でユーザーが触れる状態で merge する（死蔵コード回避）。 | なし | ○ |
| 2 | `feature/PLAN03-1-import-local` | `env import` サブコマンド追加（merge=keep-existing/prefer-incoming, --replace-keys, --replace, dry-run, 2 フェーズ書き出し, backup, --keep-last）。 | PR1 | × (PR1 merge 後) |
| 3 | `feature/PLAN03-1-s3-backend` | S3 backend (`s3://`) 追加。`boto3` を optional dep として導入。SSE-KMS/SSE-S3 強制、`GetBucketEncryption` での事前確認、`--unsafe-allow-unencrypted-bucket` の実装。 | PR1, PR2 | × (両方 merge 後) |
| ~~4~~ | ~~`feature/PLAN03-1-gcs-backend`~~ | **廃案**: GCS backend (`gs://`) は利用見込みが小さいため取りやめる。必要時は別 PLAN で切り出す。 | — | — |
| 5 | `feature/PLAN03-1-docs` | `docs/user/env-export-import.md` 新設、README リンク追加、CHANGELOG 更新。 | PR1, PR2 | ○ (PR1 merge 後に着手可) |

release branch: `release/PLAN03-1`
base branch: `main`

> PR3 は依存ライブラリ (`boto3`) が増えるため、コア (PR1-PR2) を先に merge してリリース価値を出す。
>
> PR 分割方針変更点: 旧案では「bundle/cipher のみの PR1 (CLI 未公開)」を独立させていたが、レビュー指摘により死蔵コードとなりレビューしづらい問題があったため、PR1 に `env export` の最小実装を同梱して E2E で動く状態で merge する形に統合した。
>
> PR4 (GCS backend) は 2026-05-23 に **廃案**。利用見込みが小さく、依存追加コストに見合わないため。必要になった時点で別 PLAN として切り出す。

## 7. テスト方針

- **ユニットテスト** (`tests/env/`):
  - `bundle.py`: round-trip（dict → bundle → dict）で内容一致 / sha256 検証
  - `cipher.py`: passphrase / recipient 双方のラウンドトリップ、破損データのエラー
  - `storage.py`: Local / Stdio のラウンドトリップ
  - merge/replace の挙動表テスト（衝突あり/なし、空ファイル、存在しないプロジェクト）
- **統合テスト** (`tests/cli/test_env_export_import.py`):
  - tmp_path に擬似 DEVBASE_ROOT を作り、export → import で完全一致を確認
  - `--dry-run` が `.env` を変更しないこと
  - `--replace` で backup が作成されること
  - import 後も対象ファイルのパーミッションが `0600` を維持すること
  - 改行コード (LF) / 末尾改行が export → import で保持されること（CRLF 混入による diff を防ぐ）
  - `--force-unencrypted` 未指定で平文 export を試みた場合に拒否されること
  - `DEST='-'` と `--passphrase-stdin` の併用がエラーになること
  - 未知の manifest version を含むバンドルの import が拒否されること
  - `--keep-last N` 後に古い backup ディレクトリが削除されていること
- **手動シナリオ**:
  - 本番相当の `.env`（AWS_CONFIG_BASE64 / GCP_CREDENTIALS_BASE64_default 含む）を export → 別マシンで import → `devbase env list` が一致
  - S3 export → 別 PC で import（PR3 完了後）

## 8. リスクと対応

| リスク | 対応 |
|---|---|
| age バイナリの環境差異 | `pyrage` を pip 依存に加え、外部バイナリに依存しない実装にする |
| ssh-ecdsa 鍵しか持たないユーザー | エラーメッセージで age 非対応を明示し、`age-keygen` での鍵生成手順を docs に記載・案内する |
| パスフレーズの誤入力でファイル破損扱い | import 前に manifest 検証ステップを置き、復号エラーを明示メッセージで分離 |
| S3 SSE 設定漏れで平文保存 | SDK 引数で `ServerSideEncryption='aws:kms'` を強制、加えて export 前に `GetBucketEncryption` でバケット側設定を確認（`HeadBucket` だけでは暗号化要件を検証できない）。設定不可なバケットは export を拒否し、`--unsafe-allow-unencrypted-bucket` でのみ許可 |
| 巨大プロジェクト数で tar が膨らむ | `--include-project` / `--exclude-project` の運用ガイドを docs に記載 |
| 既存 `.env` の手作業上書きとの衝突 | 既定を `--merge` にし、`--replace` 時は backup 必須 |

## 9. 完了基準 (Definition of Done)

- [ ] PR1, PR2 が merge され、`devbase env export` / `devbase env import` がローカルで動作
- [ ] `docs/user/env-export-import.md` がリリースされる
- [ ] CHANGELOG 更新済み
- [ ] 統合テストが CI で green
- [ ] 別マシンで「export → 転送 → import → `devbase env list` で一致」を 1 回手動検証

## 10. 未決事項

すべて確定済み（2026-05-21）:

1. ✅ **age を採用**（GPG/openssl 案は不採用）
2. ✅ **S3 対応を本 PLAN に含める**（PR3 として実装）。**GCS (PR4) は 2026-05-23 廃案** — 利用見込みが小さく、依存追加 (`google-cloud-storage`) に見合わないと判断。
3. ✅ **recipient ベースを既定推奨** とする
   - 既定の鍵は `~/.ssh/id_rsa.pub`（export 時の recipient）/ `~/.ssh/id_rsa`（import 時の identity）
   - 存在しない場合はエラーにし、`--recipient` / `--identity` の明示指定 or `age-keygen` での鍵生成を案内
   - OpenSSH 鍵 (`~/.ssh/id_ed25519`, `~/.ssh/id_rsa`) をそのまま `--recipient` / `--identity` に渡せる
   - ssh-ecdsa は age 非対応のため、該当ユーザーには `age-keygen` で age 専用鍵生成を案内
   - passphrase ベース (`--passphrase-env` / `--passphrase-stdin`) は CI など鍵配布が難しい環境向けにサポート継続
4. ✅ backup 保管: `--keep-last N`（既定 10）で対応
5. ✅ 元 issue `issues/i03.md` 第 1 項とのスコープ整合は本 PLAN で網羅
