# #425: DEV_SERVICE_NAME を空で書くと、bin/devbase は dev を使い Python は空の名前で開発サービスを探す

正は課題の本文（#425）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何を見つけたか
>
> プロジェクトの `env` に `DEV_SERVICE_NAME=`（空の値）と書いたとき、2 つの読み方が別のサービス名になる（コードを読んだ結果。実機では未再現）。
>
> - `bin/devbase` の `cmd_build` は `${DEV_SERVICE_NAME:-dev}` で、空なら `dev` を `docker compose build` に渡す。Dockerfile の場所を決める `python -m devbase.commands.project_dockerfile` にも同じ値を `--service` で渡す（#415）ため、通常のビルドの中では建てるサービスと場所を決めるサービスが揃う
> - Python の `get_dev_service_name()`（`lib/devbase/volume/compose.py:35`）は `os.environ.get('DEV_SERVICE_NAME', 'dev')` で、空の値をそのまま返す。`--expires` の判定（`lib/devbase/commands/container.py` の `_get_base_image_ref`）・`up`・`login`・`open`・`post-start`・`scale` はこちらを使う
>
> `bin/devbase` は `env` を `set -a` で読むため、空の値も環境変数として Python へ渡る。
>
> ## どこで見つけたか
>
> - `lib/devbase/volume/compose.py` の `get_dev_service_name`
> - `bin/devbase` の `cmd_build`（`docker compose build "${DEV_SERVICE_NAME:-dev}"` と `project_dockerfile` の `--service "${DEV_SERVICE_NAME:-dev}"`）
>
> ## なぜこの変更の範囲外なのか
>
> issue #415 の受け入れ条件は、通常のビルドと `--expires` の判定が**同じ開発サービス名から**同じ Dockerfile を決めることを求め、開発サービス名の読み方そのものは前提 1（2 つが同じ値を使う）に置いている。`get_dev_service_name()` は `up`・`login`・`open`・`post-start` などでも使われ、直すと #415 の範囲（Dockerfile の場所の決め方）の外へ広がる。#415 は、通常のビルドが `${DEV_SERVICE_NAME:-dev}` の値を引数で渡し、建てるサービスと場所を決めるサービスを揃えた（PR #427）。根拠: Value 1（MVV 版 1）
>
> ## 直さないと何が起きるか
>
> `DEV_SERVICE_NAME=` と空で書いたプロジェクトで、`--expires` の判定・`devbase up` のイメージの確かめ・`login` などが、サービス名 `''` を探して開発サービスを見失う。今の手元のプラグインのプロジェクトで `DEV_SERVICE_NAME` を `env` に書いたものは 0 件で、すぐには表に出ない。
>
> ## 由来
>
> issue #415 の設計中に見つけた。

## 目的

- プロジェクトの `env` に `DEV_SERVICE_NAME=`（空の値）と書いても、`devbase` のどの経路も同じ開発サービス名（`dev`）を使う状態にする。`bin/devbase` の `build` と、Python の `up`・`login`・`open`・`post-start`・`scale`・`--expires` の判定とで、開発サービスを見失う経路を無くす

## 前提

- 前提 1: 空の値（長さ 0 の文字列）は「`DEV_SERVICE_NAME` を書いていない」と同じに扱い、開発サービス名は `dev` になる。`bin/devbase` の `${DEV_SERVICE_NAME:-dev}` の読み方に Python を揃える（`bin/devbase` の側は変えない）
- 前提 2: 空白だけの値（`DEV_SERVICE_NAME=" "` など）は空の値に含めない。`bin/devbase` の `${DEV_SERVICE_NAME:-dev}` も空白だけの値をそのまま使うため、今も 2 つの読み方は揃っている
- 前提 3: Python で `DEV_SERVICE_NAME` を読む箇所は `lib/devbase/volume/compose.py` の `get_dev_service_name()` 1 か所だけである（`lib/` を `DEV_SERVICE_NAME` で検索した結果。ほかの当たりは文字列の説明か `get_dev_service_name()` の呼び出し）。呼び出し側（`lib/devbase/commands/container.py` の `cmd_up`・`cmd_login`・`cmd_open`・`cmd_post_start`・`cmd_scale`・`_resolve_dev_service`・`_ensure_images`・`_dev_instance_indices`・`_maybe_open_editor`・`_open_editor_at`、`lib/devbase/commands/env.py` の `_push_token_to_running`、`lib/devbase/volume/compose.py` の `generate_scaled_compose`）は名前を受け取るだけで、直す場所は `get_dev_service_name()` に閉じる
- 前提 4: 手元のプラグインのプロジェクト（`repos/`・`projects/*`）で `DEV_SERVICE_NAME` を書いたものは 0 件である（issue の起票時の調べ）。空の値を書いた利用者の環境があっても、変更後は `dev` を探すようになるだけで、`dev` 以外の名前を書いた利用者の振る舞いは変わらない

## 対象範囲

含む:
- `get_dev_service_name()` が、`DEV_SERVICE_NAME` が空の値のとき `dev` を返すようにする
- 空の値の扱いを確かめるテスト（関数の単体と、呼び出し側の経路 1 つ以上）
- 開発サービス名の既定の書き方（用語集の「開発サービス名」と、それを写した確定仕様の文）を「未設定か空なら `dev`」へ揃える
- `CHANGELOG.md` の `[Unreleased]` への記入（利用者に見える振る舞いの変更のため）

含まない:
- `bin/devbase` の `${DEV_SERVICE_NAME:-dev}` の読み方の変更（すでに空なら `dev`）
- `build` が `--service` で開発サービス名を渡す形（#415 の決定 6）をやめること。Python 側が揃った後も、建てるサービスと場所を決めるサービスを 1 つの値で揃える役目は残る
- 空白だけの値・サービス名として使えない文字を含む値の検証（前提 2。名前の形の検証は別の課題で扱う）
- `DEV_SERVICE_NAME` 以外の環境変数の空の値の扱い
- 空の値を書いたプロジェクトへの警告の表示

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 利用者がプロジェクトの `env` に `DEV_SERVICE_NAME=` と書いた | 利用者の編集 | — | — |
| E2 | `bin/devbase` が `env` を `set -a` で読み、空の `DEV_SERVICE_NAME` を環境変数として Python へ渡した | `devbase` の各コマンドの起動 | `env` を読めなければ今の扱いのまま（この変更では変えない） | E1 |
| E3 | Python が開発サービス名を求め、`dev` を得た | `up`・`login`・`open`・`post-start`・`scale`・`--expires` の判定・トークンの配布 | — （空の値でも未設定でも名前は必ず決まる） | E2 |
| E4 | 呼び出し側が compose の構成から開発サービス `dev` を引いた | E3 の名前 | 構成に `dev` が無ければ、今の「開発サービスが見つからない」扱いのまま（この変更では変えない） | E3 |
| E5 | `build` が `${DEV_SERVICE_NAME:-dev}` の `dev` を建て、同じ `dev` を `--service` で Dockerfile の場所を決める処理へ渡した | `devbase build` | 今の扱いのまま | E2 |

E3 と E5 が同じ `dev` になることが、この変更で揃える点である。

## 用語

| 用語 | 意味 |
| --- | --- |
| 開発サービス名 | `get_dev_service_name()` が返す名前。`DEV_SERVICE_NAME` が未設定か空なら `dev` |

## 受け入れ条件

- [ ] `DEV_SERVICE_NAME` が空の値（`''`）のとき、`get_dev_service_name()` は `'dev'` を返す
- [ ] `DEV_SERVICE_NAME` が未設定のとき、`get_dev_service_name()` は `'dev'` を返す（退行しない）
- [ ] `DEV_SERVICE_NAME=app` のとき、`get_dev_service_name()` は `'app'` を返す（退行しない）
- [ ] `DEV_SERVICE_NAME=" "`（空白 1 文字）のとき、`get_dev_service_name()` は `' '` を返す（前提 2。`bin/devbase` の `${DEV_SERVICE_NAME:-dev}` と同じ値）
- [ ] 前提: compose の構成に `dev` サービスがあり、`DEV_SERVICE_NAME` が空の値である
      操作: 呼び出し側の経路（`_resolve_dev_service` または `generate_scaled_compose`）を通す
      結果: `dev` サービスを開発サービスとして扱う（空の名前のサービスを探して見失わない）
- [ ] 環境変数の読み取りを集める検査（`tests/test_env_isolation.py` の `test_collector_finds_known_reads`）が、変更後も `DEV_SERVICE_NAME` の読み取りを `volume/compose.py` に見つける
- [ ] 用語集の「開発サービス名」の意味が「未設定か空なら `dev`」と読め、`docs/glossary.md` は `glossary.json` から作り直してある
- [ ] `CHANGELOG.md` の `[Unreleased]` に、空の `DEV_SERVICE_NAME` を `dev` として扱うようになったことが書いてある
- [ ] 全体のテスト・lint・固有の語の検査が通る（下の「検証手段」）

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる（互換あり）。`DEV_SERVICE_NAME=` と空で書いたときの開発サービス名が `''` から `dev` になる。今の振る舞いは開発サービスを見失うだけで、これを頼りにする利用者は無いと置く（前提 4） |
| データ | 無し |
| 既存の振る舞い | 空の `DEV_SERVICE_NAME` を書いたプロジェクトの `up`・`login`・`open`・`post-start`・`scale`・`--expires` の判定・トークンの配布が `dev` を開発サービスとして使う。未設定・空でない値のプロジェクトは変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` |
| 静的解析 | `uvx ruff check --select=E9,F63,F7,F82 lib` |
| 固有の語の検査 | `python3 .github/scripts/proper_term_check.py`（`語の一覧が無いため飛ばした` と出たら検査していないと記録する） |
| 手動確認 | リリース後テストで、作業用のプロジェクトの `env` に `DEV_SERVICE_NAME=` と書き、`devbase login` が `dev` のコンテナへ入ることを確かめる。実際の利用者の環境での `devbase up` は P3 のため人の承認を得てから行う |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「このリポジトリ」。直すのは `lib/devbase/` の Python。`projects/*`・`repos/` は管理外のため触らない |
| コーディング規約 | `AGENTS.md` の「書き方」と周りのコードに合わせる。lint は上の静的解析のコマンド |
| テスト戦略 | 単体: `get_dev_service_name()` の 4 つの値（未設定・空・`app`・空白）。結合: 空の値で呼び出し側の経路が `dev` を引くこと。テストは `monkeypatch` で `DEV_SERVICE_NAME` を置き、実の docker に触らない（`docs/specifications/test-environment-isolation.md`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 上の 3 つの検査を push の前に手元で打つ。用語集を直したら `glossary.py render` で `docs/glossary.md` を作り直す |
| 確認してから行う | `get_dev_service_name()` の戻り値の型や呼び出し方を変えること。名前の形の検証を足すこと |
| 行わない | `bin/devbase` の読み方の変更。base イメージの変更（P1）。実際の利用者の環境での `devbase up`（P3） |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 確定仕様（`docs/specifications/base-image-chain-build.md` の決定 6）の「`get_dev_service_name()` は空の値をそのまま返す」の文を、直した後の事実へどう書き換えるか（決定の理由として残すか、#425 で揃ったと書き足すか） | 確定仕様化の担当 | 確定仕様化の工程 |

## 根拠

- Value 1（入った時点で使える）: `env` の書き方の違いで開発サービスを見失わないようにする
- Mission: どのプロジェクトでも同じ読み方で開発環境へ入れるようにする
- P1・P3: 範囲に含めない（境界の「行わない」）
