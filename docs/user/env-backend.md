# 機密の保存先を選ぶ（OpenBao との連携）

devbase が扱う機密の保存先を、手元のファイル（平文 / age 暗号化）から、WebUI を持つ
OSS の機密管理サーバ [OpenBao](https://openbao.org/) へ載せ替えるためのガイドです。

保存先は `devbase env backend use <name>` で明示的に選び、いつでも元へ戻せます。
何も設定していない端末の挙動は変わりません。

## 何が変わるのか

| | 設定なし（既定） | `backend: age` | `backend: openbao` |
|---|---|---|---|
| 保存先の決め方 | ファイルの存在で自動判定 | 常に `secrets/*.age` | 常に OpenBao サーバ |
| 共通の機密 | `.env` または `secrets/global.env.age` | `secrets/global.env.age` | `team/global` |
| プロジェクトの機密 | `projects/<name>/.env` または `secrets/projects/<name>.env.age` | `secrets/projects/<name>.env.age` | `team/projects/<name>` |
| 個人単位の機密 | 持たない | 持たない | `users/<user>/global` と `users/<user>/projects/<name>` |
| 日々の操作 | `devbase env set` / `get` / `edit` … | **変わらない** | **変わらない** |
| サーバ不達時 | — | — | 手元の暗号化キャッシュで起動 |

サーバ上では `.env` 1 つが KV v2 のパス 1 つに対応し、キーと値はそのパスにまとめて置かれます。
マウント名が `devbase` なら、共通の機密は `devbase/team/global` にあります。

表の `backend: openbao` の列は、パスにアカウントグループを含まない従来の置き場（`version: 1`）です。
新しく OpenBao を選ぶ端末の既定は、置き場をアカウントグループごとに分ける `version: 2` で、
パスは `team/<group>/global` のようになります。詳しくは
[アカウントグループごとの置き場](#アカウントグループごとの置き場version-2)を参照してください。

サーバへの接続に使う資格情報（AppRole の `role_id` / `secret_id`）は、既存の age 暗号化
ストアと同じ仕組みで `secrets/bootstrap.env.age` に保存されます。**OpenBao を使う場合も
age の鍵は必要です。** 先に `devbase env keygen` を済ませてください。

## 使いはじめる

### 1. OpenBao 側で用意するもの

サーバの構築と運用は devbase の範囲外です。管理者から次の値を受け取ってください。

| 値 | 例 |
|---|---|
| 接続先 URL（`https`。ホストとポートまで） | `https://openbao.example.com` |
| KV v2 のマウント名（既定 `devbase`） | `devbase` |
| あなたの識別子（個人単位の機密の置き場に使う。社内メールアドレスの `@` より前） | `member01` |
| あなたの AppRole の `role_id` | — |
| **この端末用**の `secret_id` | — |

`secret_id` は端末ごとに発行されます。端末を増やすときは、その端末用の `secret_id` を
新しく受け取ってください（1 台の失効が他の端末に及ばないようにするためです）。

### 2. backend を切り替える

`secret_id` は**コマンド引数では受け取りません**（`ps` から読めるため）。標準入力か、
端末での伏せ字入力で渡します。

```bash
devbase env keygen                      # まだなら
printf '%s\n' "$SECRET_ID" | devbase env backend use openbao \
  --url https://openbao.example.com \
  --user member01 \
  --role-id <role-id> \
  --secret-id-stdin \
  --layout group \
  --group-alias default=nyle
```

TTY で実行する場合は `--secret-id-stdin` を省くと伏せ字入力を求められます。マウント名が
`devbase` 以外なら `--mount NAME` を足します。

`--layout group` は置き場をアカウントグループごとに分ける設定（`version: 2`）で、
`--group-alias default=nyle` はグループを宣言していないプロジェクト（`default`）の機密を
`nyle` の置き場で扱う読み替えです。**`--layout` を付けずに新しく `use openbao` しても
`version: 2` になります。** 読み替えを付けないと、グループを宣言していないプロジェクトは
`team/default/…` を読みます。チームで `default` の機密を置くグループ名が決まっていれば、
`--group-alias default=<そのグループ名>` を付けてください。
すでに OpenBao の設定がある端末で `--layout` を省くと、今のレイアウトを引き継ぎます。
詳しくは [アカウントグループごとの置き場](#アカウントグループごとの置き場version-2)を
参照してください。

設定は `secrets/backend.yml`（機密を含まない）に、資格情報は `secrets/bootstrap.env.age`
（age 暗号化）に保存されます。どちらも `0600` で、`secrets/` は Git の除外対象です。

### 3. 接続を確かめる

```bash
devbase env backend test
devbase env backend status
```

`test` は認証と参照ごとの取得を行い、接続先と読めた参照の件数を表示します。到達できない・
認証できない場合は理由を表示して非ゼロで終了します。

切り替えた後の接続先（`url`）とブートストラップ機密（`role_id` / `secret_id`）は、`devbase list` の
TUI の「環境変数」→「OpenBao の接続設定」からも変えられます。`role_id` / `secret_id` は伏せ字で入れ、
空のまま Enter の欄は変えません。保存の後に `test` と同じ確認を行います。OpenBao の token は
入力も保存もしません（詳細は [CLI リファレンス](cli-reference/03-env.md#openbao-の接続設定)）。

### 4. 既存の機密を移す

```bash
devbase env backend migrate --to openbao --dry-run   # 移すキー名だけを確認（値は出ない）
devbase env backend migrate --to openbao
```

移行は次の順で進み、途中で失敗すると設定は移行前のまま残ります。

1. 移行先の同じ参照を読み、**同じキーがあれば 1 件も書かずに中止**する（衝突したキー名を表示）
2. 移行先に元からあった内容へ移行元を重ねて書き込む（元からあったキーは消えない）
3. 読み戻して元と一致することを確認する。一致しなければこの実行で作成したキーだけを消す
4. 成功したときだけ `backend.yml` を `openbao` に書き換える

元の age / 平文ファイルは `backups/env-backend-migrate/<日時>/` へ退避され、自動では
削除されません。内容を確認してから消してください。

`version: 2`（グループ別の置き場）へ移すときは、参照ごとにグループを決めて書きます。
実行したディレクトリには左右されません。

| 移すもの | 書き先のグループ |
|---|---|
| 共通の機密（`.env` / `secrets/global.env.age`） | `$DEVBASE_ROOT/env` の `DEVBASE_ACCOUNT_GROUP`（無ければ `default`） |
| プロジェクトの機密 | そのプロジェクトの `projects/<name>/env` → `$DEVBASE_ROOT/env` → `default` |

`--dry-run` は参照ごとに書き先の `<mount>/<パス>` とキー名を表示します（値は出ません）。

書き込みの権限が無いグループのプロジェクトや、別の仕組みで機密を配っているプロジェクトは
`--exclude-project NAME`（繰り返し可）で外します。外したプロジェクトの機密は読まず、
書かず、退避もせず、ファイルは元の位置に残ります。`projects/` に無い名前を渡すと、
打ち間違いとして何もせずに終了コード 2 で止まります。

```bash
devbase env backend migrate --to openbao --exclude-project csc --dry-run
```

移すのはチーム単位の機密（共通とプロジェクト）だけです。個人単位の機密は
`devbase env set --user` で入れ直してください。チーム単位のパスへ書けるのは、サーバ側で
書き込みの権限を付けられた人だけです。権限が無いと「書き込み権限が無い」と表示されて
止まるので、管理者に移行を依頼してください。

## 日々の操作

`list` / `get` / `set` / `delete` / `edit` はそのまま使えます。`devbase env list` の保存形式
表示には `[openbao]` が付きます。

### 個人単位の機密（`--user`）

`.env` には、サービスアカウントの鍵のように**チームで同じ値を使う機密**と、各自のクラウド
アクセスキーのように**利用者ごとに値が違う機密**が混ざります。1 台のサーバへ集約すると、
後者が他の利用者から見える状態を作らないための区別が要ります。

`--user` を付けると、持ち主が「個人」の置き場（`users/<user>/...`）を相手にします。
`-p` が適用範囲（共通 / プロジェクト）を選ぶのに対し、`--user` は持ち主だけを選びます。

| 指定 | `set` / `delete` / `edit` の宛先 |
|---|---|
| なし | チーム共通（`team/global`） |
| `-p` | チームのプロジェクト（`team/projects/<name>`） |
| `--user` | 個人共通（`users/<user>/global`） |
| `--user -p` | 個人のプロジェクト（`users/<user>/projects/<name>`） |

表のパスは `version: 1` のものです。`version: 2` ではどの宛先にもグループが入ります
（[パスの対応](#パスの対応)）。

`list` は指定の無い軸を絞らず、存在する参照をすべて出します。`get` は
個人共通 → チーム共通 → 個人のプロジェクト → チームのプロジェクト の順に探します。

`devbase list` の TUI の「環境変数」→「キーの一覧と編集」では、キーごとに持ち主・適用範囲・グループを
見ながら追加・変更・削除できます。書き込みは `set` / `delete` と同じ宛先です
（[CLI リファレンス](cli-reference/03-env.md#キーの一覧と編集)）。

`devbase up` でコンテナへ載る値は、次の順に重ねて決まります（後の層が勝つ）。

1. チーム共通の機密
2. 個人共通の機密
3. プロジェクトの非機密設定（`projects/<name>/env`）
4. プロジェクトのチーム機密
5. プロジェクトの個人機密

ファイル backend（設定なし / `age` / `plaintext`）は個人単位の置き場を持ちません。
`--user` を付けた書き込みは、チーム単位へ落とさずにエラーで止まります。

個人単位のパスは、サーバ側のポリシーが本人（`--user` の識別子と同じ entity）にだけ読み書きを
許します。他の人の `users/...` は読めず、チーム単位のパスは読めるだけです（書けるのは権限を
付けられた人だけ）。WebUI からログインしても同じ範囲になります。

### 注意: コメントは残らない

OpenBao の KV v2 にはコメント・空行の置き場がありません。`devbase env edit` で書いた
コメントは次に開いたときには消えています。

### 注意: 同時に編集すると後から書いた側が止まる

1 つの参照（`.env` 1 つ分）は丸ごと 1 回で書き込まれ、直前に読んだときの版を指定します。
`env edit` でエディタを開いている間に他の人が同じ参照を書き換えていると、書き戻しは
「他の誰かが先に書きました」と表示して止まります。黙って上書きすることはありません。
もう一度 `env edit`（または `set`）をやり直してください。

## アカウントグループごとの置き場（`version: 2`）

アカウントグループ（`DEVBASE_ACCOUNT_GROUP`。[環境変数ガイド](environment-variables.md#アカウントグループ-devbase_account_group)）
ごとにボリュームが分かれるのと同じく、機密の置き場もグループごとに分けられます。
`backend.yml` の `version: 2`（`openbao.layout: group`）がこの設定です。

分けると、プロジェクトのコンテナへ届く機密は、そのプロジェクトのグループの置き場のものだけに
なります。別のグループの機密をプロジェクトの `env` の空の値で打ち消す必要はありません。
`devbase up` 1 回がサーバへ要求するのも、対象のグループのパスだけです。

ファイル backend（設定なし / `age` / `plaintext`）はグループで分けません。`version: 1` の
設定はこれまでと同じパスを読み書きします。

### パスの対応

`<g>` は置き場のグループ名（`group_aliases` で読み替えた後の名前）、`<user>` は
`openbao.user` です。

| 参照 | `version: 1` | `version: 2` |
|---|---|---|
| チーム共通 | `team/global` | `team/<g>/global` |
| チームのプロジェクト | `team/projects/<name>` | `team/<g>/projects/<name>` |
| 個人共通 | `users/<user>/global` | `users/<user>/<g>/global` |
| 個人のプロジェクト | `users/<user>/projects/<name>` | `users/<user>/<g>/projects/<name>` |

全グループに共通の置き場はありません。どのグループでも同じ値を使う機密は、グループごとの
置き場へ同じ値を置きます。

置き場のグループ名に `global` と `projects` は使えません（`version: 1` のパスと重なるため）。

### グループの決まり方

プロジェクトのグループは、機密を読む前に**非機密の `env` ファイルだけ**から決まります。

1. `projects/<name>/env` の `DEVBASE_ACCOUNT_GROUP`
2. `$DEVBASE_ROOT/env` の `DEVBASE_ACCOUNT_GROUP`
3. どちらにも無ければ `default`

プロジェクトの下位ディレクトリで打っても、`projects/<name>/env` を直接読むため同じグループに
なります。プロジェクトの外（`$DEVBASE_ROOT` など）では 2 → 3 で決まります。

**機密の置き場に書いた `DEVBASE_ACCOUNT_GROUP` はグループの決定に使われません**（置き場を
決める値をその置き場から読むことになるため）。置き場の値は機密の合成から外れ、コンテナを
起動するプロセスの環境変数にもコンテナにも載りません（`version: 1` でも同じ）。置き場に
残っていればコマンドのたびに警告が 1 回出るので、そこに書かれた
`devbase env delete DEVBASE_ACCOUNT_GROUP ...` で消してください。`devbase env set` は
このキーを置き場へ書かずに終了コード 1 で止まります（[環境変数ガイド](environment-variables.md#機密の置き場には書けない)）。

`default` をボリューム名（`devbase_home_default`）はそのままに、置き場の上だけ別の名前で
扱うには `group_aliases` を使います。`default: nyle` なら、グループを宣言していない
プロジェクトは `team/nyle/…` を読み書きします。

### `secrets/backend.yml` の例

```yaml
version: 2
backend: openbao
openbao:
  url: https://openbao.example.com
  mount: devbase
  user: member01
  layout: group                 # version: 2 では必須。group だけを受け付ける
  path_team_prefix: team        # チーム単位の親 (既定 team)
  path_user_prefix: users
  group_aliases:                # グループ名 → 置き場のグループ名 (既定は空)
    default: nyle
  timeout_seconds: 5
cache:
  enabled: true
```

`version: 2` には `path_team_global` / `path_team_project_prefix` を置けず、`version: 1` には
`layout` / `path_team_prefix` / `group_aliases` を置けません。置くと設定エラーとして止まります。
`version: 2` の設定を読めない古い devbase は、従来のパスを黙って読まずに止まります。

### 切り替える・戻す

```bash
# グループ別の置き場へ切り替える (default を nyle の置き場で扱う)
devbase env backend use openbao --layout group --group-alias default=nyle

# レイアウトはそのままで読み替えを指定し直す (今の読み替えは丸ごと置き換わる)
devbase env backend use openbao --group-alias default=nyle

# 従来の置き場 (version: 1) へ戻す
devbase env backend use openbao --layout flat
```

| 指定 | 結果 |
|---|---|
| `--layout` なし、`backend.yml` に `openbao` の設定が無い | `version: 2` で書く |
| `--layout` なし、`backend.yml` に `openbao` の設定がある | 今のレイアウトと読み替えを引き継ぐ |
| `--layout group` | `version: 2` で書く |
| `--layout flat` | `version: 1` で書く。読み替えがあれば捨て、捨てた旨を表示する |
| `--group-alias FROM=TO`（繰り返し可） | 読み替えをこの指定で置き換える。省けば今の読み替えを引き継ぐ |
| `--group-alias` と `--layout flat`（または `version: 1` の設定のまま） | 終了コード 2。設定を書き換えない |
| `--layout` / `--group-alias` と `openbao` 以外の backend | 終了コード 2。設定を書き換えない |

`FROM` と `TO` には `DEVBASE_ACCOUNT_GROUP` と同じ規則が当たり、`TO` に `global` /
`projects` は使えません。通らなければ終了コード 2 で、設定は書き換えません。

`--layout flat` で戻したときに捨てた読み替えは、もう一度 `--layout group` にしても戻りません。
`--group-alias` を付け直してください。

**レイアウトが変わると、手元のキャッシュ（`secrets/cache/`）を消します**（別のグループの機密が
暗号文のまま残り続けないようにするためです）。次にサーバへ到達したときに取り直します。

```text
backend を openbao に設定しました: .../secrets/backend.yml
  接続先:  https://openbao.example.com
  mount:   devbase
  個人単位の識別子: member01
  レイアウト: flat (version 1)
  group_aliases (default → nyle) を捨てました (version: 1 は読み替えを持ちません)
  キャッシュ: 有効
  キャッシュ (.../secrets/cache) を消しました (レイアウトが group から flat へ変わったため)
  接続を確かめる: devbase env backend test
```

devbase はサーバ上のデータを移しません。レイアウトを変えても、前のレイアウトのパスの機密は
サーバに残ります。すでに `team/global` などへ機密を置いている端末でグループ別の置き場へ
切り替えるときは、コンテナの中の `bao kv get` で従来のパスを読み、`devbase env edit --group NAME`
（個人単位は `--user` も）で新しいパスへ書き直します。

### 対象のグループを確かめる（`status`）

`devbase env backend status` は、実行したディレクトリの対象のグループと、どのファイルで
決まったか、そのグループで組んだ 4 つのパスを表示します。

```text
$ cd projects/api && devbase env backend status

=== 機密の保存先 ===
  backend: openbao (設定: .../secrets/backend.yml)
  接続先:  https://openbao.example.com
  mount:   devbase
  個人単位の識別子: member01
  レイアウト: group (version 2)
  グループ:   default → nyle (projects/api/env にも $DEVBASE_ROOT/env にも宣言なし)

  置き場 (<mount>/<path>):
    チーム共通:           devbase/team/nyle/global
    チームのプロジェクト: devbase/team/nyle/projects/api
    個人共通:             devbase/users/member01/nyle/global
    個人のプロジェクト:   devbase/users/member01/nyle/projects/api
```

`projects/web/env` に `DEVBASE_ACCOUNT_GROUP=with` があれば、`projects/web` では
`グループ:   with (projects/web/env)` と `devbase/team/with/…` が出ます。プロジェクトの外では
プロジェクトのパスが `<name>` のまま出ます。

`devbase env backend test` も対象のグループの置き場だけを調べます。グループの違う
プロジェクトは調べず、その名前を「対象のグループと違う置き場のプロジェクトは調べていません」
として表示します。

### 別のグループの置き場を操作する（`--group`）

`env list` / `get` / `set` / `delete` / `edit` / `init` / `sync` は、既定では対象のグループ
（実行したディレクトリのプロジェクトのグループ）の置き場を相手にします。別のグループの
置き場は `--group NAME` で指定します。

```bash
cd "$DEVBASE_ROOT"
devbase env list --group kkg              # team/kkg/global と users/<user>/kkg/global
devbase env set --group kkg KEY=value     # team/kkg/global へ書く
devbase env set --user --group kkg KEY=value
```

| 状況 | 結果 |
|---|---|
| `--group` なし | 対象のグループの置き場 |
| `--group NAME`、`-p` なし | 共通の参照だけが `NAME` のグループになる |
| `--group NAME` と `-p`、プロジェクトと同じ置き場 | そのプロジェクトの参照を読み書きする |
| `--group NAME` と `-p`、プロジェクトと違う置き場 | 両方のグループ名を述べて終了コード 1。読み書きしない |
| `-p` なしの `list` / `get` で、`--group` がプロジェクトと違う置き場 | 共通の参照だけを出す・探す。プロジェクトの参照を含めなかった旨を標準エラーへ出す |
| 使えないグループ名（`ubuntu`、数字だけ、`a/b` など、読み替え後が `global` / `projects`） | 理由を述べて終了コード 2 |
| `version: 1` の設定やファイル backend で `--group` | 「グループ別の置き場を選んだ設定でだけ使える」旨を述べて終了コード 2 |

「同じ置き場」かは読み替えた後の名前で比べます。`default: nyle` の読み替えがあれば、
グループを宣言していないプロジェクトで `-p --group nyle` も `-p --group default` も通ります。

`-p` で別のグループのプロジェクトの参照へ書けないのは、書いても `devbase up` がそこを
読まないためです。プロジェクトのグループを変えるときは `projects/<name>/env` の
`DEVBASE_ACCOUNT_GROUP` を直してください。

`env list` の見出しにはグループが付きます（例: `=== グローバル（グループ kkg） (...) ===`）。
`group_aliases` で読み替えているグループでは、読み替えの前と後が並びます
（例: `=== グローバル（グループ default → nyle） (...) ===`）。`env backend test` の一覧も同じ形で、
見出しのグループ名と隣のパス（`devbase/team/nyle/global`）が同じグループを指します。

### `init` / `sync` / `project` / `export` / `import`

| コマンド | グループの扱い |
|---|---|
| `env init` | 対象のグループのチーム共通へ書く。`--group NAME` で指定できる。`devbase up` が自動で起動する `env init` にはプロジェクトのグループが渡る |
| `env sync` | 対象のグループの個人共通とチーム共通のうち、キーがある方へ書く（両方にあれば個人共通、どちらにも無ければ個人共通）。`--group NAME` で指定できる。同期済みのハッシュは `$DEVBASE_ROOT/.env.sources.<g>.yml` にグループごとに控える（[`env sync`](cli-reference/03-env.md#devbase-env-sync)） |
| `env project` | 実行したプロジェクトのグループの参照へ書く |
| `env export` | 共通は対象のグループのもの。プロジェクトは対象のグループと同じ置き場のものだけを集め、外したプロジェクトの名前とグループを標準エラーへ出す |
| `env import` | 共通は対象のグループへ、プロジェクトはそれぞれのグループへ取り込む。バンドルに対象のグループと違う置き場のプロジェクトがあれば、1 件も取り込まずに名前とグループを挙げて終了コード 1。`--exclude-project NAME` で外して取り込む |

対象のグループは実行したディレクトリで決まります（プロジェクトの中ならそのプロジェクトの
グループ、外なら `$DEVBASE_ROOT/env` のグループ）。別のグループの機密を export / import する
ときは、そのグループのプロジェクトのディレクトリで実行します。

### `up` / `scale` がグループの食い違いで止まったとき

`version: 2` では、コンテナのボリュームのグループと機密のグループが食い違うと、`devbase up` と
`devbase scale` は何も作らずに終了コード 1 で止まります。スナップショット・ボリューム・
`project.local.yml` の `scale` の書き換え・`env init` の起動より前に確かめます。

ボリュームのグループはプロセスの環境変数 `DEVBASE_ACCOUNT_GROUP`、機密のグループは `env`
ファイルで決まるため、次のように打つと食い違います。

```text
$ cd projects/api                     # env にも $DEVBASE_ROOT/env にも宣言なし
$ DEVBASE_ACCOUNT_GROUP=kkg devbase up
Error: ボリュームと機密のアカウントグループが食い違うため起動しません
  ボリューム: kkg (プロセスの環境変数 DEVBASE_ACCOUNT_GROUP)
  機密:       default (projects/api/env にも $DEVBASE_ROOT/env にも宣言なし)
  グループを変えるならプロジェクトの env に DEVBASE_ACCOUNT_GROUP を書いてください
```

直し方は、どちらのグループで起動したいかで決まります。

| 起動したいグループ | 直し方 |
|---|---|
| 環境変数で指定したグループ（例: `kkg`） | `projects/<name>/env` に `DEVBASE_ACCOUNT_GROUP=kkg` を書く。プロジェクトの `env` はラッパーが読み込むため、両方が揃う |
| `env` ファイルで決まるグループ | シェルの環境変数を外す（`unset DEVBASE_ACCOUNT_GROUP`。シェルの設定ファイルで `export` していればその行も消す） |

機密の置き場に書いた `DEVBASE_ACCOUNT_GROUP` はこの食い違いを起こしません（置き場の値は
プロセスの環境変数へ載らないため）。置き場に残っていると別の警告が出るので、そこに書かれた
`devbase env delete DEVBASE_ACCOUNT_GROUP ...` で消してください。

`version: 1` の設定では、この検査は行いません。

## コンテナの中から `bao` を使う

base イメージには OpenBao の CLI `bao`（サーバと同じ 2.6 系）が入っています。backend が
`openbao` の端末で `devbase up` すると、dev コンテナに次の 2 つが渡り、**再起動せずに**
自分の機密を読み書きできます。

| 渡るもの | 形 |
|---|---|
| 接続先 | 環境変数 `BAO_ADDR`（`backend.yml` の `openbao.url`） |
| token | ファイル `~/.vault-token`（`0600`）。`bao` が既定で読む |

コンテナに置くのは 1 時間で切れる token だけで、`secret_id` はホストから出ません。token を
環境変数にしないのは、`docker inspect` や子プロセスの環境に残るためです。

### 読む・書く

置き場のパスは `-mount=devbase` からの相対で、`<user>` は `backend.yml` の `openbao.user` です。
`version: 2`（[グループ別の置き場](#アカウントグループごとの置き場version-2)）では、
`<g>` に置き場のグループ名（読み替えた後の名前）が入ります。

| 置き場 | `version: 1` | `version: 2` |
|---|---|---|
| 個人共通 | `users/<user>/global` | `users/<user>/<g>/global` |
| 個人のプロジェクト | `users/<user>/projects/<name>` | `users/<user>/<g>/projects/<name>` |
| チーム共通（読むだけ） | `team/global` | `team/<g>/global` |
| チームのプロジェクト（読むだけ） | `team/projects/<name>` | `team/<g>/projects/<name>` |

`version: 2` で使うパスは、ホストのプロジェクトのディレクトリで `devbase env backend status`
を実行すると、そのプロジェクトのグループで組んだ形で表示されます。

```bash
# version: 1
bao kv get -mount=devbase users/<user>/global                  # 一覧
bao kv get -mount=devbase -field=API_KEY users/<user>/global   # 1 キー
bao kv patch -mount=devbase users/<user>/global NEW_KEY=value  # 1 キーを足す・変える

# version: 2 (グループ nyle のプロジェクト)
bao kv get -mount=devbase users/<user>/nyle/global
bao kv get -mount=devbase -field=API_KEY users/<user>/nyle/projects/<name>
bao kv patch -mount=devbase users/<user>/nyle/global NEW_KEY=value
```

**`kv put` はパスの中身を丸ごと置き換えます。** 指定しなかったキーは消えるので、1 キーだけを
足す・変えるときは `kv patch` を使ってください。キーを消すのは、ホストの
`devbase env delete --user KEY` が確実です（残すキーを読み直して丸ごと書き戻します。
`version: 2` で別のグループの置き場なら `--group NAME` も付けます）。

起動中のシェルの環境変数は、書き換えても変わりません。今のシェルで新しい値を使うときは
読み直します。次の `devbase up` からはコンテナの環境変数にも載ります。

```bash
export API_KEY="$(bao kv get -mount=devbase -field=API_KEY users/<user>/global)"
```

コンテナで書いた値は、ホストの手元キャッシュ（`secrets/cache/`）には反映されません。ホストの
`devbase up` / `env get` はサーバの現物を読むため、到達できる限り食い違いません。

### token が切れたら

`bao` が `permission denied`（`Code: 403`）を返したら、token の期限（1 時間）が切れています。
**ホストの**プロジェクトのディレクトリで次を実行すると、起動中の dev コンテナすべての
`~/.vault-token` を新しい token に置き換えます。

```bash
devbase env token                 # 起動中の dev コンテナへ書く
devbase env token --print         # token を表示するだけ（手で渡すとき）
```

別ホストの Docker（`project.local.yml` の `docker.context`）で動かしているコンテナにも、同じ
接続先で届きます。`--context NAME` で一時的に上書きできます。

## サーバへ到達できないとき

取得できた機密は、参照ごとに age で暗号化して `secrets/cache/` に控えられます
（キーの取得元を表す指紋と一緒に 1 ファイルへ収めます）。サーバへ**通信できない**ときは
この控えでコンテナを起動し、最終取得時刻を警告として表示します。

| サーバとのやり取りの結果 | キャッシュ |
|---|---|
| 取得できた（0 件・未作成を含む）/ `set` `delete` などの書き込みが成功した | その内容で置き換える |
| 通信できない・応答を解釈できない | 残っている控えを**使う** |
| 認証を拒まれた（`secret_id` の失効、利用者の無効化）/ 読む権限が無い | 控えは残すが**使わず**、非ゼロで終了する |
| 書き込みの版が合わなかった（他の人が先に書いた） | その参照の控えを消す |
| 書き込みの結果が分からなかった（送った後に応答が届かない） | その参照の控えを消す |

控えは読み取り（`devbase up` / `list` / `get`）にだけ使います。`set` / `delete` / `edit` は
サーバから読めないと止まり、控えを元に書き戻すことはありません。

認証拒否でキャッシュへ落ちないのは、失効させた `secret_id` や外した権限が手元の起動を
止められなくなるためです。資格が戻れば、その後の不達では従来どおり控えが使われます。

接続先 URL・マウント名・置き場のパス・`role_id` のいずれかを変えると、変更前の控えは
使われません。

`version: 2` では控えもグループごとに分かれます（`secrets/cache/team/<g>/global.env.age`
など）。グループの違うプロジェクトを順に起動しても控えは互いを上書きせず、不達のときは
それぞれ自分のグループの控えで起動します。`env backend use` でレイアウトを変えると、
控えはすべて消えます。

キャッシュを持ちたくない場合は `--no-cache` を付けて `use` してください。無効にすると、
既存の控えも次の実行で消えます。戻すときは `--cache` を付けます（どちらも付けなければ
現在の設定を引き継ぎます）。

```bash
devbase env backend use openbao --no-cache
devbase env backend use openbao --cache      # 元に戻す
```

## 元へ戻す

```bash
devbase env backend migrate --to age
```

サーバ上の機密は**消しません**（他の利用者が参照している可能性があるため）。残っている
場所（接続先と `<mount>/<パス>`）を表示するので、不要なら WebUI から消してください。

`version: 2` から戻すときは、ファイル backend の共通の機密（`secrets/global.env.age`）が
1 つしかないため、次のように移します。

| 移すもの | 移し元 |
|---|---|
| 共通の機密 | `$DEVBASE_ROOT/env` のグループのチーム共通（`team/<g>/global`） |
| プロジェクトの機密 | それぞれのプロジェクトのグループのチームのプロジェクト |
| 他のグループのチーム共通 | **移さない。** 読み取りの要求も出さず、グループ名とパスを表示してサーバ上に残す |

`--exclude-project NAME` で外したプロジェクトは、`--to age` でも移しません。手元の
キャッシュは消え、`bootstrap.env.age` は残ります（再び `openbao` に戻すときに資格情報を
入れ直さずに済みます）。

移行せずに切り替えだけを行うこともできます。

```bash
devbase env backend use age        # age ストアの内容がそのまま使われる
devbase env backend use auto       # 設定なしと同じ (ファイルの存在で判定)
```

## `secrets/backend.yml` の項目

```yaml
version: 1
backend: openbao              # auto / plaintext / age / openbao
openbao:
  url: https://openbao.example.com     # https のみ (http は localhost 宛てだけ可)
  mount: devbase                       # KV v2 のマウント名
  user: member01                       # 個人単位の置き場に使う識別子 (必須)
  path_team_global: team/global        # 先頭に / を付けない
  path_team_project_prefix: team/projects
  path_user_prefix: users
  timeout_seconds: 5
cache:
  enabled: true
```

対応していない値（`/` で始まるパス、`..` を含む識別子など）は既定へ読み替えず、設定エラー
として止まります。

グループ別の置き場（`version: 2`）の項目は
[`secrets/backend.yml` の例](#secretsbackendyml-の例)を参照してください。

## 鍵の入れ替えと点検

- `devbase env rekey` は `backend: openbao` のときも実行でき、`bootstrap.env.age` と
  `secrets/cache/` 配下も再暗号化します
- `devbase env encrypt` / `decrypt` は age ストア専用です。`backend: openbao` のときは
  止まります（サーバとの間で移すには `migrate` を使います）。`backend: plaintext` のまま
  `encrypt`、`backend: age` のまま `decrypt` も止まります（変換後に設定が指す先から機密が
  消えるため。先に `backend use` で合わせるか `auto` に戻してください）
- `devbase env doctor` は backend 設定の読み込み、資格情報の有無、`backend.yml` /
  `bootstrap.env.age` / `cache/` の権限、Git の除外設定を点検します。`version: 2` では、
  対象のグループの同期済みハッシュの控え（`.env.sources.<g>.yml`）とキャッシュが Git から
  除外されているかも確かめます
- 端末を手放すときは、管理者にその端末の `secret_id` の失効を依頼してください。他の端末の
  `secret_id` はそのまま使えます

## コマンド一覧

| コマンド | 内容 |
|---|---|
| `devbase env backend status` | 現在の backend、保存先、参照ごとの置き場、キャッシュの状態。`version: 2` ではレイアウト・対象のグループとその出所も出す |
| `devbase env backend use <name> [--url] [--mount] [--user ID] [--role-id] [--secret-id-stdin] [--cache\|--no-cache] [--layout flat\|group] [--group-alias FROM=TO]...` | backend を切り替える。検証に失敗したときは書き換えない |
| `devbase env backend test` | サーバへ接続し、参照ごとに読めるかを確かめる（`version: 2` では対象のグループの置き場だけ） |
| `devbase env backend migrate --to age\|openbao [--exclude-project NAME]... [--dry-run] [--yes]` | チーム単位の機密を別の backend へ写す |

関連: [環境変数の暗号化](env-encryption.md) / [環境変数ガイド](environment-variables.md)
