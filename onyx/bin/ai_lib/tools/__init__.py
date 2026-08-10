# -*- coding: utf-8 -*-
"""
bin/ai_lib/tools/ — Onyx AI 独立工具包

每个工具族一个模块，只做纯辅助工作（零决策、零主观判断）：
  - code_analysis.py — 代码分析工具（py_diagnostics / py_symbols /
                        LspDiagnostics / LspSymbols）

约定：
  - 工具定义通过 get_native_tools(make_tool) 工厂暴露，由 bin/ai_cmd.py 统一注册；
  - 执行器（exec_*）由 _BUILTIN_HANDLERS 直接引用；
  - 模块内不 import bin.ai_cmd（避免循环依赖），共享常量自行定义。
"""

from . import code_analysis

__all__ = ["code_analysis"]
