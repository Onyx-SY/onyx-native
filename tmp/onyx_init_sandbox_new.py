def init_sandbox_config() -> None:
    """初始化沙箱配置：只读取配置文件，不再交互询问。

    配置文件（etc/onyx/sandbox）由首次启动向导（Main.py）写入；
    若文件缺失（例如被手动删除），默认启用沙箱并补齐配置文件，
    保证后续启动行为一致，且启动过程绝不阻塞等待用户输入。
    """
    global _SANDBOX_ENABLED, ROOT_DIR, _SANDBOX_CONFIG_PATH

    # ========== 关键修复：基于当前文件位置定位配置目录 ==========
    current_dir = os.path.dirname(os.path.abspath(__file__))
    onyx_root = os.path.dirname(current_dir)
    parent_of_onyx = onyx_root
    _SANDBOX_CONFIG_PATH = os.path.abspath(os.path.join(parent_of_onyx, "etc", "onyx", "sandbox"))

    # 1. 如果配置文件已存在，读取其内容
    if os.path.exists(_SANDBOX_CONFIG_PATH):
        try:
            with open(_SANDBOX_CONFIG_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip().lower()
                _SANDBOX_ENABLED = (content == "true")
            log_info(f"沙箱配置加载：{_SANDBOX_CONFIG_PATH} -> enabled={_SANDBOX_ENABLED}", str(uuid.uuid4()))
        except Exception:
            _SANDBOX_ENABLED = True
        return

    # 2. 配置文件不存在 -> 默认启用并静默补齐（首次启动向导已询问用户，此处不再询问）
    _SANDBOX_ENABLED = True
    log_info("未找到沙箱配置文件，默认启用沙箱（首次启动向导中可配置）", str(uuid.uuid4()))
    try:
        config_dir = os.path.dirname(_SANDBOX_CONFIG_PATH)
        if not os.path.exists(config_dir):
            os.makedirs(config_dir, mode=0o755)
        with open(_SANDBOX_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("true")
        if os.name == "posix":
            os.chmod(_SANDBOX_CONFIG_PATH, 0o644)
        log_info(f"沙箱配置已保存：{_SANDBOX_CONFIG_PATH} -> {_SANDBOX_ENABLED}", str(uuid.uuid4()))
    except Exception as e:
        log_error(f"沙箱配置保存失败：{str(e)}", str(uuid.uuid4()))


