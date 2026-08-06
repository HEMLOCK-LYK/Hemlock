"""AI 辅助 API 路由。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from hemlock.core.database import get_db
from hemlock.schemas.ai import (
    AnalyzeRequest,
    AnalyzeResponse,
    PayloadRequest,
    PayloadResponse,
)
from hemlock.services import ai_service

router = APIRouter(tags=["ai"])


@router.post("/ai/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest, db: Session = Depends(get_db)):
    """分析指定流量的安全风险。"""
    result = ai_service.analyze_flow(db, req.flow_id, req.question)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return AnalyzeResponse(**result)


@router.post("/ai/payloads", response_model=PayloadResponse)
async def generate_payloads(req: PayloadRequest, db: Session = Depends(get_db)):
    """根据场景生成 payload 字典。"""
    result = ai_service.generate_payloads(
        req.scene, req.context, req.count,
        method=req.method, url=req.url,
        headers=req.headers, body=req.body,
        db=db,
    )
    return PayloadResponse(**result)
