# Issue 34 検討: devbase 独自の Orca-like オーケストレーション

> 元 issue: `issues/i34.md`  
> 調査日: 2026-07-16  
> 改訂: 2026-07-17（レビュー反映: 削除順序の後置、Windows 移行、LSP trade-off、metadata 分割）  
> 結論: **CLI を制御基盤、単一ウィンドウの VS Code Extension を主 UI とする二層構成**を推奨する。
> ただし「単一 window（仮想 FS）」は手段であり、LSP 喪失が目的に見合わなければ「attach を残す
> dashboard」路線へ切り替える。
>
> 削除順序について（実施済み・当初方針からの変更）: 当初は「破壊的な sshd/Orca 削除は Phase 0 の
> Go 判定後に独立 PR で行う」方針だったが、接続性インシデントと base image／entrypoint／compose を
> 複雑化させる維持コストを機に、**sshd/Orca 経路の削除を先行して実施した（本対応で撤去済み）**。
> tmux + FileSystemProvider の feasibility spike は削除とは独立に Phase 0 で行う。理想は Go 判定後の
> 削除だったが、代替が未実装の間 Windows Orca 利用者には Remote-SSH への移行を案内する（§3.1）。

## 1. 結論

devbase が独自に作るべきものは「もう一つの IDE」ではなく、既存のコンテナ、AI CLI、
VS Code を束ねる **軽量なオーケストレーター**である。

- `devbase agent ...`（仮称）を UI 非依存の共通バックエンドにする。
- AI セッションは各コンテナ内の `tmux` で実行し、UI を閉じても継続・再接続できるようにする。
- VS Code Extension は **1つのウィンドウ内**にコンテナ／セッション一覧、複数ターミナル、
  ファイル一覧、エディタ、通知を提供する。
- CLI 版は一覧、起動、attach、send、stop を提供する。複数表示は `tmux` の window/pane に委譲する。
- コンテナを切り替えるたびに新しい VS Code window を開かない。常時多数の window が残る現状を解消する。
- ファイルは Extension が提供する仮想ファイルシステムを通じて、同じ editor area に開く。
- コンテナへの操作は **`docker exec` を唯一の標準経路**とし、コンテナ内に SSH server は置かない。
- Issue 33 の Orca 対応（sshd、SSH port publish、SSH config 生成、Orca relay）は削除する。
- Issue 31 の container attach URI は既存 `devbase up --open` の互換機能として残すが、
  Orca-like UI の標準経路にはしない。

優先順位は **共通 CLI → VS Code Extension → CLI TUI 強化**とする。CLI と Extension を別々に
実装するとセッション管理が二重化するため、先に JSON 出力可能な CLI/API を固める。

## 2. Orca の調査結果

Orca の価値は単なる複数ターミナルではなく、次の機能が一つの「worktree」に束ねられている点にある。

| 分類 | Orca の機能 | devbase での扱い |
|---|---|---|
| 分離 | タスクごとの Git worktree | 初期版では既存の複数コンテナを分離単位とする。worktree は第2段階 |
| 実行 | 複数 AI CLI、通常 shell、terminal tab/pane | `tmux` session + VS Code terminal で実現 |
| 状態 | working / waiting / idle、終了通知、Agents feed | 初期版は running / exited / attention。OSC 対応は段階導入 |
| 継続 | UI 切断後も remote agent が継続し、再接続 | コンテナ内 `tmux` により実現 |
| 編集 | Monaco editor、file search、autosave | 仮想ファイルシステムを VS Code editor に接続 |
| レビュー | diff、stage、commit、PR | Git を container 内で実行し、段階的に Source Control へ統合 |
| 遠隔 | SSH worktree、file sync、port forwarding | Orca 互換は対象外。必要なら Docker 接続先の host 側で扱う |
| 自動化 | CLI から terminal create/read/send/wait、file open | 共通 CLI の JSON API として重要度高 |
| 通知 | 完了／入力待ちを横断表示 | Extension の TreeView と VS Code notification で実現 |

参考（公式情報）:

- Worktrees: https://www.onorca.dev/docs/model/worktrees
- Agents & sessions: https://www.onorca.dev/docs/model/agents-sessions
- Terminal: https://www.onorca.dev/docs/terminal
- SSH worktrees: https://www.onorca.dev/docs/ssh
- Monaco editor: https://www.onorca.dev/docs/editing/monaco
- Orca CLI reference: https://www.onorca.dev/docs/cli/reference

### 2.1 真似るべき機能

1. 全コンテナ／全 AI セッションの状態を一画面で確認できる。
2. セッションを選ぶと即座に terminal へ再接続できる。
3. UI を閉じても処理が継続する。
4. 新しい agent を少ない操作で起動できる。
5. attention／終了を通知し、対象セッションへジャンプできる。
6. agent が言及したファイル、または指定したファイルをすぐ開ける。
7. 機械可読 CLI により、将来の自動オーケストレーションにも使える。

### 2.2 初期版では真似ない機能

- 独自コードエディタ、独自 Language Server、PR UI、埋め込みブラウザ
- GitHub／Linear／Jira の統合
- AI の会話内容を解析した高度な blocked 判定
- 独自の SSH server、SSH file sync／relay
- 複数ユーザー向けサーバー、権限管理、クラウド同期、モバイル UI

これらは VS Code、Docker CLI、既存 CLI で代替でき、devbase の中核ではない。

## 3. 現行 devbase で再利用できるもの

| 既存機能 | 再利用方法 |
|---|---|
| `project scale` と `dev-1..N` | agent の実行先一覧にする |
| Docker label (`dev.devbase.*`) | project、index、user の安定した識別子にする |
| `devbase list` TUI | CLI dashboard の入口として拡張する |
| `devbase login [index]` | shell attach の互換入口として残す |
| `editor/opener.py` | 従来の別 window attach 用として互換維持。新 UI の標準経路にはしない |
| 共通永続 volume `/persistent/ai` | agent 設定の共有に使う。session socket 自体は置かない |
| AI CLI（Codex、Claude、Gemini 等） | agent profile の既定値にする |

Issue 33 の Orca 対応は再利用しない。Orca relay の prebuilt バイナリは Orca の版と Node ABI に
依存し、sshd と公開ポートも base image／entrypoint／compose を複雑化する。最終的には
これらを削除して `docker exec` 経路へ一本化する。

**削除は Go 判定を待たず先行して実施した（本対応で撤去済み）。** 当初方針は「Phase 0 の Go 判定後に
独立 PR で削除」だったが、接続性インシデントと、sshd/公開ポートが base image／entrypoint／compose を
複雑化させ続ける維持コストを踏まえ、agent orchestration の実装とは**独立した削除 PR** を先行させた。
sshd/Orca 対応は稼働中のワークフロー（Windows Orca → Mac → コンテナへ SSH トンネル接続する運用）を
支えていたため、削除により Windows Orca 利用者は一時的に代替（tmux + FileSystemProvider）が未実装の
状態となる。この利用者には Remote-SSH への移行を案内する（§3.1）。tmux + FileSystemProvider の
feasibility spike は削除とは独立に Phase 0 で行う（§8 Phase 0）。理想は Go 判定後の削除だったという
教訓は残すが、本 doc は実施済みの判断（先行削除）に整合させている。

### 3.1 コンテナ SSH／Orca 対応の削除方針

削除対象は「コンテナへ入るための SSH server」とその Orca 専用連携である。ホスト自身への
Remote-SSH や、コンテナから Git host へ接続するための SSH client／`~/.ssh` は別機能なので残す。

| 削除対象 | 内容 |
|---|---|
| base image | `openssh-server`、Orca 用 sshd config、`/run/sshd` 準備 |
| entrypoint | `ENABLE_SSH` ブロック、host key 永続化、`authorized_keys` 展開、sshd 起動 |
| compose 生成 | container `:22` publish、SSH port 計算、SSH 専用 label |
| env | `ENABLE_SSH`、`SSH_AUTHORIZED_KEYS`、`DEVBASE_SSH_BIND`、`DEVBASE_SSH_PORT_BASE`、`DEVBASE_ORCA_HOSTNAME` |
| CLI | `devbase orca sync/prune/status`、up/down/scale の自動 sync hook |
| collector | Orca 接続鍵を収集する collector |
| relay | `.orca-remote` prebuilt COPY、Node ABI 固定理由、`README-orca-relay.md` |
| docs/tests | Orca 接続ガイド、README の Orca 対応、専用テスト |

`openssh-client`、`HOST_SSH_USER` / `HOST_SSH_HOST`、Issue 31 の
`DEVBASE_EDITOR_SSH_HOST` は目的が異なるため、一括削除しない。実装時に参照元を確認して判断する。

古い project env に残る Orca 用変数は warning を出して無視するか、release note で breaking change
として明示する。生成済みの `~/.config/devbase/orca/ssh_config` と `/persistent/ai/ssh` は
自動削除しない。ユーザーデータを勝手に消さず、不要なら削除できる手順だけを案内する。

#### 既存 Windows Orca 利用者の移行

sshd を削除すると、現行の「Windows Orca → SSH トンネル → コンテナ内 sshd」経路は
使えなくなる。この利用者に対する新方式の代替は、
**Windows の VS Code を Mac へ Remote-SSH し、その Mac host 上の Extension が Docker daemon を
`docker exec` する**構成である（§6.1）。これは「Orca をやめて VS Code + Remote-SSH に乗り換える」
運用変更であり、無視できない前提変更として扱う。

- Phase 0 の Go 条件に「Windows → Mac Remote-SSH 経由で `agent attach` が動く」ことを含める（§8）。
- 現行 Orca 運用から新方式への移行手順を breaking change note に紐付けて残す。
- sshd/Orca 経路は既に撤去済みのため、Windows Orca 利用者は代替 UI の完成を待たずに Remote-SSH
  への移行が必要になる。移行が完了するまで削除を保留する当初制約は、先行削除の判断により解除した。
  影響を受ける利用者へは breaking change note で Remote-SSH 移行手順を優先的に案内する。

## 4. 提案アーキテクチャ

```text
1つの VS Code window
├─ DEVBASE view: project > container > agent session
├─ Explorer: devbase://<project>/<index>/work/...
├─ Editor: 選択した container のファイル
└─ Terminal: container ごとの agent / shell（tab・split）
             |
             ├─ VS Code Extension
             └─ CLI / script: devbase agent ... --json
                         |
                  共通コマンド／状態モデル
                  lib/devbase/agent/
                         |
                 Docker API / docker exec
                         |
              devbase project container dev-N
                 ├─ /work（container ごとの named volume）
                 └─ tmux session
                      └─ codex / claude / gemini / shell
```

VS Code の Remote - Containers / Dev Containers は原則として「1 window = 1 remote authority」である。
その機能で container ごとに attach すると現在と同じく window が増えるため、新 UI では使用しない。
Extension 自身は Docker daemon を操作できる host、WSL、または Remote-SSH 先で動作する。

### 4.1 セッションの実体

コンテナへの到達手段は常に `docker exec` とする。ただし AI process 自体を foreground の
`docker exec` に直結すると、呼び出し側終了時の挙動、再接続、複数 viewer、scrollback の扱いが
難しい。そこで `docker exec` で container 内の tmux server を操作する。

```bash
docker exec <container> tmux new-session -d -s dbx-a1b2c3 -c /work/repo -- codex ...
```

- session ID はランダム ID とし、表示名とは分離する。
- `docker exec -it <container> tmux attach-session ...` で CLI／VS Code terminal から再接続する。
- `remain-on-exit` を有効にし、終了コードと末尾出力を確認できるようにする。
- `docker exec <container> tmux capture-pane ...` で bounded transcript を取得する。
- `docker exec <container> tmux send-keys -l ...` と Enter を別操作にし、文字列を shell command として再評価しない。
- tmux socket はコンテナごとに閉じ、共有 volume に置かない。異なるコンテナから同じ socket を
  共有すると PID namespace が違うため成立しない。
- `remain-on-exit` で残った dead pane は tmux server に蓄積するため、`prune` および起動時
  reconcile で終了コード取得後に kill し、ゴミ session を溜めない（§11）。

初期版ではコンテナ停止により session も停止する。コンテナ再作成後の「会話再開」は各 agent
CLI の resume 機能に依存し、devbase が PTY を永続化したように見せない。

### 4.2 状態モデル

最低限、次の状態に正規化する。

```text
starting -> running -> attention | idle -> exited
                     \-> unknown
```

初期判定:

- `starting`: tmux session 作成直後
- `running`: pane process が生存
- `exited`: pane dead。終了コードを保持
- `attention`: agent hook／OSC status が明示した場合
- `idle`: agent hook／OSC status が明示した場合
- `unknown`: container、tmux、metadata の一部が読めない場合

CPU 使用率や「一定時間出力がない」だけで idle／blocked を断定しない。Codex 等の OSC title や
公式 hook を使える profile から順に adapter を追加し、未対応 agent は running/exited の二値でも
正常動作する設計にする。

### 4.3 metadata

ホスト側の `~/.config/devbase/agents/sessions/<id>.json` に UI 用 metadata を atomic write する。
プロセスの生死は常に Docker + tmux を正とし、JSON だけを見て running と判断しない。

**session ごとに1ファイルとする**（単一 `sessions.json` にしない）。CLI と Extension が同時に
書き込むと、単一ファイルでは atomic rename で破損は防げても last-writer-wins で片方の更新が
消える（lost-update）。session 単位にファイルを分ければ書き込みが衝突せず、`prune` も
1ファイルの削除で済む。metadata は UI 補助情報であり真実は Docker/tmux という前提とも整合する。

主な項目:

```json
{
  "schemaVersion": 1,
  "id": "a1b2c3",
  "project": "sample",
  "containerIndex": 2,
  "profile": "codex",
  "title": "issue-34",
  "cwd": "/work/repo",
  "tmuxSession": "dbx-a1b2c3",
  "createdAt": "2026-07-16T00:00:00Z",
  "updatedAt": "2026-07-16T00:00:00Z",
  "lastKnownContainerId": "sha256:..."
}
```

container ID は再作成で変わるので永続キーにしない。`project + index` から現在の container を
Docker label で再解決する。`lastKnownContainerId` は誤 attach 検出のヒントに留め、判断の正には
使わない。

### 4.4 agent profile

コマンドを Extension 側へハードコードせず、CLI 側で profile として管理する。

```yaml
agents:
  codex:
    command: ["codex"]
  claude:
    command: ["claude"]
  gemini:
    command: ["gemini"]
```

配列形式で保持し、shell interpolation を避ける。permission bypass flag は Orca の既定をそのまま
採用せず opt-in にする。コンテナ分離は誤操作や認証情報流出まで防ぐ security boundary ではない。

## 5. CLI 版

### 5.1 コマンド案

```text
devbase agent list [--project NAME] [--json]
devbase agent start PROFILE --project NAME [--index N] [--cwd PATH]
                    [--title TITLE] [--prompt TEXT] [--attach]
devbase agent attach SESSION
devbase agent read SESSION [--lines N] [--json]
devbase agent send SESSION --text TEXT [--enter]
devbase agent stop SESSION [--force]
devbase agent open SESSION [PATH[:LINE]]
devbase agent prune
```

`list --json` を Extension と automation の公開契約にする。人間向け出力の parse は禁止する。
書き込み系は session ID の完全一致を原則とし、曖昧 prefix は一意の場合だけ許可する。

### 5.2 ターミナル切り替え／複数表示

- 1 session: `agent attach` → 対象 tmux session に接続。
- 複数 session: `agent dashboard`（第2段階）で選択後 attach。
- 同時表示: 一時的な viewer 用 tmux session をホストまたは選択 container に作り、各 pane から
  `tmux attach -t <target>` する。まずは利用者が tmux の split を使う形でもよい。

既存 questionary TUI に端末 emulator を埋め込むのは避ける。PTY rendering、resize、Unicode、
mouse、copy mode を独自実装することになり、Orca-like の中核より保守負担が大きい。

### 5.3 ファイルを開く

`agent open SESSION path:line` は session の project/index/cwd を解決し、Extension が登録した
`devbase:` URI を同じ window の editor に開く。

```text
devbase://sample/1/work/repository/src/main.py
```

CLI だけの環境ではファイル内容を標準出力へ流さず、container 内の絶対 path と、利用可能なら
host 側 editor で開くための案内を表示する。コンテナに SSH 接続する経路は設けない。

## 6. VS Code Extension 版

Extension は新規 `extensions/vscode-devbase/` に配置し、Python 内部 module を直接 import せず
`devbase agent ... --json` のみを呼ぶ。

### 6.1 MVP UI

- Activity Bar に `DEVBASE` container を追加。
- TreeView: `project > container > agent session`。
- session 行に状態、profile、経過時間を表示。
- container 行から `/work` のファイル一覧を展開し、選択したファイルを同じ editor area に開く。
- command: Start Agent、Attach Terminal、Open File、Show Changes、Stop、Refresh。
- 複数 session は VS Code の terminal tab と split terminal で表示。
- container/session を切り替えても VS Code window を新規作成しない。
- exited／attention の変化を `window.showInformationMessage` で通知。
- 表示中は短周期、非表示時は長周期の polling を使い、container と session の状態を更新する。

TreeView の例:

```text
DEVBASE
  sample (running)
    dev-1
      ● codex: issue-34       running
      ! claude: tests         attention
    dev-2
      ○ gemini: review        exited (0)
```

terminal は shell integration に依存せず、次のコマンドを起動するだけにする。

```text
devbase agent attach <session-id>
```

これにより local VS Code、WSL、Remote-SSH のどこで Extension が動く場合でも、既存 devbase CLI が
見ている Docker daemon に `docker exec` する。Extension manifest では
`extensionKind: ["workspace"]` を基本とし、Remote-SSH 上では remote extension host で動作させる。
これは「host へ Remote-SSH し、その host の Docker を操作する」構成であり、コンテナ内 sshd
への接続ではない。

### 6.2 ファイル操作

Extension は VS Code の `FileSystemProvider` を使い、`devbase:` scheme の読み書き可能な仮想
ファイルシステムを登録する。

```text
devbase://<project>/<container-index>/work/<path>
```

provider は URI から現在の container ID を Docker label で解決し、Docker 経由で次の操作を行う。

- `stat`、directory listing、read、write
- create、rename、delete
- container 停止／再作成の検出
- file change polling と `onDidChangeFile` 通知
- binary file と large file の上限／確認
- 元の mode、owner を壊さない atomic save

container ID は再作成で変わるため URI に含めない。`project + index` を永続的な authority とし、
各操作時に現在の container を再解決する。path traversal を防ぎ、既定では `/work` の外を公開しない。

同時に複数 container の root を VS Code workspace folder として常設すると検索・Git・補完の対象が
混ざるため、MVP は DEVBASE view から必要なファイルを開く方式にする。必要なら選択中 container の
root だけを workspace folder として差し替える機能を第2段階で追加する。

### 6.3 Git、差分、検索

VS Code 標準 Git extension は通常の disk path を前提とするため、仮想ファイルをそのまま渡すだけでは
完全には動作しない。Git command は対象 container 内で実行し、次の順で統合する。

1. MVP: `status --porcelain`、変更ファイル一覧、HEAD との差分表示。
2. 第2段階: VS Code Source Control API に変更一覧、stage、unstage、commit を接続。
3. 第3段階: branch、conflict、push、pull、PR 連携を必要に応じて追加。

ファイル検索も host 側 filesystem を直接検索せず、選択 container 内で `rg` を実行して結果の
`devbase:` URI を開く。全 container 横断検索は明示操作にし、通常の検索対象を不用意に10倍にしない。

### 6.4 言語機能の制約

仮想ファイルシステムでは、syntax highlight や通常の編集は VS Code 本体で利用できる。一方、disk path
や container 内 executable を前提とする language extension、Language Server、debugger はそのままでは
動かない場合がある。MVP ではこの制約を明示し、次の順で対応する。

1. file edit、terminal、Git diff、検索を先に完成させる。
2. 利用頻度の高い言語を調査し、仮想 URI 対応済み extension はそのまま利用する。
3. container 内 Language Server との中継が必要な言語だけ adapter を追加する。

この制約を回避するために container ごとの別 window attach へ自動 fallback すると、今回の目的に反する。
別 window で開く操作は明示的な互換 command としてのみ残す。

**この trade-off は本設計の成否を分ける中心論点である。** 現行の Dev Containers attach は完全な
LSP・補完・go-to-def・デバッグが効く。本提案は「単一 window」と引き換えにそれを失う。解決対象が
「window 切替の負担」である以上、「LSP を捨ててまで単一 window にする価値があるか」を実装判断の
前に評価する必要がある。§13 と §11 のリスク表でもこの点を正面から扱い、次の代替案と比較する。

- **代替案（低コスト）:** 仮想 filesystem を作らず、Dev Containers attach は残したまま、
  window を新規生成せず既存 window に focus する dashboard を提供する。これなら
  FileSystemProvider・Git 中継・検索中継という最も高コストな部分を丸ごと回避でき、LSP も維持できる。
  「単一 window での編集」は諦めるが、「window 切替負担の軽減」という本来の目的は満たしうる。
- Phase 0 spike の結果（仮想 FS の編集体験・LSP の欠落度）を見て、フル仮想 FS 路線とこの
  dashboard 路線のどちらを主とするかを判断する。

## 7. worktree の位置付け

devbase の現行 scale は「コンテナごとの作業領域」を提供するため、MVP の並列 agent 分離には使える。
ただし複数 container が同じ repository path／volume を共有する plugin では agent 同士が衝突しうる。
実装前に plugin ごとの mount を検査し、「1 container = 独立 checkout」が成立しない場合は警告する。

第2段階で次を追加する。

```text
devbase worktree create <name> --project P --index N --from REF
devbase worktree list --json
devbase worktree remove <id>
```

配置先は repository の mount 範囲内に限定し、container から作った sibling worktree が host／再作成後も
見えることを結合テストする。自動 branch 削除はデータ損失リスクがあるため、既定では行わない。

## 8. 実装フェーズ

### Phase 0: feasibility spike

**この Phase では代替（tmux + FileSystemProvider）の feasibility のみを検証する。** sshd／Orca 経路の
削除は、当初「Go 判定後に独立 PR で」実行する方針だったが、接続性インシデント等を機に**先行して
実施済み**（§3.1）。したがって本 spike は削除の可否を判断するものではなく、撤去後の代替経路が
成立するかを確認するものである。

- base image に `tmux` を追加して arm64/amd64 で確認（この `tmux` 追加は Phase 0/1 の作業であり、
  本削除 PR には含まれない。追加なので既存機能に影響しない）。
- Codex／Claude／Gemini を detached 起動し、attach、detach、resize、capture、send、exit code を確認。
- container stop/restart/recreate の境界を文書化。
- VS Code local、WSL、Remote-SSH から `agent attach` を terminal で実行確認。
  特に **Windows → Mac Remote-SSH 経由**で動くことを既存 Windows Orca 利用者の移行条件として確認（§3.1）。
- 最小 `FileSystemProvider` で named volume 内の text/binary file を同じ VS Code window から
  read/write/rename し、container 再作成後も `project + index` で再解決できることを確認。
- 仮想 FS 上での LSP 欠落度を実測し、§6.4 の「フル仮想 FS」路線と「dashboard」路線のどちらを
  主とするか判断する材料を得る。

**Go 条件:** 3 agent で detach 後も継続し、再 attach と終了コード取得が安定すること。加えて、
別 window を開かず2つ以上の container のファイルを同じ editor area で安全に編集できること。

**削除との関係:** sshd／Orca relay の削除（§3.1）は本 spike の結果を待たず先行実施済みである。
そのため Phase 0 は「削除の Go/No-Go 判定」ではなく、撤去後の代替（tmux + FileSystemProvider）が
feasible かを確認する検証に位置づけが変わった。spike が成立しない場合でも sshd/Orca は復活させず、
Windows 利用者向けには Remote-SSH 経由の attach を代替経路として維持しつつ設計を見直す。base build と
既存 project の回帰、および Windows 利用者の Remote-SSH 移行状況は継続して確認する。

### Phase 1: 共通 CLI MVP

**中核価値（永続 session + 横断状態一覧）は、この Phase 1 だけでほぼ得られる。** コストが跳ね上がる
のは Phase 2 の `FileSystemProvider` + Git 中継 + 検索中継である。したがって Phase 1 完了時点で
一度価値を確定し、Phase 2 の投資判断を段階的に下せるようにする。

- `lib/devbase/agent/` に model、Docker resolver、tmux adapter、metadata store（session 単位
  ファイル、§4.3）を追加。
- `agent list/start/attach/read/send/stop/prune` と `--json` schema を実装。
- base image への `tmux` 追加は Phase 0 で完了済み（前提）。
- agent profile と安全な argv 構築を実装。
- 単体テストと Docker 結合テストを追加。

### Phase 2: VS Code Extension MVP

- scaffold、DEVBASE TreeView、commands、terminal attach、polling、通知を実装。
- `devbase:` FileSystemProvider の stat/list/read/write/create/rename/delete/watch を実装。
- 同じ window で container/session/file を切り替える navigation と shortcut を実装。
- container 内 `git status` と diff viewer、`rg` file search を実装。
- CLI version／schema version の互換チェックを実装。

### Phase 3: orchestration 強化

- worktree lifecycle。
- Source Control API、stage/unstage/commit。
- 必要な言語向けの container Language Server adapter。
- agent hook／OSC による attention/idle。
- activity feed、unread、完了通知の永続化。
- Quick Command、prompt template、複数 session 一括起動。
- 必要性を確認後、diff summary／GitHub issue 連携を検討。

## 9. 変更ファイル案

| パス | 内容 |
|---|---|
| `containers/base/Dockerfile` | sshd／Orca relay 削除（本 PR で実施済み）。`tmux` 追加は Phase 0/1 の別作業でありこの削除 PR には含まない |
| `containers/base/entrypoint.sh` | `ENABLE_SSH` と sshd 起動処理を削除 |
| `lib/devbase/commands/orca.py` | 削除 |
| `lib/devbase/volume/ports.py` | 削除（内容は SSH publish 専用。利用元は `compose.py` の `allocate_ssh_host_port` のみと確認済み） |
| `lib/devbase/volume/compose.py` | SSH port publish／専用 label を削除 |
| `lib/devbase/env/collectors/orca.py` | 削除 |
| `lib/devbase/agent/models.py` | session/profile/status model |
| `lib/devbase/agent/docker.py` | label ベースの container 解決 |
| `lib/devbase/agent/tmux.py` | start/attach/capture/send/stop |
| `lib/devbase/agent/store.py` | schema version 付き atomic metadata |
| `lib/devbase/commands/agent.py` | CLI command 実装 |
| `lib/devbase/cli.py`, `bin/devbase` | dispatcher、completion |
| `extensions/vscode-devbase/` | TreeView、terminal、仮想 filesystem、Git diff、検索 |
| `tests/agent/` | 純粋関数・subprocess adapter の単体テスト |
| `tests/integration/` | Docker + tmux の opt-in 結合テスト |
| `docs/user/agents.md` | 利用手順、制約、troubleshooting |

## 10. テスト観点

- project 名／title／cwd に空白、Unicode、`-` があっても argv injection しない。
- prompt に改行、引用符、shell metacharacter があっても `send-keys -l` でそのまま送られる。
- 同じ project の scale 1..N を label から正しく識別する。
- container 再作成後に古い session を orphan/exited として表示し、誤 attach しない。
- 2 UI が同じ session を表示しても metadata を破損しない。
- CLI の human output を変えても JSON schema が維持される。
- Extension が CLI 不在、旧 version、Docker停止、container停止時に明確に縮退する。
- terminal resize、CJK/絵文字、Ctrl-C、detach key が VS Code／CLI 双方で動く。
- stop は通常終了を先に試し、明示的な `--force` なしに container 全体を停止しない。
- API key、prompt、terminal transcript を log／notification に無制限に出さない。
- `devbase:` URI の `..`、symlink、percent encoding で `/work` の外へ脱出できない。
- text/binary/large file の read/write、atomic save、rename、delete、permission 維持。
- 2 container で同名 path を同時に開いても content/event が混線しない。
- container 再作成後、開いている URI が新しい container ID を安全に再解決する。

## 11. リスクと対策

| リスク | 対策 |
|---|---|
| tmux と agent TUI の keybinding 衝突 | prefix を既定のままにせず devbase 専用設定を検証。attach の escape を文書化 |
| agent ごとに状態通知方式が違う | adapter 化し、未対応は running/exited へ安全に縮退 |
| container と session metadata の不整合 | Docker/tmux を正とし、`prune` と起動時 reconcile を実施 |
| Extension と CLI schema の不一致 | `schemaVersion` と `devbase agent capabilities --json` を用意 |
| prompt の shell injection | argv 配列、stdin、`send-keys -l` を使用。`sh -c` 連結を避ける |
| permission bypass による破壊 | profile 既定では付与せず、ユーザーが project 単位で opt-in |
| VS Code 依存が強い | session 制御は CLI に保ち、仮想 filesystem 等の表示機能だけを Extension の責務にする |
| 仮想 filesystem で一部 extension が動かない | MVP の対応範囲を明示し、頻出言語だけ container Language Server adapter を追加 |
| **LSP／デバッグ喪失が単一 window の目的と衝突** | 本設計の中心論点。Phase 0 で欠落度を実測し、フル仮想 FS 路線と「Dev Containers attach を残し既存 window に focus する dashboard」路線を比較（§6.4） |
| **CLI と Extension の同時書き込みで metadata lost-update** | 単一 JSON をやめ session 単位ファイルにして書き込み衝突を回避。真実は Docker/tmux（§4.3） |
| 既存 Windows Orca 利用者の運用断絶 | sshd 削除を先行実施したため、代替 UI 完成前でも Remote-SSH 移行が必要。breaking change note で移行手順を優先案内し、Remote-SSH 経由の attach を Phase 0 Go 条件に含める（§3.1） |
| Go 判定前に既存機能を削除した（後戻りコスト） | 当初は Go 判定後の独立 PR に分離し spike 失敗時は削除しない方針だったが、接続性インシデントと維持コストを機に先行削除。spike 失敗時も sshd は復活させず Remote-SSH 経路で縮退（§8） |
| Docker 経由の file I/O が遅い | directory cache、差分更新、size 上限、bounded polling。計測してから最適化 |
| Orca と重複開発になる | Monaco 自体は VS Code を使い、filesystem/Git/terminal の中継に限定 |

## 12. 受け入れ条件

受け入れ条件を Phase に対応づけ、どの段階で何が満たされるべきかを明確にする。

### Phase 0（feasibility spike の Go 判定）

- [ ] 3 agent（Codex／Claude／Gemini）を detach 後も継続でき、再 attach と終了コード取得が安定する。
- [ ] 別 window を開かず、2つ以上の container のファイルを同じ editor area で安全に編集できる。
- [ ] Windows → Mac Remote-SSH 経由で `agent attach` が動く（既存 Windows Orca 利用者の移行条件）。
- [ ] 仮想 FS 上の LSP 欠落度を実測し、フル仮想 FS 路線／dashboard 路線の判断材料が揃っている。

### Phase 1（共通 CLI MVP）

- [ ] scale された任意の dev container で Codex／Claude／Gemini／shell を起動できる。
- [ ] CLI を閉じても agent が継続し、別端末から再 attach できる。
- [ ] 全 project/container/session の running/exited 状態を CLI JSON で確認できる。
- [ ] session を graceful stop でき、終了コードと bounded transcript を取得できる。
- [ ] CLI／Docker／container が利用不能な場合に明確に縮退する。

### Phase 2（VS Code Extension MVP）

- [ ] 全 project/container/session の状態を Extension でも確認できる。
- [ ] **すべての標準操作が1つの VS Code window 内で完結し、container 切替で新しい window が開かない。**
- [ ] VS Code で2つ以上の session terminal を tab／split 表示し切り替えられる。
- [ ] 2つ以上の container の `/work` を `devbase:` URI で参照し、同じ editor area で編集・保存できる。
- [ ] 選択した container 内の変更ファイル一覧、diff、ファイル検索を同じ window で利用できる。
- [ ] Docker／container／CLI が利用不能な場合に Extension がクラッシュせず理由を表示する。

### 全体（回帰・削除）

- [ ] 既存 `devbase up/login/list` と Issue 31 の editor open に回帰がない。
- [x] （先行削除 PR で実施済み）base image／entrypoint／生成 compose に sshd、Orca relay、
      container `:22` publish が残っていない。

## 13. 最終判断

**実装する価値はあるが、Orca のクローンを目標にしない。** 解決対象は「常時10前後ある VS Code
window の切替負担」である。標準 UI は1つの window とし、そこへ「永続 agent session」
「横断状態一覧」「仮想 filesystem」「terminal」「Git diff／検索」を統合する。

最初の着手は **Phase 0 の tmux と FileSystemProvider の両 spike**とする。tmux だけ成功しても、
単一 window で安全に file edit できなければ目的を達成できない。両方の成立を確認後に JSON CLI と
Extension を実装する。Extension は単なる launcher ではなく、単一 window を成立させる中核 client
として扱う。

ただし2つの前提条件を実装判断の前に確定させる。

1. **LSP／デバッグ喪失の許容度。** 単一 window（仮想 FS）は Dev Containers attach が持つ完全な
   LSP・補完・デバッグを失う。これが「window 切替負担の軽減」という目的に見合うかを Phase 0 で
   実測し、見合わなければ「attach を残し既存 window に focus する dashboard」路線（§6.4）へ
   切り替える。単一 window は手段であって目的ではない。
2. **破壊的削除は先行実施済み（当初は Go 判定後の方針）。** sshd／Orca 対応は稼働中の Windows 運用を
   支えていたが、接続性インシデントと維持コストを機に、代替の feasibility 確認を待たず独立 PR で
   先行削除した。理想は Go 判定後の削除だったが、この判断により Windows Orca 利用者には代替 UI 完成
   前でも Remote-SSH 移行を案内する。spike 失敗時も sshd は復活させず Remote-SSH 経路で縮退する。
