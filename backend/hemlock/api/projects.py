"""项目管理 API 路由。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from hemlock.core.database import get_db
from hemlock.core.active_project import set_active_project, get_active_project
from hemlock.models.project import Project
from hemlock.models.flow import Flow
from hemlock.models.finding import Finding
from hemlock.models.task import Task
from hemlock.schemas.project import (
    ProjectCreateRequest,
    ProjectUpdateRequest,
    ProjectResponse,
    ProjectDetailResponse,
    SaveProjectRequest,
)

router = APIRouter(tags=["projects"])


class SetActiveProjectRequest(BaseModel):
    """设置当前活跃项目。"""
    project_id: str | None = None


def _scope_list_to_str(scope: list[str]) -> str:
    """将 scope 列表转为逗号分隔的字符串存入数据库。"""
    return ",".join(s.strip() for s in scope if s.strip())


def _project_to_detail(db: Session, p: Project) -> ProjectDetailResponse:
    """将 Project 模型转为含统计的详情响应。"""
    flow_count = db.query(func.count(Flow.id)).filter(Flow.project_id == p.id).scalar() or 0
    finding_count = db.query(func.count(Finding.id)).filter(Finding.project_id == p.id).scalar() or 0
    task_count = db.query(func.count(Task.id)).filter(Task.project_id == p.id).scalar() or 0
    return ProjectDetailResponse(
        id=p.id,
        name=p.name,
        scope=p.scope,
        is_temp=p.is_temp,
        created_at=p.created_at,
        flow_count=flow_count,
        finding_count=finding_count,
        task_count=task_count,
    )


@router.get("/projects", response_model=list[ProjectDetailResponse])
async def list_projects(
    include_temp: bool = True,
    db: Session = Depends(get_db),
):
    """列出所有项目（按创建时间倒序），含统计数据。
    
    默认包含临时项目（前端按需过滤）。
    """
    q = db.query(Project)
    if not include_temp:
        q = q.filter(Project.is_temp == False)
    projects = q.order_by(desc(Project.created_at)).all()
    return [_project_to_detail(db, p) for p in projects]


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(req: ProjectCreateRequest, db: Session = Depends(get_db)):
    """创建新项目。"""
    project = Project(
        name=req.name,
        scope=_scope_list_to_str(req.scope),
        is_temp=False,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.post("/projects/temp", response_model=ProjectResponse, status_code=201)
async def create_temp_project(db: Session = Depends(get_db)):
    """创建临时项目（用户点击"立即开始"）。
    
    临时项目在下次启动时自动清理。
    """
    project = Project(
        name="临时项目",
        scope="",
        is_temp=True,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.post("/projects/active")
async def set_active_project_api(req: SetActiveProjectRequest):
    """设置当前活跃项目 ID。

    前端在用户选择项目时调用此接口，代理层捕获的流量
    将自动关联到此项目。
    """
    set_active_project(req.project_id)
    return {"status": "ok", "active_project_id": req.project_id}


@router.get("/projects/active")
async def get_active_project_api():
    """获取当前活跃项目 ID。"""
    return {"active_project_id": get_active_project()}


@router.post("/projects/{project_id}/save", response_model=ProjectResponse)
async def save_temp_project(project_id: str, req: SaveProjectRequest, db: Session = Depends(get_db)):
    """将临时项目保存为正式项目（设置名称，取消临时标记）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    project.name = req.name
    project.is_temp = False
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects/{project_id}", response_model=ProjectDetailResponse)
async def get_project(project_id: str, db: Session = Depends(get_db)):
    """获取项目详情（含 flow_count, finding_count, task_count 统计）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return _project_to_detail(db, project)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, req: ProjectUpdateRequest, db: Session = Depends(get_db)):
    """更新项目（name, scope）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    if req.name is not None:
        project.name = req.name
    if req.scope is not None:
        project.scope = _scope_list_to_str(req.scope)

    db.commit()
    db.refresh(project)
    return project


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, db: Session = Depends(get_db)):
    """删除项目（级联删除关联数据）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    # 如果删除的是当前活跃项目，清除活跃状态
    if get_active_project() == project_id:
        set_active_project(None)

    db.delete(project)
    db.commit()
    return {"status": "deleted"}
