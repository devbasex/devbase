# PLAN65 実装 2 本目: `cmd_scale` の段階を分ける

## 関連リンク

- 課題: devbasex/devbase#192（この Pull Request が閉じる）
- 要求と受け入れ条件: `issues/PLAN65_scale-compose-path.md`
- 設計: `issues/PLAN65_scale-compose-path-design.md`（設計 PR #225。承認済み）
- 1 本目: #231（`release/v3.7.0` へマージ済み。計画は `issues/PLAN65_scale-compose-path-impl1.md`）
- release PR: #212（base は `release/v3.7.0`）
- この計画が扱うのは設計の「実装の分け方」の **2 本目だけ**である

## モード

`standard`（本番の振る舞いを変えない構造変更で、対象に 1 本目で入れた現状固定テストが十分にある）。

## 目的と非目的

達成したい状態:

- `cmd_scale` が「2 つの検査 → 段階 → 後処理」の 3 段に読め、`cmd_up`（`_run_pre_up_checks` →
  `_run_deploy_pipeline` → 後処理）と並べて読める（設計の決定 8）

やらないこと:

- 段階ごとに 5 つの関数へ分けること（決定 8 が退けた案）
- `_run_deploy_pipeline` と `_run_scale_pipeline` の統合（決定 8 が退けた案）
- 後処理（`_push_bao_token`・`./deploy`・完了のログ）を関数へ出すこと（決定 8。`cmd_up` も本体に持つ）
- 段階の番号の文字列・ログの文言・失敗の扱い・後処理の順序の変更（決定 5・7）
- `cmd_scale` 以外の本番コードの変更（`_SCALE_COMPOSE_FILE` の有無の確認の重複などは範囲外）
- `cmd_scale` から `_report_missing_repos` / `_apply_window_titles` を呼ぶこと（#224）

## 前提

- 前提 1: 実環境のプロジェクトで `devbase up` / `scale` / `login` を実行しない。確認はテスト（`subprocess.run` と
  段階の関数を差し替えた水準）で行う
- 前提 2: `release/v3.7.0` を base にした Pull Request では CI が動かない（#216）。`uv run --locked pytest tests/ -q` を
  手元で実行し、結果と終了コードを Pull Request 本文の Test plan へ載せる
- 前提 3: 変更前の全件は 1 本目の検査の時点で `2889 passed`、exit=0。この作業ツリーの起点（`eaa9e5d`）で取り直す
- 前提 4: 行数は `ast` で `cmd_scale` の `def` の行から関数の最後の行までを数える（`end_lineno - lineno + 1`）。
  あわせて設計の検証手段の「`def` から次の `def` まで」も並べて載せる

## 受け入れ条件（この Pull Request で満たすもの）

設計文書の「受け入れ条件とどちらの Pull Request が対応するか」の表から、2 本目で満たすものを写す。

- [ ] D-3: `cmd_scale` の本体が 40 行以下になる（起点で 92 行）。抽出した段階の関数（`_run_scale_pipeline`）が
  `[1/5]`〜`[5/5]` のログ文字列をそのまま持つ
  - 検証: `ast` で行数を数える / `grep -n "/5\]" lib/devbase/commands/container.py` の 6 行がすべて `_run_scale_pipeline` の範囲にある
- [ ] D-4: `cmd_scale` と `cmd_up` の段階の対応が設計文書の「段階の対応（変更後）」の表と一致する。段階の番号の
  文字列（`[2.5/5]` を含む）は変えない
  - 検証: 設計の表と `grep -n "/5\]"` の出力を並べる。`_check_scale_request` と `_run_scale_pipeline` の契約（設計の
    「新設・変更する関数の契約」の表）を単体テストで固定する

退行しないこと（1 本目で満たした条件を書き換えずに緑のまま通す）:

- [ ] `tests/commands/test_container_scale_order.py` の既存のテスト（順序・範囲・停止しないこと・`./deploy` の失敗の後も
  続けること・`project_name` の明示・`cmd_login` の既定の引数ほか）を**書き換えずに**各コミットで通す — B-1〜B-5 / C-1 / C-2 / E-2 / E-3
- [ ] 既存の 4 か所の `cmd_scale` のテストを書き換えずに通す — C-3
- [ ] `uv run --locked pytest tests/ -q` の全件が変更の前後で同じ（足したテストの分だけ増える）— C-4 / E-1

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | `_check_scale_request` と `_run_scale_pipeline` の 2 つを抽出し、後処理は本体に残す | 採用 | 設計の決定 8 |
| B | 段階ごとに 5 つの関数へ分ける | 不採用 | 決定 8 が退けた |
| C | `_run_deploy_pipeline` と統合して引数で分岐 | 不採用 | 決定 8 が退けた |
| D | 1 本目の cross-refactoring で 3 者が挙げた extract_method の形をそのまま使う | 参考のみ | 決定 8 が優先する。関数の名前・境界・シグネチャは設計の「新設・変更する関数のシグネチャ」に従う |

## 不変条件

- `project.yml` の `scale` は、グループの不一致と `new_scale` の不適のときは書き換わらない
- 起動が 0 以外のとき `Failed to start new containers` を出して 1 を返す（例外にしない）
- 構成生成・既定のサービスの解決・ready 待ちの失敗は `DevbaseError` として `cmd_scale` の `except` が `Scale failed: ...` を出す
- `[1/5]` → `[2/5]` → `[2.5/5]` → `[3/5]` → `default_services` → `[4/5]` → `[5/5]` → bao → `./deploy` の順

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `cmd_scale` のシグネチャ・戻り値・ログ | 変えない | 構造だけを変える |
| 新設の 2 関数 | モジュール内の private 関数を足す | 公開インタフェースではない |

## 修正対象

- `lib/devbase/commands/container.py`（`cmd_scale` と、その直前に置く新設の 2 関数）
- `tests/commands/test_container_scale_order.py`（新設の 2 関数の契約のテストを**追記**。既存のテストは書き換えない）

## タスク分解

### Task 1: `_check_scale_request` を抽出する

- **対象ファイル:** `lib/devbase/commands/container.py`、`tests/commands/test_container_scale_order.py`
- **変更内容:** `new_scale < 1`（error 1 行）と `new_scale <= current_scale`（warning + info 2 行）の判定を
  `_check_scale_request(new_scale, current_scale) -> bool` へ移す。文言と出し分けは変えない
- **満たす受け入れ条件:** D-4（前提の検査の段）・D-3 の一部
- **進め方:** 契約のテスト（1 未満で False と error、現在以下で False と warning + info、上回れば True でログ無し）を
  先に書き、関数が無いことで落ちるのを確かめてから抽出する。既存の現状固定テストが緑のままであることを確かめてコミット

### Task 2: `_run_scale_pipeline` を抽出する

- **対象ファイル:** 同上
- **変更内容:** `[1/5]`〜`[5/5]`（`write_scale`・`ensure_volumes`・`ensure_network`・`_build_scaled_override`・
  `default_services`・`docker_compose(['up', '-d', '--no-recreate', *services])`・`wait_for_containers_ready`）を
  `_run_scale_pipeline(project_name, new_scale, current_scale, config, target, dev_service_name) -> Optional[Path]` へ移す。
  起動が 0 以外なら `Failed to start new containers` を出して `None` を返す。それ以外の失敗は伝播する
- **満たす受け入れ条件:** D-3・D-4
- **進め方:** 契約のテスト（起動が 0 以外で `None` とエラーのログ、成功で生成物のパスを返し後処理を呼ばない）を先に書き、
  関数が無いことで落ちるのを確かめてから抽出する。既存の現状固定テストが緑のままであることを確かめてコミット

### Task 3: 本体の行数と段階の対応を確かめる

- **対象ファイル:** 無し（検証のみ。必要なら本体のコメントを整える）
- **変更内容:** `ast` で行数を数え、`grep -n "/5\]"` の行が `_run_scale_pipeline` の範囲にあることを確かめる。全件のテストを流す
- **満たす受け入れ条件:** D-3・D-4、C-3・C-4
- **進め方:** 検証のみ（テスト駆動は適用しない。数える対象が既にあるため）

## 影響範囲

- `devbase scale`（`bin/devbase` の dispatch → `cmd_scale`）。振る舞いは変えない

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 抽出で `try` の範囲が変わり、`DevbaseError` の捕捉の範囲がずれる | `_run_scale_pipeline` の呼び出しから後処理までを本体の `try` に収める。既存の `default_services` の失敗・`_build_scaled_override` の例外のテストで確かめる |
| テストが差し替える名前（`container.write_scale` など）を新しい関数が別の経路で引く | 新しい関数もモジュールの名前を実行時に引く。タスクごとに現状固定テストを通す |
| 触る範囲 | 狭く（`cmd_scale` の 1 関数）、テストが厚い。「タスクごとにテストを通す」で足りる |

## 切り戻し手順

- 本番コードの変更は `container.py` の 1 関数の分割だけで、データの移行は無い。Pull Request を revert すれば戻る

## 完了の定義

- [ ] D-3・D-4 を満たし、条件ごとに検証手段と結果が Pull Request 本文に対応している
- [ ] 既存の現状固定テストを書き換えずに、各コミットで緑
- [ ] `uv run --locked pytest tests/ -q` が exit=0
