# Dockerfile.dev - devbase互換の開発コンテナ
# devbase-baseをベースに、PHP 8.5拡張を追加

FROM devbase-base:latest

USER root

# PHP 8.5 + 必要な拡張 + MySQL Client をUbuntuリポジトリからインストール
# base が ubuntu:26.04 (resolute) になり、標準リポジトリが php8.5 (8.5.4) を
# 提供するようになったため ppa:ondrej/php は不要になった。
# （ondrej PPA は resolute 向けの dist を公開しておらず、追加すると apt-get update が失敗する）
RUN set -eux; \
    apt-get update; \
    apt-get install -y \
    php8.5-fpm php8.5-cli php8.5-common \
    php8.5-gd php8.5-bcmath php8.5-mysql \
    php8.5-zip php8.5-exif php8.5-redis \
    php8.5-mbstring php8.5-xml php8.5-curl \
    default-mysql-client; \
    rm -rf /var/lib/apt/lists/*

# MySQL Shell をMySQL公式リポジトリからインストール
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
