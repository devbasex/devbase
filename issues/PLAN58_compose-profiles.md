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
- [ ] `devbase container profile down X` の後も、そのサービスが使う名前付きボリュームは残る
- [ ] `devbase container profile list` は、`compose.yml` に書かれたプロファイルの名前と、そのサービスが稼働しているかを出す
- [ ] `.docker-compose.scale.yml` が無い状態では、`devbase container profile up X` / `down X` / `list` のいずれも終了コード 1 で止まる。`devbase up` を促すメッセージを出し、コンテナは作らない
- [ ] `compose.yml` に無いプロファイル名を `up` / `down` へ渡すと、存在する名前の一覧を出して終了コード 1 で止まる
- [ ] `devbase project profile up <プロジェクト> X` は、そのプロジェクトのディレクトリで `devbase container profile up X` を実行したのと同じ結果になる（`down` / `list` も同じ）

停止の網羅:

- [ ] プロファイル X のサービスが起動している状態で `devbase down` を実行すると、既定のサービスとプロファイル X のサービスの両方が削除され、終了コード 0 で終わる
- [ ] 同じ状態で `devbase up` を実行すると、冒頭の停止でプロファイル X のサービスも止まり、起動後は既定のサービスだけが動いている

フック:

- [ ] `./pre-up` と `./deploy` は `DEVBASE_ACTIVE_PROFILES` を受け取る。`devbase up` から呼ばれるときは、どちらも空である
- [ ] `devbase container profile up X` の後の `./deploy` は `DEVBASE_ACTIVE_PROFILES=X` を受け取る（複数あればカンマ区切り）
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
| システム環境 | Docker Compose v2 系および v5 系で動く。`--profile '*'` と `depends_on.required` を使う |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `devbase container profile` と `devbase project profile` を追加する。既存のコマンドの引数は変えない。`devbase down` は内部で `--profile '*'` を付ける |
| データ | なし（スキーマも名前付きボリュームの構成も変えない） |
| 既存の振る舞い | `down` が全プロファイルを対象にする。フックへ渡す環境変数が 1 つ増える。`profiles:` を使っていないプロジェクトでは、どちらも観測できる違いを生まない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest tests/ -q`（コマンドの組み立てと生成物の検証。実 docker には触れない） |
| 静的解析 | `uv run ruff check lib/ tests/`（設定がある場合。無ければ省く） |
| 手動確認 | `profiles` を付けた最小の compose（dev / app、`alpine:3`）で `devbase up` → `devbase container profile up X` → `devbase container profile down X` → `devbase down` を通し、各段で `docker ps` の Container ID と `StartedAt` を記録する |

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
