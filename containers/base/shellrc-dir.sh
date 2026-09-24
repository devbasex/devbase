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
    __devbase_shellrc_opts=
    shopt -q failglob && __devbase_shellrc_opts="$__devbase_shellrc_opts failglob"
    shopt -q dotglob && __devbase_shellrc_opts="$__devbase_shellrc_opts dotglob"
    shopt -u failglob dotglob
    __devbase_shellrc_files=("$__devbase_shellrc_dir"/*.sh)
    if [ -n "$__devbase_shellrc_opts" ]; then
        # 名前ごとに分けて渡すため、引用符で囲まない
        shopt -s $__devbase_shellrc_opts
    fi
    # 一致が無いときはグロブが文字列のまま残る。-f の判定で落ちる
    for __devbase_shellrc_file in "${__devbase_shellrc_files[@]}"; do
        if [ -f "$__devbase_shellrc_file" ] && [ -r "$__devbase_shellrc_file" ]; then
            . "$__devbase_shellrc_file"
        fi
    done
fi
# 終了状態を 0 にする役も兼ねる (最後のファイルが読めなくても非 0 を残さない)
unset __devbase_shellrc_dir __devbase_shellrc_file __devbase_shellrc_files __devbase_shellrc_opts
