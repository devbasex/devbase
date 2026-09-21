# テスト用サーバを後から起動・停止する（Compose の profiles）

dev のほかに app / db などのサービスを持つプロジェクトで、`devbase up` の既定では dev だけを起動し、テスト用のサーバ群は必要なときだけ後から起動・停止するための書き方です。起動・停止のどちらでも、dev コンテナは再作成も再起動もされません。

## 使える環境

| 項目 | 条件 |
| --- | --- |
| Docker Compose | **2.20.0 以上**。`depends_on` の `required` を使うため。動作を確かめたのは v5.1.4 |
| devbase | **3.5.0 以上**。`devbase project profile` が入った版です（CHANGELOG の `[3.5.0]`）。Plugin として配るなら [`plugin.yml` の `requires.devbase` も上げます](#plugin-として配るなら-requiresdevbase-を-350-以上へ上げる) |

## 1. `compose.yml` の書き方

テスト用のサービスへ `profiles:` を書きます。プロファイル名はプロジェクトが自由に決めてかまいません（devbase は既定の名前を持ちません）。

```yaml
services:
  dev:
    image: ...
    # dev から app への依存を残すなら required: false を付ける
    depends_on:
      app:
        condition: service_started
        required: false

  app:
    image: ...
    profiles: [test]
    depends_on:
      mysql:
        condition: service_healthy

  mysql:
    image: mysql:8
    profiles: [test]
```

| 書き方 | 理由 |
| --- | --- |
| `profiles: [test]` | `devbase up` の既定の起動対象から外れる |
| 既定のサービスからプロファイルのサービスへの `depends_on` には `required: false` | 付けないと、既定の `up` が `service "dev" depends on undefined service "app": invalid compose project` で止まる |
| プロファイルのサービスから dev への `depends_on` | どちらの形（`[dev]` / `required: false` 付き）でも書けます。devbase は `--no-deps` で起動するため dev は対象に入りません |

プロファイル名に `__devbase_none__` は使わないでください。devbase が「どのプロファイルも有効にしない」ために予約している名前です。

### Plugin として配るなら `requires.devbase` を 3.5.0 以上へ上げる

`profiles:` を使うプロジェクトを含む Plugin は、`plugin.yml` の `requires.devbase` を
`">=3.5.0"` へ上げてください。`compose.yml` を書き換えたのと同じ Pull Request で上げます。

```yaml
# <plugin>/plugin.yml
requires:
  devbase: ">=3.5.0"
```

3.5.0 未満の devbase では、`profiles:` を付けたサービスが `devbase up` の起動対象から外れたまま、
後から起動する手段（`devbase project profile up`）もありません。テスト用サーバ群が黙って起動
しない状態になります。

`requires.devbase` を上げておけば、新規の `devbase plugin install` はその場で拒否され、
`devbase plugin update` では警告が出ます。仕組みは既にあるため、Plugin 側でやることは版数を
書くことだけです。書式（必ずクォートする）と検証の詳細は
[`plugin.yml` リファレンスの `requires`](plugin-yml-reference.md#requires) を参照してください。

## 2. コマンド

```bash
devbase project profile list              # プロファイルと稼働状況を見る
devbase project profile up test           # test のサービスを起動する
devbase project profile down test         # test のサービスを停止して削除する（ボリュームは残る）

devbase project profile up carmo test     # 任意のディレクトリから carmo の test を起動する
```

`devbase list` で起動中のプロジェクトを選ぶと、操作のメニューに「テスト用サーバ起動 (profile up)」「テスト用サーバ停止 (profile down)」が出ます（プロファイルを持つプロジェクトだけ）。

`profile list` の表は次のとおりです。

```text
PROFILE  SERVICES   RUNNING
test     app,mysql  2/2 running
```

| RUNNING | 意味 |
| --- | --- |
| `2/2 running` | すべて稼働中 |
| `1/2 partial` | 一部だけ稼働中 |
| `0/2 stopped` | 停止中 |
| `不明` | Docker のデーモンへ接続できず、稼働状況を得られなかった |

どのコマンドも、先に `devbase up` を済ませて `.docker-compose.scale.yml` がある状態で使います。

## 3. `devbase up` / `devbase down` との関係

| 操作 | プロファイルのサービス |
| --- | --- |
| `devbase up` | 起動しない。起動していた場合も、冒頭の停止で止まる。**テストを続けるなら `up` の後にもう一度 `profile up` する** |
| `devbase down` | dev と一緒に削除する |
| `devbase scale` | 複製しない（dev だけが増える） |

端末の環境変数やプロジェクトの `.env` に `COMPOSE_PROFILES` を書いても、devbase 経由の操作には効きません。有効なプロファイルは devbase のコマンドで決めます。素の `docker compose` を叩いたときは従来どおり効きます。

## 4. `deploy` フックでの分岐

`devbase project profile up <名前>` は、サービスの起動が終わった後に `deploy` フックを稼働中の全インスタンスについて呼び直します。`pre-up` は呼びません。

フックは `DEVBASE_ACTIVE_PROFILES` で、どの経路から呼ばれたかを見分けられます。

| 呼ばれ方 | `DEVBASE_ACTIVE_PROFILES` |
| --- | --- |
| `devbase up` の `pre-up` / `deploy` | 空 |
| `devbase project profile up test` の `deploy` | `test` |

```bash
#!/bin/bash
# projects/<name>/deploy
if [ "$DEVBASE_ACTIVE_PROFILES" = "test" ]; then
    echo "テスト用サーバの初期データを投入する"
    exit 0
fi
# 以下は devbase up のときの処理
```

`deploy` が失敗すると `profile up` も 0 以外で終わります。値は常にプロファイル名 1 つです（カンマ区切りは将来の拡張のための予約です）。

## 関連

- [compose.yml ガイドライン](compose-yml-guidelines.md)
- [フックへ渡る環境変数](quickstart.md#フックへ渡る環境変数)
- [`devbase project profile`](../user/cli-reference/02-project.md#devbase-project-profile)
