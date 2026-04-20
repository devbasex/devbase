# ==================================
# devbase-lfm: 言語・機械学習モデル開発環境
# NVIDIA CUDA + devbase-base の共通ツール + Rust + gfortran + MeCab + DinD
# ==================================

# ==================================
# Stage: CUDA ベース + 追加パッケージ
# ==================================
FROM nvidia/cuda:12.6.3-cudnn-devel-ubuntu24.04 AS cudabase

ENV DEBIAN_FRONTEND=noninteractive

# CUDA環境に必要な追加パッケージをインストール
RUN --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/var/cache/apt,sharing=locked \
    set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
    locales git wget vim sudo nano jq make gfortran \
    curl ca-certificates gnupg lsb-release \
    libnss3 libxrandr2 libxss1 build-essential \
    mecab libmecab-dev mecab-ipadic-utf8 \
    fonts-noto-cjk fonts-noto-cjk-extra \
    iptables e2fsprogs openssl pigz xfsprogs xz-utils \
    kmod iproute2 pkg-config libopenblas-dev; \
    install -m 0755 -d /etc/apt/keyrings; \
    # Docker リポジトリ
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
    # Google Chrome リポジトリ
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list; \
    # NVIDIA Container Toolkit リポジトリ
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor -o /etc/apt/keyrings/nvidia-container-toolkit-keyring.gpg; \
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list; \
    # リポジトリ追加後に再度update
    apt-get update; \
    apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin \
    terraform \
    google-chrome-stable \
    nvidia-container-toolkit; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# set up subuid/subgid so that "--userns-remap=default" works out-of-the-box
RUN set -eux; \
    groupadd -r dockremap || true; \
    useradd -r -g dockremap dockremap || true; \
    echo 'dockremap:165536:65536' >> /etc/subuid; \
    echo 'dockremap:165536:65536' >> /etc/subgid

# ロケール
RUN locale-gen en_US.UTF-8 && \
    update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# ================
# Stage: Final
# devbase-base から共通ツールをコピー
# ================
FROM cudabase

# devbase-base から共通ツールをコピー
COPY --from=devbase-base:latest /usr/local /usr/local
COPY --from=devbase-base:latest /usr/bin/gh /usr/bin/node /usr/bin/
COPY --from=devbase-base:latest /usr/lib/node_modules /usr/lib/node_modules
COPY --from=devbase-base:latest /opt /opt
COPY --from=devbase-base:latest /home/ubuntu/.local /home/ubuntu/.local

# npm/npx シンボリックリンク作成
RUN ln -sf /usr/lib/node_modules/npm/bin/npm-cli.js /usr/bin/npm && \
    ln -sf /usr/lib/node_modules/npm/bin/npx-cli.js /usr/bin/npx

ENV PATH="/home/ubuntu/.local/bin:/opt/google-cloud-sdk/bin:${PATH}"

# 確認
RUN gh --version && node --version && npm --version && aws --version && gcloud --version

# ユーザー設定
ARG USERNAME="ubuntu"
RUN usermod -aG users,docker $USERNAME && \
    echo '%users ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/users && \
    echo "$USERNAME ALL=(ALL:ALL) NOPASSWD: ALL" > /etc/sudoers.d/$USERNAME && \
    chmod 0440 /etc/sudoers.d/users /etc/sudoers.d/$USERNAME

USER ${USERNAME}
WORKDIR /home/${USERNAME}/work

# Rust (rustup)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    echo 'source $HOME/.cargo/env' >> ~/.bashrc
ENV PATH="/home/ubuntu/.cargo/bin:${PATH}"

# Playwright インストール（ブラウザはdevbase-baseからコピーされないため個別にインストール）
RUN npx playwright install --with-deps chromium

# AI CLI aliases (auto-approve/yolo mode)
# - claude: --dangerously-skip-permissions
# - claudb: Bedrock version with --dangerously-skip-permissions
# - gemini: --yolo (auto-approve all actions)
# - codex: --dangerously-bypass-approvals-and-sandbox
# - kiro: --trust-all-tools (trust all tools without confirmation)
RUN echo 'alias claude='\''claude --dangerously-skip-permissions'\''' >> ~/.bashrc && \
    echo 'alias claudb='\''CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-west-2 ANTHROPIC_MODEL=us.anthropic.claude-sonnet-4-5-20250929-v1:0 ANTHROPIC_SMALL_FAST_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0 claude --dangerously-skip-permissions'\''' >> ~/.bashrc && \
    echo 'alias gemini='\''gemini --yolo'\''' >> ~/.bashrc && \
    echo 'alias codex='\''codex --dangerously-bypass-approvals-and-sandbox'\''' >> ~/.bashrc && \
    echo 'alias kiro='\''kiro-cli chat --trust-all-tools'\''' >> ~/.bashrc

# Git credential helper
RUN git config --global credential.helper store

# Copy entrypoint and dind from base (lfm doesn't inherit from devbase-base)
USER root
COPY --from=devbase-base:latest /entrypoint.sh /entrypoint.sh
COPY --from=devbase-base:latest /usr/local/bin/dind /usr/local/bin/dind
RUN chmod +x /entrypoint.sh /usr/local/bin/dind

# NVIDIA Container Toolkit設定（WSL2 + DinD対応）
RUN mkdir -p /etc/nvidia-container-runtime && \
    cat > /etc/nvidia-container-runtime/config.toml <<'EOF'
disable-require = false
supported-driver-capabilities = "compat32,compute,display,graphics,ngx,utility,video"

[nvidia-container-cli]
environment = []
ldconfig = "@/sbin/ldconfig"
# WSL2 + DinD環境ではload-kmodsとno-cgroupsを有効化
load-kmods = true
no-cgroups = true

[nvidia-container-runtime]
log-level = "info"
mode = "auto"
runtimes = ["docker-runc", "runc", "crun"]
EOF

# Docker daemon設定（NVIDIA runtime追加）
RUN mkdir -p /etc/docker && \
    cat > /etc/docker/daemon.json <<'EOF'
{
  "runtimes": {
    "nvidia": {
      "path": "nvidia-container-runtime",
      "runtimeArgs": []
    }
  },
  "default-runtime": "nvidia"
}
EOF

# WSL2のNVIDIAライブラリパスを環境変数に追加
ENV LD_LIBRARY_PATH="/usr/lib/wsl/lib:${LD_LIBRARY_PATH}"

USER ${USERNAME}
VOLUME /var/lib/docker

ENTRYPOINT ["/entrypoint.sh"]
CMD ["tail", "-f", "/dev/null"]
