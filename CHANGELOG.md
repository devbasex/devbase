# Changelog

本プロジェクトの変更履歴を [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) の形式に沿って記録します。バージョン番号は [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### Added
- `devbase env export` / `devbase env import` で **S3 URI (`s3://bucket/key`) を入出力先として指定**できるようになりました (PLAN03-1 PR3)。
  - 既定でオブジェクト単位の SSE (`aws:kms` または `AES256`) を強制し、export 時はバケット側のデフォルト暗号化も `GetBucketEncryption` で事前確認します。
  - 暗号化が未設定のバケットへ export する場合は `--unsafe-allow-unencrypted-bucket` の明示が必要です (オブジェクト単位の SSE はこのフラグに関係なく常に付与されます)。
  - SSE 種別 (`DEVBASE_S3_SSE`) / KMS 鍵 (`DEVBASE_S3_SSE_KMS_KEY_ID`) / エンドポイント (`DEVBASE_S3_ENDPOINT_URL`) / リージョン (`DEVBASE_S3_REGION`) は環境変数で上書きできます。MinIO / LocalStack の利用も可能です。
  - `boto3` は main dependency として常に同梱されます (S3 を使わないユーザにも 25MB 程度入りますが、引数検出や lazy install の複雑さを避けるトレードオフです)。
- `devbase env export` / `devbase env import` の利用者向けドキュメント [`docs/user/env-export-import.md`](docs/user/env-export-import.md) を新設しました (PLAN03-1 PR5)。
  - バンドル構造、age 暗号化 (recipient / identity / passphrase)、入出力先 (local / stdio / S3)、merge モード比較、`.env.sources.yml` の扱い、2 フェーズ書き込みとバックアップ、典型ワークフロー、トラブルシューティングまでを網羅します。
  - README と環境変数ガイドからのリンクも追加しました。

### Changed
- `gs://` (GCS) スキームは **PLAN03-1 PR4 廃案** により対応しません。指定すると明示的なエラーメッセージで失敗します (旧: "未実装")。
- `lib/devbase/env/` 配下の export / import モジュールをリファクタリングしました (PLAN03-1 PR5)。公開 API (`ExportOptions`, `ImportOptions`, `export`, `import_bundle`) に互換性のない変更はありません。
  - export / import で重複していた passphrase 読み取り / 既定鍵 fallback / セキュアな bytes 書き込みを `io_common.py` に集約。
  - 711 行に肥大化していた `io_import.py` を「orchestration (`io_import.py`, 209 行)」「merge 計画 (`_import_merge.py`)」「2 フェーズ atomic 書き込み + backup GC (`_import_atomic.py`)」の 3 モジュールに分割。

## [2.2.0] - 2026-04-20

OSS 化に伴う初回リリース。devbase は本バージョンより `devbasex` Organization 配下で公開されます。

### Added
- MIT License
- プラグインマーケットの概念導入。任意のレジストリを `devbase plugin repo add` で追加可能。
- 公式サンプルレジストリ `devbasex/devbase-samples`（adminer / ai-plugins / devbase を収録）。
- PHP 8.5 ベースの開発コンテナ（`containers/php85`）。
- スナップショットの差分回数ベースの世代管理。
- `GIT_HOST` 環境変数による Git ホストの切り替えサポート。

### Changed
- `DEFAULT_OFFICIAL_REGISTRY` を `devbasex/devbase-samples.git` に変更。
- README / docs 内のリポジトリ参照を `devbasex/devbase` に更新。
- ドキュメント体系を `docs/user`, `docs/plugin-dev`, `docs/developer` に再編。

### Removed
- 「公式レジストリ」固定の概念を廃止。各レジストリは対等な扱いとなる。

[Unreleased]: https://github.com/devbasex/devbase/compare/v2.2.0...HEAD
[2.2.0]: https://github.com/devbasex/devbase/releases/tag/v2.2.0
