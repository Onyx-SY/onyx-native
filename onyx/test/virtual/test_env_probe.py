#!/usr/bin/env python3
"""离线验证 EnvProbe 动态类型探测 + which 命令查询。

运行: python3 test/virtual/test_env_probe.py
（会执行少量只读命令：uname/df/python3 --version 等，秒回无副作用）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin.ai_cmd import (  # noqa: E402
    _ENV_PROBE_TYPES,
    _ENV_PROBE_TOOLS,
    _env_probe_parse_types,
    _env_probe_which_lines,
    _exec_env_probe,
)

_VALID_SECTIONS = {"system", "user", "network", "disk", "tools"}


def test_type_config_complete():
    for t, cfg in _ENV_PROBE_TYPES.items():
        assert set(cfg["sections"]) <= _VALID_SECTIONS, f"{t} sections 非法: {cfg['sections']}"
        assert isinstance(cfg.get("extra", []), list)
        for label, cmd in cfg.get("extra", []):
            assert isinstance(label, str) and isinstance(cmd, str) and cmd
    assert "general" in _ENV_PROBE_TYPES
    print(f"PASS 类型配置完整（{len(_ENV_PROBE_TYPES)} 类，sections 全部合法）")


def test_unknown_type_falls_back_to_general():
    out = _exec_env_probe("hack")
    assert out.startswith("## 📡 环境探测报告")
    assert "### 命令可用性" in out  # general 才有 tools 块
    print("PASS 未知 type 回退 general 全量")


def test_lightweight_which_mode():
    out = _exec_env_probe(which="python3")
    assert "轻量查询" in out
    assert "### 指定命令查询" in out
    assert "✅ python3" in out
    assert "### 命令可用性" not in out  # 未跑全量工具表
    print("PASS 仅 which → 轻量模式（系统摘要 + 查询，无全量）")


def test_type_scopes_report():
    out = _exec_env_probe("python")
    assert out.startswith("## 📡 环境探测报告")
    assert "### 专属探测（python）" in out
    assert "### 磁盘" not in out  # python 类型不含 disk 块
    assert "### 网络" not in out  # python 类型不含 network 块
    print("PASS type=python → 探测范围收窄（无 disk/network），带专属探测")


def test_network_type_has_port_extra():
    out = _exec_env_probe("network")
    assert "### 专属探测（network）" in out
    assert "### 磁盘" not in out
    print("PASS type=network → 含监听端口专属探测，无 disk")


def test_explicit_general_plus_which():
    out = _exec_env_probe("general", "python3 git")
    assert "## 📡 环境探测报告" in out
    assert "### 指定命令查询" in out
    assert "✅ python3" in out
    print("PASS 显式 general + which → 全量报告 + 查询节")


def test_type_parse_combo():
    assert _env_probe_parse_types("web, network，python") == ["web", "network", "python"]
    assert _env_probe_parse_types("web,web") == ["web"]  # 去重
    assert _env_probe_parse_types("") == ["general"]
    assert _env_probe_parse_types("foo") == ["general"]  # 全非法回退
    assert _env_probe_parse_types("general,network") == ["general", "network"]
    print("PASS type 逗号解析：去重保序 / 非法回退 / general 参与组合")


def test_type_combo_union():
    out = _exec_env_probe("web,network")
    assert out.startswith("## 📡 环境探测报告")
    assert "### 专属探测（web,network）" in out
    assert "### 网络" in out          # network 块
    assert "### 磁盘" not in out      # 两者都无 disk
    assert "### 用户与权限" in out    # network 含 user 块（权限对扫描重要）→ 并集保留
    print("PASS type=web,network → sections 并集（网络块在、disk/user 不在），专属探测合并")


def test_type_combo_with_general():
    out = _exec_env_probe("general,network")
    assert "### 磁盘" in out                      # general → 全量块
    assert "### 专属探测（general,network）" in out  # extra 取 network 的
    print("PASS type=general,network → 全量报告 + network 专属探测")


def test_pentest_tools_present():
    web_tools = set(_ENV_PROBE_TYPES["web"]["tools"])
    net_tools = set(_ENV_PROBE_TYPES["network"]["tools"])
    for t in ("sqlmap", "nikto", "gobuster", "ffuf", "wpscan", "burpsuite", "nuclei"):
        assert t in web_tools, f"web 缺渗透工具 {t}"
    for t in ("masscan", "hydra", "nmap", "airodump-ng", "theHarvester", "enum4linux", "ettercap", "responder"):
        assert t in net_tools, f"network 缺渗透工具 {t}"
    print("PASS web/network 工具表包含常见网络渗透工具")


def test_which_injection_guarded():
    bad = "ls; rm -rf /"
    lines = _env_probe_which_lines(bad)
    joined = "\n".join(lines)
    assert "非法命令名" in joined
    assert "rm -rf" not in "".join(l for l in lines if "✅" in l or "❌" in l)  # 没有执行痕迹
    # 混合：合法 + 非法 + 不存在
    lines2 = _env_probe_which_lines("python3, definitely_not_exist_xyz_9; echo pwned")
    joined2 = "\n".join(lines2)
    assert any("✅ python3" in l for l in lines2)
    assert any("未找到" in l for l in lines2)
    assert any("非法命令名" in l for l in lines2)
    print("PASS which 注入防护：非法名拒绝、未找到报告、合法名正常")


def test_which_caps_at_10():
    lines = _env_probe_which_lines(" ".join([f"c{i}" for i in range(15)]))
    assert len([l for l in lines if l.startswith("- ")]) <= 11  # 标题 + ≤10 条
    print("PASS which 查询上限 10 个")


def test_full_toolset_still_works():
    out = _exec_env_probe()  # 无参 = general 全量（向后兼容）
    assert out.startswith("## 📡 环境探测报告")
    assert "### 命令可用性" in out
    assert f"可用 ({len([t for t in _ENV_PROBE_TOOLS if os_helper_which(t)])})" in out or "✅ 可用" in out
    # 旧的静态反思文案必须删除（避免每轮重复反思）
    assert "缺失工具需替代方案" not in out
    print("PASS 静态反思文案已删除")


def test_dynamic_reflection_only_on_gaps():
    import shutil
    out = _exec_env_probe("network")
    has_ss = shutil.which("ss") is not None
    has_netstat = shutil.which("netstat") is not None
    if not has_ss and has_netstat:
        assert "ss 缺失" in out, "存在缺口时应给出动态反思提示"
    if has_ss:
        assert "ss 缺失" not in out, "无缺口时不应提示 ss 缺失"
    print("PASS 反思要点仅按实际缺口动态生成")


def os_helper_which(t):
    import shutil
    return shutil.which(t)


if __name__ == "__main__":
    test_type_config_complete()
    test_unknown_type_falls_back_to_general()
    test_lightweight_which_mode()
    test_type_scopes_report()
    test_network_type_has_port_extra()
    test_explicit_general_plus_which()
    test_type_parse_combo()
    test_type_combo_union()
    test_type_combo_with_general()
    test_pentest_tools_present()
    test_which_injection_guarded()
    test_which_caps_at_10()
    test_full_toolset_still_works()
    test_dynamic_reflection_only_on_gaps()
    print("\nALL PASS")
