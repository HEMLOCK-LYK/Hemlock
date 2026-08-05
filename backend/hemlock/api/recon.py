"""信息收集 API。

提供：
1. AI 信息收集助手（对话式，自主规划并执行信息收集）
2. 空间测绘引擎 API Key 管理
3. 手动多引擎搜索
4. 报告文件查看/删除
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hemlock.core.database import get_db
from hemlock.services import recon_service
from hemlock.services import agent_service
from hemlock.models.task import Task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recon", tags=["recon"])


# ===== 资产收集角色系统提示词 =====
# 原 recon_agent_service.py 已合并到主 agent_service，此提示词通过
# system_prompt_override 参数注入，让主 agent 扮演资产收集角色。

RECON_SYSTEM_PROMPT = """你是 Hemlock 的 AI 信息收集助手，专门负责全面、准确的信息收集工作。

## 核心职责
1. 接收用户提供的目标描述，理解收集意图，进行全面信息收集
2. 使用多种方法和数据源，确保收集结果全面
3. 对收集到的信息进行整理、分析和关联
4. 生成结构化的 Markdown 报告

## 目标类型（灵活接收，不要限定格式）
用户可能提供以下类型的目标，你需要智能识别并转化为可执行的收集策略：
- **公司/组织名称** → 先推断主域名，再全面收集
- **SRC 范围** → 识别公司主体域名，收集其所有子域名、IP和相关资产
- **教育机构** → 推断域名（通常含 .edu.cn 等），收集子域名和网络资产
- **具体域名** → 直接收集子域名、DNS、HTTP 信息
- **IP 或 IP 段** → 反查域名、端口扫描、服务识别
- **模糊描述** → 询问用户确认具体公司名或域名，再开始收集
- **多个目标**：用户可能一次提供多个目标，分别收集并汇总

## 信息收集方法
1. **空间测绘引擎搜索**（FOFA、Quake、Hunter、ZoomEye、Alpha）— 需要 API Key
2. **子域名枚举**（crt.sh 证书透明度、HackerTarget、Chaos）— 部分免费
3. **DNS 记录查询**（A/CNAME/MX/TXT/NS）— 免费
4. **HTTP 探测**（访问目标网站，分析响应头、技术栈、标题）— 免费
5. **其他互联网信息收集** — 根据目标灵活运用

## 工作流程
1. 分析用户提供的目标描述，确定其类型和收集范围
2. 如果目标是公司名/SRC/EDU 等，先推断主域名，再基于域名展开收集
3. 并行调用多个工具进行信息收集（先免费引擎，再付费引擎）
4. 分析初步结果，决定是否需要深入收集（如发现新子域名则继续枚举）
5. 整理所有结果，生成综合 Markdown 报告
6. 向用户汇报关键发现和资产统计

## 工具使用指南
- **recon_search**: 多引擎并行搜索。对于空间测绘引擎使用各自语法（如 FOFA: domain="example.com"），对于子域名/DNS 引擎直接输入域名
- **subdomain_enum**: 专门用于子域名枚举，自动调用 crt.sh + HackerTarget + Chaos
- **dns_lookup**: 查询 DNS 记录，了解目标域名解析情况
- **http_probe**: HTTP 探测，访问目标网站获取响应头、状态码、技术栈信息

## 注意事项
- 优先使用免费引擎（crt.sh、HackerTarget、DNS），再使用付费引擎
- 每次搜索都会自动保存 Markdown 报告
- 用中文回复用户
- 结果要全面和准确
- 收集完成后，向用户总结关键发现和资产数量
- 如果目标描述不明确，先向用户确认后再开始收集
"""


# ===== 请求/响应模型 =====


class SetKeyRequest(BaseModel):
    engine: str
    key: str


class SearchRequest(BaseModel):
    query: str
    engines: list[str]
    size: int = 100


class CreateReconChatRequest(BaseModel):
    project_id: str | None = None
    goal: str = ""


class ReconChatRequest(BaseModel):
    task_id: str
    message: str
    project_id: str | None = None


class ReconChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict]


# ===== AI 信息收集助手（对话式）=====


@router.post("/agent/conversations")
async def create_recon_conversation(req: CreateReconChatRequest, db: Session = Depends(get_db)):
    """创建一个 AI 信息收集对话任务。"""
    goal = (req.goal or "").strip()
    # 使用 [RECON] 前缀区分信息收集对话和安全测试对话
    task = agent_service.create_task(
        db, goal=f"[RECON]{goal}", project_id=req.project_id
    )

    initial_steps = [{
        "type": "system_message",
        "data": "AI 信息收集助手已就绪。请输入要收集信息的目标（域名/IP/公司名）。",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]
    task.steps = json.dumps(initial_steps, ensure_ascii=False, default=str)
    db.commit()
    db.refresh(task)

    return _task_to_dict(task)


@router.post("/agent/chat", response_model=ReconChatResponse)
async def recon_chat(req: ReconChatRequest, db: Session = Depends(get_db)):
    """AI 信息收集助手对话：用户发消息 -> AI 自主调用工具收集信息 -> 回复。

    复用主 Agent 引擎（agent_service.chat_with_agent），传入资产收集系统提示词。
    事件通过 event_bus 以 agent 前缀发布，前端监听 recon_chat_message 等事件名兼容。
    """
    result = await agent_service.chat_with_agent(
        req.task_id, req.message, db,
        system_prompt_override=RECON_SYSTEM_PROMPT,
    )
    return result


@router.get("/agent/conversations")
async def list_recon_conversations(
    project_id: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """列出 AI 信息收集对话任务（只返回 [RECON] 前缀的）。"""
    tasks = agent_service.list_tasks(db, limit=limit, project_id=project_id)
    # 只返回 goal 以 [RECON] 开头的信息收集对话
    recon_tasks = [t for t in tasks if (t.goal or "").startswith("[RECON]")]
    return recon_tasks


@router.get("/agent/conversations/{task_id}")
async def get_recon_conversation(task_id: str, db: Session = Depends(get_db)):
    """获取 AI 信息收集对话详情。"""
    task = agent_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="conversation not found")
    return _task_to_dict(task)


@router.delete("/agent/conversations/{task_id}")
async def delete_recon_conversation(task_id: str, db: Session = Depends(get_db)):
    """删除 AI 信息收集对话。"""
    task = agent_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="conversation not found")

    for finding in task.findings:
        db.delete(finding)
    db.delete(task)
    db.commit()
    return {"status": "deleted"}


def _task_to_dict(task: Task) -> dict:
    """将 Task 对象转为字典。"""
    return {
        "id": task.id,
        "project_id": task.project_id,
        "goal": task.goal or "",
        "status": task.status,
        "steps": json.loads(task.steps or "[]"),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "ended_at": task.ended_at.isoformat() if task.ended_at else None,
    }


# ===== 引擎与 API Key 管理 =====


@router.get("/engines")
async def get_engines():
    """获取所有支持的引擎列表及 Key 配置状态。"""
    return {"engines": recon_service.get_engine_list()}


@router.get("/keys")
async def get_keys():
    """获取已配置的 API Keys（脱敏）。"""
    return {"keys": recon_service.get_keys()}


@router.post("/keys")
async def set_key(req: SetKeyRequest):
    """设置指定引擎的 API Key。"""
    if req.engine not in recon_service.ENGINES:
        return {"error": f"不支持的引擎: {req.engine}"}
    recon_service.set_key(req.engine, req.key)
    return {"status": "ok", "engine": req.engine}


@router.delete("/keys/{engine}")
async def delete_key(engine: str):
    """删除指定引擎的 API Key。"""
    recon_service.delete_key(engine)
    return {"status": "ok", "engine": engine}


# ===== 手动搜索 =====


@router.post("/search")
async def search(req: SearchRequest):
    """多引擎并行搜索，结果保存为 Markdown 报告。"""
    if not req.query.strip():
        return {"error": "搜索内容不能为空"}
    if not req.engines:
        return {"error": "请至少选择一个引擎"}

    result = await recon_service.multi_search(req.engines, req.query.strip(), req.size)
    return result


# ===== 报告文件管理 =====


@router.get("/reports")
async def list_reports():
    """列出所有信息收集报告。"""
    return {"reports": recon_service.list_reports()}


@router.get("/reports/{filename}")
async def get_report(filename: str):
    """获取报告内容。"""
    content = recon_service.get_report_content(filename)
    if content is None:
        return {"error": "报告不存在"}
    return {"filename": filename, "content": content}


@router.delete("/reports/{filename}")
async def delete_report(filename: str):
    """删除报告文件。"""
    ok = recon_service.delete_report(filename)
    if not ok:
        return {"error": "报告不存在或删除失败"}
    return {"status": "ok", "filename": filename}
