# ==================================
# devbase-lfm: 言語・機械学習モデル開発環境
# NVIDIA CUDA + devbase-base の共通ツール + Rust + gfortran + MeCab + DinD
# ==================================
# lfm は base を FROM で継がず、base のイメージから COPY --from で取り込む (#275)。
# base の設定 (/etc/devbase・/etc/tmux.conf・/etc/fonts/local.conf・~/.bashrc・
# ~/.claude/settings.json) は下の「base の設定の取り込み」で同じパスへ持ち込み、
# 中身を lfm で書かない。ENV は取り込めないため同じ値で宣言する。取り込みに覆われない
# 設定を base に足すと tests/containers/test_lfm_base_settings.py が名前を挙げて落ちる。

# ==================================
# Stage: CUDA ベース + 追加パッケージ
# ==================================
FROM nvidia/cuda:13.4.1-cudnn-devel-ubuntu26.04 AS cudabase

ENV DEBIAN_FRONTEND=noninteractive

# CUDA環境に必要な追加パッケージをインストール
RUN --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/var/cache/apt,sharing=locked \
    set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
    locales git wget vim sudo nano jq make gfortran tmux \
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
#
# 【重要】cudabase の Ubuntu バージョンは devbase-base と必ず一致させること。
# 下の COPY は node / gh 等の「ビルド済みバイナリ」を持ち込むため、
# devbase-base (ubuntu:26.04 / glibc 2.43) より古い OS の上に置くと
# "GLIBC_2.4x not found" で即座に壊れる。そのため cudabase には
# nvidia/cuda の ubuntu26.04 タグを使用する。
# なお nvidia/cuda の ubuntu26.04 タグは CUDA 13.3.1 以降だけが提供されているため、
# containers/base の Ubuntu を上げる際は CUDA のバージョンも追随させる必要がある。
COPY --from=devbase-base:latest /usr/local /usr/local
COPY --from=devbase-base:latest /usr/bin/gh /usr/bin/node /usr/bin/
COPY --from=devbase-base:latest /usr/lib/node_modules /usr/lib/node_modules
COPY --from=devbase-base:latest /opt /opt
COPY --from=devbase-base:latest /home/ubuntu/.local /home/ubuntu/.local

# npm/npx シンボリックリンク作成
RUN ln -sf /usr/lib/node_modules/npm/bin/npm-cli.js /usr/bin/npm && \
    ln -sf /usr/lib/node_modules/npm/bin/npx-cli.js /usr/bin/npx

# npm のグローバル prefix は base と同じ（上の COPY /usr/local で取り込み済み。
# COPY --from は所有者を保持するので ubuntu:npm 所有のまま = sudo なしで `npm i -g` できる。
# ただし npm グループの定義自体は COPY されないので、下のユーザー設定で
# base と同じ GID で作り直して整合させる）。
ENV NPM_CONFIG_PREFIX=/usr/local/share/npm-global
ENV PATH="/usr/local/share/npm-global/bin:/home/ubuntu/.local/bin:/opt/google-cloud-sdk/bin:${PATH}"
# シェルの設定の読み込み先。base の ENV と同じ値にする (ENV は COPY --from で運べない)。
# docker exec の非対話の処理 (Claude Code の Bash など) が置き場所を見つけるのに使う
ENV DEVBASE_SHELLRC_DIR=/home/ubuntu/.shellrc.d

# 確認
RUN gh --version && node --version && npm --version && aws --version && gcloud --version

# ユーザー設定
ARG USERNAME="ubuntu"
# npm グループの GID は containers/base/Dockerfile の ARG NPM_GID と必ず一致させる。
# COPY --from が持ち込んだ /usr/local/share/npm-global 配下のファイルは GID 数値だけを
# 保持するため、lfm 側に同じ GID のグループが無いと GID が未定義のまま残り、
# setgid ディレクトリも効かず ubuntu が sudo なしで `npm i -g` できなくなる。
# ここで chown -R し直さないのは、数百MBの node_modules が丸ごと
# 新しいレイヤーへ複製されイメージが肥大化するため。
ARG NPM_GID="2000"
RUN set -eux; \
    groupadd -g "$NPM_GID" npm; \
    usermod -aG users,docker,npm $USERNAME; \
    echo '%users ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/users; \
    echo "$USERNAME ALL=(ALL:ALL) NOPASSWD: ALL" > /etc/sudoers.d/$USERNAME; \
    chmod 0440 /etc/sudoers.d/users /etc/sudoers.d/$USERNAME; \
    # 下の settings.json の取り込みより先に ubuntu の持ち物で作る。COPY --from は
    # 足りない親を root:root で作り、entrypoint が ~/.claude をボリュームへのリンクに
    # 差し替える rm -rf が Permission denied で止まるため
    install -d -o "$USERNAME" -g "$USERNAME" /home/$USERNAME/.claude

# base の設定の取り込み (#275)。base が正本で、lfm は中身を書かない。
# - /etc/devbase はディレクトリごと取り込み、base がその下へ足した物は lfm を変えずに届く
# - /etc をまるごとは取り込まない。CUDA のイメージの apt の設定や ld.so.conf.d が消える
# - ~/.bashrc は下の rustup の追記より「前」に取り込む。後にすると lfm の追記が消える
#   (起動定義の後に読み込み器を読む base の行の順序も、そのまま届く)
COPY --from=devbase-base:latest /etc/devbase /etc/devbase
COPY --from=devbase-base:latest /etc/tmux.conf /etc/tmux.conf
COPY --from=devbase-base:latest /etc/fonts/local.conf /etc/fonts/local.conf
COPY --from=devbase-base:latest --chown=ubuntu:ubuntu /home/ubuntu/.bashrc /home/ubuntu/.bashrc
COPY --from=devbase-base:latest --chown=ubuntu:ubuntu /home/ubuntu/.claude/settings.json /home/ubuntu/.claude/settings.json

USER ${USERNAME}
WORKDIR /home/${USERNAME}/work

# Rust (rustup)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    echo 'source $HOME/.cargo/env' >> ~/.bashrc
ENV PATH="/home/ubuntu/.cargo/bin:${PATH}"

# Playwright インストール（ブラウザはdevbase-baseからコピーされないため個別にインストール）
RUN npx playwright install --with-deps chromium

# フォントのキャッシュを作り直す。上の Playwright が --with-deps で fonts-wqy-zenhei などを
# 入れた「後」でなければ、後から入った書体を知らないキャッシュが残る (base と同じ理由。#243)。
# AI CLI の起動定義は ~/.bashrc の取り込みで /etc/devbase/ai-cli-aliases.sh から届くため、
# lfm では書かない (#261)
RUN sudo fc-cache -f

# Git credential helper
RUN git config --global credential.helper store

# entrypoint と dind も base から取り込む (lfm は base を FROM で継がない)
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
