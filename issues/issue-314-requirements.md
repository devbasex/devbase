# #314: env init / sync が nyle のホストの鍵（GCP・AWS）を別会社のグループ（kkg・with）へ確認なしに取り込む

正は課題の本文（#314）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

起票時の本文をそのまま引く。

> ## 何が起きたか
>
> `devbase env init` を kkg グループで流すと、[6/8] GCP認証 が `~/gcp-credentials/` の nyle のサービスアカウント鍵（`bigquery-full@nyle-carmo-analysis`）を見つけて、**確認を挟まずに** `GCP_CREDENTIALS_BASE64__default` として登録する。直後の「アクティブプロファイル」の質問には「設定しない」の選択肢が無く、空でも未知の名前でも `default` になる（`lib/devbase/env/collectors/google.py:97-106`。97-98 行で見つけたプロファイルを全件登録し、102-106 行で未知の名前を既定のプロファイルへ落とす）。kkg は GCP を使わない（AWS と Azure だけ）。
>
> 同じことは with で既に起きていた。with のチームのグローバル（`devbase/team/with/global`）に次が入っていた（2026-09-27 に手で削除済み。KV v2 の前の版には残っている）。
>
> | キー | 中身 |
> | --- | --- |
> | `GCP_CREDENTIALS_BASE64__default` | nyle の BigQuery サービスアカウント鍵 |
> | `GCP_ACTIVE_PROFILE` / `GOOGLE_APPLICATION_CREDENTIALS` / `BIGQUERY_KEY_FILE` | 上の鍵を指す設定 |
> | `GOOGLE_CLOUD_PROJECT` / `BIGQUERY_PROJECT` | `nyle-carmo-analysis` |
> | `AWS_CONFIG_BASE64` | ホストの `~/.aws` 丸ごと。nyle の SSO・プロファイル（carmo-dev / nyle-carmo / iam / infra-admin）と nyle-carmo・carmo-dev の静的な鍵を含み、lixil・kkg など他社のプロファイルも入っている |
>
> with-ai-dev はプロジェクトの `env` で `GCP_AUTH_MODE=adc` や `GOOGLE_CLOUD_PROJECT=` を書いて打ち消しているが、これは配る側で塞いでいるだけで、機密そのものは別会社のグループの置き場に入っていた。
>
> ## 原因
>
> collector はホスト全体の置き場（`~/gcp-credentials/`・`~/.aws/`・`~/.git-credentials`）を読み、対象のグループを見ずに見つけたものを全部、今のグループへ書く。collector は対象のグループを受け取らない（`lib/devbase/env/collector.py` の契約にグループが無い）。AWS は `~/.aws` の `config` と `credentials` を丸ごと 1 つの tar にする（`lib/devbase/env/collectors/aws.py:73` の `_encode_aws_config_files`）。グループが分かれる前（PLAN56 より前）の、置き場が 1 つだった頃の振る舞いのままである。
>
> アカウントグループの宣言は必須で、`default` は予約語として弾かれる（#315）。このため、どのグループのときに取り込んでよいかを、グループ名から決める手がかりは devbase に無い。ホストのファイルがどのグループの持ち物かを devbase は知らない。
>
> ## 期待
>
> - ホストのファイルから取り込む collector（GCP・AWS・Git）は、対象のグループを問わず、**勝手に登録しない**。見つけた候補を示して、取り込むかどうか・どれを取り込むかを尋ねる。既定は「取り込まない」
> - 尋ねずに取り込むグループを設けるなら、利用者の設定で名指しする（例: `backend.yml` のグループごとの許可）。devbase はグループ名を決め打ちしない
> - GCP のアクティブプロファイルの質問に「設定しない」の選択肢を足す。選んだら鍵も鍵モード用の変数も書かない
> - AWS はホストの `~/.aws` を丸ごと入れず、そのグループのプロファイル（例: kkg なら `[profile kkg]` と `[sso-session kkg]`）だけを選んで入れられるようにする
> - `devbase env sync --group <g>` も同じ規則に従い、参照に無いキー（取り込んでいないもの）を後から足さない。今の sync は参照にキーが無ければ書かない（`lib/devbase/commands/env.py:761` の `_sync_unregistered`）ので、この振る舞いをテストで固定する
>
> ## 現象レイヤーと修正レイヤー
>
> | 区分 | 場所 |
> | --- | --- |
> | 現象レイヤー | `lib/devbase/env/collectors/google.py`・`aws.py`・`git.py` |
> | 修正レイヤー | `lib/devbase/env/collector.py` の契約と `lib/devbase/commands/env.py` の `cmd_env_init`。対象のグループと取り込みの方針を、呼び出し側から collector へ渡す |
>
> ## 修正方針
>
> 新設する。collector ごとに確認の分岐を足すのではなく、`cmd_env_init` が対象のグループと取り込みの方針（尋ねる・取り込まない・許可したグループでは今までどおり取り込む）を決め、`collector.py` の契約を通して各 collector へ渡す。各 collector は、渡された方針に従って候補を示し、選ばれたものだけを書く。
>
> ## 受け入れ条件（案）
>
> - [ ] `env init --group kkg` で、GCP・AWS・Git の各ステップが確認なしに書かない。全部断れば、そのグループのチームのグローバルに GCP・AWS のキーが 1 つも入らない
> - [ ] GCP のアクティブプロファイルの質問で「設定しない」を選べる
> - [ ] AWS はプロファイル単位で選んで入れられる
> - [ ] `env sync --group kkg` が、init で断ったものを足さない
> - [ ] 取り込みを許すと設定したグループでは、今の init・sync と同じ結果になる
>
> 関連: #182 / #184（置き場のグループ分け）、#133 / #134（グループを跨いだ鍵の配布）、#315（グループの宣言の必須化）
>

## 目的

- ホストの資格情報ファイル（`~/gcp-credentials/`・`~/.aws/`・`~/.git-credentials`・`git config --global`）の中身が、利用者が選ばない限り、どのグループの参照にも入らない状態にする
- 取り込みを許すグループを利用者が名指ししたときだけ、今の `env init` / `env sync` と同じ結果になる

## 前提

- 前提 1: 取り込みを許すグループの設定は端末ごとの設定で、リポジトリへコミットしないファイルに置く。置く場所と書式は `design` で決める（候補: `secrets/backend.yml`。backend が `auto` で `backend.yml` が無い端末でも読めること）
- 前提 2: 取り込みの方針は「尋ねる」「取り込まない」「取り込む」の 3 つ。設定でグループを名指ししなければ「尋ねる」になる。devbase はグループ名から方針を決めない（`nyle` などの名前を決め打ちしない）
- 前提 3: 方針が「尋ねる」で標準入力が端末でないとき（EOF を含む）は、質問を出さずに「取り込まない」として扱う。今の `safe_input` は EOF で既定の値を返すため、既定を「取り込まない」にすれば同じ結果になる
- 前提 4: 対象のグループが決まらないとき（`layout: flat` の設定でプロジェクトの外から打ったとき）は「尋ねる」を使う。`layout: flat` でもプロジェクトの中なら、宣言のグループで方針を引く
- 前提 5: GCP の候補を 1 件も取り込まないときは、GCP の共通設定（`GOOGLE_CLOUD_LOCATION` / `BIGQUERY_LOCATION` / `BIGQUERY_DATASETS`）も書かない。ADC で使う利用者は `devbase env set` で足す
- 前提 6: `~/gcp-credentials/` に候補が無いときの今の振る舞い（鍵ファイルのパスを手で入れる質問、空で飛ばす）は変えない。手で入れた値はホストから勝手に拾ったものではないため
- 前提 7: Git の自動取得（`user.name` / `user.email` / `credential.helper` / `~/.git-credentials` / そこから取る GitHub の token）は 1 つの確認にまとめる。断ったら Git の値を 1 つも書かず、手入力の質問へも進まない。値は `devbase env set` で足す
- 前提 8: AWS の候補は `~/.aws/config` のプロファイルである。選んだプロファイルが `sso_session` で指す `[sso-session <名前>]` と、`source_profile` で指すプロファイル（連なりをたどる）を含め、含めた理由を示す。`~/.aws/credentials` からは、含めたプロファイルと同じ名前の節だけを入れる
- 前提 9: 取り込みの選択（AWS の選んだプロファイル、GCP の選んだプロファイル）は、同期済みハッシュの控えなど `sync` が読める場所に残す。残す場所と形は `design` で決める
- 前提 10: 選択の記録を持たない既存の控え（この変更より前の `init` が作ったもの）は、「`~/.aws` を丸ごと取り込んだ」ものとして扱い、`sync` は今と同じに更新する。ただし参照にキーが無ければ書かない（受け入れ条件 AC11）
- 前提 11: AWS の認証方法の選択肢（Config Files / SSO Profile / Access Key / スキップ）は残す。方針が「尋ねる」のときの既定を「スキップ」にする。SSO Profile は利用者が名前を手で入れるため確認を足さない。Access Key は `~/.aws/credentials` の `[default]` を自動で拾うため、確認を挟む

## 対象範囲

含む:
- `env init` の GCP・AWS・Git のステップが、取り込みの方針に従って候補を示し、選ばれたものだけを書くこと
- 呼び出し側（`cmd_env_init` / `cmd_env_sync`）が対象のグループと取り込みの方針を決めて、collector へ渡すこと（`collector.py` の契約の変更）
- GCP のアクティブプロファイルの質問の「設定しない」
- AWS のプロファイル単位の取り込み（`AWS_CONFIG_BASE64` の tar に選んだ節だけを入れる）
- `env sync` が、参照に無いキーを書かないこと（控えにソースが登録済みのときも含む）と、AWS の選んだプロファイルだけを入れ直すこと
- 取り込みを許すグループの設定の読み込み
- `docs/specifications/secret-backend.md` と利用者向けの文書の該当箇所の更新

含まない:
- 既に別会社のグループへ入った値の検出と削除（with のチームのグローバルは 2026-09-27 に手で削除済み。KV v2 の前の版の削除も含めない）
- ホストの資格情報ファイルをグループごとに分けて置く仕組み（`~/gcp-credentials/<グループ>/` のような置き場の新設）
- Slack・API キー・Devin・エディタ・Host の collector（ホストの資格情報ファイルを読まない。Host は既定値を補うだけ）
- `env project` / `export` / `import` / `backend migrate` の振る舞い
- グループを跨いだ鍵の配布（#133 / #134）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 対象のグループを決めた | `env init` / `env sync` の起動（`up` の子プロセスの `env init` を含む） | 宣言の不備で 1、`--group` の誤りで 2（今と同じ） | — |
| E2 | 取り込みの方針を決めた | E1 の後、取り込みを許すグループの設定を読んだ | 設定が壊れていれば、書き込む前に場所を示して非ゼロで止まる（AC13） | E1 |
| E3 | ホストの取り込みの候補を見つけた | 各 collector が `~/gcp-credentials/`・`~/.aws/`・`~/.git-credentials`・`git config --global` を読んだ | 読めないファイルは候補にせず警告を 1 行出す（今と同じ） | E2 |
| E4 | 候補を示して取り込むかを尋ねた | 方針が「尋ねる」で候補が 1 件以上ある | 標準入力が端末でなければ尋ねずに E5 を「0 件」で起こす（前提 3） | E3 |
| E5 | 取り込む候補を選んだ | 利用者の入力（既定は 0 件）。方針が「取り込む」なら全件、「取り込まない」なら 0 件 | 範囲外の番号・未知の名前は 0 件として扱い、1 行知らせる | E4（方針が「尋ねる」のとき） |
| E6 | GCP のアクティブプロファイルを決めた | E5 で GCP の候補が 1 件以上選ばれた | 未知の名前は「設定しない」ではなく既定に落ちる今の振る舞いを改め、もう一度尋ねるか「設定しない」にする（未決 3） | E5 |
| E7 | 選んだ値を参照へ書いた | E5・E6 の後、`env_file.save()` | 保存の失敗は今と同じ | E5（GCP は E6） |
| E8 | 取り込みの選択を控えへ残した | E7 の後の `_update_source_metadata` | 今と同じ（控えの失敗で参照を巻き戻さない） | E7 |
| E9 | `sync` がソースの変更を見つけた | `env sync` の起動 | 比べられなければ 1 行知らせて書かない（今と同じ） | E1・E8 |
| E10 | `sync` が選んだ範囲だけを入れ直した | E9 で変更があり、参照にキーがある | 参照にキーが無ければ書かずに 1 行知らせる（AC11） | E9 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 取り込み | ホストの資格情報ファイルから読んだ値を、対象のグループの参照へ書くこと。利用者がキーボードで入れた値を書くことは含まない |
| 取り込みの候補 | collector がホストで見つけた、取り込める値のまとまり。GCP は鍵ファイル 1 つ、AWS はプロファイル 1 つ、Git はホストの Git の設定の全体が 1 件 |
| 取り込みの方針 | 対象のグループについて、取り込みの候補を「尋ねる」「取り込まない」「取り込む」のどれで扱うか。呼び出し側が決めて collector へ渡す |
| 取り込みを許すグループの設定 | 取り込みの方針をグループごとに名指しする端末の設定。名指しの無いグループは「尋ねる」 |
| AWS のプロファイル | `~/.aws/config` の `[default]` または `[profile <名前>]` の節。compose のプロファイルとは別のもの |

## 受け入れ条件

取り込みを許すグループの設定を持たない端末で、`~/gcp-credentials/` に鍵ファイルが 2 つ、`~/.aws/config` にプロファイルが 3 つ以上（うち 1 つは `sso_session` を持つ）、`~/.git-credentials` と `git config --global user.name` がある状態を、以下「ホストに候補がある状態」と呼ぶ。

- [ ] AC1: ホストに候補がある状態で `devbase env init --group kkg` を打つと、GCP・AWS・Git の各ステップが、見つけた候補の一覧（GCP は鍵ファイル名と `project_id`、AWS はプロファイル名、Git は取り込むキーの名前）を出してから取り込むかを尋ねる。尋ねる前に参照へ何も書かない
- [ ] AC2: AC1 の各質問にすべて空の入力（Enter）で答えると、`kkg` のチームのグローバルに次のキーが 1 つも無い: `GCP_CREDENTIALS_BASE64__*`・`GCP_ACTIVE_PROFILE`・`GOOGLE_APPLICATION_CREDENTIALS`・`BIGQUERY_KEY_FILE`・`GOOGLE_CLOUD_PROJECT`・`BIGQUERY_PROJECT`・`GOOGLE_CLOUD_LOCATION`・`BIGQUERY_LOCATION`・`BIGQUERY_DATASETS`・`AWS_CONFIG_BASE64`・`AWS_PROFILE`・`AWS_DEFAULT_REGION`・`AWS_ACCESS_KEY_ID`・`AWS_SECRET_ACCESS_KEY`・`AWS_SSO_URL`・`GIT_USER_NAME`・`GIT_USER_EMAIL`・`GIT_CREDENTIAL_HELPER`・`GIT_CREDENTIALS_BASE64`・`GITHUB_PERSONAL_ACCESS_TOKEN`・`GH_TOKEN`
- [ ] AC3: 標準入力を `/dev/null` にして AC1 と同じコマンドを打つと、終了コード 0 で AC2 と同じ結果になる（`up` の子プロセスの `env init` も同じ）
- [ ] AC4: GCP の候補から 1 つだけを選ぶと、選んだ鍵ファイルの `GCP_CREDENTIALS_BASE64__<名前>` だけが書かれ、選ばなかった鍵ファイルのキーは書かれない
- [ ] AC5: GCP のアクティブプロファイルの質問に「設定しない」の選択肢が出る。選ぶと、`GCP_CREDENTIALS_BASE64__*`・`GCP_ACTIVE_PROFILE`・`GOOGLE_APPLICATION_CREDENTIALS`・`BIGQUERY_KEY_FILE`・`GOOGLE_CLOUD_PROJECT`・`BIGQUERY_PROJECT` を 1 つも書かない
- [ ] AC6: AWS の Config Files で `kkg` のプロファイルだけを選ぶと、`AWS_CONFIG_BASE64` を展開した `config` の節は `[profile kkg]` とそれが `sso_session` で指す `[sso-session <名前>]` だけで、`credentials` の節は `[kkg]` だけ（ホストに無ければ `credentials` を入れない）である
- [ ] AC7: AC6 で、選んだプロファイルが `source_profile` で指すプロファイルも `config` と `credentials` に入り、含めたことが出力に 1 行ずつ出る
- [ ] AC8: 方針が「尋ねる」のとき、AWS の認証方法の質問の既定は「スキップ」で、Access Key を選ぶと `~/.aws/credentials` の `[default]` の鍵を書く前に確認を挟む（既定は書かない）
- [ ] AC9: AC2 の後に `devbase env sync --group kkg` を打つと、`kkg` の個人共通とチーム共通のどちらにも AC2 のキーが 1 つも増えない
- [ ] AC10: AC6 の後に `~/.aws/config` の選ばなかったプロファイルだけを書き換えて `env sync --group kkg` を打つと、`AWS_CONFIG_BASE64` の中身は変わらず「変更なし」と出る。選んだプロファイルを書き換えると、選んだ節だけで入れ直す
- [ ] AC11: 控えにソース（`aws` / `git_credentials` / `gcp` のプロファイル）が登録済みで、参照からそのキーを消した状態でソースのファイルを書き換えて `env sync` を打つと、そのキーを書かずに 1 行知らせる（今は `_sync_source` と `_sync_gcp` が参照にキーが無くても書く）
- [ ] AC12: 取り込みを許すグループの設定で `nyle` を「取り込む」と名指しすると、ホストに候補がある状態の `env init --group nyle` は、この変更の前と同じ質問を同じ順で出し（取り込みの確認を足さない）、同じ入力に対して同じキーと値を書く。`env sync --group nyle` も前と同じキーと値を書く（AC11 の場合を除く）
- [ ] AC13: 取り込みを許すグループの設定が壊れている（形が不正・未知の方針）と、`env init` / `env sync` は参照を開く前に、ファイルの場所と直す箇所を示して非ゼロで止まる
- [ ] AC14: 「取り込まない」と名指ししたグループでは、`env init` は取り込みの質問を出さずに AC2 と同じ結果になる
- [ ] AC15: collector はグループ名を比べない。取り込みの方針は呼び出し側から渡る必須の引数で、方針を渡さずに GCP・AWS・Git の collector を呼ぶと `TypeError` になる（渡し忘れを既定の「取り込む」で通さない）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| セキュリティ | 取り込みの候補の一覧に機密の値を出さない（鍵ファイル名・`project_id`・プロファイル名・キーの名前だけ）。取り込みを許すグループの設定に機密を置かない |
| 移行性 | 既存の参照と控えをこの変更のために書き換えない。取り込みを許すグループの設定が無い端末では、すべてのグループが「尋ねる」になる（今まで確認なしで取り込んでいた端末は、次の `init` から尋ねられる） |
| 運用・保守性 | 新しい collector がホストの資格情報ファイルを読むとき、取り込みの方針を受け取らなければ登録できない（契約で強制する。AC15） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `env init` の質問が変わる（取り込みの確認・GCP の「設定しない」・AWS の既定）。コマンドの引数は変わらない。取り込みを許すグループの設定が増える |
| データ | 同期済みハッシュの控えに取り込みの選択が増える（前提 9）。既存の控えは読める（前提 10） |
| 既存の振る舞い | 設定の無い端末では、今まで確認なしで取り込んでいた値を尋ねるようになる。`env sync` は参照に無いキーを書かなくなる（AC11） |
| 内部の契約 | `Collector.collect_fn` の引数に取り込みの方針が増える |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `pytest tests/`（HOME と DEVBASE_ROOT を一時ディレクトリへ隔離した上で、ホストに候補がある状態を作る） |
| 静的解析・型検査 | プロジェクトの CI と同じ（`AGENTS.md` / `docs/specifications/ci-checks.md`） |
| 手動確認 | 実機の `~/gcp-credentials/`・`~/.aws/` を持つ端末で、`kkg` のプロジェクトから `devbase env init --reset` を打ち、全部断った後に `devbase env list -g --keys-only` で AC2 のキーが無いことを見る。`nyle` を「取り込む」と名指しして同じことをし、前と同じキーが並ぶことを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | collector は `lib/devbase/env/collectors/`、方針の決定は `lib/devbase/commands/env.py`、設定の読み込みは `lib/devbase/env/` の下（場所は `design`）。`projects/*` は触らない |
| コーディング規約 | `AGENTS.md` と既存のモジュールの書き方 |
| テスト戦略 | collector の単体テストで方針ごとの書き込みを、`cmd_env_init` / `cmd_env_sync` のテストで方針の決定と受け渡しを、sync のテストで AC9〜AC11 を担保する。テストは実環境の `DEVBASE_ROOT` と `HOME` を継承しない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、HOME と DEVBASE_ROOT の隔離、`secret-backend.md` の該当箇所の更新 |
| 確認してから行う | 取り込みを許すグループの設定の置き場所の決定（`design` の PR で示す）、控えの形の変更 |
| 行わない | 既に入った値の削除、グループ名の決め打ち、範囲外の collector の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 取り込みを許すグループの設定の置き場所と書式（前提 1） | `design`（設計 PR のレビューで利用者が承認） | 設計 PR |
| 2. 候補の選び方の入力の形（番号の列・名前の列・1 件ずつ y/N） | `design` | 設計 PR |
| 3. GCP のアクティブプロファイルに未知の名前を入れたとき、もう一度尋ねるか「設定しない」にするか（E6） | `design` | 設計 PR |
| 4. 取り込みの選択を残す場所と形（前提 9） | `design` | 設計 PR |
