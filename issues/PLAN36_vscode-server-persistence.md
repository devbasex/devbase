# PLAN36: VS Code Server をコンテナ再作成をまたいで保つ

## 関連リンク

- 発端: `carmo-ai` へ VS Code を attach したときのログ（毎回 215MB の再ダウンロード）
- 参考: `docs/user/container-operations.md`（ボリューム構造）、`containers/base/entrypoint.sh`（AI 設定の symlink 機構）

## モード

`standard` — 生成 compose とボリューム管理に振る舞いを足す。既存テスト (`tests/volume`) が十分にあり、
公開コマンドやスキーマは変えない。**新コマンド（例: ボリュームの掃除）を足す設計を採る場合は
`architecture` へ上げ直す**。

## 目的と非目的

達成したい状態:

- `devbase up` でコンテナを作り直しても、**VS Code Server の再ダウンロード（215MB / 約 55 秒）が起きない**。
- 拡張機能とサーバー本体が、コンテナの寿命ではなくプロジェクトの寿命で保たれる。

やらないこと:

- VS Code のバージョン更新時の再取得をなくすこと。commit ハッシュが変われば新しい本体の取得は必要（削減対象は**再作成のたびの再取得**）。
- 拡張機能の設定共有・プロファイル同期の仕組みづくり。
- `~/.vscode-server` 以外のホームディレクトリの永続化（別課題）。

## 前提

- 前提 1: `~/.vscode-server` は現在コンテナの書き込みレイヤ上にあり、`docker rm` で消える。永続化されているのは
  entrypoint の `AI_SETTINGS`（`.claude` / `.claude.json` / `.codex` / `.gemini` / `.serena` / `.ssh` / `.kiro`）と `share` のみ。
- 前提 2: 実測サイズ（`carmo-ai-dev-1`、VS Code 1.134.0 / arm64）:

  | 内訳 | サイズ | 性質 |
  |---|---|---|
  | `bin/<commit>` | 644MB | VS Code のバージョンごと。**再ダウンロードの本体**（tar 展開後） |
  | `data/agent-host` | 303MB | 拡張機能（Claude Code）の実行データ |
  | `extensions` | 338MB | インストール済み拡張 |
  | `extensionsCache` | 205MB | 拡張のキャッシュ |
  | `data/User` ほか | 数 MB | 設定・ログ・接続トークン |
  | 合計 | 約 1.6GB | |

- 前提 3: コストを払っているのは**実際に attach したコンテナだけ**。現在 14 コンテナ中 3 つ
  (`project-trygroup-prd-dev-1` 1.9GB / `carmo-ai-dev-1` 1.6GB / `bi-tools-dev-1` 1.6GB)。
- 前提 4: 既存の work ボリューム `devbase_work_<index>` は**プロジェクト間で共有**（43GB）。
  一方 VS Code Server は 1 コンテナ 1 セットで動く前提の状態を持つ（`data/Machine/.connection-token-<commit>`、
  各種 marker、ログ）。共有すると同時起動時に競合する。
- 前提 5: `devbase up` は `docker compose down` でコンテナを削除してから作り直す（`cmd_up` の [3/6] → [4/6]）。
  匿名ボリュームの引き継ぎは「既存コンテナを再作成するとき」にしか働かないため、この経路では効かない
  （実測: `up` の前後でボリューム ID が変わり、旧ボリュームが孤児として残った）。
- 前提 6: 空のボリュームをマウントすると、マウント先は **root 所有**で作られる。開発ユーザーのままでは
  書き込めない（実測: 匿名ボリュームで `Permission denied`）。

## 受け入れ条件

実装は PR #131 でマージ済み（2026-08-31）。検証は `devbase-base` 再ビルド後、
使い捨てプロジェクト `plan36-check`（scale=2）/ `plan36-check2`（scale=1）で実施した。
VS Code の attach 操作そのものは自動化できないため、AC1 / AC2 は **VS Code Server の状態が
コンテナ再作成をまたいで残ること**（再ダウンロードが要らない条件）で確認している。

- [x] AC1: 同じプロジェクトで `devbase down` → `devbase up` の後に VS Code を attach しても、
      `Installing VS Code Server` と 215MB のダウンロードが**発生しない**。
      検証: `~/.vscode-server/bin/<commit>/server.sh` を置いて `devbase down` → `devbase up`。
      再作成後も同じ内容が残った（旧構成ではコンテナ層ごと消えていた）。
- [x] AC2: 拡張機能（Claude Code / 日本語パック）が再インストールされない。
      検証: 同上の手順で `~/.vscode-server/extensions/extensions.json` が残ること。
- [x] AC3: **scale > 1 の各インスタンスが独立した状態を持つ**（同時 attach で接続トークンや設定を奪い合わない）。
      検証: scale=2 で `dev-1` / `dev-2` がそれぞれ `devbase_vscode_plan36-check_1` /
      `_2` をマウントし、`data/Machine/.connection-token-<commit>` が再起動後も別の値のままだった。
- [x] AC4: **別プロジェクトのコンテナと状態を共有しない**。
      検証: `plan36-check`（2 インスタンス）と `plan36-check2` を同時起動し、3 コンテナが
      それぞれ別ボリューム・別トークンを保持することを確認。
- [x] AC5: 初回（ボリュームが空）でも権限エラーなく VS Code Server がインストールできる。
      検証: 新規ボリュームで `ls -ld ~/.vscode-server` が `ubuntu ubuntu`、`touch` が成功。
- [x] AC6: 既存プロジェクトが壊れない。`~/.vscode-server` を持つ既存コンテナを作り直しても起動でき、
      再ダウンロードは 1 回だけ（ボリュームへ移った後は起きない）。
      検証: マウント無しの旧構成でコンテナ層に内容を置いた状態から `devbase up`。正常起動し、
      ボリュームは空（＝この 1 回だけ再取得が要る）。その後は再起動しても内容が残った。
- [x] AC7: ボリュームの掃除方法がドキュメント化されている（プロジェクト削除時に孤児が残る問題への手当て）。
      検証: `docs/user/container-operations.md` / `docs/user/troubleshooting.md` に記載した
      `docker volume ls --filter name=devbase_vscode_` → `docker ps -a --filter volume=...` →
      `docker volume rm` を実機で実行。稼働中のボリュームは Docker が削除を拒否することも確認。

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
|---|---|---|---|
| **A. コンテナごとの named volume を `~/.vscode-server` へマウント** | `devbase_vscode_<project>_<index>` | **採用** | 再作成をまたいで保ちつつ、コンテナ間の状態競合が起きない。scale・プロジェクト間の独立を同時に満たす（AC3 / AC4） |
| **A'. コンテナごとの匿名ボリューム** | `volumes: - /home/ubuntu/.vscode-server`（名前を付けない） | **不採用（実測で確認）** | Compose が匿名ボリュームを引き継ぐのは「コンテナを再作成するとき」だけ。devbase の `up` は `down`（コンテナ削除）を挟むため引き継ぎ元が消え、**毎回新しいボリュームが作られて旧ボリュームが 1.6GB の孤児として残る**。目的を達成できないうえ現状より悪化する |
| B. 全コンテナで 1 つの共有ボリューム | `devbase_home_ubuntu` と同じ扱い | 不採用 | `data/Machine/.connection-token-<commit>`・各種 marker・ログを複数コンテナが同時に書く。VS Code の前提（1 マシン 1 セット）を壊す |
| C. `bin/` だけ共有し、`data` / `extensions` はコンテナごと | 重い 644MB を共有 | 不採用（将来の最適化候補） | ダウンロード削減という目的には効くが、`bin` の中身は同一 commit で読み取り専用に近いとはいえ、VS Code が bin 配下へ書く保証が無い。まず A で目的を満たし、容量が問題化したら再検討する |
| D. ベースイメージへ VS Code Server を焼き込む | build 時に取得 | 不採用 | commit ハッシュはクライアントの VS Code 更新で変わる。更新のたびにイメージが陳腐化し、結局ダウンロードが走る |
| E. `AI_SETTINGS` に `.vscode-server` を足す（`/persistent/ai` 配下へ symlink） | 既存機構の流用 | 不採用 | `/persistent/ai` は**全コンテナ共有**なので実質 B と同じ競合が起きる |

## ドメイン用語

| 用語 | 意味 |
|---|---|
| VS Code Server | attach 時にコンテナ内へ入る `~/.vscode-server`。本体 (`bin/<commit>`)・拡張・データを含む |
| commit ハッシュ | VS Code クライアントのビルド識別子。`bin/<commit>` のディレクトリ名になり、クライアント更新で変わる |
| work ボリューム | `devbase_work_<index>`。**プロジェクト間で共有**される作業ツリー置き場 |
| AI 設定ボリューム | `devbase_home_ubuntu`。`.claude` 等を全コンテナで共有する |

## 不変条件

- 1 つの VS Code Server 状態（`~/.vscode-server`）を、同時に 2 つ以上のコンテナが書かない。
- ボリュームが空の状態から attach しても、開発ユーザー（`ubuntu`）が書き込める。
- 既存のボリューム（`devbase_work_*` / `devbase_home_ubuntu`）の扱いは変えない。

## 互換性

| 対象 | 変更 | 互換性の扱い |
|---|---|---|
| 生成 compose (`.docker-compose.scale.yml`) | dev サービスへ `~/.vscode-server` のマウントが増える | 追加のみ。`devbase up` で再生成されるため利用者の操作は不要 |
| プロジェクトの `compose.yml` | 変更不要 | プロジェクト側は書かない（scale 生成が付ける） |
| Docker ボリューム | `devbase_vscode_<project>_<index>` が増える | 新規追加。既存ボリュームは触らない |
| 既存コンテナ | 次回 `devbase up` から適用 | 初回だけ 1 度ダウンロードが走り、以後は再利用 |

## 修正対象

- `lib/devbase/volume/manager.py` — ボリューム名の解決と `ensure_volumes` での作成
- `lib/devbase/volume/compose.py` — dev インスタンスへのマウント追加（`_replace_volumes_for_instance` / `_build_volumes_section`）
- `containers/base/entrypoint.sh` — マウント先の所有者初期化（空ボリュームは root 所有で作られる）
- `docs/user/container-operations.md` — ボリューム構造と掃除方法
- `tests/volume/` — 生成 compose とボリューム名のテスト

## タスク分解

### Task 1: ボリューム名の解決と作成

- **対象ファイル:** `lib/devbase/volume/manager.py`, `tests/volume/test_manager_vscode.py`
- **変更内容:** `get_vscode_volume_for(project_name, index)` を追加し、`ensure_volumes` で scale 分を作成する。
  名前は `devbase_vscode_<project>_<index>`。プロジェクト名に使えない文字（`/` など）は正規化する。
- **満たす受け入れ条件:** AC3, AC4
- **進め方:** テスト駆動。名前の組み立て（正規化含む）と、`ensure_volumes` が scale 分を作ることを先に固定する。

### Task 2: 生成 compose へのマウント追加

- **対象ファイル:** `lib/devbase/volume/compose.py`, `tests/volume/test_compose_vscode.py`
- **変更内容:** dev インスタンスごとに `devbase_vscode_<project>_<index>:/home/ubuntu/.vscode-server` を追加し、
  `volumes:` セクションへ宣言する。プロジェクトが同じマウント先を書いていた場合は上書きしない。
- **満たす受け入れ条件:** AC1, AC2, AC3, AC4
- **進め方:** テスト駆動。既存の `/work` `/persistent/ai` 差し替えテストと同じ形で、
  各 dev インスタンスのマウントとボリューム宣言を検証する。

### Task 3: 空ボリュームの所有者初期化

- **対象ファイル:** `containers/base/entrypoint.sh`, `tests/containers/`
- **変更内容:** `~/.vscode-server` が存在し root 所有なら開発ユーザーへ `chown` する（既存の AI 設定の処理と同じ考え方）。
  既にユーザー所有なら何もしない（冪等）。
- **満たす受け入れ条件:** AC5, AC6
- **進め方:** テスト駆動。関数を切り出し、`DEVBASE_ENTRYPOINT_LIB_ONLY` で読み込んで検証する。
  **base イメージの再ビルドが必要**（[[entrypoint-change-needs-rebuild]]）。

### Task 4: ドキュメントと掃除方法

- **対象ファイル:** `docs/user/container-operations.md`, `docs/user/troubleshooting.md`, `CHANGELOG.md`
- **変更内容:** ボリューム構造の表へ追加し、プロジェクトを消したときに孤児ボリュームが残ること、
  その削除手順（`docker volume ls --filter name=devbase_vscode_` からの `docker volume rm`）を書く。
- **満たす受け入れ条件:** AC7
- **進め方:** 文書のみ。

## 影響範囲

- 全プロジェクトの生成 compose（`devbase up` のたびに再生成されるため移行作業は不要）。
- ディスク使用量: **attach したコンテナごとに約 1.6GB**。現状は同量がコンテナの書き込みレイヤにあるため
  純増ではないが、`down` してもボリュームは残るため、使っていないプロジェクト分が蓄積する。
- entrypoint 変更のため base イメージの再ビルドが必要。

## リスクと対処

| リスク | 対処 |
|---|---|
| ボリュームが増え続けディスクを圧迫する | Task 4 で掃除手順を明示。将来 `devbase` 側へ掃除コマンドを足す場合は `architecture` として再判定する |
| 空ボリュームが root 所有でインストールに失敗する | Task 3 の chown。AC5 で新規プロジェクトを検証 |
| 複数コンテナが同じボリュームを掴む | 名前にプロジェクト名と index を含める。AC3 / AC4 で同時 attach を検証 |
| プロジェクト名に Docker のボリューム名として使えない文字が含まれる | Task 1 で正規化し、テストで固定する |
| entrypoint 変更が `up` だけでは反映されない | [[entrypoint-change-needs-rebuild]]。検証手順に `devbase build --no-cache` を明記 |

## 切り戻し手順

- コード変更を revert し、`devbase up` で compose を再生成すればマウントは消える（ボリュームは残るので
  `docker volume ls --filter name=devbase_vscode_` から削除する）。
- データ移行は無く、失われるのは VS Code Server のキャッシュだけ。attach し直せば再取得される。

## 完了の定義

- [x] AC1〜AC7 を満たし、条件ごとに検証手段と結果が対応している
- [x] `uv run pytest` が green（1699 passed / 2026-08-31）
- [x] ベースイメージ再ビルド後の実機で、`down` → `up` に VS Code Server の再取得が要らないこと
- [x] `docs/` と `CHANGELOG.md` が新しいボリューム構造を説明している

## 結果

- PR: #131（codex / gemini とも round 1 で APPROVE、未解決スレッド 0）
- 確定仕様の置き場: `docs/user/container-operations.md`（ボリューム構造 / VS Code Server の永続化）、
  掃除と切り分けは `docs/user/troubleshooting.md`
- 稼働中の既存プロジェクトは、**次の `devbase up` から**マウントが付く。entrypoint の変更を
  含むため、各プロジェクトのイメージがベースイメージ再ビルド後のものである必要がある
