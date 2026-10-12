# Dockerfile.dev - devbase互換の開発コンテナ
# devbase-baseをベースに、PHP 8.5 + MySQL Client + 一般的なPHP拡張を追加

FROM devbase-base:latest

USER root

# PHP 8.5 + 必要な拡張 + MySQL Client をUbuntuリポジトリからインストール
# base が ubuntu:26.04 (resolute) になったため php8.3-* は標準リポジトリに存在しない。
# 26.04 の標準リポジトリが php8.5 (8.5.4) を提供するので PPA は不要。
RUN set -eux; \
    apt-get update; \
    apt-get install -y \
    php8.5-fpm php8.5-cli php8.5-common \
    php8.5-gd php8.5-bcmath php8.5-mysql \
    php8.5-zip php8.5-exif php8.5-redis \
    php8.5-mbstring php8.5-xml php8.5-curl \
    default-mysql-client; \
    rm -rf /var/lib/apt/lists/*

# MySQL Shell をMySQL公式リポジトリからインストール（Ubuntuのmysql-clientと競合するため分離）
RUN set -eux; \
    apt-get update; \
    apt-get install -y curl gnupg lsb-release; \
    mkdir -p /etc/apt/keyrings; \
    curl -fsSL https://repo.mysql.com/RPM-GPG-KEY-mysql-2025 | gpg --dearmor -o /etc/apt/keyrings/mysql.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/mysql.gpg] https://repo.mysql.com/apt/ubuntu/ $(lsb_release -cs) mysql-8.0" > /etc/apt/sources.list.d/mysql.list; \
    apt-get update; \
    apt-get install -y mysql-shell; \
    rm -rf /var/lib/apt/lists/*

# Composerインストール
COPY --from=composer:latest /usr/bin/composer /usr/bin/composer

# 言語サーバ（Python・TypeScript・PHP）。Claude Code の公式 LSP プラグインが PATH の上から起動する。
# base の npm のグローバル領域（tsc 7.0.2）に触れないよう、専用の置き場へ root で入れ、
# typescript-language-server が同じ置き場の typescript 5 を解決する形にする。
# PATH へ出すのは bin の 3 つのコマンドだけで、置き場の tsc は出さない。
# 版は general・php・php85 で揃える（tests/containers/test_language_servers_dockerfile.py が縛る）。
RUN set -eux; \
    npm install --prefix /opt/language-servers --omit=dev --no-audit --no-fund \
      pyright@1.1.414 \
      typescript-language-server@6.0.2 \
      typescript@5.9.3 \
      intelephense@1.18.5; \
    mkdir -p /opt/language-servers/bin; \
    ln -s /opt/language-servers/node_modules/.bin/pyright-langserver /opt/language-servers/bin/pyright-langserver; \
    ln -s /opt/language-servers/node_modules/.bin/typescript-language-server /opt/language-servers/bin/typescript-language-server; \
    ln -s /opt/language-servers/node_modules/.bin/intelephense /opt/language-servers/bin/intelephense; \
    /opt/language-servers/bin/typescript-language-server --version; \
    rm -rf /root/.npm
ENV PATH="/opt/language-servers/bin:${PATH}"

USER ubuntu
WORKDIR /work

CMD ["sleep", "infinity"]
