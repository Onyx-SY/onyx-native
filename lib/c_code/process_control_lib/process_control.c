#include "process_control.h"

bool init_process_control(const ProcessControlConfig* config) {
    if (!config) return false;
    
    // 复制配置
    strncpy(g_config.root_dir, config->root_dir, MAX_STRING_LEN - 1);
    strncpy(g_config.user_home_dir, config->user_home_dir, MAX_STRING_LEN - 1);
    g_config.max_process_count = config->max_process_count;
    g_config.cache_ttl = config->cache_ttl;
    
    // 初始化进程列表
    memset(g_process_list, 0, sizeof(g_process_list));
    g_process_count = 0;
    
    return true;
}

static int find_process_index(int pid) {
    for (int i = 0; i < g_process_count; i++) {
        if (g_process_list[i].pid == pid) {
            return i;
        }
    }
    return -1;
}

bool add_process(int pid, float start_time, const char* request_id, const char* command) {
    if (g_process_count >= g_config.max_process_count || find_process_index(pid) != -1) {
        return false;
    }
    
    ProcessInfo* proc = &g_process_list[g_process_count];
    proc->pid = pid;
    proc->start_time = start_time;
    strncpy(proc->request_id, request_id ? request_id : "", MAX_STRING_LEN - 1);
    strncpy(proc->command, command ? command : "", MAX_STRING_LEN - 1);
    
    g_process_count++;
    return true;
}

bool remove_process(int pid) {
    int idx = find_process_index(pid);
    if (idx == -1) return false;
    
    // 移动后续进程覆盖
    for (int i = idx; i < g_process_count - 1; i++) {
        g_process_list[i] = g_process_list[i + 1];
    }
    memset(&g_process_list[g_process_count - 1], 0, sizeof(ProcessInfo));
    g_process_count--;
    
    return true;
}

bool check_process_alive(int pid) {
#ifdef _WIN32
    HANDLE hSnapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (hSnapshot == INVALID_HANDLE_VALUE) return false;
    
    PROCESSENTRY32 pe32;
    pe32.dwSize = sizeof(PROCESSENTRY32);
    bool found = false;
    
    if (Process32First(hSnapshot, &pe32)) {
        do {
            if (pe32.th32ProcessID == (DWORD)pid) {
                found = true;
                break;
            }
        } while (Process32Next(hSnapshot, &pe32));
    }
    
    CloseHandle(hSnapshot);
    return found;
#else
    return kill(pid, 0) == 0;
#endif
}

bool kill_process(int pid) {
#ifdef _WIN32
    HANDLE hProcess = OpenProcess(PROCESS_TERMINATE, FALSE, (DWORD)pid);
    if (!hProcess) return false;
    
    bool success = TerminateProcess(hProcess, 0) != 0;
    CloseHandle(hProcess);
#else
    bool success = kill(pid, SIGKILL) == 0;
#endif
    
    if (success) {
        remove_process(pid);
    }
    return success;
}

int clear_stale_processes(void) {
    time_t current_time = time(NULL);
    int stale_count = 0;
    
    for (int i = g_process_count - 1; i >= 0; i--) {
        ProcessInfo* proc = &g_process_list[i];
        // 检查进程是否存活或缓存是否过期
        if (!check_process_alive(proc->pid) || (current_time - proc->start_time) > g_config.cache_ttl) {
            remove_process(proc->pid);
            stale_count++;
        }
    }
    
    return stale_count;
}

ProcessInfo* get_running_processes(int* out_count) {
    clear_stale_processes();
    *out_count = g_process_count;
    
    if (g_process_count == 0) return NULL;
    
    // 分配内存（调用者需调用free_process_list释放）
    ProcessInfo* list = (ProcessInfo*)malloc(sizeof(ProcessInfo) * g_process_count);
    if (!list) return NULL;
    
    memcpy(list, g_process_list, sizeof(ProcessInfo) * g_process_count);
    return list;
}

void free_process_list(ProcessInfo* list) {
    if (list) {
        free(list);
    }
}
