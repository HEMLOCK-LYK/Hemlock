"""多 Agent 协作 API 路由。

提供多 Agent 协作模式的任务管理和角色管理接口：
- 创建协作任务：启动指挥官 Agent，编排多个角色 Agent 协同完成渗透测试
- 角色管理：查询 / 创建 / 更新 / 删除协作角色（内置 + 自定义）
- 查询可用角色：列出所有协作角色及其职责
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hemlock.core.database import get_db
from hemlock.core.agent_registry import (
    get_all_roles,
    get_role_by_name,
    ensure_builtin_roles,
    create_role_file,
    update_role_file,
    delete_role_file,
)
from hemlock.schemas.agent import TaskResponse
from hemlock.services import agent_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/collab", tags=["collab"])


# ===== Pydantic 请求 / 响应模型 =====


class CreateCollabTaskRequest(BaseModel):
    """创建协作任务请求。"""

    project_id: str | None = None
    goal: str
    target_url: str
    roles: list[str]  # 参与的角色列表（如 ["scout", "striker", "scribe"]）
    role_providers: dict[str, str] = {}  # {role_name: provider_id}，未指定则用默认


class RoleSummary(BaseModel):
    """角色摘要（列表用）。"""

    name: str
    label: str
    description: str
    category: str
    is_builtin: bool = False

    class Config:
        from_attributes = True


class RoleDetail(RoleSummary):
    """角色详情（含系统提示词）。"""

    system_prompt: str


class RoleCreateRequest(BaseModel):
    """创建自定义角色请求。"""
    name: str
    label: str
    description: str
    category: str = "custom"
    system_prompt: str


class RoleUpdateRequest(BaseModel):
    """更新角色请求。"""
    label: str
    description: str
    category: str = "custom"
    system_prompt: str


# ===== API 端点 =====


@router.post("/tasks", response_model=TaskResponse)
async def create_collab_task(req: CreateCollabTaskRequest, db: Session = Depends(get_db)):
    """创建协作任务，启动指挥官 Agent。

    指挥官会根据任务目标，依次 spawn 指定角色的子 Agent 执行任务。
    子 Agent 通过共享的 PentestContext 协作，各自负责渗透测试的一个环节。

    Args:
        req: 包含目标 URL、任务目标、参与角色、角色 Provider 映射的请求体。

    Returns:
        创建的任务信息（含 task_id，可用于后续查询进度和结果）。
    """
    # 校验目标 URL
    if not req.target_url:
        raise HTTPException(status_code=400, detail="target_url 不能为空")

    # 校验任务目标
    if not req.goal:
        raise HTTPException(status_code=400, detail="goal 不能为空")

    # 校验角色列表
    if not req.roles:
        raise HTTPException(status_code=400, detail="roles 不能为空，至少指定一个角色")

    # 确保内置角色文件存在
    ensure_builtin_roles()
    available_roles = {r.name for r in get_all_roles(db)}
    invalid_roles = [r for r in req.roles if r not in available_roles]
    if invalid_roles:
        raise HTTPException(
            status_code=400,
            detail=f"未知角色: {invalid_roles}。可用角色: {sorted(available_roles)}",
        )

    # 构建完整任务目标（包含协作信息）
    full_goal = (
        f"[多 Agent 协作模式] {req.goal}\n"
        f"参与角色: {', '.join(req.roles)}"
    )

    # 创建任务
    task = agent_service.create_task(db, goal=full_goal, project_id=req.project_id)

    logger.info(
        f"创建协作任务 | task_id={task.id} | 目标={req.target_url} | "
        f"角色={req.roles} | providers={req.role_providers}"
    )

    # 后台启动指挥官 Agent
    asyncio.create_task(
        agent_service.run_coordinator_in_background(
            task_id=task.id,
            goal=full_goal,
            target_url=req.target_url,
            roles=req.roles,
            role_providers=req.role_providers,
        )
    )

    return _task_to_response(task)


@router.get("/roles", response_model=list[RoleSummary])
async def list_roles(db: Session = Depends(get_db)):
    """列出所有可用的协作角色。

    返回每个角色的名称、展示名、职责描述和分类。
    用于前端展示角色选择界面。
    """
    ensure_builtin_roles()
    roles = get_all_roles(db)
    return [
        RoleSummary(
            name=role.name,
            label=role.label,
            description=role.description,
            category=role.category,
            is_builtin=role.is_builtin,
        )
        for role in roles
    ]


@router.get("/roles/{name}", response_model=RoleDetail)
async def get_role(name: str, db: Session = Depends(get_db)):
    """获取指定角色的详细信息。

    返回角色的完整定义，包括系统提示词、分类和内置标记。

    Args:
        name: 角色名称（scout/striker/auditor/radar/scribe）。

    Returns:
        角色详情（含 system_prompt）。
    """
    ensure_builtin_roles()
    role = get_role_by_name(db, name)
    if not role:
        raise HTTPException(
            status_code=404,
            detail=f"角色不存在: {name}。可用角色: {[r.name for r in get_all_roles(db)]}",
        )

    return RoleDetail(
        name=role.name,
        label=role.label,
        description=role.description,
        category=role.category,
        is_builtin=role.is_builtin,
        system_prompt=role.system_prompt,
    )


@router.post("/roles", response_model=RoleDetail, status_code=201)
async def create_role(req: RoleCreateRequest, db: Session = Depends(get_db)):
    """创建自定义角色。

    在 ~/.hemlock/agents/ 目录下创建新的 .md 角色文件。
    内置角色名称不允许重复创建。

    Args:
        req: 包含角色名称、显示名、描述、分类和系统提示词的请求体。

    Returns:
        创建的角色详情。
    """
    ensure_builtin_roles()
    try:
        role = create_role_file(
            name=req.name,
            label=req.label,
            description=req.description,
            category=req.category,
            system_prompt=req.system_prompt,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(f"创建角色: {role.name} ({role.label})")
    return RoleDetail(
        name=role.name,
        label=role.label,
        description=role.description,
        category=role.category,
        is_builtin=role.is_builtin,
        system_prompt=role.system_prompt,
    )


@router.put("/roles/{name}", response_model=RoleDetail)
async def update_role(name: str, req: RoleUpdateRequest, db: Session = Depends(get_db)):
    """更新角色定义。

    更新 ~/.hemlock/agents/{name}.md 文件的内容。
    保留原文件的 is_builtin 标记。

    Args:
        name: 角色名称。
        req: 包含显示名、描述、分类和系统提示词的请求体。

    Returns:
        更新后的角色详情。
    """
    ensure_builtin_roles()
    try:
        role = update_role_file(
            name=name,
            label=req.label,
            description=req.description,
            category=req.category,
            system_prompt=req.system_prompt,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    logger.info(f"更新角色: {role.name} ({role.label})")
    return RoleDetail(
        name=role.name,
        label=role.label,
        description=role.description,
        category=role.category,
        is_builtin=role.is_builtin,
        system_prompt=role.system_prompt,
    )


@router.delete("/roles/{name}", status_code=204)
async def delete_role(name: str, db: Session = Depends(get_db)):
    """删除自定义角色。

    删除 ~/.hemlock/agents/{name}.md 文件。
    内置角色不允许删除。

    Args:
        name: 角色名称。
    """
    ensure_builtin_roles()
    try:
        delete_role_file(name)
    except ValueError as e:
        status_code = 403 if "不允许删除" in str(e) else 404
        raise HTTPException(status_code=status_code, detail=str(e))

    logger.info(f"删除角色: {name}")


# ===== 辅助函数 =====


def _task_to_response(task) -> TaskResponse:
    """将 Task 模型转为 TaskResponse（复用 agent.py 中的逻辑）。"""
    import json
    from hemlock.schemas.agent import StepData

    steps_raw = json.loads(task.steps or "[]")
    steps = [
        StepData(
            type=s.get("type", ""),
            data=s.get("data", ""),
            timestamp=s.get("timestamp", ""),
        )
        for s in steps_raw
    ]

    return TaskResponse(
        id=task.id,
        goal=task.goal or "",
        status=task.status,
        steps=steps,
        started_at=task.started_at,
        ended_at=task.ended_at,
    )
