#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include <ctype.h>
#include <limits.h>  // 跨系统PATH_MAX定义

// 跨系统兼容性宏定义
#if defined(_WIN32) || defined(WIN32)
#define S_ISREG(m) ((m) & _S_IFREG)
#define S_IXUSR _S_IXUSR
#define S_IXGRP _S_IXGRP
#define S_IXOTH _S_IXOTH
#define PATH_MAX MAX_PATH
#define opendir _opendir
#define readdir _readdir
#define closedir _closedir
#define stat _stat
typedef struct _stat struct_stat;
#else
#define struct_stat struct stat
#endif

// 系统可执行后缀定义（统一结构，无冗余）
typedef struct {
    const char** suffixes;
    int count;
} ExecSuffixes;

// 根据系统类型获取可执行后缀（统一逻辑）
static ExecSuffixes get_exec_suffixes(const char* sys_type) {
    ExecSuffixes es = {NULL, 0};
    static const char* windows_suffixes[] = {".exe", ".com", ".bat", ".cmd", ".ps1", ".vbs", NULL};
    static const char* unix_suffixes[] = {".sh", "", NULL};  // Linux/macOS/Termux通用

    if (strcmp(sys_type, "Windows") == 0) {
        es.suffixes = windows_suffixes;
        es.count = 6;
    } else {  // 其他系统统一使用unix后缀
        es.suffixes = unix_suffixes;
        es.count = 2;
    }
    return es;
}

// 跨系统路径拼接（处理Windows的\和Unix的/）
static void join_path(char* out_path, const char* dir, const char* filename) {
#if defined(_WIN32) || defined(WIN32)
    snprintf(out_path, PATH_MAX, "%s\\%s", dir, filename);
#else
    snprintf(out_path, PATH_MAX, "%s/%s", dir, filename);
#endif
}

// 判断文件是否为可执行文件（跨系统统一逻辑）
static int is_executable(const char* file_path, const char* filename, const char* sys_type, ExecSuffixes es) {
    struct_stat st;
    // Windows：仅判断后缀（无需权限检查）
    if (strcmp(sys_type, "Windows") == 0) {
        int len = strlen(filename);
        for (int i = 0; i < es.count; i++) {
            const char* suffix = es.suffixes[i];
            int suffix_len = strlen(suffix);
            if (len >= suffix_len && strcasecmp(filename + len - suffix_len, suffix) == 0) {
                return 1;
            }
        }
        return 0;
    }
    // Unix类系统（Linux/macOS/Termux）：判断文件属性+执行权限
    if (stat(file_path, &st) != 0) return 0;
    if (!S_ISREG(st.st_mode)) return 0;  // 不是普通文件
    if ((st.st_mode & (S_IXUSR | S_IXGRP | S_IXOTH)) == 0) return 0;  // 无执行权限
    return 1;
}

// 去除Windows文件名后缀（其他系统直接返回原名称）
static char* process_cmd_name(const char* filename, const char* sys_type, ExecSuffixes es) {
    if (strcmp(sys_type, "Windows") != 0) {
        return strdup(filename);
    }
    // Windows专用：去除后缀
    int len = strlen(filename);
    for (int i = 0; i < es.count; i++) {
        const char* suffix = es.suffixes[i];
        int suffix_len = strlen(suffix);
        if (len >= suffix_len && strcasecmp(filename + len - suffix_len, suffix) == 0) {
            char* cmd = malloc(len - suffix_len + 1);
            strncpy(cmd, filename, len - suffix_len);
            cmd[len - suffix_len] = '\0';
            return cmd;
        }
    }
    return strdup(filename);
}

// 扫描单个目录（跨系统统一逻辑）
static int scan_dir(const char* dir_path, const char* sys_type, ExecSuffixes es, char*** out_cmds, int* out_count) {
    DIR* dir = opendir(dir_path);
    if (!dir) return 0;

    struct dirent* entry;
    int capacity = 32;
    *out_cmds = malloc(capacity * sizeof(char*));
    *out_count = 0;

    while ((entry = readdir(dir)) != NULL) {
        // 跳过隐藏文件、目录、符号链接
        if (entry->d_name[0] == '.' || entry->d_type == DT_DIR || entry->d_type == DT_LNK) {
            continue;
        }
        // 跳过"import"命令
        if (strcasecmp(entry->d_name, "import") == 0) {
            continue;
        }
        // 跨系统路径拼接
        char file_path[PATH_MAX];
        join_path(file_path, dir_path, entry->d_name);
        // 判断是否为可执行文件
        if (!is_executable(file_path, entry->d_name, sys_type, es)) {
            continue;
        }
        // 处理命令名（Windows去后缀，其他系统直接用）
        char* cmd_name = process_cmd_name(entry->d_name, sys_type, es);
        // 转为小写（去重）
        for (int i = 0; cmd_name[i]; i++) {
            cmd_name[i] = tolower(cmd_name[i]);
        }
        // 动态扩容
        if (*out_count >= capacity) {
            capacity *= 2;
            *out_cmds = realloc(*out_cmds, capacity * sizeof(char*));
        }
        (*out_cmds)[*out_count] = cmd_name;
        (*out_count)++;
    }

    closedir(dir);
    return 1;
}

// 外部导出函数：扫描所有目录（跨系统统一入口）
int scan_path_cmds(const char* sys_type, const char** path_dirs, int dir_count, char*** out_cmds, int* out_count) {
    if (!sys_type || !path_dirs || dir_count <= 0 || !out_cmds || !out_count) {
        return -1;
    }

    ExecSuffixes es = get_exec_suffixes(sys_type);
    if (es.suffixes == NULL) {
        return -2;
    }

    // 合并所有目录的命令
    int total_count = 0;
    char** all_cmds = NULL;

    for (int i = 0; i < dir_count; i++) {
        const char* dir = path_dirs[i];
        char** dir_cmds = NULL;
        int dir_count = 0;

        if (scan_dir(dir, sys_type, es, &dir_cmds, &dir_count) && dir_count > 0) {
            // 扩容总命令列表
            all_cmds = realloc(all_cmds, (total_count + dir_count) * sizeof(char*));
            if (!all_cmds) {
                // 内存分配失败，释放已分配资源
                for (int j = 0; j < total_count; j++) free(all_cmds[j]);
                free(all_cmds);
                for (int j = 0; j < dir_count; j++) free(dir_cmds[j]);
                free(dir_cmds);
                return -3;
            }
            // 拷贝当前目录的命令
            memcpy(all_cmds + total_count, dir_cmds, dir_count * sizeof(char*));
            total_count += dir_count;
            free(dir_cmds);  // 释放临时指针数组
        }
    }

    *out_cmds = all_cmds;
    *out_count = total_count;
    return 0;
}

// 外部导出函数：释放扫描结果内存（跨系统统一）
void free_scan_result(char*** cmds, int count) {
    if (cmds && *cmds) {
        for (int i = 0; i < count; i++) {
            if ((*cmds)[i]) free((*cmds)[i]);
        }
        free(*cmds);
        *cmds = NULL;
    }
}
