# ==================================
# devbase-base: 共通基盤イメージ
# Ubuntu noble + 共通ツール群
# ==================================
FROM ubuntu:noble

ENV DEBIAN_FRONTEND=noninteractive

# APT パッケージ + Node.js + ロケール（1レイヤーにまとめる）
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
    locales git wget vim sudo nano jq make unzip \
    curl ca-certificates gnupg lsb-release \
    libnss3 libxrandr2 libxss1 \
    fonts-noto-cjk fonts-noto-cjk-extra; \
    # ロケール設定
    locale-gen en_US.UTF-8; \
    update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8; \
    # Docker リポジトリ
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
    chmod a+r /etc/apt/keyrings/docker.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list; \
    # HashiCorp（Terraform）
    curl -fsSL https://apt.releases.hashicorp.com/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/hashicorp-archive-keyring.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
    > /etc/apt/sources.list.d/hashicorp.list; \
    # GitHub CLI リポジトリ
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list; \
    # NodeSource リポジトリ
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -; \
    # ブラウザ: amd64はChrome+Chromium、arm64はChromium
    arch="$(dpkg --print-architecture)"; \
    BROWSER_PKG=""; \
    if [ "$arch" = "amd64" ]; then \
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg; \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list; \
    BROWSER_PKG="google-chrome-stable"; \
    fi; \
    apt-get update; \
    # パッケージインストール
    apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin \
    terraform gh nodejs chromium-browser $BROWSER_PKG; \
    # クリーンアップ
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/* /var/tmp/*

# AWS CLI + gcloud SDK + uv + npm globals（1レイヤーにまとめる）
RUN set -eux; \
    # AWS CLI v2
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscliv2.zip; \
    unzip -q /tmp/awscliv2.zip -d /tmp; \
    /tmp/aws/install; \
    rm -rf /tmp/awscliv2.zip /tmp/aws; \
    # gcloud SDK
    arch="$(uname -m)"; \
    case "${arch}" in \
    x86_64) gcloud_arch="x86_64" ;; \
    aarch64) gcloud_arch="arm" ;; \
    *) echo "Unsupported architecture: ${arch}" && exit 1 ;; \
    esac; \
    curl -fsSL "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-${gcloud_arch}.tar.gz" \
    | tar -xzf - -C /opt; \
    /opt/google-cloud-sdk/install.sh --quiet --path-update=true --usage-reporting=false; \
    /opt/google-cloud-sdk/bin/gcloud components remove --quiet \
    alpha beta anthoscli \
    app-engine-java app-engine-python app-engine-python-extras app-engine-go \
    cloud-datastore-emulator pubsub-emulator \
    kubectl kustomize skaffold minikube \
    docker-credential-gcr cloud-run-proxy 2>&1 || true; \
    # uv (Python パッケージマネージャー)
    curl -LsSf https://astral.sh/uv/install.sh | sh; \
    # npm グローバルパッケージ
    npm i -g yarn @playwright/test aws-cdk aws-cdk-lib typescript @google/gemini-cli @openai/codex; \
    # クリーンアップ
    rm -rf /tmp/* /var/tmp/* \
    /root/.npm /root/.cache /root/.config \
    /root/.local/share \
    /opt/google-cloud-sdk/help \
    /opt/google-cloud-sdk/.install \
    /opt/google-cloud-sdk/.git* \
    /opt/google-cloud-sdk/bin/.gsutil \
    /opt/google-cloud-sdk/platform/gsutil/third_party \
    /var/cache/* \
    /var/log/*

ENV PATH="/root/.local/bin:/opt/google-cloud-sdk/bin:${PATH}"

# 確認
RUN gh --version && node --version && npm --version && aws --version && gcloud --version

# ユーザー設定
ARG USERNAME="ubuntu"
RUN groupadd -f users; \
    groupadd -f docker; \
    if ! id -u $USERNAME >/dev/null 2>&1; then \
    useradd -m -s /bin/bash $USERNAME; \
    fi; \
    usermod -aG users,docker $USERNAME; \
    echo '%users ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/users; \
    echo "$USERNAME ALL=(ALL:ALL) NOPASSWD: ALL" > /etc/sudoers.d/$USERNAME; \
    chmod 0440 /etc/sudoers.d/users /etc/sudoers.d/$USERNAME

USER ${USERNAME}
WORKDIR /tmp

# ユーザー用ツール（uv, claude, kiro, playwright）
RUN set -eux; \
    # uv for user
    curl -LsSf https://astral.sh/uv/install.sh | sh; \
    # Claude Code CLI
    curl -fsSL https://claude.ai/install.sh | bash; \
    # Kiro CLI (Linux ZIP method for x86_64 and aarch64)
    ARCH=$(uname -m); \
    if [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "aarch64" ]; then \
    curl --proto '=https' --tlsv1.2 -sSf "https://desktop-release.q.us-east-1.amazonaws.com/latest/kirocli-${ARCH}-linux.zip" -o 'kirocli.zip' && \
    unzip kirocli.zip && \
    KIRO_CLI_SKIP_SETUP=1 ./kirocli/install.sh && \
    rm -rf kirocli.zip kirocli; \
    else \
    echo "Unsupported architecture: $ARCH, skipping Kiro CLI installation"; \
    fi; \
    # Playwright browsers
    npx playwright install --with-deps chromium; \
    # クリーンアップ
    rm -rf /tmp/* \
    ~/.cache \
    ~/.npm \
    ~/.config/yarn \
    ~/.local/share/pnpm \
    ~/.claude.json.backup*

RUN set -eux; \
    # AI CLI aliases
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc; \
    echo 'alias claude='\''claude --dangerously-skip-permissions "$@"'\''' >> ~/.bashrc; \
    echo 'alias claudb='\''CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-west-2 claude --dangerously-skip-permissions "$@"'\''' >> ~/.bashrc; \
    echo 'alias gemini='\''GOOGLE_GENAI_USE_VERTEXAI=true gemini --yolo "$@"'\''' >> ~/.bashrc; \
    echo 'alias codex='\''codex --dangerously-bypass-approvals-and-sandbox "$@"'\''' >> ~/.bashrc; \
    echo 'alias kiro='\''kiro-cli chat --trust-all-tools "$@"'\''' >> ~/.bashrc; \
    echo 'complete -o default claudb kiro' >> ~/.bashrc; \
    git config --global credential.helper store; \
    # Configure Claude Code hooks
    mkdir -p ~/.claude; \
    echo '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"echo '\''unset AWS_REGION'\'' >> \"$CLAUDE_ENV_FILE\""}]}]}}' > ~/.claude/settings.json;

# entrypoint と dind
COPY --chmod=755 entrypoint.sh /entrypoint.sh
COPY --chmod=755 dind /usr/local/bin/dind

WORKDIR /work
ENTRYPOINT ["/entrypoint.sh"]
