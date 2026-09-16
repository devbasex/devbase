# PLAN58: Compose の profiles で付随サービス群を後から起動・停止する

対象 issue: devbasex/devbase#189
関連: volareinc/devbase-ext#31（carmo-system-console 側の適用）

## 依頼（原文）

> carmo-system-consoleでは、devコンテナと同時にテスト用のapp / dbコンテナなども全て立ち上がってしまいますが、defaultはdevコンテナだけとし、
> あとから追加でテスト用サーバ群も立ち上げられるようにしたい。
>
> 方法を大まかに検討してissuesを起票してください

> 追加:
> テストコンテナ群は群後から起動も停止もできるようにしたい(この際、devコンテナの稼働には影響がないようにすること)

## 目的

dev のほかに app / db などのサービスを持つプロジェクトで、`devbase up` の既定では dev だけを起動し、付随するサービス群は後から起動・停止できるようにする。その起動・停止で dev コンテナを再作成も再起動もしない。

判定に使うのは Compose の `profiles` である。プロジェクトは `compose.yml` の各サービスへ `profiles:` を書くだけでよく、devbase 側は「プロファイルを指定して起動・停止する口」と「停止を全プロファイルへ効かせる」ことを担う。

## 前提

- 前提 1: プロファイル名は devbase が決めず、プロジェクトが `compose.yml` に書いた名前をそのまま受ける。devbase は既定のプロファイル名を持たない
- 前提 2: プロファイル付きのサービスは scale の対象にしない。dev だけが `dev-1`..`dev-N` へ複製される現在の仕組みは変えない
- 前提 3: `devbase up` はテスト用サーバが起動していても、既定の状態（dev だけ）へ揃える。継続したい利用者は `up` の後にもう一度プロファイルを起動する（利用者の指示、2026-09-16）
- 前提 4: プロファイルを使っていないプロジェクトの `up` / `down` / `scale` の挙動は変えない
- 前提 5: プロファイルの起動は `--no-deps` を付けて Compose を呼ぶ。そのため既定のサービス（dev を含む）は操作の対象に入らない。あわせて、そのプロファイルに属するサービスをすべて明示して渡す。`--no-deps` は依存先を自動起動しないためである。1 つでも渡し漏らすと、そのサービスは起動しない（設計の決定 2）

## 対象範囲

含む:

- `profiles:` 付きのサービスを `devbase up` で起動しないこと（Compose の既定の挙動をそのまま通す）
- プロファイル単位で起動・停止するコマンド
- 停止（`devbase down` と `up` 冒頭の停止）を全プロファイルへ効かせること
- プロジェクトのフックへ、有効なプロファイルを伝えること
- プロファイルを使うプロジェクト作者向けの文書

含まない:

- プロファイル付きサービスの scale（インスタンスごとの複製）
- `project.yml` で既定の有効プロファイルを宣言する仕組み（必要になってから別 issue で扱う）
- carmo-system-console 側の `compose.yml` / フックの書き換え（volareinc/devbase-ext#31）
- 新しい型・クラスの追加。実装は既存のモジュール関数の並びに足す（そのためクラス図を作らない）
- 永続データの追加・変更（そのため ER 図・テーブル定義・CRUD 図を作らない）
- 画面の追加・変更（そのため画面一覧・遷移図を作らない）

## 用語

| 用語 | 意味 |
| --- | --- |
| プロファイル | Compose の `profiles:` に書いた名前。付随サービス群をまとめる単位 |
| 既定のサービス | `profiles:` を持たないサービス。`devbase up` で起動する |
| プロファイルのサービス | そのプロファイルに属し、既定のサービスに含まれないサービス |

## 受け入れ条件

以下で `compose.yml` と書くのは、利用者がプロファイルを宣言する場所のことである。devbase が実際に読むのは、その宣言を引き継いだ生成物 `.docker-compose.scale.yml` である（設計の決定 1）。

起動と停止:

- [ ] `profiles: [X]` を持つサービスは `devbase up` で起動せず、`docker ps` に現れない
- [ ] `devbase container profile up X` を実行すると、プロファイル X のサービスだけが起動する。既定のサービス（dev を含む）の Container ID と `StartedAt` は実行の前後で変わらない
- [ ] `devbase container profile down X` を実行すると、プロファイル X のサービスのコンテナが削除される。既定のサービスの Container ID と `StartedAt` は実行の前後で変わらない
- [ ] `devbase container profile up X` は、プロファイル X に属するサービスをすべて Compose へ渡し、`--no-deps` を付ける
- [ ] `depends_on: {dev: {condition: service_started, required: false}}` を持つプロファイル X のサービスを `devbase container profile up X` で起動しても、既定のサービスの Container ID と `StartedAt` は変わらない
- [ ] `depends_on: [dev]`（`required` を書かない形）を持つサービスでも、`devbase container profile up X` で既定のサービスの Container ID と `StartedAt` は変わらない
- [ ] dev の環境変数の値を変えた後でも、`devbase container profile up X` は dev を再作成しない
- [ ] dev が `depends_on: {db: {condition: service_started, required: false}}` を持ち、db をプロファイル X に入れた構成でも、`devbase container profile down X` の前後で dev の Container ID と `StartedAt` が変わらない
- [ ] `devbase container profile down X` の後も、そのサービスが使う名前付きボリュームは残る
- [ ] `devbase container profile list` は、`compose.yml` に書かれたプロファイルの名前と、そのサービスが稼働しているかを出す
- [ ] `.docker-compose.scale.yml` が無い状態では、`devbase container profile up X` / `down X` / `list` のいずれも終了コード 1 で止まる。`devbase up` を促すメッセージを出し、コンテナは作らない
- [ ] `compose.yml` に無いプロファイル名を `up` / `down` へ渡すと、存在する名前の一覧を出して終了コード 1 で止まる
- [ ] `devbase project profile up <プロジェクト> X` は、そのプロジェクトのディレクトリで `devbase container profile up X` を実行したのと同じ結果になる（`down` / `list` も同じ）

停止の網羅:

- [ ] プロファイル X のサービスが起動している状態で `devbase down` を実行すると、既定のサービスとプロファイル X のサービスの両方が削除され、終了コード 0 で終わる
- [ ] 同じ状態で `devbase up` を実行すると、冒頭の停止でプロファイル X のサービスも止まり、起動後は既定のサービスだけが動いている
- [ ] `COMPOSE_PROFILES` が環境変数に設定された状態でも、`devbase up` は既定のサービスだけを起動する
- [ ] プロジェクトの `.env` に `COMPOSE_PROFILES` が書かれた状態でも、`devbase up` は既定のサービスだけを起動する（起動の対象をサービス名で明示するため。設計の決定 7）

フック:

- [ ] `./pre-up` と `./deploy` は `DEVBASE_ACTIVE_PROFILES` を受け取る。`devbase up` から呼ばれるときは、どちらも空である
- [ ] `devbase container profile up X` の後の `./deploy` は `DEVBASE_ACTIVE_PROFILES=X` を受け取る。値は常にプロファイル名 1 つである
- [ ] 同時に 2 つ以上のプロファイルを起動する操作は今回作らない。よって複数の値が渡る経路は無い。カンマ区切りは将来の拡張のための予約であり、この範囲では受け入れ条件にしない
- [ ] `devbase container profile up X` は `./pre-up` を呼ばない
- [ ] `devbase container profile up X` は、サービスの起動が終わった後にプロジェクトのフックを呼ぶ。フックが終了コード 0 以外を返したら、コマンドも 0 以外で終わる

退行しないこと:

- [ ] `profiles:` を 1 つも持たないプロジェクトで、`devbase up` / `down` / `scale` が起動するコンテナの集合と順序が現在と変わらない
- [ ] 生成される `.docker-compose.scale.yml` は、プロファイル付きのサービスについても `profiles:` を保ったまま出力する

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | プロファイルのサービスを起動・停止した記録が、既存のログと同じ体裁（`logger.info`）で残る |
| セキュリティ | プロファイルのサービスへ渡す機密は、そのサービスが元々 `env_file` で参照していた由来のキーだけに限る（`_services_receiving_secrets` の現在の規則を変えない）。素の `docker compose` を使わず devbase を通すのは、機密の注入と対象サービスの限定をこの規則の中で行うためである |
| システム環境 | Docker Compose 2.20.0 以上で動く。devbase が使うのは `--profile '*'`、`--no-deps`、サービスを明示した `stop` / `rm -f` である。案内する `depends_on.required` が 2.20.0 以上を要するため、2.20.0 未満は対象外とする |
| 再現性 | 有効なプロファイルは devbase が `--profile` で決める。利用者の `COMPOSE_PROFILES` に結果が左右されない（設計の決定 7） |

最低対応版を 2.20.0 とする根拠は次のとおりである。

| 使う機能 | 使える版 | 根拠 |
| --- | --- | --- |
| `--no-deps` | 2 系全般 | `docker compose up` の古くからある選択肢。版の下限を作らない |
| `stop <サービス...>` / `rm -f <サービス...>` | 2 系全般 | どちらもサービス指定が古くから安定している。版の下限を作らない |
| `depends_on.required` | 2.20.0 以上 | [公式仕様](https://docs.docker.com/reference/compose-file/services/#depends_on)に「Introduced in Docker Compose version 2.20.0」とある |
| `--profile '*'` | v5.1.4 で確認済み。2.20.0 以上 5.x 未満は未検証 | 公式ドキュメントに版の記載が無い（設計の「未確認のまま残ること」） |

`down` の `[SERVICES]` 位置引数は使わない。プロファイルの停止は `stop` と `rm -f` の 2 段で行う（設計の決定 5）。この位置引数は比較的新しい追加で、対応版を調べないと下限を決められない。加えて、指定したサービスへ `depends_on` を持つ側まで対象に含める。使わないことで、どちらの問題も起きない。

版の下限を作る機能は `depends_on.required` だけである。`--no-deps` も `stop` / `rm -f` も 2 系全般で使える。`--profile '*'` は停止の対象を広げる向きの指定であり、解釈しない版でも対象が現在と同じになる想定のため、下限を引き上げない（未検証。設計の「未確認のまま残ること」）。よって 2.20.0 という下限は `depends_on.required` だけで閉じる。2.20.0 未満の v2 では、`required: false` を書いた構成の検証に失敗する。受け入れ条件と文書の例はこの属性を使う。だから「機能は落ちるが壊れない」とは言わず、対象外と定める。動作を確かめたのは Docker Compose v5.1.4 である。

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `devbase container profile` と `devbase project profile` を追加する。既存のコマンドの引数は変えない。`devbase down` は内部で `--profile '*'` を付ける |
| データ | なし（スキーマも名前付きボリュームの構成も変えない） |
| 既存の振る舞い | `down` が全プロファイルを対象にする。devbase 経由の Compose へ `COMPOSE_PROFILES` を渡さなくなる。フックへ渡す環境変数が 1 つ増える。`profiles:` を使っていないプロジェクトでは、どちらも対象が変わらない。`--profile '*'` を確認済みなのは v5.1.4 で、2.20.0 以上 5.x 未満は未検証である |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest tests/ -q`（コマンドの組み立てと生成物の検証。実 docker には触れない） |
| 静的解析 | `uv run ruff check lib/ tests/`（設定がある場合。無ければ省く） |
| 手動確認 | `profiles` を付けた最小の compose（dev / app、`alpine:3`）で `devbase up` → `devbase container profile up X` → `devbase container profile down X` → `devbase down` を通し、各段で `docker ps` の Container ID と `StartedAt` を記録する。app へ `depends_on: {dev: {condition: service_started, required: false}}` を付けた版と、`depends_on: [dev]` を付けた版でも同じ手順を通す。さらに dev の環境変数の値を変えてから `profile up X` を実行し、dev が再作成されないことを確かめる |
| 手動確認（依存の向きが逆の構成） | dev へ `depends_on: {db: {condition: service_started, required: false}}` を書き、db をプロファイル X に入れた構成で `profile up X` → `profile down X` を通す。停止の前後で dev の Container ID と `StartedAt` が変わらないことを見る。`stop` / `rm -f` が依存元を対象に含めないことの確認である（設計の決定 5） |
| 手動確認（`COMPOSE_PROFILES` が有効な環境） | `COMPOSE_PROFILES=X` を環境変数に設定した状態と、プロジェクトの `.env` に書いた状態の両方で `devbase up` を通す。`docker ps` に既定のサービスだけが並ぶことを見る。続けて `profile up X` → `profile down X` が従来どおり効くことも確かめる |
| 手動確認（退行） | 確認済みの Docker Compose v5.1.4 で実施する。`profiles:` を持たないプロジェクトで `devbase up` と `devbase down` を通し、従来どおり動くことを確かめる。起動するコンテナの集合と順序、`down` 後に何も残らないことを見る。`--profile '*'` がこの経路に入るためである |

自動テストで dev の Container ID の不変を確かめることはできない（実コンテナが要る）。この条件は手動確認で判定する。

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | CLI の実装は `lib/devbase/` 配下（CONTRIBUTING.md）。コマンドの追加は `lib/devbase/cli.py` と `lib/devbase/commands/container.py`、compose の生成物に関わる部分は `lib/devbase/volume/compose.py` |
| コーディング規約 | Python は PEP 8（CONTRIBUTING.md）。文書は `docs/` 配下、図は Mermaid |
| テスト戦略 | `tests/commands/` と `tests/volume/` の既存の書き方に合わせ、`subprocess` を差し替えて **組み立てたコマンド列**を検証する。実 docker と実 DEVBASE_ROOT には触れない（テストは実環境の `DEVBASE_ROOT` を継承するため、backend を隔離する） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、変更範囲内の命名統一、日本語のコメントと文書 |
| 確認してから行う | 既存コマンドの引数の変更、`project.yml` のスキーマ追加、フック名の新設 |
| 行わない | 依頼範囲外のリファクタリング、`.docker-compose.scale.yml` の生成規則のうちプロファイルに関係しない部分の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| プロファイル起動の後に呼ぶフックを `./deploy` の再利用にするか、新しい名前にするか | 設計工程（`design`）で決める | 実装計画の前 |
