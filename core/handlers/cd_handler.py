"""core/handlers/cd_handler.py — cd 命令处理（从 Onyx.py 提取，使用 AppContext）"""

import os
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import AppContext


def handle_cd(ctx: "AppContext", cmd_parts: List[str], request_id: str) -> None:
    """cd 命令：切换工作目录，支持沙箱路径限制"""
    from core.i18n import t, set_lang
    set_lang(ctx.global_config["display_info"]["language"]["current"])

    try:
        from Onyx import parse_options
        supported_short = ['-P', '-L']
        supported_long = ['--physical', '--logical', '--help', '--version']
        options, paths = parse_options(cmd_parts[1:], supported_short, supported_long)

        if '--help' in options:
            print(t("cd_handler.usage")); print(t("cd_handler.desc")); return
        if '--version' in options:
            version = ctx.global_config['program_info'].get('version', '1.0.0')
            print(t("cd_handler.version", version=version)); return

        follow_symlink = not ('-P' in options or '--physical' in options)
        last_dir = os.getcwd()

        if not paths:
            target_path = ctx.USER_HOME_DIR
        elif len(paths) == 1 and paths[0] == '-':
            if 'OLDPWD' not in os.environ:
                print(t("cd_handler.oldpwd_not_set")); return
            target_path = os.environ['OLDPWD']
        elif len(paths) == 1 and paths[0] == '~':
            target_path = ctx.USER_HOME_DIR
        elif len(paths) == 1 and paths[0] == '/':
            target_path = ctx.ROOT_DIR if ctx._SANDBOX_ENABLED else "/"
        elif len(paths) == 1 and paths[0].startswith('~/'):
            target_path = os.path.abspath(os.path.join(ctx.USER_HOME_DIR, paths[0][2:]))
        else:
            target_path = os.path.abspath(paths[0])

        from core.path_ops import resolve_path
        target_path = resolve_path(ctx, target_path)
        if ' ' in target_path:
            target_path = f'"{target_path}"'

        real_path = target_path.strip('"')
        if not follow_symlink:
            real_path = os.path.realpath(real_path)

        if ctx.OS_OR_TBS != "OS":
            effective_root = ctx.ROOT_DIR if ctx._SANDBOX_ENABLED else "/"
            root_abs = os.path.abspath(effective_root)
            if not os.path.abspath(real_path).startswith(root_abs):
                print(t("cd_handler.cannot_exit_root", path=paths[0] if paths else '..'))
                return

        if not os.path.exists(real_path):
            print(t("cd_handler.no_such_file", path=paths[0] if paths else '~')); return
        if not os.path.isdir(real_path):
            print(t("cd_handler.not_a_dir", path=paths[0] if paths else '~')); return
        if not os.access(real_path, os.X_OK):
            print(t("cd_handler.perm_denied", path=paths[0] if paths else '~')); return

        os.environ['OLDPWD'] = last_dir
        os.chdir(real_path)
        os.environ['PWD'] = real_path

        from Onyx import cache_directory_files, generate_prompt
        cache_directory_files(real_path, request_id)
        from core.log_manager import log_info
        log_info(t("cd_handler.log_cd_success", from_dir=last_dir, to_dir=real_path), request_id)
        generate_prompt()

    except ValueError as e:
        print(f"cd: {str(e)}"); print(t("cd_handler.try_help"))
    except PermissionError:
        print(t("cd_handler.perm_denied", path=paths[0] if paths else "~"))
    except FileNotFoundError:
        print(t("cd_handler.no_such_file", path=paths[0] if paths else "~"))
    except NotADirectoryError:
        print(t("cd_handler.not_a_dir", path=paths[0] if paths else "~"))
    except Exception as e:
        print(t("cd_handler.unexpected_error", err=str(e)))
        from core.log_manager import log_error
        log_error(t("cd_handler.log_cd_fail", err=str(e)), request_id)
