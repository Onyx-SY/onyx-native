# -*- coding: utf-8 -*-
"""plugin 工具（AI 自助注册插件）自校验。

运行：python3 test/test_plugin_ai.py   （退出码 0 = 通过）
"""
import json
import os
import shutil
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="plugin_ai_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bin.ai_lib import plugin_tool, ui  # noqa: E402
from bin import plugin_loader  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("  OK  %s" % name if cond else "  FAIL %s %s" % (name, detail))
    if not cond:
        FAILS.append(name)


def index_exists() -> bool:
    return os.path.isfile(plugin_loader.INDEX_PATH)


def main():
    plugin_loader._is_termux = lambda: False      # 测试里不硬拷贝到真实家目录
    work = tempfile.mkdtemp(prefix="plugin_ai_work_")
    lib = os.path.join(work, "libdemo.so")
    with open(lib, "wb") as f:
        f.write(b"\x7fELF" + b"0" * 64)
    keyf = os.path.join(work, "demo.key")
    with open(keyf, "w", encoding="utf-8") as f:
        f.write("KEYDATA")

    print("== 1. 输入校验 ==")
    out = plugin_tool.dispatch(action="add", lib=os.path.join(work, "nope.so"))
    check("拒绝不存在的库", "不存在" in out, out[:80])
    bad = os.path.join(work, "a.txt")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("x")
    out = plugin_tool.dispatch(action="add", lib=bad)
    check("拒绝非动态库扩展名", "动态链接库" in out, out[:80])
    out = plugin_tool.dispatch(action="add", lib=lib, key=os.path.join(work, "nokey"))
    check("拒绝不存在的授权文件", "授权文件不存在" in out, out[:80])
    out = plugin_tool.dispatch(action="nope")
    check("拒绝未知 action", "未知 action" in out, out[:80])

    print("== 2. 用户拒绝 → 不写索引 ==")
    ui.confirm = lambda msg, **kw: False
    out = plugin_tool.dispatch(action="add", lib=lib, key=keyf)
    check("拒绝时返回取消", "取消" in out, out[:80])
    check("索引未被创建", not index_exists())

    print("== 3. 用户同意 → 注册成功 ==")
    ui.confirm = lambda msg, **kw: True
    out = plugin_tool.dispatch(action="add", lib=lib, key=keyf)
    check("注册成功", "已注册插件" in out, out[:120])
    check("索引已写入", index_exists())
    data = json.load(open(plugin_loader.INDEX_PATH, encoding="utf-8"))
    ent = (data.get("plugins") or {}).get("libdemo")
    check("索引条目含 lib/key", bool(ent) and ent.get("lib") == os.path.realpath(lib)
          and ent.get("key") == os.path.realpath(keyf), ent)

    print("== 4. list / info / verify ==")
    out = plugin_tool.dispatch(action="list")
    check("list 含插件名", "libdemo" in out, out[:200])
    out = plugin_tool.dispatch(action="info", name="libdemo")
    check("info 含 sha256", "sha256" in out, out[:200])
    check("info 含授权文件", "demo.key" in out, out[:300])
    out = plugin_tool.dispatch(action="verify", name="libdemo")
    check("verify 通过（key 存在）", "✅" in out, out[:120])

    print("== 5. 感官联动 ==")
    from bin.ai_lib import senses
    m = senses._mgr()
    m.cfg["enabled"] = True
    m.cfg["user_approved"] = True
    m.cfg["attentions"] = []
    m._save()
    r = senses.add_tool(tool="not_a_real_tool", match="ERROR")
    check("未知工具名仍可设置但给警告", bool(r.get("ok")) and bool(r.get("warning")), r)
    senses.clear_attentions()
    r = senses.add_tool(tool="libdemo", match="ERROR")
    check("插件工具名可设为注意力", bool(r.get("ok")), r)
    check("status 展示可监视工具", "可监视" in senses.status_text())
    senses.clear_attentions()

    print("== 6. remove（确认后移除）==")
    out = plugin_tool.dispatch(action="remove", name="libdemo")
    check("移除成功", "已移除" in out, out[:80])
    data = json.load(open(plugin_loader.INDEX_PATH, encoding="utf-8"))
    check("索引已删除该条目", "libdemo" not in (data.get("plugins") or {}))

    print("== 7. 工具已注册进 AI 工具表 ==")
    from bin.ai_lib.native_tools import build_native_tools
    names = []
    for t in build_native_tools():
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        names.append(fn.get("name"))
    check("native_tools 含 plugin", "plugin" in names)
    from bin.ai_lib.mcp_exec import execute_mcp_tool
    ok, out = execute_mcp_tool("plugin", {"action": "list"}, "filesystem", "low")
    check("mcp 分发 plugin 工具", bool(ok) and ("插件" in str(out)), str(out)[:120])

    print("== 8. help 文档 ==")
    hp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "bin", "help", "help_info", "commands", "ai.json")
    d = json.load(open(hp, encoding="utf-8"))
    cn = d["命令"]["ai"]["Chinese"]
    en = d["命令"]["ai"]["English"]
    check("中文含插件注册章节", "插件注册" in cn and "plugin-add" in cn)
    check("中文含感官章节", "感官系统" in cn and "eyes add_tool" in cn)
    check("英文含 Plugin Registration", "Plugin Registration" in en and "plugin-add" in en)
    check("英文含 Senses", "Senses" in en and "eyes add_tool" in en)
    check("功能：前缀存在（供摘要）", "功能：" in cn and "Function:" in en)

    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(HOME, ignore_errors=True)
    print()
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
