#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <dirent.h>
#include <fnmatch.h>
#include <limits.h>
#include <ctype.h>
#include <errno.h>

// 跨平台兼容性宏定义
#if defined(_WIN32) || defined(WIN32)
#define OS_WIN 1
#define PATH_SEP '\\'
#define STRDUP _strdup
#define REALPATH custom_realpath
#include <direct.h>
#include <windows.h>
#else
#define OS_WIN 0
#define PATH_SEP '/'
#define STRDUP strdup
#define REALPATH realpath
#include <unistd.h>
#endif

// 安全字符串复制（避免缓冲区溢出）
static void safe_strcpy(char* dest, const char* src, size_t dest_size) {
    if (!dest || !src || dest_size == 0) return;
    strncpy(dest, src, dest_size - 1);
    dest[dest_size - 1] = '\0';
}

// Windows自定义realpath实现（兼容POSIX接口）
#if OS_WIN
static char* custom_realpath(const char* path, char* resolved_path) {
    if (!path || !resolved_path) return NULL;
    DWORD len = GetFullPathNameA(path, PATH_MAX, resolved_path, NULL);
    if (len == 0 || len > PATH_MAX) return NULL;
    // 统一路径分隔符为'/'
    for (char* p = resolved_path; *p; p++) {
        if (*p == '\\') *p = '/';
    }
    return resolved_path;
#endif

// 字符串转为小写
static void str_to_lower(char* str) {
    if (!str) return;
    for (; *str; str++) {
        *str = tolower((unsigned char)*str);
    }
}

// 跨平台文件/目录存在性检查
static int path_exists(const char* path) {
    if (!path) return 0;
    struct stat st;
#if OS_WIN
    return _stat(path, &st) == 0;
#else
    return stat(path, &st) == 0;
#endif
}

// 跨平台获取当前工作目录
static char* get_current_dir(void) {
#if OS_WIN
    static char cwd[PATH_MAX];
    if (_getcwd(cwd, PATH_MAX)) return cwd;
#else
    static char cwd[PATH_MAX];
    if (getcwd(cwd, PATH_MAX)) return cwd;
#endif
    return ".";
}

// 路径拼接
static char* path_join(const char* dir1, const char* dir2) {
    if (!dir1 || !dir2) return NULL;
    
    char* result = (char*)malloc(PATH_MAX);
    if (!result) return NULL;
    
    if (dir2[0] == '/' || (OS_WIN && (dir2[0] == '\\' || (dir2[1] == ':' && (dir2[2] == '/' || dir2[2] == '\\'))))) {
        safe_strcpy(result, dir2, PATH_MAX);
        return result;
    }
    
#if OS_WIN
    snprintf(result, PATH_MAX, "%s/%s", dir1, dir2);
#else
    snprintf(result, PATH_MAX, "%s/%s", dir1, dir2);
#endif
    
    char resolved[PATH_MAX] = {0};
    if (REALPATH(result, resolved)) {
        char* ret = STRDUP(resolved);
        free(result);
        return ret;
    }
    return result;
}

// 去除字符串首尾空格
static char* strstrip(char* str) {
    if (!str) return NULL;
    char* end;
    
    while (*str == ' ' || *str == '\t' || *str == '\n' || *str == '\r') str++;
    if (*str == '\0') return str;
    
    end = str + strlen(str) - 1;
    while (end > str && (*end == ' ' || *end == '\t' || *end == '\n' || *end == '\r')) end--;
    end[1] = '\0';
    
    return str;
}

// 1. 路径解析接口
const char* resolve_onyx_path(const char* path, const char* root_dir, const char* user_home, const char* current_dir) {
    if (!path || !root_dir || !user_home || !current_dir) return NULL;
    
    char result[PATH_MAX] = {0};
    char resolved[PATH_MAX] = {0};
    
    if (strcmp(path, "/") == 0 || (OS_WIN && strcmp(path, "\\") == 0)) {
        safe_strcpy(result, root_dir, PATH_MAX);
    } else if (strcmp(path, "~") == 0) {
        safe_strcpy(result, user_home, PATH_MAX);
    } else if (strcmp(path, "-") == 0) {
        const char* oldpwd = getenv("OLDPWD");
        safe_strcpy(result, oldpwd ? oldpwd : user_home, PATH_MAX);
    } else if (strncmp(path, "~/", 2) == 0 || (OS_WIN && strncmp(path, "~\\", 2) == 0)) {
        char* subpath = (char*)malloc(strlen(path) - 1);
        if (subpath) {
            strcpy(subpath, path + 2);
            char* joined = path_join(user_home, subpath);
            if (joined) {
                safe_strcpy(result, joined, PATH_MAX);
                free(joined);
            }
            free(subpath);
        }
    } else if (path[0] == '/' || (OS_WIN && (path[0] == '\\' || (path[1] == ':' && (path[2] == '/' || path[2] == '\\'))))) {
        char* joined = path_join(root_dir, path + (path[0] == '/' || path[0] == '\\' ? 1 : 0));
        if (joined) {
            safe_strcpy(result, joined, PATH_MAX);
            free(joined);
        }
    } else {
        char* joined = path_join(current_dir, path);
        if (joined) {
            safe_strcpy(result, joined, PATH_MAX);
            free(joined);
        }
    }
    
    if (REALPATH(result, resolved)) {
        return STRDUP(resolved);
    }
    return STRDUP(result);
}

// 2. 参数验证接口
const char* validate_onyx_param(const char* param, const char* root_dir, const char* user_home, const char* current_dir) {
    if (!param) return STRDUP("");
    if (param[0] == '-') return STRDUP(param);
    
    const char* virtual_path = resolve_onyx_path(param, root_dir, user_home, current_dir);
    if (virtual_path && path_exists(virtual_path)) {
        return virtual_path;
    }
    free((void*)virtual_path);
    
    char real_path[PATH_MAX] = {0};
    if (REALPATH(param, real_path) && path_exists(real_path)) {
        return STRDUP(real_path);
    }
    
    return STRDUP(param);
}

// 3. 提取命令中的路径参数
const char* extract_onyx_paths(const char* cmd, const char* root_dir, const char* user_home, const char* current_dir) {
    if (!cmd) return NULL;
    
    char* cmd_copy = STRDUP(cmd);
    if (!cmd_copy) return NULL;
    
    char* paths = (char*)malloc(PATH_MAX * 8);
    if (!paths) {
        free(cmd_copy);
        return NULL;
    }
    paths[0] = '\0';
    
    char* token = strtok(cmd_copy, " ");
    while ((token = strtok(NULL, " ")) != NULL) {
        if (token[0] == '-') continue;
        
        const char* validated = validate_onyx_param(token, root_dir, user_home, current_dir);
        if (validated) {
            int is_path = 0;
            char path_abs[PATH_MAX] = {0};
            
            if (REALPATH(validated, path_abs) && path_exists(path_abs)) {
                is_path = 1;
            } else if (strchr(validated, '/') || strchr(validated, '\\')) {
                is_path = 1;
            }
            
            if (is_path) {
                char lower_path[PATH_MAX] = {0};
                safe_strcpy(lower_path, path_abs, PATH_MAX);
                str_to_lower(lower_path);
                
                if (strlen(paths) > 0) strcat(paths, ",");
                strncat(paths, lower_path, PATH_MAX * 8 - strlen(paths) - 1);
            }
            free((void*)validated);
        }
    }
    
    free(cmd_copy);
    return paths[0] == '\0' ? NULL : paths;
}

// 4. 加载保护路径（修复unused parameter警告：添加(void)msg）
const char* load_protected_paths(const char* root_dir, const char* msg) {
    (void)msg; // 消除unused parameter警告
    if (!root_dir) return NULL;
    
    char oppath_file[PATH_MAX] = {0};
#if OS_WIN
    snprintf(oppath_file, PATH_MAX, "%s/etc/pki/oppath.txt", root_dir);
#else
    snprintf(oppath_file, PATH_MAX, "%s/etc/pki/oppath.txt", root_dir);
#endif
    
    if (!path_exists(oppath_file)) {
#if OS_WIN
        char oppath_dir1[PATH_MAX] = {0};
        char oppath_dir2[PATH_MAX] = {0};
        snprintf(oppath_dir1, PATH_MAX, "%s/etc", root_dir);
        snprintf(oppath_dir2, PATH_MAX, "%s/etc/pki", root_dir);
        _mkdir(oppath_dir1);
        _mkdir(oppath_dir2);
#else
        char oppath_dir[PATH_MAX] = {0};
        snprintf(oppath_dir, PATH_MAX, "%s/etc/pki", root_dir);
        char* p = oppath_dir + 1;
        while (*p) {
            if (*p == '/') {
                *p = '\0';
                mkdir(oppath_dir, 0700);
                *p = '/';
            }
            p++;
        }
        mkdir(oppath_dir, 0700);
#endif
        
        FILE* fp = fopen(oppath_file, "w");
        if (fp) {
            const char* default_paths = "# Onyx oppath保护路径列表\n# 格式：每行1个路径（相对根目录），支持通配符（如*.key）\n# 注释行以#开头，空行会被忽略\n"
                                       "onyx/\n"
                                       "etc/pki/\n"
                                       "onyxlog/\n"
                                       "tools/sys_tools/\n"
                                       "*.key\n"
                                       "*.pem\n"
                                       "*.cert\n"
                                       "*.db\n";
            fwrite(default_paths, strlen(default_paths), 1, fp);
            fclose(fp);
#if !OS_WIN
            chmod(oppath_file, 0600);
#endif
        }
    }
    
    char protected_paths[PATH_MAX * 8] = {0};
    FILE* fp = fopen(oppath_file, "r");
    if (fp) {
        char line[256] = {0};
        while (fgets(line, sizeof(line), fp)) {
            char* trim_line = line;
            while (*trim_line == ' ' || *trim_line == '\t') trim_line++;
            if (trim_line[0] == '#' || trim_line[0] == '\n' || trim_line[0] == '\0') continue;
            
            char* newline = strchr(trim_line, '\n');
            if (newline) *newline = '\0';
            char* end = trim_line + strlen(trim_line) - 1;
            while (end >= trim_line && (*end == ' ' || *end == '\t')) end--;
            end[1] = '\0';
            
            if (trim_line[0] == '*') {
                char* lower_line = STRDUP(trim_line);
                str_to_lower(lower_line);
                if (strlen(protected_paths) > 0) strcat(protected_paths, ",");
                strncat(protected_paths, lower_line, PATH_MAX * 8 - strlen(protected_paths) - 1);
                free(lower_line);
            } else {
                char* abs_path = path_join(root_dir, trim_line);
                if (abs_path) {
                    char* lower_abs = STRDUP(abs_path);
                    str_to_lower(lower_abs);
                    if (strlen(protected_paths) > 0) strcat(protected_paths, ",");
                    strncat(protected_paths, lower_abs, PATH_MAX * 8 - strlen(protected_paths) - 1);
                    free(lower_abs);
                    free(abs_path);
                }
                
                char* lower_rel = STRDUP(trim_line);
                str_to_lower(lower_rel);
                if (strlen(protected_paths) > 0) strcat(protected_paths, ",");
                strncat(protected_paths, lower_rel, PATH_MAX * 8 - strlen(protected_paths) - 1);
                free(lower_rel);
            }
        }
        fclose(fp);
    }
    
    return protected_paths[0] == '\0' ? STRDUP("*.key,*.pem,*.cert,*.db") : STRDUP(protected_paths);
}

// 5. 命令检查核心接口（与Python逻辑完全一致）
int check_command(const char* cmd, const char* root_dir, const char* user_home, const char* protected_paths_str) {
    if (!cmd || !root_dir || !user_home || !protected_paths_str) return 1;
    
    char* cmd_copy = STRDUP(cmd);
    char* stripped_cmd = strstrip(cmd_copy);
    if (strlen(stripped_cmd) == 0) {
        free(cmd_copy);
        return 1;
    }
    
    const char* current_dir = get_current_dir();
    const char* paths_str = extract_onyx_paths(stripped_cmd, root_dir, user_home, current_dir);
    free(cmd_copy);
    
    if (!paths_str || strlen(paths_str) == 0) {
        free((void*)paths_str);
        return 1;
    }
    
    char user_home_abs[PATH_MAX] = {0};
    if (!REALPATH(user_home, user_home_abs)) {
        free((void*)paths_str);
        return 1;
    }
    str_to_lower(user_home_abs);
    size_t home_len = strlen(user_home_abs);
    if (home_len > 0 && user_home_abs[home_len - 1] != '/') {
        strcat(user_home_abs, "/");
        home_len++;
    }
    
    char* paths_copy = STRDUP(paths_str);
    char* protected_copy = STRDUP(protected_paths_str);
    int result = 1;
    
    char* path_token = strtok(paths_copy, ",");
    while (path_token && result) {
        char cmd_path_abs[PATH_MAX] = {0};
        if (!REALPATH(path_token, cmd_path_abs)) {
            path_token = strtok(NULL, ",");
            continue;
        }
        
        str_to_lower(cmd_path_abs);
        
        if (strncmp(cmd_path_abs, user_home_abs, home_len) == 0) {
            path_token = strtok(NULL, ",");
            continue;
        }
        
        char* prot_token = strtok(protected_copy, ",");
        while (prot_token) {
            char protected[PATH_MAX] = {0};
            safe_strcpy(protected, prot_token, PATH_MAX);
            str_to_lower(protected);
            
            if (protected[0] == '*') {
                if (fnmatch(protected, cmd_path_abs, FNM_CASEFOLD) == 0) {
                    result = 0;
                    break;
                }
            } else {
                char prot_abs[PATH_MAX] = {0};
                if (REALPATH(protected, prot_abs)) {
                    str_to_lower(prot_abs);
                    size_t prot_len = strlen(prot_abs);
                    if (strcmp(cmd_path_abs, prot_abs) == 0 || 
                        (strncmp(cmd_path_abs, prot_abs, prot_len) == 0 && cmd_path_abs[prot_len] == '/')) {
                        result = 0;
                        break;
                    }
                }
            }
            prot_token = strtok(NULL, ",");
        }
        
        free(protected_copy);
        protected_copy = STRDUP(protected_paths_str);
        path_token = strtok(NULL, ",");
    }
    
    free((void*)paths_str);
    free(paths_copy);
    free(protected_copy);
    
    return result;
}

// 内存释放函数
void free_c_string(const char* str) {
    free((void*)str);
}

// 动态库导出接口（Windows专用）
#if OS_WIN
__declspec(dllexport) const char* __cdecl resolve_onyx_path(const char* path, const char* root_dir, const char* user_home, const char* current_dir);
__declspec(dllexport) const char* __cdecl validate_onyx_param(const char* param, const char* root_dir, const char* user_home, const char* current_dir);
__declspec(dllexport) const char* __cdecl extract_onyx_paths(const char* cmd, const char* root_dir, const char* user_home, const char* current_dir);
__declspec(dllexport) const char* __cdecl load_protected_paths(const char* root_dir, const char* msg);
__declspec(dllexport) int __cdecl check_command(const char* cmd, const char* root_dir, const char* user_home, const char* protected_paths_str);
__declspec(dllexport) void __cdecl free_c_string(const char* str);
#endif
