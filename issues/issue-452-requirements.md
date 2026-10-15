# #452: PR #450 の構造改善で時間の枠を超えて見送った提案の残り 15 件（_aws_plan の重複・補完の分岐の連なり・テストの補助の重複など）

正は課題の本文（#452）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

PR #450（スプリント m10b）の構造改善（cross-refactoring rf450）は、提案のうち 5 件を適用し、16 件を PR に入れなかった。内訳は、時間の枠（budget）を超えた見送りが 15 件、ほかの採用と同じ変更（duplicate）が 1 件である。`main` = `04f7228`（2026-10-15）で、次の 15 件が残っている。

| 対象 | 兆候 |
| --- | --- |
| `lib/devbase/commands/env.py#cmd_env_keygen` | dead_code |
| `lib/devbase/commands/env.py#_aws_plan_for_selection` | duplication |
| `lib/devbase/commands/env.py#_aws_plan_for_unregistered` | duplication |
| `lib/devbase/commands/env.py#check_group_option` | feature_envy |
| `lib/devbase/volume/compose.py#_DEPRECATED_TARGET` と `lib/devbase/volume/manager.py#VSCODE_SERVER_TARGET / VSCODE_BIN_TARGET`（`/home/ubuntu`） | magic_value |
| `lib/devbase/volume/compose.py#_mask_env_none` | inconsistent_naming |
| `lib/devbase/snapshot/manager.py#SnapshotManager.list` | long_method |
| `etc/_devbase#_devbase`（`project` / `container` の `profile`） | duplication |
| `etc/devbase-completion.bash#_devbase_completions`（`devbase_root` の解決） | duplication |
| `etc/devbase-completion.bash#_devbase_completions` | conditional_chain |
| `tests/cli/test_completion.py#_bash_complete` | duplication |
| `tests/volume/test_compose_dev_environment.py#DEVBASE_MANAGED` | duplication |
| `tests/conftest.py#_Handler.do_GET` | long_method |
| `tests/containers/test_base_dockerfile_fonts.py#_run_blocks` | duplication |
| `tests/containers/test_base_image_font_matching.py#_docker_unavailable` | duplication |

提案ごとの理由と見積りは改修計画の「見送った提案」の節にある: https://github.com/devbasex/devbase/pull/450#issuecomment-6056505102

## どこで見つけたか

スプリント m10b の振り返りで、PR #450 の改修計画のコメントを起票済みの課題と突き合わせたとき。見送った提案は改修計画にだけあり、課題になっていなかった（m10a の同じ見送りは #444 として起票され、m10b で片付けた）。

## なぜこの変更の範囲外なのか

PR #450 の構造改善は時間の枠の中で入る件だけを適用する工程で、枠を超えた件はその PR の範囲に入らない。#444・#435 の受け入れ条件にも含まれない。根拠: Vision（MVV 版 1）

## 直さないと何が起きるか

利用者から見える振る舞いは変わらない。次に `env sync` の AWS の経路・補完・偽の OpenBao などを触る開発者と AI エージェントが、同じ重なりや長い関数を読み解く手間を負う。課題にしないと、改修計画のコメントが埋もれて次のスプリントの候補に上がらない。

## 由来

PR #450

## 依頼（原文）

> #452。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。本文を書いたら `spec-copy.py write 452 issues/issue-452-requirements.md` で写しを作り、コミットする（push しない）。 #452 は PR #450 の構造改善で時間の枠を超えて見送った提案の残り 15 件である。前例は #444（要求と設計は #445・#446 の形、実装 #448）で、件ごとに直すか直さないか（理由を書く）を決め、兆候が消えたと見なす基準（行数・認知的複雑度など）と、振る舞いを変えないことの確かめ方（既存のテストが手を入れずに通るか、直すならその理由）を決める。提案ごとの理由と見積りは PR #450 のコメント 6056505102 の「見送った提案」の節にある。#365 と #490 は同じスプリントだが本文の受け入れ条件で進めるため、この設計に含めない（#490 と同じ lib/devbase/volume/manager.py を触る件は、ぶつからない分け方を書く）。作業用のプロジェクトを含め `devbase up` を打たない（AGENTS.md の落とし穴。実のボリュームが付く）。base イメージを建て直さない。

（書き手: AI の起動指示、日付: 2026-10-15。利用者の指示の原文ではない）

課題の題と、上の「何を見つけたか」〜「由来」の節は、書き手が起票者（スプリント m10b の振り返り、2026-10-15）である。利用者の指示の原文は、この課題には無い。

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。承認ゲート 1 で利用者が確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 見送りの 15 件を 1 つの課題・1 本の実装の Pull Request で扱う | 本文「何を見つけたか」・前例 #444 | 前提 1 |
| S2 | 対象は「時間の枠を超えた見送り」の 15 件で、duplicate の 1 件（`cmd_env_get` の long_method）を含まない | 本文「何を見つけたか」 | 前提 1 |
| S3 | 直しても利用者から見える振る舞いは変わらない | 本文「直さないと何が起きるか」 | 受け入れ条件 4〜7 |

## 目的

- PR #450 の構造改善で時間の枠に入らなかった 15 件を、直すか直さないかに分けて片付ける。`env sync` の AWS の経路・補完のスクリプト・偽の OpenBao（`tests/conftest.py`）などを次に触る開発者と AI エージェントが、重なりや長い関数を読み解く手間を減らす
- 改修計画のコメントにだけある見送りを、判断と理由の付いた記録にする

## 前提

- 前提 1: 「15 件」は改修計画（PR #450 のコメント 6056505102）の「見送った提案」の行のうち、duplicate の 1 行を除いた 15 行で、1 件は「対象 × 兆候」の組である。下の「提案の一覧」の R1〜R15 がその 15 件で、1 本の実装の Pull Request で扱う
- 前提 2: 改修計画には提案ごとの兆候の名前と見積りしか残っておらず、提案の本文（どこが重なりの相手か・何が死んだコードか）は残っていない（提案の作業ディレクトリ `rf450/work/.cross_refactoring` には指標のキャッシュしか無いことを 2026-10-15 に確かめた）。下の「現状」の列の重なりの相手と死んだコードは、要求の時点でコード（`main` = `04f7228`）から特定したものである
- 前提 3: 行数は `def`（シェルは関数名の行）から関数の最後の行までを数える（docstring・コメントを含む）。認知的複雑度は complexipy、循環的複雑度は ruff の C901（McCabe）の値を使う。シェルの関数には認知的複雑度を測る道具が無いため、行数と分岐の置き場で判定する
- 前提 4: 兆候の基準の数値は #444 の基準（long_method は 50 行以下かつ認知的複雑度 15 以下）を引き継ぐ。長い関数から抜き出した関数も同じ基準を満たす。抜き出した先へ長さを移しただけのものは、兆候が消えたと見なさない
- 前提 5: 「直す / 直さない」の列は要求の時点の見立てである。設計で変えるときは、変えた事実と理由を設計文書の決定の記録に残す。理由の無い「直さない」は受け入れない
- 前提 6: 表に無い同じ形のもの（`env.py` のほかの関数の `from devbase.errors import DevbaseError` の取り込み直し、`env/collectors/google.py` の `/home/ubuntu` の文字列など）は扱わない
- 前提 7: #490（`VolumeManager.__init__`・`ensure_volumes` から使われない `project_name` を外す）と #365 は、この課題に含めない。#452 が `lib/devbase/volume/manager.py` で変えるのは、モジュールの先頭の定数（`VSCODE_SERVER_TARGET`・`VSCODE_BIN_TARGET` とその近く）だけで、`VolumeManager.__init__`・`ensure_volumes`・その呼び出し元（`lib/devbase/commands/container.py`）と、#490 が期待を直すテスト（`tests/commands/test_container_scale_order.py`・`tests/volume/test_manager_vscode.py`・`tests/volume/test_manager_ensure_volumes.py`）を変えない。後からマージする方が `main` を取り込む（`main` は strict）

## 提案の一覧

現状の値は `main` = `04f7228` で測った。

| # | 対象 | 兆候 | 現状 | 見立て |
| --- | --- | --- | --- | --- |
| R1 | `lib/devbase/commands/env.py#cmd_env_keygen` | dead_code | 関数の中の `from devbase.errors import DevbaseError` が、モジュールの先頭（11 行）の取り込みと重なり、使われない。47 行・認知的複雑度 6・C901 5 | 直す |
| R2 | `lib/devbase/commands/env.py#_aws_plan_for_selection` | duplication | 選んだプロファイルの計画 `(lambda: aws_profiles.build_from_home(profiles).encode()), profiles` が関数の中に 2 か所ある。20 行・認知的複雑度 4・C901 4 | 直す |
| R3 | `lib/devbase/commands/env.py#_aws_plan_for_unregistered` | duplication | 丸ごとの計画 `_encode_aws_config_files, ALL` が `_aws_sync_plan` と 2 か所にある。32 行・認知的複雑度 6・C901 6 | 直す |
| R4 | `lib/devbase/commands/env.py#check_group_option` | feature_envy | `validate_account_group`（`volume.manager`）と `settings.storage_group` を呼び、誤りを `GroupOptionError` に変える。18 行・認知的複雑度 2・C901 3 | 直さない（理由 1） |
| R5 | `lib/devbase/volume/compose.py#_DEPRECATED_TARGET` と `lib/devbase/volume/manager.py#VSCODE_SERVER_TARGET / VSCODE_BIN_TARGET` | magic_value | ホームのパス `/home/ubuntu` が `lib/devbase/volume/` の 3 つの定数にそれぞれ文字列で書かれている | 直す |
| R6 | `lib/devbase/volume/compose.py#_mask_env_none` | inconsistent_naming | `existing`・`secret_set` を受けて使わない（`_ENV_MASKERS` の 4 つの関数で引数を揃えるため）。3 行・認知的複雑度 1 | 直さない（理由 2） |
| R7 | `lib/devbase/snapshot/manager.py#SnapshotManager.list` | long_method | 27 行・認知的複雑度 14・C901 4 | 直さない（理由 3） |
| R8 | `etc/_devbase#_devbase`（`project` / `container` の `profile`） | duplication | `profile` の補完（`up|down|list` の後の `--context` と、操作の候補）の約 10 行が `project` と `container` の 2 か所にある | 直す |
| R9 | `etc/devbase-completion.bash`（`devbase_root` の解決） | duplication | `${DEVBASE_ROOT:-$(dirname "$(dirname "$(command -v devbase …)")" …)}` の式が 3 か所（8・245・326 行）にある | 直す |
| R10 | `etc/devbase-completion.bash#_devbase_completions` | conditional_chain | 語の位置（`cword`）ごとの `case` の中に、グループ（`project`・`container`・`env`・`plugin`・`snapshot`）ごとの `if` と `case` が並ぶ。関数は約 290 行 | 直す |
| R11 | `tests/cli/test_completion.py#_bash_complete` | duplication | 補完のスクリプトを読み込んで `_devbase_completions` を実行する同じ手順が `tests/cli/test_post_start_command.py`（同じ定義）と `tests/cli/test_name_context_consistency.py`（引数と戻り値の形が違う）にもある | 直す |
| R12 | `tests/volume/test_compose_dev_environment.py#DEVBASE_MANAGED` | duplication | 同じ値の辞書が `tests/volume/test_compose_secret_env.py` にもある | 直す |
| R13 | `tests/conftest.py#_Handler.do_GET` | long_method | 34 行・認知的複雑度 16・C901 12 | 直す |
| R14 | `tests/containers/test_base_dockerfile_fonts.py#_run_blocks` | duplication | 同じ手順が `tests/containers/test_base_dockerfile_shellcheck.py#_run_blocks` にある | 直す |
| R15 | `tests/containers/test_base_image_font_matching.py#_docker_unavailable` | duplication | 同じ手順（docker の有無 → daemon → イメージの有無）が `test_base_image_link_stage.py`・`test_browser_image.py`・`test_base_image_without_browser.py` にもある。理由の文とイメージの名前だけが違う | 直す |

「直さない」の理由:

1. **R4**: この関数の役目は、名前の検査の誤りを `--group` のオプションの誤り（「`--group に使えない名前です: …`」・終了コード 2）へ変えることで、文言と終了コードはコマンドの層の知識である。呼ぶ先（`volume.manager`・OpenBao の設定）へ移すと、コマンドの文言が設定の層へ入る。#444 の R2（#448）で CLI と TUI の両方が呼ぶ 1 か所として置いた形で、移す先の候補がこの形より良くならない
2. **R6**: 名前は `_ENV_MASKERS` のキー（`_env_shape` の返す `'none'`）に合わせた `_mask_env_<形>` の規則どおりで、使わない引数は表から同じ形で呼ぶために要る。名前や引数を変えると、4 つの関数の揃いが崩れる
3. **R7**: すでに long_method の基準（50 行以下・認知的複雑度 15 以下、前提 4）を満たしている

## 兆候が消えたと見なす基準

| # | 基準 |
| --- | --- |
| R1 | `cmd_env_keygen` の中に、モジュールの先頭で取り込んでいる名前の取り込み直しが無い |
| R2 | 選んだプロファイルの計画（`build_from_home(profiles).encode()` を遅らせて呼ぶ関数と `profiles` の組）を作る式が `env.py` の中で 1 か所にある |
| R3 | 丸ごとの計画（`_encode_aws_config_files` と `ALL` の組）を作る式が `env.py` の中で 1 か所にある |
| R2・R3 共通 | `_aws_sync_plan`・`_aws_plan_for_selection`・`_aws_plan_for_unregistered` と抜き出した関数のそれぞれが long_method の基準を満たし、C901 が変更の前より上がらない |
| R5 | `lib/devbase/volume/` の中で、文字列 `/home/ubuntu` を書いた定義が 1 か所にあり、`_DEPRECATED_TARGET`・`VSCODE_SERVER_TARGET`・`VSCODE_BIN_TARGET` がそれから作られる。3 つの定数の値（文字列）は変更の前と同じ |
| R8 | `etc/_devbase` で、`profile` の補完の定義が 1 か所（1 つの関数）にあり、`project` と `container` の両方がそれを呼ぶ |
| R9 | `etc/devbase-completion.bash` で、`devbase_root` を解決する式が 1 か所（1 つの関数）にあり、3 つの使い手がそれを呼ぶ |
| R10 | `_devbase_completions` が 60 行以下で、グループごとの分岐（`project`・`container`・`env`・`plugin`・`snapshot` の `case`）を本体に持たない。`etc/devbase-completion.bash` のどの関数も 60 行以下 |
| R11 | 補完のスクリプトを読み込んで `_devbase_completions` を実行するスクリプトの文が `tests/cli/` の 1 か所にあり、`test_completion.py`・`test_post_start_command.py`・`test_name_context_consistency.py` がそれを使う |
| R12 | `DEVBASE_MANAGED` の定義が `tests/volume/` の 1 か所にあり、2 つのテストのファイルがそれを取り込む。値は変更の前と同じ |
| R13 | `_Handler.do_GET` と、そこから抜き出したメソッドのそれぞれが long_method の基準を満たす |
| R14 | RUN ブロックへ分ける手順の定義が `tests/containers/` の 1 か所にあり、2 つのテストのファイルがそれを使う |
| R15 | Docker とイメージが使えない理由を返す手順の定義が `tests/containers/` の 1 か所にあり、4 つのテストのファイルがそれを使う。各ファイルの飛ばす理由の文（イメージの名前と建て方の案内を含む）は変更の前と同じ |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 着手前の全体のテストと基準の値を記録した | 実装の着手 | 既存の失敗があれば、構造の変更の前に記録して人へ戻す（この課題では直さない） | — |
| E2 | 15 件のそれぞれを直すか直さないかに確定した | 設計 | 決められない件は「直さない」にして理由を書く（前提 5） | E1 |
| E3 | 本番のコードと補完のスクリプト（R1〜R10）の構造を、`tests/` を変えずに変えた | 実装 | 基準を満たせない件は変更を戻して「直さない」にし、理由を書く。テストが落ちたら、テストではなく構造の変更を直す | E2 |
| E4 | テストの補助（R11〜R15）を共通の置き場へ移した | 実装 | 移した後に落ちるテストがあれば、補助の移し方を直す。assert の中身は変えない | E3 |
| E5 | 変更の後の全体のテストと検査が通り、直した件の基準を測った | E4 の後の検査 | 基準を満たさない件は E3 / E4 へ戻すか「直さない」へ移す | E4 |

E3 を E4 より先に置くのは、本番のコードと補完の変更を「既存のテストが手を入れずに通る」ことで確かめるためである（R11 の補助を先に動かすと、R8〜R10 の確かめに使うテストが変わった後になる）。

## 受け入れ条件

- [ ] R1〜R15 の 15 件のそれぞれが「直す」か「直さない」に分かれ、「直さない」の件には理由が本文（Pull Request か、この課題）に書かれている
- [ ] 「直す」とした件は、「兆候が消えたと見なす基準」の該当の行を満たす。基準の値を測ったコマンドと、変更の前後の値が Pull Request の本文にある
- [ ] 直した関数から抜き出した関数も、long_method の基準（50 行以下・認知的複雑度 15 以下。シェルの関数は 60 行以下）を満たす
- [ ] `env keygen`・`env sync`（AWS の経路）・`env` の `--group` の検査（CLI と TUI）・`compose` のスケールで作るファイル（`.docker-compose.scale.yml`）・`snapshot list` の、標準出力・標準エラー・終了コード・ファイルへの書き込みが変更の前と同じである。R1〜R10 を入れたコミットで `tests/` を変えず、既存のテストが手を入れずに通ることで確かめる
- [ ] bash と zsh の補完の候補が変更の前と同じである。`tests/cli/test_completion.py`・`test_name_context_consistency.py`・`test_zsh_completion_run.py`・`test_post_start_command.py` が、R8〜R10 を入れたコミットで手を入れずに通ることで確かめる
- [ ] R11〜R15 の補助の移動を除き、`tests/` の既存のテストを変えていない。補助の移動でも assert の中身を変えていない。変えたときは、変えたテストと理由が Pull Request の本文にある
- [ ] R15 の後も、Docker かイメージが無い端末で 4 つのファイルのテストが飛ばされ、飛ばす理由の文が変更の前と同じである
- [ ] `lib/devbase/volume/manager.py` の変更がモジュールの先頭の定数に限られ、`VolumeManager.__init__`・`ensure_volumes` と前提 7 に挙げたテストを変えていない
- [ ] 表に無い関数と、前提 6 に挙げた同じ形のものを変えていない
- [ ] 全体のテスト（`env -u DEVBASE_ROOT uv run --locked pytest tests/ -q`）が通る
- [ ] CI と同じ lint（`uvx ruff check --select=E9,F63,F7,F82 lib`）と固有の語の検査（`python3 .github/scripts/proper_term_check.py`）が通る。固有の語の検査が「語の一覧が無いため飛ばした」と出たときは、通ったと扱わず Pull Request の本文にそう書く
- [ ] `bash -n etc/devbase-completion.bash` と `zsh -n etc/_devbase` が通り、`devbase-base:latest` の shellcheck（`-s bash`）の `etc/devbase-completion.bash` への指摘が変更の前より増えない
- [ ] 実装の中で `devbase up` を打たず（作業用のプロジェクトを含む）、base イメージを建て直していない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 「兆候が消えたと見なす基準」を満たす。変更の後、対象のファイルの関数で C901 と認知的複雑度が変更の前より上がったものが無い |
| セキュリティ | R2・R3 の変更の後も、`env sync` が AWS の設定と認証情報を書く条件（選んだプロファイルとその連なりだけ・丸ごと・書かない）が変わらない。R1 の変更の後も、`env keygen` が鍵を書く先と、標準出力・ログへ出す範囲が変わらない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（コマンド・オプション・出力・終了コード・補完の候補）。モジュールの内部の関数（`_` で始まるもの）・補完のスクリプトの内部の関数・テストの補助の置き場だけが変わる |
| データ | 変わらない。置き場・設定ファイル・ボリュームの形と移行は無い |
| 既存の振る舞い | 変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` |
| 範囲のテスト | `tests/commands/test_env_keygen.py`（R1）、`tests/commands/test_env_host_import.py`・`test_env_sync_group.py`・`test_env_sync_owner.py`（R2・R3）、`tests/volume/`・`tests/editor/` の VS Code Server のテスト（R5）、`tests/cli/test_completion.py`・`test_name_context_consistency.py`・`test_zsh_completion_run.py`・`test_post_start_command.py`（R8〜R11）、`tests/volume/test_compose_secret_env.py`・`test_compose_dev_environment.py`（R12）、偽の OpenBao を使うテスト（R13。全体のテストで見る）、`tests/containers/` の 6 つのファイル（R14・R15） |
| 静的解析 | `uvx ruff check --select=E9,F63,F7,F82 lib`、`python3 .github/scripts/proper_term_check.py`、`bash -n etc/devbase-completion.bash`、`zsh -n etc/_devbase`、`devbase-base:latest` の shellcheck（建て直さず、手元にあるイメージで打つ） |
| 基準の測定 | `uvx ruff check --select C901 --config 'lint.mccabe.max-complexity=0' <ファイル>`（C901）、`uvx complexipy <ファイル>`（認知的複雑度）、Python の行数は `ast` の `lineno`・`end_lineno`、シェルの関数の行数と式の重なりは `grep -n` で数える。変更の前後の値を Pull Request の本文に並べる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「このリポジトリ」。本番のコードは `lib/devbase/`、補完は `etc/`、テストは `tests/`。テストの共通の補助はそれを使うテストと同じディレクトリ（`tests/cli/`・`tests/volume/`・`tests/containers/`）に置き、`lib/` へ置かない |
| コーディング規約 | 周りのコードの書き方に合わせる（`AGENTS.md` の「書き方」）。補完のスクリプトは bash 3.2（macOS）と zsh で動く書き方を保つ |
| テスト戦略 | 振る舞いは既存のテストで固定する。構造の変更のためにテストを足すのは、既存のテストが振る舞いを固定していない経路だけで、足したテストは変更の前のコードで通る。兆候の消滅は測定で確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 着手前と変更の後の全体のテスト、lint、基準の測定、R1〜R10 と R11〜R15 のコミットを分けること |
| 確認してから行う | テストの期待値の変更（理由を本文に書いてから。承認ゲート 2 で人が見る） |
| 行わない | 表に無い関数の構造改善、出力の文言・終了コード・補完の候補の変更、#490・#365 の範囲の変更、`env -u DEVBASE_ROOT` を外したテストの実行、base イメージの変更と建て直し（P1）、`devbase up`（作業用のプロジェクトを含む。P3）、`CHANGELOG.md` の記入（利用者に見える変更が無いため） |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 15 件の「直す / 直さない」の確定（要求の見立てを変えるときは理由を残す） | 設計の担当（設計文書の決定の記録） | 設計 PR |
| R10 の分け方（語の位置ごとの関数か、グループごとの関数か、その両方か） | 設計の担当 | 設計 PR |
| R13 の分け方（失敗を返す前段の検査と、版・削除済みの応答の組み立てをどう分けるか） | 設計の担当 | 設計 PR |
| R5 のホームのパスの定数を置くモジュール（`manager.py` の定数の近くか、`compose.py` か） | 設計の担当（前提 7 の範囲で） | 設計 PR |
| R11・R15 の共通の補助の置き場（`conftest.py` か、取り込み用のモジュールか） | 設計の担当 | 設計 PR |
