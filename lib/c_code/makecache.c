#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>
#include <ctype.h>

#define MAX_ENTRIES 200
#define MAX_PATH_LEN 1024
#define JSON_BUF_SIZE 8192

// 目录项结构体
typedef struct {
    char name[MAX_PATH_LEN];
    int is_file;
    int is_link;
    long long size;
    time_t mtime;
} DirEntry;

// 缓存结果结构体
typedef struct {
    DirEntry entries[MAX_ENTRIES];
    int file_count;
    int total_entries;
} DirCacheResult;

/**
 * 构建目录缓存
 * @param dir_path 目录路径
 * @param ttl 缓存过期时间（秒）
 * @param max_files 最大缓存文件数
 * @return 缓存结果指针（需调用free_dir_cache释放）
 */
void* build_dir_cache(const char* dir_path, int ttl, int max_files) {
    if (!dir_path || !*dir_path) return NULL;
    
    DIR* dir = opendir(dir_path);
    if (!dir) return NULL;
    
    DirCacheResult* result = (DirCacheResult*)malloc(sizeof(DirCacheResult));
    if (!result) {
        closedir(dir);
        return NULL;
    }
    memset(result, 0, sizeof(DirCacheResult));
    
    struct dirent* entry;
    char full_path[MAX_PATH_LEN];
    struct stat st;

    while ((entry = readdir(dir)) != NULL && result->total_entries < MAX_ENTRIES) {
        // 跳过隐藏文件
        if (entry->d_name[0] == '.') {
            continue;
        }
        
        // 构建完整路径
        snprintf(full_path, MAX_PATH_LEN, "%s/%s", dir_path, entry->d_name);
        
        if (stat(full_path, &st) != 0) {
            continue;
        }
        
        DirEntry* dir_entry = &result->entries[result->total_entries];
        strncpy(dir_entry->name, entry->d_name, MAX_PATH_LEN - 1);
        
        dir_entry->is_file = S_ISREG(st.st_mode) ? 1 : 0;
        dir_entry->is_link = S_ISLNK(st.st_mode) ? 1 : 0;
        dir_entry->size = dir_entry->is_file ? st.st_size : 4096;
        dir_entry->mtime = st.st_mtime;
        
        if (dir_entry->is_file && !dir_entry->is_link) {
            result->file_count++;
        }
        
        result->total_entries++;
    }
    
    closedir(dir);
    
    // 转换为JSON字符串返回（供Python解析）
    char* json_buf = (char*)malloc(JSON_BUF_SIZE);
    if (!json_buf) {
        free(result);
        return NULL;
    }
    
    int offset = snprintf(json_buf, JSON_BUF_SIZE, "{\"file_count\":%d,\"entries\":[", result->file_count);
    for (int i = 0; i < result->total_entries && offset < JSON_BUF_SIZE - 256; i++) {
        DirEntry* e = &result->entries[i];
        offset += snprintf(json_buf + offset, JSON_BUF_SIZE - offset,
            "{\"name\":\"%s\",\"is_file\":%d,\"is_link\":%d,\"size\":%lld,\"mtime\":%lld}",
            e->name, e->is_file, e->is_link, e->size, (long long)e->mtime);
        
        if (i < result->total_entries - 1) {
            offset += snprintf(json_buf + offset, JSON_BUF_SIZE - offset, ",");
        }
    }
    snprintf(json_buf + offset, JSON_BUF_SIZE - offset, "]}");
    
    free(result);
    return json_buf;
}

/**
 * 释放缓存内存
 */
void free_dir_cache(void* cache_ptr) {
    if (cache_ptr) {
        free(cache_ptr);
    }
}
