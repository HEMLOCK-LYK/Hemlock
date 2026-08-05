"""Skill 和 MCP 服务器管理 API。

Skill 部分基于 Markdown 文件（~/.hemlock/skills/*.md），无数据库依赖。
MCP 部分仍使用数据库存储配置。
"""

import json
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hemlock.core.database import get_db
from hemlock.core.skill_registry import (
    get_enabled_skills,
    get_skill_by_name,
    create_skill_file,
    update_skill_file,
    delete_skill_file,
)
from hemlock.models.mcp_server import McpServer
from hemlock.core.mcp_manager import mcp_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/skills", tags=["skills"])


# ===== Skill 管理（文件方案） =====

class SkillCreateRequest(BaseModel):
    name: str
    content: str = ""  # 完整 Markdown 内容
    # 兼容旧前端：也可传 label/description/instructions 分离字段
    label: str | None = None
    description: str | None = None
    instructions: str | None = None


class SkillUpdateRequest(BaseModel):
    content: str | None = None  # 完整 Markdown 内容
    # 兼容旧前端
    label: str | None = None
    description: str | None = None
    instructions: str | None = None


@router.get("")
async def list_skills():
    """列出所有技能（扫描 ~/.hemlock/skills/*.md）。"""
    skills = get_enabled_skills()
    return {
        "skills": [
            {
                "id": s.name,  # id = name（文件名）
                "name": s.name,
                "label": s.label,
                "description": s.description,
                "category": s.category,
                "enabled": True,  # 文件存在即启用
                "is_builtin": s.is_builtin,
                "instructions_preview": (s.instructions or "")[:200],
            }
            for s in skills
        ]
    }


@router.get("/{skill_name}")
async def get_skill(skill_name: str):
    """获取技能详情（含完整指令）。"""
    skill = get_skill_by_name(None, skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 '{skill_name}' 不存在")
    return {
        "id": skill.name,
        "name": skill.name,
        "label": skill.label,
        "description": skill.description,
        "instructions": skill.instructions,
        "category": skill.category,
        "enabled": True,
        "is_builtin": skill.is_builtin,
    }


@router.post("")
async def create_skill(req: SkillCreateRequest):
    """创建技能 .md 文件。

    两种方式：
    1. 传 content（完整 Markdown）——推荐，直接写入文件
    2. 传 label + description + instructions ——组装成 Markdown 后写入
    """
    # 组装完整 Markdown 内容
    if req.content:
        content = req.content
    elif req.instructions:
        parts = []
        if req.label:
            parts.append(f"# {req.label}")
        if req.description:
            parts.append(f"\n{req.description}\n")
        parts.append(f"\n{req.instructions}")
        content = "\n".join(parts)
    else:
        raise HTTPException(status_code=400, detail="需要提供 content 或 instructions 字段")

    try:
        skill = create_skill_file(req.name, content)
        return {"id": skill.name, "name": skill.name, "message": "技能创建成功"}
    except ValueError as e:
        status_code = 409 if "已存在" in str(e) else 400
        raise HTTPException(status_code=status_code, detail=str(e))


@router.put("/{skill_name}")
async def update_skill(skill_name: str, req: SkillUpdateRequest):
    """更新技能 .md 文件内容。"""
    # 组装完整 Markdown 内容
    if req.content:
        content = req.content
    elif req.instructions:
        existing = get_skill_by_name(None, skill_name)
        if not existing:
            raise HTTPException(status_code=404, detail=f"技能 '{skill_name}' 不存在")

        parts = []
        label = req.label or existing.label
        desc = req.description or existing.description
        if label:
            parts.append(f"# {label}")
        if desc:
            parts.append(f"\n{desc}\n")
        parts.append(f"\n{req.instructions}")
        content = "\n".join(parts)
    else:
        raise HTTPException(status_code=400, detail="需要提供 content 或 instructions 字段")

    try:
        update_skill_file(skill_name, content)
        return {"message": "技能更新成功"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{skill_name}")
async def delete_skill(skill_name: str):
    """删除技能 .md 文件。"""
    try:
        delete_skill_file(skill_name)
        return {"message": "技能已删除"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ===== MCP 服务器管理（仍使用数据库） =====

class McpServerCreateRequest(BaseModel):
    name: str
    transport: str = "stdio"  # stdio / http
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    url: str = ""
    enabled: bool = False


class McpServerUpdateRequest(BaseModel):
    name: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    enabled: bool | None = None


@router.get("/mcp/list")
async def list_mcp_servers(db: Session = Depends(get_db)):
    """列出所有 MCP 服务器。"""
    return {"servers": mcp_manager.get_status_list(db)}


@router.post("/mcp")
async def create_mcp_server(req: McpServerCreateRequest, db: Session = Depends(get_db)):
    """添加 MCP 服务器配置。"""
    existing = db.query(McpServer).filter(McpServer.name == req.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"MCP 服务器 '{req.name}' 已存在")

    server = McpServer(
        name=req.name,
        transport=req.transport,
        command=req.command,
        args=json.dumps(req.args),
        env=json.dumps(req.env),
        url=req.url,
        enabled=req.enabled,
    )
    db.add(server)
    db.commit()
    return {"id": server.id, "name": server.name, "message": "MCP 服务器添加成功"}


@router.put("/mcp/{server_id}")
async def update_mcp_server(server_id: str, req: McpServerUpdateRequest, db: Session = Depends(get_db)):
    """更新 MCP 服务器配置。"""
    server = db.query(McpServer).filter(McpServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP 服务器不存在")

    if req.name is not None:
        server.name = req.name
    if req.transport is not None:
        server.transport = req.transport
    if req.command is not None:
        server.command = req.command
    if req.args is not None:
        server.args = json.dumps(req.args)
    if req.env is not None:
        server.env = json.dumps(req.env)
    if req.url is not None:
        server.url = req.url
    if req.enabled is not None:
        server.enabled = req.enabled

    db.commit()
    return {"message": "MCP 服务器更新成功"}


@router.post("/mcp/{server_id}/connect")
async def connect_mcp_server(server_id: str, db: Session = Depends(get_db)):
    """连接 MCP 服务器。"""
    server = db.query(McpServer).filter(McpServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP 服务器不存在")

    success = await mcp_manager.connect_server(db, server)
    if success:
        return {"status": "connected", "tool_count": server.tool_count, "message": "连接成功"}
    else:
        return {"status": "error", "error": server.last_error, "message": "连接失败"}


@router.post("/mcp/{server_id}/disconnect")
async def disconnect_mcp_server(server_id: str, db: Session = Depends(get_db)):
    """断开 MCP 服务器。"""
    server = db.query(McpServer).filter(McpServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP 服务器不存在")

    await mcp_manager.disconnect_server(db, server)
    return {"status": "disconnected", "message": "已断开"}


@router.delete("/mcp/{server_id}")
async def delete_mcp_server(server_id: str, db: Session = Depends(get_db)):
    """删除 MCP 服务器。"""
    server = db.query(McpServer).filter(McpServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP 服务器不存在")

    # 先断开连接
    await mcp_manager.disconnect_server(db, server)
    db.delete(server)
    db.commit()
    return {"message": "MCP 服务器已删除"}


@router.get("/mcp/{server_id}/tools")
async def list_mcp_tools(server_id: str, db: Session = Depends(get_db)):
    """列出 MCP 服务器提供的工具。"""
    server = db.query(McpServer).filter(McpServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="MCP 服务器不存在")

    conn = mcp_manager.get_connection(server.name)
    if not conn:
        return {"tools": [], "message": "服务器未连接"}

    return {"tools": conn.tools}
