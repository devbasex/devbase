# ==================================
# devbase-general: 汎用開発環境
# devbase-base を継承
# ==================================

FROM devbase-base:latest

USER root

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
