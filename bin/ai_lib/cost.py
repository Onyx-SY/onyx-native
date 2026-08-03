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
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# ── 旧估算表（models.json 无价格数据时的最后回退，近似值）──
_LEGACY_PRICE: Dict[str, Dict[str, float]] = {
    "deepseek":  {"input": 0.27, "output": 1.10},
    "openai":    {"input": 0.50, "output": 1.50},
    "anthropic": {"input": 3.00, "output": 15.00},
    "custom":    {"input": 0.50, "output": 1.50},
}


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


def estimate_cost(platform: str, model: str,
                  prompt_tokens: int, completion_tokens: int) -> float:
    """按解析单价估算 USD 费用。"""
    pin, pout = resolve_model_price(platform, model)
    return (prompt_tokens / 1_000_000 * pin +
            completion_tokens / 1_000_000 * pout)


def append_cost_record(mem_root: str, platform: str, model: str,
                       prompt_tokens: int, completion_tokens: int) -> None:
    """
    追加一条费用记录到 <mem_root>/.ai_s/cost.json。
    与 ai_interactive 原 _append_cost_record 行为一致（5000 条上限），
    但单价改为 models.json 动态解析。
    """
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
    """费用估算误差提示（/cost 面板与子代理提示共用）。"""
    if lang == "english":
        return ("Cost is an estimate based on the models.json price table; "
                "the actual bill may differ slightly due to cache hits and price changes.")
    return ("费用为估算值（基于 models.json 单价表），实际账单可能因缓存命中、"
            "价格调整等因素存在误差。")
