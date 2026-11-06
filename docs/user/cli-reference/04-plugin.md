# plugin (pl) グループ

[CLI リファレンス目次に戻る](README.md)

プラグインの管理を行うコマンド群です。

## `devbase plugin list`

インストール済み、または利用可能なプラグインを一覧表示します。

```
devbase plugin list [--available]
```

| オプション | 説明 |
|-----------|------|
| `--available` | リポジトリから取得可能なプラグインを表示 |

## `devbase plugin install`

プラグインをインストールします。

```
devbase plugin install <source>
```

ソースの指定形式:

| 形式 | 説明 | 例 |
|------|------|----|
| 名前のみ | 登録済みリポジトリから検索 | `devbase plugin install adminer` |
| リポジトリ直接指定 | 特定リポジトリのプラグイン | `devbase plugin install user/repo:plugin-name` |
| 全プラグイン一括 | リポジトリの全プラグインをインストール | `devbase plugin install user/repo --all` |
| ローカルリンク | ローカルディレクトリからリンク | `devbase plugin install /path:plugin-name --link` |

## `devbase plugin uninstall`

プラグインをアンインストールします。

```
devbase plugin uninstall <name>
```

## `devbase plugin update`

プラグインを最新バージョンに更新します。

```
devbase plugin update [name]
```

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `name` | いいえ | 更新するプラグイン名（省略時は全プラグイン） |

## `devbase plugin info`

プラグインの詳細情報を表示します。

```
devbase plugin info <name>
```

## `devbase plugin sync`

プロジェクトのシンボリックリンクを再同期します。

```
devbase plugin sync
```

## `devbase plugin migrate`

旧形式 (`plugins/<name>` へのコピー) でインストールされたプラグインを、`repos/` 配下の永続クローンへ移行します。`install` / `update` 実行時にも自動で呼び出されるため、通常は手動実行不要です。

```
devbase plugin migrate
```

移行の挙動:

| 状況 | 動作 |
|---|---|
| コピーがクローンと一致 | 旧コピーを削除し `repos/` へ移行 (migrated) |
| コピーにローカル変更あり | 旧コピーを `plugins/<name>.bak` として保全 (preserved、手動で reconcile) |
| 移行できない (ソース未登録 等) | スキップしてエラーを表示 (skipped) |

`--link` でインストールしたプラグインは移行対象外です。

## 作成フロー（`plugin new` / `plugin repo new` / `project new` に共通）

リポジトリ・プラグイン・プロジェクトを足すコマンドは、権限の確認からマージ、手元への取り込み、登録までを 1 回で行います。

- **対象は GitHub だけです。** 引数は `owner/repo`・`https://github.com/owner/repo[.git]`・`git@github.com:owner/repo[.git]` のどれかです。`git@` の形で渡すと clone の URL も `git@` になります
- **`gh` が要ります。** 入っていない・ログインしていないときは、何も変えずに次の操作を出して終了コード 1 で止まります。認証は `gh` と `git` の設定に任せます
- **変更の前に作成の計画を示します。** 端末があれば確認を取り、断ると何も変えません（終了コード 1）。端末が無いときは `--yes` が要ります
- **作業は一時の浅い clone で行います。** 手元の `repos/` の clone に未コミットの変更があっても失われません。手元への取り込みは早送りだけで、できないときは変更を残したまま止まります
- **マージは 1 回だけ試します。** CI を待たず、`--auto` と `--admin` を使いません。必須のレビューや検査でマージできないときは、PR の URL とマージの後に打つコマンドを出して終了コード 1 で止まり、`plugins.yml` は変わりません
- **コンテナは起動しません。** 起動は `devbase up` を別に打ちます

作業ブランチは `devbase/new-<種類>-<名前>` で、マージのときに消えます。git のブランチ名にできない名前（`..` を含むか `.`・`.lock` で終わる）は、`.` を `-` に置き換えてハッシュを足した形になります。同じブランチが既にあると止まり、開いている PR があればその URL を出します。

## `devbase plugin new`

GitHub のリポジトリへプラグインを足して導入します。

```
devbase plugin new <repo> [--name <名前>] [--public] [--yes]
```

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `repo` | はい | GitHub のリポジトリ（[作成フロー](#作成フローplugin-new--plugin-repo-new--project-new-に共通)） |
| `--name` | いいえ | プラグインの名前（既定: リポジトリ名）。英数字で始まり英数字・`.`・`-`・`_` だけからなる形 |
| `--public` | いいえ | リポジトリを新しく作るときに公開する（既定: private）。既存のリポジトリの公開範囲は変えない |
| `--yes` | いいえ | 計画の確認を省く。端末が無いときは必須 |

`<名前>/plugin.yml`・`<名前>/projects/.gitkeep`・`registry.yml` の項目を足す PR を作ってマージし、手元へ取り込んで導入します。

未登録のリポジトリは、先に `plugin repo new` と同じ分岐を通します。リポジトリの分の変更とプラグインの PR は別の変更として順に入り、登録はプラグインの PR のマージの後です。

同じ名前のプラグインが `registry.yml` か導入済みの中にあるとき、同じ名前のパスがリポジトリにあるときは、何も変えずに終了コード 1 で止まります。登録の手元の clone が無いときは、登録し直すコマンドを出して止まります。登録の手元の clone が GitHub の既定ブランチを追っていないときは、切り替えのコマンドを出して止まります。

## `devbase plugin repo new`

プラグインリポジトリを用意して登録します。

```
devbase plugin repo new <repo> [--public] [--yes]
```

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `repo` | はい | GitHub のリポジトリ（[作成フロー](#作成フローplugin-new--plugin-repo-new--project-new-に共通)） |
| `--public` | いいえ | リポジトリを新しく作るときに公開する（既定: private） |
| `--yes` | いいえ | 計画の確認を省く。端末が無いときは必須 |

| リポジトリ | push | `registry.yml` | 動作 | 終了コード |
|-----------|------|----------------|------|-----------|
| 無い | owner に作れる | — | 作る → 最初のコミットを既定ブランチへ直接入れる → 登録 | `0` |
| 無い | owner に作れない | — | 何もしない | `1` |
| 空 | できる | — | 最初のコミットを既定ブランチへ直接入れる → 登録 | `0` |
| ある | できる | 無い | `registry.yml` を足す PR → マージ → 登録 | `0` |
| ある | できる | ある | 登録だけ | `0` |
| 登録済み | できる | — | 何もしない（確認も出ない） | `0` |
| ある・空・登録済み | できない | — | 何もしない | `1` |

最初のコミットを直接入れるのは、GitHub が空のリポジトリに PR を作れないためです。読めない private のリポジトリは「無い」と同じに見えるので、`gh repo create` の失敗の出力を示して止まります。

## `devbase plugin repo add`

プラグインリポジトリを登録します。

```
devbase plugin repo add <url>
```

```bash
# GitHub ショートハンド
devbase plugin repo add user/repo

# 完全な URL
devbase plugin repo add https://github.com/user/repo.git
```

## `devbase plugin repo remove`

リポジトリの登録を削除します。

```
devbase plugin repo remove <name>
```

## `devbase plugin repo list`

登録済みリポジトリの一覧を表示します。

```
devbase plugin repo list
```

## `devbase plugin repo refresh`

プラグイン一覧をリポジトリから再取得します。

```
devbase plugin repo refresh [name]
```

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `name` | いいえ | 更新するリポジトリ名（省略時は全リポジトリ） |
