# ==================================
# devbase-base: 共通基盤イメージ
# Ubuntu 26.04 LTS (resolute) + 共通ツール群
# ==================================
# 26.04 は 2026-04 リリースの最新 LTS (サポート 2031-04 まで)。
# 24.04 (noble) からの主な差分は system python が 3.12 -> 3.14 になること。
# 3.12/3.13 は既定リポジトリに無いため、必要なら `uv python install 3.12` を使う。
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive

# APT パッケージ + Node.js + ロケール（1レイヤーにまとめる）
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
    locales git wget vim sudo nano jq make unzip tmux \
    openssh-client \
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
    # NodeSource リポジトリ (Active LTS を追従。バージョン固定しない)
    curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -; \
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

# ユーザー設定
# npm グローバル領域 (後述の NPM_CONFIG_PREFIX) を sudo なしで更新できるようにするため、
# ユーザーとグループは npm パッケージのインストールより「前」に作っておく。
# こうすると所有者・パーミッションの変更を install と同じ RUN 内で完結でき、
# 数百MBの node_modules がメタデータ変更だけのために新しいレイヤーへ
# 丸ごと複製されるのを避けられる。
ARG USERNAME="ubuntu"
# npm グループの GID は派生イメージ (containers/lfm) と揃える必要があるため固定する。
# lfm は base から /usr/local を COPY するだけで base のグループ定義を引き継がないため、
# GID が動的だと COPY されたファイルの GID が派生側で未定義になる。
# 2000 は Ubuntu の system group (<1000) とも既定ユーザー ubuntu (1000) とも衝突しない。
ARG NPM_GID="2000"
RUN set -eux; \
    groupadd -f users; \
    groupadd -f docker; \
    groupadd -g "$NPM_GID" npm; \
    if ! id -u $USERNAME >/dev/null 2>&1; then \
    useradd -m -s /bin/bash $USERNAME; \
    fi; \
    usermod -aG users,docker,npm $USERNAME; \
    echo '%users ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/users; \
    echo "$USERNAME ALL=(ALL:ALL) NOPASSWD: ALL" > /etc/sudoers.d/$USERNAME; \
    chmod 0440 /etc/sudoers.d/users /etc/sudoers.d/$USERNAME

# npm のグローバル prefix を /usr/local/share/npm-global に置く。
# 既定の /usr/lib/node_modules + /usr/bin は root しか書けず、ubuntu ユーザでの
# `npm i -g`（codex などの自動バージョンアップ）が EACCES で失敗するため。
# 置き場所とパーミッション設計は Dev Containers 標準
# (devcontainers/images の javascript-node) に合わせている。
# ディレクトリは ubuntu:npm 所有 + setgid で作る（下の RUN で実施）。
ENV NPM_CONFIG_PREFIX=/usr/local/share/npm-global
ENV PATH="/usr/local/share/npm-global/bin:${PATH}"

# AWS CLI + gcloud SDK + uv + npm globals（1レイヤーにまとめる）
RUN set -eux; \
    # 所有を $USERNAME:npm、ディレクトリへ setgid を付けて作る。
    # 後から誰が入れてもグループが npm のまま揃う
    # (UID/GID を振り直しても壊れない Dev Containers 方式)。
    install -d -m 2775 -o "$USERNAME" -g npm "$NPM_CONFIG_PREFIX"; \
    # AWS CLI v2
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscliv2.zip; \
    unzip -q /tmp/awscliv2.zip -d /tmp; \
    /tmp/aws/install; \
    rm -rf /tmp/awscliv2.zip /tmp/aws; \
    # AWS Session Manager plugin
    case "$(dpkg --print-architecture)" in \
    amd64) ssm_arch="ubuntu_64bit" ;; \
    arm64) ssm_arch="ubuntu_arm64" ;; \
    *) echo "Unsupported architecture for session-manager-plugin: $(dpkg --print-architecture)" && exit 1 ;; \
    esac; \
    curl -fsSL "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/${ssm_arch}/session-manager-plugin.deb" -o /tmp/session-manager-plugin.deb; \
    dpkg -i /tmp/session-manager-plugin.deb; \
    rm -f /tmp/session-manager-plugin.deb; \
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
    # npm グローバルパッケージ（umask 0002 で npm グループにも書き込み権を残す）
    umask 0002; \
    npm i -g yarn @playwright/test aws-cdk aws-cdk-lib typescript @google/gemini-cli @openai/codex @googleworkspace/cli; \
    # root が作ったファイルも ubuntu が上書きできるように揃える。
    # install レイヤーと同じ RUN なのでレイヤーの複製は発生しない。
    chown -R "$USERNAME":npm "$NPM_CONFIG_PREFIX"; \
    chmod -R g+rwX "$NPM_CONFIG_PREFIX"; \
    find "$NPM_CONFIG_PREFIX" -type d -exec chmod g+s {} +; \
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
RUN gh --version && node --version && npm --version && aws --version && gcloud --version && session-manager-plugin --version

USER ${USERNAME}
WORKDIR /tmp

# ユーザー用ツール（uv, claude, kiro, playwright）
RUN set -eux; \
    # uv for user
    curl -LsSf https://astral.sh/uv/install.sh | sh; \
    # Claude Code CLI
    curl -fsSL https://claude.ai/install.sh | bash; \
    # Antigravity CLI (agy)。~/.local/bin へ単体バイナリを置く方式で、
    # インストーラは SHA512 を検証し linux の amd64 / arm64 どちらでも動く。
    # 併せて .bashrc / .zshrc / .profile へ PATH 行を足すため、下の RUN が入れる
    # 同じ行と重複するが、内容が同一なので実害は無い (--skip-path 相当の口は無い)。
    # パイプ直結だと既定 shell に pipefail が無く curl の失敗を bash の成功が覆い隠すため、
    # 一度ファイルへ落としてから実行し、最後にバイナリの実体を検証する。
    curl -fsSL https://antigravity.google/cli/install.sh -o agy-install.sh; \
    bash agy-install.sh; \
    rm -f agy-install.sh; \
    "$HOME/.local/bin/agy" --version; \
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
    # クリーンアップ。--with-deps の sudo apt や Node24/npm11 の
    # /tmp/node-compile-cache が root 所有ファイルを残すため sudo で削除する
    # (非 root の rm では Permission denied になりビルドが失敗する)。
    sudo rm -rf /tmp/* \
    ~/.cache \
    ~/.npm \
    ~/.config/yarn \
    ~/.local/share/pnpm \
    ~/.claude.json.backup*

# AI CLI の起動定義。~/.bashrc へ直接書かずファイルにしているのは、Docker を
# 起動せずに tests/containers/test_ai_cli_aliases.py で振る舞いを固定するため。
COPY --chmod=0644 ai-cli-aliases.sh /etc/devbase/ai-cli-aliases.sh

RUN set -eux; \
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc; \
    echo '. /etc/devbase/ai-cli-aliases.sh' >> ~/.bashrc; \
    git config --global credential.helper store; \
    # Configure Claude Code hooks
    mkdir -p ~/.claude; \
    echo '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"echo '\''unset AWS_REGION'\'' >> \"$CLAUDE_ENV_FILE\""}]}]}}' > ~/.claude/settings.json;

# tmux の既定設定。素の tmux は履歴 2000 行・マウス無効・フォーカス通知を転送しない
# ため、代替画面へ切り替わったあとの出力履歴に実質手が届かない。/etc/tmux.conf は
# ~/.tmux.conf より先に読まれるので、利用者は個人設定で上書きできる。
COPY --chmod=0644 tmux.conf /etc/tmux.conf

# tmux セッションの整理コマンド。tmux-first は居座りクライアントを切断して
# 一番若い番号のセッションへ切り替え、tmux-clean は置き去りのセッションを削除する。
# 背景と使い方は各スクリプト冒頭のコメントと docs/user/environment-variables.md を参照。
COPY --chmod=0755 tmux-first /usr/local/bin/tmux-first
COPY --chmod=0755 tmux-clean /usr/local/bin/tmux-clean

# 短縮名は alias ではなく symlink で用意する。alias は bash の対話シェルにしか効かず、
# zsh などの他シェルや非対話実行 (docker exec など) では使えないため。
RUN sudo ln -sf tmux-first /usr/local/bin/tmux1 \
 && sudo ln -sf tmux-clean /usr/local/bin/tmuxc

# entrypoint と dind
COPY --chmod=755 entrypoint.sh /entrypoint.sh
COPY --chmod=755 dind /usr/local/bin/dind

WORKDIR /work
ENTRYPOINT ["/entrypoint.sh"]
