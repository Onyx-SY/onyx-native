# -*- coding: utf-8 -*-
"""step-6 验证：多行完整性判定 34 条正反例（含旧实现的全部误判/漏判）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
fails = []

from lib.terminal.repl.multiline import is_complete, auto_indent, continuation_prompt  # noqa: E402

INCOMPLETE = [
    ('echo "unclosed', "未闭合双引号（旧实现漏判）"),
    ("echo 'unclosed", "未闭合单引号（旧实现漏判）"),
    ("if true; then", "if 缺 fi"),
    ("for i in 1 2 3", "for 缺 do（旧实现漏判）"),
    ("for i in 1 2 3; do", "for 缺 done"),
    ("while true; do", "while 缺 done"),
    ("case $x in", "case 缺 esac"),
    ("echo foo |", "行尾管道"),
    ("echo foo &&", "行尾 &&"),
    ("echo foo \\", "行尾反斜杠"),
    ("cat << EOF", "heredoc 未结束"),
    ("cat << EOF\nhello", "heredoc 未结束（多行）"),
    ("echo $(unclosed", "未闭合 $("),
    ("echo {unclosed", "未闭合 {"),
    ("if true; then\n  echo hi", "块未闭合（跨行）"),
    ("f() {", "函数体未闭合"),
    ('echo "a\nb', "跨行未闭合引号"),
    ("case $x in\n a) echo 1;;", "case 未闭合（跨行）"),
    ("if true; then\n  if false; then\n    echo x\n  fi", "嵌套 if 外层缺 fi"),
    ("echo `unclosed", "未闭合反引号"),
    ("ls |\n grep x |", "行尾管道（多行）"),
]

COMPLETE = [
    ('echo "if x; then y; fi"', "引号内的 if/then/fi（旧实现误判）"),
    ("# if x; then", "注释里的 if（旧实现误判）"),
    ('echo "a << b"', "引号内的 <<（旧实现误判为 heredoc）"),
    ('printf "%s" "cat << EOF"', "引号内的 heredoc（旧实现误判）"),
    ("echo $((1<<3))", "算术展开（旧实现误判为 heredoc）"),
    ("if true; then echo 1; fi", "单行 if"),
    ("for i in 1 2 3; do echo $i; done", "单行 for"),
    ("echo hello", "普通命令"),
    ("cat << EOF\nhello\nEOF", "完整 heredoc"),
    ("cat <<- EOF\n\thello\nEOF", "带 tab 的 heredoc"),
    ("ls -la | grep py", "管道"),
    ('echo "quoted \\" escape"', "引号内转义"),
    ("echo foo; echo bar", "分号"),
    ("f() { echo hi; }", "单行函数"),
    ("case $x in a) echo 1;; esac", "单行 case"),
    ('echo "multi\\nline"', "字面反斜杠 n"),
    ("echo $HOME/${VAR}/x", "变量展开"),
    ("a=1 && b=2", "赋值 + &&"),
    ("if true; then\n  if false; then\n    echo x\n  fi\nfi", "嵌套 if 完整"),
    ('echo "a" && echo "b"', "带引号的 &&"),
    ("echo 'single' \"double\" `backtick`", "三种引号"),
    ("echo for", "for 作为参数（非关键字）"),
]

print("── 应当判为「不完整」 ──────────────────────────────")
for text, why in INCOMPLETE:
    r = is_complete(text)
    ok = not r.complete
    print(f"{'✅' if ok else '❌'} {why:28} {text!r:34} → {r.reason or 'complete'}")
    if not ok:
        fails.append(f"漏判: {why}")

print("\n── 应当判为「完整」 ────────────────────────────────")
for text, why in COMPLETE:
    r = is_complete(text)
    ok = r.complete
    print(f"{'✅' if ok else '❌'} {why:28} {text!r:34} → {r.reason or 'complete'}")
    if not ok:
        fails.append(f"误判: {why}")

print("\n── 续行提示符 / 自动缩进 ───────────────────────────")
r = is_complete("if true; then")
print("continuation_prompt →", repr(continuation_prompt(r)))
if continuation_prompt(r).strip() != "fi":
    fails.append("续行提示符未显示待闭合记号")
print("auto_indent('if true; then') =", repr(auto_indent("if true; then")))
if auto_indent("if true; then") != "    ":
    fails.append("块开启未缩进")
print("auto_indent('  fi') =", repr(auto_indent("if true; then\n  echo x\n  fi")))
print("auto_indent('echo hi') =", repr(auto_indent("echo hi")))
if auto_indent("echo hi") != "":
    fails.append("普通行被误缩进")

print("\n统计：不完整用例 %d 条 / 完整用例 %d 条" % (len(INCOMPLETE), len(COMPLETE)))
print("FAILS:", fails if fails else "none")
sys.exit(1 if fails else 0)
