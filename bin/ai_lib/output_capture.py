# -*- coding: utf-8 -*-
"""
output_capture.py — 命令输出实时捕获（RealTimeOutputCatcher / capture_command_output）

从 bin/ai_cmd.py 的 handle_ai 内嵌定义拆出（模块化架构重构）：
- RealTimeOutputCatcher：ANSI 剥离 + 行数限制的流式捕获器；
- capture_command_output：上下文管理器，临时替换 sys.stdout/stderr；
- cleanup_output_cache：AI 工具输出缓存超限清理；
- check_session_file_size：library 会话文件超限轮转。
闭包依赖（log_error/log_info/request_id/缓存引用）全部参数化，无隐式状态。
"""

import os
import re
import sys
import time
from contextlib import contextmanager

# ANSI 转义序列正则（颜色码、光标控制等）
_RE_ANSI = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][0-9;]*[^\x07]*\x07|\x1b\(B')


class RealTimeOutputCatcher:
    def __init__(self, stream_type):
        self.stream_type = stream_type
        self.buffer = []
        self._closed = False
        self._line_count = 0        # 累计行数
        self._ai_triggered = False  # AI 触发时限制显示行数

    def write(self, message):
        if self._closed:
            return
        # 剥离 ANSI 颜色码后再存入 buffer（AI 上下文需要干净文本）
        cleaned = _RE_ANSI.sub('', message) if message else ''
        if cleaned:
            self.buffer.append(cleaned)
        # 显示策略：AI 触发 → 前10行实时显示后截断；用户触发 → 全量
        if self.stream_type == "stdout":
            self._line_count += message.count('\n')
            if self._ai_triggered and self._line_count > 10:
                return  # AI 模式超过10行，停止实时显示
            sys.__stdout__.write(message)
            sys.__stdout__.flush()
        else:
            sys.__stderr__.write(message)
            sys.__stderr__.flush()

    def flush(self):
        if self._closed:
            return
        if self.stream_type == "stdout":
            sys.__stdout__.flush()
        else:
            sys.__stderr__.flush()

    def isatty(self):
        return False

    def close(self):
        self._closed = True

    def get_output(self):
        return "".join(self.buffer)


@contextmanager
def capture_command_output(log_error=None, request_id=""):
    """临时替换 sys.stdout/stderr 为捕获器；异常时记录日志并重抛。"""
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    stdout_catcher = RealTimeOutputCatcher("stdout")
    stderr_catcher = RealTimeOutputCatcher("stderr")

    try:
        sys.stdout = stdout_catcher
        sys.stderr = stderr_catcher
        yield stdout_catcher, stderr_catcher
    except Exception as e:
        if log_error:
            log_error(f"Command execution capture exception: {str(e)}", request_id)
        raise
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        stdout_catcher.close()
        stderr_catcher.close()


def cleanup_output_cache(AI_TOOL_OUTPUT_CACHE, MAX_CACHE_SIZE):
    """AI 工具输出缓存超限时淘汰最旧的 1/5。"""
    if len(AI_TOOL_OUTPUT_CACHE) > MAX_CACHE_SIZE:
        items = list(AI_TOOL_OUTPUT_CACHE.items())
        for k, _ in items[:len(items) // 5]:
            AI_TOOL_OUTPUT_CACHE.pop(k, None)


def check_session_file_size(file_path: str, max_size: int,
                            log_info=None, log_error=None, request_id: str = "") -> bool:
    """library 会话文件超限时轮转（改名 .bak），返回 False 表示已轮转。"""
    if not os.path.exists(file_path):
        return True
    try:
        if os.path.getsize(file_path) > max_size:
            backup_path = f"{file_path}.{int(time.time())}.bak"
            os.rename(file_path, backup_path)
            if log_info:
                log_info(f"Session file exceeded size limit, rotated: {os.path.basename(backup_path)}", request_id)
            return False
    except Exception as e:
        if log_error:
            log_error(f"Failed to check session file size: {str(e)}", request_id)
    return True
