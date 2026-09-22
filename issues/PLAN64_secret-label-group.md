# PLAN64: 機密の参照の見出しに、グループの読み替えの前後を出す

対象 issue: devbasex/devbase#188

- ワークフローモード: `standard`
  - 根拠: 利用者が読む公開の出力の振る舞いを変える（`env backend test` と `env list` の見出し）。
    あわせて確定仕様 `docs/specifications/secret-backend.md` の自己矛盾を解く
- 参照: release Pull Request devbasex/devbase#212（`release/v3.7.0`）

## 目的

`group_aliases` のある端末で、機密の参照の見出しに出るグループ名と、その隣に並ぶ置き場のパスが
同じグループを指していると読めるようにする。あわせて、確定仕様の「参照の表示」と「文言の表示」の
2 つの記述が食い違ったままにしない。

## 曖昧語の具体化

| 依頼文の語 | 具体化 |
| --- | --- |
| 「見出し」 | 利用者が正常系の一覧として読む行に限る。`env backend test` の参照ごとの行（`lib/devbase/commands/env_backend.py`）と、`env list` のチーム共通・個人共通・プロジェクトの節の `=== ... ===` と末尾の件数の行（`lib/devbase/commands/env.py`）。エラー文言・警告・ログは含まない |
| 「読み替えの前後を出す」 | `OpenBaoSettings.display_group(group)` の返り値をそのまま使う。読み替えがあれば `default → nyle`、無ければ `default`（`→` を付けない） |
| 「矛盾が解ける」 | 仕様書の 2 つの記述が、どの文言がどちらの形になるかを重ならない形で述べ、どちらを読んでも同じ結論になる |
| 「変わらない」 | 出力の文字列がバイト単位で今と同じ |

## 前提

- 前提 1: `version: 1` とファイル backend（`plaintext` / `age`）では `SecretRef.group` が常に `None` に
  なる（`SecretStore.ref_group` の契約、確定仕様「参照のグループ」）。見出しにグループが付かない。
  したがって「見出しが変わらない」ことは、グループの有無で分岐する形にすれば構造として保てる
- 前提 2: **`config.openbao` が `None` かどうかで backend の種類を判定してはならない。**
  `_openbao_from_dict`（`lib/devbase/env/backend_config.py`）は `backend: age` でも `openbao:` 節が
  残っていれば設定を返す。ファイル backend で `None` になるのは、節が無く `version: 1` のときだけである
  （2026-09-22 に確認）。表示の分岐は `SecretStore.storage_group(ref.group)` に任せる。この 1 つが
  backend の種類・設定の有無・`layout`・グループの有無をまとめて見て、当たらなければ `None` を返す
- 前提 3: `OpenBaoSettings.display_group` は `storage_group` を経由する。名前の検証と予約語の検査で
  `BackendConfigError` を送出しうる。**誤りを伝える文言の組み立ての中では呼ばない。** 見出しは
  対応するパスの解決が成功した後に出る（`path_of` も `storage_group` を通る）。そのため
  見出しの経路で新たに失敗する場面は生じない
- 前提 4: 一覧の桁揃え（`env backend test` の `{...:<28}`）は文字数で数えており、全角文字の表示幅を
  数えない。読み替えの無い状態でも `プロジェクト 'carmo-ai'（グループ default）` は 31 文字で 28 を
  超えている（2026-09-22 に実測）。桁あふれは本 issue の変更で始まるものではない
- 前提 5: この端末の機密の置き場は本番の系である。実機での確認は読み取りに限る
  （`env backend status` / `env backend test` / `env list --keys-only`）。`env set` / `migrate` /
  backend の切り替えは行わない

## 対象範囲

含む:

- `SecretRef.label()` の契約（グループの表示をどう決めるか）
- `env backend test` の参照ごとの行の見出し
- `env backend migrate` の移行の計画の一覧と、`--to age` の完了後の「サーバ上の機密はそのまま残っています」の一覧
- `env list` のチーム共通・個人共通・プロジェクトの節の見出しと末尾の件数の行
- `docs/specifications/secret-backend.md` の 2 つの記述の書き分けと、`list` の見出しの例
- 読み替えのあるグループを使う新しいテスト
- 利用者向け文書の見出しの例（`docs/user/env-backend.md`、`docs/user/cli-reference/03-env.md`）と CHANGELOG

含まない:

- エラー文言・警告・ログに出る `label()` の表示（前提 3 の理由で読み替え前の名前のまま）
- `env backend status` の表示（すでに `display_group` を使っており、変えない）
- `env encrypt` / `env decrypt` の一覧（`lib/devbase/commands/env_migrate.py`）と `env rekey` の一覧
  （`lib/devbase/commands/env_ops.py`）。参照を `group=` を渡さずに組んでいるため、`ref.group` が
  常に `None` になり、グループが見出しに出ない（`env_migrate.py:85-94`、`env_ops.py:56-63`。
  2026-09-22 に確認）
- すでに `OpenBaoSettings.display_group` を直接呼んでいる文言。読み替えの前後を今も出しており、
  変えない。`bundle.py` / `io_import.py` / `env_backend.py` の「別の置き場のプロジェクト」の列挙と、
  `lib/devbase/commands/env.py` の `_project_group_mismatch`（`--group` がプロジェクトのグループと
  違う置き場である旨の文言）がこれにあたる
- 一覧の桁幅（`{...:<28}` / `{...:<40}`）の変更（前提 4）
- `group_aliases` の設定方法・置き場のパスの組み立て・キャッシュの位置
- `DEVBASE_ACCOUNT_GROUP` の警告文に付く ` --group <読み替える前の名前>` の引数
  （引数は読み替える前の名前でなければ通らないため、変えない）
- `tests/conftest.py` による `DEVBASE_ROOT` の隔離（別の Pull Request #217 が扱う）
- `lib/devbase/commands/env_migrate.py` の使われていない `Target.label`（#222 として起票。出力が変わらない）
- 新しい永続データ・画面の追加（そのため ER 図・テーブル定義・CRUD 図・画面遷移図を作らない）

## 受け入れ条件

見出しの表示（#188）:

- [ ] 1. 前提: `backend: openbao` / `version: 2` / `group_aliases: {default: nyle}`、対象のグループが `default`
      操作: `devbase env backend test` を実行する
      結果: チーム共通の行の見出しが `グローバル（グループ default → nyle）` になる。個人共通は
      `個人のグローバル（グループ default → nyle）`。隣に並ぶパスは今と同じ（`…/team/nyle/global`）
      検証: 新規テスト（`env backend test` の出力の行を読む）
- [ ] 2. 前提: 1 と同じ設定で、プロジェクト `web` のグループが `default`
      操作: `devbase env list` を `projects/web` で実行する
      結果: `=== グローバル（グループ default → nyle） (...) ===` と
      `=== プロジェクト: web（グループ default → nyle） (...) ===` になり、末尾の件数の行
      （`グローバル（グループ default → nyle）: N変数` / `プロジェクト（グループ default → nyle）: N変数`）も同じ形になる
      検証: 新規テスト（`capsys` の出力を読む）
- [ ] 3. 前提: 1 と同じ設定
      操作: `devbase env backend migrate --to age --dry-run` を実行する
      結果: 移行の計画の一覧の見出しが `グローバル（グループ default → nyle）` になる
      検証: 新規テスト
      （2026-09-22 追加。issue 本文は `test` と `list` だけを挙げるが、同じ一覧の形で
      `label()` とサーバのパスを並べる箇所が `lib/devbase/commands/env_backend.py` に 2 つあり、
      直さないと同じ食い違いが残るため）
- [ ] 4. 前提: 1 と同じ設定で、対象のグループが `kkg`（`group_aliases` に対応が無い）
      操作: `devbase env backend test` / `devbase env list` を実行する
      結果: 見出しは `グローバル（グループ kkg）` のまま。`→` は出ない
      検証: 新規テスト

変わらないこと:

- [ ] 5. `version: 1`（`layout: flat`）の `openbao` backend で、`env backend test` と `env list` の見出しに
      グループが付かない（今と同じ文字列）
      検証: 新規テスト。`env list` の見出しが `=== グローバル (` で始まり、出力全体に `（グループ` が
      1 つも出ないことを見る。`env backend test` の参照ごとの行も同じ。あわせて既存テスト
      （`tests/env/test_groups.py`・`tests/env/test_runtime.py`）が変更なしで通ること
      （2026-09-22 変更。既存テストは `'=== グローバル'` の前方一致で見ており、見出しに
      `（グループ default → nyle）` が付いても通ってしまうため、これだけでは条件を確かめられない）
- [ ] 6. ファイル backend（`plaintext` / `age`）の `env list` の見出しが今と同じ文字列
      検証: 新規テスト。`openbao:` 節を残した `backend: age` の設定でも、見出しに `（グループ` が
      出ないことを見る（前提 2 の取り違えをそのまま検査する）。あわせて既存テスト
      （`tests/commands/test_env_user_axis.py`）が変更なしで通ること
      （2026-09-22 変更。理由は条件 5 と同じ）
- [ ] 7. `SecretRef.label()` を通して参照を出すエラー文言・警告・ログは、読み替えのあるグループでも
      読み替える**前**の名前のままである。例は `layout: flat` でグループ付きの参照を拒む例外
      （`lib/devbase/env/openbao.py` の `OpenBaoBackend._check_group`）と、置き場の
      `DEVBASE_ACCOUNT_GROUP` を使わない旨の警告（`lib/devbase/env/runtime.py`）である
      検証: 新規テスト 1 件（読み替えのあるグループで、この 2 つの文言に `→` が出ないこと）
- [ ] 8. `env backend status` の `グループ: default → nyle` が今と同じ
      検証: 既存テストが変更なしで通ること

仕様書（#188）:

- [ ] 9. `docs/specifications/secret-backend.md` の「`label()` はグループがあれば `（グループ <名前>）` を
      後ろに付ける」と「文言には読み替えの前と後を `default → nyle` の形で出す（`display_group`）」が、
      どの文言がどちらになるかを重ならない形で述べている
      検証: 設計 Pull Request と実装 Pull Request のレビュー（読んで確かめる）
- [ ] 10. 同文書の `list` の見出しの例に、読み替えのある場合（`=== グローバル（グループ default → nyle） (...) ===`）が
      加わっている
      検証: 同上
- [ ] 11. 利用者向け文書（`docs/user/env-backend.md`、`docs/user/cli-reference/03-env.md`）の見出しの例に
      読み替えのある場合が加わっている
      検証: 同上

退行しないこと:

- [ ] 12. 全体テスト（`uv run pytest tests/ -q`）が通る
      検証: 実装 Pull Request の本文に実行結果を載せる（`release/v3.7.0` を base にした Pull Request では
      CI が 1 件も動かないため、手元の実行が唯一の証跡になる。#216）

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる: `group_aliases` のあるグループでのみ、`env backend test` / `env list` / `env backend migrate`（計画の一覧と `--to age` の完了表示）の見出しのグループ名が `default` から `default → nyle` になる。CHANGELOG では Fixed に書く |
| データ | 変わらない。置き場のパス・キャッシュの位置・`backend.yml` の内容は同じ |
| 既存の振る舞い | `SecretRef.label()` の既定の返り値は変えない。エラー文言・警告・ログは今のまま |
| 確定仕様 | `docs/specifications/secret-backend.md` の 2 か所の記述を書き分け、例を 1 つ足す |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest tests/ -q`（`release/v3.7.0` を base にする Pull Request では CI が動かないため手元で行い、結果を Pull Request 本文へ載せる） |
| 静的解析 | `ruff check --select=E9,F63,F7,F82 lib` |
| 手動確認 | この端末（`group_aliases: {default: nyle}`）で `devbase env backend test` と `devbase env list --keys-only` を打ち、見出しとパスが同じグループを指すことを見る。**読み取りのみ**（前提 5） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 参照の値と表示の文言は `lib/devbase/env/secret_store.py`、グループの読み替えの規則は `lib/devbase/env/backend_config.py`、コマンドの出力は `lib/devbase/commands/`。文言の組み立てはコマンド側へ写さない |
| コーディング規約 | 既存に合わせる（`ruff`）。`SecretRef` は `frozen=True` のまま |
| テスト戦略 | 表示の契約は単体テスト、コマンドの出力は `capsys` を使うコマンドのテスト。`DEVBASE_ROOT` は各テストが `monkeypatch.setenv` で自分で差し替える既存の流儀に合わせる（`tests/conftest.py` の autouse fixture による隔離は #217 が扱い、まだ `release/v3.7.0` に入っていない） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 手元で全体テスト、`ruff` |
| 確認してから行う | `SecretRef.label()` の引数の追加（設計 Pull Request の承認で確かめる）、確定仕様の書き分け |
| 行わない | エラー文言・ログの表示の変更、桁幅の変更、実環境への書き込み |

## 実装計画

設計は [PLAN64_secret-label-group-design.md](PLAN64_secret-label-group-design.md)。

## 依頼（原文）

devbasex/devbase#188 より。

> ## 何が起きたか
>
> `version: 2`（`group_aliases: {default: nyle}`）の端末で `devbase env backend test` を打つと、見出しが読み替え前の名前だけになる。
>
> ```text
>   グローバル（グループ default）          devbase/team/nyle/global                 22 変数
> ```
>
> `env backend status` は `グループ: default → nyle` と両方を出すのに、`SecretRef.label()` を使う見出し（`env backend test`・`env list`）は `default` だけで、隣のパス（`nyle`）と食い違って見える。
>
> 現象は v3.6.0 でも再現する。`SecretRef.label()`（`lib/devbase/env/secret_store.py:151`）は `self.group` をそのまま埋め込み、`storage_group` も `display_group` も呼ばない。
>
> ## 仕様書が自己矛盾している
>
> `docs/specifications/secret-backend.md` に、相反する 2 つの記述がある。
>
> | 箇所 | 記述 |
> | --- | --- |
> | 「`label()` はグループがあれば `（グループ <名前>）` を後ろに付ける」 | 読み替え前を出す |
> | 「文言には読み替えの前と後を `default → nyle` の形で出す（`display_group`）」 | 読み替え後も出す |
>
> `list` の見出しの例は `=== グローバル（グループ with） (...) ===` で固定されているが、`with` は読み替えの無いグループなので、この例では前後が一致してしまい矛盾が表に出ない。**コードを直す前に、どちらを採るかを仕様の側で決める必要がある。**
>
> ## 修正レイヤー
>
> **現象レイヤー**: `env backend test` の見出し（`lib/devbase/commands/env_backend.py:472`）と `env list` の見出し（`lib/devbase/commands/env.py` の `cmd_env_list` / `_group_suffix`）。
>
> **修正レイヤー**: `SecretRef.label()`（`lib/devbase/env/secret_store.py:151`）が返すグループの表示。呼び出される側の契約であり、`env list` の `_group_suffix` は「文言は `SecretRef.label()` が持ち、ここでは写さずに差分だけを取り出す」と明記して label() に従っている。見出しを 1 か所ずつ直すと、この従属関係が崩れる。
>
> `label()` は `SecretRef` にあり `OpenBaoSettings` を知らないため、読み替えの前後を出すには表示の側へ設定を渡す必要がある。`label()` のコメントは「チーム単位の文字列は変えない（誤りの伝達や桁揃えに埋め込まれている）」と断っているので、`label()` にグループの表示だけを差し替える引数を足すか、表示用のラッパを 1 つ置くかを設計で決める。
>
> **採る手**: 移動（`move_responsibility`）。グループの表示の責務を、呼び出し側から `label()` の契約へ寄せる。
>
> ### 波及の範囲
>
> `label()` は `lib/` の中で **52 か所 / 10 ファイル**から呼ばれる。大半はエラー文言とログである。
>
> **既定の振る舞いを変えると 52 か所すべてに及ぶ。** 見出しだけを変えるなら、差し替えるのは `commands/env_backend.py:472` と `commands/env.py` の `_group_suffix` の 2 か所で足りる。どちらにするかが設計の分かれ目である。
>
> ## 期待すること
>
> 見出しのグループ名も `status` と同じく読み替えの前後を出す（`OpenBaoSettings.display_group` を使う）。`version: 1` とファイル backend の見出しは変えない。
>
> ## 受け入れ条件
>
> - `group_aliases` のあるグループで `env backend test` と `env list` の見出しが読み替えの前後を出す
> - `docs/specifications/secret-backend.md` の 2 つの記述の矛盾が解け、`list` の見出しの例に読み替えのある場合（`default → nyle`）が加わる
> - `version: 1` とファイル backend の見出しが変わらない
