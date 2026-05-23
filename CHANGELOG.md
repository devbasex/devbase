# Changelog

本プロジェクトの変更履歴を [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) の形式に沿って記録します。バージョン番号は [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### Added
- `devbase env export` / `devbase env import` で **S3 URI (`s3://bucket/key`) を入出力先として指定**できるようになりました (PLAN03-1 PR3)。
  - 既定でオブジェクト単位の SSE (`aws:kms` または `AES256`) を強制し、export 時はバケット側のデフォルト暗号化も `GetBucketEncryption` で事前確認します。
  - 暗号化が未設定のバケットへ export する場合は `--unsafe-allow-unencrypted-bucket` の明示が必要です (オブジェクト単位の SSE はこのフラグに関係なく常に付与されます)。
  - SSE 種別 (`DEVBASE_S3_SSE`) / KMS 鍵 (`DEVBASE_S3_SSE_KMS_KEY_ID`) / エンドポイント (`DEVBASE_S3_ENDPOINT_URL`) / リージョン (`DEVBASE_S3_REGION`) は環境変数で上書きできます。MinIO / LocalStack の利用も可能です。
  - `boto3` は `pip install 'devbase[s3]'` で導入される optional 依存です。

### Changed
- `gs://` (GCS) スキームは **PLAN03-1 PR4 廃案** により対応しません。指定すると明示的なエラーメッセージで失敗します (旧: "未実装")。

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
