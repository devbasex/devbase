# PLAN67: base イメージに shellcheck を入れる の実装計画

## 関連リンク

- 課題: devbasex/devbase#249
- 要求と受け入れ条件: [PLAN67_base-shellcheck.md](PLAN67_base-shellcheck.md)（受け入れ条件 1〜9）
- 設計: [PLAN67_base-shellcheck-design.md](PLAN67_base-shellcheck-design.md)（決定 1〜5）
- 設計の Pull Request: devbasex/devbase#250（マージ済み）

## モード

`standard`。base イメージが同梱するコマンドを変え、建て直した全員に届く（要求の文書の根拠のとおり）。

## 目的と非目的

達成したい状態:

- base と派生イメージのコンテナで `shellcheck` が使え、診断を返す
- shellcheck が入っていないイメージはビルドの時点で止まる

やらないこと（要求の文書の「含まない」のとおり）:

- bash-language-server の導入、#247 の片付け、CI の変更、版の固定、lfm / snapshot への導入
- 「イメージの詳細」の表の、base 以外の行のベース列の訂正（実装中に見つけた。#251 として起票）

## 修正対象

- `containers/base/Dockerfile`
- `tests/containers/test_base_dockerfile_shellcheck.py`（新設）
- `docs/user/container-operations.md`
- `CHANGELOG.md`

## タスク分解

### Task 1: 1 回目の apt-get install の一覧へ shellcheck を足す

- **対象ファイル:** `tests/containers/test_base_dockerfile_shellcheck.py`、`containers/base/Dockerfile`
- **変更内容:** `poppler-utils ...;` の行の `;` を `\` に変え、理由のコメントと `shellcheck; \` を足す（設計「入出力の契約」）
- **満たす受け入れ条件:** 5
- **進め方:** 失敗するテスト（1 回目の一覧に `shellcheck` がある / `shellcheck` を入れる別の `RUN` が無く `apt-get update` が 2 回のまま）→ Dockerfile の変更 → 整理

### Task 2: 版の確認の RUN へ `shellcheck --version` を足す

- **対象ファイル:** 同上
- **変更内容:** `session-manager-plugin --version` の後へ `&& shellcheck --version`
- **満たす受け入れ条件:** 4
- **進め方:** 失敗するテスト（版の確認の `RUN` に `shellcheck --version` がある）→ Dockerfile の変更

### Task 3: 利用者向け文書と CHANGELOG

- **対象ファイル:** `docs/user/container-operations.md`、`CHANGELOG.md`
- **変更内容:** 「イメージの詳細」の表の base の行へ shellcheck、新しい小節「Bash の静的検査（base 以降）」、`[Unreleased]` の `### Added`。表以外の 2 か所に `devbase build base --no-cache` が要ることを書く
- **満たす受け入れ条件:** 9
- **進め方:** 文書のためテスト駆動を適用しない。差分の目視で確かめる

### Task 4: 手元で建てて確かめる

- **対象:** 手元の arm64 の Docker
- **変更内容:** 無し（証跡の採取）。変更前の `devbase-base:latest` で受け入れ条件 6 の `apt-get install -s` を採ってから、`devbase build base --no-cache` → `devbase-general` / `devbase-php` の建て直し → 受け入れ条件 1・2・3・6 のコマンド
- **満たす受け入れ条件:** 1・2・3・6・8。7 は `uv run --locked pytest tests/ -q`
- **進め方:** 出力を Pull Request 本文へ貼る。CI はイメージを建てない（要求の文書の前提 2）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| Docker のビルドキャッシュが壊れた層を配る | `--no-cache` で建てる。0 バイトのファイルが出たら builder prune の後に建て直す |
| 変更前のイメージを建て直しで失う | 受け入れ条件 6 の `apt-get install -s` を建て直しの前に採る |
| pytest が実環境の `DEVBASE_ROOT` を継承する | 新しいテストは Dockerfile の文字列しか読まない |

## 切り戻し手順

要求の文書の「切り戻し手順」のとおり（revert → `devbase build base --no-cache` → 派生イメージの建て直し → コンテナの作り直し）。

## 完了の定義

- [ ] 受け入れ条件 1〜9 をすべて満たし、条件ごとの検証手段と結果を Pull Request 本文に載せた
- [ ] `uv run --locked pytest tests/ -q` が終了コード 0
