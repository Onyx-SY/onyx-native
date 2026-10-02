# -*- coding: utf-8 -*-
"""修 `source <script>` 的 `{ ... }` 块 / 函数定义被逐行拆散。

根因：Onyx 的 source 是**内置命令**（Onyx.py 注册 "source": _lazy_source →
bin/source_cmd.py），在 Python 层逐行解析脚本再交给 parse_and_execute。
但它的结构识别只认 if/for/while/until/case + heredoc + 续行 \\，
**不认 `{ ... }` 花括号块和 `foo() { ... }` 函数定义** → 被逐行拆开送给
底层 shell → bash 报 "unexpected end of file from '{'" 并卡在 `>` 续行。
"""
import shutil

P = 'bin/source_cmd.py'
shutil.copy(P, P + '.bak')
s = open(P, encoding='utf-8').read()


def rep(a, b, cnt=1):
    global s
    n = s.count(a)
    assert n == cnt, (a[:60], n)
    s = s.replace(a, b)


rep("        struct_stack = []           # 结构栈 if/for/while/case",
    "        struct_stack = []           # 结构栈 if/for/while/case\n"
    "        brace_depth = 0             # 花括号块深度 { }\n"
    "        paren_depth = 0             # 子 shell 深度 ( )")

rep("""            # ============= 结构化命令 if/for/while/case =============
            first_word = stripped.split()[0] if stripped else \"\"""",
    """            # ============= 花括号块 / 子 shell { } ( ) =============
            # 此前未识别 → `{ ... }` 块与 `foo() { ... }` 函数定义被逐行
            # 拆开送给底层 shell，bash 报 "unexpected end of file from '{'"
            # 并卡在续行提示符（用户只能一直敲到 EOF）。
            _br = stripped.count('{') - stripped.count('}')
            _pa = stripped.count('(') - stripped.count(')')
            if brace_depth or paren_depth or _br > 0 or _pa > 0:
                brace_depth += _br
                paren_depth += _pa
                block_buffer.append(line)
                if brace_depth <= 0 and paren_depth <= 0:
                    brace_depth = 0
                    paren_depth = 0
                    full_cmd = "\\n".join(block_buffer)
                    parse_and_execute(full_cmd, is_recursive=True)
                    block_buffer = []
                continue

            # ============= 结构化命令 if/for/while/case =============
            first_word = stripped.split()[0] if stripped else \"\"""")

open(P, 'w', encoding='utf-8').write(s)
print('SOURCE BLOCK FIX APPLIED')
