# PLAN33: devbase の Orca 対応（コンテナを SSH target として接続可能にする）

## 関連リンク

- 元 issue: `issues/i33.md`（Orca 対応）
- 参考アドバイス: `issues/i33-advice.md`（ChatGPT による構成案）
- Orca 公式:
  - SSH worktrees — https://www.onorca.dev/docs/ssh
  - Work on a remote machine over SSH（recipe）— https://www.onorca.dev/docs/recipes/remote-worktrees
  - Remote Orca Servers — https://www.onorca.dev/docs/remote-servers
  - file transfer over ProxyCommand の open issue — https://github.com/stablyai/orca/issues/7781

## 概要

Orca（https://www.onorca.dev/）から devbase が起動したコンテナへ接続し、コンテナ内で
`git worktree` / AI エージェント CLI（claude / codex / gemini など）を動かせるようにする。

Orca の remote 開発モデルは **「SSH target 上に worktree を作り、agent も SSH target 側で
動かし、editor/diff は手元で使う」** である（SSH worktrees）。したがって devbase コンテナを
**単純な `HostName + Port` の SSH host** として Orca に見せるのが最も素直で堅牢な構成となる。

`docker exec` を terminal だけコンテナに入れる方式や `ProxyCommand docker exec ... sshd -i`
方式は、Orca の file explorer / diff / worktree 管理がホスト側を見てしまう・SFTP が使えない
（上記 open issue）などの制約があるため **採らない**（`issues/i33-advice.md` の結論に従う）。

実現するのは次の 3 層:

```text
Laptop (Windows / macOS)
  └─ Orca
      └─ SSH target: devbase-<project>-<index>   ← Orca は普通の SSH host として認識
            └─ macOS 上の Docker container (devbase)
                 ├─ sshd (:22 → host 127.0.0.1:<port> に publish)
                 ├─ git / claude / codex / gemini（既存）/ worktree
                 └─ repo (/work, /workspaces)
```

### やりたいこと（issue より）と本 plan の対応

| issue の要望 | 本 plan での実現方法 |
|---|---|
| base イメージに ssh 等をインストール | PR1: `openssh-server` を base に追加、`sshd_config` を Orca 向けに設定 |
| devbase 起動時に Orca の接続先へ追加 | PR3: `devbase up` 時に **Orca 専用の隔離 SSH config** を生成／更新 |
| `~/.ssh/config` を編集/include/配布のいずれか検討 | PR3: **ホストの `~/.ssh/config` は触らず**、専用ファイル `~/.config/devbase/orca/ssh_config` を生成し Orca にはそれだけを import させる（下記「設計判断」） |
| Orca から他の SSH ホストを見せたくない | 上記のとおり **専用ファイル方式**で自然に隔離（`~/.ssh/config` を混ぜない） |
| Windows → macOS 上コンテナへ接続 | PR3/PR4: publish 先を `127.0.0.1:<port>` にし、Windows からは SSH トンネル or Tailscale で到達。HostName は設定で切替可能 |
| 外出先で macOS 上の Orca から操作 | 同一 Mac 上なら Docker Desktop が `127.0.0.1:<port>` を公開するため直結可能 |

## 設計判断（確定事項）

| 論点 | 決定 | 理由 |
|---|---|---|
| Orca への見せ方 | コンテナ内 `sshd` を publish して単純な SSH host にする | Orca の SSH worktree モデルと相性最良。`docker exec` / `ProxyCommand` 方式は Orca の file/diff/worktree がコンテナを向かない・SFTP 不可（advice の結論） |
| `~/.ssh/config` の扱い | **編集しない。専用ファイルを別途生成し Orca にだけ import させる** | 「Orca から他の SSH ホストを見せたくない」を満たす唯一の隔離手段。`Include` はメイン config にマージされ Orca が全ホストを読むため不採用 |
| 隔離 config の置き場所 | `~/.config/devbase/orca/ssh_config`（ホスト側） | devbase 管理の単一ファイル。Orca は Settings→SSH で OpenSSH config を import できるためこれだけを渡す |
| sshd の起動制御 | env `ENABLE_SSH=true` のとき entrypoint で起動（DinD と同じ opt-in パターン） | 全コンテナで無条件に sshd を上げない。既存 `ENABLE_DIND` の実装に倣う |
| 認証方式 | 公開鍵認証のみ（`PasswordAuthentication no` / `PubkeyAuthentication yes`） | パスワード認証は無効。laptop の公開鍵を authorized_keys に登録 |
| authorized_keys の供給 | env `SSH_AUTHORIZED_KEYS`（複数行可）を entrypoint で `~/.ssh/authorized_keys` へ展開 | 既存の env 収集機構・`.ssh` 永続化（`/persistent/ai/.ssh`）と整合。`devbase env init` で収集 |
| host key の永続化 | sshd host key を `/persistent/ai/ssh/` に生成・永続化し entrypoint で `/etc/ssh` へ復元 | 再ビルド/再作成で host key が変わると Orca 側 known_hosts が壊れるのを防ぐ |
| publish の bind 先 | 既定 `127.0.0.1`（外部非公開）。env で上書き可 | 安全寄り。Windows からはトンネル/Tailscale で到達させる |
| ポート割当 | プロジェクト+index から決定的に算出（既定 base `2200` + オフセット）。env で base 変更可 | 複数プロジェクト/scale で衝突しない。`down`→`up` で同じポートに戻る |
| Orca CLI / `orca serve`（Remote Orca Server） | **本 plan では対象外（将来検討）** | 一人開発では sshd + SSH worktree の方がシンプル（advice の推奨）。まず primary 経路を通す。base への orca CLI 追加は PR1 で導入可否のみ調査 |
| TcpForwarding | `AllowTcpForwarding yes` | Orca の Ports tab による remote port forward / preview を効かせるため |

## アーキテクチャ整合

### 既存機構との対応

- **base イメージ**: `containers/base/Dockerfile`。APT で `openssh-server` を追加。DinD 用
  `dind` ラッパや entrypoint の DinD 起動と同じ「opt-in で常駐プロセスを起動」パターンを踏襲。
- **entrypoint**: `containers/base/entrypoint.sh`。既存の DinD ブロック（`ENABLE_DIND`）と AI
  設定 symlink ブロックに倣い、`ENABLE_SSH` ブロックと authorized_keys/host key 復元を追加。
  `.ssh` は既に `/persistent/ai/.ssh` へ symlink 済み（AI_SETTINGS 配列）。
- **ポート publish**: `lib/devbase/volume/compose.py` の `_build_dev_instance()` が
  scale 生成時に各 `dev-<index>` サービス定義を作る唯一の箇所。ここへ
  `ports: ["<bind>:<host_port>:22"]` を注入する。
- **up/down フック**: `lib/devbase/commands/container.py` の `cmd_up()`（`[5/6]` 完了後）と
  `cmd_down()`。ここから Orca config の同期/剪定を呼ぶ。
- **新規コマンド**: `devbase orca ...`（config の手動同期・状態確認）。`bin/devbase` の
  ディスパッチ（`resolve_command` の commands 一覧）と Python parser に追加。
- **env keys**: `lib/devbase/env/keys.py` に `ENABLE_SSH` / `SSH_AUTHORIZED_KEYS` /
  `DEVBASE_SSH_BIND` / `DEVBASE_SSH_PORT_BASE` / `DEVBASE_ORCA_HOSTNAME` を追加。

```mermaid
flowchart TD
    subgraph host["ホスト (macOS)"]
      up["devbase up"] --> gen["generate_scaled_compose()<br/>_build_dev_instance で<br/>127.0.0.1:port:22 を publish"]
      gen --> compose["docker compose up"]
      up --> orcasync["orca config sync<br/>~/.config/devbase/orca/ssh_config"]
      down["devbase down"] --> orcaprune["orca config prune"]
    end
    subgraph ctr["container (devbase)"]
      entry["entrypoint.sh<br/>ENABLE_SSH=true"] --> sshd["sshd :22<br/>authorized_keys / host key 復元"]
    end
    compose --> entry
    subgraph laptop["Laptop の Orca"]
      import["Settings→SSH に<br/>ssh_config を import"] --> target["SSH target:<br/>devbase-project-1"]
    end
    orcasync -. import .-> import
    target -. 127.0.0.1:port (直結 or トンネル/Tailscale) .-> sshd
```

## 変更ファイル（PR 別）

### PR1: base イメージへ sshd 追加

| ファイル | 種別 | 内容 |
|---|---|---|
| `containers/base/Dockerfile` | 変更 | APT に `openssh-server` 追加。`/etc/ssh/sshd_config.d/` に Orca 向け設定（Pubkey 認証・Password 無効・TcpForwarding 有効）を配置。`/run/sshd` 作成 |
| `containers/base/entrypoint.sh` | 変更 | `ENABLE_SSH` ブロック追加: host key を `/persistent/ai/ssh/` から復元 or 生成、`SSH_AUTHORIZED_KEYS` を `~/.ssh/authorized_keys` へ展開、sshd 起動 |
| `docs/user/*`（該当章） | 変更 | base ツール一覧に openssh-server / SSH 有効化手順を追記 |

> ⚠ base の Dockerfile / entrypoint 変更は `devbase up` では反映されず
> **`devbase build --no-cache`（base 再ビルド）が必要**（`memory: entrypoint-change-needs-rebuild`）。
> PR の Test plan / release PR の検証手順に明記する。

### PR2: コンテナ SSH ポートの publish

| ファイル | 種別 | 内容 |
|---|---|---|
| `lib/devbase/env/keys.py` | 変更 | `ENABLE_SSH` / `DEVBASE_SSH_BIND` / `DEVBASE_SSH_PORT_BASE` 定数追加 |
| `lib/devbase/volume/compose.py` | 変更 | `_build_dev_instance()` で `ENABLE_SSH` 時に `ports: ["<bind>:<port>:22"]` を注入。ポート算出ヘルパ追加 |
| `lib/devbase/volume/ports.py`（新規 or util 内） | 新規 | プロジェクト名+index → host port の決定的算出（`DEVBASE_SSH_PORT_BASE` 起点） |
| `tests/volume/test_compose_ssh_ports.py` | 新規 | ENABLE_SSH 有無・bind・ポート決定性・衝突回避の単体テスト |

### PR3: ホスト側 Orca SSH config の生成・隔離・up/down 連携

| ファイル | 種別 | 内容 |
|---|---|---|
| `lib/devbase/env/keys.py` | 変更 | `SSH_AUTHORIZED_KEYS` / `DEVBASE_ORCA_HOSTNAME` 追加 |
| `lib/devbase/commands/orca.py`（新規） | 新規 | `devbase orca sync` / `devbase orca prune` / `devbase orca status`。稼働中コンテナと publish ポートを解決し `~/.config/devbase/orca/ssh_config` を生成/剪定 |
| `lib/devbase/commands/container.py` | 変更 | `cmd_up()` 完了後に orca sync、`cmd_down()` で prune を呼ぶ（失敗しても本処理は止めない） |
| `lib/devbase/cli.py` / `bin/devbase` | 変更 | `orca` サブコマンドをディスパッチ（`resolve_command` の commands 一覧と parser） |
| `lib/devbase/env/collectors/orca.py`（新規, 任意） | 新規 | `devbase env init` で公開鍵（`~/.ssh/id_ed25519.pub` 等）を `SSH_AUTHORIZED_KEYS` として収集 |
| `tests/commands/test_orca.py` | 新規 | config 生成内容・隔離（他ホスト非混入）・prune・非稼働時の扱いの単体テスト |

### PR4: ドキュメント（接続手順）

| ファイル | 種別 | 内容 |
|---|---|---|
| `docs/user/orca.md`（新規） | 新規 | Orca 接続ガイド: base 再ビルド → `env init`（公開鍵）→ `ENABLE_SSH=true` で `up` → `devbase orca sync` → Orca に import → worktree 作成。Windows(トンネル/Tailscale) / macOS(直結) の両ケース、Ports tab、トラブルシュート（`SFTP is not available` を避ける理由）|
| `README.md` / `docs/README.md` | 変更 | Orca 対応の紹介と `docs/user/orca.md` への導線 |

## 実装詳細

### PR1: sshd（base + entrypoint）

**Dockerfile**（APT 行へ `openssh-server` を追加し、設定ファイルを配置）:

```dockerfile
# sshd 設定（Orca 向け: 公開鍵のみ・TcpForwarding 有効）
RUN set -eux; \
    mkdir -p /run/sshd /etc/ssh/sshd_config.d; \
    printf 'PasswordAuthentication no\nPubkeyAuthentication yes\nAllowTcpForwarding yes\nX11Forwarding no\nPermitRootLogin no\n' \
      > /etc/ssh/sshd_config.d/10-devbase-orca.conf
```

**entrypoint.sh**（`ENABLE_DIND` ブロックの近くに追加。`exec "$@"` の前）:

```bash
# SSH server (Orca 連携) — enabled by ENABLE_SSH=true
if [ "$ENABLE_SSH" = "true" ] || [ "$ENABLE_SSH" = "1" ]; then
    echo "Starting sshd for Orca..."
    HOST_KEY_DIR="/persistent/ai/ssh"
    sudo mkdir -p "$HOST_KEY_DIR"
    # host key を永続領域から復元、無ければ生成して保存（Orca の known_hosts 破壊防止）
    if ! ls "$HOST_KEY_DIR"/ssh_host_*_key >/dev/null 2>&1; then
        sudo ssh-keygen -A -f /tmp/hk >/dev/null 2>&1 || sudo ssh-keygen -A
        sudo cp /etc/ssh/ssh_host_*_key* "$HOST_KEY_DIR"/ 2>/dev/null || true
    fi
    sudo cp "$HOST_KEY_DIR"/ssh_host_*_key* /etc/ssh/ 2>/dev/null || true
    # authorized_keys（.ssh は /persistent/ai/.ssh に symlink 済み）
    if [ -n "$SSH_AUTHORIZED_KEYS" ]; then
        mkdir -p ~/.ssh && chmod 700 ~/.ssh
        printf '%s\n' "$SSH_AUTHORIZED_KEYS" > ~/.ssh/authorized_keys
        chmod 600 ~/.ssh/authorized_keys
    fi
    sudo /usr/sbin/sshd -e
    echo "sshd started"
fi
```

（host key 生成コマンド・権限まわりは実装時に実機検証。`ssh-keygen -A` は既存 key を上書き
しないため冪等。）

### PR2: ポート publish（`_build_dev_instance`）

```python
# ports.py（決定的ポート算出）
def ssh_host_port(project_name: str, index: int, base: int) -> int:
    # project 名のハッシュ下位 + index で base からのオフセットを決める。
    # 同じ (project, index) は常に同じポートに解決する。
    offset = (_stable_hash(project_name) % 100) * 10 + (index - 1)
    return base + offset
```

`_build_dev_instance()` 内（`ENABLE_SSH` が有効なときのみ）:

```python
if _ssh_enabled():
    bind = os.environ.get('DEVBASE_SSH_BIND', '127.0.0.1')
    base = int(os.environ.get('DEVBASE_SSH_PORT_BASE', '2200'))
    port = ssh_host_port(os.environ['COMPOSE_PROJECT_NAME'], index, base)
    service.setdefault('ports', []).append(f"{bind}:{port}:22")
```

- `_build_dev_instance` は現状 `project_name` を受け取らないため、`COMPOSE_PROJECT_NAME`
  env（wrapper が設定済み）を参照するか、シグネチャに project 名を通す（後者が明示的で望ましい）。
- ポート衝突は「別プロジェクトが既に同ポートを publish していないか」を up 前に検査し、
  衝突時は次の空きへずらす（もしくは警告）。テストで決定性と衝突回避を担保。

### PR3: Orca config 同期（隔離ファイル）

`~/.config/devbase/orca/ssh_config` を **devbase 管理ブロック**として全生成（毎回上書き）:

```sshconfig
# Managed by devbase — do not edit. Import this file into Orca (Settings → SSH).
Host devbase-carmo-1
  HostName 127.0.0.1
  Port 2231
  User ubuntu
  IdentityFile ~/.ssh/id_ed25519
  StrictHostKeyChecking accept-new

Host devbase-carmo-2
  HostName 127.0.0.1
  Port 2232
  ...
```

- `HostName` は既定 `127.0.0.1`。`DEVBASE_ORCA_HOSTNAME`（Tailscale 名 / Mac の LAN IP）で上書き可
  → Windows から Tailscale/LAN 直結する構成に対応。
- 稼働中コンテナと publish ポートは `docker compose ps` / compose override（`.docker-compose.scale.yml`）
  から解決。`cmd_up` 直後は override ファイルが存在するため確実。
- **他の SSH ホストは一切書かない**ため、Orca にこのファイルを import すれば devbase コンテナ
  だけが見える（issue の隔離要件を満たす）。
- `devbase orca sync`: 全プロジェクト横断で稼働中コンテナを集約して再生成。
  `devbase orca prune`: 停止済みエントリを除去（`down` から呼ぶ）。
  `devbase orca status`: 現在の import 対象一覧と Orca への登録手順を表示。

### 認証情報の流れ（authorized_keys）

```text
laptop: ~/.ssh/id_ed25519.pub
  → devbase env init (collectors/orca.py) が SSH_AUTHORIZED_KEYS に格納 → .env
    → entrypoint が ~/.ssh/authorized_keys に展開（/persistent/ai/.ssh に永続化）
      → Orca (同じ id_ed25519 で接続) が公開鍵認証で入る
```

## テスト計画

### 単体テスト（各 PR）

- PR2 `tests/volume/test_compose_ssh_ports.py`:
  - `ENABLE_SSH` 無効時に `ports` が注入されない
  - 有効時に `127.0.0.1:<port>:22` が各 `dev-<index>` に付く
  - `DEVBASE_SSH_BIND` / `DEVBASE_SSH_PORT_BASE` の反映
  - 同一 (project, index) が常に同一ポート（決定性）／別 project で非衝突
- PR3 `tests/commands/test_orca.py`:
  - 生成 config に devbase ホストのみ含まれ、他ホストが混入しない（隔離）
  - `HostName` の env 上書き
  - prune で停止エントリが消える／稼働エントリは残る
  - 稼働コンテナ 0 のとき空（or ヘッダのみ）を安全に生成
- 既存一式の回帰（`pytest tests/`）。

### 結合（release ブランチ / 手動・実機）

個別 PR では検出できない End-to-End をここで確認する:

- [ ] `devbase build --no-cache`（base 再ビルド）後に sshd 入りイメージができる
- [ ] `ENABLE_SSH=true` で `devbase up` → `ssh -p <port> ubuntu@127.0.0.1 'whoami; git --version'` が通る
- [ ] `ssh ... 'claude --version || codex --version || gemini --version'` が通る
- [ ] `devbase orca sync` 生成ファイルを Orca に import → SSH target Test 成功
- [ ] Orca で SSH target を location に repo/worktree 作成 → `git worktree add` がコンテナ側で走る
- [ ] scale 2 で 2 つの target が別ポートで登録され、両方接続できる
- [ ] `devbase down` 後に `devbase orca` エントリが prune される
- [ ] host key 永続化: 再ビルド後も Orca の known_hosts 警告が出ない
- [ ] Windows→Mac: `ssh -L <port>:127.0.0.1:<port> mac-host` トンネル経由で Orca 接続成功
- [ ] （Tailscale 構成）`DEVBASE_ORCA_HOSTNAME=<tailscale>` で Windows から直結成功

## 受け入れ条件（issue より）

- [ ] base コンテナイメージに sshd（+ 必要な SSH ツール）がインストールされている
- [ ] `devbase up` で起動したコンテナへ Orca から SSH 接続でき、worktree/agent がコンテナ内で動く
- [ ] devbase 起動時に Orca の接続先（隔離 SSH config）へコンテナが追加される
- [ ] Orca からは devbase コンテナ以外の `~/.ssh/config` のホストが見えない（隔離）
- [ ] Windows(手元)→ macOS 上コンテナへ Orca から接続できる手順が用意されている
- [ ] macOS 上の Orca からも同一手順で接続できる
- [ ] base 変更の反映に `build --no-cache` が要る旨がドキュメント化されている

## PR 分割計画

| PR # | branch | 概要 | 依存 | 並行可否 |
|---|---|---|---|---|
| 1 | `feature/PLAN33-base-sshd` | base に openssh-server + entrypoint の sshd 起動 | なし | ○ |
| 2 | `feature/PLAN33-port-publish` | compose 生成で SSH ポート publish + ポート算出 | なし（PR1 と概念依存のみ） | ○（mock で並行可）|
| 3 | `feature/PLAN33-orca-config` | ホスト側 Orca 隔離 config + up/down 連携 + `devbase orca` | PR2（ポート解決） | △（PR2 merge 後が安全）|
| 4 | `feature/PLAN33-docs` | 接続ガイド（Windows/macOS）+ README 導線 | なし | ○ |

```text
release branch: release/PLAN33
base branch:    main
```

- 個別 PR は `/ndf:cross-review` でセルフレビュー → release へ squash merge。
- release ブランチで **結合観点のみ**（上記 E2E チェックリスト）を実機/手動で検証。
- release PR body は self-contained（背景=Orca からコンテナ接続したい / 変更内容=sshd + publish +
  隔離 config + docs）で記述し、個別 PR は `<details>` 内の開発用情報に留める。

## 未確定・要判断（実装前に確認したい点）

- **Orca CLI / `orca serve`（Remote Orca Server）** を base に入れるか: 本 plan は primary の
  sshd + SSH worktree のみ対象。将来 `orca serve` 経路を足す場合は別 issue/PLAN で。
- **User 名**: コンテナのログインユーザーは `ubuntu`（`USERNAME` ARG 既定）。Orca config の
  `User` もこれに合わせる。プロジェクトが `USERNAME` を変える場合の追随は PR3 で吸収。
- **ポート base 既定値 `2200`** と算出方式（ハッシュ）で十分か、衝突検査の厳密さ。
