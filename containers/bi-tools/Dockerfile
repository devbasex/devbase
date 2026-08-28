# ==================================
# devbase-bi-tools: bi-tools プロジェクト用開発環境
# devbase-base を継承し、dbt(BigQuery) と Lightdash CLI を追加する
# ==================================
#
# base に既に含まれるもの（ここでは追加しない）:
#   - Python パッケージマネージャ uv（ubuntu ユーザにインストール済み）
#   - gcloud SDK（`bq` / `gcloud` を含む）
#   - Node.js LTS（`node` / `npm` / `npx` / `yarn`）
#   - Docker（DooD）, gh, terraform, aws-cli, git 等
#
# BIツール（tools/{evidence,lightdash,superset}）の起動形態と CLI の扱い:
#   - Lightdash : サーバは tools/lightdash の docker-compose で起動。ただし
#                 dbt プロジェクトに対する `lightdash deploy` / `lightdash preview`
#                 はホストから叩けるクライアント CLI があるため、これは入れておく。
#   - Evidence  : `evidence` はプロジェクトローカルの devDependency。
#                 tools/evidence で `npm install` → `npm run build/dev` で動くため
#                 ホストにグローバル CLI は不要（base の node/npm/npx で足りる）。
#   - Superset  : `superset` コマンドはサーバ本体に同梱の管理 CLI で、Superset
#                 コンテナ内でのみ実行する（superset db upgrade / init 等）。
#                 ホスト常駐 CLI は存在せず、自動化は REST API / MCP で行うため
#                 dev イメージには入れない。

FROM devbase-base:latest

# --- ubuntu: グローバル npm CLI（Lightdash クライアント）---
# npm のグローバル prefix (/usr/local/share/npm-global) は base で ubuntu 所有にしてあるため
# root へ切り替えずにインストールする（root で入れると ubuntu が更新できなくなる）。
USER ubuntu
RUN set -eux; \
    npm i -g @lightdash/cli; \
    lightdash --version

# --- ubuntu: dbt-core + BigQuery アダプタ ---
USER ubuntu
WORKDIR /work

# 非対話シェル（docker exec / CI 等）でも dbt を解決できるよう
# ubuntu ユーザの user-bin を PATH へ通す。
ENV PATH="/home/ubuntu/.local/bin:${PATH}"

# バージョンは本プロジェクトで動作確認済みの 1.12 系 / Python 3.11 に固定。
# `dbt` 実行ファイルは dbt-core が提供するため dbt-core を主パッケージとし、
# BigQuery アダプタは --with で同梱する。uv tool により隔離インストールされ、
# 実行ファイルは ~/.local/bin に配置される。
RUN set -eux; \
    uv tool install --python 3.11 "dbt-core==1.12.*" --with "dbt-bigquery==1.12.*"; \
    dbt --version

# 補足:
#   - dbt パッケージ（dbt_utils 等）は各プロジェクトで `dbt deps` を実行して
#     取得する（dbt/dbt_packages/ は .gitignore 済み・イメージには焼き込まない）。
#   - profiles.yml はユーザ固有・ADC/OAuth 認証のためイメージに含めない
#     （dbt/profiles.example.yml を各自コピーして使う）。

CMD ["sleep", "infinity"]
