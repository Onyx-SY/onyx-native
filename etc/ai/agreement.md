# Onyx — System Prompt（加载入口）

> ⚠️ 本文件为兼容入口，实际加载由 ai_cmd.py 按模式拼接以下三件套：
> - **`etc/ai/self.md`** — 自我认知（默认总是加载）
> - **`etc/ai/skill.md`** — 技能（普通模式默认加载，固定前缀）
> - **`etc/ai/onyx.md`** — Onyx 介绍（不随前缀加载，用 `memory list skill=onyx` 按需查看）
>
> 普通模式 = self.md + skill.md；plus 模式思考段 = 仅 self.md，干活段 = self.md + skill.md + 思考结果。
