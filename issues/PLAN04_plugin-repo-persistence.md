# PLAN04: plugins の .git 保持 — repos/ 永続クローン + シンボリックリンク化

## 背景と目的

現在 `devbase plugin install` は対象プラグインを `plugins/` ディレクトリにファイルコピーしている。
この方式では `.git` 情報が失われるため、プラグインの修正・commit・push が非常にやりづらい。

**目標**: `repos/` ディレクトリに git clone を永続保持し、`projects/` からシンボリックリンクで参照する構造に変更する。
これにより repos/ ベースのプラグインは `plugins/` を経由せず直接参照され、プラグイン開発は `repos/` 内で直接 git 操作が可能になる (`plugins/` は `--link` インストール専用として存続)。

## 現状アーキテクチャ

```
devbase/
├── plugins.yml              # レジストリ (installed_plugins + repositories)
├── plugins/                 # ファイルコピー (.git なし)
│   ├── adminer/
│   │   ├── plugin.yml
│   │   └── projects/adminer/
│   ├── carmo-web/
│   └── ...
└── projects/                # シンボリックリンク → plugins/*/projects/*
    ├── adminer → ../plugins/adminer/projects/adminer
    └── ...
```

**フロー (現状)**:
1. `repo add` → temp clone → `registry.yml` 読み取り → 破棄 → `plugins.yml` にメタデータ保存
2. `plugin install` → temp clone → `copy_plugin()` で `plugins/<name>/` へコピー
3. `sync_projects()` → `projects/<proj>` → `../plugins/<plugin>/projects/<proj>` シンボリックリンク作成
4. `plugin update` → temp clone → `_sync_dir()` で差分同期 (ユーザ編集保持)

## 新アーキテクチャ

```
devbase/
├── plugins.yml              # レジストリ (スキーマ拡張: repos にローカルパス追加)
├── repos/                   # git clone 永続保持 (.git あり)
│   ├── github.com--devbasex--devbase-samples/  # ← github.com/devbasex/devbase-samples.git
│   │   ├── .git/
│   │   ├── registry.yml
│   │   ├── adminer/
│   │   │   ├── plugin.yml
│   │   │   └── projects/adminer/
│   │   └── ...
│   ├── github.com--volareinc--devbase-ext/     # ← github.com/volareinc/devbase-ext.git
│   └── github.com--takemi-ohama--devbase-ext/  # ← github.com/takemi-ohama/devbase-ext.git
├── plugins/                 # --link 専用 (repos/ ベース install では不使用。--link が 0 件なら削除)
└── projects/                # repos/ を直接参照
    ├── adminer → ../repos/github.com--devbasex--devbase-samples/adminer/projects/adminer
    └── ...
```

`plugins/` 中間層は設けない。PR1 から `projects/` → `repos/` の直接リンクにする。
既存の `plugins/` ベースインストールは PR2 のマイグレーションで repos/ ベースに変換する。

> **NOTE**: repos/ 内でユーザーが加えた変更は projects/ 経由で即座に反映される。
> これは意図的な仕様であり、プラグイン開発時にローカル変更を即テストできる利点がある。

**フロー (新)**:
1. `repo add` → `repos/<owner>--<repo>/` に永続 git clone → `registry.yml` 読み取り → `plugins.yml` 保存
2. `plugin install` → repos/ 内の既存クローンからシンボリックリンク作成 (コピー不要)
3. `sync_projects()` → `projects/<proj>` → `../repos/<repo>/<plugin>/projects/<proj>` 直接リンク
4. `plugin update` → `repos/<repo>/` で `git pull` → シンボリックリンクは自動追従
5. `repo refresh` → `repos/<repo>/` で `git pull` + `registry.yml` 再読み込み → `plugins.yml` メタデータ更新
6. プラグイン開発 → `repos/<repo>/` で直接 commit/push 可能

## 変更対象ファイル

### PR1 (repos/ 永続クローン + 直接リンク install)

| ファイル | 変更内容 |
|---|---|
| `lib/devbase/plugin/models.py` | `RegisteredRepository` に `local_path` フィールド追加 |
| `lib/devbase/plugin/registry.py` | `get_repos_dir()` 追加、`InstalledPlugin.path` を repos/ ベースに変更、`get_plugins_dir()` は `--link` 用に維持 |
| `lib/devbase/plugin/repo_manager.py` | `add_repository()`: 永続クローン、`refresh_repository()`: git pull + registry.yml 再読み込み、`remove_repository()`: repos/ 削除 (dirty check + `--force` 対応) |
| `lib/devbase/plugin/installer.py` | `git_clone()`: `--depth 1` を除去し full clone に変更 (永続クローン用)、`_install_from_repo()`: repos/ ベースのシンボリックリンク作成に変更、`uninstall_plugin()`: repos/ 内ファイル保護 (シンボリックリンク削除のみ、`shutil.rmtree()` を排除)、`_link_plugin()`: `InstalledPlugin.path` を devbase_root 相対に変更、`copy_plugin()` / `_sync_dir()` 削除 |
| `lib/devbase/plugin/syncer.py` | `sync_projects()`: `plugins_dir.iterdir()` フラット走査から `InstalledPlugin.path` ベースのネスト対応走査に変更、`projects/` → repos/ 直接リンクに変更 + 同名衝突時の suffix リンク追加 |
| `lib/devbase/plugin/updater.py` | `update_plugin()`: git pull ベースに変更、`_migrate_removed_plugin()`: repos/ ベースでの再インストールに変更 (`copy_plugin()` 呼び出しを排除) |
| `.gitignore` | `repos/` 追加、マイグレーション完了後に `plugins/*/` / `!plugins/.gitkeep` を削除 |
| `tests/plugin/` | 新規テスト追加 |

### PR2 (既存 plugins/ → repos/ マイグレーション)

| ファイル | 変更内容 |
|---|---|
| 新規 `lib/devbase/plugin/migrator.py` | マイグレーションロジック (plugins/ の差分検出・repos/ クローン・パス書き換え・リンク再作成) |
| `lib/devbase/commands/plugin.py` | `devbase plugin migrate` サブコマンド追加 |
| ドキュメント | 移行手順の説明 |
| `tests/plugin/` | マイグレーションのテスト |

## 設計上の重要判断

### 1. repos/ のディレクトリ命名規則

常に `host--owner--repo` 形式 (ダブルハイフン区切り) で統一する:
- `github.com--devbasex--devbase-samples` ← `https://github.com/devbasex/devbase-samples.git`
- `github.com--volareinc--devbase-ext` ← `https://github.com/volareinc/devbase-ext.git`
- `gitlab.com--user--my-repo` ← `https://gitlab.com/user/my-repo.git`
- URL から `host` と `owner/repo` を抽出し、`/` を `--` に置換して `host--owner--repo` を生成
- SSH 形式 (`git@github.com:owner/repo.git`) と HTTPS 形式は同一 dirname に正規化され、重複検出が機能する
- `plugins.yml` の `RegisteredRepository` に `local_path` を追記して追跡

**host を含める理由**: github.com と gitlab.com など異なるホストで同名の owner/repo を扱う場合に dirname が衝突するのを防ぐため (PR1 レビューで判明し対応)。

**ドット区切りではなくダブルハイフンを使う理由**: owner 名やリポジトリ名にドットを含むケース (例: `my.org/my.repo`) でパース時に曖昧になるため。`--` は GitHub の owner/repo 名に使用されない文字列なので一意に分割できる。host (`github.com` 等) のドットはそのまま含めるが、`--` 区切りで host / owner / repo を分離するため曖昧にならない。

### 2. install 時のシンボリックリンク戦略

`plugins/` 中間層は設けず、`projects/` から `repos/` へ直接リンクする。

```
# 現状 (ファイルコピー)
plugins/adminer/           ← 実ファイル (.git なし)
projects/adminer → ../plugins/adminer/projects/adminer

# 新方式 (repos/ 直接リンク)
projects/adminer → ../repos/github.com--devbasex--devbase-samples/adminer/projects/adminer
```

`InstalledPlugin.path` は `repos/github.com--devbasex--devbase-samples/adminer` のように repos/ 配下のパスを保持する。
パス解決を `get_plugins_dir()` ベースから `devbase_root` ベースに変更する。
変更対象: `registry.py` (`get_plugins_dir()` → `get_repos_dir()`)、`installer.py` / `syncer.py` / `updater.py` のパス結合ロジック。

### 3. 同名プロジェクト衝突時の suffix 付きシンボリックリンク

複数プラグインが同名プロジェクトを提供する場合、現状は priority が高い方だけがリンクされ、低い方は無視される (warning ログのみ)。

**新方式**: 衝突時に `<project>.<repos-dirname>` 形式の suffix 付きシンボリックリンクも作成する。suffix には repos/ ディレクトリ名 (`host--owner--repo`) 全体を使い、同一 owner が複数リポジトリを持つケースでも一意になるようにする。

```
projects/
├── carmo                                          → ../repos/github.com--volareinc--devbase-ext/carmo-web/projects/carmo  # winner (priority 高)
└── carmo.github.com--takemi-ohama--devbase-ext    → ../repos/github.com--takemi-ohama--devbase-ext/personal/projects/carmo  # 明示指定用 (loser のみ)
```

- 衝突がない場合は bare name のみ (suffix なし、suffix 版は作成しない)
- 衝突がある場合は winner が bare name を取得し、**loser のみ** suffix 版を作成
- winner は bare name でアクセスできるため suffix 版は不要 (リンクの重複を避ける)
- loser を使うには `cd projects/carmo.github.com--takemi-ohama--devbase-ext && devbase up` のように suffix 付きディレクトリに移動して起動する (`devbase up` は CWD のディレクトリ名を `COMPOSE_PROJECT_NAME` として使用する)
- suffix 識別子は `syncer._extract_owner()` が生成: repos/ ベースは `repos/<host--owner--repo>/...` の dirname 部分 (`parts[1]`) を、`--link` プラグインは `source` パス末尾を返す
- **互換性確認済み**: `devbase up` は `basename "$PWD"` を `COMPOSE_PROJECT_NAME` に設定するだけでバリデーションなし。Docker Compose もドット・ハイフンを含むプロジェクト名を許容するため、`carmo.github.com--takemi-ohama--devbase-ext` 形式で問題なく動作する
- ログに衝突の全候補と suffix 付きアクセス方法を表示

**変更対象**: `syncer.py` の `sync_projects()` に suffix リンク生成ロジック追加

### 4. --link インストールとの共存

`devbase plugin install --link /path:plugin` (ローカルリンク) は repos/ を経由せず、従来どおり動作する。
repos/ 経由のインストールとローカルリンクは `InstalledPlugin.linked` フラグで区別される。

`--link` インストール時の `InstalledPlugin.path` はローカルパスへの symlink を指すため、`plugins/` ではなく devbase_root 相対の実パスを保持する。
具体的には `_link_plugin()` で `plugins_dir / name` に symlink を作成する現行ロジックを維持し、`InstalledPlugin.path` は `plugins/{name}` のまま据え置く。
`plugins/` ディレクトリは `--link` 専用として存続させ、repos/ ベースのインストールとは明確に分離する。
マイグレーション後も `plugins/` を完全削除するのは `--link` インストールが 0 件の場合のみとする。

同名プロジェクト衝突が `--link` プラグインと repos/ プラグインの間で起きた場合の suffix ルール:
- `--link` プラグインは owner 情報を持たないため、suffix は `.<source-basename>` 形式とする (例: `carmo.my-local-repo`)
- `source-basename` は `InstalledPlugin.source` のパス末尾から取得する

### 5. ref 指定時の制約

同一リポジトリの異なる branch/tag を別々に参照するケース (例: `repo-a@main` と `repo-a@v2`) は **PLAN04 スコープ外** とする。
現状 `repos/` には 1 リポジトリにつき 1 クローンのみ保持し、ref はデフォルトブランチに固定する。

将来 ref 別管理が必要になった場合は `repos/<host--owner--repo>@<ref>/` 形式で分離する設計を検討するが、
`RegisteredRepository` のモデル拡張 (ref 単位の管理) や `git pull` 対象の複数化など影響が大きいため別 PLAN とする。

### 6. .gitignore と機密情報保護

repos/ 配下のリポジトリは各自の `.gitignore` で管理されるが、devbase 側でも防御:
- `repos/` 自体を devbase の `.gitignore` に追加 (devbase リポジトリには含めない)
- 各プラグインの `.env`, `.docker-compose.scale.yml` 等は各リポジトリの `.gitignore` 責任
- マイグレーション完了後、既存の `plugins/*/` / `!plugins/.gitkeep` エントリは `--link` インストールが残っている場合のみ維持し、なければ削除する

### 7. git_clone の full clone 化

現在の `git_clone()` は `--depth 1` で shallow clone を行っている (installer.py:57)。
repos/ 永続クローンでは `git pull` / `git push` / `git log` 等のフル git 操作が必要なため、永続クローン用には `--depth 1` を除去する。

**変更方針**: `git_clone()` に `shallow: bool = True` パラメータを追加する。
- 既存の temp clone 呼び出し (残存する場合) → `shallow=True` (デフォルト、現行動作維持)
- repos/ 永続クローン → `shallow=False` で full clone

### 8. update / refresh の動作変更

`plugin update` と `repo refresh` はどちらも `git pull` を実行するが、目的が異なる:
- **`plugin update`**: 特定プラグインの更新。`git pull` 後、プラグインが削除されていた場合の `_migrate_removed_plugin()` 処理を含む
- **`repo refresh`**: リポジトリ全体のメタデータ更新。`git pull` 後、`registry.yml` を再読み込みして `plugins.yml` のプラグイン一覧を同期する。インストール済みプラグインが `registry.yml` から削除されていた場合は warning を表示し、`plugin update` と同等の `_migrate_removed_plugin()` 検出・通知を行う

**`refresh_repository()` の遷移詳細**: 現行実装 (repo_manager.py:157-206) は temp clone → `parse_registry_yml()` → `RegisteredRepository` 再構築 → `registry.add_repository()` というフロー。新方式では:
1. `repos/<owner>--<repo>/` で `git pull` を実行
2. 同ディレクトリ内の `registry.yml` を `parse_registry_yml()` で再読み込み
3. `RegisteredRepository` を再構築して `registry.add_repository()` で更新 (既存ロジック流用)
4. `resolve_repo_url()` は repos/ 永続クローン時に `add_repository()` で既に処理済みのため不要

| 操作 | 現状 | 新方式 |
|---|---|---|
| `plugin update` | temp clone → `_sync_dir()` 差分同期 | `git pull` (repos/ 内) |
| ユーザ編集の保持 | `.new` ファイルで衝突解決 | git の通常の merge/conflict で解決 |
| orphan ファイル | 自動保持 | git 管理外ファイルは untracked として残る |

**利点**: git 本来のバージョン管理がそのまま使える。`_sync_dir()` の独自衝突解決ロジックが不要になる。

`repos/` 内で未コミット変更がある状態で `plugin update` (= `git pull`) が実行された場合、git が通常のエラーを返すのでそのまま伝搬する (強制 reset はしない)。

### 9. `repo remove` の安全性

`repos/` 内に未コミット・未 push の変更がある場合、`repo remove` でディレクトリごと削除すると作業が失われる。
- `repo remove` 実行前に `repos/` 内の `git status` をチェック
- dirty (未コミット変更あり) または unpushed commits がある場合はエラーで中断し、状態を表示
- `--force` フラグで強制削除を許可

### 10. `uninstall_plugin()` の repos/ 保護

現行の `uninstall_plugin()` (installer.py:459-476) は `shutil.rmtree(plugin_dir)` で `plugins/` 内のディレクトリを物理削除する。
新方式では `InstalledPlugin.path` が `repos/` 配下を指すため、`shutil.rmtree()` をそのまま実行すると repos/ 内のファイルが破壊される。

**変更方針**:
- `linked=False` かつ repos/ ベースのプラグイン → `registry.remove(name)` + `sync_projects()` のみ実行。repos/ 内のファイルは削除しない
- `linked=True` (`--link` インストール) → 従来どおり `plugins/` 内の symlink を `unlink()` で削除
- repos/ 内のファイルを完全に削除したい場合は `repo remove` を使う

### 11. `repo add` の冪等性

現行の `add_repository()` (repo_manager.py:48-53) は重複 URL を `RepositoryError` で拒否する。
新方式では repos/ に永続クローンが残るため、「既に `repo add` 済みの URL を再度 `repo add` する」シナリオが増える。

**方針**: 重複 URL チェックは維持し、エラーメッセージに `repos/` の既存クローンを再利用する旨を案内する (現行動作と同じ)。
テスト項目の「同一 URL を 2 回実行したとき、既存クローンを再利用してエラーにならない」は誤り — 正しくは「同一 URL を 2 回実行したとき、RepositoryError が返り、repos/ の既存クローンは破壊されない」。

### 12. `sync_projects()` の走査方式変更

現行の `sync_projects()` (syncer.py:79) は `plugins_dir.iterdir()` でフラット走査し、各プラグインディレクトリの `projects/` を探索する。
新方式では repos/ が `repos/<owner>--<repo>/<plugin>/` のネスト構造を持つため、この走査ロジックは根本的に変わる。

**変更方針**: `plugins_dir.iterdir()` ベースの走査を廃止し、`registry.list_installed()` から各 `InstalledPlugin.path` を取得して走査する。
これにより repos/ のディレクトリ構造に依存せず、`InstalledPlugin` のメタデータから直接プラグインを参照できる。

```python
# 現行: plugins/ フラット走査
for plugin_entry in sorted(plugins_dir.iterdir()):
    if plugin_entry.name not in installed_names:
        continue
    ...

# 新方式: InstalledPlugin.path ベース走査
for plugin in registry.list_installed():
    plugin_dir = registry.devbase_root / plugin.path
    if not plugin_dir.is_dir():
        logger.warning("Plugin directory missing: %s", plugin.path)
        continue
    ...
```

### 13. `RegisteredRepository.local_path` の後方互換性

`RegisteredRepository` に `local_path` フィールドを追加するが、`from_dict()` は `.get()` ベースのため、旧バージョンの devbase が新形式の `plugins.yml` を読み込んでも `local_path` は無視される (後方互換)。
逆に新バージョンが旧形式を読む場合も `local_path` は `""` にフォールバックするため問題ない。

PR1 + PR2 は同時リリース (release/PLAN04 → main 一括マージ) のため、`InstalledPlugin.path` の新旧フォーマット (`plugins/X` vs `repos/owner--repo/X`) が混在する運用期間は発生しない。

### 14. マイグレーション戦略

既存の `plugins/` インストール → `repos/` ベースへの移行:
1. `repo add` 済みのリポジトリ → `repos/` にクローン (まだない場合)
2. 各 `plugins/<name>/` と `repos/` 内の対応ディレクトリを比較し、`plugins/` 側に git 未追跡の差分 (ユーザー変更) がないか検出
3. 差分がある場合 → warning を表示し、`plugins/<name>/` を `plugins/<name>.bak/` にリネームして保全。ユーザーに手動マージを促す
4. 差分がない場合 → `plugins/<name>/` を削除
5. 各インストール済みプラグイン → `InstalledPlugin.path` を更新
6. `sync_projects()` で全シンボリックリンクを再作成
7. `plugins/` ディレクトリ内に `--link` インストールが残っていなければ、`.gitkeep` のみ残して削除。`--link` インストールが残っている場合は `plugins/` を維持する

マイグレーションは `devbase plugin migrate` コマンドまたは `plugin install/update` 初回実行時に自動で行う。

## PR 分割計画

| PR # | branch 名 | base | 概要 | 状態 |
|---|---|---|---|---|
| 1 | feature/PLAN04-repos-core | release/PLAN04 | repos/ 永続クローン + 直接リンク install + git pull update + plugins/ 廃止 | ✅ マージ済み (#29, merge 79b661f) |
| 2 | feature/PLAN04-migration | release/PLAN04 | 既存 plugins/ → repos/ マイグレーション + ドキュメント (PR1 マージ後に作成) | ✅ マージ済み (#31, merge 5a6158a) |

release branch: `release/PLAN04` (base: `main`)
release PR: **#26 (release/PLAN04 → main) — OPEN / Ready / MERGEABLE。PR1・PR2 統合済み、main へのリリース待ち**

> **PR2 の PR 番号について**: 当初 PR #30 として作成し `/ndf:cross-review` で 6 round 収束させたが、
> マージ前に rotate (PR #30 を close → 同一ブランチ `feature/PLAN04-migration` から PR #31 を再作成) し、
> #31 を `release/PLAN04` へマージした。コードは #30 のレビュー収束済み内容と同一。

**リリース戦略**: PR1 → PR2 の順に `release/PLAN04` へマージし、全体を `main` へ一括リリースする。
PR1 と PR2 は必ず同時リリースとなるため、`InstalledPlugin.path` の新旧フォーマット混在は発生しない。
**現状**: PR1・PR2 ともに `release/PLAN04` へマージ完了 (HEAD=5a6158a)。次工程は release PR #26 を main へマージするリリースフェーズ。

### PR 1: repos/ 永続クローン + 直接リンク install (core)

**スコープ**:
- `models.py`: `RegisteredRepository.local_path` フィールド追加、`from_dict()` / `to_dict()` 対応
- `registry.py`: `get_repos_dir()` 追加、`InstalledPlugin.path` を repos/ ベースに変更、`get_plugins_dir()` は `--link` 用に維持
- `repo_manager.py`: `add_repository()` を `repos/` 永続クローンに変更、`refresh_repository()` を `repos/` 内 git pull + registry.yml 再読み込みに変更 (temp clone 廃止)、`remove_repository()` で `repos/` 削除追加 (dirty check + `--force` 対応)
- `installer.py`: `git_clone()` に `shallow` パラメータ追加 (永続クローンは full clone)、`_install_from_repo()` を repos/ ベースのシンボリックリンク作成に変更、`uninstall_plugin()` を repos/ 保護対応に変更 (registry 削除 + sync のみ、`shutil.rmtree()` 排除)、`copy_plugin()` / `_sync_dir()` / `_SyncReport` / `_hash_file()` / `_replace_entry()` / `_ALWAYS_OVERWRITE_AT_ROOT` 削除
- `syncer.py`: `sync_projects()` を `plugins_dir.iterdir()` フラット走査から `InstalledPlugin.path` ベース走査に変更、`projects/` → repos/ 直接リンクに変更 + 同名衝突時の suffix リンク追加
- `updater.py`: `update_plugin()` を git pull ベースに変更、`_migrate_removed_plugin()` を repos/ ベースに変更
- `.gitignore`: `repos/` 追加 (ディレクトリ全体を ignore)
- `tests/plugin/`: コア機能のユニットテスト

**差分見積**: ~850 行 (テスト含む。`_sync_dir` 系 ~160 行削除、shallow パラメータ・uninstall 保護・syncer 走査変更の追加分を含む)

### PR 2: 既存 plugins/ → repos/ マイグレーション

**スコープ**:
- 新規 `migrator.py`: マイグレーションロジック (plugins/ の差分検出 → repos/ クローン → `InstalledPlugin.path` 書き換え → リンク再作成 → plugins/ 削除)
- `commands/plugin.py`: `devbase plugin migrate` サブコマンド追加
- ドキュメント更新
- マイグレーションのテスト

**差分見積**: ~300 行 (テスト含む)

## テスト計画

### PR 1 テスト項目

> PR1 (#29) マージ済み。自動テストでカバーした項目を `[x]`、手動/結合確認が必要な
> 項目を `[ ]` (末尾に「手動」と注記) とする。テストは `tests/plugin/test_repos_core.py` (全 50 テスト)。

**基本機能**:
- [x] `repo add` → `repos/<host--owner--repo>/` にクローンされる、`.git/` が存在する (`test_add_creates_persistent_clone`)
- [x] `plugin install <name>` → `projects/<proj>` が repos/ 内のプロジェクトへ直接リンクされる (`test_install_creates_symlinks_via_repos`, `test_install_all_plugins`)
- [x] `sync_projects()` → `projects/<proj>` が `../repos/<host--owner--repo>/<plugin>/projects/<proj>` へリンクされる (`test_basic_sync_creates_symlinks`)
- [x] `plugin update` → `git pull` が実行される (`test_update_calls_git_pull`, `test_update_deduplicates_git_pull`)
- [x] `plugin uninstall` → `projects/` のシンボリックリンクが削除される、repos/ 内のファイルは残る (`test_uninstall_repos_plugin_preserves_files`)
- [x] `repo refresh` → git pull + registry.yml 再読み込み (`test_refresh_pulls_and_updates_metadata`)
- [x] `repo refresh` → インストール済みプラグインが registry.yml から削除されていた場合に warning 表示 (`test_refresh_warns_removed_installed_plugin`)
- [x] `repo add` → 同一 URL を 2 回実行したとき RepositoryError が返り、repos/ の既存クローンは破壊されない (`test_add_duplicate_url_raises`)
- [x] `repo add` → full clone (shallow でない) が作成される (`test_shallow_false_no_depth` / `test_shallow_true_adds_depth`)
- [x] `repo remove` → dirty check 後に `repos/<name>/` ディレクトリも削除される (`test_remove_deletes_clone_dir`)
- [x] `repo remove` → repos/ 内に未コミット変更がある場合はエラーで中断、`--force` で強制削除 (`test_remove_dirty_repo_raises_without_force`, `test_remove_dirty_repo_succeeds_with_force`)
- [x] `repo remove` → インストール済みプラグインの `projects/` シンボリックリンクが全て削除される (`test_remove_uninstalls_plugins_and_syncs`)
- [x] `repo remove` → `repos/` ディレクトリが削除される (`test_remove_deletes_clone_dir`)
- [x] `repo remove --force` → dirty な repos/ でも強制削除される (`test_remove_dirty_repo_succeeds_with_force`)
- [x] `--link` インストールは従来どおり `plugins/` 内に symlink が作成される (`test_uninstall_linked_plugin_removes_symlink`)
- [ ] repos/ 内で直接 `git commit` / `git push` が可能 (手動 — 永続クローンの自明な性質)

**同名衝突**:
- [x] 同名プロジェクト衝突時に loser に suffix 付きシンボリックリンクが作成される (`test_collision_creates_suffix_links`)
- [x] winner は bare name のみ (suffix 版は作成されない) (`test_winner_has_no_suffix`)
- [x] 衝突がない場合は suffix なしの bare name のみ作成される (`test_no_collision_no_suffix`)
- [ ] suffix 付きディレクトリに `cd` して `devbase up` で loser プロジェクトを起動できる (手動 — devbase up 結合確認)
- [x] `--link` プラグインと repos/ プラグインの衝突時に `.<source-basename>` suffix が正しく生成される (`test_link_plugin_collision_uses_source_basename`)

**追加対応 (PR1 レビューで判明・実装)**:
- [x] 異なるホスト (github.com / gitlab.com) の同名 owner/repo が dirname 衝突しない (`test_different_hosts_produce_different_dirnames`)
- [x] SSH / HTTPS 形式の同一リポジトリが同一 dirname に正規化される (`test_ssh_and_https_same_host_match`)
- [x] 登録済みリポジトリへの `@ref` 指定は PluginError で拒否 (`test_install_ref_rejected_for_registered_repo`, `test_install_ref_rejected_for_unregistered_repo`)
- [x] `local_path` 未設定の legacy repo は install 時に永続クローンへ自動移行 (`test_install_legacy_repo_without_local_path`)
- [x] `refresh_repository` は git pull 前の projects スナップショットを `_update_repo_plugins` に渡す (`test_refresh_passes_pre_pull_projects`, `test_snapshot_*`)

**エッジケース**:
- [ ] `repos/` 内で未コミット変更がある状態で `plugin update` → git エラーが伝搬される (強制 reset しない) (手動 — 実 git 操作)
- [ ] `repos/` ディレクトリが手動削除された状態で `plugin install` → 適切なエラーメッセージ (手動)
- [x] `repos/` ディレクトリ/プラグインディレクトリが欠落した状態で `sync_projects()` → warning 表示してスキップ (`test_missing_plugin_dir_warns`, `test_real_directory_skipped`)
- [ ] `repo add` 済み + `plugin install` 前に `repos/` 内のファイルを手動変更 → シンボリックリンク作成のみ、変更は projects/ 経由で反映される (手動 — 意図的な仕様)

### PR 2 テスト項目

> PR2 (#31, 旧 #30) マージ済み。自動テストでカバーした項目を `[x]`、手動/結合確認が必要な項目を
> `[ ]` (末尾に「手動」と注記) とする。テストは `tests/plugin/test_migrator.py`。
> release/PLAN04 全体で pytest 281 件 green / compileall OK / ruff(E9,F63,F7,F82) クリーン (2026-05-28 検証)。

- [x] `devbase plugin migrate` → 既存 plugins/ → repos/ 移行が正常完了 (`TestMigrateClean`, `TestCmdPluginMigrate`)
- [x] マイグレーション後に `projects/` のシンボリックリンクが正しい (`test_clean_migration_creates_repos_symlink`)
- [ ] マイグレーション前後で `devbase up <project>` が正常動作 (手動 — devbase up 結合確認)
- [x] `plugins/` にユーザー変更がある場合 → warning 表示 + `.bak/` にリネームして保全 (`TestMigrateWithLocalChanges`)
- [x] `plugins/` にユーザー変更がない場合 → そのまま削除 (`test_clean_migration_updates_path_and_deletes_copy`)
- [x] `--link` インストールが残っている場合 → `plugins/` ディレクトリは維持される (`TestMigrateKeepsLinked`)
- [x] `--link` インストールが 0 件の場合 → `plugins/` ディレクトリは `.gitkeep` のみ残して削除 (`test_clean_migration_empties_plugins_dir_to_gitkeep`)

**追加実装 (計画の「自動移行」要件)**:
- [x] `plugin install` 初回実行時に legacy plugins/ を自動移行 (`TestAutoMigrateOnInstall`)
- [x] `plugin update` 初回実行時に legacy plugins/ を自動移行 (`TestAutoMigrateOnUpdate`)
- [x] `local_path` 未設定の legacy repo は移行時に永続クローンを作成 (`TestMigrateClonesMissingRepo`)
- [x] source repo が未登録の plugin は skip し、コピー/メタデータを破壊しない (`TestMigrateSkips`)
- [x] 差分判定 (`_dirs_differ`): 内容変更・ユーザー追加ファイル・upstream 追加ファイルを差分として検出 (`TestDirsDiffer`)

## リスクと対策

| リスク | 影響 | 対策 |
|---|---|---|
| repos/ のディスク使用量増加 (full clone 化) | ストレージ圧迫 | extension リポジトリは履歴が軽量なため full clone で問題なし (shallow → full で数 MB 程度の増加)。将来肥大化した場合は `git gc` や shallow 化を検討 |
| 既存ユーザの plugins/ にカスタム変更がある | マイグレーションでデータ消失 | マイグレーション前に `plugins/` の差分検出 → 差分がある場合は `.bak/` にリネームして保全 + warning 表示 |
| 同一リポジトリ内の異なるブランチ/タグ参照 | repos/ は 1 クローン | PLAN04 スコープ外。将来 `repos/<host--owner--repo>@<ref>/` 形式で分離を検討 |
| オフライン環境での初回 install | git clone 不可 | 既存 repos/ があればオフラインでもシンボリックリンク作成可 |
| repos/ 内の未コミット変更と plugin update の競合 | git pull 失敗 | git エラーをそのまま伝搬し、ユーザに commit/stash を促すメッセージを表示 |
| repos/ と plugins.yml の不整合 (手動操作等) | 動作不安定 | `repo refresh` で repos/ の存在確認 + 再クローン機能を提供 |
| `repo remove` で未コミット作業の消失 | データ消失 | dirty check (未コミット変更・unpushed commits) でエラー中断、`--force` で強制削除 |

## スコープ外 (将来の検討事項)

- ref 指定による同一リポジトリの複数クローン管理 (別 PLAN)
- `repo dev` コマンド (repos/ 内での開発ヘルパ — ニーズが明確になってから設計)
- Windows ネイティブ対応 (シンボリックリンク主体の設計のため非対応。WSL2 上での利用は問題なし — 要動作確認)
