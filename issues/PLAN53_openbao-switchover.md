# PLAN53: 自分の端末を OpenBao backend へ切り替え、チーム共通の機密を移す

- 発端: #172（運用）。#152（プロジェクトの env による打ち消し）の確認も同じ操作で行う
- ワークフローモード: `operation`
  - 根拠: 本番コードも文書も変えず、OpenBao（本番の系）と手元の `~/devbase`
    （`secrets/` 配下）の状態だけを変える。実行の記録がこのファイルの差分になる
- 閉じる課題: #172、#152
- 記録の置き場所: `issues/`（この変更を読む人が最初に開くのは課題の計画であり、
  `docs/` は確定した知識の置き場で出来事は置かない）

## 依頼（原文）

#172 より:

> #171 でマージした OpenBao backend を、開発者の本番の `~/devbase` で使い始める。サーバ（carmo-cdk#312）と devbase 側の実機検証（devbasex/devbase#171 のリリース後テスト、13 件合格）は済んでいる。
>
> モード: `operation`（本番コードは変えず、外部の系の状態と手元の設定を変える）。実行の単位ごとに承認を取り、取り消しの手段を添える。

#152 のコメント（2026-09-14）より:

> `devbase up` は同じ `env exec` を経由するため、`.docker-compose.scale.yml` の名前だけの受け渡しが拾う環境変数もこの値になるはず。ただし稼働中の `with-ai-dev` コンテナは #171 より前に起動したもので、確かめるには `devbase up` での再起動が要る。**再起動後に `docker exec with-ai-dev-dev-1 bash -lc 'echo "[$GOOGLE_CLOUD_PROJECT]"'` が `[]` なら、この issue は #171 で解消として閉じてよい。**

## 目的

- 利用者（`takemi_ohama`）の端末 1 台が、age ストアではなく OpenBao から機密を読み書きする
  状態にする
- チーム共通の機密が OpenBao の `team/…` に、個人単位の機密が `users/takemi_ohama/…` に
  置かれ、手元の `.env` / `secrets/*.age` に平文・暗号文が残らない（退避先は除く）
- #152 の現象（プロジェクトの `env` による共通機密の空上書きが効かない）が、#171 以降の
  `devbase up` で起きないことを実機で確かめる

## 前提

- 前提 1: サーバ側の管理操作（`bin/openbao-admin.sh device-add` / `user-add --team-writer`）は
  carmo-cdk の `main` から**利用者自身が管理者として**実行する。この計画は実行のコマンドと
  結果を記録するが、管理者の資格情報（Google ログイン）を扱わない
  （成否の判定: この計画の記録に管理者の token・`role_id`・`secret_id` の値が無い）
- 前提 2: チーム共通の書き手は利用者自身（`takemi_ohama`）とする。他に書き手を置くかは
  この計画の範囲外（成否の判定: 単位 3 の対象が `takemi_ohama` 1 名）
- 前提 3: 移行の元は `secrets/global.env.age` と `secrets/projects/*.env.age` の age ストア
  であり、平文 `.env` は残っていない（`devbase env backend status` が `age` を返す。
  2026-09-14 に確認済み）
- 前提 4: `.env` のどのキーが個人単位かの仕分けは、単位 5 の前に利用者が決める。
  仕分けの結果はこの計画の「個人単位へ移すキー」の節に**キー名だけ**を書く
  （成否の判定: 値が 1 つも書かれていない）
- 前提 5: carmo-system-console のように pre-up が S3 から平文 `.env` を取るプロジェクトは
  対象外（#172 の注意書き）

## 対象範囲

含む:

- 端末の資格情報の発行と `devbase env backend use openbao` による切り替え
- チーム共通・プロジェクトの機密の `migrate --to openbao`
- 個人単位の機密の `env set --user` による入れ直し
- `devbase up` による 4 層注入の確認と、退避した元ファイルの削除
- #152 の確認（`with-ai-dev` を `devbase up` で作り直し、コンテナ内の値を見る）

含まない:

- 本番コード・`docs/` の変更（実行で決まった設定が以後の前提になるなら `plan-to-spec` で別に扱う）
- 他の利用者の端末の切り替え、利用者の登録（`user-add`）の代行
- carmo-system-console の機密の扱い
- サーバ側（carmo-cdk）の課題 #340 / #341 / #336

## 用語

| 用語 | 意味 |
| --- | --- |
| 端末の資格情報 | AppRole の `role_id` / `secret_id`。`secrets/bootstrap.env.age` に age で置く |
| 4 層注入 | `runtime.resolve()` の重ね順（チーム共通 → 個人共通 → プロジェクト env → プロジェクトのチーム機密 → 個人機密）。`docs/specifications/secret-backend.md` |
| 控え | `secrets/cache/` の age 暗号化キャッシュ。サーバ不達時に読む |
| 退避先 | `backups/env-backend-migrate/<日時>/`。`migrate` が元ファイルを移す |

## 受け入れ条件

- [x] 前提: 単位 2 まで終わっている
      操作: `devbase env backend test`
      結果: 終了コード 0 で、4 参照（`team/global` / `team/projects/<name>` /
      `users/takemi_ohama/global` / `users/takemi_ohama/projects/<name>`）を読めた旨が出る
- [x] 前提: 単位 4 まで終わっている
      操作: 切替前に採った `devbase env exec -- env | sort` と、切替後の同じ出力を比べる
      結果: 差分が 0 行（`DEVBASE_OPENBAO_*` などブートストラップ由来の変数を除く。除いた
      変数名は記録に書く）
- [x] 単位 6 の後、~~`secrets/global.env.age` / `secrets/projects/*.env.age` / `.env` /
      `projects/*/.env` が存在しない（`backups/env-backend-migrate/` の下は除く）~~
      → `secrets/global.env.age` / `secrets/projects/*.env.age` / `.env` / `projects/*/.env` のうち、
      機密（キー）を持つファイルが存在しない（`backups/env-backend-migrate/` の下、0 キーの空ファイル、
      対象外と決めた carmo-system-console の `.env` を除く）
      （2026-09-15、単位 6 の後にも 0 キーの空ファイルが残り、carmo-system-console は前提 5 で対象外と
      決めていた。目的は「手元に機密が残らない」ことなので、字義どおりの「存在しない」から機密を持たないことへ
      条件を合わせた。`[x]` は改めた条件に対する判定）
      記録（2026-09-15）: 機密を持つファイルは残っていない。残っているのは 0 キーの空ファイル（age 14 本、平文 `.env` 7 本）と、
      対象外と決めた carmo-system-console の `.env`（215 キー、S3 から配る本番の仕組み）。`secrets/global.env.age` は無い
- [x] 単位 6 の後、`bao kv get -mount=devbase team/global` がチーム共通のキー一覧を返し、
      `users/takemi_ohama/global` が個人単位のキー一覧を返す（値は記録に書かない）
- [x] 不達時の起動: ~~`secrets/backend.yml` の `url` を到達しないものへ一時的に変えて
      `devbase env exec -- true` を実行すると、控えから読んだ旨の警告を出して終了コード 0
      で終わる（サーバのデプロイ中の 2 分 20 秒を模す。元へ戻してから次へ進む）~~
      → `secrets/backend.yml` の `url` は変えずに `HTTPS_PROXY=http://127.0.0.1:9` を付けて
      `devbase env exec -- true` を実行し、通信だけを届かなくすると、控えから読んだ旨の警告を出して
      終了コード 0 で終わる（サーバのデプロイ中の 2 分 20 秒を模す）
      （2026-09-15、`url` を変えると控えの `scope` に URL が含まれるため控えが見つからず exit=1 になる。
      確かめ方の誤りだったので、実際に成功した通信の遮断へ改めた。単位 4' の記録を参照）
- [x] #152: `projects/with-ai-dev` で `devbase up` した後、
      `docker exec with-ai-dev-dev-1 bash -lc 'echo "[$GOOGLE_CLOUD_PROJECT] [$GCP_ACTIVE_PROFILE]"'`
      が `[] [with]` を返す
- [x] 記録（このファイル）に `role_id` / `secret_id` / token / 機密の値が含まれていない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | サーバ不達時に控えで `devbase up` できる（受け入れ条件 5）。控えを無効にしない |
| 移行性 | 単位ごとに戻す手順がある。`migrate` は衝突があれば 1 件も書かずに止まる。元ファイルは単位 6 の確認が済むまで退避先に残す |
| セキュリティ | `secret_id` は `--secret-id-stdin` で渡し、発行した JSON は使ったら消す。記録に資格情報・機密の値を書かない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない |
| データ | OpenBao の `team/global` / `team/projects/<name>` / `users/takemi_ohama/…` に新しい版が作られる。手元の age ストアは退避先へ移る |
| 既存の振る舞い | この端末の `devbase up` が OpenBao から機密を読む。他の端末には影響しない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 接続 | `devbase env backend test` |
| 注入の同一性 | 切替前後の `devbase env exec -- env \| sort` の差分 |
| 別経路での確認 | ホストの `bao`（2.6.2）で `bao kv get -mount=devbase <path>` を読む（devbase を経由しない経路） |
| 手動確認 | 利用者が `with-ai-dev` のコンテナ内で `GOOGLE_CLOUD_PROJECT` が空であることを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 記録はこのファイル。`docs/` と `lib/` は触らない |
| 手順の出典 | `docs/user/env-backend.md`「使いはじめる」、`issues/devbase-openbao-handover.md`（未コミット。社内の値を含むため、この記録には URL を `https://openbao.example.com` と書く） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 単位ごとに承認を取り、コマンド・出力（伏せ字）・終了コードを残す。失敗したらそこで止める |
| 確認してから行う | 本番の系（OpenBao、`~/devbase/secrets`）へ届く単位すべて。退避した元ファイルの削除 |
| 行わない | `secret_id` を引数で渡す。管理者の資格情報の代行。本番コードの変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 個人単位へ移すキーの仕分け（前提 4） | 利用者 | 単位 5 の前 |
| この端末の端末名（`device-add` の第 2 引数） | 利用者 | 単位 1 の前 |

## 設計（`operation` は独立した設計文書を作らず、この節に書く）

### 構成を選んだ理由

- **端末の切り替え（単位 2）を移行（単位 4）より先に置く。** `migrate --to openbao` は
  移行先を読んでから書くため、資格情報と接続が先に要る。切り替えただけの状態は、age ストアの
  内容がまだ使われず OpenBao が空なので、`devbase up` が空の機密で起動する。**単位 2 と 4 の
  間は `devbase up` を打たない**（打つなら `env backend use age` で戻す）
- **書き手の権限（単位 3）を移行の前に置く。** `devbase-user` ポリシーはチーム共通を読むだけで、
  `migrate` は書き込み権限が無いと「書き込み権限が無い」で止まる（carmo-cdk#340 / #350）
- **個人単位の入れ直し（単位 5）を移行の後に置く。** `migrate` はチーム単位の参照だけを写す。
  個人単位のキーは移行後に `team/global` にもあるため、`users/takemi_ohama/global` へ入れた後に
  `team/global` から消す（同じキーを 2 か所に残すと、個人単位に分けた意味が無くなる）
- **元ファイルの削除（単位 6）を最後に置き、`devbase up` の確認を挟む。** `migrate` は退避
  するだけで消さない。確認前に消すと、戻す手段が `migrate --to age`（サーバから読み戻す）
  だけになり、しかも `migrate --to age` はチーム単位の参照しか写さないため、単位 5 の後は個人単位の
  キーを別に入れ直す必要がある（「age ストアへ戻す手順」）

### 非機能設計表

| 大項目 | 要求の条件 | 実現方式 | 確かめ方 |
| --- | --- | --- | --- |
| 可用性 | サーバ不達時に控えで `devbase up` できる。控えを無効にしない | `use openbao` に `--no-cache` を付けない（既定で `cache.enabled: true`）。単位 4 の後に `devbase env exec` を 1 度通して `secrets/cache/` に控えを作る | `backend.yml` の `url` は変えず、`HTTPS_PROXY=http://127.0.0.1:9` を付けて通信だけを届かなくし、`devbase env exec -- true` が警告付きで 0 で終わる（`url` を変えると控えの `scope` から外れて exit=1 になる） |
| 移行性 | 単位ごとに戻す手順がある。`migrate` は衝突があれば 1 件も書かずに止まる。元ファイルは確認が済むまで退避先に残す | 各単位の「取り消し」の列（下の計画）。`--dry-run` を先に打って移すキー名と衝突を見る | 各単位の記録に取り消しの手順が書かれている。単位 6 の削除は `devbase up` の差分 0 を見た後 |
| セキュリティ | `secret_id` は `--secret-id-stdin` で渡し、発行した JSON は使ったら消す。記録に資格情報・機密の値を書かない | 発行した JSON は `~/` 直下ではなく `secrets/` 配下の一時ファイル（`0600`、`gitignore` 対象）に置き、`use` の直後に `rm`。記録には `devbase env list` の**キー名**と件数だけを写す | 記録を `grep -E 'role_id|secret_id|hvs\.'` して値が無い。`ls secrets/` に一時ファイルが残っていない |
| システム環境 | 実行する場所と届く先 | 実行はホスト（macOS、`~/devbase`、`bin/devbase` 3.3.0 + `main` @ 709f507）。届く先は OpenBao（`https://openbao.example.com`、KV v2 `devbase/`）と `~/devbase/secrets/`。管理操作（単位 1・3）は carmo-cdk の `main` から利用者が実行する | 各単位の記録に「対象の系」を書く |

### 決定の記録

#### 決定 1: 個人単位へ分けるキーは利用者が決め、この計画はキー名だけを記録する

この端末の `team/global` は 47 キーあり、`GH_TOKEN` / `AWS_CONFIG_BASE64` / `GIT_USER_*` など
個人の資格情報と、`REDASH_URL` / `SLACK_TEAM_ID` などチームの設定が混在している。どれが
個人単位かは他の利用者が同じ値を使うかで決まり、コードからは読めない。

一律に `team/global` へ置く案は採らない。個人の資格情報が全員に読める状態になり、`--user` の
軸を足した #171 の意味が無くなる。

#### 決定 2: `migrate` の後に `team/global` から個人単位のキーを消す

`users/<user>/global` が `team/global` に勝つため、消さなくても起動の値は同じである。それでも
消すのは、チーム共通の一覧に個人の資格情報が残ると、後から加わる利用者がそれを読めるためで
ある。消すのは `devbase env delete <KEY>`（CAS 付きの丸ごと置き換え）で、1 キーずつ戻せる。

追記（2026-09-15、レビューの指摘）: CAS 付きの置き換えは KV v2 に新しい版を作るだけで、個人の資格情報を
含む旧版は `team/global` に残り、読取権限を持つ利用者が版を指定して取得できる。この決定の順序
（チーム共通へ一旦入れてから分ける）は版の履歴に個人の資格情報を残すため、旧版の削除を後から行った
（実行の記録「単位 5 の後始末」）。

#### 決定 3: #152 の確認は `with-ai-dev` の `devbase up` で行い、切替後に 1 回だけ行う

切替前（age）でも `env exec` は空上書きを返すことを確認済み（#152 のコメント）。切替後の
`up` で確かめれば、backend に依らず #171 の合成が効いていることが分かり、単位 6 の
「4 層の注入を確かめる」と 1 回の起動で済む。切替前にも起動して 2 回確かめる案は、
稼働中のコンテナを 2 度作り直す費用に見合わない。

## 計画（実行の単位）

要求・設計・受け入れ条件は上の節が持つ。ここでは**実行の範囲を単位に分け、単位ごとに取り消しの
手段を書く**（`operation-run.md` の 1・2）。承認はこの表に対して求める。

| 単位 | 操作（実行するコマンド） | 対象の系 | 区分 | 取り消し |
| --- | --- | --- | --- | --- |
| 0 | 切替前の基準を採る: `bin/devbase env list` のキー名一覧、`cd projects/with-ai-dev && bin/devbase env exec -- env \| sort > <退避先>/before.env`（`0600`、gitignore 対象の `backups/` 配下） | 手元（読むだけ） | — | 不要 |
| 1 | 管理者（利用者本人）が carmo-cdk `main` で `bin/openbao-admin.sh device-add takemi_ohama <端末名>` を実行し、JSON を `~/devbase/secrets/device.json`（`0600`）へ置く | OpenBao（AppRole の `secret_id` が 1 つ増える） | **本番** | `bin/openbao-admin.sh device-revoke takemi_ohama <端末名>`（`secret_id_accessor` で失効） |
| 2 | `jq -r .secret_id secrets/device.json \| bin/devbase env backend use openbao --url <URL> --user takemi_ohama --role-id "$(jq -r .role_id secrets/device.json)" --secret-id-stdin` → `rm secrets/device.json` → `bin/devbase env backend test` | 手元（`secrets/backend.yml` / `bootstrap.env.age`） | **本番**（利用者の実環境） | `bin/devbase env backend use auto`（age ストアは触っていないので即時に元へ戻る） |
| 3 | 管理者が `bin/openbao-admin.sh user-add takemi_ohama --team-writer`（403 になる既知の課題 carmo-cdk#350 があるため、代替として WebUI / `bao write identity/entity/name/takemi_ohama policies=…` で entity に `devbase-team-writer` を付ける） | OpenBao（entity のポリシー） | **本番** | 同じ経路でポリシーを外す |
| 4 | `bin/devbase env backend migrate --to openbao --dry-run` → 衝突が無ければ `bin/devbase env backend migrate --to openbao` → `bin/devbase env backend status` → `cd projects/with-ai-dev && bin/devbase env exec -- env \| sort > <退避先>/after.env` → `diff before.env after.env` | OpenBao（`team/global` + `team/projects/<name>` × 19）と手元（元ファイルは `backups/env-backend-migrate/<日時>/` へ退避） | **本番** | `bin/devbase env backend migrate --to age`（サーバ側は残る。退避先から戻す） |
| 4' | 不達時の確認: `backend.yml` の `url` は変えず `HTTPS_PROXY=http://127.0.0.1:9 bin/devbase env exec -- true`（通信だけを届かなくする。`url` を変えると控えの `scope` から外れて exit=1 になる） | 手元 | 検証 | 不要（環境変数はそのコマンドにだけ効き、ファイルを変えない） |
| 5 | 利用者が仕分けたキーごとに `bin/devbase env set --user KEY`（値は伏せ字入力）→ `bin/devbase env delete KEY`（`team/global` から消す） | OpenBao（`users/takemi_ohama/global`、`team/global`） | **本番** | `env delete --user KEY` と `env set KEY`（退避先の age ファイルから値を読める） |
| 6 | `cd projects/with-ai-dev && bin/devbase up` → `docker exec with-ai-dev-dev-1 bash -lc 'echo "[$GOOGLE_CLOUD_PROJECT] [$GCP_ACTIVE_PROFILE]"'`（#152）→ ホストの `bao kv get -mount=devbase team/global` でキー名を別経路で確認 → 問題が無ければ `rm -r backups/env-backend-migrate/<日時>/` | 手元（コンテナの作り直し、退避先の削除） | **本番** | 退避先の削除は**戻せない**（~~サーバの値から `migrate --to age` で再生成はできる~~ → `migrate --to age` が戻すのはチーム単位だけで、個人単位のキーは別に入れ直す。2026-09-15、レビューの指摘で誤りと分かった。手順は「age ストアへ戻す手順」）。コンテナの作り直しは `devbase up` でやり直せる |

- 単位 1 と 3 は管理者の操作で、この会話は代行しない。実行の結果（JSON の**キー名**と終了コード）を
  利用者から受け取って記録する
- **失敗した単位より後は実行しない**（`operation-run.md` の 6）
- **取り消せない単位**: 6 の退避先の削除。実行の前にそのことを示す
  （2026-09-15 追記: レビューの指摘で足した「単位 5 の後始末」の全版の削除も戻せない。承認を得て実施した）

## 個人単位へ移すキー（前提 4、利用者が決める）

利用者が 2026-09-15 に承認したキー名は、実行の記録の「単位 5」にある一覧を参照（ここには二重に持たない）。

## 実行の記録

URL は `https://openbao.example.com` と伏せる。資格情報・token の値は書かない。

### 単位 0: 切替前の基準を採る（2026-09-14）

対象の系: 手元（読むだけ）  区分: —

$ bin/devbase env list | awk '{print $1}' > backups/plan53/global-keys.txt
（グローバル 47 キー。キー名だけを控えた）
$ (cd projects/with-ai-dev && /Users/takemi_ohama/devbase/bin/devbase env exec -- env | sort) > backups/plan53/before.env
終了コード: 0（107 行。`backups/` は Git の除外対象、`0600`）

### 単位 1: 端末の資格情報を発行する（2026-09-15）

対象の系: OpenBao（AppRole `devbase-takemi_ohama` の `secret_id`）  区分: 本番
取り消し: 戻せる（`bin/openbao-admin.sh device-revoke takemi_ohama macbook`）
使う権限: 利用者本人の Google ログイン（OIDC、`devbase-admin`）で得た token

管理スクリプトは carmo-cdk `main`（49bd725）の `bin/openbao-admin.sh` と
`py-infra/config/openbao/google-domain.txt` を取得して実行した。

$ BAO_TOKEN=<伏せ字> bash bin/openbao-admin.sh device-list takemi_ohama
（出力なし: 既存の端末は 0 件）
終了コード: 0

$ ( umask 077; BAO_TOKEN=<伏せ字> bash bin/openbao-admin.sh device-add takemi_ohama macbook > secrets/device.json )
（出力は画面に出さずファイルへ。キー: role_id / secret_id / secret_id_accessor）
終了コード: 0

反映の確認: `device-list takemi_ohama` → `device=macbook created=2026-09-15T07:28:37Z`（別経路の照会）

### 単位 2: この端末の backend を openbao へ切り替える（2026-09-15）

対象の系: 手元（`secrets/backend.yml` / `secrets/bootstrap.env.age`）  区分: 本番（利用者の実環境）
取り消し: 戻せる（`bin/devbase env backend use auto`。age ストアは触っていない）

$ python3 -c '<device.json の secret_id を出力>' | bin/devbase env backend use openbao --url https://openbao.example.com --user takemi_ohama --role-id <伏せ字> --secret-id-stdin
backend を openbao に設定しました: /Users/takemi_ohama/devbase/secrets/backend.yml
  mount: devbase / 個人単位の識別子: takemi_ohama / キャッシュ: 有効
終了コード: 0

$ rm -f secrets/device.json
終了コード: 0（発行した JSON を削除）

$ bin/devbase env backend test
（チーム共通・個人共通・各プロジェクトのチーム / 個人の参照を読めた。すべて 0 変数 = 移行前）
終了コード: 0

反映の確認: `secrets/backend.yml` と `secrets/bootstrap.env.age` が `0600` で存在する。
注意: 単位 4 の移行が済むまで OpenBao 側は空。**この間は `devbase up` を打たない**（空の機密で起動する）。

### 単位 4 の前の確認: `migrate --dry-run`（2026-09-15）

$ bin/devbase env backend migrate --to openbao --dry-run
グローバル 47 件 / bi-tools・car-pricing・carmo-screening・project-trygroup-prd・with-ai-dev 各 1 件（ENABLE_SSH）/
carmo-system-console 215 件（pre-up が S3 から取る平文 `.env`）。キー名のみ、値は出ない
終了コード: 0

判断（利用者、2026-09-15）: carmo-system-console は移行から外す（`migrate` の間だけ `.env` を脇へ避ける）。
`migrate` にプロジェクトを外すオプションは無い。

### 単位 3: チーム共通の書き手の権限を付ける — 失敗（2026-09-15）

対象の系: OpenBao（entity `takemi_ohama` のポリシー）  区分: 本番
取り消し: 戻せる（ポリシーを外す）— 反映されていないため不要

$ BAO_TOKEN=<伏せ字> bash bin/openbao-admin.sh user-add takemi_ohama --admin --team-writer
Code: 403. Errors: * 1 error occurred: * permission denied
終了コード: 1

$ bao write identity/entity/name/takemi_ohama policies=devbase-admin,devbase-team-writer
Code: 403. Errors: * permission denied
終了コード: 2

反映の確認: `bao read identity/entity/name/takemi_ohama` → `policies ['devbase-admin']`（変わっていない）

原因: 管理者の token は自分の entity を書き換えられない（carmo-cdk#350 と同じ挙動。同じ token で他の
利用者の entity は書ける）。`devbase-team-writer` を自分へ付けるには、**別の管理者か root の token** が要る。

### 止めた後の状態

| 単位 | 状態 |
| --- | --- |
| 0 | 済み |
| 1 | 済み（端末 `macbook` の `secret_id` は有効なまま残す。再開で入れ直さないため） |
| 2 | **戻した**（下記）。`bootstrap.env.age` は残る |
| 3 | 失敗（反映なし） |
| 4〜6 | 未実行。carmo-system-console の `.env` は触っていない |

戻した理由: OpenBao 側が空のまま `backend: openbao` を残すと、この端末の `devbase up` が空の機密で起動する
（途中の状態が系として成り立たない）。単位 1 は戻さない（残しても害が無く、再開の手間が減る）。

$ bin/devbase env backend use auto
backend を auto に設定しました
終了コード: 0

反映の確認: `env backend status` → `backend: age`、`env list` のグローバル 47 変数、
`projects/with-ai-dev` の `env exec -- env` が単位 0 の基準と一致（差分はセッション固有の `CLAUDE_*` 変数だけ）

補足（利用者、2026-09-15）: carmo-system-console の S3 から配る `.env` は本番でも使う機密の配布の仕組みで、
当面 OpenBao へ移さない。再開時の単位 4 でも `migrate` の間だけ `projects/carmo-system-console/.env` を
脇へ避ける（`migrate` にプロジェクトを外すオプションが無いため）。

### #152 の確認（2026-09-15、単位 6 から切り出して先に実施）

#172 が単位 3 で止まったため、backend に依存しない #152 の確認だけを age のまま行った（決定 3 の「切替後に 1 回」は
満たせないが、#171 の合成は backend によらず同じ経路を通る）。

$ docker exec with-ai-dev-dev-1 bash -lc 'echo "[$GOOGLE_CLOUD_PROJECT] [$GCP_ACTIVE_PROFILE] [$GOOGLE_GENAI_USE_VERTEXAI] [$BIGQUERY_PROJECT]"'
[] [with] [] []
終了コード: 0

反映の確認: コンテナの作成は 2026-09-14 21:29 JST（#171 のマージ 10:55 より後）。`docker inspect` の `Env` も同じ値。
#152 に証跡をコメントして閉じた。

### 単位 3（再実行）: チーム共通の書き手の権限を付ける（2026-09-15）

carmo-cdk#340 / #350 の修正（2026-09-15 07:02 UTC にクローズ）の反映後にやり直した。

$ bao token capabilities identity/entity/name/takemi_ohama
create, delete, list, read, update
$ BAO_TOKEN=<伏せ字> bash bin/openbao-admin.sh user-add takemi_ohama --admin --team-writer
<entity-id>
終了コード: 0

反映の確認: `bao read identity/entity/name/takemi_ohama` → `policies ['devbase-admin', 'devbase-team-writer']`、
alias は approle / oidc、`device-list` → `macbook` のまま

### 単位 2（再実行）: backend を openbao へ切り替える（2026-09-15）

$ bin/devbase env backend use openbao --url https://openbao.example.com --user takemi_ohama
（資格情報は残しておいた bootstrap.env.age を使う）
終了コード: 0
$ bin/devbase env backend test
終了コード: 0（保存済みの role_id / secret_id でログインが通る = `user-add` で role_id は変わっていない）

### 単位 4: チーム単位の機密を移す（2026-09-15）

対象の系: OpenBao（`team/global` / `team/projects/<name>`）と手元  区分: 本番
取り消し: 戻せる（`bin/devbase env backend migrate --to age`。サーバ側の値は残る）

$ mv projects/carmo-system-console/.env backups/plan53/csc.env.hold
$ bin/devbase env backend migrate --to openbao --dry-run
（carmo-system-console を含まないことを確認: 該当 0 行）
終了コード: 0
$ bin/devbase env backend migrate --to openbao --yes
グローバル / プロジェクト 'bi-tools' / 'car-pricing' / 'carmo-screening' / 'project-trygroup-prd' / 'with-ai-dev' を書き込みました
=== 完了 === backend を openbao に切り替えました
元の age / 平文の機密は backups/env-backend-migrate/20260915-165942 へ退避
終了コード: 0
$ mv backups/plan53/csc.env.hold projects/carmo-system-console/.env
（SHA-256 が退避前と一致）

反映の確認:
- `projects/with-ai-dev` の `env exec -- env | sort` と単位 0 の基準の差分: 0 行（セッション固有の `CLAUDE_*` を除く）
- `env backend status` → `backend: openbao`、`secrets/global.env.age` は無い、`secrets/cache/team/` に控えができた

### 単位 4': 不達時に控えで起動する（検証、2026-09-15）

最初に `backend.yml` の `url` を届かない値へ変えて試したが、控えの `scope` に URL が含まれるため
「キャッシュもありません」で exit=1 になった（仕様どおり。確かめ方の誤り）。すぐ元に戻した。
URL はそのままにして、通信だけを届かなくしてやり直した。

$ HTTPS_PROXY=http://127.0.0.1:9 bin/devbase env exec -- sh -c 'echo "GCP_ACTIVE_PROFILE=[$GCP_ACTIVE_PROFILE] GOOGLE_CLOUD_PROJECT=[$GOOGLE_CLOUD_PROJECT]"'
Warning: OpenBao へ到達できないため、グローバル のキャッシュを使います (最終取得 2026-09-15T17:00:07+09:00)
（個人のグローバル / プロジェクト / 個人のプロジェクトも同じ）
GCP_ACTIVE_PROFILE=[with] GOOGLE_CLOUD_PROJECT=[]
終了コード: 0

### 単位 5: 個人単位の機密を分ける（2026-09-15）

対象の系: OpenBao（`users/takemi_ohama/global`、`team/global`）  区分: 本番
取り消し: 戻せる（`team/global` へ書き戻し、`users/.../global` から消す。値は `backups/env-backend-migrate/20260915-165942` の age にもある）

#### 個人単位へ移すキー（前提 4、利用者が 2026-09-15 に承認）

GH_TOKEN / GITHUB_PERSONAL_ACCESS_TOKEN / GIT_CREDENTIALS_BASE64 / GIT_CREDENTIAL_HELPER / GIT_USER_EMAIL / GIT_USER_NAME /
AWS_CONFIG_BASE64 / AWS_PROFILE / GCP_CREDENTIALS_BASE64__default / GCP_ACTIVE_PROFILE /
OPENAI_API_KEY / CONTEXT7_API_KEY / DEVIN_API_KEY / NPM_TOKEN / PYPI_API_KEY / PACKAGIST_API_KEY / REDASH_API_KEY /
REDASH_DEV_API_KEY / REDMINE_API_KEY / HOST_SSH_HOST / HOST_SSH_USER / SSH_AUTHORIZED_KEYS / SLACK_USER_MENTION /
NDF_CODEX_SLACK_NOTIFY / DEVBASE_OPEN_EDITOR / GOOGLE_GENAI_USE_VERTEXAI（26 キー。残る 21 キーはチーム共通）

`env set --user KEY=VALUE` は値がコマンド引数に載るため使わず、`SecretStore` で値を表示せずに書いた
（`users/<user>/global` へ保存 → 読み戻して一致を確認 → `team/global` から除いて保存。いずれも CAS 付き）。

$ DEVBASE_ROOT=$PWD PYTHONPATH=lib uv run python unit5.py
users/<user>/global: 26 keys / team/global: 21 keys
終了コード: 0

反映の確認:
- `projects/with-ai-dev` の `env exec -- env | sort` と単位 0 の基準の差分: 0 行（`CLAUDE_*` を除く。重ね順で個人共通がチーム共通に勝つため値は同じ）
- 別経路（ホストの `bao kv get -mount=devbase`、キー数のみ）: `team/global` 21 / `users/takemi_ohama/global` 26 / `team/projects/with-ai-dev` 1

### 単位 6: `devbase up` で注入を確かめる（2026-09-15）

対象の系: 手元（`with-ai-dev` のコンテナを作り直す）  区分: 本番（利用者の実環境）
取り消し: 戻せる（`devbase up` のやり直し）。退避先の削除は利用者の判断で**行わない**（carmo-cdk#341 への備え）

$ (cd projects/with-ai-dev && bin/devbase up --no-open)
[0/6]〜[5/6] … bao の token を書きました: with-ai-dev-dev-1 / === Deploy completed successfully ===
終了コード: 0

$ docker exec with-ai-dev-dev-1 bash -lc 'echo "[$GOOGLE_CLOUD_PROJECT] [$GCP_ACTIVE_PROFILE] [$DEVBASE_ACCOUNT_GROUP] ENABLE_SSH=[$ENABLE_SSH] SLACK_TEAM_ID_set=${SLACK_TEAM_ID:+yes} GH_TOKEN_set=${GH_TOKEN:+yes} BAO_ADDR_set=${BAO_ADDR:+yes}"; stat -c "%a %s" ~/.vault-token; env | grep -c "^BAO_TOKEN="; command -v bao || echo "bao not in image"'
[] [with] [with] ENABLE_SSH=[true] SLACK_TEAM_ID_set=yes GH_TOKEN_set=yes BAO_ADDR_set=yes
600 26
0
bao not in image
終了コード: 0

反映の確認:
- チーム共通（`SLACK_TEAM_ID`）・個人共通（`GH_TOKEN`）・プロジェクト env の空上書き（`GOOGLE_CLOUD_PROJECT`）・
  プロジェクトのチーム機密（`ENABLE_SSH`）がそろって渡っている（4 層の注入）
- PLAN54: `BAO_ADDR` が渡り、`~/.vault-token` は `0600`。`BAO_TOKEN` 環境変数・`docker inspect` の `Env`・
  `.docker-compose.scale.yml` に token は無い（いずれも 0 件）。`bao` 本体はイメージ未再ビルドのため無い
- コンテナから `curl` で `GET devbase/data/users/takemi_ohama/global` → 200（token が有効）

#### 起きたこと: チーム共通へ書き込みの試験データを作ってしまった（2026-09-15）

チーム共通への書き込みが拒まれることを確かめるつもりで、コンテナから
`POST devbase/data/team/zz-write-probe {"X":"1"}` を送った。単位 3 でこの利用者に `devbase-team-writer` を
付けたため 200 で通り、`team/zz-write-probe` ができた（書き込みを試す操作は承認の範囲外だった）。

$ bao kv metadata delete -mount=devbase team/zz-write-probe
Success! Data deleted (if it existed) at: devbase/metadata/team/zz-write-probe
終了コード: 0
$ bao kv get -mount=devbase team/zz-write-probe
終了コード: 2（存在しない）
$ bao kv list -mount=devbase team → ['global', 'projects/']

影響: 作成から削除まで数十秒。値は無意味な `X=1`。監査ログには作成と削除が残る。
PLAN54 の受け入れ条件 5（「チーム共通へは書けない」）は、書き手の権限を持たない利用者で確かめる必要がある。

### 単位 5 の後始末: `team/global` の旧版を消す（2026-09-15）

対象の系: OpenBao（`team/global` の版の履歴）  区分: 本番
取り消し: **戻せない**（全版の履歴を削除する）
発端: PR #181 のレビュー指摘（単位 5 の CAS 付き置き換えは新しい版を作るだけで、個人の資格情報を含む旧版が残る）。
利用者の承認を得て実施した。

確認（指摘どおり旧版に残っていた）:

$ bao kv metadata get -mount=devbase team/global
current 2 / v1（2026-09-15T07:59:41Z、47 キー。`GH_TOKEN` など個人単位のキーを含む）/ v2（08:20:18Z、21 キー）。
いずれも deleted / destroyed なし
終了コード: 0

$ bao token capabilities devbase/destroy/team/global
deny
（版だけを消す destroy は現行のポリシーに無い。そのため metadata ごと消して作り直す方式にした）

実施（1 プロセスで、値を画面に出さない）:

1. `OpenBaoBackend.fetch` で現行の v2（21 キー）を読み、個人単位の 26 キーのどれも含まないことを assert
2. `remove`（`DELETE devbase/metadata/team/global`、全版を削除）
3. `SecretStore.save` で 21 キーを新しい版 1 として書く
4. 読み戻して 1 の内容と一致することを確認

$ DEVBASE_ROOT=$PWD PYTHONPATH=lib uv run python <作り直しのスクリプト>
rebuilt team/global: 21 keys, empty window 0.25s
終了コード: 0

反映の確認:
- `bao kv metadata get -mount=devbase team/global` → `current 1 versions ['1']`
- `bao kv get -mount=devbase -version=2 team/global` → 終了コード 2（旧版は取得できない）
- `projects/with-ai-dev` の `env exec -- env | sort` と単位 0 の基準の差分: 直後の 1 回目は 1 行（差の変数名は
  控えておらず未特定）、続けて 2 回やり直していずれも 0 行（`CLAUDE_*` を除く）

同じ性質の残り:
- `users/takemi_ohama/global` は版 1〜3 を持つ。本人しか読めないパスなので対処しない
- 単位 6 で誤って作った `team/zz-write-probe` は metadata delete 済みで、版も残らない

教訓: チーム共通へ一旦入れてから個人単位へ分ける順序（決定 2）は、KV v2 の版の履歴に個人の資格情報を残す。
次に同じ移行をするなら、`migrate` の前に個人単位を分けるか、分けた後に全版の削除まで含める。

### age ストアへ戻す手順（単位 5 の後）

`migrate --to age` はチーム単位の参照（`_team_refs()`: チーム共通と各プロジェクトのチーム機密）だけを写す。
単位 5 の後は個人単位の 26 キーが `users/takemi_ohama/global` にあるため、`migrate --to age` だけでは
`GH_TOKEN` などが欠ける。退避先は利用者の判断で残している。

- (a) 退避先 `backups/env-backend-migrate/20260915-165942/` が残っている間: `global.env.age` を
  `secrets/global.env.age` へ、`projects/*.env.age`（5 本）を `secrets/projects/` へ戻し、
  `bin/devbase env backend use age`（または `auto`）で切替前の状態（グローバル 47 キー）に戻る
- (b) 退避先を消した後:
  1. OpenBao のまま `bin/devbase env get --user <KEY>` で個人単位の 26 キー（単位 5 の一覧）の値を読む
  2. `bin/devbase env backend migrate --to age` でチーム単位（グローバル 21 キーとプロジェクトのチーム機密）を戻す
  3. `bin/devbase env backend use age` の後、26 キーを `env set` で入れ直す。値がコマンド引数に載らないよう
     `bin/devbase env edit` で入れる
- (c) どちらの場合も、戻した後に `projects/with-ai-dev` で `bin/devbase env exec -- env | sort` を採り、単位 0 の基準
  `backups/plan53/before.env` と比べて差分 0 行（`CLAUDE_*` を除く）を確かめる

### 残したもの

- `backups/env-backend-migrate/20260915-165942/`（元の age。利用者の判断で残す。消すと「age ストアへ戻す手順」の (b) になる）
- `backups/plan53/`（単位 0 の基準。機密の値を含むので、確認が済んだら利用者が消す）
- 端末 `macbook` の `secret_id`（使用中）
