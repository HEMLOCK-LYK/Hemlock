"""流量服务：查询、过滤、重放。"""

import asyncio
from sqlalchemy.orm import Session
from sqlalchemy import desc

from hemlock.models.flow import Flow
from hemlock.services.request_service import send_raw_request
from hemlock.services.flowtag_engine import enrich_flow_fields


def list_flows(
    db: Session,
    page: int = 1,
    page_size: int = 50,
    method: str | None = None,
    url_contains: str | None = None,
    source: str | None = None,
    status: int | None = None,
    project_id: str | None = None,
    hide_noise: bool = False,
    host: str | None = None,
    tags_only: bool = False,
    search: str | None = None,
) -> tuple[list[Flow], int]:
    """分页查询流量列表，返回 (items, total)。

    hide_noise=True 时过滤掉 is_noise=True 的流量。
    始终排除 source="intruder" 的流量（爆破器数据有独立视图查看）。
    当 source 参数显式指定为 "intruder" 时才包含（用于杂包管理等特殊场景）。
    host 模糊匹配 host 字段。
    tags_only=True 时仅返回 tags 非空的流量。
    search 参数会在所有文本字段中模糊搜索（url, method, host, path,
    req_headers, req_body, resp_headers, resp_body, title, tags, mime_type）。
    """
    from sqlalchemy import or_

    q = db.query(Flow)
    if method:
        q = q.filter(Flow.method == method.upper())
    if url_contains:
        q = q.filter(Flow.url.contains(url_contains))
    if source:
        q = q.filter(Flow.source == source)
    else:
        # 默认排除 intruder 源（爆破器数据包不显示在流量列表中）
        q = q.filter(Flow.source != "intruder")
    if status:
        q = q.filter(Flow.resp_status == status)
    if project_id:
        q = q.filter(Flow.project_id == project_id)
    if hide_noise:
        q = q.filter(Flow.is_noise == False)  # noqa: E712
    if host:
        q = q.filter(Flow.host.contains(host))
    if tags_only:
        q = q.filter(Flow.tags != None, Flow.tags != "")  # noqa: E711
    if search:
        # 全字段搜索：在所有文本字段中模糊匹配关键字
        keyword = f"%{search}%"
        q = q.filter(
            or_(
                Flow.url.contains(search),
                Flow.method.contains(search),
                Flow.host.contains(search),
                Flow.path.contains(search),
                Flow.req_headers.contains(search),
                Flow.req_body.contains(search),
                Flow.resp_headers.contains(search),
                Flow.resp_body.contains(search),
                Flow.title.contains(search),
                Flow.tags.contains(search),
                Flow.mime_type.contains(search),
                Flow.noise_reason.contains(search),
            )
        )

    total = q.count()
    items = (
        q.order_by(desc(Flow.timestamp))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def count_noise(db: Session, project_id: str) -> int:
    """统计项目中杂包数量。"""
    return (
        db.query(Flow)
        .filter(Flow.project_id == project_id)
        .filter(Flow.is_noise == True)  # noqa: E712
        .count()
    )


def get_noise_summary(db: Session, project_id: str) -> list[dict]:
    """获取杂包摘要：按 noise_reason 分组统计。"""
    from sqlalchemy import func
    rows = (
        db.query(Flow.noise_reason, func.count(Flow.id))
        .filter(Flow.project_id == project_id)
        .filter(Flow.is_noise == True)  # noqa: E712
        .group_by(Flow.noise_reason)
        .all()
    )
    return [{"reason": r[0] or "未知", "count": r[1]} for r in rows]


def get_noise_flows(db: Session, project_id: str, page: int = 1, page_size: int = 50) -> tuple[list[Flow], int]:
    """获取杂包列表（分页），返回 (items, total)。"""
    q = (
        db.query(Flow)
        .filter(Flow.project_id == project_id)
        .filter(Flow.is_noise == True)  # noqa: E712
        .order_by(desc(Flow.timestamp))
    )
    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    return items, total


def clear_flows(db: Session, project_id: str) -> int:
    """清空指定项目的所有流量（保留 intruder 源数据）。

    删除 source != 'intruder' 的全部流量，避免误删爆破器数据。
    返回删除的记录数。
    """
    deleted = (
        db.query(Flow)
        .filter(Flow.project_id == project_id)
        .filter(Flow.source != "intruder")
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


def mark_flow_noise(db: Session, flow_id: str, is_noise: bool, reason: str = "") -> Flow | None:
    """手动标记/取消标记流量为杂包。"""
    flow = db.query(Flow).filter(Flow.id == flow_id).first()
    if not flow:
        return None
    flow.is_noise = is_noise
    flow.noise_reason = reason if is_noise else ""
    db.commit()
    db.refresh(flow)
    return flow


def get_flow(db: Session, flow_id: str) -> Flow | None:
    return db.query(Flow).filter(Flow.id == flow_id).first()


def count_flows_after(db: Session, project_id: str, timestamp) -> int:
    """统计项目中时间戳晚于指定时间的流量数量（用于计算流量编号）。"""
    from datetime import datetime
    if timestamp is None:
        return 0
    # 确保 timestamp 带时区
    if isinstance(timestamp, datetime) and timestamp.tzinfo is None:
        from datetime import timezone
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return (
        db.query(Flow)
        .filter(Flow.project_id == project_id)
        .filter(Flow.source != "intruder")
        .filter(Flow.timestamp > timestamp)
        .count()
    )


async def replay_flow(db: Session, flow_id: str) -> Flow | None:
    """重放指定流量：用原始请求重新发送，新响应存为新 flow。"""
    original = get_flow(db, flow_id)
    if not original:
        return None

    resp = await send_raw_request(
        method=original.method,
        url=original.url,
        headers_str=original.req_headers,
        body=original.req_body,
    )

    resp_headers = resp.get("headers", "")
    resp_body = resp.get("body", "")
    req_headers = resp.get("req_headers", original.req_headers)

    meta = enrich_flow_fields(
        url=original.url,
        req_headers=req_headers,
        req_body=original.req_body,
        resp_headers=resp_headers,
        resp_body=resp_body,
        resp_status=resp.get("status"),
    )

    new_flow = Flow(
        project_id=original.project_id,
        method=original.method,
        url=original.url,
        req_headers=req_headers,
        req_body=original.req_body,
        resp_status=resp.get("status"),
        resp_headers=resp_headers,
        resp_body=resp_body,
        source="repeater",
        duration_ms=resp.get("duration_ms"),
        **meta,
    )
    db.add(new_flow)
    db.commit()
    db.refresh(new_flow)
    return new_flow
