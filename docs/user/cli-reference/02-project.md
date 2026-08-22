# project グループ

[CLI リファレンス目次に戻る](README.md)

プロジェクト（コンテナ）のライフサイクル管理と一覧表示を行うコマンド群です。

## プロジェクト名指定（CWD 非依存）

`up` / `down` / `ps` / `logs` / `scale` は省略可能な `[name]` 引数を取ります。`[name]`
を指定すると、**現在のディレクトリに依存せず** `$DEVBASE_ROOT/projects/<name>` を対象に
操作できます。

```bash
# 任意のディレクトリから adminer プロジェクトを起動
devbase project up adminer

# 省略時は従来どおりカレントディレクトリのプロジェクトを対象にする
cd $DEVBASE_ROOT/projects/adminer && devbase project up
```

- `<name>` は `$DEVBASE_ROOT/projects/` 配下のプロジェクト名（`devbase project list` で確認可能）
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

> **⚠ 衝突注意（footgun）:** トップレベルシノニムの名前解決は「存在性ベース」のため、本来
> positional 引数として渡したい値が実在プロジェクト名と一致すると、その引数が名前解決の対象と
> なり project への `cd` が優先されて引数の意味が変わります。例えば `projects/2` が存在する状態の
> `devbase login 2` は index=2 ではなく project `2` への操作に、`projects/web` が存在する状態の
> `devbase build web` は image=web ではなく project `web` のビルドに化けます（`scale` の service 引数
> も同様）。これは「`build carmo` / `login carmo` でそのプロジェクトを操作する」意図的設計の
> トレードオフです。**回避策:** 衝突する場合は対象プロジェクトのディレクトリ内で実行するか、
> 明示的にそのプロジェクトへ切り替えてから（`cd` 済みの状態で）コマンドを実行してください。

## `devbase project up`

コンテナを起動します。

```
devbase project up [name]
devbase up [name]
```

- 起動時にスナップショットを自動作成（新世代 or 差分追加）
  - 直近のスナップショット取得から既定 60 分以内のときはスキップします
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

> **Note (entrypoint / Dockerfile を変更したとき):** `containers/` 配下の `entrypoint.sh` や
> Dockerfile はビルド時にイメージへ焼き込まれます。これらを変更しても、上記のとおり
> `devbase up` はイメージが 7 日より新しいと再ビルドをスキップするため、変更が反映されない
> ことがあります。確実に反映するには **`devbase build [name] --no-cache`** で再ビルドしてから
> `devbase up` してください（`--no-cache` は `build` のオプションで、`rebuild` にはありません）。

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

## `devbase project build`

コンテナイメージをビルドします。キャッシュの扱いは 3 モードあります。

```
devbase project build [image]
devbase build [image] [--no-cache | --expires[=DAYS]]
```

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
   [2] carmo      (carmo, stopped)
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

起動中プロジェクトの操作サブメニューでは up / down / login / ps / logs / scale /
build / rebuild を選べます。最下部のカテゴリメニューから実行できる操作
（実体は対応する CLI コマンドへの委譲）:

| カテゴリ | 選べる操作 |
|---------|-----------|
| 環境変数 | 変数一覧（グローバル）/ edit / sync / project / init |
| プラグイン | 導入済み一覧 / 利用可能一覧 / install / uninstall / update / info / sync / migrate / repo 管理 |
| スナップショット | list / create / restore / copy / delete / rotate |
| ステータス | 環境全体の状態を表示（`devbase status` 相当） |

- 確認プロンプト (y/N) が出るのは破壊的操作（plugin uninstall / plugin repo remove /
  snapshot restore / snapshot delete）のみで、その他は CLI 既定値で即実行します
- 操作の出力後は Enter キーで一覧へ戻ります（出力が流れて読めなくなるのを防ぐため）
- TUI が提供しない細かいオプション（`env get/set/delete/export/import`、
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
carmo         carmo         stopped
carmo.takemi  carmo-fork    stopped
```

- `PLUGIN` 列はシンボリックリンク先から解決するため、PLAN04 の同名衝突 suffix
  （例 `carmo.takemi`）が付いていても正しいプラグイン名を表示します
- `STATUS` は `running (N containers)` / `stopped` / `unknown`（docker 未起動・
  `compose.yml` 不在等で判定不能）のいずれか

## container (ct) グループ（非推奨）

> **非推奨:** `container` グループは `project` グループへ移行しました。`devbase container
> <sub>` は当面 `devbase project <sub>` のエイリアスとして動作しますが、実行時に非推奨警告を
> 表示します（移行期間後のリリースで削除予定）。`[name]` 指定や `list` などの新機能は
> `project` 側のみで提供されます。

```bash
# 旧（非推奨・警告が出ます）
devbase container up

# 新（推奨）
devbase project up
devbase up
```
