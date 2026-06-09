#!/usr/bin/env bash
#
# devbase ワンライナー installer (PLAN31_1)
#
#   curl -fsSL https://dl.basex.jp/i | bash
#
# clone (または pull) して `bin/devbase init` を 1 回呼ぶだけの薄い導入スクリプト。
# uv の自動導入・rc 追記・補完登録・plugins.yml 生成・冪等性・旧版移行はすべて
# `devbase init` 側の既存処理に委譲する。env init は対話必須のため案内のみ。
#
# 環境変数で上書き可能:
#   DEVBASE_INSTALL_DIR   配置先          (既定: $HOME/devbase)
#   DEVBASE_INSTALL_REPO  clone 元 URL    (既定: https://github.com/devbasex/devbase.git)
#   DEVBASE_INSTALL_REF   branch / tag    (既定: main)
#
set -euo pipefail

INSTALL_DIR="${DEVBASE_INSTALL_DIR:-$HOME/devbase}"
REPO="${DEVBASE_INSTALL_REPO:-https://github.com/devbasex/devbase.git}"
REF="${DEVBASE_INSTALL_REF:-main}"

# --- 出力ヘルパ --------------------------------------------------------------
info() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
err()  { printf 'error: %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

# --- 前提チェック ------------------------------------------------------------
require_commands() {
    local missing=()
    local cmd
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        err "required command(s) not found: ${missing[*]}"
        die "git と curl をインストールしてから再実行してください。"
    fi
}

# --- REF サニタイズ ----------------------------------------------------------
# REF は `git clone --branch` に渡るため、オプション注入 (先頭 '-') やシェル
# メタ文字混入を防ぐ。branch/tag 名として妥当な文字 (英数 . _ - /) のみ許可。
validate_ref() {
    local ref="$1"
    case "$ref" in
        ""|-*) die "invalid DEVBASE_INSTALL_REF: ${ref:-<empty>}" ;;
    esac
    if [[ ! "$ref" =~ ^[A-Za-z0-9._/-]+$ ]]; then
        die "invalid DEVBASE_INSTALL_REF: ${ref}"
    fi
}

# --- 配置先判定 --------------------------------------------------------------
# 既存ディレクトリが devbase repo か (bin/devbase を持つ git work tree か) 判定。
is_devbase_repo() {
    local dir="$1"
    [ -f "$dir/bin/devbase" ] \
        && git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1
}

dir_is_empty() {
    local dir="$1"
    local entry
    for entry in "$dir"/* "$dir"/.*; do
        [ -e "$entry" ] || continue
        case "${entry##*/}" in .|..) continue ;; esac
        return 1
    done
    return 0
}

# --- 完了案内 ----------------------------------------------------------------
print_next_steps() {
    cat <<EOF

============================================================
 devbase のインストールが完了しました
   配置先: ${INSTALL_DIR}
------------------------------------------------------------
 次の手順:
   1. シェルを再読み込み:
        source "\$("${INSTALL_DIR}/bin/devbase" shell-rc)"
   2. plugin を導入:
        devbase plugin install <name>
   3. プロジェクトへ移動して env を初期化 (対話):
        cd "${INSTALL_DIR}/projects/<project>"
        devbase env init
   4. ビルドして起動:
        devbase build && devbase up && devbase login
============================================================
EOF
}

main() {
    info "devbase installer"

    require_commands git curl
    command -v docker >/dev/null 2>&1 \
        || warn "docker が見つかりません。build/up 実行時に Docker と Compose が必要です。"
    validate_ref "$REF"

    if [ -e "$INSTALL_DIR" ] && ! dir_is_empty "$INSTALL_DIR"; then
        if is_devbase_repo "$INSTALL_DIR"; then
            info "既存の devbase を更新します (git pull --ff-only): ${INSTALL_DIR}"
            git -C "$INSTALL_DIR" pull --ff-only
        else
            err "配置先が devbase ではない既存ディレクトリです: ${INSTALL_DIR}"
            err "誤上書きを避けるため中止します。別の配置先を指定してください:"
            die "  DEVBASE_INSTALL_DIR=/path/to/dir で再実行してください。"
        fi
    else
        info "devbase を clone します: ${REPO} (${REF}) -> ${INSTALL_DIR}"
        git clone --branch "$REF" -- "$REPO" "$INSTALL_DIR"
    fi

    info "初期化を実行します (uv 自動導入 / rc / 補完 / plugins)..."
    cd "$INSTALL_DIR"
    "$INSTALL_DIR/bin/devbase" init

    print_next_steps
}

main "$@"
