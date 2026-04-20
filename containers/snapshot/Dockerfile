# ==================================
# devbase-snapshot: スナップショット専用軽量イメージ
# Ubuntu noble + tar + zstd のみ
# ==================================
FROM ubuntu:noble

RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends zstd \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
