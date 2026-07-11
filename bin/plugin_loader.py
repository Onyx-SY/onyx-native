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

PLUGIN_DIR = os.path.join(os.path.expanduser("~"), ".ai_onyx_plugin")
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_KEY_PATH = os.path.join(PROJECT_DIR, "key.key")
PRIVATE_KEY_PATH = os.path.join(PROJECT_DIR, "ai_plugin", "private.key")
LICENSE_SCHEMA = "1.0"


# ── Machine fingerprint ───────────────────────────────────────────

def get_machine_id() -> str:
    """Get a stable hardware-bound machine identifier.

    Returns a SHA256 hexdigest that SHOULD be unique per machine and
    reasonably stable across OS reinstalls (tied to hardware UUID).
    """
    ids = []
    # Linux
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        if os.path.exists(p):
            try:
                ids.append(open(p).read().strip())
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
    from cryptography.hazmat.primitives import hashes, padding
    return base64.b64encode(key.sign(data, padding.PKCS1v15(), hashes.SHA256())).decode()


def _verify(data: bytes, sig_b64: str, key) -> bool:
    from cryptography.hazmat.primitives import hashes, padding
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

def _find(name: str) -> Optional[str]:
    if not os.path.isdir(PLUGIN_DIR):
        return None
    for ext in (".so", ".dll", ".dylib"):
        for b in (name, f"{name}_lib"):
            fp = os.path.join(PLUGIN_DIR, f"{b}{ext}")
            if os.path.isfile(fp):
                return fp
    if os.path.isfile(name):
        return name
    return None


def _licpath(fp: str) -> str:
    return fp.rsplit(".", 1)[0] + ".lic"


def _modname(fp: str) -> str:
    return os.path.splitext(os.path.basename(fp))[0].replace("_lib", "")


# ── Public API ─────────────────────────────────────────────────────

def verify(name: str) -> Tuple[bool, str, dict]:
    """Verify .lic RSA signature + payload + binary hash + expiry."""
    fp = _find(name)
    if not fp:
        return False, f"not found: {name}", {}
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
    """Verify + load plugin. Returns ctypes handle or None."""
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
    if not os.path.isdir(PLUGIN_DIR):
        return []
    res = []
    for f in sorted(os.listdir(PLUGIN_DIR)):
        if f.endswith((".so", ".dll", ".dylib")):
            fp = os.path.join(PLUGIN_DIR, f)
            nm = _modname(fp)
            ok, reason, payload = verify(nm)
            res.append({"name": nm, "path": fp, "size": os.path.getsize(fp),
                        "verified": ok, "status": "ok" if ok else reason,
                        "expires": payload.get("expires", "")})
    return res


_CACHE: Dict[str, ctypes.CDLL] = {}


# ── CLI ────────────────────────────────────────────────────────────

def _help():
    print(f"""AI Plugin Loader

Commands:
  python {sys.argv[0]} list                    List plugins
  python {sys.argv[0]} verify <name>            Verify license
  python {sys.argv[0]} load <name>              Load plugin
  python {sys.argv[0]} sign <name> [v] [exp]    Sign binary (binds to this machine)
  python {sys.argv[0]} machine-id              Show this machine's ID

  key.key (root)          — public, in repo
  ai_plugin/private.key   — private, local only
  ~/.ai_onyx_plugin/      — plugin storage
""")


def main():
    if len(sys.argv) < 2:
        return _help()
    cmd = sys.argv[1]

    if cmd == "list":
        for p in list_plugins():
            icon = "✅" if p["verified"] else "❌"
            e = f" exp:{p['expires']}" if p.get("expires") else ""
            print(f"  {icon} {p['name']:20s} {p['size']:>8}B  {p['status']}{e}")

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
