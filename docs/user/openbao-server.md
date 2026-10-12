# OpenBao サーバを用意する

devbase の機密の保存先に [OpenBao](https://openbao.org/) を使うとき、サーバをどう持つか
（利用の形）と、サーバの側で用意するもの（マウント・AppRole・ポリシー・利用者・端末）を
説明するガイドです。用意したサーバへ devbase をつなぐ手順と日々の操作は
[機密の保存先を選ぶ](env-backend.md)にあります。

devbase は共通の OpenBao サーバを運用していません。サーバは利用者が個人またはチームで立てます。
この文書の手順と、リポジトリに置いた雛形だけで同じ構成を作れます。

## 利用の形を選ぶ

| 利用の形 | サーバ | 管理する人 | 利用者が用意するもの |
|---|---|---|---|
| [個人の形](#個人の形) | 自分で 1 台立て、1 人で使う | 自分 | サーバ・[管理の手順](#管理の手順個人とチームで共通)の全部 |
| [チームの形](#チームの形) | チームで 1 台立て、チーム共通と個人の置き場を分ける | チームの誰か | 管理する人: サーバ・管理の手順の全部と、利用者と端末の追加・失効。利用者: 受け取った値で接続する |
| [既存のサーバの形](#既存のサーバの形) | 組織が運用しているサーバへ接続する | 組織の管理者 | 管理者から受け取った値で接続する |

この文書の「管理する人」は、サーバでマウント・AppRole・ポリシー・entity を作る人です。
個人の形では利用者本人です。

まず試すだけなら、[試用の構成](#試用の構成で試す)で手元に 1 台立て、数分で接続から
`devbase up` まで通せます。

## リポジトリに置いた雛形

`$DEVBASE_ROOT/docs/user/openbao/` に、サーバの側で使うファイルがあります。devbase は実行時に
これらを読みません。

| ファイル | 内容 |
|---|---|
| [`compose.yml`](openbao/compose.yml) | 試用の構成。開発モードの OpenBao を手元に立てる。OpenBao の版はこのファイルの `image` の 1 行にある |
| [`policies/devbase-user.hcl`](openbao/policies/devbase-user.hcl) | 個人の雛形。本人の `users/<本人>/` の下だけを読み書きさせる（`version: 1` / `2` 共通） |
| [`policies/v2/team-read.hcl`](openbao/policies/v2/team-read.hcl) | チームの読み取りの雛形（`version: 2`）。`__GROUP__` を置き換えたグループの `team/<group>/` を読ませる |
| [`policies/v2/team-write.hcl`](openbao/policies/v2/team-write.hcl) | チームの書き込みの雛形（`version: 2`）。同じグループの `team/<group>/` へ書かせる |
| [`policies/v1/team-read.hcl`](openbao/policies/v1/team-read.hcl) | チームの読み取りの雛形（`version: 1`）。`team/global` と `team/projects/<name>` を読ませる |
| [`policies/v1/team-write.hcl`](openbao/policies/v1/team-write.hcl) | チームの書き込みの雛形（`version: 1`） |

`version: 2` は置き場をアカウントグループごとに分ける設定で、新しく `devbase env backend use openbao`
した端末の既定です（[アカウントグループごとの置き場](env-backend.md#アカウントグループごとの置き場version-2)）。
個人の形も `version: 2` を使い、グループは `personal` にします。

雛形は KV v2 のマウント名 `devbase` を書いています。ほかの名前でマウントするときは、書き込む前に
置き換えます（例: `sed 's#^path "devbase/#path "secrets/#'`）。

## 個人の形

### 試用の構成で試す

手元の 1 台に開発モードの OpenBao を立て、devbase をつないで `devbase up` まで通します。

**開発モードのサーバは機密を記憶にだけ置き、止めると全部消えます。本物の機密は置かないでください。**
続けて使うなら[自分のサーバで使う](#自分のサーバで使う)へ進みます。

使うのは Docker と Docker Compose だけで、ホストに `bao` は要りません。管理の操作は、サーバの
コンテナの中の `bao` で打ちます（開発モードのサーバが root token をコンテナの中に置くため、
root token を画面に出さずに済みます）。

#### 1. 今の接続設定を退避する

`devbase env backend use openbao` は `secrets/backend.yml` と `secrets/bootstrap.env.age` を
上書きします。すでに使っている接続設定があれば、先に退避します。

```bash
cd "$DEVBASE_ROOT"
mkdir -p secrets/trial-backup
for f in backend.yml bootstrap.env.age; do
  [ -f "secrets/$f" ] && cp -p "secrets/$f" secrets/trial-backup/
done
```

age の鍵がまだ無ければ作ります（OpenBao を使うときも要ります）。

```bash
devbase env keygen
```

#### 2. サーバを起動する

```bash
docker compose -f "$DEVBASE_ROOT/docs/user/openbao/compose.yml" up -d

# 管理の操作を打つための関数 (この後の手順で使う)
bao_admin() {
  docker compose -f "$DEVBASE_ROOT/docs/user/openbao/compose.yml" exec -T openbao bao "$@"
}

bao_admin status          # Initialized true / Sealed false と Version が出れば起動している
```

サーバはホストの `127.0.0.1:8200` にだけ公開されます（同じ LAN の他の端末からは届きません）。
ポート 8200 がほかで使われていて起動しないときは、`compose.yml` の `ports` の左側のポートを変え、
以後の `http://127.0.0.1:8200` も同じポートにします。

#### 3. サーバを用意する

[管理の手順](#管理の手順個人とチームで共通)の 1〜4 を上から打ちます。利用者は自分 1 人
（例: `member01`）で、グループは `personal`、チーム共通への書き込みも自分に許します。

#### 4. 接続する

[管理の手順](#管理の手順個人とチームで共通)の 5 で、この端末の `secret_id` を発行して
そのまま `devbase env backend use openbao` へ渡します。`--url` は `http://127.0.0.1:8200` です。

```bash
devbase env backend test --group personal
```

#### 5. プロジェクトで使う

プロジェクトの `projects/<name>/env` でアカウントグループ `personal` を宣言します（宣言の無い
プロジェクトは機密のコマンドも `up` も止まります）。

```bash
DEVBASE_ACCOUNT_GROUP=personal
```

プロジェクトのディレクトリで読み書きし、起動します。

```bash
devbase env set API_KEY=example-value             # チーム共通 (team/personal/global)
devbase env set --user MY_TOKEN=example-value     # 個人共通 (users/<本人>/personal/global)
devbase env get API_KEY
devbase up
```

起動したコンテナの環境変数に、書いた値が入ります。コンテナの中の `bao` も手元のサーバへ
届きます（[コンテナの中の `bao`](#コンテナの中の-bao)）。

```bash
bao kv get -mount=devbase users/<本人>/personal/global     # dev コンテナの中で
```

#### 6. 片付ける

サーバを止めると、サーバの機密は全部消えます。

```bash
docker compose -f "$DEVBASE_ROOT/docs/user/openbao/compose.yml" down
```

devbase の接続設定を元へ戻します。退避した設定があれば戻します（手元のキャッシュ
`secrets/cache/` は元の接続の控えを含むため消しません。試用の控えは接続先が違うため使われません）。

```bash
cd "$DEVBASE_ROOT"
cp -p secrets/trial-backup/* secrets/ && rm -r secrets/trial-backup
```

退避した設定が無ければ（試用の前は OpenBao を使っていなければ）、設定なしへ戻し、試用の
資格情報と控えを消します。

```bash
cd "$DEVBASE_ROOT"
devbase env backend use auto
rm -f secrets/bootstrap.env.age
rm -rf secrets/cache
rmdir secrets/trial-backup 2>/dev/null
```

どちらのときも、最後に OpenBao の試用のサーバを指していないことを確かめます。

```bash
devbase env backend status
```

`up` したプロジェクトのコンテナは `devbase down` で止めます。

### 自分のサーバで使う

本物の機密を置くサーバは、開発モードではなく、ストレージを持つ構成で立てます。サーバの構築・
初期化（`bao operator init`）・封印の解除・TLS の証明書・バックアップ・監査ログは OpenBao の
文書に従ってください。

- [Install OpenBao](https://openbao.org/docs/install/)
- [Configuration](https://openbao.org/docs/configuration/)

ここから先は、初期化と封印の解除が済み、管理の権限を持つ token で `bao` を打てるサーバを前提にします。

1. `bao_admin` を、管理する人の端末の `bao` の呼び出しにします。`BAO_ADDR` はサーバの URL です

   ```bash
   export BAO_ADDR=https://openbao.example.com
   bao login                 # 管理の権限を持つ token (入力は伏せ字)
   bao_admin() { bao "$@"; }
   ```

2. [管理の手順](#管理の手順個人とチームで共通)を上から打ちます。手順は試用の構成と同じです
3. 接続の `--url` は、devbase がサーバへ届く URL です。`https`、または手元のサーバの `http` の
   loopback（`http://127.0.0.1:<ポート>` / `http://localhost:<ポート>`）だけを受け付けます

devbase の接続に root token は使いません。devbase は AppRole の `role_id` と端末ごとの
`secret_id` だけで接続します。

## 管理の手順（個人とチームで共通）

例の利用者は `member01`、グループは `personal`、端末の名前は `laptop01` です。コマンドの
`bao_admin` は、試用の構成ではサーバのコンテナの中の `bao`、自分のサーバでは管理する人の端末の
`bao` です（[試用の構成](#2-サーバを起動する) / [自分のサーバ](#自分のサーバで使う)）。
雛形の置き場は `T="$DEVBASE_ROOT/docs/user/openbao/policies"` とします。

### 1. KV v2 をマウントする

```bash
bao_admin secrets list                                  # devbase/ がまだ無いことを確かめる
bao_admin secrets enable -path=devbase -version=2 kv
```

`devbase/` が既にあれば作り直さず、`bao_admin secrets list -detailed` で種類が `kv`・
`version` が `2` であることを確かめます。別の名前のマウントを使うときは、雛形のマウント名を
置き換え（[雛形](#リポジトリに置いた雛形)）、接続で `--mount <名前>` を付けます。

### 2. AppRole を有効にする

```bash
bao_admin auth enable approle
```

### 3. ポリシーを書き込む

個人の雛形は `version: 1` と `2` で共通です。

```bash
T="$DEVBASE_ROOT/docs/user/openbao/policies"
bao_admin policy write devbase-user - < "$T/devbase-user.hcl"
```

`version: 2` は、使うグループごとに読み取りと書き込みの 2 つを書きます。

```bash
g=personal
sed "s/__GROUP__/$g/g" "$T/v2/team-read.hcl"  | bao_admin policy write "devbase-team-read-$g" -
sed "s/__GROUP__/$g/g" "$T/v2/team-write.hcl" | bao_admin policy write "devbase-team-write-$g" -
```

`version: 1`（`devbase env backend use openbao --layout flat`）では次の 2 つです。

```bash
bao_admin policy write devbase-team-read  - < "$T/v1/team-read.hcl"
bao_admin policy write devbase-team-write - < "$T/v1/team-write.hcl"
```

### 4. 利用者を足す

利用者 1 人につき、AppRole のロール・entity・entity alias を 1 つずつ作ります。**entity の名前は、
その利用者が devbase の接続で `--user` に渡す識別子と同じ値にします。** 個人の雛形は entity の
名前で本人の置き場を絞るため、違えると本人の置き場が 403 になります。

```bash
u=member01

# ロール: token は 1 時間 (devbase が更新する)。権限は entity の側に付ける
bao_admin write "auth/approle/role/$u" token_policies=default token_ttl=1h token_max_ttl=1h

# entity: 名前は利用者の識別子。付けるポリシーで読み書きの範囲が決まる
bao_admin write identity/entity name="$u" \
  policies=devbase-user,devbase-team-read-personal,devbase-team-write-personal

# entity alias: AppRole の alias の名前は role_id
role_id=$(bao_admin read -field=role_id "auth/approle/role/$u/role-id")
entity_id=$(bao_admin read -field=id "identity/entity/name/$u")
accessor=$(bao_admin read -field=accessor sys/auth/approle)
bao_admin write identity/entity-alias name="$role_id" canonical_id="$entity_id" mount_accessor="$accessor"
```

| 付けるポリシー | 許すこと |
|---|---|
| `devbase-user` | 本人の `users/<本人>/` の下の読み書き |
| `devbase-team-read-<group>`（`version: 1` は `devbase-team-read`） | そのグループのチーム共通とチームのプロジェクトの読み取り |
| `devbase-team-write-<group>`（`version: 1` は `devbase-team-write`） | そのグループのチーム共通とチームのプロジェクトへの書き込み。書いてよい人にだけ付ける |

個人の形では、自分に 3 つとも付けます。

### 5. 端末の `secret_id` を発行して接続する

`secret_id` は端末ごとに発行します。端末の名前を metadata に入れておくと、失効させるときに
`secret_id` の値を控えずに端末を選べます（[端末を失効させる](#端末の-secret_id-を失効させる)）。

接続する端末で管理の操作も打てるとき（個人の形）は、発行した `secret_id` を画面に出さず、
そのまま `--secret-id-stdin` へ渡します。

```bash
u=member01
role_id=$(bao_admin read -field=role_id "auth/approle/role/$u/role-id")
bao_admin write -f -field=secret_id "auth/approle/role/$u/secret-id" \
    metadata='{"terminal":"laptop01"}' \
  | devbase env backend use openbao \
      --url http://127.0.0.1:8200 \
      --user "$u" \
      --role-id "$role_id" \
      --secret-id-stdin
```

`--layout` を付けなければ `version: 2` になります。`version: 1` の雛形を書いたサーバでは
`--layout flat` を付けます。

## チームの形

管理する人がサーバを 1 台立て、[管理の手順](#管理の手順個人とチームで共通)の 1〜3 を一度だけ
打ちます。グループごとに 3 の `version: 2` の 2 つを書きます。以後、利用者と端末が増えるたびに
次を打ちます。

### 利用者を足す

[管理の手順の 4](#4-利用者を足す)を、その利用者の識別子で打ちます。チーム共通へ書かせない
利用者には、書き込みのポリシーを付けません。

```bash
u=member02
bao_admin write "auth/approle/role/$u" token_policies=default token_ttl=1h token_max_ttl=1h
bao_admin write identity/entity name="$u" policies=devbase-user,devbase-team-read-personal
role_id=$(bao_admin read -field=role_id "auth/approle/role/$u/role-id")
entity_id=$(bao_admin read -field=id "identity/entity/name/$u")
accessor=$(bao_admin read -field=accessor sys/auth/approle)
bao_admin write identity/entity-alias name="$role_id" canonical_id="$entity_id" mount_accessor="$accessor"
```

利用者には、接続先の URL・マウント名・識別子（`member02`）・`role_id` を渡します。

### チーム共通への書き込みを許す・外す

書き込みは entity のポリシーで決まります。`policies` は書いた一覧で丸ごと置き換わるため、
今の一覧を確かめてから、残すものを全部並べて書きます。

```bash
bao_admin read -field=policies identity/entity/name/member02
bao_admin write identity/entity/name/member02 \
  policies=devbase-user,devbase-team-read-personal,devbase-team-write-personal   # 許す
bao_admin write identity/entity/name/member02 \
  policies=devbase-user,devbase-team-read-personal                              # 外す
```

ほかのグループの置き場も読ませるときは、そのグループの `devbase-team-read-<group>` を足します。

### 端末を足す

端末ごとに `secret_id` を発行します。管理する人と利用者が別の人なら、発行した `secret_id` は
安全な経路で利用者へ渡します。

```bash
bao_admin write -f -field=secret_id auth/approle/role/member02/secret-id \
  metadata='{"terminal":"desktop02"}'
```

受け取った利用者は、自分の端末で接続します。`--secret-id-stdin` を省くと伏せ字で入力を
求められます（`secret_id` をコマンド引数では渡しません）。

```bash
devbase env backend use openbao \
  --url https://openbao.example.com \
  --user member02 \
  --role-id <role_id>
```

### 端末の `secret_id` を失効させる

端末を手放したときは、その端末の `secret_id` だけを失効させます。同じ利用者の別の端末は
そのまま使えます。

```bash
u=member02
for a in $(bao_admin list -format=json "auth/approle/role/$u/secret-id" | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin)))'); do
  echo "$a $(bao_admin write -field=metadata "auth/approle/role/$u/secret-id-accessor/lookup" secret_id_accessor="$a")"
done
# 端末の名前で accessor を選んで消す
bao_admin write "auth/approle/role/$u/secret-id-accessor/destroy" secret_id_accessor=<accessor>
```

`secret_id` を消しても止まるのは新しいログインだけで、その端末へ発行済みの token（コンテナの
`~/.vault-token`）は期限（最大 1 時間）まで読み書きできます。続けて、その端末の token も失効させます。
token には発行に使った `secret_id` の metadata（`terminal`）とロールの名前（`role_name`）が載るため、
その 2 つで選びます。

```bash
t=laptop01   # 失効させる端末の名前 (secret_id の metadata の terminal)
for a in $(bao_admin list -format=json auth/token/accessors | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin)))'); do
  m=$(bao_admin write -format=json auth/token/lookup-accessor accessor="$a" \
    | python3 -c 'import json,sys; m=json.load(sys.stdin)["data"].get("meta") or {}; print(m.get("role_name", ""), m.get("terminal", ""))')
  if [ "$m" = "$u $t" ]; then
    bao_admin write auth/token/revoke-accessor accessor="$a"
  fi
done
```

`secret_id` を失効させた端末では、`devbase env backend test` が「認証が拒まれました」で止まります。
このコマンドは毎回 `secret_id` で新しくログインするため、確かめられるのは新しいログインが止まったこと
だけです。発行済みの token が残っていないことは、上の手順をもう一度打って `revoke-accessor` が 1 度も
走らないことで確かめます。

利用者ごと外すときは、ロールと entity を消します（`bao_admin delete auth/approle/role/<u>` と
`bao_admin delete identity/entity/name/<u>`）。その利用者の全端末が止まります。

### 雛形で決まる権限

`version: 2` の雛形で、`member01` にだけ `personal` の書き込みのポリシーを付けたときの結果です。

| 操作 | `member01` | `member02` |
|---|---|---|
| 本人の `users/<本人>/personal/global` の読み書き | 通る | 通る |
| 他人の `users/<他人>/…` の読み取り | 403 | 403 |
| `users/` 直下の一覧 | 403 | 403 |
| `team/personal/global` の読み取り | 通る | 通る |
| `team/personal/global` への書き込み | 通る | 403 |
| ポリシーを付けていないグループの `team/<group>/…` | 403 | 403 |
| `sys/` の管理の経路 | 403 | 403 |

`version: 1` の雛形でも、他人の個人の置き場の 403 と、`team/global` への書き込みの制限は
同じく効きます。

## 既存のサーバの形

組織が運用しているサーバへ接続するときは、サーバの用意は要りません。管理者から次の値を
受け取り、[機密の保存先を選ぶ](env-backend.md#2-backend-を切り替える)の手順で接続します。

| 値 | 例 |
|---|---|
| 接続先 URL（`https`。ホストとポートまで） | `https://openbao.example.com` |
| KV v2 のマウント名（既定 `devbase`） | `devbase` |
| あなたの識別子（サーバの entity 名と同じ値） | `member01` |
| あなたの AppRole の `role_id` | — |
| **この端末用**の `secret_id` | — |
| 置き場の版（`version: 1` か `2`）と、使うアカウントグループ | `version: 2`、`acme` |

## コンテナの中の `bao`

`devbase up` は dev コンテナへ接続先 `BAO_ADDR` と 1 時間の token を渡し、コンテナの中の `bao` で
本人の置き場を読み書きできます（[コンテナの中から `bao` を使う](env-backend.md#コンテナの中から-bao-を使う)）。

コンテナの中の `127.0.0.1` はコンテナ自身を指します。そのため、接続先 URL が `http` の loopback
（`http://127.0.0.1:8200` など）で、Docker が手元の端末のものなら、devbase はコンテナへ渡す
`BAO_ADDR` のホストを `host.docker.internal` に置き換え（`http://host.docker.internal:8200`）、
dev コンテナに `host.docker.internal:host-gateway` の行を足します。手元の devbase が使う URL は
変わりません。

| 接続先 URL | Docker | コンテナの `BAO_ADDR` |
|---|---|---|
| `http://127.0.0.1:8200` / `http://localhost:8200` / `http://[::1]:8200` | 手元の端末 | `http://host.docker.internal:8200` |
| 同上 | 別の端末（`project.local.yml` の `docker.context`） | 置き換えない（コンテナのある端末からは手元のサーバへ届きません） |
| `https://…` | どちらでも | 置き換えない |

dev サービスが `network_mode: host` のときは置き換えません（loopback がそのまま届きます）。
dev サービスの `extra_hosts` に `host.docker.internal` の行が既にあれば、その行を使います。

確かめた環境は macOS の Docker Desktop です。Linux の Docker では確かめていません。Linux の
Docker では、試用の構成のようにサーバのポートをホストの `127.0.0.1` にだけ公開すると、
`host-gateway` 宛ての要求が届かない見込みです。コンテナの中の `bao` を使うには、サーバを
Docker のブリッジから届くアドレスでも待たせてください。

## 関連

- [機密の保存先を選ぶ（OpenBao との連携）](env-backend.md)
- 確定仕様: [機密の保存先（secret backend）](../specifications/secret-backend.md#openbao-との契約)
- [OpenBao: AppRole auth](https://openbao.org/docs/auth/approle/) /
  [Identity](https://openbao.org/docs/concepts/identity/) /
  [Policies](https://openbao.org/docs/concepts/policies/)
