# #218: test: pytest が継承する DEV_SERVICE_NAME などの環境変数が隔離されておらず、export した端末で 47 件落ちる

正は課題の本文（#218）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

pytest は実行したシェルの環境をそのまま継承する。#209 では `DEVBASE_ROOT` だけを
`tests/conftest.py` の autouse fixture で隔離したが、`lib/devbase` と `bin/devbase` が
`os.environ` から読む変数はほかにもあり、どれも隔離していない。

**変数ごとに実測したところ、影響は 1 つの変数に集中していた。**

| 変数 | 読む場所 | 単独で設定したときの pytest（3233 件） | 起こりうること |
| --- | --- | --- | --- |
| **`DEV_SERVICE_NAME`** | `lib/devbase/volume/compose.py:37`（`get_dev_service_name`） | **47 failed**（`tests/volume/test_compose_gcp_auth.py` と `tests/volume/test_compose_secret_env.py` の 48 件中 47 件） | **いま実際に赤が出る唯一の変数。** compose の dev サービス名が実環境の値になり、生成物の照合がすべて外れる |
| `DEVBASE_ACCOUNT_GROUP` | `lib/devbase/env/keys.py:58` の定数経由（`env/groups.py` が呼ぶ `volume/manager.py:65` の `resolve_account_group`）。ほかに `commands/container.py:1220` と `commands/status.py:178` が直接読む | 0 failed（3233 passed） | アカウントグループの既定が `default` でなくなり、置き場のパスとボリューム名が変わる（PLAN62 / #185 の対象そのもの）。**いまは赤にならない潜在** |
| `DEVBASE_AGE_KEY_FILE` | `lib/devbase/env/agekeys.py:34` | 0 failed | 実環境の age 鍵を読む。`openbao_root` は setenv するが、他の tmp の root を作る fixture は設定しない |
| `COMPOSE_PROJECT_NAME` | `lib/devbase/utils/config.py` | 0 failed | compose のプロジェクト名が実環境の値になる |
| `XDG_CONFIG_HOME` / `EDITOR` / `SHELL` | `lib/devbase/` 各所 | 0 failed | 設定の置き場と起動する外部コマンドが端末ごとに変わる |
| `DEVBASE_OPEN_INDEX` | `lib/devbase/` 各所 | 0 failed | 既定の選択が変わる |
| `PWD` | `lib/devbase/` 5 か所 | 未測定 | `monkeypatch.chdir` と食い違う（`openbao_root` は setenv するが、他は設定しない） |

実測（2026-09-26、`main` = `5edc75f`、macOS / arm64）。

```
$ DEV_SERVICE_NAME=bogusdev uv run --locked pytest \
    tests/volume/test_compose_gcp_auth.py tests/volume/test_compose_secret_env.py -q -p no:randomly
47 failed, 1 passed in 0.35s

$ uv run --locked pytest tests/ -q -p no:randomly
3233 passed

$ DEVBASE_ACCOUNT_GROUP=nyle uv run --locked pytest tests/ -q -p no:randomly
3233 passed
```

**`DEVBASE_ACCOUNT_GROUP` は、実測では 1 件も落ちない。** 落ちるのは `DEV_SERVICE_NAME` で、
**この変数を export している端末では、いま現在テストが 47 件落ちる。**

## どこで見つけたか

#209 の実装中。`tests/conftest.py` の autouse fixture `_isolate_devbase_root`
（PR #217）を書くにあたり、隔離の範囲を `DEVBASE_ROOT` 1 つに限ると決めたため。

調べ方:

```bash
grep -rhoE "environ(\.get)?[\(\[]'[A-Z_]+'" lib/devbase/ bin/ | grep -oE "'[A-Z_]+'" | sort | uniq -c | sort -rn
```

変数名を定数（`env/keys.py`）経由で読む箇所はこの grep に掛からないため、`keys.` の参照も併せて見る。
変数ごとの影響は、1 つずつ設定して測った。

## なぜこの変更の範囲外なのか

#209 の本文が修正の対象を `DEVBASE_ROOT` に定めており、完了の目安も
「autouse で `DEVBASE_ROOT` を隔離した状態で、テスト全体の結果が変わらないこと」である。
他の変数へ広げると、実環境の値に依存して通っているテストが表に出て、この目安を満たせるか
どうかが `DEVBASE_ROOT` の隔離とは別の理由で決まる。#209 の受け入れ条件でも
「含まない」に入れた。

## 直さないと何が起きるか

- **`DEV_SERVICE_NAME` を export している端末では、いま 47 件が落ちる。** 原因が自分の環境に
  あることは、落ちたテストの中身からは読み取れない
- 新しく書いたテストが、隔離されていない変数を設定し忘れたときに実環境の状態を読む。
  `DEVBASE_ACCOUNT_GROUP` は置き場のパスとボリューム名を変えるため、**いまは赤にならなくても
  分岐の入り口が変わる**

修正は `tests/conftest.py` の autouse fixture で行う（#209 の `_isolate_devbase_root` と同じ形で、
変数ごとに `delenv` するか固定値へ `setenv` する）。変数ごとに「未設定が既定か、固定値が既定か」を
決める必要がある。**`DEV_SERVICE_NAME` は `delenv` が素直である**（未設定のときの既定値 `dev` を
`lib/devbase/volume/compose.py:37` が持っているため）。

## 由来

issue #209 / PR #217

## 依頼（原文）

> 修正は `tests/conftest.py` の autouse fixture で行う（#209 の `_isolate_devbase_root` と同じ形で、
> 変数ごとに `delenv` するか固定値へ `setenv` する）。変数ごとに「未設定が既定か、固定値が既定か」を
> 決める必要がある。**`DEV_SERVICE_NAME` は `delenv` が素直である**（未設定のときの既定値 `dev` を
> `lib/devbase/volume/compose.py:37` が持っているため）。

（上の「直さないと何が起きるか」から）

## 目的

- pytest の結果を、pytest を起動したシェルの環境変数とホームディレクトリの中身から切り離す。どの端末から走らせても、同じコミットなら同じ件数が通る状態にする
- 新しく環境変数を読むコードを足したときに、隔離を足し忘れたことにテストで気づける状態にする

## 前提

- 前提 1: 隔離は `tests/conftest.py` の autouse fixture で行い、テストの側の `monkeypatch.setenv` / `delenv` が後勝ちで働く形を保つ（#209 の `_isolate_devbase_root` と同じ）
- 前提 2: 変数ごとの既定は「未設定」を原則にする（`lib/devbase` が未設定のときの既定値を持つため）。固定値にする変数があれば、理由を設計の決定に残す
- 前提 3: 隔離の対象は、`lib/devbase` と `bin/devbase` が読む変数（本文の表の変数と、`env/keys.py` の定数経由で読む変数）と、ホームディレクトリ（`HOME`）である。`XDG_CONFIG_HOME` を消すと age の鍵の既定の置き場が `~/.config` へ落ちるため、`HOME` を隔離しないと実環境の鍵を読む（`lib/devbase/env/agekeys.py:51-52`）。ほかにも `~/.devbase/config.yml`・`~/.git-credentials`・`~/.ssh` を読む経路がある
- 前提 4: `HOME` を差し替えると、テストが起動する外部のコマンド（`git` など）が利用者の設定を読まなくなる。それで落ちるテストは、テストの側で必要な設定を与える形に直す（テストを消さない）
- 前提 5: `PATH`・`TMPDIR`・`LANG` など、実行環境そのものを表す変数は隔離しない
- 前提 6: 2026-09-26 時点の基準: `main` = `5edc75f` で `uv run --locked pytest tests/ -q -p no:randomly` が 3233 passed。`DEV_SERVICE_NAME=bogusdev` だけで 47 failed

## 対象範囲

含む:
- `tests/conftest.py` の autouse fixture による、環境変数と `HOME` の隔離
- 隔離で表に出た、実環境の値に依存して通っていたテストの修正
- `lib/devbase` が新しく読む変数を隔離の一覧に入れ忘れたときに落ちるテスト

含まない:
- `lib/devbase` と `bin/devbase` の振る舞いの変更
- `DEVBASE_ROOT` の隔離の変更（#209 で済み）
- CI の設定の変更
- 設計のクラス図（型を足さず変えない。足すのは `tests/conftest.py` の定数と fixture だけ）
- 設計の非機能設計表（この要求は非機能の条件を持たない）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 開発者が変数を export した端末で pytest を起動した | 開発者 | 継承した値でテストが落ちる、または実環境の置き場を読む | — |
| E2 | autouse fixture が変数と `HOME` を既定の状態へ戻した | E1（テストごと） | 一覧に無い変数が残る | E1 |
| E3 | テストが自分で変数を設定した | テスト | 後勝ちにならず、テストの設定が消える | E2 |
| E4 | `lib/devbase` に、環境変数を読むコードが足された | 開発者 | 隔離の一覧に足し忘れる → 漏れを見るテストが落ちる | — |

## 用語

| 用語 | 意味 |
| --- | --- |
| 環境の隔離 | pytest を起動したシェルから継承した環境変数を、テストごとに既定の状態（未設定か固定値）へ戻すこと |

## 受け入れ条件

- [ ] `DEV_SERVICE_NAME=bogusdev uv run --locked pytest tests/ -q -p no:randomly` が、変数を設定しないときと同じ件数ですべて通る
- [ ] 次の変数をすべて実在しない値へ設定して起動しても、`uv run --locked pytest tests/ -q -p no:randomly` が変数を設定しないときと同じ件数ですべて通る: `DEV_SERVICE_NAME`・`DEVBASE_ACCOUNT_GROUP`・`DEVBASE_AGE_KEY_FILE`・`COMPOSE_PROJECT_NAME`・`XDG_CONFIG_HOME`・`EDITOR`・`SHELL`・`DEVBASE_OPEN_INDEX`・`DEVBASE_OPEN_EDITOR`・`DEVBASE_EDITOR`・`DOCKER_CONTEXT`・`DOCKER_HOST`・`DEVBASE_DOCKER_CONTEXT`・`GCP_AUTH_MODE`・`GCP_ACTIVE_PROFILE`・`TMUX`（`SHELL` は実在する別のシェルでもよい）
- [ ] テストの実行中、`os.environ['HOME']` は実行した利用者のホームディレクトリを指さない（テストごとの tmp を指す）。これを確かめるテストがある
- [ ] `lib/devbase` の中で `os.environ[...]` / `os.environ.get(...)` / `os.getenv(...)` に文字列で直接書いた変数名と、`lib/devbase/env/keys.py` の定数のうち `lib/devbase` が環境変数として読むものが、隔離の一覧か「隔離しない一覧（理由つき）」のどちらかに入っていないと、pytest が落ち、落ちた理由に変数名が出る
- [ ] テストの側で `monkeypatch.setenv` した値が、autouse fixture より後勝ちで効く（既存のテストがこの形のまま通る）
- [ ] 変数を何も設定しない端末での `uv run --locked pytest tests/ -q` の件数が、変更前より減らずすべて通る。件数が変わるなら、足したテストの分だけ増える

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない |
| 既存の振る舞い | 変わらない（テストの環境だけを変える） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --locked pytest tests/ -q -p no:randomly`（変数なし）と、受け入れ条件 2 の変数を設定した起動の 2 回で件数を比べる |
| 手動確認 | 実装の後、隔離の一覧から `DEV_SERVICE_NAME` を 1 つ外すと、受け入れ条件 1 の起動で 47 件前後が落ちることを見て戻す |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 隔離は `tests/conftest.py`。漏れを見るテストは `tests/` の直下か `tests/utils/` |
| コーディング規約 | `CONTRIBUTING.md` |
| テスト戦略 | 隔離そのものは全体の pytest を変数つきで走らせて確かめる。一覧の漏れは静的な読み取り（ソースの文字列の照合）で見る |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 隔離で落ちたテストの原因を 1 件ずつ記録する |
| 確認してから行う | テストの期待値の書き換え（実環境の値を期待していたテストに限る） |
| 行わない | テストの削除・skip の追加で件数を合わせること |
