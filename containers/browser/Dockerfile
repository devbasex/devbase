# ==================================
# devbase-browser: ブラウザと文書のフォント
# devbase-base + Playwright の Chromium・ブラウザの依存パッケージ・追加の太さのフォント
# ==================================
# base はブラウザを持たない (#402)。ブラウザを動かす・HTML を PDF やスクリーンショットにする
# プロジェクトは、dev サービスの build.context でこのイメージを選ぶ。
# ブラウザの置き場 (/opt/ms-playwright) と ENV PLAYWRIGHT_BROWSERS_PATH は base から継ぐため、
# ここでは宣言しない。base のフォントの規則 (/etc/fonts/local.conf) も書き換えずに継ぐ。

FROM devbase-base:latest

# 追加の太さのフォント (Noto CJK の Thin・Light・Medium・Black など)。標準の太さは base の
# fonts-noto-cjk にある。標準のアーカイブのパッケージで、外部の取得元は要らない
USER root
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends fonts-noto-cjk-extra; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Playwright の Chromium とその依存パッケージ。npx は base の @playwright/test の CLI を使うため、
# 版は base の @playwright/test に揃う。置き場は base の root の RUN が ubuntu:npm・2775 で作っている。
# 片付けは --with-deps の sudo apt が残す root 所有の物も消すため sudo で行い、
# ブラウザの置き場 ($PLAYWRIGHT_BROWSERS_PATH) とその親の /opt は消さない
USER ubuntu
RUN set -eux; \
    npx playwright install --with-deps chromium; \
    sudo find /usr/share/doc -mindepth 1 ! -type d ! -xtype d ! -name copyright -delete; \
    sudo rm -rf /tmp/* \
    /var/lib/apt/lists/* \
    ~/.cache \
    ~/.npm

# 上の 2 つの RUN が入れたフォントをキャッシュへ載せる
RUN sudo fc-cache -f
