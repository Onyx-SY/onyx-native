# -*- coding: utf-8 -*-
"""感官系统端到端自校验：eyes 工具 → 视觉 → 事件 → 唤醒文本 → 限流 → 子代理隔离。

运行：python3 test/test_senses_smoke.py   （退出码 0 = 通过）
"""
import os
import shutil
import sys
import tempfile
import time

HOME = tempfile.mkdtemp(prefix="senses_smoke_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bin.ai_lib import senses  # noqa: E402
from bin.ai_lib.mcp_exec import execute_mcp_tool  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  ✅ %s" % name)
    else:
        print("  ❌ %s %s" % (name, detail))
        FAILS.append(name)


def main():
    work = tempfile.mkdtemp(prefix="senses_smoke_work_")
    target = os.path.join(work, "app.log")
    with open(target, "w", encoding="utf-8") as f:
        f.write("boot ok\n")

    m = senses._mgr()
    cfg = m.cfg
    cfg["enabled"] = True
    cfg["user_approved"] = True
    cfg["notify"]["debounce_sec"] = 0
    cfg["notify"]["min_interval_sec"] = 0
    cfg["attentions"] = []
    m._save()

    print("== 1. 通过 mcp_exec 分发调用 eyes ==")
    ok, out = execute_mcp_tool("eyes", {"action": "add_file", "path": target,
                                        "start": 1, "end": 0,
                                        "trigger": "on_change", "debounce_sec": 0},
                               "filesystem", "low")
    check("eyes add_file 分发成功", ok and "已设置视觉点" in out, out[:120])
    m.stop()   # 关掉后台线程，测试用手动 tick 保证确定性

    ok, out = execute_mcp_tool("eyes", {"action": "list"}, "filesystem", "low")
    check("eyes list 报出数量", ok and "共 1 个注意力" in out, out[:120])

    print("== 2. 外部改动 → 事件 → 唤醒文本（含改前/改后）==")
    m._tick()
    senses.pop_events()
    with open(target, "a", encoding="utf-8") as f:
        f.write("ERROR: disk full\n")
    m._tick()
    evs = senses.pop_events()
    check("产生事件", len(evs) == 1, len(evs))
    prompt = senses.build_wake_prompt(evs)
    check("唤醒文本含【主动感知】", "主动感知" in prompt, prompt[:60])
    check("唤醒文本含改前", "改前" in prompt and "boot ok" in prompt)
    check("唤醒文本含改后", "改后" in prompt and "disk full" in prompt)

    print("== 3. 限流：can_notify / mark_notified ==")
    cfg["notify"]["min_interval_sec"] = 60
    m._save()
    check("首次可提醒", senses.can_notify())
    senses.mark_notified()
    check("间隔内不可提醒", not senses.can_notify())
    cfg["notify"]["min_interval_sec"] = 0
    m._save()
    check("间隔归零后可提醒", senses.can_notify())

    print("== 4. 忙碌标记：AI 回合进行中不投递 ==")
    senses.turn_begin()
    check("turn_begin → is_busy", senses.is_busy())
    senses.turn_end()
    check("turn_end → 不 busy", not senses.is_busy())

    print("== 5. 工具注意力（关键词命中）==")
    m.cfg["attentions"] = []
    m._save()
    ok, out = execute_mcp_tool("eyes", {"action": "add_tool", "tool": "web_search",
                                        "match": "内存", "trigger": "on_tool"},
                               "filesystem", "low")
    check("add_tool 分发成功", ok and "已设置工具注意力" in out, out[:120])
    m.stop()
    senses.pop_events()
    senses.emit_tool_event("web_search", "q=内存占用", "内存使用 87% (high)")
    evs = senses.pop_events()
    check("命中关键词产生事件", len(evs) == 1, len(evs))
    senses.emit_tool_event("web_search", "q=天气", "晴天 25 度")
    check("未命中不产生事件", len(senses.pop_events()) == 0)

    print("== 6. 子代理隔离：白名单里没有 eyes ==")
    from bin.ai_lib.subagent import TOOL_SETS, gate_rejection
    leaked = [k for k, v in TOOL_SETS.items() if "eyes" in v]
    check("所有子代理类型都看不到 eyes", not leaked, leaked)
    # 运行时闸门：即使模型幻觉调用，也必须被显式拒绝（专属理由，不是白名单兜底）
    r_eyes = gate_rejection("eyes", "explore")
    check("运行时闸门显式拒绝 eyes", bool(r_eyes) and "本体私有" in r_eyes, r_eyes)
    check("白名单内工具放行", gate_rejection("read_file", "explore") is None)
    r_w = gate_rejection("write_file", "explore")
    check("白名单外工具仍拒绝", bool(r_w) and "白名单" in r_w, r_w)

    print("== 7. 视觉块注入开关 ==")
    m.cfg["enabled"] = False
    m._save()
    check("关闭时不注入视觉", senses.get_vision_block() == "")
    m.cfg["enabled"] = True
    m._save()
    check("开启时注入视觉", "视觉" in senses.get_vision_block())

    print("== 8. /config 开关（set_enabled）==")
    senses.set_enabled(False)
    check("可关闭", not senses.is_enabled())
    senses.set_enabled(True, approve=True)
    check("可开启", senses.is_enabled())

    print("== 9. 安全 / 性能加固 ==")
    sshdir = os.path.join(HOME, ".ssh")
    os.makedirs(sshdir, exist_ok=True)
    keyp = os.path.join(sshdir, "id_rsa")
    with open(keyp, "w", encoding="utf-8") as f:
        f.write("PRIVATE\n")
    ok, out = execute_mcp_tool("eyes", {"action": "add_file", "path": keyp},
                               "filesystem", "low")
    check("eyes 拒绝敏感路径", "拒绝" in out, out[:120])
    blob = os.path.join(work, "blob.bin")
    with open(blob, "wb") as f:
        f.write(bytes([0, 1]) + b"binary")
    ok, out = execute_mcp_tool("eyes", {"action": "add_file", "path": blob},
                               "filesystem", "low")
    check("eyes 拒绝二进制", "二进制" in out, out[:120])
    check("视觉块含不可信声明", "不可信数据" in senses.get_vision_block())

    m.cfg["attentions"] = []
    m._save()
    execute_mcp_tool("eyes", {"action": "add_file", "path": target, "start": 1, "end": 0,
                              "trigger": "on_change", "debounce_sec": 0},
                     "filesystem", "low")
    m._tick()
    n0 = m._read_count
    m._tick()
    m._tick()
    check("stat 快路径：未变化不重读", m._read_count == n0, (n0, m._read_count))

    m.stop()
    shutil.rmtree(HOME, ignore_errors=True)
    print()
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
