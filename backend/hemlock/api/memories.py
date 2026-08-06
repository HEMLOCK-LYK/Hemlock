"""Memory 管理 API - 基于 Markdown 文件的跨会话长期记忆增删改查。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hemlock.core.database import get_db
from hemlock.core import memory_manager
from hemlock.core.memory_manager import MEMORY_TYPES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memories", tags=["memories"])


# ===== 请求模型 =====

class MemoryCreateRequest(BaseModel):
    name: str
    description: str
    type: str  # user/feedback/project/reference
    trigger: list[str] = []
    content: str = ""
    project_id: str | None = None


class MemoryUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    type: str | None = None
    trigger: list[str] | None = None
    content: str | None = None
    project_id: str | None = None


class MemorySearchRequest(BaseModel):
    text: str
    n_results: int = 5


# ===== API 端点 =====
# 注意：固定路径 (/index, /search) 必须在参数路径 (/{memory_id}) 之前定义

@router.get("")
async def list_memories(
    type: str | None = Query(None, description="按类型过滤: user/feedback/project/reference"),
    project_id: str | None = Query(None, description="按项目 ID 过滤"),
    db: Session = Depends(get_db),
):
    """列出所有记忆，支持按类型和项目过滤。"""
    memories = memory_manager.list_memories(db, type_filter=type, project_id=project_id)
    return {
        "memories": [memory_manager._memory_to_dict(m) for m in memories],
        "total": len(memories),
    }


@router.get("/index")
async def get_memory_index(db: Session = Depends(get_db)):
    """获取记忆摘要索引。"""
    index = memory_manager.get_memory_index(db)
    return {"index": index, "total": len(index)}


@router.post("/search")
async def search_memories(req: MemorySearchRequest, db: Session = Depends(get_db)):
    """按关键词搜索记忆。"""
    results = memory_manager.search_by_trigger(db, text=req.text, n_results=req.n_results)
    return {"results": results, "total": len(results)}


@router.post("")
async def create_memory(req: MemoryCreateRequest, db: Session = Depends(get_db)):
    """创建记忆条目（写入 .md 文件）。name 必须唯一。"""
    try:
        memory = memory_manager.create_memory(
            db=db,
            name=req.name,
            description=req.description,
            type=req.type,
            trigger_list=req.trigger,
            content=req.content,
            project_id=req.project_id,
        )
        return {
            "id": memory.name,
            "name": memory.name,
            "message": "记忆创建成功",
        }
    except ValueError as e:
        if "已存在" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{memory_id}")
async def get_memory(memory_id: str, db: Session = Depends(get_db)):
    """获取记忆详情（含完整正文）。"""
    memory = memory_manager.get_memory(db, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return memory_manager._memory_to_dict(memory)


@router.put("/{memory_id}")
async def update_memory(memory_id: str, req: MemoryUpdateRequest, db: Session = Depends(get_db)):
    """更新记忆条目。"""
    try:
        kwargs = {k: v for k, v in req.model_dump().items() if v is not None}
        if "trigger" in kwargs:
            kwargs["trigger_list"] = kwargs.pop("trigger")

        memory = memory_manager.update_memory(db=db, memory_id=memory_id, **kwargs)
        return {
            "id": memory.name,
            "name": memory.name,
            "message": "记忆更新成功",
        }
    except ValueError as e:
        if "不存在" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        if "已存在" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str, db: Session = Depends(get_db)):
    """删除记忆条目（删除 .md 文件）。"""
    try:
        memory_manager.delete_memory(db=db, memory_id=memory_id)
        return {"message": "记忆已删除"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
