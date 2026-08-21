#!/usr/bin/env python3
"""
AI Plugin Loader — RSA-signed C extension manager.

Architecture:
  Developer signs plugins with PRIVATE KEY (ai_plugin/private.key, local only).
  Runtime verifies .lic with PUBLIC KEY (key.key in project root).

Only the public key is in the repo.  Private key is exclusively local.
License module_id is enforced against the filename at load time.

Files:
  key.key                       RSA 2048 public key (committed)
  ai_plugin/private.key         RSA 2048 private key (local only)
  ~/.ai_onyx_plugin/<name>.so   Compiled plugin binary
  ~/.ai_onyx_plugin/<name>.lic  RSA-SHA256 license file

Usage:
  python bin/plugin_loader.py list                     List all plugins
  python bin/plugin_loader.py verify <name>            Verify license
  python bin/plugin_loader.py load <name>              Load plugin (verify + ctypes)
  python bin/plugin_loader.py sign <name> [ver] [exp]  Sign binary (needs private.key)

  Shortcut from project root:
  python -m bin.plugin_loader list
"""

import os, sys, json, ctypes, hashlib, base64, datetime, subprocess, re
from typing import Optional, Dict, Any, List, Tuple

# ── Termux 检测 ────────────────────────────────────────────────
def _is_termux() -> bool:
    return "termux" in sys.prefix.lower() or os.path.exists("/data/data/com.termux")


def _real_user_home() -> str:
    """获取真实的用户主目录（Termux 下绕过虚拟 HOME）。"""
    if _is_termux():
        # Termux 真实家目录是固定的
        return "/data/data/com.termux/files/home"
    return os.path.expanduser("~")


PLUGIN_DIR = os.path.join(os.path.expanduser("~"), ".ai_onyx_plugin")
REAL_PLUGIN_DIR = os.path.join(_real_user_home(), ".ai_onyx_plugin")
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_KEY_PATH = os.path.join(PROJECT_DIR, "key.key")
PRIVATE_KEY_PATH = os.path.join(PROJECT_DIR, "ai_plugin", "private.key")
LICENSE_SCHEMA = "1.0"

# ── 插件注册索引（ai plugin-add 写入）───────────────────────────────
# 位置: ~/.ai_s/plugin_tool/index.json
# 结构: {"schema": 1, "plugins": {"<name>": {"lib": <绝对路径>, "key": <绝对路径|None>, ...}}}
INDEX_PATH = os.path.join(os.path.expanduser("~"), ".ai_s", "plugin_tool", "index.json")
INDEX_SCHEMA = 1


# ── Machine fingerprint ───────────────────────────────────────────

def get_machine_id() -> str:
    """Get a stable hardware-bound machine identifier.

    Returns a SHA256 hexdigest that SHOULD be unique per machine and
    reasonably stable across OS reinstalls (tied to hardware UUID).

    Linux/Termux 无 machine-id 时：使用真实家目录下的持久化文件
    (~/.ai_onyx_machine_id，Termux 为 /data/data/com.termux/files/home)，
    首次运行自动生成——避免 uuid.getnode() 在部分 Termux 环境跨进程漂移。
    C 插件 (mem_proc_monitor/keygen) 使用完全相同的算法，保证机器码一致。
    """
    ids = []
    # Linux
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        if os.path.exists(p):
            try:
                ids.append(open(p).read().strip())
            except Exception:
                pass
    # Linux/Termux 兜底：持久化机器指纹文件（与 C 工具共享同一路径/算法）
    if not ids and (sys.platform.startswith("linux") or _is_termux()):
        mid_file = os.path.join(_real_user_home(), ".ai_onyx_machine_id")
        try:
            if os.path.isfile(mid_file):
                v = open(mid_file, "r").read().strip()
                if len(v) >= 8:
                    ids.append(v)
            else:
                import secrets
                v = secrets.token_hex(16)
                with open(mid_file, "w") as f:
                    f.write(v)
                try:
                    os.chmod(mid_file, 0o600)
                except Exception:
                    pass
                ids.append(v)
        except Exception:
            pass
    # macOS
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5
            )
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', r.stdout)
            if m:
                ids.append(m.group(1))
        except Exception:
            pass
        try:
            r = subprocess.run(["scutil", "--get", "ComputerName"],
                               capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                ids.append(r.stdout.strip())
        except Exception:
            pass
    # Windows
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["wmic", "csproduct", "get", "uuid"],
                capture_output=True, text=True, timeout=5
            )
            for ln in r.stdout.splitlines():
                ln = ln.strip()
                if ln and ln != "UUID":
                    ids.append(ln)
        except Exception:
            pass
    # Fallback: MAC-based
    if not ids:
        import uuid
        ids.append(str(uuid.getnode()))
    # Combine and hash
    raw = "-".join(ids)
    return hashlib.sha256(raw.encode()).hexdigest()


# ── RSA ────────────────────────────────────────────────────────────

def _pubkey():
    if not os.path.exists(PUBLIC_KEY_PATH):
        return None
    try:
        from cryptography.hazmat.primitives import serialization
        with open(PUBLIC_KEY_PATH, "rb") as f:
            return serialization.load_pem_public_key(f.read())
    except Exception:
        return None


def _privkey():
    if not os.path.exists(PRIVATE_KEY_PATH):
        return None
    try:
        from cryptography.hazmat.primitives import serialization
        with open(PRIVATE_KEY_PATH, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    except Exception:
        return None


def _sign(data: bytes, key) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    return base64.b64encode(key.sign(data, padding.PKCS1v15(), hashes.SHA256())).decode()


def _verify(data: bytes, sig_b64: str, key) -> bool:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.exceptions import InvalidSignature
    try:
        key.verify(base64.b64decode(sig_b64), data, padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, Exception):
        return False


# ── License payload ────────────────────────────────────────────────

def _payload(mod_id: str, bh: str, ver: str = "1.0.0",
             exp: str = "", issuer: str = "onyx-dev",
             machine_id: str = "") -> dict:
    p = {"schema": LICENSE_SCHEMA, "module_id": mod_id, "binary_hash": bh,
         "version": ver, "issued": datetime.date.today().isoformat(), "issuer": issuer}
    if exp:
        p["expires"] = exp
    if machine_id:
        p["machine_id"] = machine_id
    return p


def _ser(p: dict) -> bytes:
    return json.dumps(p, sort_keys=True, separators=(",", ":")).encode()


# ── Discovery ──────────────────────────────────────────────────────

def _sync_plugin_from_src(name: str) -> None:
    """执行前同步：把用户输入路径(src)的插件复制到正式路径(lib)。

    规则：src 存在且 (lib 不存在 或 src 的 mtime/大小 更新) → 复制。
    插件更新 = 直接替换 src 路径下的文件，下次调用自动同步。
    """
    try:
        entry = _index_entry(name)
        if not entry:
            return
        src = entry.get("src")
        lib = entry.get("lib")
        if not src or not lib or src == lib:
            return
        if not os.path.isfile(src):
            return
        need = not os.path.isfile(lib)
        if not need:
            try:
                ss, ls = os.stat(src), os.stat(lib)
                need = (ss.st_mtime > ls.st_mtime) or (ss.st_size != ls.st_size)
            except OSError:
                need = True
        if need:
            import shutil
            os.makedirs(os.path.dirname(lib), exist_ok=True)
            shutil.copy2(src, lib)
            print(f"🔄 插件已同步: {src} → {lib} (Plugin synced: {src} → {lib})")
    except Exception:
        pass


def _find(name: str) -> Optional[str]:
    """按名称查找动态链接库：优先查注册索引（ai plugin-add），再查旧插件目录。"""
    # 0) 注册插件：先同步 src → lib（Termux 或任何平台，更新后自动生效）
    _sync_plugin_from_src(name)
    # 1) 注册索引（.ai_s/plugin_tool/index.json）
    entry = _index_entry(name)
    if entry and entry.get("lib") and os.path.isfile(entry["lib"]):
        return entry["lib"]
    # 2) 旧插件目录 ~/.ai_onyx_plugin/
    if os.path.isdir(PLUGIN_DIR):
        for ext in (".so", ".dll", ".dylib"):
            for b in (name, f"{name}_lib"):
                fp = os.path.join(PLUGIN_DIR, f"{b}{ext}")
                if os.path.isfile(fp):
                    return fp
    # 3) 直接路径
    if os.path.isfile(name):
        return os.path.abspath(name)
    return None


def _key_info(name: str) -> Optional[Tuple[str, bool]]:
    """返回注册索引里的密钥信息 (value, is_data)；无密钥则 None。

    is_data=True  → value 是密钥内容（直接记录在 index，注入 C 库时用内容）；
    is_data=False → value 是密钥文件路径。
    """
    entry = _index_entry(name)
    if not entry or not entry.get("key"):
        return None
    v = entry["key"]
    if entry.get("key_is_data"):
        return (v, True)
    if os.path.isfile(v):
        return (v, False)
    # 兼容：旧记录路径已失效 → 改按内容传递（由插件内部判定）
    return (v, True)


def _licpath(fp: str) -> str:
    return fp.rsplit(".", 1)[0] + ".lic"


def _modname(fp: str) -> str:
    return os.path.splitext(os.path.basename(fp))[0].replace("_lib", "")


# ── Termux 同步 ──────────────────────────────────────────────────

def sync_to_termux() -> bool:
    """在 Termux 环境下，把插件从虚拟 HOME 复制到真实 Termux 家目录。

    Onyx 开了 sandbox 后 HOME 指向虚拟目录，但 Termux 的 C 扩展
    需要在真实家目录才能被加载。这个函数把整个插件目录同步过去。
    """
    if not _is_termux():
        return False
    if not os.path.exists(PLUGIN_DIR):
        return False
    if os.path.abspath(PLUGIN_DIR) == os.path.abspath(REAL_PLUGIN_DIR):
        return True  # 已经在真实目录了

    os.makedirs(REAL_PLUGIN_DIR, exist_ok=True)
    import shutil
    count = 0
    for f in os.listdir(PLUGIN_DIR):
        src = os.path.join(PLUGIN_DIR, f)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(REAL_PLUGIN_DIR, f)
        try:
            shutil.copy2(src, dst)
            count += 1
        except Exception:
            pass
    if count:
        print(f"📱 Termux: 已同步 {count} 个文件到 {REAL_PLUGIN_DIR}")
    return True


# ── 注册索引（.ai_s/plugin_tool/index.json）────────────────────────

def _load_index() -> dict:
    """读取插件注册索引；不存在或损坏时返回空索引。"""
    try:
        if os.path.isfile(INDEX_PATH):
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("plugins"), dict):
                return data
    except Exception:
        pass
    return {"schema": INDEX_SCHEMA, "plugins": {}}


def _save_index(data: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        tmp = INDEX_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, INDEX_PATH)
        return True
    except Exception:
        return False


def _index_entry(name: str) -> Optional[dict]:
    data = _load_index()
    return data.get("plugins", {}).get(name) or None


def _registered_plugins() -> List[str]:
    data = _load_index()
    return sorted(data.get("plugins", {}).keys())


def plugin_add(path: str, key_path: str = None) -> bool:
    """把动态链接库（+可选密钥）注册进 ~/.ai_s/plugin_tool/index.json。

    key 参数（ai plugin-add <path> key <keyfile>）：
      密钥文件路径 → 原样记录用户指定的绝对路径（不拷贝、不改写）。
    没有 key 时默认无密钥（key=None）。
    Termux：仅动态链接库硬拷贝到真实家目录（.ai_onyx_plugin/），key 保持用户指定路径。
    """
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        print(f"❌ 动态链接库不存在: {path} (Shared library not found: {path})", file=sys.stderr)
        return False

    key_val = None
    if key_path:
        key_abs = os.path.abspath(os.path.expanduser(key_path))
        if not os.path.isfile(key_abs):
            print(f"❌ 密钥文件不存在: {key_abs} (Key file not found: {key_abs})", file=sys.stderr)
            return False
        key_val = key_abs          # 密钥文件路径（key = 用户手动指定的文件，不拷贝、不改写）

    name = _modname(path)
    real_lib = path
    real_key = key_val
    src_path = path          # 用户输入时的路径（插件更新 = 替换这里的文件）

    # Termux：只把动态链接库硬拷贝到真实家目录（key 保持用户指定路径）
    if _is_termux():
        import shutil
        try:
            os.makedirs(REAL_PLUGIN_DIR, exist_ok=True)
            real_lib = os.path.join(REAL_PLUGIN_DIR, os.path.basename(path))
            shutil.copy2(path, real_lib)
            print(f"📱 Termux: 已硬拷贝 {os.path.basename(path)} → {real_lib} "
                  f"(Copied to real Termux home: {real_lib})")
        except Exception as e:
            print(f"❌ Termux 拷贝失败: {e} (Termux copy failed: {e})", file=sys.stderr)
            return False

    data = _load_index()
    data.setdefault("plugins", {})
    entry = {
        "lib": real_lib,                    # 正式路径（Termux = 硬拷贝后的真实主目录）
        "src": src_path,                    # 用户输入路径（更新插件 = 替换此文件）
        "key": real_key if key_path else None,
        "added": datetime.date.today().isoformat(),
        "system": "termux" if _is_termux() else platform_system(),
    }
    data["plugins"][name] = entry
    if not _save_index(data):
        print(f"❌ 写入索引失败: {INDEX_PATH} (Failed to write index: {INDEX_PATH})", file=sys.stderr)
        return False

    if real_key:
        key_note = f"  密钥: {real_key} (Key: {real_key})"
    else:
        key_note = "  密钥: 无 (Key: none)"
    print(f"✅ 已注册插件 [{name}] (Plugin registered: [{name}])")
    print(f"   库:   {real_lib} (Lib: {real_lib})")
    if src_path != real_lib:
        print(f"   源:   {src_path} (Src: {src_path})")
    print(f"{key_note}")
    print(f"   索引: {INDEX_PATH} (Index: {INDEX_PATH})")
    if src_path != real_lib:
        print(f"   💡 插件更新: 把新的动态链接库放到上述「源」路径替换原文件即可，"
              f"AI 调用时会自动同步 (Update: replace the file at the source path, "
              f"it is synced automatically on next use)")

    # 注册成功后尝试通过 C 库接口抓取工具调用说明（JSON schema）
    try:
        sch = tool_schema(name)
        if sch:
            print(f"  接口: plugin_tool_schema → 工具调用说明已记录 (Tool schema recorded)")
    except Exception:
        pass

    # 通知 AI 工具表失效（下次重建即包含新插件工具）
    try:
        from bin.ai_lib.native_tools import invalidate_native_tools_cache
        invalidate_native_tools_cache()
    except Exception:
        try:
            from .ai_lib.native_tools import invalidate_native_tools_cache
            invalidate_native_tools_cache()
        except Exception:
            pass
    return True


def plugin_remove(name: str) -> bool:
    data = _load_index()
    plugins = data.get("plugins", {})
    if name not in plugins:
        print(f"❌ 插件未注册: {name} (Plugin not registered: {name})", file=sys.stderr)
        return False
    del plugins[name]
    data["plugins"] = plugins
    if not _save_index(data):
        print(f"❌ 写入索引失败: {INDEX_PATH} (Failed to write index: {INDEX_PATH})", file=sys.stderr)
        return False
    print(f"✅ 已移除插件 [{name}]（文件本身未删除 / Files are kept）")
    return True


def platform_system() -> str:
    return "termux" if _is_termux() else sys.platform


# ── Public API ─────────────────────────────────────────────────────

def verify(name: str) -> Tuple[bool, str, dict]:
    """校验插件授权。

    两种模式：
      1) 旧版 RSA .lic（key.key 验签）——兼容 ai -plugin sign/verify 流程；
      2) 注册索引（ai plugin-add）带密钥文件——密钥文件存在即视为授权
         （密钥内容合法性由插件内部 plugin_set_key 深度校验），
         无密钥时视为未授权（允许裸加载由调用方自行决定）。
    在 Termux 下自动同步到真实家目录（sandbox 虚拟 HOME 兜底）。
    """
    # Termux：确保插件同步到真实目录
    if _is_termux():
        sync_to_termux()

    fp = _find(name)
    if not fp:
        return False, f"not found / 未找到: {name}", {}

    # 模式 2：注册索引 + 密钥（路径或内容）
    entry = _index_entry(name)
    if entry is not None:
        kp = entry.get("key")
        if kp:
            if entry.get("key_is_data"):
                return True, "ok (index key)", {"module_id": name, "key": kp, "key_is_data": True}
            if not os.path.isfile(kp):
                return False, f"missing key file / 密钥文件缺失: {kp}", {}
            return True, "ok (index key)", {"module_id": name, "key_path": kp}
        return True, "ok (no key / 无密钥)", {"module_id": name}

    lp = _licpath(fp)
    if not os.path.exists(lp):
        return False, f"missing license: {lp}", {}

    pub = _pubkey()
    if pub is None:
        return False, "public key unavailable (cryptography?)", {}

    try:
        lic = json.load(open(lp))
    except Exception as e:
        return False, f"bad license file: {e}", {}

    payload, sig = lic.get("payload", {}), lic.get("signature", "")
    if not _verify(_ser(payload), sig, pub):
        return False, "RSA signature INVALID — license forged or corrupted", payload

    # Validate payload
    mid = payload.get("module_id", "")
    if mid != _modname(fp):
        return False, f"module_id mismatch: '{mid}' != '{_modname(fp)}'", payload
    if not payload.get("binary_hash"):
        return False, "missing binary_hash", payload
    exp = payload.get("expires", "")
    if exp:
        try:
            if datetime.date.fromisoformat(exp) < datetime.date.today():
                return False, f"license expired: {exp}", payload
        except ValueError:
            return False, f"bad expiry: {exp}", payload

    # Anti-tamper
    with open(fp, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    if actual != payload.get("binary_hash", ""):
        return False, "binary hash mismatch — plugin modified after signing", payload

    # Machine-bound: verify running on the licensed device
    bound_machine = payload.get("machine_id", "")
    if bound_machine:
        current_machine = get_machine_id()
        if current_machine != bound_machine:
            return False, f"machine_id mismatch — license bound to different device", payload

    return True, "ok", payload


def load(name: str) -> Optional[ctypes.CDLL]:
    """Verify + load plugin. Returns ctypes handle or None.

    若注册索引带密钥且库导出 plugin_set_key(const char*)，
    会自动把密钥（文件路径或内容）直接注入插件（插件内部负责深度校验/解密）。
    """
    ok, reason, payload = verify(name)
    if not ok:
        print(f"❌ {reason}", file=sys.stderr)
        return None

    mid = payload.get("module_id", name)
    if mid in _CACHE:
        return _CACHE[mid]

    fp = _find(name)
    if not fp:
        return None
    try:
        lib = ctypes.CDLL(fp)
        kinfo = _key_info(mid)
        if kinfo:
            kvalue, kdata = kinfo
            setter = getattr(lib, "plugin_set_key", None)
            if setter is not None:
                try:
                    setter.argtypes = [ctypes.c_char_p]
                    setter.restype = ctypes.c_int
                    rc = setter(kvalue.encode("utf-8", "surrogateescape"))
                    if rc != 0:
                        print(f"❌ {mid} 密钥校验失败（rc={rc}） / Key validation failed (rc={rc})", file=sys.stderr)
                        return None
                    if kdata:
                        shown = str(kvalue)
                        print(f"🔑 {mid} 密钥内容已注入 (Key data injected: {shown[:24]}{'...' if len(shown) > 24 else ''})")
                    else:
                        print(f"🔑 {mid} 密钥已注入: {kvalue} (Key injected: {kvalue})")
                except Exception as e:
                    print(f"⚠️ plugin_set_key 调用异常: {e} (plugin_set_key exception: {e})", file=sys.stderr)
        _CACHE[mid] = lib
        exp = f" (exp:{payload['expires']})" if payload.get("expires") else ""
        print(f"✅ {mid} loaded{exp}")
        return lib
    except Exception as e:
        print(f"❌ ctypes load failed: {e}", file=sys.stderr)
        return None


def sign(name: str, version: str = "1.0.0", expires: str = "",
         issuer: str = "onyx-dev", machine_id: str = "") -> bool:
    """Sign a plugin binary. Requires ai_plugin/private.key."""
    fp = _find(name)
    if not fp:
        print(f"❌ not found: {name}", file=sys.stderr)
        return False

    priv = _privkey()
    if priv is None:
        print("❌ private.key not found (expected at ai_plugin/private.key)", file=sys.stderr)
        return False

    with open(fp, "rb") as f:
        bh = hashlib.sha256(f.read()).hexdigest()

    mid = _modname(fp)
    # If no machine_id given, bind to the current machine
    if not machine_id:
        machine_id = get_machine_id()
        print(f"   Binding to current device: {machine_id[:16]}...")

    p = _payload(mid, bh, version, expires, issuer, machine_id)
    sig = _sign(_ser(p), priv)

    with open(_licpath(fp), "w") as f:
        json.dump({"payload": p, "signature": sig}, f, indent=2)

    binds = "perpetual" if not expires else f"expires {expires}"
    dev = f", bound to device" if machine_id else ""
    print(f"✅ Signed {mid} v{version} ({binds}{dev})")
    return True


def list_plugins() -> List[Dict]:
    """列出全部插件：注册索引（ai plugin-add）+ 旧插件目录。"""
    res = []
    seen = set()

    # 1) 注册索引
    for nm in _registered_plugins():
        entry = _index_entry(nm)
        if not entry:
            continue
        fp = entry.get("lib", "")
        seen.add(nm)
        ok, reason, payload = verify(nm)
        res.append({"name": nm, "path": fp,
                    "size": os.path.getsize(fp) if fp and os.path.isfile(fp) else 0,
                    "verified": ok, "status": "ok" if ok else reason,
                    "expires": "", "key": entry.get("key"),
                    "schema": entry.get("schema")})

    # 2) 旧插件目录 ~/.ai_onyx_plugin/（Termux：先同步再列出）
    if _is_termux():
        sync_to_termux()
    scan_dir = REAL_PLUGIN_DIR if _is_termux() else PLUGIN_DIR
    if os.path.isdir(scan_dir):
        for f in sorted(os.listdir(scan_dir)):
            if not f.endswith((".so", ".dll", ".dylib")):
                continue
            fp = os.path.join(scan_dir, f)
            nm = _modname(fp)
            if nm in seen:
                continue
            ok, reason, payload = verify(nm)
            res.append({"name": nm, "path": fp, "size": os.path.getsize(fp),
                        "verified": ok, "status": "ok" if ok else reason,
                        "expires": payload.get("expires", ""), "key": None,
                        "schema": None})
    return res


def tool_schema(name: str) -> Optional[dict]:
    """通过 C 库 plugin_tool_schema 接口获取工具调用说明（JSON）。

    优先读 index 缓存；无缓存时加载插件调用接口获取并缓存到 index。
    返回 OpenAI function-calling 风格的 dict，供 AI 参考如何调用该工具。
    """
    entry = _index_entry(name)
    if entry and isinstance(entry.get("schema"), dict):
        return entry["schema"]
    lib = load(name)
    if not lib:
        return None
    fn = getattr(lib, "plugin_tool_schema", None)
    if fn is None:
        return None
    fn.restype = ctypes.c_char_p
    try:
        raw = fn()
    except Exception:
        return None
    if not raw:
        return None
    try:
        schema = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None
    try:
        data = _load_index()
        if name in data.get("plugins", {}):
            data["plugins"][name]["schema"] = schema
            _save_index(data)
    except Exception:
        pass
    return schema


def refresh_tool_schemas() -> int:
    """遍历注册插件，通过各插件 plugin_tool_schema 接口刷新调用说明。"""
    n = 0
    for name in _registered_plugins():
        try:
            if tool_schema(name) is not None:
                n += 1
        except Exception:
            continue
    return n


def plugin_tools_schemas() -> Dict[str, dict]:
    """返回 {插件名: schema}——供 AI 工具列表注入（只含已缓存 schema 的插件）。"""
    out: Dict[str, dict] = {}
    for name in _registered_plugins():
        entry = _index_entry(name)
        sch = entry.get("schema") if entry else None
        if isinstance(sch, dict):
            out[name] = sch
    return out


def _argv_from_arguments(name: str, arguments: dict) -> List[str]:
    """AI 参数字典 → 插件命令行参数（--flag value；bool True → --flag）。"""
    argv = [name]
    for k, v in (arguments or {}).items():
        if v is None or v is False:
            continue
        flag = "--" + str(k).replace("_", "-")
        if isinstance(v, bool):
            argv.append(flag)
        else:
            argv += [flag, str(v)]
    return argv


def _capture_stdout(fn):
    """fd 重定向捕获 C 层 stdout（os.dup2 到 pipe + fflush），返回 (rc, text)。"""
    _libc = None
    try:
        _libc = ctypes.CDLL(None)  # libc
    except Exception:
        pass

    def _flush():
        if _libc is not None:
            try:
                _libc.fflush(None)
            except Exception:
                pass

    r, w = os.pipe()
    saved = os.dup(1)
    _flush()          # 重定向前冲掉 C 缓冲
    os.dup2(w, 1)
    try:
        rc = fn()
        _flush()      # 重定向期间把 C 缓冲冲进 pipe
    finally:
        os.dup2(saved, 1)
        os.close(saved)
        os.close(w)
    chunks = []
    while True:
        try:
            chunk = os.read(r, 65536)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    os.close(r)
    return rc, b"".join(chunks).decode("utf-8", "replace")


def execute_plugin_tool(name: str, arguments: dict = None) -> str:
    """执行注册的 C 插件工具（AI function-calling 入口）。

    把 AI 参数字典转命令行参数，自动补 --key（index 里的密钥），
    调用 .so 的 plugin_run()，fd 重定向捕获 C 库 stdout 返回给 AI。
    """
    entry = _index_entry(name)
    if not entry or not entry.get("lib"):
        return "错误: 插件未注册 (plugin not registered)"
    fp = entry["lib"]
    if not os.path.isfile(fp):
        return f"错误: 插件库不存在: {fp} (plugin library not found)"
    argv = _argv_from_arguments(name, arguments or {})
    # 自动补密钥（index 里的路径或内容）
    kinfo = _key_info(name)
    if kinfo and not any(a == "--key" for a in argv):
        argv += ["--key", kinfo[0]]
    try:
        lib = ctypes.CDLL(fp)
        fn = getattr(lib, "plugin_run", None)
        if fn is None:
            return "错误: 插件未导出 plugin_run (plugin does not export plugin_run)"
        fn.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
        fn.restype = ctypes.c_int
        argv_b = [a.encode("utf-8", "surrogateescape") for a in argv]
        arr = (ctypes.c_char_p * len(argv_b))(*argv_b)
        rc, out = _capture_stdout(lambda: fn(len(argv_b), arr))
        if out.strip():
            return out if rc == 0 else f"(exit={rc})\n{out}"
        return f"(exit={rc})"
    except Exception as e:
        return f"错误: 插件执行失败: {e} (plugin execution failed)"


_CACHE: Dict[str, ctypes.CDLL] = {}


# ── CLI ────────────────────────────────────────────────────────────

def _help():
    print(f"""AI Plugin Loader

Commands:
  python {sys.argv[0]} add <lib> [key <keyfile>]   Register plugin (.so/.dll/.dylib)
  python {sys.argv[0]} remove <name>               Unregister plugin
  python {sys.argv[0]} list                        List plugins
  python {sys.argv[0]} verify <name>               Verify license
  python {sys.argv[0]} load <name>                 Load plugin
  python {sys.argv[0]} sign <name> [v] [exp]       Sign binary (binds to this machine)
  python {sys.argv[0]} machine-id                  Show this machine's ID

  key.key (root)          — public, in repo
  ai_plugin/private.key   — private, local only
  ~/.ai_onyx_plugin/      — plugin storage
  ~/.ai_s/plugin_tool/index.json — plugin registry (ai plugin-add)
""")


def main():
    if len(sys.argv) < 2:
        return _help()
    cmd = sys.argv[1]

    if cmd == "add":
        if len(sys.argv) < 3:
            print("Usage: add <lib> [key <keyfile>]")
            sys.exit(1)
        key_path = None
        rest = sys.argv[3:]
        if "key" in rest:
            ki = rest.index("key")
            if ki + 1 < len(rest):
                key_path = rest[ki + 1]
        ok = plugin_add(sys.argv[2], key_path)
        sys.exit(0 if ok else 1)

    elif cmd == "remove":
        if len(sys.argv) < 3:
            print("Usage: remove <name>")
            sys.exit(1)
        ok = plugin_remove(sys.argv[2])
        sys.exit(0 if ok else 1)

    elif cmd == "list":
        for p in list_plugins():
            icon = "✅" if p["verified"] else "❌"
            e = f" exp:{p['expires']}" if p.get("expires") else ""
            key_note = " 🔑" if p.get("key") else ""
            print(f"  {icon} {p['name']:20s} {p['size']:>8}B  {p['status']}{e}{key_note}")

    elif cmd == "verify":
        ok, r, p = verify(sys.argv[2])
        print(f"{'✅' if ok else '❌'} {sys.argv[2]}: {r}")
        if ok and p:
            print(f"  module:    {p.get('module_id')}")
            print(f"  version:   {p.get('version')}")
            print(f"  issued:    {p.get('issued')}")
            print(f"  expires:   {p.get('expires', 'perpetual')}")
            if p.get("machine_id"):
                print(f"  machine:   {p['machine_id'][:16]}... (bound)")

    elif cmd == "load":
        lib = load(sys.argv[2])
        if lib is None:
            sys.exit(1)

    elif cmd == "machine-id":
        mid = get_machine_id()
        print(f"Machine ID: {mid}")
        print("Use this with: sign <name> [ver] [exp]")

    elif cmd == "sign":
        if len(sys.argv) < 3:
            print("Usage: sign <name> [version] [expires] [machine_id]")
            return
        mid = sys.argv[5] if len(sys.argv) > 5 else ""
        sign(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "1.0.0",
             sys.argv[4] if len(sys.argv) > 4 else "",
             "onyx-dev", mid)

    else:
        _help()
        sys.exit(1)


if __name__ == "__main__":
    main()
