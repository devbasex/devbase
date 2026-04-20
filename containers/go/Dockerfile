# Dockerfile.dev - devbase互換の開発コンテナ
# devbase-baseをベースに、Go開発環境を追加

FROM devbase-base:latest

USER root

# Go 1.23 インストール
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
        amd64) go_arch="amd64" ;; \
        arm64) go_arch="arm64" ;; \
        *) echo "Unsupported architecture: ${arch}" && exit 1 ;; \
    esac; \
    curl -fsSL "https://go.dev/dl/go1.23.6.linux-${go_arch}.tar.gz" \
        | tar -xzf - -C /usr/local; \
    rm -rf /tmp/* /var/tmp/*

ENV PATH="/usr/local/go/bin:${PATH}"

# 確認
RUN go version

USER ubuntu
WORKDIR /work

CMD ["sleep", "infinity"]
