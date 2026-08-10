# lib/terminal/colors.py
"""
ANSI terminal color constants — drop-in replacement for colorama.
Uses raw ESC codes only; no library init needed, no prompt_toolkit conflict.

Usage:
    from lib.terminal.colors import Fore, Style
    print(f"{Fore.RED}error{Style.RESET_ALL}")
"""


class Fore:
    BLACK   = '\033[30m'
    RED     = '\033[31m'
    GREEN   = '\033[32m'
    YELLOW  = '\033[33m'
    BLUE    = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN    = '\033[36m'
    WHITE   = '\033[37m'
    RESET   = '\033[39m'

    # bright variants
    LIGHTBLACK   = '\033[90m'
    LIGHTRED     = '\033[91m'
    LIGHTGREEN   = '\033[92m'
    LIGHTYELLOW  = '\033[93m'
    LIGHTBLUE    = '\033[94m'
    LIGHTMAGENTA = '\033[95m'
    LIGHTCYAN    = '\033[96m'
    LIGHTWHITE   = '\033[97m'


class Back:
    BLACK   = '\033[40m'
    RED     = '\033[41m'
    GREEN   = '\033[42m'
    YELLOW  = '\033[43m'
    BLUE    = '\033[44m'
    MAGENTA = '\033[45m'
    CYAN    = '\033[46m'
    WHITE   = '\033[47m'
    RESET   = '\033[49m'


class Style:
    RESET_ALL  = '\033[0m'
    BRIGHT     = '\033[1m'
    DIM        = '\033[2m'
    NORMAL     = '\033[22m'
