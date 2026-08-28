# ==================================
# devbase-snapshot: スナップショット専用軽量イメージ
# Ubuntu 26.04 + tar + zstd のみ
# ==================================
FROM ubuntu:26.04

RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends zstd \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
