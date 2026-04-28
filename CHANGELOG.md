# Changelog

本プロジェクトの変更履歴を [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) の形式に沿って記録します。バージョン番号は [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

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
