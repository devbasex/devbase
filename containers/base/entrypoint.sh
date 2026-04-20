#!/bin/bash

set -e

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
# AI Agent Settings Symlink Setup
# ========================================
echo "Setting up AI agent settings symlinks..."

AI_PERSISTENT_DIR="/persistent/ai"
AI_SETTINGS=(
    ".claude.json"
    ".claude"
    ".codex"
    ".gemini"
    ".serena"
    ".ssh"
)

# Ensure /persistent/ai directory exists
if [ ! -d "$AI_PERSISTENT_DIR" ]; then
    echo "Creating $AI_PERSISTENT_DIR directory..."
    sudo mkdir -p "$AI_PERSISTENT_DIR"
    sudo chown "${USERNAME}:${USERNAME}" "$AI_PERSISTENT_DIR"
fi

# Create symlinks for each AI setting
for setting in "${AI_SETTINGS[@]}"; do
    HOME_PATH="/home/${USERNAME}/${setting}"
    PERSISTENT_PATH="${AI_PERSISTENT_DIR}/${setting}"

    # Skip if symlink already exists and points to correct location
    if [ -L "$HOME_PATH" ] && [ "$(readlink -f "$HOME_PATH")" = "$PERSISTENT_PATH" ]; then
        echo "  ✓ ${setting} (symlink exists)"
        continue
    fi

    # Remove existing file/directory/broken symlink in home
    if [ -e "$HOME_PATH" ] || [ -L "$HOME_PATH" ]; then
        echo "  Removing existing ${setting} from home..."
        rm -rf "$HOME_PATH"
    fi

    # If setting doesn't exist in persistent storage, create placeholder
    if [ ! -e "$PERSISTENT_PATH" ]; then
        # Determine if it's a file or directory based on extension
        if [[ "$setting" == *.json ]]; then
            echo "  Creating empty file: ${setting}"
            sudo touch "$PERSISTENT_PATH"
        else
            echo "  Creating empty directory: ${setting}"
            sudo mkdir -p "$PERSISTENT_PATH"
        fi
        sudo chown -R "${USERNAME}:${USERNAME}" "$PERSISTENT_PATH"
    fi

    # Create symlink
    echo "  Creating symlink: ${setting} -> ${PERSISTENT_PATH}"
    ln -s "$PERSISTENT_PATH" "$HOME_PATH"
    chown -h "${USERNAME}:${USERNAME}" "$HOME_PATH"
done

echo "AI agent settings symlinks setup completed"
# ========================================

# Git operations (optional, don't fail if they error)
if [ -n "$GIT_USER" ] && [ -n "$GIT_REPO" ]; then
    # Clone repository only if it doesn't exist
    GIT_HOST="${GIT_HOST:-github.com}"
    if [ ! -d "$GIT_REPO" ]; then
        echo "Cloning repository: $GIT_HOST/$GIT_USER/$GIT_REPO"
        git clone "https://$GIT_HOST/$GIT_USER/$GIT_REPO.git" || echo "Warning: Failed to clone repository"
    else
        echo "Repository already exists: $GIT_REPO"
    fi
    # Run init.sh from cloned repository root if it exists
    [ -f "$GIT_REPO/init.sh" ] && (cd "$GIT_REPO" && ./init.sh) || true
fi

# Move to repository directory if it exists
echo "Current directory before cd: $(pwd)"
if [ -n "$GIT_REPO" ]; then
    echo "GIT_REPO=$GIT_REPO"
    if [ -d "$GIT_REPO" ]; then
        echo "Directory $GIT_REPO exists, changing to it"
        cd "$GIT_REPO"
        echo "Current directory after cd: $(pwd)"
    else
        echo "Directory $GIT_REPO does not exist in $(pwd)"
    fi
fi

# Signal that entrypoint setup is complete
touch /tmp/entrypoint-ready

exec "$@"
