# -*- coding: utf-8 -*-
"""
web_search.py — web_search 网络调研工具（多重混合搜索：多查询 × 多引擎，去重/过滤/抓页）

从 bin/ai_cmd.py 拆分（模块化架构重构）：
- 纯函数 + 局部 import，无 ai_cmd 闭包依赖；
- _SUPPORTED_PLATFORMS 从 .config 导入（弱 AI 摘要选廉价模型用）；
- 缓存 /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test 引擎健康窗口均为本模块私有状态（进程内）。
"""

import os
import re
import json
import time
import threading
from typing import List, Tuple, Optional, Dict, Any, Callable


from .config import _SUPPORTED_PLATFORMS


def _is_private_ip(ip) -> bool:
    """判断 IP 是否为内网/回环/链路本地/保留地址（SSRF 防护）。"""
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _ssrf_block_reason(url: str) -> Optional[str]:
    """2026-09 加固（M3）：检查 URL 是否指向内网/保留地址。

    返回拒绝原因字符串；放行返回 None。域名会解析全部 A 记录，
    任一记录指向内网即拒绝（防 DNS rebinding 的常见变体）。
    """
    from urllib.parse import urlparse as _urlparse
    import ipaddress as _ipaddr
    import socket as _sock
    try:
        _u = _urlparse(url)
        if _u.scheme not in ("http", "https"):
            return "仅支持 http/https 协议"
        _host = _u.hostname
        if not _host:
            return "URL 缺少主机名"
        try:
            _ip = _ipaddr.ip_address(_host)
        except ValueError:
            try:
                _infos = _sock.getaddrinfo(_host, None)
            except Exception:
                return f"无法解析主机: {_host}"
            for _info in _infos:
                _ip_str = _info[4][0].split("%")[0]
                try:
                    _ip = _ipaddr.ip_address(_ip_str)
                except ValueError:
                    continue
                if _is_private_ip(_ip):
                    return f"域名 {_host} 解析到内网地址 {_ip_str}"
            return None
        if _is_private_ip(_ip):
            return f"目标地址是内网/保留地址: {_host}"
        return None
    except Exception as _e:
        return f"URL 校验失败: {_e}"


def _detect_html_charset(raw: bytes) -> Optional[str]:
    """从 HTML 头部提取字符集声明（返回 charset 字符串，找不到返回 None）。

    识别两种写法：
      <meta charset="gbk">
      <meta http-equiv="Content-Type" content="text/html; charset=gbk">
    """
    if not raw:
        return None
    try:
        _head_s = raw[:4096].decode("latin-1", errors="ignore")
    except Exception:
        return None
    # 1) <meta charset="xxx">
    _m = re.search(r'<meta[^>]+charset\s*=\s*["\']?\s*([A-Za-z0-9._-]+)', _head_s, re.IGNORECASE)
    if _m:
        return _m.group(1).strip().strip('"\'')
    # 2) <meta http-equiv="Content-Type" content="...; charset=xxx">
    _m = re.search(r'<meta[^>]+http-equiv\s*=\s*["\']?content-type["\']?[^>]*>', _head_s, re.IGNORECASE)
    if _m:
        _m2 = re.search(r'charset\s*=\s*([A-Za-z0-9._-]+)', _m.group(0), re.IGNORECASE)
        if _m2:
            return _m2.group(1).strip()
    return None


def _decode_html_bytes(raw: bytes, hint_encoding: Optional[str] = None) -> str:
    """多级编码解码：HTTP 头 hint → <meta charset> → 探测库 → 常见编码兜底。

    解决 GBK/GB2312 等老网页在无 charset 响应头时 requests `.text` 误判
    ISO-8859-1 导致乱码的问题。
    """
    if not raw:
        return ""
    _cands: List[str] = []
    if hint_encoding and str(hint_encoding).lower() not in ("iso-8859-1", "latin-1", "ascii", "utf-8", "utf8"):
        _cands.append(str(hint_encoding))
    _meta = _detect_html_charset(raw)
    if _meta and _meta.lower() not in (_.lower() for _ in _cands):
        _cands.append(_meta)
    # 探测库（requests 依赖 charset_normalizer，优先；chardet 兜底）
    for _mod in ("charset_normalizer", "chardet"):
        try:
            if _mod == "charset_normalizer":
                import charset_normalizer as _cn
                _guess = _cn.from_bytes(raw).best()
                _enc = _guess.encoding if _guess else None
            else:
                import chardet as _cd
                _enc = _cd.detect(raw).get("encoding")
            if _enc and str(_enc).lower() not in (_.lower() for _ in _cands):
                _cands.append(str(_enc))
        except Exception:
            continue
    # 常见编码兜底（gb18030 是 GBK/GB2312 超集；big5/shift_jis 覆盖港台日韩老站）
    _cands += ["utf-8", "gb18030", "big5", "shift_jis", "latin-1"]
    _seen: set = set()
    for _enc in _cands:
        _key = str(_enc).lower().replace("_", "-")
        if _key in _seen:
            continue
        _seen.add(_key)
        try:
            return _sanitize_text(raw.decode(_enc))
        except (UnicodeDecodeError, LookupError):
            continue
    return _sanitize_text(raw.decode("utf-8", errors="replace"))


def _sanitize_text(text: str, unescape_html: bool = False) -> str:
    """清洗抓取文本：特殊空白→普通空格；零宽/控制字符→删除；可选 HTML 实体转换。

    覆盖：
      - 特殊空白：U+00A0(nbsp) U+1680 U+2000-U+200A(含 ensp U+2002 / emsp U+2003)
                  U+202F(窄不换行) U+205F(中数学) U+3000(全角空格)
      - 零宽字符：U+200B U+200C U+200D U+2060 U+FEFF(BOM)
      - ANSI CSI（ESC [ ... 终结字母）
      - ESC 引导序列：DCS(ESC P) SS2(ESC N) SS3(ESC O) SOS(ESC X) PM(ESC ^) APC(ESC _) OSC(ESC ])
      - 孤立 C0/C1 控制字符（保留 \\t \\n \\r）
      - unescape_html=True 时：HTML 实体字面量（&ensp; &amp; &lt; 等）→ 对应字符
    """
    if not text:
        return text
    # 0) HTML 实体字面量 → 对应字符（先解实体，后续特殊空白/控制字符清洗才覆盖到 &ensp; 等）
    if unescape_html:
        import html as _htm
        text = _htm.unescape(text)
    # 1) 特殊空白 → 普通空格
    text = re.sub(r"[\u00A0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200A\u202F\u205F\u3000]", " ", text)
    # 2) 零宽字符 → 删除
    text = re.sub(r"[\u200B\u200C\u200D\u2060\uFEFF]", "", text)
    # 3) ANSI CSI（ESC [ 参数 终结字母）→ 删除
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    # 4) ESC 引导序列：长序列（DCS=ESC P / SOS=ESC X / PM=ESC ^ / APC=ESC _ / OSC=ESC ]）
    #    删除到 ST（ESC \）或 BEL（\x07）；单字符换档（SS2=ESC N / SS3=ESC O）只删引导符本身
    text = re.sub(r"\x1b[PX^_\]][\s\S]*?(?:\x1b\\|\x07)", "", text)
    text = re.sub(r"\x1b[NO]", "", text)
    text = text.replace("\x1b", "")
    # 5) 孤立 C0/C1 控制字符（保留 \t \n \r）
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]", "", text)
    return text


# 真实浏览器 UA + 标准请求头工厂（合法爬虫：真实 UA、常规 Accept，
# 不绕过验证码/登录；配合时间预算控制速率）。
_WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _web_browser_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """生成标准浏览器请求头；extra 覆盖默认值。"""
    _h = {
        "User-Agent": _WEB_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra:
        _h.update(extra)
    return _h


def _http_get_text(url: str, timeout: int, headers: dict) -> Tuple[Optional[str], str]:
    """GET 并返回页面文本：requests 优先（跟随重定向），requests 缺失时回退 curl。

    返回 (text, "") 成功；(None, 错误信息) 失败。供搜索引擎页面与抓取共用，
    保证 requests 库不可用时 web_search 依然可用（需系统有 curl）。
    """
    try:
        import requests as _req
    except ImportError:
        pass
    else:
        try:
            _hdr = headers or _web_browser_headers()
            _resp = _req.get(url, timeout=timeout, headers=_hdr)
            if _resp.status_code >= 400:
                return None, f"HTTP {_resp.status_code}"
            return _decode_html_bytes(_resp.content, _resp.encoding), ""
        except Exception as _e:
            return None, f"requests 失败: {_e}"
    try:
        import subprocess as _sp
        _result = _sp.run(
            ["curl", "-sL", "--max-time", str(timeout),
             "-A", _WEB_UA,
             "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             "-H", "Accept-Encoding: gzip, deflate",
             url],
            capture_output=True, timeout=timeout + 5)
        if _result.stdout:
            return _decode_html_bytes(_result.stdout), ""
        return None, f"curl 返回空: {_result.stderr[:200]}"
    except Exception as _e:
        return None, f"curl 失败: {_e}"


# ═══════════════════════════════════════════════════════════════
# web_search — 多重混合搜索（多查询 × 多引擎，去重/过滤/抓页）
# ═══════════════════════════════════════════════════════════════

_WEB_ENGINES: Tuple[str, ...] = ("duckduckgo", "bing")

# web_search 并行度：搜索阶段（查询 × 引擎任务池）与抓取阶段（页面池）。
# 串行实现总耗时 = 任务数 × 单请求；并行后 ≈ 最慢单个请求（弱 AI 摘要随抓取并行）。
_WEB_SEARCH_WORKERS = 6
_WEB_FETCH_WORKERS = 4

# 阶段总时间预算（秒）：到点收工，不等最慢任务（超时任务标注后跳过）。
_WEB_SEARCH_BUDGET = 12.0
_WEB_FETCH_BUDGET = 20.0

# topics 批量主题：单次调用并行查询多个独立主题（每主题独立搜索+抓页+分栏输出）
_WEB_TOPICS_MAX = 5
_WEB_TOPICS_WORKERS = 5
_WEB_TOPIC_OVERRIDES = ("query", "engines", "max_results", "fetch_pages", "fetch_limit",
                        "max_chars_per_page", "ai_assist", "allowed_domains", "exclude_domains",
                        "language", "region", "time_range", "safe_search", "output_format",
                        "timeout")

# ── 查询增强（无外部依赖的精度提升）──
# 英文长句去停用词生成关键词变体；中文含 ASCII 技术词时附加英文变体（技术文档英文更全）。
_WEB_STOPWORDS_EN = frozenset({
    "how", "to", "the", "a", "an", "is", "are", "was", "were", "what", "which", "why",
    "when", "where", "who", "do", "does", "did", "for", "with", "of", "in", "on",
    "at", "and", "or", "vs", "versus", "use", "using", "can", "should", "best",
})

# ── 结果重排信号（软加权，不埋没好结果）──
_WEB_AUTHORITY_DOMAINS = frozenset({
    "github.com", "gitlab.com", "readthedocs.io", "w3.org", "developer.mozilla.org",
    "python.org", "nodejs.org", "nginx.org", "apache.org", "kubernetes.io", "docker.com",
    "react.dev", "vuejs.org", "stackoverflow.com", "docs.python.org", "pypi.org",
    "npmjs.com", "crates.io", "docs.rs", "openai.com", "anthropic.com", "microsoft.com",
    "google.com", "apple.com", "ibm.com", "oracle.com", "cloudflare.com",
    "deepseek.com", "aliyun.com", "tencent.com", "baidu.com", "bytedance.com",
    "aws.amazon.com", "azure.microsoft.com", "developer.android.com",
    "zhihu.com", "segmentfault.com", "juejin.cn", "ruanyifeng.com", "cnblogs.com",
})
_WEB_JUNK_DOMAIN_HINTS = ("top10", "top-10", "best10", "rank", "coupon", "deals",
                          "vip", "free-", "-free", "download", "list")
_WEB_JUNK_TITLE_HINTS = ("top 10", "top10", "best 10", "coupon", "discount",
                         "免费下载", "优惠券", "福利")

# ── SERP 结果缓存（进程内 LRU）：同键查询 15 分钟内直接命中，零网络请求；
#    失败结果缓存 30 秒（防抖，避免反复打刚挂掉的引擎）。 ──
_WEB_CACHE_TTL = 900
_WEB_CACHE_FAIL_TTL = 30
_WEB_CACHE_MAX = 200
_WEB_CACHE: Dict[str, Tuple[float, float, Tuple[List[Dict], str]]] = {}  # key -> (ts, ttl, (items, err))
_WEB_CACHE_LOCK = threading.Lock()

# ── 引擎健康滑动窗口：连续 _ENGINE_DEGRADE_AFTER 次失败 → 降级 _ENGINE_DEGRADE_SECONDS
#    （降级期跳过请求；到期自动恢复试探）。 ──
_ENGINE_STATS_WINDOW = 10
_ENGINE_DEGRADE_AFTER = 3
_ENGINE_DEGRADE_SECONDS = 1800
_WEB_ENGINE_STATS: Dict[str, List[Tuple[float, bool]]] = {}   # engine -> [(ts, ok), ...]
_WEB_ENGINE_DEGRADED_UNTIL: Dict[str, float] = {}
_WEB_ENGINE_LOCK = threading.Lock()


def _ddg_url_normalize(href: str) -> str:
    """DuckDuckGo HTML 结果链接是 /l/?uddg=<目标> 跳转，解出真实 URL。"""
    if "uddg=" in href:
        from urllib.parse import unquote as _unquote
        m = re.search(r"[?&]uddg=([^&]+)", href)
        if m:
            return _unquote(m.group(1))
    if href.startswith("//"):
        href = "https:" + href
    return href


def _extract_ddg_results(html: str) -> List[Dict]:
    """解析 DuckDuckGo HTML 结果页（result__a 链接 + result__snippet 摘要）。"""
    import html as _htm
    results = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                         html, re.DOTALL):
        title = _sanitize_text(re.sub(r"<[^>]+>", "", m.group(2)).strip(), unescape_html=True)
        if title:
            results.append({"title": title, "url": _ddg_url_normalize(m.group(1)), "snippet": ""})
    snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
    for i, s in enumerate(snips[: len(results)]):
        results[i]["snippet"] = _sanitize_text(re.sub(r"<[^>]+>", "", s).strip(), unescape_html=True)
    return results


def _extract_lite_results(html: str) -> List[Dict]:
    """解析 DuckDuckGo lite 端点结果页（rel=nofollow 链接 + result-snippet 摘要）。"""
    import html as _htm
    results = []
    for m in re.finditer(r'<a[^>]*rel="nofollow"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                         html, re.DOTALL):
        href = m.group(1)
        if href.startswith("javascript") or "lite.duckduckgo.com" in href:
            continue
        title = _sanitize_text(re.sub(r"<[^>]+>", "", m.group(2)).strip(), unescape_html=True)
        if title:
            results.append({"title": title, "url": _ddg_url_normalize(href), "snippet": ""})
    snips = re.findall(r'class="result-snippet"[^>]*>(.*?)</td>', html, re.DOTALL)
    for i, s in enumerate(snips[: len(results)]):
        results[i]["snippet"] = _sanitize_text(re.sub(r"<[^>]+>", "", s).strip(), unescape_html=True)
    return results


def _extract_bing_results(html: str) -> List[Dict]:
    """解析 Bing 结果页（li.b_algo 内 h2>a 链接 + p 摘要）。"""
    import html as _htm
    results = []
    for m in re.finditer(r'<li class="b_algo".*?</li>', html, re.DOTALL):
        block = m.group(0)
        am = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not am:
            continue
        url = am.group(1)
        title = _sanitize_text(re.sub(r"<[^>]+>", "", am.group(2)).strip(), unescape_html=True)
        pm = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)
        snippet = _sanitize_text(re.sub(r"<[^>]+>", "", pm.group(1)).strip(), unescape_html=True) if pm else ""
        if url:
            results.append({"title": title, "url": url, "snippet": snippet})
    return results


# ── Bing 结果跑偏检测（服务端对部分中文查询会退化返回单字字典页）──
# 实测：Accept-Language: zh-CN 下，bing 对"第五人格 加页手记 上线时间"等查询
# 会只匹配查询首字，整页返回"第"/"加"的字典条目；同查询 en-US 头则正常。
_WEB_BING_DICT_HINTS = re.compile(
    r"(zidian|hanyuguoxue|hgcha|chagushici|gushici\.net|ufanv\.cn|koolearn\.com/dict|"
    r"dict\.cn|iciba\.com|dictionary\.cambridge|wiktionary|kotobank|strokeorder\.cc|"
    r"dict\.revised\.moe\.edu)", re.IGNORECASE)


def _web_bing_results_junk(results: List[Dict], query: str) -> bool:
    """判断 Bing SERP 是否跑偏（返回单字字典页等与查询无关的垃圾）。

    判定依据（都要求查询含中文词）：
      - 结果中"单字字典/词典站"（URL 命中字典站特征且标题 ≤14 字）占比 ≥ 1/2；
      - 或"低相关"占比 ≥ 3/5：标题/摘要不含任何查询词、URL 不含 ≥4 字核心词。
    保守设计：宁可不判、不做额外请求，也不误杀正常结果。
    """
    if not results or not query:
        return False
    _cjk_words = [w for w in re.split(r"[^\w]+", query)
                  if re.search(r"[\u4e00-\u9fff]", w)]
    if not _cjk_words:
        return False
    _core = [w for w in _cjk_words if len(w) >= 4]
    _dict_hits = 0
    _low_hits = 0
    for _r in results:
        _title = (_r.get("title") or "").strip()
        _url = (_r.get("url") or "").lower()
        _snip = _r.get("snippet") or ""
        if len(_title) <= 14 and _WEB_BING_DICT_HINTS.search(_url):
            _dict_hits += 1
        _has_word = any(w in _title or w in _snip for w in _cjk_words)
        if not _has_word and not (_core and any(w.lower() in _url for w in _core)):
            _low_hits += 1
    _n = len(results)
    if _n and _dict_hits * 2 >= _n:
        return True
    if _n and _low_hits * 5 >= _n * 3:
        return True
    return False


def _web_parallel(fn, tasks: list, workers: int, budget: float = 0.0) -> list:
    """按输入顺序并行执行 fn(task)（线程池），返回与 tasks 同序的结果列表。

    - 任务 ≤ 1 时直接串行（避免无谓线程开销）；
    - budget > 0：总时间预算（秒），到点收工——未完成任务在结果中占位 None；
    - 中断/异常时不阻塞等待未完成请求（残留线程随各自请求超时自然结束）。
    """
    if len(tasks) <= 1:
        return [fn(t) for t in tasks]
    import concurrent.futures as _cf
    _ex = _cf.ThreadPoolExecutor(max_workers=max(1, min(workers, len(tasks))))
    try:
        _futs = {_ex.submit(fn, t): i for i, t in enumerate(tasks)}
        _out = [None] * len(tasks)
        if budget <= 0:
            for _f, _i in _futs.items():
                _out[_i] = _f.result()
            return _out
        _deadline = time.time() + budget
        for _f in _cf.as_completed(_futs):
            if time.time() >= _deadline:
                break
            _i = _futs[_f]
            try:
                _out[_i] = _f.result()
            except Exception:
                _out[_i] = None
        return _out
    finally:
        _ex.shutdown(wait=False, cancel_futures=True)


def _web_cache_key(engine: str, query: str, region: str, lang: str,
                   time_range: str, safe: bool) -> str:
    """SERP 缓存键：引擎 + 规范化查询 + 偏好参数。"""
    _q = " ".join((query or "").lower().split())
    return "|".join([engine, _q, region, lang, time_range, "1" if safe else "0"])


def _web_cache_get(key: str) -> Optional[Tuple[List[Dict], str]]:
    """读 SERP 缓存：命中且未过期 → (items, err)；否则 None（过期条目清除）。"""
    with _WEB_CACHE_LOCK:
        _hit = _WEB_CACHE.get(key)
        if not _hit:
            return None
        _ts, _ttl, _payload = _hit
        if time.time() - _ts > _ttl:
            _WEB_CACHE.pop(key, None)
            return None
        _items, _err = _payload
        return ([dict(i) for i in _items], _err)


def _web_cache_put(key: str, items: List[Dict], err: str, ttl: float) -> None:
    """写 SERP 缓存（浅拷贝防外部修改污染）；超上限淘汰最旧条目。"""
    with _WEB_CACHE_LOCK:
        if len(_WEB_CACHE) >= _WEB_CACHE_MAX and key not in _WEB_CACHE:
            try:
                _old = min(_WEB_CACHE, key=lambda _k: _WEB_CACHE[_k][0])
                del _WEB_CACHE[_old]
            except Exception:
                pass
        _WEB_CACHE[key] = (time.time(), ttl, ([dict(i) for i in items], err))


def _web_engine_report(engine: str, ok: bool) -> None:
    """更新引擎健康滑动窗口；最近连续 _ENGINE_DEGRADE_AFTER 次失败 → 降级。"""
    with _WEB_ENGINE_LOCK:
        _now = time.time()
        _win = _WEB_ENGINE_STATS.setdefault(engine, [])
        _win.append((_now, ok))
        while len(_win) > _ENGINE_STATS_WINDOW:
            _win.pop(0)
        _tail = _win[-_ENGINE_DEGRADE_AFTER:]
        if len(_tail) >= _ENGINE_DEGRADE_AFTER and not any(_o for _, _o in _tail):
            _WEB_ENGINE_DEGRADED_UNTIL[engine] = _now + _ENGINE_DEGRADE_SECONDS


def _web_engine_degraded(engine: str) -> bool:
    """引擎是否处于降级期（调用方跳过其请求）；过期自动恢复。"""
    with _WEB_ENGINE_LOCK:
        _until = _WEB_ENGINE_DEGRADED_UNTIL.get(engine, 0.0)
        if time.time() >= _until:
            _WEB_ENGINE_DEGRADED_UNTIL.pop(engine, None)
            return False
        return True


def _web_result_relevant(query: str, title: str, url: str, snippet: str = "") -> bool:
    """抓页前相关性预筛（保守：只拦「明显不相关」的 SERP 结果）。

    - snippet 是引擎按查询返回的摘要，最可靠信号：命中查询词即相关；
    - 标题命中查询词 → 相关；URL 含核心词（≥4 字符）→ 相关；
    - 标题过短且无摘要（如纯编号/单字母）→ 信息不足，宁抓勿漏；
    - 仅当标题、摘要、URL 均无任何查询词痕迹时判为低相关（跳过自动抓取；
      不影响手动指定 urls）。中文等无空白语言整句参与匹配。
    """
    if not query:
        return True
    _words = [w for w in re.split(r"[^\w]+", query.lower()) if len(w) >= 2]
    if not _words:
        return True
    _title = (title or "").lower().strip()
    _snip = (snippet or "").lower()
    if any(w in _snip for w in _words):
        return True
    if any(w in _title for w in _words):
        return True
    _core = [w for w in _words if len(w) >= 4]
    if _core and any(w in (url or "").lower() for w in _core):
        return True
    if len(_title) < 8 and not _snip:
        return True
    return False


def _web_query_enhance(query: str) -> List[str]:
    """查询规范化与扩展：返回候选查询列表（原查询 + 变体，最多 3 个）。

    - 英文长句（≥4 词且停用词占比高）→ 去停用词的关键词短语变体；
    - 中文查询含 ASCII 技术词 → 附加纯英文变体（技术文档英文更全更准）；
    - 简单短查询不扩展（请求量不变）；变体去重、去空。
    """
    _q = (query or "").strip()
    if not _q:
        return []
    _cands = [_q]
    _ascii_words = [w for w in re.split(r"[^\w]+", _q)
                    if re.search(r"[A-Za-z0-9]", w)]
    # 1) 英文长句：去停用词生成关键词短语
    if len(_ascii_words) >= 4:
        _kept = [w for w in _ascii_words if w.lower() not in _WEB_STOPWORDS_EN]
        if 2 <= len(_kept) < len(_ascii_words):
            _cands.append(" ".join(_kept))
    # 2) 中文含 ASCII 技术词 → 英文变体
    _has_cjk = any("\u4e00" <= c <= "\u9fff" for c in _q)
    if _has_cjk and _ascii_words:
        _en = " ".join(w for w in _ascii_words if len(w) >= 2)[:100]
        if _en:
            _cands.append(_en)
    # 去重去空
    _seen: set = set()
    _out: List[str] = []
    for _c in _cands:
        _cc = re.sub(r"\s+", " ", _c).strip()
        if _cc and _cc.lower() not in _seen:
            _seen.add(_cc.lower())
            _out.append(_cc)
    return _out[:3]


def _web_rerank(results: List[Dict]) -> List[Dict]:
    """结果重排（按查询分组，组内打分降序；查询主序保留）。

    信号（软加权，稳定排序不埋没好结果）：
      - 标题命中查询词 +2/词、摘要命中 +1/词；
      - 权威域（官方文档/知名社区）+4；
      - SEO 垃圾域（top10/best/free 等）−3、垃圾标题词 −2。
    """
    from urllib.parse import urlparse as _urlparse
    _groups: Dict[str, List[Dict]] = {}
    _order: List[str] = []
    for _r in results:
        _q = _r.get("query", "")
        if _q not in _groups:
            _groups[_q] = []
            _order.append(_q)
        _groups[_q].append(_r)
    _out: List[Dict] = []
    for _q in _order:
        _words = [w for w in re.split(r"[^\w]+", _q.lower()) if len(w) >= 2]
        _scored = []
        for _r in _groups[_q]:
            _s = 0.0
            _title = (_r.get("title") or "").lower()
            _snip = (_r.get("snippet") or "").lower()
            _host = (_urlparse(_r.get("url", "")).netloc or "").lower().split(":")[0]
            for _w in _words:
                if _w in _title:
                    _s += 2.0
                elif _w in _snip:
                    _s += 1.0
            if any(_host == d or _host.endswith("." + d) for d in _WEB_AUTHORITY_DOMAINS):
                _s += 4.0
            elif any(_h in _host for _h in _WEB_JUNK_DOMAIN_HINTS):
                _s -= 3.0
            if any(_t in _title for _t in _WEB_JUNK_TITLE_HINTS):
                _s -= 2.0
            _scored.append((_s, _r))
        _scored.sort(key=lambda _x: -_x[0])  # 稳定排序：同分保持引擎原序
        _out.extend(_r for _s, _r in _scored)
    return _out


def _web_search_one(query: str, engine: str, timeout: int, region: str, lang: str,
                    time_range: str, safe: bool, max_results: int) -> Tuple[List[Dict], str]:
    """单查询 × 单引擎搜索：缓存优先，失败/空结果自动重试，健康上报。

    - 缓存：同键（引擎+规范化查询+偏好）15 分钟内直接返回上次结果，零网络请求；
      失败结果缓存 30 秒（防抖，避免反复打刚挂掉的引擎）。
    - DDG：html 端点请求成功但零结果/反爬空页时回退 lite 端点（更轻、更容忍 bot）；
      传输层失败（域名不可达/DNS 超时）不回退——同一域名的 lite 几乎必然同样不可达，
      避免双倍超时等待。检测 anomaly/challenge 拦截页并给出明确错误。
    - Bing：请求成功但零结果（空白/consent 页）重试一次；传输层失败不重试。
    - 健康上报：传输失败/反爬拦截计为引擎故障；正常响应（含零结果）计为可用，
      连续故障触发降级（见 _web_engine_degraded）。
    返回 (结果列表, 错误信息)；错误为空表示成功（结果可为空列表）。
    """
    _key = _web_cache_key(engine, query, region, lang, time_range, safe)
    _hit = _web_cache_get(_key)
    if _hit is not None:
        return _hit
    _items: List[Dict] = []
    _err = ""
    try:
        from urllib.parse import quote as _quote
        _headers = _web_browser_headers()
        if lang:
            _headers["Accept-Language"] = f"{lang};q=0.9,en;q=0.6"
        if engine == "duckduckgo":
            _headers["Referer"] = "https://duckduckgo.com/"
            _url = f"https://html.duckduckgo.com/html/?q={_quote(query)}"
            if region:
                _url += f"&kl={_quote(region)}"
            if lang:
                _url += f"&l={_quote(lang)}"
            if time_range in ("day", "week", "month", "year"):
                _url += f"&df={time_range[0]}"
            if safe:
                _url += "&kp=1"
            _html, _err = _http_get_text(_url, timeout, _headers)
            _items = _extract_ddg_results(_html) if _html is not None else []
            if not _items and _html is not None:
                # 请求成功但零结果/反爬空页 → 回退 lite 端点（更轻、更容忍 bot）；
                # 传输层失败不回退（同一域名几乎必然同样不可达，避免双倍超时）
                _lite = f"https://lite.duckduckgo.com/lite/?q={_quote(query)}"
                if region:
                    _lite += f"&kl={_quote(region)}"
                if safe:
                    _lite += "&kp=1"
                _html2, _err2 = _http_get_text(_lite, timeout, _headers)
                _items = _extract_lite_results(_html2) if _html2 is not None else []
            if not _items:
                if _html and ("anomaly" in _html.lower() or "challenge" in _html.lower()):
                    _err = f"duckduckgo:{query[:30]} → 反爬拦截（anomaly/challenge 页）"
                else:
                    _err = f"duckduckgo:{query[:30]} → {_err or _err2 or '无结果'}"
            else:
                _err = ""
        elif engine == "bing":
            # 构造 bing 请求（语言头可覆盖——跑偏纠错时用 en 头重试）
            def _bing_fetch(q: str, lg: str) -> Tuple[List[Dict], str]:
                _h = _web_browser_headers()
                if lg:
                    _h["Accept-Language"] = f"{lg};q=0.9,en;q=0.6"
                _u = f"https://www.bing.com/search?q={_quote(q)}"
                if lg:
                    _u += f"&setlang={_quote(lg)}"
                if region:
                    _u += f"&cc={_quote(region)}"
                if safe:
                    _u += "&adlt=strict"
                _html, _e1 = _http_get_text(_u, timeout, _h)
                _its = _extract_bing_results(_html) if _html is not None else []
                if not _its and _html is not None:
                    # 瞬时失败/空白/consent 页 → 重试一次；传输层失败不重试
                    _html2, _e2 = _http_get_text(_u, timeout, _h)
                    _its = _extract_bing_results(_html2) if _html2 is not None else []
                    if _e2 and not _e1:
                        _e1 = _e2
                return _its, _e1

            _items, _err = _bing_fetch(query, lang)
            # bing 服务端对部分中文查询会退化返回单字字典页（实测 zh-CN 语言头下
            # 稳定复现，如"第五人格 加页手记 上线时间"→"第"字字典页）。
            # 检测到跑偏时自动用备选参数纠错重试，最多 2 个变体。
            if _items and _web_bing_results_junk(_items, query):
                _variants: List[Tuple[str, str]] = []
                if lang and str(lang).lower().startswith(("zh", "cn")):
                    _variants.append((query, ""))          # 去掉 zh 语言偏好（主因）
                elif not lang:
                    _variants.append((query, "zh-CN"))     # 无语言偏好仍跑偏：补 zh 市场
                if re.search(r"[\u4e00-\u9fff]", query):   # 中文逐词加引号 + 强制 en
                    _q2 = " ".join('"%s"' % w for w in re.split(r"\s+", query.strip()) if w)
                    if _q2 and _q2 != query:
                        _variants.append((_q2, ""))
                for _qv, _lgv in _variants[:2]:
                    _items2, _err2 = _bing_fetch(_qv, _lgv)
                    if _items2 and not _web_bing_results_junk(_items2, _qv):
                        _items, _err = _items2, ""
                        break
                    if _items2:
                        _items = _items2
            if not _items:
                _err = _err or "无结果"
            else:
                _err = ""
        else:
            _err = f"{engine}:{query[:30]} → 未知引擎"
    except Exception as _e:
        _err = f"{engine}:{query[:30]} → {_e}"
    if _err:
        _web_cache_put(_key, [], _err, _WEB_CACHE_FAIL_TTL)
        _web_engine_report(engine, False)
        return [], _err
    # 最终结果仍跑偏（纠错重试未成功）：短缓存防坏结果长期命中，不降级引擎
    _final_junk = engine == "bing" and _web_bing_results_junk(_items, query)
    _web_cache_put(_key, _items, "", _WEB_CACHE_FAIL_TTL if _final_junk else _WEB_CACHE_TTL)
    _web_engine_report(engine, True)
    return _items[:max_results], ""


def _extract_page_title(html: str) -> str:
    """提取 <title> 标签文本（用于正文前标注页面标题）。"""
    import html as _htm
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    if not m:
        return ""
    return _sanitize_text(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip(), unescape_html=True)


def _extract_page_text(html: str, max_chars: int) -> str:
    """粗抽取正文：优先 article/main 区块，去 script/style/nav 等噪音 → 去标签 → 折叠空白。

    优先取 <article>/<main> 内容可跳过大量站点级导航/侧栏/页脚噪音；
    再整体剔除脚本、样式、表单、悬浮层等无正文价值标签与 HTML 注释。
    """
    _m = re.search(r"(?is)<(article|main)[^>]*>(.*?)</\1>", html)
    if _m:
        html = _m.group(2)
    _text = re.sub(r"(?is)<!--.*?-->", " ", html)
    _text = re.sub(
        r"(?is)<(script|style|nav|footer|header|aside|form|noscript|iframe|svg|canvas"
        r"|template|figure|select|button|input|textarea|dialog|menu)[^>]*>.*?</\1>",
        " ", _text)
    _text = re.sub(r"<[^>]+>", " ", _text)
    _text = _sanitize_text(_text, unescape_html=True)
    _text = re.sub(r"\s+", " ", _text).strip()
    return _text[:max_chars]


def _fetch_page_text(url: str, timeout: int, max_chars: int = 3000) -> Tuple[bool, str]:
    """抓取页面并抽取正文（SSRF 防护 + 逐跳校验重定向 + title 提取 + curl 回退）。"""
    try:
        from urllib.parse import urlparse as _urlparse
        _scheme = (_urlparse(url).scheme or "").lower()
        if _scheme not in ("http", "https"):
            return False, f"非 http/https 协议: {url}"
        try:
            import requests as _req
        except ImportError:
            _req = None
        _html = None
        if _req is not None:
            _target = url
            _resp = None
            _req_err = ""
            for _attempt in range(2):
                # 整体重试一次：瞬时网络错误（连接重置/超时）时成功率显著提升
                _target = url
                _resp = None
                _hop_err = ""
                for _hop in range(6):
                    _reason = _ssrf_block_reason(_target)
                    if _reason:
                        return False, f"已拒绝 {_target}（{_reason}）"
                    try:
                        _resp = _req.get(
                            _target, timeout=timeout,
                            headers=_web_browser_headers(),
                            allow_redirects=False,
                        )
                    except Exception as _e:
                        _hop_err = str(_e)
                        _resp = None
                        break
                    if _resp.status_code in (301, 302, 303, 307, 308):
                        _loc = _resp.headers.get("Location")
                        if not _loc:
                            break
                        _target = _req.compat.urljoin(_target, _loc)
                        continue
                    break
                if _resp is not None and _resp.status_code < 400:
                    break
                _req_err = _hop_err or (f"HTTP {_resp.status_code}" if _resp is not None else "无响应")
                if _attempt == 0:
                    import time as _time
                    _time.sleep(0.3)
            if _resp is not None and _resp.status_code < 400:
                _html = _decode_html_bytes(_resp.content, _resp.encoding)
            else:
                # requests 失败/被拒 → 记下原因，继续走 curl 兜底（TLS/头差异常能救回）
                _html = None
                _req_err = _req_err or (f"HTTP {_resp.status_code}" if _resp is not None else "请求失败")
        if _html is None:
            # 回退：curl（同样过 SSRF 检查；不跟随重定向；带浏览器头）
            _reason = _ssrf_block_reason(url)
            if _reason:
                return False, f"已拒绝 {url}（{_reason}）"
            import subprocess as _sp
            _result = _sp.run(
                ["curl", "-s", "--max-redirs", "0",
                 "--max-time", str(timeout),
                 "-A", _WEB_UA,
                 "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                 "-H", "Accept-Encoding: gzip, deflate",
                 url],
                capture_output=True, timeout=timeout + 5)
            _html = _decode_html_bytes(_result.stdout or b"")
            if not _html:
                return False, f"抓取失败: {_req_err or ('curl 返回空: ' + (_result.stderr or b'').decode('utf-8', 'replace')[:200])}"
        _title = _extract_page_title(_html)
        _text = _extract_page_text(_html, max_chars)
        if not _text:
            return False, f"页面无正文: {url}"
        if _title:
            _text = f"📄 {_title}\n{_text}"
        return True, _text
    except Exception as _e:
        return False, f"抓取失败: {_e}"


# ── 弱 AI 长文摘要 + 关键行压缩（web_search ai_assist 模式）──
_WEB_ASSIST_CONFIG_KEY = "web_ai_assist"   # 全局开关：~/.config/onyx/config.json（Config 工具可读写）
_WEB_ASSIST_MIN_CHARS = 1200              # 正文超过该长度才触发辅助 AI 摘要
_WEB_ASSIST_FETCH_CAP = 12000             # 辅助 AI 模式单页抓取上限（喂给弱 AI 的完整正文）
_WEB_ASSIST_SUMMARY_MAX = 1200            # 摘要输出防御性截断


def _load_web_ai_assist_flag() -> bool:
    """读取全局 AI 辅助开关（web_ai_assist，缺省关闭）。"""
    try:
        import json as _json
        _p = os.path.join(os.path.expanduser("~"), ".config", "onyx", "config.json")
        with open(_p, "r", encoding="utf-8") as _f:
            _cfg = _json.load(_f)
        return bool(_cfg.get(_WEB_ASSIST_CONFIG_KEY, False))
    except Exception:
        return False


def _web_assist_model(platform: str, current: str) -> str:
    """辅助 AI 用当前平台最便宜的模型（弱 AI = 低单价）：价格表取最低 input 价，
    无价格表时 deepseek 回退 flash，其余回退当前模型。"""
    _info = _SUPPORTED_PLATFORMS.get(platform or "")
    if not _info:
        return current or ""
    _models = _info.get("models") or []
    _prices = _info.get("price_per_million_tokens") or {}
    _cheapest, _best = None, None
    for _m in _models:
        _in_p = (_prices.get(_m) or {}).get("input")
        if _in_p is None:
            continue
        if _best is None or _in_p < _best:
            _best, _cheapest = _in_p, _m
    if _cheapest:
        return _cheapest
    if platform == "deepseek" and "deepseek-v4-flash" in _models:
        return "deepseek-v4-flash"
    return current or ""


def _web_assist_summarize(text: str, query: str) -> Optional[str]:
    """弱 AI 长文摘要：把完整正文交给当前平台最便宜模型总结，返回摘要。

    失败 / 中断 / 无密钥返回 None（调用方回退关键行压缩，工具不阻塞）；
    成功时同步把本次调用写入 cost.json（与压缩/子代理成本入账一致）。
    """
    try:
        from .api import call_ai_api_sse
        from .config import load_key_conf
        from .cost import append_cost_record
        import hashlib as _hl
        _conf = load_key_conf() or {}
        _plat = _conf.get("platform", "deepseek")
        _model = _web_assist_model(_plat, _conf.get("model", ""))
        _sys = (
            "You are a web article summarizer. Given a fetched page (title + body), "
            "output a concise summary in the SAME language as the article (Chinese stays Chinese). "
            "Keep key facts: names, numbers, dates, versions, URLs, conclusions, comparisons. "
            "Aim under 500 characters. No preamble, no bracket markers, no label. "
            "If the body is mostly navigation/ads noise, say in one sentence what the page is about."
        )
        _result = call_ai_api_sse(
            question="",
            messages=[
                {"role": "system", "content": _sys},
                {"role": "user", "content": f"查询主题: {query}\n\n页面正文:\n{text}"},
            ],
            tools=[],
            ai_tools_prompt="",
            user_home_dir=None,
            memory_block="",
            session_id="webassist_" + _hl.md5((query + text[:500]).encode("utf-8", "ignore")).hexdigest()[:12],
            model_override=_model,
            platform_override=_plat,
        )
        if _result.get("_interrupted") or _result.get("error"):
            return None
        _txt = (_result.get("txt") or "").strip()
        if not _txt:
            return None
        try:
            _u = _result.get("_usage") or {}
            _pt, _ct = _u.get("prompt_tokens") or 0, _u.get("completion_tokens") or 0
            if _pt or _ct:
                append_cost_record(os.path.expanduser("~"), _plat, _model, _pt, _ct)
        except Exception:
            pass
        return _txt[:_WEB_ASSIST_SUMMARY_MAX]
    except Exception:
        return None


def _compress_text_key_lines(text: str, terms: List[str], max_chars: int) -> str:
    """关键行压缩：按句子切分 → 查询词命中/位置打分 → 取高分句至预算上限。

    有查询词命中时优先保留命中句（解决"只抓开头/首页噪音"导致的失真）；
    无命中时按原文顺序取前部句子（标题自然保留）。输出保证 ≤ max_chars。
    """
    _clean = re.sub(r"\s+", " ", text).strip()
    if not _clean or len(_clean) <= max_chars:
        return _clean
    _sents = [s.strip() for s in re.split(r"(?<=[。！？；.!?])\s*|\n", _clean) if s.strip()]
    _kw = [t.lower() for t in terms if t and len(t) >= 2]
    _scored = []
    for _i, _s in enumerate(_sents):
        _score = 0.0
        _low = _s.lower()
        for _k in _kw:
            if _k in _low:
                _score += 3.0
        if _i == 0:
            _score += 1.5
        if len(_s) < 10:
            _score -= 1.0
        if len(_s) > 400:
            _score -= 0.5
        _scored.append((_score, _i, _s))
    if any(_sc[0] > 0 for _sc in _scored):
        _scored.sort(key=lambda _x: (-_x[0], _x[1]))
    else:
        _scored.sort(key=lambda _x: _x[1])
    _out: List[str] = []
    _used = 0
    for _sc in _scored:
        _s = _sc[2]
        if _used + len(_s) + 1 > max_chars:
            if _out:
                break
            _s = _s[:max_chars]
        _out.append(_s)
        _used += len(_s) + 1
    return " ".join(_out)[:max_chars]


def _web_fetch_one(url: str, timeout: int, fetch_cap: int, assist_on: bool,
                   max_chars: int, queries: List[str], query: str) -> Dict:
    """抓取单页（worker 线程内完成正文抽取 / 弱 AI 摘要 / 关键行压缩）。

    SSRF/协议/超时防线复用 _fetch_page_text；摘要与压缩逻辑与主循环一致，
    但移入 worker → 多页抓取与弱 AI 摘要并行，总耗时 ≈ 最慢单页。
    """
    _ok, _txt = _fetch_page_text(url, timeout, fetch_cap)
    if _ok and assist_on and len(_txt) > _WEB_ASSIST_MIN_CHARS:
        # 长文 + 辅助 AI 开启：完整正文交弱 AI 总结（失败回退关键行压缩）
        _sum = _web_assist_summarize(_txt, query or "web")
        if _sum:
            return {"url": url, "ok": True, "text": _sum, "mode": "summary"}
    if _ok and len(_txt) > max_chars:
        # 关键行压缩：查询词命中句优先，替代机械截取前 N 字符
        return {"url": url, "ok": True,
                "text": _compress_text_key_lines(_txt, queries, max_chars),
                "mode": "compress"}
    return {"url": url, "ok": _ok, "text": _txt, "mode": "raw"}


def _strip_wrap_quotes(s: str) -> str:
    """剥离字符串外层多余的成对包裹引号："xxx" / 'xxx' / `xxx` → xxx。

    模型有时把字符串值写成带包裹引号的形式（如 "deepseek 推理 api"），
    搜索引擎会把引号当字面字符导致搜索失真；这里统一剥掉。
    """
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'", "`"):
        return s[1:-1].strip()
    return s


def _web_normalize_params(p: dict) -> dict:
    """web_search 参数规范化：修复模型常见的不规范传参。

    - query 为空时回退 range_str/path/body（_parse_tool_params 旧格式回退产物）；
    - `query=xxx` 形式提取 xxx；
    - 剥离字符串值里多余的成对包裹引号（"xxx" → xxx）；
    - queries 列表逐项清洗；布尔/数字字符串自动转换。
    """
    if not isinstance(p, dict):
        p = {}
    p = dict(p)
    _q = str(p.get("query") or "").strip()
    if not _q:
        # 回退 _parse_tool_params 旧格式回退产物：path=第一个token、range_str=其余
        # （两者都在时合并还原完整查询，避免只取后半截丢词）
        _path = str(p.get("path") or "").strip()
        _range = str(p.get("range_str") or "").strip()
        _body = str(p.get("body") or "").strip()
        if _path and _range:
            _q = _path + " " + _range
        elif _range:
            _q = _range
        elif _path:
            _q = _path
        elif _body and _body.lower() != "none":
            _q = _body
    if _q.startswith("query="):
        _q = _q[len("query="):].strip()
    _q = _strip_wrap_quotes(_q)
    if _q:
        p["query"] = _q
    for _k in ("language", "region", "time_range", "output_format", "action"):
        _v = p.get(_k)
        if isinstance(_v, str):
            p[_k] = _strip_wrap_quotes(_v.strip())
    _qs = p.get("queries")
    if isinstance(_qs, list):
        _clean = []
        for _x in _qs:
            if isinstance(_x, str):
                _x = _strip_wrap_quotes(_x.strip())
            if _x:
                _clean.append(_x)
        p["queries"] = _clean
    for _k in ("fetch_pages", "safe_search", "ai_assist"):
        _v = p.get(_k)
        if isinstance(_v, str):
            p[_k] = str(_v).strip().lower() in ("true", "1", "yes", "on")
    for _k in ("max_results", "fetch_limit", "max_chars_per_page", "timeout"):
        _v = p.get(_k)
        if isinstance(_v, str):
            try:
                p[_k] = int(_v.strip())
            except ValueError:
                pass
    return p


def _exec_web_search_multi(params: dict) -> str:
    """web_search 入口分发器。

    - 无 topics → 单主题完整链路（_exec_web_search_one_topic，原有行为不变）；
    - 有 topics[] → 批量独立主题：每个主题继承顶层参数、可覆盖，全部并行执行，
      输出按主题分栏（text）或结构化数组（json）。空 query 主题忽略，最多 5 个。
    """
    _p = _web_normalize_params(params or {})
    _topics = [t for t in (_p.get("topics") or [])
               if isinstance(t, dict) and str(t.get("query") or "").strip()]
    _topics = _topics[:_WEB_TOPICS_MAX]
    if not _topics:
        return _exec_web_search_one_topic(_p)
    _merged = []
    for _t in _topics:
        _tp = {k: v for k, v in _p.items() if k != "topics"}
        for _k in _WEB_TOPIC_OVERRIDES:
            if _k in _t and _t[_k] is not None:
                _tp[_k] = _t[_k]
        _merged.append(_tp)
    _fmt = str(_p.get("output_format") or "text").lower()
    _outs = _web_parallel(lambda _tp: _exec_web_search_one_topic(_tp), _merged,
                          _WEB_TOPICS_WORKERS, budget=_WEB_SEARCH_BUDGET * 2)
    if _fmt == "json":
        return json.dumps({
            "action": "topics", "topic_count": len(_merged),
            "topics": [
                {"query": _t.get("query", ""),
                 "output": _o or "❌ 主题超时未完成（预算内未返回）"}
                for _t, _o in zip(_merged, _outs)
            ],
        }, ensure_ascii=False, indent=1)
    _lines = [f"🔎 web_search(topics): {len(_merged)} 个独立主题并行查询"]
    for _i, (_t, _o) in enumerate(zip(_merged, _outs), 1):
        _lines.append("")
        _lines.append("─" * 24)
        _lines.append(f"## 主题 {_i}: {str(_t.get('query', ''))[:60]}")
        _lines.append(_o or "❌ 主题超时未完成（预算内未返回）")
    return "\n".join(_lines)


def _exec_web_search_one_topic(params: dict) -> str:
    """web_search 单主题完整链路（search / fetch / mixed 三模式）。

    第一性原则设计，覆盖完整调研链路（旧 WebSearch/WebFetch 能力已合并）：
      action: search=仅搜索；fetch=仅抓取 urls 指定页面；mixed=搜索+自动抓页（默认）
      query / queries[]: 主查询 + 附加查询（最多 10 个，一次覆盖多个角度）
      urls[]: 指定 URL 直接抓正文（fetch 必填 / mixed 追加；同样过域名过滤与 SSRF 防护）
      engines[]: duckduckgo / bing；max_results: 每查询每引擎条数
      allowed_domains[] / exclude_domains[]: 域名双向过滤（对结果与 urls 都生效）
      language / region / time_range / safe_search: 搜索偏好（best-effort）
      fetch_pages / fetch_limit: 搜索后自动抓取排名靠前页
      max_chars_per_page: 单页正文截断；output_format: text / json；timeout: 单请求超时
    """
    try:
        from urllib.parse import urlparse as _urlparse, quote as _quote

        _p = _web_normalize_params(params or {})
        _action = str(_p.get("action") or "mixed").lower()
        if _action not in ("search", "fetch", "mixed"):
            _action = "mixed"
        _query = _strip_wrap_quotes(str(_p.get("query", "") or "").strip())
        _urls = [str(u).strip() for u in (_p.get("urls") or []) if str(u).strip()]
        if _action in ("search", "mixed") and not _query:
            return "❌ web_search: action=search/mixed 需要 query 参数"
        if _action == "fetch" and not _urls:
            return "❌ web_search: action=fetch 需要 urls 参数（要抓取的 URL 列表）"
        _queries = [_query] if _query else []
        for _q in (_p.get("queries") or [])[:10]:
            _q = str(_q or "").strip()
            if _q and _q not in _queries:
                _queries.append(_q)
        _queries = _queries[:10]
        _engines = [str(e).lower() for e in (_p.get("engines") or _WEB_ENGINES)]
        _engines = [e for e in _engines if e in _WEB_ENGINES] or list(_WEB_ENGINES)
        try:
            _max_results = max(1, min(int(_p.get("max_results") or 8), 15))
        except Exception:
            _max_results = 8
        _allowed = [str(d).lower() for d in (_p.get("allowed_domains") or []) if str(d).strip()]
        _excluded = [str(d).lower() for d in (_p.get("exclude_domains") or []) if str(d).strip()]
        # site: 语法 → 域名硬过滤：bing 对中文站 site: 支持差（实测返回 site 单词字典页
        # 或完全无关结果），自动把查询中的 site: 域并入 allowed_domains，
        # 保证结果至少限定在指定站内；用户显式传 allowed_domains 时不覆盖。
        _site_domains = []
        for _qq in _queries:
            _site_domains += [m.lower().lstrip(".")
                              for m in re.findall(r"(?i)\bsite:([a-z0-9.\-]+)", _qq)]
        if _site_domains and not _allowed:
            _allowed = list(dict.fromkeys(_site_domains))[:3]
        _lang = str(_p.get("language") or "").strip()
        _region = str(_p.get("region") or "").strip()
        _time = str(_p.get("time_range") or "").strip().lower()
        _safe = bool(_p.get("safe_search", False))
        _fetch_pages = bool(_p.get("fetch_pages", False))
        try:
            _fetch_limit = max(1, min(int(_p.get("fetch_limit") or 3), 5))
        except Exception:
            _fetch_limit = 3
        try:
            _max_chars = max(500, min(int(_p.get("max_chars_per_page") or 3000), 8000))
        except Exception:
            _max_chars = 3000
        _fmt = str(_p.get("output_format") or "text").lower()
        try:
            _timeout = max(5, min(int(_p.get("timeout") or 15), 60))
        except Exception:
            _timeout = 15
        # ── 弱 AI 长文摘要：per-call ai_assist 覆盖全局开关（web_ai_assist）──
        _assist_flag = _p.get("ai_assist")
        if isinstance(_assist_flag, bool):
            _assist_on = _assist_flag
        elif str(_assist_flag).lower() in ("true", "1", "yes", "on"):
            _assist_on = True
        elif str(_assist_flag).lower() in ("false", "0", "no", "off"):
            _assist_on = False
        else:
            _assist_on = _load_web_ai_assist_flag()
        # 抓取上限无条件放大：max_chars_per_page 作为输出预算，关键行压缩/弱 AI 摘要
        # 需要读到比输出更多的正文才有筛选余地（解决"只抓开头/首页噪音"失真）
        _fetch_cap = max(_max_chars, _WEB_ASSIST_FETCH_CAP)

        _results: List[Dict] = []
        _errors: List[str] = []

        # ── 1. 搜索阶段（search/mixed）——多查询 × 多引擎并行（线程池）──
        # 原串行实现总耗时 = 查询数 × 引擎数 × 单请求；并行后 ≈ 最慢单个请求
        # （含失败重试）。结果按「查询 → 引擎」原顺序收集，输出确定性不变。
        if _action in ("search", "mixed") and _queries:
            # 引擎健康降级：连续失败的引擎跳过请求（到期自动恢复）
            _engines_active = [e for e in _engines if not _web_engine_degraded(e)]
            if len(_engines_active) < len(_engines):
                _errors.append("引擎降级跳过: " + ", ".join(
                    sorted(set(_engines) - set(_engines_active))))
            if not _engines_active:
                _errors.append("所有引擎均处于降级状态，本次搜索跳过")
            else:
                # 查询增强：英文长句去停用词、中文技术查询加英文变体 → 多候选并行
                # （简单短查询不扩展，请求量基本不变；候选结果统一按原查询标注）
                _query_cands = [_web_query_enhance(_q) for _q in _queries]
                _tasks = [(qi, ci, ei)
                          for qi, _cands in enumerate(_query_cands)
                          for ci, _cand in enumerate(_cands)
                          for ei, _eng in enumerate(_engines_active)]
                # 总时间预算：到点收工，不等最慢引擎（超时任务标注后跳过）
                _outs = _web_parallel(
                    lambda _t: _web_search_one(_query_cands[_t[0]][_t[1]], _engines_active[_t[2]],
                                               _timeout, _region, _lang, _time,
                                               _safe, _max_results),
                    _tasks, _WEB_SEARCH_WORKERS, budget=_WEB_SEARCH_BUDGET)
                for (_qi, _ci, _ei), _res in zip(_tasks, _outs):
                    if _res is None:
                        _errors.append(f"{_engines_active[_ei]}:{_queries[_qi][:30]} → 超时未完成")
                        continue
                    _items, _werr = _res
                    if _werr:
                        _errors.append(_werr)
                        continue
                    _q, _eng = _queries[_qi], _engines_active[_ei]
                    for _it in _items:
                        _it["query"] = _q
                        _it["engine"] = _eng
                        _results.append(_it)

            # ── 去重（URL 规范化） + 域名过滤 ──
            _seen: set = set()
            _deduped: List[Dict] = []
            for _r in _results:
                _host = (_urlparse(_r["url"]).netloc or "").lower().split(":")[0]
                if not _host:
                    continue
                if _allowed and not any(_host == d or _host.endswith("." + d) for d in _allowed):
                    continue
                if any(_host == d or _host.endswith("." + d) for d in _excluded):
                    continue
                _key = _r["url"].lower().rstrip("/")
                if _key in _seen:
                    continue
                _seen.add(_key)
                _deduped.append(_r)
            # 组内重排（按查询分组）：权威域/词命中前置、SEO 垃圾信号降权，
            # 查询主序保留——展示顺序与预筛抓页（取前 N）都受益
            _results = _web_rerank(_deduped)

        # ── 2. 抓取阶段（fetch/mixed）：指定 urls + 搜索排名靠前页（同样过域名过滤与 SSRF）──
        _pages: List[Dict] = []
        _fetch_targets: List[str] = []
        _skipped: List[str] = []   # 相关性预筛跳过的 SERP 页（避免浪费抓取名额在无关页）
        if _action == "fetch":
            _fetch_targets = list(_urls)
        elif _action == "mixed":
            if _fetch_pages and _results:
                _picked = 0
                for _r in _results:
                    if _picked >= _fetch_limit:
                        break
                    if _web_result_relevant(_query, _r.get("title", ""), _r["url"],
                                             _r.get("snippet", "")):
                        _fetch_targets.append(_r["url"])
                        _picked += 1
                    else:
                        _skipped.append(_r["url"])
            _fetch_targets += _urls
        if _fetch_targets:
            _seen_urls: set = set()
            _entries: List[Dict] = []          # 按输入顺序占位；None = 待抓取（并行任务）
            _jobs: List[Tuple[int, str]] = []
            for _u in _fetch_targets:
                _host = (_urlparse(_u).netloc or "").lower().split(":")[0]
                if not _host:
                    _entries.append({"url": _u, "ok": False, "text": "无效 URL"})
                    continue
                if _allowed and not any(_host == d or _host.endswith("." + d) for d in _allowed):
                    _entries.append({"url": _u, "ok": False, "text": "被 allowed_domains 过滤"})
                    continue
                if any(_host == d or _host.endswith("." + d) for d in _excluded):
                    _entries.append({"url": _u, "ok": False, "text": "被 exclude_domains 过滤"})
                    continue
                _key = _u.lower().rstrip("/")
                if _key in _seen_urls:
                    continue
                _seen_urls.add(_key)
                _entries.append(None)
                _jobs.append((len(_entries) - 1, _u))
            if _jobs:
                # 并行抓取：正文抽取 / 长文摘要 / 关键行压缩均在 worker 内完成
                _outs = _web_parallel(
                    lambda _t: _web_fetch_one(_t[1], _timeout, _fetch_cap, _assist_on,
                                              _max_chars, _queries, _query),
                    _jobs, _WEB_FETCH_WORKERS, budget=_WEB_FETCH_BUDGET)
                for (_i, _u), _pg in zip(_jobs, _outs):
                    if _pg is None:
                        _pg = {"url": _u, "ok": False, "text": "超时未完成（预算内未返回）"}
                    _entries[_i] = _pg
            _pages = [e for e in _entries if e is not None]

        # ── 3. 输出 ──
        if _fmt == "json":
            return json.dumps({
                "action": _action, "query": _query, "queries": _queries, "engines": _engines,
                "ai_assist": _assist_on, "result_count": len(_results),
                "results": _results, "pages": _pages, "errors": _errors, "skipped": _skipped,
            }, ensure_ascii=False, indent=1)

        _lines: List[str] = []
        if _action == "fetch":
            _lines.append(f"📄 web_search(fetch): 抓取 {len(_pages)} 个指定 URL")
        else:
            _lines.append(f"🔎 web_search({_action}): {len(_queries)} 个查询 × {len(_engines)} 个引擎，"
                          f"共 {len(_results)} 个唯一结果")
        if _errors:
            _lines.append("⚠️ 部分请求失败: " + "；".join(_errors[:3]))
        if _skipped:
            _lines.append("⏭️ 低相关跳过（未命中查询词）: " + "；".join(_skipped[:3]))
        for _i, _r in enumerate(_results, 1):
            _lines.append(f"{_i}. {_r['title'] or '(无标题)'}")
            _lines.append(f"   URL: {_r['url']}  （{_r['engine']}，查询: {_r['query'][:40]}）")
            if _r.get("snippet"):
                _lines.append(f"   {_r['snippet'][:400]}")
        if _pages:
            if _action != "fetch" and _results:
                _lines.append("")
            _lines.append(f"📄 已抓取 {len(_pages)} 个页面正文：")
            for _pg in _pages:
                _lines.append(f"--- {_pg['url']}")
                if not _pg["ok"]:
                    _lines.append("❌ " + _pg["text"])
                elif _pg.get("mode") == "summary":
                    _lines.append("🤖 AI 摘要: " + _pg["text"])
                elif _pg.get("mode") == "compress":
                    _lines.append("🔑 关键行: " + _pg["text"])
                else:
                    _lines.append("✅ " + _pg["text"])
        if _action != "fetch" and not _results:
            _lines.append("(无结果；可换关键词、减少 allowed_domains 限制或换引擎)")
        return "\n".join(_lines)
    except Exception as _e:
        return f"❌ web_search 执行失败: {_e}"
