# PLAN57: この端末の機密をアカウントグループ別の置き場へ移し直す

- 発端: #182（PLAN56 の配布後に行う移し直し。PLAN56 の前提 6）
- ワークフローモード: `operation`
  - 根拠: 本番コードも文書も変えず、OpenBao（本番の系）の置き場と、この端末の `secrets/backend.yml` の
    状態だけを変える。記録はこのファイルに残す（課題と計画を置く `issues/` が、この変更を読む人が
    最初に開く場所であるため。PLAN53 と同じ置き場）
- 閉じる課題: #182
- 前提となる変更: PR #184（`backend.yml` の `version: 2`、`env --group`、`env backend use --layout`）。
  この端末の `main` @ 7fe3ad9 に入っている

## 依頼（原文）

> - この端末の置き場 | `team/global`（21 キー）・`team/projects/*`・`users/takemi_ohama/*` をグループ別のパスへ移し直す
> - 移行の順序と、KV v2 の版の履歴に他グループの機密を残さない手順（#181 の記録にある教訓）

（#182 本文「変わるところ（見込み）」と「未決」から）

## 目的

- この端末を `version: 2`（`layout: group`、`default → nyle`）へ切り替え、with / kkg のプロジェクトの
  コンテナへ nyle のチーム共通の機密と会社固有の個人の機密が届かない状態にする
- 古い置き場（`team/global` など）を版の履歴ごと消し、他グループの機密をサーバに残さない

## 前提

- 前提 1: グループの割り当ては非機密の `env` のまま使う。`with-ai-dev` は `with`、`project-trygroup-prd` は
  `kkg`、それ以外（宣言なし）は `default` → `nyle`
- 前提 2: 個人単位のキーの配り方は利用者が 2026-09-15 に決めた（「配る先」の節）
- 前提 3: `GCP_CREDENTIALS_BASE64__default` は `team/nyle/global` へ置く（利用者が 2026-09-15 に決めた）
- 前提 4: ~~書き込みに使う権限は、この端末の AppRole（`devbase-user` + entity の `devbase-team-writer`）。
  PLAN53 の単位 3 で付けたもの。サーバのポリシーはまだグループ単位ではない（volareinc/carmo-cdk#363）~~ →
  サーバのポリシーはグループ単位に切り替わっている（carmo-cdk#365、2026-09-16 に実環境へ反映済み）。この端末の
  AppRole の token の `identity_policies` は `devbase-admin` / `devbase-team-{nyle,with,kkg}` /
  `devbase-team-writer-{nyle,with,kkg}`。`bao token capabilities` で、新しいパス（`team/<g>/global`・
  `users/takemi_ohama/<g>/global`）は読み書き、古いパスは `data` の読み取りと `metadata` の削除ができることを確かめた
  （古いパスを読めるのは管理者だけ。2026-09-16）
- 前提 5: with / kkg のプロジェクトの `env` にある空の上書き（`BIGQUERY_*` など）はこの作業では消さない
  （残っていても害が無い。消すかは別に決める）

## 受け入れ条件

- [x] 1. 単位 2 の後、`bin/devbase env backend status` が `レイアウト: group (version 2)` と、
      `projects/with-ai-dev` で `グループ: with (projects/with-ai-dev/env)`、`$DEVBASE_ROOT` で
      `グループ: default → nyle` を出す（終了コード 0）
- [x] 2. 単位 3 で、`projects/with-ai-dev` と `projects/project-trygroup-prd` の
      `bin/devbase env exec -- env | sort` に「with / kkg に届かなくなるキー」（下の節）の値が無い。
      ホストのシェルに同名の変数があるキーは、その変数を外して（`env -u`）確かめる（2026-09-16 に追記）。
      `projects/bi-tools`（nyle）の同じ出力は単位 0 の基準と差分 0 行（`CLAUDE_*` を除く）
- [x] 3. 単位 3 で、別経路（ホストの `bao kv get -mount=devbase`、キー数のみ）で新しいパスのキー数が
      「配る先」の表と一致する
- [x] 4. 単位 4 の後、古いパス（`team/global`、`users/takemi_ohama/global`、`team/projects/<5 件>`）を
      `bao kv metadata get` すると見つからない（版の履歴が残っていない）
- [x] 5. 記録に機密の値・`role_id` / `secret_id`・token が無い（`hvs.` / `secret_id=` / `role_id=` の grep で、この条件の行を除いて 0 件）

## 配る先

キー名だけを書く。値は書かない。

| 新しいパス | キー | 件数 | 元のパス |
| --- | --- | ---: | --- |
| `team/nyle/global` | `team/global` の 21 キーすべて + `GCP_CREDENTIALS_BASE64__default` | 22 | `team/global` / `users/takemi_ohama/global` |
| `users/takemi_ohama/nyle/global` | `users/takemi_ohama/global` の 26 キーから `GCP_CREDENTIALS_BASE64__default` を除いたもの | 25 | `users/takemi_ohama/global` |
| `users/takemi_ohama/with/global` | 会社に依らない 17 キー（下の一覧） | 17 | `users/takemi_ohama/global` |
| `users/takemi_ohama/kkg/global` | 同上 | 17 | `users/takemi_ohama/global` |
| `team/nyle/projects/bi-tools` / `car-pricing` / `carmo-screening` | `ENABLE_SSH` | 各 1 | `team/projects/<name>` |
| `team/kkg/projects/project-trygroup-prd` | `ENABLE_SSH` | 1 | `team/projects/project-trygroup-prd` |
| `team/with/projects/with-ai-dev` | `ENABLE_SSH` | 1 | `team/projects/with-ai-dev` |

`team/with/global` と `team/kkg/global` は作らない（空）。

会社に依らない 17 キー: `GIT_CREDENTIALS_BASE64` / `GIT_CREDENTIAL_HELPER` / `GIT_USER_EMAIL` / `GIT_USER_NAME` /
`GH_TOKEN` / `GITHUB_PERSONAL_ACCESS_TOKEN` / `HOST_SSH_HOST` / `HOST_SSH_USER` / `SSH_AUTHORIZED_KEYS` /
`DEVBASE_OPEN_EDITOR` / `SLACK_USER_MENTION` / `NDF_CODEX_SLACK_NOTIFY` / `CONTEXT7_API_KEY` / `OPENAI_API_KEY` /
`NPM_TOKEN` / `PYPI_API_KEY` / `PACKAGIST_API_KEY`

### with / kkg に届かなくなるキー

今は with / kkg のコンテナにも届いていて、移し直しの後は届かなくなる。**これらを with / kkg で使っている
なら、単位 2 の後に `env set --group with` などで入れ直す。**

| 由来 | キー |
| --- | --- |
| チーム共通（nyle） | `AWS_DEFAULT_REGION` / `BIGQUERY_KEY_FILE` / `DEVIN_API_ORG_WIDE` / `DEVIN_ORG_ID` / `DEVIN_SERVICE_ADMIN` / `DEVIN_SERVICE_USER` / `GOOGLE_APPLICATION_CREDENTIALS` / `GOOGLE_APPLICATION_CREDENTIALS_BASE64` / `GOOGLE_CLOUD_LOCATION` / `REDASH_DEV_URL` / `REDASH_URL` / `SLACK_ADMIN_CLIENT_ID` / `SLACK_ADMIN_CLIENT_SECRET` / `SLACK_ADMIN_REDIRECT_URI` / `SLACK_BOT_TOKEN` / `SLACK_CHANNEL_ID` / `SLACK_TEAM_ID`（`BIGQUERY_DATASET` / `BIGQUERY_LOCATION` / `BIGQUERY_PROJECT` / `GOOGLE_CLOUD_PROJECT` は今もプロジェクトの `env` で空にしている） |
| 個人単位（会社固有） | `AWS_CONFIG_BASE64`（with は今も空にしている）/ `AWS_PROFILE` / `GCP_ACTIVE_PROFILE`（どちらも with / kkg はプロジェクトの `env` で値を持つ）/ `GCP_CREDENTIALS_BASE64__default` / `GOOGLE_GENAI_USE_VERTEXAI`（今も空にしている）/ `DEVIN_API_KEY` / `REDASH_API_KEY` / `REDASH_DEV_API_KEY` / `REDMINE_API_KEY` |

## 設計（`operation` は独立した設計文書を作らず、この節に書く）

### 構成を選んだ理由

- **新しいパスへ書く（単位 1）を、設定の切り替え（単位 2）より先に置く。** 切り替えた時点で `devbase up` は
  新しいパスを読む。先に切り替えると、空の置き場でコンテナが起動しうる。単位 1 の間、この端末は
  `version: 1` のまま古いパスを読み続けるので、途中で `devbase up` を打っても今と同じ値で起動する
- **書き込みは 1 つのスクリプトで、値を画面に出さずに行う。** `env set KEY=VALUE` は値が引数に載り、
  `env edit --group` は 1 キーずつ手で写すことになる（打ち間違いが残る）。スクリプトは
  `SecretStore` に `version: 2` の設定を明示して渡し（`backend.yml` は書き換えない）、古いパスから
  `fetch` → 新しいパスへ CAS 付きで `save` → 読み戻して一致を確かめる。出すのはパスとキー数だけ
- **古いパスの削除（単位 4）を最後に置き、確認（単位 3）を挟む。** 削除は版の履歴ごと消すので戻せない。
  確認の前に消すと、戻す手段が無くなる
- **削除は `metadata` ごと行う。** PLAN53 の単位 5 の後始末で、`destroy`（版だけ消す）はポリシーに無く
  `metadata` の削除は通ると確かめている
- 移し直し専用のコマンドは devbase に足さない（PLAN56 決定 9）

### 非機能設計表

| 大項目 | 要求の条件 | 実現方式 | 確かめ方 |
| --- | --- | --- | --- |
| 可用性 | 移し直しの途中でも `devbase up` が今と同じ値で起動できる | 単位 1 は新しいパスへ書くだけで、古いパスと `backend.yml` を変えない。単位 2 は設定 1 ファイルの書き換え。キャッシュは `use` がレイアウトの変更で消し、次の取得で作り直す | 単位 1 の後に `projects/bi-tools` で `env exec -- env` が基準と差分 0 行。単位 3 で 3 プロジェクトの `env exec` が 0 で終わる |
| 移行性 | 単位ごとに戻す手順がある。戻せない単位（4）は実行の前に示す | 下の計画の「取り消し」の列 | 単位 4 の前に、単位 3 の受け入れ条件 2・3 を満たしている |
| セキュリティ | 他グループの機密を古いパスの版の履歴にも残さない。記録に値を書かない | 単位 4 で古いパスを `metadata` ごと消す。スクリプトはキー名と件数だけを出す。基準の `env` 出力は `backups/plan57/`（`backups/.gitignore` で除外、`0600`）に置き、確認後に消す | 受け入れ条件 4・5 |
| システム環境 | 実行する場所と届く先 | ホスト（macOS、`~/devbase` の `main` @ 7fe3ad9）。届く先は OpenBao（KV v2 `devbase/`）と `~/devbase/secrets/`。`bao` はホストの CLI | 各単位の記録に「対象の系」を書く |

## 計画（実行の単位）

| 単位 | 操作（実行するコマンド） | 対象の系 | 区分 | 取り消し |
| --- | --- | --- | --- | --- |
| 0 | 基準を採る: `projects/with-ai-dev`・`projects/project-trygroup-prd`・`projects/bi-tools` でそれぞれ `bin/devbase env exec -- env \| sort > backups/plan57/before-<name>.env`（`0600`） | 手元（読むだけ） | — | 不要 |
| 1 | 移し直しのスクリプト（`backups/plan57/relocate.py`、記録には処理の中身を書く）を実行: 「配る先」の 9 パスへ書き、読み戻して一致を確認。古いパスと `backend.yml` は変えない | OpenBao（新しい 9 パス） | **本番** | 新しい 9 パスを `metadata` ごと消す（まだ誰も読んでいないので影響なし） |
| 2 | `bin/devbase env backend use openbao --layout group --group-alias default=nyle` → `bin/devbase env backend status`（`$DEVBASE_ROOT` と `projects/with-ai-dev`）→ `bin/devbase env backend test` | 手元（`secrets/backend.yml`、`secrets/cache/`） | **本番**（利用者の実環境） | `bin/devbase env backend use openbao --layout flat`（古いパスが残っているので即時に元へ戻る） |
| 3 | 確認: 単位 0 の 3 プロジェクトで `env exec -- env \| sort` を採り直して基準と比べる。ホストの `bao kv get -mount=devbase <新しいパス>` でキー数を別経路で確かめる | 手元（読むだけ）/ OpenBao（読むだけ） | — | 不要 |
| 4 | 古いパス 7 件（`team/global`、`users/takemi_ohama/global`、`team/projects/{bi-tools,car-pricing,carmo-screening,project-trygroup-prd,with-ai-dev}`）を `metadata` ごと消す → `bao kv metadata get` で見つからないことを確かめる → `backups/plan57/before-*.env` を消す | OpenBao（古い 7 パス）、手元 | **本番** | **戻せない**（全版の履歴を消す）。消した後に値が要るときは、新しいパスから読める（`team/nyle/global` などに同じ値がある）。`version: 1` へ戻すには、新しいパスの値を古いパスへ書き戻す必要がある |

- **失敗した単位より後は実行しない**（`operation-run.md` の 6）
- **取り消せない単位: 4。** 実行の前にそのことを示し、承認を得る
- 使うのはこの端末の AppRole（`secrets/bootstrap.env.age`）。値は記録に書かない
- 単位 1 のスクリプトは記録に処理の中身を書き、ファイルそのものは `backups/plan57/` に置いて単位 4 で消す
  （キー名の一覧を持つだけで値は持たないが、リポジトリの差分にはしない）

## 実行の記録

### 単位 0: 基準を採る（2026-09-16 02:13）

対象の系: 手元（読むだけ）  区分: —
取り消し: 不要

`projects/*` はシンボリックリンクのため、出力先を相対パスで書くと外れた（1 回目は 3 件とも `No such file or directory`、
終了コード 127、何も書かれていない）。絶対パスで採り直した。

$ B=~/devbase/backups/plan57; umask 077
$ for p in with-ai-dev project-trygroup-prd bi-tools; do (cd projects/$p && ~/devbase/bin/devbase env exec -- env | sort > $B/before-$p.env); done
with-ai-dev exit=0 lines=106 / project-trygroup-prd exit=0 lines=106 / bi-tools exit=0 lines=104
終了コード: 0（3 件とも）

### 実行の前の確認: サーバのポリシー（2026-09-16 03:0x）

carmo-cdk#365（グループ単位のポリシー）が実環境へ反映されたと利用者から連絡を受け、計画の前提 4 を確かめ直した。

$ T=$(bin/devbase env token --print); BAO_TOKEN=$T bao token lookup -format=json | jq .data.identity_policies
["devbase-admin","devbase-team-kkg","devbase-team-nyle","devbase-team-with","devbase-team-writer-kkg","devbase-team-writer-nyle","devbase-team-writer-with"]
$ BAO_TOKEN=$T bao token capabilities devbase/<パス>
data/team/global: read / data/team/{nyle,with,kkg}/global: create, delete, patch, read, update /
data/users/takemi_ohama/nyle/global: create, delete, patch, read, update / data/team/projects/with-ai-dev: read /
metadata/team/global・metadata/team/projects/with-ai-dev・metadata/users/takemi_ohama/global: delete, list, read
終了コード: 0

（token の値は変数に置いただけで表示していない）

### 単位 1: 新しい 9 パスへ書く（2026-09-16 03:30）

対象の系: OpenBao（新しい 9 パス）  区分: 本番
取り消し: 戻せる（新しい 9 パスを `metadata` ごと消す）

スクリプト（`backups/plan57/relocate.py`、`0600`。値を出さない）の処理:

1. 今の `backend.yml`（`version: 1`）の `SecretStore` と、同じ接続先で `version: 2`（`layout: group`、
   `default → nyle`）の設定を明示した `SecretStore` を作る（`backend.yml` は書き換えない）
2. 古いパスを `fetch`: `team/global` が 21 キー、`users/takemi_ohama/global` が 26 キーであること、
   `GCP_CREDENTIALS_BASE64__default` が個人単位にあること、会社に依らない 17 キーがそろうこと、
   `team/projects/<5 件>` が `ENABLE_SSH` だけであることを assert
3. 新しいパスが空であることを assert してから、「配る先」の表どおりに `save`（読んだ版を基準にした CAS）
4. 控えを持たない別の `SecretStore` で読み戻し、書いた内容と一致することを assert

$ DEVBASE_ROOT=$PWD PYTHONPATH=lib uv run python backups/plan57/relocate.py
devbase/team/nyle/global: 22 keys (読み戻し一致)
devbase/users/takemi_ohama/nyle/global: 25 keys (読み戻し一致)
devbase/users/takemi_ohama/with/global: 17 keys (読み戻し一致)
devbase/users/takemi_ohama/kkg/global: 17 keys (読み戻し一致)
devbase/team/nyle/projects/bi-tools: 1 keys (読み戻し一致)
devbase/team/nyle/projects/car-pricing: 1 keys (読み戻し一致)
devbase/team/nyle/projects/carmo-screening: 1 keys (読み戻し一致)
devbase/team/kkg/projects/project-trygroup-prd: 1 keys (読み戻し一致)
devbase/team/with/projects/with-ai-dev: 1 keys (読み戻し一致)
終了コード: 0

反映の確認: 単位 3 の `bao kv get`（別経路）

### 単位 2: この端末を `version: 2` へ切り替える（2026-09-16 03:31）

対象の系: 手元（`secrets/backend.yml`、`secrets/cache/`）  区分: 本番（利用者の実環境）
取り消し: 戻せる（`bin/devbase env backend use openbao --layout flat`。古いパスは単位 4 まで残る）

$ bin/devbase env backend use openbao --layout group --group-alias default=nyle
backend を openbao に設定しました: ~/devbase/secrets/backend.yml
  レイアウト: group (version 2)
  グループの読み替え: default → nyle
  キャッシュ (~/devbase/secrets/cache) を消しました (レイアウトが flat から group へ変わったため)
終了コード: 0

$ bin/devbase env backend status
  レイアウト: group (version 2)
  グループ:   default → nyle ($DEVBASE_ROOT/env に宣言なし)
  置き場: devbase/team/nyle/global / devbase/team/nyle/projects/<name> / devbase/users/takemi_ohama/nyle/global / devbase/users/takemi_ohama/nyle/projects/<name>
  接続資格情報: role_id [伏せた] (~/devbase/secrets/bootstrap.env.age)
終了コード: 0

$ cd projects/with-ai-dev && bin/devbase env backend status
  グループ:   with (projects/with-ai-dev/env)
  置き場: devbase/team/with/global / devbase/team/with/projects/with-ai-dev / devbase/users/takemi_ohama/with/global / devbase/users/takemi_ohama/with/projects/with-ai-dev
終了コード: 0

$ bin/devbase env backend test
対象のグループと違う置き場のプロジェクトは調べていません: project-trygroup-prd (kkg), with-ai-dev (with)
読めた参照: 74 件（0 変数でないもの: team/nyle/global 22 / users/takemi_ohama/nyle/global 25 / team/nyle/projects/{bi-tools,car-pricing,carmo-screening} 各 1）
終了コード: 0

受け入れ条件 1: 満たす

### 単位 3: 確かめる（2026-09-16 03:31）

対象の系: 手元（読むだけ）/ OpenBao（読むだけ）  区分: —
取り消し: 不要

$ for p in with-ai-dev project-trygroup-prd bi-tools; do (cd projects/$p && bin/devbase env exec -- env | sort > $B/after-$p.env); done
with-ai-dev exit=0 lines=85 / project-trygroup-prd exit=0 lines=84 / bi-tools exit=0 lines=104
終了コード: 0（3 件とも）

基準との比較（変数名と、値の SHA-256 の一致だけを見た。値は出していない。`CLAUDE_*` を除く）:

| プロジェクト | 無くなった変数 | 値が変わった変数 |
| --- | --- | --- |
| with-ai-dev | `AWS_DEFAULT_REGION` / `BIGQUERY_KEY_FILE` / `DEVIN_API_KEY` / `DEVIN_API_ORG_WIDE` / `DEVIN_ORG_ID` / `DEVIN_SERVICE_ADMIN` / `DEVIN_SERVICE_USER` / `GCP_CREDENTIALS_BASE64__default` / `GOOGLE_APPLICATION_CREDENTIALS` / `GOOGLE_APPLICATION_CREDENTIALS_BASE64` / `REDASH_API_KEY` / `REDASH_DEV_API_KEY` / `REDASH_DEV_URL` / `REDASH_URL` / `REDMINE_API_KEY` / `SLACK_ADMIN_CLIENT_ID` / `SLACK_ADMIN_CLIENT_SECRET` / `SLACK_ADMIN_REDIRECT_URI` / `SLACK_BOT_TOKEN` / `SLACK_CHANNEL_ID` / `SLACK_TEAM_ID`（21 件） | なし |
| project-trygroup-prd | 上の 21 件 + `AWS_CONFIG_BASE64`（22 件） | なし |
| bi-tools（nyle） | なし（基準との差分 0 行） | なし |

- 無くなった変数は「with / kkg に届かなくなるキー」の表のとおり。`AWS_CONFIG_BASE64` は with-ai-dev では前から
  プロジェクトの `env` で空にしており、名前は残る
- `GOOGLE_CLOUD_LOCATION` は with / kkg の出力にも値が残った。ホストのシェルの環境変数にあり、`env exec` がそれを
  引き継いだもので、`$DEVBASE_ROOT/env` とプロジェクトの `env` には無い。~~機密の置き場から来たものではない~~ →
  この比較ではホストの値に隠れて、置き場から来なくなったことを確かめられていなかった（2026-09-16、PR #187 の
  レビュー）。ホストの同名の変数を外して確かめ直した:

  （`env exec` の終了コードと、値の有無を判定する `grep -q` の終了コードを分けて採った。`grep -q` は値があれば 0、
  無ければ 1。2026-09-16 05:47 に採り直した）

  $ for p in with-ai-dev project-trygroup-prd bi-tools; do (cd projects/$p && out=$(env -u GOOGLE_CLOUD_LOCATION bin/devbase env exec -- env); rc_exec=$?; printf '%s\n' "$out" | grep -q '^GOOGLE_CLOUD_LOCATION=.'; echo "$p: env exec exit=$rc_exec / grep exit=$?"); done
  with-ai-dev: env exec exit=0 / grep exit=1（値なし）
  project-trygroup-prd: env exec exit=0 / grep exit=1（値なし）
  bi-tools: env exec exit=0 / grep exit=0（値あり。nyle の対照）
  $ bao kv get -mount=devbase -format=json team/nyle/global | jq '.data.data | has("GOOGLE_CLOUD_LOCATION")'
  true（exit=0）

  with / kkg へは置き場から届かず、nyle の置き場には残っている

別経路（ホストの `bao kv get -mount=devbase -format=json <パス> | jq '.data.data|length'`）:

| パス | キー数 | 終了コード |
| --- | ---: | ---: |
| team/nyle/global | 22 | 0 |
| users/takemi_ohama/nyle/global | 25 | 0 |
| users/takemi_ohama/with/global | 17 | 0 |
| users/takemi_ohama/kkg/global | 17 | 0 |
| team/nyle/projects/bi-tools | 1 | 0 |
| team/nyle/projects/car-pricing | 1 | 0 |
| team/nyle/projects/carmo-screening | 1 | 0 |
| team/kkg/projects/project-trygroup-prd | 1 | 0 |
| team/with/projects/with-ai-dev | 1 | 0 |
| team/with/global / team/kkg/global | 無し（作っていない） | — |

- `secrets/cache/` には `team/{nyle,with,kkg}/…` と `user/{nyle,with,kkg}/…` の控えだけがあり、`version: 1` の
  位置（`team/global.env.age` など）のファイルは無い

受け入れ条件 2・3: 満たす（条件 2 の `GOOGLE_CLOUD_LOCATION` はホストの同名の変数を外した再検証で判定した）

### 単位 4: 古い 7 パスを版の履歴ごと消す（2026-09-16 05:24）

対象の系: OpenBao（古い 7 パス）、手元  区分: 本番
取り消し: **戻せない**（利用者が 2026-09-16 に、戻せないことを示したうえで承認した）

$ T=$(bin/devbase env token --print)
$ for p in team/global users/takemi_ohama/global team/projects/{bi-tools,car-pricing,carmo-screening,project-trygroup-prd,with-ai-dev}; do BAO_TOKEN=$T bao kv metadata delete -mount=devbase $p; done
Success! Data deleted (if it existed) at: devbase/metadata/team/global
Success! Data deleted (if it existed) at: devbase/metadata/users/takemi_ohama/global
Success! Data deleted (if it existed) at: devbase/metadata/team/projects/bi-tools
Success! Data deleted (if it existed) at: devbase/metadata/team/projects/car-pricing
Success! Data deleted (if it existed) at: devbase/metadata/team/projects/carmo-screening
Success! Data deleted (if it existed) at: devbase/metadata/team/projects/project-trygroup-prd
Success! Data deleted (if it existed) at: devbase/metadata/team/projects/with-ai-dev
終了コード: 0（7 件とも）

反映の確認:

$ for p in <同じ 7 パス>; do BAO_TOKEN=$T bao kv metadata get -mount=devbase $p; done
終了コード: 2（7 件とも）
$ BAO_TOKEN=$T bao kv metadata get -mount=devbase team/global
No value found at devbase/metadata/team/global
（終了コード 2 は権限の不足ではなく「見つからない」。対照として `team/nyle/global` の metadata は `current_version 1` で読める）

$ for p in with-ai-dev project-trygroup-prd bi-tools; do (cd projects/$p && bin/devbase env exec -- env | sort > $B/final-$p.env); done
3 件とも終了コード 0。単位 3 の出力との差分 0 行（`CLAUDE_*` を除く）

$ rm -r backups/plan57
終了コード: 0

受け入れ条件 4: 満たす

### 残したもの

- `backups/env-backend-migrate/20260915-165942/`（PLAN53 の退避。切り替え前のチーム単位の機密の age）。利用者の判断で残す
- `backups/plan53/`（PLAN53 の単位 0 の基準）

### 次に変えること（2026-09-16）

- with / kkg のプロジェクトの `env` にある空の上書き（`BIGQUERY_*` / `GOOGLE_CLOUD_PROJECT` / `GOOGLE_GENAI_USE_VERTEXAI` /
  with-ai-dev の `AWS_CONFIG_BASE64`）は、打ち消す相手が置き場から無くなったので不要になった。消すかは利用者が決める
  （前提 5。projects/* は plugin リポジトリ側にある）
- with / kkg で「届かなくなるキー」を使っていたら、`bin/devbase env set --group with KEY` などで入れ直す
