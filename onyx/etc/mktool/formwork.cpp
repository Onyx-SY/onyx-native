/*
Onyx mktool C++模板（独立文件）
存储路径规则：USER_HOME_DIR/.[toolname]/
扩展路径：基于存储路径自动推导（cache/log/config等）
语言配置：USER_HOME_DIR/.config/onyx/language
*/
#include <iostream>
#include <fstream>
#include <string>
#include <stdexcept>
#include <cstring>
#include <vector>
#include <algorithm>

// 平台特定头文件
#ifdef _WIN32
    #include <windows.h>
    #include <direct.h>
    #include <shlobj.h>
    #define mkdir(dir, mode) _mkdir(dir)
    #define PATH_SEPARATOR '\\'
    #define PATH_SEPARATOR_STR "\\"
#else
    #include <unistd.h>
    #include <sys/stat.h>
    #include <sys/types.h>
    #include <pwd.h>
    #include <libgen.h>
    #define PATH_SEPARATOR '/'
    #define PATH_SEPARATOR_STR "/"
#endif

// 全局变量（由mktool命令注入）
const std::string TOOL_NAME = "{{TOOL_NAME}}";
const std::string CREATE_TIME = "{{CREATE_TIME}}";

// 跨平台工具函数
bool create_directory(const std::string& path) {
    #ifdef _WIN32
        return _mkdir(path.c_str()) == 0 || errno == EEXIST;
    #else
        return mkdir(path.c_str(), 0755) == 0 || errno == EEXIST;
    #endif
}

bool path_exists(const std::string& path) {
    #ifdef _WIN32
        DWORD attrib = GetFileAttributes(path.c_str());
        return (attrib != INVALID_FILE_ATTRIBUTES);
    #else
        struct stat st;
        return (stat(path.c_str(), &st) == 0);
    #endif
}

bool is_directory(const std::string& path) {
    #ifdef _WIN32
        DWORD attrib = GetFileAttributes(path.c_str());
        return (attrib != INVALID_FILE_ATTRIBUTES && (attrib & FILE_ATTRIBUTE_DIRECTORY));
    #else
        struct stat st;
        if (stat(path.c_str(), &st) == 0) {
            return S_ISDIR(st.st_mode);
        }
        return false;
    #endif
}

std::string get_root_path() {
    char buffer[1024];
    
    #ifdef _WIN32
        DWORD length = GetModuleFileName(NULL, buffer, sizeof(buffer));
        if (length == 0 || length >= sizeof(buffer)) {
            return ".";
        }
        std::string script_path(buffer);
        // 找到最后一个路径分隔符
        size_t last_slash = script_path.find_last_of("\\/");
        if (last_slash != std::string::npos) {
            script_path = script_path.substr(0, last_slash);
        }
    #else
        ssize_t count = readlink("/proc/self/exe", buffer, sizeof(buffer)-1);
        if (count == -1) {
            // 备用方法
            std::string script_path = buffer;
            if (realpath(script_path.c_str(), buffer) == NULL) {
                return ".";
            }
            script_path = buffer;
        } else {
            buffer[count] = '\0';
        }
        std::string script_path(buffer);
        // 找到最后一个路径分隔符
        size_t last_slash = script_path.find_last_of('/');
        if (last_slash != std::string::npos) {
            script_path = script_path.substr(0, last_slash);
        }
    #endif
    
    std::string search_path = script_path;
    int max_depth = 10;
    
    for (int i = 0; i < max_depth; i++) {
        std::string tools_path = search_path + PATH_SEPARATOR_STR + "tools";
        
        if (is_directory(tools_path)) {
            return search_path;
        }
        
        // 向上级目录查找
        size_t last_slash = search_path.find_last_of(PATH_SEPARATOR);
        if (last_slash == std::string::npos) {
            break;
        }
        
        std::string parent_path = search_path.substr(0, last_slash);
        if (parent_path == search_path) {
            break;
        }
        search_path = parent_path;
    }
    
    return script_path;
}

std::string get_username() {
    try {
        // 环境变量
        const char* env_vars[] = {"USER", "USERNAME", "LOGNAME"};
        for (const char* env_var : env_vars) {
            char* env_val = std::getenv(env_var);
            if (env_val != nullptr && std::string(env_val) != "default_user") {
                return std::string(env_val);
            }
        }
        
        // Unix/Linux 系统
        #ifndef _WIN32
        struct passwd* pwd = getpwuid(getuid());
        if (pwd != nullptr && pwd->pw_name != nullptr) {
            return std::string(pwd->pw_name);
        }
        #endif
        
        // Windows 系统
        #ifdef _WIN32
        char username[256];
        DWORD len = 256;
        if (GetUserName(username, &len)) {
            return std::string(username);
        }
        #endif
        
        return "default_user";
    } catch (...) {
        return "default_user";
    }
}

std::string get_user_home_dir(const std::string& ROOT_PATH) {
    std::string username = get_username();
    bool is_root = false;
    
    #ifdef _WIN32
        is_root = IsUserAnAdmin() != 0;
    #else
        is_root = geteuid() == 0;
    #endif
    
    std::string user_home;
    if (is_root) {
        user_home = ROOT_PATH + PATH_SEPARATOR_STR + "root";
    } else {
        user_home = ROOT_PATH + PATH_SEPARATOR_STR + "home" + PATH_SEPARATOR_STR + username;
    }
    
    try {
        if (!path_exists(user_home)) {
            create_directory(user_home);
            std::cout << "[初始化] 创建onyx用户主目录：" << user_home << std::endl;
        }
    } catch (const std::exception& e) {
        std::cout << "[警告] 创建用户主目录失败：" << e.what() << " → " << user_home << std::endl;
    }
    
    return user_home;
}

std::string get_storage_path(const std::string& USER_HOME_DIR) {
    std::string storage_path = USER_HOME_DIR + PATH_SEPARATOR_STR + "." + TOOL_NAME;
    
    try {
        if (!path_exists(storage_path)) {
            create_directory(storage_path);
            std::cout << "[初始化] 创建存储目录（统一路径）：" << storage_path << std::endl;
        }
    } catch (const std::exception& e) {
        std::cout << "[警告] 创建存储目录失败：" << e.what() << " → " << storage_path << std::endl;
        return USER_HOME_DIR;
    }
    
    return storage_path;
}

void get_extend_paths(const std::string& storage_path) {
    std::vector<std::pair<std::string, std::string>> extend_paths = {
        {storage_path + PATH_SEPARATOR_STR + "cache", "cache_path"},
        {storage_path + PATH_SEPARATOR_STR + "log", "log_path"}
    };
    
    for (const auto& [path, path_name] : extend_paths) {
        try {
            if (!path_exists(path)) {
                create_directory(path);
                std::cout << "[初始化] 创建扩展目录：" << path_name << " = " << path << std::endl;
            }
        } catch (const std::exception& e) {
            std::cout << "[警告] 创建扩展目录失败：" << path_name << " = " << path << " → " << e.what() << std::endl;
        }
    }
}

std::string get_current_language(const std::string& USER_HOME_DIR) {
    std::string lang_path = USER_HOME_DIR + PATH_SEPARATOR_STR + ".config" + 
                            PATH_SEPARATOR_STR + "onyx" + PATH_SEPARATOR_STR + "language";
    std::string lang_dir = USER_HOME_DIR + PATH_SEPARATOR_STR + ".config" + 
                          PATH_SEPARATOR_STR + "onyx";
    
    try {
        if (!path_exists(lang_dir)) {
            create_directory(lang_dir);
        }
    } catch (const std::exception& e) {
        std::cout << "[警告] 创建语言配置目录失败：" << e.what() << " → " << lang_dir << std::endl;
    }
    
    std::ifstream lang_file(lang_path);
    if (!lang_file.is_open()) {
        std::ofstream fp(lang_path);
        if (fp) {
            fp << "chinese";
            fp.close();
        }
        return "chinese";
    }
    
    std::string language;
    std::getline(lang_file, language);
    lang_file.close();
    
    // 清理字符串
    language.erase(std::remove(language.begin(), language.end(), '\r'), language.end());
    language.erase(std::remove(language.begin(), language.end(), '\n'), language.end());
    
    return language.empty() ? "chinese" : language;
}

class LangMsg {
public:
    std::string var_title;
    std::string root_path_label;
    std::string user_home_label;
    std::string storage_label;
    std::string cache_label;
    std::string log_label;
    std::string extend_tip;
    std::string example_title;
    std::string create_test_file;
    std::string create_demo_file;
    std::string tool_init_complete;
    std::string current_user;
    std::string language_file;
    std::string storage_rule;
    std::string test_file_content;
    std::string demo_file_content;
};

LangMsg get_lang_map(const std::string& language) {
    LangMsg msg;
    
    if (language == "english") {
        msg.var_title = "[Auto-generated Core Variables]";
        msg.root_path_label = "  1. ROOT_PATH (onyx root)：";
        msg.user_home_label = "  2. USER_HOME_DIR (user home)：";
        msg.storage_label = "  3. storage_path (unified storage)：";
        msg.cache_label = "  4. cache_path (cache)：";
        msg.log_label = "  5. log_path (log)：";
        msg.extend_tip = "  Extend tip: Add config_path、data_path in get_extend_paths()";
        msg.example_title = "[Example Function]";
        msg.create_test_file = "Create test file to storage path：";
        msg.create_demo_file = "Create demo data file：";
        msg.tool_init_complete = "✅ Tool " + TOOL_NAME + " initialization completed!";
        msg.current_user = "👤 Current user：";
        msg.language_file = "🌐 Language config file：USER_HOME_DIR/.config/onyx/language";
        msg.storage_rule = "📁 Storage rule: All data stored in USER_HOME_DIR/.[toolname]/";
        msg.test_file_content = "Tool Name: " + TOOL_NAME + "\nCreate Time: " + CREATE_TIME + 
                                "\nONYXPATH: {}\nUSERHOME: {}\nSTORAGE: {}";
        msg.demo_file_content = "This is the exclusive data file for " + TOOL_NAME + 
                                " tool\nAll tool data stored in unified storage directory\nStorage Path: {}";
    } else {
        msg.var_title = "【自动生成核心变量】";
        msg.root_path_label = "  1. ROOT_PATH（onyx主目录）：";
        msg.user_home_label = "  2. USER_HOME_DIR（用户主目录）：";
        msg.storage_label = "  3. storage_path（统一存储目录）：";
        msg.cache_label = "  4. cache_path（缓存路径）：";
        msg.log_label = "  5. log_path（日志路径）：";
        msg.extend_tip = "  扩展提示：可在get_extend_paths()中添加config_path、data_path等";
        msg.example_title = "【示例功能】";
        msg.create_test_file = "创建测试文件到存储目录：";
        msg.create_demo_file = "创建演示数据文件：";
        msg.tool_init_complete = "✅ 工具 " + TOOL_NAME + " 初始化完成！";
        msg.current_user = "👤 当前用户：";
        msg.language_file = "🌐 语言配置文件：USER_HOME_DIR/.config/onyx/language";
        msg.storage_rule = "📁 存储规则：所有数据均保存在USER_HOME_DIR/.[工具名]/下";
        msg.test_file_content = "工具名称：" + TOOL_NAME + "\n创建时间：" + CREATE_TIME + 
                                "\nONYXPATH：{}\nUSERHOME：{}\nSTORAGE：{}";
        msg.demo_file_content = "这是 " + TOOL_NAME + " 工具的专属数据文件\n所有工具数据都会存储在统一存储目录中\n存储路径：{}";
    }
    
    return msg;
}

int main(int argc, char* argv[]) {
    try {
        std::string ROOT_PATH = get_root_path();
        std::string USER_HOME_DIR = get_user_home_dir(ROOT_PATH);
        std::string storage_path = get_storage_path(USER_HOME_DIR);
        get_extend_paths(storage_path);
        std::string language = get_current_language(USER_HOME_DIR);
        LangMsg MSG = get_lang_map(language);
        
        std::string cache_path = storage_path + PATH_SEPARATOR_STR + "cache";
        std::string log_path = storage_path + PATH_SEPARATOR_STR + "log";
        
        std::cout << "============================================================\n";
        std::cout << "Tool: " << TOOL_NAME << " (C++)\n";
        std::cout << "Create Time: " << CREATE_TIME << "\n";
        std::cout << "============================================================\n";
        
        std::cout << "\n" << MSG.var_title << "\n";
        std::cout << MSG.root_path_label << ROOT_PATH << "\n";
        std::cout << MSG.user_home_label << USER_HOME_DIR << "\n";
        std::cout << MSG.storage_label << storage_path << "\n";
        std::cout << MSG.cache_label << cache_path << "\n";
        std::cout << MSG.log_label << log_path << "\n";
        std::cout << MSG.extend_tip << "\n";
        std::cout << "============================================================\n";
        
        std::cout << "\n" << MSG.example_title << "\n";
        std::string test_file = storage_path + PATH_SEPARATOR_STR + "tool_test.txt";
        std::ofstream out_file(test_file);
        
        if (out_file) {
            // 替换占位符
            std::string test_content = MSG.test_file_content;
            size_t pos1 = test_content.find("{}");
            if (pos1 != std::string::npos) {
                test_content.replace(pos1, 2, ROOT_PATH);
            }
            size_t pos2 = test_content.find("{}", pos1 + ROOT_PATH.length() - 2);
            if (pos2 != std::string::npos) {
                test_content.replace(pos2, 2, USER_HOME_DIR);
            }
            size_t pos3 = test_content.find("{}", pos2 + USER_HOME_DIR.length() - 2);
            if (pos3 != std::string::npos) {
                test_content.replace(pos3, 2, storage_path);
            }
            
            out_file << test_content;
            out_file.close();
            std::cout << MSG.create_test_file << test_file << "\n";
            
            std::string demo_file = log_path + PATH_SEPARATOR_STR + "demo_data.txt";
            std::ofstream demo_out(demo_file);
            if (demo_out) {
                std::string demo_content = MSG.demo_file_content;
                size_t pos = demo_content.find("{}");
                if (pos != std::string::npos) {
                    demo_content.replace(pos, 2, storage_path);
                }
                demo_out << demo_content;
                demo_out.close();
                std::cout << MSG.create_demo_file << demo_file << "\n";
            }
        } else {
            std::cout << "[警告] 文件创建失败：" << test_file << std::endl;
        }
        
        std::cout << "============================================================\n";
        std::cout << MSG.tool_init_complete << std::endl;
        std::cout << MSG.current_user << get_username() << std::endl;
        std::cout << MSG.language_file << std::endl;
        std::cout << MSG.storage_rule << std::endl;
        std::cout << "============================================================\n";
        
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }
}