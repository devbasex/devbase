# PLAN56: 機密ストアのチーム共通と個人単位の置き場を、アカウントグループごとに分ける

- 発端: #182
- ワークフローモード: `standard`
  - 根拠: 機密の置き場のパスと、`env` コマンド・`env backend`・`devbase up` の注入の振る舞いを
    変える。`env` コマンドのオプション（公開インタフェース）が増え、`secrets/backend.yml` の形も
    変わる。対象には `tests/env/test_backend_config.py` / `tests/env/test_openbao.py` /
    `tests/env/test_cache.py` / `tests/env/test_runtime.py` / `tests/commands/test_env_user_axis.py`
    / `tests/commands/test_env_backend_migrate.py` / `tests/cli/test_up_roundtrips.py` がある
- 閉じる課題: #182（この端末の置き場の移し直しは別の `operation` の Pull Request で行い、そこで閉じる）
- 設計: `issues/PLAN56_secret-group-paths-design.md`

## 依頼（原文）

> ## 何をするか
>
> 機密ストア（OpenBao backend）のチーム共通と個人単位の置き場を、ボリュームと同じくアカウントグループ（`DEVBASE_ACCOUNT_GROUP`）ごとに分ける。グループは `nyle` / `with` / `kkg` の 3 つ。
>
> ## 背景
>
> - ボリュームは `DEVBASE_ACCOUNT_GROUP` ごとに `devbase_home_<group>` へ分かれている（`with-ai-dev` は `with`、`project-trygroup-prd` は `kkg`、未設定は `default` = 実質 nyle）
> - 一方、機密のチーム共通は `team/global` 1 つで、#172 の移行後は nyle の値（GCP プロジェクト、BigQuery のサービスアカウント鍵、Slack など）が入っている
> - そのため with / kkg のプロジェクトにも nyle の機密がいったん配られ、プロジェクトの `env` の空上書きで打ち消している（#152 と同じ、企業をまたいで混ざる形）
> - サーバのポリシーも「チーム共通を読める人は全グループの鍵を読める」ことになり、with / kkg だけの利用者を登録できない
> - #172 の単位 5 で `GCP_CREDENTIALS_BASE64__default`（nyle の BigQuery 用サービスアカウント鍵）を個人単位へ移したが、中身はチームの鍵だった。置き場を決める軸が足りないことの表れ
>
> ## 決まっていること（利用者、2026-09-15）
>
> - グループ名は `nyle` / `with` / `kkg`。いまの `default` は `nyle` として扱う
> - チーム共通だけでなく、個人単位（`users/<user>/…`）もグループで分ける（同じ人でも会社ごとに `GH_TOKEN` や AWS のプロファイルが違う）
> - v3.4.0 のマイルストーンで扱う（v3.4.0 は公開済みのため、配布は次の版）
>
> ## 未決（要求・設計の工程で決める）
>
> - `default` を `nyle` と読み替える方法（`DEVBASE_ACCOUNT_GROUP` の既定値を変えるのか、置き場だけ対応させるのか。ボリューム名 `devbase_home_default` との関係）
> - グループの外に置くもの（全グループ共通の機密）を持つか
> - ファイル backend（`age` / `plaintext`）でも分けるか
> - 移行の順序と、KV v2 の版の履歴に他グループの機密を残さない手順（#181 の記録にある教訓）

（「変わるところ（見込み）」の表と「関連」は #182 の本文にある。ここへは写さない）

## 目的

- `backend: openbao` の端末で、プロジェクトのコンテナへ届く機密を、そのプロジェクトの
  アカウントグループの置き場のものだけにする。別グループの機密を空上書きで打ち消す運用を
  要らなくする
- サーバがグループ単位で読み書きを許せる形（パスの先頭側でグループが分かれる）にする。
  `devbase up` 1 回が、対象のグループ以外のパスへ要求を出さない
- 設定を変えない端末（`backend.yml` が今の形のまま、またはファイル backend）の挙動を変えない

## 前提

- 前提 1: グループの分け方を使うかは `secrets/backend.yml` の設定で選ぶ。今の形の設定ファイルは
  書き換えなくても、今と同じパス（`team/global` など）を読み書きする。
  成否の判定: 受け入れ条件 9
- 前提 2: `DEVBASE_ACCOUNT_GROUP` の解決（`default` への既定を含む）とボリューム名
  `devbase_home_<group>` は変えない。`default` を `nyle` と読むのは**置き場のパスだけ**で、
  その対応は `backend.yml` に書く（devbase のコードに社名を持ち込まない）。
  成否の判定: 受け入れ条件 2
- 前提 3: 全グループ共通の置き場は作らない。全グループで同じ値を使う機密は、グループごとの
  置き場へ同じ値を置く（共通の置き場を持つと、そこへ企業固有の機密が再び混ざる。置き場を
  足すのは後から足しても局所的に直せる）。成否の判定: 受け入れ条件 1 の「他のパスへ要求を
  出さない」
- 前提 4: ファイル backend（`auto` / `age` / `plaintext`）はグループで分けない。1 台の端末の
  中に閉じており、サーバのポリシーという動機が当たらない。
  成否の判定: 受け入れ条件 10
- 前提 5: プロジェクトのアカウントグループは、機密を読む前に非機密設定（`projects/<name>/env`、
  `$DEVBASE_ROOT/env`）から決まる。機密の置き場に書いた `DEVBASE_ACCOUNT_GROUP` はグループの
  決定に使わない（置き場を決める値を、その置き場から読む循環になる）。
  成否の判定: 受け入れ条件 3・4
- 前提 6: この端末の既存の置き場（`team/global` 21 キー・`team/projects/*`・
  `users/takemi_ohama/*`）をグループ別のパスへ移し直し、古いパスを版の履歴ごと消す作業は、
  この変更の配布後に `operation` の Pull Request で行う。devbase に置き場を移し直す専用の
  コマンドは作らない（1 回きりの作業で、`env` の `--group` と `bao kv` で足りる。足りなければ
  その `operation` の計画で起票する）。成否の判定: 対象範囲の「含まない」
- 前提 7: サーバ（carmo-cdk）のポリシーをグループ単位にする変更は carmo-cdk で行う。今の
  ポリシー（`team/*` を読める・`users/<entity>/*` を読み書きできる）はグループ別のパスも
  そのまま含むため、devbase の変更はサーバの変更を待たずに配布できる。
  成否の判定: 受け入れ条件 1 を今のサーバで確かめられること（リリース後テスト）

## 対象範囲

含む:

- グループを含む置き場のパスの組み立てと、それを選ぶ `backend.yml` の設定
- `default` を置き場の上で別の名前へ対応させる設定
- `runtime.resolve()`（`devbase up` / `scale` / `env exec` など注入の全経路）が、対象の
  プロジェクトのアカウントグループのパスを読むこと。プロジェクトを切り替える経路を含む
- `env list` / `get` / `set` / `delete` / `edit` の対象グループの決め方と、グループを明示する
  オプション
- `env init` / `sync` / `project` / `export` / `import` が扱うチーム単位の参照を、対象の
  グループのものにすること
- `env backend status` の表示、`env backend use` での設定、`env backend test` が調べる参照、
  `env backend migrate --to openbao` でのグループの扱いと、移行からプロジェクトを外すオプション
  （`test` は 2026-09-15 に追記。設計の決定 8）
- `devbase up` の起動の前に、ボリュームのグループと機密のグループの食い違いを検査すること
  （2026-09-15 に追記。設計の決定 7）
- 手元のキャッシュ（`secrets/cache/`）をグループごとに分けること
- 利用者向け文書（`docs/user/env-backend.md`・`docs/user/environment-variables.md`・
  `docs/user/cli-reference/03-env.md`）の更新と、`docs/specifications/secret-backend.md` への
  取り込み（`plan-to-spec`）

含まない:

- この端末の既存の置き場の移し直しと古いパスの削除（前提 6、別の `operation`）
- carmo-cdk のポリシーの変更（前提 7。carmo-cdk へ起票する）
- ファイル backend のグループ分け（前提 4）
- 全グループ共通の置き場（前提 3）
- `DEVBASE_ACCOUNT_GROUP` の既定値・ボリューム名・entrypoint の変更（前提 2）
- コンテナの中の `bao` と `env token` の変更（接続先と token は変わらず、パスを知っているのは
  利用者である。文書に新しいパスの例を足すだけにする）

## 用語

| 用語 | 意味 |
| --- | --- |
| アカウントグループ（グループ） | `DEVBASE_ACCOUNT_GROUP` の解決結果。未設定なら `default`。ボリューム `devbase_home_<group>` の単位 |
| 置き場のグループ名 | パスに入れる名前。グループ名と同じだが、`backend.yml` の対応で読み替えたもの（この端末では `default` → `nyle`） |
| グループ別の置き場 | パスにグループ名を含む置き場。例: `team/<group>/global`、`users/<user>/<group>/projects/<name>` |
| 従来の置き場 | 今のパス（`team/global` など）。グループ別の置き場を選ばない設定で使う |
| 対象のグループ | 1 回の操作が読み書きするグループ。プロジェクトが決まればそのプロジェクトのグループ |

## 受け入れ条件

グループ別の置き場を選んだ設定（`default` → `nyle` の対応あり）と偽 OpenBao サーバを前提とする
条件は、その旨を「前提」に書く。パスの例は設計で決める既定の並びを使い、並びが変われば
設計の決定に合わせて書き換える（書き換えた事実を残す）。

- [ ] 1. 前提: グループ別の置き場。`projects/web/env` に `DEVBASE_ACCOUNT_GROUP=with`
      操作: `projects/web` の中で `devbase up`（docker の呼び出しは差し替える）
      結果: 偽サーバへの取得が `team/with/global` / `users/<user>/with/global` /
      `team/with/projects/web` / `users/<user>/with/projects/web` の 4 回だけで、認証は 1 回。
      それ以外のパス（`team/nyle/…`・従来の置き場を含む）への要求が 0 回
- [ ] 2. 前提: グループ別の置き場。`projects/api/env` にも `$DEVBASE_ROOT/env` にも
      `DEVBASE_ACCOUNT_GROUP` が無い
      操作: `projects/api` の中で `devbase up`
      結果: 取得するパスが `team/nyle/…` / `users/<user>/nyle/…` の 4 つ。生成される構成の
      グループのボリュームは `devbase_home_default` のまま
- [ ] 3. 前提: グループ別の置き場。`projects/web/env` に `DEVBASE_ACCOUNT_GROUP=with`
      操作: `projects/web/src`（下位ディレクトリ）で `devbase env set FOO=1`、
      `devbase env set -p FOO=1`、`devbase env set --user FOO=1`
      結果: 書き込み先がそれぞれ `team/with/global`、`team/with/projects/web`、
      `users/<user>/with/global`
- [ ] 4. 前提: グループ別の置き場。`team/with/global` に `DEVBASE_ACCOUNT_GROUP=nyle` が入っている
      操作: 条件 1 と同じ `devbase up`
      結果: 取得するパスは条件 1 と同じ（置き場に書いた値でグループが変わらない）
- [ ] 5. 前提: グループ別の置き場
      操作: `$DEVBASE_ROOT`（プロジェクトの外）で `devbase env list --group kkg`、`env get --group kkg KEY`、
      `env set --group kkg KEY=v`、`env delete --group kkg KEY`、`env edit --group kkg`
      結果: 読み書きの対象が `team/kkg/global`（`--user` を付ければ `users/<user>/kkg/global`）。
      一覧の見出しにグループ名が出る。
      ~~`-p` を付ければ実行時のプロジェクトの `team/kkg/projects/<name>`~~ → 条件 5a へ分けた
      （2026-09-15、設計の決定 6。`$DEVBASE_ROOT` では `-p` がそもそも使えない）
- [ ] 5a. 前提: グループ別の置き場（`default: nyle` の対応あり）。`projects/web/env` に
      `DEVBASE_ACCOUNT_GROUP=with`、`projects/api/env` に宣言なし
      操作: `projects/web` で `env set -p --group kkg FOO=1` と `env get --group kkg FOO`。
      `projects/api` で `env set -p --group nyle FOO=1`
      結果: `web` の `set -p` は両方のグループ名を述べて非ゼロで終了し、サーバへ要求を出さない。
      `web` の `get` は `team/kkg/global` / `users/<user>/kkg/global` だけを探す。`api` の `set -p` は
      `team/nyle/projects/api` へ書く（読み替えた後の名前で同じ置き場と判定する）
- [ ] 6. 操作: `--group` に使えない名前（`ubuntu`、`1`、`bad name`、`a/b`）を渡す
      結果: 1 件も読み書きせず、`DEVBASE_ACCOUNT_GROUP` の検証と同じ理由を述べて非ゼロで
      終了する。従来の置き場の設定とファイル backend で `--group` を渡しても、黙って無視せず
      非ゼロで終了する
- [ ] 7. 前提: グループ別の置き場。プロジェクト `api`（グループ `nyle`）の中
      操作: `devbase up web`（`web` のグループは `with`）
      結果: 認証 1 回。起動時の環境変数に `api` の 4 参照だけにあるキーが残っていない。
      ~~取得は `team/with/…` / `users/<user>/with/…` の 4 パスを含み、`web` の起動に
      `nyle` の置き場の値が使われない~~ → 取得は `team/with/…` / `users/<user>/with/…` の
      4 パスだけで、`nyle` の置き場へ要求しない（2026-09-15、目的の「対象のグループ以外の
      パスへ要求を出さない」に揃えた。設計の決定 11）。Python を直接起動する経路（TUI など）でも同じ
- [ ] 8. 前提: グループ別の置き場。`nyle` のプロジェクトと `with` のプロジェクトをそれぞれ 1 回
      `devbase up` した後、サーバへ到達できなくする
      操作: 2 つのプロジェクトで順に `devbase up`
      結果: どちらも自分のグループの控えで起動する（後から起動した方の控えが先の控えを
      上書きしていない）。一方のグループの控えが他方のプロジェクトに使われない
- [ ] 9. 前提: 今の形の `backend.yml`（グループ別の置き場を選ぶ設定が無い）
      操作: `devbase up`、`env list` / `get` / `set` / `delete` / `edit`、`env backend status`
      結果: 読み書きするパスとキャッシュのファイルの位置が変更前と同じ。既存のテストが
      期待値を変えずに通る
- [ ] 10. backend が `auto` / `age` / `plaintext` のとき、`up` の注入結果・`env` コマンドの
      出力と保存先・`env backend status` の出力が変更前と同じ（既存のテストが変更なしで通る）
- [ ] 11. 前提: グループ別の置き場
      操作: `devbase env backend status`（`projects/web` の中と `$DEVBASE_ROOT` で）
      結果: 対象のグループ名（読み替えがあれば `default → nyle` の形）と、そのグループの
      4 参照のパスが出る
- [ ] 12. 前提: ファイル backend で、`secrets/global.env.age` と `secrets/projects/web.env.age`
      （`web` は `with`）と `secrets/projects/csc.env.age` がある。移行先はグループ別の置き場
      操作: `devbase env backend migrate --to openbao --exclude-project csc`
      結果: 共通は `team/nyle/global`、`web` は `team/with/projects/web` へ書かれる。`csc` の
      参照はサーバへ書かれず、ファイルは退避されずに元の位置に残る。`--dry-run` は書き先の
      パスとキー名を出し、値を出さない。
      逆向き（グループ別の置き場の OpenBao から `--to age`）では、`$DEVBASE_ROOT/env` のグループの
      共通の参照と各プロジェクトのグループのプロジェクトの参照が age へ移り、他のグループの共通の
      参照は移さずにグループ名とパスを表示する（2026-09-15 に追記。設計の「`env backend migrate`」）
- [ ] 13. `env init` / `sync` / `project` / `export` / `import` は、対象のグループのチーム単位の
      参照だけを読み書きし、他のグループのパスへ要求を出さない。`export` は対象のグループに
      属するプロジェクトだけを集め、外したプロジェクト名を出す。`import` はバンドルに別グループの
      プロジェクトがあれば 1 件も取り込まずに名前とグループを挙げて非ゼロで終了する
      （2026-09-15 に追記。設計の決定 12）。`env sync` は同期済みのハッシュを置き場のグループごとに
      持ち、グループ A で同期した後でもグループ B の同期が変更を検出する（2026-09-15 に追記。
      設計の決定 13）
- [ ] 14. 機密の値・`secret_id`・token が、追加した出力（`status` のグループの行、`--dry-run`、
      誤りの文言）に載らない
- [ ] 15. `uv run pytest tests/` が全件通り、`ruff check lib` と `python -m compileall -q lib bin`
      が変更前と同じ結果
- [ ] 16. 前提: グループ別の置き場。`projects/web/env` に `DEVBASE_ACCOUNT_GROUP=with`
      操作: `projects/web` で `DEVBASE_ACCOUNT_GROUP=kkg devbase up`（ボリュームのグループが `kkg`）と、
      同じ環境変数での `devbase scale 2`
      結果: どちらも両方のグループ名と出所を述べて非ゼロで終了し、コンテナ・ボリューム・
      スナップショットを作らず、子プロセスの `env init` を起動せず、`project.local.yml` の `scale` を書き換えない。今の形の
      `backend.yml` では同じ操作で止めない（2026-09-15 に追記。設計の決定 7）
- [ ] 17. 前提: グループ別の置き場。`projects/` に `nyle` と `with` のプロジェクトがある
      操作: `projects/web`（`with`）で `devbase env backend test`
      結果: 偽サーバへの要求が `with` の置き場のパスだけで、`nyle` のプロジェクトの参照を
      調べない（2026-09-15 に追記。設計の決定 8）
- [ ] 18. 前提: グループ別の置き場。`team/with/global` が未作成で、`env init` の子プロセスは
      偽サーバへ `INIT_KEY=value` を保存して成功終了するものに差し替える
      操作: `projects/web`（`with`）で `devbase up`
      結果: 子プロセスが `team/with/global` へ書き、その `up` のコンテナへ `INIT_KEY` が渡る
      （2026-09-15 に追記。設計の決定 10）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | `devbase up` 1 回の往復は変更前と同じ（プロジェクト内で認証 1 回 + 取得 4 回）。グループの決定でサーバへの往復を足さない |
| 移行性 | 今の形の `backend.yml` とキャッシュはそのまま使える。グループ別の置き場へ切り替えた後に今のキャッシュが別グループの控えとして使われない。古い devbase がグループ別の置き場の設定を読んだとき、従来の置き場を黙って読まずに止まる |
| セキュリティ | 1 回の `up` / `env` 操作が要求するパスは対象のグループのものだけ（サーバがグループ単位のポリシーで拒んでも、対象のグループの操作は通る） |
| 運用・保守性 | グループの読み替え（`default` → `nyle`）は `backend.yml` の 1 か所にだけ書き、`status` で確かめられる |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`env list` / `get` / `set` / `delete` / `edit` に `--group`、`env backend migrate` に `--exclude-project` が増える。`backend.yml` に項目が増える。既存のオプションと既存の設定ファイルの意味は変えない |
| データ | サーバ上のパスの並びが変わる（選んだ端末だけ）。既存のパスの内容は devbase が移さない（前提 6） |
| 既存の振る舞い | グループ別の置き場を選んだ端末で、注入・`env` コマンド・キャッシュの位置が変わる。選ばない端末は変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest tests/`（偽 OpenBao サーバは `tests/conftest.py`） |
| 静的解析 | `ruff check lib` / `python -m compileall -q lib bin` |
| 手動確認 | リリース後に、この端末で `operation`（前提 6）の後に `projects/with-ai-dev` と nyle のプロジェクトで `devbase env backend status` と `devbase up` を行い、`env exec -- env` に別グループの機密が無いことを確かめる（`release-verification`） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 機密の置き場は `lib/devbase/env/`、コマンドは `lib/devbase/commands/`、引数は `lib/devbase/cli.py`。グループ名の検証は `lib/devbase/volume/manager.py` の `resolve_account_group` を再利用し、同じ検証を別の場所へ写さない |
| コーディング規約 | 既存のモジュールの docstring の密度と日本語の説明に合わせる。`ruff check lib` |
| テスト戦略 | パスの組み立てと設定の検証は単体（`tests/env/`）、`env` コマンドの宛先は CLI 層（`tests/commands/`）、`up` の往復とグループの切替は偽サーバを使う結合（`tests/cli/`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`ruff`、設定を変えない端末の互換の確認 |
| 確認してから行う | サーバ上の既存のパスへの書き込み・削除（この変更の中では行わない）、`DEVBASE_ACCOUNT_GROUP` の既定値の変更 |
| 行わない | carmo-cdk の変更、ファイル backend の構造変更、置き場を移し直す専用コマンドの追加 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| ~~`backend.yml` でグループ別の置き場を選ぶ形とパスの並び~~ → `version: 2` とパスの対応表（設計の決定 1、2026-09-15） | 設計 | 決定済み |
| ~~`default` の読み替えの書き方~~ → `openbao.group_aliases`（設計の決定 4） | 設計 | 決定済み |
| ~~`-p` と `--group` の組み合わせ~~ → プロジェクトのグループと違えば拒む（設計の決定 6） | 設計 | 決定済み |
| 前提 2〜4（`default` は置き場だけ読み替える・全グループ共通の置き場を持たない・ファイル backend は分けない） | 利用者（設計 Pull Request の承認で確定） | 設計 Pull Request のマージ |
