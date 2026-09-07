# PLAN51: 機密ストアの保存先を差し替え可能にする — 設計

要求と受け入れ条件は [PLAN51_secret-backend-pluggable.md](PLAN51_secret-backend-pluggable.md) にある。
この文書は「どう作るか」だけを扱う。

## 構成要素

| 要素 | 責務 |
| --- | --- |
| `env/backend_config.py`（新規） | どの backend を使うかと、その非機密設定を `secrets/backend.yml` から読み書きする |
| `env/backends.py`（新規） | backend 名から実装を作る登録簿。未知の名前を、利用できる名前の一覧を添えて拒む |
| `env/infisical.py`（新規） | Infisical の REST を叩く `SecretBackend` 実装 |
| `env/bootstrap.py`（新規） | サーバ接続に使う機密を `secrets/bootstrap.env.age` から読む。**登録簿を経由しない** |
| `env/cache.py`（新規） | 取得できた機密を age で暗号化して控え、不達時に読み出す |
| `env/secret_store.py`（改修） | `backend_for()` が設定を見るようにする。設定が無ければ現行の自動判定のまま |
| `commands/env_backend.py`（新規） | `devbase env backend` の 4 サブコマンド |
| `commands/env_ops.py`（改修） | `doctor` に backend 設定・ブートストラップ・キャッシュの点検を足す |
| `cli.py`（改修） | サブコマンドの登録と、prefix 解決の優先指定 |

`PlaintextBackend` と `AgeBackend` は `secret_store.py` に置いたままにする。登録簿はそれらを
参照するだけで、移動しない。

**登録簿は `backend_for()` の中から遅延 import する。** `env/backends.py` は
`secret_store.py` の `SecretBackend` を参照し、`secret_store.py` は登録簿を参照するため、
モジュール先頭で import すると循環する。関数内 import は既存コードでも使っている書き方である
（`commands/env.py:46` ほか）。

```mermaid
flowchart LR
    CLI[devbase env / up] --> ST[SecretStore]
    ST --> CFG[backend_config]
    ST --> REG[backends 登録簿]
    REG --> PT[PlaintextBackend]
    REG --> AGE[AgeBackend]
    REG --> INF[InfisicalBackend]
    INF --> BS[bootstrap]
    INF --> CA[cache]
    BS --> AGEFILE[(secrets/bootstrap.env.age)]
    CA --> CAFILE[(secrets/cache/)]
```

## データ構造

### 1. backend の選択と非機密設定 — `$DEVBASE_ROOT/secrets/backend.yml`

```yaml
version: 1
backend: infisical
infisical:
  url: https://infisical.example.com
  project_id: 7f0e2c1a-....
  environment: shared
  path_global: /global
  path_project_prefix: /projects
  api_version: v4
  timeout_seconds: 5
  include_personal_overrides: false
cache:
  enabled: true
```

| キー | 意味 | 空・不在のとき |
| --- | --- | --- |
| `version` | この形式の版。将来の変更を検出するためだけに持つ | 不在なら `1` として読む |
| `backend` | 使う backend 名。`auto` / `plaintext` / `age` / `infisical` | **`auto`**（＝現行のファイル存在による判定） |
| `infisical.url` | 接続先。**スキームは `https` に限る**（`http` は明示的に拒む） | `backend: infisical` のとき必須。不在は設定エラー |
| `infisical.project_id` | Infisical の project の識別子 | 同上 |
| `infisical.environment` | environment の slug | 不在なら `shared` |
| `infisical.path_global` | 共通機密を置く `secretPath` | 不在なら `/global` |
| `infisical.path_project_prefix` | プロジェクト機密の親 `secretPath` | 不在なら `/projects` |
| `infisical.api_version` | 叩く API の版。`v4` / `v3` | 不在なら `v4` |
| `infisical.timeout_seconds` | 1 回の HTTP の待ち時間 | 不在なら `5` |
| `infisical.include_personal_overrides` | 読み取りに personal override を含めるか | 不在なら `false`（決定 6） |
| `cache.enabled` | キャッシュを書く・読むか | 不在なら `true` |

- **このファイルに機密は入らない。** 値が入るのはブートストラップ（次項）だけである
- 権限は `0600`。`secrets/` は既に `.gitignore` に載っているため、追加の除外設定は要らない
- 空の値は「未設定」であって「該当なし」ではない。上表の既定へ落ちる

### 2. ブートストラップ機密 — `$DEVBASE_ROOT/secrets/bootstrap.env.age`

`KEY=VALUE` 形式を age で暗号化したもの。既存の `AgeBackend` と同じ暗号化・同じ権限。

| キー | 意味 |
| --- | --- |
| `DEVBASE_INFISICAL_CLIENT_ID` | machine identity の client ID |
| `DEVBASE_INFISICAL_CLIENT_SECRET` | 同 client secret |

**このファイルだけは登録簿を経由せず、直接 `AgeBackend` で読む。** 有効な backend を通して
読もうとすると、「接続するための値を、接続しないと読めない」循環になる。鍵が無い端末では
`PlaintextBackend` へ落ちる（既存の自動判定と同じ規則）。

### 3. キャッシュ — `$DEVBASE_ROOT/secrets/cache/`

| パス | 中身 |
| --- | --- |
| `cache/global.env.age` | 共通機密の控え（age 暗号化） |
| `cache/projects/<name>.env.age` | プロジェクト機密の控え |
| `cache/index.json` | 参照ごとの `fetched_at` / `backend` / `url_host` |

`index.json` に**キー名も値も入れない**。何が保存されているかは暗号文の側にしか無い状態を保つ。

```json
{
  "version": 1,
  "entries": {
    "global": {"fetched_at": "2026-09-08T10:00:00+09:00", "backend": "infisical", "url_host": "infisical.example.com"},
    "project:carmo": {"fetched_at": "2026-09-08T10:00:00+09:00", "backend": "infisical", "url_host": "infisical.example.com"}
  }
}
```

**時系列の扱い: 上書きし、過去を残さない。** 理由は決定 5 にある。

**移行:** 既存データの変換は無い。`devbase env backend migrate` が明示的に呼ばれたときだけ、
age ストアの内容をサーバへ写す。写した後の age 側は `backups/env-backend-migrate/<日時>/` へ
退避し、自動削除しない（既存の `env encrypt` と同じ性質）。

**検査の手段:** `devbase env doctor` に次を足す。

- `secrets/backend.yml` が YAML として読め、`backend` が登録簿にある名前であること
- `backend.yml` / `bootstrap.env.age` / `cache/` 配下の権限が `0600`、ディレクトリが `0700` であること
- `backend: infisical` なのにブートストラップの 2 キーが揃っていない状態を指摘すること
- 上記 3 つのパスが `git check-ignore` で除外されること（既存の `_ignore_probe_paths` へ追加する）

### 4. Infisical 上での配置

| devbase の参照 | Infisical 側の位置 |
| --- | --- |
| 共通（`global`） | `projectId=<設定値>`, `environment=<設定値>`, `secretPath=<path_global>` |
| プロジェクト（`project:<name>`） | 同上、`secretPath=<path_project_prefix>/<name>` |

`<name>` は既存の `_validate_project_name()` を通したものだけを使う。パス区切りと `..` は
そこで拒まれるため、組み立てた `secretPath` が設定した親の外へ出ることはない。

## 入出力の契約

### 増えるコマンド

| 名前 | 入力 | 出力（成功） | 失敗の形 |
| --- | --- | --- | --- |
| `devbase env backend status` | なし | 現在の backend 名、保存先（パスまたは URL）、キャッシュの有無と最終取得時刻。終了コード 0 | 設定が壊れているとき、読めなかった箇所を述べて 1 |
| `devbase env backend use <name>` | `<name>`、`--url`、`--project-id`、`--environment`、`--client-id`、`--client-secret-stdin`、`--no-cache` | 書き込んだ設定の要約（**client secret は伏せる**）。終了コード 0 | 未知の名前なら利用できる名前の一覧を添えて 2。必須の設定が欠けていれば欠けた項目名を述べて 2。**いずれの場合も設定を書き換えない** |
| `devbase env backend test` | なし | 接続先 URL と、読めた参照の件数。終了コード 0 | 到達できない・認証できない・参照が読めない、のどれかを述べて 1 |
| `devbase env backend migrate --to <name>` | `--to`、`--dry-run`、`--yes` | 移す参照とキー**名**の一覧。`--dry-run` では書き込まない | 検証に失敗したら書いた分を消し、移行前の設定のまま 1 |

**client secret を引数で受け取らない。** `--client-secret-stdin` で標準入力から読むか、TTY では
`questionary` の伏せ字入力で尋ねる。`ps` から読める位置に置かないためである。

`--dry-run` の出力にキーの**値**を含めない。キー名だけを並べる。

### 既存コマンドの互換性

| コマンド | 扱い |
| --- | --- |
| `env list` / `get` / `set` / `delete` / `edit` / `project` / `sync` | 引数・出力形式ともに変えない。`SecretStore` 越しに動くため backend を問わない |
| `env encrypt` / `decrypt` / `rekey` | **age 固有のまま**。有効な backend が `age` / `auto` 以外のとき、age 専用である旨を述べて非ゼロ終了する |
| `env export` / `import` | バンドル形式を変えない。書き出しは有効な backend から読み、取り込みは有効な backend へ書く |
| `devbase up` ほかコンテナ操作 | 変えない。`runtime.resolve()` の入力が変わるだけで、compose へ渡すものは同じ |

`cli.py` の `SUBCMD_MAP` に `backend` を足す。`b` は他と衝突しないが、`SUBCMD_PREFIX_PREFERENCES`
は既存の指定を変えない。

### Infisical との契約（外部）

devbase 側では OpenAPI 記述を持たず、Infisical が公開しているものを正とする。使う経路は次の 5 つ。

| 用途 | 経路 | 送るもの |
| --- | --- | --- |
| 認証 | `POST /api/v1/auth/universal-auth/login` | `clientId`, `clientSecret` → `accessToken`, `expiresIn` |
| 一括取得 | `GET /api/v4/secrets` | `projectId`, `environment`, `secretPath`, `includePersonalOverrides` → `{secrets: [{secretKey, secretValue}]}` |
| 作成 | `POST /api/v4/secrets/{secretName}` | `projectId`, `environment`, `secretValue`, `secretPath` |
| 更新 | `PATCH /api/v4/secrets/{secretName}` | 同上 |
| 削除 | `DELETE /api/v4/secrets/{secretName}` | `projectId`, `environment`, `secretPath` |

認証以外はすべて `Authorization: Bearer <accessToken>` を付ける。access token は
プロセス内にだけ持ち、ディスクへ書かない。`expiresIn` を過ぎたら取り直す。

**参照ごとに 1 回、`GET /api/v4/secrets` を呼ぶ。** `devbase up` で読む参照は共通と
プロジェクトの 2 つなので、認証 1 回 + 取得 2 回の計 3 往復に収まる。`recursive` は使わない
（プロジェクトを 1 つ読むだけの場面で全プロジェクトの機密を受け取らないため）。

**検査の手段:** `http.server` で立てた偽サーバに対する結合テスト。実サーバに対する確認は
`carmo-cdk#312` の完了後に手動で行う。

## 処理の流れ

`devbase up` が機密を解決するまで。

```mermaid
flowchart TD
    A[devbase up] --> B[backend.yml を読む]
    B --> C{backend は}
    C -->|auto| D[ファイルの存在で判定<br/>現行どおり]
    C -->|age / plaintext| E[そのファイル backend]
    C -->|infisical| F[bootstrap.env.age から<br/>client id / secret を読む]
    F --> G[universal-auth で認証]
    G --> H{到達できたか}
    H -->|できた| I[GET /api/v4/secrets<br/>参照ごとに 1 回]
    I --> J[キャッシュを書き直す]
    H -->|できない| K{キャッシュはあるか}
    K -->|ある| L[キャッシュを復号<br/>警告と最終取得時刻を出す]
    K -->|ない| M[到達できない旨と URL<br/>非ゼロ終了]
    D --> N[runtime.resolve が合成]
    E --> N
    J --> N
    L --> N
    N --> O[compose へ変数名だけを渡す]
```

## 決定の記録

### 決定 1: backend の選択を設定ファイルへ置き、現行の自動判定は `auto` として残す

保存先がファイルでない backend は「ファイルの存在」では選べないため、選択を明示する場所が
要る。既定を `auto` にすることで、設定ファイルを持たない既存の端末は現行と同じ経路を通り、
挙動が変わらない（前提 3）。

「暗号化ファイルがあれば `age`、無ければ `plaintext`」という現行規則を単に拡張して
「サーバの設定があれば `infisical`」とする案は採らない。設定の有無で保存先が変わると、
接続設定を書いた時点で機密の読み先が黙って移り、移行の意思表示と区別がつかない。

### 決定 2: サーバ接続に使う機密を専用のファイルへ分ける

有効な backend を通してこれを読むと、「接続するための値を、接続しないと読めない」循環になる。
共通の機密（`global.env.age`）へ相乗りさせる案は、`migrate` でその中身がサーバへ移った時点で
手元から消え、次回以降つながらなくなるため採らない。

### 決定 3: HTTP は標準ライブラリで書く

`urllib.request` で足りる範囲の呼び出ししか行わない。`requests` や `httpx` を足すと、devbase を
入れるすべての端末に常時の依存が 1 つ増える（前提 2）。`infisical` CLI を必須にする案も、
Go のバイナリを各端末へ配る手間が同じ理由で釣り合わない。

### 決定 4: `SecretStore` の生成箇所 11 か所は変えない

`backend_for()` の内側で設定を見る形にすれば、呼び出し側は 1 か所も変えずに済む。
工場関数を新設して 11 か所を書き換える案は、差分が広がるわりに得るものが無く、
既存テストの書き換えも要る。

`store.age` / `store.plaintext` を名指しで触っている `env_migrate.py` と `env_ops.py` は、
age 固有の操作なのでそのまま残す（決定 8）。

### 決定 5: キャッシュは上書きし、過去を残さない

キャッシュは**サーバの内容の写し**であって記録ではない。誰がいつ何を変えたかは Infisical の
監査ログが持つ。履歴を手元に積むと、失効したはずの値が端末に残り続け、守りたいものと逆に
働く。

保持するのは最後に取得できた 1 世代だけとし、取得に失敗したときは書き換えない（前提 4）。
これにより「サーバが空を返した」ことでキャッシュが消える事故も起きない。

### 決定 6: 初版は shared の機密だけを読み書きする

`include_personal_overrides` は設定として持つが、既定は `false` にする。読みで personal を
含めて書きが shared へ行くと、`set` した値が `get` で返らない状態が起きる。

人ごとに値が違うものをどう表すかは `carmo-cdk#312` の「決めてほしいこと」に上げてあり、
そこが決まってから既定を見直す。

### 決定 7: 移行は片方向ずつ行い、読み戻して検証する

`env encrypt` が既に「書く → 読み戻して一致を確認する → 元を退避する」という順序を持っている。
機密を失う経路を 2 通り作らないため、同じ順序に揃える。

両方向を同時に同期する案は採らない。どちらが正かを devbase 側で判断できず、
`SecretStore.backend_for()` が暗号化と平文の同時存在を拒んでいるのと同じ理由で、
黙って一方を選ぶと事故になる。

### 決定 8: `encrypt` / `decrypt` / `rekey` は age 専用のまま残す

これらは age の受信者と鍵を操作するコマンドであり、サーバ backend には対応する概念が無い。
汎用の名前へ広げると、backend ごとに意味が違うコマンドになる。有効な backend が age 系で
ないときは、その旨を述べて止める。

## テスト設計

| 受け入れ条件 | 何で確かめるか |
| --- | --- |
| 未設定で `backend status` が `age` / `plaintext` を返す | `tests/commands/test_env_backend.py`（設定ファイル無しの単体） |
| `backend use infisical` で設定が保存され `status` が変わる | 同上（`tmp_path` の `DEVBASE_ROOT`） |
| `backend use age` で戻り、値が同じまま取れる | 同上 + `tests/env/test_secret_store.py` の往復 |
| `backend test` が到達可否で 0 / 非ゼロを返す | 偽サーバに対する結合テスト（`tests/env/test_infisical.py`） |
| 未知の backend 名を拒み、設定を書き換えない | `tests/commands/test_env_backend.py` |
| `list` / `set` / `get` / `delete` が backend を問わず同じ形で動く | 偽サーバに対する結合テスト。既存の `tests/commands/test_env_store_switch.py` に倣う |
| `-p` で書いた値が別プロジェクトから取れない | `secretPath` の組み立ての単体テスト + 結合テスト |
| `devbase up` の環境変数が age のときと同じ変数名で載る | `tests/env/test_runtime.py` に backend 差し替えの場合を追加 |
| `migrate --dry-run` が値を出さず書き込まない | 偽サーバの受信記録を検査する結合テスト |
| `migrate` が読み戻して一致しなければ巻き戻す | 偽サーバが不一致を返す場合の結合テスト |
| 移行失敗後も `get` が移行前と同じ値を返す | 同上 |
| 不達 + キャッシュあり → 起動して警告 | 偽サーバを落とした結合テスト |
| 不達 + キャッシュなし → 非ゼロ終了 | 同上 |
| 取得失敗でキャッシュが変化しない | `tests/env/test_cache.py`（ファイルのハッシュ比較） |
| キャッシュが age で暗号化されている | 同上（先頭が age のヘッダであること） |
| 値がログ・エラー・`--dry-run` に出ない | `caplog` と標準出力を走査する検査を上記各テストへ足す |
| トークンが argv に載らない | `backend use` の引数定義に client secret を取る位置引数・オプションが無いことの検査 |
| 既存利用者の挙動が変わらない | **既存テストを 1 行も書き換えずに通す**（`uv run pytest`） |
| `doctor` が backend 設定とキャッシュの権限を検出する | `tests/commands/test_env_ops.py` に追加 |
| 新しい設定・キャッシュが Git から除外される | `doctor` の `git check-ignore` 検査へ 3 パスを追加し、その検査自体をテストする |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 自己ホストの Infisical が `v4` を持つか | 公開ドキュメントの現行版は `/api/v4/secrets` を示すが、実際に立てる版がこれを持つかは構築後にしか確かめられない。`api_version` を設定で持たせ、`v3`（`/api/v3/secrets/raw`）へ落とせるようにして受ける |
| personal override の要否 | `carmo-cdk#312` の「決めてほしいこと」に上げてある。決まるまで既定は `false`（決定 6） |
| access token の有効期間 | `expiresIn` の実値がサーバ設定に依存する。過ぎたら取り直す実装にして、値そのものには依存しない |
| 同時実行 | 2 つの `devbase up` が同時にキャッシュを書く場合。既存の `write_secure_bytes_atomic` で置き換えるため壊れた中身は残らないが、どちらが残るかは決まらない。実害が無い（どちらもサーバの写し）と判断して扱わない |
| プロジェクト数が増えたときの往復 | 現状 20 個前後で、1 回の `up` が読むのは 2 参照のみ。全参照を一度に読む場面（`env list` の全体表示）だけ `recursive` を使うかは、実測してから決める |
