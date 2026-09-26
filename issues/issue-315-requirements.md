# #315: アカウントグループの default を廃止し、全プロジェクトでグループの宣言を必須にする

正は課題の本文（#315）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 背景
>
> アカウントグループ（nyle / with / kkg）で機密の置き場とホームのボリュームを分けている（#182 / #184）が、グループを宣言していないプロジェクトは `default` へ落ちる。`default` は機密ストアで `group_aliases: default → nyle` によって `team/nyle/…` へ読み替えられ、ボリュームは `devbase_home_default` を使う。つまり **`default` は実質 nyle だが、名前からはそう読めない**。
>
> - 38 プロジェクト中 36 がグループを宣言していない（宣言しているのは with-ai-dev = with、project-trygroup-prd = kkg だけ）
> - 決め方は `projects/<name>/env` → `$DEVBASE_ROOT/env` → `default`（`lib/devbase/env/groups.py`）。コンテナ側も `containers/base/entrypoint.sh:785` の `${DEVBASE_ACCOUNT_GROUP:-default}`
> - 別会社のプロジェクトを足して宣言を書き忘れると、黙って nyle の機密とボリュームを共有する（#133 はこれ）。`--group` を付けずに打つ機密のコマンドも nyle に当たる。#314（nyle のホストの鍵が kkg・with へ流れる）も、グループを明示せずに動けることが根にある
>
> ## やること
>
> 1. **グループの宣言を必須にし、フォールバックを無くす。** 宣言の無いプロジェクトは `devbase up` などで止め、宣言の書き方を示す。`$DEVBASE_ROOT/env` での全体の既定も認めない（名前を変えた `default` になるため）
> 2. **プロジェクトの外で打つ機密のコマンドで `--group` を必須にする**（`env init` / `sync` / `set` / `get` / `delete` / `list` など）。TUI は最初にグループを選ばせる
> 3. **`default` を実名にする。**
>    - 機密ストア: 実体は既に `team/nyle/…` にあるので `group_aliases` の `default → nyle` を外す
>    - ボリューム: `devbase_home_default` → `devbase_home_nyle` へ移す（手順とロールバックの設計が要る）
>    - entrypoint: `:-default` のフォールバックを外す（`containers/entrypoint.sh` の変更は `build --no-cache` が要る）
> 4. **新しいグループ `personal` を作る**（機密の置き場・ボリューム・OpenBao のポリシー）。個人・OSS のプロジェクトへ nyle の機密が入らないようにする
> 5. **全プロジェクトに `DEVBASE_ACCOUNT_GROUP` を書く。** `projects/*` は devbase-ext への symlink なので、PR は plugin repo 側に出す
>
> ## プロジェクトの割り当て
>
> | グループ | プロジェクト |
> | --- | --- |
> | nyle | adminer / bi-tools / car-pricing / carmo / carmo-ai / carmo-autonavi-wordpress / carmo-batch / carmo-cdk / carmo-column / carmo-contractors-app / carmo-contractors-public / carmo-gas / carmo-kaitori-lp / carmo-magazine / carmo-magazine-gatsby / carmo-magazine-wordpress / carmo-screening / carmo-system-console / carmo-system-serverside / carmo-three / carmo-website / carmo-website-gatsby / carmo-website-gatsby-usedcar / carmo-website-static / engineering-introduction-handbook / laravel-admin / nyle-dx / predict_contract |
> | personal | devbase / devbase-ext / ai-plugins / md-specgen / ideabase / investment / github_work_time |
> | with | with-ai-dev（宣言済み） |
> | kkg | project-trygroup-prd（宣言済み） |
> | 未決 | uttaro-system（業務委託の契約が終わり、当面は動かない） |
>
> nyle の行は名前から割り当てた。adminer・laravel-admin・engineering-introduction-handbook・predict_contract は実装の前に確かめる。
>
> ## 受け入れ条件（案）
>
> - [ ] グループを宣言していないプロジェクトの `devbase up` が止まり、宣言の書き方を示す
> - [ ] プロジェクトの外で `--group` を付けずに打った機密のコマンドがエラーになる（TUI はグループを選ばせる）
> - [ ] `default` という名前がグループとして使われない（置き場の読み替え・ボリューム名・entrypoint の既定）
> - [ ] `devbase_home_default` の中身が `devbase_home_nyle` へ移り、nyle のプロジェクトで今までどおりログインや MCP のトークンが使える。ロールバックの手順がある
> - [ ] `personal` のグループが機密の置き場・ボリューム・OpenBao のポリシーを持ち、上の 7 プロジェクトがそこで起動する
> - [ ] 全プロジェクトが `DEVBASE_ACCOUNT_GROUP` を宣言している
> - [ ] 既存のテストがすべて通る
>
> 関連: #314、#182 / #184、#133 / #134
>

## 目的

- どのプロジェクトも、どのアカウントグループの機密とホームのボリュームを使うかを自分の `env` で名指しし、書き忘れたときに別会社（nyle）の機密とボリュームへ黙って落ちない状態にする
- `default` という中身の読めない名前を無くし、機密の置き場・ボリューム・コンテナの中のどこでも実名（nyle / personal / with / kkg）で読めるようにする
- 個人・OSS のプロジェクトを nyle から切り離す置き場として `personal` を用意する

## 前提

- 前提 1: グループの宣言は `projects/<name>/env` の空でない `DEVBASE_ACCOUNT_GROUP` だけを認める。空の値（`DEVBASE_ACCOUNT_GROUP=`）は宣言が無いものとして扱う
- 前提 2: `$DEVBASE_ROOT/env` に `DEVBASE_ACCOUNT_GROUP` があれば、グループを決めるコマンドは黙って無視せずエラーで止まり、その行を消すよう示す（名前を変えた `default` を作らせない）
- 前提 3: グループの宣言を必須にするのは backend と version を問わない（ボリュームのグループはどの backend でも使う）。機密のコマンドの `--group` を必須にするのは、グループ別の置き場（`backend: openbao`・`version: 2`）を選んだ設定のときだけである（それ以外の設定では `--group` 自体を受け付けない今の振る舞いを変えない）
- 前提 4: `default` はグループ名として受け付けない予約語にする。`DEVBASE_ACCOUNT_GROUP=default` と `--group default` はエラーで止まり、移し先のグループを書くよう示す
- 前提 5: `openbao.group_aliases` のキーに `default` を持つ設定は、読み込み時にエラーで止まり、その対応を外すよう示す。`default` 以外の読み替えは今のまま使える
- 前提 6: ボリュームの移行は、移し先のグループを利用者が指定する手順にする。devbase は移し先を nyle と決め打ちしない（devbase は他の利用者にも配布され、その端末の `default` の中身が nyle とは限らない）。この端末の移し先は nyle とする
- 前提 7: 移行は元のボリューム `devbase_home_default` をコピーで移し、元を消さない。元を消すのはロールバックの期間が過ぎたと利用者が判断した後に、利用者が手で行う
- 前提 8: entrypoint の「`default` のときだけ `/persistent/ai` から取り込む初回シード」は、グループの実名化と合わせて無くす。取り込み元は PLAN39 より前の置き場で、この端末では移行済みである
- 前提 9: `personal` の機密は nyle から写さない。必要なキーは利用者が `env init --group personal` などで入れる。`devbase_home_personal` は空で作られ、personal のプロジェクトでは claude・gh・gcloud などのログインを取り直す
- 前提 10: OpenBao のサーバ側のポリシー（`team/personal/…` の読み書きを誰に許すか）は運用側のリポジトリ（carmo-cdk、#363 の系列）の持ち物で、devbase の変更に含めない。devbase の側は、ポリシーが無いときの 403 を今のエラーの形で出す
- 前提 11: 全プロジェクトを回すコマンド（`env export` / `import` / `migrate` / `backend use` の移行など）は、宣言の無いプロジェクトが 1 つでもあれば、書き込みの前に名前を挙げて止まる（一部だけを移さない）
- 前提 12: この変更は既存の利用者の起動を止める破壊的変更として扱い、リリースノートに宣言の書き方とボリュームの移行手順を載せる
- 前提 13: `projects/*` の `env` は plugin repo の持ち物で、PR は devbase ではなくそれぞれの plugin repo に出す。対象は volareinc/devbase-ext・takemi-ohama/devbase-ext・devbasex/devbase-samples の 3 つである

## 対象範囲

含む:
- グループの宣言が無いプロジェクトで、グループを決めるコマンド（`up` / `scale` など、ボリュームのグループか機密のグループを決めるもの）を止め、書き方を示すこと
- グループ別の置き場で、プロジェクトの外の機密のコマンドに `--group` を必須にすること。TUI の env の操作でプロジェクトの外を相手にするときに、最初にグループを選ばせること
- `default` を名前として使わないこと（置き場の読み替え・ボリュームの既定・entrypoint の既定・`devbase status` の表示・予約語）
- `devbase_home_<元>` から `devbase_home_<先>` へ中身を移す手順とロールバックの手順（コマンドか文書の手順。どちらにするかは設計で決める）
- `personal` のグループを使えるようにすること（置き場のパス `team/personal/…` とボリューム `devbase_home_personal`）
- 利用者向けの文書（`docs/user/` の env・スナップショット・CLI リファレンス）と確定仕様（`docs/specifications/secret-backend.md`）と用語集の更新
- 各 plugin repo の `projects/*/env` へ `DEVBASE_ACCOUNT_GROUP` を書く PR（前提 13）

含まない:
- OpenBao のサーバのポリシー・AppRole の変更（前提 10）
- #314（nyle のホストの鍵が kkg・with へ流れる）の修正そのもの。この変更は根の 1 つ（グループを明示せずに動けること）を塞ぐだけである
- `devbase_home_ubuntu`（全グループの共通のボリューム）の中身の分け直し
- 元のボリューム `devbase_home_default` の削除（前提 7）
- uttaro-system のグループの決定（未決）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | プロジェクトのグループの宣言を読んだ | `up` / `scale` / 機密のコマンドがプロジェクトの中で打たれた | 宣言が無い・空・予約語（`default` / `ubuntu` / 数字だけ）・`$DEVBASE_ROOT/env` に宣言がある → 副作用の前に止まり、書き方か直す場所を示す | — |
| E2 | 対象のグループを決めた | プロジェクトの外で機密のコマンドが打たれた（グループ別の置き場） | `--group` が無い → 置き場を開く前に止まる。TUI では選ぶまで先へ進まない | — |
| E3 | 置き場のグループ名へ写した | E1 か E2 の後、置き場のパスを組むとき | `group_aliases` のキーに `default` がある → 設定の読み込みで止まる | E1 または E2 |
| E4 | グループのボリュームを付けてコンテナを作った | `up` / `scale` | ボリュームと機密のグループが食い違う → 今の食い違いの検査で止まる | E1 |
| E5 | コンテナの中でグループの設定のリンクを張った | コンテナの起動（entrypoint） | `DEVBASE_ACCOUNT_GROUP` が渡っていない → entrypoint がエラーで終わり、コンテナは起動しない | E4 |
| E6 | 元のボリュームの中身を移し先のボリュームへ写した | 利用者が移行の手順を実行した | 移し先が既にあって空でない・元が無い・コンテナが元を使って動いている → 何も書かずに止まる | 移し先のグループを宣言したプロジェクトのコンテナが止まっている |
| E7 | 置き場の読み替え `default → nyle` を外した | 利用者が `backend.yml` を直した（または devbase の手順が直した） | 外した後に宣言の無いプロジェクトが残っていれば、そのプロジェクトは E1 で止まる | 全プロジェクトの宣言（E8） |
| E8 | 全プロジェクトがグループを宣言した | plugin repo の PR がマージされ、`devbase plugin` で手元へ届いた | 宣言の漏れ → そのプロジェクトの `up` が E1 で止まる | — |
| E9 | 移し先のボリュームで今までのログインと MCP のトークンが使えた | nyle のプロジェクトを `up` した | 使えない → ロールバックの手順（宣言と読み替えを戻し、元のボリュームへ戻す） | E6・E7・E8 |

E6・E7・E8 の順序は、利用者向けの移行手順の中で決める（未決の「移行の順序」）。

## 用語

| 用語 | 意味 |
| --- | --- |
| アカウントグループ | `DEVBASE_ACCOUNT_GROUP` の値。この変更の後は、プロジェクトの `env` での宣言が必須で、既定の値を持たない。ボリューム `devbase_home_<group>` の単位でもある |
| グループの宣言 | `projects/<name>/env` に書いた空でない `DEVBASE_ACCOUNT_GROUP` の行。グループを決める唯一の出所 |
| 置き場のグループ名 | パスに入れる名前。グループ名を `group_aliases` で読み替えた後の名前 |
| 対象のグループ | 1 回の操作が読み書きするグループ |
| グループのボリューム | アカウントグループごとの `devbase_home_<group>`。コンテナの `/persistent/group` にマウントされる |

## 受け入れ条件

グループの宣言（やること 1）

- [ ] 前提: `projects/<name>/env` に `DEVBASE_ACCOUNT_GROUP` が無い
      操作: そのプロジェクトで `devbase up` を打つ
      結果: 終了コードが 0 でなく、`projects/<name>/env` に `DEVBASE_ACCOUNT_GROUP=<グループ>` を書くよう示す文が出る。ボリューム・生成物（`.docker-compose.scale.yml`）・コンテナ・`pre-up` の副作用のどれも作られない
- [ ] `DEVBASE_ACCOUNT_GROUP=`（空）の宣言でも、上と同じく止まる
- [ ] 前提: `$DEVBASE_ROOT/env` に `DEVBASE_ACCOUNT_GROUP=nyle` があり、プロジェクトは宣言済み
      操作: `devbase up` を打つ
      結果: 終了コードが 0 でなく、`$DEVBASE_ROOT/env` のその行を消すよう示す
- [ ] 宣言の無いプロジェクトで `devbase scale` を打つと、`up` と同じく副作用の前に止まる
- [ ] 宣言の無いプロジェクトの中で機密のコマンド（`env get` など）を打つと、置き場を開く前に止まり、宣言の書き方を示す
- [ ] 宣言済みのプロジェクトの `up` は今までどおり起動する（`devbase_home_<宣言した名前>` がマウントされる）

`--group` の必須化（やること 2、グループ別の置き場のとき）

- [ ] プロジェクトの外（`$DEVBASE_ROOT` など）で `--group` を付けずに `env init` / `sync` / `list` / `set` / `get` / `delete` / `edit` を打つと、置き場を開く前に終了コード 2 で止まり、`--group <名前>` を付けるよう示す
- [ ] プロジェクトの外で `--group nyle` を付けて打った同じコマンドは、`team/nyle/…` を読み書きする
- [ ] プロジェクトの中で `--group` を省いたコマンドは、今までどおりプロジェクトのグループの置き場を相手にする
- [ ] グループ別の置き場でない設定（`version: 1` や file / age の backend）では、プロジェクトの外で `--group` を付けずに打ったコマンドの振る舞いが変わらない
- [ ] TUI の env の操作でプロジェクトの外（共通の範囲）を相手にするとき、置き場を読む前にグループの選択が出る。選択の候補に `default` が出ない

`default` の実名化（やること 3）

- [ ] `DEVBASE_ACCOUNT_GROUP=default` の宣言と `--group default` は予約語としてエラーになり、移し先のグループを書くよう示す
- [ ] `backend.yml` の `openbao.group_aliases` に `default` のキーがあると、機密のコマンドが設定の読み込みで止まり、その対応を外すよう示す
- [ ] `lib/devbase` と `containers/` に、グループ名の既定として `default` を返す・渡すコード（`DEFAULT_ACCOUNT_GROUP`・`${DEVBASE_ACCOUNT_GROUP:-default}`・`group="${N:-default}"`）が残らない（`grep` で確かめる）
- [ ] コンテナを `DEVBASE_ACCOUNT_GROUP` 無しで起動すると、entrypoint がグループが渡っていない旨を出して 0 でない終了コードで終わる
- [ ] `devbase status` をプロジェクトの外で打つと、アカウントグループの欄に `default` ではなく宣言が無いことが出る。宣言済みのプロジェクトの中で打つと、その名前が出る
- [ ] `docs/user/`・`docs/specifications/secret-backend.md`・用語集（`docs/glossary/glossary.json` と `docs/glossary.md`）に、`default` をグループの既定として説明する文が残らない

ボリュームの移行（やること 3）

- [ ] 移行の手順を実行すると、`devbase_home_default` の中身（ファイル・ディレクトリ・持ち主・権限・シンボリックリンク）が `devbase_home_nyle` へ写り、`devbase_home_default` が残る
- [ ] 移し先が既にあって空でない・元が無い・元を使うコンテナが動いている、のどれかのとき、移行は何も書かずに止まり、理由を示す
- [ ] 移行の後、nyle のプロジェクト（例: carmo-ai）を `up` したコンテナで、`claude` のログイン・`gh auth status`・`gcloud config get account`・MCP のトークンが移行前と同じ結果を返す（手動確認）
- [ ] ロールバックの手順（宣言と読み替えを戻し、元のボリュームを使う）が利用者向けの文書にあり、その手順で移行前と同じ状態に戻る（手動確認）

`personal`（やること 4）

- [ ] `personal` を宣言したプロジェクトの `up` で `devbase_home_personal` が作られ、`devbase_home_nyle` がマウントされない
- [ ] `personal` を宣言したプロジェクトのコンテナの環境に、`team/nyle/…` にしか無いキーが入らない
- [ ] `devbase env list --group personal` が `team/personal/…` を読む（サーバのポリシーが揃った後。前提 10）
- [ ] 割り当ての表の personal の 7 プロジェクトが `personal` で起動する（未決の割り当てが決まった後の行は、決まった割り当てで判定する）

全プロジェクトの宣言（やること 5）

- [ ] 手元の `projects/*`（38 件）のうち、未決に残した uttaro-system を除く全件の `env` に空でない `DEVBASE_ACCOUNT_GROUP` がある（`projects/*/env` を読む 1 行のスクリプトで確かめる）
- [ ] 宣言の行は各 plugin repo の PR で入っており、devbase の PR には `projects/*` の変更が入らない

退行しないこと

- [ ] `pytest`・Ruff・ShellCheck の CI の検査ジョブがすべて通る
- [ ] with-ai-dev（with）と project-trygroup-prd（kkg）の起動と機密の読み書きが変わらない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 移行性 | 移す対象は `devbase_home_default` の中身と `backend.yml` の `default → nyle` の読み替え。元のボリュームを残し、宣言と読み替えを戻せばロールバックできる。移行の途中で止まっても、元のボリュームは書き換わらない |
| セキュリティ | どのコマンドも、宣言も `--group` も無いまま特定のグループ（とりわけ nyle）の機密の置き場やボリュームへ触れない。`personal` のコンテナへ nyle のチーム単位の機密が入らない |
| 運用・保守性 | 止まったときの文は、どのファイルのどの行を書けば（消せば）よいかを名指しする |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。宣言の無いプロジェクトの `up` / `scale` / 機密のコマンドが止まる。グループ別の置き場ではプロジェクトの外の機密のコマンドに `--group` が要る。`default` が予約語になる。互換の経路は持たず、リリースノートで移行を案内する（前提 12） |
| データ | ボリューム `devbase_home_default` の中身を移し先へ写す。OpenBao の `team/nyle/…` は移さない（既に実名の場所にある） |
| 既存の振る舞い | entrypoint の既定と初回シードが無くなる（イメージの作り直しが要る。`containers/entrypoint.sh` の変更は `build --no-cache` で反映する）。`devbase status` のグループの表示が変わる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | 宣言済み・未宣言・空・`$DEVBASE_ROOT/env` に宣言、の各プロジェクトで `devbase up` |
| テスト | `pytest`（`tests/` 全体。up / scale の harness は backend と docker を隔離する） |
| 静的解析・型検査 | Ruff、ShellCheck（CI と同じ基準の版） |
| 手動確認 | 移行の後に carmo-ai を `up` し、`claude` のログイン・`gh auth status`・`gcloud config get account`・MCP のトークンを移行前と比べる。ロールバックの手順を 1 度通す。personal のプロジェクト（例: ideabase）の `env` に nyle のキーが無いことを `devbase env list` で見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | devbase の変更は `lib/devbase`・`containers/base`・`docs`・`tests` に置く。`projects/*` は plugin repo の持ち物で、devbase の PR に入れない |
| コーディング規約 | `AGENTS.md` と CI（Ruff・ShellCheck）。グループ名の検証は `resolve_account_group` 1 か所に置き、同じ規則を別の場所へ写さない（`lib/devbase/env/groups.py` の方針） |
| テスト戦略 | 宣言の読み取りと予約語は単体テスト、`up` / `scale` の停止は副作用が無いことまで harness で、`--group` の必須化は CLI の単位で、entrypoint は ShellCheck と既存のシェルのテスト、ボリュームの移行は手動確認 |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、Ruff・ShellCheck、グループ名の文言と用語集の統一 |
| 確認してから行う | 手元の `backend.yml` の `group_aliases` を外すこと、ボリュームの移行の実行、plugin repo への PR、`build --no-cache` |
| 行わない | `devbase_home_default` の削除、OpenBao のポリシーの変更、#314 の修正、`projects/*` の変更を devbase の PR に入れること |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| adminer の割り当て。課題の表は nyle だが、実体は公開のサンプル（devbasex/devbase-samples）にある | 利用者 | plugin repo の PR の前 |
| devbase-ext の割り当て。課題の表は personal だが、実体は volareinc/devbase-ext の carmo-ai にある | 利用者 | plugin repo の PR の前 |
| predict_contract の割り当て。課題の表は nyle だが、実体は takemi-ohama/devbase-ext の personal にある | 利用者 | plugin repo の PR の前 |
| laravel-admin・engineering-introduction-handbook の割り当て（課題で確かめるとした 2 件。実体は volareinc/devbase-ext の carmo-system で、表の nyle と矛盾しない） | 利用者 | plugin repo の PR の前 |
| devbase-samples（公開のサンプル）の 5 プロジェクトの `env` に `personal` と書くか。書くとサンプルを使う他の利用者にもグループ名を押しつけ、書かないとサンプルがそのままでは起動しない | 利用者 | 設計 |
| uttaro-system のグループ。当面は宣言を書かず、`up` が止まる状態で据え置く | 利用者 | 動かすとき |
| 移行の順序（ボリュームの移行・読み替えを外す・全プロジェクトの宣言）と、移行をコマンドにするか文書の手順にするか | 設計 | 設計 PR |
| `devbase_home_default` を含む既存のスナップショットの系列を、移行の後にどう扱うか（移し先のボリュームへ復元できるか） | 設計 | 設計 PR |
| グループを決めるコマンドの一覧（`up` / `scale` のほかに `login` などが当たるか） | 設計 | 設計 PR |
