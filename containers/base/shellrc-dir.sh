# ~/.bashrc から bash が source する。実行しないため shebang を置かない
# shellcheck shell=bash
# 作り直しても残るシェルの設定を読む (PLAN70)。
#
# 対話シェルの ~/.bashrc から、/etc/devbase/ai-cli-aliases.sh の**後**に読まれる。
# 置き場所 (DEVBASE_SHELLRC_DIR、空なら ~/.shellrc.d) の直下の *.sh を名前の順に読む。
# 置き場所の実体はアカウントグループのボリュームにあり、entrypoint.sh が symlink を張る。
#
# - 外部コマンドもサブシェルも起動しない (対話シェルの起動を遅くしないため)
# - グロブの展開の間だけ failglob と dotglob を切り、読む前に利用者の状態へ戻す。
#   failglob が有効なまま一致が無いと no match を出し、dotglob が有効だと *.sh が
#   . で始まる名前にも一致する
# - 変数名を __devbase_ で始め、最後に消す。利用者の変数 (f など) を上書きしない
__devbase_shellrc_dir="${DEVBASE_SHELLRC_DIR:-$HOME/.shellrc.d}"
if [ -d "$__devbase_shellrc_dir" ]; then
    # 1 つずつ別の変数に控える。1 本の文字列にまとめて分割で戻すと、利用者の IFS に
    # 空白が無いとき (IFS=$'\n\t' など) に分割されず戻せない
    __devbase_shellrc_failglob=
    __devbase_shellrc_dotglob=
    shopt -q failglob && __devbase_shellrc_failglob=1
    shopt -q dotglob && __devbase_shellrc_dotglob=1
    shopt -u failglob dotglob
    __devbase_shellrc_files=("$__devbase_shellrc_dir"/*.sh)
    [ -n "$__devbase_shellrc_failglob" ] && shopt -s failglob
    [ -n "$__devbase_shellrc_dotglob" ] && shopt -s dotglob
    # 一致が無いときはグロブが文字列のまま残る。-f の判定で落ちる
    for __devbase_shellrc_file in "${__devbase_shellrc_files[@]}"; do
        if [ -f "$__devbase_shellrc_file" ] && [ -r "$__devbase_shellrc_file" ]; then
            # 読むのは利用者が置き場所に置くファイルで、検査の時点では存在しない
            # shellcheck source=/dev/null
            . "$__devbase_shellrc_file"
        fi
    done
fi
# 終了状態を 0 にする役も兼ねる (最後のファイルが読めなくても非 0 を残さない)
unset __devbase_shellrc_dir __devbase_shellrc_file __devbase_shellrc_files \
    __devbase_shellrc_failglob __devbase_shellrc_dotglob
