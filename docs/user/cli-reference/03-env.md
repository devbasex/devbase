# env グループ

[CLI リファレンス目次に戻る](README.md)

環境変数の管理を行うコマンド群です。詳細は [環境変数ガイド](../environment-variables.md) を参照してください。

## グループ別の置き場と `--group`

機密の保存先が OpenBao で、グループ別の置き場（`secrets/backend.yml` の `version: 2`）を選んだ
端末では、`env` コマンドが読み書きする置き場がアカウントグループごとに分かれます。詳細は
[機密の保存先を選ぶ](../env-backend.md#アカウントグループごとの置き場version-2)を参照してください。

コマンドが相手にするグループ（対象のグループ）は、既定では実行したディレクトリで決まります。

| 実行した場所 | 対象のグループ |
|---|---|
| `projects/<name>` とその下位ディレクトリ | `projects/<name>/env` → `$DEVBASE_ROOT/env` → `default` の順に最初に見つかった `DEVBASE_ACCOUNT_GROUP` |
| プロジェクトの外 | `$DEVBASE_ROOT/env` → `default` |

`env list` / `get` / `set` / `delete` / `edit` / `init` / `sync` は `--group NAME` で対象のグループを
指定できます。`-p` との組み合わせは次のとおりです。

| 指定 | 結果 |
|---|---|
| `--group NAME`（`-p` なし） | 共通の参照（`team/<g>/global`、`--user` なら `users/<user>/<g>/global`）を `NAME` のグループで読み書きする |
| `--group NAME -p`、`NAME` がプロジェクトのグループと同じ置き場 | プロジェクトの参照を読み書きする |
| `--group NAME -p`、`NAME` がプロジェクトのグループと違う置き場 | 両方のグループ名を述べて終了コード 1。読み書きしない |
| `list` / `get`（`-p` なし）で、`NAME` がプロジェクトのグループと違う置き場 | 共通の参照だけを出す・探す。プロジェクトの参照を含めなかった旨を標準エラーへ出す |
| 使えない名前（`ubuntu`、数字だけ、`/` を含むなど。読み替えた後が `global` / `projects` のときも） | 理由を述べて終了コード 2 |
| `version: 1` の設定やファイル backend | `--group` を無視せず、終了コード 2 |

「同じ置き場」かは `group_aliases` で読み替えた後の名前で比べます（`default: nyle` の読み替えが
あれば、グループを宣言していないプロジェクトで `--group nyle -p` が通ります）。

## `devbase env init`

環境変数の対話式初期セットアップを実行します。

```
devbase env init [--reset] [--group NAME]
```

| オプション | 説明 |
|-----------|------|
| `--reset` | 既存の設定をリセットして再設定 |
| `--group NAME` | 書き込むチーム共通の置き場のグループ（グループ別の置き場だけ。[グループ別の置き場と `--group`](#グループ別の置き場と---group)） |

`devbase up` が機密の未作成を検出して自動で起動する `env init` には、グループ別の置き場では
起動するプロジェクトのグループが `--group` で渡ります。

## `devbase env sync`

ソースファイル（`~/.aws/config` 等）の変更を検出し、環境変数を再同期します。

```
devbase env sync [--user] [--group NAME]
```

| オプション | 説明 |
|-----------|------|
| `--user` | 同期する全てのキーを個人共通へ書く。個人単位の機密を持たないファイルの backend（平文・age）では終了コード 2 |
| `--group NAME` | 対象のグループ（グループ別の置き場だけ。[グループ別の置き場と `--group`](#グループ別の置き場と---group)）。それ以外の設定では終了コード 2 |

同期するキーごとに、対象のグループの個人共通とチーム共通のうち、そのキーが現にある参照へ書きます。

| キーのある場所 | 書き込み先 |
|---|---|
| 個人共通だけ、または両方 | 個人共通（コンテナでは個人共通の値が勝つため） |
| チーム共通だけ | チーム共通 |
| どちらにも無い | 個人共通（`~/.aws/credentials` の静的キーなどがチーム全員の読める置き場へ入らないように） |
| ファイルの backend | 常にチーム共通（`$DEVBASE_ROOT/.env`、age なら `secrets/global.env.age`） |

チーム共通に置きたいキーは、先に `devbase env set KEY=VALUE` でチーム共通に作っておくと、以後の
`sync` はそこを更新します。個人共通へ書いた行には末尾に `（個人共通）` が付きます。

2 つの参照は現物から読みます（キャッシュへ落ちません）。どちらかを読めなければ何も書かずに
終了コード 1 です。保存は個人共通、チーム共通の順に行います。

同期済みのハッシュは、グループ別の置き場では `$DEVBASE_ROOT/.env.sources.<g>.yml` にグループごとに
控えるため、あるグループで同期した後でも、別のグループの同期が変更を見落としません（それ以外の設定では
`$DEVBASE_ROOT/.env.sources.yml`）。控えに登録するのは、個人共通とチーム共通のどちらかにキーがある
ソースです。

| 出力の行 | 意味 |
|---|---|
| `<ソース>: ソース未登録（<参照>にキーがあります）。今のファイルと比べて更新しました` | 控えに項目が無いソースのキーが参照にあった。今のファイルと値を比べて書き、控えに登録した |
| `<ソース>: ソース未登録（<参照>にキーがあります）。今のファイルと比べて変更なし` | 同上で、値が同じだった。控えに登録した |
| `<ソース>: ソース未登録（<参照>にキーがあります）。元のファイルがありません` | 同上で、元のファイル（`~/.aws/config` など）が無い |
| `<ソース>: 控えと比べられません（ハッシュか元のファイルがありません）` | 控えに項目はあるが、ハッシュか元のファイルが無い |

## TUI でのキーの編集と OpenBao の接続設定

`devbase list` の TUI の「環境変数」の操作に、次の 2 つがあります。

### キーの一覧と編集

1. 範囲（「共通」か「プロジェクト」）を選ぶ。プロジェクトが 1 つも無ければ「共通」だけが出る
2. 共通でグループ別の置き場（`version: 2`）なら、グループを選ぶ（既定は `$DEVBASE_ROOT/env` のグループ。
   候補に無い名前は「名前を入力」で入れる）。プロジェクトならプロジェクトを選び、グループはそのプロジェクトの
   グループに決まる
3. キーの一覧が出る。各行はキー・持ち主（チーム / 個人）・適用範囲（共通 / プロジェクト）・グループ・値で、
   値は常に `******` で伏せる。同じキーが複数の参照にあれば全ての行が出て、重ね順で勝つ行に `★` が付く
   （`projects/<name>/env` の非機密の設定とは比べない）

```text
★ AWS_CONFIG_BASE64            個人    共通              group-a   ******
  AWS_CONFIG_BASE64            チーム  共通              group-a   ******
★ GITHUB_TOKEN                 チーム  共通              group-a   ******
```

| 操作 | 内容 |
|---|---|
| 先頭の「＋ キーを追加」 | キー名 → 持ち主 → 適用範囲 → 値の順に入れる。持ち主は個人単位の機密を持つ backend（OpenBao）だけ、適用範囲はプロジェクトの範囲で開いたときだけ選ぶ |
| 行を選んで「値を変更」 | 同じ置き場の値を入れ直す |
| 行を選んで「削除」 | 確認で「はい」を選んだときだけ消す |

- 値は伏せ字の欄で入れ、画面に出しません。空の値と改行を含む値は受け付けません（改行を含む値は
  `devbase env edit` で編集してください）
- キー名は英字か `_` で始まり、英数字と `_` だけです。`DEVBASE_ACCOUNT_GROUP` は書けません
- 書き込みは `devbase env set` / `delete` と同じで、選んだ置き場は次の指定と同じ意味です

| 選んだ置き場 | CLI での同じ操作 |
|---|---|
| チーム・共通・グループ g | `devbase env set KEY=VALUE --group g` |
| 個人・共通・グループ g | `devbase env set KEY=VALUE --user --group g` |
| チーム・プロジェクト p | `projects/p` で `devbase env set KEY=VALUE -p` |
| 個人・プロジェクト p | `projects/p` で `devbase env set KEY=VALUE -p --user` |

- 一覧はサーバから読み直します（キャッシュを使いません）。読めないとき（接続できない・403）は、どの範囲で
  何が起きたかを 1 行出して操作メニューへ戻ります
- 読んでから書くまでの間に他の誰かが同じ参照を書き換えたとき（版の食い違い）は上書きせず、一覧を
  読み直します。読み直した内容でもう一度操作してください

### OpenBao の接続設定

backend が `openbao` の端末で、接続先（`url`）とブートストラップ機密（`role_id` / `secret_id`）を変えます。

- 見出しに今の接続先と、ブートストラップ機密が設定済みかだけを出します（`role_id` / `secret_id` の値は出しません）
- `role_id` / `secret_id` は伏せ字の欄で入れます。空のまま Enter の欄は変えません。`role_id` を変えるときは
  `secret_id` も入れてください
- 保存は `devbase env backend use openbao` と同じで、`mount` / `user` / レイアウト / `group_aliases` /
  キャッシュの設定は変えません。`https` でない接続先（ループバック宛ての `http` を除く）は保存しません
- 保存の後に `devbase env backend test` と同じ確認を行います。失敗しても保存した設定は残り、入れ直せます
- OpenBao の token は入力も保存もしません（実行のたびにブートストラップ機密で取り直します）

backend がファイル（平文・age）の端末では、今の backend の名前と、切り替えのコマンド
（`devbase env backend use openbao --url <OpenBao の URL> --role-id <role_id> --secret-id-stdin` と
`devbase env backend migrate --to openbao`）を示すだけで、設定を変えません。

## `devbase env list`

設定済みの環境変数を一覧表示します。

```
devbase env list [-g|-p] [-r] [-k] [--user] [--group NAME]
```

| オプション | 説明 |
|-----------|------|
| `-g` | グローバル変数のみ表示 |
| `-p` | プロジェクト変数のみ表示 |
| `-r` | 値も表示（デフォルトではキーのみ） |
| `-k` | キー名でソート |
| `--user` | 個人単位の置き場だけを表示（サーバ backend のみ） |
| `--group NAME` | 対象のグループを指定（グループ別の置き場のみ）。見出しにグループ名が付く（例: `=== グローバル（グループ kkg） ...`。`group_aliases` で読み替えているグループは `=== グローバル（グループ default → nyle） ...`） |

```bash
# グローバル変数のみ、値付きで表示
devbase env list -g -r

# プロジェクト変数をキー名順で表示
devbase env list -p -k
```

## `devbase env set`

環境変数を設定します。

```
devbase env set KEY=VALUE [-p] [--user] [--group NAME]
```

| オプション | 説明 |
|-----------|------|
| `-p` | プロジェクトレベルに設定（デフォルトはグローバル） |
| `--user` | 個人単位の置き場に設定（サーバ backend のみ。[機密の保存先を選ぶ](../env-backend.md)） |
| `--group NAME` | 対象のグループの置き場に設定（グループ別の置き場のみ。`-p` と組み合わせるときはプロジェクトのグループと同じ置き場に限る） |

`DEVBASE_ACCOUNT_GROUP` は書けません（どのオプションでも終了コード 1）。アカウントグループは
`projects/<name>/env` か `$DEVBASE_ROOT/env` に書きます（[環境変数ガイド](../environment-variables.md#機密の置き場には書けない)）。

```bash
# グローバルに設定
devbase env set ANTHROPIC_API_KEY=sk-xxx

# プロジェクトレベルに設定
devbase env set GCP_ACTIVE_PROFILE=my-project -p

# 自分だけの値として設定 (OpenBao)
devbase env set AWS_ACCESS_KEY_ID=AKIA... --user

# グループ kkg のチーム共通に設定 (グループ別の置き場)
devbase env set SOME_KEY=value --group kkg
```

## `devbase env get`

環境変数の値を取得します。個人共通 → チーム共通 → 個人のプロジェクト → チームのプロジェクト
の順に探します。

```
devbase env get KEY [--user] [--group NAME]
```

`--group NAME` を付けると、そのグループの置き場を探します（グループ別の置き場のみ）。
`NAME` が実行したプロジェクトのグループと違う置き場なら、共通の参照だけを探します。

```bash
devbase env get AWS_PROFILE
```

## `devbase env delete`

環境変数を削除します。

```
devbase env delete KEY [-p] [--user] [--group NAME]
```

| オプション | 説明 |
|-----------|------|
| `-p` | プロジェクト設定から削除（デフォルトはグローバル）。`projects/<name>` 配下で実行してください |
| `--user` | 個人単位の置き場から削除（サーバ backend のみ） |
| `--group NAME` | 対象のグループの置き場から削除（グループ別の置き場のみ） |

```bash
# グローバルから削除
devbase env delete OLD_API_KEY

# カレントプロジェクトの設定から削除
devbase env delete GCP_ACTIVE_PROFILE -p
```

## `devbase env edit`

デフォルトエディタで設定を開きます。設定が暗号化されている場合は、復号した内容を一時ファイルで編集し、保存時に再暗号化します。

```
devbase env edit [-p] [--user] [--group NAME]
```

| オプション | 説明 |
|-----------|------|
| `-p` | カレントプロジェクトの設定を開く（デフォルトはグローバル）。`projects/<name>` 配下で実行してください |
| `--user` | 個人単位の置き場を開く（サーバ backend のみ） |
| `--group NAME` | 対象のグループの置き場を開く（グループ別の置き場のみ） |

## `devbase env project`

プロジェクト固有の環境変数を対話式で設定します。

```
devbase env project
```

グループ別の置き場では、実行したプロジェクトのグループの置き場へ書きます。

## `devbase env keygen`

設定の暗号化に使う devbase 専用の age 鍵を生成します。鍵ファイルは `0600`、置き場のディレクトリは `0700` で作成されます。

```
devbase env keygen [--force] [-y|--yes]
```

| オプション | 説明 |
|-----------|------|
| `--force` | 既存の鍵を作り直す。**旧鍵でしか復号できない機密は失われます** |
| `-y`, `--yes` | `--force` 時の確認プロンプトを省略（CI 等での自動実行用） |

鍵の場所は次のとおりで、コマンドラインからは指定できません（生成先と復号時の探索先を必ず一致させるため）。別の場所に置きたい場合は `DEVBASE_AGE_KEY_FILE` を設定してから実行します。

| 指定 | 鍵ファイルのパス |
|-----|-----------------|
| 既定 | `~/.config/devbase/age/keys.txt`（`XDG_CONFIG_HOME` があればその配下） |
| `DEVBASE_AGE_KEY_FILE` | 指定したパスをそのまま使用 |

```bash
# 既定の場所に生成する（既に鍵があれば公開鍵を表示するだけで何もしない）
devbase env keygen

# 置き場を変えて生成する
DEVBASE_AGE_KEY_FILE=~/keys/devbase-age.txt devbase env keygen

# 既存の鍵を捨てて作り直す（確認プロンプトあり）
devbase env keygen --force
```

> **鍵のバックアップは必須です。** この鍵を失うと、暗号化した機密は誰にも復号できません（devbase 側にも復旧手段はありません）。生成後に表示される鍵ファイルを、パスワード管理ツールなど端末とは別の場所へ必ず複製してください。鍵は全ワークスペース共通のため、`--force` で作り直すと他のワークスペースで暗号化した機密も復号できなくなります。

## `devbase env encrypt`

平文で保存されている設定を、暗号化ストア (`$DEVBASE_ROOT/secrets/`) へ移します。事前に `devbase env keygen` で鍵を作っておく必要があります。

```
devbase env encrypt [--project NAME]... [--dry-run] [-y|--yes]
```

| オプション | 説明 |
|-----------|------|
| `--project NAME` | 対象を指定プロジェクトだけに絞る（繰り返し指定可）。指定すると共通設定は対象外になります |
| `--dry-run` | 変更内容と構成ファイルの差分を表示するだけで、何も書き換えません |
| `-y`, `--yes` | 確認プロンプトを省略 |

実行すると次の 3 つが行われます。

1. 平文の設定を暗号化して `secrets/` 配下へ保存する
2. **暗号化した内容を読み戻して元と一致することを確認**してから、元の平文を `backups/env-encrypt/<日時>/` へ退避する
3. 各プロジェクトの `compose.yml` から機密ファイルの参照をコメントアウトする（元の行はコメントとして残るため、`decrypt` で復元できます）

```bash
# 何が変わるかを先に確認する
devbase env encrypt --dry-run

# 共通設定とすべてのプロジェクトを暗号化する
devbase env encrypt

# 特定プロジェクトだけを暗号化する
devbase env encrypt --project web
```

> 退避した平文は**自動では消しません**。内容を確認したうえで、案内された `backups/env-encrypt/<日時>/` を削除してください。削除するまでは端末上に平文の認証情報が残ったままです。

> 退避先は毎回新しく作られます。同じ秒に再実行して名前が衝突した場合は `<日時>-2`, `<日時>-3` … と別のディレクトリになり、**過去の退避物を上書きすることはありません**。

## `devbase env decrypt`

暗号化された設定を平文へ戻します。`encrypt` と対になる退避コマンドです。

```
devbase env decrypt [--project NAME]... [--dry-run] [-y|--yes]
```

オプションは `encrypt` と同じです。`compose.yml` のコメントアウトも元に戻るため、暗号化前の状態へそのまま復帰します。機密ファイルは `KEY=VALUE` の一覧へ畳まず原文のバイト列のまま暗号化しているので、コメント・空行・`export KEY=...` 表記・値のクォートもそのまま戻ります。

> 原文が保たれるのは**値を書き換えるまで**です。暗号化した状態で `devbase env set` などを実行すると、内容は `KEY=VALUE` を昇順に並べた書式へ正規化され、コメントは残りません（平文だけを使っていた頃と同じ挙動です）。

```bash
devbase env decrypt --dry-run
devbase env decrypt
```

## `devbase env exec`

復号した機密を環境変数として渡した状態で、任意のコマンドを実行します。値はその子プロセスの環境変数としてのみ渡り、ファイルには書き出されません。

```
devbase env exec [--context NAME] -- CMD [ARGS...]
```

起動ラッパーは共通の機密ファイルを読み込まないため、ホスト側で機密を必要とする処理（Docker Compose の変数展開など）はこのコマンドを通します。devbase 自身の `devbase build` も内部でこれを使っています。

カレントディレクトリがプロジェクトなら、その `project.local.yml` と env から docker context を解決して子プロセスの `DOCKER_CONTEXT` に載せます。`--context NAME` はそれを上書きします（`devbase build --context NAME` が内部で渡す口です）。

```bash
# コンテナに渡る値を確認する
devbase env exec -- printenv ANTHROPIC_API_KEY

# 機密を必要とする compose 操作を手で実行する
devbase env exec -- docker compose config
```

> `devbase env exec -- printenv` のように値を表示するコマンドは、画面共有や端末ログに認証情報がそのまま残ります。実行する場面に注意してください。

## `devbase env token`

起動中の dev コンテナの `~/.vault-token` を、OpenBao の新しい token で置き換えます（backend が
`openbao` のときだけ）。コンテナの中の `bao` の token が切れたときに使います。詳しくは
[機密の保存先を選ぶ](../env-backend.md) の「コンテナの中から `bao` を使う」を参照してください。

```
devbase env token [--print] [--context NAME]
```

| オプション | 説明 |
|---|---|
| なし | 現在地のプロジェクトの起動中の dev コンテナ（サービス `<dev>-<n>`）すべてへ書き、書いたコンテナ名を表示する |
| `--print` | コンテナへ書かず、token だけを標準出力へ出す |
| `--context NAME` | docker context を一時的に上書きする |

プロジェクトの外で実行したとき、起動中の dev コンテナが無いときは、token を発行せずに
終了コード 1 で止まります。一部のコンテナへ書けなかったときも 1 です。

## `devbase env rekey`

誰が機密を復号できるかを変更し、暗号化済みの機密をまとめて暗号化し直します。

```
devbase env rekey [--add-recipient KEY]... [--remove-recipient KEY]... [--dry-run] [-y|--yes]
```

| オプション | 説明 |
|-----------|------|
| `--add-recipient KEY` | 受信者を追加（繰り返し指定可）。`age1...` / `ssh-ed25519 ...` / `@PATH` |
| `--remove-recipient KEY` | 受信者を削除（繰り返し指定可） |
| `--dry-run` | 変更内容を表示するだけで、何も書き換えません |
| `-y`, `--yes` | 確認プロンプトを省略 |

受信者は `$DEVBASE_ROOT/secrets/recipients.txt` に記録されます。リストがまだ無い状態で追加すると、自分の公開鍵も一緒に登録されます（登録しないと自分が受信者から外れ、自分の機密を復号できなくなるため）。

```bash
# 同僚を追加する
devbase env rekey --add-recipient age1xxxxxxxx...

# 抜けた人を外す
devbase env rekey --remove-recipient age1xxxxxxxx...
```

> 自分の公開鍵を受信者から外すと、再暗号化後にその端末では機密を復号できなくなります。実行前に警告が表示されます。

受信者リストの更新と全機密の再暗号化は、途中で失敗しても中途半端な状態を残さない 1 つのまとまりとして適用されます。書き込みに失敗した場合は受信者リストも各暗号文も実行前の内容へ戻るため、旧受信者宛と新受信者宛の暗号文が混在することはありません。

## `devbase env doctor`

端末上に残る平文と、除外設定の穴を点検します。問題が見つかると非ゼロで終了するため、定期実行にも使えます。

```
devbase env doctor
```

確認する内容:

| 観点 | 内容 |
|-----|------|
| 鍵 | 鍵ファイルの有無と権限、置き場のディレクトリ権限 |
| 保存先の衝突 | 暗号化ファイルと平文が同時に存在していないか |
| 退避された平文 | `backups/env-encrypt/` / `backups/env-import/` に平文が残っていないか |
| 控えファイル | `.env.bak-<日時>` のような平文の控えが残っていないか |
| 除外設定 | `.env` / `secrets/` 配下 / `.env.bak-<日時>` / `projects/<name>/.env` が実際に Git から除外されるか |

除外設定の点検は `.gitignore` を読んで解釈するのではなく、代表的なパスを `git check-ignore` に渡して **Git 自身に判定させます**（`.gitignore` の解釈は Git の実装が正であり、独自に真似ると書き方によって食い違うため）。ルート指定（`/.env`）でも任意階層（`**/.env`）でも、Git が実際に除外できていれば報告されません。逆に次のように **Git は除外しない** 書き方は、除外されないパスを挙げて報告します。

- `.env # 機密` — Git は行頭の `#` だけをコメントとして扱うため、これは `.env # 機密` というパターンになります
- `.env` の後に `!.env` — 後段の再包含で除外が取り消されます
- `secrets/*.age` — 配下の平文（`secrets/leftover.env` など）が漏れます

`DEVBASE_ROOT` が Git リポジトリでない場合や `git` が使えない場合は、「除外設定を確認できませんでした」と報告します（誤って「問題なし」とは言いません）。

## `devbase env export`

複数プロジェクトの `.env` 群を暗号化したまま 1 つのバンドルにまとめて書き出します。

```
devbase env export <bundle>
```

オプション（age 鍵 / passphrase / S3 入出力など）の詳細は
[環境変数の export / import ガイド](../env-export-import.md#devbase-env-export-リファレンス)を参照してください。

グループ別の置き場では、共通の機密は対象のグループのものを、プロジェクトは対象のグループと
同じ置き場のものだけを集めます。外したプロジェクトは名前とグループを標準エラーへ出し、
その置き場へは要求を出しません。`--no-metadata` を付けなければ、対象のグループの
`.env.sources.<g>.yml` を含めます。

## `devbase env import`

`devbase env export` で作成したバンドルを復号し、環境変数を取り込みます。

```
devbase env import <bundle>
```

`--dry-run` での確認や identity 鍵指定などの詳細は
[環境変数の export / import ガイド](../env-export-import.md#devbase-env-import-リファレンス)を参照してください。

グループ別の置き場では、共通の機密は対象のグループへ、プロジェクトはそれぞれのプロジェクトの
グループへ取り込みます。バンドルに対象のグループと違う置き場のプロジェクトがあると、
**1 件も取り込まずに**プロジェクト名とグループを挙げて終了コード 1 で止まります。
案内される `--exclude-project NAME` を付けて外すか、そのグループのプロジェクトの
ディレクトリで実行してください。`--merge-metadata` は対象のグループの `.env.sources.<g>.yml` へ
書きます。

## `devbase env backend`

機密の保存先（平文 / age / OpenBao サーバ）を選び、確かめ、移します。詳細は
[機密の保存先を選ぶ](../env-backend.md) を参照してください。

```
devbase env backend status
devbase env backend use <name> [--url URL] [--mount NAME] [--user ID]
                               [--role-id ID] [--secret-id-stdin] [--cache|--no-cache]
                               [--layout flat|group] [--group-alias FROM=TO]...
devbase env backend test
devbase env backend migrate --to <age|openbao> [--exclude-project NAME]... [--dry-run] [--yes]
```

| サブコマンド | 内容 |
|---|---|
| `status` | 現在の backend 名、保存先、参照ごとの置き場、キャッシュの状態を表示。グループ別の置き場では、レイアウト、対象のグループ（読み替えがあれば `default → nyle` の形）とそれを決めたファイル、そのグループで組んだ 4 つのパスも出す |
| `use <name>` | backend を切り替える（`auto` / `plaintext` / `age` / `openbao`）。検証に失敗したときは設定を書き換えない。`secret_id` は `--secret-id-stdin` か伏せ字入力で受け取り、引数では受け取らない |
| `test` | サーバへ接続し、参照ごとに読めるかを確かめる。グループ別の置き場では対象のグループの置き場だけを調べ、グループの違うプロジェクトは名前を表示して調べない |
| `migrate --to NAME` | チーム単位の機密を別の backend へ写す。移行先に同じキーがあれば 1 件も書かない。読み戻して一致しなければ作成したキーだけを消す |

`use openbao` のオプション（詳細は [機密の保存先を選ぶ](../env-backend.md#アカウントグループごとの置き場version-2)）:

| オプション | 説明 |
|---|---|
| `--layout group` | 置き場をアカウントグループごとに分ける（`version: 2`）。`--layout` を省くと、OpenBao の設定がまだ無ければ `group`、あれば今のレイアウトを引き継ぐ |
| `--layout flat` | パスにグループを含まない従来の置き場（`version: 1`）で書く。読み替えは捨てる |
| `--group-alias FROM=TO` | グループ `FROM` の機密を置き場のグループ名 `TO` で扱う（繰り返し可）。指定すると今の読み替えを丸ごと置き換える。`--layout flat` や `version: 1` の設定と組み合わせると終了コード 2 |

`--layout` / `--group-alias` は `openbao` 以外の backend と組み合わせると終了コード 2 です。
レイアウトが変わると、手元のキャッシュ（`secrets/cache/`）を消します。

```bash
# グループ別の置き場を選び、グループを宣言していないプロジェクトを nyle の置き場で扱う
devbase env backend use openbao --layout group --group-alias default=nyle

# 従来の置き場へ戻す
devbase env backend use openbao --layout flat
```

`migrate` のオプション:

| オプション | 説明 |
|---|---|
| `--exclude-project NAME` | そのプロジェクトの機密を移行から外す（繰り返し可）。読まず、書かず、退避もしない。`projects/` に無い名前は終了コード 2 |
| `--dry-run` | 移す参照とキー名だけを表示する（値は出さない）。グループ別の置き場では書き先の `<mount>/<パス>` も出す |
| `--yes` | 確認プロンプトを省略 |

グループ別の置き場への `migrate --to openbao` は、共通の機密を `$DEVBASE_ROOT/env` のグループへ、
プロジェクトの機密をそれぞれのプロジェクトのグループへ書きます。`--to age` では、共通の機密は
`$DEVBASE_ROOT/env` のグループのものだけを移し、他のグループのチーム共通は移さずに、グループ名と
パスを表示してサーバ上に残します。
