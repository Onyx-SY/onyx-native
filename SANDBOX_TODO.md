# Onyx 沙箱虚拟化 — 未完成任务

## 背景

Onyx 是一个终端 AI 编程助手，运行在用户的项目目录中。当前已经有了基础的沙箱系统（`SANDBOX_ENABLED` / `SANDBOX_CONFIG`），但 AI 仍然可以"看穿"到真实文件系统根目录。

## 目标

当 `SANDBOX_ENABLED == True` 时：

1. **AI 视角的根目录 = 项目根目录**（`ROOT_DIR`，如 `/root/onyx-tbs/`）
   - AI 不应该知道 `/` 的存在，它看到的 `/` 就是 `ROOT_DIR`
   - AI 的所有路径操作都不能逃逸到 `ROOT_DIR` 之外
   - `onyx/` 子目录受保护（AI 不能修改自身）

2. **AI 收到的路径全部是虚拟路径**
   - `pwd` → 返回虚拟路径（如 `/home/user/project` 而不是 `/root/onyx-tbs/...`）
   - `ls`、`cd` 等命令的参数 → 虚拟路径自动转物理路径
   - MCP filesystem server 的根 → 绑定到 `ROOT_DIR`

3. **当 `SANDBOX_ENABLED == False` 时**
   - 不做任何限制，真实路径直通

## 相关文件

| 文件 | 作用 |
|---|---|
| `onyx/Onyx.py` | 主入口：`init_sandbox_config()` (L1314)、`effective_root` 选择 (L1897-1901)、`get_virtual_path()` / `replace_virtual_path_in_cmd()` |
| `onyx/core/path_ops.py` | 虚拟↔物理路径转换核心 |
| `onyx/core/security.py` | `check_sandbox_path()` — 检查路径是否在 ROOT_DIR 内 |
| `onyx/lib/native_fs/engine.py` | AI 文件操作引擎（VIEW/EDIT/WRITE/APPEND/INSERT/DELETE），已有 `_resolve_path()` + 沙箱边界校验 |
| `onyx/lib/native_fs/__init__.py` | `process_markup()` / `process_blocks()` — 当前未接入执行链 |
| `onyx/lib/parse_and_execute.py` | 命令执行总调度：`replace_virtual_path_in_cmd_func`、`check_sandbox_path_func` |
| `onyx/etc/mcp/mcp.json` | MCP 服务器配置，`{CWD}` 作为文件系统根 |
| `onyx/bin/ai_lib/mcp_transport.py` | MCP 传输层，`{CWD}` 变量替换 |

## AI 上下文中路径的来源

AI 看到当前目录来自于：

1. **prompt 显示** (`core/display.py` L32-57)：`generate_prompt()` 调用 `get_virtual_path(ctx, os.getcwd())`
2. **`cd` 命令** (`core/handlers/cd_handler.py` L70)：`os.chdir(real_path)` 后 `os.environ['PWD'] = real_path`
3. **system prompt 模板**：包含 `{virtual_path}` 变量
4. **MCP filesystem**：由 `{CWD}` 决定根目录，实际替换的是物理路径

## 需要修改的地方

### 1. MCP filesystem 根目录（无需修改 ✅）

MCP filesystem 使用 `{CWD}` 作为根是对的。路径转换在 Onyx 的命令执行层完成（`replace_virtual_path_in_cmd`），不在 MCP 层。

### 2. AI 文件工具路径强制沙箱化

`lib/native_fs/engine.py` 的 `execute_block()` 已经有沙箱校验（L96-108），但 `process_markup` / `process_blocks` 尚未接入主执行链。需要在 `bin/ai_lib/api.py` 消费 `markup_blocks` 时调用沙箱校验。

此外，即使 `process_markup` 未接入，也需要确保 `call_ai_api_sse` 返回的 `markup_blocks` 中的路径在 sandbox 启用时被虚拟化。

### 3. AI 命令执行路径重写

`parse_and_execute.py` 中 `replace_virtual_path_in_cmd_func` 负责将命令参数中的虚拟路径替换为物理路径。需要确认 sandbox 启用时：

- 所有从 AI 发起的命令（`is_ai_triggered=True`）都经过路径重写
- 路径重写使用 `ROOT_DIR` 作为根
- `check_sandbox_path_func` 拒绝所有超出 `ROOT_DIR` 的访问

### 4. virtual_path/pwd 一致性

确认以下路径是一致的：

- `ctx.VIRTUAL_ROOT` 初始化（Onyx.py L1058-1070）
- `generate_prompt()` 中的虚拟路径显示
- `cd_handler.py` 中的路径处理
- `get_virtual_path()` / `get_physical_path()` 双向转换

### 5. Sandbox 配置流程

`init_sandbox_config()` (Onyx.py L1314-1395) 负责：

1. 读取 `/etc/onyx/sandbox` 文件内容（true/false）
2. 文件不存在时交互式询问用户
3. 设置 `_SANDBOX_ENABLED` 和 `SANDBOX_CONFIG`

需要确认在 Linux 桌面环境（非 Termux）上这个流程工作正常。

## 关键函数签名

```python
# Onyx.py
def init_sandbox_config() -> None:  # L1314
    ...

# core/path_ops.py  
def get_virtual_path(ctx, physical_path: str) -> str:  # L74
    ...
def get_physical_path(ctx, virtual_path: str) -> str:  # L71
    ...
def replace_virtual_path_in_cmd(cmd, path_resolver) -> str:  # L152
    ...

# core/security.py
def check_sandbox_path(ctx, path: str, request_id: str) -> bool:  # L11
    ...

# lib/native_fs/engine.py
def _resolve_path(path: str, cwd: str = None) -> str:  # L81
def execute_block(block, cwd, panel_mgr, sandbox_root):  # L91
```

## 测试场景

1. sandbox 启用 → AI 执行 `ls /` → 只能看到项目根目录下的文件
2. sandbox 启用 → AI 执行 `cat /etc/passwd` → 被拦截（超出 ROOT_DIR）
3. sandbox 启用 → AI 修改 `onyx/` 下文件 → 被保护目录拦截
4. sandbox 关闭 → 所有路径操作正常工作
5. sandbox 启用 → `pwd` 返回虚拟路径（如 `/` 对应 `ROOT_DIR`）
