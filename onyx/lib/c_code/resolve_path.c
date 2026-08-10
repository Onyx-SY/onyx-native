#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libgen.h>
#include <limits.h>
#include <errno.h>
#include <ctype.h>

#if defined(_WIN32) || defined(_WIN64)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <direct.h>
#define realpath _fullpath
#define access _access_s
#define F_OK 0
#define getenv _getenv
#else
#include <sys/stat.h>
#endif

#define MAX_PATH_LEN PATH_MAX
#define ENV_OLDPWD "OLDPWD"
#define SAFE_FREE(ptr) do { if (ptr) { free(ptr); ptr = NULL; } } while (0)
#define FORBIDDEN_MSG "You cannot cross root dir"

/* ================= perm_path 缓存（从Python传入） ================= */

typedef struct perm_rule {
    char pattern[MAX_PATH_LEN];
    int depth;
    struct perm_rule* next;
} perm_rule_t;

static perm_rule_t* perm_rules = NULL;

/* ================= 工具 ================= */

static int starts_with(const char *s, const char *p) {
    return strncmp(s, p, strlen(p)) == 0;
}

static int ends_with(const char *s, const char *p) {
    size_t slen = strlen(s), plen = strlen(p);
    return slen >= plen && strcmp(s + slen - plen, p) == 0;
}

/* ================= 路径规范化 ================= */

static int normalize_path(const char* path, char* resolved) {
    if (!path || !resolved) return -1;
#if defined(_WIN32) || defined(_WIN64)
    if (_fullpath(resolved, path, MAX_PATH_LEN) == NULL) return -1;
    for (char* p = resolved; *p; p++) if (*p == '\\') *p = '/';
#else
    if (realpath(path, resolved) == NULL) {
        strncpy(resolved, path, MAX_PATH_LEN - 1);
        resolved[MAX_PATH_LEN - 1] = '\0';
        for (char* p = resolved; *p; p++) if (*p == '\\') *p = '/';
    }
#endif
    return 0;
}

/* ================= 虚拟根校验 ================= */

static int is_in_root_dir(const char* path, const char* root_abs) {
    if (!path || !root_abs) return 0;
    size_t root_len = strlen(root_abs);
    if (strncmp(path, root_abs, root_len) != 0) return 0;
    return (path[root_len] == '\0' || path[root_len] == '/');
}

static int is_root_overlap(const char* root_dir) {
    char real_root[MAX_PATH_LEN] = {0};
    char virtual_root[MAX_PATH_LEN] = {0};
#if defined(_WIN32) || defined(_WIN64)
    _fullpath(real_root, "/", MAX_PATH_LEN);
#else
    realpath("/", real_root);
#endif
    normalize_path(root_dir, virtual_root);
    return strcmp(real_root, virtual_root) == 0;
}

/* ================= 设置 perm_path 规则（从Python调用） ================= */

void set_perm_rules(const char* rules_json) {
    // 先释放原有规则
    perm_rule_t* cur = perm_rules;
    while (cur) {
        perm_rule_t* next = cur->next;
        free(cur);
        cur = next;
    }
    perm_rules = NULL;
    
    if (!rules_json || strlen(rules_json) == 0) return;
    
    // 解析 JSON
    char* json_copy = strdup(rules_json);
    if (!json_copy) return;
    
    char* saveptr;
    char* line = strtok_r(json_copy, "\n", &saveptr);
    
    while (line) {
        // 跳过空行
        if (strlen(line) > 0) {
            char* colon = strchr(line, ':');
            if (colon) {
                *colon = '\0';
                char* pattern = line;
                char* depth_str = colon + 1;
                
                perm_rule_t* rule = (perm_rule_t*)calloc(1, sizeof(perm_rule_t));
                if (rule) {
                    strncpy(rule->pattern, pattern, MAX_PATH_LEN - 1);
                    rule->pattern[MAX_PATH_LEN - 1] = '\0';
                    rule->depth = atoi(depth_str);
                    rule->next = perm_rules;
                    perm_rules = rule;
                }
            }
        }
        line = strtok_r(NULL, "\n", &saveptr);
    }
    
    free(json_copy);
}

/* ================= 规则匹配 ================= */

static int match_perm_rule(const char* path, char* out_resolved) {
    for (perm_rule_t* r = perm_rules; r; r = r->next) {
        if (r->depth > 0) {
            int cnt = 0;
            for (const char* p = path; *p; p++)
                if (*p == '/') cnt++;
            if (cnt != r->depth + 1) continue;
        }
        if (starts_with(path, r->pattern)) {
            snprintf(out_resolved, MAX_PATH_LEN, "%s", path);
            return 1;
        }
    }
    return 0;
}

/* ================= 是否解析 ================= */

static int should_resolve(const char* path, const char* root_abs) {
    if (!path) return 0;
    if (strcmp(path, ".") == 0) return 0;

    if (path[0] == '/' && is_in_root_dir(path, root_abs))
        return 0;

    return (
        path[0] == '/' ||
        path[0] == '~' ||
        path[0] == '-' ||
        starts_with(path, "./") ||
        starts_with(path, "../")
    );
}

/* ================= 核心解析 ================= */

char* resolve_path(const char* path,
                   const char* root_dir,
                   const char* user_home,
                   const char* current_dir) {

    if (!path || strlen(path) == 0) {
        char* r = malloc(1);
        if (r) *r = '\0';
        return r;
    }

    if (is_root_overlap(root_dir)) {
        char* r = malloc(strlen(path) + 1);
        strcpy(r, path);
        return r;
    }

    char root_abs[MAX_PATH_LEN] = {0};
    char user_abs[MAX_PATH_LEN] = {0};
    char current_abs[MAX_PATH_LEN] = {0};

    normalize_path(root_dir, root_abs);
    normalize_path(user_home, user_abs);
    normalize_path(current_dir, current_abs);

    char resolved[MAX_PATH_LEN] = {0};

    /* perm_path 规则命中 → 强制解析 */
    if (match_perm_rule(path, resolved)) {
        normalize_path(resolved, resolved);
        if (!is_in_root_dir(resolved, root_abs)) {
            char* r = malloc(strlen(FORBIDDEN_MSG) + 1);
            strcpy(r, FORBIDDEN_MSG);
            return r;
        }
        char* r = malloc(strlen(resolved) + 1);
        strcpy(r, resolved);
        return r;
    }

    if (!should_resolve(path, root_abs)) {
        char* r = malloc(strlen(path) + 1);
        strcpy(r, path);
        return r;
    }

    if (strcmp(path, "/") == 0) {
        strcpy(resolved, root_abs);
    } else if (strcmp(path, "~") == 0) {
        strcpy(resolved, user_abs);
    } else if (strcmp(path, "-") == 0) {
        const char* oldpwd = getenv(ENV_OLDPWD);
        normalize_path(oldpwd ? oldpwd : user_abs, resolved);
    } else if (starts_with(path, "~/")) {
        snprintf(resolved, MAX_PATH_LEN, "%s/%s", user_abs, path + 2);
    } else if (path[0] == '/') {
        snprintf(resolved, MAX_PATH_LEN, "%s/%s", root_abs, path + 1);
    } else {
        snprintf(resolved, MAX_PATH_LEN, "%s/%s", current_abs, path);
    }

    normalize_path(resolved, resolved);

    if (!is_in_root_dir(resolved, root_abs)) {
        char* r = malloc(strlen(FORBIDDEN_MSG) + 1);
        strcpy(r, FORBIDDEN_MSG);
        return r;
    }

    char* r = malloc(strlen(resolved) + 1);
    strcpy(r, resolved);
    return r;
}