# ==================================
# devbase-trygroup: project-trygroup-prd / project-trygroup-prd-customer 専用環境
# devbase-base を継承し、Poetry と pnpm を追加
# ==================================

FROM devbase-base:latest

# --- user レイヤー: pnpm（npm グローバル） ---
# npm のグローバル prefix (/usr/local/share/npm-global) は base で ubuntu 所有にしてあるため
# root へ切り替えずにインストールする（root で入れると ubuntu が更新できなくなる）。
USER ubuntu
RUN set -eux; \
    npm i -g pnpm; \
    npm cache clean --force; \
    rm -rf ~/.npm

# --- user レイヤー: Poetry ---
# uv との競合回避:
#   - Poetry は公式インストーラで独立した専用 venv に導入する（uv の管理環境に非干渉）
#   - バイナリは ~/.local/bin/poetry、uv は ~/.local/bin/uv で名前衝突なし
#   - Poetry の仮想環境は既定の ~/.cache/pypoetry に作成し、
#     uv が使うプロジェクト直下の .venv と分離する（virtualenvs.in-project は false のまま）
USER ubuntu
RUN set -eux; \
    curl -sSL https://install.python-poetry.org | python3 -; \
    ~/.local/bin/poetry --version; \
    rm -rf ~/.cache/pypoetry/cache ~/.cache/pip

USER ubuntu
WORKDIR /work
