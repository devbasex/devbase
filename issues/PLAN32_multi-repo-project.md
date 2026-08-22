# PLAN32: 1 project = 1 container = 複数リポジトリ構成への変更

## 関連リンク

- 元 issue: `issues/i32.md`
- 参考: `docs/plugin-dev/repo-backed-projects.md` (pre-up populate パターン。今回の対象外)
- 参考: `docs/user/environment-variables.md`, `docs/plugin-dev/quickstart.md`

## モード

`architecture` — プロジェクト設定の公開インタフェース (`projects/<name>/env` の `GIT_USER`/`GIT_REPO`) を
**後方互換なしで** `project.yml` へ置き換え、host CLI・entrypoint・editor・plugin リポジトリ 3 本 (136 project)
の複数モジュールにまたがるため。

## 目的と非目的

達成したい状態:

- 1 つの dev コンテナで**複数リポジトリ**を `/work` 配下へ clone し、横断作業できる。
- リポジトリ指定は配列を素直に表現できる **YAML (`projects/<name>/project.yml`)** で行う。
- `CONTAINER_SCALE` / `WORK_DIR` / `DEVBASE_OPEN_EDITOR` といった **devbase 自身の設定**も YAML 側へ集約し、
  `env` は「コンテナへ渡す環境変数」だけを持つ役割に純化する。
- VS Code は複数リポジトリを 1 ウィンドウで開く **multi-root workspace** で開く。

やらないこと:

- **後方互換の維持はしない** (issue 明記)。旧 `GIT_USER`/`GIT_REPO` 経路は削除し、
  移行漏れは黙って動かすのではなく**明示的なエラー**にする。
- `pre-up` populate パターン (`docs/plugin-dev/repo-backed-projects.md`) の再設計。今回は触らない。
- 複数リポジトリ間の依存解決・同時 push などのワークフロー支援。clone と workspace 生成までが範囲。
- `volareinc/nyle-dx` の取り込み (ユーザー判断により後回し)。

## 前提

- 前提 1: 移行対象は把握済み plugin リポジトリ 3 本。`repos/` 配下で `projects/*/env` は **136 件**
  (`volareinc/devbase-ext` 122 / `takemi-ohama/devbase-ext` 8 / `devbasex/devbase-samples` 6)。
  キー分布は `WORK_DIR`/`GIT_USER`/`GIT_REPO`/`DEVBASE_OPEN_EDITOR`/`CONTAINER_SCALE` が全 136 件、
  `ENABLE_SSH` 2 件、`GIT_HOST` 1 件、`AWS_CONFIG_BASE64` 1 件 → 機械変換で移行できる。
- 前提 2: entrypoint は base image 由来の 1 本のみ (`containers/base/entrypoint.sh`)。lfm も
  `COPY --from=devbase-base` で同一ファイルを使う。変更は base 再ビルドで全イメージへ届く。
- 前提 3: `projects/<name>/compose.yml` は `env_file: - env` を持ち、`env` が実在しないと compose が落ちる
  (`_drop_missing_env_files` は機密由来の参照しか落とさない)。→ 移行後も `env` ファイルは残す。
- 前提 4: 題材 (pilot) は `takemi-ohama/devbase-ext` の 2 プロジェクト。
  - `project-trygroup-prd` ← `project-trygroup-prd` + `project-trygroup-prd-customer` (同一 owner `KK-Generation` / 同一イメージ `containers/trygroup`)
  - `uttarov2` ← `uttarov2` (**gitlab.com** / `uttaro_dev`) + `uttarov2-doc` + `uttarov2migration` (**github.com** / `uttaro-dev2`)
  - 後者は **repo ごとに host が違う**ため、per-repo `host` の検証題材になる。統合後のイメージは `containers/php85` に寄せる。

## 受け入れ条件

- [ ] AC1: `project.yml` に 2 件以上の repo を書いたプロジェクトで `devbase up` すると、コンテナ内 `/work/<dir>` に
      **全 repo が clone** され、primary repo の dir に `cd` した状態でログインできる。
      検証: `devbase build --no-cache` → `up` → `devbase login` で `pwd` と `ls /work`。
- [ ] AC2: repo ごとに `host` を変えられる (github.com と gitlab.com の混在)。
      検証: pilot `uttarov2` (gitlab 1 + github 2) の clone 結果。
- [ ] AC3: `branch` 指定があれば clone 後にそのブランチがチェックアウトされる。検証: コンテナ内 `git -C <dir> rev-parse --abbrev-ref HEAD`。
- [ ] AC4: repo が 2 件以上のとき、`devbase up --open` は multi-root workspace (`/work/<project>.code-workspace`) を開き、
      全 repo フォルダが VS Code のエクスプローラに並ぶ。1 件のときは従来どおり `/work/<dir>` フォルダを開く。
      検証: 生成された `.code-workspace` の内容 + 実機の VS Code。
- [ ] AC5: `scale` / `open_editor` を `project.yml` から読む。`devbase scale N` は `project.yml` を書き換える。
      検証: 単体テスト + `devbase scale 2` 実行後の `project.yml` diff。
- [ ] AC6: `project.yml` が無い / スキーマ不正のプロジェクトは、`up` が**明示エラー**で停止し、移行方法を案内する
      (旧 `GIT_USER`/`GIT_REPO` へ暗黙フォールバックしない)。検証: 単体テスト + 未移行プロジェクトでの `up`。
- [ ] AC7: 1 repo の clone に失敗しても他 repo の clone は継続する (fail-soft)。検証: 存在しない repo を含む `project.yml` で `up`。
- [ ] AC8: 把握済み plugin リポジトリ 3 本の **全 136 project** が `project.yml` を持ち、`env` から
      `GIT_*`/`WORK_DIR`/`CONTAINER_SCALE`/`DEVBASE_OPEN_EDITOR` が除かれている。検証: 変換コマンドの `--dry-run` 出力と PR diff。
- [ ] AC9: pilot 2 プロジェクトが統合後の構成 (2 repo / 3 repo) で実際に起動し、旧 5 プロジェクトのうち統合された
      3 つ (`project-trygroup-prd-customer` / `uttarov2-doc` / `uttarov2migration`) のディレクトリが削除されている。
- [ ] AC10: devbase 本体 / plugin repo 各 PR の `/ndf:cross-review` が APPROVE になる (issue の完了条件)。

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| 設定ファイル名 `project.yml` (repos + devbase 設定を集約) | `repos` に加え `scale` / `open_editor` / `work_dir` を持つ | **採用** | issue の「`CONTAINER_SCALE` は yaml が相応しい」に沿う。設定の置き場所が 1 つに決まり「どっちに書くか」の迷いが消える |
| 設定ファイル名 `repos.yml` (repo 定義のみ) | scale 等は `env` に残す | 不採用 | devbase 設定とコンテナ環境変数が `env` に同居したままで、issue の振り分け要求を半分しか満たさない |
| repo リストの transport: **base64 TSV** を `DEVBASE_REPOS` で渡す | `url\tdir\tbranch\tinit` の行を base64 化 | **採用** | entrypoint 側が `base64 -d` + `while read` だけで解釈でき、jq/python への依存を増やさない (lfm など base 非継承イメージでも安全)。base64 なので compose の `$` 展開・改行事故も起きない |
| transport: base64 JSON + jq | entrypoint で `jq` パース | 不採用 | `jq` は base image にはあるが lfm 系の派生イメージで保証できない。TSV で足りる |
| transport: `project.yml` を bind mount して entrypoint で解釈 | コンテナ内で YAML を読む | 不採用 | project ディレクトリは現在マウントしていない。マウント経路の追加とコンテナ内 YAML パーサ依存の 2 つを同時に増やす |
| workspace ファイル: **host で JSON を組み立て base64 で渡し entrypoint が書き出す** | `DEVBASE_WORKSPACE_B64` | **採用** | 生成ロジックを Python 側 (テスト可能) に置ける。entrypoint は `base64 -d > file` の 1 行 |
| workspace ファイル: entrypoint で shell 組み立て | printf で JSON を書く | 不採用 | テストできない場所にエスケープ処理を置くことになる |
| 移行: 変換コマンドを実装して機械適用 | `devbase project migrate-config` | **採用** | 136 件を手で書くのは誤りが混じる。冪等 + `--dry-run` で diff をレビューできる |
| 移行: 手作業 | — | 不採用 | 件数が多く、レビューでの見落としリスクが高い |

## ドメイン用語

| 用語 | 意味 |
| --- | --- |
| project | `projects/<name>/` 1 ディレクトリ = 1 compose プロジェクト = 1 dev コンテナ (群) |
| repo | project が `/work` 配下へ clone する git リポジトリ。今回から**複数** |
| primary repo | ログイン時の `cd` 先、および repo 1 件時にエディタが開くフォルダ。既定は `repos` の先頭 |
| clone プラン | `project.yml` を正規化した内部表現。base64 TSV で `DEVBASE_REPOS` としてコンテナへ渡る |
| plugin repo | project 定義を配布するリポジトリ (`volareinc/devbase-ext` 等)。`projects/*` はここへの symlink |

## 不変条件

- clone プランの `dir` はプロジェクト内で一意 (同じ `/work/<dir>` を 2 repo が奪い合わない)。
- primary repo はちょうど 1 件。
- `DEVBASE_REPOS` に機密を含めない (URL / dir / branch のみ。認証は既存の git 資格情報機構)。
- `project.yml` は人間が編集する正であり、`DEVBASE_REPOS` は内部 wire format。両者の変換は Python 側だけが行う。

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `projects/<name>/env` の `GIT_USER`/`GIT_REPO`/`GIT_HOST`/`WORK_DIR` | 削除 | **破壊的**。`project.yml` へ移行。旧キーが残っていても無視し、`project.yml` 不在なら `up` はエラー |
| `projects/<name>/env` の `CONTAINER_SCALE`/`DEVBASE_OPEN_EDITOR` | `project.yml` の `scale`/`open_editor` へ移動 | **破壊的**。グローバル `.env` の `DEVBASE_OPEN_EDITOR` は既定値として残す |
| `projects/<name>/env` (ファイル自体) | 残す | `ENABLE_SSH` 等のコンテナ環境変数用。空でも compose が参照するため削除しない |
| `containers/base/entrypoint.sh` | 単一 clone → 複数 clone | **base image 再ビルドが必要**。`devbase build --no-cache` |
| `devbase scale N` | 書き込み先が `env` → `project.yml` | コマンド名・引数は不変 |
| plugin repo の project 定義 | 全 136 件へ `project.yml` 追加 | devbase 本体と plugin repo の**同時切り替え (flag day)**。移行漏れは AC6 のエラーで即座に分かる |

## スキーマ (`projects/<name>/project.yml`)

```yaml
version: 1              # 必須。スキーマ版
scale: 1                # 任意 (既定 2)。旧 CONTAINER_SCALE
open_editor: true       # 任意 (未指定ならグローバル .env の DEVBASE_OPEN_EDITOR)
work_dir: /work/carmo   # 任意。既定は primary repo の /work/<dir>

defaults:               # 任意。repos の各要素へ継承させる既定値
  host: github.com      # 既定 github.com
  owner: volareinc

repos:
  - repo: carmo         # 必須
    primary: true       # 任意。未指定なら先頭要素が primary
  - repo: carmo-batch   # host/owner は defaults を継承
  - repo: uttarov2
    host: gitlab.com    # 個別上書き (pilot uttarov2 の実例)
    owner: uttaro_dev
    dir: system         # 任意。/work 配下の clone 先名 (既定 repo 名)
    branch: develop     # 任意。clone 後に checkout
    init: false         # 任意 (既定 true)。clone 後の ./init.sh 実行有無
```

正規化とバリデーション:

- `owner` / `repo` 必須。`host` 既定 `github.com`、`dir` 既定 `repo`、`init` 既定 `true`。
- `dir` 重複はエラー。`primary: true` が 2 件以上はエラー。`repos` が空はエラー。
- 未知キーはエラー (typo を黙って無視しない)。

wire format (`DEVBASE_REPOS`, base64 TSV / 1 行 1 repo, タブ区切り):

```
https://github.com/volareinc/carmo.git<TAB>carmo<TAB>main<TAB>1
https://gitlab.com/uttaro_dev/uttarov2.git<TAB>system<TAB><TAB>0
```

列: `url`, `dir`, `branch` (空可), `init` (`1`/`0`)。primary は別変数 `DEVBASE_PRIMARY_DIR` で渡す
(TSV の列を増やさず、entrypoint の `cd` 先判定を単純に保つ)。

## 修正対象

devbase 本体:

- 新規 `lib/devbase/project/__init__.py`, `lib/devbase/project/config.py` (ローダ・正規化・検証・wire format)
- 新規 `lib/devbase/project/migrate.py` (env → project.yml 変換)
- `lib/devbase/utils/config.py` (`get_container_scale` の取得元)
- `lib/devbase/commands/container.py` (`cmd_up` 配線 / `cmd_scale` の書き込み先 / `_maybe_open_editor`)
- `lib/devbase/volume/compose.py` (dev サービスへ `DEVBASE_REPOS` 等を注入)
- `lib/devbase/editor/opener.py` (`resolve_workdir` / `resolve_workspace` の入力を project 設定へ)
- `lib/devbase/commands/project.py` + `lib/devbase/cli.py` (`project migrate-config` サブコマンド)
- `containers/base/entrypoint.sh` (複数 clone + workspace 書き出し)
- `docs/user/environment-variables.md`, `docs/user/cli-reference/02-project.md`, `docs/user/container-operations.md`,
  `docs/plugin-dev/quickstart.md`, `docs/plugin-dev/compose-yml-guidelines.md`, `docs/developer/architecture.md`, `CHANGELOG.md`
- `tests/project/`, `tests/editor/test_opener.py`, `tests/volume/`, `tests/cli/`

plugin リポジトリ (別 PR):

- `takemi-ohama/devbase-ext`: pilot 統合 2 件 + 残り project の機械移行 (8 project)
- `volareinc/devbase-ext`: 122 project の機械移行
- `devbasex/devbase-samples`: 6 project の機械移行

## タスク分解

各 Task = 1 PR。base branch は `release/PLAN32` (devbase 本体)。

### Task 1: `project.yml` ローダと wire format

- **対象ファイル:** `lib/devbase/project/config.py`, `tests/project/test_config.py`
- **変更内容:** `ProjectConfig` / `RepoSpec` dataclass、YAML 読み込み・`defaults` 継承・正規化・検証、
  `encode_repo_plan()` / `decode_repo_plan()` (base64 TSV)。`project.yml` 不在・不正時は移行手順を含む
  `ConfigError` を送出。この PR では**呼び出し元を差し替えない** (挙動変更なし)。
- **満たす受け入れ条件:** AC6 の一部 (エラー文言)、AC2/AC3 のデータ表現
- **進め方:** テスト駆動。正常系 (defaults 継承 / dir 明示 / host 混在 / primary 指定) と
  異常系 (owner 欠落 / dir 重複 / primary 複数 / 未知キー / repos 空 / ファイル不在) を先に書く。

### Task 2: host 側配線 (`up` / `scale` / editor)

- **対象ファイル:** `lib/devbase/commands/container.py`, `lib/devbase/volume/compose.py`,
  `lib/devbase/utils/config.py`, `lib/devbase/editor/opener.py`, `tests/volume/`, `tests/editor/`, `tests/commands/`
- **変更内容:** `cmd_up` で `project.yml` を読み、生成 compose の dev サービスへ `DEVBASE_REPOS` /
  `DEVBASE_PRIMARY_DIR` / `DEVBASE_WORKSPACE_B64` を注入。`get_container_scale` は `project.yml` の `scale` を、
  `cmd_scale` は `project.yml` を書き換える。`resolve_workdir` は primary repo、repo 2 件以上なら
  `DEVBASE_WORKSPACE` に `/work/<project>.code-workspace` を立てる。旧 `GIT_*`/`WORK_DIR` 参照は削除。
- **満たす受け入れ条件:** AC4 (host 側)、AC5、AC6
- **進め方:** テスト駆動。生成 compose に期待の env が載ること / `scale` 書き換えの冪等性 / 未移行時のエラーを先に書く。

### Task 3: entrypoint の複数 clone と workspace 書き出し

- **対象ファイル:** `containers/base/entrypoint.sh`, `tests/containers/test_entrypoint_repos.py` (bash を直接実行する形)
- **変更内容:** `DEVBASE_REPOS` を `base64 -d` して 1 行ずつ clone → `branch` があれば checkout →
  `init=1` なら `./init.sh` → 最後に `DEVBASE_PRIMARY_DIR` へ `cd`。clone 失敗は warning で継続。
  `DEVBASE_WORKSPACE_B64` があれば `DEVBASE_WORKSPACE` のパスへ書き出す。旧 `GIT_USER`/`GIT_REPO` 分岐は削除。
- **満たす受け入れ条件:** AC1、AC2、AC3、AC7、AC4 (書き出し側)
- **進め方:** shell 関数を抽出し、ホスト側 pytest から `bash -c` で呼ぶ形で先にテストを書く
  (clone は `file://` のローカル bare repo を使う)。**base image 再ビルド必須**を PR body に明記
  ([[entrypoint-change-needs-rebuild]])。

### Task 4: 移行コマンド `devbase project migrate-config`

- **対象ファイル:** `lib/devbase/project/migrate.py`, `lib/devbase/commands/project.py`, `lib/devbase/cli.py`, `tests/project/test_migrate.py`
- **変更内容:** `projects/*/env` (または指定ディレクトリ配下) を走査し、`GIT_USER`/`GIT_REPO`/`GIT_HOST`/
  `WORK_DIR`/`CONTAINER_SCALE`/`DEVBASE_OPEN_EDITOR` から `project.yml` を生成、`env` からは該当キーを除去。
  `--dry-run` で diff 表示、冪等 (再実行しても差分なし)。symlink 先 (plugin repo の実体) を書き換えることを明示。
- **満たす受け入れ条件:** AC8
- **進め方:** テスト駆動。実 env のパターン (GIT_HOST 有無 / ENABLE_SSH 残留 / 既に移行済み) を fixture 化。

### Task 5: ドキュメントと CHANGELOG

- **対象ファイル:** `docs/` 各所, `CHANGELOG.md`, `issues/PLAN32_multi-repo-project.md`
- **変更内容:** `project.yml` スキーマ、複数 repo 構成の手順、移行コマンドの使い方、破壊的変更の告知。
- **満たす受け入れ条件:** AC8 の周辺 (手順の再現性)
- **進め方:** テスト駆動の対象外 (文書)。

### Task 6: plugin repo 移行 — `takemi-ohama/devbase-ext` (pilot 含む)

- **対象ファイル:** `personal/projects/*`, `bplus/projects/*`
- **変更内容:** Task 4 のコマンドで 8 project を移行。加えて pilot 統合:
  `project-trygroup-prd` へ customer repo を追加し `project-trygroup-prd-customer/` を削除。
  `uttarov2` へ doc/migration repo を追加し `uttarov2-doc/` `uttarov2migration/` を削除 (イメージは php85 に統一)。
- **満たす受け入れ条件:** AC1〜AC4, AC9
- **進め方:** 変換 → 手で pilot を統合 → 実機で `build --no-cache` + `up` 検証。

### Task 7: plugin repo 移行 — `volareinc/devbase-ext` (122) / `devbasex/devbase-samples` (6)

- **対象ファイル:** 各 repo の `*/projects/*/env` と新規 `project.yml`
- **変更内容:** Task 4 のコマンドによる機械変換のみ (統合はしない)。
- **満たす受け入れ条件:** AC8
- **進め方:** `--dry-run` の全件 diff をレビュー → 適用 → 代表 project で `up` 確認。

## PR 分割計画

devbase 本体 (`volareinc/devbase` 相当。ここでは `devbase` repo):

| PR # | branch 名 | 対応 Task | 概要 | 依存 | 並行可否 |
|---|---|---|---|---|---|
| 1 | `feature/PLAN32-config-loader` | Task 1 | `project.yml` ローダ・正規化・検証・wire format (純ライブラリ、挙動変更なし) | なし | ○ |
| 2 | `feature/PLAN32-host-wiring` | Task 2 | `up`/`scale`/editor の配線を `project.yml` へ切替、`DEVBASE_REPOS` 注入 | PR1 | × (PR1 merge 後) |
| 3 | `feature/PLAN32-entrypoint` | Task 3 | entrypoint 複数 clone + workspace 書き出し (**base image 再ビルド必須**) | PR1 (wire format の契約のみ) | ○ (PR2 と並行可) |
| 4 | `feature/PLAN32-migrate-cmd` | Task 4 | `devbase project migrate-config` (env → project.yml 変換) | PR1 | ○ |
| 5 | `feature/PLAN32-docs` | Task 5 | docs / CHANGELOG | PR1〜4 | × (最後に統合) |

```
release branch: release/PLAN32
base branch: main
```

plugin リポジトリ (別リポジトリのため release ブランチは使わず単体 PR):

| repo | branch 名 | 対応 Task | 概要 | 依存 |
|---|---|---|---|---|
| `takemi-ohama/devbase-ext` | `feature/PLAN32-project-yml` | Task 6 | 8 project の移行 + pilot 統合 2 件 | 本体 PR1〜4 |
| `volareinc/devbase-ext` | `feature/PLAN32-project-yml` | Task 7 | 122 project の機械移行 | 本体 PR1〜4 |
| `devbasex/devbase-samples` | `feature/PLAN32-project-yml` | Task 7 | 6 project の機械移行 | 本体 PR1〜4 |

plugin repo の PR は本体 release PR の merge 直後に merge する (flag day)。

## 影響範囲

- 全 project の起動経路 (`devbase up` / `list` / TUI)。移行前の project は起動できなくなる (意図した破壊的変更)。
- base image を使う全コンテナ (再ビルドが必要)。
- `devbase scale` / `devbase env init` (エディタ設定の置き場所)。
- ドキュメント全般 (`GIT_USER`/`GIT_REPO` を前提にした記述)。

## リスクと対処

| リスク | 対処 |
| --- | --- |
| devbase 本体と plugin repo の切り替えタイミングのずれで起動不能になる | AC6 の明示エラーで原因が即分かるようにする。plugin repo 側 PR は本体 release PR の merge 直後に merge する。移行コマンドは本体 merge 前でも `--dry-run` で確認可能 |
| entrypoint 変更が `up` では反映されない | [[entrypoint-change-needs-rebuild]]。Task 3 / 結合検証で `devbase build --no-cache` を必須手順として PR body に明記 |
| 136 件の機械変換でキーの取りこぼし (ENABLE_SSH 等) | 変換対象キーを allowlist で限定し、それ以外は `env` に残す。`--dry-run` の全件 diff をレビュー |
| pilot 統合でイメージ差 (php / php85) による退行 | 統合先を php85 に統一し、doc 側のツール要件を統合後に実機確認 |
| gitlab.com repo の clone 認証が github と別経路 | pilot `uttarov2` で実機検証 (AC2)。失敗時は fail-soft (AC7) で他 repo は継続 |
| 生成 compose に repo URL が載る | URL は機密ではない。`DEVBASE_REPOS` に機密を入れない不変条件をレビュー観点に含める |

## 切り戻し手順

- devbase 本体: `release/PLAN32` の revert で旧 entrypoint / 旧 `GIT_*` 経路へ戻る。base image の再ビルドが必要。
- plugin repo: 各 PR の revert で `env` が復元される (`project.yml` は削除)。データ移行は無く、生成物は
  コンテナ内 `/work` の clone だけなので、切り戻し後も再 clone で復旧できる。
- pilot 統合 (project ディレクトリ削除) は revert で復元されるが、統合後に作った `/work` ボリュームは
  `devbase down` + ボリューム削除で作り直す。

## 完了の定義

- [ ] AC1〜AC10 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run pytest` が green
- [ ] devbase 本体 release PR と plugin repo 各 PR の `/ndf:cross-review` が APPROVE
- [ ] `docs/` と `CHANGELOG.md` が新方式のみを説明している (旧方式の記述が残っていない)
