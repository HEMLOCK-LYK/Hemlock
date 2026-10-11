"""攻击链画布 API（第二期）。

第一期已落库的数据（models/attack_chain.py + services/attack_chain_builder.py）
通过这里暴露给前端画布：列表 / 详情 / 存坐标 / 加删边 / 确认状态。

边界约定（与 attack_chain_builder 一致）：
- 边存 AttackChain.edges JSON 列，格式 [{"from": and_x, "to": and_y, "label": "..."}]
- 主干边由自动构建按时间序维护，这里加删的是画布上的手工编辑
- credential 节点的凭据在入库时已掩码，API 只做展示不做二次脱敏
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from hemlock.core.database import get_db
from hemlock.models.attack_chain import AttackChain, AttackNode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attack-chain", tags=["attack-chain"])

# 与 builder 的幂等去重保持同序：先按 source_step，手工节点（无 source_step）按创建时间
_NODE_ORDER = (
    AttackNode.source_step.asc().nullslast(),
    AttackNode.created_at.asc(),
)


def _parse_json(text: str | None, fallback):
    try:
        parsed = json.loads(text or "")
    except (json.JSONDecodeError, ValueError):
        return fallback
    return parsed if parsed is not None else fallback


# ===== 响应模型 =====

class AttackNodeOut(BaseModel):
    id: str
    node_type: str
    title: str
    detail: dict = {}
    source_step: int | None = None
    flow_id: str | None = None
    x: float = 0.0
    y: float = 0.0


class AttackChainSummary(BaseModel):
    id: str
    task_id: str | None = None
    name: str = ""
    status: str = "draft"
    node_count: int = 0
    updated_at: str | None = None


class AttackChainDetailOut(AttackChainSummary):
    nodes: list[AttackNodeOut] = []
    edges: list[dict] = []


# ===== 请求模型 =====

class PositionRequest(BaseModel):
    x: float
    y: float


class EdgeRequest(BaseModel):
    source: str
    target: str
    label: str = ""


class EdgeDeleteRequest(BaseModel):
    source: str
    target: str


class StatusRequest(BaseModel):
    status: str  # draft / confirmed / failed


# ===== 端点 =====

@router.get("", response_model=list[AttackChainSummary])
async def list_chains(
    project_id: str | None = Query(None, description="按项目过滤"),
    db: Session = Depends(get_db),
):
    query = db.query(AttackChain)
    if project_id:
        query = query.filter(AttackChain.project_id == project_id)
    chains = query.order_by(AttackChain.updated_at.desc()).all()
    if not chains:
        return []

    counts = dict(
        db.query(AttackNode.chain_id, func.count(AttackNode.id))
        .filter(AttackNode.chain_id.in_([c.id for c in chains]))
        .group_by(AttackNode.chain_id)
        .all()
    )
    return [
        AttackChainSummary(
            id=c.id,
            task_id=c.task_id,
            name=c.name or "",
            status=c.status or "draft",
            node_count=counts.get(c.id, 0),
            updated_at=c.updated_at.isoformat() if c.updated_at else None,
        )
        for c in chains
    ]


@router.get("/{chain_id}", response_model=AttackChainDetailOut)
async def get_chain(chain_id: str, db: Session = Depends(get_db)):
    chain = db.query(AttackChain).filter(AttackChain.id == chain_id).first()
    if chain is None:
        raise HTTPException(status_code=404, detail="attack chain not found")
    nodes = (
        db.query(AttackNode)
        .filter(AttackNode.chain_id == chain_id)
        .order_by(*_NODE_ORDER)
        .all()
    )
    return AttackChainDetailOut(
        id=chain.id,
        task_id=chain.task_id,
        name=chain.name or "",
        status=chain.status or "draft",
        node_count=len(nodes),
        updated_at=chain.updated_at.isoformat() if chain.updated_at else None,
        nodes=[
            AttackNodeOut(
                id=n.id,
                node_type=n.node_type,
                title=n.title or "",
                detail=_parse_json(n.detail, {}),
                source_step=n.source_step,
                flow_id=n.flow_id,
                x=n.x if n.x is not None else 0.0,
                y=n.y if n.y is not None else 0.0,
            )
            for n in nodes
        ],
        edges=_parse_json(chain.edges, []),
    )


@router.put("/{chain_id}/nodes/{node_id}/position")
async def save_node_position(
    chain_id: str, node_id: str, req: PositionRequest, db: Session = Depends(get_db)
):
    """拖拽节点后持久化画布坐标。"""
    node = (
        db.query(AttackNode)
        .filter(AttackNode.id == node_id, AttackNode.chain_id == chain_id)
        .first()
    )
    if node is None:
        raise HTTPException(status_code=404, detail="attack node not found")
    node.x = req.x
    node.y = req.y
    db.commit()
    return {"ok": True}


@router.post("/{chain_id}/edges")
async def add_edge(chain_id: str, req: EdgeRequest, db: Session = Depends(get_db)):
    """画布连线 → 加边。同向同边幂等，自环拒绝。"""
    chain = db.query(AttackChain).filter(AttackChain.id == chain_id).first()
    if chain is None:
        raise HTTPException(status_code=404, detail="attack chain not found")
    if req.source == req.target:
        raise HTTPException(status_code=400, detail="cannot connect a node to itself")
    node_ids = {
        n.id
        for n in db.query(AttackNode.id)
        .filter(AttackNode.chain_id == chain_id, AttackNode.id.in_([req.source, req.target]))
        .all()
    }
    if len(node_ids) < 2:
        raise HTTPException(status_code=404, detail="node not in this chain")

    edges = _parse_json(chain.edges, [])
    if not isinstance(edges, list):
        edges = []
    for e in edges:
        if isinstance(e, dict) and e.get("from") == req.source and e.get("to") == req.target:
            if e.get("label", "") != req.label:
                e["label"] = req.label
                db.commit()
            return {"ok": True, "duplicate": True}

    edges.append({"from": req.source, "to": req.target, "label": req.label})
    chain.edges = json.dumps(edges, ensure_ascii=False)
    db.commit()
    return {"ok": True, "duplicate": False}


@router.delete("/{chain_id}/edges")
async def delete_edge(chain_id: str, req: EdgeDeleteRequest, db: Session = Depends(get_db)):
    """删除一条边（按方向匹配）。注意：主干边可能在下次自动构建时被重建。"""
    chain = db.query(AttackChain).filter(AttackChain.id == chain_id).first()
    if chain is None:
        raise HTTPException(status_code=404, detail="attack chain not found")

    edges = _parse_json(chain.edges, [])
    if not isinstance(edges, list):
        edges = []
    kept = [
        e
        for e in edges
        if not (
            isinstance(e, dict) and e.get("from") == req.source and e.get("to") == req.target
        )
    ]
    if len(kept) == len(edges):
        return {"ok": True, "deleted": False}
    chain.edges = json.dumps(kept, ensure_ascii=False)
    db.commit()
    return {"ok": True, "deleted": True}


@router.put("/{chain_id}/status")
async def update_chain_status(
    chain_id: str, req: StatusRequest, db: Session = Depends(get_db)
):
    """确认 / 撤回链状态：draft / confirmed / failed。"""
    if req.status not in ("draft", "confirmed", "failed"):
        raise HTTPException(status_code=400, detail="status must be draft/confirmed/failed")
    chain = db.query(AttackChain).filter(AttackChain.id == chain_id).first()
    if chain is None:
        raise HTTPException(status_code=404, detail="attack chain not found")
    chain.status = req.status
    db.commit()
    return {"ok": True, "status": chain.status}
