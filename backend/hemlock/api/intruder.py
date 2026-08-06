"""Intruder API 路由。"""

import asyncio
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from hemlock.core.database import SessionLocal, get_db
from hemlock.core.scope import check_scope
from hemlock.schemas.intruder import IntruderConfig, IntruderResult
from hemlock.services.intruder_service import run_intruder, _expand_payloads

router = APIRouter(tags=["intruder"])

# 内存中保存任务结果（简化版，后续可入库存）
_tasks: dict[str, dict] = {}


@router.post("/intruder/run", response_model=IntruderResult)
async def intruder_run(config: IntruderConfig, db: Session = Depends(get_db)):
    """启动爆破任务，立即返回 task_id。

    任务在后台执行，结果通过 WebSocket (intruder_progress 事件) 逐个推送。
    前端可通过 GET /intruder/{task_id}/result 查询最终结果。
    """
    if not check_scope(config.url):
        raise HTTPException(status_code=403, detail="URL 超出授权目标范围")

    task_id = f"int_{uuid.uuid4().hex[:16]}"
    total = len(_expand_payloads(config.positions, config.attack_type))

    # 在后台运行爆破任务（使用独立 DB 会话，避免请求结束后会话关闭）
    async def _run_background():
        bg_db = SessionLocal()
        try:
            result = await run_intruder(
                db=bg_db,
                method=config.method,
                url=config.url,
                headers=config.headers,
                body=config.body,
                positions=config.positions,
                concurrency=config.concurrency,
                project_id=config.project_id,
                attack_type=config.attack_type,
                task_id=task_id,
            )
            _tasks[task_id] = result
        except Exception:
            # 即使失败也记录，以便前端查询
            _tasks[task_id] = {
                "task_id": task_id,
                "total": 0,
                "results": [],
            }
        finally:
            bg_db.close()

    asyncio.create_task(_run_background())

    # 立即返回，前端通过 WebSocket 接收逐个结果
    return IntruderResult(task_id=task_id, total=total, results=[])


@router.get("/intruder/{task_id}/result", response_model=IntruderResult)
async def intruder_result(task_id: str):
    """查询爆破结果。"""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail="Intruder task not found")
    return IntruderResult(**_tasks[task_id])
