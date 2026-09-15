# #182: 機密ストアの置き場をアカウントグループごとに分ける（設計）

要求と受け入れ条件は `issues/PLAN56_secret-group-paths.md` にある。この文書は「どう作るか」だけを扱う。

作るものは 3 つである。

1. **グループを含むパスの並び**を `backend.yml` の `version: 2` として足す（決定 1）
2. **参照（`SecretRef`）にグループを持たせる**。グループは機密を読む前に非機密の `env`
   ファイルから決める（決定 2・3）
3. **`default` の読み替え**を `backend.yml` の `group_aliases` に置く（決定 4）

`version: 1` の設定とファイル backend では、参照のグループは常に空で、パス・キャッシュ・
往復は今と同じになる（決定 5）。

## 機能一覧

| # | 機能 | 誰が使うか |
| --- | --- | --- |
| F1 | `devbase up` などの注入が、プロジェクトのアカウントグループの置き場だけを読む | 開発者（意識せずに使う） |
| F2 | `env list` / `get` / `set` / `delete` / `edit` が、実行したプロジェクトのグループを相手にする。`--group` で明示もできる | 開発者 |
| F3 | `default` などのグループ名を、置き場の上だけ別の名前へ読み替える | 端末の設定者 |
| F4 | `env backend status` が対象のグループとそのパスを表示する | 開発者 |
| F5 | `env backend use openbao --layout group --group-alias default=nyle` でグループ別の置き場へ切り替える | 端末の設定者 |
| F6 | `env backend migrate --to openbao` がプロジェクトごとのグループへ書き、`--exclude-project` で移行から外す | 端末の設定者 |
| F7 | 手元のキャッシュがグループごとに分かれ、不達のときに自分のグループの控えで起動する | 開発者（意識せずに使う） |
| F8 | 起動するボリュームのグループと機密のグループが食い違えば `up` / `scale` を止める | 開発者（誤設定の検出） |

## 構成要素

| 要素 | 責務 |
| --- | --- |
| `env/groups.py`（新設） | `declared_group(root, project)`: `projects/<project>/env` → `$DEVBASE_ROOT/env` → `default` の順に `DEVBASE_ACCOUNT_GROUP` を**ファイルから**読み、`volume.manager.resolve_account_group` で検証する。プロセスの環境変数と機密の置き場は見ない（決定 3） |
| `env/backend_config.py`（変える） | `version: 2` を受け付ける。`OpenBaoSettings` に `layout`（`flat` / `group`）・`path_team_prefix`・`group_aliases` を足す。`storage_group(group)` が読み替えと予約語の検査を行う。`path_of(ref)` が `layout` でパスを組む。`cache_relpath(ref)` を足す |
| `env/secret_store.py` `SecretRef`（変える） | 末尾に `group: Optional[str] = None` を足す。`for_global` / `for_project` が `group=` を受ける。`label()` はグループがあれば `（グループ <名前>）` を後ろに付ける |
| `env/secret_store.py` `SecretStore`（変える） | `ref_group(project)` を足す。設定が `openbao` かつ `layout: group` のときだけ `declared_group` の結果を返し、それ以外は `None` |
| `env/openbao.py` `OpenBaoBackend`（変える） | `layout: group` で `ref.group` が空の参照を受けたら `SecretStoreError` で止める（呼び出しの誤りを従来のパスへ落とさない） |
| `env/cache.py`（変える） | `entry_path` / `entry_key` を設定の `cache_relpath` から組む。`layout: flat` では今と同じ位置 |
| `env/runtime.py` `resolve()`（変える） | `store.ref_group(project)` で得たグループを 4 参照に渡す |
| `commands/env.py`（変える） | `_global_env` / `_project_env` / `_target_env` がグループを決める。`--group` の検証と、`-p` とプロジェクトのグループの食い違いを拒む。`list` の見出しにグループを出す。`init` / `sync` / `project` / `export` / `import` も同じ決め方を使う |
| `env/sources.py` `SourcesManager`（変える） | 同期済みのハッシュの控えを置き場のグループごとに持つ。`layout: group` では `$DEVBASE_ROOT/.env.sources.<g>.yml`、それ以外は今の `.env.sources.yml`（決定 13） |
| `env/bundle.py` / `env/io_import.py`（変える） | 共通の参照は対象のグループ、プロジェクトの参照はそのプロジェクトのグループで作る。`export` は対象のグループに属するプロジェクトだけを集め、`import` はバンドルに別グループのプロジェクトがあれば 1 件も取り込まずに止める（決定 12） |
| `commands/env_backend.py`（変える） | `status` にグループの行と 4 パス。`use` に `--layout` / `--group-alias`。レイアウトが変わったらキャッシュを消す。`test` は対象のグループに属するプロジェクトだけ調べる。`migrate` に `--exclude-project` と、参照ごとのグループ |
| `commands/container.py` `_ensure_env_files` / `_run_pre_up_checks` / `cmd_scale`（変える） | 存在判定の参照にグループを渡す。子プロセスの `env init`（`cwd` は `$DEVBASE_ROOT`）へ `--group <プロジェクトのグループ>` を渡す（決定 10）。共通の検査 `_check_group_consistency(project)` でボリュームのグループ（`resolve_account_group()`）と `declared_group` を比べ、`layout: group` で食い違えば止める（決定 7）。呼ぶ位置は `_run_pre_up_checks` の冒頭（`_ensure_env_files` より前）と `cmd_scale` の冒頭 |
| `cli.py` `_load_secret_env`（変える） | `layout: group` で、名前を指定したライフサイクル操作（`up <name>` など）の dispatch 前の注入を、実行時のディレクトリではなく指定したプロジェクトで解決する（決定 11） |
| `commands/env_ops.py` `doctor`（変える） | `git check-ignore` で点検するキャッシュのパスを設定の `cache_relpath` から組む。`layout: group` では `.env.sources.<g>.yml` も点検する |
| `.gitignore`（変える） | `.env.sources.yml` の行を `.env.sources*.yml` に広げ、グループごとの控えを追跡対象から外す（決定 13） |
| `cli.py` の引数（変える） | `env list/get/set/delete/edit` と `env init` に `--group NAME`、`env backend use` に `--layout {flat,group}` と `--group-alias FROM=TO`（繰り返し可）、`env backend migrate` に `--exclude-project NAME`（繰り返し可） |
| 文書（変える） | `docs/user/env-backend.md`・`docs/user/environment-variables.md`「アカウントグループ」・`docs/user/cli-reference/03-env.md`。確定仕様 `docs/specifications/secret-backend.md` は `plan-to-spec` で |

**`version` の値の集合へ `2` を足すため、`1` だけを前提にした既存の規則を集めた。** 当てはまらない
規則は次の 7 つで、いずれも上の表に載せた。

| 規則 | 置き場所 |
| --- | --- |
| `version != 1` の拒否 | `backend_config._from_dict` |
| 設定の書き出し | `backend_config.to_dict` |
| 既存設定の引き継ぎ | `env backend use` |
| キャッシュの位置と `index.json` のキー | `cache.entry_path` / `entry_key` |
| 除外を点検するキャッシュのパス | `env doctor` |
| パスの表示 | `env backend status` |
| 参照の列挙 | `env backend test` |

`rekey` と `encrypt` / `decrypt` は当てはまるため変えない。`rekey` は手元の age 暗号文をパスに
よらず集め、`encrypt` / `decrypt` はファイル backend だけを扱う。

構成要素の関係:

```mermaid
graph TD
    subgraph cmd [コマンド]
        ENV[commands/env.py]
        EB[commands/env_backend.py]
        CT[commands/container.py]
        BUN[env/bundle.py / io_import.py]
    end
    subgraph env [env]
        GR[groups.declared_group]
        RT[runtime.resolve]
        ST[SecretStore.ref_group]
        REF[SecretRef.group]
        CFG[backend_config<br/>layout / group_aliases]
        OB[OpenBaoBackend]
        CA[cache]
    end
    VOL[volume.manager<br/>resolve_account_group]
    FILES[(projects/name/env<br/>DEVBASE_ROOT/env)]
    SRV[(OpenBao)]
    ENV --> ST
    EB --> ST
    BUN --> ST
    CT --> RT
    CLI[cli._load_secret_env] --> RT
    CT -->|食い違いの検査| VOL
    CT --> GR
    RT --> ST
    ST --> CFG
    ST --> GR
    GR --> FILES
    GR -->|名前の検証| VOL
    ST --> REF
    OB --> CFG
    OB --> CA
    CA --> CFG
    OB -->|team/group/... だけ| SRV
```

図に含めない要素は次の 3 つである。

| 要素 | 含めない理由 |
| --- | --- |
| `cli.py` の引数 | 引数を定義し、上の各コマンドへ値を渡すだけ |
| `env_ops.py` の `doctor` | `backend_config.cache_relpath` を読むだけ |
| 利用者向け文書 | 実行時の関係を持たない |

## 構造

```mermaid
classDiagram
    class SecretRef {
        +kind: str
        +name: str | None
        +owner: str
        +group: str | None
        +for_global(owner, group) SecretRef
        +for_project(name, owner, group) SecretRef
        +label() str
    }
    class SecretStore {
        +config: BackendConfig
        +ref_group(project) str | None
    }
    class OpenBaoSettings {
        +layout: str
        +path_team_prefix: str
        +path_team_global: str
        +path_team_project_prefix: str
        +path_user_prefix: str
        +group_aliases: dict
        +storage_group(group) str
        +path_of(ref) str
        +cache_relpath(ref) str
    }
    class groups {
        +declared_group(root, project) str
    }
    SecretStore --> OpenBaoSettings : config.openbao
    SecretStore --> groups : layout group のとき
    OpenBaoSettings --> SecretRef : パスを組む
```

`OpenBaoBackend._seen` の鍵は `SecretRef` のままでよい。`layout: group` ではグループが参照の
等価性に入るため、グループの違う同じ種類の参照を取り違えない。`layout: flat` ではグループが
常に `None` で、等価性は今と同じになる。

## データ構造

### `secrets/backend.yml`（`version: 2`）

```yaml
version: 2
backend: openbao
openbao:
  url: https://openbao.example.com
  mount: devbase
  user: member01
  layout: group
  path_team_prefix: team
  path_user_prefix: users
  group_aliases:
    default: nyle
  timeout_seconds: 5
cache:
  enabled: true
```

| キー | `version: 1` | `version: 2` |
| --- | --- | --- |
| `openbao.layout` | 置けない（`flat` として動く） | 必須。`group` だけを受け付ける。不在なら拒む |
| `openbao.path_team_global` / `path_team_project_prefix` | 今と同じ | 置けない（置けば拒む） |
| `openbao.path_team_prefix` | 置けない | チーム単位の親。既定 `team` |
| `openbao.path_user_prefix` | 今と同じ | 今と同じ（既定 `users`） |
| `openbao.group_aliases` | 置けない | グループ名 → 置き場のグループ名の対応。キーも値も `resolve_account_group` の検証を通す。既定は空 |

**版とレイアウトを 1 対 1 にする。** `version: 1` は `flat`、`version: 2` は `group` である。
`version: 2` で `layout` を必須にするのは、読み手が版の番号から並びを思い出さなくて済むためである。
置けないキーを置いたときは、キー名と版を添えて拒む（今の「対応していない値は既定へ
読み替えない」規則に揃える）。

### パスの対応

`<g>` は `storage_group(ref.group)`、すなわち `group_aliases` で読み替えた後の名前である。

| 参照 | `version: 1`（今と同じ） | `version: 2` |
| --- | --- | --- |
| チーム共通 | `team/global` | `<path_team_prefix>/<g>/global` |
| チームのプロジェクト | `team/projects/<name>` | `<path_team_prefix>/<g>/projects/<name>` |
| 個人共通 | `users/<user>/global` | `<path_user_prefix>/<user>/<g>/global` |
| 個人のプロジェクト | `users/<user>/projects/<name>` | `<path_user_prefix>/<user>/<g>/projects/<name>` |

**置き場のグループ名に `global` と `projects` を使えない。** `team/projects/global`
（`version: 1` のプロジェクト `global`）と `team/projects/<name>` の並びに、`version: 2` の
パスが重なるためである。読み替えの後の名前で検査する。

### キャッシュ

| 参照 | `version: 1`（今と同じ） | `version: 2` |
| --- | --- | --- |
| チーム共通 | `cache/team/global.env.age` | `cache/team/<g>/global.env.age` |
| チームのプロジェクト | `cache/team/projects/<name>.env.age` | `cache/team/<g>/projects/<name>.env.age` |
| 個人共通 | `cache/user/global.env.age` | `cache/user/<g>/global.env.age` |
| 個人のプロジェクト | `cache/user/projects/<name>.env.age` | `cache/user/<g>/projects/<name>.env.age` |
| `index.json` のキー（チーム共通 / チームのプロジェクト / 個人共通 / 個人のプロジェクト） | `team:global` / `team:project:<name>` / `user:global` / `user:project:<name>` | `team:<g>:global` / `team:<g>:project:<name>` / `user:<g>:global` / `user:<g>:project:<name>` |

控えの中身と `scope`（URL・`mount`・パス・`role_id` の SHA-256）は変えない。パスが変わる
ため、`version: 1` の控えを `version: 2` の参照に使うことは `scope` の不一致で起きない。
それでも `use` でレイアウトが変わったら `cache/` を消す（別グループの機密が暗号文のまま
残り続けるのを避ける）。

## 入出力の契約

### `env list` / `get` / `set` / `delete` / `edit`

```text
devbase env {set|delete|edit} [-p] [--user] [--group NAME] ...
devbase env list [-g|-p] [--user] [--group NAME] ...
devbase env get [--user] [--group NAME] KEY
devbase env init [--reset] [--group NAME]
```

`set` / `delete` / `edit` の `-p` は宛先を、`list` の `-p` はプロジェクトの節だけを出すことを
指す（今と同じ）。`get` は `-p` を取らず、プロジェクトの参照を実行時のディレクトリから
自動で含める。以下の「`-p` あり」は、`list -p` を含めた 4 コマンドに当たる。

| 状況 | 対象のグループ | 結果 |
| --- | --- | --- |
| `version: 2`、`--group` なし | `declared_group(root, 実行時のプロジェクト)` | そのグループの参照 |
| `version: 2`、`--group NAME`、`-p` なし | `NAME` | 共通の参照だけ `NAME` のもの |
| `version: 2`、`--group NAME`、`-p` あり、プロジェクトと同じ置き場 | `NAME` | そのグループのプロジェクトの参照 |
| `version: 2`、`--group NAME`、`-p` あり、プロジェクトと違う置き場 | — | 両方の名前を述べて 1。読み書きしない（決定 6） |
| `version: 2`、`-p` なしの `list` と `get` で `--group NAME`、プロジェクトと違う置き場 | `NAME` | 共通の参照だけを出す・探す。プロジェクトの参照は含めず、含めなかった旨を標準エラーへ 1 行出す |
| `--group` の名前が使えない | — | `resolve_account_group` と同じ理由（予約語・数字だけ・文字種）、または読み替えた後の名前が `global` / `projects` であることを述べて 2 |
| `version: 1` またはファイル backend で `--group` | — | 「グループ別の置き場を選んだ設定でだけ使える」旨を述べて 2 |

- **「同じ置き場」は読み替えた後の名前（`storage_group`）で比べる。** `default: nyle` の対応が
  あれば、宣言の無いプロジェクトで `-p --group nyle` も `-p --group default` も通る。
  誤りの文言には読み替える前と後の両方を出す（`default → nyle`）
- `list` の各節の見出しは `グローバル（グループ with）` の形になる（`SecretRef.label()`）。
  `version: 1` では今と同じ
- 終了コード 2 は、今の `env` コマンドの引数の誤りと同じ扱いである

### `env backend use`

```text
devbase env backend use openbao [--layout {flat,group}] [--group-alias FROM=TO]... [既存の項目]
```

| 入力 | 結果 |
| --- | --- |
| `--layout` なし・既存の設定あり | 既存の版とレイアウトを引き継ぐ |
| `--layout` なし・既存の設定なし | `group`（`version: 2`）で書く |
| `--layout group` | `version: 2` で書く。`path_team_global` / `path_team_project_prefix` は捨てる |
| `--layout flat` | `version: 1` で書く。既存の `group_aliases` があれば捨てた旨を出す |
| `--group-alias` と、`--layout flat` または（`--layout` なしで）既存の設定が `version: 1` | 組み合わせの誤りとして 2。設定を書き換えない（`version: 1` の `--group` を 2 で拒むのと揃える） |
| `--group-alias default=nyle` | `group_aliases` を**この指定で置き換える**（1 つも無ければ既存を引き継ぐ）。`FROM` と `TO` は `resolve_account_group` の検証を通す。`TO` が `global` / `projects` のときも拒む（`FROM` は拒まない。`global` という名前のグループを別の置き場へ向ける対応は成り立つ）。通らなければ 2、設定を書き換えない |
| レイアウトが変わった | 設定を書いた後に `cache/` を消し、消した旨を出す |

### `env backend status`

`version: 2` では、今の出力の「置き場」の前に次の行を足す。4 つのパスは対象のグループで
組んだ実際のパスを出す。

```text
  レイアウト: group (version 2)
  グループ:   default → nyle (projects/api/env にも $DEVBASE_ROOT/env にも宣言なし)
```

グループの行の括弧には、どのファイルで決まったかを出す。

### `env backend migrate`

```text
devbase env backend migrate --to {openbao,age} [--exclude-project NAME]... [--dry-run] [--yes]
```

- 共通の参照は `declared_group(root, None)` で作る（`$DEVBASE_ROOT/env` → `default`）
- プロジェクトの参照は `declared_group(root, name)` で作る。どちらも実行時のディレクトリに
  左右されない
- `--exclude-project NAME` の参照は、読まない・書かない・退避しない。存在しないプロジェクト名は
  名前を述べて 2（打ち間違いで移行してしまうのを防ぐ）
- `--dry-run` は参照ごとに `<mount>/<パス>` と衝突したキー名を出す。値は出さない
- `--to age` は**移行元と移行先で参照を分けて作る**。移行元（`layout: group` の OpenBao）の参照は
  上の 2 行と同じ規則でグループを持ち、移行先（age）の参照はグループを持たない（ファイル backend は
  分けない）。移行元の参照をグループなしで作ると、`OpenBaoBackend` が空のグループを拒んで読めない
- `--to age` で移せる共通の参照は 1 つ（`secrets/global.env.age`）だけである。`$DEVBASE_ROOT/env` の
  グループの共通の参照を移し、他のグループの共通の参照は移さない。プロジェクトの参照は、
  それぞれのプロジェクトのグループから移す
- 「他のグループ」は、移す対象のプロジェクト（`--exclude-project` で外したものを除く）の
  `declared_group` のうち、`$DEVBASE_ROOT/env` のグループと違う置き場のものである。**その共通の
  参照へは要求を出さない。** 組み立てたパスを表示し、サーバ上に残す旨を述べるだけにする
  （今の `--to age` がサーバ側を消さないのと同じ扱い）
- `migrate --to openbao` は全プロジェクトのグループへ書く。グループ単位のポリシーで書けない
  グループのプロジェクトは `--exclude-project` で外す（外さなければ今と同じく「書き込み権限が
  無い」で止まる）

### `devbase up` / `scale` の食い違いの検査

`layout: group` のときだけ、**副作用のある処理より前に**行う。

| コマンド | 検査の位置 | その後にある副作用 |
| --- | --- | --- |
| `up` | `_run_pre_up_checks` の冒頭（`_ensure_env_files` より前） | 子プロセスの `env init`、`pre-up` フック、自動スナップショット、ボリュームの作成、機密の注入、構成の生成 |
| `scale` | `cmd_scale` の冒頭（`project_runtime.write_scale` の前） | `project.local.yml` の `scale` の書き換え、ボリュームの作成、構成の生成 |

途中で止めると、別グループの名前のボリュームや書き換えた `scale` が残るためである。

| 比べるもの | 食い違ったとき |
| --- | --- |
| ボリュームのグループ `resolve_account_group()`（プロセスの環境変数） ↔ `declared_group(root, project)`（ファイル） | 両方の値と、それぞれの出所を述べて 1。コンテナを起動しない |

## 処理の流れ

`devbase up web` を `projects/api`（グループ宣言なし → `default` → `nyle`）の中から打ち、
`web` のグループが `with` のとき:

```mermaid
sequenceDiagram
    participant CLI as cli
    participant RT as runtime
    participant ST as SecretStore
    participant GR as groups
    participant OB as OpenBaoBackend
    participant SRV as OpenBao
    participant DL as _dispatch_lifecycle
    participant PRE as _run_pre_up_checks
    participant DEP as _run_deploy_pipeline
    CLI->>RT: inject(root, "web")（指定した名前で解決。決定 11）
    RT->>ST: ref_group("web")
    ST->>GR: declared_group(root, "web")
    GR-->>ST: with
    RT->>OB: load 4 参照（group=with）
    OB->>SRV: login + GET team/with/global ほか 3
    CLI->>DL: up web（chdir と env の載せ直し）
    DL->>RT: clear_injected → inject(root, "web")（控えから返る）
    DL->>PRE: 起動前の検査
    PRE->>GR: 冒頭で declared_group(root, "web") と resolve_account_group() を比べる
    PRE->>PRE: _ensure_env_files（食い違わなかったときだけ）
    DL->>DEP: スナップショットの後に起動
    DEP->>RT: inject(root, "web")（控えから返る）
```

往復は認証 1 回・取得 4 回になり、`api` のグループ（`nyle`）のパスへは要求しない。
`version: 1` では dispatch 前の注入を今どおり実行時のディレクトリで解決する（PLAN55 の
往復の表のまま）。ラッパー経由の `devbase up web` は、Python の前に `projects/web` へ移るため、
どちらの版でも最初から `web` で解決する。

`env set -p FOO=1` を `projects/web/src` で打ったとき:

```mermaid
graph TD
    A[env set -p FOO=1] --> B[current_project_name → web]
    B --> C{--group はあるか}
    C -->|なし| D[declared_group root web → with]
    C -->|あり| E{プロジェクトのグループと同じか}
    E -->|違う| X[両方の名前を述べて 1]
    E -->|同じ| D
    D --> F[SecretRef.for_project web group=with]
    F --> G[fetch → 版を指定して save]
    G --> H[team/with/projects/web]
```

## 非機能の実現方式

| 大項目 | 実現方式 |
| --- | --- |
| 性能・拡張性 | グループの決定はファイル 2 つを読むだけで、サーバへ往復しない。プロジェクト内の `up` は認証 1 回 + 取得 4 回のまま |
| 移行性 | `version: 1` の設定はパス・キャッシュの位置・参照の等価性が変わらない。`version: 2` を古い devbase が読むと、今の `version` の検査で拒まれ、従来のパスを読まない |
| セキュリティ | 参照はグループを持って作られ、1 回の操作が組むパスは対象のグループのものだけになる。`layout: group` でグループの無い参照を受けた `OpenBaoBackend` は止まる。ボリュームと機密のグループの食い違いで起動しない |
| 運用・保守性 | 読み替えは `group_aliases` の 1 か所。`status` がグループと出所とパスを出す |

## 決定の記録

### 決定 1: グループ別の置き場は `backend.yml` の `version: 2` にする

古い devbase は未知のキーを黙って無視する（`_openbao_from_dict`）。キーを足すだけでは、
配布が行き渡る前の端末が `version: 2` 相当の設定を読んで `team/global` を読み続け、別グループの
機密をコンテナへ渡す。版を上げれば、今の「`version` が 1 以外なら拒む」規則でその端末が止まる。

パスの設定に `{group}` の差し込みを許す案は採らない。個人単位のパスは `<prefix>/<user>/…` を
コードが組んでおり、差し込みの位置を設定で表すにはキーを増やすことになる。古い devbase が
`{group}` を文字どおりのパスとして読む問題も残る。

### 決定 2: グループは `SecretRef` のフィールドとして持つ

参照が自分のグループを持てば、`_seen` の鍵・キャッシュの位置・`label()` がすべて参照から
決まる。`up web` を `api` から打ったときのように、1 つの `SecretStore` の中でグループが
変わっても取り違えない。

グループを `SecretStore` のインスタンスに持たせる案は採らない。PLAN55 でストアはライフサイクル
操作 1 回の間持ち回られ、プロジェクトの切替をまたぐ。インスタンスのグループを書き換えると、
控えの鍵がグループを含まず、切替元の値が返る。

### 決定 3: グループは非機密の `env` ファイルだけから決め、プロセスの環境変数を見ない

ラッパーは実行時のディレクトリの `env` だけを読む。プロジェクトの下位ディレクトリから
打つと、プロジェクトの `env` がプロセスに載らない。`projects/<name>/env` を直接読めば、
下位ディレクトリからでも同じグループになる（受け入れ条件 3）。置き場から読んだ値も
使わないため、置き場を決める値をその置き場から読む循環も起きない（受け入れ条件 4）。

グループを宣言していないプロジェクト（`$DEVBASE_ROOT/env` にも宣言なし）でシェルから
`DEVBASE_ACCOUNT_GROUP=kkg devbase up` と打つと、ラッパーが source する `env` に同じキーが無い
ため環境変数が残り、ボリュームは `kkg`、機密は `default` になり食い違う。プロジェクトの `env` が
宣言していれば、ラッパーの source が環境変数を上書きするので食い違わない。この食い違いは
決定 7 で起動を止めて知らせる。

### 決定 4: `default` の読み替えは `group_aliases` で置き場の上だけ行う

`DEVBASE_ACCOUNT_GROUP` の既定値を `nyle` に変えると、ボリューム名が `devbase_home_default`
から変わり、既存の認証と会話ログのボリュームを移すことになる。devbase は公開リポジトリで、
コードに社名を既定値として持ち込むことにもなる。読み替えを端末の設定に置けば、ボリュームに
触らず、社名はその会社の端末の設定にだけ入る。

### 決定 5: `version: 1` とファイル backend では参照のグループを常に空にする

`ref_group()` が `None` を返せば、参照は今と同じ値になる。既存のテストの期待値・キャッシュの
位置・往復の数（受け入れ条件 9・10）が、分岐を足さずにそのまま保たれる。

グループを常に参照へ入れ、`version: 1` のパスの組み立てで無視する案は採らない。
`_seen` の鍵にグループが入り、グループの違うプロジェクトへ切り替えたときに同じパスを
2 度取りに行く。

### 決定 6: `-p` と違うグループの `--group` は拒む

`team/kkg/projects/web` に書いても、`web` のグループが `with` なら `up` はそこを読まない。
書けたように見えて使われない機密が残る。プロジェクトのグループを変えたいなら
`projects/web/env` を直すのが筋で、それを文言で案内する。

### 決定 7: `layout: group` の `up` と `scale` はボリュームと機密のグループの食い違いで止める

ボリュームは `resolve_account_group()`（プロセスの環境変数）、機密は `declared_group()`
（ファイル）で決まり、経路が 2 つある。食い違ったまま起動すると、`with` のボリュームの認証で
`nyle` の機密を使うコンテナができ、この変更で防ぎたい混ざり方がそのまま起きる。

ボリュームの側を `declared_group()` へ揃える案は採らない。スナップショット・`status`・
entrypoint へ渡す値まで経路が変わり、この変更の範囲（前提 2）を超える。`version: 1` では
検査しない（今の起動を止めない）。`scale` は `_run_deploy_pipeline` を通らずにコンテナを
足すため、検査を共通の関数にして両方から呼ぶ。

### 決定 8: `env backend test` は対象のグループに属するプロジェクトだけを調べる

今の `test` は `projects/` の全プロジェクトの参照を取りに行く。グループ単位のポリシーの
サーバでは、別グループのプロジェクトで 403 になり、正しい設定でも失敗に見える。対象の
グループ（実行時のプロジェクト、無ければ `$DEVBASE_ROOT/env`）に属するプロジェクトに絞る。

### 決定 9: 置き場を移し直すコマンドは作らない

移し直しはグループ別の置き場へ切り替える端末ごとに 1 回で、チームのパスは 1 人が移せば
済む。`version: 1` の設定で `env get` / `bao kv get` で読み、`version: 2` の設定で
`env edit --group` で書けば足りる。版の履歴を消す操作（`kv metadata delete`）は権限が
管理者側にあり（carmo-cdk#340）、devbase のコマンドに入れても利用者の端末からは実行できない。

### 決定 10: `_ensure_env_files` は子プロセスの `env init` へ `--group` を渡す

子プロセスは `cwd=$DEVBASE_ROOT` で起動する（`commands/container.py` の `_ensure_env_files`）。
実行時のプロジェクトが無いため、共通の参照は `$DEVBASE_ROOT/env` のグループになる。`with` の
プロジェクトの `up` で `team/with/global` が空だと、子プロセスは `team/nyle/global` へ書き、
親は読み直しても空のまま起動する。`--group` でプロジェクトのグループを渡せば、書く先と読む先が
揃う。

子プロセスの `cwd` をプロジェクトのディレクトリへ変える案は採らない。`env init` の収集器が
`cwd` に依存しないことを確かめる範囲が広がり、`version: 1` の端末の挙動まで変わりうる。
`layout: group` でないときは `--group` を渡さない。

### 決定 11: 名前を指定したライフサイクル操作は、dispatch 前の注入から切替先で解決する

`cli._load_secret_env` は dispatch の前に実行時のディレクトリのプロジェクトで注入する。
`projects/api` から Python を直接起動した `up web`（TUI や `python -m devbase.cli`）では、
切替元 `api` のグループのパスへ要求し、別グループの機密をいったんホストのプロセスへ載せる。
`layout: group` で指定した名前が `projects/` に実在するときは、その名前で解決する。

`version: 1` には広げない。PLAN55 の往復の表とテストの期待値（受け入れ条件 9）が変わる。
`version: 1` ではパスがグループで分かれず、取得するのは同じチームの置き場である。

### 決定 12: `export` は対象のグループのプロジェクトだけを集め、`import` は別グループのプロジェクトで止める

今の `export` は既定で全プロジェクト、`import` はバンドル内の全プロジェクトを扱う。
グループ単位のポリシーのサーバでは、別グループのパスで 403 になる。`export` は
`declared_group` が対象のグループと同じ置き場のプロジェクトだけを集め、外したプロジェクト名を
標準エラーへ出す。

`import` で別グループのプロジェクトを黙って飛ばすと、取り込んだつもりの機密が欠ける。
名前とグループを挙げて 1 件も取り込まずに 1 で終了し、既存の `--exclude-project NAME`（繰り返し可）での
除外を案内する。

### 決定 13: `env sync` の同期済みハッシュは置き場のグループごとに持つ

`SourcesManager` は `$DEVBASE_ROOT/.env.sources.yml` 1 つに、ソースファイルのハッシュを記録する。
`env sync` の宛先だけをグループ別にすると、グループ A で同期した時点でハッシュが更新され、
グループ B の同期は「変更なし」と判定される。B の置き場には古い認証情報が残る。
`layout: group` では控えのファイル名に置き場のグループ名を入れ、`env sync` / `export` / `import` の
`--merge-metadata` が対象のグループの控えを読み書きする。控えは認証情報のソースの位置とハッシュを
持つため、今の `.env.sources.yml` と同じく Git の追跡から外す。`.gitignore` を `.env.sources*.yml` へ
広げ、`env doctor` の除外の点検に加える。

控えを 1 つのファイルの中でグループごとの節に分ける案は採らない。`version: 1` の端末と
同じファイルの形が変わり、古い devbase が読むと節を知らずに全体を書き戻す。

## テスト設計

| 受け入れ条件 | 何で確かめるか |
| --- | --- |
| 1 | `tests/cli/test_up_roundtrips.py` に `version: 2` の場合を足し、偽サーバの要求のパスの一覧が 4 つ（`team/with/…` / `users/<user>/with/…`）だけで、認証が 1 回であること |
| 2 | 同上。宣言なし + `group_aliases: {default: nyle}` で `team/nyle/…`。生成される構成のボリュームが `devbase_home_default` |
| 3 | `tests/commands/test_env_user_axis.py` に、`projects/web/src` を実行時のディレクトリにした `set` / `set -p` / `set --user` の書き込み先 |
| 4 | `tests/env/test_groups.py`（新設）で、置き場に `DEVBASE_ACCOUNT_GROUP` があってもファイルの値を返すこと。`declared_group` はストアを受け取らない（シグネチャで固定） |
| 5 | `tests/commands/test_env_user_axis.py` に `$DEVBASE_ROOT` での `--group kkg` の 5 コマンドの宛先と `list` の見出し |
| 5a | 同上。`projects/web`（`with`）での `set -p --group kkg` が 1 で要求 0 回、`get --group kkg` がプロジェクトの参照を探さないこと。宣言の無いプロジェクトで `set -p --group nyle`（`default: nyle`）が通ること |
| 6 | 同上。使えない名前 4 つで終了コード 2 と偽サーバへの要求 0 回。`version: 1` とファイル backend での `--group` の拒否 |
| 7 | `tests/cli/test_up_roundtrips.py` の切替の場合に、グループの違う 2 プロジェクト。偽サーバへの要求に `team/nyle/…` / `users/<user>/nyle/…` が無く、認証 1 回・取得 4 回。`api` 固有のキーが残らない |
| 8 | `tests/env/test_cache.py` に、`nyle` と `with` の控えが別ファイルに置かれ、不達で各グループの控えが使われること |
| 9 | 既存の `tests/env/` / `tests/commands/` / `tests/cli/` が期待値を変えずに通ること。`tests/env/test_backend_config.py` に `version: 1` のパスの対応を固定する表を足す |
| 10 | 既存のファイル backend のテストが変更なしで通ること |
| 11 | `tests/commands/test_env_backend.py` に `status` のレイアウト・グループ・出所・4 パスの行 |
| 12 | `tests/commands/test_env_backend_migrate.py` に、グループの違う 2 プロジェクトと `--exclude-project` の場合、存在しない名前の 2、`--dry-run` がパスとキー名だけを出すこと。`version: 2` の OpenBao から `--to age` へ戻す場合に、`$DEVBASE_ROOT/env` のグループの共通の参照とプロジェクトごとのグループの参照が age へ移り、他のグループの共通の参照は移さずに名前とパスが表示され、そのパスへの要求が 0 回であること |
| 13 | `tests/cli/test_env_bundle_backend.py` に、`nyle` と `with` のプロジェクトがある `version: 2` で、`export` が要求するパスの一覧が対象のグループだけであること、`import` が別グループのプロジェクトを含むバンドルで 1 かつ要求 0 回であること。`env init` / `sync` / `project` の書き込み先 |
| 決定 13 | `tests/commands/` の `env sync` のテストに、`layout: group` で `nyle` と `with` を順に同期し、ソースファイルを更新した後の 2 回目もそれぞれの置き場へ書かれること。`version: 1` では `.env.sources.yml` を使うこと。`tests/commands/test_env_ops_backend.py` に、`doctor` が `.env.sources.<g>.yml` の除外を点検すること |
| 14 | 追加した出力を検査するテストで、偽サーバに置いた値と `secret_id` が標準出力・標準エラー・ログに現れないこと |
| 15 | `uv run pytest tests/`、`ruff check lib`、`python -m compileall -q lib bin` |
| 16 | `tests/commands/test_container_up_order.py` に、`layout: group` でボリュームとファイルのグループが違うと `up` と `scale` が 1 で終わり、スナップショット・ボリュームの作成・`project.local.yml` の書き換えが起きていないこと。`version: 1` では止めないこと |
| 17 | `tests/commands/test_env_backend.py` に、`nyle` と `with` のプロジェクトがある `projects/` で `test` を打ち、偽サーバへの要求が対象のグループのパスだけであること |
| 18 | `tests/cli/test_up_roundtrips.py` の `env init` を走らせる場合を `version: 2` と `with` のプロジェクトで行い、子プロセスの引数に `--group with` があり、書いた値でその `up` が起動すること |
| 決定 1 | `tests/env/test_backend_config.py` に、`version: 2` で `layout` が無いときと `path_team_global` を置いたときの拒否、`version: 1` で `group_aliases` を置いたときの拒否、読み替えた後の `global` / `projects` の拒否。`use --group-alias default=global` の 2 |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 実サーバでの `version: 2` のパスの読み書き | 今のポリシー（`team/*` の読み取り、`users/<entity>/*` の読み書き）がグループ別のパスを含むことを、リリース後テストで確かめる（前提 7） |
| 移し直しで古いパスの版の履歴を消す権限 | チーム単位のパスの `kv metadata delete` は、管理者も今は実行できない（carmo-cdk#340）。移し直しの `operation` の計画で扱う |
| ボリュームのグループがプロジェクトの下位ディレクトリで食い違う既存の挙動 | ラッパーが下位ディレクトリでプロジェクトの `env` を読まないため、今もボリュームのグループが共通の値になりうる。決定 7 の検査がこの場合も止めるかは、`up` を下位ディレクトリから打てるかに依存する。実装で確かめ、範囲外なら起票する |
