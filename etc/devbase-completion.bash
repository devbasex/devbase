 # bash completion for devbase

_devbase_completions() {
    local cur prev words cword
    _init_completion 2>/dev/null || {
        COMPREPLY=()
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
        cword=$COMP_CWORD
    }

    local commands="init status container ct env plugin pl snapshot ss up down login build ps help"
    local container_subcommands="up down ps login logs scale build"
    local env_subcommands="init sync list set get delete edit project"
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
            # container subcommand arguments
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
