import sys
import os
import json
import uuid
from lib.terminal.colors import Fore, Style
from typing import Dict, List, Tuple, Optional, Any

def get_color_attr(attr_name: str, default: str = "") -> Any:
    """获取颜色属性，兼容颜色模块加载失败场景"""
    try:
        return getattr(Fore, attr_name, default)
    except AttributeError:
        return default

# ====================== 路径配置（修改为读取多级JSON文件）======================
HELP_INFO_ROOT = os.path.join(os.path.dirname(__file__), "help_info")
COMMANDS_DIR = os.path.join(HELP_INFO_ROOT, "commands")
LINUX_DIR = os.path.join(HELP_INFO_ROOT, "linux")
CONFIG_JSON_PATH = ""
LANGUAGE_PATH = ""
TOOLS_DIR = ""

# ====================== 从 help_info 目录加载所有JSON文件 ======================
CMD_HELP_INFO: Dict[str, Any] = {"命令": {}, "工具": {}}

def load_all_help_json():
    """加载commands和linux目录下所有JSON文件的帮助信息"""
    global CMD_HELP_INFO
    cmd_dict = {}
    # 加载通用命令
    if os.path.exists(COMMANDS_DIR):
        for filename in os.listdir(COMMANDS_DIR):
            if filename.endswith(".json"):
                file_path = os.path.join(COMMANDS_DIR, filename)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        cmd_dict.update(data.get("命令", {}))
                except Exception as e:
                    print(f"{get_color_attr('YELLOW')}警告：读取 {filename} 失败：{str(e)}{get_color_attr('RESET', '')}")
    # 加载Linux专属命令
    if os.path.exists(LINUX_DIR):
        for filename in os.listdir(LINUX_DIR):
            if filename.endswith(".json"):
                file_path = os.path.join(LINUX_DIR, filename)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        cmd_dict.update(data.get("命令", {}))
                except Exception as e:
                    print(f"{get_color_attr('YELLOW')}警告：读取 {filename} 失败：{str(e)}{get_color_attr('RESET', '')}")
    # 加载工具信息（从原mktool迁移）
    CMD_HELP_INFO["工具"]["mktool"] = {
        "Chinese": """
名称：mktool
功能：创建标准化工具（自动生成配置/权限文件）
用法：mktool -n <工具名> -l <语言>
支持语言：python/c/cpp
示例：mktool -n orca -l python
说明：工具目录自动生成在 tools/ 下
        """,
        "English": """
Name: mktool
Function: Create standardized tool (auto-generate config/perm files)
Usage: mktool -n <toolname> -l <language>
Supported Languages: python/c/cpp
Example: mktool -n orca -l python
Description: Tool dir generated in tools/
        """
    }
    CMD_HELP_INFO["命令"] = cmd_dict

# 初始化加载所有JSON
load_all_help_json()

def import_onyx_deps():
    """延迟导入Onyx依赖，初始化路径变量（增加容错）"""
    global CONFIG_JSON_PATH, LANGUAGE_PATH, TOOLS_DIR
    try:
        from Onyx import USER_HOME_DIR, ROOT_DIR
        root_dir = ROOT_DIR if ROOT_DIR and os.path.exists(ROOT_DIR) else os.getcwd()
        user_home = USER_HOME_DIR if USER_HOME_DIR and os.path.exists(USER_HOME_DIR) else os.path.expanduser("~")
        
        CONFIG_JSON_PATH = os.path.join(root_dir, "onyx", "etc", "config.json")
        LANGUAGE_PATH = os.path.join(user_home, ".config", "onyx")
        TOOLS_DIR = os.path.join(root_dir, "tools")
    except ImportError:
        CONFIG_JSON_PATH = os.path.join(os.getcwd(), "onyx", "etc", "config.json")
        LANGUAGE_PATH = os.path.join(os.path.expanduser("~"), ".config", "onyx")
        TOOLS_DIR = os.path.join(os.getcwd(), "tools")

# ====================== 核心辅助函数 ======================
def get_current_language() -> str:
    """获取当前语言（优先 language 文件，兜底中文）"""
    import_onyx_deps()
    global CONFIG_JSON_PATH, LANGUAGE_PATH, TOOLS_DIR
    
    if os.path.exists(LANGUAGE_PATH):
        try:
            with open(LANGUAGE_PATH, "r", encoding="utf-8") as f:
                lang = f.read().strip().capitalize()
                return lang if lang in ["Chinese", "English"] else "Chinese"
        except Exception:
            pass
    
    if os.path.exists(CONFIG_JSON_PATH):
        try:
            with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
                return config["display_info"]["language"]["default"]
        except Exception:
            pass
    
    return "Chinese"

def load_config() -> Dict[str, Any]:
    """加载全局配置（文件缺失/格式错误均返回默认配置，不报错）"""
    import_onyx_deps()
    global CONFIG_JSON_PATH, LANGUAGE_PATH, TOOLS_DIR
    
    if not os.path.exists(CONFIG_JSON_PATH):
        return {
            "display_info": {
                "language": {"default": "Chinese"}
            }
        }
    
    try:
        with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
            if "display_info" not in config or "language" not in config["display_info"]:
                raise KeyError("配置结构不完整")
            return config
    except (json.JSONDecodeError, KeyError, Exception) as e:
        print(f"{get_color_attr('YELLOW')}警告：config.json 格式异常，使用默认配置：{str(e)}{get_color_attr('RESET', '')}")
        return {
            "display_info": {
                "language": {"default": "Chinese"}
            }
        }

def log_info(content: str, request_id: str) -> None:
    """信息日志输出"""
    pass

def log_error(content: str, request_id: str) -> None:
    """错误日志输出"""
    pass

def scan_tool_dirs() -> List[str]:
    """扫描 tools/ 目录下的所有工具（仅获取工具名）"""
    import_onyx_deps()
    global CONFIG_JSON_PATH, LANGUAGE_PATH, TOOLS_DIR
    
    tool_names = []
    if not os.path.exists(TOOLS_DIR):
        return tool_names
    
    try:
        for dir_name in os.listdir(TOOLS_DIR):
            dir_path = os.path.join(TOOLS_DIR, dir_name)
            if os.path.isdir(dir_path) and not dir_name.startswith("."):
                entry_files = ["Main.py", "main.py", "tool.py"]
                if any(os.path.exists(os.path.join(dir_path, f)) for f in entry_files):
                    tool_names.append(dir_name)
    except PermissionError:
        print(f"{get_color_attr('YELLOW')}警告：无权限访问工具目录 {TOOLS_DIR}{get_color_attr('RESET', '')}")
    
    return tool_names

def extract_summary(help_text: str) -> str:
    """从帮助文本中提取功能摘要"""
    lines = [line.strip() for line in help_text.split("\n") if line.strip()]
    for line in lines:
        if line.startswith("功能："):
            return line.replace("功能：", "").strip()
        elif line.startswith("Function:"):
            return line.replace("Function:", "").strip()
    return "无详细描述"

# ====================== 命令分类映射 ======================
COMMAND_CATEGORIES: Dict[str, List[str]] = {
    "files": ["cat", "cp", "echo", "ls", "mkdir", "mv", "pwd", "rm", "touch"],
    "navigation": ["cd", "source", "export", "history", "alias", "unalias"],
    "system": ["activite", "manage", "import", "run", "refresh"],
    "ai": ["ai", "mktool"],
    "security": ["sado", "nanosado", "set-adv-pwd"],
    "help_utils": ["help", "clear", "exit", "switch-prompt", "autocmd"],
}

CATEGORY_NAMES_CN = {
    "files": "📁 文件操作",
    "navigation": "🧭 导航与Shell",
    "system": "⚙️ 系统管理",
    "ai": "🤖 AI与工具",
    "security": "🔒 安全",
    "help_utils": "💡 帮助与实用",
}

CATEGORY_NAMES_EN = {
    "files": "📁 File Operations",
    "navigation": "🧭 Navigation & Shell",
    "system": "⚙️ System Management",
    "ai": "🤖 AI & Tools",
    "security": "🔒 Security",
    "help_utils": "💡 Help & Utilities",
}

CATEGORY_COLORS = {
    "files": "CYAN",
    "navigation": "BLUE",
    "system": "YELLOW",
    "ai": "MAGENTA",
    "security": "RED",
    "help_utils": "WHITE",
}

QUICK_START_CN = """  {green}ls{RESET}      查看目录内容
  {green}cd{RESET}       切换目录
  {green}ai{RESET}       与AI对话（如：ai 如何创建Python文件）
  {green}help{RESET}     查看帮助（如：help ls）
  {green}manage{RESET}   系统设置（如：manage set language en）"""

QUICK_START_EN = """  {green}ls{RESET}      List directory contents
  {green}cd{RESET}       Change directory
  {green}ai{RESET}       Chat with AI (e.g.: ai How to create a Python file)
  {green}help{RESET}     Get help (e.g.: help ls)
  {green}manage{RESET}   System settings (e.g.: manage set language en)"""

# ====================== 核心帮助处理逻辑 ======================
def handle_help(cmd_parts: List[str], request_id: str) -> None:
    lang = get_current_language()
    config = load_config()
    
    is_windows = sys.platform.startswith("win32")
    cmd_help_info = CMD_HELP_INFO
    dynamic_tools = scan_tool_dirs()
    # 全局帮助（输入 help）
    if len(cmd_parts) == 1:
        G = get_color_attr
        RESET = G("RESET", "")
        
        print(G("CYAN") + "="*80 + RESET)
        title = "                   ██████╗ ███╗   ██╗██╗   ██╗██╗  ██╗" if lang == "Chinese" else "                   ██████╗ ███╗   ██╗██╗   ██╗██╗  ██╗"
        print(G("GREEN") + title + RESET)
        subtitle = "                   Onyx Toolbox - 帮助手册（{}版）".format("Windows" if is_windows else "Linux/Termux") if lang == "Chinese" else "                   Onyx Toolbox - Help Manual（{} Version）".format("Windows" if is_windows else "Linux/Termux")
        print(G("GREEN") + subtitle + RESET)
        print(G("CYAN") + "="*80 + RESET)
        
        # ===== 快速入门 =====
        quick_title = "\n🚀 快速入门" if lang == "Chinese" else "\n🚀 Quick Start"
        print(G("YELLOW") + quick_title + RESET)
        qs = QUICK_START_CN if lang == "Chinese" else QUICK_START_EN
        print(qs.format(green=G("GREEN"), RESET=RESET))
        
        # ===== 命令分类列表 =====
        cat_names = CATEGORY_NAMES_CN if lang == "Chinese" else CATEGORY_NAMES_EN
        linux_only_cmds = ["sudo", "du", "chmod", "gzip"]
        
        cat_title = "\n📋 命令分类" if lang == "Chinese" else "\n📋 Commands by Category"
        print(G("YELLOW") + cat_title + RESET)
        
        for cat_key in ["files", "navigation", "system", "ai", "security", "help_utils"]:
            cmds_in_cat = [c for c in COMMAND_CATEGORIES[cat_key] if c in cmd_help_info["命令"]]
            if not cmds_in_cat:
                continue
            # Filter linux-only on Windows
            visible_cmds = [c for c in cmds_in_cat if not (is_windows and c in linux_only_cmds)]
            if not visible_cmds:
                continue
            color_key = CATEGORY_COLORS.get(cat_key, "WHITE")
            print(f"\n  {G(color_key)}{cat_names[cat_key]}{RESET}")
            for cmd_name in visible_cmds:
                summary = extract_summary(cmd_help_info["命令"][cmd_name][lang])
                snote = " (Linux)" if cmd_name in linux_only_cmds else ""
                print(f"    {G('GREEN')}{cmd_name:<14}{RESET}{summary}{snote}")
        
        # ===== 动态工具 =====
        if dynamic_tools:
            dt_title = f"\n🔍 动态工具 ({len(dynamic_tools)})" if lang == "Chinese" else f"\n🔍 Dynamic Tools ({len(dynamic_tools)})"
            print(G("YELLOW") + dt_title + RESET)
            print(f"  {G('BLUE')}" + ", ".join(dynamic_tools) + RESET)
            print(f"  {G('WHITE')}" + ("输入 help <工具名> 查看详情" if lang == "Chinese" else "Type help <toolname> for details") + RESET)
        
        # ===== 使用提示 =====
        tip_title = "\n💡 使用提示" if lang == "Chinese" else "\n💡 Usage Tips"
        print(G("YELLOW") + tip_title + RESET)
        if lang == "Chinese":
            tips = [
                ("help <名称>", "查看命令/工具详情（如 help ls、help ai）"),
                ("manage set language en", "一键切换为英文界面"),
                ("ai -m plan <任务>", "AI计划模式，先规划再执行"),
                ("help code-line", "查看快捷键大全"),
                ("activite -m adv", "解锁高级功能"),
                ("refresh", "重新扫描工具索引"),
                ("manage clean cache", "清理缓存释放内存"),
            ]
        else:
            tips = [
                ("help <name>", "View command/tool details (e.g., help ls, help ai)"),
                ("manage set language zh", "Switch to Chinese interface"),
                ("ai -m plan <task>", "AI plan mode — plan first, then execute"),
                ("help code-line", "View keyboard shortcuts"),
                ("activite -m adv", "Unlock advanced features"),
                ("refresh", "Rescan tool index"),
                ("manage clean cache", "Clear cache to free memory"),
            ]
        for i, (cmd, desc) in enumerate(tips, 1):
            print(f"  {G('GREEN')}{i}. {cmd:<30}{RESET}{G('WHITE')}{desc}{RESET}")
        
        # ===== 分类参考 =====
        if lang == "Chinese":
            print(f"\n{G('YELLOW')}📖 分类参考{RESET}")
            print(f"  {G('WHITE')}输入 help 查看全局说明   |   help <命令名> 查看命令详情{RESET}")
        else:
            print(f"\n{G('YELLOW')}📖 Reference{RESET}")
            print(f"  {G('WHITE')}help - show this page   |   help <command> - detailed help{RESET}")
        
        print(G("CYAN") + "\n" + "="*80 + RESET)
        log_info("用户查看全局帮助信息", request_id)
        return
    
    # 查看指定命令/工具帮助
    target_name = cmd_parts[1].lower()
    linux_only_cmds = ["sudo", "du", "chmod", "gzip"]
    # 匹配预设命令
    if target_name in cmd_help_info["命令"]:
        if is_windows and target_name in linux_only_cmds:
            print(get_color_attr("RED") + f"「{target_name}」是Linux/Termux专属命令，Windows系统不支持" + get_color_attr("RESET", "")) if lang == "Chinese" else print(get_color_attr("RED") + f"「{target_name}」is Linux/Termux-only, not supported on Windows" + get_color_attr("RESET", ""))
            return
        
        print(get_color_attr("CYAN") + "="*60 + get_color_attr("RESET", ""))
        cmd_help_title = f"「{target_name}」命令帮助" if lang == "Chinese" else f"「{target_name}」Command Help"
        print(get_color_attr("GREEN") + cmd_help_title + get_color_attr("RESET", ""))
        print(get_color_attr("CYAN") + "="*60 + get_color_attr("RESET", ""))
        print(get_color_attr("WHITE") + cmd_help_info["命令"][target_name][lang] + get_color_attr("RESET", ""))
        print(get_color_attr("CYAN") + "="*60 + get_color_attr("RESET", ""))
        log_info(f"用户查看命令帮助：{target_name}", request_id)
        return
    
    # 匹配预设工具
    if target_name in cmd_help_info["工具"]:
        print(get_color_attr("CYAN") + "="*60 + get_color_attr("RESET", ""))
        tool_help_title = f"「{target_name}」工具帮助" if lang == "Chinese" else f"「{target_name}」Tool Help"
        print(get_color_attr("GREEN") + tool_help_title + get_color_attr("RESET", ""))
        print(get_color_attr("CYAN") + "="*60 + get_color_attr("RESET", ""))
        print(get_color_attr("WHITE") + cmd_help_info["工具"][target_name][lang] + get_color_attr("RESET", ""))
        print(get_color_attr("CYAN") + "="*60 + get_color_attr("RESET", ""))
        log_info(f"用户查看预设工具帮助：{target_name}", request_id)
        return
    
    # 匹配动态工具
    target_tool_name = target_name.lower()
    if target_tool_name in [t.lower() for t in dynamic_tools]:
        tool_dir_name = next(t for t in dynamic_tools if t.lower() == target_tool_name)
        tool_dir_path = os.path.join(TOOLS_DIR, tool_dir_name)
        config_file = os.path.join(tool_dir_path, "config.conf")
        tool_config = {
            "name": tool_dir_name,
            "version": "1.0.0",
            "author": "未知",
            "introduction": "无详细功能介绍",
            "type": "other"
        }
        
        if os.path.exists(config_file):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("name = "):
                            tool_config["name"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                        elif line.startswith("version = "):
                            tool_config["version"] = line.split("=", 1)[1].strip()
                        elif line.startswith("author = "):
                            tool_config["author"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                        elif line.startswith("introduction = "):
                            tool_config["introduction"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                        elif line.startswith("type = "):
                            tool_config["type"] = line.split("=", 1)[1].strip()
            except Exception as e:
                log_error(f"读取工具配置失败：{str(e)}", request_id)
        
        perm_file = os.path.join(tool_dir_path, ".perm")
        tool_perm = "3"
        if os.path.exists(perm_file):
            try:
                with open(perm_file, "r", encoding="utf-8") as f:
                    tool_perm = f.read().strip()
                    tool_perm = tool_perm if tool_perm.isdigit() and 1 <= int(tool_perm) <= 5 else "3"
            except Exception as e:
                log_error(f"读取工具权限失败：{str(e)}", request_id)
        
        entry_files = [f for f in ["Main.py", "main.py", "tool.py"] if os.path.exists(os.path.join(tool_dir_path, f))]
        entry_file = entry_files[0] if entry_files else "未知"
        tool_path = os.path.join(tool_dir_path, entry_file) if entry_file != "未知" else tool_dir_path
        
        print(get_color_attr("CYAN") + "="*60 + get_color_attr("RESET", ""))
        dynamic_tool_title = f"「{tool_dir_name}」工具" if lang == "Chinese" else f"「{tool_dir_name}」Tool Help"
        print(get_color_attr("GREEN") + dynamic_tool_title + get_color_attr("RESET", ""))
        print(get_color_attr("CYAN") + "="*60 + get_color_attr("RESET", ""))
        
        perm_desc = ["", "低", "中低", "中", "高", "极高"] if lang == "Chinese" else ["", "Low", "Low-Medium", "Medium", "High", "Critical"]
        perm_text = perm_desc[int(tool_perm)] if 1 <= int(tool_perm) <= 5 else "未知"
        
        if lang == "Chinese":
            print(f"  工具名称：{tool_config['name']}")
            print(f"  工具版本：{tool_config['version']}")
            print(f"  作者：{tool_config['author']}")
            print(f"  权限等级：{tool_perm}级（{perm_text}风险）")
            print(f"  工具类型：{tool_config['type']}类")
            print(f"  工具路径：{tool_path}")
            print(f"  入口文件：{entry_file}")
            print(f"  功能介绍：{tool_config['introduction']}")
            print(f"  调用方式：直接输入工具名（如 {tool_dir_name}）")
            print(f"  适配系统：Windows/Linux/Termux全兼容")
        else:
            print(f"  Tool Name：{tool_config['name']}")
            print(f"  Version：{tool_config['version']}")
            print(f"  Author：{tool_config['author']}")
            print(f"  Permission Level：Level {tool_perm} ({perm_text} Risk)")
            print(f"  Tool Type：{tool_config['type']}")
            print(f"  Tool Path：{tool_path}")
            print(f"  Entry File：{entry_file}")
            print(f"  Introduction：{tool_config['introduction']}")
            print(f"  Invocation：Enter {tool_dir_name} directly")
            print(f"  Compatible Systems：Windows/Linux/Termux")
        
        print(get_color_attr("CYAN") + "="*60 + get_color_attr("RESET", ""))
        log_info(f"用户查看动态工具帮助：{tool_dir_name}", request_id)
        return
    
    # 未找到目标
    not_found_msg = f"未找到「{target_name}」的帮助信息" if lang == "Chinese" else f"No help information found for 「{target_name}」"
    tip_msg = "提示：输入 'help' 查看所有可用命令/工具" if lang == "Chinese" else "Tip: Enter 'help' to view all available commands/tools"
    print(get_color_attr("RED") + not_found_msg + get_color_attr("RESET", ""))
    print(get_color_attr("YELLOW") + tip_msg + get_color_attr("RESET", ""))
    log_error(f"用户查询不存在的帮助目标：{target_name}", request_id)

# ====================== 对外暴露主函数 ======================
def main(cmd_parts: List[str], request_id: str) -> None:
    """帮助命令主入口"""
    try:
        handle_help(cmd_parts, request_id)
    except Exception as e:
        request_id = request_id or str(uuid.uuid4())
        log_error(f"帮助命令执行异常：{str(e)}", request_id)
        print(get_color_attr("RED") + f"帮助命令执行异常：{str(e)}" + get_color_attr("RESET", ""))

