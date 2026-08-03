# -*- coding: utf-8 -*-
"""
code_analysis.py — 代码分析工具包（纯辅助，零主观判断）

包含 4 个 AI 工具（注册定义 + 执行器）：
  - py_diagnostics / py_symbols — 零依赖内置实现（ast/compile），仅支持 .py
  - LspDiagnostics / LspSymbols — 外部语言服务器（多语言），仅 error 级别诊断

设计原则：
  - 没有任何决策：诊断只做真实编译尝试，只在编译失败时报错；
  - 唯一用途：归拢所有定义的函数/类及其精确位置（path:line）。

工具定义通过 get_native_tools(make_tool) 工厂暴露，由 bin/ai_cmd.py 统一注册；
执行器（exec_*）由 _BUILTIN_HANDLERS 直接引用。
"""

from __future__ import annotations

from ..i18n import _ as _i18n
from ..py_analysis import exec_py_diagnostics, exec_py_symbols
from lib.lsp_client import LspManager

# 与 bin/ai_cmd.py 中权限常量同值（纯字符串）
PERM_READONLY = "ReadOnly"


# ── 外部语言服务器客户端管理器（进程级复用，按语言缓存） ──
LSP_MANAGER = LspManager()


def shutdown_lsp() -> None:
    """关闭所有外部语言服务器进程（进程退出时调用）。"""
    LSP_MANAGER.shutdown_all()


# ── 执行器 ──

def exec_lsp_diagnostics(path: str) -> str:
    """获取文件的编译错误（仅 error 级别）。纯辅助：不返回任何警告/提示等主观信息。"""
    try:
        client = LSP_MANAGER.get_client(path)
        if not client:
            return _i18n("lsp_no_server", "bilingual", path=path)
        diagnostics = client.diagnostics(path)
        errors = [d for d in diagnostics if d.severity == "error"]
        if not errors:
            return _i18n("lsp_no_errors", "bilingual", path=path)
        lines = [_i18n("lsp_error_header", "bilingual", path=path, count=len(errors))]
        for d in errors:
            lines.append(f"  ❌ {d.line}:{d.character} {d.message}")
        return "\n".join(lines)
    except Exception as e:
        return _i18n("lsp_diag_failed", "bilingual", err=e)


def exec_lsp_symbols(path: str) -> str:
    """归拢文件中的所有函数/类定义及其位置（唯一用途）。"""
    try:
        client = LSP_MANAGER.get_client(path)
        if not client:
            return _i18n("lsp_no_server", "bilingual", path=path)
        symbols = client.symbols(path)
        if not symbols:
            return _i18n("lsp_no_symbols", "bilingual", path=path)
        lines = [_i18n("lsp_symbols_header", "bilingual", path=path, count=len(symbols))]
        kind_icons = {
            "function": "ƒ", "method": "ƒ", "class": "◈", "interface": "◇",
            "module": "📦", "variable": "■", "constant": "🔶", "property": "◈",
            "enum": "📋", "namespace": "📁",
        }
        for sym in symbols:
            icon = kind_icons.get(sym.kind, "•")
            lines.append(f"  {icon} `{sym.name}` ({sym.kind}) @ {sym.path}:{sym.line}")
        return "\n".join(lines)
    except Exception as e:
        return _i18n("lsp_symbols_failed", "bilingual", err=e)


# ── 工具注册定义（由 ai_cmd.build_native_tools 调用） ──

def get_native_tools(make_tool) -> list:
    """返回本工具包的全部工具定义（OpenAI function calling 格式）。

    make_tool 由调用方传入（bin/ai_cmd.py 的 _make_tool），
    其内部会自动将描述双语化（i18n 模块）。
    """
    return [
        make_tool(
            "py_diagnostics",
            "检查 Python 文件的语法/编译错误（仅真实编译失败时报错，无任何启发式判断）。零依赖，纯内置实现。",
            {"path": {"type": "string", "description": ".py 文件路径"}},
            ["path"], PERM_READONLY,
        ),
        make_tool(
            "py_symbols",
            "归拢文件中所有定义的函数/类及其精确位置（path:line）。零依赖，纯内置实现。",
            {"path": {"type": "string", "description": ".py 文件路径"}},
            ["path"], PERM_READONLY,
        ),
        make_tool(
            "LspDiagnostics",
            "获取文件的编译错误（仅 error 级别，不显示警告/提示等主观信息）。自动根据文件扩展名启动对应的语言服务器。",
            {"path": {"type": "string", "description": "文件路径"}},
            ["path"], PERM_READONLY,
        ),
        make_tool(
            "LspSymbols",
            "归拢文件中的所有函数/类定义及其位置（符号名、类型、path:line）。",
            {"path": {"type": "string", "description": "文件路径"}},
            ["path"], PERM_READONLY,
        ),
    ]
