"""流量 API 路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hemlock.core.database import get_db
from hemlock.services import flow_service
from hemlock.services.flow_filter import get_noise_analysis, get_flagged_targets
from hemlock.schemas.flow import (
    FlowResponse,
    FlowListResponse,
    FlowListItem,
    ReplayResponse,
)

router = APIRouter(tags=["flows"])


class MarkNoiseRequest(BaseModel):
    is_noise: bool
    reason: str = ""


@router.get("/flows", response_model=FlowListResponse)
async def list_flows(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    method: str | None = None,
    url: str | None = None,
    source: str | None = None,
    status: int | None = None,
    project_id: str | None = None,
    hide_noise: bool = False,
    host: str | None = None,
    tags: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    """查询流量列表，支持分页与过滤。

    project_id 为必填过滤条件：未提供时返回空列表，
    确保流量数据严格按项目隔离，不会泄露其他项目的数据。
    hide_noise=True 时过滤掉自动检测的杂包。
    host 模糊匹配 host 字段。
    tags 非空时仅返回包含标签的流量。
    search 参数在所有文本字段（请求头、请求体、响应头、响应体等）中模糊搜索。
    """
    if not project_id:
        return FlowListResponse(total=0, page=page, page_size=page_size, items=[], noise_count=0)

    items, total = flow_service.list_flows(
        db, page=page, page_size=page_size,
        method=method, url_contains=url, source=source, status=status,
        project_id=project_id, hide_noise=hide_noise,
        host=host, tags_only=bool(tags),
        search=search,
    )
    # 统计杂包总数（不受 hide_noise 影响）
    noise_count = flow_service.count_noise(db, project_id)

    # 计算流量编号：按时间倒序，最新流量编号最大（=total），最早为 1
    offset = (page - 1) * page_size
    item_list = []
    for idx, flow in enumerate(items):
        item = FlowListItem.model_validate(flow)
        item.flow_number = total - offset - idx
        item_list.append(item)
    return FlowListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=item_list,
        noise_count=noise_count,
    )


@router.delete("/flows")
async def clear_flows(
    project_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """清空指定项目的所有流量（保留 intruder 源数据）。

    project_id 为必填查询参数；仅删除 source != 'intruder' 的流量，
    避免误删爆破器（Intruder）数据。
    """
    deleted = flow_service.clear_flows(db, project_id)
    return {"deleted": deleted, "project_id": project_id}


@router.get("/flows/noise/stats")
async def get_noise_stats(
    project_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """获取杂包统计信息。"""
    if not project_id:
        return {"total": 0, "summary": []}
    noise_total = flow_service.count_noise(db, project_id)
    summary = flow_service.get_noise_summary(db, project_id)
    return {"total": noise_total, "summary": summary}


@router.get("/flows/noise/list", response_model=FlowListResponse)
async def list_noise_flows(
    project_id: str = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """获取杂包流量列表。"""
    if not project_id:
        return FlowListResponse(total=0, page=page, page_size=page_size, items=[], noise_count=0)

    items, total = flow_service.get_noise_flows(db, project_id, page, page_size)
    offset = (page - 1) * page_size
    item_list = []
    for idx, flow in enumerate(items):
        item = FlowListItem.model_validate(flow)
        item.flow_number = total - offset - idx
        item_list.append(item)
    return FlowListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=item_list,
        noise_count=total,
    )


@router.post("/flows/{flow_id}/noise")
async def mark_flow_noise(
    flow_id: str,
    req: MarkNoiseRequest,
    db: Session = Depends(get_db),
):
    """手动标记/取消标记流量为杂包。"""
    flow = flow_service.mark_flow_noise(db, flow_id, req.is_noise, req.reason)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    return {"id": flow.id, "is_noise": flow.is_noise, "noise_reason": flow.noise_reason}


@router.get("/flows/noise/analysis")
async def noise_analysis(limit: int = Query(20, ge=1, le=100)):
    """抽样排序分析：返回当前窗口内请求最多的 URL 和 host。

    实现"抽取一部分数据包来检测，然后数量排序"策略。
    用户可查看此列表，了解哪些 URL/host 产生最多流量。
    """
    return {"items": get_noise_analysis(limit)}


@router.get("/flows/noise/flagged")
async def noise_flagged():
    """获取所有已标记为杂包的 URL 和 host。"""
    return get_flagged_targets()


@router.get("/flows/{flow_id}", response_model=FlowResponse)
async def get_flow(flow_id: str, db: Session = Depends(get_db)):
    """获取单条流量详情。"""
    flow = flow_service.get_flow(db, flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    resp = FlowResponse.model_validate(flow)
    # 计算流量编号（该项目中时间戳晚于当前 flow 的数量 + 1）
    if flow.project_id:
        newer_count = flow_service.count_flows_after(db, flow.project_id, flow.timestamp)
        resp.flow_number = newer_count + 1
    return resp


@router.post("/flows/{flow_id}/replay", response_model=ReplayResponse)
async def replay_flow(flow_id: str, db: Session = Depends(get_db)):
    """重放指定流量。"""
    new_flow = await flow_service.replay_flow(db, flow_id)
    if not new_flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    return ReplayResponse(
        flow_id=new_flow.id,
        resp_status=new_flow.resp_status,
        resp_headers=new_flow.resp_headers,
        resp_body=new_flow.resp_body,
    )
