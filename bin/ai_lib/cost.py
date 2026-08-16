# -*- coding: utf-8 -*-
"""
cost.py — 成本估算模块（models.json 价格表驱动）

设计（用户需求）：
- 价格默认取「当前用户正在使用的 AI」单价：resolve_model_price(platform, model) 优先
  命中 models.json 的 price_per_million_tokens[model]。
- 「X Pro」= 当前选中系列中价格最低的模型（Explore 子代理默认档）：
  resolve_cheapest_model(platform)。
- 缺省链：精确模型价 → 该平台最便宜模型价 → 旧估算表 _LEGACY_PRICE。
- 所有费用均为估算值，展示时必须附带误差提示（cost_disclaimer）。
- 余额查询：支持查询各平台 API 剩余余额/额度
"""

import os
import json
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Union
import urllib.request
import urllib.error


# ── 旧估算表（models.json 无价格数据时的最后回退，近似值）──
_LEGACY_PRICE: Dict[str, Dict[str, float]] = {
    "deepseek":  {"input": 0.27, "output": 1.10},
    "openai":    {"input": 0.50, "output": 1.50},
    "anthropic": {"input": 3.00, "output": 15.00},
    "custom":    {"input": 0.50, "output": 1.50},
}

# ── 平台余额查询配置 ──
_BALANCE_CONFIG: Dict[str, Dict] = {
    "openai": {
        "url": "https://api.openai.com/v1/dashboard/billing/credit_grants",
        "method": "GET",
        "auth_type": "bearer",
        "response_parser": "_parse_openai_balance",
    },
    "deepseek": {
        "url": "https://api.deepseek.com/user/balance",
        "method": "GET",
        "auth_type": "bearer",
        "response_parser": "_parse_deepseek_balance",
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/credits",
        "method": "GET",
        "auth_type": "x-api-key",
        "response_parser": "_parse_anthropic_balance",
    },
    "google": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models",
        "method": "GET",
        "auth_type": "query",
        "auth_param": "key",
        "response_parser": "_parse_google_balance",
    },
}


def _parse_openai_balance(data: dict) -> Tuple[float, str]:
    """解析 OpenAI 余额响应"""
    try:
        available = data.get("total_available", 0.0)
        if isinstance(available, (int, float)):
            return float(available), "USD"
        grants = data.get("grants", {})
        if isinstance(grants, dict):
            available = grants.get("total_available", 0.0)
            return float(available), "USD"
    except Exception:
        pass
    return 0.0, "USD"


def _parse_deepseek_balance(data: dict) -> Tuple[float, str]:
    """解析 DeepSeek 余额响应。

    官方格式（api-docs.deepseek.com/api/get-user-balance）：
    {"is_available": bool,
     "balance_infos": [{"currency": "CNY|USD",
                        "total_balance": "110.00",   ← 字符串
                        "granted_balance": "10.00",
                        "topped_up_balance": "100.00"}]}

    旧实现读 data["balance"]（字段不存在）→ 恒返回 0.00，显示「💰 0.00 CNY」。
    多币种时取余额最大的条目（通常即主币种）；兼容旧/第三方网关的 balance 字段。
    """
    try:
        infos = data.get("balance_infos") or []
        best = None
        for info in infos:
            if not isinstance(info, dict):
                continue
            try:
                total = float(str(info.get("total_balance", "0")))
            except (TypeError, ValueError):
                total = 0.0
            currency = str(info.get("currency", "CNY"))
            if best is None or total > best[0]:
                best = (total, currency)
        if best and best[0] > 0:
            return best
        # 兼容旧/第三方网关：直接 balance 字段
        legacy = data.get("balance", 0.0)
        if isinstance(legacy, (int, float)) and legacy:
            return float(legacy), str(data.get("currency", "CNY"))
    except Exception:
        pass
    return 0.0, "CNY"


def _parse_anthropic_balance(data: dict) -> Tuple[float, str]:
    """解析 Anthropic 余额响应"""
    try:
        remaining = data.get("remaining_credits", data.get("total_credits", 0.0))
        if isinstance(remaining, (int, float)):
            return float(remaining), "USD"
    except Exception:
        pass
    return 0.0, "USD"


def _parse_google_balance(data: dict) -> Tuple[float, str]:
    """解析 Google 余额响应（通过配额方式）"""
    try:
        if "error" in data:
            error_msg = data.get("error", {}).get("message", "")
            if "quota" in error_msg.lower() or "exhausted" in error_msg.lower():
                return 0.0, "USD"
        if "models" in data:
            return -1.0, "USD"  # -1 表示"可用但未返回具体数值"
    except Exception:
        pass
    return 0.0, "USD"


def get_balance(platform: str, api_key: str) -> Tuple[float, str, str]:
    """
    查询指定平台的 API 余额
    
    参数：
    - platform: 平台名称 (openai, deepseek, anthropic, google)
    - api_key: API 密钥
    
    返回：
    - (余额, 货币单位, 状态信息)
    - 状态信息: "success", "no_key", "network_error", "parse_error", "quota_unavailable"
    """
    if not api_key or not api_key.strip():
        return 0.0, "", "no_key"
    
    config = _BALANCE_CONFIG.get(platform.lower())
    if not config:
        return 0.0, "", "unsupported_platform"
    
    url = config.get("url", "")
    method = config.get("method", "GET")
    auth_type = config.get("auth_type", "bearer")
    parser_name = config.get("response_parser", "")
    
    if not url or not parser_name:
        return 0.0, "", "config_error"
    
    # 构建请求
    headers = {}
    if auth_type == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    elif auth_type == "x-api-key":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif auth_type == "query":
        auth_param = config.get("auth_param", "key")
        if "?" in url:
            url = f"{url}&{auth_param}={api_key}"
        else:
            url = f"{url}?{auth_param}={api_key}"
    
    headers["Content-Type"] = "application/json"
    
    try:
        req = urllib.request.Request(url, method=method, headers=headers)
        timeout = 10
        with urllib.request.urlopen(req, timeout=timeout) as response:
            response_data = response.read().decode("utf-8")
            data = json.loads(response_data)
            
            parser_func = globals().get(parser_name)
            if parser_func and callable(parser_func):
                balance, currency = parser_func(data)
                if balance == -1:
                    return balance, currency, "quota_unavailable"
                return balance, currency, "success"
            else:
                return 0.0, "", "parse_error"
                
    except urllib.error.HTTPError as e:
        error_msg = ""
        try:
            error_data = json.loads(e.read().decode("utf-8"))
            error_msg = error_data.get("error", {}).get("message", str(e))
        except Exception:
            error_msg = str(e)
        return 0.0, "", f"http_error:{error_msg}"
    except urllib.error.URLError as e:
        return 0.0, "", f"network_error:{str(e)}"
    except json.JSONDecodeError as e:
        return 0.0, "", f"parse_error:{str(e)}"
    except Exception as e:
        return 0.0, "", f"unknown_error:{str(e)}"


def get_all_balances(api_keys: Dict[str, str]) -> Dict[str, Dict]:
    """
    查询所有配置了 API Key 的平台余额
    
    参数：
    - api_keys: {platform: api_key} 字典
    
    返回：
    - {platform: {balance, currency, status, display}}
    """
    results = {}
    for platform, api_key in api_keys.items():
        if not api_key or not api_key.strip():
            results[platform] = {
                "balance": 0.0,
                "currency": "",
                "status": "no_key",
                "display": "未配置 API Key"
            }
            continue
        
        balance, currency, status = get_balance(platform, api_key)
        
        if status == "success":
            if balance == -1:
                display = "✅ 额度可用（具体余额请查看控制台）"
            else:
                display = f"💰 {balance:.2f} {currency}"
        elif status == "no_key":
            display = "❌ 未配置 API Key"
        elif status.startswith("http_error:"):
            error_detail = status.split(":", 1)[1] if ":" in status else "HTTP 错误"
            display = f"⚠️ {error_detail[:50]}"
        elif status.startswith("network_error:"):
            display = "⚠️ 网络连接失败"
        elif status == "quota_unavailable":
            display = "✅ 额度可用（具体余额请查看控制台）"
        else:
            display = f"⚠️ 查询失败: {status}"
        
        results[platform] = {
            "balance": balance,
            "currency": currency,
            "status": status,
            "display": display
        }
    
    return results


def _load_current_api_keys() -> Dict[str, str]:
    """从 key.conf 加载当前平台的 API Key"""
    try:
        from .config import load_key_conf
        conf = load_key_conf()
        if not conf:
            return {}
        platform = conf.get("platform", "deepseek")
        api_key = conf.get("api_key", "")
        if api_key:
            return {platform: api_key}
    except Exception:
        pass
    return {}


def get_balance_report(lang: str = "chinese") -> str:
    """
    获取格式化的余额报告（供外部调用）
    
    参数：
    - lang: "chinese" 或 "english"
    
    返回：
    - 格式化的余额报告文本，如果无 API Key 则返回空字符串
    """
    api_keys = _load_current_api_keys()
    if not api_keys:
        return ""
    
    results = get_all_balances(api_keys)
    if not results:
        return ""
    
    return format_balance_report(results, lang)


def format_balance_report(results: Dict[str, Dict], lang: str = "chinese") -> str:
    """
    格式化余额报告
    
    参数：
    - results: get_all_balances 的返回结果
    - lang: "chinese" 或 "english"
    
    返回：
    - 格式化的文本报告
    """
    if not results:
        if lang == "english":
            return "No platform configured."
        return "未配置任何平台。"
    
    lines = []
    if lang == "english":
        lines.append("=" * 50)
        lines.append("API Balance Report")
        lines.append("=" * 50)
    else:
        lines.append("=" * 50)
        lines.append("API 余额报告")
        lines.append("=" * 50)
    
    for platform, info in results.items():
        platform_display = platform.upper()
        display_text = info.get("display", "未知状态")
        lines.append(f"{platform_display:12} {display_text}")
    
    if lang == "english":
        lines.append("=" * 50)
        lines.append("Note: Some platforms may not provide precise balance via API.")
        lines.append("      Please check the console for accurate figures.")
    else:
        lines.append("=" * 50)
        lines.append("注意：部分平台可能不通过 API 返回精确余额。")
        lines.append("      请前往控制台查看准确数值。")
    
    return "\n".join(lines)


def get_balance_summary(results: Dict[str, Dict], lang: str = "chinese") -> str:
    """
    获取简短的余额摘要（用于快速查看）
    
    返回：
    - 单行摘要文本
    """
    if not results:
        if lang == "english":
            return "No platform configured"
        return "未配置任何平台"
    
    parts = []
    for platform, info in results.items():
        status = info.get("status", "unknown")
        display = info.get("display", "")
        if status == "success":
            parts.append(f"{platform.upper()}: {display}")
        elif status == "no_key":
            parts.append(f"{platform.upper()}: ⚠️ No key")
        elif status == "quota_unavailable":
            parts.append(f"{platform.upper()}: ✅ Available")
        else:
            parts.append(f"{platform.upper()}: ⚠️ Error")
    
    return " | ".join(parts)


def _platform_prices(platform: str) -> Dict[str, Dict[str, float]]:
    """读取 models.json 中某平台的价格表（USD / 1M tokens）。"""
    try:
        from .config import _SUPPORTED_PLATFORMS
        info = _SUPPORTED_PLATFORMS.get(platform or "", {})
        prices = info.get("price_per_million_tokens")
        if isinstance(prices, dict):
            return prices
    except Exception:
        pass
    return {}


def resolve_model_price(platform: str, model: str) -> Tuple[float, float]:
    """
    解析模型单价 (input, output) USD / 1M tokens。

    缺省链：精确模型价 → 平台最便宜模型价 → 旧估算表。
    """
    prices = _platform_prices(platform)
    if model and model in prices:
        p = prices[model]
        return float(p.get("input", 0.0)), float(p.get("output", 0.0))
    if prices:
        try:
            best = min(
                prices.items(),
                key=lambda kv: float(kv[1].get("input", 0.0)) + float(kv[1].get("output", 0.0)),
            )
            p = best[1]
            return float(p.get("input", 0.0)), float(p.get("output", 0.0))
        except Exception:
            pass
    est = _LEGACY_PRICE.get(platform or "", _LEGACY_PRICE["custom"])
    return float(est["input"]), float(est["output"])


def resolve_cheapest_model(platform: str) -> str:
    """
    当前选中系列中价格最低的模型 —— 「X Pro」= 最低价 AI（Explore 子代理默认档）。

    有价格表：按 input+output 单价排序取最低；无价格表：取模型列表第一个。
    """
    try:
        from .config import _SUPPORTED_PLATFORMS
        info = _SUPPORTED_PLATFORMS.get(platform or "", {})
        models = info.get("models") or []
        prices = info.get("price_per_million_tokens") or {}
        if prices and models:
            def _total(m: str) -> float:
                p = prices.get(m)
                if not p:
                    return float("inf")
                return float(p.get("input", 0.0)) + float(p.get("output", 0.0))
            ranked = sorted(models, key=_total)
            if ranked and _total(ranked[0]) != float("inf"):
                return ranked[0]
        if models:
            return models[0]
        return info.get("default_model", "") or ""
    except Exception:
        return ""


def resolve_default_model(platform: str) -> str:
    """
    当前平台配置的默认模型（与主 AI 同款）——子代理默认档。

    无 default_model 时返回空串，调用方回退到 resolve_cheapest_model。
    """
    try:
        from .config import _SUPPORTED_PLATFORMS
        info = _SUPPORTED_PLATFORMS.get(platform or "", {})
        return info.get("default_model", "") or ""
    except Exception:
        return ""


def resolve_smarter_model(platform: str, base_model: str = "") -> str:
    """
    同系列中比 base_model 更聪明的模型（按单价取更高档，取最近一档）。

    规划子代理（plan）默认档：主 AI 用 flash 时 → 返回 pro；已是最高档或
    无价格表时返回 base_model 本身，保证调用方拿到的永远是有效模型名。
    """
    try:
        from .config import _SUPPORTED_PLATFORMS
        info = _SUPPORTED_PLATFORMS.get(platform or "", {})
        models = info.get("models") or []
        prices = info.get("price_per_million_tokens") or {}
        if not models or not base_model:
            return base_model or ""
        if not prices:
            try:
                idx = models.index(base_model)
                if idx + 1 < len(models):
                    return models[idx + 1]
            except ValueError:
                pass
            return base_model
        def _total(m: str) -> float:
            p = prices.get(m)
            if not p:
                return float("inf")
            return float(p.get("input", 0.0)) + float(p.get("output", 0.0))
        base_total = _total(base_model)
        if base_total == float("inf"):
            return base_model
        candidates = sorted([m for m in models if _total(m) > base_total], key=_total)
        return candidates[0] if candidates else base_model
    except Exception:
        return base_model


def resolve_best_model(platform: str) -> str:
    """
    当前系列中价格最高（最贵）的模型 —— plus 思考流水线默认档。

    有价格表：按 input+output 单价排序取最高；无价格表：取模型列表最后一个。
    失败返回空串，调用方回退到默认模型。
    """
    try:
        from .config import _SUPPORTED_PLATFORMS
        info = _SUPPORTED_PLATFORMS.get(platform or "", {})
        models = info.get("models") or []
        prices = info.get("price_per_million_tokens") or {}
        if prices and models:
            def _total(m: str) -> float:
                p = prices.get(m)
                if not p:
                    return float("-inf")
                return float(p.get("input", 0.0)) + float(p.get("output", 0.0))
            ranked = sorted(models, key=_total, reverse=True)
            if ranked and _total(ranked[0]) != float("-inf"):
                return ranked[0]
        if models:
            return models[-1]
        return info.get("default_model", "") or ""
    except Exception:
        return ""


def estimate_cost(platform: str, model: str,
                  prompt_tokens: int, completion_tokens: int) -> float:
    """按解析单价估算 USD 费用。"""
    pin, pout = resolve_model_price(platform, model)
    return (prompt_tokens / 1_000_000 * pin +
            completion_tokens / 1_000_000 * pout)


# append_cost_record 并发写保护
_COST_APPEND_LOCK = threading.Lock()


def append_cost_record(mem_root: str, platform: str, model: str,
                       prompt_tokens: int, completion_tokens: int) -> None:
    """
    追加一条费用记录到 <mem_root>/.ai_s/cost.json。
    与 ai_interactive 原 _append_cost_record 行为一致（5000 条上限），
    但单价改为 models.json 动态解析。
    """
    with _COST_APPEND_LOCK:
        try:
            cost_path = os.path.join(mem_root, ".ai_s", "cost.json")
            cost = estimate_cost(platform, model, prompt_tokens, completion_tokens)
            record = {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "platform": platform,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": round(cost, 6),
            }
            data: List[dict] = []
            if os.path.exists(cost_path):
                try:
                    with open(cost_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, list):
                        data = loaded
                except Exception:
                    data = []
            data.append(record)
            data = data[-5000:]
            os.makedirs(os.path.dirname(cost_path), exist_ok=True)
            with open(cost_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def cost_disclaimer(lang: str = "chinese") -> str:
    """
    费用估算误差提示（/cost 面板与子代理提示共用）。
    
    【新增】如果配置了 API Key，会在免责声明后附加余额信息。
    """
    # 获取余额报告
    balance_report = get_balance_report(lang)
    
    if lang == "english":
        base = ("Cost is an estimate based on the models.json price table; "
                "the actual bill may differ slightly due to cache hits and price changes.")
        if balance_report:
            return base + "\n\n" + balance_report
        return base
    else:
        base = ("费用为估算值（基于 models.json 单价表），实际账单可能因缓存命中、"
                "价格调整等因素存在误差。")
        if balance_report:
            return base + "\n\n" + balance_report
        return base