# PLAN51: 機密ストアの保存先を差し替え可能にする — 実装計画

## 関連リンク

- 仕様: [PLAN51_secret-backend-pluggable.md](PLAN51_secret-backend-pluggable.md)
- 設計 1（構成要素とデータ構造）: [PLAN51_secret-backend-design.md](PLAN51_secret-backend-design.md)
- 設計 2（入出力の契約と処理の流れ）: [PLAN51_secret-backend-contract.md](PLAN51_secret-backend-contract.md)
- 設計 3（決定の記録とテスト設計）: [PLAN51_secret-backend-decisions.md](PLAN51_secret-backend-decisions.md)
- 設計 Pull Request: #159（2026-09-08 マージ済み）
- 発端: [security-key.md](security-key.md)

## モード

`standard` — 公開コマンド（`devbase env backend`、`--user`）を追加し、認証情報の取得経路を
変える。`lib/devbase/env/` と `lib/devbase/commands/` の複数モジュールにまたがり、既存テストが
十分にある。

## 目的と非目的

達成したい状態:

- 機密の保存先を `secrets/backend.yml` で明示的に選べ、Infisical サーバへ載せ替えられる
- backend を設定していない端末の挙動は 1 つも変わらない（既存テスト 1921 件が無改変で通る）
- サーバ不達時は age 暗号化キャッシュで起動でき、認証拒否時はキャッシュを使わず止まる

やらないこと（仕様の「含まない」と同じ）:

- Infisical サーバの構築（`carmo-cdk#312`）
- Vaultwarden / OpenBao / Bitwarden の adapter
- バンドル形式の変更、個人単位の機密の export / import
- 実サーバに対する手動確認（`carmo-cdk#312` の完了後、`release-verification` で行う）

## 前提

仕様の前提 1〜8 をそのまま引き継ぐ。実装で追加する前提は次の 1 つ。

- 前提 9: 偽サーバは標準ライブラリの `http.server` でテストプロセス内に立て、受信した
  リクエストを記録できる。（成否の判定: `tests/env/conftest.py` の fixture だけで結合テストが
  外部プロセス無しに通ること）

## 受け入れ条件

仕様の「受け入れ条件」の全項目（5 群 + 起きてはいけないこと）を対象とする。ここでは
タスクへ紐づけるために群ごとの番号を振る。

| 番号 | 群 | 仕様の節 |
| --- | --- | --- |
| AC-1 | backend の選択と切り替え | 「backend の選択と切り替え」8 項目 |
| AC-2 | 日々の操作が backend を問わず同じ形で動く | 同名の節 16 項目 |
| AC-3 | 個人単位とチーム単位の区別 | 同名の節 10 項目 |
| AC-4 | 移行 | 「移行」11 項目 |
| AC-5 | サーバ不達時と認証拒否時 | 同名の節 17 項目 |
| AC-6 | 起きてはいけないこと | 同名の節 7 項目 |

検証手段は設計 3 の「テスト設計」の表が受け入れ条件ごとに持つ。

## ドメイン用語

仕様の「用語」と設計 1 の「参照の形」に従う。実装での名前は次のとおり。

| 用語 | 実装での名前 |
| --- | --- |
| 参照 | `SecretRef(kind, name, owner)` |
| 持ち主 | `SecretRef.owner`（`'team'` / `'user'`） |
| backend の設定 | `BackendConfig`（`env/backend_config.py`） |
| ブートストラップ設定 | `env/bootstrap.py` が `secrets/bootstrap.env.age` から読む 2 キー |
| キャッシュ | `SecretCache`（`env/cache.py`） |
| 登録簿 | `env/backends.py` の `BACKEND_NAMES` / `create_backend()` |

## 不変条件

- 機密の値・client secret は、ログ・例外メッセージ・`--dry-run` 出力・`index.json` に載らない
- `backend.yml` に機密は入らない
- ブートストラップとキャッシュは age 暗号文としてしかディスクに置かれない
- backend が `auto` のとき、`SecretStore` の全メソッドの結果は変更前と同じ
- `SecretStore(...)` の生成箇所（11 か所）の呼び出し形は変えない
- `pyproject.toml` の `dependencies` は 4 件のまま

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `devbase env` サブコマンド | `backend` グループを追加、`list/get/set/delete/edit` に `--user` を追加 | 追加のみ。既存の引数・出力形式は変えない |
| `SecretRef` | 末尾に `owner: str = 'team'` を追加 | 既定値付きのため既存の生成・比較は変わらない |
| `SecretBackend` Protocol | `direct_edit: bool` を追加 | 既存 2 実装に属性を足す |
| データ | `secrets/backend.yml`、`secrets/bootstrap.env.age`、`secrets/cache/` が増える | 既存ファイルの変換は無し。`secrets/` は `.gitignore` 済み |
| バンドル形式 | 変えない | `manifest.yml` の `version` は 1 のまま |

## 修正対象

新規:

- `lib/devbase/env/backend_config.py`
- `lib/devbase/env/backends.py`
- `lib/devbase/env/bootstrap.py`
- `lib/devbase/env/cache.py`
- `lib/devbase/env/infisical.py`
- `lib/devbase/commands/env_backend.py`
- `tests/env/conftest.py`（偽 Infisical サーバ fixture）
- `tests/env/test_backend_config.py`、`test_bootstrap.py`、`test_cache.py`、`test_infisical.py`
- `tests/commands/test_env_backend.py`
- `docs/user/env-backend.md`

改修:

- `lib/devbase/env/secret_store.py`（`SecretRef.owner`、`direct_edit`、`backend_for()` の設定参照）
- `lib/devbase/env/secret_view.py`（`backup()` の分岐）
- `lib/devbase/env/runtime.py`（4 層の重ね順）
- `lib/devbase/env/io_import.py`、`_import_atomic.py`（backend 越しの適用と退避）
- `lib/devbase/commands/env.py`（`--user`、`_mode_suffix`、`direct_edit`）
- `lib/devbase/commands/env_ops.py`（`rekey` の対象、`doctor` の点検）
- `lib/devbase/commands/env_migrate.py`（`encrypt` / `decrypt` の backend 判定）
- `lib/devbase/cli.py`（サブコマンド登録、`--user`）
- `tests/env/test_runtime.py`、`tests/commands/test_env_store_switch.py`、`test_env_ops.py`、
  `tests/cli/test_env_import.py`、`test_env_export.py`（追加のみ、既存テストは書き換えない）
- `docs/README.md`、`CHANGELOG.md`

## タスク分解

各タスクは受け入れ条件を 1 群以上満たす単位で切る。`SecretStore` の内側から外側へ向かって
進め、各タスクの終わりで `uv run pytest` が全件通る状態を保つ。

### Task 1: 参照の持ち主と backend の選択を `SecretStore` の内側に通す

- **対象ファイル:** `env/secret_store.py`、`env/backend_config.py`（新規）、`env/backends.py`（新規）、
  `tests/env/test_backend_config.py`、`tests/env/test_secret_store.py`
- **変更内容:**
  - `SecretRef` に `owner` を足し、`label()` を持ち主で分ける。`for_global()` / `for_project()` に
    `owner=` キーワード引数を足す
  - `BackendConfig` が `secrets/backend.yml` を読み書きする。`https` 限定（ループバックだけ `http`）、
    `api_version` は `v4` のみ、`user` 必須と `..`・区切りの拒否、既定値の補完
  - 登録簿 `BACKEND_NAMES = ('auto', 'plaintext', 'age', 'infisical')` と `create_backend()`。
    未知の名前は一覧を添えて拒む
  - `SecretStore.backend_for()` / `mode()` / `exists()` / `path()` が設定を見る。`auto` は現行の
    自動判定。`direct_edit(ref)` を足す
  - `PlaintextBackend` / `AgeBackend` は `owner='user'` に対して `exists()` 偽・`load()` 空、
    書き込みは非ゼロ終了させる例外
- **満たす受け入れ条件:** AC-1 の未知の backend 名・`http` 制限・`api_version`・`user` の 4 項目、
  AC-3 のファイル backend で `--user` を拒む項目、AC-6 の既存挙動を変えない項目
- **進め方:** 失敗するテスト → 最小実装 → 整理。`backend.yml` が無いときの全経路を既存テストで
  担保する

### Task 2: `devbase env backend status` / `use` とブートストラップ

- **対象ファイル:** `env/bootstrap.py`（新規）、`commands/env_backend.py`（新規）、`cli.py`、
  `commands/env.py`（振り分け）、`tests/env/test_bootstrap.py`、`tests/commands/test_env_backend.py`
- **変更内容:**
  - `bootstrap.py` が `AgeBackend` を直接使って `secrets/bootstrap.env.age` を読み書きする。
    識別鍵が無ければ平文へ落とさず例外
  - `backend use <name>` が設定を検証してから書き、`--client-secret-stdin` または伏せ字入力で
    client secret を受ける。失敗時は設定を書き換えない
  - `backend status` が backend 名・保存先・4 参照の `secretPath`・`user`・キャッシュの状態を出す
  - `cli.py` の `SUBCMD_MAP` に `backend` を足し、`_NO_SECRET_INJECTION` に `('env', 'backend')` を
    足す（設定を触るコマンドで注入を試みない）
- **満たす受け入れ条件:** AC-1 の `status` / `use` の 4 項目、AC-3 の `status` 表示、AC-6 の
  鍵が無い端末・argv に client secret を載せない項目
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 3: Infisical adapter と `backend test`

- **対象ファイル:** `env/infisical.py`（新規）、`tests/env/conftest.py`（偽サーバ）、
  `tests/env/test_infisical.py`、`commands/env_backend.py`
- **変更内容:**
  - `InfisicalBackend`: `urllib.request` で `universal-auth/login`、`GET/POST/PATCH/DELETE
    /api/v4/secrets`。`expandSecretReferences=false`、`includePersonalOverrides=false` を常に送る
  - `secretPath` の組み立て（4 参照）。`save()` / `save_bytes()` は取得 → 差分 → DELETE/PATCH/POST、
    値が同じキーは送らない。途中失敗は反映済みキー名を述べて例外
  - 401 は access token を捨てて 1 度だけ再認証。再度 401 / 403 は `SecretAuthError`、
    接続不能・タイムアウト・5xx・解釈不能は `SecretUnreachableError`（キャッシュの判定に使う）
  - `backend test` が認証 + 参照ごとの取得を行い件数を出す
- **満たす受け入れ条件:** AC-1 の `backend test`、AC-2 の `set/get/delete`・参照記法・値の
  変わっていないキーを送らない・キーを消す操作で `DELETE` が届く項目、AC-3 の `secretPath` と
  他人の置き場を読めない項目、非機能条件の往復回数
- **進め方:** 偽サーバの受信記録に対する結合テスト → 最小実装 → 整理

### Task 4: 暗号化キャッシュと不達時のフォールバック

- **対象ファイル:** `env/cache.py`（新規）、`env/infisical.py`、`tests/env/test_cache.py`
- **変更内容:**
  - 参照ごとに 1 ファイル。`{"version", "scope", "backend", "fetched_at", "secrets"}` を age で
    暗号化し `write_secure_bytes_atomic` で置き換える。`index.json` は表示用
  - `scope` = SHA-256(url, project_id, environment, secretPath, client_id)
  - 取得成功（0 件を含む）と書き込み成功で置き換え、書き込み途中失敗と置き換え失敗で消す。
    通信失敗・解釈不能では残して使う。401 / 403 では残して使わない
  - `cache.enabled` が偽なら backend 解決時に `cache/` の控えを消し、消せなければ例外
  - `InfisicalBackend.load()` が `SecretUnreachableError` のときだけキャッシュへ落ち、警告と
    最終取得時刻を出す。キャッシュが無ければ URL を添えて例外
- **満たす受け入れ条件:** AC-5 の 17 項目、AC-3 の `user` を変えるとキャッシュが使われない項目
- **進め方:** 失敗するテスト → 最小実装 → 整理。中断・同時書き込みは `write_secure_bytes_atomic`
  を monkeypatch して再現する

### Task 5: `--user` と 4 層の重ね順、`edit` / `init --reset` の分岐

- **対象ファイル:** `env/runtime.py`、`commands/env.py`、`env/secret_view.py`、`cli.py`、
  `tests/env/test_runtime.py`、`tests/commands/test_env_store_switch.py`
- **変更内容:**
  - `runtime.resolve()` がチーム共通 → 個人共通 → 非機密設定 → チームのプロジェクト → 個人の
    プロジェクトの順に重ね、変数名は 4 層の順で畳む
  - `list` / `get` / `set` / `delete` / `edit` に `--user`。`list` は存在しない参照の節を出さない。
    `get` の探索順は個人共通 → チーム共通 → 個人のプロジェクト → チームのプロジェクト
  - `_mode_suffix()` が `mode()` を見る。`cmd_env_edit()` が `direct_edit` で分岐し、
    `_edit_encrypted()` を `_edit_via_tempfile()` へ改名
  - `SecretEnvFile.backup()` がサーバ backend では `load_bytes()` を age 暗号化して
    `backups/env-init/<日時>/` へ控え、作れなければ例外。`cmd_env_init()` は退避に失敗したら
    削除へ進まず非ゼロ
- **満たす受け入れ条件:** AC-2 の `list` の列構成・保存形式表示・`edit`・`init --reset`・`up` で
  同じ変数名・`-p` の分離、AC-3 の残り 8 項目
- **進め方:** 失敗するテスト → 最小実装 → 整理。既存の `test_env_ops.py` / `test_env_store_switch.py`
  は書き換えずに通す

### Task 6: `export` / `import` を backend 越しに通す

- **対象ファイル:** `env/io_import.py`、`env/_import_atomic.py`、`env/bundle.py`、
  `tests/cli/test_env_import.py`、`tests/cli/test_env_export.py`
- **変更内容:**
  - `_build_plans()` が参照ごとに `(SecretRef, bytes)` を持ち、適用は `store.save_bytes()`。
    ファイル backend では現行の複製と `os.replace()` を保つ（既存テストが `os.replace` を
    monkeypatch するため経路を残す）
  - サーバ backend の退避は取り込み前の値を age 暗号化して `backups/` へ控え、受信者鍵が
    無ければ 1 件も取り込まずに非ゼロ。巻き戻しは控えを `save_bytes()` で書き戻す
  - `export` はチーム単位の 2 参照だけを `store.exists()` / `load_bytes()` で読む（委譲により
    サーバ上の機密が収録される）
- **満たす受け入れ条件:** AC-2 の `export` / `import` の 3 項目、AC-3 の `export` / `import` が
  個人単位を触らない項目
- **進め方:** 偽サーバに対する結合テスト → 最小実装 → 整理。既存の import / export テストは
  書き換えない

### Task 7: `devbase env backend migrate`

- **対象ファイル:** `commands/env_backend.py`、`tests/commands/test_env_backend.py`、
  `tests/env/test_infisical.py`
- **変更内容:**
  - `--to infisical`: 移行先の同じ参照を読んで衝突を検査 → 移行先の内容へ移行元を重ねて
    `save()` → 読み戻して一致を確認 → 失敗ならこの実行で作成したキーだけ `DELETE` → 成功なら
    age ストアを `backups/env-backend-migrate/<日時>/` へ退避し `backend.yml` を書き換える
  - `--to age`: サーバ上の機密を読み、age ストアへ `save_bytes()` → 読み戻し → 成功なら
    `cache/` を消して `backend.yml` を書き換える。サーバ側は消さず、URL と `secretPath` を表示
  - `--dry-run` はキー名だけ表示、`--yes` で確認を飛ばす。移す対象はチーム単位の参照だけ
- **満たす受け入れ条件:** AC-4 の 11 項目
- **進め方:** 偽サーバに対する結合テスト → 最小実装 → 整理

### Task 8: `rekey` / `encrypt` / `decrypt` / `doctor` の backend 対応

- **対象ファイル:** `commands/env_ops.py`、`commands/env_migrate.py`、`tests/commands/test_env_ops.py`
- **変更内容:**
  - `_encrypted_refs()` を age 暗号文のパスの一覧へ広げ、`bootstrap.env.age` と `cache/` 配下を
    含める。表示・確認・差し替えは 1 つのまとまりのまま
  - `encrypt` / `decrypt` は backend が `age` / `auto` 以外なら age 専用である旨を述べて非ゼロ
  - `doctor` に backend 設定の読み込み・登録簿の名前・権限（`0600` / `0700`）・ブートストラップの
    2 キー・`user` の点検を足し、`_IGNORE_PROBE_PATHS` に 3 パスを足す
- **満たす受け入れ条件:** AC-6 の `doctor`・Git 除外・`rekey` の 3 項目、設計 2 の `encrypt` /
  `decrypt` の扱い
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 9: 利用者向けドキュメントと CHANGELOG

- **対象ファイル:** `docs/user/env-backend.md`（新規）、`docs/README.md`、`CHANGELOG.md`
- **変更内容:** backend の選び方、`backend.yml` の項目、ブートストラップの入れ方、`--user` の
  意味、キャッシュの挙動（不達と認証拒否の違い）、移行の手順、コメントが残らないことの注意
- **満たす受け入れ条件:** 対象範囲の「利用者向けドキュメント」
- **進め方:** テスト駆動を適用しない（文書）。`markdown-writing` に従う

## 影響範囲

- `SecretStore` を使うすべての経路（`up` / `env` 系 / export / import / doctor）。`auto` のときは
  結果が変わらないことを既存テストで担保する
- `cli.py` の prefix 解決。`b` は既存のサブコマンドと衝突しない

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `commands/env.py`（833 行）に `--user` の分岐を足すと `_target_env()` の責務が増える | タスクごとにテストを通す。持ち主の解決は `_target_env()` / `_project_env()` の引数に閉じ、分岐を 1 か所に置く |
| `io_import.py` の適用経路がファイル前提で、backend 越しにすると既存の `os.replace` テストが崩れる | ファイル backend の経路を残し、サーバ backend だけ別経路にする（設計 2 の表のとおり） |
| `SecretStore` に設定読みが入り、`backend.yml` が壊れていると全コマンドが止まる | 読み込み失敗は `SecretStoreError` として理由を述べる。`doctor` が先に検出する |
| 偽サーバのテストが並列実行でポート衝突する | `port=0` で OS に採番させる |
| 循環 import（`secret_store` ↔ `backends`） | 登録簿は `backend_for()` の中で遅延 import |

## 切り戻し手順

- `backend.yml` を消せば `auto` に戻り、現行と同じ経路になる。age ストアの内容は `migrate` の
  退避先にある
- `infisical → age` の移行はサーバ側を消さないため、`backend use infisical` で再び接続できる
- コードの切り戻しは PR の revert で足りる（既存データの変換は無い）

## 完了の定義

- [ ] 受け入れ条件（AC-1〜AC-6）をすべて満たし、設計 3 のテスト設計の各行に対応するテストがある
- [ ] 既存テスト 1921 件を 1 行も書き換えずに通す
- [ ] `pyproject.toml` の `dependencies` が 4 件のまま
- [ ] `cross-refactoring` → `cross-review` → `quality-gates` を通す
