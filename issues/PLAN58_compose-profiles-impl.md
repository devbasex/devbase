# PLAN58: Compose の profiles で付随サービス群を後から起動・停止する — 実装計画

## 関連リンク

- 課題: devbasex/devbase#189
- 要求と受け入れ条件: [PLAN58_compose-profiles.md](PLAN58_compose-profiles.md)
- 設計: [PLAN58_compose-profiles-design.md](PLAN58_compose-profiles-design.md)
- 決定の記録: [PLAN58_compose-profiles-decisions.md](PLAN58_compose-profiles-decisions.md)
- 設計 Pull Request: devbasex/devbase#190（マージ済み・2026-09-17 承認）

## モード

`standard`。公開インタフェース（`devbase project profile` / `container profile`、TUI の操作メニュー、フックの環境変数）を追加し、`up` / `down` の本番の振る舞いを変える。

## 目的と非目的

達成したい状態は要求仕様の「目的」のとおりである。受け入れ条件・前提・対象範囲は要求仕様を唯一の置き場とし、ここへ写さない。

やらないこと（要求仕様の「含まない」に加えて、この計画で決めたもの）:

- `cmd_scale` が直接呼ぶ `compose up -d --no-recreate` の `env=`（要求仕様で範囲外）
- `docker_compose_down` の引数追加（決定 5）
- `bin/devbase` の `_PROJECT_NAME_SUBCOMMANDS` の変更（決定 6）

## 修正対象

| ファイル | 変更 |
| --- | --- |
| `lib/devbase/utils/docker.py` | `compose_env()` を新設。`docker_compose` が `env=` を渡す。`docker_compose_up` が `services` を受ける。`docker_compose_down` が `--profile '*'` を足す |
| `lib/devbase/commands/container.py` | `default_services` / `profile_services` / `_dev_instance_indices` / `cmd_profile_up` / `cmd_profile_down` / `cmd_profile_list` を新設。`_compose_run` / `_resolve_dev_service` / `_read_compose_services` が `env=` を渡す。`_run_deploy_pipeline` が既定のサービスを渡す。`_run_deploy_script_for_instances` が成否と `active_profiles` を持つ。`_dispatch_lifecycle` に `profile` |
| `lib/devbase/editor/opener.py` | `ps --format json` の呼び出しへ `env=` を渡す |
| `lib/devbase/project/runtime.py` | `hook_env(config, active_profiles=())` |
| `lib/devbase/cli.py` | `project profile` / `container profile` の subparser（`dest='profile_subcommand'`）、`SUBCMD_MAP` |
| `lib/devbase/tui/actions_project.py` | `_running_ops(devbase_root, name)` で 2 項目を出し分け、プロファイル名の選択と委譲 |
| `docs/plugin-dev/compose-profiles.md` | 新設。プロジェクト作者向けの書き方（`profiles:`、`required: false`、予約名 `__devbase_none__`、最低対応版 2.20.0） |
| `docs/plugin-dev/quickstart.md` | フックへ渡る環境変数の表へ `DEVBASE_ACTIVE_PROFILES` |
| `tests/utils/test_docker_profiles.py` | 新設 |
| `tests/commands/test_container_profile.py` | 新設 |
| `tests/cli/tui/test_profile_menu.py` | 新設 |
| `tests/volume/test_compose_profiles.py` | 新設 |
| `tests/commands/test_hook_env.py` | `DEVBASE_ACTIVE_PROFILES` の検査を追加 |
| 既存の `up` のテストの harness（`test_container_up_order.py` / `test_up_roundtrips.py` / `test_container_context.py` / `test_container_bao.py` / `tui/test_dispatch.py`） | `default_services` を差し替える（実 docker へ問い合わせないため） |

## タスク分解

機能単位で切る。各タスクは「失敗するテスト → 通す最小実装 → 整理」で進め、終わるたびに `uv run pytest tests/ -q` を通す。

### Task 1: devbase 経由の Compose へ打ち消し用のプロファイル名を渡す（決定 7 の 1）

- **対象:** `utils/docker.py`、`commands/container.py`（`_compose_run` / `_resolve_dev_service` / `_read_compose_services`）、`editor/opener.py`
- **変更:** `compose_env(environ=None) -> dict` を `utils/docker.py` に置き、`os.environ` の複製の `COMPOSE_PROFILES` を `__devbase_none__` にして返す。定数 `NO_PROFILE = '__devbase_none__'`。5 経路が `env=compose_env()` を渡す
- **満たす受け入れ条件:** 停止の網羅「`COMPOSE_PROFILES` が端末の環境変数に設定…」「`.env` に書かれた…」の自動検査の部分
- **テスト:** `COMPOSE_PROFILES=test` を `monkeypatch.setenv` で置き、各経路の `subprocess.run` に渡った `env['COMPOSE_PROFILES']` が打ち消し用の名前であること。他の環境変数が保たれること

### Task 2: プロファイルの解決（決定 1）

- **対象:** `commands/container.py`
- **変更:** `default_services(compose_file, environ=None) -> list[str]`（`config --services`）と `profile_services(compose_file, environ=None) -> dict[str, list[str]]`（`config --profiles` → 各 X で `--profile X config --services` から既定を差し引く）。どちらも `compose_env` を渡し、失敗は `DevbaseError` にする（呼び出し側で扱いを分ける）。並びは Compose の出力順を保つ
- **満たす受け入れ条件:** `profile list` の名前と対応、変数の式を含む `profiles` の名前一致（テスト設計の該当行）
- **テスト:** `subprocess.run` を偽物に差し替え、`config --profiles` / `config --services` の出力から対応が作られること。引数列に `-f <生成物>` と `--profile X` が subcommand より前に並ぶこと

### Task 3: `up` の起動は既定のサービスを明示し、停止は全プロファイルを対象にする（F4、決定 4・7 の 2）

- **対象:** `utils/docker.py`（`docker_compose_up` / `docker_compose_down`）、`commands/container.py`（`_run_deploy_pipeline`）、既存テストの harness
- **変更:** `docker_compose_up(compose_file, detach=True, services=())` が `['up', '-d', *services]` を組む。`docker_compose_down` は `['--profile', '*', 'down', '-t0']`。`_run_deploy_pipeline` は生成直後に `default_services(override_file)` を求めて渡す
- **満たす受け入れ条件:** 停止の網羅の 6 件（自動検査の部分）、退行しないこと
- **テスト:** `docker_compose_down` の引数列、`docker_compose_up` の引数列（空なら従来どおり `['up', '-d']`）、`_run_deploy_pipeline` が既定のサービスを `docker_compose_up` へ渡すこと、起動の引数列に `--profile` が入らないこと

### Task 4: フックへ有効なプロファイルを伝える（F5、決定 3）

- **対象:** `project/runtime.py`、`commands/container.py`（`_run_deploy_script_for_instances`）
- **変更:** `hook_env(config, active_profiles=())` が `DEVBASE_ACTIVE_PROFILES=','.join(active_profiles)` を足す。`_run_deploy_script_for_instances(..., active_profiles=()) -> bool`。`config` が無いときも `DEVBASE_ACTIVE_PROFILES` は渡す。`cmd_up` / `cmd_scale` は戻り値を使わない
- **満たす受け入れ条件:** フックの「`./pre-up` と `./deploy` は受け取る。`up` からは空」、「フックの失敗が終了コードへ出る」の関数側
- **テスト:** `tests/commands/test_hook_env.py` の既存の dump スクリプトで値を確かめる。失敗する `./deploy` で `False` が返ること

### Task 5: `cmd_profile_up` / `cmd_profile_down` / `cmd_profile_list`（F1〜F3、決定 2・5）

- **対象:** `commands/container.py`
- **変更:**
  - 共通の前段: `_prepare_compose(context)` → 生成物が無ければ `devbase up` を促して 1 → `profile_services` → 未知の名前なら既知の一覧を出して 1
  - `up`: `docker_compose(['--profile', X, 'up', '-d', '--no-deps', *services], check=False)`。0 以外ならその終了コードを返す。`./deploy` があれば `_dev_instance_indices(生成物)`（`get_dev_service_name()` の `<名前>-<数字>` を生成物の `services` から読む）へ `active_profiles=(X,)` で走らせ、1 つでも失敗すれば 1
  - `down`: `stop` → 失敗なら 1（`rm` を呼ばない）→ `rm -f`。フックは呼ばない
  - `list`: `ps --format json` を `compose_env` で呼び、`State == running` のサービスを数えて `PROFILE / SERVICES / RUNNING` の表を出す。`ps` が失敗したら RUNNING を `不明` にして 0。生成物が無ければ 1
  - 起動・停止の対象を `logger.info` で 1 行ずつ残す
- **満たす受け入れ条件:** 起動と停止の自動検査の部分すべて、フックの `profile up` 側（`DEVBASE_ACTIVE_PROFILES=X`、生成物の番号で全インスタンス、`DEV_SERVICE_NAME=workspace`、`./pre-up` を呼ばない、失敗で 0 以外）
- **テスト:** `tests/commands/test_container_profile.py`。偽の `subprocess.run` で引数列・呼び出し回数・終了コード・出力を検査する（テスト設計の表の該当行を 1 つずつ）

### Task 6: 引数の受け口（決定 6）

- **対象:** `cli.py`、`commands/container.py`（`_dispatch_lifecycle`）
- **変更:** `project profile {up,down} [name] <profile>` と `project profile list [name]`、`container profile {up,down} <profile>` / `list`。入れ子は `dest='profile_subcommand'`。`--context` も付ける。`_dispatch_lifecycle` の handlers へ `'profile'` を足し、`profile_subcommand` で振り分ける。`SUBCMD_MAP` の `project` / `container` へ `profile` を足す
- **満たす受け入れ条件:** `project profile up <プロジェクト> X` が現在地と同じ結果、`container` / `ct` が同じ結果で非推奨の警告 1 行、`project profile list` が `project list` へ流れない
- **テスト:** 既存の `tests/cli/test_project_dispatch.py` の書き方に合わせ、解析結果と呼ばれたハンドラの引数を検査する

### Task 7: 一覧の操作メニュー（F6、決定 8）

- **対象:** `tui/actions_project.py`
- **変更:** `_running_ops(devbase_root, name) -> list` を新設し、`projects/<name>/.docker-compose.scale.yml` があり `profile_services` が 1 件以上返すときだけ「テスト用サーバ起動 (profile up)」「テスト用サーバ停止 (profile down)」を足す。解決はそのプロジェクトのディレクトリを作業ディレクトリにして行い、失敗は「持たない」として扱う。選択後は `menu.select` でプロファイル名を選ばせ（1 件でも選択を出す）、`dispatch_lifecycle('profile', name, profile_subcommand=..., profile=...)` へ委譲する。2 項目は `_BACK_TO_TOP_OPS` に入れる
- **満たす受け入れ条件:** TUI の 6 件の自動検査の部分
- **テスト:** `tests/cli/tui/test_profile_menu.py`。`profile_services` を差し替えて項目の出し分け、委譲の属性、`back_after` を検査する。フォールバック（番号入力）は既存テストが通ること

### Task 8: 生成物が `profiles` と `depends_on.required` を保つことの固定

- **対象:** `tests/volume/test_compose_profiles.py`（実装の変更は想定しない）
- **満たす受け入れ条件:** 退行しないこと「生成物は `profiles:` を保つ」、テスト設計「生成物が `depends_on` の `required` を保つ」
- **進め方:** 現状固定テスト。失敗したときだけ `volume/compose.py` を直す

### Task 9: プロジェクト作者向けの文書

- **対象:** `docs/plugin-dev/compose-profiles.md`（新設）、`docs/plugin-dev/quickstart.md`、`docs/README.md` の索引
- **満たす受け入れ条件:** 対象範囲「プロファイルを使うプロジェクト作者向けの文書」
- **進め方:** テスト駆動は当たらない（文書）。コマンド例は Task 6 の実装と突き合わせる

### Task 10: 手動確認

- **対象:** 要求仕様「検証手段」の手動確認 7 行
- **進め方:** `alpine:3` の最小構成を scratchpad に作り、この作業ツリーの `bin/devbase` で通す。結果（Container ID / `StartedAt` の前後）は Pull Request 本文へ貼る。実 docker を使うため、自分のプロジェクトとは別の `COMPOSE_PROJECT_NAME` で行う

## 実装中に範囲へ入れたもの

- `tui/dispatch.py` の `_preserve_cwd_env()` が機密の注入履歴を戻さず、TUI で別プロジェクトを 2 回続けて操作すると最初のプロジェクト固有の機密が次の Compose へ渡る欠陥（PR 前からの `dispatch_lifecycle` 経路にもある）。Task 7 のメニュー表示時の照会が同じ欠陥を操作前に踏ませるため、原因と形が同じとして範囲に入れ、`runtime.snapshot_injected` / `restore_injected` で両経路を 1 か所で直した（PR #191 レビュー round 3）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `commands/container.py` は 1962 行あり、Task 1〜6 の多くが触る | 先に整える対象にはしない。追加は既存の関数の並び（ヘルパー → dispatch → `cmd_*`）へ足し、構造の見直しは構造改善（`cross-refactoring`）へ回す。タスクごとに全テストを通す |
| `_run_deploy_pipeline` が `config` を呼ぶため、既存テストの harness が実 docker へ問い合わせる | Task 3 で各 harness へ `default_services` の差し替えを足す。足し漏れは `docker` の無い環境で失敗として現れるため、差し替え前にテストを流して対象を洗い出す |
| TUI で行を選ぶたびに `docker compose config` が 2 回以上走る | 起動中の行を選んだときだけ呼ぶ。遅さが目立てば構造改善で対応を検討する |
| 生成物が `${VAR:?}` を含み、機密を注入しない TUI の解決で `config` が失敗する | 失敗は「プロファイルを持たない」として 2 項目を出さない。CLI の `profile list` では注入済みで呼ぶ |
| `--profile '*'` を古い Compose が解釈しない | 設計の「未確認のまま残ること」のまま。手動確認は v5.1.4 で行う |

## 切り戻し手順

データ移行は無い。Pull Request を revert すれば戻る。プロジェクト側の `compose.yml` に書いた `profiles:` は、revert 後の devbase では従来の Compose の挙動（`up` で起動しない・`down` で残る）に戻る。

## 完了の定義

- [ ] 要求仕様の受け入れ条件がすべて満たされ、条件ごとに自動テストか手動確認の結果が対応している
- [ ] `uv run pytest tests/ -q` と `uv run ruff check lib/ tests/` が通る
- [ ] 手動確認 7 行の結果を Pull Request 本文へ記録した
