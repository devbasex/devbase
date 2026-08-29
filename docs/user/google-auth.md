# Google 認証ガイド

devbase のコンテナで Google Cloud（gcloud）と Google Workspace（gws）を使うための手順です。
**アカウントグループごとに人が 1 回だけ対話的に認証する**ことを前提にした仕組みなので、
新しいグループを足すときはこのページを最初から順に実行してください。

このページのコマンドと出力は、すべて実機（`carmo-ai` コンテナ、gcloud 582.0.0 / gws 0.22.5）で
実行した結果を貼っています。

## 1. 前提

### アカウントグループとは

**使用する Google / AWS アカウントの単位**です。`DEVBASE_ACCOUNT_GROUP` で宣言し、
未設定なら `default` になります。グループごとに専用のボリュームが作られ、
認証情報はその中にだけ入ります。

| マウント先 | ボリューム | 共有範囲 | 入るもの |
|---|---|---|---|
| `/persistent/ai` | `devbase_home_ubuntu` | 全コンテナ | `~/.claude/plugins` などテナントに紐づかない共通資産 |
| `/persistent/group` | `devbase_home_<group>` | 同じグループ | gcloud / gws の設定、Claude Code の認証と会話ログ、`.gemini` |

nyle.co.jp で認証した gcloud を kk-generation.com のプロジェクトが引き継がないための仕切りです。
ボリューム構造の全体は [コンテナ操作ガイド](container-operations.md) を参照してください。

### `~/.config/gcloud` は gcloud の設定ディレクトリ**ではありません**

devbase は `CLOUDSDK_CONFIG` を `/persistent/group/gcloud` へ向けています。
`credentials.db` / `access_tokens.db` / `legacy_credentials/` / `configurations/` と
ADC ファイルはすべてそちらに入ります。

```console
$ echo $CLOUDSDK_CONFIG
/persistent/group/gcloud
```

`~/.config/gcloud` に残るのは、鍵モード（後述）で書き出されるサービスアカウント鍵だけです。
これはコンテナ層（揮発）にあり、毎起動 `env` から書き直されます。
**設定や認証情報を見たいときは `$CLOUDSDK_CONFIG` を参照してください。**

`CLOUDSDK_CONFIG` は gcloud CLI 専用の仕組みではなく `google.auth` の探索経路そのものなので、
BigQuery クライアントなどのライブラリも同じ場所を見ます。

## 2. 新しいグループの初回セットアップ

プロジェクトの `env` にグループ名（と必要なら認証モード）を書いて起動します。

```bash
# projects/<name>/env
DEVBASE_ACCOUNT_GROUP=kkg
GCP_AUTH_MODE=adc          # サービスアカウント鍵を使わない場合（推奨）
```

```bash
devbase project up <name>
devbase project login <name>
```

グループ名には次の 3 つが使えません。`devbase up` の前にエラーになります。

```console
$ DEVBASE_ACCOUNT_GROUP=ubuntu devbase up
Error: Deploy failed: DEVBASE_ACCOUNT_GROUP に予約語は使えません: 'ubuntu'。共通ボリューム devbase_home_ubuntu と同じ名前になります

$ DEVBASE_ACCOUNT_GROUP=1 devbase up
Error: Deploy failed: DEVBASE_ACCOUNT_GROUP に数字だけの名前は使えません: '1'。インスタンス番号のボリューム devbase_home_<index> と同じ名前になります

$ DEVBASE_ACCOUNT_GROUP="bad name" devbase up
Error: Deploy failed: DEVBASE_ACCOUNT_GROUP が不正です: 'bad name'。Docker のボリューム名に使える文字 (英数字・ドット・ハイフン・アンダースコア、先頭は英数字) だけを使ってください
```

起動できたら、コンテナ内でどのグループにいるかを確認します。

```console
$ echo $DEVBASE_ACCOUNT_GROUP
kkg
$ echo $CLOUDSDK_CONFIG
/persistent/group/gcloud
```

新しいグループは当然まだ未認証です。

```console
$ gcloud auth list
To login, run:
  $ gcloud auth login `ACCOUNT`
```

## 3. gcloud の認証

**2 回実行します。**`gcloud auth login`（CLI 用）と
`gcloud auth application-default login`（ライブラリ用の ADC）は**別物**です。

### 3.1 `gcloud auth login`（CLI 用）

```bash
gcloud auth login
```

**フラグは要りません。** この環境では自動的に「URL を貼って認証コードを戻す」フローになります。
gcloud はブラウザを起動できるかを `DISPLAY` / `WAYLAND_DISPLAY` / `MIR_SOCKET` の有無で判定し、
コンテナ内ではどれも無いため `--no-launch-browser` と同じ経路が選ばれるためです。
VS Code のポート転送の有無は関係ありません。

手元の別マシンのブラウザで URL を開き、表示された認証コードをターミナルへ貼り戻します。

完了すると active account が設定されます。

```console
$ gcloud auth list
        Credentialed Accounts
ACTIVE  ACCOUNT
*       takemi_ohama@kk-generation.com

To set the active account, run:
    $ gcloud config set account `ACCOUNT`

$ gcloud config get account
takemi_ohama@kk-generation.com
```

このとき `$CLOUDSDK_CONFIG` の中身は次のようになります。

```console
$ ls -A $CLOUDSDK_CONFIG
.last_survey_prompt.yaml  access_tokens.db  active_config  config_sentinel
configurations  credentials.db  default_configs.db  gce  legacy_credentials  logs
```

**この時点では ADC ファイルはまだありません。**

```console
$ ls -l $CLOUDSDK_CONFIG/application_default_credentials.json
ls: cannot access '/persistent/group/gcloud/application_default_credentials.json': No such file or directory
```

### 3.2 `gcloud auth application-default login`（ライブラリ用）

BigQuery クライアントなど、`google.auth` を使うライブラリはこちらを見ます。

```console
$ gcloud auth application-default login
Go to the following link in your browser, and complete the sign-in prompts:

    https://accounts.google.com/o/oauth2/auth?response_type=code&client_id=...&redirect_uri=https%3A%2F%2Fsdk.cloud.google.com%2Fapplicationdefaultauthcode.html&scope=openid+...&prompt=consent&token_usage=remote&access_type=offline&code_challenge=...&code_challenge_method=S256

Once finished, enter the verification code provided in your browser: <ブラウザに表示されたコードを貼る>

Credentials saved to file: [/persistent/group/gcloud/application_default_credentials.json]

These credentials will be used by any library that requests Application Default Credentials (ADC).
WARNING:
Cannot find a quota project to add to ADC. You might receive a "quota exceeded" or "API not enabled" error. Run $ gcloud auth application-default set-quota-project to add a quota project.
```

保存先が **`/persistent/group/gcloud/`**（= グループボリューム）になっている点が要点です。

```console
$ ls -l $CLOUDSDK_CONFIG/application_default_credentials.json
-rw------- 1 ubuntu ubuntu 351 Aug 29 06:00 /persistent/group/gcloud/application_default_credentials.json
```

これでライブラリ側からユーザー認証が使えます。

```console
$ PYTHONPATH=/opt/google-cloud-sdk/lib/third_party python3 -c \
    "import google.auth; c, p = google.auth.default(); print(p, type(c).__name__)"
nyle-carmo-analysis Credentials
```

`Credentials`（= ユーザー認証）であって `ServiceAccountCredentials` ではないことを確認してください。

> **Note:** 末尾の警告のとおり、この時点では **quota project が ADC に書かれていません**。
> quota project を要する API（`quota exceeded` / `API not enabled` が出るもの）を使うなら
> 追加してください。
>
> ```bash
> gcloud auth application-default set-quota-project <プロジェクトID>
> ```
>
> ```console
> $ python3 -c 'import json;print(sorted(json.load(open("/persistent/group/gcloud/application_default_credentials.json")).keys()))'
> ['account', 'client_id', 'client_secret', 'refresh_token', 'type', 'universe_domain']
> ```
>
> `quota_project_id` が無い状態です。

> **Note:** `gcloud auth login --update-adc` で 1 回に減らす案は**採りません**。
> `--update-adc` は quota project を ADC に書かないため（`add_quota_project=False` のまま
> ADC を書き出す）、quota project を要する API で困ります。
> `gcloud auth application-default login` は quota project も書き込みます。

> **Note:** 鍵モード（`GCP_AUTH_MODE=key`）で実行すると、gcloud が
> 「Credentials will still be generated to the default location / To use these credentials,
> unset this environment variable before running your application」と警告します。
> `GOOGLE_APPLICATION_CREDENTIALS` が設定されていると ADC よりそちらが優先されるためです。
> ADC を使いたいなら `GCP_AUTH_MODE=adc` にしてください（後述）。

### 3.3 コンテナを作り直しても認証が残ることの確認

ここが PLAN39 で直した点です。`devbase down` はコンテナを削除しますが、認証情報は
グループボリュームに残るので**再認証は要りません**。

```console
$ devbase project down <name> && devbase project up <name>
$ gcloud auth list
        Credentialed Accounts
ACTIVE  ACCOUNT
*       takemi_ohama@kk-generation.com

$ PYTHONPATH=/opt/google-cloud-sdk/lib/third_party python3 -c \
    "import google.auth; c, p = google.auth.default(); print(p, type(c).__name__)"
nyle-carmo-analysis Credentials
```

### 3.4 グループごとに別のアカウントになっていることの確認

これが分離の目的です。ホスト側からボリュームを直接覗くと、グループごとに別のアカウントの
認証情報が入っていることが分かります。

```console
$ docker run --rm -v devbase_home_default:/g alpine ls /g/gcloud/legacy_credentials
takemi_ohama@nyle.co.jp

$ docker run --rm -v devbase_home_kkg:/g alpine ls /g/gcloud/legacy_credentials
takemi_ohama@kk-generation.com
```

コンテナ内から見ると、自分のグループのアカウントしか見えません。

```console
# default グループのコンテナ
$ gcloud config get account
takemi_ohama@nyle.co.jp

# kkg グループのコンテナ
$ gcloud config get account
takemi_ohama@kk-generation.com
```

## 4. gws（Google Workspace CLI）の認証

`gws` は base イメージに同梱されています。

```console
$ command -v gws
/usr/local/share/npm-global/bin/gws
$ gws --version
gws 0.22.5
```

設定ディレクトリは `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` でグループボリュームへ向いています。

```console
$ gws auth status
{
  "auth_method": "none",
  "client_config": "/persistent/group/gws/client_secret.json",
  "client_config_exists": false,
  "credential_source": "none",
  "encrypted_credentials": "/persistent/group/gws/credentials.enc",
  "encrypted_credentials_exists": false,
  "keyring_backend": "keyring",
  "plain_credentials": "/persistent/group/gws/credentials.json",
  "plain_credentials_exists": false,
  "storage": "none",
  "token_cache_exists": false
}
```

認証は 2 段です。`gws auth setup` は **gcloud に依存する**ので、先に 3.1 を済ませてください。

```
gws auth setup    # Cloud プロジェクトと OAuth クライアントを設定する
gws auth login    # OAuth2 で認証する
```

### 4.1 先に GCP プロジェクトを決める（詰まりやすい点）

`gws auth setup` は **gcloud の設定に GCP プロジェクトが要ります**。
`GOOGLE_CLOUD_PROJECT` 環境変数は見てくれません。

```console
$ gcloud config get project
(unset)
$ gws auth setup --dry-run
🏃 DRY RUN — no changes will be made

Step 1/6: Checking for gcloud CLI...
  ✓ gcloud CLI found
Step 2/6: Checking authentication...
  ✓ Authenticated as takemi_ohama@kk-generation.com
{
  "error": {
    "code": 400,
    "message": "No GCP project configured. Use --project <id> or run `gcloud config set project <id>`",
    "reason": "validationError"
  }
}
error[validation]: No GCP project configured. Use --project <id> or run `gcloud config set project <id>`
```

`--project` で明示するか、`gcloud config set project <id>` で設定してください。
`--dry-run` を付けると変更を加えずに手前の段階まで確認できます。

使えるプロジェクトが分からないときは `gcloud projects list` を見ます。認証したアカウントに
プロジェクトが 1 つも無いと 0 件になり、そのアカウントでは `gws auth setup` を通せません。

```console
$ gcloud projects list --limit=15
Listed 0 items.
```

### 4.2 OAuth クライアントを Console で作る

`gws auth setup` は **OAuth クライアントを自動生成できません**。Step 5/5 で手動作成を求められます。

```
  ✓ Step 1/5: gcloud CLI — found
  ✓ Step 2/5: Authentication — takemi_ohama@nyle.co.jp
  ✓ Step 3/5: GCP project — nyle-carmo-analysis
  ✓ Step 4/5: Workspace APIs — 0 enabled, 22 skipped
  ▸ Step 5/5: OAuth credentials — Waiting for manual input...

  Manual OAuth client setup required.

  Step A — Consent screen (if not configured):
  https://console.cloud.google.com/apis/credentials/consent?project=<プロジェクトID>
  → User Type: External, then save through all screens.

  Step B — Create an OAuth client:
  https://console.cloud.google.com/apis/credentials?project=<プロジェクトID>
  → 'Create Credentials' → 'OAuth client ID'
  → Application type: Desktop app
  → Redirect URI: http://localhost (auto-negotiated; no manual entry needed)
```

Console で作った **クライアント ID** と **クライアント シークレット**を、続くプロンプトへ順に貼ります。

> **Warning:** クライアント シークレットを `$DEVBASE_ROOT/env` やプロジェクトの `env` に
> **書かないでください**。`env` は非機密用で `source` されるため、`KEY=値` の形でない行を
> 書くと `devbase` コマンド自体が壊れます（`command not found`）。gws が
> `$GOOGLE_WORKSPACE_CLI_CONFIG_DIR/client_secret.json` として保存するので、
> どこかへ控える必要はありません。

成功すると `client_secret.json` がグループボリュームに置かれます。

```console
$ ls -l $GOOGLE_WORKSPACE_CLI_CONFIG_DIR
total 4
-rw------- 1 ubuntu ubuntu 470 Aug 29 08:46 client_secret.json

$ gws auth status
{
  "auth_method": "none",
  "client_config": "/persistent/group/gws/client_secret.json",
  "client_config_exists": true,
  "config_client_id": "12826645....com",
  "credential_source": "client_secret.json",
  "enabled_api_count": 110,
  ...
  "project_id": "nyle-carmo-analysis",
  "storage": "none"
}
```

### 4.3 ログイン

`gws auth login` は gcloud と**流儀が違います**。認証コードを貼り戻すのではなく、
**コンテナ内の `localhost:<ランダムポート>` でコールバックを待ち受けます**。

```console
$ docker exec -it <コンテナ名> gws auth login --readonly
Open this URL in your browser to authenticate:

  https://accounts.google.com/o/oauth2/auth?scope=...&redirect_uri=http://localhost:34437&response_type=code&client_id=...&prompt=select_account+consent
```

> **Note:** `docker exec -it <コンテナ> bash -lc 'gws auth login'` の形だと
> `Failed to read prompt input: stream did not contain valid UTF-8` で落ちることがあります。
> `bash -lc` を挟まずに直接実行するか、`docker exec -it <コンテナ> bash` で入ってから
> 実行してください。

スコープは `--readonly`（読み取りのみ）/ `--full`（pubsub + cloud-platform を含む全部）/
`--services drive,gmail,sheets` のように選べます。迷うなら `--readonly` が安全です。

#### ブラウザがホスト側にある場合（devbase では通常こちら）

`redirect_uri` の `localhost` は**コンテナ内の localhost** です。ホストのブラウザから
`http://localhost:34437` を開いてもコンテナには届きません。次の手順で中継します。

1. 表示された URL をホストのブラウザで開き、認証を済ませる
2. ブラウザが `http://localhost:<ポート>/?code=...` へリダイレクトされ「接続できません」になる
3. **アドレスバーの URL 全体をコピーする**
4. 別のターミナルから、コンテナ内でその URL を叩いてコールバックを届ける

```bash
docker exec <コンテナ名> curl -s "http://localhost:<ポート>/?code=...&scope=..."
```

ポート番号は実行のたびに変わるので、手順 1 で表示された `redirect_uri` の値を使ってください。

> **Note:** VS Code でコンテナにアタッチしている場合は、VS Code の自動ポート転送が効いて
> ホストのブラウザから直接届くことがあります。その場合は手順 3〜4 は不要です。

認証が通ると、コールバックを受けた側に許可されたスコープと `"status": "success"` が出ます。

```console
{
  "scopes": [
    "https://www.googleapis.com/auth/drive.readonly",
    ...
    "https://www.googleapis.com/auth/userinfo.profile"
  ],
  "status": "success"
}
```

`$GOOGLE_WORKSPACE_CLI_CONFIG_DIR` に `credentials.enc`（暗号化済みの認証情報）が作られます。

```console
$ ls -l $GOOGLE_WORKSPACE_CLI_CONFIG_DIR
total 12
drwxr-xr-x 2 ubuntu ubuntu 4096 Aug 29 08:47 cache
-rw------- 1 ubuntu ubuntu  470 Aug 29 08:46 client_secret.json
-rw------- 1 ubuntu ubuntu  334 Aug 29 09:24 credentials.enc

$ gws auth status | grep -E '"auth_method"|"storage"'
  "auth_method": "oauth2",
  "storage": "encrypted",
```

コンテナを作り直しても**再認証は要りません**。

```console
$ devbase project down <name> && devbase project up <name>
$ ls -l $GOOGLE_WORKSPACE_CLI_CONFIG_DIR
total 12
drwxr-xr-x 2 ubuntu ubuntu 4096 Aug 29 08:47 cache
-rw------- 1 ubuntu ubuntu  470 Aug 29 08:46 client_secret.json
-rw------- 1 ubuntu ubuntu  334 Aug 29 09:24 credentials.enc

$ gws auth status | grep -E '"auth_method"|encrypted_credentials_exists|"storage"'
  "auth_method": "oauth2",
  "encrypted_credentials_exists": true,
  "storage": "encrypted",
```

> **Note:** `keyring_backend` は `keyring` のままで動きました。コンテナに OS キーリングが
> 無くても `credentials.enc` として暗号化保存されるため、
> `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file` を指定する必要はありませんでした。

#### 同意画面で止まる場合

`--readonly` でも `drive.readonly` / `gmail.readonly` は Google の**制限付きスコープ**です。
OAuth 同意画面が「テスト中」でテストユーザーに自分が入っていない、あるいはアプリ情報が
未入力だと、同意フローが先へ進まないことがあります。Console の
「OAuth 同意画面」で公開ステータスとテストユーザーを確認してください。

スコープを絞れば制限付きスコープを避けられます。疎通確認だけなら次で十分です。

```bash
gws auth login --scopes openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/userinfo.profile
```

> **Note:** 待ち受けプロセスを止めると、その回に発行された認証コードは使えなくなります
> （`redirect_uri` のポートが変わるため）。`gws auth login` をやり直したら、
> **新しく表示された URL** から認証し直してください。

## 5. 認証モードの切り替え

`GCP_AUTH_MODE` はプロジェクトの `env` かグローバル `env` に手書きします。

| 値 | 挙動 |
|---|---|
| `adc`（推奨） | 鍵を書かない。`GOOGLE_APPLICATION_CREDENTIALS` と `BIGQUERY_KEY_FILE` を**コンテナへ渡さない**。認証は `$CLOUDSDK_CONFIG/application_default_credentials.json` に委ねる |
| `key` | `GCP_CREDENTIALS_BASE64__<profile>` を復号して書き、上記 2 変数を渡す（従来どおり）|
| 未設定 | アクティブプロファイルの鍵の env があれば `key`、無ければ `adc` |

**鍵が要るのはどういう場面か。** ユーザー認証では権限が足りない、あるいは人に紐づかない
実行主体が必要な場面です。たとえば本番データセットへの読み取りがサービスアカウントにしか
付与されていない場合や、コンテナ内から実行するバッチが特定の SA として動く必要がある場合です。
それ以外の日常的な開発では ADC で足ります（Google もローカル開発には
`gcloud auth application-default login` を推奨しています）。

切り替えたら **`devbase up` が必要**です。コンテナへ渡す環境変数が変わるためで、
コンテナ内で `export` しても `docker exec` の別シェルには反映されません。

```console
$ echo 'GCP_AUTH_MODE=adc' >> projects/<name>/env
$ devbase project up <name>
```

`adc` に切り替わると 2 変数は**未設定**になります。

```console
$ echo ${GOOGLE_APPLICATION_CREDENTIALS-<unset>}
<unset>
$ echo ${BIGQUERY_KEY_FILE-<unset>}
<unset>
```

値だけ残して実体が無いと ADC はユーザー認証へフォールバックせず落ちるため、devbase は
「空にする」のではなく「渡さない」を選んでいます。

## 6. 確認コマンド

### いま自分がどのグループにいるか

ホスト側:

```console
$ devbase status
...
[環境]
  アカウントグループ          kkg (devbase_home_kkg / env)
```

末尾は値が `env` 由来か、未設定によるフォールバック（`既定`）かを示します。

コンテナ内:

```console
$ echo $DEVBASE_ACCOUNT_GROUP
kkg
$ echo $CLOUDSDK_CONFIG
/persistent/group/gcloud
$ readlink -f ~/.claude
/persistent/group/.claude
$ readlink -f ~/.claude/plugins
/persistent/ai/.claude/plugins
```

最後の 2 行が要点です。会話ログや認証はグループ側、プラグインなどの共通資産は共通側を指します。

コンテナの起動ログにも 1 行出ます。

```console
$ devbase project logs <name> | grep "Account group"
Account group: kkg (gcloud account: takemi_ohama@kk-generation.com, CLOUDSDK_CONFIG: /persistent/group/gcloud)
```

### 認証の疎通

```bash
gcloud auth list                    # CLI 側の active account
gcloud config get account           # 同上 (1 行)
gws auth status                     # gws の認証状態
```

ライブラリ側（ADC）は `google.auth` で確認します。コンテナの `python3` には
`google` パッケージが入っていないため、gcloud 同梱のものを使います。

```console
$ PYTHONPATH=/opt/google-cloud-sdk/lib/third_party python3 -c \
    "import google.auth; c, p = google.auth.default(); print(p, type(c).__name__)"
nyle-carmo-analysis Credentials
```

## 7. トラブルシュート

### `DefaultCredentialsError: Your default credentials were not found.`

**まだ ADC の認証をしていない**状態です。3.2 の
`gcloud auth application-default login` を実行してください。これは `adc` モードで
未認証のときの**正常な状態**です。

### `DefaultCredentialsError: File /... was not found.`

`GOOGLE_APPLICATION_CREDENTIALS` が**実体の無いパスを指しています**。ADC はこの場合
ユーザー認証へフォールバックせず例外で落ちます。

devbase は `adc` モードでこの変数をコンテナへ渡さないので、通常は起きません。起きるとすれば
プロジェクトの `env`（機密ではない方）にこの変数が直接書かれている場合です。次で確認します。

```console
$ echo ${GOOGLE_APPLICATION_CREDENTIALS-<unset>}
```

`<unset>` でなければ `env` からその行を消して `devbase up` し直してください。

### `database is locked`

gcloud は**並行実行を想定していません**（公式ドキュメント: "Parallel execution of multiple
gcloud CLI commands is not supported."）。`credentials.db` は SQLite なので、
**同じアカウントグループの複数コンテナが同時に `gcloud` を叩く**と出ることがあります。

恒久対策は取っていません。少し待って**再実行**してください。これはグループボリュームを
同じグループの全コンテナで共有する設計に内在するもので、認証情報をどう置いても同じです。

### 意図しないアカウントで操作していた

まず**どのグループにいるか**を確認します（6 章）。グループが正しいのにアカウントが違う場合は、
そのグループに複数のアカウントで認証しています。

```console
$ gcloud auth list
        Credentialed Accounts
ACTIVE  ACCOUNT
        someone@example.com
*       takemi_ohama@kk-generation.com
```

切り替えは `gcloud config set account`、要らないものは `gcloud auth revoke` で消します。

```bash
gcloud config set account <正しいアカウント>
gcloud auth revoke <不要なアカウント>
```

グループ自体が間違っていた場合は、プロジェクトの `env` の `DEVBASE_ACCOUNT_GROUP` を直して
`devbase up` し直してください。**別グループの認証は互いに見えない**ので、正しいグループへ
移れば意図しないアカウントは選択肢にすら出てきません。

## 関連

- [コンテナ操作ガイド](container-operations.md) — ボリューム構造、AI 設定の永続化
- [環境変数ガイド](environment-variables.md) — `DEVBASE_ACCOUNT_GROUP` / `GCP_AUTH_MODE`
