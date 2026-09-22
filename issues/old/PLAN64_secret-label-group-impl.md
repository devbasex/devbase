# PLAN64: 機密の参照の見出しにグループの読み替えを出す（実装）

## 関連リンク

- 課題: devbasex/devbase#188
- 要求と受け入れ条件: [PLAN64_secret-label-group.md](PLAN64_secret-label-group.md)
- 設計: [PLAN64_secret-label-group-design.md](PLAN64_secret-label-group-design.md)（設計 Pull Request #223）
- まとまり: release Pull Request #212（`release/v3.7.0`）

## モード

`standard`。利用者が読む公開の出力の振る舞いを変え、確定仕様の自己矛盾を解くため。

## 目的と非目的

達成したい状態:

- `group_aliases` のある端末で、見出しのグループ名（`default → nyle`）と隣のパス（`…/team/nyle/…`）が
  同じグループを指していると読める
- 参照の表示の文言を組み立てる場所が `SecretRef.label()` の 1 つに保たれる
- 確定仕様の 2 つの記述が、文言の種類で重ならない形に分かれる

やらないこと:

- エラー文言・警告・ログ・巻き戻しの説明に出る `label()` の表示（43 か所。引数なしのまま）
- `env backend status` の表示（すでに `display_group` を使っている）
- `env encrypt` / `env decrypt` / `env rekey` の一覧（`ref.group` が常に `None` で出力が変わらない）
- 一覧の桁幅（`:<24` / `:<28` / `:<40`）の変更
- `SecretRef` のフィールドと等価性、置き場のパス・サーバへの要求・キャッシュ

## 前提

- 前提 1: `version: 1` とファイル backend では `SecretRef.group` が常に `None`（`SecretStore.ref_group` の契約）
- 前提 2: `config.openbao` が `None` かどうかで backend の種類を判定してはならない。
  `backend: age` でも `openbao:` 節が残っていれば `None` にならない。判定は `SecretStore.storage_group` に任せる
- 前提 3: `OpenBaoSettings.display_group` は `storage_group` を経由し `BackendConfigError` を送出しうる。
  誤りを伝える文言の組み立ての中では呼ばない
- 前提 4: この端末の機密の置き場は本番の系である。確認はテストと読み取りに限り、書き込みを行わない

## 受け入れ条件

要求文書の 12 件をそのまま引き継ぐ。検証手段は設計の「テスト設計」の表に対応する。

- [ ] 1. `env backend test` の見出しが `グローバル（グループ default → nyle）` / `個人のグローバル（グループ default → nyle）`
      になり、隣のパスは今と同じ — 新規テスト
- [ ] 2. `env list` の節の見出しと件数の行（計 4 行）が読み替えの前後を出す — 新規テスト
- [ ] 3. `env backend migrate` の計画の一覧と `--to age` の完了後の一覧が、どちらも読み替えの前後を出す — 新規テスト 2 件
- [ ] 4. 読み替えの対応が無いグループ（`kkg`）では `（グループ kkg）` のままで `→` が出ない — 新規テスト
- [ ] 5. `version: 1`（`layout: flat`）で出力全体に `（グループ` が 1 つも出ない — 新規テスト。既存
      `tests/env/test_groups.py` / `tests/env/test_runtime.py` が変更なしで通る
- [ ] 6. `openbao:` 節を残した `backend: age` でも出力全体に `（グループ` が出ない — 新規テスト。既存
      `tests/commands/test_env_user_axis.py` が変更なしで通る
- [ ] 7. 引数なしの `label()` を通すエラー文言・警告に `→` が出ない — 新規テスト 1 件
- [ ] 8. `env backend status` の表示が今と同じ — 既存 `tests/commands/test_env_backend.py` が変更なしで通る
- [ ] 9・10・11. 確定仕様と利用者向け文書 — Pull Request のレビューで読んで確かめる
- [ ] 12. `uv run --locked pytest tests/ -q` が通り、結果を Pull Request 本文へ載せる

## 代替案と採否

設計の「決定の記録」が正本。ここでは採否だけを控える。

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | `label()` に `group_display` を足し、`SecretStore.display_label` を唯一の口にする | 採用 | 文言が 1 か所に残り、エラー文言が読み替えの解決を背負わない（決定 1・2・3） |
| B | `label()` の既定を読み替え後にする | 不採用 | 43 か所のエラー文言・警告・ログが `BackendConfigError` を背負う（決定 2） |
| C | 見出しの側で `f'（グループ {display}）'` を組み立てる | 不採用 | 同じ文言が 5 か所へ複製される（決定 1） |
| D | `config.openbao is None` で backend の種類を判定する | 不採用 | `backend: age` に節が残ると `None` にならない（決定 3、前提 2） |

## 修正対象

| ファイル | 変更 |
| --- | --- |
| `lib/devbase/env/secret_store.py` | `SecretRef.label()` に `group_display` を足す / `SecretStore.display_label` を足す |
| `lib/devbase/commands/env.py` | `cmd_env_list` の共通の節 / `_group_suffix`（`store` を受ける） |
| `lib/devbase/commands/env_backend.py` | `cmd_env_backend_test` / `cmd_env_backend_migrate` の完了表示 / `_MigrationPlan._heading` |
| `tests/env/test_secret_store_label.py` | 新設。`label()` の契約と `display_label` の分岐 |
| `tests/commands/test_env_group_label.py` | 新設。読み替えのあるグループでのコマンドの出力 |
| `docs/specifications/secret-backend.md` | 2 つの記述の書き分けと `list` の見出しの例 |
| `docs/user/env-backend.md` / `docs/user/cli-reference/03-env.md` | 見出しの例に読み替えのある場合を足す |
| `CHANGELOG.md` | Unreleased の Fixed |

## タスク分解

### Task 1: 表示の口を作る

- **対象ファイル:** `lib/devbase/env/secret_store.py`、`tests/env/test_secret_store_label.py`
- **変更内容:** `SecretRef.label(*, group_display=None)` と `SecretStore.display_label(ref)` を足す。
  `display_label` は `storage_group(ref.group)` が `None` か読み替え無しなら `ref.label()` を返し、
  読み替えがあるときだけ `display_group` の結果を渡す
- **満たす受け入れ条件:** 7、および 1・2・3・4・5・6 の土台
- **進め方:** `label(group_display=...)` と `display_label` の 3 分岐を先に失敗するテストで固定してから実装する

### Task 2: `env backend test` と `env list` の見出しを寄せる

- **対象ファイル:** `lib/devbase/commands/env_backend.py`、`lib/devbase/commands/env.py`、
  `tests/commands/test_env_group_label.py`
- **変更内容:** `cmd_env_backend_test` の参照ごとの行と `cmd_env_list` の共通の節を `store.display_label(ref)` に、
  `_group_suffix` を `store` を受ける形に変える
- **満たす受け入れ条件:** 1・2・4・5・6
- **進め方:** 読み替えのあるグループの出力を先に失敗するテストで固定してから差し替える

### Task 3: `env backend migrate` の 2 つの一覧を寄せる

- **対象ファイル:** `lib/devbase/commands/env_backend.py`、`tests/commands/test_env_group_label.py`
- **変更内容:** `_MigrationPlan._heading` を `self.server_store.display_label(...)` に、完了後の一覧を
  `server_store.display_label(...)` にする
- **満たす受け入れ条件:** 3
- **進め方:** 計画の一覧（`--dry-run`）と完了後の一覧（偽サーバ）を別々のテストで固定してから差し替える

### Task 4: 文書を直す

- **対象ファイル:** `docs/specifications/secret-backend.md`、`docs/user/env-backend.md`、
  `docs/user/cli-reference/03-env.md`、`CHANGELOG.md`
- **変更内容:** 確定仕様の 2 つの記述を文言の種類で分け、`list` の見出しの例に読み替えのある場合を足す。
  利用者向け文書の例にも足す。CHANGELOG の Unreleased の Fixed へ 1 行
- **満たす受け入れ条件:** 9・10・11
- **進め方:** テスト駆動を適用しない（文書のため）。レビューで読んで確かめる

## 影響範囲

`group_aliases` のある端末の 6 つの行の文字列だけが変わる。置き場のパス・サーバへの要求・キャッシュ・
`backend.yml` の内容は変わらない。読み替えの対応が無いグループ、`version: 1`、ファイル backend の
出力はバイト単位で今と同じになる。

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 既存テストが前方一致で見ており、見出しが変わっても通ってしまう | 「`（グループ` が 1 つも出ない」ことを見る出力テストを新しく置く（受け入れ条件 5・6） |
| `config.openbao` の `None` 判定に落ちる | 判定を `storage_group` の 1 か所に閉じ、`backend: age` に節を残した設定のテストで検査する |
| 実環境（本番の OpenBao）へ要求を出す | テストは隔離した `DEVBASE_ROOT` と偽の設定だけで行う。`env set` / `migrate` / backend の切り替えを実環境で行わない |
| `release/v3.7.0` を base にすると CI が動かない（#216） | 手元で `uv run --locked pytest tests/ -q` を走らせ、結果を Pull Request 本文へ載せる |

触る対象（`secret_store.py` の `label` と `SecretStore`、2 つのコマンド）は責務が分かれており、
先に構造を整える必要は無い。

## 切り戻し手順

コードの変更だけで、データの移行を伴わない。Pull Request を revert すれば元の文字列へ戻る。

## 完了の定義

- [ ] 受け入れ条件 1〜8 と 12 に、対応するテストの実行結果がある
- [ ] 9・10・11 は Pull Request のレビューで読んで確かめる
- [ ] `ruff check --select=E9,F63,F7,F82 lib` が通る
