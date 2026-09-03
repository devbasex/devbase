# AI CLI の起動定義。対話シェルの ~/.bashrc から読み込まれる。
#
# ここに置くのは alias と補完の登録だけで、副作用を持つ処理は書かない。
# ~/.bashrc へ直接書き出すのをやめてファイルにしているのは、Docker を起動せずに
# tests/containers/test_ai_cli_aliases.py で振る舞いを固定するためである。
#
# 定義に "$@" は書かない。alias の "$@" は alias の引数ではなくシェルの位置
# パラメータへ展開されるため、引数を渡す働きをしない。引数は alias の展開で
# 末尾へ付く。
#
# 認証方式を決める環境変数もここでは設定しない。プロジェクトが env で選んだ値を
# 起動定義が上書きしてしまうため (PLAN50)。gemini の Vertex AI / OAuth の
# 切り替えは GOOGLE_GENAI_USE_VERTEXAI で行う。

# 各 CLI は開発コンテナの中でだけ使う前提のため、確認プロンプトを省くオプションを
# 既定で付ける。コンテナの外へ影響しない。
alias claude='claude --dangerously-skip-permissions'
# `command` を挟むのは claude の alias を展開させないため。挟まないと
# --dangerously-skip-permissions が 2 度渡り、claude 側の定義を変えたときに
# claudb まで一緒に変わる。
alias claudb='CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-west-2 command claude --dangerously-skip-permissions'
alias gemini='gemini --yolo'
alias codex='codex --dangerously-bypass-approvals-and-sandbox'
alias kiro='kiro-cli chat --trust-all-tools'
alias agy='agy --dangerously-skip-permissions'

complete -o default claudb kiro
