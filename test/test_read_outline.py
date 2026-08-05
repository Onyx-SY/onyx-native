#!/usr/bin/env python3
"""read_file 大纲模式冒烟测试：>64 KiB 自动大纲、head/tail/range 钻取、小文件不折叠。"""
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bin.ai_cmd import _exec_read_file, _fmt_read_size, _build_symbol_outline

# ── 1. 大小格式化 ──
assert _fmt_read_size(319283) == "311.8 KiB", _fmt_read_size(319283)
assert _fmt_read_size(1024) == "1.0 KiB", _fmt_read_size(1024)
assert _fmt_read_size(512) == "512 B", _fmt_read_size(512)
print("PASS: _fmt_read_size")

# ── 2. 构造测试文件 ──
tmp = tempfile.mkdtemp(prefix="read_outline_")
big_py = os.path.join(tmp, "big.py")
small = os.path.join(tmp, "small.txt")
try:
    lines = ["#!/usr/bin/env python3", ""]
    for i in range(1, 1600):  # 1600 个函数 ≈ 69 KiB，超过 64 KiB 阈值
        lines.append(f"def func_{i}(a, b):")
        lines.append(f"    return a + b + {i}")
        lines.append("")
    lines.append("class TopClass:")
    lines.append("    pass")
    content = "\n".join(lines) + "\n"
    with open(big_py, "w", encoding="utf-8") as f:
        f.write(content)
    with open(small, "w", encoding="utf-8") as f:
        f.write("line one\nline two\nline three\n")

    size = os.path.getsize(big_py)
    assert size > 64 * 1024, f"测试文件不够大: {size}"

    # ── 3. 大文件无参数 → 大纲模式 ──
    out = _exec_read_file(big_py)
    assert "outline" in out and "outline mode" in out, "缺少双语 outline 标记"
    assert "前 80 行" in out or "First 80 lines" in out, "缺少前 80 行章节"
    assert "符号大纲" in out or "Symbol outline" in out, "缺少符号大纲章节"
    assert "grep_search" in out, "缺少钻取提示"
    assert "def func_1" in out and "def func_199" in out, "符号大纲内容缺失"
    # 前 80 行方向感：第一行应出现
    assert "1  │ #!/usr/bin/env python3" in out, "前 80 行预览缺失"
    # 符号大纲无数量上限：全部 1599 个函数 + 末尾 class 都应在
    assert "def func_1599" in out, "符号大纲未包含全部函数"
    assert "class TopClass" in out, "符号大纲未包含末尾的 class"
    assert "1600" in out, "符号大纲计数应为 1600（1599 函数 + 1 class）"
    print("PASS: 大文件自动大纲模式")

    # ── 4. range 钻取不受大纲影响 ──
    r = _exec_read_file(big_py, "20-22")
    assert "(range 20-22" in r and "func_7" in r, "range 钻取失败"
    print("PASS: range 钻取")

    # ── 5. head / tail 钻取 ──
    h = _exec_read_file(big_py, head=3)
    assert "(head 3" in h and "func_1" in h and "func_2" not in h, "head 钻取失败"
    t = _exec_read_file(big_py, tail=3)
    assert "(tail 3" in t and "class TopClass" in t and "func_1598" not in t, "tail 钻取失败"
    print("PASS: head/tail 钻取")

    # ── 6. 小文件不折叠 ──
    s = _exec_read_file(small)
    assert "outline 模式" not in s and "(full" in s, "小文件不应进入大纲模式"
    print("PASS: 小文件保持 full 模式")

    # ── 7. 非 Python 语言正则兜底 ──
    go_file = os.path.join(tmp, "main.go")
    with open(go_file, "w", encoding="utf-8") as f:
        f.write("package main\nfunc main() {\n\tprintln(\"hi\")\n}\nfunc helper(x int) int { return x }\n")
    o = _build_symbol_outline(go_file, open(go_file, encoding="utf-8").read().split("\n"), 5)
    assert "func main" in o and "func helper" in o, f"Go 正则大纲失败: {o!r}"
    print("PASS: 非 Python 正则兜底")

    # ── 8. 文件不存在 ──
    nf = _exec_read_file(os.path.join(tmp, "nope.txt"))
    assert "文件不存在" in nf or "File not found" in nf, nf
    print("PASS: 不存在文件报错")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

print("\n全部通过 ✅")
