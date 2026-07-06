// build_tool_index.c
// 编译命令: gcc -O3 -fPIC -shared -o libbuild_tool_index.so build_tool_index.c -lpthread
// Windows: gcc -O3 -shared -o build_tool_index.dll build_tool_index.c -lpthread

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include <pthread.h>
#include <time.h>

#ifdef _WIN32
    #include <windows.h>
    #include <shlwapi.h>
    #pragma comment(lib, "shlwapi.lib")
    #define PATH_SEP '\\'
    #define PATH_SEP_STR "\\"
#else
    #define PATH_SEP '/'
    #define PATH_SEP_STR "/"
#endif

#define MAX_PATH_LEN 4096
#define MAX_TOOL_COUNT 100000
#define MAX_THREADS 64
#define BATCH_SIZE 100

// 工具信息结构体
typedef struct {
    char path[MAX_PATH_LEN];
    char name[256];
    uint8_t is_cli;
    uint8_t tool_perm;
    char tool_type[32];
    uint64_t size;
    int64_t mtime;
} ToolInfo;

// 扫描任务结构体
typedef struct {
    char base_dir[MAX_PATH_LEN];
    char **sub_dirs;
    int sub_dir_count;
    int start_idx;
    int end_idx;
    ToolInfo *results;
    int *result_count;
    pthread_mutex_t *mutex;
    uint8_t sys_type;  // 0=Linux, 1=Windows
    int depth;
} ScanTask;

// 支持的入口文件
static const char *SUPPORTED_MAIN_FILES[] = {
    "Main.py", "Main.pyc", "main.py", "main.pyc",
    "tool.py", "tool.pyc", "entry.py", "entry.pyc",
    NULL
};

// 入口关键词
static const char *MAIN_KEYWORDS[] = {
    "main", "entry", "start", "launch", NULL
};

// 工具类型映射
static const struct {
    const char *keyword;
    const char *type;
} TOOL_TYPE_MAP[] = {
    {"scan", "scan"},
    {"crack", "crack"},
    {"exploit", "exploit"},
    {"wireless", "wireless"},
    {"web", "web"},
    {"app", "app"},
    {NULL, "other"}
};

// 快速字符串转小写（原地）
static void str_to_lower(char *str) {
    for (; *str; ++str) {
        *str = (*str >= 'A' && *str <= 'Z') ? (*str + 32) : *str;
    }
}

// 检查字符串是否以某后缀结尾
static bool ends_with(const char *str, const char *suffix) {
    size_t str_len = strlen(str);
    size_t suffix_len = strlen(suffix);
    if (str_len < suffix_len) return false;
    return strcmp(str + str_len - suffix_len, suffix) == 0;
}

// 判断是否为 CLI 工具
static uint8_t is_cli_tool(const char *tool_dir) {
    char config_path[MAX_PATH_LEN];
    snprintf(config_path, sizeof(config_path), "%s" PATH_SEP_STR "config.conf", tool_dir);
    
    FILE *fp = fopen(config_path, "r");
    if (!fp) return 1;  // 默认是 CLI
    
    char line[256];
    uint8_t cli_val = 1;
    while (fgets(line, sizeof(line), fp)) {
        if (strncmp(line, "cli=", 4) == 0) {
            cli_val = atoi(line + 4);
            break;
        }
    }
    fclose(fp);
    return (cli_val == 1 || cli_val == 2) ? 1 : 0;
}

// 获取工具权限
static uint8_t get_tool_perm(const char *tool_dir) {
    char perm_path[MAX_PATH_LEN];
    snprintf(perm_path, sizeof(perm_path), "%s" PATH_SEP_STR ".perm", tool_dir);
    
    FILE *fp = fopen(perm_path, "r");
    if (!fp) return 3;  // 默认权限3
    
    int perm = 3;
    fscanf(fp, "%d", &perm);
    fclose(fp);
    
    if (perm < 1) perm = 1;
    if (perm > 5) perm = 5;
    return (uint8_t)perm;
}

// 获取工具类型
static const char* get_tool_type(const char *tool_name) {
    char name_lower[256];
    strncpy(name_lower, tool_name, sizeof(name_lower) - 1);
    name_lower[sizeof(name_lower) - 1] = '\0';
    str_to_lower(name_lower);
    
    for (int i = 0; TOOL_TYPE_MAP[i].keyword; i++) {
        if (strstr(name_lower, TOOL_TYPE_MAP[i].keyword)) {
            return TOOL_TYPE_MAP[i].type;
        }
    }
    return "other";
}

// 查找入口文件
static const char* find_tool_entry(const char *tool_dir) {
    DIR *dir = opendir(tool_dir);
    if (!dir) return NULL;
    
    struct dirent *entry;
    static char entry_path[MAX_PATH_LEN];
    const char *best_match = NULL;
    int best_priority = 999;
    
    while ((entry = readdir(dir)) != NULL) {
        if (entry->d_name[0] == '.') continue;
        
        // 检查是否支持的文件类型
        if (ends_with(entry->d_name, ".py") || ends_with(entry->d_name, ".pyc")) {
            // 优先级1: 完全匹配工具名
            char *dot_pos = strchr(entry->d_name, '.');
            size_t base_len = dot_pos ? (dot_pos - entry->d_name) : strlen(entry->d_name);
            char *tool_name = strrchr(tool_dir, PATH_SEP);
            tool_name = tool_name ? tool_name + 1 : (char*)tool_dir;
            
            if (strncasecmp(entry->d_name, tool_name, base_len) == 0) {
                best_match = entry->d_name;
                break;
            }
            
            // 优先级2: 匹配 MAIN_FILE_KEYWORDS
            for (int i = 0; SUPPORTED_MAIN_FILES[i]; i++) {
                if (strcmp(entry->d_name, SUPPORTED_MAIN_FILES[i]) == 0) {
                    best_match = entry->d_name;
                    best_priority = 1;
                    break;
                }
            }
            
            // 优先级3: 匹配关键词
            if (!best_match || best_priority > 2) {
                char name_lower[256];
                strncpy(name_lower, entry->d_name, sizeof(name_lower) - 1);
                str_to_lower(name_lower);
                
                for (int i = 0; MAIN_KEYWORDS[i]; i++) {
                    if (strstr(name_lower, MAIN_KEYWORDS[i])) {
                        best_match = entry->d_name;
                        best_priority = 2;
                        break;
                    }
                }
            }
        }
    }
    
    closedir(dir);
    
    if (best_match) {
        snprintf(entry_path, sizeof(entry_path), "%s" PATH_SEP_STR "%s", tool_dir, best_match);
        return entry_path;
    }
    return NULL;
}

// 扫描单个目录
static void scan_directory(const char *dir_path, int depth, 
                           ToolInfo *results, int *result_count,
                           pthread_mutex_t *mutex, uint8_t sys_type) {
    DIR *dir = opendir(dir_path);
    if (!dir) return;
    
    struct dirent *entry;
    char sub_path[MAX_PATH_LEN];
    
    while ((entry = readdir(dir)) != NULL) {
        if (entry->d_name[0] == '.') continue;
        
        snprintf(sub_path, sizeof(sub_path), "%s" PATH_SEP_STR "%s", dir_path, entry->d_name);
        
        struct stat st;
        if (stat(sub_path, &st) != 0) continue;
        
        if (S_ISDIR(st.st_mode)) {
            // 深度为2的目录才是工具目录
            if (depth == 1) {
                // 这是工具目录，直接扫描
                const char *entry_file = find_tool_entry(sub_path);
                if (entry_file) {
                    pthread_mutex_lock(mutex);
                    if (*result_count < MAX_TOOL_COUNT) {
                        ToolInfo *info = &results[*result_count];
                        strncpy(info->path, entry_file, MAX_PATH_LEN - 1);
                        
                        // 提取工具名
                        char *tool_name = strrchr(sub_path, PATH_SEP);
                        tool_name = tool_name ? tool_name + 1 : (char*)sub_path;
                        strncpy(info->name, tool_name, sizeof(info->name) - 1);
                        
                        info->is_cli = is_cli_tool(sub_path);
                        info->tool_perm = get_tool_perm(sub_path);
                        strncpy(info->tool_type, get_tool_type(tool_name), sizeof(info->tool_type) - 1);
                        info->size = st.st_size;
                        info->mtime = st.st_mtime;
                        
                        (*result_count)++;
                    }
                    pthread_mutex_unlock(mutex);
                }
            } else if (depth < 1) {
                // 继续向下扫描
                scan_directory(sub_path, depth + 1, results, result_count, mutex, sys_type);
            }
        }
    }
    
    closedir(dir);
}

// 线程工作函数
static void* scan_worker(void *arg) {
    ScanTask *task = (ScanTask*)arg;
    
    for (int i = task->start_idx; i < task->end_idx; i++) {
        scan_directory(task->sub_dirs[i], task->depth + 1, 
                       task->results, task->result_count, 
                       task->mutex, task->sys_type);
    }
    
    return NULL;
}

// 获取所有子目录
static int get_sub_dirs(const char *base_dir, char ***dirs_out) {
    DIR *dir = opendir(base_dir);
    if (!dir) return 0;
    
    struct dirent *entry;
    char **dirs = malloc(sizeof(char*) * MAX_TOOL_COUNT);
    int count = 0;
    
    while ((entry = readdir(dir)) != NULL && count < MAX_TOOL_COUNT) {
        if (entry->d_name[0] == '.') continue;
        
        char full_path[MAX_PATH_LEN];
        snprintf(full_path, sizeof(full_path), "%s" PATH_SEP_STR "%s", base_dir, full_path);
        
        struct stat st;
        if (stat(full_path, &st) == 0 && S_ISDIR(st.st_mode)) {
            dirs[count] = malloc(MAX_PATH_LEN);
            strncpy(dirs[count], full_path, MAX_PATH_LEN - 1);
            count++;
        }
    }
    
    closedir(dir);
    *dirs_out = dirs;
    return count;
}

// 主函数：构建工具索引
// 参数格式: root_dir|user_home_dir|sys_type
// 返回: JSON格式的索引数据
char* build_tool_index(const char *root_dir, const char *user_home_dir, 
                       const char *sys_type_str, int *out_len) {
    if (!root_dir || !user_home_dir) {
        if (out_len) *out_len = 0;
        return NULL;
    }
    
    char tool_main_dir[MAX_PATH_LEN];
    snprintf(tool_main_dir, sizeof(tool_main_dir), "%s" PATH_SEP_STR "tools", root_dir);
    
    // 获取子目录（分类目录）
    char **cat_dirs = NULL;
    int cat_count = get_sub_dirs(tool_main_dir, &cat_dirs);
    
    if (cat_count == 0) {
        if (out_len) *out_len = 0;
        if (cat_dirs) free(cat_dirs);
        return NULL;
    }
    
    // 分配结果数组
    ToolInfo *results = malloc(sizeof(ToolInfo) * MAX_TOOL_COUNT);
    memset(results, 0, sizeof(ToolInfo) * MAX_TOOL_COUNT);
    int result_count = 0;
    pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
    
    uint8_t sys_type = 0;
    if (strstr(sys_type_str, "Windows") != NULL) sys_type = 1;
    
    // 获取二级目录（工具目录）
    char **tool_dirs = NULL;
    int tool_count = 0;
    
    for (int i = 0; i < cat_count; i++) {
        char **sub_dirs = NULL;
        int sub_count = get_sub_dirs(cat_dirs[i], &sub_dirs);
        
        for (int j = 0; j < sub_count; j++) {
            if (tool_count >= MAX_TOOL_COUNT) break;
            tool_dirs = realloc(tool_dirs, sizeof(char*) * (tool_count + 1));
            tool_dirs[tool_count] = malloc(MAX_PATH_LEN);
            strncpy(tool_dirs[tool_count], sub_dirs[j], MAX_PATH_LEN - 1);
            tool_count++;
        }
        
        for (int j = 0; j < sub_count; j++) free(sub_dirs[j]);
        free(sub_dirs);
    }
    
    // 多线程扫描
    int thread_count = (int)sysconf(_SC_NPROCESSORS_ONLN);
    if (thread_count > MAX_THREADS) thread_count = MAX_THREADS;
    if (thread_count < 1) thread_count = 1;
    
    pthread_t threads[MAX_THREADS];
    ScanTask tasks[MAX_THREADS];
    
    int dirs_per_thread = (tool_count + thread_count - 1) / thread_count;
    
    for (int t = 0; t < thread_count; t++) {
        tasks[t].sub_dirs = tool_dirs;
        tasks[t].start_idx = t * dirs_per_thread;
        tasks[t].end_idx = (t + 1) * dirs_per_thread;
        if (tasks[t].end_idx > tool_count) tasks[t].end_idx = tool_count;
        tasks[t].results = results;
        tasks[t].result_count = &result_count;
        tasks[t].mutex = &mutex;
        tasks[t].sys_type = sys_type;
        tasks[t].depth = 1;
        
        if (tasks[t].start_idx < tasks[t].end_idx) {
            pthread_create(&threads[t], NULL, scan_worker, &tasks[t]);
        }
    }
    
    for (int t = 0; t < thread_count; t++) {
        if (tasks[t].start_idx < tasks[t].end_idx) {
            pthread_join(threads[t], NULL);
        }
    }
    
    // 构建JSON输出
    size_t json_size = 1024 * 1024;  // 1MB初始
    char *json = malloc(json_size);
    size_t offset = 0;
    
    offset += snprintf(json + offset, json_size - offset, "{\"tools\":[");
    
    for (int i = 0; i < result_count; i++) {
        if (i > 0) offset += snprintf(json + offset, json_size - offset, ",");
        offset += snprintf(json + offset, json_size - offset, 
            "{\"name\":\"%s\",\"path\":\"%s\",\"is_cli\":%d,\"tool_perm\":%d,\"tool_type\":\"%s\",\"size\":%llu,\"mtime\":%lld}",
            results[i].name, results[i].path, results[i].is_cli, 
            results[i].tool_perm, results[i].tool_type,
            (unsigned long long)results[i].size, (long long)results[i].mtime);
    }
    
    offset += snprintf(json + offset, json_size - offset, "]}");
    
    if (out_len) *out_len = (int)offset;
    
    // 清理
    for (int i = 0; i < cat_count; i++) free(cat_dirs[i]);
    free(cat_dirs);
    for (int i = 0; i < tool_count; i++) free(tool_dirs[i]);
    free(tool_dirs);
    free(results);
    
    return json;
}

// 释放内存
void free_build_result(char *result) {
    if (result) free(result);
}