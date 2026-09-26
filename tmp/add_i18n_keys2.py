# -*- coding: utf-8 -*-
"""step-2/7 补充：/doctor 面板 + TUI 危险确认 的双语键。

用法：python3 tmp/add_i18n_keys2.py
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANG_JSON = os.path.join(ROOT, "bin", "ai_lib", "lang.json")
BACKUP = os.path.join(ROOT, "tmp", "lang.json.bak2")

NEW_KEYS = {
    # ── TUI 危险命令确认框 ──
    "tui_cmd_label": ("命令", "Command"),
    "tui_risk_label": ("风险", "Risk"),
    "tui_user_refused": ("用户拒绝执行", "User refused"),
    # ── /doctor 健康检查（此前仅中文）──
    "doctor_item_key": ("AI 密钥", "AI key"),
    "doctor_item_platform": ("平台模型", "Platform & models"),
    "doctor_item_mcp": ("MCP 服务器", "MCP servers"),
    "doctor_item_memory": ("记忆目录", "Memory dir"),
    "doctor_item_tools": ("工具目录", "Tools dir"),
    "doctor_item_runtime": ("运行环境", "Runtime"),
    "doctor_item_memmode": ("记忆模式", "Memory mode"),
    "doctor_item_lang": ("语言", "Language"),
    "doctor_key_info": (
        "平台={plat} 模型={model} 协议={proto}",
        "platform={plat} model={model} protocol={proto}",
    ),
    "doctor_key_unconfigured": (
        "未配置（运行 ai 命令引导配置）",
        "not configured (run `ai` to set up)",
    ),
    "doctor_key_read_fail": ("读取失败: {err}", "read failed: {err}"),
    "doctor_platforms_loaded": ("已加载 {n} 个平台", "{n} platforms loaded"),
    "doctor_models_load_fail": ("models.json 加载失败", "models.json failed to load"),
    "doctor_module_error": ("模块异常", "module error"),
    "doctor_memory_records": ("{path}（{n} 条记录）", "{path} ({n} records)"),
    "doctor_memory_empty": ("{path}（尚未创建记录）", "{path} (no records yet)"),
    "doctor_tools_count": ("{n} 个工具", "{n} tools"),
    "doctor_tools_missing": ("tools/ 不存在（TBS 模式）", "tools/ not found (TBS mode)"),
    "doctor_unreadable": ("无法读取", "unreadable"),
    "doctor_memmode_project": ("project（当前目录专属）", "project (this directory only)"),
    "doctor_memmode_global": ("global（全局 library）", "global (global library)"),
}


def main() -> int:
    with open(LANG_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    shutil.copy2(LANG_JSON, BACKUP)

    added = 0
    for key, (cn, en) in NEW_KEYS.items():
        for lang, text in (("chinese", cn), ("english", en)):
            if key not in data[lang]:
                added += 1
            data[lang][key] = text

    tmp = LANG_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, LANG_JSON)

    cn_keys, en_keys = set(data["chinese"]), set(data["english"])
    if cn_keys != en_keys:
        print(f"❌ 中英键不一致：{sorted(cn_keys ^ en_keys)}")
        return 1
    print(f"✅ 写入 {len(NEW_KEYS)} 个键（新增 {added}），中英各 {len(cn_keys)} 条，完全对齐")
    return 0


if __name__ == "__main__":
    sys.exit(main())
