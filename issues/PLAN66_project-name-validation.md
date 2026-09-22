# PLAN66: 名前の形に合わないプロジェクトを、作られた時点で知らせる

対象 issue: devbasex/devbase#203

- ワークフローモード: `standard`
  - 根拠: プラグインの同期と `env import` に検査を足すのは本番の振る舞いの追加である。
    `devbase plugin install` / `update` / `sync` と `devbase env import` の出力が変わる
- release Pull Request: #212（base は `release/v3.7.0`）

## 依頼（原文）

issue #203 より:

> ## 何を見つけたか
>
> プロジェクト名を検証する規則が、場所ごとに違います。
>
> | 場所 | 規則 | 用途 |
> | --- | --- | --- |
> | `lib/devbase/env/bundle.py` の `is_valid_project_name`（`_VALID_PROJECT_NAME_RE`） | `^[A-Za-z0-9_][A-Za-z0-9_.\-]*$`。先頭に `_` を許す | env の export / import の書庫の中の名前 |
> | `lib/devbase/env/secret_store.py` の `_validate_project_name` | 空と、`Path(name).name` と一致しない値（区切り文字を含む）と、`.`・`..` だけを弾く | 機密の保存先のファイル名 |
> | `lib/devbase/utils/names.py` の `SINGLE_SEGMENT_NAME_PATTERN` | `[A-Za-z0-9][A-Za-z0-9._-]*`。先頭は英数字 | 名前の指定（`devbase up <name>` や TUI の一覧） |
> | `lib/devbase/snapshot/manager.py` の `_VALID_NAME_RE` | `^[a-zA-Z0-9][a-zA-Z0-9._-]*$` | スナップショットの名前 |
>
> 3 つ目と 4 つ目は文字集合が同じで、別々の定数として 2 重に持っています。
>
> `_` 始まりのプロジェクトは、env の export / import はできますが、名前を指定した操作は
> できません。該当するプロジェクトは今のところ実在しません。
>
> ## 寄せないことは確定仕様になっている
>
> （中略）したがって「3 つの規則を 1 つへ寄せるか」は決着済みです。残っているのは、
> **名前の形に合わないプロジェクトが作られるのを防ぐかどうか**です。
>
> ## 修正レイヤー
>
> **修正レイヤー**: 名前を作る側。`lib/devbase/plugin/syncer.py` の `discover_projects` /
> `sync_projects` が `projects/` に名前を載せる唯一の入口で、今は `.` 始まりを外すだけで
> 名前の形を検査していません。
>
> （中略）検証の下流を揃えるのではなく、**生成の入口で弾くか警告するのが責務の場所**です。
> 入口で防げば、4 つの規則が違うままでも、名前の指定から操作できないプロジェクトは
> 生まれません。
>
> **採る手**: 移動（`move_responsibility`）。名前の形の責務を、使う側の検証から
> プラグインの同期へ移します。
>
> ## 決めること
>
> - プラグインの同期（`discover_projects` / `sync_projects`）で名前の形を検査するか。
>   弾くのか、警告に留めるのか
> - `utils/names.py` の `SINGLE_SEGMENT_NAME_PATTERN` と `snapshot/manager.py` の
>   `_VALID_NAME_RE` は文字集合が同じである。この 2 つだけを 1 つへ寄せるか。確定仕様が
>   「寄せない」としたのは用途と互換性が違う 3 つについてで、この 2 つは同じ形をしている

## 実測（本文の誤りを含む）

この作業ツリー（`release/v3.7.0` の先頭 688efde）で数え直した。**本文の 2 つの記述が
事実と違う。**

| # | 本文の記述 | 実測 | 手段 |
| --- | --- | --- | --- |
| 1 | 名前を検証する規則は「4 か所」 | プロジェクト名そのものは **6 か所**。`env/_import_merge.py` の `_PROJECT_ENV_RE`（書庫の中の `env/projects/<name>/.env` の名前）と、`bin/devbase` の `_SINGLE_SEGMENT_NAME_RE`（shell 版）と、`syncer.discover_projects` の `.` 始まりの除外が本文に無い。同じ文字集合の正規表現は他に `volume/manager.py` の `_GROUP_NAME_RE`（アカウントグループ名）がある | `grep -rn -E '\[A-Za-z0-9\]\[A-Za-z0-9\|\[a-zA-Z0-9\]\[a-zA-Z0-9' lib bin` ほか |
| 2 | `syncer` が `projects/` に名前を載せる**唯一の入口** | **違う。`devbase env import` が `projects/<name>/` を実ディレクトリとして作る。** 書庫に `env/projects/_foo/.env` が入っていると、隔離した root への import が終了コード 0 で `projects/_foo/` を作り、警告を 1 行も出さない | 下の「実験 1」 |
| 3 | 名前の形に合わないプロジェクトは「今のところ実在しない」 | **合っている。** 登録済み 3 リポジトリ・22 プラグインの `projects/` 直下 **133 件**すべてが名前の形に合う。`.` 始まり 0 件、先頭 `_` 0 件、ASCII 外 0 件。うち 3 件は未コミットの手元のディレクトリ（`predict_contract`・`car-pricing`・`carmo-screening`） | `plugins.yml` の 22 プラグインを辿って `re.fullmatch` で判定（読み取りのみ） |
| 4 | `SINGLE_SEGMENT_NAME_PATTERN` と `_VALID_NAME_RE` は「文字集合が同じ」 | 文字集合は同じだが**振る舞いが違う**。`_VALID_NAME_RE.match("abc\n")` は True（`re.match` + `$` は末尾の改行の前で一致する）、`is_single_segment_name("abc\n")` は False（`fullmatch`）。`env/bundle.py` の規則も同じ穴を持つ | 下の「実験 3」 |

## 実験の記録

### 実験 1: `env import` が `projects/_foo/` を作る

隔離した root で `import_bundle` を呼んだ。実環境の `DEVBASE_ROOT` は使っていない。
書庫には `env/projects/_foo/.env` と `env/projects/ok-name/.env` を入れた。

```
import rc = 0
projects/ の中身: ['_foo', 'ok-name']
_foo は実ディレクトリか: True symlink か: False
_foo/.env: A=1
```

同じ root で名前の解決を呼ぶと、`devbase list` には出るが名前では操作できない。

```
list_projects の名前: ['_foo', 'ok-name']
プロジェクト名に使えない形です: '_foo'（英数字で始まり、英数字・'.'・'-'・'_' だけからなる名前）
_foo  -> False
ok-name -> True
```

### 実験 2: 同期が作る別名も名前の形に合わないことがある

`sync_projects` は名前の衝突に敗れた側へ `<プロジェクト名>.<owner>` の別名を張る。
`owner` は `--link` のプラグインでは**元パスの basename そのまま**である
（`syncer._extract_owner`）。空白を含むパスから張ると、devbase 自身が名前の形に合わない
名前を作る。

```
owner = 'my plugin'
生成される別名 = 'carmo.my plugin' 名前の形に合うか: False
作られた symlink の数: 1
projects/ の中身: ['carmo.my plugin']
repos 由来の owner = 'github.com--volareinc--devbase-ext' → 'carmo.github.com--volareinc--devbase-ext' 形に合うか: True
```

## 実験 3: 2 つの規則の振る舞いの差

```
'_foo'    single: False snapshot: False bundle: True
'sp ace'  single: False snapshot: False bundle: False
'abc\n'   single: False snapshot: True  bundle: True   ← 末尾の改行だけが食い違う
'abc\nx'  single: False snapshot: False bundle: False
'a.b'     single: True  snapshot: True  bundle: True
```

## 目的

- 名前の形に合わないプロジェクトが `projects/` に載った時点で、利用者がそれを知り、
  何ができないか（名前を指定した操作）と何ができるか（そのディレクトリで名前なしに打つ）が
  分かる
- 名前の形の規則が 2 つの定数に分かれて別々に育たない

## 前提

- 前提 1: **名前の形の規則そのものは変えない。** `[A-Za-z0-9][A-Za-z0-9._-]*` の全体一致
  （`docs/specifications/cli-argument-resolution.md` の「用語」）のまま。先頭の `_` を
  受け付けるようにはしない
- 前提 2: **`env/bundle.py` と `env/secret_store.py` の規則は寄せない。** 確定仕様の
  「運用」が用途と互換性の違いを理由に決めている。書庫の中の名前が先頭の `_` を許すことも
  変えない（過去に export した書庫を import できなくなる）
- 前提 3: **下流（名前の指定）の検証は消さない。** `../etc` のような値は `projects/` の
  一覧から来ず、利用者の打ち間違いとして直接渡る。入口の検査では防げない
- 前提 4: 名前の形に合わない名前を作る経路は 3 つ（プラグインの同期が張る symlink、同期が
  作る別名、`env import` が作る実ディレクトリ）と、手で作った実ディレクトリである。
  手で作ったものは devbase が作っていないが、同期が既に列挙している（`real_projects`）
- 前提 5: 名前の形に合わないプロジェクトが実在しないことは、**この端末で確かめた値である**
  （実測 3）。他の端末に実在しないことは確かめられない
- 前提 6: **`env import` が `projects/<name>/` を作るのは、機密の保存先がファイル backend の
  平文のときだけである。** 書き出し先は `io_import._build_plans` が `store.path(ref)` で
  決める。age の backend では `secrets/projects/<name>.env.age`、サーバの backend では
  サーバ側へ書く。知らせの文は保存先ごとに変える

## 対象範囲

含む:

- `lib/devbase/plugin/syncer.py`: 同期が `projects/` に載せる名前の形の検査と知らせ。
  対象は `discover_projects` の結果・合成する別名・`projects/` 直下の実ディレクトリの 3 つ
- `lib/devbase/env/io_import.py` と `lib/devbase/env/_import_merge.py`: import が書き出す
  名前の知らせ。**保存先ごとに文を変える**（前提 6）
- `lib/devbase/utils/names.py`: 文言の定数を足す（述語は既にある）
- `lib/devbase/snapshot/manager.py`: `_VALID_NAME_RE` を `utils/names` の述語へ寄せる
- `docs/specifications/cli-argument-resolution.md` の「運用」の 2 つの箇条書き
- `CHANGELOG.md`

含まない:

- 名前の形の規則の変更（前提 1）
- `env/bundle.py`・`env/secret_store.py` の規則を寄せること（前提 2）
- 下流の 3 つの検証を消すこと・変えること（前提 3）。
  対象は `bin/devbase` の `maybe_cd_project`、`cli._named_lifecycle_project`、
  `container._resolve_project_name` である
- `lib/devbase/commands/container.py` の既存の文言を新しい定数へ寄せること
  （G5 の束が同じファイルを触る。次の束で寄せる → #229 で起票）
- `lib/devbase/plugin/info.py` が `.` 始まりを除外しない食い違い（→ #226 で起票）
- `env/bundle.py`・`env/_import_merge.py` が末尾の改行を通すこと（→ #227 で起票）
- `docs/plugin-dev/plugin-yml-reference.md` の別名の形の記述が実装と違うこと（→ #228 で起票）
- 名前の形に合わない名前を**弾く**こと（設計の決定 1 で退けた）
- 新しい型・永続データ・画面（そのためクラス図・ER 図・画面遷移図を作らない）

## 用語

| 用語 | 意味 |
| --- | --- |
| 名前の形 | `[A-Za-z0-9][A-Za-z0-9._-]*` の全体一致（確定仕様の「用語」と同じ） |
| 名前を作る経路 | 名前を `projects/` の直下か機密の置き場に出現させる処理。同期の symlink・同期の別名・`env import` の書き出しの 3 つ（import の書き出し先は保存先で変わる。前提 6） |
| 別名 | 名前の衝突に敗れたプラグインのプロジェクトへ張る `<プロジェクト名>.<owner>` の symlink |
| 知らせ | `logger.warning` の 1 行。処理は止めない |

## 受け入れ条件（同期）

同期（プラグイン）:

- [ ] 1. 前提: プラグインの `projects/` に `_foo` と `ok-name` がある。
      操作: `sync_projects(registry)` を呼ぶ（`devbase plugin sync` / `install` / `update` の経路）。
      結果: 2 つの symlink が**どちらも張られ**、作られた数も今と同じ。`_foo` について警告が
      1 回だけ出る。警告は名前・名前を指定した操作ができないこと・そのディレクトリで名前なしに
      打てば動くことを含む。終了コードは 0。
      検証: 既存の `tests/plugin/test_repos_core.py` の `TestSyncProjects` へテストを足す
      （`caplog` で警告を見る）。
- [ ] 2. 前提: プラグインの `projects/` の名前がすべて名前の形に合う。
      操作: 同上。
      結果: 名前の形についての警告が 1 行も出ない。
      検証: 同上。
- [ ] 3. 前提: 名前が衝突し、敗れた側が `--link` のプラグインである。元パスの basename は
      `my plugin`（空白を含む）。
      操作: 同上。
      結果: 別名 `carmo.my plugin` の symlink は今と同じく張られる。その名前について警告が
      1 回出る。
      検証: 同上。
- [ ] 4. 前提: `projects/_foo` が実ディレクトリとして存在する（プラグイン由来ではない）。
      操作: 同上。
      結果: 実ディレクトリは今と同じく残る（symlink で上書きしない）。その名前について
      警告が 1 回出る。
      検証: 同上。
- [ ] 5. 前提: プラグインの `projects/` に `.hidden` がある。
      操作: 同上。
      結果: 今と同じく `projects/.hidden` は作られない。名前の形の警告も出ない
      （`.` 始まりの除外は変えない）。
      検証: 同上。

## 受け入れ条件（env import とスナップショット）

`env import`:

- [ ] 6. 前提: 書庫に `env/projects/_foo/.env` と `env/projects/ok-name/.env` が入っている。
      操作: `devbase env import <書庫>`（`import_bundle`）を実行する。
      結果: 今と同じく 2 つのディレクトリが作られ、終了コードは 0。`_foo` について警告が
      1 回出る。
      検証: 既存の `tests/env/test_io_import.py` へテストを足す。
- [ ] 7. 前提: 6 と同じ書庫。
      操作: `--dry-run` を付けて実行する。
      結果: 書き込みは起きない。`_foo` の警告は出る。
      検証: 同上。
- [ ] 8. 前提: 書庫の名前がすべて名前の形に合う。
      操作: `devbase env import <書庫>` を実行する。
      結果: 名前の形についての警告が 1 行も出ない。
      検証: 同上。
- [ ] 9. 前提: `backend_config` に `backend: age` を明示保存した root で、書庫に
      `env/projects/_foo/.env` が入っている。
      操作: `devbase env import <書庫>` を実行する。
      結果: `projects/_foo/` は作られず、`secrets/projects/_foo.env.age` が書かれる（今と同じ）。
      警告は 1 回出て、**実際の保存先を名指しし、`projects/` に作るとは書かない**。
      検証: 既存の `tests/cli/test_env_bundle_backend.py` へテストを足す。
      backend の明示は `bc.save(root, bc.BackendConfig(backend='age'))` で行う。
      既存の `test_import_into_an_explicit_age_backend_encrypts_new_references` と同じ形にする。

スナップショットの名前の規則:

- [ ] 10. 操作: `SnapshotManager._validate_name` に `abc\n`（末尾に改行）を渡す。
      結果: `SnapshotError` になる（今は通る）。
      検証: `tests/snapshot/test_manager_name.py`（新設）。
- [ ] 11. 操作: `_validate_name` に `ok-name`・`a.b`・`A_b`・`0abc` の 4 件を渡す。
      結果: どれも例外にならない。`_foo`・`.x`・`-x`・空・`..`・`café`・`a/b`・`abc\n` の
      8 件は `SnapshotError` になる。文言（`無効なスナップショット名`）は変わらない。
      検証: 同上。受理と拒否を固定するテストで、共有した述語を将来広げたときにここで落ちる。
- [ ] 12. `lib/devbase/snapshot/manager.py` に `_VALID_NAME_RE` が残っていない。
      検証: `grep -n "_VALID_NAME_RE" lib/devbase/snapshot/manager.py` が 0 件。

## 受け入れ条件（文書と退行しないこと）

確定仕様と文書:

- [ ] 13. `docs/specifications/cli-argument-resolution.md` の「運用」が次の 3 つを書いている。
      検証はいずれも実装 Pull Request の差分で見る（設計 Pull Request には載せない）。
      - 名前の形に合わないプロジェクトが載った時点で知らせが出ること
      - 寄せていない規則は `env/bundle.py` と `env/secret_store.py` の 2 つであること
      - スナップショットの名前が `utils/names` の述語を共有すること
- [ ] 14. `CHANGELOG.md` の `[Unreleased]` に Added（知らせ）と Changed（スナップショットの
      名前が末尾の改行を受け付けなくなる）が載っている。
      検証: 同上。

退行しないこと:

- [ ] 15. 名前の形に合うプロジェクトだけのとき、`sync_projects` が返す数と `projects/` の
      中身が今と同じである。
      検証: `tests/plugin/test_repos_core.py` の既存の `TestSyncProjects`（7 件）が変更なしで
      通る。
- [ ] 16. 名前の形に合う書庫のとき、`env import` が書き出すファイルと終了コードが今と
      同じである。
      検証: `tests/env/test_io_import.py`・`test_import_merge.py`・`test_store_roundtrip.py` の
      既存のテストが変更なしで通る。
- [ ] 17. 全体テスト（`uv run --locked pytest -q tests/`）が通る。
      検証: 実装 Pull Request で実行し、結果を本文へ載せる。

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 名前の形の判定は `utils/names.is_single_segment_name` だけを使い、新しい正規表現を作らない。知らせの文言は 1 か所の定数に置く |
| 移行性 | 既存の利用者に移行の作業を求めない（決定 1 で弾かないため）。名前の形に合わないプロジェクトを持つ利用者は、警告を見て名前を変えるかどうかを自分で決める |
| セキュリティ | 下流のパストラバーサル防止（前提 3）を弱めない。知らせに利用者の名前以外の情報を載せない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる（出力のみ）: `devbase plugin install` / `update` / `sync` と `devbase env import` に警告が増えうる。終了コードと作られるものは変わらない。`devbase snapshot` の名前は末尾の改行を受け付けなくなる |
| データ | 変わらない（スキーマ・移行なし） |
| 既存の振る舞い | 同期と import の出力。スナップショット名の検証（末尾の改行 1 ケース） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --locked pytest -q tests/` |
| 静的解析 | `ruff check --select=E9,F63,F7,F82 lib` |
| 手動確認 | 隔離した root でプラグインの同期と `env import` を実行し、警告の文と終了コードを見る（`DEVBASE_ROOT` を実環境へ向けない） |
| CI | **`release/v3.7.0` を base にした Pull Request では 1 件も動かない**（`.github/workflows/ci.yml` の対象が `main` だけ。#216）。検証は手元で行い、証跡を Pull Request 本文へ載せる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 名前の形の規則は `lib/devbase/utils/names.py` に 1 つ（確定仕様「構成要素」）。この module は `re` だけに依存し副作用を持たない（ログを足さない）。知らせは呼び出し側の logger が出す |
| コーディング規約 | 既存の logger（`devbase.plugin.syncer` / `devbase.env.io_import`）を使い、`logger.warning` は 1 件 1 行。文言は日本語 |
| テスト戦略 | 同期は `tests/plugin/test_repos_core.py` の既存の fixture（`devbase_root` = `tmp_path`、`registry`、`_make_repo_dir`）で単体。import は `tests/env/test_io_import.py` の流儀で、age の保存先は `tests/cli/test_env_bundle_backend.py` の流儀（`backend_config` に `backend: age` を明示保存する）。**pytest は実環境の `DEVBASE_ROOT` を継承する**（隔離は #217 が入れる）ため、どのテストも `DEVBASE_ROOT` に依存しない引数渡しの経路だけを使う |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 手元で全体テスト、`ruff` |
| 確認してから行う | 弾くか警告に留めるかの決定（設計 Pull Request のレビューで確かめる）、スナップショットの名前を狭めること |
| 行わない | 名前の形の規則の変更、下流の検証の削除、`env/bundle.py` と `secret_store.py` を寄せること |

## 実装計画

設計は [PLAN66_project-name-validation-design.md](PLAN66_project-name-validation-design.md)。
実装のタスクは設計の「実装の分け方」に従い、2 本の Pull Request に分ける。
