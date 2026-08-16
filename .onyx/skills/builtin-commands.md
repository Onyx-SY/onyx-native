# Onyx 内置命令参考（Built-in Commands）

> 原位于 etc/ai/agreement.md 的系统提示中，为节省每轮 token 移入 skill 目录。
> 需要时由 AI 加载本文件查阅；不要在任务开始时自动注入。

## 命令速查

- `manage set <key> <value>` — 设置配置（language / mcp / debug-times / clean-log-time / assistant）
- `manage clean <cache|logs|all>` — 清理缓存/日志
- `activite -m <low/mid/adv>` — 切换活跃模式；adv 需要密码：不要询问用户，直接生成命令
- `sado <command>` — 提权执行；必须位于命令行最前面
- `switch-prompt <list|preview <theme>|switch <theme>>` — 主题切换
- `autocmd add <cmd>` — 添加自动命令
- `mktool -n <name> -l <lang>` — 创建工具
- `tml install <name>` — 安装 tml 包

## 注意

- 内置命令不支持 bash 语法（由 Onyx 解析；`cd` 也不能用于其中）——这不适用于 `RunCommand`。
