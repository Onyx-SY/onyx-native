#!/usr/bin/env python3
"""
AI Plugin Loader — RSA-signed C extension manager for commercial licensing.

Architecture:
  Developer signs plugins with PRIVATE KEY (local only, never pushed).
  Runtime verifies .lic with PUBLIC KEY (key.key in project root).

  Only the public key is in the repo.  Private key is exclusively local.
  The license payload's module_id is enforced against the filename at load time.

Files:
  key.key                       RSA 2048 public key (committed)
  private.key                   RSA 2048 private key (NOT committed, local only)
  ~/.ai_onyx_plugin/<name>.so   Compiled plugin binary
  ~/.ai_onyx_plugin/<name>.lic  RSA-SHA256 license file

Usage:
  python plugin_loader.py list                    List all plugins
  python plugin_loader.py verify <name>           Verify license only
  python plugin_loader.py load <name>             Load plugin (verify + ctypes)
  python plugin_loader.py sign <name> [ver] [exp] [iss]  Sign binary (needs private.key)

License payload format (after signature verification):
  {
    "schema": "1.0",
    "module_id": "<name>",          # MUST match filename
    "binary_hash": "sha256hex...",  # anti-tamper
    "version": "1.0.0",
    "issued": "2026-01-01",
    "expires": "2027-12-31",        # empty = perpetual
    "issuer": "onyx-dev"
  }
"""

import os, sys, json, ctypes, hashlib, base64, datetime
from typing import Optional, Dict, Any, List, Tuple

PLUGIN_DIR = os.path.join(os.path.expanduser("~"), ".ai_onyx_plugin")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_KEY_PATH = os.path.join(PROJECT_DIR, "key.key")
PRIVATE_KEY_PATH = os.path.join(PROJECT_DIR, "private.key")
LICENSE_SCHEMA = "1.0"

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

def _mkpayload(mod_id: str, bh: str, ver: str = "1.0.0", exp: str = "", issuer: str = "onyx-dev") -> dict:
    p = {"schema": LICENSE_SCHEMA, "module_id": mod_id, "binary_hash": bh,
         "version": ver, "issued": datetime.date.today().isoformat(), "issuer": issuer}
    if exp:
        p["expires"] = exp
    return p

def _ser(p: dict) -> bytes:
    return json.dumps(p, sort_keys=True, separators=(",", ":")).encode()

def _validate(payload: dict, expected_id: str) -> Tuple[bool, str]:
    if payload.get("schema") != LICENSE_SCHEMA:
        return False, f"schema mismatch: {payload.get('schema')}"
    mid = payload.get("module_id", "")
    if mid != expected_id:
        return False, f"module_id mismatch: '{mid}' != '{expected_id}'"
    if not payload.get("binary_hash"):
        return False, "missing binary_hash"
    exp = payload.get("expires", "")
    if exp:
        try:
            if datetime.date.fromisoformat(exp) < datetime.date.today():
                return False, f"expired {exp}"
        except ValueError:
            return False, f"bad expiry: {exp}"
    return True, "ok"

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

def verify_plugin(name: str) -> Tuple[bool, str, dict]:
    """Verify .lic RSA signature + payload schema + binary hash + expiry.

    Returns (ok, reason, payload).
    """
    fp = _find(name)
    if not fp:
        return False, f"not found: {name}", {}
    lp = _licpath(fp)
    if not os.path.exists(lp):
        return False, f"no .lic: {lp}", {}

    pub = _pubkey()
    if pub is None:
        return False, "public key unavailable (install cryptography?)", {}

    try:
        lic = json.load(open(lp))
    except Exception as e:
        return False, f"bad .lic: {e}", {}

    payload, sig = lic.get("payload", {}), lic.get("signature", "")
    if not _verify(_ser(payload), sig, pub):
        return False, "RSA signature INVALID", payload

    ok, reason = _validate(payload, _modname(fp))
    if not ok:
        return False, reason, payload

    with open(fp, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    if actual != payload.get("binary_hash", ""):
        return False, "binary hash mismatch (tampered)", payload

    return True, "ok", payload


def load_plugin(name: str) -> Optional[ctypes.CDLL]:
    """Verify + load plugin. Returns ctypes handle or None."""
    ok, reason, payload = verify_plugin(name)
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
        exp = payload.get("expires", "perpetual")
        print(f"✅ {mid} loaded (license: {exp})")
        return lib
    except Exception as e:
        print(f"❌ ctypes load failed: {e}", file=sys.stderr)
        return None


def sign_plugin(name: str, version: str = "1.0.0", expires: str = "",
                issuer: str = "onyx-dev") -> bool:
    """Sign a binary.  Needs private.key locally.

    Creates .lic next to the .so/.dll/.dylib.
    """
    fp = _find(name)
    if not fp:
        print(f"❌ not found: {name}", file=sys.stderr)
        return False

    priv = _privkey()
    if priv is None:
        print("❌ private.key not found (local only)", file=sys.stderr)
        return False

    with open(fp, "rb") as f:
        bh = hashlib.sha256(f.read()).hexdigest()

    mid = _modname(fp)
    payload = _mkpayload(mid, bh, version, expires, issuer)
    sig = _sign(_ser(payload), priv)

    with open(_licpath(fp), "w") as f:
        json.dump({"payload": payload, "signature": sig}, f, indent=2)

    life = f"expires {expires}" if expires else "perpetual"
    print(f"✅ Signed {mid} v{version} ({life})")
    return True


def list_plugins() -> List[Dict]:
    if not os.path.isdir(PLUGIN_DIR):
        return []
    res = []
    for f in sorted(os.listdir(PLUGIN_DIR)):
        if f.endswith((".so", ".dll", ".dylib")):
            fp = os.path.join(PLUGIN_DIR, f)
            nm = _modname(fp)
            ok, reason, payload = verify_plugin(nm)
            res.append({"name": nm, "path": fp, "size": os.path.getsize(fp),
                        "verified": ok, "status": "ok" if ok else reason,
                        "expires": payload.get("expires", "")})
    return res


_CACHE: Dict[str, ctypes.CDLL] = {}


# ── CLI ────────────────────────────────────────────────────────────

def _help():
    print(f"""AI Plugin Loader — RSA-signed C extension manager

Commands:
  list                          List plugins
  verify <name>                 Verify license
  load <name>                   Load plugin
  sign <name> [ver] [exp] [iss] Sign binary (private.key needed)

  key.key (public)  — in repo, used for verification
  private.key       — local only, used for signing
  ~/.ai_onyx_plugin/ — plugin storage (auto-created)
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
        ok, r, p = verify_plugin(sys.argv[2])
        print(f"{'✅' if ok else '❌'} {sys.argv[2]}: {r}")
        if ok and p:
            print(f"  module_id: {p.get('module_id')}  v{p.get('version')}  issued:{p.get('issued')}  expires:{p.get('expires','—')}")

    elif cmd == "load":
        lib = load_plugin(sys.argv[2])
        if lib is None:
            sys.exit(1)

    elif cmd == "sign":
        if len(sys.argv) < 3:
            print("Usage: sign <name> [version] [expires] [issuer]")
            return
        sign_plugin(sys.argv[2], sys.argv[3] if len(sys.argv)>3 else "1.0.0",
                    sys.argv[4] if len(sys.argv)>4 else "",
                    sys.argv[5] if len(sys.argv)>5 else "onyx-dev")

    else:
        _help()
        sys.exit(1)

if __name__ == "__main__":
    main()
