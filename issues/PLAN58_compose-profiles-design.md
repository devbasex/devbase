# 189: Compose の profiles で付随サービス群を後から起動・停止する

要求と受け入れ条件は [PLAN58_compose-profiles.md](PLAN58_compose-profiles.md) にある。この文書は「どう作るか」だけを扱う。

## 機能一覧

| # | 機能 | 誰が使うか |
| --- | --- | --- |
| F1 | プロファイルのサービスを起動する | プロジェクトの利用者 |
| F2 | プロファイルのサービスを停止して削除する | プロジェクトの利用者 |
| F3 | プロファイルの一覧と稼働状況を見る | プロジェクトの利用者 |
| F4 | 停止（`down` と `up` 冒頭）を全プロファイルへ効かせる | プロジェクトの利用者 |
| F5 | 有効なプロファイルをフックへ伝える | プロジェクトの作者 |

## 構成要素

| 要素 | 責務 |
| --- | --- |
| プロファイルの解決 | 生成済みの構成ファイルを読み、プロファイル名からサービス名の集合を求める。`profiles` を持たない既定のサービスの一覧も同じ場所から求める。稼働状況は持たない |
| プロファイルの操作 | 起動・停止・一覧の 3 つの入口。接続先の反映と機密の注入を済ませてから Compose を呼ぶ |
| Compose の呼び出し | `docker compose` のコマンド列を組み立てて実行する。起動には `--no-deps` を付ける。プロファイルの停止は `stop` と `rm -f` の 2 段で行う。全体の停止には全プロファイルを指定する。有効なプロファイルは devbase が `--profile` で決め、`COMPOSE_PROFILES` は子プロセスの環境から外す。`devbase up` の起動は既定のサービスを明示して渡す（決定 7） |
| フックの実行 | プロジェクトの `./deploy` を、有効なプロファイルを環境変数へ載せて呼ぶ。同じ環境変数は `./pre-up` にも渡る |
| 引数の受け口 | `project` / `container` の配下に `profile` のサブコマンドを足す |

```mermaid
graph TD
    subgraph 入口
        CLI[引数の受け口]
    end
    subgraph 操作
        OP[プロファイルの操作]
        RS[プロファイルの解決]
        HK[フックの実行]
    end
    subgraph 実行
        DC[Compose の呼び出し]
    end
    CLI --> OP
    OP --> RS
    OP --> DC
    OP --> HK
    HK --> DC
```

## システム構成

### 文脈

```mermaid
graph LR
    利用者 --> devbase[devbase の配布物]
    devbase --> compose[Docker Compose]
    compose --> daemon[Docker デーモン]
    devbase --> hook[プロジェクトのフック]
    devbase --> store[機密の置き場]
```

Docker Compose と Docker デーモンは、こちらが変えられない外部の系である。プロジェクトのフックは各プロジェクトが持つ。devbase が定めるのは呼び出しの約束（環境変数）だけである。

### 配置

```mermaid
graph TD
    subgraph 利用者の端末
        W[wrapper: bin/devbase]
        P[Python: lib/devbase]
        F[生成物: .docker-compose.scale.yml]
    end
    subgraph Docker の実行基盤
        D1[dev-1..N]
        D2[プロファイルのサービス]
    end
    W -->|引数と env| P
    P -->|読む| F
    P -->|コマンド列 + 機密の環境変数| D2
    P -.->|触らない| D1
```

境界をまたぐのは 2 つである。Compose へ渡すコマンド列と、`_inject_secrets` がプロセスの環境へ載せた機密である。この環境からは `COMPOSE_PROFILES` を取り除く。有効なプロファイルを決めるのは devbase の `--profile` だけにするためである（決定 7）。プロファイルの操作はサービス名をすべて明示して渡す。起動はさらに `--no-deps` を付ける。停止は対象を広げない `stop` と `rm -f` を使う（決定 5）。そのため dev-1..N はどちらの操作の対象にも入らない。

## 置き場所

```text
lib/devbase/
├── cli.py                     # profile サブコマンドの登録（変更）
├── commands/
│   └── container.py           # cmd_profile_up / down / list、dispatch（変更）
├── project/
│   └── runtime.py             # hook_env に有効なプロファイルを足す（変更）
├── utils/
│   └── docker.py              # docker_compose が COMPOSE_PROFILES を外す、docker_compose_down を全プロファイル対応へ（変更）
└── volume/
    └── compose.py             # profiles を保つことの確認のみ（変更なし）

tests/
├── commands/
│   └── test_container_profile.py   # 新設
├── utils/
│   └── test_docker_profiles.py     # 新設（子プロセスの env から COMPOSE_PROFILES が外れることの検査）
└── volume/
    └── test_compose_profiles.py    # 新設
```

## 構造

型は追加しない。変わるのは処理の順序と、関数の責務の境目である。

| 関数 | 変更 | 責務 |
| --- | --- | --- |
| `profile_services(compose: dict) -> dict[str, list[str]]` | 新設（`commands/container.py`） | 構成の辞書から「プロファイル名 → サービス名」を作る。純粋な処理で、終了コードも出力も持たない |
| `default_services(compose: dict) -> list[str]` | 新設（`commands/container.py`） | 構成の辞書から `profiles` を持たないサービス名を宣言順に返す。`cmd_up` が起動の対象として渡す（決定 7）。純粋な処理である |
| `cmd_profile_up(profile, context)` | 新設 | F1。プロファイルのサービスをすべて明示し、`--no-deps` を付けて起動する（決定 2）。終了コードを返す |
| `cmd_profile_down(profile, context)` | 新設 | F2。`docker_compose_down` は通さず、`stop` と `rm -f` の 2 段をサービス名付きで組む（決定 5）。終了コードを返す |
| `cmd_profile_list(context)` | 新設 | F3。終了コードを返す |
| `docker_compose(command, ...)` | 変更（`utils/docker.py`） | F1 / F2 / F4 の共通の土台。現在は `subprocess.run(cmd, ...)` を `env=` なしで呼ぶ。`env=` を新たに構築し、`COMPOSE_PROFILES` を除いたうえで渡す（決定 7）。コマンド列の組み立て方は変えない |
| `docker_compose_up(compose_file, detach=True, services=())` | 変更（`utils/docker.py`） | 起動の対象を受け取り、`['up', '-d', *services]` を組む。空なら現在と同じ `['up', '-d']` になる |
| `_compose_run(subcommand, ...)` | 変更（`commands/container.py:269`） | `docker_compose` を経由せず直接 `subprocess.run` する経路（`ps` / `logs`）。同じ `env=` を組み立てて渡す（決定 7） |
| `_run_deploy_pipeline(...)` | 変更（`commands/container.py`） | 生成した `.docker-compose.scale.yml` から `default_services` を求め、`docker_compose_up` へ渡す |
| `docker_compose_down(compose_file, all_profiles=True)` | 変更（`utils/docker.py`） | F4。`--profile '*'` を付けて呼ぶ。`['down', '-t0']` という固定の形は変えない |
| `hook_env(config, active_profiles=())` | 変更（`project/runtime.py`） | F5。`DEVBASE_ACTIVE_PROFILES` を足す。`_run_pre_up_hook` と `_run_deploy_script_for_instances` の両方に効く |
| `_run_deploy_script_for_instances(deploy_script, indices, config=None, active_profiles=()) -> bool` | 変更（`commands/container.py`） | 全インスタンスで成功したかを返す。`active_profiles` を受け取り、加工せず `hook_env` へ渡す。`cmd_up` は戻り値を使わず、現在の警告だけの扱いを保つ |
| `_dispatch_lifecycle` | 変更 | `profile` を handlers へ 1 つ足し、`args.profile_subcommand` で 3 つの入口へ振り分ける |

`active_profiles` は `cmd_profile_up` からフックまで、引数として順に手渡す。`_run_deploy_script_for_instances` がこの引数を持たないと、`hook_env` へ値を届ける経路が無い。既存の呼び出しを壊さないため、既定は空のタプルとする。

| 呼び出し元 | 渡す値 | `./deploy` が受け取る `DEVBASE_ACTIVE_PROFILES` |
| --- | --- | --- |
| `cmd_up` | 省略（既定の `()`） | 空文字列 |
| `cmd_profile_up` | `(X,)`（起動したプロファイル名） | `X` |

`_run_deploy_script_for_instances` は受け取った値を加工せず `hook_env(config, active_profiles=active_profiles)` へ渡す。環境変数の名前と区切り（カンマ）を決めるのは `hook_env` だけである。

今回の範囲では、渡る値は常にプロファイル名 1 つである。`cmd_profile_up` が受ける名前が 1 つだけで、同時に 2 つ以上を起動する操作を作らないためである。カンマ区切りは将来の拡張のための予約であり、現時点でその形になる経路は無い（「未確認のまま残ること」）。

`_run_deploy_script_for_instances` へ渡す `indices` は `range(1, scale + 1)` である。`scale` は `project.yml` の `config.scale` から取る。未指定なら `project_runtime.DEFAULT_SCALE`（現在は 2）を使う。`cmd_up` が使っている解決の式をそのまま再利用する。`.docker-compose.scale.yml` の `dev-*` を数え直すことはしない。

`profile` の subparser は `dest` を親と分ける。親の `project` / `container` は `dest='subcommand'` のままとし、入れ子側は `dest='profile_subcommand'` を使う。`cli.py` の `_dispatch` は `args.subcommand == 'list'` を見て `project list`（プロジェクト一覧）へ振り分けるためである。入れ子で `subcommand` を再利用すると、`devbase project profile list` がそちらへ流れてしまう。`_dispatch_lifecycle` の handlers には `'profile'` を 1 つだけ足す。その中で `profile_subcommand` を見て up / down / list を選ぶ。

## 入出力の契約

仕様記述の置き場所は設けない。OpenAPI の対象になる経路が無く、変わる約束はコマンドだけである。

### 変わる約束

| 名前 | 入力 | 出力（成功） | 失敗の形 |
| --- | --- | --- | --- |
| `devbase project profile up [name] <profile>` | プロファイル名（必須）、プロジェクト名（省略時は現在地） | 対象サービスを起動し、フックを実行して 0 | 構成ファイルが無い / 未知のプロファイル / Compose かフックが失敗 → 1 |
| `devbase project profile down [name] <profile>` | 同上 | 対象サービスを停止し、そのコンテナを削除して 0 | 同上。`stop` と `rm -f` のどちらかが失敗すれば 1（フックは呼ばない） |
| `devbase project profile list [name]` | プロジェクト名（省略可） | プロファイルと稼働状況を表で出して 0 | 構成ファイルが無い → 1 |
| `devbase container profile up <profile>` / `down <profile>` / `list`（`ct` も同じ） | プロファイル名のみ。プロジェクト名は受け付けない | 同上。非推奨の警告を 1 行出す | 同上 |

`project` の `[name]` と `<profile>` の並びは既存の `scale` と同じ規則に従う。値が 1 個ならプロファイル名に割り当てられる。2 個なら（プロジェクト名、プロファイル名）になる。

`container` / `ct` は `[name]` を持たない。値は常にプロファイル名である。`container` 群のサブコマンドは現在地のプロジェクトで動く既存の規約に従う。この非対称は `project` / `container` の既存の作りと同じである。

### プロファイルの決め方

有効なプロファイルは devbase が経路ごとに明示して決める。利用者の環境や `.env` には従わない（決定 7）。

| 経路 | `--profile` | 対象の渡し方 | `COMPOSE_PROFILES` |
| --- | --- | --- | --- |
| `devbase up` の起動 | 付けない | 既定のサービスをすべて明示する | 子プロセスの環境から外す |
| `devbase down` と `up` 冒頭の停止 | `--profile '*'` | 渡さない | 同上 |
| `profile up X` / `profile down X` | `--profile X` | 対象のサービスをすべて明示する | 同上 |

`COMPOSE_PROFILES` は空文字列にするのではなく、渡さない。空文字列を渡す版の扱いを調べずに済むためである。

**環境変数を外すだけでは足りない。** Compose はプロジェクトの `.env` を自分で読むため、`devbase up` の起動では既定のサービス名も明示する（決定 7）。

### `profile list` の表

| 列 | 内容 |
| --- | --- |
| PROFILE | プロファイル名。生成物に現れた順で並べる |
| SERVICES | そのプロファイルに属するサービス名。宣言順にカンマ区切りで並べる |
| RUNNING | `稼働数/総数` と状態語。例: `3/3 running`、`1/3 partial`、`0/3 stopped` |

状態語の決め方は次のとおりである。

| 稼働数 | 状態語 |
| --- | --- |
| 総数と同じ | `running` |
| 1 以上で総数未満 | `partial` |
| 0 | `stopped` |

稼働の判定には `docker compose ps --format json` を使う。`State` が `running` のサービスだけを数える。`exited` や `paused` は稼働に数えない。プロファイルが 1 つも無ければ、見出しの行だけを出して 0 で終わる。

### 互換性の扱い

| 変更 | 既存の呼び出し側への影響 |
| --- | --- |
| `profile` サブコマンドの追加 | 無い。既存の引数の形を変えない |
| `docker compose down` に `--profile '*'` を付ける | プロファイルを持たないプロジェクトでは対象が同じになる。Docker Compose v5.1.4 で確認済み。2.20.0 以上 5.x 未満は未検証 |
| `DEVBASE_ACTIVE_PROFILES` の追加 | 無い。既存のフックは読まなければ従来どおり動く |
| `COMPOSE_PROFILES` を子プロセスへ渡さない | devbase 経由の Compose だけが対象。素の `docker compose` を手で叩く経路には影響しない |
| `devbase up` の起動に既定のサービス名を明示する | 対象は現在と同じ（`profiles` を持たないサービスの全件）。生成物は `up` のたびに作り直すため、一覧が古くなることはない |

`--profile '*'` の行だけは、影響の範囲が広い。この経路は `devbase down` と `devbase up` 冒頭の停止に入るため、プロファイルを使わない既存の全プロジェクトを通る。だから「退行しないこと」の受け入れ条件に直結する。

対象が変わらないと言えるのは、確かめた版だけである。ワイルドカードを解釈しない版で `*` がリテラルのプロファイル名として扱われるかは「未確認のまま残ること」に載せたままであり、ここでも断定しない。

| 版 | `--profile '*'` を付けた `down` の対象 | 根拠 |
| --- | --- | --- |
| v5.1.4 | 現在と同じ | 手元で確認済み |
| 2.20.0 以上 5.x 未満 | 現在と同じになる想定 | 未検証 |

`bin/devbase` の `_PROJECT_NAME_SUBCOMMANDS` は変えない。現在の中身は `up down ps logs scale rebuild` である。この一覧は「3 番目の引数をプロジェクト名として解決してよいサブコマンド」を表す。`profile` ではその位置に `up` / `down` / `list` が来る。一覧へ足すと、`up` という名前のプロジェクトが実在したときにそちらへ移動してしまう。プロジェクト名の解決は Python 側の `_dispatch_lifecycle` に任せる。

同じ一覧から `login` / `build` も外れている。この 2 つは `project` でも `[name]` を受け付けない。`profile` が `container` で `[name]` を受け付けないのも同じ筋である。

### 検査の手段

`uv run pytest tests/commands/test_container_profile.py -q` が、組み立てたコマンド列と終了コードを検査する。`uv run pytest tests/utils/test_docker_profiles.py -q` が、子プロセスへ渡す環境から `COMPOSE_PROFILES` が外れていることを検査する。同じテストで、`devbase up` の起動が既定のサービス名をすべて渡すことも検査する。

## 処理の流れ

```mermaid
sequenceDiagram
    participant U as 利用者
    participant CLI as 引数の受け口
    participant OP as プロファイルの操作
    participant RS as プロファイルの解決
    participant DC as Compose の呼び出し
    participant HK as フックの実行
    U->>CLI: devbase project profile up local-app
    CLI->>OP: cmd_profile_up("local-app")
    OP->>OP: 接続先を反映し機密を注入する
    OP->>RS: 生成済みの構成を読む
    alt 構成ファイルが無い
        RS-->>OP: 無し
        OP-->>U: devbase up を促して 1
    else 未知のプロファイル
        RS-->>OP: 該当なし
        OP-->>U: 既知の名前を並べて 1
    else
        RS-->>OP: サービス名の集合
        OP->>DC: compose --profile local-app up -d --no-deps <サービス...>
        DC-->>OP: 終了コード
        OP->>HK: ./deploy（DEVBASE_ACTIVE_PROFILES=local-app）
        HK-->>OP: 全インスタンスの成否
        OP-->>U: 0 または 1
    end
```

停止（F2）は同じ並びから、フックの呼び出しを除いたものである。Compose を呼ぶ回数だけが 2 回になる。

```mermaid
sequenceDiagram
    participant U as 利用者
    participant OP as プロファイルの操作
    participant RS as プロファイルの解決
    participant DC as Compose の呼び出し
    U->>OP: cmd_profile_down("local-app")
    OP->>RS: 生成済みの構成を読む
    RS-->>OP: サービス名の集合
    OP->>DC: compose --profile local-app stop <サービス...>
    DC-->>OP: 終了コード
    alt stop が失敗
        OP-->>U: 1（rm は呼ばない）
    else
        OP->>DC: compose --profile local-app rm -f <サービス...>
        DC-->>OP: 終了コード
        OP-->>U: 0 または 1
    end
```

`down` は使わない。`docker_compose_down` も通らない。理由は決定 5 に書く。

`--profile` は subcommand より前に置く必要がある。`docker_compose` は `-f` の後に、渡された配列をそのまま並べる。そのため `--profile X` を配列の先頭へ入れる。組み立てるコマンド列は次のとおりである。`<サービス...>` はどれもプロファイル X に属するサービスの全件である（決定 2）。

| 操作 | コマンド列 | 子プロセスの `COMPOSE_PROFILES` |
| --- | --- | --- |
| プロファイルの起動 | `['--profile', X, 'up', '-d', '--no-deps', <サービス...>]` | 外す |
| プロファイルの停止（1 段目） | `['--profile', X, 'stop', <サービス...>]` | 外す |
| プロファイルの停止（2 段目） | `['--profile', X, 'rm', '-f', <サービス...>]` | 外す |
| `devbase up` の起動 | `['up', '-d', <既定のサービス...>]`（`--profile` を付けない） | 外す |
| `devbase down` / `up` 冒頭の停止 | `['--profile', '*', 'down', '-t0']` | 外す |

`devbase up` の `<既定のサービス...>` は、生成物のうち `profiles` を持たないサービスの全件である（決定 7）。`--profile` を付けないことと合わせて、プロファイルのサービスは対象に入らない。

`up` と `down` の冒頭の停止（F4）は次のように変わる。`COMPOSE_PROFILES` の除去は `docker compose` を呼ぶどの経路にも共通で効く（決定 7）。

```mermaid
graph LR
    A[devbase down] --> B["env から COMPOSE_PROFILES を外す → compose --profile '*' down -t0"]
    C[devbase up] --> D["env から COMPOSE_PROFILES を外す → compose --profile '*' down -t0"]
    D --> E["env から COMPOSE_PROFILES を外す → compose up -d 既定のサービス一覧、--profile なし"]
    E --> F[既定のサービスだけが動く]
```

この除去が無いと、`COMPOSE_PROFILES=test` を持つ環境で `devbase up` が test のサービスまで起動する。`up` 冒頭の停止は `--profile '*'` で全部を落とすため、残骸ではなく新しい起動として現れる。`.env` に書かれた値には除去が効かない。そちらは既定のサービスの明示で防ぐ（決定 7）。

## 非機能の実現方式

| 大項目 | 要求の条件 | 実現方式 | 確かめ方 |
| --- | --- | --- | --- |
| 運用・保守性 | プロファイルのサービスを起動・停止した記録が、既存のログと同じ体裁（`logger.info`）で残る | 対象サービス名とプロファイル名を `logger.info` で 1 行ずつ出す。Compose の出力はそのまま標準出力へ流す | `caplog` で起動・停止の各 1 行を検査する |
| セキュリティ | プロファイルのサービスへ渡す機密は、そのサービスが元々 `env_file` で参照していた由来のキーだけに限る | 生成済みの `.docker-compose.scale.yml` をそのまま使う。機密の列挙はこのファイルが既に持つため、プロファイルの操作は新しい注入経路を作らない。実行前に `_prepare_compose` で既存と同じ注入を通す | 生成物のサービスごとの `environment` が、プロファイルの有無で変わらないことをテストで検査する |
| システム環境 | Docker Compose 2.20.0 以上で動く。devbase が使うのは `--profile '*'`、`--no-deps`、サービスを明示した `up` / `stop` / `rm -f` である | 最低対応版を 2.20.0 とする。`--no-deps` は起動にだけ、`--profile '*'` は全体の停止にだけ使う。プロファイルの停止は `stop` と `rm -f` で行い、`down` のサービス指定は使わない（決定 5）。ワイルドカードを解釈しない版では「`*` という名前のプロファイル」として扱われ、対象が現在と同じになる想定である（未検証）。あわせて `docker_compose` が `COMPOSE_PROFILES` を子プロセスの環境から外し、`devbase up` の起動では既定のサービスを明示する（決定 7）。サービスを明示した `up` も 2 系全般で使えるため、どちらも版の下限を作らない | v5.1.4 で全サービスが消えることを手動で確かめる。同じ版で、プロファイルを持たないプロジェクトの `up` / `down` が従来どおり動くことも確かめる。`COMPOSE_PROFILES=test` を環境変数と `.env` の両方に置いて `devbase up` を通し、既定のサービスだけが動くことも確かめる。`--profile '*'` が使える最古の版は公式ドキュメントに記載が無く、2.20.0 以上 5.x 未満は未検証のまま「未確認のまま残ること」に載せる |

起動で既定のサービスを対象から外すのは `--no-deps` である。停止で対象から外すのは、サービスを明示した `stop` / `rm -f` である。どれも古くからある形で、版の下限を作らない。下限を決めるのは `depends_on.required` だけである。この属性は 2.20.0 からの機能で、受け入れ条件と文書の構成例が使う。そのため 2.20.0 未満では、構成の検証そのものに失敗する。

| 版 | 扱い |
| --- | --- |
| 2.20.0 以上 | 対応する。手元で確かめたのは v5.1.4 |
| 2.20.0 未満の v2 | 対象外。`required: false` を書いた構成の検証に失敗する |

`depends_on.required: false` を書くのはプロジェクト側であり、devbase の実装には現れない。文書で案内する。ただし dev を対象から外す働きは持たない。その役目は `--no-deps` が担う（決定 2）。

## 決定の記録

### 決定 1: プロファイルの解決は生成済みの `.docker-compose.scale.yml` を読んで行う

`devbase` が Compose へ渡すのはこのファイルである。プロファイルの割り当てもここで確定している。元の `compose.yml` を読むと、生成の過程で加わる差を二重に解釈することになる。その差は機密の列挙と dev の複製である。

要求仕様が `compose.yml` と書く箇所との対応は次のとおりである。利用者がプロファイルを宣言するのは `compose.yml` であり、devbase が読むのはその宣言を引き継いだ生成物である。宣言の内容は同じなので、受け入れ条件の文言は変えない。

`docker compose config --services` を 2 回呼んで差を取る案は採らない。`list` のような読むだけの操作でも Docker デーモンへの接続が要る。変数の展開に失敗すると一覧すら出せない。

### 決定 2: プロファイルの操作はサービス名をすべて明示し、`--no-deps` を付ける

サービス名を省いて `--profile X up -d` とすると、既定のサービスも照合の対象に入る。機密は名前だけを列挙する形で生成物に書かれる。値はプロセスの環境から解決される。**照合の対象に入った時点で dev の構成が変わりうる。** そのため対象を明示する。

**サービス名の明示だけでは足りない。** `_build_scaled_services` は非 dev サービスにも `_rewrite_depends_on` を適用する（`lib/devbase/volume/compose.py:488`）。プロファイルのサービスが `depends_on: dev` を書いていると、生成物では `dev-1`..`dev-N` になる。Compose は依存先を解決して対象へ取り込む。受け入れ条件の「既定のサービスの Container ID と `StartedAt` が変わらない」はこれで崩れる。

**`required: false` はこれを止めない。** この属性が緩めるのは「依存先が不在のときのエラー」だけである。依存先を操作の対象から外す働きは持たない。Compose v5.1.4 の dry-run でも dev の起動が含まれる。依存先の構成が変わったときに再作成する実装のため、機密などの値が変わった状態では Container ID が変わりうる。

そこで `--no-deps` を使う。起動のコマンド列は `--profile X up -d --no-deps <対象サービス...>` になる。この選択肢は依存先を操作の対象から外す。

**`--no-deps` は依存先を自動起動しない。** そのため、プロファイルに属するサービスをすべて明示して渡すことが前提になる（要求仕様の前提 5）。1 つでも落とすと、そのサービスは起動しない。渡す集合は「プロファイルの解決」が生成物から求めるため、取りこぼしは起きない。

| 組み立て方 | `profile up X` が触るもの | 判定 |
| --- | --- | --- |
| `--profile X up -d`（サービス名なし） | 既定のサービスを含む全体 | 不可。dev を再作成しうる |
| `--profile X up -d <一部のサービス>` | 渡したサービスだけ | 不可。残りが起動しない |
| `--profile X up -d <対象サービス...>`（`--no-deps` なし） | そのサービスと依存先の dev-1..N | 不可。dev を再作成しうる |
| `--profile X up -d --no-deps <対象サービス...>` | そのサービスだけ | 可 |

`depends_on` の書き方は前提にしない。`depends_on: {dev: {condition: service_started, required: false}}` でも `depends_on: [dev]` でも、`--no-deps` を付ければ dev は対象に入らない。

生成のときに devbase が `required: false` を補う案は採らない。依存が必須かどうかはプロジェクトが決める意図であり、生成物が黙って緩めると `devbase up` の起動順の意図が読めなくなる。対象から外す役目は `--no-deps` が担うため、補う必要もない。

### 決定 3: プロファイル起動の後は `./deploy` を呼び直す。新しいフックは作らない

`./deploy` は既にインスタンスごとに呼ばれ、プロジェクト側で冪等に書かれている。プロファイルの有無は `DEVBASE_ACTIVE_PROFILES` で分岐できる。フック名を増やす理由がない。

`./post-profile-up` のような新しい名前を足す案は採らない。プロジェクトが持つ約束の数が増える。どちらに書くべきかの判断も各プロジェクトに生まれる。

`hook_env` は `_run_pre_up_hook` も呼んでいる。そのため `./pre-up` にも `DEVBASE_ACTIVE_PROFILES` が渡る。`./pre-up` を呼ぶのは `cmd_up` だけなので、値は常に空である。`profile up` は `./pre-up` を呼ばない。コンテナの起動前に済ませる準備は `up` の役目だからである。要求仕様が `./pre-up` を挙げるのは、この空の値を含めた約束のことである。

| フック | 呼ぶ経路 | `DEVBASE_ACTIVE_PROFILES` |
| --- | --- | --- |
| `./pre-up` | `cmd_up` のみ | 常に空 |
| `./deploy` | `cmd_up` | 空 |
| `./deploy` | `cmd_profile_up` | 起動したプロファイル名 1 つ |

### 決定 4: 停止は全プロファイルを対象にし、`up` は既定の状態へ揃える

`devbase up` は「その構成で開発環境を作り直す」操作である。プロファイルのサービスだけが前の状態のまま残ると、`up` の後の状態が直前の操作に依存する。

`up` の冒頭の停止を dev-1..N だけに絞る案は採らない（利用者の指示、2026-09-16）。テストを続けたい場合は `up` の後にもう一度プロファイルを起動する。

### 決定 5: プロファイルの停止は `down` を使わず、`stop` と `rm -f` の 2 段で行う

`cmd_profile_down` はまず `docker_compose(['--profile', X, 'stop', <サービス...>])` を呼ぶ。続けて `docker_compose(['--profile', X, 'rm', '-f', <サービス...>])` を呼ぶ。`<サービス...>` はプロファイル X の全件である（決定 2）。1 段目が失敗したら 2 段目は呼ばず、1 を返す。

`down <サービス...>` を渡す案は採らない。理由は 2 つある。

| 理由 | 中身 |
| --- | --- |
| 対象が広がらない | `stop` / `rm` のサービス指定は古くから安定しており、依存元をたどって対象を広げない |
| 版の下限を作らない | `down [SERVICES]` の対応版を調べる必要がなくなり、最低対応版の根拠が `depends_on.required` の 2.20.0 だけで閉じる |

**`down <サービス...>` は依存元も削除する。** Compose の対象選択は、指定したサービスの祖先も含める。祖先とは、そのサービスへ `depends_on` を持つ側である。根拠は [v5.1.4 の実装](https://github.com/docker/compose/blob/v5.1.4/pkg/compose/dependencies.go#L104-L129)である。dev が `depends_on: {db: {condition: service_started, required: false}}` を持つ構成では、`down db` が dev も消す。受け入れ条件「既定のサービスの Container ID と `StartedAt` が変わらない」はこれで崩れる。`required: false` はこれを止めない（決定 2 と同じ理由）。

**`down [SERVICES]` は版の下限を左右する。** この位置引数は比較的新しい追加で、宣言している最低対応版 2.20.0 が受け付ける保証が無い。受け付けない版では、`down` がプロジェクト全体（dev を含む）を落とす。`stop` / `rm` へ寄せると、対応版を調べる必要そのものが消える。

**猶予は既定の 10 秒とする。** プロファイルに入るのはデータベースのような状態を持つサービスである。`devbase down` は開発環境ごと畳む操作のため `-t0` で即座に落とす。プロファイルの停止は稼働中の開発環境を残したまま行うため、`stop` に `-t` を付けず、既定の猶予で落とす。

**ボリュームは消さない。** `rm` には `-f` だけを付け、`-v` は付けない。`-v` は匿名ボリュームを消す選択肢である。名前付きボリュームが残ることは受け入れ条件にある。`-f` は削除の確認を省くためだけに要る。

`docker_compose_down` に `services` と `timeout` の引数を足す案は採らない。この関数の呼び出し側は `devbase down` と `up` 冒頭の停止だけであり、どちらも全体を `-t0` で落とす。引数を増やすと、使われない組み合わせが関数の表に残る。変更は `--profile '*'` を足すことに留める（決定 4）。

| 関数 | 用途 | 使う subcommand | 猶予 | サービスの指定 |
| --- | --- | --- | --- | --- |
| `docker_compose_down` | `devbase down` / `up` 冒頭の停止 | `down` | `-t0` | しない（全体） |
| `cmd_profile_down` | プロファイルの停止 | `stop` → `rm -f` | 既定（10 秒） | する |

### 決定 6: プロジェクト名は位置引数で受け、`bin/devbase` は変えない

プロジェクト名を受けるのは `project profile` だけである。既存の `scale` と同じ並び（`[name] <値>`）に揃える。`container profile` は受けない。`container` 群のサブコマンドは現在地のプロジェクトで動く、という既存の規約に従う。`project` でも `login` / `build` が同じ理由で `[name]` を持たない。

`bin/devbase` は変えない。`_PROJECT_NAME_SUBCOMMANDS`（`up down ps logs scale rebuild`）の名前解決は 3 番目の引数だけを見る。`profile` をその一覧へ足すと、`up` / `down` / `list` がプロジェクト名として解決されうる。Python 側の `_dispatch_lifecycle` には名前を解決して移動する経路が既にある。そちらに寄せる。

| 入口 | `[name]` | 名前の解決 |
| --- | --- | --- |
| `devbase project profile up [name] <profile>` | 受ける | `_dispatch_lifecycle`（Python 側） |
| `devbase container profile up <profile>` | 受けない | しない（現在地で動く） |
| `devbase ct profile up <profile>` | 受けない | しない（現在地で動く） |

### 決定 7: 有効なプロファイルは devbase が決め、起動の対象も明示する

`docker_compose` は現在 `subprocess.run(cmd, ...)` を `env=` なしで呼ぶ（`lib/devbase/utils/docker.py:14-55`）。子プロセスは `os.environ` を暗黙に継承する。`COMPOSE_PROFILES=test` が設定された端末では、`devbase up` の `compose up -d` が test のサービスまで起動する。受け入れ条件「`up` の後は既定のサービスだけが動く」はこれで崩れる。v5.1.4 の最小構成で確認済みである。

そこで対策を 2 つ重ねる。**環境変数を子へ渡さないことと、起動の対象をサービス名で明示することである。**

**1. 環境変数を外す。** `docker_compose` で `env=` を新たに構築する。`os.environ` の複製から `COMPOSE_PROFILES` を除き、それを `subprocess.run(..., env=env)` へ渡す。有効なプロファイルは経路ごとに `--profile` で明示する。

**2. 起動の対象を明示する。** 1 だけでは足りない。Compose はプロジェクトの `.env` を自分で読むため、そこに書かれた `COMPOSE_PROFILES` は効いてしまう。そこで `devbase up` の起動は、**生成物から読んだ「`profiles` を持たないサービス」をサービス名としてすべて渡す**。プロファイルが `.env` 経由で有効になっても、対象に入らないサービスは起動しない。

既定のサービスの一覧は `default_services` が `.docker-compose.scale.yml` から作る（決定 1）。`devbase up` は起動の直前に生成物を作り直すため、一覧は常に最新である。

| 経路 | プロファイルの指定 | 対象の渡し方 | `COMPOSE_PROFILES` |
| --- | --- | --- | --- |
| `devbase up` の起動 | 付けない | 既定のサービスをすべて明示する | 外す |
| `devbase down` と `up` 冒頭の停止 | `--profile '*'` | 渡さない（全体が対象） | 外す |
| `profile up X` | `--profile X` | そのプロファイルのサービスをすべて明示し、`--no-deps` を付ける | 外す |
| `profile down X` | `--profile X` | 同じ一覧を `stop` と `rm -f` へ渡す | 外す |

**この方式の前提と限界。** 前提は、生成物が `up` のたびに作り直されることである。限界は、対象を明示するため、生成物に無いサービスは `up` で起動しないことである。生成物には必ず `dev-1`..`dev-N` が入るため、一覧が空になることはない。

**停止には要らない。** 停止は `--profile '*'` で対象を広げる向きの指定である。`.env` が別のプロファイルを有効にしても、対象が狭まることはない。

**`docker compose` を呼ぶ経路の棚卸し。** 現在は 3 か所ある。扱いは次のとおりである。

| 経路 | 場所 | 扱い |
| --- | --- | --- |
| `docker_compose` | `lib/devbase/utils/docker.py:14` | 対象。`up` / `down` / `profile` の各操作はここを通る |
| `_compose_run`（`ps` / `logs`） | `lib/devbase/commands/container.py:269` | 対象。`docker_compose` を経由せず直接 `subprocess.run` するため、同じ `env=` を別に組み立てる |
| `cmd_scale` の直接呼び出し | `lib/devbase/commands/container.py:1282` | 範囲外。要求仕様の「対象範囲・含まない」に挙げる |

`ps` / `logs` はコンテナを起動しない読み取りの操作である。それでも対象に含めるのは、`COMPOSE_PROFILES` が効くと Compose が解釈するサービスの集合が変わり、表示の中身が利用者の環境に左右されるためである。devbase の表示は、devbase が決めたプロファイルに揃える。

**空文字列にはしない。** `COMPOSE_PROFILES=` を渡す形は、版によって「空の一覧」と「未設定」のどちらに解釈されるかを調べる必要が出る。キーごと外せばその判断が要らない。

**`.env` は書き換えない。** `.env` は利用者とプロジェクトの持ち物であり、devbase が値を消すと素の `docker compose` を叩いたときの挙動まで変わる。環境変数の除去と起動の対象の明示なら、影響は devbase 経由の呼び出しだけに閉じる。

| 案 | 効く範囲 | 判定 |
| --- | --- | --- |
| `.env` から `COMPOSE_PROFILES` を消す | 素の `docker compose` にも及ぶ | 不可。利用者の持ち物を変える |
| 環境変数を空文字列にする | devbase 経由のみ | 不可。空の解釈が版に依る |
| 環境変数を外すだけ | devbase 経由のみ。`.env` には効かない | 足りない。単独では受け入れ条件を満たせない |
| 環境変数を外し、起動の対象を既定のサービスへ限る | devbase 経由のみ | 可 |

`--profile` を明示する経路では、環境変数を外しても対象は変わらない。`--profile` と `COMPOSE_PROFILES` は和集合として扱われるため、外して困るのは「環境変数だけでプロファイルを有効にしていた」場合である。devbase はその使い方を約束していない。プロファイルの起動は `profile up` が唯一の入口である。

## テスト設計

| 受け入れ条件 | 何で確かめるか |
| --- | --- |
| `profiles` 付きのサービスは `devbase up` で起動しない | Compose の既定の挙動。生成物が `profiles` を保つことを `tests/volume/test_compose_profiles.py` で検査する |
| `profile up X` で対象サービスだけが起動する | `subprocess.run` を差し替え、組み立てた引数列が `--profile X up -d --no-deps <対象サービス...>` であり、dev を含まないことを検査する |
| プロファイルのサービスをすべて渡す | 同じ引数列に、プロファイル X に属するサービスが全件並ぶことを検査する。1 つでも欠けると `--no-deps` で起動しないため（決定 2） |
| 既定のサービスの Container ID と `StartedAt` が変わらない | 手動確認（`alpine:3` の最小構成で `docker inspect` の値を前後で比較する） |
| `depends_on: dev` を持つプロファイルサービスで dev が再作成されない | 手動確認。`depends_on: {dev: {condition: service_started, required: false}}` の構成と `depends_on: [dev]` の構成の両方で `profile up X` を実行し、dev-1..N の Container ID と `StartedAt` を前後で比べる |
| dev の環境変数の値が変わっても dev が再作成されない | 手動確認。機密など dev の環境変数の値を変えてから `profile up X` を実行し、dev-1..N の Container ID と `StartedAt` を前後で比べる。`--no-deps` が無い組み立てでは再作成が起きることも確かめ、この選択肢が要ることを示す |
| 生成物が `depends_on` の `required` を保つ | `generate_scaled_compose` の出力で、`depends_on: {dev: {condition: service_started, required: false}}` が `dev-1`..`dev-N` へ写り、各要素に `condition` と `required` が残ることを検査する |
| `profile down X` でコンテナが削除され、ボリュームは残る | 2 回の呼び出しが `--profile X stop <対象サービス...>` と `--profile X rm -f <対象サービス...>` であり、`down` も `-v` / `--volumes` も含まないことを検査する。`stop` が失敗したときに `rm` が呼ばれず 1 で終わることも見る |
| dev がプロファイルのサービスへ `depends_on` を持っても dev が止まらない | 手動確認。dev に `depends_on: {db: {condition: service_started, required: false}}` を書いた構成で `profile up X` → `profile down X` を通し、dev-1..N の Container ID と `StartedAt` を停止の前後で比べる。`down db` の組み立てでは dev が消えることも確かめ、`stop` / `rm` へ寄せる必要を示す（決定 5） |
| `profile list` がプロファイル名と稼働状況を出す | 稼働中のサービスを返す偽の `docker compose ps` を与え、出力の行を検査する。全稼働・一部稼働・全停止の 3 通りで `3/3 running` / `1/3 partial` / `0/3 stopped` を確かめる |
| `project profile list` が `project list` へ流れない | `profile_subcommand` の分離を、`devbase project profile list` の解析結果と呼ばれたハンドラで検査する |
| 構成ファイルが無いときに 1 で止まる | 空の一時ディレクトリで呼び、終了コードと、コンテナを作る呼び出しが発生しないことを検査する |
| 未知のプロファイル名で 1 で止まる | 既知の名前が出力に並ぶことと終了コードを検査する |
| `project profile up <名前> X` が同じ結果になる | 引数の解釈（`[name] <profile>` の割り当て）を `tests/cli` の既存の書き方で検査する |
| `devbase down` が全プロファイルを消し、0 で終わる | 引数列に `--profile *` が含まれることを検査する。全体の削除は手動確認 |
| `devbase up` の後は既定のサービスだけが動く | 冒頭の停止が `--profile *` を通ることと、起動の引数列に `--profile` が入らないことを検査する。実際の状態は手動確認 |
| `COMPOSE_PROFILES` が環境変数に設定されていても `devbase up` は既定のサービスだけを起動する | `COMPOSE_PROFILES=test` を `monkeypatch.setenv` で置き、`docker_compose` と `_compose_run` が組み立てた `env` にこのキーが無いことを検査する。`up` の起動・`down`・`profile up` / `down` の各経路で見る。実際の状態は手動確認 |
| `.env` に `COMPOSE_PROFILES` が書かれていても `devbase up` は既定のサービスだけを起動する | 生成物から読んだ既定のサービス名が `up -d` の引数へ並ぶことを検査する。引数にプロファイルのサービスが入らないことも見る。実際の状態は手動確認 |
| フックが `DEVBASE_ACTIVE_PROFILES` を受け取る | `hook_env` の戻り値と、`subprocess.run` へ渡された `env` を検査する。`cmd_up` 経由は空文字列、`cmd_profile_up` 経由はプロファイル名 1 つになることを見る。複数値はこの範囲では作れないため検査しない |
| フックの失敗が終了コードへ出る | 失敗する `./deploy` を置き、戻り値が 1 になることを検査する |
| プロファイルを持たないプロジェクトの挙動が変わらない | 既存の `tests/commands/test_container_up_order.py` と `tests/cli/test_up_roundtrips.py` が通ること |
| プロファイルを持たないプロジェクトで `up` / `down` が従来どおり動く | 手動確認（確認済みの v5.1.4 で実施）。`profiles:` を持たない既存のプロジェクトで `devbase up` と `devbase down` を通し、起動するコンテナの集合と `down` 後に残らないことを確かめる。`--profile '*'` がこの経路に入るため |
| 生成物が `profiles` を保つ | `generate_scaled_compose` の出力を読み、非 dev サービスの `profiles` が残ることを検査する |

テストは実 docker と実 `DEVBASE_ROOT` に触れない。`subprocess.run` を差し替える。作業ディレクトリは `tmp_path` を使う。

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| `--profile '*'` が使える最古の版 | 公式ドキュメント（[profiles](https://docs.docker.com/compose/how-tos/profiles/)）に版の記載が無く、確かめられなかった。最低対応版は `depends_on.required` の 2.20.0 を根拠に定めた |
| 古い Compose での `--profile '*'` | 確認済みは v5.1.4 のみ。2.20.0 以上 5.x 未満は未検証。ワイルドカードを解釈しない版で `*` がリテラルのプロファイル名として扱われるか（対象が現在と同じに留まる想定）は未確認 |
| 既定のサービスがプロファイルのサービスへ `depends_on` を持つ構成 | `.env` でそのプロファイルが有効になっていると、`up -d <既定のサービス...>` が依存先として起動しうる。`up` には `--no-deps` を付けないためである。この組み合わせは未検証である |
| プロファイルが複数同時に有効な場合 | 同時に 2 つ以上を起動する操作は今回作らない。よって `DEVBASE_ACTIVE_PROFILES` は常に単一値で、カンマ区切りは将来の拡張のための予約である。`profile up` を 2 回呼ぶと、2 回目のフックへ渡るのは 2 つ目の名前だけになる |
