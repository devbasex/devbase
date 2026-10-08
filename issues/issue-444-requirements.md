# #444: PR #442 の構造改善で時間の枠を超えて見送った提案の残り 13 件（_aws_sync_plan の _SyncContext 化・env list / keygen の長い関数・テストの補助の重複）

正は課題の本文（#444）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

PR #442（スプリント m10a）の構造改善（cross-refactoring）は、提案のうち 4 件を適用し、13 件を PR に入れなかった。内訳は、時間の枠（budget）を超えた見送りが 12 件、締め切りまでにコミットが無かったものが 1 件である（重複として外した 1 行は数えない）。`main` = `54c1c27`（2026-10-07）で、次の 13 件がそのまま残っている。

| 対象 | 兆候 | 結果 |
| --- | --- | --- |
| `lib/devbase/commands/env.py#_aws_sync_plan`（838 行） | long_method（認知的複雑度 21・CC 15・69 行・return 11 本。sync の他の関数は `_SyncContext` を受ける形に揃ったが、この関数だけ `sources`・`targets`・`store` を別々に受ける）。conditional_chain の提案も同じ変更 | not_done |
| `lib/devbase/tui/actions_env_keys.py#group_attrs`（182 行） | duplication | budget |
| `lib/devbase/volume/compose.py#_mask_secret_environment`（229 行） | conditional_chain・long_method | budget |
| `lib/devbase/commands/container.py#_run_post_start`（1276 行） | long_parameter_list | budget |
| `lib/devbase/commands/env.py#_collect_from_env_yml`（1371 行） | long_method | budget |
| `lib/devbase/commands/env.py#cmd_env_keygen`（1544 行） | long_method | budget |
| `lib/devbase/commands/env.py#cmd_env_list`（1051 行） | duplication・long_method | budget |
| `lib/devbase/snapshot/manager.py#SnapshotManager.restore`（408 行） | long_method | budget |
| `tests/volume/test_compose_vscode.py#_write_compose / _load_scaled / _mount_source`（31 行〜） | duplication | budget |
| `tests/volume/test_compose_group.py#_mount_source`（40 行） | duplication | budget |
| `tests/conftest.py#_Handler.do_POST`（222 行） | long_method | budget |

着手の注意:

- `_aws_sync_plan` の手順は改修計画の I-001 にある（入れ子の report の関数を外へ出す → `aws_profiles` の分岐と控えの無い分岐を抽出 → `(ctx)` を受けて振り分けるだけにする）。確かめるテストは `tests/commands/test_env_host_import.py`・`test_env_sync_group.py`・`test_env_sync_owner.py`
- `tests/volume/` の 2 つの `_mount_source` は同じ補助の重複。まとめるなら共通の置き場（`conftest.py` など）へ移す

提案ごとの理由と手順は改修計画にある（I-001 と「見送った提案」の節）: https://github.com/devbasex/devbase/pull/442#issuecomment-6031923142

## どこで見つけたか

PR #442 の検査のプランの cross-refactoring。想定最大時間は 30 分で、改修計画を立てた時点で使える時間は 6.92 分だった。スプリント m10a の振り返りで、見送った提案が起票されていないことを見つけた（#310 は同じ形で PR #308 から起票したもの）。

## なぜこの変更の範囲外なのか

構造改善は時間の枠の中で入る分だけを適用する。13 件は枠に入らなかった。振る舞いの不具合ではなく、#425 #397 #310 #333 の受け入れ条件のどれにも関わらない。

## 直さないと何が起きるか

振る舞いは変わらない。`env sync` の AWS の経路と `env list`・`env keygen` は、次に触る変更で読み解く手間がかかる。`_aws_sync_plan` だけが `_SyncContext` を受けない形のまま残り、#310 で揃えた sync の関数の受け方が 1 か所だけ違う。

## 由来

PR #442（issue #310 と同じスプリント m10a）の振り返りで見つけた。

## 依頼（原文）

起票の時点の受け入れ条件を、そのまま写す。下の「受け入れ条件」はこれを観測できる形へ言い換えたもの。

> - [ ] 表の 13 件のそれぞれを、直す（Pull Request に入れる）か直さない（理由を本文に書く）かに分け、直したものは表の兆候が消えている
> - [ ] 直しても本番の振る舞い（`env` のコマンドと TUI、`snapshot restore`、`up` の起動の後の処理の出力・終了コード・書き込み）は変わらない。既存のテストが手を入れずに通るか、テストを直したときは理由を本文に書く
> - [ ] 全体のテスト（`env -u DEVBASE_ROOT uv run --locked pytest tests/ -q`）と CI と同じ lint が通る

## 目的

- PR #442 の構造改善で時間の枠に入らなかった提案を片付け、`env sync` の AWS の経路・`env list`・`env keygen` などを、次に触る変更で読み解ける大きさにする
- sync の関数の受け方を `_SyncContext` に揃え、#310 で揃えた形の例外を無くす

## 前提

- 前提 1: 「13 件」は改修計画（PR #442 のコメント）の「見送った提案」の行のうち、重複として外した 1 行を除いた 13 行で、1 件は「対象の関数 × 兆候」の組である。起票の表は 11 行だが、`cmd_env_list` と `_mask_secret_environment` が兆候を 2 つ持つため 13 件になる。下の「提案の一覧」の R1〜R13 がその 13 件である
- 前提 2: 「兆候が消えている」は、兆候ごとに下の「兆候が消えたと見なす基準」で判定する。基準の数値は、この課題で決めた値である（NDF の構造改善に兆候ごとの決まった閾値は無い）。設計で基準を変えるときは、変えた事実と理由を本文に残す
- 前提 3: 行数は `def` の行から関数の最後の行までを数える（docstring を含む）。認知的複雑度は complexipy、循環的複雑度は ruff の C901（McCabe）の値を使う。現状の値は `main` = `54c1c27` で測った
- 前提 4: 長い関数から抜き出した関数も、同じ基準（行数・認知的複雑度）を満たす。抜き出した先へ長さを移しただけのものは、兆候が消えたと見なさない
- 前提 5: R2（`group_attrs` の duplication）の重複の相手は、`lib/devbase/commands/env.py#_target_group` の `--group` の名前の検証（`openbao.storage_group(group)` を通し、通らなければ「`--group に使えない名前です: …`」を出す）である。提案の時点の記録に相手が書かれていないため、要求の時点でコードから特定した
- 前提 6: `tests/volume/test_compose.py` にも `_write_compose`・`_load_scaled` があるが、引数の形が違い、表に無い。この課題では扱わない
- 前提 7: 直さないと決めた件は、その件の行に「直さない」と理由を本文（Pull Request の本文か、この課題の本文）に書く。理由の無い「直さない」は受け入れない

## 対象範囲

含む:

- 提案の一覧の R1〜R13 の、直すか直さないかの判断と、直すものの構造の変更
- 構造の変更に合わせた、関数の呼び出し元の書き換え（同じモジュールの中と、R2 の `_target_group`）
- テストの補助を `tests/volume/` の共通の置き場へ移すこと（R11・R12）

含まない:

- 本番の振る舞いの変更（出力の文言・終了コード・置き場やファイルへの書き込み・ログの文言）
- 表に無い関数の構造改善（`SnapshotManager._backup_before_restore`・`tests/volume/test_compose.py` の補助など、測ると値が高いものも含めない）
- 公開のコマンドの引数・オプションの変更
- `CHANGELOG.md` の記入（利用者に見える変更が無いため。AGENTS.md の Pull Request の節）

## 提案の一覧

| # | 対象 | 兆候 | 現状の値（`main` = `54c1c27`） |
| --- | --- | --- | --- |
| R1 | `lib/devbase/commands/env.py#_aws_sync_plan` | long_method | 69 行・認知的複雑度 21・C901 15。`sources`・`targets`・`store` を別々に受ける |
| R2 | `lib/devbase/tui/actions_env_keys.py#group_attrs` | duplication | `--group` の名前の検証が `commands/env.py#_target_group` と 2 か所にある（前提 5） |
| R3 | `lib/devbase/volume/compose.py#_mask_secret_environment` | conditional_chain | `environment` の形（none / dict / list / その他）の分岐が 1 つの関数に並ぶ。C901 8 |
| R4 | `lib/devbase/volume/compose.py#_mask_secret_environment` | long_method | 58 行・認知的複雑度 23 |
| R5 | `lib/devbase/commands/container.py#_run_post_start` | long_parameter_list | 引数 7 個 |
| R6 | `lib/devbase/commands/env.py#_collect_from_env_yml` | long_method | 33 行・認知的複雑度 19 |
| R7 | `lib/devbase/commands/env.py#cmd_env_keygen` | long_method | 93 行・認知的複雑度 13 |
| R8 | `lib/devbase/commands/env.py#cmd_env_list` | duplication | 節の見出し・変数の表示・件数の行を出す処理が、チーム共通の節とプロジェクトの節の 2 か所にある |
| R9 | `lib/devbase/commands/env.py#cmd_env_list` | long_method | 49 行・認知的複雑度 21 |
| R10 | `lib/devbase/snapshot/manager.py#SnapshotManager.restore` | long_method | 60 行・認知的複雑度 15 |
| R11 | `tests/volume/test_compose_vscode.py#_write_compose / _load_scaled / _mount_source` | duplication | 同じ 3 つの補助が `tests/volume/test_compose_group.py` にもある |
| R12 | `tests/volume/test_compose_group.py#_mount_source` | duplication | R11 の `_mount_source` と同じ補助 |
| R13 | `tests/conftest.py#_Handler.do_POST` | long_method | 46 行・認知的複雑度 20・C901 13 |

R1 は改修計画の not_done の行（I-001）で、手順 4 の「`(ctx)` を受けて振り分けるだけにする」を含む。R11 と R12 は同じ補助の重複だが、改修計画では 2 行に分かれているため 2 件に数え、それぞれ判定する。

## 兆候が消えたと見なす基準

| 兆候 | 基準 |
| --- | --- |
| long_method | 対象の関数と、そこから抜き出した関数のそれぞれが、50 行以下かつ認知的複雑度 15 以下 |
| conditional_chain（R3） | `environment` の形ごとの処理が形ごとに別の関数（または形をキーにした対応表の 1 項目）になり、`_mask_secret_environment` の C901 が 5 以下 |
| long_parameter_list（R5） | `_run_post_start` の引数が 4 個以下 |
| duplication（R2） | `--group` の名前の検証（`storage_group` を通し、通らなければ「`--group に使えない名前です: …`」の文を作る）が 1 つの関数にあり、`_target_group` と `group_attrs` の両方がそれを呼ぶ |
| duplication（R8） | 節の見出し・変数の表示・件数の行を出す処理が `cmd_env_list` の中で 1 か所（1 つの関数）にある |
| duplication（R11） | `tests/volume/test_compose_vscode.py` に `_write_compose`・`_load_scaled`・`_mount_source` の定義が無く、同じ補助の定義が `tests/volume/` の 1 か所にある |
| duplication（R12） | `tests/volume/test_compose_group.py` に `_mount_source` の定義が無く、R11 と同じ 1 か所の定義を使う |
| R1 の追加の基準 | `_aws_sync_plan` が `_SyncContext` 1 つを受け、`_sync_credential_sources` からの呼び出しが `_aws_sync_plan(ctx)` の形になる |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 着手前の全体のテストが通った | 実装の着手 | 既存の失敗があれば、構造の変更の前に記録して人へ戻す（この課題では直さない） | — |
| E2 | 13 件のそれぞれを直すか直さないかに分けた | 設計 | 決められない件は「直さない」にして理由を書く（前提 7） | E1 |
| E3 | 直す件の構造を変えた | 実装 | 基準を満たせない件は、変更を戻して「直さない」にし、理由を書く | E2 |
| E4 | 変更の後の全体のテストと lint が通った | E3 の後の検査 | テストが落ちたら、テストではなく構造の変更を直す。テストを直すときは理由を本文に書く | E3 |
| E5 | 直した件の兆候が基準で消えたことを測った | E4 の後の測定 | 基準を満たさない件は E3 へ戻すか、「直さない」へ移す | E4 |

## 受け入れ条件

- [ ] R1〜R13 の 13 件のそれぞれが「直す」か「直さない」に分かれ、「直さない」の件には理由が本文に書かれている
- [ ] 「直す」とした件は、「兆候が消えたと見なす基準」の該当の行を満たす。基準の値を測ったコマンドと結果が Pull Request の本文にある
- [ ] 直した関数から抜き出した関数も、long_method の基準（50 行以下・認知的複雑度 15 以下）を満たす
- [ ] `env sync`（AWS の経路）・`env list`・`env keygen`・`env` の TUI のグループの選択・`snapshot restore`・`up` の起動の後の処理（post-start）の、標準出力・標準エラー・終了コード・ファイルと置き場への書き込みが変更の前と同じである。既存のテストが手を入れずに通ることで確かめる
- [ ] R11・R12 のテストの補助の移動を除き、`tests/` の既存のテストの期待値（assert の中身）を変えていない。変えたときは、変えたテストと理由が Pull Request の本文にある
- [ ] `--group` に使えない名前を渡したときの文（CLI の `--group に使えない名前です: …` と TUI で選択へ戻る前のエラーの行）が変更の前と同じである
- [ ] `compose` のスケールで作るファイル（`.docker-compose.scale.yml`）で、機密のキーの値が書かれず、非機密の値が残る振る舞いが変わらない（`tests/volume/test_compose_secret_env.py` が手を入れずに通る）
- [ ] 全体のテスト（`env -u DEVBASE_ROOT uv run --locked pytest tests/ -q`）が通る
- [ ] CI と同じ lint（`uvx ruff check --select=E9,F63,F7,F82 lib`）と固有の語の検査（`python3 .github/scripts/proper_term_check.py`）が通る
- [ ] 表に無い関数（「含まない」に挙げたもの）を変えていない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 「兆候が消えたと見なす基準」を満たす。変更の後、対象のファイルの関数で C901 と認知的複雑度が変更の前より上がったものが無い |
| セキュリティ | R3・R4 の変更の後も、機密のキーの値がスケールで作るファイルへ書かれない（`compose` の機密のマスクの振る舞いを保つ）。R7 の変更の後も、`env keygen` が鍵の値を標準出力とログへ出す範囲が変わらない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（コマンド・オプション・出力・終了コード）。モジュールの内部の関数（`_` で始まるもの）と `actions_env_keys.group_attrs` の中身の分け方だけが変わる |
| データ | 変わらない。置き場・設定ファイル・ボリュームの形と移行は無い |
| 既存の振る舞い | 変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` |
| 範囲のテスト | `tests/commands/test_env_host_import.py`・`test_env_sync_group.py`・`test_env_sync_owner.py`（R1）、`tests/volume/test_compose_secret_env.py`（R3・R4）、`tests/commands/test_container_post_start.py`・`tests/cli/test_post_start_command.py`（R5）、`tests/volume/test_compose_vscode.py`・`test_compose_group.py`（R11・R12）、`env list`・`env keygen`・`snapshot restore`・TUI の env のテスト（R2・R6〜R10）、R13 の `do_POST` を使うテスト |
| 静的解析 | `uvx ruff check --select=E9,F63,F7,F82 lib`、`python3 .github/scripts/proper_term_check.py` |
| 基準の測定 | `uvx ruff check --select C901 --config 'lint.mccabe.max-complexity=0' <ファイル>`（C901）、`uvx complexipy <ファイル>`（認知的複雑度）、行数と引数の数は `ast` で関数の `lineno`・`end_lineno`・引数を数える。変更の前後の値を Pull Request の本文に並べる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「このリポジトリ」。実装は `lib/devbase/`、テストは `tests/`。テストの共通の補助は `tests/volume/conftest.py` など `tests/volume/` の中に置き、`lib/` へ置かない |
| コーディング規約 | 周りのコードの書き方に合わせる（`AGENTS.md` の「書き方」）。lint は上の検証手段 |
| テスト戦略 | 振る舞いは既存のテストで固定する（構造の変更のためにテストを足すのは、既存のテストが振る舞いを固定していない経路だけ。足したテストは変更の前のコードで通る）。兆候の消滅は測定で確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 着手前と変更の後の全体のテスト、lint、基準の測定 |
| 確認してから行う | テストの期待値の変更（理由を本文に書いてから） |
| 行わない | 表に無い関数の構造改善、出力の文言・終了コードの変更、`env -u DEVBASE_ROOT` を外したテストの実行、base イメージの変更（P1）、実の環境での `devbase up`（P3） |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 13 件のうち「直さない」とする件と、その理由 | 設計の担当（設計文書の決定の記録） | 設計 PR |
| R13（`tests/conftest.py#_Handler.do_POST`）を分ける形。偽のサーバの経路ごとに関数を分けるか、経路をキーにした対応表にするか | 設計の担当 | 設計 PR |
