 # bash completion for devbase

# projects/ 配下のプロジェクト名 (symlink / 実ディレクトリ) を列挙する。
# `devbase project up <name>` やトップレベルシノニム `devbase up <name>` の
# name 補完に使う。
_devbase_project_names() {
    local devbase_root
    devbase_root="${DEVBASE_ROOT:-$(dirname "$(dirname "$(command -v devbase 2>/dev/null)")" 2>/dev/null)}"
    local projects_dir="${devbase_root}/projects"
    if [ -d "$projects_dir" ]; then
        # -L で symlink を辿り -type d で判定することで、実ディレクトリと
        # 「ディレクトリへの symlink」のみを列挙する (壊れた symlink / ファイルへの
        # symlink は除外)。zsh 側 (*(N-/:t)) と挙動を揃える。GNU 専用の -xtype は
        # BSD/macOS find で動かないため避け、POSIX/BSD/GNU 共通の -L を使う。
        # basename 化は `xargs -r`(GNU 拡張) を避け、各行を POSIX パラメータ展開
        # ${p##*/} で処理する (外部プロセス不要・空入力でも安全・BSD/macOS 互換)。
        find -L "$projects_dir" -mindepth 1 -maxdepth 1 -type d 2>/dev/null \
            | while IFS= read -r p; do
                printf '%s\n' "${p##*/}"
            done
    fi
}

_devbase_completions() {
    local cur prev words cword
    _init_completion 2>/dev/null || {
        COMPREPLY=()
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
        cword=$COMP_CWORD
    }

    local commands="init status shell-rc project container ct env plugin pl snapshot ss up down login build ps scale list help"
    # project / container は同じサブコマンド群 (container は非推奨だが補完は維持)。
    local project_subcommands="up down ps login logs scale build list"
    local container_subcommands="up down ps login logs scale build"
    local env_subcommands="init sync list set get delete edit project export import"
    local plugin_subcommands="list install uninstall update info sync repo"
    local repo_subcommands="add remove list refresh"
    local snapshot_subcommands="create list restore copy delete rotate"

    case "$cword" in
        1)
            COMPREPLY=($(compgen -W "$commands" -- "$cur"))
            ;;
        2)
            case "$prev" in
                login)
                    COMPREPLY=($(compgen -W "1 2" -- "$cur"))
                    ;;
                # トップレベルシノニム: up/down/scale は [name] を取るため
                # プロジェクト名を補完する (login=index / build=image は対象外)。
                up|down|scale)
                    COMPREPLY=($(compgen -W "$(_devbase_project_names)" -- "$cur"))
                    ;;
                # ps は [name] と -a フラグの両方を取る (project ps と同じ挙動)。
                ps)
                    if [[ "$cur" == -* ]]; then
                        COMPREPLY=($(compgen -W "--all -a" -- "$cur"))
                    else
                        COMPREPLY=($(compgen -W "$(_devbase_project_names)" -- "$cur"))
                    fi
                    ;;
                # list は位置引数を取らず --interactive のみ。`-*` ガードを外し
                # 常にフラグ候補を出す (zsh 側 _arguments と挙動を揃える)。
                list)
                    COMPREPLY=($(compgen -W "--interactive -i" -- "$cur"))
                    ;;
                project)
                    COMPREPLY=($(compgen -W "$project_subcommands" -- "$cur"))
                    ;;
                container|ct)
                    COMPREPLY=($(compgen -W "$container_subcommands" -- "$cur"))
                    ;;
                env)
                    COMPREPLY=($(compgen -W "$env_subcommands" -- "$cur"))
                    ;;
                plugin|pl)
                    COMPREPLY=($(compgen -W "$plugin_subcommands" -- "$cur"))
                    ;;
                snapshot|ss)
                    COMPREPLY=($(compgen -W "$snapshot_subcommands" -- "$cur"))
                    ;;
            esac
            ;;
        3)
            local group="${words[1]}"
            # トップレベルシノニム ps: `devbase ps web -<TAB>` (group=ps, cword=3)
            # でも name 位置が埋まった後にフラグを補完する。project ps と対称化。
            if [ "$group" = "ps" ]; then
                if [[ "$cur" == -* ]]; then
                    COMPREPLY=($(compgen -W "--all -a" -- "$cur"))
                fi
            fi
            # project subcommand arguments (推奨グループ)
            if [ "$group" = "project" ]; then
                case "$prev" in
                    up|down)
                        COMPREPLY=($(compgen -W "$(_devbase_project_names)" -- "$cur"))
                        ;;
                    login)
                        COMPREPLY=($(compgen -W "1 2" -- "$cur"))
                        ;;
                    scale)
                        # `project scale <name> N` / `project scale N` の両形。
                        # name 補完を提示する (数値はユーザが直接入力)。
                        COMPREPLY=($(compgen -W "$(_devbase_project_names)" -- "$cur"))
                        ;;
                    ps)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--all -a" -- "$cur"))
                        else
                            COMPREPLY=($(compgen -W "$(_devbase_project_names)" -- "$cur"))
                        fi
                        ;;
                    logs)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--follow -f --tail" -- "$cur"))
                        else
                            COMPREPLY=($(compgen -W "$(_devbase_project_names)" -- "$cur"))
                        fi
                        ;;
                    # list は位置引数を取らず --interactive のみ。`-*` ガードを外し
                    # 常にフラグ候補を出す (zsh 側 _arguments と挙動を揃える)。
                    list)
                        COMPREPLY=($(compgen -W "--interactive -i" -- "$cur"))
                        ;;
                esac
            fi
            # container subcommand arguments (非推奨: project へ移行してください)
            if [ "$group" = "container" ] || [ "$group" = "ct" ]; then
                case "$prev" in
                    login)
                        COMPREPLY=($(compgen -W "1 2" -- "$cur"))
                        ;;
                    scale)
                        COMPREPLY=($(compgen -W "1 2 3 4 5" -- "$cur"))
                        ;;
                    ps)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--all -a" -- "$cur"))
                        fi
                        ;;
                    logs)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--follow -f --tail" -- "$cur"))
                        fi
                        ;;
                esac
            fi
            # env subcommand arguments
            if [ "$group" = "env" ]; then
                case "$prev" in
                    init)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--reset" -- "$cur"))
                        fi
                        ;;
                    list)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--global -g --project -p --reveal -r --keys -k" -- "$cur"))
                        fi
                        ;;
                    set)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--project -p" -- "$cur"))
                        fi
                        ;;
                    export)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--include-project --exclude-project --no-global --no-metadata --recipient --passphrase-env --passphrase-stdin --force-unencrypted --unsafe-allow-unencrypted-bucket" -- "$cur"))
                        fi
                        ;;
                    import)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--merge --replace-keys --replace --dry-run --identity --passphrase-env --passphrase-stdin --include-project --exclude-project --no-global --no-metadata --merge-metadata --backup-dir --keep-last" -- "$cur"))
                        fi
                        ;;
                esac
            fi
            # plugin subcommand arguments
            if [ "$group" = "plugin" ] || [ "$group" = "pl" ]; then
                case "$prev" in
                    list)
                        COMPREPLY=($(compgen -W "--available" -- "$cur"))
                        ;;
                    install)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--link --all" -- "$cur"))
                        fi
                        ;;
                    uninstall|update|info)
                        local devbase_root
                        devbase_root="${DEVBASE_ROOT:-$(dirname "$(dirname "$(command -v devbase 2>/dev/null)")" 2>/dev/null)}"
                        local plugin_dir="${devbase_root}/plugins"
                        if [ -d "$plugin_dir" ]; then
                            local plugins
                            plugins=$(find "$plugin_dir" -mindepth 1 -maxdepth 1 -type d -o -type l | xargs -I{} basename {} 2>/dev/null)
                            COMPREPLY=($(compgen -W "$plugins" -- "$cur"))
                        fi
                        ;;
                    repo)
                        COMPREPLY=($(compgen -W "$repo_subcommands" -- "$cur"))
                        ;;
                esac
            fi
            # snapshot subcommand arguments
            if [ "$group" = "snapshot" ] || [ "$group" = "ss" ]; then
                case "$prev" in
                    create)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--name --full" -- "$cur"))
                        fi
                        ;;
                    restore)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--point" -- "$cur"))
                        fi
                        ;;
                    rotate)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--keep" -- "$cur"))
                        fi
                        ;;
                esac
            fi
            ;;
        4)
            local group="${words[1]}"
            # project ps/logs: name 位置が埋まった後 (例: `project ps web -<TAB>`)
            # でもフラグを補完する。subcommand は words[2]。
            if [ "$group" = "project" ]; then
                case "${words[2]}" in
                    ps)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--all -a" -- "$cur"))
                        fi
                        ;;
                    logs)
                        if [[ "$cur" == -* ]]; then
                            COMPREPLY=($(compgen -W "--follow -f --tail" -- "$cur"))
                        fi
                        ;;
                esac
            fi
            # plugin install flags after source argument
            if [ "$group" = "plugin" ] || [ "$group" = "pl" ]; then
                if [ "${words[2]}" = "install" ]; then
                    if [[ "$cur" == -* ]]; then
                        COMPREPLY=($(compgen -W "--link --all" -- "$cur"))
                    fi
                fi
                # plugin repo subcommand arguments
                if [ "${words[2]}" = "repo" ]; then
                    case "$prev" in
                        add)
                            if [[ "$cur" == -* ]]; then
                                COMPREPLY=($(compgen -W "--name" -- "$cur"))
                            fi
                            ;;
                        remove|refresh)
                            local devbase_root
                            devbase_root="${DEVBASE_ROOT:-$(dirname "$(dirname "$(command -v devbase 2>/dev/null)")" 2>/dev/null)}"
                            local yml="${devbase_root}/plugins.yml"
                            if [ -f "$yml" ] && command -v python3 >/dev/null 2>&1; then
                                local repos
                                repos=$(python3 -c "
import yaml, sys
try:
    d = yaml.safe_load(open('$yml'))
    print(' '.join(r['name'] for r in (d or {}).get('repositories', [])))
except Exception:
    pass
" 2>/dev/null)
                                COMPREPLY=($(compgen -W "$repos" -- "$cur"))
                            fi
                            ;;
                    esac
                fi
            fi
            ;;
    esac
}

complete -F _devbase_completions devbase
