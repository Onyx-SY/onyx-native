#!/usr/bin/env python3
"""web_search 网络调研工具（唯一 web 工具）：三模式 / 多引擎 / 去重 / 过滤 / 抓页 / JSON。

离线验证（mock requests.get / subprocess.run，不访问真实网络）。
运行: python3 test/virtual/test_web_search_tool.py
"""
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin import ai_cmd  # noqa: E402


class _FakeResp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    @property
    def headers(self):
        return {}


def _reset_web_state():
    """清空 SERP 缓存与引擎健康统计（模块级状态，测试间隔离）。"""
    ai_cmd._WEB_CACHE.clear()
    ai_cmd._WEB_ENGINE_STATS.clear()
    ai_cmd._WEB_ENGINE_DEGRADED_UNTIL.clear()

DDG_HTML = """
<title>DDG Search</title>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&rut=1">Example Docs</a>
<a class="result__snippet" href="#">The official documentation for example.</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fblog&rut=1">Example Blog</a>
<a class="result__snippet" href="#">Blog posts about things.</a>
"""
BING_HTML = """
<li class="b_algo"><h2><a href="https://example.com/docs">Example Docs</a></h2><p>A snippet here.</p></li>
<li class="b_algo"><h2><a href="https://example.com/blog">Example Blog</a></h2><p>Another snippet.</p></li>
<li class="b_algo"><h2><a href="https://excluded.com/x">Excluded</a></h2><p>no</p></li>
"""
PAGE_HTML = ("<html><head><title>Docs Page</title></head>"
             "<body><style>s</style><script>a()</script><nav>nav</nav>"
             "<p>Hello <b>world</b> content.</p></body></html>")


def _fake_get(url, timeout=15, headers=None, allow_redirects=True):
    if "duckduckgo" in url:
        return _FakeResp(DDG_HTML)
    if "bing.com" in url:
        return _FakeResp(BING_HTML)
    if "example.com/docs" in url:
        return _FakeResp(PAGE_HTML)
    return _FakeResp("<html></html>")


def test_multi_query_multi_engine_dedup_and_filter():
    with mock.patch("requests.get", side_effect=_fake_get):
        out = ai_cmd._exec_web_search_multi({
            "query": "test framework",
            "queries": ["docs"],
            "engines": ["duckduckgo", "bing"],
            "allowed_domains": ["example.com"],
            "exclude_domains": ["excluded.com"],
            "max_results": 5,
        })
    assert "2 个查询 × 2 个引擎" in out, out
    assert out.count("Example Docs") == 1, f"跨引擎应去重: {out}"
    assert "excluded.com" not in out, "exclude_domains 应生效"
    assert "https://example.com/docs" in out
    assert "uddg=" not in out, "DDG 跳转链接应解码为真实 URL"
    print("PASS 多查询 × 多引擎聚合、去重、域名过滤、uddg 解码")


def test_fetch_mode_uses_given_urls_only():
    """action=fetch：只抓指定 urls，不搜索、不需要 query。"""
    with mock.patch("requests.get", side_effect=_fake_get), \
         mock.patch("bin.ai_lib.web_search._load_web_ai_assist_flag", return_value=False):
        out = ai_cmd._exec_web_search_multi({
            "action": "fetch",
            "urls": ["https://example.com/docs", "https://10.0.0.1/x"],
        })
    assert "web_search(fetch)" in out and "抓取 2 个指定 URL" in out, out
    assert "Hello world content" in out, "应抓到指定 URL 的正文"
    assert "📄 Docs Page" in out, "应展示页面标题"
    assert "10.0.0.1" in out and "❌" in out, "失败 URL 应显示错误而非静默"
    # fetch 模式缺 urls → 报错
    assert "需要 urls 参数" in ai_cmd._exec_web_search_multi({"action": "fetch"})
    print("PASS action=fetch：只抓指定 urls（含标题提取与失败展示），缺 urls 报错")


def test_mixed_with_explicit_urls():
    """action=mixed：搜索 + urls 追加抓取。"""
    with mock.patch("requests.get", side_effect=_fake_get), \
         mock.patch("bin.ai_lib.web_search._load_web_ai_assist_flag", return_value=False):
        out = ai_cmd._exec_web_search_multi({
            "query": "x", "engines": ["bing"],
            "urls": ["https://example.com/docs"],
        })
    assert "共 3 个唯一结果" in out, out
    assert "已抓取 1 个页面正文" in out and "Hello world content" in out
    print("PASS action=mixed：搜索 + 指定 urls 追加抓取")


def test_safe_search_and_prefs_passed_to_engines():
    """safe_search / language / region / time_range 应拼进引擎请求 URL。"""
    seen_urls = []

    def _capture(url, timeout=15, headers=None, allow_redirects=True):
        seen_urls.append(url)
        return _FakeResp(DDG_HTML if "duckduckgo" in url else BING_HTML)

    with mock.patch("requests.get", side_effect=_capture):
        ai_cmd._exec_web_search_multi({
            "query": "x", "safe_search": True,
            "language": "zh", "region": "cn-zh", "time_range": "week",
        })
    ddg = [u for u in seen_urls if "duckduckgo" in u][0]
    bing = [u for u in seen_urls if "bing.com" in u][0]
    assert "kp=1" in ddg and "&kl=cn-zh" in ddg and "&l=zh" in ddg and "df=w" in ddg, ddg
    assert "adlt=strict" in bing and "&cc=cn-zh" in bing and "setlang=zh" in bing, bing
    print("PASS safe_search/语言/地区/时效参数正确传递到 duckduckgo 与 bing")


def test_fetch_pages_and_max_chars():
    """自动抓页 + 单页截断控制。"""
    long_html = "<html><body><p>" + "word " * 4000 + "</p></body></html>"

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        if "bing.com" in url:
            return _FakeResp('<li class="b_algo"><h2><a href="https://example.com/docs">D</a></h2></li>')
        return _FakeResp(long_html)

    with mock.patch("requests.get", side_effect=_fake), \
         mock.patch("bin.ai_lib.web_search._load_web_ai_assist_flag", return_value=False):
        out = ai_cmd._exec_web_search_multi({
            "query": "x", "engines": ["bing"],
            "fetch_pages": True, "fetch_limit": 1,
            "max_chars_per_page": 500,
        })
    assert "已抓取 1 个页面正文" in out
    assert "🔑 关键行" in out, "超长页应走关键行压缩"
    body = out.split("🔑 关键行:")[1]
    assert len(body.strip()) <= 510, f"单页正文应被截断到 ~500 字符: {len(body)}"
    print("PASS fetch_pages 自动抓页 + max_chars_per_page 关键行压缩生效")


def test_json_output_and_validation():
    with mock.patch("requests.get", side_effect=_fake_get):
        out = ai_cmd._exec_web_search_multi({"query": "x", "output_format": "json"})
    d = json.loads(out)
    assert d["query"] == "x" and d["result_count"] >= 1
    assert "action" in d and "results" in d and "pages" in d and "errors" in d
    assert "ai_assist" in d and isinstance(d["ai_assist"], bool), "JSON 应带 ai_assist 开关状态"
    # search/mixed 缺 query / 非法 action 回落
    assert "需要 query 参数" in ai_cmd._exec_web_search_multi({})
    assert "需要 query 参数" in ai_cmd._exec_web_search_multi({"action": "search"})
    print("PASS JSON 结构化输出 + 参数校验（缺 query / 非法 action 回落 mixed）")


def test_curl_fallback_when_requests_missing():
    """requests 缺失时：搜索走 curl 回退（web_search 不依赖 requests 仍可用）。"""
    import subprocess as _sp

    _reset_web_state()
    with mock.patch.dict(sys.modules, {"requests": None}):
        with mock.patch.object(_sp, "run", return_value=type(
                "R", (), {"stdout": DDG_HTML, "stderr": ""})()) as _run:
            out = ai_cmd._exec_web_search_multi({"query": "x", "engines": ["duckduckgo"]})
    assert _run.called, "requests 缺失时应调用 curl"
    assert "Example Docs" in out, f"curl 回退应正常解析结果: {out}"
    print("PASS requests 缺失 → curl 回退仍可搜索")


def test_fetch_ssrf_guard():
    """抓页走 SSRF 防护：内网地址直接拒绝（无需真实网络）。"""
    ok, msg = ai_cmd._fetch_page_text("http://127.0.0.1/admin", 15)
    assert not ok and ("内网" in msg or "拒绝" in msg), msg
    ok, msg = ai_cmd._fetch_page_text("ftp://example.com/x", 15)
    assert not ok and "协议" in msg, msg
    print("PASS 抓页 SSRF：内网地址与非法协议被拒")


def test_tool_schema_has_new_params():
    """工具 schema：三模式 + urls + safe_search + max_chars_per_page，required 为空（fetch 免 query）。"""
    tools = ai_cmd.build_native_tools()
    w = [t for t in tools if t.get("function", {}).get("name") == "web_search"][0]
    assert w.get("x_permission") == "ReadOnly", "web_search 应自动放行"
    props = w["function"]["parameters"]["properties"]
    for p in ("action", "ai_assist", "query", "queries", "urls", "engines", "max_results",
              "allowed_domains", "exclude_domains", "language", "region",
              "time_range", "safe_search", "fetch_pages", "fetch_limit",
              "max_chars_per_page", "output_format", "timeout"):
        assert p in props, f"缺参数 {p}: {list(props)}"
    assert "topics" in props, f"缺批量主题参数 topics: {list(props)}"
    assert w["function"]["parameters"]["required"] == [], "required 应为空（fetch 模式免 query）"
    # 旧工具已下线
    names = [t.get("function", {}).get("name") for t in tools]
    assert "WebSearch" not in names and "WebFetch" not in names, "旧工具应已删除"
    print("PASS schema：18 参数齐全（含 ai_assist）、ReadOnly、required 空、旧 WebSearch/WebFetch 已下线")


def test_key_line_compression_keeps_matching_sentences():
    """ai_assist=false（默认关闭）：长文按查询词关键行压缩，而非机械截取前 N 字符。"""
    mid = "中间句包含 GPT-5 发布的关键信息。"
    body = "第一句导航噪音。" + "无关填充句子。" * 60 + mid + "结尾补充内容。" * 40
    html = f"<html><head><title>Long Page</title></head><body><p>{body}</p></body></html>"

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        if "bing.com" in url:
            return _FakeResp('<li class="b_algo"><h2><a href="https://example.com/long">L</a></h2></li>')
        return _FakeResp(html)

    with mock.patch("requests.get", side_effect=_fake), \
         mock.patch("bin.ai_lib.web_search._load_web_ai_assist_flag", return_value=False):
        out = ai_cmd._exec_web_search_multi({
            "query": "GPT-5", "engines": ["bing"],
            "fetch_pages": True, "fetch_limit": 1, "max_chars_per_page": 500,
        })
    assert "🔑 关键行" in out, out
    assert "GPT-5 发布" in out, f"命中查询词的句子应被保留（而非只取前 N 字符）: {out}"
    assert len(out.split("🔑 关键行:")[1].strip()) <= 510, "压缩后应满足字符预算"
    print("PASS 关闭辅助 AI：关键行压缩保留查询词命中句，满足字符预算")


def test_ai_assist_summarize_long_page():
    """ai_assist=true：长文完整交给弱 AI 总结，摘要替换原文返回。"""
    body = "第一段说明。" + "长内容填充段落。" * 200  # ~1600 字符，超过 1200 触发阈值
    html = f"<html><head><title>Article</title></head><body><p>{body}</p></body></html>"
    summary = "GPT-5 在 2026 年推出新版本，这是弱 AI 生成的摘要。"

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        if "bing.com" in url:
            return _FakeResp('<li class="b_algo"><h2><a href="https://example.com/art">A</a></h2></li>')
        return _FakeResp(html)

    with mock.patch("requests.get", side_effect=_fake), \
         mock.patch("bin.ai_lib.api.call_ai_api_sse",
                    return_value={"txt": summary,
                                  "_usage": {"prompt_tokens": 100, "completion_tokens": 50}}), \
         mock.patch("bin.ai_lib.cost.append_cost_record") as _cost, \
         mock.patch("bin.ai_lib.web_search._load_web_ai_assist_flag", return_value=False):
        out = ai_cmd._exec_web_search_multi({
            "query": "GPT-5", "engines": ["bing"],
            "fetch_pages": True, "fetch_limit": 1, "ai_assist": True,
        })
    assert "🤖 AI 摘要" in out, out
    assert summary in out, f"弱 AI 摘要应替换原文: {out}"
    assert "长内容填充段落" not in out.split("🤖 AI 摘要:")[1], "原文不应原样进入输出"
    assert _cost.called, "辅助 AI 调用应入账 cost.json"
    print("PASS ai_assist=true：长文经弱 AI 摘要返回（原文不进入输出，成本入账）")


def test_ai_assist_fallback_to_compression():
    """ai_assist=true 但弱 AI 调用失败（无密钥/报错/中断）→ 回退关键行压缩，工具不阻塞。"""
    body = "无关填充。" * 300 + "包含 GPT-5 版本信息的关键句。" + "尾部填充。" * 100
    html = f"<html><body><p>{body}</p></body></html>"

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        if "bing.com" in url:
            return _FakeResp('<li class="b_algo"><h2><a href="https://example.com/fb">F</a></h2></li>')
        return _FakeResp(html)

    with mock.patch("requests.get", side_effect=_fake), \
         mock.patch("bin.ai_lib.api.call_ai_api_sse", return_value={"error": "未配置 API 密钥"}), \
         mock.patch("bin.ai_lib.web_search._load_web_ai_assist_flag", return_value=False):
        out = ai_cmd._exec_web_search_multi({
            "query": "GPT-5", "engines": ["bing"],
            "fetch_pages": True, "fetch_limit": 1, "ai_assist": True,
            "max_chars_per_page": 500,
        })
    assert "🔑 关键行" in out and "GPT-5" in out, f"失败应回退关键行压缩: {out}"
    assert "🤖 AI 摘要" not in out
    print("PASS 辅助 AI 失败 → 回退关键行压缩（工具不阻塞）")


def test_ai_assist_global_flag_and_param_override():
    """全局开关 web_ai_assist 生效；per-call ai_assist=false 可覆盖关闭。"""
    body = "填充内容段落。" * 600  # 4200 字符，超过默认 3000 输出预算 → 触发压缩
    html = f"<html><body><p>{body}</p></body></html>"
    summary = "全局开关开启后的摘要。"

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        if "bing.com" in url:
            return _FakeResp('<li class="b_algo"><h2><a href="https://example.com/g">G</a></h2></li>')
        return _FakeResp(html)

    with mock.patch("requests.get", side_effect=_fake), \
         mock.patch("bin.ai_lib.web_search._load_web_ai_assist_flag", return_value=True), \
         mock.patch("bin.ai_lib.api.call_ai_api_sse", return_value={"txt": summary}):
        out = ai_cmd._exec_web_search_multi({
            "query": "x", "engines": ["bing"],
            "fetch_pages": True, "fetch_limit": 1,
        })
    assert "🤖 AI 摘要" in out and summary in out, f"全局开关开启时应走摘要: {out}"

    with mock.patch("requests.get", side_effect=_fake), \
         mock.patch("bin.ai_lib.web_search._load_web_ai_assist_flag", return_value=True), \
         mock.patch("bin.ai_lib.api.call_ai_api_sse", return_value={"txt": summary}):
        out2 = ai_cmd._exec_web_search_multi({
            "query": "x", "engines": ["bing"],
            "fetch_pages": True, "fetch_limit": 1, "ai_assist": False,
        })
    assert "🤖 AI 摘要" not in out2 and "🔑 关键行" in out2, f"per-call false 应覆盖全局开关: {out2}"
    print("PASS 全局开关 web_ai_assist + per-call ai_assist 覆盖")


def test_ddg_lite_fallback_when_html_empty():
    """html 端点空结果/被限流 → 自动回退 lite 端点解析。"""
    _reset_web_state()
    calls = []

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        calls.append(url)
        if "html.duckduckgo.com" in url:
            return _FakeResp("<html>anomaly</html>")
        if "lite.duckduckgo.com" in url:
            return _FakeResp(
                '<table><tr><td><a rel="nofollow" href="https://example.com/lite">Lite Result</a></td></tr>'
                '<tr><td class="result-snippet">A lite snippet.</td></tr></table>')
        return _FakeResp("<html></html>")

    with mock.patch("requests.get", side_effect=_fake):
        out = ai_cmd._exec_web_search_multi({"query": "x", "engines": ["duckduckgo"]})
    assert "Lite Result" in out and "A lite snippet." in out, out
    assert any("lite.duckduckgo.com" in u for u in calls), f"应回退 lite 端点: {calls}"
    print("PASS html 端点空结果 → lite 端点回退解析")


def test_ddg_anomaly_detection_reports_block():
    """html + lite 均被反爬拦截 → 明确报「反爬拦截」而非静默空结果。"""
    _reset_web_state()
    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        return _FakeResp("<html>anomaly detection challenge</html>")

    with mock.patch("requests.get", side_effect=_fake):
        out = ai_cmd._exec_web_search_multi({"query": "x", "engines": ["duckduckgo"]})
    assert "反爬拦截" in out, out
    assert "duckduckgo:x" in out, f"错误应带引擎与查询: {out}"
    print("PASS 反爬拦截页给出明确错误")


def test_ddg_skip_lite_on_transport_failure():
    """html 端点传输层失败（域名不可达）→ 不回退 lite，避免双倍超时等待。"""
    _reset_web_state()
    calls = []

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        calls.append(url)
        raise ConnectionError("timed out")

    try:
        with mock.patch("requests.get", side_effect=_fake):
            out = ai_cmd._exec_web_search_multi({"query": "x", "engines": ["duckduckgo"]})
        assert len(calls) == 1, f"传输层失败不应再试 lite: {calls}"
        assert "duckduckgo:x" in out and "timed out" in out, out
        print("PASS 传输层失败跳过 lite 回退（避免双倍超时）")
    finally:
        _reset_web_state()  # 失败缓存会污染同键后续测试


def test_bing_retry_on_empty_then_success():
    """Bing 瞬时空页 → 自动重试一次。"""
    _reset_web_state()
    state = {"n": 0}

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        state["n"] += 1
        if state["n"] == 1:
            return _FakeResp("<html></html>")
        return _FakeResp(BING_HTML)

    with mock.patch("requests.get", side_effect=_fake):
        out = ai_cmd._exec_web_search_multi({"query": "x", "engines": ["bing"]})
    assert "Example Docs" in out, out
    assert state["n"] == 2, f"应重试一次: {state['n']}"
    print("PASS Bing 空页重试一次后成功")


def test_parallel_search_preserves_query_major_order():
    """并行搜索结果仍按「查询 → 引擎」顺序输出（确定性）。

    两个查询返回不同结果（避免跨查询去重把第二个查询清空），
    验证并行收集后输出仍保持「查询 → 引擎」原顺序。
    """
    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        if "q=zzz" in url:
            if "duckduckgo" in url:
                return _FakeResp(
                    '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fzeta.example%2Fz">'
                    'Zeta Result</a>')
            return _FakeResp(
                '<li class="b_algo"><h2><a href="https://zeta.example/z2">Zeta Bing</a></h2></li>')
        return _FakeResp(DDG_HTML if "duckduckgo" in url else BING_HTML)

    with mock.patch("requests.get", side_effect=_fake):
        out = ai_cmd._exec_web_search_multi({
            "query": "aaa", "queries": ["zzz"], "engines": ["duckduckgo", "bing"],
        })
    i_aaa = out.find("查询: aaa")
    i_zzz = out.find("查询: zzz")
    assert i_aaa != -1 and i_zzz != -1 and i_aaa < i_zzz, \
        f"输出应按查询顺序（aaa 在 zzz 前）: {out}"
    print("PASS 并行搜索输出保持查询主序确定性")


def test_title_entities_unescaped():
    """标题/摘要中的 HTML 实体应解码（A &amp; B → A & B）。"""
    _reset_web_state()
    html = ('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fe">'
            'A &amp; B Test</a>')
    with mock.patch("requests.get", side_effect=lambda *a, **k: _FakeResp(html)):
        out = ai_cmd._exec_web_search_multi({"query": "x", "engines": ["duckduckgo"]})
    assert "A & B Test" in out and "&amp;" not in out, out
    print("PASS 标题 HTML 实体解码")


def test_serp_cache_reuses_results():
    """同键查询 15 分钟内命中缓存：第二次调用零网络请求。"""
    _reset_web_state()
    calls = []

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        calls.append(url)
        return _FakeResp(BING_HTML)

    try:
        with mock.patch("requests.get", side_effect=_fake):
            out1 = ai_cmd._exec_web_search_multi({"query": "x", "engines": ["bing"]})
            out2 = ai_cmd._exec_web_search_multi({"query": "x", "engines": ["bing"]})
        assert "Example Docs" in out1 and "Example Docs" in out2
        assert len(calls) == 1, f"第二次应命中缓存: {calls}"
        print("PASS SERP 缓存：同键第二次调用零网络请求")
    finally:
        _reset_web_state()


def test_engine_degraded_after_repeated_failures():
    """连续 3 次传输失败 → 引擎降级：后续跳过请求，降级期过后自动恢复。"""
    _reset_web_state()
    calls = []

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        calls.append(url)
        raise ConnectionError("boom")

    try:
        with mock.patch("requests.get", side_effect=_fake):
            for i in range(3):
                ai_cmd._exec_web_search_multi({"query": f"x{i}", "engines": ["bing"]})
        n_before = len(calls)
        out = ai_cmd._exec_web_search_multi({"query": "x9", "engines": ["bing"]})
        assert len(calls) == n_before, f"降级后不应再发请求: {calls}"
        assert "降级" in out, out
        # 降级期结束 → 自动恢复试探
        ai_cmd._WEB_ENGINE_DEGRADED_UNTIL["bing"] = ai_cmd.time.time() - 1
        with mock.patch("requests.get", side_effect=_fake):
            ai_cmd._exec_web_search_multi({"query": "x8", "engines": ["bing"]})
        assert len(calls) > n_before, "降级期过后应恢复请求"
        print("PASS 引擎降级：连续失败 3 次跳过，到期自动恢复")
    finally:
        _reset_web_state()


def test_search_budget_cuts_off_slow_engine():
    """搜索总预算：慢引擎未在预算内完成 → 标注超时，其余引擎结果照常。"""
    _reset_web_state()

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        if "duckduckgo" in url:
            ai_cmd.time.sleep(0.3)  # 模拟慢引擎
            return _FakeResp(DDG_HTML)
        return _FakeResp(BING_HTML)

    try:
        with mock.patch("requests.get", side_effect=_fake), \
             mock.patch("bin.ai_lib.web_search._WEB_SEARCH_BUDGET", 0.05):
            out = ai_cmd._exec_web_search_multi({"query": "x", "engines": ["duckduckgo", "bing"]})
        assert "Example Docs" in out, f"快引擎结果应照常返回: {out}"
        assert "超时未完成" in out, f"慢引擎应被预算截断并标注: {out}"
        print("PASS 搜索总预算：到点收工，不等最慢引擎")
    finally:
        ai_cmd.time.sleep(0.4)  # 等慢引擎后台线程结束，避免其写入污染后续测试
        _reset_web_state()


def test_fetch_relevance_prescreen_skips_unrelated():
    """mixed 自动抓页：低相关 SERP 页跳过并注明，相关页正常抓取。"""
    _reset_web_state()
    bing_html = (
        '<li class="b_algo"><h2><a href="https://unrelated-site.com/home">Unrelated Home</a></h2></li>'
        '<li class="b_algo"><h2><a href="https://example.com/docs">Example Docs</a></h2></li>'
    )

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        if "bing.com" in url:
            return _FakeResp(bing_html)
        if "example.com/docs" in url:
            return _FakeResp(PAGE_HTML)
        return _FakeResp("<html></html>")

    try:
        with mock.patch("requests.get", side_effect=_fake), \
             mock.patch("bin.ai_lib.web_search._load_web_ai_assist_flag", return_value=False):
            out = ai_cmd._exec_web_search_multi({
                "query": "example docs", "engines": ["bing"],
                "fetch_pages": True, "fetch_limit": 2,
            })
        assert "低相关跳过" in out, out
        assert "unrelated-site.com/home" in out, out
        assert "Hello world content" in out, f"相关页应正常抓取: {out}"
        print("PASS 相关性预筛：低相关 SERP 页跳过并注明，相关页照常")
    finally:
        _reset_web_state()


def test_full_snippet_output():
    """snippet 完整输出（不截断 200 字符）：长摘要信息不丢失。"""
    _reset_web_state()
    long_snip = "snippet word " * 20  # 300 字符
    bing_html = (f'<li class="b_algo"><h2><a href="https://example.com/l">Long Snippet</a></h2>'
                 f'<p>{long_snip}</p></li>')
    try:
        with mock.patch("requests.get", side_effect=lambda *a, **k: _FakeResp(bing_html)):
            out = ai_cmd._exec_web_search_multi({"query": "x", "engines": ["bing"]})
        assert len(long_snip) > 200, "测试前提：摘要应超过旧截断阈值"
        assert long_snip.strip() in out, f"snippet 应完整输出: {out[:300]}"
        print("PASS snippet 完整输出（不截断 200 字符）")
    finally:
        _reset_web_state()


def test_topics_batch_parallel():
    """topics 批量：多个独立主题一次并行查询，分栏输出，参数继承与覆盖生效。"""
    _reset_web_state()

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        if "duckduckgo" in url:
            return _FakeResp(DDG_HTML)
        if "example.com/docs" in url:
            return _FakeResp(PAGE_HTML)
        return _FakeResp(BING_HTML)

    try:
        with mock.patch("requests.get", side_effect=_fake), \
             mock.patch("bin.ai_lib.web_search._load_web_ai_assist_flag", return_value=False):
            out = ai_cmd._exec_web_search_multi({
                "topics": [
                    {"query": "example docs", "engines": ["bing"],
                     "fetch_pages": True, "fetch_limit": 1},
                    {"query": "beta", "engines": ["duckduckgo"]},
                ],
            })
        assert "web_search(topics)" in out and "2 个独立主题" in out, out
        assert "## 主题 1: example docs" in out and "## 主题 2: beta" in out, out
        assert "Example Docs" in out, "主题 1 结果应出现"
        assert "Hello world content" in out, "主题 1 应抓页（fetch_pages 覆盖生效）"
        assert "Example Blog" in out, "主题 2 结果应出现（引擎独立）"
        print("PASS topics 批量并行：多主题分栏、参数继承/覆盖生效")
    finally:
        _reset_web_state()


def test_topics_validation_and_json():
    """topics 校验：空 query 忽略、超 5 个截断、JSON 结构；无 topics 回落单主题。"""
    _reset_web_state()

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        return _FakeResp(DDG_HTML if "duckduckgo" in url else BING_HTML)

    try:
        with mock.patch("requests.get", side_effect=_fake):
            out = ai_cmd._exec_web_search_multi({
                "output_format": "json",
                "topics": [{"query": ""}, {"query": "a"}, {"query": "b"}, {"query": "c"},
                           {"query": "d"}, {"query": "e"}, {"query": "f"}],
            })
        d = json.loads(out)
        assert d["action"] == "topics" and d["topic_count"] == 5, d
        assert len(d["topics"]) == 5, "空 query 应忽略、超 5 个截断"
        assert all(t["query"] and t["output"] for t in d["topics"]), d
        # 无 topics → 走单主题路径（原有行为不变）
        with mock.patch("requests.get", side_effect=_fake):
            out2 = ai_cmd._exec_web_search_multi({"query": "x", "engines": ["bing"]})
        assert "web_search(topics)" not in out2, out2
        print("PASS topics 校验：空 query 忽略、上限 5 截断、JSON 结构、无 topics 回落单主题")
    finally:
        _reset_web_state()


def test_query_enhance_generates_variants():
    """查询增强：英文长句去停用词、中文技术查询加英文变体、短查询不扩展。"""
    assert ai_cmd._web_query_enhance("how to deploy nginx") == [
        "how to deploy nginx", "deploy nginx"], \
        ai_cmd._web_query_enhance("how to deploy nginx")
    v = ai_cmd._web_query_enhance("deepseek api 文档")
    assert v[0] == "deepseek api 文档" and "deepseek api" in v, v
    assert ai_cmd._web_query_enhance("hello world") == ["hello world"], "短查询不扩展"
    assert ai_cmd._web_query_enhance("") == [] and ai_cmd._web_query_enhance("  ") == []
    print("PASS 查询增强：长句去停用词、中英变体、短查询不动")


def test_rerank_authority_and_junk_signals():
    """结果重排：权威域前置、垃圾域降权但保留、查询主序不变。"""
    results = [
        {"title": "Deploy Nginx", "url": "https://junk-top10.com/deploy-nginx",
         "snippet": "", "query": "deploy nginx"},
        {"title": "nginx documentation", "url": "https://nginx.org/en/docs/",
         "snippet": "official", "query": "deploy nginx"},
        {"title": "zeta", "url": "https://zeta.example/z", "snippet": "", "query": "zzz"},
    ]
    out = ai_cmd._web_rerank(results)
    assert out[0]["url"] == "https://nginx.org/en/docs/", f"权威域应排最前: {out}"
    assert out[1]["url"] == "https://junk-top10.com/deploy-nginx", f"垃圾域降权但保留: {out}"
    assert out[-1]["query"] == "zzz", f"查询主序保留: {out}"
    print("PASS 重排：权威域前置、垃圾域降权、查询主序保留")


def test_rerank_applied_in_search_output():
    """集成：搜索结果按重排后顺序输出（nginx.org 排在 junk 域前）。"""
    _reset_web_state()
    bing_html = (
        '<li class="b_algo"><h2><a href="https://junk-top10.com/docs">Junk Deploy Nginx</a></h2>'
        '<p>noise</p></li>'
        '<li class="b_algo"><h2><a href="https://nginx.org/en/docs/">nginx documentation</a></h2>'
        '<p>official guide</p></li>'
    )

    def _fake(url, timeout=15, headers=None, allow_redirects=True):
        return _FakeResp(bing_html)

    try:
        with mock.patch("requests.get", side_effect=_fake):
            out = ai_cmd._exec_web_search_multi({"query": "deploy nginx", "engines": ["bing"]})
        i_junk = out.find("junk-top10")
        i_docs = out.find("nginx.org")
        assert i_docs != -1 and i_junk != -1 and i_docs < i_junk, \
            f"权威域应排在垃圾域前: {out}"
        print("PASS 集成：搜索结果按重排顺序输出（nginx.org 在 junk 前）")
    finally:
        _reset_web_state()


if __name__ == "__main__":
    test_multi_query_multi_engine_dedup_and_filter()
    test_fetch_mode_uses_given_urls_only()
    test_mixed_with_explicit_urls()
    test_safe_search_and_prefs_passed_to_engines()
    test_fetch_pages_and_max_chars()
    test_json_output_and_validation()
    test_curl_fallback_when_requests_missing()
    test_fetch_ssrf_guard()
    test_tool_schema_has_new_params()
    test_key_line_compression_keeps_matching_sentences()
    test_ai_assist_summarize_long_page()
    test_ai_assist_fallback_to_compression()
    test_ai_assist_global_flag_and_param_override()
    test_ddg_lite_fallback_when_html_empty()
    test_ddg_anomaly_detection_reports_block()
    test_bing_retry_on_empty_then_success()
    test_ddg_skip_lite_on_transport_failure()
    test_parallel_search_preserves_query_major_order()
    test_title_entities_unescaped()
    test_serp_cache_reuses_results()
    test_engine_degraded_after_repeated_failures()
    test_search_budget_cuts_off_slow_engine()
    test_fetch_relevance_prescreen_skips_unrelated()
    test_full_snippet_output()
    test_topics_batch_parallel()
    test_topics_validation_and_json()
    test_query_enhance_generates_variants()
    test_rerank_authority_and_junk_signals()
    test_rerank_applied_in_search_output()
    print("\nALL PASS")
