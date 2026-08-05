"""请求发送 API 路由。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from hemlock.core.database import get_db
from hemlock.core.scope import check_scope
from hemlock.models.flow import Flow
from hemlock.schemas.request import SendRequestRequest, SendRequestResponse
from hemlock.services.request_service import send_raw_request
from hemlock.services.flowtag_engine import enrich_flow_fields

router = APIRouter(tags=["request"])


@router.post("/request/send", response_model=SendRequestResponse)
async def send_request(req: SendRequestRequest, db: Session = Depends(get_db)):
    """发送自定义 HTTP 请求，响应存入流量库（source=repeater）。"""
    if not check_scope(req.url):
        raise HTTPException(status_code=403, detail="URL 超出授权目标范围")

    resp = await send_raw_request(
        method=req.method,
        url=req.url,
        headers_str=req.headers,
        body=req.body,
    )

    resp_headers = resp.get("headers", "")
    resp_body = resp.get("body", "")
    req_headers = resp.get("req_headers", req.headers)

    # 提取流量元数据（host/mime/tags 等），与代理捕获保持一致
    meta = enrich_flow_fields(
        url=req.url,
        req_headers=req_headers,
        req_body=req.body,
        resp_headers=resp_headers,
        resp_body=resp_body,
        resp_status=resp.get("status"),
    )

    flow = Flow(
        project_id=req.project_id,
        method=req.method.upper(),
        url=req.url,
        req_headers=req_headers,
        req_body=req.body,
        resp_status=resp.get("status"),
        resp_headers=resp_headers,
        resp_body=resp_body,
        source="repeater",
        duration_ms=resp.get("duration_ms"),
        **meta,
    )
    db.add(flow)
    db.commit()
    db.refresh(flow)

    return SendRequestResponse(
        flow_id=flow.id,
        status=resp.get("status"),
        headers=resp_headers,
        body=resp_body,
        duration_ms=resp.get("duration_ms"),
    )
