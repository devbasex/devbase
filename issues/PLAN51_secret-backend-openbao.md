# PLAN51: サーバ backend を Infisical から OpenBao へ組み替える（実装計画）

## 関連リンク

- 確定仕様（OpenBao 前提に書き直し済み。#170 でマージ）: `docs/specifications/secret-backend.md`
- 利用者向けガイド: `docs/user/env-backend.md`
- 設計の差分と開発モードのサーバでの確認結果: #166
- Infisical 版の実装 PR（この PR に置き換わる。出さない）: #167
- 範囲外: #169（base イメージに `bao` を入れる。別 PR）、#168（`devbase up` の 2 度注入）
- サーバ側の構築: carmo-cdk#312

## モード

`standard`。公開インタフェース（`backend.yml` のキー、`env backend use` の引数、ブートストラップの
キー名）と本番の振る舞い（サーバとの契約、キャッシュの規則）を変える。設計 PR（#170）は
マージ済み。

## 目的と非目的

達成したい状態:

- `backend: openbao` で、AppRole + KV v2 の OpenBao に対して `devbase up` / `env` の各コマンドが
  確定仕様どおりに動く
- #167 で作った仕組みのうち Infisical に依存しない部分（参照の持ち主、`SecretStore` の委譲、
  ブートストラップ、キャッシュ、4 層の合成、`migrate` / `import` / `export` / `rekey` /
  `doctor` の骨格）はそのまま残る
- Infisical の名前が本番コード・テスト・文書から消える（仕様の「採る理由」と CHANGELOG を除く）

やらないこと:

- `bao` CLI をイメージへ入れる（#169）
- `devbase up` の 2 度注入の解消（#168）
- サーバ側の構成（マウント・ポリシー・AppRole・token の期限）。devbase は既存のサーバへ接続するだけ
- Infisical との互換の維持。`backend: infisical` の設定は未知の backend 名として拒む

## 前提

- 前提 1: サーバは KV v2 を `<mount>` に持ち、`GET /v1/<mount>/data/<path>` が 200 で
  `data.data` / `data.metadata.version`、未作成で 404 を返す（#166 で確認済み）
- 前提 2: 保存の成功応答は `data.version` を返す（KV v2 API の仕様。偽サーバも同じ形を返す）
- 前提 3: 論理削除された最新版の 404 は本文に `data.metadata.version` を含む（#166 で確認済み）
- 前提 4: AppRole の token は実行時間より十分長い（サーバ側 1h）。実行中の失効は事前の
  取り直しだけで扱う

## 受け入れ条件

#166 の案を確定仕様に合わせて確定したもの。番号を Task から参照する。

- [ ] AC1 `backend: openbao` で `url` または `user` が無いと、欠けたキー名を述べて
      `BackendConfigError`（`test_backend_config.py`）
- [ ] AC2 `mount` / `user` のパス区切り・`..`、`/` で始まる・終わる `path_*`、`version` の
      不正値は既定へ読み替えず、キー名を添えて拒む（同上）
- [ ] AC3 `devbase up` がログイン 1 回と、4 参照それぞれの `GET /v1/<mount>/data/<path>` で
      機密を解決する。プロジェクト指定なしは取得 2 回（`test_openbao.py` / `test_runtime.py`）
- [ ] AC4 `--user` を付けた操作は `users/<user>/...` へ、付けない操作は `team/...` へ届く
      （`test_env_user_axis.py`）
- [ ] AC5 保存は参照ごとに 1 回の `POST` で、`options.cas` にその `SecretStore` で最後に読んだ
      版が入る。読んでいなければ直前の `GET` の版、404 なら `metadata.version` か 0
      （`test_openbao.py`）
- [ ] AC6 読んでから書くまでの間に他人が書いた（版が進んだ）とき、CAS 不一致の 400 で
      `SecretConflictError` になり非ゼロ終了し、その参照の控えが消える（同上 / `test_cache.py`）
- [ ] AC7 保存の成功後、同じ `SecretStore` で同じ参照をもう一度書くと、応答の `data.version` が
      CAS に使われる（`import` の巻き戻しが通る。`test_env_bundle_backend.py`）
- [ ] AC8 ログインの 400 / 403 で `SecretAuthError` になりキャッシュを読まず非ゼロ終了し、控えの
      中身は変わらない（`test_openbao.py` / `test_cache.py`）
- [ ] AC9 取得の 404 が機密 0 件として扱われ、キャッシュが空の世代で置き換わる（同上）
- [ ] AC10 取得の 403 は `SecretAuthError` で「読む権限が無い」と `user` の設定違いの可能性を
      述べ、書き込みの 403 は「書き込み権限が無い」と述べる。両者と資格の取り消しの文言が
      互いに異なる（`test_openbao.py`）
- [ ] AC11 結果不明の書き込み（送信後の接続断・タイムアウト・途中切れ・5xx・解釈できない応答）
      でその参照の控えが消え、拒否が確定した 4xx（403 と CAS の 400 を除く）では残る
      （`test_cache.py`）
- [ ] AC12 控えから読んだ参照への `save` は「到達できないため書き込めない」で拒まれる。
      `set` / `delete` / `edit` は不達なら控えへ落ちずに止まり、`edit` はエディタを開かない
      （`test_cache.py` / `test_env_user_axis.py`）
- [ ] AC13 `secret_id` が `env backend status` の出力にも例外メッセージにも現れず、argv で
      受け取る経路が無い（`test_env_backend.py`）
- [ ] AC14 `mount` または `role_id` を書き換えた後は、変更前に取得した控えが使われない
      （`test_cache.py`）
- [ ] AC15 `migrate --to openbao` で書き込み権限が無いときは手順 2 で「書き込み権限が無い」と
      述べて 1 で終了し、設定は移行前のまま（`test_env_backend_migrate.py`）
- [ ] AC16 応答の `data.data` に文字列以外の値があれば `SecretUnreachableError`（解釈できない
      応答）になる（`test_openbao.py`）
- [ ] AC17 `grep -rn -i infisical lib tests docs` が `docs/specifications/secret-backend.md` の
      「採る理由」の節と関連リンク以外に当たらない。全テストが通る
- [ ] 退行しない: `backend` が `auto` / `age` / `plaintext` の既存テストがすべて通る。
      `SecretRef` / `runtime.resolve` / `bundle` / `env_migrate` は触らない

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | `infisical.py` を `openbao.py` へ置き換え、`InfisicalSettings` → `OpenBaoSettings` へ改名する | 採用 | Infisical 版は出さない決定（B 案）。互換を残す理由が無く、名前を残すと読み手が 2 つの backend を探す |
| B | `infisical.py` を残して `openbao.py` を足し、登録簿に両方載せる | 不採用 | Infisical Community 版は要件を満たさないと確定している。載せると動かない選択肢を利用者へ見せる |
| C | 汎用の `kv_v2.py` にして OpenBao / Vault 共用にする | 不採用 | 契約は同じだが、devbase が前提にするのは #166 のサーバ構成であり、Vault での検証は無い。名前は検証した対象に合わせる |
| D | 書き込みコマンドの「控えへ落ちない読み出し」を `SecretStore.fetch()` を足して実現する | 採用 | `import` / `migrate` が既に backend の `fetch` を使っている。同じ経路を `SecretEnvFile` にも通せば、backend の `load` の意味を変えずに済む |
| E | 同上を、`load()` の中で「書き込み予定」フラグを見て切り替える | 不採用 | `load` の意味が呼び出し側の意図で変わり、テストで区別できない |

## ドメイン用語

| 用語 | 意味 |
| --- | --- |
| パス | KV v2 の中で 1 つの参照を指す相対パス（`team/global` など）。仕様の「secretPath」に相当 |
| 版 | KV v2 がパスごとに持つ書き込みの通し番号。`options.cas` に指定する |
| 基準 | その `SecretStore` で参照を最後に読んだ（または書いた）ときの内容と版。次の書き込みの CAS に使う |
| `role_id` / `secret_id` | AppRole の識別子と端末ごとの資格。旧 client ID / client secret |

## 不変条件

- 機密の値と `secret_id` はログ・例外・`status` の出力・`index.json` に載らない
- サーバ backend への 1 つの参照の書き込みは、丸ごと反映されるか何も反映されないかのどちらか
- 控えは読み取りにだけ使う。控えから読んだ参照は書き戻せない
- `backend: auto` のとき、`SecretStore` の全メソッドの結果は backend の仕組みが無いときと同じ

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `secrets/backend.yml` | `backend: infisical` と `infisical:` 節を廃止し `openbao:` 節を追加。`path_*` の既定を先頭 `/` なしへ | 破る。`infisical` は未知の名前として一覧を添えて拒む（未配布の機能。移行手順は不要） |
| `secrets/bootstrap.env.age` のキー | `DEVBASE_INFISICAL_CLIENT_ID` / `CLIENT_SECRET` → `DEVBASE_OPENBAO_ROLE_ID` / `SECRET_ID` | 破る。旧キーの暗号文は「キーが欠けている」として `BootstrapError`。`use openbao --role-id --secret-id-stdin` で入れ直す |
| `devbase env backend use` の引数 | `--project-id` / `--environment` / `--client-id` / `--client-secret-stdin` → `--mount` / `--role-id` / `--secret-id-stdin` | 破る（未配布） |
| `devbase env backend migrate --to` | `infisical` → `openbao` | 破る（未配布） |
| `secrets/cache/` の控え | `backend` の値が `openbao`、`scope` の材料が変わる | 旧控えは `backend` 不一致で使われない。消す必要は無い |
| 例外 | `SecretWriteError` を廃止し `SecretConflictError` を追加 | `applied` を読む呼び出しは無い（#167 で `io_import` は例外の型で分岐しない） |
| `SecretBackend` Protocol | `fetch(ref)` は既に OpenBao / Infisical 固有。`SecretStore.fetch(ref)` を追加（backend が持たなければ `load` へ委譲） | 追加のみ |

## 修正対象

- `lib/devbase/env/openbao.py`（新規。`infisical.py` を削除）
- `lib/devbase/env/backend_config.py`（`OpenBaoSettings`、`BACKEND_NAMES`、検証、`path()` 用の `<mount>/<パス>`）
- `lib/devbase/env/backends.py`（登録簿）
- `lib/devbase/env/bootstrap.py`（キー名と `Credentials` の項目名）
- `lib/devbase/env/cache.py`（`scope` の材料を backend から受け取る）
- `lib/devbase/env/secret_store.py`（`fetch` の委譲、docstring）
- `lib/devbase/env/secret_view.py`（`fresh` な読み出し）
- `lib/devbase/commands/env.py`（`_target_env` を `fresh` に、文言）
- `lib/devbase/commands/env_backend.py`（`status` / `use` / `test` / `migrate` の引数と表示、書き込み権限が無いときの扱い）
- `lib/devbase/commands/env_ops.py`（`doctor` の 2 キーと文言）
- `lib/devbase/cli.py`（`use` の引数、help）
- `tests/conftest.py`（`FakeInfisical` → `FakeOpenBao`）
- `tests/env/test_openbao.py`（新規。`test_infisical.py` を削除）
- `tests/env/test_backend_config.py`、`test_cache.py`、`test_secret_store_backend.py`、
  `tests/commands/test_env_backend.py`、`test_env_backend_migrate.py`、`test_env_ops_backend.py`、
  `test_env_user_axis.py`、`tests/cli/test_env_bundle_backend.py`
- `CHANGELOG.md`（Unreleased の Infisical を OpenBao へ）

## タスク分解

各 Task は「失敗するテスト → 通す最小実装 → 整理」で進める。Task 1〜3 は他の Task の土台で、
Task 3 が終わるまで既存のテストは Infisical の名前で落ちたままになる。**Task 3 の終わりで
全テストを通す。**

### Task 1: 設定を OpenBao の形にする

- **対象ファイル:** `backend_config.py`、`backends.py`、`tests/env/test_backend_config.py`、`tests/env/test_secret_store_backend.py`
- **変更内容:**
  - `InfisicalSettings` → `OpenBaoSettings(url, user, mount='devbase', path_team_global='team/global', path_team_project_prefix='team/projects', path_user_prefix='users', timeout_seconds=5)`。`project_id` / `environment` / `api_version` / `SUPPORTED_API_VERSIONS` を消す
  - 検証: `url` / `user` 必須、`mount` と `user` は `_validate_segment`、`path_*` は空でなく `/` で始まらず終わらず `..` を含まない
  - `secret_path(ref)` → `path_of(ref)`（相対パス）。表示用に `display_path(ref)` = `<mount>/<path>`
  - `BACKEND_NAMES = ('auto', 'plaintext', 'age', 'openbao')`、`BACKEND_OPENBAO`。`BackendConfig.infisical` → `openbao`、`_openbao_from_dict`
  - 登録簿は `openbao` → `OpenBaoBackend`
- **満たす受け入れ条件:** AC1、AC2
- **進め方:** `test_backend_config.py` を OpenBao のキーへ書き換え（`/` で始まる `path_*` と `mount` の `..` を拒むテストを足す）→ 実装 → 旧キーの残骸を消す

### Task 2: ブートストラップのキー名と `secret_id` の入力経路

- **対象ファイル:** `bootstrap.py`、`env_backend.py`（`_store_credentials`、`_read_client_secret`）、`cli.py`、`tests/env/test_bootstrap.py`、`tests/commands/test_env_backend.py`
- **変更内容:**
  - `ROLE_ID_KEY = 'DEVBASE_OPENBAO_ROLE_ID'`、`SECRET_ID_KEY = 'DEVBASE_OPENBAO_SECRET_ID'`。`Credentials(role_id, secret_id)`、`__repr__` で `secret_id` を伏せる
  - `use` の引数を `--url --mount --user --role-id --secret-id-stdin --no-cache` に。`_build_openbao_settings`。`use openbao` はブートストラップ → `backend.yml` の順
  - `status` は `mount` と 4 参照の `<mount>/<path>`、`role_id` を表示し `secret_id` を出さない
- **満たす受け入れ条件:** AC13（argv に `--secret-id` が無いこと、`status` に `secret_id` が出ないこと）
- **進め方:** `test_env_backend.py` の argv / 出力の断定を先に書き換える → 実装

### Task 3: OpenBao adapter（認証・取得・版付きの書き込み・失敗の分類）と偽サーバ

- **対象ファイル:** `openbao.py`（新規）、`infisical.py`（削除）、`tests/conftest.py`、`tests/env/test_openbao.py`（新規）、`tests/env/test_infisical.py`（削除）
- **変更内容:**
  - `OpenBaoBackend`: `login()` は `POST /v1/auth/approle/login`、`auth.client_token` と `auth.lease_duration`（正の数でなければ 3600）。`X-Vault-Token`。応答の状態による再認証は置かない
  - `fetch(ref)`: `GET /v1/<mount>/data/<path>`。200 → `data.data`（文字列以外の値は解釈できない応答）と `data.metadata.version`。404 → `{}` と `metadata.version`（無ければ 0）。403 → `SecretAuthError`（読む権限が無い / `user` の設定違い）。控え `_seen[ref] = (内容, 版)` を更新し、キャッシュへ書く
  - `load(ref)`: `_seen` → `fetch` → 不達なら控え。控えから読んだときは `_seen[ref] = (内容, None)`
  - `save(ref, data)`: 基準の版は `_seen[ref]`。無ければ `fetch`。`None`（控え由来）なら `SecretUnreachableError`（到達できないため書き込めない）。`POST` with `options.cas`。成功 → `_seen[ref] = (data, 応答の data.version)`、キャッシュを書いた内容で置き換え。応答に `data.version` が無ければ結果不明。400 で `errors` に `check-and-set` → `SecretConflictError` + 控えを消す。403 → `SecretAuthError`（書き込み権限が無い）。それ以外の 4xx → `SecretUnreachableError`（控えは残す）。接続断・タイムアウト・途中切れ・5xx・解釈できない応答 → `SecretUnreachableError` + 控えを消す
  - `remove(ref)`: `fetch` で存在を確かめ、`DELETE /v1/<mount>/metadata/<path>`
  - `probe(refs)`: `login` + 各 `fetch`
  - `SecretWriteError` を消し `SecretConflictError` を足す。`url_host` は残す
  - 偽サーバ `FakeOpenBao`: `role_id` / `secret_id` / `mount`、`secrets: {path: (data, version)}`、AppRole ログイン（`reject_login` は 400 `invalid role or secret`、`disable_entity` は 403）、`GET` は 404 + `metadata`（`soft_deleted` 用）、`POST` は `cas` を検査して不一致なら 400 `check-and-set parameter did not match the current version`、`DELETE metadata`。`fail_get_attempts` / `truncate_get_body` / `fail_write_attempts`（500）/ `drop_write_response`（送信後に切る）/ `readback_tamper` / `forbidden_prefixes`（403）/ `team_writable: bool`（偽なら `team/` への POST は 403）を移す。fixture 名は `openbao` / `openbao_root` / `configure_openbao`
- **満たす受け入れ条件:** AC3、AC5、AC6、AC7、AC8、AC9、AC10、AC11、AC16
- **進め方:** `test_infisical.py` の各テストを OpenBao の契約へ書き直して `test_openbao.py` にする（往復回数・符号化・403・不達・`backend test` は移し、差分適用のテストは CAS のテストに置き換える。読んだ版を基準にした書き込み / 読んでから書くまでの間の更新で 400 / 保存後の再書き込み / 結果不明の書き込みで控えが消える / 控えから読んだ参照の `save` 拒否を足す）→ `openbao.py` を書く → `infisical.py` を消す → **ここで全テストを実行し、Task 4 以降の対象以外がすべて通ることを確かめる**

### Task 4: キャッシュの `scope` と書き込み後の規則

- **対象ファイル:** `cache.py`、`openbao.py`、`tests/env/test_cache.py`
- **変更内容:**
  - `scope_of(url, mount, path, role_id)`。`SecretCache._scope` は `backend.cache_scope(ref)` を呼ぶ（backend が材料を持つ。cache が設定の形を知らない）
  - `test_cache.py` を OpenBao の fixture へ移し、AC11 / AC12 / AC14 のテストを足す
- **満たす受け入れ条件:** AC11、AC12（backend 側）、AC14
- **進め方:** テストを先に書き換える → 実装

### Task 5: 書き込みコマンドは控えへ落ちない

- **対象ファイル:** `secret_store.py`、`secret_view.py`、`commands/env.py`、`tests/commands/test_env_user_axis.py`
- **変更内容:**
  - `SecretStore.fetch(ref)`: backend が `fetch` を持てばそれ、無ければ `load`。`fetch_bytes` も同様
  - `SecretEnvFile(store, ref, fresh=False)`: `fresh` なら `load` / `load_bytes` が `store.fetch` を使う
  - `_target_env`（`set` / `delete` / `edit` の入口）は `fresh=True` で作る。`list` / `get` / `up` は従来どおり
  - `_target_env` のエラー文言の `use infisical` → `use openbao`
- **満たす受け入れ条件:** AC4、AC12（コマンド側: 不達で `set` / `delete` / `edit` が止まり、`edit` がエディタを開かない）
- **進め方:** `test_env_user_axis.py` に「サーバ停止中の `edit` はエディタを起動しない」「`set` は控えがあっても止まる」を足す → 実装

### Task 6: `env backend` の `test` / `migrate` と `doctor`

- **対象ファイル:** `env_backend.py`、`env_ops.py`、`cli.py`、`tests/commands/test_env_backend_migrate.py`、`tests/commands/test_env_ops_backend.py`
- **変更内容:**
  - `test`: `OpenBaoBackend` を前提に、表示を `<mount>/<path>` へ
  - `migrate`: `MIGRATE_TARGETS = ('age', 'openbao')`、`--to openbao`。`SecretAuthError`（書き込み権限が無い）は手順 2 で 1 で終了し設定を変えない。`--to age` の「残っている場所」を `<mount>/<path>` へ。文言の `infisical` を消す
  - `doctor`: `BACKEND_OPENBAO` で `ROLE_ID_KEY` / `SECRET_ID_KEY` を点検。文言
  - `cli.py`: help の文言
- **満たす受け入れ条件:** AC15
- **進め方:** `test_env_backend_migrate.py` に「`team_writable=False` で 1 と設定不変」を足し、fixture を移す → 実装

### Task 7: `import` / `export` と残りのテスト、CHANGELOG

- **対象ファイル:** `tests/cli/test_env_bundle_backend.py`、`CHANGELOG.md`、`docs/user/cli-reference/03-env.md`（`use` の引数が変わっていれば）
- **変更内容:**
  - `test_env_bundle_backend.py` を OpenBao の fixture へ移し、巻き戻しが「保存後の版」で通ることを確かめる（AC7）
  - CHANGELOG の Unreleased を OpenBao の記述へ。`encrypt` / `decrypt` の行も
- **満たす受け入れ条件:** AC7、AC17
- **進め方:** テストを移す → 通す → `grep -rn -i infisical lib tests docs CHANGELOG.md` で残骸を確かめる

## 影響範囲

- `devbase up` の機密の解決（`runtime.resolve` は触らないが、backend の契約が変わる）
- `devbase env` の全サブコマンド（`set` / `delete` / `edit` の読み出し経路が `fetch` になる）
- `devbase env backend` の 4 コマンド、`env doctor` / `rekey`
- `secrets/backend.yml` / `bootstrap.env.age` / `cache/` の形式

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `infisical.py`（366 行）を丸ごと書き直す間、既存の 9 本のテストファイルが名前の変更で一斉に落ちる | Task 1〜3 を 1 つの区切りとして進め、Task 3 の終わりで全テストを通す。Task 4 以降は 1 Task ごとにテストを通す |
| 偽サーバの CAS の実装が本物と食い違う（400 の本文、404 の `metadata` の形） | #166 で本物に対して確かめた応答の形をそのまま偽サーバに写す。本物との突き合わせは release-verification（実サーバ）で行う |
| 「控えから読んだ参照は書き戻さない」の実現に `SecretEnvFile` / `_target_env` を触り、`init` / `sync` / `project` の経路まで変える | `fresh` の既定は偽で、`_target_env`（`set` / `delete` / `edit`）だけが真にする。他の経路は従来どおり |
| `cache.py` が `backend.settings.project_id` 等を直接読んでいる結合 | `backend.cache_scope(ref)` へ寄せ、cache は設定の形を知らない構造にする（実装の後の構造改善で足りる範囲） |

## 切り戻し手順

- 実装 PR のマージ前: ブランチを捨てるだけ。`feat/secret-backend-pluggable`（#167）は open のまま
- マージ後、配布前: revert。未配布の機能なので利用者への影響は無い
- 配布後: 設定を持たない端末は影響を受けない（`auto` の挙動は不変）。`backend: openbao` の端末は
  `devbase env backend migrate --to age` で戻せる

## 完了の定義

- [ ] AC1〜AC17 をすべて満たし、条件ごとに検証手段（テスト名またはコマンド）と結果が対応している
- [ ] `uv run pytest`（全体）、`python -m compileall -q lib bin`、`ruff check --select=E9,F63,F7,F82 lib` が exit=0
- [ ] cross-refactoring（差分が大きければ）→ cross-review 収束 → quality-gates → PR（`main` ← `feat/secret-backend-openbao`）
- [ ] plan-to-spec でこの計画の内容が `docs/specifications/secret-backend.md` に反映され、この計画は削除される
- [ ] #167 を新 PR を指すコメント付きで close
