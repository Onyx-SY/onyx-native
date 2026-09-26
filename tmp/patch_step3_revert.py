# -*- coding: utf-8 -*-
"""step-3：多行「换内核，不换交互」。

- 恢复原来的逐行多行流程（去掉 v2 守卫）；
- 把「这段输入是否已经完整」的判定换成状态机（修掉正则表的**误判**：
  `echo "if x; then y; fi"` / `echo "a << b"` / `echo $((1<<3))` 会被当成未闭合结构）。
  交互模型、续行提示符、逐行 prompt 一律不动。
"""
import io
import sys


def rep(path, old, new, tag):
    s = io.open(path, encoding="utf-8").read()
    n = s.count(old)
    if n != 1:
        print(f"❌ {tag}: 命中 {n} 次")
        sys.exit(1)
    io.open(path, "w", encoding="utf-8").write(s.replace(old, new))
    print(f"✅ {tag}")


L = "lib/terminal/input_lib.py"

# ── ① 恢复原来的逐行多行流程 ────────────────────────────────
rep(L, '''        if user_input_stripped and not _REPL_V2:
            # 新输入层：多行已在同一个缓冲区里完成（Enter 绑定 + 完整性判定），
            # 再走旧的逐行循环会把整段命令拆散。
            multiline_result = _process_multiline_input(''',
    '''        if user_input_stripped:
            multiline_result = _process_multiline_input(''',
    "恢复逐行多行流程")

# ── ② 判定内核换成状态机（保留原实现作兜底）────────────────
rep(L, '''    # python 块以缩进闭合（无显式终止符），用严格 AST 判定：
    # 原始代码必须能直接解析（不能借助 _fix_incomplete_code 式的自动补全）。
    if expected_syntax == "python":''',
    '''    # ── 判定内核：逐字符状态机（引号 / 转义 / 注释 / 括号 / heredoc / 关键字）──
    # 旧内核是正则表，会**误判**：
    #     echo "if x; then y; fi"   → 当成未闭合的 if 块
    #     echo "a << b"             → 当成 heredoc
    #     echo $((1<<3))            → 当成 heredoc
    # 换内核后这些单行命令不再被拖进续行模式。交互模型（逐行 prompt + 原续行提示符）
    # 完全不变 —— 只换"算不算完整"这一处判断。
    if expected_syntax != "python":
        try:
            from lib.terminal.repl.multiline import is_complete as _sm_is_complete
            _shell = "powershell" if _is_cmd() else "bash"
            return bool(_sm_is_complete(text, _shell).complete)
        except Exception:
            pass

    # python 块以缩进闭合（无显式终止符），用严格 AST 判定：
    # 原始代码必须能直接解析（不能借助 _fix_incomplete_code 式的自动补全）。
    if expected_syntax == "python":''',
    "判定内核换状态机")

print("done")
