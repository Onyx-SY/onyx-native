/*
Onyx mktool C模板（独立文件）
存储路径规则：USER_HOME_DIR/.[toolname]/
扩展路径：基于存储路径自动推导（cache/log/config等）
语言配置：USER_HOME_DIR/.config/onyx/language
*/
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <time.h>

// 平台特定头文件
#ifdef _WIN32
    #include <windows.h>
    #include <direct.h>
    #define mkdir(dir, mode) _mkdir(dir)
    #define PATH_SEPARATOR '\\'
    #define PATH_SEPARATOR_STR "\\"
    // Windows 版本的 dirname
    char* dirname_win(char* path) {
        static char drive[_MAX_DRIVE];
        static char dir[_MAX_DIR];
        static char fname[_MAX_FNAME];
        static char ext[_MAX_EXT];
        static char result[_MAX_PATH];
        
        _splitpath(path, drive, dir, fname, ext);
        if (strlen(dir) == 0) {
            strcpy(result, ".");
        } else {
            // 移除末尾的路径分隔符
            size_t len = strlen(dir);
            if (len > 0 && (dir[len-1] == '\\' || dir[len-1] == '/')) {
                dir[len-1] = '\0';
            }
            sprintf(result, "%s%s", drive, dir);
        }
        return result;
    }
    #define dirname(path) dirname_win(path)
#else
    #include <unistd.h>
    #include <sys/stat.h>
    #include <sys/types.h>
    #include <pwd.h>
    #include <libgen.h>
    #define PATH_SEPARATOR '/'
    #define PATH_SEPARATOR_STR "/"
#endif

#define MAX_PATH_LEN 1024

// 全局变量（由mktool命令注入）
const char* TOOL_NAME = "{{TOOL_NAME}}";
const char* CREATE_TIME = "{{CREATE_TIME}}";

// 创建目录函数（跨平台）
int create_dir(const char* path) {
    #ifdef _WIN32
        return _mkdir(path);
    #else
        return mkdir(path, 0755);
    #endif
}

// 检查文件或目录是否存在
int path_exists(const char* path) {
    #ifdef _WIN32
        DWORD attrib = GetFileAttributes(path);
        return (attrib != INVALID_FILE_ATTRIBUTES);
    #else
        struct stat st;
        return (stat(path, &st) == 0);
    #endif
}

// 检查是否为目录
int is_directory(const char* path) {
    #ifdef _WIN32
        DWORD attrib = GetFileAttributes(path);
        return (attrib != INVALID_FILE_ATTRIBUTES && (attrib & FILE_ATTRIBUTE_DIRECTORY));
    #else
        struct stat st;
        if (stat(path, &st) == 0) {
            return S_ISDIR(st.st_mode);
        }
        return 0;
    #endif
}

void get_root_path(const char* script_path, char* ROOT_PATH) {
    char current_path[MAX_PATH_LEN];
    char search_path[MAX_PATH_LEN];
    char* dir = dirname((char*)script_path);
    
    strncpy(current_path, dir, MAX_PATH_LEN - 1);
    current_path[MAX_PATH_LEN - 1] = '\0';
    strncpy(search_path, current_path, MAX_PATH_LEN - 1);
    search_path[MAX_PATH_LEN - 1] = '\0';
    
    int max_depth = 10;
    for (int i = 0; i < max_depth; i++) {
        char tools_path[MAX_PATH_LEN];
        #ifdef _WIN32
            snprintf(tools_path, MAX_PATH_LEN, "%s\\tools", search_path);
        #else
            snprintf(tools_path, MAX_PATH_LEN, "%s/tools", search_path);
        #endif
        
        if (is_directory(tools_path)) {
            strncpy(ROOT_PATH, search_path, MAX_PATH_LEN - 1);
            ROOT_PATH[MAX_PATH_LEN - 1] = '\0';
            return;
        }
        
        char parent_path[MAX_PATH_LEN];
        strncpy(parent_path, dirname(search_path), MAX_PATH_LEN - 1);
        parent_path[MAX_PATH_LEN - 1] = '\0';
        if (strcmp(parent_path, search_path) == 0) break;
        strncpy(search_path, parent_path, MAX_PATH_LEN - 1);
        search_path[MAX_PATH_LEN - 1] = '\0';
    }
    strncpy(ROOT_PATH, current_path, MAX_PATH_LEN - 1);
    ROOT_PATH[MAX_PATH_LEN - 1] = '\0';
}

char* get_username() {
    static char username[MAX_PATH_LEN] = "default_user";
    
    // 清空用户名
    username[0] = '\0';
    
    // 尝试环境变量
    char* env_vars[] = {"USER", "USERNAME", "LOGNAME"};
    for (int i = 0; i < 3; i++) {
        char* env_val = getenv(env_vars[i]);
        if (env_val != NULL && strlen(env_val) > 0) {
            strncpy(username, env_val, MAX_PATH_LEN - 1);
            return username;
        }
    }
    
    // Unix/Linux 系统
    #ifndef _WIN32
    struct passwd* pwd = getpwuid(getuid());
    if (pwd != NULL && pwd->pw_name != NULL) {
        strncpy(username, pwd->pw_name, MAX_PATH_LEN - 1);
        return username;
    }
    #endif
    
    // Windows 系统
    #ifdef _WIN32
    DWORD size = MAX_PATH_LEN;
    if (GetUserName(username, &size)) {
        return username;
    }
    #endif
    
    // 默认值
    strcpy(username, "default_user");
    return username;
}

void get_user_home_dir(const char* ROOT_PATH, char* USER_HOME_DIR) {
    char* username = get_username();
    int is_root = 0;
    
    #ifdef _WIN32
        is_root = IsUserAnAdmin() != 0;
    #else
        is_root = geteuid() == 0;
    #endif
    
    if (is_root) {
        #ifdef _WIN32
            snprintf(USER_HOME_DIR, MAX_PATH_LEN, "%s\\root", ROOT_PATH);
        #else
            snprintf(USER_HOME_DIR, MAX_PATH_LEN, "%s/root", ROOT_PATH);
        #endif
    } else {
        #ifdef _WIN32
            snprintf(USER_HOME_DIR, MAX_PATH_LEN, "%s\\home\\%s", ROOT_PATH, username);
        #else
            snprintf(USER_HOME_DIR, MAX_PATH_LEN, "%s/home/%s", ROOT_PATH, username);
        #endif
    }
    
    if (!path_exists(USER_HOME_DIR)) {
        if (create_dir(USER_HOME_DIR) != 0 && errno != EEXIST) {
            printf("[警告] 创建用户主目录失败：%s → %s\n", strerror(errno), USER_HOME_DIR);
        } else {
            printf("[初始化] 创建onyx用户主目录：%s\n", USER_HOME_DIR);
        }
    }
}

void get_storage_path(const char* USER_HOME_DIR, char* storage_path) {
    #ifdef _WIN32
        snprintf(storage_path, MAX_PATH_LEN, "%s\\.%s", USER_HOME_DIR, TOOL_NAME);
    #else
        snprintf(storage_path, MAX_PATH_LEN, "%s/.%s", USER_HOME_DIR, TOOL_NAME);
    #endif
    
    if (!path_exists(storage_path)) {
        if (create_dir(storage_path) != 0 && errno != EEXIST) {
            printf("[警告] 创建存储目录失败：%s → %s\n", strerror(errno), storage_path);
            strncpy(storage_path, USER_HOME_DIR, MAX_PATH_LEN - 1);
        } else {
            printf("[初始化] 创建存储目录（统一路径）：%s\n", storage_path);
        }
    }
}

void get_extend_paths(const char* storage_path) {
    char cache_path[MAX_PATH_LEN], log_path[MAX_PATH_LEN];
    
    #ifdef _WIN32
        snprintf(cache_path, MAX_PATH_LEN, "%s\\cache", storage_path);
        snprintf(log_path, MAX_PATH_LEN, "%s\\log", storage_path);
    #else
        snprintf(cache_path, MAX_PATH_LEN, "%s/cache", storage_path);
        snprintf(log_path, MAX_PATH_LEN, "%s/log", storage_path);
    #endif
    
    char* extend_paths[] = {cache_path, log_path};
    char* path_names[] = {"cache_path", "log_path"};
    int path_count = 2;
    
    for (int i = 0; i < path_count; i++) {
        if (!path_exists(extend_paths[i])) {
            if (create_dir(extend_paths[i]) != 0 && errno != EEXIST) {
                printf("[警告] 创建扩展目录失败：%s = %s → %s\n", path_names[i], extend_paths[i], strerror(errno));
            } else {
                printf("[初始化] 创建扩展目录：%s = %s\n", path_names[i], extend_paths[i]);
            }
        }
    }
}

void get_current_language(const char* USER_HOME_DIR, char* language) {
    char lang_path[MAX_PATH_LEN];
    char lang_dir[MAX_PATH_LEN];
    
    #ifdef _WIN32
        snprintf(lang_path, MAX_PATH_LEN, "%s\\.config\\onyx\\language", USER_HOME_DIR);
        snprintf(lang_dir, MAX_PATH_LEN, "%s\\.config\\onyx", USER_HOME_DIR);
    #else
        snprintf(lang_path, MAX_PATH_LEN, "%s/.config/onyx/language", USER_HOME_DIR);
        snprintf(lang_dir, MAX_PATH_LEN, "%s/.config/onyx", USER_HOME_DIR);
    #endif
    
    // 创建语言配置目录
    if (!path_exists(lang_dir)) {
        char parent_dir[MAX_PATH_LEN];
        #ifdef _WIN32
            char* last_slash = strrchr(lang_dir, '\\');
        #else
            char* last_slash = strrchr(lang_dir, '/');
        #endif
        
        if (last_slash) {
            strncpy(parent_dir, lang_dir, last_slash - lang_dir);
            parent_dir[last_slash - lang_dir] = '\0';
            if (!path_exists(parent_dir)) {
                create_dir(parent_dir);
            }
        }
        create_dir(lang_dir);
    }
    
    FILE* lang_file = fopen(lang_path, "r");
    if (!lang_file) {
        FILE* fp = fopen(lang_path, "w");
        if (fp) {
            fprintf(fp, "chinese");
            fclose(fp);
        }
        strncpy(language, "chinese", MAX_PATH_LEN - 1);
        return;
    }
    
    if (fgets(language, MAX_PATH_LEN, lang_file) == NULL) {
        strncpy(language, "chinese", MAX_PATH_LEN - 1);
    } else {
        char* newline = strchr(language, '\n');
        if (newline) *newline = '\0';
        char* newline2 = strchr(language, '\r');
        if (newline2) *newline2 = '\0';
    }
    fclose(lang_file);
}

typedef struct {
    const char* var_title;
    const char* root_path_label;
    const char* user_home_label;
    const char* storage_label;
    const char* cache_label;
    const char* log_label;
    const char* extend_tip;
    const char* example_title;
    const char* create_test_file;
    const char* create_demo_file;
    const char* tool_init_complete;
    const char* current_user;
    const char* language_file;
    const char* storage_rule;
    const char* test_file_content;
    const char* demo_file_content;
} LangMsg;

LangMsg* get_lang_map(const char* language) {
    static LangMsg lang_cn = {
        "【自动生成核心变量】",
        "  1. ROOT_PATH（onyx主目录）：",
        "  2. USER_HOME_DIR（用户主目录）：",
        "  3. storage_path（统一存储目录）：",
        "  4. cache_path（缓存路径）：",
        "  5. log_path（日志路径）：",
        "  扩展提示：可在get_extend_paths()中添加config_path、data_path等",
        "【示例功能】",
        "创建测试文件到存储目录：",
        "创建演示数据文件：",
        "✅ 工具 {{TOOL_NAME}} 初始化完成！",
        "👤 当前用户：",
        "🌐 语言配置文件：USER_HOME_DIR/.config/onyx/language",
        "📁 存储规则：所有数据均保存在USER_HOME_DIR/.[工具名]/下",
        "工具名称：%s\n创建时间：%s\nONYXPATH：%s\nUSERHOME：%s\nSTORAGE：%s",
        "这是 %s 工具的专属数据文件\n所有工具数据都会存储在统一存储目录中\n存储路径：%s"
    };
    static LangMsg lang_en = {
        "[Auto-generated Core Variables]",
        "  1. ROOT_PATH (onyx root)：",
        "  2. USER_HOME_DIR (user home)：",
        "  3. storage_path (unified storage)：",
        "  4. cache_path (cache)：",
        "  5. log_path (log)：",
        "  Extend tip: Add config_path、data_path in get_extend_paths()",
        "[Example Function]",
        "Create test file to storage path：",
        "Create demo data file：",
        "✅ Tool {{TOOL_NAME}} initialization completed!",
        "👤 Current user：",
        "🌐 Language config file：USER_HOME_DIR/.config/onyx/language",
        "📁 Storage rule: All data stored in USER_HOME_DIR/.[toolname]/",
        "Tool Name: %s\nCreate Time: %s\nONYXPATH: %s\nUSERHOME: %s\nSTORAGE: %s",
        "This is the exclusive data file for %s tool\nAll tool data stored in unified storage directory\nStorage Path: %s"
    };
    
    if (language != NULL && strcmp(language, "english") == 0) {
        return &lang_en;
    }
    return &lang_cn;
}

int main(int argc, char* argv[]) {
    char script_path[MAX_PATH_LEN], ROOT_PATH[MAX_PATH_LEN], USER_HOME_DIR[MAX_PATH_LEN];
    char storage_path[MAX_PATH_LEN], cache_path[MAX_PATH_LEN], log_path[MAX_PATH_LEN];
    char language[MAX_PATH_LEN];
    
    // 获取脚本路径
    #ifdef _WIN32
        GetModuleFileName(NULL, script_path, MAX_PATH_LEN);
    #else
        if (realpath(argv[0], script_path) == NULL) {
            strncpy(script_path, argv[0], MAX_PATH_LEN - 1);
            script_path[MAX_PATH_LEN - 1] = '\0';
        }
    #endif
    
    // 初始化核心变量
    get_root_path(script_path, ROOT_PATH);
    get_user_home_dir(ROOT_PATH, USER_HOME_DIR);
    get_storage_path(USER_HOME_DIR, storage_path);
    get_extend_paths(storage_path);
    get_current_language(USER_HOME_DIR, language);
    LangMsg* MSG = get_lang_map(language);
    
    // 拼接缓存/日志路径
    #ifdef _WIN32
        snprintf(cache_path, MAX_PATH_LEN, "%s\\cache", storage_path);
        snprintf(log_path, MAX_PATH_LEN, "%s\\log", storage_path);
    #else
        snprintf(cache_path, MAX_PATH_LEN, "%s/cache", storage_path);
        snprintf(log_path, MAX_PATH_LEN, "%s/log", storage_path);
    #endif
    
    printf("============================================================\n");
    printf("Tool: %s (C)\n", TOOL_NAME);
    printf("Create Time: %s\n", CREATE_TIME);
    printf("============================================================\n");
    
    printf("\n%s\n", MSG->var_title);
    printf("%s%s\n", MSG->root_path_label, ROOT_PATH);
    printf("%s%s\n", MSG->user_home_label, USER_HOME_DIR);
    printf("%s%s\n", MSG->storage_label, storage_path);
    printf("%s%s\n", MSG->cache_label, cache_path);
    printf("%s%s\n", MSG->log_label, log_path);
    printf("%s\n", MSG->extend_tip);
    printf("============================================================\n");
    
    // 示例：创建测试文件
    printf("\n%s\n", MSG->example_title);
    char test_file[MAX_PATH_LEN];
    #ifdef _WIN32
        snprintf(test_file, MAX_PATH_LEN, "%s\\tool_test.txt", storage_path);
    #else
        snprintf(test_file, MAX_PATH_LEN, "%s/tool_test.txt", storage_path);
    #endif
    
    FILE* fp = fopen(test_file, "w");
    if (fp) {
        fprintf(fp, MSG->test_file_content, TOOL_NAME, CREATE_TIME, ROOT_PATH, USER_HOME_DIR, storage_path);
        fclose(fp);
        printf("%s%s\n", MSG->create_test_file, test_file);
        
        char demo_file[MAX_PATH_LEN];
        #ifdef _WIN32
            snprintf(demo_file, MAX_PATH_LEN, "%s\\demo_data.txt", log_path);
        #else
            snprintf(demo_file, MAX_PATH_LEN, "%s/demo_data.txt", log_path);
        #endif
        
        FILE* demo_fp = fopen(demo_file, "w");
        if (demo_fp) {
            fprintf(demo_fp, MSG->demo_file_content, TOOL_NAME, storage_path);
            fclose(demo_fp);
            printf("%s%s\n", MSG->create_demo_file, demo_file);
        }
    } else {
        printf("[警告] 文件创建失败：%s → %s\n", strerror(errno), test_file);
    }
    
    printf("============================================================\n");
    printf("%s\n", MSG->tool_init_complete);
    printf("%s%s\n", MSG->current_user, get_username());
    printf("%s\n", MSG->language_file);
    printf("%s\n", MSG->storage_rule);
    printf("============================================================\n");
    
    return 0;
}