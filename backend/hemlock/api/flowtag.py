"""FlowTag 规则管理 API。"""

import json
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from hemlock.services.flowtag_engine import flowtag_engine, COLOR_MAP

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/flowtag", tags=["flowtag"])


class FlowTagRuleItem(BaseModel):
    name: str
    f_regex: str = ""
    s_regex: str = ""
    format: str = "{0}"
    color: str = "gray"
    scope: str = "any"
    enabled: bool = True
    sensitive: bool = True


class FlowTagRulesResponse(BaseModel):
    rules: list[FlowTagRuleItem]
    colors: dict[str, str]


class FlowTagRulesUpdateRequest(BaseModel):
    rules: list[FlowTagRuleItem]


class FlowTagMatchRequest(BaseModel):
    """手动对指定流量重新匹配。"""
    req_headers: str = ""
    req_body: str = ""
    resp_headers: str = ""
    resp_body: str = ""


class FlowTagMatchResponse(BaseModel):
    tags: str = ""
    tag_colors: str = ""


@router.get("/rules", response_model=FlowTagRulesResponse)
async def get_flowtag_rules():
    """获取所有 FlowTag 规则。"""
    rules_dict = flowtag_engine.get_rules_dict()
    return FlowTagRulesResponse(
        rules=[FlowTagRuleItem(**r) for r in rules_dict],
        colors=COLOR_MAP,
    )


@router.put("/rules", response_model=FlowTagRulesResponse)
async def update_flowtag_rules(req: FlowTagRulesUpdateRequest):
    """更新全部 FlowTag 规则（覆盖式保存）。"""
    rules_data = [r.model_dump() for r in req.rules]
    flowtag_engine.save_rules(rules_data)
    return FlowTagRulesResponse(
        rules=[FlowTagRuleItem(**r) for r in rules_data],
        colors=COLOR_MAP,
    )


@router.post("/match", response_model=FlowTagMatchResponse)
async def match_flowtag(req: FlowTagMatchRequest):
    """手动对指定内容执行 FlowTag 匹配。"""
    tags, tag_colors = flowtag_engine.match(
        req.req_headers, req.req_body, req.resp_headers, req.resp_body
    )
    return FlowTagMatchResponse(tags=tags, tag_colors=tag_colors)


@router.get("/colors")
async def get_flowtag_colors():
    """获取颜色映射。"""
    return COLOR_MAP
