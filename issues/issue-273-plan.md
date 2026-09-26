# #273 実装計画

設計は `issues/issue-273-design.md` と `issues/issue-273-design-decisions.md`。この文書は実装の順と区切りだけを扱う。
各区切りは「テストを先に足す → 実装 → `uv run --locked pytest` の該当分 → コミット」で進める。

## 区切り

| # | 区切り | 触るもの | 先に足すテスト |
| --- | --- | --- | --- |
| 1 | `env sync` の書き込み先（I2・I3・I10、#268） | `commands/env.py`（`SyncTargets`・`cmd_env_sync`・`_sync_source`・`_sync_gcp`・`_sync_host`・`_update_source_metadata`）、`cli.py`（`sync` の `--user` / `--group`）、`env/sources.py`（`check_changed` の docstring） | `tests/commands/test_env_sync_owner.py`（新設）、`test_env_user_axis.py`（`sync --user` を受け付ける組へ） |
| 2 | キーの行の取り出し（`collect_key_rows`） | `commands/env_rows.py`（新設） | `tests/commands/test_env_rows.py`（新設。偽の OpenBao の要求数・ファイルの backend） |
| 3 | `env backend use` の値で渡す `secret_id` | `commands/env_backend.py`（`_read_secret_id`・`_store_credentials`） | `tests/commands/test_env_backend.py` に追加 |
| 4 | 伏せ字の入力欄 | `tui/menu.py`（`secret`） | `tests/cli/tui/test_menu_pty.py` に追加（打った文字が画面に出ない・Esc で戻る） |
| 5 | キーの一覧の画面 | `tui/actions_env_keys.py`（新設） | `tests/cli/tui/test_actions_env_keys.py`（委譲の写し・検査・削除の確認・読めないとき・偽の OpenBao での一連） |
| 6 | 接続設定の画面 | `tui/actions_env_openbao.py`（新設） | `tests/cli/tui/test_actions_env_openbao.py`（案内だけ・`url` だけ・不正な `url`・`secret_id`・空の欄・確認の失敗） |
| 7 | env メニューへの 2 つの追加 | `tui/actions_env.py`（`_ENV_OPS`・`_OP_HANDLERS`・注記） | `tests/cli/tui/test_actions_env.py`（並びを 7 つに） |
| 8 | 文書 | `docs/user/cli-reference/02-project.md`・`03-env.md`・`docs/user/env-backend.md`・`docs/specifications/secret-backend.md` | —（文言を照合するテストは書かない） |

## 決めた細部（設計の範囲の中）

- `SyncTargets` は `commands/env.py` に置く。`set` は書いた参照を覚え、`save_dirty` は個人共通 → チーム共通の順に保存する。
  先の保存が失敗したら後を保存せず、例外をそのまま送る（`cmd_env_sync` が参照の表示と 1 行にして 1 を返す）
- `_sync_host` は `get` / `set` を持つものなら受ける（既存の `EnvFile` を渡すテストを保つ）
- `_update_source_metadata(devbase_root, env_file, *more)` は、渡した参照を個人共通 → チーム共通の順に見て、
  どれかにキーがあればソースを登録する。`init` の呼び出しは今のまま 1 つを渡す
- 控えに項目の無いソースの判定に使うキー名は、aws が `AWS_CONFIG_BASE64`、git が `GIT_CREDENTIALS_BASE64`、
  gcp が `GCP_CREDENTIALS_BASE64_<p>`（ファイルは `_update_source_metadata` と同じ引き方）
- 既存の `test_env_sync_group.py` の「要求したパスの集合」の照合は、`sync` が個人共通も読むようになるため、
  チーム単位のパスに絞って照合する形へ直す（宛先の変更は設計の決定 1・12 による）
- TUI の画面は `collect_args` の中の直線のコードで書き、Esc は `flow.BackOut` で 1 つ前へ戻す

## 確かめること

- `uv run --locked pytest tests/ -q`
- `python -m compileall -q lib bin`、`ruff check --select=E9,F63,F7,F82 lib`
