# repo 連携プロジェクトと `pre-up` populate パターン

外部リポジトリ（アプリ本体）を丸ごと取り込み、複数コンテナ（app / nginx / db 等）で共有して動かすタイプのプロジェクト向けのガイドです。`pre-up` ライフサイクルフックで **ホスト側リポジトリの clone/pull** と **共有 work ボリュームへの populate** を行い、2 回目以降の `devbase up` では populate 済みを検出して同期をスキップする冪等パターンを解説します。

リファレンス実装は Laravel Sail ベースの `carmo-system-console` プラグインです。**このリポジトリには含まれません** — 社内向けの private プラグインレジストリで配布されており、`devbase plugin install` 後に `projects/carmo-system-console/`（`projects/` は `.gitignore` 対象）へ展開されます。アクセス権が無い場合でも、本書のコード断片と [チェックリスト](#7-チェックリスト新規に-repo-連携プロジェクトを作るとき) だけでパターンを再現できます。

> **前提:** ライフサイクルフック自体の基本は [プラグイン開発クイックスタート](quickstart.md#25-ライフサイクルフック任意) を、共有ボリュームや `CONTAINER_SCALE` の一般論は [compose.yml ガイドライン](compose-yml-guidelines.md) と [コンテナ操作ガイド](../user/container-operations.md#並行開発) を参照してください。本書はそれらを組み合わせた「repo 連携」パターンに絞って説明します。

---

## 1. なぜこのパターンが必要か

`devbase-general` / `devbase-php` のような単一 dev コンテナのプロジェクトでは、各コンテナが専用の `/work` ボリュームを持ち、ソースはコンテナ内で `git clone` すれば十分です。

一方で、アプリ本体のリポジトリに付属する `docker-compose.dev.yml` 相当（app / nginx / mysql / redis …）を devbase 上で再現したい場合、次の要件が生じます。

- **複数コンテナが同一のソースツリーを共有**する必要がある（app が書いた成果物を nginx が配信する等）。
- app サービスは **リポジトリ内の `Dockerfile` をビルドコンテキスト**として使うため、ホスト側にソースの実体が必要。
- コンテナ内 `git clone` に頼ると、複数コンテナの起動順で **clone レース**が起きる。

これを解決するのが「ホスト `repo/` を用意し、それを共有 work ボリュームへ populate してから全コンテナを起動する」パターンです。populate を `pre-up`（`docker compose up` の前）に寄せることで、app / nginx / mysql が立ち上がる前にソースを確定できます。

---

## 2. 全体構成

```mermaid
graph TD
    S["リモート git リポジトリ<br/>(volareinc/app 等)"] -->|"pre-up ① clone/pull"| R["ホスト ./repo<br/>(app のビルドコンテキスト)"]
    S3["S3<br/>env/&lt;env&gt;.env"] -->|"pre-up ② 取得"| E["ホスト ./.env<br/>(compose 変数展開用)"]
    R -->|"pre-up ③ populate"| V["共有 work ボリューム<br/>/work/&lt;GIT_REPO&gt;"]
    E -->|"pre-up ④ 配置"| V
    V --> A["app コンテナ /work"]
    V --> N["nginx コンテナ /work:ro"]
    V --> M["mysql コンテナ /work:ro"]
    V --> D["dev コンテナ /work"]
```

| 要素 | 実体 | 役割 |
|------|------|------|
| ホスト `./repo` | `git clone` した作業コピー | app イメージのビルドコンテキスト兼、work ボリュームの populate 元 |
| ホスト `./.env` | S3 から取得 | `docker compose` の変数展開（`${DB_DATABASE}` 等）に使用 |
| 共有 work ボリューム | `external: true` の named volume | 全コンテナが `/work` にマウントする実行時ソース |

`compose.yml` では work ボリュームを **external** として宣言し、インスタンスごとに名前を切り替えます。

```yaml
services:
  app:
    build:
      context: ./repo          # ← ホスト repo/ をビルドコンテキストに
      dockerfile: docker/Dockerfile
    volumes:
      - work:/work             # ← 共有 work ボリューム
  nginx:
    volumes:
      - work:/work:ro
  # ...
volumes:
  work:
    external: true
    name: ${DEVBASE_WORK_VOLUME:-devbase_work_${DEVBASE_INSTANCE_INDEX:-1}}
```

> **Note:** `pre-up` は子プロセスのため `export DEVBASE_WORK_VOLUME` しても後続の `docker compose up` へは伝播しません。`compose.yml` 側は `${DEVBASE_WORK_VOLUME:-devbase_work_${DEVBASE_INSTANCE_INDEX:-1}}` のフォールバック式で解決し、加えて `pre-up` が同じ値を `.env` に書き出すことで整合を取ります。

### スケール前提: `CONTAINER_SCALE=1`

**このパターンは scale=1（1 プロジェクト = 1 work ボリューム）を前提としています。** devbase の既定は `CONTAINER_SCALE=2` なので、プロジェクトの `env` に `CONTAINER_SCALE=1` を明示してください。

現行実装では、scale>1 にすると「全コンテナが同一のソースツリーを共有する」という本パターンの前提が次の 2 点で崩れます。

- **`pre-up` はインデックスなしで 1 回しか実行されない。** devbase が `DEVBASE_INSTANCE_INDEX` を環境変数として渡すのは、インスタンスごとに実行される `deploy` フックだけです。`pre-up` には渡らないため、populate されるのは `.env`（または既定値 `1`）で解決される **単一の work ボリュームのみ**で、`devbase_work_2` 以降は空のまま残ります。
- **scale 生成が書き換えるのは dev サービスの `/work` だけ。** `.docker-compose.scale.yml` の生成では `dev-<index>` の `/work` マウントが `devbase_work_<index>` へ固定で差し替えられる一方、app / nginx / mysql などの非 dev サービスは `compose.yml` に書いた共有 work ボリュームを参照し続けます。結果として dev-2 以降だけが別（空の）ボリュームを見ることになります。

さらに、**同一リポジトリを複数インスタンスへ分離して並行稼働させることは現行実装では未サポート**です。回避策として思いつく 2 つの手はいずれも成立しません。

- **`DEVBASE_WORK_VOLUME` で名前を分ける。** scale 生成は dev サービスの `/work` を `devbase_work_<index>`（scale=1 なら常に `devbase_work_1`）へ無条件に差し替えます（`compose.yml` に `/work` マウントを書いていなくても追加されます）。`DEVBASE_WORK_VOLUME` が効くのは app / nginx など非 dev サービスだけなので、既定名以外を指定すると dev だけが別ボリュームを見る分裂状態になります。
- **プロジェクトディレクトリごと複製する。** work ボリュームは `COMPOSE_PROJECT_NAME` の接頭辞が付かない[グローバルな external ボリューム](#クリーンに作り直す再-populate)なので、複製先も同じ `devbase_work_1` を共有します。分離になりません。

同一リポジトリを同時に複数環境で動かす必要がある場合は、Docker ホスト（`docker context`）そのものを分けてください。なお **別リポジトリ**の repo 連携プロジェクト同士は、populate 先が `/work/<GIT_REPO>` とサブディレクトリで分かれるため、同じ work ボリュームを共有したまま共存できます。

---

## 3. `pre-up` の 4 つの責務

`pre-up` は毎回の `devbase up` 前に次を行います。

| # | 処理 | 内容 |
|---|------|------|
| ① | `repo/` の clone / pull | 無ければ `git clone`、あれば `git pull --ff-only`（app ビルドコンテキストの最新化） |
| ② | `.env` の取得 | S3 等から取得してホスト `./.env` に配置（`docker compose` の変数展開前に必要） |
| ③ | work ボリュームへ populate | `repo/` の内容を `/work/<GIT_REPO>` へコピー |
| ④ | `.env` を work ボリュームへ配置 | Laravel 等のランタイムが `/work/<GIT_REPO>/.env` を参照するため |

② を `deploy`（`up` 後フック）ではなく `pre-up` で行うのは、`compose.yml` の `MYSQL_DATABASE: ${DB_DATABASE:-...}` のような変数展開が `docker compose` パース時（＝ MySQL コンテナ初回起動前）に `.env` を要求するためです。`deploy` 段階では間に合わず、DB がデフォルト名で初期化されてしまいます。

---

## 4. 冪等性 — populate 済みならスキップ（重要）

**このパターンの肝は「初回だけ populate し、2 回目以降はコンテナ側に触れない」ことです。**

`pre-up` は work ボリューム上に `/work/<GIT_REPO>/.git` が存在するかどうかで populate 済みを判定し、済みの場合は ②③④ をスキップします。

| # | 処理 | 未populate（初回） | populate 済み（2回目以降） |
|---|------|:---:|:---:|
| ① | `repo/` の `git pull` | 実行 | **実行**（構成変更をビルドに追従） |
| ② | `.env` の S3 取得 | 実行 | スキップ |
| ③ | ソース populate | 実行 | スキップ |
| ④ | `.env` を volume へ配置 | 実行 | スキップ |

### なぜスキップするのか

populate 済みの work ボリュームを毎回ホスト `repo/` で上書き同期すると、次の破壊が起きます。

- **同期の除外リスト（`storage/` / `vendor/` / `node_modules/` / `.env` 等）に無いファイルが消える。** コンテナ内で生成した認証ファイルや作業ファイルが `devbase up` のたびに削除される。
- **コンテナ側で編集した `.env` が上書きされる。**

これを避けるため、実行時ソースと `.env` の供給は初回 populate 時に限定し、以降はコンテナ側を手動管理に委ねます。これはアプリ本体リポジトリが取る一般的な開発フローと同じ考え方です。多くのリポジトリでは、環境ファイルの取得やソースの用意は**ビルド時のセットアップスクリプト**が担い、日常の**起動（`docker compose up`）は環境ファイルやソースに触れません**。devbase の初回 populate がこのビルド時セットアップに相当し、2 回目以降の `up` は起動だけを行います。

一方で ①（`repo/` の pull）は常に実行します。これはホスト側のビルドコンテキストであり、`compose.yml` / `Dockerfile` / `docker/` 構成の変更を次回の app イメージ再ビルドへ反映するためです（アプリのソースコードそのものは work ボリューム側で管理）。

---

## 5. ソース・`.env` の更新運用

populate 済み以降、更新経路は次のように分かれます。

| 対象 | 場所 | 更新方法 |
|------|------|---------|
| ビルドコンテキスト | ホスト `./repo` | `pre-up` が毎回 `git pull`（自動） |
| 実行時ソース | work ボリューム `/work/<GIT_REPO>` | **コンテナ内で手動 `git pull`** |
| 実行時 `.env` | work ボリューム `/work/<GIT_REPO>/.env` | コンテナ内で手動編集 |

```bash
# 実行時ソースの更新（dev コンテナ内）
cd /work/<GIT_REPO>
git pull origin main
```

### クリーンに作り直す（再 populate）

`.env` やソースを S3 / `repo/` の内容からやり直したい場合は、populate 済み判定に使われる `/work/<GIT_REPO>` を消して、次回 `up` で populate を再実行させます。

> **Warning:** work ボリューム（既定 `devbase_work_1`）は `COMPOSE_PROJECT_NAME` の接頭辞が付かない **グローバルな external ボリューム**で、同じインスタンス index を使う **すべての devbase プロジェクトが共有**します。`docker volume rm` でボリュームごと消すと、停止中の別プロジェクトのソースや生成物まで巻き添えで失われます。プロジェクトの分離単位はボリュームではなく `/work/<GIT_REPO>` サブディレクトリなので、**通常はサブディレクトリだけを削除**してください。

**推奨: このプロジェクトのサブディレクトリだけを削除する**

```bash
devbase down

# 何が入っているか（＝他プロジェクトが同居していないか）を確認
docker run --rm -v devbase_work_1:/work alpine ls -la /work

# このプロジェクトのソースだけを削除（<GIT_REPO> は env の値）
docker run --rm -v devbase_work_1:/work alpine rm -rf /work/<GIT_REPO>

devbase up                        # pre-up が ②③④ を再実行
```

**ボリュームごと作り直す場合**（他プロジェクトが同じ work ボリュームを使っていないことを確認してから）

```bash
devbase down

# このボリュームをマウントしているコンテナを列挙（停止中も含む）
docker ps -a --filter volume=devbase_work_1 --format '{{.Names}}'

docker volume rm devbase_work_1   # external volume のため project 名の接頭辞は付かない
                                  # DEVBASE_WORK_VOLUME を設定している場合はその名前
devbase up
```

> **Note:** どちらの手順でも、削除前にコンテナ内で加えた変更（未コミットのソース変更、編集した `.env`）をコミット / 退避してください。DB 等の `sail-*` ボリュームは別管理なので、work ボリュームを消してもデータは残ります。

---

## 6. 関連する環境変数

| 変数 | 既定 | 効果 |
|------|------|------|
| `DEVBASE_REPO_PULL` | `1` | `0` にすると ①（`repo/` の `git pull`）を抑止。オフラインや意図的にビルドコンテキストを固定したいとき |
| `DEVBASE_ENV_OVERWRITE` | `backup` | 未 populate 時の既存ホスト `.env` の扱い。`backup`（`.env.bak.<ts>` に退避して上書き）/ `skip`（既存があれば S3 取得しない）/ `force`（退避せず上書き） |
| `DEVBASE_WORK_VOLUME` | `devbase_work_<index>` | `compose.yml` が参照する共有 work ボリューム名の明示指定。未指定なら `DEVBASE_INSTANCE_INDEX` から解決。ただし効くのは **app / nginx など非 dev サービスだけ**で、dev サービスの `/work` は scale 生成時に `devbase_work_<index>` へ無条件に差し替えられます。dev から実行時ソースを触る本パターンでは **既定名のまま**にしてください（[スケール前提](#スケール前提-container_scale1) を参照） |
| `DEVBASE_INSTANCE_INDEX` | `1` | work ボリューム名のインデックス。**devbase 本体が渡すのは `deploy` フックに対してのみ**で、`pre-up` や `docker compose` のプロセス環境には渡りません。`compose.yml` の `${DEVBASE_INSTANCE_INDEX:-1}` は `.env` に書かれた値、無ければ `1` に解決されます（[スケール前提](#スケール前提-container_scale1) を参照） |

> **Note:** `.env` の環境選択（例: `s3://.../env/local.env` の `local` 部分）など、S3 パスやプロファイルはプロジェクト固有の変数（例: `CARMO_ENV`）で制御することがあります。プロジェクトの `pre-up` 冒頭コメントを参照してください。

---

## 7. チェックリスト（新規に repo 連携プロジェクトを作るとき）

- [ ] `env` に `GIT_USER` / `GIT_REPO` を定義した
- [ ] `env` に `CONTAINER_SCALE=1` を明記した（既定は `2`。[スケール前提](#スケール前提-container_scale1) を参照）
- [ ] `compose.yml` で work ボリュームを `external: true` + `name: ${DEVBASE_WORK_VOLUME:-devbase_work_${DEVBASE_INSTANCE_INDEX:-1}}` で宣言した
- [ ] app サービスの `build.context` をホスト `./repo` にした
- [ ] `pre-up` で ①clone/pull → ②`.env`取得 → ③populate → ④`.env`配置 を実装した
- [ ] `pre-up` が `/work/<GIT_REPO>/.git` の有無で populate 済みを判定し、②③④ をスキップする
- [ ] populate 時の owner を `1000:1000`（コンテナ内ユーザー）に設定した
- [ ] `storage/` / `vendor/` / `node_modules/` 等、初回のみ生成され上書きしたくないパスの扱いを決めた
- [ ] README にソース・`.env` の更新運用（手動 pull / 再 populate）を記載した

---

## 参考

- リファレンス実装: `carmo-system-console` プラグインの `pre-up` / `compose.yml` / `README.md`（社内 private レジストリ配布。インストール後は `projects/carmo-system-console/` 配下に展開され、このリポジトリには含まれません）
- [プラグイン開発クイックスタート](quickstart.md) — ライフサイクルフックの基本
- [compose.yml ガイドライン](compose-yml-guidelines.md) — 共有ボリューム・スケール構成
- [コンテナ操作ガイド](../user/container-operations.md) — `/work` ボリュームの一般論
