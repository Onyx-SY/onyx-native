# bin/ai_bin/help.py
# TUI /help 命令 — 中英双语帮助手册

HELP_TEXT_CN = """
📋 AI TUI 命令帮助
═══════════════════════════════════════

/help       显示此帮助信息
/exit       退出 TUI 模式
/plan       切换 计划模式 / 普通模式
              - 计划模式: AI 只生成计划，不执行操作
              - 普通模式: AI 正常执行命令和工具
/clear      清空当前对话历史
/tools      列出当前可用的 AI 工具

快捷键:
  ESC        退出 TUI（AI 完成任务后会询问）
  ↑↓         在 Plan 确认选择器中移动
  Enter      确认选择
  Ctrl+C     中断当前操作

普通命令行模式:
  ai <问题>          直接向 AI 提问
  ai -tui            进入 TUI 模式
  ai -m plan <问题>   以计划模式提问
  ai -m normal <问题> 以普通模式提问
  ai -f <文件>        将文件内容作为问题上下文
  ai -t <文本>        输入长文本
  ai -cmd false       AI 不自动执行命令
  ai --debug          调试模式

AI 工具有:
  read_file    按行号范围读取文件
  write_file   创建或覆盖写入文件
  edit_file    按行号编辑文件（替换/插入/删除/追加）
"""

HELP_TEXT_EN = """
📋 AI TUI Command Help
═══════════════════════════════════════

/help       Show this help
/exit       Exit TUI mode
/plan       Toggle plan mode / normal mode
              - Plan mode: AI only generates plans, no execution
              - Normal mode: AI executes commands and tools normally
/clear      Clear conversation history
/tools      List available AI tools

Shortcuts:
  ESC        Exit TUI (AI will ask after completing a task)
  ↑↓         Move in plan confirmation selector
  Enter      Confirm selection
  Ctrl+C     Interrupt current operation

Normal CLI mode:
  ai <question>          Ask AI directly
  ai -tui                Enter TUI mode
  ai -m plan <question>   Ask in plan mode
  ai -m normal <question> Ask in normal mode
  ai -f <file>           Use file content as context
  ai -t <text>           Input long text
  ai -cmd false          Don't auto-execute commands
  ai --debug             Debug mode

AI Tools:
  read_file    Read file with line range
  write_file   Create or overwrite file
  edit_file    Edit file by line number (replace/insert/delete/append)
"""


def get_help(lang: str = "chinese") -> str:
    if lang == "english":
        return HELP_TEXT_EN.strip()
    return HELP_TEXT_CN.strip()
