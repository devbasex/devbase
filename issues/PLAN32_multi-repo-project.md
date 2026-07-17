# PLAN32: 1 project = 1 container = 複数リポジトリ構成への変更

> 元 issue: `issues/i32.md`
> 種別: 構成変更 (multi-PR) / base branch: `main` / release branch: `release/PLAN32`

## 1. 背景と目的

現在の devbase は **1 プロジェクト = 1 コンテナ = 1 リポジトリ** を原則とする構成になっている。

- プロジェクトごとの repo 指定は `projects/<name>/env` の `GIT_USER` / `GIT_REPO` (単一ペア) で行う。
- コンテナ起動時、`containers/base/entrypoint.sh` が `https://$GIT_HOST/$GIT_USER/$GIT_REPO.git` を **1 本だけ** `/work` に clone し、`cd` する。
- VS Code は `WORK_DIR=/work/$GIT_REPO` (単一 repo) を開く。

これを **1 プロジェクト = 1 コンテナ = 複数リポジトリ** に拡張したい。あわせて、repo 指定を env の文字列変数で表現するのは配列表現力に限界があるため、**YAML (`projects/<name>/repos.yml`) を新たな正とする**。

### 解決したい課題
1. 1 つの開発コンテナで複数 repo (例: `carmo` 本体 + `carmo-batch` + `carmo-cdk`) を同時にチェックアウトして横断作業したい。
2. repo ごとの host / owner / branch / clone 先ディレクトリ / init 実行有無 を宣言的に、増減しやすい形で管理したい。
3. 既存の単一 repo プロジェクト (env `GIT_USER`/`GIT_REPO` ベース) を壊さず移行できること。

## 2. 現状アーキテクチャ (調査結果)

| レイヤ | ファイル | 役割 | 単一 repo 前提の箇所 |
|---|---|---|---|
| プロジェクト設定 | `projects/<name>/env` | `GIT_USER`/`GIT_REPO`/`WORK_DIR` を定義 | 単一ペアのみ |
| プロジェクト compose | `projects/<name>/compose.yml` | `env_file: [root .env, env, .env]` で dev サービスへ注入 | — |
| 起動フロー (host) | `lib/devbase/commands/container.py` | `_load_project_env` (env 解析+`$VAR`展開), `_run_pre_up_hook` (`./pre-up`), `generate_scaled_compose` | env は単一 repo キーのみ想定 |
| clone (container) | `containers/base/entrypoint.sh:265-289` | `GIT_USER`+`GIT_REPO` を 1 本 clone → `init.sh` → `cd` | **中核**: 単一 clone/cd |
| エディタ起動 | `lib/devbase/editor/opener.py:resolve_workdir` | `WORK_DIR` or `/work/$GIT_REPO` を開く | 単一フォルダのみ |
| scale 生成 | `lib/devbase/volume/compose.py` | dev を N 台へ複製、`/work` を named volume 化 | repo 数と直交 (影響小) |

観測: 認証情報は既に **base64 env blob** (`GCP_CREDENTIALS_BASE64__*` 等) としてコンテナへ渡す実装パターンが確立している。repo リストの transport にも同じ手法が使える。

## 3. 設計方針

### 3.1 YAML スキーマ (新規 `projects/<name>/repos.yml`)

```yaml
# 任意: 各 repo のデフォルト値 (DRY 用)
defaults:
  host: github.com
  owner: volareinc

repos:
  - repo: carmo          # 必須。clone 先 dir 名 (default: repo 名)
    primary: true        # 任意: cd 先 & エディタ既定フォルダ (未指定なら先頭要素)
    branch: main         # 任意: clone 後に checkout
  - repo: carmo-batch    # host/owner は defaults を継承
  - repo: carmo-cdk
    owner: volareinc     # defaults を個別上書き可
    dir: cdk             # 任意: /work 配下の clone 先 dir 名を明示指定
    init: false          # 任意: clone 後の init.sh 実行有無 (default: true)
```

- **正規化ルール**: `host` default `github.com`、`dir` default = `repo`、`init` default `true`、`primary` 未指定なら先頭 repo。
- **バリデーション**: `owner`/`repo` 必須、`dir` 重複禁止、`primary` は最大 1 件。

### 3.2 config → container の transport (推奨: host 正規化 → env blob)

**YAML を人間向けの正とし、コンテナへは正規化済みの「clone プラン」を base64 env blob で渡す**ハイブリッド構成を採用する。

```
[人間] repos.yml (YAML, 表現力)
   │  devbase up  (host / Python)
   ▼
[PR1 loader] parse + validate + 正規化
   │  → clone プラン (JSON) を base64 化
   ▼
DEVBASE_REPOS 環境変数 (compose 経由でコンテナへ)
   │  devbase up → docker compose
   ▼
[PR2 entrypoint] DEVBASE_REPOS を decode → repo ごとに clone/checkout/init → primary へ cd
```

採用理由:
- パース/バリデーションを **テスト可能な Python** 側に集約でき、entrypoint (bash) を単純に保てる。
- コンテナ内に YAML パーサ (yq / pyyaml) を新規依存として持ち込まずに済む (既存の base64 env パターンと一致)。
- env は内部 wire format にすぎず、**人間が触る正は YAML** という issue の要求を満たす。

> 代替案 (不採用): `repos.yml` をコンテナへ bind mount し entrypoint 内で `python -c` パース。/work は named volume でありプロジェクト dir は未マウントのため mount 経路の追加が必要で、transport が複雑化する。将来 in-container で再 clone したいニーズが出た場合に再検討する。

### 3.3 後方互換

- `repos.yml` が **無い**プロジェクトは、従来どおり env の `GIT_USER`/`GIT_REPO` から **単一要素の clone プラン**を loader が合成する。既存 40+ プロジェクトは無変更で動作する。
- `repos.yml` が **有る**場合は env の `GIT_USER`/`GIT_REPO` を無視 (YAML 優先)。両方あるときは warning を出す。
- entrypoint は `DEVBASE_REPOS` があればそれを、無ければ従来の `GIT_USER`/`GIT_REPO` 分岐 (現行コード) をそのまま使う二段構え。→ entrypoint 単体でも後方互換。

### 3.4 エディタ (複数 repo のワークスペース)

- `primary` repo を `resolve_workdir` の既定フォルダにする (単一 repo 時と同じ挙動)。
- 複数 repo 時は `.code-workspace` (multi-root) をコンテナ内 `/work` に生成し、全 repo フォルダを 1 ウィンドウで開けるようにする。`DEVBASE_WORKSPACE` (既存機構, `resolve_workspace`) 経由で開く。

## 4. PR 分割計画

| PR # | branch 名 | 概要 | 依存 | 並行可否 |
|---|---|---|---|---|
| 1 | `feature/PLAN32-config-loader` | `repos.yml` スキーマ定義 + Python loader (`lib/devbase/repos/config.py`): parse・validate・正規化・env 合成フォールバック + 単体テスト。**挙動変更なしの純ライブラリ** | なし | ○ |
| 2 | `feature/PLAN32-up-transport` | `devbase up` で loader を呼び `DEVBASE_REPOS` (base64 clone プラン) をコンテナ環境へ注入。`container.py`/compose 生成への配線 | PR1 | × (PR1 の loader API 確定後) |
| 3 | `feature/PLAN32-entrypoint` | `entrypoint.sh` を複数 repo clone ループへ拡張 (`DEVBASE_REPOS` decode → clone/checkout/init → primary cd)。`GIT_USER`/`GIT_REPO` 後方互換分岐を保持。**要 base image 再ビルド** | PR1 (clone プラン形式の契約のみ) | ○ (PR2 と mock 契約で並行可) |
| 4 | `feature/PLAN32-editor` | 複数 repo の `.code-workspace` 生成 + `resolve_workdir`/opener の primary 対応 + 単体テスト | PR1 | ○ (mock で先行可) |
| 5 | `feature/PLAN32-migrate-docs` | env→`repos.yml` 変換ヘルパ + README/docs 更新 + サンプルプロジェクト (`repos.yml` 例) + CHANGELOG | PR1〜4 | × (最後に統合) |

```
release branch: release/PLAN32
base branch: main
```

依存グラフ: PR1 が全ての土台。PR2/PR3/PR4 は PR1 の **clone プラン JSON 形式の契約**さえ固定すれば並行開発可 (PR3 は entrypoint 側、PR2 は host 側で同じ契約の両端)。PR5 は結合・ドキュメントで最後。

## 5. PR ごとの実装詳細

### PR1: config loader (foundation)
- 新規 `lib/devbase/repos/__init__.py`, `lib/devbase/repos/config.py`。
- API 案:
  - `load_repo_plan(project_dir: Path, environ: Mapping) -> list[RepoSpec]`
    - `repos.yml` があれば YAML を読み、`defaults` 継承 → 正規化 → validate。
    - 無ければ env の `GIT_USER`/`GIT_REPO`/`GIT_HOST` から単一 `RepoSpec` を合成。両方あれば warning。
  - `RepoSpec` = `{host, owner, repo, dir, branch, init, primary}` (dataclass)。
  - `encode_repo_plan(specs) -> str` (JSON→base64) / `decode_repo_plan(str)` は PR2/PR3 の契約テストで共有。
- clone プラン JSON 契約 (PR2 が生成 / PR3 が消費) を **このPRで確定**し docstring に明記:
  ```json
  [{"url":"https://github.com/volareinc/carmo.git","dir":"carmo","branch":"main","init":true,"primary":true}, ...]
  ```
- テスト: 正常系 (defaults 継承 / dir 明示 / primary 指定)、異常系 (owner 欠落 / dir 重複 / primary 複数)、env フォールバック、YAML+env 併存 warning。

### PR2: up transport (host wiring)
- `container.py:cmd_up` (および必要なら `generate_scaled_compose` 前処理) で `load_repo_plan` → `encode_repo_plan` → `DEVBASE_REPOS` を **生成 compose の dev サービス environment もしくは補助 env_file** へ注入。
  - secret 露出回避のため既存方針 (`environment` を除去し `env_file` 優先) と整合させる。base64 blob を書き出す一時 env ファイル方式が安全。
- 単一 repo (env フォールバック) でも同じ `DEVBASE_REPOS` を注入し、経路を一本化。
- テスト: プロジェクト固定で `DEVBASE_REPOS` が期待 JSON を base64 で持つこと。

### PR3: entrypoint 複数 clone (container)
- `entrypoint.sh:265-289` を置換:
  - `DEVBASE_REPOS` があれば base64 decode → 各要素で `git clone <url> <dir>` → `branch` 指定時 `git -C <dir> checkout` → `init: true` なら `(cd <dir> && [ -f init.sh ] && ./init.sh)` → `primary` の dir へ最後に `cd`。
  - `DEVBASE_REPOS` 無し時は現行の `GIT_USER`/`GIT_REPO` 単一 clone を維持 (後方互換)。
  - clone 失敗は現行同様 warning で継続 (fail-soft)。
  - decode/iterate は bash + `base64 -d` + 小さな Python one-liner (base image に uv/python 有) で JSON→行変換。新規 apt 依存を増やさない。
- **base image 再ビルドが必要** ([[entrypoint-change-needs-rebuild]]): 検証は `devbase build --no-cache` 必須。`devbase up` だけでは反映されない点を PR body / テスト手順に明記。

### PR4: editor 複数 repo
- `opener.py`: `resolve_workdir` は primary repo を返す。複数 repo 時は `/work/<name>.code-workspace` (全 repo フォルダを含む multi-root JSON) を生成し `DEVBASE_WORKSPACE` を設定 → `resolve_workspace` 経由で開く。
- 生成タイミング: entrypoint (コンテナ内 `/work` 実体を見て生成) が素直。PR3 の clone 後段に組み込むか、opener 側で attach 時生成するかを PR4 冒頭で確定。
- テスト: single repo→従来フォルダ、multi repo→workspace パス解決。

### PR5: migration + docs
- `env`→`repos.yml` 変換ヘルパ (既存プロジェクトの `GIT_USER`/`GIT_REPO` を読み `repos.yml` を生成、`--dry-run` 付き)。一括移行は任意 (後方互換があるため強制しない)。
- `README.md` / `docs/` に複数 repo 構成の手順・スキーマ・移行方法を追記。
- サンプル: `projects/` にマルチ repo の `repos.yml` 例、または `docs/examples/`。
- `CHANGELOG.md` 追記。

## 6. テスト / 検証計画

### 単体 (各個別 PR)
- PR1: loader の正常/異常/フォールバック (pytest)。
- PR2: `DEVBASE_REPOS` 注入内容の検証。
- PR4: workspace パス解決。

### 結合 (release PR / Step 7 相当)
- [ ] `repos.yml` (2〜3 repo) を持つ検証用プロジェクトで `devbase build --no-cache` → `up` → コンテナ内 `/work` に全 repo が clone され、primary に cd していること。
- [ ] `repos.yml` 内 `branch` 指定が反映されること。
- [ ] 既存 env-only プロジェクト (`GIT_USER`/`GIT_REPO`) が無変更で従来どおり単一 clone されること (後方互換の回帰確認)。
- [ ] `DEVBASE_OPEN_EDITOR=1` で複数 repo が multi-root workspace として開くこと。
- [ ] clone 失敗時 (存在しない repo) に fail-soft で他 repo が継続 clone されること。

## 7. リスク / 留意点

| リスク | 対策 |
|---|---|
| entrypoint 変更が `up` では反映されず古い挙動が残る | [[entrypoint-change-needs-rebuild]]。PR3/結合テストで `build --no-cache` 必須を明記 |
| base64 blob に repo URL 以上の機密は含めない | URL/dir/branch のみ。認証は既存の git credentials 機構を流用 |
| YAML+env 併存時の優先順位の混乱 | YAML 優先 + warning。docs に明記 |
| 既存 40+ プロジェクトへの回帰 | 後方互換フォールバックを PR1 で担保し、結合テストに env-only 回帰を含める |
| `dir` 衝突 / primary 複数 | PR1 の validate で早期 fail |

## 8. 次のアクション

本 plan は **作成フェーズ**の成果物。実装に進む場合は `/ndf:issue-plan-strategy` の実行フェーズ (Step 3〜) に従い:
1. `release/PLAN32` ブランチ + release Draft PR を先行作成。
2. PR1〜5 の個別 Draft PR を作成 (PR1 を最優先で着手)。
3. PR1 完了・merge 後に PR2/PR3/PR4 を worktree 並行開発、PR5 を最後に統合。
