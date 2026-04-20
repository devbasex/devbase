# Dockerfile.dev - devbase互換の開発コンテナ
# devbase-baseをベースに、PHP 8.3 + MySQL Client + 一般的なPHP拡張を追加

FROM devbase-base:latest

USER root

# PHP 8.3 + 必要な拡張 + MySQL Client をUbuntuリポジトリからインストール
RUN set -eux; \
    apt-get update; \
    apt-get install -y \
    php8.3-fpm php8.3-cli php8.3-common \
    php8.3-gd php8.3-bcmath php8.3-mysql \
    php8.3-zip php8.3-exif php8.3-redis \
    php8.3-mbstring php8.3-xml php8.3-curl \
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

USER ubuntu
WORKDIR /work

CMD ["sleep", "infinity"]
