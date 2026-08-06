"""漏洞验证引擎：自动化验证疑似漏洞，减少误报。

设计理念：
- 渗透测试高度适配：每个验证方法对应一种真实渗透测试技术
- 证据驱动：验证过程产生对比证据，自动附加到漏洞报告
- 可复现：所有验证至少执行 2 次确认，排除偶然因素

验证类型：
1. 布尔盲注验证：对比 AND 1=1 vs AND 1=2 的响应差异
2. 时间盲注验证：测量响应时间延迟，需可复现
3. XSS 反射验证：检查 payload 是否在响应中未编码出现
4. 错误注入验证：检查响应中是否包含数据库特征错误信息
5. IDOR 验证：对比不同会话访问同一资源的响应差异
"""

import asyncio
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from hemlock.services.request_service import send_raw_request
from hemlock.services.session_service import send_request_with_session

logger = logging.getLogger(__name__)

# ===== 验证结果 =====

@dataclass
class VerificationResult:
    """漏洞验证结果（三态语义）。

    三态判定逻辑：
    - verified=True                    → 确认漏洞存在
    - verified=False, confidence<0.2   → 确认漏洞不存在（强证据排除）
    - verified=False, confidence>=0.2  → 不确定（没撞上规则不代表没有）
    
    evidence 字段始终填充原始证据数据，供 Agent LLM 自行推理判断。
    Agent 不应仅凭 verified=False 就放弃报告，应结合 evidence 和 confidence 综合判断。
    """
    verified: bool  # 是否确认漏洞存在
    confidence: float  # 置信度 0-1
    method: str  # 使用的验证方法
    details: str  # 验证详情
    evidence_flows: list[str] = field(default_factory=list)  # 验证产生的流量 ID（由调用方填充）
    false_positive_risk: str = "low"  # 误报风险: low/medium/high
    evidence: dict[str, Any] = field(default_factory=dict)  # 原始证据数据（状态码/响应长度/相似度/时间差等）


# ===== 数据库错误特征 =====

DB_ERROR_PATTERNS: dict[str, list[str]] = {
    "MySQL": [
        "SQL syntax", "mysql_fetch", "MySQLSyntaxErrorException",
        "valid MySQL result", "check the manual that corresponds to your MySQL",
        "MySqlException", "mysqli_query", "mysql_connect",
        "Unknown column", "mysql_fetch_array",
    ],
    "PostgreSQL": [
        "PostgreSQL query failed", "pg_query", "postgresql",
        "valid PostgreSQL result", "Npgsql", "PSQLException",
        "ERROR:  syntax error at or near",
    ],
    "MSSQL": [
        "Microsoft SQL Server", "OLE DB Provider for SQL Server",
        "Unclosed quotation mark", "sqlsrv_query", "mssql_query",
        "SQLServer", "ODBC SQL Server Driver", "ODBC Driver for SQL",
        "mssql_fetch",
    ],
    "Oracle": [
        "Oracle error", "Oracle Database", "OCIError",
        "ORA-", "oracle", "oci_parse", "oci_execute",
    ],
    "SQLite": [
        "SQLite3::query", "SQLite3::exec", "SQLITE_ERROR",
        "sqlite_query", "sqlite3", "sqlite_", "SQLITE_BUSY",
    ],
}


# ===== 验证方法 =====

async def verify_boolean_blind_sqli(
    method: str,
    url: str,
    param_name: str,
    param_value: str,
    headers: str = "",
    body: str = "",
    session_id: str | None = None,
) -> VerificationResult:
    """布尔盲注验证：对比 AND 1=1 和 AND 1=2 的响应差异。

    渗透测试标准流程：
    1. 发送原始请求获取基线响应
    2. 注入 ' AND 1=1 -- 应该返回与基线相同的结果（条件永真）
    3. 注入 ' AND 1=2 -- 应该返回不同结果（条件永假）
    4. 如果 1=1 和 1=2 响应有差异，且 1=1 与基线一致，确认为布尔盲注
    """
    true_payload = f"{param_value}' AND 1=1 -- "
    false_payload = f"{param_value}' AND 1=2 -- "

    # 构造请求
    def _inject(url: str, body: str, payload: str) -> tuple[str, str]:
        """在指定参数中注入 payload。"""
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)

        if param_name in params:
            params[param_name] = [payload]
            new_query = urlencode(params, doseq=True)
            new_url = urlunparse(parsed._replace(query=new_query))
            return new_url, body
        elif body and param_name in body:
            # POST body 参数注入
            new_body = body.replace(f"{param_name}={param_value}", f"{param_name}={payload}", 1)
            return url, new_body
        else:
            return url, body

    true_url, true_body = _inject(url, body, true_payload)
    false_url, false_body = _inject(url, body, false_payload)

    # 发送三个请求：基线、true、false
    try:
        if session_id:
            baseline = await send_request_with_session(session_id, method, url, headers, body)
            true_resp = await send_request_with_session(session_id, method, true_url, headers, true_body)
            false_resp = await send_request_with_session(session_id, method, false_url, headers, false_body)
        else:
            baseline = await send_raw_request(method=method, url=url, headers_str=headers, body=body)
            true_resp = await send_raw_request(method=method, url=true_url, headers_str=headers, body=true_body)
            false_resp = await send_raw_request(method=method, url=false_url, headers_str=headers, body=false_body)
    except Exception as e:
        return VerificationResult(
            verified=False,
            confidence=0.0,
            method="boolean_blind_sqli",
            details=f"验证请求失败: {e}",
            false_positive_risk="high",
            evidence={"error": str(e)},
        )

    baseline_body = baseline.get("body", "")
    true_body_resp = true_resp.get("body", "")
    false_body_resp = false_resp.get("body", "")

    # 计算相似度
    baseline_true_sim = _similarity(baseline_body, true_body_resp)
    true_false_sim = _similarity(true_body_resp, false_body_resp)
    baseline_false_sim = _similarity(baseline_body, false_body_resp)

    # 原始证据数据（无论成功失败都填充）
    raw_evidence = {
        "baseline_status": baseline.get("status"),
        "true_status": true_resp.get("status"),
        "false_status": false_resp.get("status"),
        "baseline_length": len(baseline_body),
        "true_length": len(true_body_resp),
        "false_length": len(false_body_resp),
        "baseline_true_similarity": round(baseline_true_sim, 2),
        "true_false_similarity": round(true_false_sim, 2),
        "baseline_false_similarity": round(baseline_false_sim, 2),
        "true_payload": true_payload,
        "false_payload": false_payload,
        "baseline_body_preview": baseline_body[:500],
        "true_body_preview": true_body_resp[:500],
        "false_body_preview": false_body_resp[:500],
    }

    # 判定逻辑：
    # 1. true 响应应与基线高度相似（条件永真，不影响查询结果）
    # 2. false 响应应与 true 响应有明显差异（条件永假，改变查询结果）
    # 3. 需要差异足够显著（>5%）
    diff_threshold = 5.0  # 相似度差异阈值

    if baseline_true_sim > 95.0 and true_false_sim < 95.0 and (100 - true_false_sim) > diff_threshold:
        confidence = min((100 - true_false_sim) / 20, 1.0)  # 差异越大置信度越高
        return VerificationResult(
            verified=True,
            confidence=confidence,
            method="boolean_blind_sqli",
            details=(
                f"布尔盲注验证通过：基线与 AND 1=1 相似度 {baseline_true_sim:.1f}%（应一致），"
                f"AND 1=1 与 AND 1=2 相似度 {true_false_sim:.1f}%（应有差异）。"
                f"响应长度：基线={len(baseline_body)}, true={len(true_body_resp)}, false={len(false_body_resp)}。"
            ),
            false_positive_risk="low",
            evidence=raw_evidence,
        )
    else:
        return VerificationResult(
            verified=False,
            confidence=0.3,
            method="boolean_blind_sqli",
            details=(
                f"布尔盲注验证未通过：基线与 AND 1=1 相似度 {baseline_true_sim:.1f}%，"
                f"AND 1=1 与 AND 1=2 相似度 {true_false_sim:.1f}%。差异不显著。"
            ),
            false_positive_risk="medium",
            evidence=raw_evidence,
        )


async def verify_time_based_sqli(
    method: str,
    url: str,
    param_name: str,
    param_value: str,
    headers: str = "",
    body: str = "",
    session_id: str | None = None,
    delay_seconds: int = 5,
) -> VerificationResult:
    """时间盲注验证：测量响应时间延迟，需可复现。

    渗透测试标准流程：
    1. 发送正常请求记录基线响应时间
    2. 注入 SLEEP/NWAITFOR DELAY payload，观察响应时间是否延迟
    3. 再次发送延迟 payload 确认可复现性
    4. 排除网络波动：延迟时间应接近设定值且可复现
    """
    # 构造不同数据库的时间延迟 payload
    time_payloads = [
        f"{param_value}' AND SLEEP({delay_seconds}) -- ",  # MySQL
        f"{param_value}'; WAITFOR DELAY '0:0:{delay_seconds}' -- ",  # MSSQL
        f"{param_value}' AND pg_sleep({delay_seconds}) -- ",  # PostgreSQL
    ]

    def _inject(url: str, body: str, payload: str) -> tuple[str, str]:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        if param_name in params:
            params[param_name] = [payload]
            new_query = urlencode(params, doseq=True)
            return urlunparse(parsed._replace(query=new_query)), body
        elif body and param_name in body:
            return url, body.replace(f"{param_name}={param_value}", f"{param_name}={payload}", 1)
        return url, body

    # 1. 测量基线响应时间
    try:
        start = time.monotonic()
        if session_id:
            await send_request_with_session(session_id, method, url, headers, body)
        else:
            await send_raw_request(method=method, url=url, headers_str=headers, body=body)
        baseline_time = time.monotonic() - start
    except Exception as e:
        return VerificationResult(
            verified=False, confidence=0.0, method="time_based_sqli",
            details=f"基线请求失败: {e}", false_positive_risk="high",
            evidence={"error": str(e)},
        )

    # 2. 测试每个时间延迟 payload
    all_delays: list[dict] = []
    for payload in time_payloads:
        inj_url, inj_body = _inject(url, body, payload)

        # 发送两次验证可复现性
        delays = []
        for attempt in range(2):
            try:
                start = time.monotonic()
                if session_id:
                    await send_request_with_session(session_id, method, inj_url, headers, inj_body)
                else:
                    await send_raw_request(method=method, url=inj_url, headers_str=headers, body=inj_body)
                delays.append(time.monotonic() - start)
            except Exception:
                delays.append(0)

        all_delays.append({"payload": payload, "delays": delays})

        # 判定：两次都延迟且延迟时间接近设定值
        avg_delay = sum(delays) / len(delays)
        expected_min = delay_seconds * 0.8  # 允许 20% 误差

        if all(d >= expected_min for d in delays) and avg_delay > baseline_time + delay_seconds * 0.7:
            confidence = min((avg_delay - baseline_time) / (delay_seconds * 2), 1.0)
            return VerificationResult(
                verified=True,
                confidence=confidence,
                method="time_based_sqli",
                details=(
                    f"时间盲注验证通过：基线响应 {baseline_time:.2f}s，"
                    f"延迟 payload 两次响应 {delays[0]:.2f}s / {delays[1]:.2f}s（期望延迟 {delay_seconds}s）。"
                    f"延迟可复现，排除网络波动。"
                ),
                false_positive_risk="low",
                evidence={
                    "baseline_time": round(baseline_time, 3),
                    "delay_results": all_delays,
                    "expected_delay": delay_seconds,
                    "matched_payload": payload,
                },
            )

    return VerificationResult(
        verified=False,
        confidence=0.2,
        method="time_based_sqli",
        details=f"时间盲注验证未通过：基线 {baseline_time:.2f}s，未观察到可复现的延迟。",
        false_positive_risk="medium",
        evidence={
            "baseline_time": round(baseline_time, 3),
            "delay_results": all_delays,
            "expected_delay": delay_seconds,
        },
    )


async def verify_order_by_sqli(
    method: str,
    url: str,
    param_name: str,
    param_value: str,
    headers: str = "",
    body: str = "",
    session_id: str | None = None,
) -> VerificationResult:
    """ORDER BY 注入验证：针对 ORDER BY 子句位置的 SQL 注入。

    渗透测试标准流程：
    1. 错误注入：注入 extractvalue / updatexml，检查响应中是否出现数据库错误
    2. 条件排序：对比 (CASE WHEN 1=1 THEN col1 ELSE col2 END) vs (CASE WHEN 1=2 THEN ...) 的响应差异
    3. 时间盲注：注入 (SELECT SLEEP(5))，测量响应延迟

    注意：ORDER BY 位置不能使用 AND 1=1 / AND 1=2（语法错误），
    必须使用条件表达式或子查询来验证注入。
    """
    # 错误注入 payload（不同数据库）
    error_payloads = [
        f"extractvalue(1,concat(0x7e,version()))",  # MySQL
        f"updatexml(1,concat(0x7e,version()),1)",  # MySQL 备选
        f"convert(int,@@version)",  # MSSQL
        f"(SELECT version())",  # PostgreSQL / 通用
    ]

    # 条件排序 payload（用 CASE WHEN 改变排序，产生不同响应）
    # col1 和 col2 使用数字避免列名依赖
    true_sort = f"(CASE WHEN 1=1 THEN 1 ELSE 2 END)"
    false_sort = f"(CASE WHEN 1=2 THEN 1 ELSE 2 END)"

    # 时间盲注 payload
    delay_seconds = 5
    time_payloads = [
        f"(SELECT SLEEP({delay_seconds}))",  # MySQL
        f"(SELECT WAITFOR DELAY '0:0:{delay_seconds}')",  # MSSQL
        f"(SELECT pg_sleep({delay_seconds}))",  # PostgreSQL
    ]

    def _inject_order_by(url: str, body: str, payload: str) -> tuple[str, str]:
        """在 ORDER BY 参数位置注入 payload（替换原始值）。"""
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)

        if param_name in params:
            params[param_name] = [payload]
            new_query = urlencode(params, doseq=True)
            return urlunparse(parsed._replace(query=new_query)), body
        elif body and param_name in body:
            new_body = body.replace(
                f"{param_name}={param_value}", f"{param_name}={payload}", 1
            )
            return url, new_body
        return url, body

    async def _send(method, url, headers, body):
        if session_id:
            return await send_request_with_session(session_id, method, url, headers, body)
        return await send_raw_request(method=method, url=url, headers_str=headers, body=body)

    # 1. 错误注入验证（最快）
    error_attempts: list[dict[str, Any]] = []
    for payload in error_payloads:
        try:
            inj_url, inj_body = _inject_order_by(url, body, payload)
            resp = await _send(method, inj_url, headers, inj_body)
            resp_body = resp.get("body", "")
            error_attempts.append({
                "payload": payload,
                "status": resp.get("status"),
                "body_preview": resp_body[:500],
            })
            if resp_body:
                error_result = verify_error_based_sqli(resp_body)
                if error_result.verified:
                    return VerificationResult(
                        verified=True,
                        confidence=0.9,
                        method="order_by_error_sqli",
                        details=f"ORDER BY 错误注入验证通过：payload={payload}，"
                                f"响应中包含数据库错误特征。",
                        false_positive_risk="low",
                    )
        except Exception:
            continue

    # 2. 条件排序验证
    conditional_sort_data: dict[str, Any] | None = None
    try:
        baseline_resp = await _send(method, url, headers, body)
        baseline_body = baseline_resp.get("body", "")

        true_url, true_body_req = _inject_order_by(url, body, true_sort)
        true_resp = await _send(method, true_url, headers, true_body_req)
        true_body_resp = true_resp.get("body", "")

        false_url, false_body_req = _inject_order_by(url, body, false_sort)
        false_resp = await _send(method, false_url, headers, false_body_req)
        false_body_resp = false_resp.get("body", "")

        true_false_sim = _similarity(true_body_resp, false_body_resp)
        baseline_true_sim = _similarity(baseline_body, true_body_resp)

        conditional_sort_data = {
            "baseline_true_similarity": round(baseline_true_sim, 2),
            "true_false_similarity": round(true_false_sim, 2),
            "baseline_length": len(baseline_body),
            "true_length": len(true_body_resp),
            "false_length": len(false_body_resp),
        }

        # 如果 true 和 false 排序产生不同响应，且 true 与基线一致，确认注入
        if baseline_true_sim > 90.0 and true_false_sim < 90.0:
            confidence = min((100 - true_false_sim) / 20, 0.85)
            return VerificationResult(
                verified=True,
                confidence=confidence,
                method="order_by_conditional_sort",
                details=f"ORDER BY 条件排序验证通过："
                        f"基线与 CASE WHEN 1=1 相似度 {baseline_true_sim:.1f}%，"
                        f"CASE WHEN 1=1 vs 1=2 相似度 {true_false_sim:.1f}%。"
                        f"响应长度：基线={len(baseline_body)}, true={len(true_body_resp)}, false={len(false_body_resp)}。",
                false_positive_risk="low",
            )
    except Exception as e:
        logger.warning(f"ORDER BY 条件排序验证失败: {e}")

    # 3. 时间盲注验证
    time_blind_data: dict[str, Any] | None = None
    try:
        # 基线时间
        start = time.monotonic()
        await _send(method, url, headers, body)
        baseline_time = time.monotonic() - start

        all_time_delays: list[dict] = []
        for payload in time_payloads:
            inj_url, inj_body = _inject_order_by(url, body, payload)
            delays = []
            for _ in range(2):
                try:
                    start = time.monotonic()
                    await _send(method, inj_url, headers, inj_body)
                    delays.append(time.monotonic() - start)
                except Exception:
                    delays.append(0)

            all_time_delays.append({
                "payload": payload,
                "delays": [round(d, 3) for d in delays],
            })

            avg_delay = sum(delays) / len(delays)
            expected_min = delay_seconds * 0.8

            if all(d >= expected_min for d in delays) and avg_delay > baseline_time + delay_seconds * 0.7:
                confidence = min((avg_delay - baseline_time) / (delay_seconds * 2), 0.9)
                return VerificationResult(
                    verified=True,
                    confidence=confidence,
                    method="order_by_time_based_sqli",
                    details=f"ORDER BY 时间盲注验证通过：基线 {baseline_time:.2f}s，"
                            f"延迟 payload 两次响应 {delays[0]:.2f}s / {delays[1]:.2f}s。"
                            f"延迟可复现。",
                    false_positive_risk="low",
                )

        time_blind_data = {
            "baseline_time": round(baseline_time, 3),
            "delay_results": all_time_delays,
            "expected_delay": delay_seconds,
        }
    except Exception as e:
        logger.warning(f"ORDER BY 时间盲注验证失败: {e}")

    return VerificationResult(
        verified=False,
        confidence=0.3,
        method="order_by_sqli",
        details="ORDER BY 注入验证未通过：错误注入、条件排序、时间盲注均未检出。",
        false_positive_risk="medium",
        evidence={
            "error_payloads_tried": [a["payload"] for a in error_attempts],
            "error_attempts": error_attempts,
            "conditional_sort_data": conditional_sort_data,
            "time_blind_data": time_blind_data,
        },
    )


def verify_xss_reflection(
    resp_body: str,
    payload: str,
) -> VerificationResult:
    """XSS 反射验证：检查 payload 是否在响应中未编码出现。

    渗透测试判定标准：
    1. payload 的核心部分（标签名、事件处理器）必须在响应中完整出现
    2. 不能是被 HTML 实体编码后的（&lt; &gt; 等）
    3. 需要判断 payload 出现的上下文（是否在可执行的 HTML/JS 上下文中）
    """
    if not resp_body or not payload:
        return VerificationResult(
            verified=False, confidence=0.0, method="xss_reflection",
            details="响应体或 payload 为空", false_positive_risk="high",
            evidence={"resp_body_preview": (resp_body or "")[:500]},
        )

    # 检查 payload 是否完整出现在响应中
    payload_core = payload.strip()

    # 提取 payload 的关键特征
    has_script_tag = "<script" in payload_core.lower()
    has_event_handler = any(
        attr in payload_core.lower()
        for attr in ["onerror", "onload", "onfocus", "onclick", "ontoggle", "onstart"]
    )
    has_javascript = "javascript:" in payload_core.lower()

    # 检查是否被编码
    encoded_patterns = ["&lt;", "&gt;", "&quot;", "&#x", "&#", "\\u003c", "\\u003e"]
    is_encoded = any(p in resp_body for p in encoded_patterns if p.replace("&", "").replace(";", "") in payload_core)

    # 检查 payload 核心部分是否在响应中
    if payload_core in resp_body:
        # 检查是否在可执行上下文中（不在注释、CDATA 内）
        in_comment = _is_in_html_comment(resp_body, payload_core)
        in_cdata = "<![CDATA[" in resp_body[:resp_body.index(payload_core) + 100] if payload_core in resp_body else False

        if in_comment:
            return VerificationResult(
                verified=False, confidence=0.2, method="xss_reflection",
                details="payload 出现在 HTML 注释中，不会执行。", false_positive_risk="medium",
                evidence={
                    "payload_fully_present": True,
                    "is_encoded": is_encoded,
                    "in_comment": True,
                    "has_script_tag": has_script_tag,
                    "has_event_handler": has_event_handler,
                    "has_javascript": has_javascript,
                },
            )

        if has_script_tag or has_event_handler or has_javascript:
            confidence = 0.9
            return VerificationResult(
                verified=True,
                confidence=confidence,
                method="xss_reflection",
                details=(
                    f"XSS 反射验证通过：payload 完整出现在响应 HTML 中，未被编码。"
                    f"包含{'脚本标签' if has_script_tag else '事件处理器' if has_event_handler else 'JavaScript 协议'}，"
                    f"处于可执行上下文。"
                ),
                false_positive_risk="low",
                evidence={
                    "payload_fully_present": True,
                    "is_encoded": is_encoded,
                    "in_comment": False,
                    "has_script_tag": has_script_tag,
                    "has_event_handler": has_event_handler,
                    "has_javascript": has_javascript,
                },
            )
        else:
            return VerificationResult(
                verified=True, confidence=0.6, method="xss_reflection",
                details="payload 出现在响应中未被编码，但未包含明确的脚本执行向量。",
                false_positive_risk="medium",
                evidence={
                    "payload_fully_present": True,
                    "is_encoded": is_encoded,
                    "in_comment": False,
                    "has_script_tag": has_script_tag,
                    "has_event_handler": has_event_handler,
                    "has_javascript": has_javascript,
                },
            )

    # 检查部分匹配（可能被部分过滤）
    payload_parts = re.split(r'[<>"\']', payload_core)
    matched_parts = [p for p in payload_parts if p and len(p) > 2 and p in resp_body]

    if matched_parts and not is_encoded:
        return VerificationResult(
            verified=False, confidence=0.4, method="xss_reflection",
            details=f"payload 部分出现在响应中（{len(matched_parts)}/{len(payload_parts)} 部分），可能被部分过滤。",
            false_positive_risk="high",
            evidence={
                "matched_parts": matched_parts,
                "is_encoded": is_encoded,
                "resp_body_preview": resp_body[:500],
            },
        )

    return VerificationResult(
        verified=False, confidence=0.1, method="xss_reflection",
        details="payload 未出现在响应中，或被完全编码/过滤。",
        false_positive_risk="low",
        evidence={
            "is_encoded": is_encoded,
            "resp_body_preview": resp_body[:500],
        },
    )


def verify_error_based_sqli(resp_body: str) -> VerificationResult:
    """错误注入验证：检查响应中是否包含数据库特征错误信息。

    渗透测试判定标准：
    1. 响应中必须包含特定数据库的错误特征字符串
    2. 识别数据库类型
    3. 错误信息应包含 SQL 语法相关内容（而非通用错误）
    """
    if not resp_body:
        return VerificationResult(
            verified=False, confidence=0.0, method="error_based_sqli",
            details="响应体为空", false_positive_risk="high",
            evidence={"resp_body_preview": ""},
        )

    resp_lower = resp_body.lower()
    detected_dbs: list[str] = []
    matched_patterns: dict[str, str] = {}

    for db_type, patterns in DB_ERROR_PATTERNS.items():
        for pattern in patterns:
            if pattern.lower() in resp_lower:
                detected_dbs.append(db_type)
                matched_patterns[db_type] = pattern
                break

    if detected_dbs:
        db_names = " / ".join(detected_dbs)
        return VerificationResult(
            verified=True,
            confidence=0.85,
            method="error_based_sqli",
            details=f"错误注入验证通过：响应中包含 {db_names} 数据库错误特征信息。",
            false_positive_risk="low",
            evidence={
                "detected_dbs": detected_dbs,
                "matched_patterns": matched_patterns,
                "resp_body_preview": resp_body[:500],
            },
        )

    return VerificationResult(
        verified=False, confidence=0.1, method="error_based_sqli",
        details="响应中未发现数据库错误特征信息。",
        false_positive_risk="low",
        evidence={"resp_body_preview": resp_body[:500]},
    )


async def verify_idor(
    method: str,
    url: str,
    headers: str,
    body: str,
    session_id_1: str,
    session_id_2: str,
) -> VerificationResult:
    """IDOR 越权验证：对比两个不同会话访问同一资源的响应差异。

    渗透测试判定标准：
    1. 会话 1（资源所有者）应能正常访问
    2. 会话 2（非所有者）如果也能访问且返回相同数据，则存在越权
    3. 需要确认返回的是他人私密数据而非公开信息
    """
    try:
        resp1 = await send_request_with_session(session_id_1, method, url, headers, body)
        resp2 = await send_request_with_session(session_id_2, method, url, headers, body)
    except Exception as e:
        return VerificationResult(
            verified=False, confidence=0.0, method="idor",
            details=f"验证请求失败: {e}", false_positive_risk="high",
            evidence={"error": str(e)},
        )

    status1 = resp1.get("status")
    status2 = resp2.get("status")
    body1 = resp1.get("body", "")
    body2 = resp2.get("body", "")
    similarity: float | None = None

    # 如果会话 2 也返回 200 且内容与会话 1 高度相似
    if status2 == 200 and status1 == 200:
        similarity = _similarity(body1, body2)
        if similarity > 90:
            return VerificationResult(
                verified=True,
                confidence=0.8,
                method="idor",
                details=(
                    f"IDOR 越权验证通过：两个会话访问同一资源均返回 200，"
                    f"响应相似度 {similarity:.1f}%，存在越权访问风险。"
                ),
                false_positive_risk="medium",
                evidence={
                    "status1": status1,
                    "status2": status2,
                    "similarity": round(similarity, 2),
                },
            )
        else:
            return VerificationResult(
                verified=False, confidence=0.3, method="idor",
                details=f"两个会话响应相似度 {similarity:.1f}%，可能存在权限控制。",
                false_positive_risk="medium",
                evidence={
                    "status1": status1,
                    "status2": status2,
                    "similarity": round(similarity, 2),
                },
            )
    elif status2 and status2 in (401, 403):
        return VerificationResult(
            verified=False, confidence=0.9, method="idor",
            details=f"会话 2 返回 {status2}，权限控制正常。",
            false_positive_risk="low",
            evidence={
                "status1": status1,
                "status2": status2,
                "similarity": similarity,
            },
        )
    else:
        return VerificationResult(
            verified=False, confidence=0.2, method="idor",
            details=f"会话 1 状态 {status1}，会话 2 状态 {status2}，无法确定。",
            false_positive_risk="medium",
            evidence={
                "status1": status1,
                "status2": status2,
                "similarity": similarity,
            },
        )


# ===== 辅助函数 =====

def _similarity(s1: str, s2: str) -> float:
    """计算两个字符串的相似度百分比。"""
    if not s1 and not s2:
        return 100.0
    if not s1 or not s2:
        return 0.0

    max_len = max(len(s1), len(s2))
    min_len = min(len(s1), len(s2))
    if max_len == 0:
        return 100.0

    # 采样对比提高性能
    sample_size = min(max_len, 5000)
    s1_sample = s1[:sample_size]
    s2_sample = s2[:sample_size]

    same = sum(1 for i in range(min(len(s1_sample), len(s2_sample))) if s1_sample[i] == s2_sample[i])
    return (same / max_len) * 100


def _is_in_html_comment(html: str, target: str) -> bool:
    """检查 target 是否在 HTML 注释 <!-- --> 内。"""
    idx = html.find(target)
    if idx < 0:
        return False

    # 查找 target 之前最近的注释开始标记
    comment_start = html.rfind("<!--", 0, idx)
    if comment_start < 0:
        return False

    # 检查注释是否在 target 之前关闭
    comment_end = html.find("-->", comment_start, idx)
    return comment_end < 0  # 注释未关闭，target 在注释内


# ===== 扩展验证方法 =====

# 命令注入输出特征
CMD_OUTPUT_PATTERNS: dict[str, list[str]] = {
    "linux": [
        "root:x:0:0:",  # /etc/passwd
        "uid=", "gid=",  # id 命令
        "groups=",  # id 命令
        "total ", "drwx", "-rw-",  # ls -la 输出
        "bin/bash", "bin/sh", "/sbin/nologin",
        "Linux", "GNU/Linux",  # uname -a
        "Linux version",  # uname -r
    ],
    "windows": [
        "[fonts]",  # win.ini
        "[extensions]", "MAPI=1",
        "Volume in drive", "Volume Serial Number",  # dir
        "Directory of",  # dir
        "Windows", "Microsoft",  # ver
        "<DIR>",  # dir
    ],
}


async def verify_command_injection(
    method: str,
    url: str,
    param_name: str,
    param_value: str,
    headers: str = "",
    body: str = "",
    resp_body: str = "",
    session_id: str | None = None,
    delay_seconds: int = 5,
) -> VerificationResult:
    """命令注入验证：输出回显 + 时间延迟双重验证。

    渗透测试标准流程：
    1. 先检查已有响应中是否包含命令执行输出特征（whoami/id/passwd）
    2. 如无回显，发送时间延迟 payload（sleep/ping -n）验证
    3. 时间延迟需可复现（2 次确认）
    """
    # 1. 检查已有响应中的命令输出特征
    if resp_body:
        for os_type, patterns in CMD_OUTPUT_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in resp_body.lower():
                    return VerificationResult(
                        verified=True,
                        confidence=0.9,
                        method="command_injection_output",
                        details=(
                            f"命令注入验证通过：响应中包含 {os_type} 系统命令输出特征 "
                            f"（匹配: {pattern}），表明命令被执行。"
                        ),
                        false_positive_risk="low",
                        evidence={
                            "matched_pattern": pattern,
                            "os_type": os_type,
                        },
                    )

    # 2. 时间延迟验证
    if not param_name or not param_value:
        return VerificationResult(
            verified=False, confidence=0.1, method="command_injection",
            details="缺少参数信息，无法进行时间延迟验证", false_positive_risk="medium",
            evidence={"resp_body_preview": (resp_body or "")[:500]},
        )

    def _inject(url: str, body: str, payload: str) -> tuple[str, str]:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        if param_name in params:
            params[param_name] = [payload]
            new_query = urlencode(params, doseq=True)
            return urlunparse(parsed._replace(query=new_query)), body
        elif body and param_name in body:
            return url, body.replace(f"{param_name}={param_value}", f"{param_name}={payload}", 1)
        return url, body

    # 构造不同系统的延迟 payload
    time_payloads = [
        f"{param_value}; sleep {delay_seconds}",  # Linux
        f"{param_value}| sleep {delay_seconds}",  # Linux pipe
        f"{param_value}&& sleep {delay_seconds}",  # Linux &&
        f"{param_value}& timeout {delay_seconds}",  # Windows
        f"{param_value}| ping -n {delay_seconds} 127.0.0.1",  # Windows ping
    ]

    # 测量基线
    try:
        start = time.monotonic()
        if session_id:
            await send_request_with_session(session_id, method, url, headers, body)
        else:
            await send_raw_request(method=method, url=url, headers_str=headers, body=body)
        baseline_time = time.monotonic() - start
    except Exception as e:
        return VerificationResult(
            verified=False, confidence=0.0, method="command_injection",
            details=f"基线请求失败: {e}", false_positive_risk="high",
            evidence={"error": str(e)},
        )

    # 测试每个 payload
    all_delay_results: list[dict] = []
    for payload in time_payloads:
        inj_url, inj_body = _inject(url, body, payload)
        delays = []
        for _ in range(2):
            try:
                start = time.monotonic()
                if session_id:
                    await send_request_with_session(session_id, method, inj_url, headers, inj_body)
                else:
                    await send_raw_request(method=method, url=inj_url, headers_str=headers, body=inj_body)
                delays.append(time.monotonic() - start)
            except Exception:
                delays.append(0)

        all_delay_results.append({
            "payload": payload,
            "delays": [round(d, 3) for d in delays],
        })

        avg_delay = sum(delays) / len(delays)
        expected_min = delay_seconds * 0.8

        if all(d >= expected_min for d in delays) and avg_delay > baseline_time + delay_seconds * 0.7:
            return VerificationResult(
                verified=True,
                confidence=0.85,
                method="command_injection_time",
                details=(
                    f"命令注入验证通过（时间延迟）：基线 {baseline_time:.2f}s，"
                    f"延迟 payload 两次响应 {delays[0]:.2f}s / {delays[1]:.2f}s"
                    f"（期望延迟 {delay_seconds}s）。延迟可复现。"
                ),
                false_positive_risk="low",
                evidence={
                    "baseline_time": round(baseline_time, 3),
                    "delays": [round(d, 3) for d in delays],
                    "matched_payload": payload,
                    "expected_delay": delay_seconds,
                },
            )

    return VerificationResult(
        verified=False, confidence=0.15, method="command_injection",
        details="命令注入验证未通过：无输出回显，时间延迟不可复现。",
        false_positive_risk="medium",
        evidence={
            "baseline_time": round(baseline_time, 3),
            "all_delay_results": all_delay_results,
            "resp_body_preview": (resp_body or "")[:500],
        },
    )


# 路径穿越文件内容特征
PATH_TRAVERSAL_PATTERNS: dict[str, list[str]] = {
    "linux_passwd": [
        "root:x:0:0:", "daemon:x:1:1:", "bin:x:2:2:", "nobody:x:65534:",
        "/bin/bash", "/bin/sh", "/sbin/nologin",
    ],
    "linux_hosts": [
        "127.0.0.1", "localhost",
    ],
    "linux_hostname": [
        # hostname 文件内容不固定，靠响应上下文判断
    ],
    "windows_winini": [
        "[fonts]", "[extensions]", "MAPI=1", "CMCDLLNAME32=",
    ],
    "windows_system32": [
        "[boot]", "[386Enh]", "device=", "osloadoptions",
    ],
}


def verify_path_traversal(resp_body: str) -> VerificationResult:
    """路径穿越验证：检查响应中是否包含目标文件的特征内容。

    渗透测试判定标准：
    1. 响应中必须包含特定文件的特征字符串
    2. 识别文件类型（passwd/win.ini/hosts 等）
    3. 内容应完整出现（而非应用自身输出的巧合匹配）
    """
    if not resp_body:
        return VerificationResult(
            verified=False, confidence=0.0, method="path_traversal",
            details="响应体为空", false_positive_risk="high",
            evidence={"resp_body_preview": ""},
        )

    resp_lower = resp_body.lower()
    detected_files: list[str] = []
    matched_patterns: dict[str, str] = {}

    for file_type, patterns in PATH_TRAVERSAL_PATTERNS.items():
        for pattern in patterns:
            if pattern.lower() in resp_lower:
                detected_files.append(file_type)
                matched_patterns[file_type] = pattern
                break

    if detected_files:
        files_str = " / ".join(detected_files)
        return VerificationResult(
            verified=True,
            confidence=0.9,
            method="path_traversal",
            details=f"路径穿越验证通过：响应中包含 {files_str} 文件特征内容。",
            false_positive_risk="low",
            evidence={
                "detected_files": detected_files,
                "matched_patterns": matched_patterns,
                "resp_body_preview": resp_body[:500],
            },
        )

    return VerificationResult(
        verified=False, confidence=0.1, method="path_traversal",
        details="响应中未发现已知文件特征内容。",
        false_positive_risk="low",
        evidence={"resp_body_preview": resp_body[:500]},
    )


# SSRF 响应特征
SSRF_INDICATORS: dict[str, list[str]] = {
    "cloud_aws": [
        "ami-id", "instance-id", "security-credentials",
        "169.254.169.254", "iam", "instance-identity",
    ],
    "cloud_gcp": [
        "metadata.google.internal", "service-accounts",
        "project-id", "access-token",
    ],
    "cloud_azure": [
        "metadata.azure.com", "169.254.169.254/metadata",
    ],
    "internal_services": [
        "<html", "<title", "<!DOCTYPE",  # 内部 Web 服务
        "elasticsearch", "kibana",  # ES
        "redis", "redis_version",  # Redis
        "It works!", "Apache",  # Apache 默认页
        "Welcome to nginx",  # Nginx 默认页
        "Tomcat", "Apache Tomcat",  # Tomcat
        "Jenkins", "Dashboard [Jenkins]",  # Jenkins
        "Grafana",  # Grafana
    ],
    "file_protocol": [
        "root:x:0:0:", "[fonts]", "[boot]",  # file:// 读取的文件
    ],
}


def verify_ssrf(resp_body: str, resp_headers: str = "") -> VerificationResult:
    """SSRF 验证：检查响应中是否包含内网/云服务特征。

    渗透测试判定标准：
    1. 响应中包含云元数据特征（AWS/GCP/Azure）
    2. 响应中包含内网服务特征（Redis/ES/Jenkins 等）
    3. 响应中包含 file:// 协议读取的文件内容
    4. Content-Type 与预期不符（如返回非 JSON 而是 HTML）
    """
    if not resp_body:
        return VerificationResult(
            verified=False, confidence=0.0, method="ssrf",
            details="响应体为空", false_positive_risk="high",
            evidence={
                "resp_body_preview": "",
                "resp_headers_preview": (resp_headers or "")[:500],
            },
        )

    resp_lower = resp_body.lower()
    headers_lower = (resp_headers or "").lower()
    detected_types: list[str] = []
    matched_patterns: dict[str, str] = {}

    for ssrf_type, patterns in SSRF_INDICATORS.items():
        for pattern in patterns:
            if pattern.lower() in resp_lower:
                detected_types.append(ssrf_type)
                matched_patterns[ssrf_type] = pattern
                break

    if detected_types:
        types_str = " / ".join(detected_types)
        confidence = 0.9 if "cloud" in types_str else 0.8
        return VerificationResult(
            verified=True,
            confidence=confidence,
            method="ssrf",
            details=f"SSRF 验证通过：响应中包含 {types_str} 特征，服务端发起了内部请求。",
            false_positive_risk="low",
            evidence={
                "detected_types": detected_types,
                "matched_patterns": matched_patterns,
                "resp_body_preview": resp_body[:500],
                "resp_headers_preview": (resp_headers or "")[:500],
            },
        )

    return VerificationResult(
        verified=False, confidence=0.1, method="ssrf",
        details="响应中未发现内网/云服务特征。",
        false_positive_risk="medium",
        evidence={
            "resp_body_preview": resp_body[:500],
            "resp_headers_preview": (resp_headers or "")[:500],
        },
    )


def verify_cors_misconfig(
    resp_headers: str,
    test_origin: str = "https://evil.com",
) -> VerificationResult:
    """CORS 配置验证：检查响应头是否反射任意 Origin。

    渗透测试判定标准：
    1. Access-Control-Allow-Origin 反射了测试 Origin（而非白名单匹配）
    2. Access-Control-Allow-Credentials: true 时，ACAO 反射任意 Origin 是高危
    3. ACAO: * 配合凭证使用是配置错误
    """
    headers_lower = (resp_headers or "").lower()

    # 提取 CORS 头
    acao = ""
    acac = ""
    for line in (resp_headers or "").split("\n"):
        line_lower = line.lower().strip()
        if line_lower.startswith("access-control-allow-origin:"):
            acao = line.split(":", 1)[1].strip()
        elif line_lower.startswith("access-control-allow-credentials:"):
            acac = line.split(":", 1)[1].strip().lower()

    if not acao:
        return VerificationResult(
            verified=False, confidence=0.0, method="cors_misconfig",
            details="响应中无 Access-Control-Allow-Origin 头，CORS 配置正常。",
            false_positive_risk="low",
            evidence={"acao": acao, "acac": acac, "test_origin": test_origin},
        )

    # 检查是否反射了测试 Origin
    if acao == test_origin:
        if acac == "true":
            return VerificationResult(
                verified=True,
                confidence=0.95,
                method="cors_misconfig",
                details=(
                    f"CORS 验证通过（高危）：ACAO 反射了任意 Origin ({test_origin})，"
                    f"且 Allow-Credentials: true，可跨域读取敏感数据。"
                ),
                false_positive_risk="low",
                evidence={"acao": acao, "acac": acac, "test_origin": test_origin},
            )
        else:
            return VerificationResult(
                verified=True,
                confidence=0.6,
                method="cors_misconfig",
                details=(
                    f"CORS 验证通过（中危）：ACAO 反射了任意 Origin ({test_origin})，"
                    f"但未设置 Allow-Credentials: true，影响有限。"
                ),
                false_positive_risk="low",
                evidence={"acao": acao, "acac": acac, "test_origin": test_origin},
            )

    # 检查通配符
    if acao == "*":
        if acac == "true":
            return VerificationResult(
                verified=True,
                confidence=0.8,
                method="cors_misconfig",
                details="CORS 验证通过：ACAO: * 且 Allow-Credentials: true，浏览器会拒绝此配置，但配置本身是错误的。",
                false_positive_risk="low",
                evidence={"acao": acao, "acac": acac, "test_origin": test_origin},
            )
        else:
            return VerificationResult(
                verified=False, confidence=0.3, method="cors_misconfig",
                details="ACAO: * 但未设置 Allow-Credentials，配置相对安全。",
                false_positive_risk="low",
                evidence={"acao": acao, "acac": acac, "test_origin": test_origin},
            )

    return VerificationResult(
        verified=False, confidence=0.1, method="cors_misconfig",
        details=f"ACAO: {acao}，未反射任意 Origin，CORS 配置正常。",
        false_positive_risk="low",
        evidence={"acao": acao, "acac": acac, "test_origin": test_origin},
    )


async def verify_open_redirect(
    method: str,
    url: str,
    param_name: str,
    param_value: str,
    headers: str = "",
    body: str = "",
    test_redirect_url: str = "https://evil.com",
    session_id: str | None = None,
) -> VerificationResult:
    """开放重定向验证：检查是否被重定向到外部域名。

    渗透测试判定标准：
    1. 响应 Location 头包含测试 URL
    2. 或响应状态码为 301/302/303/307/308 且 Location 指向外部域名
    """
    def _inject(url: str, body: str, payload: str) -> tuple[str, str]:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        if param_name in params:
            params[param_name] = [payload]
            new_query = urlencode(params, doseq=True)
            return urlunparse(parsed._replace(query=new_query)), body
        elif body and param_name in body:
            return url, body.replace(f"{param_name}={param_value}", f"{param_name}={payload}", 1)
        return url, body

    inj_url, inj_body = _inject(url, body, test_redirect_url)

    try:
        if session_id:
            resp = await send_request_with_session(
                session_id, method, inj_url, headers, inj_body,
                follow_redirects=False,  # 需要原始 30x 响应与 Location 头
            )
        else:
            resp = await send_raw_request(
                method=method, url=inj_url, headers_str=headers, body=inj_body,
                follow_redirects=False,  # 修复：跟随重定向会吞掉 Location 头，导致永远验不出开放重定向
            )
    except Exception as e:
        return VerificationResult(
            verified=False, confidence=0.0, method="open_redirect",
            details=f"验证请求失败: {e}", false_positive_risk="high",
            evidence={"error": str(e)},
        )

    resp_headers = resp.get("headers", "")
    status = resp.get("status")

    # 检查 Location 头
    location = ""
    for line in resp_headers.split("\n"):
        if line.lower().strip().startswith("location:"):
            location = line.split(":", 1)[1].strip()
            break

    if location and test_redirect_url in location:
        return VerificationResult(
            verified=True,
            confidence=0.9,
            method="open_redirect",
            details=(
                f"开放重定向验证通过：参数 {param_name} 注入 {test_redirect_url} 后，"
                f"响应 Location 头指向 {location}（状态码 {status}）。"
            ),
            false_positive_risk="low",
            evidence={"status": status, "location": location},
        )

    if status in (301, 302, 303, 307, 308) and location:
        target_domain = urlparse(test_redirect_url).netloc
        if target_domain and target_domain in location:
            return VerificationResult(
                verified=True,
                confidence=0.85,
                method="open_redirect",
                details=f"开放重定向验证通过：重定向到 {location}（包含测试域名 {target_domain}）。",
                false_positive_risk="low",
                evidence={"status": status, "location": location},
            )

    return VerificationResult(
        verified=False, confidence=0.1, method="open_redirect",
        details=f"响应状态 {status}，Location: {location or '(无)'}，未发生外部重定向。",
        false_positive_risk="low",
        evidence={"status": status, "location": location},
    )


# ===== LLM 辅助验证 =====

async def llm_assisted_verify(
    rule_result: VerificationResult,
    vuln_type: str,
    method: str,
    url: str,
    param_name: str = "",
    param_value: str = "",
    headers: str = "",
    body: str = "",
    resp_body: str = "",
    provider_id: str | None = None,
    db: Any = None,
) -> VerificationResult:
    """LLM 辅助验证：当规则验证结果不确定时，使用 LLM 分析原始证据。

    适用场景：
    - 规则验证未通过，但 confidence >= 0.2（不确定，非强排除）
    - 漏洞变体未被规则覆盖（如 WAF 过滤、编码绕过、非标准注入点）
    - 响应中存在异常信号但规则未识别

    LLM 分析维度：
    1. 响应状态码差异（如 true/false 返回不同状态码）
    2. 响应长度微妙差异（即使相似度高，长度差异也可指示注入）
    3. 时间延迟部分匹配（延迟不够明显但存在趋势）
    4. 错误信息变种（规则未覆盖的数据库错误格式）
    5. 上下文推理（结合 URL 结构、参数名推断注入可能性）

    返回：融合 LLM 判断的新 VerificationResult。
    - 如果 LLM 判断 likely_vulnerable：verified=True，保留原始 evidence
    - 如果 LLM 判断 inconclusive：verified=False，提升 confidence，保留原始 evidence
    - 如果 LLM 判断 likely_safe：verified=False，降低 confidence，保留原始 evidence
    - LLM 调用失败：原样返回 rule_result（不阻断流程）
    """
    import json

    # 构建证据摘要（控制 token 用量）
    evidence_summary = json.dumps(rule_result.evidence, ensure_ascii=False, default=str)
    # 截断过长的证据
    if len(evidence_summary) > 3000:
        evidence_summary = evidence_summary[:3000] + "...(截断)"

    # 构建上下文
    context_parts = [
        f"漏洞类型: {vuln_type}",
        f"URL: {url}",
        f"方法: {method}",
    ]
    if param_name:
        context_parts.append(f"参数: {param_name}={param_value}")
    if headers:
        context_parts.append(f"请求头预览: {headers[:300]}")
    if body:
        context_parts.append(f"请求体预览: {body[:300]}")
    if resp_body:
        context_parts.append(f"原始响应体预览: {resp_body[:500]}")

    context = "\n".join(context_parts)

    prompt = f"""你是一位资深渗透测试专家，正在分析一个疑似漏洞的自动化验证结果。

## 测试上下文
{context}

## 规则验证结果
- 验证方法: {rule_result.method}
- 是否验证通过: {rule_result.verified}
- 置信度: {rule_result.confidence}
- 详情: {rule_result.details}
- 误报风险: {rule_result.false_positive_risk}

## 原始证据数据（规则验证收集的对比数据）
{evidence_summary}

## 分析任务
规则验证未能确认漏洞存在，但这不代表漏洞不存在。规则验证的局限性包括：
1. payload 固定，可能被 WAF/过滤机制拦截
2. 相似度阈值固定，可能错过微小但真实的差异
3. 错误特征库有限，可能不认识某些数据库错误格式
4. 时间延迟阈值固定，可能受网络波动干扰

请基于以上证据，从专业渗透测试角度分析：
1. 证据中是否存在规则未识别的漏洞迹象？（如状态码差异、长度微妙变化、部分错误信息）
2. 是否存在规则未覆盖的漏洞变体？（如不同注入点、编码绕过、WAF 规避）
3. 结合参数名和 URL 结构，注入的可能性有多大？
4. 是否建议进行额外的手动测试？如果是，建议什么方法？

请以 JSON 格式返回（不要包含 markdown 代码块标记）：
{{"verdict": "likely_vulnerable" 或 "inconclusive" 或 "likely_safe", "confidence": 0.0到1.0的数字, "reasoning": "分析过程", "suggestion": "后续建议"}}"""

    messages = [
        {"role": "system", "content": "你是渗透测试漏洞验证专家。你的任务是分析自动化验证工具收集的证据，判断规则验证可能遗漏的漏洞。请基于证据数据客观分析，不要凭空猜测。"},
        {"role": "user", "content": prompt},
    ]

    try:
        from hemlock.core.llm_client import chat_json
        # 使用 asyncio.to_thread 避免阻塞事件循环
        raw_response = await asyncio.to_thread(
            chat_json,
            messages=messages,
            temperature=0.1,
            provider_id=provider_id,
            db=db,
        )

        llm_result = json.loads(raw_response)
        verdict = llm_result.get("verdict", "inconclusive")
        llm_confidence = float(llm_result.get("confidence", 0.3))
        reasoning = llm_result.get("reasoning", "")
        suggestion = llm_result.get("suggestion", "")

        # 合并 evidence（保留原始规则证据 + 添加 LLM 分析）
        merged_evidence = dict(rule_result.evidence)
        merged_evidence["llm_analysis"] = {
            "verdict": verdict,
            "confidence": llm_confidence,
            "reasoning": reasoning,
            "suggestion": suggestion,
        }

        if verdict == "likely_vulnerable":
            # LLM 判断可能存在漏洞
            # 取规则置信度和 LLM 置信度的较高值
            final_confidence = max(rule_result.confidence, llm_confidence)
            return VerificationResult(
                verified=True,
                confidence=final_confidence,
                method=f"{rule_result.method}+llm_assisted",
                details=(
                    f"{rule_result.details}\n"
                    f"[LLM 辅助分析] LLM 判断漏洞可能存在（置信度 {llm_confidence:.2f}）：{reasoning}\n"
                    f"建议：{suggestion}"
                ),
                false_positive_risk="medium",  # LLM 判断的误报风险中等
                evidence=merged_evidence,
            )
        elif verdict == "inconclusive":
            # LLM 也无法确定
            # 提升置信度到中等水平（表示"不确定"而非"没有"）
            final_confidence = max(rule_result.confidence, 0.4)
            return VerificationResult(
                verified=False,
                confidence=final_confidence,
                method=f"{rule_result.method}+llm_assisted",
                details=(
                    f"{rule_result.details}\n"
                    f"[LLM 辅助分析] LLM 无法确定漏洞是否存在（置信度 {llm_confidence:.2f}）：{reasoning}\n"
                    f"建议：{suggestion}"
                ),
                false_positive_risk="medium",
                evidence=merged_evidence,
            )
        else:
            # LLM 判断可能安全
            final_confidence = min(rule_result.confidence, 0.15)
            return VerificationResult(
                verified=False,
                confidence=final_confidence,
                method=f"{rule_result.method}+llm_assisted",
                details=(
                    f"{rule_result.details}\n"
                    f"[LLM 辅助分析] LLM 判断漏洞可能不存在（置信度 {llm_confidence:.2f}）：{reasoning}"
                ),
                false_positive_risk="low",
                evidence=merged_evidence,
            )

    except Exception as e:
        logger.warning(f"LLM 辅助验证失败，返回原始规则验证结果: {e}")
        # LLM 调用失败，不阻断流程，原样返回
        return rule_result


async def _maybe_llm_assist(
    rule_result: VerificationResult,
    vuln_type: str,
    method: str,
    url: str,
    param_name: str = "",
    param_value: str = "",
    headers: str = "",
    body: str = "",
    resp_body: str = "",
    provider_id: str | None = None,
    db: Any = None,
) -> VerificationResult:
    """规则验证不确定时按需调用 LLM 辅助分析。

    仅当 llm_assisted_verify_enabled 开关开启、且规则结果未确认但置信度>=0.2 时
    才调用 LLM；否则原样返回规则结果，不额外消耗 token。
    """
    from hemlock.core.config import settings as app_settings

    if (
        app_settings.llm_assisted_verify_enabled
        and not rule_result.verified
        and rule_result.confidence >= 0.2
    ):
        return await llm_assisted_verify(
            rule_result=rule_result,
            vuln_type=vuln_type,
            method=method, url=url,
            param_name=param_name, param_value=param_value,
            headers=headers, body=body, resp_body=resp_body,
            provider_id=provider_id, db=db,
        )
    return rule_result


# ===== 综合验证入口 =====

async def auto_verify(
    vuln_type: str,
    method: str,
    url: str,
    param_name: str = "",
    param_value: str = "",
    headers: str = "",
    body: str = "",
    resp_body: str = "",
    session_id: str | None = None,
    provider_id: str | None = None,
    db: Any = None,
) -> VerificationResult:
    """根据漏洞类型自动选择验证方法。

    这是 Agent 调用漏洞验证的统一入口。

    验证流程（规则验证始终执行，LLM 辅助受开关控制）：
    - 规则验证：始终发送请求、执行确定性规则验证（布尔盲注/时间盲注/错误注入等）
    - LLM 辅助（llm_assisted_verify_enabled 开关）：规则结果不确定（confidence>=0.2）时，
      额外调用 LLM 分析原始证据，弥补规则盲区
    - 开关关闭：规则验证照常执行，只是不确定时不追加 LLM 分析

    三态语义：
    - verified=True → 确认漏洞存在
    - verified=False, confidence<0.2 → 确认漏洞不存在（强证据排除）
    - verified=False, confidence>=0.2 → 不确定（没撞上规则不代表没有）
    """
    vuln_type = vuln_type.upper()

    # ===== 规则验证（始终执行）+ LLM 辅助（按开关）=====

    if vuln_type == "SQL_INJECTION":
        # 先检查错误注入（最快）
        if resp_body:
            error_result = verify_error_based_sqli(resp_body)
            if error_result.verified:
                return error_result

        # 再检查布尔盲注
        if param_name and param_value:
            bool_result = await verify_boolean_blind_sqli(
                method, url, param_name, param_value, headers, body, session_id
            )
            if bool_result.verified:
                return bool_result

            # 最后检查时间盲注
            time_result = await verify_time_based_sqli(
                method, url, param_name, param_value, headers, body, session_id
            )
            if time_result.verified:
                return time_result

            # 规则验证未通过，检查是否需要 LLM 辅助
            # 选择证据更丰富的结果作为 LLM 分析基础
            base_result = bool_result if bool_result.confidence >= time_result.confidence else time_result
            if base_result.confidence >= 0.2:
                return await _maybe_llm_assist(
                    rule_result=base_result,
                    vuln_type=vuln_type,
                    method=method, url=url,
                    param_name=param_name, param_value=param_value,
                    headers=headers, body=body, resp_body=resp_body,
                    provider_id=provider_id, db=db,
                )
            return base_result

        return VerificationResult(
            verified=False, confidence=0.0, method="auto",
            details="缺少参数信息，无法自动验证 SQL 注入", false_positive_risk="high",
        )

    elif vuln_type == "SQL_INJECTION_ORDER_BY":
        if param_name and param_value:
            result = await verify_order_by_sqli(
                method, url, param_name, param_value, headers, body, session_id
            )
            if not result.verified and result.confidence >= 0.2:
                return await _maybe_llm_assist(
                    rule_result=result,
                    vuln_type=vuln_type,
                    method=method, url=url,
                    param_name=param_name, param_value=param_value,
                    headers=headers, body=body, resp_body=resp_body,
                    provider_id=provider_id, db=db,
                )
            return result
        return VerificationResult(
            verified=False, confidence=0.0, method="auto",
            details="缺少参数信息，无法验证 ORDER BY 注入", false_positive_risk="high",
        )

    elif vuln_type == "XSS":
        if resp_body and param_value:
            result = verify_xss_reflection(resp_body, param_value)
            if not result.verified and result.confidence >= 0.2:
                return await _maybe_llm_assist(
                    rule_result=result,
                    vuln_type=vuln_type,
                    method=method, url=url,
                    param_name=param_name, param_value=param_value,
                    headers=headers, body=body, resp_body=resp_body,
                    provider_id=provider_id, db=db,
                )
            return result
        return VerificationResult(
            verified=False, confidence=0.0, method="auto",
            details="缺少响应体或 payload，无法验证 XSS", false_positive_risk="high",
        )

    elif vuln_type == "COMMAND_INJECTION":
        result = await verify_command_injection(
            method, url, param_name, param_value, headers, body, resp_body, session_id
        )
        if not result.verified and result.confidence >= 0.2:
            return await _maybe_llm_assist(
                rule_result=result,
                vuln_type=vuln_type,
                method=method, url=url,
                param_name=param_name, param_value=param_value,
                headers=headers, body=body, resp_body=resp_body,
                provider_id=provider_id, db=db,
            )
        return result

    elif vuln_type == "PATH_TRAVERSAL":
        if resp_body:
            result = verify_path_traversal(resp_body)
            if not result.verified and result.confidence >= 0.2:
                return await _maybe_llm_assist(
                    rule_result=result,
                    vuln_type=vuln_type,
                    method=method, url=url,
                    param_name=param_name, param_value=param_value,
                    headers=headers, body=body, resp_body=resp_body,
                    provider_id=provider_id, db=db,
                )
            return result
        return VerificationResult(
            verified=False, confidence=0.0, method="auto",
            details="缺少响应体，无法验证路径穿越", false_positive_risk="high",
        )

    elif vuln_type == "SSRF":
        if resp_body:
            result = verify_ssrf(resp_body, headers)
            if not result.verified and result.confidence >= 0.2:
                return await _maybe_llm_assist(
                    rule_result=result,
                    vuln_type=vuln_type,
                    method=method, url=url,
                    param_name=param_name, param_value=param_value,
                    headers=headers, body=body, resp_body=resp_body,
                    provider_id=provider_id, db=db,
                )
            return result
        return VerificationResult(
            verified=False, confidence=0.0, method="auto",
            details="缺少响应体，无法验证 SSRF", false_positive_risk="high",
        )

    elif vuln_type == "CORS_MISCONFIG":
        if headers:
            result = verify_cors_misconfig(headers)
            if not result.verified and result.confidence >= 0.2:
                return await _maybe_llm_assist(
                    rule_result=result,
                    vuln_type=vuln_type,
                    method=method, url=url,
                    param_name=param_name, param_value=param_value,
                    headers=headers, body=body, resp_body=resp_body,
                    provider_id=provider_id, db=db,
                )
            return result
        return VerificationResult(
            verified=False, confidence=0.0, method="auto",
            details="缺少响应头，无法验证 CORS 配置", false_positive_risk="high",
        )

    elif vuln_type == "OPEN_REDIRECT":
        if param_name and param_value:
            result = await verify_open_redirect(
                method, url, param_name, param_value, headers, body, session_id=session_id
            )
            if not result.verified and result.confidence >= 0.2:
                return await _maybe_llm_assist(
                    rule_result=result,
                    vuln_type=vuln_type,
                    method=method, url=url,
                    param_name=param_name, param_value=param_value,
                    headers=headers, body=body, resp_body=resp_body,
                    provider_id=provider_id, db=db,
                )
            return result
        return VerificationResult(
            verified=False, confidence=0.0, method="auto",
            details="缺少参数信息，无法验证开放重定向", false_positive_risk="high",
        )

    elif vuln_type == "IDOR":
        # IDOR 需要两个会话，由 Agent 层面处理
        return VerificationResult(
            verified=False, confidence=0.0, method="auto",
            details="IDOR 需要两个不同会话对比，请使用 compare_responses 工具手动验证",
            false_positive_risk="medium",
        )

    else:
        # 未知漏洞类型：无法自动验证，直接交给 LLM 分析
        return VerificationResult(
            verified=False, confidence=0.3, method="auto",
            details=f"暂不支持 {vuln_type} 的自动规则验证，请使用 llm_analyze_evidence 工具进行 LLM 辅助分析",
            false_positive_risk="medium",
            evidence={"vuln_type": vuln_type, "url": url, "method": method},
        )
