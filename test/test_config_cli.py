# -*- coding: utf-8 -*-
"""`ai config` 私人配置命令自校验。

运行：python3 test/test_config_cli.py   （退出码 0 = 通过）
"""
import json
import os
import shutil
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="config_cli_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bin.ai_lib import config_cli, config, ui  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("  OK  %s" % name if cond else "  FAIL %s %s" % (name, detail))
    if not cond:
        FAILS.append(name)


def capture(fn, *a, **kw):
    import io as _io
    import contextlib
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a, **kw)
    return rc, buf.getvalue()


def main():
    print("== 1. 初始化配置 ==")
    config.save_key_conf("deepseek", "sk-test-1234567890", "deepseek-v4-pro",
                         {"temperature": 0.1, "max_tokens": 4096})
    check("key.json 已创建", os.path.isfile(config.KEY_CONF_PATH))

    print("== 2. show（掩码）==")
    rc, out = capture(config_cli.run, "show")
    check("show 退出码 0", rc == 0)
    check("show 含平台/模型", "deepseek" in out and "deepseek-v4-pro" in out)
    check("show 不含明文 key", "sk-test-1234567890" not in out, out[:200])
    check("show 含掩码", "***" in out or "sk-t" in out)
    rc, out = capture(config_cli.run, "show", ["--json"])
    data = json.loads(out)
    check("--json 输出可解析", data.get("platform") == "deepseek" and data.get("model"))

    print("== 3. get ==")
    rc, out = capture(config_cli.run, "get", ["model"])
    check("get model", out.strip() == "deepseek-v4-pro", out)
    rc, out = capture(config_cli.run, "get", ["param.temperature"])
    check("get param.temperature", out.strip() == "0.1", out)
    rc, out = capture(config_cli.run, "get", ["nope"])
    check("get 未知键返回 1", rc == 1)

    print("== 4. set 校验 ==")
    rc, out = capture(config_cli.run, "set", ["key=short"])
    check("拒绝过短密钥", rc == 1 and "无效" in out, out[:100])
    rc, out = capture(config_cli.run, "set", ["url=ftp://x"])
    check("拒绝非 http(s) URL", rc == 1)
    rc, out = capture(config_cli.run, "set", ["platform=nonexistent"])
    check("拒绝未知平台", rc == 1)
    rc, out = capture(config_cli.run, "set", ["foo=bar"])
    check("拒绝未知键", rc == 1)

    print("== 5. set 生效 + 切平台保留用户参数 ==")
    rc, out = capture(config_cli.run, "set", ["param.temperature=0.7", "param.max_tokens=2048"])
    check("set 参数成功", rc == 0)
    conf = config.load_key_conf()
    check("temperature 已写入", float(conf["params"]["temperature"]) == 0.7, conf["params"])
    rc, out = capture(config_cli.run, "set", ["platform=openai"])
    conf = config.load_key_conf()
    check("平台已切换", conf["platform"] == "openai")
    check("用户参数被保留", float(conf["params"]["temperature"]) == 0.7, conf["params"])
    check("模型切到新平台默认", conf["model"] == config._SUPPORTED_PLATFORMS["openai"]["default_model"])

    print("== 6. test（不可达地址 → 失败分支）==")
    capture(config_cli.run, "set", ["url=http://127.0.0.1:9/v1/chat/completions"])
    rc, out = capture(config_cli.run, "test", [])
    check("不可达时返回 1", rc == 1, out[:120])
    check("输出含失败信息", "❌" in out or "失败" in out or "failed" in out, out[:160])
    ok, msg = config_cli.test_connection(timeout=3)
    check("test_connection 返回 (False, 文本)", ok is False and bool(msg))

    print("== 7. backup / restore ==")
    rc, out = capture(config_cli.run, "backup", ["t1"])
    check("备份成功", rc == 0 and "已备份" in out, out[:120])
    bdir = os.path.join(config_cli.BACKUP_ROOT, "t1")
    check("备份目录存在 manifest", os.path.isfile(os.path.join(bdir, "manifest.json")))
    capture(config_cli.run, "set", ["model=changed-model"])
    check("模型已改", config.load_key_conf()["model"] == "changed-model")
    ui.confirm = lambda msg, **kw: True
    rc, out = capture(config_cli.run, "restore", ["t1"])
    check("恢复成功", rc == 0 and "已恢复" in out, out[:150])
    check("模型已回滚", config.load_key_conf()["model"] != "changed-model",
          config.load_key_conf()["model"])
    ui.confirm = lambda msg, **kw: False
    rc, out = capture(config_cli.run, "restore", ["t1"])
    check("拒绝恢复时取消", rc == 1 and "取消" in out, out[:100])

    print("== 8. doctor ==")
    ui.confirm = lambda msg, **kw: True
    bak = config.KEY_CONF_PATH + ".bak"
    with open(bak, "w", encoding="utf-8") as f:
        f.write('api_key = sk-plaintext-abcdefghijklmnop\n')
    os.chmod(bak, 0o644)
    rc, out = capture(config_cli.run, "doctor", [])
    check("doctor 发现明文残留", "明文" in out, out[:400])
    check("doctor 报告权限问题", "权限" in out or "chmod" in out, out[:400])
    os.remove(bak)
    rc, out = capture(config_cli.run, "path", [])
    check("path 列出 key.json", config.KEY_CONF_PATH in out)

    print("== 9. 命令行解析接入 ==")
    from bin.ai_lib.helpers import parse_arguments
    r = parse_arguments(["ai", "config", "set", "model=x"], {})
    check("helpers 解析 config set", r[0] == "config_command" and r[1] == "set" and r[2] == ["model=x"])
    r = parse_arguments(["ai", "-config", "doctor"], {})
    check("helpers 解析 -config doctor", r[0] == "config_command" and r[1] == "doctor")
    cmdjson = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "etc", "cmd.json"), encoding="utf-8"))
    raw = json.dumps(cmdjson, ensure_ascii=False)
    check("etc/cmd.json 含 -config", "-config" in raw)

    print("== 10. 文档 ==")
    hp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "bin", "help", "help_info", "commands", "ai.json")
    d = json.load(open(hp, encoding="utf-8"))["命令"]["ai"]
    check("中文含私人配置章节", "私人配置" in d["Chinese"] and "ai config doctor" in d["Chinese"])
    check("英文含 Personal config", "Personal config" in d["English"] and "ai config doctor" in d["English"])

    shutil.rmtree(HOME, ignore_errors=True)
    print()
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
