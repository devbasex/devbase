#!/bin/bash

set -e

# ===================================================================
# PLAN32: 複数リポジトリの clone / workspace 生成
# ===================================================================
# ホスト側 (devbase up) が projects/<name>/project.yml を正規化し、clone プランを
# base64 のレコード列 (DEVBASE_REPOS) としてコンテナへ渡す。ここでは 1 行ずつ読んで clone
# するだけなので、コンテナイメージへ YAML/JSON パーサ依存を増やさずに済む。
#
# DEVBASE_REPOS         : base64 の行区切りレコード。1 行 = url / dir / branch / init を
#                         US (0x1f) 区切りで並べたもの。branch は空可、init は 1/0。
#                         行区切りは LF で末尾にも LF が付く (符号化側の契約:
#                         lib/devbase/project/config.py の encode_repo_plan)
# DEVBASE_PRIMARY_DIR   : 起動後に cd する /work 配下のディレクトリ名
# DEVBASE_WORKSPACE     : 書き出す *.code-workspace の絶対パス (複数 repo 時)
# DEVBASE_WORKSPACE_FOLDERS
#                       : base64 の行区切りレコード。1 行 = <dir> と folder オブジェクトの
#                         JSON を US (0x1f) で並べたもの。clone できた dir の行だけを
#                         連結して workspace にする (PLAN37)
# DEVBASE_WORKSPACE_B64 : 完成済みの workspace (base64 JSON)。DEVBASE_WORKSPACE_FOLDERS を
#                         渡さない古いホスト向けの fallback
#
# 関数定義だけを読み込みたいテストからは
# `DEVBASE_ENTRYPOINT_LIB_ONLY=1 . entrypoint.sh` で source する。

# clone プランを復号して 1 行 1 repo で出力する (未設定なら何も出さない)。
devbase_repo_plan_lines() {
    [ -n "${DEVBASE_REPOS:-}" ] || return 0
    printf '%s' "$DEVBASE_REPOS" | base64 -d
}

# clone プランの各リポジトリを <work_root>/<dir> へ clone する。
#
# 個々の失敗 (clone / checkout / init.sh) は warning に留めて次の repo へ進む。
# 1 つ落ちただけでコンテナが起動しないと、他リポジトリでの作業まで止まるため。
devbase_clone_repos() {
    local work_root="${1:-/work}"
    local plan url dir branch init target cloned

    if ! plan="$(devbase_repo_plan_lines 2>/dev/null)"; then
        echo "Warning: Failed to decode DEVBASE_REPOS (skipping repository setup)"
        return 0
    fi
    if [ -z "$plan" ]; then
        echo "No repositories configured (DEVBASE_REPOS is empty)"
        return 0
    fi

    mkdir -p "$work_root"
    local extra index=0
    # フィールド区切りは US (0x1f)。タブだと IFS の空白扱いで連続する区切りが 1 つに
    # 畳まれ、branch 未指定 (空フィールド) の行で init の値がずれる。
    while IFS=$'\x1f' read -r url dir branch init extra; do
        # 末尾の空行 (符号化側が付ける末尾改行) は読み飛ばす
        [ -n "$url$dir$branch$init$extra" ] || continue
        index=$((index + 1))
        if [ -z "$url" ] || [ -z "$dir" ] || [ -n "$extra" ] ||
           { [ "$init" != "1" ] && [ "$init" != "0" ]; }; then
            echo "Warning: Ignoring malformed clone plan entry (line $index)"
            continue
        fi
        target="$work_root/$dir"

        cloned=0
        if [ -d "$target/.git" ]; then
            echo "Repository already exists: $dir"
        else
            echo "Cloning repository: $url -> $target"
            if ! git clone "$url" "$target"; then
                echo "Warning: Failed to clone repository: $url"
                continue
            fi
            cloned=1
        fi

        # checkout は clone 直後だけ。既存 clone に対して毎回実行すると、コンテナ内で
        # 作業ブランチへ切り替えたユーザが再起動のたびに引き戻されてしまう。
        # 失敗したら意図しない branch で init.sh を走らせないよう この repo は打ち切る。
        if [ "$cloned" = "1" ] && [ -n "$branch" ]; then
            if ! git -C "$target" checkout "$branch"; then
                echo "Warning: Failed to checkout branch '$branch' in $dir (skipping)"
                continue
            fi
        fi

        if [ "$init" = "1" ] && [ -f "$target/init.sh" ]; then
            echo "Running init.sh in $dir"
            (cd "$target" && ./init.sh) || echo "Warning: init.sh failed in $dir"
        fi
    done <<EOF
$plan
EOF
}

# 複数 repo をまとめて開くための *.code-workspace を書き出す。
#
# clone できなかった repo のフォルダを載せると、VS Code のエクスプローラに開けない
# フォルダが並ぶ (PLAN37)。そこで DEVBASE_WORKSPACE_FOLDERS の各行を見て、
# <work_root>/<dir> が実在する行の JSON だけを連結する。
#
# 各 folder の JSON はホスト側が直列化済みなので、ここで組み立てるのは外枠 (
# `{"folders": [` … `]}`) とカンマだけ。dir に " や \ が入っていてもシェルで
# エスケープを考えずに済む。
devbase_write_workspace() {
    local work_root="${1:-/work}"
    [ -n "${DEVBASE_WORKSPACE:-}" ] || return 0

    local dest="$DEVBASE_WORKSPACE"
    mkdir -p "$(dirname "$dest")"

    if [ -z "${DEVBASE_WORKSPACE_FOLDERS:-}" ]; then
        # 旧ホスト (PLAN37 前) から起動された場合。完成品をそのまま置く。
        devbase_write_workspace_verbatim "$dest"
        return 0
    fi

    local records dir folder extra first=1
    if ! records="$(printf '%s' "$DEVBASE_WORKSPACE_FOLDERS" | base64 -d 2>/dev/null)"; then
        echo "Warning: Failed to decode DEVBASE_WORKSPACE_FOLDERS"
        devbase_write_workspace_verbatim "$dest"
        return 0
    fi

    {
        printf '{\n  "folders": [\n'
        while IFS=$'\x1f' read -r dir folder extra; do
            [ -n "$dir$folder$extra" ] || continue
            if [ -z "$dir" ] || [ -z "$folder" ] || [ -n "$extra" ]; then
                echo "Warning: Ignoring malformed workspace folder record" >&2
                continue
            fi
            if [ ! -d "$work_root/$dir" ]; then
                echo "Warning: Skipping workspace folder (not cloned): $dir" >&2
                continue
            fi
            [ "$first" = "1" ] || printf ',\n'
            printf '    %s' "$folder"
            first=0
        done <<EOF
$records
EOF
        printf '\n  ]\n}\n'
    } > "$dest.tmp"

    mv "$dest.tmp" "$dest"
    echo "Workspace file written: $dest"
}

# ホストが組み立て済みの workspace (DEVBASE_WORKSPACE_B64) をそのまま書き出す。
devbase_write_workspace_verbatim() {
    local dest="$1"
    [ -n "${DEVBASE_WORKSPACE_B64:-}" ] || return 0

    if printf '%s' "$DEVBASE_WORKSPACE_B64" | base64 -d > "$dest.tmp" 2>/dev/null; then
        mv "$dest.tmp" "$dest"
        echo "Workspace file written: $dest"
    else
        rm -f "$dest.tmp"
        echo "Warning: Failed to write workspace file: $dest"
    fi
}

# primary リポジトリのディレクトリへ移動する (ログイン直後の作業場所)。
devbase_enter_primary_dir() {
    local work_root="${1:-/work}"
    local target="$work_root/${DEVBASE_PRIMARY_DIR:-}"

    if [ -z "${DEVBASE_PRIMARY_DIR:-}" ]; then
        return 0
    fi
    if [ -d "$target" ]; then
        cd "$target"
        echo "Current directory: $(pwd)"
    else
        echo "Warning: Primary directory does not exist: $target"
    fi
}

# ===================================================================
# PLAN39: AI 設定の永続化 (共通 / アカウントグループの 2 層)
# ===================================================================
# /persistent/ai    … 全コンテナ共通 (分類 A)。plugins / skills のように
#                     契約やテナントに紐づかない資産。グループ数だけ重複させない
# /persistent/group … アカウントグループ単位 (分類 B)。認証情報と会話履歴のように
#                     企業テナントへ紐づくもの。グループをまたいで共有しない
#
# ~/.claude の既定は**グループ側**にする。Claude Code は projects / sessions /
# tasks のようなディレクトリを随時作るため、永続化するエントリを列挙する方式だと
# 列挙漏れが黙って揮発する。既定をグループ側に倒し、共通にしたいものだけを
# 名指しで共通側へ張る。

# 分類 A: ホーム直下
DEVBASE_SHARED_SETTINGS=(
    ".codex"
    ".serena"
    ".ssh"
    ".kiro"
    "share"
)

# 分類 B: ホーム直下
DEVBASE_GROUP_SETTINGS=(
    ".claude.json"
    ".claude"
    ".gemini"
)

# 分類 A のうち ~/.claude 配下にあるもの (グループ側の .claude から共通側へ張る)
DEVBASE_SHARED_CLAUDE_SETTINGS=(
    "plugins"
    "skills"
    "commands"
    "CLAUDE.md"
    "settings.json"
)

# ファイルとして作るエントリ (末尾の要素名で判定する)。ここに無いものは
# ディレクトリとして作る。
#
# 拡張子で判定していた頃は `.jsonl` が `*.json` にマッチせず、history.jsonl が
# **ディレクトリとして**作られて Claude Code が追記できなくなっていた。
# 新しいファイルのエントリを足すときはこの一覧にも足すこと。
DEVBASE_FILE_ENTRIES=(
    ".claude.json"
    ".credentials.json"
    "history.jsonl"
    "CLAUDE.md"
    "settings.json"
)

# パスの末尾要素がファイルとして作るエントリか判定する。
devbase_is_file_entry() {
    local name="${1##*/}" entry
    for entry in "${DEVBASE_FILE_ENTRIES[@]}"; do
        [ "$name" = "$entry" ] && return 0
    done
    return 1
}

# 永続領域のルートを用意する。
#
# 空の named volume は **root 所有**で作られ uid 1000 では書き込めないため、
# 書けなければ chown する。テストのように最初から書ける場所では sudo を呼ばない。
devbase_ensure_persistent_root() {
    local root="$1" owner="${2:-${USERNAME:-ubuntu}}"

    if [ ! -d "$root" ]; then
        mkdir -p "$root" 2>/dev/null || sudo mkdir -p "$root"
    fi
    if [ ! -w "$root" ]; then
        sudo chown "${owner}:${owner}" "$root"
    fi
}

# 実体が無ければプレースホルダを作る (親ディレクトリごと)。
devbase_ensure_entry() {
    local path="$1"

    mkdir -p "$(dirname "$path")"
    if [ -e "$path" ]; then
        return 0
    fi
    if devbase_is_file_entry "$path"; then
        : > "$path"
    else
        mkdir -p "$path"
    fi
}

# <link_path> を <target_path> への symlink にする。
#
# **link 側と実体側の双方**で親ディレクトリを作るのが要点。入れ子パス
# (.claude/plugins) ではどちらの親も無いことがあり、以前は実体側の作成が
# `No such file or directory` で落ちて壊れた symlink が残っていた。
#
# 既存の実体は `rm -rf` してから張り直す。symlink に対する `rm -rf` は
# **リンクだけ**を消すので、共通側の実体は巻き添えにならない。
devbase_link_setting() {
    local link_path="$1" target_path="$2" owner="${3:-${USERNAME:-ubuntu}}"

    devbase_ensure_entry "$target_path"

    if [ -L "$link_path" ] && [ "$(readlink "$link_path")" = "$target_path" ]; then
        echo "  ✓ ${link_path} (symlink exists)"
        return 0
    fi

    mkdir -p "$(dirname "$link_path")"
    if [ -e "$link_path" ] || [ -L "$link_path" ]; then
        echo "  Removing existing ${link_path}..."
        rm -rf "$link_path"
    fi

    echo "  Creating symlink: ${link_path} -> ${target_path}"
    ln -s "$target_path" "$link_path"
    chown -h "${owner}:${owner}" "$link_path" 2>/dev/null || true
}

# シード元から 1 エントリを**コピー**する (既にあれば何もしない)。
#
# 第 3 引数以降は「コピーしない直下の名前」。分類 A の共通資産をグループ側へ
# 複製しないために使う。
devbase_seed_entry() {
    local src="$1" dest="$2"
    shift 2

    if [ -e "$dest" ]; then
        return 0
    fi
    if [ ! -e "$src" ]; then
        echo "  skip (シード元なし): $src"
        return 0
    fi

    mkdir -p "$(dirname "$dest")"
    if [ ! -d "$src" ]; then
        cp -a "$src" "$dest"
        echo "  seeded: $dest"
        return 0
    fi

    mkdir -p "$dest"
    local child name excluded skip
    # `.[!.]*` と `..?*` で隠しファイルも拾う (`.credentials.json` 等)。
    for child in "$src"/* "$src"/.[!.]* "$src"/..?*; do
        [ -e "$child" ] || [ -L "$child" ] || continue
        name="${child##*/}"
        skip=0
        for excluded in "$@"; do
            if [ "$name" = "$excluded" ]; then
                skip=1
                break
            fi
        done
        [ "$skip" = "1" ] && continue
        cp -a "$child" "$dest/$name"
    done
    echo "  seeded: $dest"
}

# default グループの初回シード。
#
# 現行 /persistent/ai に実体がある分類 B のデータ (.claude.json / 認証 / 履歴 /
# .gemini) をグループ側へ **コピー** して初期化する。move ではないので切り戻し時に
# 元データが残る。非 default では走らせない — 走らせるとグループ分離の意味が
# 失われる。gcloud / gws はシード元が存在しないため対象外 (AC8)。
devbase_seed_group_settings() {
    local ai_root="$1" group_root="$2" group="$3"
    local entry

    if [ "$group" != "default" ]; then
        return 0
    fi

    echo "Seeding account group '${group}' from ${ai_root} (first run only)..."
    for entry in "${DEVBASE_GROUP_SETTINGS[@]}"; do
        if [ "$entry" = ".claude" ]; then
            devbase_seed_entry "$ai_root/$entry" "$group_root/$entry" \
                "${DEVBASE_SHARED_CLAUDE_SETTINGS[@]}"
        else
            devbase_seed_entry "$ai_root/$entry" "$group_root/$entry"
        fi
    done
}

# AI 設定の symlink を 2 系統ぶん張る (初回シードを含む)。
devbase_setup_ai_settings() {
    local home_root="$1" ai_root="$2" group_root="$3" group="${4:-default}"
    local owner="${5:-${USERNAME:-ubuntu}}"
    local entry

    devbase_ensure_persistent_root "$ai_root" "$owner"
    devbase_ensure_persistent_root "$group_root" "$owner"

    # symlink を張る**前**にシードする。張ったあとに走らせると、共通側を指す
    # symlink の中身へコピーしてしまう。
    devbase_seed_group_settings "$ai_root" "$group_root" "$group"

    for entry in "${DEVBASE_SHARED_SETTINGS[@]}"; do
        devbase_link_setting "$home_root/$entry" "$ai_root/$entry" "$owner"
    done
    for entry in "${DEVBASE_GROUP_SETTINGS[@]}"; do
        devbase_link_setting "$home_root/$entry" "$group_root/$entry" "$owner"
    done
    for entry in "${DEVBASE_SHARED_CLAUDE_SETTINGS[@]}"; do
        devbase_link_setting "$group_root/.claude/$entry" \
            "$ai_root/.claude/$entry" "$owner"
    done
}

# テストは関数定義だけを使う (source 時のみ有効な return で以降を読み飛ばす)。
if [ -n "${DEVBASE_ENTRYPOINT_LIB_ONLY:-}" ]; then
    return 0 2>/dev/null || exit 0
fi

# Setup authentication credentials from environment variables
USERNAME="${USERNAME:-ubuntu}"

# 1. Setup Google Cloud credentials from base64 encoded environment variable
# New format: GCP_CREDENTIALS_BASE64__{profile} with GCP_ACTIVE_PROFILE
# Legacy format: GOOGLE_APPLICATION_CREDENTIALS_BASE64
_GCP_PROFILE="${GCP_ACTIVE_PROFILE:-default}"
_GCP_VAR="GCP_CREDENTIALS_BASE64__${_GCP_PROFILE}"
_GCP_CREDS_B64="${!_GCP_VAR:-$GOOGLE_APPLICATION_CREDENTIALS_BASE64}"

if [ -n "$_GCP_CREDS_B64" ]; then
    echo "Setting up Google Cloud credentials (profile: ${_GCP_PROFILE})..."
    DEFAULT_CREDS_PATH="/home/${USERNAME}/.config/gcloud/credentials.json"

    # Decode base64 content once
    CREDS_CONTENT=$(printf '%s' "$_GCP_CREDS_B64" | base64 -d)

    # Output to GOOGLE_APPLICATION_CREDENTIALS path
    GAC_PATH="${GOOGLE_APPLICATION_CREDENTIALS:-$DEFAULT_CREDS_PATH}"
    GAC_DIR=$(dirname "$GAC_PATH")
    mkdir -p "$GAC_DIR"
    printf '%s' "$CREDS_CONTENT" > "$GAC_PATH"
    chmod 600 "$GAC_PATH"
    export GOOGLE_APPLICATION_CREDENTIALS="$GAC_PATH"
    echo "Google Cloud credentials saved to: $GAC_PATH"

    # Output to BIGQUERY_KEY_FILE path if different
    BQ_PATH="${BIGQUERY_KEY_FILE:-$DEFAULT_CREDS_PATH}"
    if [ "$BQ_PATH" != "$GAC_PATH" ]; then
        BQ_DIR=$(dirname "$BQ_PATH")
        mkdir -p "$BQ_DIR"
        printf '%s' "$CREDS_CONTENT" > "$BQ_PATH"
        chmod 600 "$BQ_PATH"
        echo "BigQuery key file saved to: $BQ_PATH"
    fi
    export BIGQUERY_KEY_FILE="$BQ_PATH"
fi

# 2. Setup Git configuration
if [ -n "$GIT_USER_NAME" ]; then
    git config --global user.name "$GIT_USER_NAME" 2>/dev/null || true
fi

if [ -n "$GIT_USER_EMAIL" ]; then
    git config --global user.email "$GIT_USER_EMAIL" 2>/dev/null || true
fi

# 3. Setup Git credentials
# 3.1. Restore .git-credentials from base64 encoded environment variable (preferred method)
if [ -n "$GIT_CREDENTIALS_BASE64" ]; then
    echo "Restoring git credentials from GIT_CREDENTIALS_BASE64..."
    # Remove existing .git-credentials file to avoid stale data
    rm -f ~/.git-credentials
    # Create temporary file and then move to avoid permission issues
    TMP_CRED=$(mktemp)
    echo "$GIT_CREDENTIALS_BASE64" | base64 -d > "$TMP_CRED"
    # Ensure ~/.git-credentials directory is writable
    mkdir -p ~/.config
    sudo install -m 600 -o $(id -u) -g $(id -g) "$TMP_CRED" ~/.git-credentials 2>/dev/null || \
        (cat "$TMP_CRED" > ~/.git-credentials && chmod 600 ~/.git-credentials)
    rm -f "$TMP_CRED"
    echo "Git credentials restored successfully"
fi

# 3.2. Setup Git credential helper
if [ -n "$GIT_CREDENTIAL_HELPER" ]; then
    git config --global credential.helper "$GIT_CREDENTIAL_HELPER" 2>/dev/null || true
    echo "Git credential helper configured: $GIT_CREDENTIAL_HELPER"
else
    # Default to store if not specified
    git config --global credential.helper store 2>/dev/null || true
fi

# 3.3. Legacy: Setup Git credentials using GitHub token (backward compatibility)
if [ -z "$GIT_CREDENTIALS_BASE64" ] && [ -n "$GITHUB_PERSONAL_ACCESS_TOKEN" ]; then
    echo "Using legacy GITHUB_PERSONAL_ACCESS_TOKEN..."
    # Create .git-credentials file with username:token format
    TMP_CRED=$(mktemp)
    echo "https://x-access-token:$GITHUB_PERSONAL_ACCESS_TOKEN@github.com" > "$TMP_CRED"
    sudo install -m 600 -o $(id -u) -g $(id -g) "$TMP_CRED" ~/.git-credentials 2>/dev/null || \
        (cat "$TMP_CRED" > ~/.git-credentials && chmod 600 ~/.git-credentials)
    rm -f "$TMP_CRED"
    # Configure git to use credential helper
    git config --global credential.helper store 2>/dev/null || true
fi

# 4. Setup AWS configuration
# Priority: AWS_CONFIG_BASE64 (config + credentials) > AWS_PROFILE > AWS_ACCESS_KEY_ID
if [ -n "$AWS_CONFIG_BASE64" ]; then
    # Config files mode - decode and extract tar.gz archive (config + credentials only)
    echo "Restoring AWS configuration from AWS_CONFIG_BASE64..."
    mkdir -p ~/.aws
    # Remove existing config and credentials to avoid stale data
    rm -f ~/.aws/config ~/.aws/credentials
    # Decode base64 and extract tar.gz to ~/.aws
    echo "$AWS_CONFIG_BASE64" | base64 -d | tar -xzf - -C ~/.aws
    chmod 700 ~/.aws
    chmod 600 ~/.aws/config ~/.aws/credentials 2>/dev/null || true
    echo "AWS configuration restored successfully (all profiles available)"
    # List available profiles for reference
    if [ -f ~/.aws/config ]; then
        profiles=$(grep -E '^\[profile |^\[default\]' ~/.aws/config 2>/dev/null | sed 's/\[profile /  - /g; s/\[default\]/  - default/g; s/\]//g')
        if [ -n "$profiles" ]; then
            echo "Available profiles:"
            echo "$profiles"
        fi
    fi
elif [ -n "$AWS_PROFILE" ]; then
    # AWS SSO Profile mode - credentials are managed by aws sso login
    # ~/.aws should be mounted from host to use cached credentials
    echo "Using AWS Profile: $AWS_PROFILE"
elif [ -n "$AWS_ACCESS_KEY_ID" ] && [ -n "$AWS_SECRET_ACCESS_KEY" ]; then
    # Access Key mode - create credentials file
    mkdir -p ~/.aws
    cat > ~/.aws/credentials <<EOF
[default]
aws_access_key_id = $AWS_ACCESS_KEY_ID
aws_secret_access_key = $AWS_SECRET_ACCESS_KEY
EOF
    chmod 600 ~/.aws/credentials

    # Create config file with region
    if [ -n "$AWS_DEFAULT_REGION" ]; then
        cat > ~/.aws/config <<EOF
[default]
region = $AWS_DEFAULT_REGION
EOF
        chmod 600 ~/.aws/config
    fi
fi

# 5. Docker-in-Docker (DinD) Setup - enabled by ENABLE_DIND=true
if [ "$ENABLE_DIND" = "true" ] || [ "$ENABLE_DIND" = "1" ]; then
    echo "Starting Docker-in-Docker..."
    # Docker公式パターンに準拠したDinD起動スクリプト
    # 参考: https://github.com/docker-library/docker/blob/master/dockerd-entrypoint.sh

    # 1. 古いPIDファイルのクリーンアップ
    echo "Cleaning up stale PID files..."
    find /run /var/run -iname 'docker*.pid' -delete 2>/dev/null || true
    find /run /var/run -iname 'containerd*.pid' -delete 2>/dev/null || true

    # 2. Stale socketのクリーンアップ
    echo "Cleaning up stale socket files..."
    rm -f /var/run/docker.sock 2>/dev/null || true
    rm -rf /var/run/docker/containerd/*.sock 2>/dev/null || true

    # 3. dockerdプロセスチェック
    if pgrep -x dockerd > /dev/null; then
        echo "Docker daemon already running (PID: $(pgrep -x dockerd))"
    else
        echo "Starting Docker daemon..."

        # dind wrapper script使用（mount操作にroot権限が必要）
        if [ -x '/usr/local/bin/dind' ]; then
            echo "Using dind wrapper script"
            # tini (docker-init) が利用可能ならPID 1問題を解決
            if command -v docker-init >/dev/null 2>&1; then
                echo "Using docker-init (tini) for proper signal handling"
                sudo docker-init /usr/local/bin/dind dockerd &
            else
                sudo /usr/local/bin/dind dockerd &
            fi
        else
            echo "Using dockerd directly"
            if command -v docker-init >/dev/null 2>&1; then
                sudo docker-init dockerd &
            else
                sudo dockerd &
            fi
        fi

        echo "Docker daemon started (PID: $!)"

        # 起動確認（最大30秒待機）
        echo "Waiting for Docker daemon to be ready..."
        for i in {1..30}; do
            if docker info > /dev/null 2>&1; then
                echo "Docker daemon is ready"
                break
            fi
            sleep 1
        done

        # 起動失敗チェック
        if ! docker info > /dev/null 2>&1; then
            echo "ERROR: Docker daemon failed to start within 30 seconds"
            echo "Dockerd process:"
            ps aux | grep dockerd || echo "  No dockerd process found"
            echo "Docker socket:"
            ls -la /var/run/docker.sock 2>/dev/null || echo "  Docker socket not found"
            exit 1
        fi
    fi
fi

# ========================================
# AI Agent Settings Symlink Setup (PLAN39: 共通 / グループの 2 層)
# ========================================
# DEVBASE_ACCOUNT_GROUP はホスト (devbase up) が解決して渡す。ホスト側で
# 検証済みなので、ここでは未設定時に default へ落とすだけにする。
DEVBASE_ACCOUNT_GROUP="${DEVBASE_ACCOUNT_GROUP:-default}"
AI_PERSISTENT_DIR="/persistent/ai"
GROUP_PERSISTENT_DIR="/persistent/group"

echo "Setting up AI agent settings symlinks (account group: ${DEVBASE_ACCOUNT_GROUP})..."
devbase_setup_ai_settings \
    "/home/${USERNAME}" "$AI_PERSISTENT_DIR" "$GROUP_PERSISTENT_DIR" \
    "$DEVBASE_ACCOUNT_GROUP" "$USERNAME"
echo "AI agent settings symlinks setup completed"
# ========================================

# Repository setup (PLAN32: 1 project = 複数リポジトリ)
# 個々の失敗はコンテナ起動を止めない (関数内で warning 扱い)。
DEVBASE_WORK_ROOT="${DEVBASE_WORK_ROOT:-/work}"
devbase_clone_repos "$DEVBASE_WORK_ROOT"
devbase_write_workspace "$DEVBASE_WORK_ROOT"
devbase_enter_primary_dir "$DEVBASE_WORK_ROOT"

# Signal that entrypoint setup is complete
touch /tmp/entrypoint-ready

exec "$@"
