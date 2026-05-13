"""Shell-related utility commands"""

from devbase.utils.shell import get_shell_rc_file


def cmd_shell_rc() -> int:
    """Print the appropriate shell RC file path to stdout (single line).

    Intended for `source "$(devbase shell-rc)"` so users can reload the
    file `devbase init` wrote to without needing to know which one
    (zsh -> ~/.zshrc, bash on macOS -> ~/.bash_profile,
    bash on Linux -> ~/.bashrc).
    """
    print(get_shell_rc_file())
    return 0
