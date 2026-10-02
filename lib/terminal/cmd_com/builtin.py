# cmd_com/builtin.py
"""
Onyx 内置动态命令补全集合（60 个）。

设计原则：
    - 每个补全器都走 cached()，避免频繁 subprocess 造成卡顿
    - 子命令 / 参数优先走「真实查询」：
        * 项目内数据（.git / package.json / pyproject.toml /
          composer.json / Makefile / playbook / 测试文件 / 已跟踪文件）
          直接读文件，不 fork 子进程
        * 系统或容器 / 云侧数据（docker ps、kubectl get、helm list、
          aws configure list-profiles、gh pr list ...）调用 CLI 拿到
          真实结果
        * 静态表只做兜底
    - 外部命令不存在时静默降级为静态候选（不报错、不阻塞）
    - git 的分支/标签/remote 直接读 .git（不 fork），绕开 Termux
      共享存储上的「dubious ownership」报错，也省掉每次补全的 fork 开销

命令覆盖（60 个）：
    git docker kubectl npm pip cargo go make systemctl ssh tmux
    aws terraform ansible helm composer mvn gradle dotnet conda
    poetry yarn pnpm pytest rake bundle jupyter hugo ffmpeg gcloud
    gh glab az podman docker-compose minikube kind helmfile psql
    mysql redis-cli mongosh sqlite3 curl wget rsync scp tar openssl
    gpg jq yq rg fd bat code nvim cmake bazel deno

如果你要自己扩展：
    - 在 ~/.cmd_com/ 下新建 xxx.py
    - 实现 register(reg) 或 COMMANDS = {...}
    - 见 dynamic_cmd.py 顶部文档
"""

import fnmatch
import json
import os
from typing import Iterable, List

from lib.terminal.dynamic_cmd import (
    CompletionContext, CompletionItem, cached, run_command, prefix_match,
)


# ============================================================
# 通用工具
# ============================================================

def _emit(items: Iterable[str], current: str, meta: str = "",
          style: str = "") -> Iterable[CompletionItem]:
    seen = set()
    for it in items:
        if not it or it in seen:
            continue
        if prefix_match(current, it):
            seen.add(it)
            yield CompletionItem(text=it, meta=meta, style=style)


def _subcommands(names: List[str], meta: str = "subcmd",
                 style: str = "ansiyellow"):
    """数据驱动的子命令补全器工厂。"""
    names = list(names)

    def _completer(ctx: CompletionContext):
        yield from _emit(names, ctx.current, meta, style)

    return _completer


def _static(spec: dict):
    """{subcmd: [args...], "_opts": [...], "_subs": [...], "_default": [...]}"""
    def _completer(ctx: CompletionContext):
        current = ctx.current
        opts = spec.get("_opts", [])
        if current.startswith('-'):
            yield from _emit(opts, current, "option", "ansired")
            return
        if not ctx.args:
            yield from _emit(spec.get("_subs", []), current,
                             "subcmd", "ansiyellow")
            return
        sub = ctx.args[0]
        table = spec.get(sub, spec.get("_default", []))
        yield from _emit(table, current, "arg", "ansimagenta")
        yield from _emit(opts, current, "option", "ansired")
    return _completer


def _cli_first(key: str, ttl: float, cmd: List[str], static: List[str],
               merge: bool = False) -> List[str]:
    """优先跑真实 CLI；拿不到时回退 static。merge=True 时两者合并。"""
    result = cached(key, ttl, lambda: run_command(cmd))
    if merge:
        combined = list(result)
        for x in static:
            if x not in combined:
                combined.append(x)
        return combined
    return result if result else list(static)


def _files_glob(patterns, max_depth: int = 3, skip_dirs=None) -> List[str]:
    """递归查找 cwd 下匹配 glob 的文件（返回相对路径）。"""
    if skip_dirs is None:
        skip_dirs = {'node_modules', 'venv', '.venv', '__pycache__',
                     'target', 'build', 'dist', '.git', 'vendor',
                     '.tox', '.mypy_cache', '.pytest_cache', '.cache',
                     '.idea', '.vscode', 'coverage', 'out'}
    result: List[str] = []
    try:
        cwd = os.getcwd()
    except Exception:
        return result
    for root, dirs, files in os.walk(cwd):
        depth = root[len(cwd):].count(os.sep)
        if depth >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs
                   if not d.startswith('.') and d not in skip_dirs]
        for f in files:
            for pat in patterns:
                if fnmatch.fnmatch(f, pat):
                    result.append(os.path.relpath(os.path.join(root, f), cwd))
                    break
    return sorted(set(result))


def _file_completions(ctx: CompletionContext) -> Iterable[CompletionItem]:
    """统一的工作目录文件 / 目录补全。"""
    try:
        base = os.path.dirname(ctx.current) or "."
        name = os.path.basename(ctx.current)
        if not os.path.isdir(base):
            return
        for f in sorted(os.listdir(base)):
            if not f.lower().startswith(name.lower()):
                continue
            if f.startswith('.') and not name.startswith('.'):
                continue
            full = os.path.join(base, f)
            is_dir = os.path.isdir(full)
            yield CompletionItem(
                text=f + (os.sep if is_dir else ""),
                meta="dir" if is_dir else "file",
                style="ansicyan" if is_dir else "ansiwhite",
                start_position=-len(name) if name else 0,
            )
    except Exception:
        return


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============================================================
# 1. git
# ============================================================

_GIT_SUBS = [
    "add", "am", "apply", "archive", "bisect", "blame", "branch",
    "bundle", "checkout", "cherry-pick", "clean", "clone", "commit",
    "config", "describe", "diff", "fetch", "format-patch", "gc",
    "grep", "init", "log", "merge", "mv", "notes", "pull", "push",
    "rebase", "reflog", "remote", "reset", "restore", "revert", "rm",
    "shortlog", "show", "stash", "status", "submodule", "switch",
    "tag", "worktree",
]

_GIT_BRANCH_SUBS = {
    "checkout", "switch", "merge", "rebase", "diff", "log", "reset",
    "cherry-pick", "show", "branch", "restore", "revert",
}
_GIT_REMOTE_SUBS = {"push", "pull", "fetch", "remote", "clone"}
_GIT_PATH_SUBS = {"add", "rm", "restore", "reset", "checkout", "diff",
                  "log", "blame", "show", "ls-files", "stash"}


def _find_git_dir() -> str:
    """从 cwd 向上查找 .git 目录（兼容 worktree / submodule 的 gitdir 文件）。"""
    try:
        path = os.getcwd()
    except Exception:
        return ""
    while True:
        cand = os.path.join(path, ".git")
        if os.path.isdir(cand):
            return cand
        if os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                    line = f.readline().strip()
            except OSError:
                return ""
            if line.startswith("gitdir:"):
                gd = line[len("gitdir:"):].strip()
                if not os.path.isabs(gd):
                    gd = os.path.normpath(os.path.join(path, gd))
                return gd if os.path.isdir(gd) else ""
            return ""
        parent = os.path.dirname(path)
        if parent == path:
            return ""
        path = parent


def _read_git_refs(kind: str) -> List[str]:
    """直接读 .git 里的引用名（kind = "heads" / "tags"），不 fork git 子进程。"""
    git_dir = _find_git_dir()
    if not git_dir:
        return []

    prefix = "refs/%s/" % kind
    names = set()

    base = os.path.join(git_dir, "refs", kind)
    for root, _dirs, files in os.walk(base):
        for fn in files:
            full = os.path.join(root, fn)
            names.add(os.path.relpath(full, base).replace(os.sep, "/"))

    try:
        with open(os.path.join(git_dir, "packed-refs"), "r",
                  encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line[0] in "#^":
                    continue
                parts = line.split(" ", 1)
                if len(parts) == 2 and parts[1].startswith(prefix):
                    names.add(parts[1][len(prefix):])
    except OSError:
        pass

    if kind == "heads":
        try:
            with open(os.path.join(git_dir, "HEAD"), "r",
                      encoding="utf-8", errors="ignore") as f:
                head = f.readline().strip()
            if head.startswith("ref: refs/heads/"):
                names.add(head[len("ref: refs/heads/"):])
        except OSError:
            pass

    return sorted(names)


def _read_git_remotes() -> List[str]:
    """直接解析 .git/config 的 [remote "xxx"] 段，不 fork git。"""
    git_dir = _find_git_dir()
    if not git_dir:
        return []
    names: List[str] = []
    try:
        with open(os.path.join(git_dir, "config"), "r",
                  encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("[remote "):
                    continue
                name = line[len("[remote "):].rstrip("]").strip().strip('"')
                if name and name not in names:
                    names.append(name)
    except OSError:
        return []
    return names


def _git_cli(args: List[str]) -> List[str]:
    """git 命令兜底：带 -c safe.directory=*，避免共享存储上的 ownership 误报。"""
    return run_command(["git", "-c", "safe.directory=*"] + args)


def _git_cached(name: str, ttl: float, direct, cli_args: List[str]) -> List[str]:
    """直读 .git 优先，拿不到再退回 git 命令；缓存 key 带上 cwd（cd 后不串味）。"""
    try:
        cwd = os.getcwd()
    except Exception:
        cwd = ""
    return cached("%s@%s" % (name, cwd), ttl,
                  lambda: direct() or _git_cli(cli_args))


def _git_branches() -> List[str]:
    return _git_cached("git_branches", 3.0,
                       lambda: _read_git_refs("heads"),
                       ["branch", "--format=%(refname:short)"])


def _git_tags() -> List[str]:
    return _git_cached("git_tags", 5.0,
                       lambda: _read_git_refs("tags"),
                       ["tag"])


def _git_remotes() -> List[str]:
    return _git_cached("git_remotes", 5.0,
                       _read_git_remotes,
                       ["remote"])


def _git_tracked() -> List[str]:
    return _git_cached("git_tracked", 15.0,
                       lambda: [],
                       ["ls-files"])


def git_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_GIT_SUBS, ctx.current, "subcmd", "ansiyellow")
        return

    sub = ctx.args[0]

    if sub == "stash":
        yield from _emit(
            ["list", "show", "pop", "apply", "drop", "clear", "push", "save"],
            ctx.current, "action", "ansiyellow")
        return

    if sub == "remote":
        if len(ctx.args) == 1:
            yield from _emit(
                ["add", "remove", "rename", "set-url", "show", "prune",
                 "get-url", "set-head", "update"],
                ctx.current, "action", "ansiyellow")
            return
        yield from _emit(_git_remotes(), ctx.current, "remote", "ansicyan")
        return

    if sub == "config":
        yield from _emit(
            ["--global", "--local", "--system", "--list",
             "--get", "--set", "--unset", "--edit", "--add"],
            ctx.current, "option", "ansired")
        return

    if sub == "branch" and len(ctx.args) == 1:
        yield from _emit(_git_branches(), ctx.current, "branch", "ansicyan")
        yield from _emit(["-a", "-r", "-d", "-D", "-m", "-M", "--list",
                          "--all", "--remotes"],
                         ctx.current, "option", "ansired")
        return

    if sub in _GIT_BRANCH_SUBS:
        for b in _git_branches():
            if prefix_match(ctx.current, b):
                yield CompletionItem(text=b, meta="branch", style="ansicyan")
        if sub in ("checkout", "switch", "tag", "merge", "rebase"):
            for t in _git_tags():
                if prefix_match(ctx.current, t):
                    yield CompletionItem(text=t, meta="tag", style="ansimagenta")
        return

    if sub in _GIT_REMOTE_SUBS:
        if len(ctx.args) == 1:
            yield from _emit(_git_remotes(), ctx.current, "remote", "ansicyan")
        elif sub in ("push", "pull") and len(ctx.args) >= 2:
            for b in _git_branches():
                if prefix_match(ctx.current, b):
                    yield CompletionItem(text=b, meta="branch", style="ansicyan")
        return

    if sub in _GIT_PATH_SUBS:
        for f in _git_tracked():
            if prefix_match(ctx.current, f):
                yield CompletionItem(text=f, meta="tracked", style="ansicyan")
        yield from _file_completions(ctx)
        return

    yield from _emit(
        ["--help", "--verbose", "--quiet", "-v", "-q"],
        ctx.current, "option", "ansired")


# ============================================================
# 2. docker
# ============================================================

_DOCKER_SUBS = [
    "attach", "build", "commit", "compose", "container", "context",
    "cp", "create", "diff", "events", "exec", "export", "history",
    "image", "images", "import", "info", "inspect", "kill", "load",
    "login", "logout", "logs", "network", "pause", "plugin", "port",
    "ps", "pull", "push", "rename", "restart", "rm", "rmi", "run",
    "save", "search", "start", "stats", "stop", "swarm", "system",
    "tag", "top", "unpause", "update", "version", "volume", "wait",
]

_DOCKER_CONTAINER_SUBS = {
    "exec", "stop", "start", "kill", "logs", "inspect",
    "restart", "rm", "attach", "top", "pause", "unpause", "port",
    "rename", "stats", "wait", "diff", "export", "commit", "cp",
    "update",
}
_DOCKER_IMAGE_SUBS = {
    "rmi", "run", "pull", "push", "tag", "history", "save", "inspect",
}


def _docker_containers(running_only: bool = True) -> List[str]:
    key = "docker_ps" if running_only else "docker_ps_all"
    cmd = ["docker", "ps", "--format", "{{.Names}}"]
    if not running_only:
        cmd.insert(2, "-a")
    return cached(key, 3.0, lambda: run_command(cmd))


def _docker_images() -> List[str]:
    return cached("docker_images", 5.0, lambda: run_command(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"]))


def _docker_networks() -> List[str]:
    return cached("docker_networks", 10.0, lambda: run_command(
        ["docker", "network", "ls", "--format", "{{.Name}}"]))


def _docker_volumes() -> List[str]:
    return cached("docker_volumes", 10.0, lambda: run_command(
        ["docker", "volume", "ls", "--format", "{{.Name}}"]))


def _docker_contexts() -> List[str]:
    return cached("docker_contexts", 30.0, lambda: run_command(
        ["docker", "context", "ls", "--format", "{{.Name}}"]))


def _docker_compose_services() -> List[str]:
    for fname in ("docker-compose.yml", "docker-compose.yaml",
                  "compose.yml", "compose.yaml"):
        if os.path.exists(fname):
            return cached("docker_compose_svc", 10.0, lambda: run_command(
                ["docker", "compose", "config", "--services"]))
    return []


def docker_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_DOCKER_SUBS, ctx.current, "subcmd", "ansiyellow")
        return

    sub = ctx.args[0]

    if sub == "network":
        if len(ctx.args) == 1:
            yield from _emit(
                ["create", "ls", "rm", "connect", "disconnect",
                 "inspect", "prune"],
                ctx.current, "action", "ansiyellow")
            return
        if ctx.args[1] in ("rm", "inspect", "connect", "disconnect"):
            yield from _emit(_docker_networks(), ctx.current,
                             "network", "ansicyan")
        return

    if sub == "volume":
        if len(ctx.args) == 1:
            yield from _emit(
                ["create", "ls", "rm", "inspect", "prune"],
                ctx.current, "action", "ansiyellow")
            return
        if ctx.args[1] in ("rm", "inspect"):
            yield from _emit(_docker_volumes(), ctx.current,
                             "volume", "ansicyan")
        return

    if sub == "context":
        if len(ctx.args) == 1:
            yield from _emit(
                ["create", "ls", "rm", "inspect", "use", "show", "update"],
                ctx.current, "action", "ansiyellow")
            return
        if ctx.args[1] in ("rm", "inspect", "use", "update"):
            yield from _emit(_docker_contexts(), ctx.current,
                             "context", "ansicyan")
        return

    if sub == "compose":
        if len(ctx.args) == 1:
            yield from _emit(
                ["up", "down", "start", "stop", "restart", "logs",
                 "ps", "build", "pull", "push", "exec", "run",
                 "config", "rm", "kill", "top"],
                ctx.current, "action", "ansiyellow")
            return
        if ctx.args[1] in ("up", "down", "start", "stop", "restart",
                           "logs", "ps", "build", "exec", "run", "kill"):
            yield from _emit(_docker_compose_services(), ctx.current,
                             "service", "ansimagenta")
        return

    if sub in _DOCKER_CONTAINER_SUBS:
        yield from _emit(_docker_containers(True), ctx.current,
                         "container", "ansicyan")
        yield from _emit(_docker_containers(False), ctx.current,
                         "container", "ansiblue")
        return

    if sub in _DOCKER_IMAGE_SUBS:
        yield from _emit(_docker_images(), ctx.current,
                         "image", "ansimagenta")
        return

    yield from _emit(
        ["--help", "-h", "--format", "--filter"],
        ctx.current, "option", "ansired")


# ============================================================
# 3. kubectl
# ============================================================

_KUBECTL_SUBS = [
    "apply", "attach", "auth", "autoscale", "certificate", "cluster-info",
    "completion", "config", "cordon", "cp", "create", "delete", "describe",
    "diff", "drain", "edit", "exec", "explain", "expose", "get", "label",
    "logs", "patch", "plugin", "port-forward", "proxy", "replace", "rollout",
    "run", "scale", "set", "taint", "top", "uncordon", "version",
    "api-resources", "api-versions",
]

_KUBECTL_RESOURCES = [
    "pods", "deployments", "services", "replicasets", "statefulsets",
    "daemonsets", "jobs", "cronjobs", "configmaps", "secrets",
    "namespaces", "nodes", "events", "ingresses", "persistentvolumes",
    "persistentvolumeclaims", "serviceaccounts", "endpoints",
    "networkpolicies", "roles", "rolebindings", "clusterroles",
    "clusterrolebindings", "storageclasses", "horizontalpodautoscalers",
]

_KUBECTL_GET_VERBS = ["get", "describe", "delete", "edit", "logs",
                      "exec", "scale", "patch", "label", "annotate"]


def _kubectl_namespaces() -> List[str]:
    return cached("kube_ns", 5.0, lambda: [
        line.strip()
        for line in run_command(["kubectl", "get", "ns", "--no-headers",
                                 "-o", "custom-columns=:metadata.name"])
        if line.strip()
    ])


def _kubectl_contexts() -> List[str]:
    return cached("kube_ctx", 10.0, lambda: [
        line.strip()
        for line in run_command(["kubectl", "config", "get-contexts",
                                 "-o", "name"])
        if line.strip()
    ])


def _kubectl_objects(res: str) -> List[str]:
    return cached(f"kube_obj_{res}", 3.0, lambda: [
        line.strip()
        for line in run_command(["kubectl", "get", res, "--no-headers",
                                 "-o", "custom-columns=:metadata.name"])
        if line.strip()
    ])


def kubectl_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_KUBECTL_SUBS, ctx.current, "subcmd", "ansiyellow")
        return

    sub = ctx.args[0]

    if sub == "config":
        if len(ctx.args) == 1:
            yield from _emit(
                ["current-context", "get-contexts", "use-context",
                 "view", "set", "unset", "rename-context",
                 "delete-context", "set-context", "set-cluster",
                 "set-credentials"],
                ctx.current, "action", "ansiyellow")
            return
        if ctx.args[1] in ("use-context", "delete-context", "rename-context"):
            yield from _emit(_kubectl_contexts(), ctx.current,
                             "context", "ansimagenta")
        return

    if sub == "rollout":
        yield from _emit(
            ["status", "history", "undo", "restart", "pause", "resume"],
            ctx.current, "action", "ansiyellow")
        return

    if sub in _KUBECTL_GET_VERBS and len(ctx.args) == 1:
        yield from _emit(_KUBECTL_RESOURCES, ctx.current,
                         "resource", "ansimagenta")
        return

    if sub in _KUBECTL_GET_VERBS and len(ctx.args) >= 2:
        res = ctx.args[1]
        if res in _KUBECTL_RESOURCES:
            yield from _emit(_kubectl_objects(res), ctx.current,
                             "object", "ansicyan")
        return

    yield from _emit(
        ["-n", "--namespace", "-A", "--all-namespaces",
         "-o", "--output", "-f", "--filename", "--context",
         "-l", "--selector", "--field-selector"],
        ctx.current, "option", "ansired")


# ============================================================
# 4. npm
# ============================================================

_NPM_SUBS = [
    "install", "i", "uninstall", "update", "run", "test", "start",
    "build", "publish", "pack", "link", "init", "list", "ls", "outdated",
    "audit", "fund", "config", "cache", "ci", "dedupe", "doctor",
    "explain", "exec", "help", "login", "logout", "ping", "prefix",
    "prune", "repo", "restart", "root", "search", "stop", "team",
    "token", "unpublish", "version", "view", "whoami", "npx",
]


def _npm_scripts() -> List[str]:
    def _read():
        data = _read_json(os.path.join(os.getcwd(), "package.json"))
        if not data:
            return []
        return list((data.get("scripts") or {}).keys())
    return cached("npm_scripts", 5.0, _read)


def _npm_deps(dev: bool = False) -> List[str]:
    def _read():
        data = _read_json(os.path.join(os.getcwd(), "package.json"))
        if not data:
            return []
        key = "devDependencies" if dev else "dependencies"
        return sorted(set((data.get(key) or {}).keys()))
    return cached("npm_deps_dev" if dev else "npm_deps", 10.0, _read)


def npm_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_NPM_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("run", "run-script"):
        yield from _emit(_npm_scripts(), ctx.current, "script", "ansimagenta")
        return
    if sub in ("install", "i", "add", "uninstall", "remove", "update"):
        deps = _npm_deps() + _npm_deps(True)
        yield from _emit(deps, ctx.current, "dep", "ansimagenta")
        yield from _emit(
            ["--save", "--save-dev", "--save-peer",
             "--save-optional", "--no-save", "-g", "--global",
             "--registry", "--prefix"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(["--help", "-h"], ctx.current, "option", "ansired")


# ============================================================
# 5. pip
# ============================================================

_PIP_SUBS = [
    "install", "uninstall", "freeze", "list", "show", "search",
    "download", "wheel", "hash", "completion", "help", "check",
    "config", "cache", "debug", "index", "inspect",
]


def _pip_installed() -> List[str]:
    return cached("pip_installed", 30.0, lambda: [
        line.split("==")[0]
        for line in run_command(["pip", "freeze"])
        if "==" in line
    ])


def pip_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_PIP_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("show", "uninstall", "install", "download", "wheel"):
        yield from _emit(_pip_installed(), ctx.current,
                         "pkg", "ansimagenta")
    if sub in ("install", "download", "wheel"):
        yield from _emit(
            ["-r", "--requirement", "-U", "--upgrade",
             "--user", "--pre", "--no-deps",
             "-i", "--index-url", "--extra-index-url",
             "--no-cache-dir", "-t", "--target"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(["--help", "-h"], ctx.current, "option", "ansired")


# ============================================================
# 6. cargo
# ============================================================

_CARGO_SUBS = [
    "build", "check", "clean", "doc", "new", "init", "add", "remove",
    "run", "test", "bench", "update", "search", "publish", "install",
    "uninstall", "login", "logout", "package", "fetch", "fix", "fmt",
    "clippy", "metadata", "tree", "vendor", "verify-project", "version",
    "yank", "generate-lockfile", "locate-project", "rustc", "rustdoc",
]


def cargo_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_CARGO_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    yield from _emit(
        ["--release", "--debug", "--target", "--features",
         "--all-features", "--no-default-features", "-p", "--package",
         "--manifest-path", "--verbose", "-v", "--quiet", "-q"],
        ctx.current, "option", "ansired")


# ============================================================
# 7. go
# ============================================================

_GO_SUBS = [
    "build", "clean", "doc", "env", "bug", "fix", "fmt", "generate",
    "get", "install", "list", "mod", "work", "run", "test", "tool",
    "version", "vet",
]


def _go_packages() -> List[str]:
    return cached("go_pkgs", 10.0, lambda: run_command(["go", "list", "./..."]))


def go_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_GO_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("run", "test", "build", "install", "vet"):
        yield from _emit(_go_packages(), ctx.current, "pkg", "ansimagenta")
        yield from _emit(["-v", "-race", "-tags", "-o"], ctx.current,
                         "option", "ansired")
        return
    if sub == "mod":
        yield from _emit(
            ["init", "tidy", "download", "edit", "graph", "verify", "why"],
            ctx.current, "action", "ansiyellow")
        return
    if sub == "env":
        yield from _emit(
            ["GOPATH", "GOROOT", "GOBIN", "GOOS", "GOARCH", "GOPROXY"],
            ctx.current, "var", "ansicyan")
        return
    yield from _emit(["-h", "--help"], ctx.current, "option", "ansired")


# ============================================================
# 8. make
# ============================================================

def _make_targets() -> List[str]:
    def _read():
        targets = []
        for fname in ("Makefile", "makefile", "GNUmakefile"):
            path = os.path.join(os.getcwd(), fname)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if line.startswith(("\t", "#")) or "=" in line:
                            continue
                        m = line.split(":", 1)
                        if len(m) == 2 and m[0].strip():
                            targets.append(m[0].strip())
            except Exception:
                pass
            break
        return list(dict.fromkeys(targets))
    return cached("make_targets", 3.0, _read)


def make_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-f", "-C", "-j", "-n", "-k", "-s", "-B", "--always-make",
             "--dry-run", "--jobs", "--directory"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(_make_targets(), ctx.current, "target", "ansimagenta")


# ============================================================
# 9. systemctl
# ============================================================

_SYSTEMCTL_SUBS = [
    "start", "stop", "restart", "reload", "status", "enable", "disable",
    "is-active", "is-enabled", "daemon-reload", "list-units",
    "list-unit-files", "list-sockets", "list-timers", "show", "cat",
    "edit", "mask", "unmask", "reset-failed", "kill", "isolate",
    "poweroff", "reboot", "suspend", "hibernate",
]


def _systemctl_units() -> List[str]:
    return cached("systemctl_units", 10.0, lambda: run_command(
        ["systemctl", "list-units", "--no-legend",
         "--no-pager", "--plain"]))


def systemctl_completer(ctx: CompletionContext):
    if not ctx.args or ctx.args[0].startswith('-'):
        yield from _emit(_SYSTEMCTL_SUBS, ctx.current, "subcmd", "ansiyellow")
        if ctx.current.startswith('-'):
            yield from _emit(
                ["--user", "--system", "--type", "--all", "--state",
                 "--no-pager", "--no-legend", "--plain"],
                ctx.current, "option", "ansired")
        return
    sub = ctx.args[0]
    if sub in ("start", "stop", "restart", "reload", "status", "enable",
               "disable", "is-active", "is-enabled", "cat", "show", "edit",
               "mask", "unmask", "kill"):
        units = _systemctl_units()
        unit_names = [u.split()[0] for u in units if u.split()]
        yield from _emit(unit_names, ctx.current, "unit", "ansimagenta")
        return


# ============================================================
# 10. ssh
# ============================================================

def _ssh_hosts() -> List[str]:
    def _read():
        hosts = []
        for path in (
            os.path.expanduser("~/.ssh/config"),
            os.path.expanduser("~/.ssh/known_hosts"),
        ):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        m = line.split()
                        if not m:
                            continue
                        if line.lower().startswith("host "):
                            if len(m) >= 2:
                                hosts.append(m[1])
                        else:
                            h = m[0].split(",")[0]
                            if "|" not in h:
                                hosts.append(h)
            except Exception:
                pass
        return list(dict.fromkeys(hosts))
    return cached("ssh_hosts", 30.0, _read)


def ssh_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-p", "-i", "-L", "-R", "-D", "-o", "-N", "-f", "-v",
             "-X", "-Y", "-J", "-F"],
            ctx.current, "option", "ansired")
        return
    if len(ctx.args) == 0:
        yield from _emit(_ssh_hosts(), ctx.current, "host", "ansicyan")


# ============================================================
# 11. tmux
# ============================================================

_TMUX_SUBS = [
    "attach", "attach-session", "bind", "break-pane", "capture-pane",
    "choose-tree", "clear-history", "copy-mode", "detach", "display",
    "display-message", "has-session", "kill-pane", "kill-server",
    "kill-session", "kill-window", "link-window", "list-buffers",
    "list-clients", "list-commands", "list-keys", "list-panes",
    "list-sessions", "list-windows", "load-buffer", "lock-client",
    "lock-server", "lock-session", "move-pane", "move-window", "new",
    "new-session", "new-window", "next-layout", "next-window",
    "paste-buffer", "pipe-pane", "previous-layout", "previous-window",
    "rename-session", "rename-window", "resize-pane", "respawn-pane",
    "respawn-window", "rotate-window", "save-buffer", "select-layout",
    "select-pane", "select-window", "send-keys", "set", "set-buffer",
    "set-environment", "set-option", "set-window-option", "show",
    "show-environment", "show-messages", "show-options",
    "show-window-options", "source-file", "split-window",
    "start-server", "swap-pane", "swap-window", "switch-client",
    "unbind", "unlink-window", "wait-for",
]


def _tmux_sessions() -> List[str]:
    return cached("tmux_sessions", 5.0, lambda: [
        line.split(":")[0]
        for line in run_command(["tmux", "list-sessions", "-F",
                                 "#{session_name}"])
        if line.strip()
    ])


def tmux_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_TMUX_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("attach", "attach-session", "switch-client", "has-session",
               "kill-session", "rename-session", "list-windows",
               "list-panes"):
        yield from _emit(_tmux_sessions(), ctx.current,
                         "session", "ansimagenta")
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-t", "-s", "-n", "-d", "-x", "-y", "-v", "-h", "-p", "-l"],
            ctx.current, "option", "ansired")


# ============================================================
# 12. aws
# ============================================================

_AWS_TOP = [
    "s3", "ec2", "iam", "lambda", "cloudformation", "rds", "sns", "sqs",
    "dynamodb", "ecs", "eks", "kms", "logs", "sts", "route53", "s3api",
    "s3control", "secretsmanager", "ssm", "apigateway", "configure",
    "help",
]

_AWS_REGIONS = [
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1", "eu-north-1",
    "ap-south-1", "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
    "ap-northeast-2", "ap-northeast-3", "sa-east-1", "ca-central-1",
    "me-south-1", "af-south-1",
]


def _aws_profiles() -> List[str]:
    def _read():
        try:
            with open(os.path.expanduser("~/.aws/config"), "r",
                      encoding="utf-8", errors="ignore") as f:
                names = []
                for line in f:
                    line = line.strip()
                    if line.startswith("[") and line.endswith("]"):
                        name = line[1:-1].strip()
                        if name.startswith("profile "):
                            name = name[len("profile "):]
                        if name and name != "default":
                            names.append(name)
                return names
        except Exception:
            return []
    return cached("aws_profiles", 30.0,
                  lambda: _read() or run_command(
                      ["aws", "configure", "list-profiles"]))


def aws_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_AWS_TOP, ctx.current, "service", "ansiyellow")
        return
    if ctx.current.startswith('--'):
        yield from _emit(
            ["--region", "--profile", "--output", "--query",
             "--no-cli-pager", "--endpoint-url", "--no-verify-ssl"],
            ctx.current, "option", "ansired")
        return
    # --region / --profile 后面给真实候选
    prev = ctx.args[-1] if ctx.args else ""
    if prev == "--region":
        yield from _emit(_AWS_REGIONS, ctx.current, "region", "ansimagenta")
    elif prev == "--profile":
        yield from _emit(_aws_profiles(), ctx.current, "profile", "ansimagenta")
    elif prev == "--output":
        yield from _emit(["json", "yaml", "text", "table"],
                         ctx.current, "format", "ansimagenta")


# ============================================================
# 13. terraform
# ============================================================

_TF_SUBS = [
    "apply", "console", "destroy", "env", "fmt", "force-unlock", "get",
    "graph", "import", "init", "login", "logout", "output", "plan",
    "providers", "refresh", "show", "state", "taint", "untaint",
    "validate", "version", "workspace", "test", "metadata",
]


def _tf_workspaces() -> List[str]:
    return cached("tf_ws", 5.0, lambda: [
        line.replace("*", "").strip()
        for line in run_command(["terraform", "workspace", "list"])
    ])


def terraform_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_TF_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub == "workspace":
        if len(ctx.args) == 1:
            yield from _emit(
                ["list", "select", "new", "delete", "show"],
                ctx.current, "action", "ansiyellow")
        else:
            yield from _emit(_tf_workspaces(), ctx.current, "ws", "ansimagenta")
        return
    if sub == "state":
        yield from _emit(
            ["list", "mv", "pull", "push", "rm", "show", "replace-provider"],
            ctx.current, "action", "ansiyellow")
        return
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-auto-approve", "-var", "-var-file", "-target",
             "-out", "-input=false", "-lock=true", "-upgrade",
             "-reconfigure", "-backend-config"],
            ctx.current, "option", "ansired")


# ============================================================
# 14. ansible
# ============================================================

_ANSIBLE_SUBS = [
    "playbook", "galaxy", "vault", "doc", "config", "inventory",
    "console", "pull", "lint",
]


def _ansible_playbooks() -> List[str]:
    return cached("ansible_pb", 5.0,
                  lambda: _files_glob(["*.yml", "*.yaml"], max_depth=2))


def ansible_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_ANSIBLE_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] == "playbook":
        if len(ctx.args) == 1:
            yield from _emit(_ansible_playbooks(), ctx.current,
                             "playbook", "ansimagenta")
            yield from _file_completions(ctx)
            return
        yield from _emit(
            ["-i", "--inventory", "-l", "--limit",
             "-u", "--user", "--ask-pass", "--ask-become-pass",
             "-e", "--extra-vars", "-v", "--verbose", "--check",
             "--syntax-check", "--list-tasks", "--tags"],
            ctx.current, "option", "ansired")
        return


# ============================================================
# 15. helm
# ============================================================

_HELM_SUBS = [
    "create", "dependency", "env", "get", "history", "install", "lint",
    "list", "package", "plugin", "pull", "push", "repo", "rollback",
    "search", "show", "status", "template", "test", "uninstall",
    "upgrade", "verify", "version",
]


def _helm_releases() -> List[str]:
    return cached("helm_releases", 5.0, lambda: [
        line.split()[0] for line in run_command(["helm", "list", "-q"])
    ])


def _helm_repos() -> List[str]:
    return cached("helm_repos", 10.0,
                  lambda: run_command(["helm", "repo", "list", "-o", "name"]))


def helm_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_HELM_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("uninstall", "upgrade", "rollback", "status", "history",
               "get", "test"):
        yield from _emit(_helm_releases(), ctx.current,
                         "release", "ansimagenta")
        return
    if sub == "repo":
        if len(ctx.args) == 1:
            yield from _emit(
                ["add", "list", "remove", "update", "index"],
                ctx.current, "action", "ansiyellow")
        elif ctx.args[1] in ("remove", "update", "index"):
            yield from _emit(_helm_repos(), ctx.current,
                             "repo", "ansicyan")
        return


# ============================================================
# 16. composer
# ============================================================

_COMPOSER_SUBS = [
    "install", "update", "require", "remove", "create-project",
    "dump-autoload", "dump-env", "config", "diagnose", "doctor",
    "init", "run-script", "show", "self-update", "status", "validate",
    "outdated", "licenses", "archive", "browse", "clear-cache",
    "depends", "prohibits", "why", "why-not", "exec", "global",
]


def _composer_scripts() -> List[str]:
    def _read():
        data = _read_json(os.path.join(os.getcwd(), "composer.json"))
        if not data:
            return []
        return list((data.get("scripts") or {}).keys())
    return cached("composer_scripts", 5.0, _read)


def _composer_deps() -> List[str]:
    def _read():
        data = _read_json(os.path.join(os.getcwd(), "composer.json"))
        if not data:
            return []
        return sorted(set(
            list((data.get("require") or {}).keys()) +
            list((data.get("require-dev") or {}).keys())))
    return cached("composer_deps", 10.0, _read)


def composer_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_COMPOSER_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("run", "run-script"):
        yield from _emit(_composer_scripts(), ctx.current,
                         "script", "ansimagenta")
        return
    if sub in ("update", "remove"):
        yield from _emit(_composer_deps(), ctx.current,
                         "dep", "ansimagenta")


# ============================================================
# 17. mvn
# ============================================================

_MVN_PHASES = [
    "validate", "compile", "test", "package", "verify", "install",
    "deploy", "clean", "site", "initialize", "generate-sources",
    "process-sources", "generate-resources", "process-resources",
    "process-classes", "generate-test-sources", "process-test-sources",
    "generate-test-resources", "process-test-resources",
    "test-compile", "process-test-classes", "prepare-package",
    "pre-integration-test", "integration-test", "post-integration-test",
    "pre-site", "post-site", "site-deploy",
]


def mvn_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-D", "-P", "-f", "-o", "-U", "-X", "-e", "-q", "-B",
             "-s", "-T", "--batch-mode", "--offline"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(_MVN_PHASES, ctx.current, "phase", "ansimagenta")


# ============================================================
# 18. gradle
# ============================================================

_GRADLE_SUBS = [
    "build", "clean", "test", "assemble", "check", "publish", "run",
    "bootRun", "jar", "war", "install", "dependencies", "projects",
    "tasks", "properties", "init", "wrapper", "help",
]


def gradle_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_GRADLE_SUBS, ctx.current, "task", "ansiyellow")
        return
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-P", "-D", "-q", "-i", "-d", "-s", "--info",
             "--debug", "--stacktrace", "--offline",
             "--refresh-dependencies"],
            ctx.current, "option", "ansired")


# ============================================================
# 19. dotnet
# ============================================================

_DOTNET_SUBS = [
    "build", "clean", "pack", "publish", "restore", "run", "test",
    "new", "add", "remove", "list", "nuget", "tool", "workload",
    "sln", "vstest", "format", "watch", "help",
]


def dotnet_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_DOTNET_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub == "new":
        if len(ctx.args) == 1:
            yield from _emit(
                ["console", "classlib", "web", "webapi", "mvc", "blazor",
                 "worker", "xunit", "nunit", "mstest", "sln", "gitignore",
                 "globaljson", "editorconfig"],
                ctx.current, "template", "ansimagenta")
        else:
            yield from _emit(
                ["-n", "--name", "-o", "--output", "-f", "--framework",
                 "--language", "--no-restore"],
                ctx.current, "option", "ansired")
        return
    if sub in ("add", "remove"):
        yield from _emit(
            ["package", "reference", "project"],
            ctx.current, "action", "ansiyellow")


# ============================================================
# 20. conda
# ============================================================

_CONDA_SUBS = [
    "activate", "deactivate", "create", "install", "remove", "update",
    "upgrade", "list", "search", "info", "config", "clean", "env",
    "init", "package", "run", "doctor", "build", "develop",
]


def _conda_envs() -> List[str]:
    return cached("conda_envs", 10.0, lambda: [
        line.split()[-1]
        for line in run_command(["conda", "env", "list"])
        if line.strip() and not line.startswith("#")
    ])


def conda_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_CONDA_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("activate", "deactivate", "env", "remove", "update"):
        yield from _emit(_conda_envs(), ctx.current, "env", "ansimagenta")
        return
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-n", "--name", "-c", "--channel", "-y", "--yes",
             "-q", "--quiet", "--dry-run"],
            ctx.current, "option", "ansired")


# ============================================================
# 21. poetry
# ============================================================

_POETRY_SUBS = [
    "add", "build", "check", "config", "export", "init", "install",
    "lock", "new", "publish", "remove", "run", "shell", "show",
    "update", "version", "env", "cache", "debug", "source", "about",
]


def _poetry_scripts() -> List[str]:
    def _read():
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore
            except ImportError:
                return []
        try:
            with open(os.path.join(os.getcwd(), "pyproject.toml"), "rb") as f:
                data = tomllib.load(f)
            scripts = (data.get("tool") or {}).get("poetry", {}).get("scripts", {})
            return list(scripts.keys())
        except Exception:
            return []
    return cached("poetry_scripts", 5.0, _read)


def poetry_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_POETRY_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] == "run":
        yield from _emit(_poetry_scripts(), ctx.current,
                         "script", "ansimagenta")


# ============================================================
# 22. yarn
# ============================================================

_YARN_SUBS = [
    "add", "audit", "autoclean", "bin", "cache", "check", "config",
    "create", "dedupe", "generate-lock-entry", "global", "help",
    "import", "info", "init", "install", "licenses", "link", "list",
    "login", "logout", "node", "outdated", "owner", "pack", "plugin",
    "policies", "prune", "publish", "remove", "run", "set", "tag",
    "team", "test", "unlink", "unplug", "upgrade", "upgrade-interactive",
    "version", "versions", "why", "workspace", "workspaces",
]


def yarn_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_YARN_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] in ("run", "test"):
        yield from _emit(_npm_scripts(), ctx.current, "script", "ansimagenta")


# ============================================================
# 23. pnpm
# ============================================================

_PNPM_SUBS = [
    "add", "audit", "bin", "config", "create", "dedupe", "deploy",
    "doctor", "env", "fetch", "import", "init", "install", "licenses",
    "link", "list", "outdated", "patch", "patch-commit",
    "prune", "publish", "rebuild", "remove", "root", "run", "server",
    "setup", "start", "store", "test", "unlink", "update", "why",
]


def pnpm_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_PNPM_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] in ("run", "start", "test"):
        yield from _emit(_npm_scripts(), ctx.current, "script", "ansimagenta")


# ============================================================
# 24. pytest
# ============================================================

def _pytest_files() -> List[str]:
    return cached("pytest_files", 5.0,
                  lambda: _files_glob(["test_*.py", "*_test.py"], max_depth=4))


def pytest_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-v", "-q", "-s", "-x", "-k", "-m", "--cov",
             "--tb", "--maxfail", "--disable-warnings",
             "-p", "--collect-only", "-n", "--asyncio-mode"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(_pytest_files(), ctx.current, "test", "ansimagenta")


# ============================================================
# 25. rake
# ============================================================

def _rake_tasks() -> List[str]:
    return cached("rake_tasks", 10.0, lambda: run_command(["rake", "-T"]))


def rake_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-T", "-P", "-D", "-f", "-N", "-v", "-q", "-t"],
            ctx.current, "option", "ansired")
        return
    tasks = [line.split()[1] for line in _rake_tasks()
             if len(line.split()) >= 2]
    yield from _emit(tasks, ctx.current, "task", "ansimagenta")


# ============================================================
# 26. bundle
# ============================================================

_BUNDLE_SUBS = [
    "install", "update", "exec", "add", "remove", "binstubs", "check",
    "clean", "config", "doctor", "env", "gem", "info", "init",
    "inject", "list", "lock", "open", "outdated", "platform",
    "plugin", "pristine", "show", "viz", "cache", "console",
]


def _bundle_execs() -> List[str]:
    def _scan():
        try:
            from os import environ
            path = environ.get("PATH", "")
            cmds = set()
            for d in path.split(os.pathsep):
                if not d or not os.path.isdir(d):
                    continue
                try:
                    for f in os.listdir(d):
                        cmds.add(f)
                except OSError:
                    continue
            return sorted(cmds)
        except Exception:
            return []
    return cached("bundle_execs", 30.0, _scan)


def bundle_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_BUNDLE_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] == "exec":
        yield from _emit(_bundle_execs(), ctx.current, "exec", "ansimagenta")


# ============================================================
# 27. jupyter
# ============================================================

_JUPYTER_SUBS = [
    "notebook", "lab", "console", "qtconsole", "nbconvert", "nbformat",
    "kernelspec", "kernel", "trust", "run", "execute", "convert",
    "migrate", "troubleshoot", "debug", "contrib", "bundlerextension",
    "serverextension", "nbextension", "nbclassic",
]


def _jupyter_kernels() -> List[str]:
    return cached("jupyter_kernels", 30.0, lambda: [
        line.split()[0]
        for line in run_command(["jupyter", "kernelspec", "list"])
        if line.strip() and not line.startswith("Available")
        and not line.startswith(" ")
    ])


def jupyter_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_JUPYTER_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("kernelspec", "kernel"):
        if len(ctx.args) == 1:
            yield from _emit(
                ["list", "install", "uninstall"],
                ctx.current, "action", "ansiyellow")
        else:
            yield from _emit(_jupyter_kernels(), ctx.current,
                             "kernel", "ansimagenta")


# ============================================================
# 28. hugo
# ============================================================

_HUGO_SUBS = [
    "new", "build", "server", "config", "convert", "deploy",
    "env", "gen", "import", "list", "mod", "version",
]


def hugo_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_HUGO_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] == "new":
        if len(ctx.args) == 1:
            yield from _emit(["site", "theme", "content"],
                             ctx.current, "action", "ansiyellow")
        return


# ============================================================
# 29. ffmpeg
# ============================================================

_FFMPEG_COMMON = [
    "-i", "-c", "-c:v", "-c:a", "-b:v", "-b:a", "-r", "-s",
    "-ss", "-t", "-to", "-f", "-vn", "-an", "-y", "-n",
    "-filter_complex", "-vf", "-af", "-crf", "-preset",
    "-pix_fmt", "-map", "-threads", "-loglevel", "-hide_banner",
]


def ffmpeg_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_FFMPEG_COMMON, ctx.current, "option", "ansired")
        return
    yield from _file_completions(ctx)


# ============================================================
# 30. gcloud
# ============================================================

_GCLOUD_TOP = [
    "auth", "compute", "container", "config", "projects", "iam",
    "logging", "storage", "pubsub", "sql", "functions", "app",
    "builds", "deployment-manager", "deploy", "kms", "organizations",
    "services", "source", "spanner", "endpoints", "info", "version",
]


def _gcloud_projects() -> List[str]:
    return cached("gcloud_projects", 60.0, lambda: [
        line.split()[0]
        for line in run_command(["gcloud", "projects", "list",
                                 "--format=value(projectId)"])
        if line.strip()
    ])


def gcloud_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_GCLOUD_TOP, ctx.current, "group", "ansiyellow")
        return
    if ctx.current.startswith('--'):
        yield from _emit(
            ["--project", "--account", "--configuration", "--format",
             "--filter", "--quiet", "--verbosity"],
            ctx.current, "option", "ansired")
        return
    if ctx.args and ctx.args[-1] == "--project":
        yield from _emit(_gcloud_projects(), ctx.current,
                         "project", "ansimagenta")


# ============================================================
# 31. gh (GitHub CLI)
# ============================================================

_GH_SUBS = [
    "alias", "api", "auth", "browse", "codespace", "completion",
    "config", "extension", "gist", "gpg-key", "issue", "label",
    "pr", "project", "release", "repo", "run", "search", "secret",
    "ssh-key", "status", "variable", "workflow",
]


def _gh_repos() -> List[str]:
    return cached("gh_repos", 30.0, lambda: run_command(
        ["gh", "repo", "list", "--limit", "50", "--json", "nameWithOwner",
         "-q", ".[].nameWithOwner"]))


def _gh_prs() -> List[str]:
    return cached("gh_prs", 10.0, lambda: run_command(
        ["gh", "pr", "list", "--limit", "50", "--json", "number",
         "-q", ".[].number"]))


def _gh_branches() -> List[str]:
    return cached("gh_branches", 10.0, lambda: run_command(
        ["gh", "api", "repos/{owner}/{repo}/branches",
         "--jq", ".[].name"]))


def gh_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_GH_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub == "repo":
        if len(ctx.args) == 1:
            yield from _emit(
                ["clone", "create", "fork", "list", "view", "delete",
                 "edit", "rename", "sync", "archive", "set-default"],
                ctx.current, "action", "ansiyellow")
            return
        if ctx.args[1] in ("clone", "fork", "view", "delete", "edit",
                           "rename", "sync", "archive", "set-default"):
            yield from _emit(_gh_repos(), ctx.current, "repo", "ansimagenta")
        return
    if sub == "pr":
        if len(ctx.args) == 1:
            yield from _emit(
                ["list", "view", "create", "checkout", "close", "merge",
                 "reopen", "review", "diff", "comment", "edit", "status"],
                ctx.current, "action", "ansiyellow")
            return
        if ctx.args[1] in ("view", "checkout", "close", "merge", "reopen",
                           "review", "diff", "comment", "edit"):
            yield from _emit(_gh_prs(), ctx.current, "pr", "ansimagenta")
        return
    if sub == "issue":
        if len(ctx.args) == 1:
            yield from _emit(
                ["list", "view", "create", "close", "reopen", "comment",
                 "edit", "status", "transfer", "delete"],
                ctx.current, "action", "ansiyellow")
        return
    yield from _emit(["--help", "-h", "--repo", "-R"],
                     ctx.current, "option", "ansired")


# ============================================================
# 32. glab (GitLab CLI)
# ============================================================

_GLAB_SUBS = [
    "alias", "api", "auth", "check-update", "ci", "cluster", "completion",
    "config", "duo", "incident", "issue", "job", "label", "mr", "opentofu",
    "release", "repo", "runner", "schedule", "securefile", "snippet",
    "ssh-key", "stack", "variable", "version",
]


def glab_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_GLAB_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub == "mr":
        if len(ctx.args) == 1:
            yield from _emit(
                ["list", "view", "create", "checkout", "close", "merge",
                 "reopen", "approve", "revoke", "diff", "note"],
                ctx.current, "action", "ansiyellow")
        return
    if sub == "issue":
        if len(ctx.args) == 1:
            yield from _emit(
                ["list", "view", "create", "close", "reopen", "note",
                 "update", "board"],
                ctx.current, "action", "ansiyellow")
        return
    yield from _emit(["--help", "-h"], ctx.current, "option", "ansired")


# ============================================================
# 33. az (Azure CLI)
# ============================================================

_AZ_TOP = [
    "account", "acr", "aks", "appconfig", "bicep", "cdn", "cloud",
    "cognitiveservices", "config", "configure", "container",
    "cosmosdb", "deployment", "disk", "dns", "functionapp", "group",
    "identity", "keyvault", "logic", "login", "logout", "monitor",
    "network", "policy", "provider", "redis", "role", "search",
    "servicebus", "signalr", "sql", "sshkey", "storage", "vm", "webapp",
]


def _az_accounts() -> List[str]:
    return cached("az_accounts", 60.0, lambda: [
        line.split()[2]
        for line in run_command(["az", "account", "list",
                                 "--query", "[].name", "-o", "tsv"])
        if line.strip()
    ])


def _az_groups() -> List[str]:
    return cached("az_groups", 30.0, lambda: [
        line.strip()
        for line in run_command(["az", "group", "list",
                                 "--query", "[].name", "-o", "tsv"])
        if line.strip()
    ])


def az_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_AZ_TOP, ctx.current, "group", "ansiyellow")
        return
    if ctx.args[0] == "account":
        yield from _emit(
            ["list", "show", "set", "clear", "list-locations",
             "get-access-token", "show", "login", "logout"],
            ctx.current, "action", "ansiyellow")
        if len(ctx.args) >= 2 and ctx.args[1] == "set":
            yield from _emit(_az_accounts(), ctx.current,
                             "account", "ansimagenta")
        return
    if ctx.args[0] == "group":
        yield from _emit(
            ["create", "delete", "list", "show", "update", "exists"],
            ctx.current, "action", "ansiyellow")
        if len(ctx.args) >= 2 and ctx.args[1] in ("delete", "show", "update"):
            yield from _emit(_az_groups(), ctx.current,
                             "group", "ansimagenta")
        return
    if ctx.current.startswith('--'):
        yield from _emit(
            ["--resource-group", "-g", "--subscription", "--output",
             "-o", "--query", "--location", "-l"],
            ctx.current, "option", "ansired")


# ============================================================
# 34. podman
# ============================================================

_PODMAN_SUBS = [
    "attach", "build", "commit", "compose", "container", "cp", "create",
    "diff", "events", "exec", "export", "generate", "healthcheck",
    "history", "image", "images", "import", "info", "inspect", "kill",
    "load", "login", "logout", "logs", "machine", "manifest", "mount",
    "network", "pause", "play", "pod", "port", "ps", "pull", "push",
    "rename", "restart", "rm", "rmi", "run", "save", "search",
    "secret", "start", "stats", "stop", "system", "tag", "top",
    "unpause", "untag", "volume", "wait",
]


def _podman_containers(all_: bool = True) -> List[str]:
    cmd = ["podman", "ps", "--format", "{{.Names}}"]
    if all_:
        cmd.insert(2, "-a")
    return cached("podman_ps_all" if all_ else "podman_ps", 5.0,
                  lambda: run_command(cmd))


def _podman_images() -> List[str]:
    return cached("podman_images", 5.0, lambda: run_command(
        ["podman", "images", "--format", "{{.Repository}}:{{.Tag}}"]))


def _podman_pods() -> List[str]:
    return cached("podman_pods", 5.0, lambda: run_command(
        ["podman", "pod", "ps", "--format", "{{.Name}}"]))


def podman_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_PODMAN_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("exec", "stop", "start", "kill", "logs", "inspect",
               "restart", "rm", "attach", "top", "pause", "unpause",
               "port", "rename", "stats", "wait"):
        yield from _emit(_podman_containers(True), ctx.current,
                         "container", "ansicyan")
        return
    if sub in ("rmi", "run", "pull", "push", "tag", "history",
               "save", "inspect"):
        yield from _emit(_podman_images(), ctx.current,
                         "image", "ansimagenta")
        return
    if sub == "pod":
        if len(ctx.args) == 1:
            yield from _emit(
                ["create", "exists", "inspect", "kill", "pause", "ps",
                 "prune", "restart", "rm", "start", "stats", "stop",
                 "top", "unpause"],
                ctx.current, "action", "ansiyellow")
        else:
            yield from _emit(_podman_pods(), ctx.current, "pod", "ansicyan")
        return


# ============================================================
# 35. docker-compose (standalone)
# ============================================================

_DC_SUBS = [
    "build", "config", "create", "down", "events", "exec", "help",
    "images", "kill", "logs", "ls", "pause", "port", "ps", "pull",
    "push", "restart", "rm", "run", "scale", "start", "stop", "top",
    "unpause", "up", "version", "volumes", "watch",
]


def _dc_services() -> List[str]:
    for fname in ("docker-compose.yml", "docker-compose.yaml",
                  "compose.yml", "compose.yaml"):
        if os.path.exists(fname):
            return cached("dc_services", 10.0, lambda: run_command(
                ["docker-compose", "config", "--services"]))
    return []


def docker_compose_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_DC_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("up", "down", "start", "stop", "restart", "logs", "ps",
               "build", "exec", "run", "kill", "top", "pause", "unpause"):
        yield from _emit(_dc_services(), ctx.current,
                         "service", "ansimagenta")


# ============================================================
# 36. minikube
# ============================================================

_MINIKUBE_SUBS = [
    "addons", "cache", "completion", "config", "dashboard", "delete",
    "docker-env", "help", "ip", "kubectl", "logs", "mount", "node",
    "pause", "podman-env", "profile", "service", "ssh", "ssh-host",
    "ssh-key", "start", "status", "stop", "tunnel", "unpause", "update-check",
    "update-context", "version",
]


def _minikube_profiles() -> List[str]:
    return cached("minikube_profiles", 10.0, lambda: [
        line.split()[0]
        for line in run_command(["minikube", "profile", "list",
                                 "-o", "json"])
        if line.strip()
    ])


def minikube_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_MINIKUBE_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("delete", "start", "stop", "ssh", "dashboard", "kubectl",
               "pause", "unpause", "ip", "logs", "update-context"):
        yield from _emit(_minikube_profiles(), ctx.current,
                         "profile", "ansimagenta")


# ============================================================
# 37. kind
# ============================================================

_KIND_SUBS = [
    "build", "completion", "create", "delete", "export", "get", "help",
    "load", "version",
]


def _kind_clusters() -> List[str]:
    return cached("kind_clusters", 10.0,
                  lambda: run_command(["kind", "get", "clusters"]))


def kind_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_KIND_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("delete", "export", "get", "load"):
        yield from _emit(_kind_clusters(), ctx.current,
                         "cluster", "ansimagenta")
    if sub == "create":
        yield from _emit(["cluster", "node"], ctx.current,
                         "action", "ansiyellow")


# ============================================================
# 38. helmfile
# ============================================================

_HELMFILE_SUBS = [
    "apply", "build", "charts", "delete", "deps", "destroy", "diff",
    "fetch", "help", "lint", "list", "repos", "secrets", "status",
    "sync", "template", "test", "version", "write-values",
]


def _helmfile_files() -> List[str]:
    return cached("helmfile_files", 5.0, lambda: _files_glob(
        ["helmfile*.yaml", "helmfile*.yml", "helmfile"], max_depth=3))


def helmfile_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_HELMFILE_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.current.startswith('-f') or ctx.args[-1] == '-f':
        yield from _emit(_helmfile_files(), ctx.current,
                         "file", "ansimagenta")


# ============================================================
# 39. psql
# ============================================================

_PSQL_SUBS = [
    "\\d", "\\dt", "\\dn", "\\du", "\\l", "\\c", "\\q", "\\h",
    "\\?", "\\x", "\\timing", "\\e", "\\i", "\\o", "\\dp", "\\df",
]


def _psql_databases() -> List[str]:
    return cached("psql_dbs", 30.0, lambda: [
        line.strip()
        for line in run_command(["psql", "-lqt"])
        if line.strip() and not line.startswith("|")
    ])


def psql_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_PSQL_SUBS, ctx.current, "meta", "ansiyellow")
        return
    if ctx.args[0] == "-d" or ctx.args[-1] == "-d":
        yield from _emit(_psql_databases(), ctx.current,
                         "database", "ansimagenta")
    elif ctx.current.startswith('-'):
        yield from _emit(
            ["-d", "-h", "-p", "-U", "-W", "-c", "-f", "-l",
             "--list", "--echo-all", "--no-psqlrc"],
            ctx.current, "option", "ansired")


# ============================================================
# 40. mysql
# ============================================================

_MYSQL_SUBS = [
    "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER",
    "SHOW", "USE", "DESCRIBE", "EXPLAIN", "COMMIT", "ROLLBACK", "GRANT",
]


def mysql_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-u", "-p", "-h", "-P", "-D", "-e", "--host", "--user",
             "--password", "--database", "--port", "--protocol"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(_MYSQL_SUBS, ctx.current, "keyword", "ansimagenta")


# ============================================================
# 41. redis-cli
# ============================================================

_REDIS_SUBS = [
    "GET", "SET", "DEL", "EXISTS", "EXPIRE", "TTL", "KEYS", "SCAN",
    "HGET", "HSET", "HDEL", "HGETALL", "LPUSH", "RPUSH", "LPOP",
    "RPOP", "LRANGE", "SADD", "SREM", "SMEMBERS", "ZADD", "ZRANGE",
    "PUBLISH", "SUBSCRIBE", "INFO", "PING", "FLUSHDB", "FLUSHALL",
    "SELECT", "DBSIZE", "TYPE", "RENAME", "CONFIG", "CLIENT", "CLUSTER",
]


def redis_cli_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-h", "-p", "-a", "-n", "-u", "--user", "--pass",
             "--tls", "--raw", "--no-raw", "--scan"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(_REDIS_SUBS, ctx.current, "cmd", "ansimagenta")


# ============================================================
# 42. mongosh / mongo
# ============================================================

_MONGO_SUBS = [
    "show", "use", "db", "rs", "sh", "help", "exit", "cls", "load",
    "insertOne", "insertMany", "find", "findOne", "updateOne",
    "updateMany", "deleteOne", "deleteMany", "aggregate", "count",
    "createIndex", "drop", "dropDatabase", "getCollection",
    "getDbs", "getCollectionNames", "stats",
]


def mongosh_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["--host", "--port", "--username", "--password",
             "--authenticationDatabase", "--eval", "--quiet",
             "--nodb", "--shell", "--file"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(_MONGO_SUBS, ctx.current, "cmd", "ansimagenta")


# ============================================================
# 43. sqlite3
# ============================================================

_SQLITE_SUBS = [
    ".databases", ".dump", ".exit", ".help", ".import", ".indexes",
    ".load", ".mode", ".output", ".quit", ".read", ".schema",
    ".tables", ".timeout", ".width", ".headers", ".backup",
    ".restore", ".save", ".changes", ".open", ".cd",
]


def _sqlite_files() -> List[str]:
    return cached("sqlite_files", 10.0,
                  lambda: _files_glob(["*.db", "*.sqlite", "*.sqlite3",
                                       "*.db3"], max_depth=3))


def sqlite3_completer(ctx: CompletionContext):
    if ctx.args:
        yield from _emit(_SQLITE_SUBS, ctx.current, "meta", "ansiyellow")
        yield from _file_completions(ctx)
    else:
        yield from _emit(_sqlite_files(), ctx.current,
                         "database", "ansimagenta")
        yield from _file_completions(ctx)


# ============================================================
# 44. curl
# ============================================================

_CURL_OPTS = [
    "-X", "--request", "-H", "--header", "-d", "--data",
    "--data-raw", "--data-binary", "-F", "--form", "-u", "--user",
    "-A", "--user-agent", "-e", "--referer", "-b", "--cookie",
    "-c", "--cookie-jar", "-o", "--output", "-O", "--remote-name",
    "-L", "--location", "-k", "--insecure", "--compressed",
    "-s", "--silent", "-v", "--verbose", "-i", "--include",
    "-I", "--head", "--http2", "--retry", "--max-time", "-x", "--proxy",
]


def curl_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_CURL_OPTS, ctx.current, "option", "ansired")
        return
    yield from _emit(["http://", "https://", "ftp://", "file://"],
                     ctx.current, "scheme", "ansimagenta")
    yield from _file_completions(ctx)


# ============================================================
# 45. wget
# ============================================================

_WGET_OPTS = [
    "-O", "--output-document", "-o", "--output-file", "-P",
    "--directory-prefix", "-c", "--continue", "-r", "--recursive",
    "-np", "--no-parent", "-nH", "--no-host-directories",
    "--limit-rate", "-q", "--quiet", "-v", "--verbose",
    "-nc", "--no-clobber", "--user-agent", "--header",
    "--user", "--password", "-i", "--input-file",
]


def wget_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_WGET_OPTS, ctx.current, "option", "ansired")
        return
    yield from _emit(["http://", "https://", "ftp://"],
                     ctx.current, "scheme", "ansimagenta")
    yield from _file_completions(ctx)


# ============================================================
# 46. rsync
# ============================================================

_RSYNC_OPTS = [
    "-a", "--archive", "-v", "--verbose", "-z", "--compress",
    "-P", "--partial", "--progress", "-r", "--recursive",
    "-u", "--update", "--delete", "--exclude", "--include",
    "-e", "--rsh", "--bwlimit", "--dry-run", "-n",
    "--exclude-from", "--files-from", "--chmod", "--chown",
]


def rsync_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_RSYNC_OPTS, ctx.current, "option", "ansired")
        return
    yield from _file_completions(ctx)


# ============================================================
# 47. scp
# ============================================================

def scp_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-r", "-P", "-p", "-i", "-C", "-q", "-v", "-o", "-l",
             "-F", "-J", "-3", "-4", "-6"],
            ctx.current, "option", "ansired")
        return
    for h in _ssh_hosts():
        if prefix_match(ctx.current, h):
            yield CompletionItem(text=h + ":", meta="host", style="ansicyan")
    yield from _file_completions(ctx)


# ============================================================
# 48. tar
# ============================================================

def _tar_archives() -> List[str]:
    return cached("tar_archives", 5.0, lambda: _files_glob(
        ["*.tar", "*.tar.gz", "*.tgz", "*.tar.bz2", "*.tar.xz",
         "*.tar.zst", "*.zip"], max_depth=3))


def tar_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-c", "-x", "-t", "-v", "-f", "-z", "-j", "-J", "-C",
             "--extract", "--create", "--list", "--file", "--gzip",
             "--bzip2", "--xz", "--directory", "--strip-components",
             "--exclude", "--wildcards"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(_tar_archives(), ctx.current,
                     "archive", "ansimagenta")
    yield from _file_completions(ctx)


# ============================================================
# 49. openssl
# ============================================================

_OPENSSL_SUBS = [
    "asn1parse", "ca", "ciphers", "cms", "crl", "crl2pkcs7", "dgst",
    "dhparam", "dsa", "dsaparam", "ec", "ecparam", "enc", "engine",
    "errstr", "gendsa", "genpkey", "genrsa", "mac", "nseq", "ocsp",
    "passwd", "pkcs12", "pkcs7", "pkcs8", "pkey", "pkeyparam",
    "pkeyutl", "prime", "rand", "req", "rsa", "rsautl", "s_client",
    "s_server", "s_time", "sess_id", "smime", "speed", "spkac",
    "srp", "storeutl", "ts", "verify", "version", "x509",
]


def openssl_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_OPENSSL_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-in", "-out", "-key", "-pubkey", "-cert", "-CAfile",
             "-CApath", "-noout", "-text", "-passin", "-passout",
             "-days", "-nodes", "-newkey", "-subj", "-config"],
            ctx.current, "option", "ansired")


# ============================================================
# 50. gpg
# ============================================================

_GPG_SUBS = [
    "--list-keys", "--list-secret-keys", "--import", "--export",
    "--export-secret-keys", "--encrypt", "--decrypt", "--sign",
    "--verify", "--clearsign", "--detach-sign", "--gen-key",
    "--quick-generate-key", "--full-generate-key", "--edit-key",
    "--delete-key", "--delete-secret-key", "--card-status",
    "--card-edit", "--recv-keys", "--send-keys", "--keyserver",
    "--search-keys", "--refresh-keys", "--fingerprint",
    "--list-packets", "--armor", "--output", "--batch",
    "--yes", "--quiet", "--verbose",
]


def gpg_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_GPG_SUBS, ctx.current, "option", "ansired")
        return
    yield from _file_completions(ctx)


# ============================================================
# 51. jq
# ============================================================

_JQ_OPTS = [
    "-r", "--raw-output", "-c", "--compact-output", "-n",
    "--null-input", "-s", "--slurp", "-e", "--exit-status",
    "-j", "--join-output", "-a", "--ascii-output", "-S", "--sort-keys",
    "-f", "--from-file", "--arg", "--argjson", "--slurpfile",
    "--rawfile", "--tab", "--indent", "--stream",
]


def jq_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_JQ_OPTS, ctx.current, "option", "ansired")
        return
    yield from _file_completions(ctx)


# ============================================================
# 52. yq
# ============================================================

_YQ_OPTS = [
    "-i", "--inplace", "-P", "--prettyPrint", "-p", "--input-format",
    "-o", "--output-format", "-N", "--no-colors", "-C", "--colors",
    "-e", "--exit-status", "-n", "--null-input", "-s", "--slurp",
    "--arg", "--argjson",
]


def yq_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_YQ_OPTS, ctx.current, "option", "ansired")
        return
    yield from _file_completions(ctx)


# ============================================================
# 53. rg (ripgrep)
# ============================================================

_RG_OPTS = [
    "-i", "--ignore-case", "-s", "--case-sensitive", "-S",
    "--smart-case", "-w", "--word-regexp", "-v", "--invert-match",
    "-c", "--count", "-l", "--files-with-matches", "-L",
    "--files-without-match", "-n", "--line-number", "-N",
    "--no-line-number", "-H", "--with-filename", "--no-heading",
    "-A", "--after-context", "-B", "--before-context", "-C",
    "--context", "-t", "--type", "-T", "--type-not", "-g", "--glob",
    "--hidden", "--no-ignore", "-uu", "--follow", "-F",
    "--fixed-strings", "-e", "--regexp", "-f", "--file",
    "--max-depth", "--max-count", "-U", "--multiline",
]


def rg_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_RG_OPTS, ctx.current, "option", "ansired")
        return
    yield from _file_completions(ctx)


# ============================================================
# 54. fd
# ============================================================

_FD_OPTS = [
    "-H", "--hidden", "-I", "--no-ignore", "-u", "--unrestricted",
    "-s", "--case-sensitive", "-i", "--ignore-case", "-g", "--glob",
    "-e", "--extension", "-t", "--type", "-d", "--max-depth",
    "-a", "--absolute-path", "-L", "--follow", "-p", "--full-path",
    "-x", "--exec", "-X", "--exec-batch", "-E", "--exclude",
    "--changed-within", "--changed-before", "--owner", "-0",
    "--print0", "--strip-cwd-prefix",
]


def fd_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_FD_OPTS, ctx.current, "option", "ansired")
        return
    yield from _file_completions(ctx)


# ============================================================
# 55. bat
# ============================================================

_BAT_OPTS = [
    "-p", "--plain", "-A", "--show-all", "-n", "--number",
    "-l", "--list-languages", "--theme", "--theme-dark",
    "--theme-light", "-s", "--squeeze-blank", "-r", "--line-range",
    "-H", "--highlight-line", "-m", "--map-syntax",
    "-f", "--force-colorization", "-d", "--diff", "--diff-context",
    "--style", "--paging", "-P", "--no-paging",
]


def bat_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_BAT_OPTS, ctx.current, "option", "ansired")
        return
    yield from _file_completions(ctx)


# ============================================================
# 56. code (VS Code)
# ============================================================

_CODE_OPTS = [
    "-n", "--new-window", "-r", "--reuse-window", "-g", "--goto",
    "-a", "--add", "-d", "--diff", "-m", "--merge", "-w",
    "--wait", "--user-data-dir", "--extensions-dir", "--list-extensions",
    "--install-extension", "--uninstall-extension", "--disable-extensions",
    "--enable-proposed-api", "--verbose", "--log",
]


def code_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_CODE_OPTS, ctx.current, "option", "ansired")
        return
    yield from _file_completions(ctx)


# ============================================================
# 57. nvim / vim
# ============================================================

_NVIM_OPTS = [
    "-c", "--cmd", "-u", "--noplugin", "-p", "--nofork", "-o",
    "-O", "-d", "-R", "-M", "-n", "-b", "-e", "-es", "-s",
    "--clean", "--headless", "--version", "--help",
    "+", "-S", "--startuptime", "-i",
]


def _nvim_completer(ctx: CompletionContext):
    if ctx.current.startswith(('+', '-')):
        if ctx.current.startswith('-'):
            yield from _emit(_NVIM_OPTS, ctx.current, "option", "ansired")
        return
    yield from _file_completions(ctx)


# ============================================================
# 58. cmake
# ============================================================

_CMAKE_SUBS = [
    "-S", "-B", "-G", "-D", "-U", "-T", "-A", "--build",
    "--install", "--target", "--config", "--clean-first",
    "--build", "--parallel", "-j", "--verbose", "-L", "-N",
    "--fresh", "--trace", "--debug-output", "--log-level",
]


def cmake_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_CMAKE_SUBS, ctx.current, "option", "ansired")
        return
    yield from _file_completions(ctx)


# ============================================================
# 59. bazel
# ============================================================

_BAZEL_SUBS = [
    "build", "test", "run", "clean", "query", "cquery", "aquery",
    "fetch", "sync", "info", "version", "help", "shutdown",
    "coverage", "mobile-install", "canonicalize-flags", "dump",
    "mod", "vendor", "analyze-profile", "completion",
]


def _bazel_targets() -> List[str]:
    return cached("bazel_targets", 30.0, lambda: run_command(
        ["bazel", "query", "...", "--output=label"]))


def bazel_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_BAZEL_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.current.startswith('-'):
        yield from _emit(
            ["--config", "--keep_going", "-k", "--jobs", "--verbose_failures",
             "--test_output", "--test_filter", "--copt", "--cxxopt",
             "--compilation_mode", "-c", "--platforms", "--remote_cache"],
            ctx.current, "option", "ansired")
        return
    if ctx.args[0] in ("build", "test", "run", "query", "coverage"):
        yield from _emit(_bazel_targets(), ctx.current,
                         "target", "ansimagenta")


# ============================================================
# 60. deno
# ============================================================

_DENO_SUBS = [
    "bench", "bundle", "cache", "check", "compile", "completions",
    "coverage", "doc", "eval", "fmt", "info", "init", "install",
    "jsonc", "jupyter", "lint", "lsp", "repl", "run", "serve",
    "task", "test", "types", "uninstall", "upgrade", "vendor",
]


def _deno_tasks() -> List[str]:
    def _read():
        for fname in ("deno.json", "deno.jsonc"):
            path = os.path.join(os.getcwd(), fname)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                # 简单剥离 // 注释，容忍 jsonc
                import re
                text = re.sub(r"//[^\n]*", "", text)
                data = json.loads(text)
                return list((data.get("tasks") or {}).keys())
            except Exception:
                return []
        return []
    return cached("deno_tasks", 5.0, _read)


def deno_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_DENO_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub == "task":
        yield from _emit(_deno_tasks(), ctx.current, "task", "ansimagenta")
        return
    if sub in ("run", "test", "bench", "check", "lint", "fmt"):
        yield from _file_completions(ctx)
    if ctx.current.startswith('-'):
        yield from _emit(
            ["--allow-all", "-A", "--allow-read", "--allow-write",
             "--allow-net", "--allow-env", "--allow-run",
             "--import-map", "--config", "--reload", "--unstable",
             "--watch", "--no-check", "--quiet"],
            ctx.current, "option", "ansired")


# ============================================================
# 注册（60 个）
# ============================================================

def register(reg):
    # 30 个基础命令
    reg.register("git",         git_completer,         "Git dynamic completion")
    reg.register("docker",      docker_completer,      "Docker dynamic completion")
    reg.register("kubectl",     kubectl_completer,     "Kubernetes dynamic completion")
    reg.register("npm",         npm_completer,         "npm scripts and subcommands")
    reg.register("pip",         pip_completer,         "pip subcommands and options")
    reg.register("cargo",       cargo_completer,       "cargo subcommands")
    reg.register("go",          go_completer,          "go subcommands and packages")
    reg.register("make",        make_completer,        "Makefile targets")
    reg.register("systemctl",   systemctl_completer,   "systemd units")
    reg.register("ssh",         ssh_completer,         "SSH hosts from ~/.ssh/config")
    reg.register("tmux",        tmux_completer,        "tmux subcommands")
    reg.register("aws",         aws_completer,         "AWS CLI services")
    reg.register("terraform",   terraform_completer,   "terraform subcommands + workspaces")
    reg.register("ansible",     ansible_completer,     "ansible playbooks")
    reg.register("helm",        helm_completer,        "helm releases and repos")
    reg.register("composer",    composer_completer,    "composer scripts")
    reg.register("mvn",         mvn_completer,         "Maven lifecycle phases")
    reg.register("gradle",      gradle_completer,      "Gradle tasks")
    reg.register("dotnet",      dotnet_completer,      "dotnet subcommands")
    reg.register("conda",       conda_completer,       "conda environments")
    reg.register("poetry",      poetry_completer,      "poetry subcommands and scripts")
    reg.register("yarn",        yarn_completer,        "yarn subcommands and scripts")
    reg.register("pnpm",        pnpm_completer,        "pnpm subcommands and scripts")
    reg.register("pytest",      pytest_completer,      "pytest test files")
    reg.register("rake",        rake_completer,        "Rake tasks")
    reg.register("bundle",      bundle_completer,      "bundler subcommands")
    reg.register("jupyter",     jupyter_completer,     "jupyter subcommands")
    reg.register("hugo",        hugo_completer,        "hugo subcommands")
    reg.register("ffmpeg",      ffmpeg_completer,      "ffmpeg options and files")
    reg.register("gcloud",      gcloud_completer,      "gcloud groups and options")

    # 新增 30 个命令
    reg.register("gh",          gh_completer,          "GitHub CLI repos/prs/issues")
    reg.register("glab",        glab_completer,        "GitLab CLI subcommands")
    reg.register("az",          az_completer,          "Azure CLI groups and accounts")
    reg.register("podman",      podman_completer,      "podman containers/images/pods")
    reg.register("docker-compose", docker_compose_completer, "docker-compose services")
    reg.register("minikube",    minikube_completer,    "minikube profiles")
    reg.register("kind",        kind_completer,        "kind clusters")
    reg.register("helmfile",    helmfile_completer,    "helmfile files and subcommands")
    reg.register("psql",        psql_completer,        "psql meta and databases")
    reg.register("mysql",       mysql_completer,       "mysql options and keywords")
    reg.register("redis-cli",   redis_cli_completer,   "redis-cli commands")
    reg.register("mongosh",     mongosh_completer,     "mongosh commands")
    reg.register("sqlite3",     sqlite3_completer,     "sqlite3 databases and meta")
    reg.register("curl",        curl_completer,        "curl options and URLs")
    reg.register("wget",        wget_completer,        "wget options and URLs")
    reg.register("rsync",       rsync_completer,       "rsync options and paths")
    reg.register("scp",         scp_completer,         "scp hosts and paths")
    reg.register("tar",         tar_completer,         "tar archives and options")
    reg.register("openssl",     openssl_completer,     "openssl subcommands")
    reg.register("gpg",         gpg_completer,         "gpg options and files")
    reg.register("jq",          jq_completer,          "jq options and files")
    reg.register("yq",          yq_completer,          "yq options and files")
    reg.register("rg",          rg_completer,          "ripgrep options and files")
    reg.register("fd",          fd_completer,          "fd options and files")
    reg.register("bat",         bat_completer,         "bat options and files")
    reg.register("code",        code_completer,        "VS Code options and files")
    reg.register("nvim",        _nvim_completer,       "nvim options and files")
    reg.register("vim",         _nvim_completer,       "vim options and files")
    reg.register("cmake",       cmake_completer,       "cmake options and files")
    reg.register("bazel",       bazel_completer,       "bazel subcommands and targets")
    reg.register("deno",        deno_completer,        "deno subcommands and tasks")