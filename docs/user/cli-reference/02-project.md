# project グループ

[CLI リファレンス目次に戻る](README.md)

プロジェクト（コンテナ）のライフサイクル管理と一覧表示を行うコマンド群です。

## プロジェクト名指定（CWD 非依存）

`up` / `down` / `ps` / `logs` / `scale` / `rebuild` / `open` / `post-start` は省略可能な `[name]` 引数を取ります。`[name]`
を指定すると、**現在のディレクトリに依存せず** `$DEVBASE_ROOT/projects/<name>` を対象に
操作できます。

```bash
# 任意のディレクトリから adminer プロジェクトを起動
devbase project up adminer

# 省略時は従来どおりカレントディレクトリのプロジェクトを対象にする
cd $DEVBASE_ROOT/projects/adminer && devbase project up
```

> **`profile` も `[name]` を取りますが、解決の経路が違います。** `project profile up` /
> `profile down` / `profile list` は `[name]` を受け付けますが、`bin/devbase` の
> `_PROJECT_NAME_SUBCOMMANDS`（`up` / `down` / `ps` / `logs` / `scale` / `rebuild` / `open` / `post-start`）
> には入っていません。`profile` では 3 番目の引数に `up` / `down` / `list` が来るため、
> ラッパーでは位置で名前を解決できないからです。名前の解決は Python 側の
> `_dispatch_lifecycle` が行います。

- `<name>` は `$DEVBASE_ROOT/projects/` 配下のプロジェクト名（`devbase project list` で確認可能）
- 名前として受け付ける形は、英数字で始まり英数字・`.`・`-`・`_` だけからなる文字列です
  （`myapp`、`github_work_time`、`myapp-ai`、`myapp.alice`）。`../etc` や `a/b` のように
  形に合わない値は名前として扱わず、`..` や `/` で `projects/<name>` の外を指すことはできません。
  `[name]` を取るコマンドに渡すと、プロジェクト名に使えない形である旨を出して終了コード 1 になります
  （なお `projects/<name>` 自体がシンボリックリンクの場合は、その実体のディレクトリへ移動して
  そこの `env` を読みます。プラグインの同期が張るリンクがこれにあたり、実体は `repos/` 配下など
  `projects/` の外にあります）
- 存在しない名前を指定するとエラーになり、利用可能なプロジェクト候補が表示されます
- 名前解決はラッパー (`bin/devbase`) が対象ディレクトリへ `cd` してから実行します。
  これにより `build`（シェル実装）を含む全操作が名前指定で成立します
- `devbase` は PATH 上の実行ファイルとして子プロセスで起動されるため、この `cd` が
  **呼び出し元シェルの作業ディレクトリを変えることはありません**

> **`project login` / `project build` は `[name]` を取りません。** これらの単一引数はそれぞれ
> `index` / `image` であり、`[name]` を許すと `project login 2` / `project build web` が誤解釈される
> ため除外しています。一方、トップレベルシノニム `devbase build <name>` / `devbase login <name>` は
> ラッパー (`bin/devbase`) の存在性判定（`$DEVBASE_ROOT/projects/<name>` が実在すれば cd）で
> 名前解決されます（実在しない場合は従来どおり `index` / `image` として下流へ渡されます）。
> `devbase build <name>` で `containers/<name>` も実在する場合はイメージが優先されます
> （[`devbase project build`](#devbase-project-build) を参照）。

> **衝突注意:** トップレベルの `devbase login <index>` / `devbase scale <N>` は、値が実在する
> プロジェクト名と一致すると名前として解釈されます（`projects/2` が存在する状態の `devbase login 2`
> は index=2 ではなく project `2` への操作になります）。数字だけのプロジェクト名は通常作られないため
> 衝突は偶発に限られます。
>
> 名前の解決は現在地を見ません（`$DEVBASE_ROOT/projects/<値>` が実在するかだけを見ます）。
> そのため対象プロジェクトのディレクトリ内で実行しても回避できません（`projects/web` の中で
> `devbase login 2` と打つと `projects/2` へ切り替わり、`2` が取り除かれて index は既定の 1 に
> なります）。該当する場合は次のように打ってください。
>
> - `devbase project login 2` — `project login` は `[name]` を取らないため名前解決の対象外で、
>   `2` は index のままカレントプロジェクトへ渡ります
> - `devbase project scale <name> 2` — `<name>` が名前として取り除かれ、残る `2` が `new_scale`
>   になります（`devbase project scale 2` は `projects/2` へ切り替わるため、名前は省略しません）

## `--context NAME`（共通オプション）

`up` / `down` / `ps` / `logs` / `login` / `scale` / `build` / `rebuild` / `open` / `profile`（`project` /
`container` 配下と、トップレベルのショートカット）と `project post-start` は `--context NAME` を受け付けます。
そのコマンドの `docker` / `docker compose` を、指定した docker context の daemon へ向けます。

```bash
devbase up myapp --context gpu-wsl     # この 1 回だけ gpu-wsl 上に立てる
devbase build --context gpu-wsl        # イメージをリモート側でビルドする
```

優先順位は CLI > env `DEVBASE_DOCKER_CONTEXT` > `projects/<name>/project.local.yml` の
`docker.context` > 現在の context です。設定の書き方は
[`project.local.yml`](../project-yml.md#projectlocalyml個人機材ごとの設定)、動作の詳細は
[環境変数ガイドの「リモート Docker」](../environment-variables.md#リモート-docker別ホストの-daemon-にコンテナを立てる)
を参照してください。

## `devbase project up`

コンテナを起動します。

```
devbase project up [name] [--context NAME]
devbase up [name] [--context NAME]
```

- 最初にプロジェクトのアカウントグループの宣言（`projects/<name>/env` の `DEVBASE_ACCOUNT_GROUP`）を
  読みます。宣言が無い・空・`default`・`$DEVBASE_ROOT/env` に宣言がある・プロジェクトの外、のどれかなら、
  `.env`・`pre-up`・スナップショット・ボリューム・生成物・コンテナのどれも作らずに終了コード 1 で止まり、
  直すファイルと行を示します。シェルの環境変数 `DEVBASE_ACCOUNT_GROUP` が宣言と違うときも止まります
  （[環境変数ガイド](../environment-variables.md#宣言が無いときは止まる)）
- 解決した docker context が現在の context と異なる（**リモート扱い**）とき:
  - `DOCKER_CONTEXT` を全 docker 呼び出しへ渡し、`DOCKER_GID` はリモート側の値にする
  - `project.local.yml` の `docker.home` で bind mount の `~` を展開する
  - 自動スナップショットは作らない（控えたいボリュームがリモートにあるため）
  - `DOCKER_HOST` が設定されていれば警告して外す（docker は `DOCKER_HOST` を `DOCKER_CONTEXT` より優先するため）

- 起動時にスナップショットを自動作成（起動するアカウントグループの系列の最新の世代へ差分追加。系列に世代が無いか差分が上限に達していれば新世代）
  - 同じアカウントグループ（系列）の直近のスナップショット取得から既定 60 分以内のときはスキップします。別のグループのスナップショットの時刻は見ません
  - 間隔は `DEVBASE_SNAPSHOT_MIN_INTERVAL_MINUTES` 環境変数で上書き可能（既定 60、`0` で無効化＝毎回取得、不正値は警告して既定値）
- `project.yml` の `scale` に基づいてコンテナ数を決定（既定: 2）
- `project.yml` の `repos` を clone プランへ正規化してコンテナへ渡す（コンテナ内で `/work` 配下へ clone される）
- イメージの自動準備（`devbase up` は `devbase rebuild`＝`devbase build --expires=7` 相当を実行）:
  - `build:` 定義あり、イメージ未存在 → `devbase build` を自動実行
  - `build:` 定義あり、イメージ存在 → プロジェクトイメージの作成日で再ビルドの要否を判定:
    - 7日未満 → 再ビルドしない（既存イメージをそのまま使用）
    - 7日以上 + ベースが閾値内＝新しい → プロジェクトのみ no-cache（ベースはキャッシュ）
    - 7日以上 + ベースが古い/判定不能 → ベースも含めて no-cache
    - ベースイメージ `FROM devbase-*` の作成日はプロジェクトと独立して判定します
  - `image:` のみ（公開イメージ）、未存在 → `docker pull` を自動実行
  - `image:` のみ、前回 pull から7日以上経過 → `docker pull` で再取得
    （前回 pull 日時は `${DEVBASE_ROOT}/.cache/pulls/<image>` の touch-file mtime で判定）
  - 閾値は `DEVBASE_IMAGE_MAX_AGE_DAYS` 環境変数で上書き可能（既定 7、不正値は警告して既定値）
- コンテナを起動した後、entrypoint の完了（コンテナの中の `/tmp/entrypoint-ready`）を最長 60 回（1 秒おき）待ちます。
  1 台が起動できなくても、残りのコンテナの完了は待ち続けます
- 起動できたコンテナへ、**起動の後の処理**を次の順で行います。どれも失敗しても `up` を失敗にはしません
  1. clone できなかったリポジトリの報告（`/work` に無いリポジトリを警告で出す）
  2. プロジェクトの `./deploy`（あれば。番号ごとに `DEVBASE_INSTANCE_INDEX` を付けて走らせる）
  3. backend が `openbao` のとき、コンテナの `~/.vault-token` へ token を書く
  4. VS Code の窓のタイトルをコンテナ名始まりにする（[環境変数ガイドの「ウィンドウタイトル」](../environment-variables.md#ウィンドウタイトルどの窓がどのプロジェクトか)）
  5. エディタの自動オープン（有効なときだけ。開く番号のコンテナにだけ）

### 起動できなかったコンテナがあるとき

起動の待ちの間に終了した・見つからない・時間内に entrypoint が終わらなかったコンテナがあると、`up` は次のように動きます。

- 起動できたコンテナには、上の 1〜4 を行います。起動できなかったコンテナには行いません
- エディタは、開く番号のコンテナが起動できていれば開きます。起動できていなければ、警告を出して開きません（ほかの番号へは替えません）
- 起動できたコンテナを止めたり消したりはしません
- 出力の最後に `Deploy failed` の行、起動できなかったすべてのコンテナの名前と理由（`exited unexpectedly` とログの末尾 10 行・`container not found`・`timeout`）、補う手順を出し、終了コード `1` で終わります

補う手順は次のとおりです。

1. 落ちたコンテナを起こし直す（`docker start <コンテナ名>` など）
2. その起動が終わった後に `devbase project post-start <name>` を打つ。`docker start` で起こし直しただけでは、起動の後の処理（token の配布・窓のタイトルの設定など）は行われません
3. `./deploy` も含めて最初からやり直すときは `devbase up <name>` を打つ（全コンテナを作り直します）

> **Note (entrypoint / Dockerfile を変更したとき):** `containers/` 配下の `entrypoint.sh` や
> Dockerfile はビルド時にイメージへ焼き込まれます。これらを変更しても、上記のとおり
> `devbase up` はイメージが 7 日より新しいと再ビルドをスキップするため、変更が反映されない
> ことがあります。確実に反映するには **`devbase build [name] --no-cache`** で再ビルドしてから
> `devbase up` してください（`--no-cache` は `build` のオプションで、`rebuild` にはありません）。

## `devbase project open`

閉じた VS Code の窓を、コンテナを再起動せずに開き直します。`devbase up` の最後の段で開くのと同じ窓（dev コンテナへ接続したフォルダ、リポジトリが 2 件以上ならワークスペース）を開きます。

```
devbase project open [name] [--open-index N] [--context NAME]
devbase open [name] [--open-index N] [--context NAME]
devbase container open [--open-index N] [--context NAME]
```

| パラメータ | 必須 | デフォルト | 説明 |
|-----------|------|-----------|------|
| `name` | いいえ | カレント | 対象プロジェクト名。`container open` では受け付けません |
| `--open-index N` | いいえ | `DEVBASE_OPEN_INDEX`、無ければ `1` | 開く dev コンテナの番号 |

プロジェクトの状態で動きが変わります。

| 状態 | 動き | 終了コード |
|------|------|-----------|
| dev コンテナが 1 つ以上動いている | コンテナに触らずに窓を開く | 開いた・SSH で手元のコマンドを提示した: `0` / 開けなかった（非 TTY・`code` が無い）: `1` |
| dev コンテナが 1 つも動いていない | `devbase up --open` と同じく起動してから窓を開く | `up` の終了コード |
| 指定した番号のコンテナが動いていない | 動いている番号を示して止まる（起動はしない） | `1` |
| コンテナの状態を取得できない（Docker のデーモンに届かない） | 起動せずに止まる | `1` |

- 開くかどうかの設定（`project.yml` の `open_editor` / `DEVBASE_OPEN_EDITOR`）は見ません。明示のコマンドなので、自動オープンを無効にした端末でも開きます
- 番号の上限は `project.yml` の `scale` ではなく、動いているコンテナで決まります（`devbase scale` で増やした分も開けます）
- `--open` / `--no-open` は受け付けません（`up` の自動オープンのためのオプションです）

## `devbase project post-start`

動いているコンテナへ、`up` の起動の後の処理を、コンテナを作り直さずにやり直します。`up` / `scale` で起動できなかったコンテナを起こし直した後や、時間切れになったコンテナが後から起動を終えた後に使います。

```
devbase project post-start [name] [--context NAME]
```

| パラメータ | 必須 | デフォルト | 説明 |
|-----------|------|-----------|------|
| `name` | いいえ | カレント | 対象プロジェクト名 |

- 対象は、動いていて entrypoint の完了（`/tmp/entrypoint-ready`）を確かめられた dev コンテナです。完了を待たず、各コンテナを 1 回だけ確かめます
- 対象へ、clone できなかったリポジトリの報告 → token の配布（backend が `openbao` のとき）→ 窓のタイトルの設定を行います
- `./deploy` は走らせず、エディタも開きません（エディタは `devbase open` で開けます）
- コンテナを起動・停止・作り直しはしません。何度打っても同じ結果になります
- トップレベルの短縮形と `container` グループにはありません

| 状態 | 動き | 終了コード |
|------|------|-----------|
| 対象のコンテナが 1 つ以上ある | 対象へ処理を行う。`project.yml` の `scale` までの番号のうち、動いていないものと、entrypoint の完了を確かめられないものを、分けて警告で出す。最後に `=== Post-start completed ===` を出す | `0` |
| 動いているが、entrypoint の完了を確かめられたものが 1 つも無い | 処理を行わない。起動が終わってからもう一度打つ | `1` |
| dev コンテナが 1 つも動いていない | 処理を行わない。起動には `devbase up` を使う（起動はしない） | `1` |
| コンテナの状態を取得できない（Docker のデーモンに届かない） | 処理を行わない | `1` |

## `devbase project down`

コンテナを停止・削除します。

```
devbase project down [name]
devbase down [name]
```

- 停止時にスナップショットのローテーションを自動実行

## `devbase project login`

コンテナにログインします。

```
devbase project login [index]
devbase login [index]
```

| パラメータ | 必須 | デフォルト | 説明 |
|-----------|------|-----------|------|
| `index` | いいえ | `1` | ログインするコンテナの番号 |

```bash
# 1番目のコンテナにログイン
devbase login

# 2番目のコンテナにログイン
devbase login 2
```

## `devbase project ps`

対象プロジェクトのコンテナ状態を `docker compose ps` で表示します。複数プロジェクトの
横断一覧は `devbase project list` を使用してください。

```
devbase project ps [name] [-a]
devbase ps [name] [-a]
```

| オプション | 説明 |
|-----------|------|
| `-a` | 停止中のコンテナも表示 |

## `devbase project logs`

コンテナのログを表示します（トップレベルシノニムはありません）。

```
devbase project logs [name] [-f] [--tail N]
```

| オプション | 説明 |
|-----------|------|
| `-f` | ログをリアルタイムで追跡 |
| `--tail N` | 末尾 N 行のみ表示 |

```bash
# 最新50行をリアルタイムで追跡
devbase project logs -f --tail 50
```

## `devbase project scale`

既存のコンテナを再起動せずにスケールします。

```
devbase project scale [name] <num>
devbase scale [name] <num>
```

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `name` | いいえ | 対象プロジェクト名（省略時はカレント） |
| `<num>` | はい | コンテナ数 |

```bash
# コンテナを3台に増やす
devbase project scale 3

# 任意のディレクトリから adminer を3台に
devbase project scale adminer 3
```

新しい値は `project.yml` の `scale` に書き戻されるため、次回の `devbase up` にも引き継がれます。
`up` と同じく最初にアカウントグループの宣言を確かめ、読めなければ `project.yml` を書き換える前に
止まります。

増やしたコンテナが起動できたら、`up` と同じ起動の後の処理（clone できなかったリポジトリの報告 →
`./deploy` → token の配布 → 窓のタイトルの設定）を、増やしたコンテナにだけ同じ順で行います。
既存のコンテナには行いません。エディタは開きません。

起動できなかったコンテナがあるときは、`up` と同じく、増やしたコンテナのうち起動できたものにだけ
起動の後の処理を行い、`Scale failed` の行と、起動できなかったすべてのコンテナの名前・理由・補う手順
（`devbase project post-start <name>`）を出して終了コード `1` で終わります。既存のコンテナが
起動できなかったときも同じです。`project.yml` の `scale` は書き換わったまま残るため、同じ数で
`devbase scale` を打ち直すことはできません。補うには `devbase project post-start` を使います。

## `devbase project profile`

`compose.yml` で `profiles:` を付けたサービス群を、dev コンテナに触れずに後から起動・停止します。
書き方は [テスト用サーバを後から起動・停止する](../../plugin-dev/compose-profiles.md) を参照してください。

```
devbase project profile up [name] <profile> [--context NAME]
devbase project profile down [name] <profile> [--context NAME]
devbase project profile list [name] [--context NAME]
```

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `name` | いいえ | 対象プロジェクト名（省略時はカレント）。`container profile` / `ct profile` では受け付けません |
| `<profile>` | はい（`up` / `down`） | `compose.yml` に書いたプロファイル名 |

- `up`: そのプロファイルのサービスをすべて `--no-deps` 付きで起動し、`deploy` フックを `DEVBASE_ACTIVE_PROFILES=<profile>` で稼働中の全インスタンスについて呼び直す
- `down`: そのプロファイルのサービスを `stop` → `rm -f` で停止・削除する（ボリュームは残る。フックは呼ばない）
- `list`: `PROFILE` / `SERVICES` / `RUNNING` の表を出す。Docker のデーモンへ接続できないときは `RUNNING` を `不明` にする
- どれも `devbase up` の後（`.docker-compose.scale.yml` がある状態）で使う。無ければ終了コード 1
- `devbase up` の冒頭の停止と `devbase down` は、プロファイルのサービスも止める

## `devbase project migrate-config`

旧 `env` 形式（`GIT_USER` / `GIT_REPO` / `GIT_HOST` / `WORK_DIR` / `CONTAINER_SCALE` /
`DEVBASE_OPEN_EDITOR`）のプロジェクト定義を [`project.yml`](../project-yml.md) へ変換します。

```
devbase project migrate-config [NAME ...] [--dry-run] [--projects-dir DIR]
```

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `NAME` | いいえ | 対象プロジェクト名（省略時は `projects/` 配下すべて） |
| `--dry-run` | いいえ | 生成される `project.yml` を表示するだけで書き換えない |
| `--projects-dir` | いいえ | 対象ディレクトリ（既定: `$DEVBASE_ROOT/projects`）。devbase へリンクしていないプラグインリポジトリ内の `projects/` を直接変換する場合に使う |

```bash
# まず変換結果を確認する
devbase project migrate-config --dry-run

# 変換を適用する
devbase project migrate-config
```

- 変換対象は上記キーのみで、`ENABLE_SSH` などそれ以外は `env` に残ります
- 既存の `project.yml` は上書きしません（手で複数リポジトリ構成へ整えたものを壊さないため）。
  `env` に残った旧キーの掃除だけを行うため、何度実行しても同じ状態になります
- `projects/<name>` はプラグインリポジトリへのシンボリックリンクです。書き換わるのはリンク先の
  実体（＝定義の正）で、出力には実際に触れたパスが表示されます

## `devbase project migrate-volume`

グループの宣言が必須になる前に、宣言の無いプロジェクトが使っていたボリューム
`devbase_home_default`（旧既定のボリューム）の中身を、指定したグループのボリューム
`devbase_home_<group>` へ写します。元は残します。手順の全体は
[`default` からの移行](../environment-variables.md#default-からの移行)を参照してください。

```
devbase project migrate-volume --to <group> [--dry-run]
```

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `--to` | はい | 移し先のグループ名（`DEVBASE_ACCOUNT_GROUP` と同じ規則。`default` は使えない） |
| `--dry-run` | いいえ | 前提の検査だけを行い、写す件数と移し先の状態を表示する（書かない） |

```bash
devbase project migrate-volume --to acme --dry-run
devbase project migrate-volume --to acme
```

- 元は読み取り専用で付け、`cp -a` でファイル・ディレクトリ・持ち主・権限・シンボリックリンク
  （リンクのまま）を写します。元を書き換えず、消しません
- 元が無い・移し先が存在して空でない・元か移し先をマウントした稼働中のコンテナがある・Docker に
  届かない、のどれかなら、移し先を作らず何も書かずに終了コード 1 で止まり、理由と直し方を示します
- 写した後に元と移し先のエントリの数を照らし、合わなければ移し先を消して打ち直すよう示します
- `--to` が無い・使えない名前なら終了コード 2
- ヘルパーのコンテナには `devbase-snapshot:latest` を使います（無ければ作ります）

## `devbase project build`

コンテナイメージをビルドします。キャッシュの扱いは 3 モードあります。

```
devbase project build [image]
devbase build [<project> | <image>] [--no-cache | --project-no-cache | --expires[=DAYS]] [--context NAME]
devbase build --help
```

`devbase build --help` / `-h` は、ビルドを起こさずにトップレベル `build` の使い方（上のオプションと
`<image>` 指定）を出して終了コード 0 で終わります。`devbase build myapp --help` のようにプロジェクト名
の後ろに置いても、そのプロジェクトへ移動せずに使い方を出します。

| モード | 子イメージ | 親イメージ（`FROM devbase-*`） |
|--------|-----------|-------------------------------|
| `devbase build` | キャッシュがあれば使う | キャッシュがあれば使う |
| `devbase build --no-cache` | 無条件で no-cache | 無条件で no-cache |
| `devbase build --expires[=DAYS]` | DAYS 日以上古ければ no-cache、未満なら再ビルドしない | 親の作成日で独立に同判定 |

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `image` | いいえ | 単体ビルドするイメージ名（`$DEVBASE_ROOT/containers/<image>` を直接ビルド。省略時は compose イメージ） |
| `--no-cache` | いいえ | base / project とも無条件でキャッシュ無視 |
| `--expires[=DAYS]` | いいえ | 作成日が DAYS 日以上のときのみ no-cache 再ビルド、未満なら再ビルドしない（既定 7、`DEVBASE_IMAGE_MAX_AGE_DAYS` で上書き可）。`--no-cache` とは併用しません |

> **`--no-cache` / `--expires` は compose ビルド（`image` 省略時）に適用されます。** `image` 指定の
> 単体ビルドでは `--no-cache` のみ反映され、`--expires` は対象外です。`--expires` 付きビルドは
> 作成日判定のため Python 経路（`project build`）で処理されます。

### 単体ビルドが作るイメージ

`image` を指定すると、`$DEVBASE_ROOT/containers/<image>` を次のコマンドでビルドします。

```
docker buildx build --load -t devbase-<image>:latest $DEVBASE_ROOT/containers/<image>
```

タグは必ず `devbase-` を前置します。`containers/` 配下のイメージは他の Dockerfile から
`FROM devbase-base:latest` の形で参照されるため、前置しないタグではビルドしても解決できません。
タグは `containers/` 配下のディレクトリ名から一意に決まります。`<image>` にはディレクトリ名を
渡してください。`devbase build devbase-base` のように接頭辞込みで渡すと `containers/devbase-base`
を探して見つからず、終了コード 1 で終わります。

ビルドは 1 回だけで、compose イメージは巻き込みません。`containers/<image>` または
その `Dockerfile` が無い場合は、探したパスを表示して終了コード 1 で終わります。

> **`<name>` が `$DEVBASE_ROOT/containers/` と `$DEVBASE_ROOT/projects/` の両方に実在する場合、
> トップレベルの `devbase build <name>` はイメージ `<name>` の単体ビルドとして扱います。** `build <image>`
> はイメージを明示した指定で、プロジェクトのビルドは通常そのディレクトリで引数なしに行うためです。
> このとき、プロジェクトとしても解釈できたことと、プロジェクトをビルドする方法（そのディレクトリで
> `devbase build`）を stderr に 1 行知らせます。`projects/` にだけある名前は今までどおりそのプロジェクトの
> ビルドです。詳細は [#142](https://github.com/devbasex/devbase/issues/142) を参照してください。

## `devbase project rebuild`

`devbase build --expires=7` のシノニムです（既定 7 日）。プロジェクトイメージが 7 日以上古ければ
no-cache で再ビルドし、未満なら再ビルドしません（既存イメージを使用）。親イメージ（`FROM devbase-*`）の
作成日は独立して判定します。トップレベルショートカット `devbase rebuild` を持ちます。

```
devbase project rebuild [name]
devbase rebuild [name]
```

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `name` | いいえ | 対象プロジェクト名（省略時はカレント） |

## `devbase project list`

`$DEVBASE_ROOT/projects/` 配下のプロジェクトを `NAME` / `PLUGIN` / `STATUS` の一覧で
表示します。

TTY（端末）では**デフォルトで階層メニュー TUI** が起動し、プロジェクトの起動・操作と
カテゴリ操作（環境変数 / プラグイン / スナップショット / ステータス）を 1 画面から
実行できます。パイプ・リダイレクト・CI などの非 TTY 環境では自動的に一覧表示のみへ
フォールバックします。

```
devbase project list [--no-interactive|--plain|-P]
devbase list [--no-interactive|--plain|-P]
```

| オプション | 説明 |
|-----------|------|
| `--no-interactive` / `--plain` / `-P` | TUI を起動せず一覧表示のみ |
| `--interactive` / `-i` | （後方互換）TUI 起動。デフォルトのため通常は不要 |

### TUI の画面構成とキー操作

```
? プロジェクトまたは操作を選択 (↑↓ 移動 / 名前で絞り込み / ←→ 下部メニュー / Enter 決定 / Esc・Ctrl-C 終了):
 » [1] adminer    (adminer, running (2 containers))
   [2] myapp      (myapp, stopped)
──────────────────────────────────────────────────────────────
  環境変数    プラグイン    スナップショット    ステータス
```

| キー | 動作 |
|------|------|
| ↑↓ / 文字入力 | プロジェクト一覧の移動・名前での絞り込み |
| ← → | 最下部の常設カテゴリメニューへ移動し項目間を巡回（バー上の ↑↓ で一覧へ戻る） |
| Enter | 決定。プロジェクト行では停止中はそのまま起動 (up)、起動中は操作サブメニューを表示。最下部のカテゴリメニューにフォーカスがある場合は、選択中カテゴリの操作画面へ遷移 |
| Esc / ← | サブメニューでは 1 つ前の画面へ戻る（トップでは Esc で終了） |
| Ctrl-C | どの画面でも全体を中止 |

起動中プロジェクトの操作サブメニューでは open / up / down / login / ps / logs / scale /
build / rebuild を選べます。先頭（Enter 1 回で決まる位置）は **エディタを開く (open)** で、
閉じた VS Code の窓をコンテナに触らずに開き直します。再起動 (up) はその 1 つ下です。最下部のカテゴリメニューから実行できる操作
（実体は対応する CLI コマンドへの委譲）:

| カテゴリ | 選べる操作 |
|---------|-----------|
| 環境変数 | キーの一覧と編集 / sync / init / OpenBao の接続設定 |
| プラグイン | 導入済み一覧 / 利用可能一覧 / install / uninstall / update / info / sync / migrate / repo 管理 |
| スナップショット | list / create / restore / copy / delete / rotate |
| ステータス | 環境全体の状態を表示（`devbase status` 相当） |

- 確認プロンプト (y/N) が出るのは破壊的操作（plugin uninstall / plugin repo remove /
  snapshot restore / snapshot delete / 環境変数のキーの削除）のみで、その他は CLI 既定値で即実行します
- 操作の出力後は Enter キーで一覧へ戻ります（出力が流れて読めなくなるのを防ぐため）
- 環境変数の「キーの一覧と編集」と「OpenBao の接続設定」は [環境変数の TUI](03-env.md#tui-でのキーの編集と-openbao-の接続設定) を参照してください
- TUI が提供しない細かいオプション（`env get/export/import`、
  `plugin install --link/--all`、`snapshot create --full`、`logs --follow` 等）は
  CLI を使用してください
- questionary 未導入時は従来の番号入力（選択 → up）にフォールバックします

```bash
# 階層メニュー TUI を起動（TTY デフォルト）
devbase list

# 一覧表示のみ（TUI を起動しない）
devbase list --no-interactive
```

出力例（`--no-interactive` / 非 TTY）:

```
NAME          PLUGIN        STATUS
adminer       adminer       running (2 containers)
myapp         myapp         stopped
myapp.alice   myapp-fork    stopped
```

- `PLUGIN` 列はシンボリックリンク先から解決するため、PLAN04 の同名衝突 suffix
  （例 `myapp.alice`）が付いていても正しいプラグイン名を表示します
- `STATUS` は `running (N containers)` / `stopped` / `unknown`（docker 未起動・
  `compose.yml` 不在等で判定不能）のいずれか

## container (ct) グループ（非推奨）

> **非推奨:** `container` グループは `project` グループへ移行しました。`devbase container
> <sub>` は当面 `devbase project <sub>` のエイリアスとして動作しますが、実行時に非推奨警告を
> 表示します（移行期間後のリリースで削除予定）。`[name]` 指定や `list` などの新機能は
> `project` 側のみで提供されます。`devbase container up <name>`（`ct` も同じ）は実在する
> プロジェクト名でも受け付けず、argparse の usage エラー（終了コード 2）になります。名前の指定は
> `devbase project up <name>` か `devbase up <name>` を使ってください。

```bash
# 旧（非推奨・警告が出ます）
devbase container up

# 新（推奨）
devbase project up
devbase up
```
