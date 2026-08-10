#ifndef PROCESS_CONTROL_H
#define PROCESS_CONTROL_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdbool.h>

#ifdef _WIN32
#include <windows.h>
#include <tlhelp32.h>
#else
#include <unistd.h>
#include <signal.h>
#include <sys/wait.h>
#include <sys/stat.h>
#endif

#define MAX_PROCESS_LIST 100
#define MAX_STRING_LEN 1024

// 进程信息结构体
typedef struct {
    int pid;
    float start_time;
    char request_id[MAX_STRING_LEN];
    char command[MAX_STRING_LEN];
} ProcessInfo;

// 配置结构体
typedef struct {
    char root_dir[MAX_STRING_LEN];
    char user_home_dir[MAX_STRING_LEN];
    int max_process_count;
    int cache_ttl;
} ProcessControlConfig;

// 全局状态
static ProcessInfo g_process_list[MAX_PROCESS_LIST];
static int g_process_count = 0;
static ProcessControlConfig g_config;

// 函数声明
bool init_process_control(const ProcessControlConfig* config);
bool add_process(int pid, float start_time, const char* request_id, const char* command);
bool remove_process(int pid);
bool check_process_alive(int pid);
bool kill_process(int pid);
int clear_stale_processes(void);
ProcessInfo* get_running_processes(int* out_count);
void free_process_list(ProcessInfo* list);

#endif
