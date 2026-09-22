# PLAN65 実装 1 本目: Compose の呼び出しを共通経路へ寄せる

## 関連リンク

- 課題: devbasex/devbase#192
- 要求と受け入れ条件: `issues/PLAN65_scale-compose-path.md`
- 設計: `issues/PLAN65_scale-compose-path-design.md`（設計 PR #225。案 A を承認済み）
- release PR: #212（base は `release/v3.7.0`）
- この計画が扱うのは設計の「実装の分け方」の **1 本目だけ**である。2 本目（`cmd_scale` の段階を分ける）は
  この Pull Request のマージ後に別に出す

## モード

`standard`（`devbase scale` の子プロセスの環境と起動の対象が変わり、確定仕様も変える。要求の文書の判定のまま）。

## 目的と非目的

達成したい状態:

- `lib/` の中で `compose_env()` を通さずに `docker compose` を起動する箇所が 0 件になる
  （`cmd_scale` の `[4/5]` と `cmd_login` の 2 か所を塞ぐ）
- `devbase scale` の起動の対象が、`devbase up` と同じく `default_services(<生成物>)` で明示される
- `docker compose config --format json` を起動する関数が `_compose_config_services` の 1 つになる
- 確定仕様 `docs/specifications/compose-profiles.md` の経路の一覧が実装と一致し、
  「devbase 経由の操作には効かない」が例外を持たない
- `devbase scale` の正常系の手順（順序と範囲）がテストで固定される

やらないこと:

- `cmd_scale` の段階の抽出（`_check_scale_request` / `_run_scale_pipeline` の新設）。2 本目の範囲で、
  受け入れ条件 D-3・D-4 はこの Pull Request では満たさない
- `docker_compose_up()` / `docker_compose_down()` / `compose_env()` のシグネチャの変更（設計の決定 3）
- `cmd_scale` のログの文言・段階の番号・失敗の扱いの変更（決定 5・7）
- `_previous_scale_compose()` を `cmd_scale` へ入れること（決定 6）
- `docs/plugin-dev/compose-profiles.md` の変更（A-4）
- `cmd_scale` から `_report_missing_repos` / `_apply_window_titles` を呼ぶこと（#224 として起票済み）

## 前提

- 前提 1: 実環境のプロジェクトで `devbase up` / `scale` / `login` を実行しない。確認はコマンド列を組み立てる
  水準と `subprocess.run` を差し替えた水準で行う
- 前提 2: `release/v3.7.0` を base にした Pull Request では CI が動かない（#216）。`uv run pytest` を手元で実行し、
  結果と終了コードを Pull Request 本文の Test plan へ載せる
- 前提 3: `tests/conftest.py` の autouse fixture（#217）がテストごとに `DEVBASE_ROOT` を tmp へ向ける。
  新しいテストもこれに乗る
- 前提 4: 変更前の全件の結果は `2863 passed`（`uv run pytest -q`、exit=0、2026-09-22、`b112584` + 空コミット）

## 受け入れ条件（この Pull Request で満たすもの）

設計文書の「受け入れ条件とどちらの Pull Request が対応するか」の表から、1 本目で満たすものを写す。
番号は要求の文書のもの。

- [ ] A-1: `compose_env()` を渡さない起動が 0 件。`grep -rn "'docker', 'compose'\|\"docker\", \"compose\"" lib/` が 6 → 3 件
  （`utils/docker.py` の共通経路・`_compose_base_args`・`editor/opener.py`）。`cmd_login` は `_compose_base_args` 経由のため目視で辿る
- [ ] A-2: 確定仕様に `cmd_scale` を共通経路の対象から外す記述が無い。経路の表に `scale` の起動が載る
- [ ] A-3: 確定仕様の「組み立てるコマンド列」の表に `devbase scale` の行（`up -d --no-recreate <既定のサービス...>`、`__devbase_none__`）がある
- [ ] A-4: `docs/plugin-dev/compose-profiles.md` を変えない（`git diff --name-only` に出ない）
- [ ] A-5: `tests/utils/test_docker_profiles.py` の棚卸しのコメントと、そこに並ぶテストが変更後の経路
  （`_compose_run` / `_compose_lines` / `cmd_login` / `editor._query_container_name`）と一致する
- [ ] B-1: `COMPOSE_PROFILES=web` のうえで `cmd_scale(2)` の起動の子プロセスの `COMPOSE_PROFILES` が `__devbase_none__`
- [ ] B-2: `cmd_scale` の前後で `os.environ['COMPOSE_PROFILES']` が `web` のまま
- [ ] B-3: 起動のコマンド列が `['docker', 'compose', '-f', <生成物>, 'up', '-d', '--no-recreate', <default_services の全件>]`
- [ ] B-4: 起動が非 0 なら `Failed to start new containers` を出して 1。例外は外へ出ない
- [ ] B-5: プロファイルを持たない生成物では、起動の対象が `config --services` の全件（サービス名を付けない `up` と同じ集合）
- [ ] C-1: 正常系の順序 `group → write_scale → volumes → network → generate → default_services → up → wait → bao → deploy` を固定するテストがある。`grep -rn "no-recreate" tests/` が 1 件以上
- [ ] C-2: `_push_bao_token` の `start` が `current + 1`、`./deploy` の範囲が `range(current + 1, new + 1)`
- [ ] C-3: 既存の `cmd_scale` のテスト 4 か所を書き換えずに通す
- [ ] C-4: `uv run pytest` の全件が変更の前後で同じ結果（新設分だけ件数が増え、失敗 0）
- [ ] D-1: `grep -rn "'config', '--format', 'json'" lib/` が 1 件
- [ ] D-2: `_compose_config_services` は非 0 で `(rc, {})`、不正 JSON で `json.JSONDecodeError`、正常で `(0, services)`。
  `_resolve_dev_service` は非 0 と不正 JSON で `None`
- [ ] E-1: `tests/commands/test_container_up_order.py` を書き換えずに通す
- [ ] E-2: `profiles:` を持つサービスが `scale` の起動のコマンド列に出ない
- [ ] E-3: `scale` の呼び出しに停止（`down` / `stop` / `rm`）が 1 件も無い

## 代替案と採否

設計で決めたもの（決定 1〜4・9）はここで再検討しない。計画で決めたのはコミットの並べ方だけである。

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 仕様 → 共通経路へ寄せる（新しい振る舞いのテストを先に書いて落とす）→ 現状固定テスト | **採用** | 設計文書の「#192 が指定した順序」の表のとおり。#192 の順序（仕様 → 共通経路 → 現状固定テスト）とも一致する |
| B | 現状固定テストを先に書いて緑を確かめ、そこから寄せる | 不採用 | 設計文書が「3 を 2 より先に置かない理由」で退けている。寄せる変更は `[4/5]` のコマンド列と `env` を**意図して**変えるため、現状（`env` 無し・サービス名無し）を先に固定すると 2 でそのテストを書き換えることになる。承認した設計から外れる |

案 A の中での「先に失敗するテスト」の置き方:

- 共通経路へ寄せるコミットでは、**新しい振る舞い**（B-1〜B-4、D-1/D-2、`cmd_login` の `env`）を表すテストを先に書き、
  変更前の実装で落ちることを確かめてから寄せる（`tdd-cycle`）
- 現状固定テスト（C-1・C-2・E-3）は寄せた後の実装に対して書き、書いた時点で緑であることを確かめる。
  これが 2 本目（構造の変更）の前の安全網になる。2 本目の構造変更の前に緑で入っている、という設計の要件を満たす
- `[4/5]` 以外の手順（順序・範囲）は寄せるコミットで変わらないため、C-1 の順序のうち `default_services` の位置だけが
  寄せるコミットで新しく生まれる。これは B-3 のテストで先に固定する

## 不変条件

- 呼び出し側の `os.environ` は書き換えない（`compose_env()` は複製を返す）
- `cmd_scale` のログの文字列（`[1/5]`〜`[5/5]`、`[2.5/5]`、`Using --no-recreate ...`、`Failed to start new containers`、
  `Scale failed: %s`、`=== Scale completed successfully ===`）は変えない
- `cmd_scale` は既存のコンテナを止めない（停止の段を持たない）

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| CLI（`devbase scale` / `devbase login`） | 引数は変えない | 変えない |
| `devbase scale` の起動 | 子プロセスの `COMPOSE_PROFILES` が常に `__devbase_none__`、対象が既定のサービス | **振る舞いが変わる**（承認済み）。端末や `.env` に `COMPOSE_PROFILES` を置いた人の `scale` はプロファイルのサービスを起動しなくなる。プロファイルを持たないプロジェクトでは対象の集合は変わらない |
| `devbase login` | `exec` の子プロセスの `COMPOSE_PROFILES` | 観測できる振る舞いは変わらない（開発サービスは `profiles:` を持たない） |
| モジュール関数 | `_read_compose_services` を削除し `_compose_config_services` を新設 | 呼び出し元は `_ensure_images` の 1 つ。`_resolve_dev_service` の名前と契約は保つ |

## 修正対象

- `docs/specifications/compose-profiles.md`
- `lib/devbase/commands/container.py`（`cmd_scale` の `[4/5]` / `cmd_login` / `_resolve_dev_service` / `_read_compose_services` → `_compose_config_services` / `_ensure_images`）
- `tests/commands/test_container_scale_order.py`（新設）
- `tests/utils/test_docker_profiles.py`
- `issues/PLAN65_scale-compose-path-impl1.md`（この計画）

## タスク分解

コミットは設計の順序表に合わせて 4 つにする（決定 2 のとおり `cmd_login` は独立したコミット）。

### Task 1: 確定仕様を書き換える（1 つ目のコミット）

- **対象ファイル:** `docs/specifications/compose-profiles.md`
- **変更内容:** 設計の「確定仕様の書き換え」の表の 7 か所。構成要素の表に `scale` の起動、経路の表を 5 行
  （`docker_compose` / `_compose_lines` / `_compose_run` / `cmd_login` / `_query_container_name`）に畳み、
  `cmd_scale` の除外を「経路はこの表の 5 つだけ」に置き換え、コマンド列の表に `scale` と `login` の行、
  `up`/`down` の節に `scale` の段落、運用に「起動の対象にも入れない。動いているプロファイルのサービスは止めない」、
  テスト観点に `scale` の行
- **満たす受け入れ条件:** A-2 / A-3 / A-4
- **進め方:** ドキュメントのためテスト駆動は適用しない。`grep -n "cmd_scale"` と目視で確かめる

### Task 2: `cmd_scale` の起動と config の読み取りを共通経路へ寄せる（2 つ目のコミット）

- **対象ファイル:** `lib/devbase/commands/container.py`、`tests/commands/test_container_scale_order.py`（新設）、`tests/utils/test_docker_profiles.py`
- **変更内容:**
  - `[4/5]` を `docker_compose(['up', '-d', '--no-recreate', *services], compose_file=override_file, check=False)` にする。
    `services = default_services(override_file)` は `[3/5]` の生成の直後に求める（番号は付けない。決定 7）
  - `_compose_config_services()` を新設し `docker_compose(['config', '--format', 'json'], check=False, capture_output=True)` を通す。
    `_read_compose_services` を削除し、`_ensure_images` と `_resolve_dev_service` をその上に載せる
  - 棚卸しの節のうち config の 2 経路のテストを `docker_compose` を通る経路として書き直す
- **満たす受け入れ条件:** A-1（`cmd_scale` 分）/ B-1〜B-5 / D-1 / D-2 / E-2
- **進め方:** 失敗するテスト（B-1〜B-5・E-2・D-2 の `_compose_config_services`）を先に書き、変更前の実装で落ちることを確かめる →
  寄せる → 全件

### Task 3: `cmd_login` に `compose_env()` を渡す（3 つ目のコミット）

- **対象ファイル:** `lib/devbase/commands/container.py`、`tests/utils/test_docker_profiles.py`
- **変更内容:** `subprocess.run(cmd, env=compose_env())` の 1 行。棚卸しのコメントを「`docker_compose` を通らずに直接呼ぶ経路 4 か所」
  （`_compose_run` / `_compose_lines` / `cmd_login` / `_query_container_name`）へ書き直し、`cmd_login`（生成物あり・なし）と `_compose_lines` のテストを並べる
- **満たす受け入れ条件:** A-1（`cmd_login` 分）/ A-5
- **進め方:** `cmd_login` の `env` のテストを先に書いて落とす → 1 行 → 全件

### Task 4: `devbase scale` の正常系の手順を固定する（4 つ目のコミット）

- **対象ファイル:** `tests/commands/test_container_scale_order.py`
- **変更内容:** `cmd_scale` の外部作用を差し替える harness（`test_container_up_order.py` の `up_harness` と同型）で、
  順序（C-1）、bao と `./deploy` の範囲（C-2）、停止を呼ばないこと（E-3）を固定する
- **満たす受け入れ条件:** C-1 / C-2 / E-3
- **進め方:** 現状固定テスト。書いた時点で緑であることを確かめる（落ちたら実装の振る舞いを読み直す。テストに合わせて実装を変えない）

## 影響範囲

- `devbase scale`（起動の環境と対象）、`devbase login`（環境のみ）、`devbase build --expires` / `rebuild` / 起動前のイメージ確認（config の読み取りの経路。振る舞いは同じ）
- `tests/cli/test_base_image_staleness.py` は `_resolve_dev_service` の名前を差し替えるため、名前を保てば影響しない

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `container.py` は 2000 行を超える 1 ファイルで、`cmd_scale` は 86 行の通しの関数 | 構造は 2 本目で分ける（設計の決定 10）。この Pull Request では `[4/5]` の数行と `default_services` の 1 行だけを触り、Task 4 の現状固定テストを 2 本目の前に入れる |
| `docker_compose_up()` を使うと `CalledProcessError` が `except DevbaseError` を素通りして traceback で落ちる | `docker_compose(..., check=False)` を直接呼ぶ（決定 3）。B-4 のテストで例外が外へ出ないことを固定する |
| `default_services` の解決の失敗という新しい失敗の経路 | `DevbaseError` として `Scale failed: ...` で 1 になる。`up` が同じ解決を既に行っているため、`up` が通るプロジェクトでは踏まない（設計の「処理の流れ」） |
| 既存の `test_container_context.py` の scale テストが `default_services` を差し替えていない harness で落ちる | harness は `default_services` を差し替え済み（`['dev-1']`）。C-3 のとおり書き換えずに通すことを確かめる |

## 切り戻し手順

- この Pull Request の revert で戻る。データ（`project.yml` / 生成物）の形は変わらないため、移行は無い

## 完了の定義

- [ ] 上の受け入れ条件（D-3・D-4 を除く）が、条件ごとに検証手段と結果で対応している
- [ ] `uv run pytest` の全件が exit=0 で、結果を Pull Request 本文の Test plan に載せた
- [ ] 4 つのコミットが設計の順序どおりに並び、Draft の Pull Request へ push した
