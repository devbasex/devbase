#!/bin/bash
# devbase_ai_1 から devbase_home_ubuntu へのデータ移行スクリプト

set -euo pipefail

# 色付きログ関数
log_info() {
    echo -e "\033[0;32m[INFO]\033[0m $1"
}

log_warn() {
    echo -e "\033[0;33m[WARN]\033[0m $1"
}

log_error() {
    echo -e "\033[0;31m[ERROR]\033[0m $1"
}

# ボリューム名
SOURCE_VOLUME="devbase_ai_1"
TARGET_VOLUME="devbase_home_ubuntu"

log_info "devbase_ai_1 -> devbase_home_ubuntu データ移行を開始します"

# ソースボリュームの存在確認
if ! docker volume inspect "$SOURCE_VOLUME" &>/dev/null; then
    log_error "ソースボリューム '$SOURCE_VOLUME' が見つかりません"
    exit 1
fi

log_info "✓ ソースボリューム '$SOURCE_VOLUME' を確認しました"

# ターゲットボリュームの作成
if docker volume inspect "$TARGET_VOLUME" &>/dev/null; then
    log_warn "ターゲットボリューム '$TARGET_VOLUME' は既に存在します"
    read -p "上書きしますか？ (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "移行を中止しました"
        exit 0
    fi
    log_info "既存のボリューム '$TARGET_VOLUME' を削除します"
    docker volume rm "$TARGET_VOLUME"
fi

log_info "ターゲットボリューム '$TARGET_VOLUME' を作成します"
docker volume create "$TARGET_VOLUME"

# データコピー（一時コンテナを使用）
log_info "データをコピー中..."
docker run --rm \
    -v "$SOURCE_VOLUME:/source:ro" \
    -v "$TARGET_VOLUME:/target" \
    alpine sh -c "cp -a /source/. /target/"

log_info "✓ データコピーが完了しました"

# コピー結果の確認
log_info "コピー結果を確認します"

SOURCE_FILES=$(docker run --rm -v "$SOURCE_VOLUME:/data:ro" alpine find /data -type f | wc -l)
TARGET_FILES=$(docker run --rm -v "$TARGET_VOLUME:/data:ro" alpine find /data -type f | wc -l)

log_info "  ソースファイル数: $SOURCE_FILES"
log_info "  ターゲットファイル数: $TARGET_FILES"

if [ "$SOURCE_FILES" -eq "$TARGET_FILES" ]; then
    log_info "✓ ファイル数が一致しています"
else
    log_warn "! ファイル数が一致しません（差分: $((TARGET_FILES - SOURCE_FILES))）"
fi

log_info "移行が完了しました"
log_info ""
log_info "次のステップ:"
log_info "  1. devbaseコードを更新してdevbase_home_ubuntuを使用するようにする"
log_info "  2. 動作確認後、古いボリュームを削除: docker volume rm $SOURCE_VOLUME"
