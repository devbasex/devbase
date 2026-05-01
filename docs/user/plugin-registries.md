# プラグインレジストリ

devbase のプラグインは **プラグインレジストリ** を通じて配布されます。レジストリは `plugin.yml` を含む Git リポジトリの集合体で、`devbase plugin repo add` で任意のレジストリを追加できます。

## レジストリの考え方

devbase v2.2.0 以降、「公式レジストリ」固定の概念は廃止されました。すべてのレジストリは対等な立場で扱われ、ユーザーが自分の用途に合わせてレジストリを組み合わせて使います。

- 初回インストール時には `devbasex/devbase-samples`（サンプルレジストリ）が自動登録されます。
- 追加のレジストリは `devbase plugin repo add <user>/<repo>` で登録します（GitHub ショートハンド対応）。
- 完全な URL（`https://github.com/...`、SSH 形式）も指定可能です。

## 公開レジストリ

### devbasex/devbase-samples（サンプルレジストリ）

- URL: <https://github.com/devbasex/devbase-samples>
- 役割: 初めて devbase を使うユーザーがすぐに試せる汎用ツールを収録。
- 主な収録プラグイン:
  - `adminer` -- DB 管理ツール
  - `ai-plugins` -- Claude Code プラグインマーケットプレイス開発環境
  - `devbase` -- devbase 本体の開発環境

`devbase init` 実行時に自動で登録されます。

## プラグインマーケット（追加登録向け）

組織内・個人で運用する private レジストリも、アクセス権があれば `devbase plugin repo add <user>/<repo>` で追加できます。

> private リポジトリにアクセスするには、Git の認証（HTTPS のクレデンシャル、または SSH 鍵）が事前に設定されている必要があります。

## レジストリ操作コマンド

```bash
# 登録
devbase plugin repo add <user>/<repo>
devbase plugin repo add https://github.com/<user>/<repo>.git

# 一覧
devbase plugin repo list

# 削除
devbase plugin repo remove <name>

# 同期（最新化）
devbase plugin repo sync
```

## 自前レジストリの作成

独自のプラグインレジストリを作成する手順は [プラグイン開発クイックスタート](../plugin-dev/quickstart.md) を参照してください。レジストリは以下の構造を持つ Git リポジトリです。

```
<registry-root>/
├── registry.yml             # レジストリメタデータ
├── <plugin-name>/
│   ├── plugin.yml
│   └── compose.yml
└── ...
```

## 関連ドキュメント

- [はじめに](getting-started.md)
- [CLI リファレンス](cli-reference.md) -- `plugin repo` サブコマンドの詳細
- [プラグイン開発クイックスタート](../plugin-dev/quickstart.md)
- [plugin.yml リファレンス](../plugin-dev/plugin-yml-reference.md)
