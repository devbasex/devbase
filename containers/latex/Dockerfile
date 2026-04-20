# ==================================
# devbase-latex: LaTeX開発環境
# devbase-base + LaTeX
# ==================================

# ベースイメージから継承
FROM devbase-base:latest

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    texlive-lang-japanese \
    texlive-latex-extra \
    texlive-fonts-recommended \
    dvipdfmx && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
USER ubuntu

