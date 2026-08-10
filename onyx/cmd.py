#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Onyx Standalone Command Executor
单次命令执行入口，复用 Onyx 完整的安全和权限逻辑
"""

import sys
import argparse
import signal

# 导入 Onyx 模块（假设在同一目录或 PYTHONPATH 中）
try:
    import Onyx
except ImportError:
    # 如果直接运行，尝试添加父目录到路径
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import Onyx


def signal_handler(signum, frame):
    """信号处理：优雅退出"""
    print("\nReceived termination signal, exiting...")
    sys.exit(128 + signum)


def main():
    parser = argparse.ArgumentParser(
        description='Onyx Single Command Executor - Execute one command with full security',
        epilog='Example: python cmd.py -c "ls -la"'
    )
    parser.add_argument(
        '-c', '--command',
        type=str,
        required=True,
        help='Command to execute (with full Onyx security)'
    )
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Suppress non-error output (errors still displayed)'
    )

    args = parser.parse_args()

    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 静默模式：重定向 stdout 到 null
    original_stdout = sys.stdout
    if args.quiet:
        sys.stdout = open('/dev/null', 'w')

    try:
        # 调用 Onyx 的单次命令执行函数
        exit_code = Onyx.run_command_once(args.command)
    except KeyboardInterrupt:
        print("\n^C")
        exit_code = 130
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        exit_code = 1
    finally:
        # 恢复 stdout
        if args.quiet:
            sys.stdout.close()
            sys.stdout = original_stdout

    sys.exit(exit_code)


if __name__ == "__main__":
    main()