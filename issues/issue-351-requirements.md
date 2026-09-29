# #351: 固有の名前の除去 ③: コードとテストの例を置き換え、エラー文のグループ名の例を 1 か所に寄せる

正は課題の本文（#351）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

#294 の分割 ③（区分 E・F）。置き換えの表・決めたこと・親の受け入れ条件の正は親課題 #294 の本文。この本文は、そのうち `lib/`・`bin/`・`tests/` に掛かる部分を観測できる形へ書き下ろしたもの。

## 依頼（原文）

> 利用者からの依頼（2026-09-26）。作成者の所属組織・顧客・個人に固有の名前を、文書・コード・テストから除く

（親課題 #294 の「依頼（原文）」の写し。この課題の起票時の本文は「#294 の AC2 のうち `lib/`・`bin/`・`tests/`」「#294 の AC4」「#294 の AC6」の 3 行だった）

## 解釈

- `lib/`・`bin/`・`tests/` の追跡されているファイルから、#294 の「語ごとの出現」の表の語を、#294 の「置き換えの表」の名前へ置き換える
- 利用者の端末に出るエラー文・使い方の例のグループ名を、1 か所の定義から出す形に寄せる
- 振る舞い（引数の解釈・戻り値・エラーになる条件）は変えない。変わるのは文言の中の例と、テストの fixture・期待値の名前だけ

## 現状（2026-09-30、`main` = `c66b3af`）

`git ls-files <場所> | xargs grep -ioE 'carmo|nyle|kkg|kk-generation|with-ai-dev|takemi|ohama|uttaro|volareinc|devbase-ext'` で数えた。

| 場所 | ファイル | 出現 |
| --- | --- | --- |
| `lib/` | 15 | 27 |
| `bin/` | 1（`bin/devbase:439,471`） | 3 |
| `tests/` | 84 | 1422 |

`tests/` の語の内訳: `carmo` 560、`nyle` 476、`kkg` 249、`volareinc` 79、`uttaro` 33、`takemi` 18、`ohama` 2、`devbase-ext` 2、`with-ai-dev` 2、`kk-generation` 1。グループ `with` は英単語と区別できないため上の数に入らない（`DEVBASE_ACCOUNT_GROUP=with`・`team/with/`・`users/<名前>/with/`・`devbase_home_with`・`'with'` のグループの値として `tests/cli/test_env_bundle_backend.py`・`tests/cli/test_up_roundtrips.py`・`tests/cli/tui/test_actions_env.py`・`tests/cli/tui/test_actions_snapshot.py`・`tests/commands/test_container_up_order.py` ほかに出る）。

利用者に出る文言の例（#294 の AC4 の対象）:

| 場所 | 今の文言 |
| --- | --- |
| `lib/devbase/env/groups.py:123` | `DEVBASE_ACCOUNT_GROUP=<グループ> (nyle / personal など)` |
| `lib/devbase/volume/manager.py:84` | `移し先のグループ名 (nyle / personal など) を書いてください。` |
| `lib/devbase/commands/env.py:92` | `--group <名前> を付けてください (例: --group nyle)` |
| `lib/devbase/commands/snapshot.py:46` | `--group <名前> を付けてください (例: --group nyle)` |
| `lib/devbase/commands/project.py:241` | `--to <グループ> で移し先のグループを指定してください (例: --to nyle)` |

`acme` は今、グループの読み替えの「前」の名前として使われている（`acme → nyle`。`lib/devbase/commands/env.py:1093`・`commands/env_rows.py:26`・`env/backend_config.py:227`・`env/secret_store.py:158`、`tests/` の 7 ファイル）。`nyle` をそのまま `acme` にすると `acme → acme` になり、読み替えの例が意味を失う。

`alice` は今、`tests/env/test_cipher.py`・`tests/env/test_collector_host.py`・`tests/volume/test_bind_mounts.py` で使われている。`tests/volume/test_bind_mounts.py` では `/home/takemi`（展開する側の HOME）と `~alice`（他の利用者の HOME で展開しない側）が別の人を表す。

全体テストの件数: `env -u DEVBASE_ROOT uv run --locked pytest tests/ --co -q` で 3793 件（2026-09-30、この課題のブランチ）。

## 前提

- 前提 1: 置き換えの名前は #294 の「置き換えの表」に従う。表に無い形は次のとおり置く（誤っても置き換え直すだけで局所的に直せる）
  - グループ `with` → `initech`（表のとおり）。`team/with/` → `team/initech/`、`devbase_home_with` → `devbase_home_initech` のように、グループ名を含む KV のパス・ボリューム名・スナップショット名も同じ名前で置き換える
  - `devbase_home_nyle` → `devbase_home_acme`、`devbase_home_kkg` → `devbase_home_globex`
  - リポジトリの owner の `KK-Generation` → `Globex`、`volareinc` → `example-org`、`takemi-ohama`（GitHub の利用者名）→ `alice`
  - `carmo.takemi--carmo`（衝突の suffix の例）→ `myapp.alice--myapp`、`repos/takemi--carmo/...` → `repos/alice--myapp/...`
  - `carmo-plugin` → `myapp-plugin`
- 前提 2: `acme` を読み替えの「前」の名前として使っている箇所（`lib/` の 4 か所と `tests/` の 7 ファイル）は、`nyle` → `acme` と衝突しないよう、先に `acme` → `umbrella` へ置き換える。結果の例は `umbrella → acme` になる
- 前提 3: 既に `alice` が別の人を表しているテスト（`tests/volume/test_bind_mounts.py` の `~alice`）では、既存の `alice` を `bob` へ置き換え、`takemi` を `alice` へ置き換える。同じテストの中で 2 人が同じ名前にならない
- 前提 4: テストの期待値が名前の並び（整列の結果）に依存しているところ（例: `tests/cli/tui/test_actions_env.py:286` の `['nyle', 'with']`）は、置き換えた名前で整列し直した順へ期待値を直す。対象のコードは変えない
- 前提 5: グループ名の例の文言の定義は `lib/devbase/env/groups.py` に置く（#294 の「対象範囲」）。定義の形（定数か関数か）と、`volume/manager.py` からの参照の仕方（循環 import を避ける方法）は `design` が決める
- 前提 6: 文言の中の例だけを変え、文言の他の部分（語順・句読点・案内の内容）は変えない
- 前提 7: `lib/devbase/project/local_config.py:13` の `home: /home/takemi` は区分 B（#349）にも数えられている。#349 が先に直していればこの課題では触らず、残っていればこの課題で `/home/alice` にする（どちらが入れても結果は同じ）
- 前提 8: `docs/`・`CHANGELOG.md`・`issues/`・`containers/`・`plugins/` などの `lib/`・`bin/`・`tests/` の外は #349・#350・#352 が扱う。コードのコメントが計画書（`issues/PLAN*`）を指す参照の張り替えは #352 の範囲で、この課題では触らない

## 未決

- 未決 1: `lib/devbase/editor/opener.py:443` の例 `/home/ubuntu/share/work/uttarov2-doc.workspace` の置き換え先。#294 の表は「`myapp-doc.workspace` など `myapp` 系」とする。`myapp-doc.workspace` で進め、#294 の表と食い違えば表に合わせる（`design` で確定する）
- 未決 2: #294 の表に無い語が `tests/` に残る。`csc`（`tests/commands/test_env_backend_migrate.py` の 24 件。社内プロダクトの略称）と `investment`（`tests/editor/test_opener.py:991,999`。作成者のプロジェクト名）。#294 の境界「確認してから行う: 表に無い固有の語を見つけたときの置き換え先（表へ足して本文を直す）」に当たるため、この課題では置き換え先を決めない。案は `csc` → `myapp-console`、`investment` → `myapp-ml`。#294 の表へ足すかを利用者が決めるまで、AC1 の対象に含めない

## ドメインイベント

| # | イベント（過去形） | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 読み替えの「前」の名前 `acme` が `umbrella` へ置き換わった | 開発者の置き換え | E2 の後に行うと `acme → acme` と `umbrella → umbrella` が混ざる | E2 より前（前提 2） |
| E2 | `lib/`・`bin/`・`tests/` の固有の語が置き換えの表の名前へ置き換わった | 開発者の置き換え | テストが落ちる。AC3 で検出する | E1 の後 |
| E3 | グループ名の例の文言の定義が `groups.py` の 1 か所へ寄った | 開発者の変更 | 呼ぶ側に定義の写しが残る。AC2 の grep で検出する | E2 と前後は問わない |
| E4 | 全体テストが置き換えの前と同じ件数で通った | 開発者・CI の実行 | 件数の差・失敗を AC3 で検出する | E1〜E3 の後 |
| E5 | #353 の検査が `main` で 0 件を確かめた | #353 の Pull Request | この課題の範囲外（#353 が扱う） | #349〜#352 がすべて入った後 |

## 受け入れ条件

- [ ] AC1: `git ls-files lib bin tests | xargs grep -ilE 'carmo|nyle|kkg|kk-generation|with-ai-dev|takemi|ohama|uttaro|volareinc|devbase-ext'` が 0 件。加えて、グループ `with` の残りを拾う `git ls-files lib bin tests | xargs grep -nE "['\"]with['\"]|=with\b|/with/|_with\b|\bwith-(main|team|me|ai-dev)\b|\.with\.|グループ with" | grep -vE '\["with"\]|get\("with"\)'` が 0 件（置き換え前は 252 行。除いているのは GitHub Actions の `with:` を読むテストの 2 形）。この式で拾えない形（識別子の `G_WITH`・`WITH_ONLY` など）はレビューで読み、グループを表すものは置き換える
- [ ] AC2: 次の 5 か所のエラー文・使い方の例が、グループ名として `acme`（`acme / personal など`、`--group acme`、`--to acme`）を出す。`groups.py:123`・`volume/manager.py:84`・`commands/env.py:92`・`commands/snapshot.py:46`・`commands/project.py:241` の相当箇所。例のグループ名の文字列リテラル `acme` がこの 5 か所の文言の中で `lib/devbase/env/groups.py` の 1 か所にだけ書かれている（残りの 4 か所は `groups.py` の定義を参照する）
- [ ] AC3: `env -u DEVBASE_ROOT uv run --locked pytest tests/` が失敗 0 で通り、集めた件数が置き換えの前と同じ（3793 件。この課題の作業中に `main` から取り込んだ変更で件数が変わった場合は、取り込んだ後の置き換え前の件数と比べる）
- [ ] AC4: 読み替えの例（`lib/devbase/commands/env.py`・`commands/env_rows.py`・`env/backend_config.py`・`env/secret_store.py` の docstring・コメントと、それを確かめるテスト）で、読み替えの前後が別の名前（`umbrella → acme`）になっている。`git ls-files lib tests | xargs grep -nE 'acme → acme|umbrella → umbrella'` が 0 件
- [ ] AC5: `lib/`・`bin/`・`tests/` の変更の差分に、名前の置き換えと AC2 の定義の集約の外の変更（引数の解釈・戻り値・エラーになる条件・テストの確かめる内容）が無い。前提 4 の期待値の並べ替えは含めてよい

## 対象範囲と非対象

含む:
- `lib/`・`bin/`・`tests/` の追跡されているファイルの、docstring・コメント・文言の例・テストの fixture と期待値の名前（#294 の区分 E・F）
- 利用者に出るエラー文・使い方の例のグループ名を `lib/devbase/env/groups.py` の 1 か所の定義に寄せる

含まない:
- `lib/`・`bin/`・`tests/` の外（`docs/`・`CHANGELOG.md`・`issues/`・`containers/`・`plugins/`・`.github/` など。#349・#350・#352・#353 が扱う）
- コードのコメントから計画書（`issues/PLAN*`・`issues/old/`）への参照の張り替え（#352）
- 固有の語を検出する検査のスクリプトと CI のジョブ（#353）
- エラー文の例以外の文言の書き直し、置き換えに乗じたリファクタリング
- git の履歴、マージ済みの Pull Request・issue の本文
- `personal`（#315 で devbase が示すグループ名）と `default`（予約語）の置き換え

## 実装前に明文化する項目

| 項目 | 内容 |
| --- | --- |
| 目的 | OSS として読む第三者が、コードとテストの例を固有の事情を知らずに読める。利用者の端末に出るエラー文に作成者の組織名が出ない |
| 成功条件 | 上の AC1〜AC5 |
| 検証手段 | AC1・AC4: 条件の中の `git ls-files ... \| xargs grep` を打つ。AC2: `git grep -n "acme" lib/devbase/env/groups.py lib/devbase/volume/manager.py lib/devbase/commands/env.py lib/devbase/commands/snapshot.py lib/devbase/commands/project.py` と、文言を確かめるテスト。AC3: `env -u DEVBASE_ROOT uv run --locked pytest tests/`（`--co -q` で件数）。AC5: 差分のレビュー |
| プロジェクト構造 | 変更は `lib/`・`bin/`・`tests/` に置く。文言の例の定義は `lib/devbase/env/groups.py` |
| コーディング規約 | `CONTRIBUTING.md` と既存のコードの形に従う。CI の検査（`.github/workflows/`）が通る |
| テスト戦略 | 既存の単体テスト・CLI のテストがそのまま置き換え後の名前で通ることで担保する。AC2 の 5 か所の文言は、既存のテストが文言を確かめていれば置き換えで足り、確かめていない箇所には文言の例を確かめるテストを足す（`tdd-cycle`） |
| 境界 | 常に行う: 置き換えの表と前提 1〜3 に従う。置き換えた後に全体テストを走らせる。確認してから行う: 表と前提に無い固有の語を見つけたときの置き換え先（#294 の表へ足して本文を直す）。行わない: 振る舞いの変更、置き換えに乗じた書き直し、`lib/`・`bin/`・`tests/` の外の変更 |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない。エラー文・使い方の文言の例のグループ名だけが `nyle` から `acme` に変わる |
| データ | 無い（テストの fixture の名前だけ） |
| 既存の振る舞い | 無い |

## MVV との突き合わせ

- Value 3（プロジェクトの境界を守る）: 作成者の組織・顧客の名前を公開のコードから除く変更で、反しない
- P1〜P3・C1〜C8 に当たる操作は無い（base イメージ・タグ・実環境の `devbase up`・秘密・利用者の設定に触れない）

## 由来

親課題 #294（利用者からの依頼、2026-09-26）の分割 ③。関連: #349・#350・#352・#353

## 用語の突き合わせ

`glossary.py check` は、この本文と関係の無い用語集の不整合（`docs/glossary/glossary.json` の `pending_source` が `issues/old/` へ移した設計文書を指す 6 件。#294 の前提 2 で #352 が直す）で止まり、語の突き合わせまで進まなかった。この本文に「用語」の節は無い。
