# ==================================
# devbase-base: 共通基盤イメージ
# Ubuntu noble + 共通ツール群
# ==================================
FROM ubuntu:noble

ENV DEBIAN_FRONTEND=noninteractive

# APT パッケージ + Node.js + ロケール（1レイヤーにまとめる）
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
    locales git wget vim sudo nano jq make unzip tmux \
    openssh-client \
    curl ca-certificates gnupg lsb-release \
    libnss3 libxrandr2 libxss1 \
    fonts-noto-cjk fonts-noto-cjk-extra; \
    # ロケール設定
    locale-gen en_US.UTF-8; \
    update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8; \
    # Docker リポジトリ
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
    chmod a+r /etc/apt/keyrings/docker.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list; \
    # HashiCorp（Terraform）
    curl -fsSL https://apt.releases.hashicorp.com/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/hashicorp-archive-keyring.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
    > /etc/apt/sources.list.d/hashicorp.list; \
    # GitHub CLI リポジトリ
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list; \
    # NodeSource リポジトリ (Active LTS を追従。バージョン固定しない)
    curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -; \
    # ブラウザ: amd64はChrome+Chromium、arm64はChromium
    arch="$(dpkg --print-architecture)"; \
    BROWSER_PKG=""; \
    if [ "$arch" = "amd64" ]; then \
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg; \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list; \
    BROWSER_PKG="google-chrome-stable"; \
    fi; \
    apt-get update; \
    # パッケージインストール
    apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin \
    terraform gh nodejs chromium-browser $BROWSER_PKG; \
    # クリーンアップ
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/* /var/tmp/*

# AWS CLI + gcloud SDK + uv + npm globals（1レイヤーにまとめる）
RUN set -eux; \
    # AWS CLI v2
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscliv2.zip; \
    unzip -q /tmp/awscliv2.zip -d /tmp; \
    /tmp/aws/install; \
    rm -rf /tmp/awscliv2.zip /tmp/aws; \
    # AWS Session Manager plugin
    case "$(dpkg --print-architecture)" in \
    amd64) ssm_arch="ubuntu_64bit" ;; \
    arm64) ssm_arch="ubuntu_arm64" ;; \
    *) echo "Unsupported architecture for session-manager-plugin: $(dpkg --print-architecture)" && exit 1 ;; \
    esac; \
    curl -fsSL "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/${ssm_arch}/session-manager-plugin.deb" -o /tmp/session-manager-plugin.deb; \
    dpkg -i /tmp/session-manager-plugin.deb; \
    rm -f /tmp/session-manager-plugin.deb; \
    # gcloud SDK
    arch="$(uname -m)"; \
    case "${arch}" in \
    x86_64) gcloud_arch="x86_64" ;; \
    aarch64) gcloud_arch="arm" ;; \
    *) echo "Unsupported architecture: ${arch}" && exit 1 ;; \
    esac; \
    curl -fsSL "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-${gcloud_arch}.tar.gz" \
    | tar -xzf - -C /opt; \
    /opt/google-cloud-sdk/install.sh --quiet --path-update=true --usage-reporting=false; \
    /opt/google-cloud-sdk/bin/gcloud components remove --quiet \
    alpha beta anthoscli \
    app-engine-java app-engine-python app-engine-python-extras app-engine-go \
    cloud-datastore-emulator pubsub-emulator \
    kubectl kustomize skaffold minikube \
    docker-credential-gcr cloud-run-proxy 2>&1 || true; \
    # uv (Python パッケージマネージャー)
    curl -LsSf https://astral.sh/uv/install.sh | sh; \
    # npm グローバルパッケージ
    npm i -g yarn @playwright/test aws-cdk aws-cdk-lib typescript @google/gemini-cli @openai/codex; \
    # クリーンアップ
    rm -rf /tmp/* /var/tmp/* \
    /root/.npm /root/.cache /root/.config \
    /root/.local/share \
    /opt/google-cloud-sdk/help \
    /opt/google-cloud-sdk/.install \
    /opt/google-cloud-sdk/.git* \
    /opt/google-cloud-sdk/bin/.gsutil \
    /opt/google-cloud-sdk/platform/gsutil/third_party \
    /var/cache/* \
    /var/log/*

ENV PATH="/root/.local/bin:/opt/google-cloud-sdk/bin:${PATH}"

# 確認
RUN gh --version && node --version && npm --version && aws --version && gcloud --version && session-manager-plugin --version

# ユーザー設定
ARG USERNAME="ubuntu"
RUN groupadd -f users; \
    groupadd -f docker; \
    if ! id -u $USERNAME >/dev/null 2>&1; then \
    useradd -m -s /bin/bash $USERNAME; \
    fi; \
    usermod -aG users,docker $USERNAME; \
    echo '%users ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/users; \
    echo "$USERNAME ALL=(ALL:ALL) NOPASSWD: ALL" > /etc/sudoers.d/$USERNAME; \
    chmod 0440 /etc/sudoers.d/users /etc/sudoers.d/$USERNAME

USER ${USERNAME}
WORKDIR /tmp

# ユーザー用ツール（uv, claude, kiro, playwright）
RUN set -eux; \
    # uv for user
    curl -LsSf https://astral.sh/uv/install.sh | sh; \
    # Claude Code CLI
    curl -fsSL https://claude.ai/install.sh | bash; \
    # Kiro CLI (Linux ZIP method for x86_64 and aarch64)
    ARCH=$(uname -m); \
    if [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "aarch64" ]; then \
    curl --proto '=https' --tlsv1.2 -sSf "https://desktop-release.q.us-east-1.amazonaws.com/latest/kirocli-${ARCH}-linux.zip" -o 'kirocli.zip' && \
    unzip kirocli.zip && \
    KIRO_CLI_SKIP_SETUP=1 ./kirocli/install.sh && \
    rm -rf kirocli.zip kirocli; \
    else \
    echo "Unsupported architecture: $ARCH, skipping Kiro CLI installation"; \
    fi; \
    # Playwright browsers
    npx playwright install --with-deps chromium; \
    # クリーンアップ。--with-deps の sudo apt や Node24/npm11 の
    # /tmp/node-compile-cache が root 所有ファイルを残すため sudo で削除する
    # (非 root の rm では Permission denied になりビルドが失敗する)。
    sudo rm -rf /tmp/* \
    ~/.cache \
    ~/.npm \
    ~/.config/yarn \
    ~/.local/share/pnpm \
    ~/.claude.json.backup*

RUN set -eux; \
    # AI CLI aliases
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc; \
    echo 'alias claude='\''claude --dangerously-skip-permissions "$@"'\''' >> ~/.bashrc; \
    echo 'alias claudb='\''CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-west-2 claude --dangerously-skip-permissions "$@"'\''' >> ~/.bashrc; \
    echo 'alias gemini='\''GOOGLE_GENAI_USE_VERTEXAI=true gemini --yolo "$@"'\''' >> ~/.bashrc; \
    echo 'alias codex='\''codex --dangerously-bypass-approvals-and-sandbox "$@"'\''' >> ~/.bashrc; \
    echo 'alias kiro='\''kiro-cli chat --trust-all-tools "$@"'\''' >> ~/.bashrc; \
    echo 'complete -o default claudb kiro' >> ~/.bashrc; \
    git config --global credential.helper store; \
    # Configure Claude Code hooks
    mkdir -p ~/.claude; \
    echo '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"echo '\''unset AWS_REGION'\'' >> \"$CLAUDE_ENV_FILE\""}]}]}}' > ~/.claude/settings.json;

# tmux-first / tmux-clean: tmux セッションの整理コマンド
#
# VS Code のウィンドウが異常終了 (クラッシュ / ホスト再起動 / 接続断) すると、
# VS Code サーバー側に pty が取り残され、tmux クライアントだけがセッションに
# 繋がったまま残る。統合ターミナルの起動スクリプトは「クライアントが繋がって
# いるセッション = 使用中」と判定するため、<repo>-1 に戻れず <repo>-2、
# <repo>-3 ... と新しいセッションを作り続けてしまう。tmux-first はその居座り
# クライアントを切断し、一番若い番号のセッションへ現在の端末を切り替える。
# tmux-clean は置き去りになったセッションを削除する (既定では未アタッチかつ
# シェル以外を実行していないもののみ)。詳細は docs/user/environment-variables.md を参照。
COPY --chmod=0755 <<'TMUX_FIRST_SH' /usr/local/bin/tmux-first
#!/bin/sh
# tmux-first - 同じベース名のセッションのうち、最も番号の小さいものへ切り替える。
#
# VS Code のウィンドウが異常終了 (クラッシュ / ホスト再起動 / 接続断) すると、
# VS Code サーバー側に pty が取り残され、tmux クライアントだけがセッションに
# 繋がったまま残る。次に開いた端末はそのセッションを「使用中」と判断するため、
# adminer-1 に戻らず adminer-2, adminer-3 ... と新しいセッションを作ってしまう。
#
# このコマンドは、対象セッションに居座っている自分以外のクライアントを切断し、
# 一番若い番号のセッションへ現在の端末を切り替える。
#
#   tmux-first             現在のセッション名からベース名を推定 (adminer-2 -> adminer)
#   tmux-first adminer     ベース名を明示
#   tmux-first -n          切り替えず、対象と切断予定のクライアントだけ表示
#
set -eu

DRY=0
BASE=""
usage() {
	cat <<'USAGE'
使い方: tmux-first [-n|--dry-run] [ベース名]

  引数なし    現在の tmux セッション名から末尾の -数字 を除いたものをベース名にする
              (tmux の外にいる場合はカレントディレクトリ名)
  ベース名    明示指定 (例: tmux-first adminer)
  -n          切り替えを行わず、対象セッションと切断予定のクライアントを表示する
USAGE
}

for a in "$@"; do
	case "$a" in
	-n | --dry-run) DRY=1 ;;
	-h | --help)
		usage
		exit 0
		;;
	-*)
		echo "tmux-first: 不明なオプション: $a" >&2
		usage >&2
		exit 2
		;;
	*) BASE="$a" ;;
	esac
done

command -v tmux >/dev/null 2>&1 || {
	echo "tmux-first: tmux が見つかりません" >&2
	exit 1
}
tmux list-sessions >/dev/null 2>&1 || {
	echo "tmux-first: tmux サーバーが起動していません" >&2
	exit 1
}

# ベース名: 引数 > 現在のセッション名から末尾の -数字 を除去 > カレントディレクトリ名
if [ -z "$BASE" ]; then
	if [ -n "${TMUX:-}" ]; then
		BASE=$(tmux display-message -p '#S' | sed -E 's/-[0-9]+$//')
	else
		BASE=$(basename "$PWD" | tr './:' '___')
	fi
fi
[ -n "$BASE" ] || {
	echo "tmux-first: ベース名を決定できません" >&2
	exit 1
}

# <ベース名>-<数字> のセッションを集め、数字の昇順で先頭を採る。
# ベース名に '-' や正規表現メタ文字が含まれても壊れないよう awk で前方一致比較する。
TARGET=$(tmux list-sessions -F '#{session_name}' | awk -v b="$BASE" '
	{
		n = length(b)
		if (substr($0, 1, n + 1) == b "-") {
			suf = substr($0, n + 2)
			if (suf ~ /^[0-9]+$/) printf "%d\t%s\n", suf, $0
		}
	}' | sort -n | head -1 | cut -f2-)

if [ -z "$TARGET" ]; then
	echo "tmux-first: '${BASE}-<数字>' に一致するセッションがありません" >&2
	echo "  既存のセッション:" >&2
	tmux list-sessions -F '    - #{session_name}' >&2
	exit 1
fi

# 自分自身のクライアント (これは絶対に切断しない)
ME=""
if [ -n "${TMUX:-}" ]; then
	ME=$(tmux display-message -p '#{client_name}' 2>/dev/null || true)
fi

# 対象セッションに繋がっている自分以外のクライアント = 居座り。
# tmux の中にいるのに自分のクライアントを特定できない場合は、自分自身を切断して
# しまう恐れがあるため切断対象を空にする (切り替えだけ行う)。
GHOSTS=""
if [ -n "${TMUX:-}" ] && [ -z "$ME" ]; then
	echo "tmux-first: 自分のクライアントを特定できないため切断は行いません" >&2
else
	GHOSTS=$(tmux list-clients -t "=$TARGET" -F '#{client_name}' 2>/dev/null |
		grep -v -x -F "${ME:-__no_client__}" || true)
fi

if [ "$DRY" = 1 ]; then
	echo "base   : $BASE"
	echo "target : $TARGET"
	if [ -n "$ME" ]; then
		echo "self   : $ME"
	elif [ -n "${TMUX:-}" ]; then
		echo "self   : (クライアント未接続)"
	else
		echo "self   : (tmux の外)"
	fi
	if [ -n "$GHOSTS" ]; then
		echo "$GHOSTS" | sed 's/^/detach : /'
	else
		echo "detach : (なし)"
	fi
	exit 0
fi

if [ -n "$GHOSTS" ]; then
	echo "$GHOSTS" | while IFS= read -r c; do
		[ -n "$c" ] || continue
		echo "tmux-first: 居座りクライアントを切断: $c -> $TARGET"
		tmux detach-client -t "$c" || true
	done
fi

if [ -n "${TMUX:-}" ]; then
	if [ "$(tmux display-message -p '#S')" = "$TARGET" ]; then
		echo "tmux-first: すでに $TARGET です"
	else
		echo "tmux-first: $TARGET へ切り替えます"
		tmux switch-client -t "=$TARGET"
	fi
else
	echo "tmux-first: $TARGET にアタッチします"
	exec tmux attach-session -t "=$TARGET"
fi
TMUX_FIRST_SH

COPY --chmod=0755 <<'TMUX_CLEAN_SH' /usr/local/bin/tmux-clean
#!/bin/sh
# tmux-clean - 同じベース名の不要な tmux セッションを掃除する。
#
# tmux-first で若い番号のセッションへ戻したあと、置き去りになった <ベース名>-<数字>
# のセッションを削除するためのコマンド。既定では安全側に倒し、次のセッションは
# 残す:
#
#   - 残すセッション (keeper): tmux 内なら現在のセッション、外なら最小番号のもの
#   - アタッチ中のセッション  : 誰かが使用中とみなす
#   - シェル以外が動いているセッション: ビルドやサーバを実行中とみなす
#
# 上記も含めて消したい場合は -f を付ける。
#
#   tmux-clean            現在のセッション名からベース名を推定して掃除
#   tmux-clean adminer    ベース名を明示
#   tmux-clean -n         削除せず、対象と除外理由だけ表示 (dry-run)
#   tmux-clean -f         アタッチ中・実行中のセッションも削除する
#
set -eu

DRY=0
FORCE=0
BASE=""

usage() {
	cat <<'USAGE'
使い方: tmux-clean [-n|--dry-run] [-f|--force] [ベース名]

  引数なし    現在の tmux セッション名から末尾の -数字 を除いたものをベース名にする
              (tmux の外にいる場合はカレントディレクトリ名)
  ベース名    明示指定 (例: tmux-clean adminer)
  -n          削除せず、削除対象と除外理由を表示する
  -f          アタッチ中・シェル以外を実行中のセッションも削除する

残すセッション (keeper) は tmux 内なら現在のセッション、tmux 外なら最小番号のもの。
USAGE
}

for a in "$@"; do
	case "$a" in
	-n | --dry-run) DRY=1 ;;
	-f | --force) FORCE=1 ;;
	-h | --help)
		usage
		exit 0
		;;
	-*)
		echo "tmux-clean: 不明なオプション: $a" >&2
		usage >&2
		exit 2
		;;
	*) BASE="$a" ;;
	esac
done

command -v tmux >/dev/null 2>&1 || {
	echo "tmux-clean: tmux が見つかりません" >&2
	exit 1
}
tmux list-sessions >/dev/null 2>&1 || {
	echo "tmux-clean: tmux サーバーが起動していません" >&2
	exit 1
}

# ベース名: 引数 > 現在のセッション名から末尾の -数字 を除去 > カレントディレクトリ名
if [ -z "$BASE" ]; then
	if [ -n "${TMUX:-}" ]; then
		BASE=$(tmux display-message -p '#S' | sed -E 's/-[0-9]+$//')
	else
		BASE=$(basename "$PWD" | tr './:' '___')
	fi
fi
[ -n "$BASE" ] || {
	echo "tmux-clean: ベース名を決定できません" >&2
	exit 1
}

# <ベース名>-<数字> のセッションを数字の昇順で列挙する。
# ベース名に '-' や正規表現メタ文字が含まれても壊れないよう awk で前方一致比較する。
SESSIONS=$(tmux list-sessions -F '#{session_name}' | awk -v b="$BASE" '
	{
		n = length(b)
		if (substr($0, 1, n + 1) == b "-") {
			suf = substr($0, n + 2)
			if (suf ~ /^[0-9]+$/) printf "%d\t%s\n", suf, $0
		}
	}' | sort -n | cut -f2-)

if [ -z "$SESSIONS" ]; then
	echo "tmux-clean: '${BASE}-<数字>' に一致するセッションがありません" >&2
	exit 1
fi

# keeper: tmux 内なら現在のセッション、外なら最小番号。
# 現在のセッションがベース名に属さない場合も最小番号を残す。
KEEP=""
if [ -n "${TMUX:-}" ]; then
	CUR=$(tmux display-message -p '#S' 2>/dev/null || true)
	for s in $SESSIONS; do
		[ "$s" = "$CUR" ] && KEEP="$s" && break
	done
fi
[ -n "$KEEP" ] || KEEP=$(printf '%s\n' "$SESSIONS" | head -1)
echo "tmux-clean: base=$BASE keeper=$KEEP"

# シェルとみなすコマンド名 (これしか動いていなければ削除して良いと判断する)
is_shell() {
	case "$1" in
	sh | bash | zsh | dash | fish | ksh | tcsh | csh | ash | -sh | -bash | -zsh | login) return 0 ;;
	*) return 1 ;;
	esac
}

KILLED=0
KEPT=0
for s in $SESSIONS; do
	if [ "$s" = "$KEEP" ]; then
		echo "  keep  $s  (keeper)"
		KEPT=$((KEPT + 1))
		continue
	fi

	# display-message の -t は target-pane を取るためセッション指定に使えない。
	# また tmux は書式内のタブを '_' に置換するため区切り文字での parse も不可。
	# list-sessions のフィルタで名前完全一致のものだけを取り出す。
	ATT=$(tmux list-sessions -F '#{session_attached}' \
		-f "#{==:#{session_name},$s}" 2>/dev/null)
	BUSY=""
	for c in $(tmux list-panes -s -t "=$s" -F '#{pane_current_command}' 2>/dev/null); do
		if ! is_shell "$c"; then
			BUSY="$c"
			break
		fi
	done

	if [ "$FORCE" = 0 ] && [ "${ATT:-0}" != 0 ]; then
		echo "  keep  $s  (アタッチ中: クライアント ${ATT} 個)"
		KEPT=$((KEPT + 1))
		continue
	fi
	if [ "$FORCE" = 0 ] && [ -n "$BUSY" ]; then
		echo "  keep  $s  (実行中: $BUSY)"
		KEPT=$((KEPT + 1))
		continue
	fi

	if [ "$DRY" = 1 ]; then
		echo "  KILL  $s  (dry-run)"
	else
		tmux kill-session -t "=$s"
		echo "  KILL  $s"
	fi
	KILLED=$((KILLED + 1))
done

if [ "$DRY" = 1 ]; then
	echo "tmux-clean: 削除対象 ${KILLED} 件 / 残す ${KEPT} 件 (dry-run のため実行していません)"
else
	echo "tmux-clean: 削除 ${KILLED} 件 / 残す ${KEPT} 件"
fi
TMUX_CLEAN_SH

RUN echo "alias tmux1='tmux-first'" >> ~/.bashrc \
 && echo "alias tmuxc='tmux-clean'" >> ~/.bashrc

# entrypoint と dind
COPY --chmod=755 entrypoint.sh /entrypoint.sh
COPY --chmod=755 dind /usr/local/bin/dind

WORKDIR /work
ENTRYPOINT ["/entrypoint.sh"]
