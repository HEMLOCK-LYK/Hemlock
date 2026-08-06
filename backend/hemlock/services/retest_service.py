"""漏洞复测服务：重放证据链请求，AI 验证漏洞是否已修复。

工作流：
1. 从 Evidence 中提取原始请求
2. 重新发送该请求
3. 对比原始响应和新响应
4. AI 分析漏洞是否仍然存在
5. 更新漏洞状态
"""

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from hemlock.core.llm_client import chat_json
from hemlock.core.config import settings
from hemlock.models.finding import Finding, FINDING_STATUS_CONFIRMED, FINDING_STATUS_CLOSED
from hemlock.models.evidence import Evidence
from hemlock.models.flow import Flow
from hemlock.services.request_service import send_raw_request
from hemlock.services.event_bus import event_bus
from hemlock.services.flowtag_engine import enrich_flow_fields


def _llm_available(db: Session) -> bool:
    """检查是否配置了 LLM：.env 或数据库 Provider（UI 配置）。"""
    if settings.llm_api_key:
        return True
    try:
        from hemlock.models.llm_provider import LLMProvider
        return db.query(LLMProvider).first() is not None
    except Exception:
        return False


def _parse_evidence_request(request_text: str) -> dict[str, Any]:
    """从证据请求文本中解析出 method, url, headers, body。

    证据格式：
    GET /api/users HTTP/1.1
    Host: example.com
    Content-Type: application/json

    {"key": "value"}
    """
    lines = request_text.strip().splitlines()
    if not lines:
        return {}

    # 解析请求行
    request_line = lines[0].strip()
    parts = request_line.split()
    if len(parts) < 2:
        return {}

    method = parts[0]
    path = parts[1]

    # 解析 headers
    headers = []
    body_start = 0
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "":
            body_start = i + 1
            break
        headers.append(line)

    # 提取 Host 头构建完整 URL
    host = ""
    for h in headers:
        if h.lower().startswith("host:"):
            host = h.split(":", 1)[1].strip()
            break

    # 构建完整 URL
    if path.startswith("http://") or path.startswith("https://"):
        url = path
    elif host:
        url = f"http://{host}{path}"
    else:
        url = path

    # 解析 body
    body = "\n".join(lines[body_start:]) if body_start < len(lines) else ""

    return {
        "method": method,
        "url": url,
        "headers": "\n".join(headers),
        "body": body,
    }


async def retest_finding(db: Session, finding_id: str) -> dict[str, Any]:
    """复测漏洞：重放证据请求，验证漏洞是否已修复。

    Returns:
        {
            "finding_id": str,
            "retest_status": "still_vulnerable" | "fixed" | "uncertain" | "error",
            "original_status": int,
            "new_status": int,
            "similarity": float,
            "analysis": str,
            "new_flow_id": str,
        }
    """
    finding = db.query(Finding).filter(Finding.id == finding_id).first()
    if not finding:
        return {"error": "finding not found"}

    if not finding.evidence_id:
        return {"error": "finding has no evidence"}

    evidence = db.query(Evidence).filter(Evidence.id == finding.evidence_id).first()
    if not evidence:
        return {"error": "evidence not found"}

    if not evidence.replayable:
        return {"error": "evidence is not replayable"}

    # 解析原始请求
    req_data = _parse_evidence_request(evidence.request)
    if not req_data or not req_data.get("url"):
        return {"error": "failed to parse original request from evidence"}

    # 重新发送请求
    try:
        new_resp = await send_raw_request(
            method=req_data["method"],
            url=req_data["url"],
            headers_str=req_data.get("headers", ""),
            body=req_data.get("body", ""),
        )
    except Exception as e:
        return {"error": f"failed to resend request: {e}"}

    # 存储新流量
    resp_headers = new_resp.get("headers", "")
    resp_body = new_resp.get("body", "")
    actual_req_headers = new_resp.get("req_headers", req_data.get("headers", ""))
    meta = enrich_flow_fields(
        url=req_data["url"],
        req_headers=actual_req_headers,
        req_body=req_data.get("body", ""),
        resp_headers=resp_headers,
        resp_body=resp_body,
        resp_status=new_resp.get("status"),
    )
    new_flow = Flow(
        project_id=finding.project_id,
        method=req_data["method"],
        url=req_data["url"],
        req_headers=actual_req_headers,
        req_body=req_data.get("body", ""),
        resp_status=new_resp.get("status"),
        resp_headers=resp_headers,
        resp_body=resp_body,
        source="retest",
        duration_ms=new_resp.get("duration_ms"),
        **meta,
    )
    db.add(new_flow)
    db.commit()
    db.refresh(new_flow)

    # 解析原始响应
    original_resp = evidence.response or ""
    original_status = _extract_status_from_response(original_resp)
    original_body = _extract_body_from_response(original_resp)

    new_status = new_resp.get("status")
    new_body = new_resp.get("body", "")

    # 计算相似度
    similarity = _calculate_similarity(original_body, new_body)

    # AI 分析漏洞是否仍然存在
    analysis = await _ai_analyze_retest(
        db=db,
        finding=finding,
        evidence=evidence,
        original_status=original_status,
        original_body=original_body,
        new_status=new_status,
        new_body=new_body,
        similarity=similarity,
    )

    retest_status = analysis.get("retest_status", "uncertain")

    # 自动更新漏洞状态
    if retest_status == "fixed":
        finding.status = FINDING_STATUS_CLOSED
        finding.updated_at = datetime.now(timezone.utc)
        db.commit()
    elif retest_status == "still_vulnerable":
        finding.status = FINDING_STATUS_CONFIRMED
        finding.updated_at = datetime.now(timezone.utc)
        db.commit()

    # 发布事件
    event_bus.publish("finding", {
        "event": "retest_complete",
        "finding_id": finding.id,
        "retest_status": retest_status,
        "new_flow_id": new_flow.id,
    })

    return {
        "finding_id": finding.id,
        "retest_status": retest_status,
        "original_status": original_status,
        "new_status": new_status,
        "similarity": round(similarity, 1),
        "analysis": analysis.get("analysis", ""),
        "new_flow_id": new_flow.id,
    }


def _extract_status_from_response(response_text: str) -> int | None:
    """从证据响应文本中提取状态码。"""
    match = re.match(r"HTTP/\d\.?\d?\s+(\d+)", response_text.strip())
    if match:
        return int(match.group(1))
    return None


def _extract_body_from_response(response_text: str) -> str:
    """从证据响应文本中提取响应体。"""
    parts = response_text.split("\n\n", 1)
    if len(parts) > 1:
        return parts[1]
    return response_text


def _calculate_similarity(s1: str, s2: str) -> float:
    """计算两个字符串的相似度百分比。"""
    if not s1 and not s2:
        return 100.0
    if not s1 or not s2:
        return 0.0

    if len(s1) > 5000 or len(s2) > 5000:
        s1 = s1[:5000]
        s2 = s2[:5000]

    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 100.0

    min_len = min(len(s1), len(s2))
    same = sum(1 for i in range(min_len) if s1[i] == s2[i])
    return (same / max_len) * 100


async def _ai_analyze_retest(
    db: Session,
    finding: Finding,
    evidence: Evidence,
    original_status: int | None,
    original_body: str,
    new_status: int | None,
    new_body: str,
    similarity: float,
) -> dict:
    """使用 AI 分析漏洞是否仍然存在。"""
    # 修复逻辑 bug：只查 settings.llm_api_key 会漏掉 UI 配置的数据库 Provider，
    # 导致复测的 AI 分析永远走不到。改为同时检查 settings 与数据库 Provider。
    if not _llm_available(db):
        # 无 LLM 时使用基于规则的分析
        return _rule_based_retest(
            finding, original_status, new_status, original_body, new_body, similarity
        )

    prompt = f"""你是 Web 安全测试专家。请分析以下漏洞复测结果，判断漏洞是否已修复。

## 漏洞信息
- 类型: {finding.vuln_type}
- 标题: {finding.title}
- 详情: {finding.detail}
- 原始判定指标: {evidence.indicator}

## 原始响应
状态码: {original_status}
响应体（前 1000 字符）:
{original_body[:1000]}

## 复测响应
状态码: {new_status}
响应体（前 1000 字符）:
{new_body[:1000]}

## 对比数据
响应相似度: {similarity:.1f}%

请分析：
1. 原始漏洞的判定指标在复测响应中是否仍然存在？
2. 状态码和响应内容是否有显著变化？
3. 漏洞是否已修复？

以 JSON 返回：
{{
    "retest_status": "still_vulnerable" | "fixed" | "uncertain",
    "analysis": "详细分析说明",
    "confidence": "high" | "medium" | "low"
}}"""

    try:
        result = chat_json(
            [
                {"role": "system", "content": "你是专业的 Web 安全测试专家，擅长分析漏洞复测结果。"},
                {"role": "user", "content": prompt},
            ],
            db=db,  # 使用数据库 Provider（UI 配置）而非仅 settings
        )
        return json.loads(result)
    except (json.JSONDecodeError, Exception):
        return _rule_based_retest(
            finding, original_status, new_status, original_body, new_body, similarity
        )


def _rule_based_retest(
    finding: Finding,
    original_status: int | None,
    new_status: int | None,
    original_body: str,
    new_body: str,
    similarity: float,
) -> dict:
    """基于规则的复测分析（无 LLM 时使用）。"""
    analysis_parts = []

    # 状态码变化
    if original_status != new_status:
        analysis_parts.append(
            f"状态码从 {original_status} 变为 {new_status}，表明服务端行为已改变。"
        )

    # 响应相似度
    if similarity < 50:
        analysis_parts.append(
            f"响应内容相似度仅 {similarity:.1f}%，响应内容发生显著变化。"
        )
    elif similarity < 80:
        analysis_parts.append(
            f"响应内容相似度 {similarity:.1f}%，存在一定变化。"
        )
    else:
        analysis_parts.append(
            f"响应内容相似度 {similarity:.1f}%，响应内容基本一致。"
        )

    # 判断
    if original_status != new_status and similarity < 50:
        retest_status = "fixed"
        analysis_parts.append("综合判断：漏洞很可能已修复（状态码和响应均发生显著变化）。")
    elif similarity > 90 and original_status == new_status:
        retest_status = "still_vulnerable"
        analysis_parts.append("综合判断：漏洞很可能仍然存在（响应几乎未变化）。")
    else:
        retest_status = "uncertain"
        analysis_parts.append("综合判断：无法确定漏洞是否已修复，建议人工验证。")

    return {
        "retest_status": retest_status,
        "analysis": " ".join(analysis_parts),
        "confidence": "medium",
    }
