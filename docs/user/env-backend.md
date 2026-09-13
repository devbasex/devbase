# 機密の保存先を選ぶ（Infisical との連携）

devbase が扱う機密の保存先を、手元のファイル（平文 / age 暗号化）から、WebUI を持つ
OSS の機密管理サーバ [Infisical](https://infisical.com/) へ載せ替えるためのガイドです。

保存先は `devbase env backend use <name>` で明示的に選び、いつでも元へ戻せます。
何も設定していない端末の挙動は変わりません。

## 何が変わるのか

| | 設定なし（既定） | `backend: age` | `backend: infisical` |
|---|---|---|---|
| 保存先の決め方 | ファイルの存在で自動判定 | 常に `secrets/*.age` | 常に Infisical サーバ |
| 共通の機密 | `.env` または `secrets/global.env.age` | `secrets/global.env.age` | `/team/global` |
| プロジェクトの機密 | `projects/<name>/.env` または `secrets/projects/<name>.env.age` | `secrets/projects/<name>.env.age` | `/team/projects/<name>` |
| 個人単位の機密 | 持たない | 持たない | `/users/<user>/global` と `/users/<user>/projects/<name>` |
| 日々の操作 | `devbase env set` / `get` / `edit` … | **変わらない** | **変わらない** |
| サーバ不達時 | — | — | 手元の暗号化キャッシュで起動 |

サーバへの接続に使う資格情報（machine identity の client ID / client secret）は、
既存の age 暗号化ストアと同じ仕組みで `secrets/bootstrap.env.age` に保存されます。
**Infisical を使う場合も age の鍵は必要です。** 先に `devbase env keygen` を済ませてください。

## 使いはじめる

### 1. Infisical 側で用意するもの

サーバの構築と運用は devbase の範囲外です。管理者から次の値を受け取ってください。

| 値 | 例 |
|---|---|
| 接続先 URL（`https`） | `https://infisical.example.com` |
| project の ID | `7f0e2c1a-…` |
| environment の slug（既定 `common`） | `common` |
| machine identity の client ID / client secret | — |
| あなたの識別子（個人単位の機密の置き場に使う） | `member01` |

### 2. backend を切り替える

client secret は**コマンド引数では受け取りません**（`ps` から読めるため）。標準入力か、
端末での伏せ字入力で渡します。

```bash
devbase env keygen                      # まだなら
printf '%s\n' "$CLIENT_SECRET" | devbase env backend use infisical \
  --url https://infisical.example.com \
  --project-id 7f0e2c1a-... \
  --user member01 \
  --client-id <client-id> \
  --client-secret-stdin
```

TTY で実行する場合は `--client-secret-stdin` を省くと伏せ字入力を求められます。

設定は `secrets/backend.yml`（機密を含まない）に、資格情報は `secrets/bootstrap.env.age`
（age 暗号化）に保存されます。どちらも `0600` で、`secrets/` は Git の除外対象です。

### 3. 接続を確かめる

```bash
devbase env backend test
devbase env backend status
```

`test` は認証と参照ごとの取得を行い、接続先と読めた参照の件数を表示します。到達できない・
認証できない場合は理由を表示して非ゼロで終了します。

### 4. 既存の機密を移す

```bash
devbase env backend migrate --to infisical --dry-run   # 移すキー名だけを確認（値は出ない）
devbase env backend migrate --to infisical
```

移行は次の順で進み、途中で失敗すると設定は移行前のまま残ります。

1. 移行先の同じ参照を読み、**同じキーがあれば 1 件も書かずに中止**する（衝突したキー名を表示）
2. 移行先に元からあった内容へ移行元を重ねて書き込む（元からあったキーは消えない）
3. 読み戻して元と一致することを確認する。一致しなければこの実行で作成したキーだけを消す
4. 成功したときだけ `backend.yml` を `infisical` に書き換える

元の age / 平文ファイルは `backups/env-backend-migrate/<日時>/` へ退避され、自動では
削除されません。内容を確認してから消してください。

移すのはチーム単位の機密（共通とプロジェクト）だけです。個人単位の機密は
`devbase env set --user` で入れ直してください。

## 日々の操作

`list` / `get` / `set` / `delete` / `edit` はそのまま使えます。`devbase env list` の保存形式
表示には `[infisical]` が付きます。

### 個人単位の機密（`--user`）

`.env` には、サービスアカウントの鍵のように**チームで同じ値を使う機密**と、各自のクラウド
アクセスキーのように**利用者ごとに値が違う機密**が混ざります。1 台のサーバへ集約すると、
後者が他の利用者から見える状態を作らないための区別が要ります。

`--user` を付けると、持ち主が「個人」の置き場（`/users/<user>/...`）を相手にします。
`-p` が適用範囲（共通 / プロジェクト）を選ぶのに対し、`--user` は持ち主だけを選びます。

| 指定 | `set` / `delete` / `edit` の宛先 |
|---|---|
| なし | チーム共通（`/team/global`） |
| `-p` | チームのプロジェクト（`/team/projects/<name>`） |
| `--user` | 個人共通（`/users/<user>/global`） |
| `--user -p` | 個人のプロジェクト（`/users/<user>/projects/<name>`） |

`list` は指定の無い軸を絞らず、存在する参照をすべて出します。`get` は
個人共通 → チーム共通 → 個人のプロジェクト → チームのプロジェクト の順に探します。

`devbase up` でコンテナへ載る値は、次の順に重ねて決まります（後の層が勝つ）。

1. チーム共通の機密
2. 個人共通の機密
3. プロジェクトの非機密設定（`projects/<name>/env`）
4. プロジェクトのチーム機密
5. プロジェクトの個人機密

ファイル backend（設定なし / `age` / `plaintext`）は個人単位の置き場を持ちません。
`--user` を付けた書き込みは、チーム単位へ落とさずにエラーで止まります。

### 注意: コメントは残らない

Infisical にはコメント・空行の置き場がありません。`devbase env edit` で書いたコメントは
次に開いたときには消えています。

## サーバへ到達できないとき

取得できた機密は、参照ごとに age で暗号化して `secrets/cache/` に控えられます
（キーの取得元を表す指紋と一緒に 1 ファイルへ収めます）。サーバへ**通信できない**ときは
この控えでコンテナを起動し、最終取得時刻を警告として表示します。

| サーバとのやり取りの結果 | キャッシュ |
|---|---|
| 取得できた（0 件を含む）/ `set` `delete` などの書き込みが成功した | その内容で置き換える |
| 通信できない・応答を解釈できない | 残っている控えを**使う** |
| 認証を拒まれた（401 / 403） | 控えは残すが**使わず**、非ゼロで終了する |
| 書き込みが途中で失敗した | その参照の控えを消す |

認証拒否でキャッシュへ落ちないのは、失効させた client secret や外した権限が手元の起動を
止められなくなるためです。資格が戻れば、その後の不達では従来どおり控えが使われます。

接続先 URL・project・environment・識別子・client ID のいずれかを変えると、変更前の控えは
使われません。

キャッシュを持ちたくない場合は `--no-cache` を付けて `use` してください。無効にすると、
既存の控えも次の実行で消えます。

```bash
devbase env backend use infisical --no-cache
```

## 元へ戻す

```bash
devbase env backend migrate --to age
```

サーバ上の機密は**消しません**（他の利用者が参照している可能性があるため）。残っている
場所（接続先と `secretPath`）を表示するので、不要なら WebUI から消してください。手元の
キャッシュは消え、`bootstrap.env.age` は残ります（再び `infisical` に戻すときに資格情報を
入れ直さずに済みます）。

移行せずに切り替えだけを行うこともできます。

```bash
devbase env backend use age        # age ストアの内容がそのまま使われる
devbase env backend use auto       # 設定なしと同じ (ファイルの存在で判定)
```

## `secrets/backend.yml` の項目

```yaml
version: 1
backend: infisical            # auto / plaintext / age / infisical
infisical:
  url: https://infisical.example.com   # https のみ (http は localhost 宛てだけ可)
  project_id: 7f0e2c1a-...
  environment: common
  user: member01                       # 個人単位の置き場に使う識別子 (必須)
  path_team_global: /team/global
  path_team_project_prefix: /team/projects
  path_user_prefix: /users
  api_version: v4                      # v4 のみ
  timeout_seconds: 5
cache:
  enabled: true
```

対応していない値（`api_version: v3` など）は既定へ読み替えず、設定エラーとして止まります。

## 鍵の入れ替えと点検

- `devbase env rekey` は `backend: infisical` のときも実行でき、`bootstrap.env.age` と
  `secrets/cache/` 配下も再暗号化します
- `devbase env encrypt` / `decrypt` は age ストア専用です。`backend: infisical` のときは
  止まります（サーバとの間で移すには `migrate` を使います）。`backend: plaintext` のまま
  `encrypt`、`backend: age` のまま `decrypt` も止まります（変換後に設定が指す先から機密が
  消えるため。先に `backend use` で合わせるか `auto` に戻してください）
- `devbase env doctor` は backend 設定の読み込み、資格情報の有無、`backend.yml` /
  `bootstrap.env.age` / `cache/` の権限、Git の除外設定を点検します

## コマンド一覧

| コマンド | 内容 |
|---|---|
| `devbase env backend status` | 現在の backend、保存先、参照ごとの置き場、キャッシュの状態 |
| `devbase env backend use <name> [--url] [--project-id] [--environment] [--user ID] [--client-id] [--client-secret-stdin] [--no-cache]` | backend を切り替える。検証に失敗したときは書き換えない |
| `devbase env backend test` | サーバへ接続し、参照ごとに読めるかを確かめる |
| `devbase env backend migrate --to age\|infisical [--dry-run] [--yes]` | チーム単位の機密を別の backend へ写す |

関連: [環境変数の暗号化](env-encryption.md) / [環境変数ガイド](environment-variables.md)
