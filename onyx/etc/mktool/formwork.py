"""
Onyx mktool Python模板（独立文件）
存储路径规则：USER_HOME_DIR/.[toolname]/
扩展路径：基于存储路径自动推导（cache/log/config等）
语言配置：USER_HOME_DIR/.config/onyx/language 
"""
import sys
import os
import platform
import time
from datetime import datetime

# 平台检测常量
IS_WINDOWS = sys.platform.startswith("win32")
IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform.startswith("darwin")

def get_current_language(USER_HOME_DIR):
    """读取全局语言配置（路径：USER_HOME_DIR/.config/onyx/language）"""
    try:
        lang_path = os.path.join(USER_HOME_DIR, ".config", "onyx", "language")
        lang_dir = os.path.dirname(lang_path)
        
        # 跨平台安全的目录创建
        if not os.path.exists(lang_dir):
            os.makedirs(lang_dir, mode=0o755, exist_ok=True)
        
        if os.path.exists(lang_path):
            with open(lang_path, "r", encoding="utf-8-sig") as f:
                lang = f.read().strip().lower()
            # 支持更多语言代码
            return lang if lang in ["chinese", "english", "zh", "en"] else "chinese"
        else:
            with open(lang_path, "w", encoding="utf-8") as f:
                f.write("chinese")
            return "chinese"
    except Exception as e:
        print(f"[WARNING] Failed to read language config: {e}")
        return "chinese"

def get_root_path():
    """自动推导ROOT_PATH（onyx主目录：查找包含'tools'文件夹的目录）"""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        current_path = script_dir
        max_depth = 10
        
        for _ in range(max_depth):
            # 同时检查多种可能的标志目录
            possible_markers = ["tools", "onyx", ".onyx", "src"]
            for marker in possible_markers:
                marker_path = os.path.join(current_path, marker)
                if os.path.exists(marker_path) and os.path.isdir(marker_path):
                    return current_path
            
            # 检查是否有配置文件
            config_files = ["onyx_config.json", "config.ini", "setup.py", "pyproject.toml"]
            for config_file in config_files:
                if os.path.exists(os.path.join(current_path, config_file)):
                    return current_path
            
            parent_path = os.path.dirname(current_path)
            if parent_path == current_path:  # 到达根目录
                break
            current_path = parent_path
        
        # 如果没有找到，返回脚本目录
        return script_dir
    except Exception as e:
        print(f"[WARNING] Failed to deduce onyx root directory: {e}")
        return os.path.abspath(".")

def get_username():
    """获取当前用户名（跨平台兼容）"""
    try:
        import getpass
        
        # 首选 getpass
        username = getpass.getuser()
        if username and username.lower() not in ["", "default_user", "unknown", "root"]:
            return username
        
        # 环境变量
        env_vars = ["USER", "USERNAME", "LOGNAME"]
        for env_var in env_vars:
            username = os.getenv(env_var)
            if username and username.lower() not in ["", "default_user", "unknown"]:
                return username
        
        # Unix/Linux系统
        if not IS_WINDOWS:
            try:
                import pwd
                return pwd.getpwuid(os.getuid()).pw_name
            except (ImportError, AttributeError, KeyError):
                pass
        
        # Windows系统
        if IS_WINDOWS:
            try:
                # 优先使用标准库方法
                username = os.getenv("USERNAME") or os.getenv("USER")
                if username:
                    return username
                    
                # 尝试 win32api
                try:
                    import win32api
                    return win32api.GetUserName()
                except ImportError:
                    pass
                    
                # 尝试 ctypes
                if hasattr(ctypes, 'windll'):
                    MAX_NAME = 256
                    name_buffer = ctypes.create_unicode_buffer(MAX_NAME)
                    size = ctypes.c_ulong(MAX_NAME)
                    if ctypes.windll.advapi32.GetUserNameW(name_buffer, ctypes.byref(size)):
                        return name_buffer.value
            except:
                pass
        
        # 最后手段：使用平台特定的默认值
        if IS_WINDOWS:
            return "Administrator" if os.getenv("USERNAME") == "Administrator" else "User"
        else:
            return "root" if os.geteuid() == 0 else "user"
            
    except Exception as e:
        print(f"[WARNING] Failed to get username: {e}")
        return "default_user"

def get_user_home_dir(ROOT_PATH):
    """推导USER_HOME_DIR（onyx用户主目录：root_path/root/ 或 root_path/home/username/）"""
    username = get_username()
    
    # 检测是否以管理员/root权限运行
    is_root = False
    if IS_WINDOWS:
        try:
            is_root = ctypes.windll.shell32.IsUserAnAdmin() != 0
        except:
            # 回退方法：检查常见的管理员标志
            is_root = os.getenv("USERNAME") == "Administrator" or "Admin" in os.getenv("USERNAME", "")
    else:
        try:
            is_root = os.geteuid() == 0
        except:
            is_root = False
    
    # 构建用户主目录路径
    if is_root:
        user_home = os.path.join(ROOT_PATH, "root")
    else:
        user_home = os.path.join(ROOT_PATH, "home", username)

    
    # 创建目录（跨平台安全）
    try:
        os.makedirs(user_home, mode=0o755, exist_ok=True)
        print(f"[INIT] Created onyx user home directory: {user_home}")
    except Exception as e:
        print(f"[WARNING] Failed to create user home directory: {e} -> {user_home}")
        # 回退：使用ROOT_PATH作为用户目录
        user_home = ROOT_PATH
    
    return user_home

def get_storage_path(USER_HOME_DIR, tool_name):
    """推导存储路径：USER_HOME_DIR/.[toolname]/（统一存储目录）"""
    # 清理工具名，确保目录名安全
    safe_tool_name = "".join(c for c in tool_name if c.isalnum() or c in "._-").strip("._")
    if not safe_tool_name:
        safe_tool_name = "tool"
    
    storage_path = os.path.join(USER_HOME_DIR, f".{safe_tool_name}")
    try:
        os.makedirs(storage_path, mode=0o755, exist_ok=True)
        print(f"[INIT] Created unified storage directory: {storage_path}")
    except Exception as e:
        print(f"[WARNING] Failed to create storage directory: {e} -> {storage_path}")
        # 回退：使用用户主目录
        storage_path = USER_HOME_DIR
    
    return storage_path

def get_extend_paths(storage_path):
    """推导扩展路径（缓存/日志/配置等，可扩展）"""
    # 定义所有可能的路径
    paths = {
        "cache_path": os.path.join(storage_path, "cache"),
        "log_path": os.path.join(storage_path, "logs"),
        "config_path": os.path.join(storage_path, "config"),
        "data_path": os.path.join(storage_path, "data"),
        "temp_path": os.path.join(storage_path, "temp"),
        "backup_path": os.path.join(storage_path, "backups")
    }
    
    # 创建所有目录
    for path_name, path in paths.items():
        try:
            os.makedirs(path, mode=0o755, exist_ok=True)
            print(f"[INIT] Created extended directory: {path_name} = {path}")
        except Exception as e:
            print(f"[WARNING] Failed to create extended directory {path_name}: {e}")
            # 设置路径为storage_path作为回退
            paths[path_name] = storage_path
    
    return paths

def get_lang_map(language, tool_name):
    """双语映射表（根据语言动态返回）"""
    # 标准化语言代码
    if language in ["chinese", "zh", "zh-cn", "zh-tw"]:
        language = "chinese"
    elif language in ["english", "en", "en-us", "en-gb"]:
        language = "english"
    
    lang_map = {
        "chinese": {
            "var_title": "【自动生成核心变量】",
            "root_path_label": "  1. ROOT_PATH（onyx主目录）：",
            "user_home_label": "  2. USER_HOME_DIR（用户主目录）：",
            "storage_label": "  3. storage_path（统一存储目录）：",
            "cache_label": "  4. cache_path（缓存路径）：",
            "log_label": "  5. log_path（日志路径）：",
            "config_label": "  6. config_path（配置路径）：",
            "data_label": "  7. data_path（数据路径）：",
            "extend_tip": "  扩展提示：可在get_extend_paths()中添加更多路径",
            "example_title": "【示例功能】",
            "create_test_file": "创建测试文件到存储目录：",
            "create_demo_file": "创建演示数据文件：",
            "tool_init_complete": f"✅ 工具 {tool_name} 初始化完成！",
            "current_user": "👤 当前用户：",
            "current_platform": "🖥️  当前平台：",
            "language_file": "🌐 语言配置文件：USER_HOME_DIR/.config/onyx/language",
            "storage_rule": "📁 存储规则：所有数据均保存在USER_HOME_DIR/.[工具名]/下",
            "test_file_content": f"工具名称：{tool_name}\n创建时间：{{0}}\n平台信息：{{1}}\nONYXPATH：{{2}}\nUSERHOME：{{3}}\nSTORAGE：{{4}}",
            "demo_file_content": f"这是 {tool_name} 工具的专属数据文件\n所有工具数据都会存储在统一存储目录中\n存储路径：{{0}}\n生成时间：{{1}}"
        },
        "english": {
            "var_title": "[Auto-generated Core Variables]",
            "root_path_label": "  1. ROOT_PATH (onyx root)：",
            "user_home_label": "  2. USER_HOME_DIR (user home)：",
            "storage_label": "  3. storage_path (unified storage)：",
            "cache_label": "  4. cache_path (cache)：",
            "log_label": "  5. log_path (log)：",
            "config_label": "  6. config_path (config)：",
            "data_label": "  7. data_path (data)：",
            "extend_tip": "  Extend tip: Add more paths in get_extend_paths()",
            "example_title": "[Example Function]",
            "create_test_file": "Create test file to storage path：",
            "create_demo_file": "Create demo data file：",
            "tool_init_complete": f"✅ Tool {tool_name} initialization completed!",
            "current_user": "👤 Current user：",
            "current_platform": "🖥️  Current platform：",
            "language_file": "🌐 Language config file：USER_HOME_DIR/.config/onyx/language",
            "storage_rule": "📁 Storage rule: All data stored in USER_HOME_DIR/.[toolname]/",
            "test_file_content": f"Tool Name: {tool_name}\nCreate Time: {{0}}\nPlatform: {{1}}\nONYXPATH: {{2}}\nUSERHOME: {{3}}\nSTORAGE: {{4}}",
            "demo_file_content": f"This is the exclusive data file for {tool_name} tool\nAll tool data stored in unified storage directory\nStorage Path: {{0}}\nGenerated Time: {{1}}"
        }
    }
    return lang_map.get(language, lang_map["english"])

def get_platform_info():
    """获取详细的平台信息"""
    platform_info = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version()
    }
    
    # 创建可读的字符串
    if IS_WINDOWS:
        os_name = f"Windows {platform_info['release']}"
    elif IS_MAC:
        os_name = f"macOS {platform_info['release']}"
    elif IS_LINUX:
        import distro
        os_name = f"{distro.name()} {distro.version()}"
    else:
        os_name = platform_info['system']
    
    return f"{os_name} ({platform_info['machine']}) | Python {platform_info['python_version']}"

def main(tool_name, create_time):
    """工具主函数（模板入口）"""
    print("=" * 60)
    print(f"Tool: {tool_name} (Python)")
    print(f"Create Time: {create_time}")
    print("=" * 60)
    
    # 初始化核心变量
    ROOT_PATH = get_root_path()
    USER_HOME_DIR = get_user_home_dir(ROOT_PATH)
    storage_path = get_storage_path(USER_HOME_DIR, tool_name)
    extend_paths = get_extend_paths(storage_path)
    
    # 解包扩展路径
    cache_path = extend_paths.get("cache_path", storage_path)
    log_path = extend_paths.get("log_path", storage_path)
    config_path = extend_paths.get("config_path", storage_path)
    data_path = extend_paths.get("data_path", storage_path)
    
    language = get_current_language(USER_HOME_DIR)
    MSG = get_lang_map(language, tool_name)
    
    # 获取平台信息
    platform_info = get_platform_info()
    
    # 打印核心变量
    print(f"\n{MSG['var_title']}")
    print(f"{MSG['root_path_label']}{ROOT_PATH}")
    print(f"{MSG['user_home_label']}{USER_HOME_DIR}")
    print(f"{MSG['storage_label']}{storage_path}")
    print(f"{MSG['cache_label']}{cache_path}")
    print(f"{MSG['log_label']}{log_path}")
    print(f"{MSG['config_label']}{config_path}")
    print(f"{MSG['data_label']}{data_path}")
    print(f"{MSG['extend_tip']}")
    print("=" * 60)
    
    # 示例：创建测试文件
    print(f"\n{MSG['example_title']}")
    try:
        # 创建测试文件
        test_file = os.path.join(storage_path, "tool_test.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(MSG['test_file_content'].format(
                create_time, 
                platform_info,
                ROOT_PATH, 
                USER_HOME_DIR, 
                storage_path
            ))
        print(f"{MSG['create_test_file']}{test_file}")
        
        # 创建演示数据文件
        demo_file = os.path.join(data_path, "demo_data.txt")
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(demo_file, "w", encoding="utf-8") as f:
            f.write(MSG['demo_file_content'].format(storage_path, current_time))
        print(f"{MSG['create_demo_file']}{demo_file}")
        
    except Exception as e:
        print(f"[WARNING] File creation failed: {e}")
    
    print("=" * 60)
    print(MSG['tool_init_complete'])
    print(f"{MSG['current_user']}{get_username()}")
    print(f"{MSG['current_platform']}{platform_info}")
    print(f"{MSG['language_file']}")
    print(f"{MSG['storage_rule']}")
    print("=" * 60)
    
    # 返回核心路径供其他模块使用
    return {
        "ROOT_PATH": ROOT_PATH,
        "USER_HOME_DIR": USER_HOME_DIR,
        "storage_path": storage_path,
        "extend_paths": extend_paths,
        "language": language,
        "platform": platform_info
    }

if __name__ == "__main__":
    # 动态注入工具名和创建时间（由mktool命令传入）
    tool_name = "{{TOOL_NAME}}"
    create_time = "{{CREATE_TIME}}"
    
