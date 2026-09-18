# PLAN62: 機密の置き場に書いた `DEVBASE_ACCOUNT_GROUP` を注入しない

対象 issue: devbasex/devbase#185

- ワークフローモード: `standard`
  - 根拠: 機密の注入（`runtime.resolve` / `inject`）の本番の振る舞いを変える。`version: 1` の挙動も変わる

## 依頼（原文）

> `runtime.inject()` は機密の置き場（`.env` / age / OpenBao）の値をそのままプロセスの環境変数へ載せる。置き場に `DEVBASE_ACCOUNT_GROUP` が入っていると、`volume.manager.resolve_account_group()`（プロセスの環境変数を読む）が決めるボリュームのグループがその値に変わる。
>
> - プロジェクトの `env` がグループを宣言していれば、ラッパーの `source` が後から上書きするので起きない
> - `$DEVBASE_ROOT/env` での宣言や、宣言が無いプロジェクトでは起きうる
> - `version: 1`（今の設定）でも同じ経路がある。PLAN56（#182 / #184）の `layout: group` では、機密の置き場のグループはファイルだけから決めるため、この場合は `up` / `scale` がボリュームとのグループの食い違いで止まる
>
> 注入の対象から `DEVBASE_ACCOUNT_GROUP` を外すかどうかは、`version: 1` の挙動を変えるため別に判断する。

## 目的

- アカウントグループ（ボリューム `devbase_home_<group>` と、`layout: group` での機密の置き場）を決める値が、
  `env` ファイル（`projects/<name>/env` / `$DEVBASE_ROOT/env`）とシェルの環境変数からだけ来るようにする。
  機密の置き場に書いた値が、どの設定（`version: 1` / `layout: group`）でもグループを変えないようにする

## 前提

- 前提 1: **注入の対象から外す**（issue の「外すかどうか」への答え）。`version: 1` でも外す。
  理由: 利用者向け文書（`docs/user/environment-variables.md` の「機密の置き場もグループで分ける（OpenBao）」と
  `docs/user/env-backend.md` の「グループの決まり方」）は PLAN56 以降
  「置き場に書いた値はグループの決定に使われない」と説明しており、`version: 1` だけ置き場の値が効く今の
  挙動は文書と食い違っている。置き場の値でグループを切り替える使い方は文書に無い
- 前提 2: 外す場所は機密の合成（`runtime.resolve`）の 1 か所にする。`inject`・`child_env`・コンテナへ
  列挙する変数名は、どれも `resolve` の結果から作られるため、1 か所で全経路に効く
- 前提 3: 置き場に書かれた値を見つけたときは、無視したことを警告で 1 回知らせ、消し方
  （`devbase env delete DEVBASE_ACCOUNT_GROUP`、置き場に応じて `-p` / `--user` / `--group`）を示す。
  値そのものは出さない
- 前提 4: `devbase env set DEVBASE_ACCOUNT_GROUP=...` は置き場へ書かずに拒否し、`env` ファイルへ書くよう案内する
  （終了コード 1）。書いても使われない値を新たに作らないため。`env import` / `env edit` / 既存の値は拒否しない
  （前提 3 の警告で知らせる）
- 前提 5: コンテナ内の `DEVBASE_ACCOUNT_GROUP` は今と同じく `volume/compose.py` が構成へ書く値
  （解決済みのグループ）で決まる。置き場の値がコンテナへ列挙されることは無くなる

## 対象範囲

含む:

- 機密の合成（`runtime.resolve`）から `DEVBASE_ACCOUNT_GROUP` を外すことと、その警告
- `devbase env set` での拒否
- 利用者向け文書（`docs/user/environment-variables.md` / `docs/user/env-backend.md` / `docs/user/cli-reference/03-env.md` の `env set`）の該当箇所と CHANGELOG

含まない:

- `DEVBASE_ACCOUNT_GROUP` 以外の「置き場に書くべきでない」キーの扱い（`DEVBASE_ROOT` など。必要なら別の issue）
- `env import` / `env edit` での拒否
- 置き場に残っている値の自動削除
- ボリュームのグループの決め方（`resolve_account_group`）と、`layout: group` の機密のグループの決め方の変更
- 新しい型・永続データ・画面の追加（そのためクラス図・ER 図・画面遷移図を作らない）。要求に非機能の条件は無い（注入の要求の回数・経路を変えない）

## 受け入れ条件

- [ ] 1. 前提: チーム共通の機密（`version: 1`・`plaintext` / `age` backend）に `DEVBASE_ACCOUNT_GROUP=kkg` があり、
      プロセスの環境変数に `DEVBASE_ACCOUNT_GROUP` が無い
      操作: `runtime.inject(root, project)` を呼ぶ
      結果: プロセスの環境変数に `DEVBASE_ACCOUNT_GROUP` が載らず、`resolve_account_group()` は `default` を返す
- [ ] 2. 前提: 1 と同じ置き場で、プロセスの環境変数に `DEVBASE_ACCOUNT_GROUP=with` がある（`env` ファイル由来）
      操作: `runtime.inject(root, project)` を呼ぶ
      結果: プロセスの環境変数は `with` のまま変わらない
- [ ] 3. 個人共通・プロジェクトのチーム・プロジェクトの個人の置き場にあっても、1・2 と同じになる
- [ ] 4. 置き場にあったときは、キー名と置き場の種類・消し方を含む警告が出て、値は出ない。
      1 回の CLI 起動で同じ置き場について 2 回以上出ない
- [ ] 5. `resolve()` の結果（`SecretEnv.names` / `values`）に `DEVBASE_ACCOUNT_GROUP` が含まれない。
      したがって dev コンテナの `environment` へ置き場の値は列挙されない
- [ ] 6. `layout: group` で、置き場（`$DEVBASE_ROOT/env` にも `projects/<name>/env` にもグループの宣言が無い
      プロジェクトの、共通の機密）に `DEVBASE_ACCOUNT_GROUP=kkg` があっても、`devbase up` の前の
      グループの食い違いの検査で止まらない
- [ ] 7. `devbase env set DEVBASE_ACCOUNT_GROUP=kkg`（`-p` / `--user` / `--group` 付きも同じ）は、置き場へ書かずに
      `env` ファイルへ書くよう案内して終了コード 1
- [ ] 8. 他のキーの注入（重ね順・注入の履歴・`clear_injected` による巻き戻し）の既存テストが変更なしで通る
- [ ] 9. 全体テスト（`uv run pytest tests/`）が通る

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる: `env set DEVBASE_ACCOUNT_GROUP=...` が拒否される。CHANGELOG に「変更」として書く |
| データ | 置き場の値は消さない |
| 既存の振る舞い | `version: 1` で置き場に `DEVBASE_ACCOUNT_GROUP` を書いていた利用者は、ボリュームのグループが `env` ファイルの宣言（無ければ `default`）へ戻る。警告で知らせる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest tests/ -q` |
| 静的解析 | `ruff check --select=E9,F63,F7,F82 lib` |
| 手動確認 | 検証用のプロジェクトで置き場に値を入れ、`devbase ps` 等で警告が出ること、`devbase env set` が拒否することを見る（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 注入の規則は `lib/devbase/env/runtime.py`、キー名は `lib/devbase/env/keys.py` |
| テスト戦略 | `resolve` / `inject` は `tests/env/` の単体テストで、差し替えの `SecretStore` を使う。`env set` は `tests/cli/` または `tests/env/` の既存の形に合わせる。`DEVBASE_ROOT` は tmp へ向ける |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体テスト |
| 確認してから行う | 前提 1（`version: 1` の挙動を変えること。設計 Pull Request の承認で確かめる） |
| 行わない | 置き場の値の自動削除、他のキーの扱いの変更 |
