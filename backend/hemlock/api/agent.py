"""Agent 任务 & 漏洞管理 API 路由。"""

import json
import asyncio
from datetime import datetime, timezone
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import PlainTextResponse, HTMLResponse
from sqlalchemy.orm import Session

from hemlock.core.database import get_db
from hemlock.schemas.agent import (
    CreateTaskRequest,
    TaskResponse,
    TaskSummary,
    FindingResponse,
    FindingDetailResponse,
    EvidenceResponse,
    UpdateFindingStatusRequest,
    RetestResponse,
    ReportRequest,
    StrategyResponse,
    PresetResponse,
    SessionInfoResponse,
    StepData,
)
from hemlock.services import agent_service
from hemlock.services.retest_service import retest_finding
from hemlock.services.report_service import generate_report
from hemlock.services.session_service import session_manager, get_session_info
from hemlock.core.pentest_strategies import get_all_strategies, get_all_presets, build_goal_from_preset

router = APIRouter(tags=["agent"])


# ===== Agent 任务 =====

@router.post("/agent/tasks", response_model=TaskResponse)
async def create_task(req: CreateTaskRequest, db: Session = Depends(get_db)):
    """创建并启动 Agent 任务。"""
    # 应用策略预设：将 preset + user goal 组合为完整 goal
    full_goal = build_goal_from_preset(req.preset, req.goal)
    if not full_goal:
        raise HTTPException(status_code=400, detail="goal 和 preset 不能同时为空")

    task = agent_service.create_task(db, goal=full_goal, project_id=req.project_id)

    asyncio.create_task(
        agent_service.run_agent_task(
            task_id=task.id,
            goal=full_goal,
            target_url=req.target_url,
        )
    )

    return _task_to_response(task)


@router.get("/agent/tasks", response_model=list[TaskSummary])
async def list_tasks(
    limit: int = 50,
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    """列出所有 Agent 任务，支持按 project_id 过滤。"""
    tasks = agent_service.list_tasks(db, limit=limit, project_id=project_id)
    return tasks


@router.get("/agent/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, db: Session = Depends(get_db)):
    """获取任务详情（含执行步骤）。"""
    task = agent_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_to_response(task)


@router.post("/agent/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消运行中的 Agent 任务。"""
    success = agent_service.cancel_agent_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="task not running or not found")
    return {"status": "cancelled"}


@router.post("/agent/tasks/{task_id}/resume")
async def resume_task(task_id: str, db: Session = Depends(get_db)):
    """恢复中断的 Agent 任务（从 checkpoint 续跑）。

    仅可恢复 failed/paused 状态的任务；运行时每 3 步保存的
    task.messages（对话历史）+ task.checkpoint（PentestContext 快照）
    会被自动加载，Agent 从上次进度继续，而非从头开始。
    """
    task = agent_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status not in ("failed", "paused"):
        raise HTTPException(status_code=400, detail="仅可恢复 failed/paused 状态的任务")

    # 从 checkpoint 提取 target_url（恢复时 run() 会加载 PentestContext 快照）
    target_url = ""
    try:
        ckpt = json.loads(task.checkpoint or "{}")
        pc = ckpt.get("pentest_context") or {}
        target_url = pc.get("target_url", "")
    except Exception:
        pass

    # 重置状态并后台续跑
    task.status = "running"
    task.ended_at = None
    db.commit()

    asyncio.create_task(
        agent_service.run_agent_task(
            task_id=task.id,
            goal=task.goal or "",
            target_url=target_url,
        )
    )
    return {"status": "resumed", "task_id": task.id}


@router.post("/agent/tasks/{task_id}/approve")
async def approve_task(task_id: str):
    """批准 Agent 的敏感操作请求。"""
    success = agent_service.approve_agent_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="task not waiting for confirmation")
    return {"status": "approved"}


@router.post("/agent/tasks/{task_id}/reject")
async def reject_task(task_id: str):
    """拒绝 Agent 的敏感操作请求。"""
    success = agent_service.reject_agent_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="task not waiting for confirmation")
    return {"status": "rejected"}


# ===== 对话式 Agent（Codex 风格）=====

class ChatRequest(BaseModel):
    """对话式 Agent 请求。"""
    task_id: str
    message: str
    provider_id: str | None = None  # 指定使用的 LLM Provider，为 None 时用默认


class ChatResponse(BaseModel):
    """对话式 Agent 响应。"""
    reply: str
    tool_calls: list[dict]


@router.post("/agent/chat", response_model=ChatResponse)
async def agent_chat(req: ChatRequest, db: Session = Depends(get_db)):
    """对话式 Agent 交互：用户发消息 -> AI 回复（可调用工具）-> 工具结果展示 -> 等待下一条消息。

    可通过 provider_id 指定使用的 LLM Provider（API Key），为 None 时使用默认 Provider。
    """
    # try/finally 保证终止事件必然补发（对齐主流 Agent 的终止机制）：
    # chat_with_agent 内部只发 chat_message、不发 task_complete，且完成后任务状态为
    # idle（非 done/failed），前端运行状态清理依赖这两个信号。
    # 若前端在请求期间切换页面（WS 断开 / handleSend finally 被卸载拦截），
    # 或 chat_with_agent 中途抛异常，补发的 task_complete 是前端解锁输入框的唯一依据。
    try:
        result = await agent_service.chat_with_agent(
            req.task_id, req.message, db, provider_id=req.provider_id
        )
        return result
    finally:
        try:
            task = agent_service.get_task(db, req.task_id)
            if task:
                from hemlock.services.event_bus import event_bus
                event_bus.publish("agent", {
                    "event": "task_complete" if task.status in ("done", "idle") else "task_error",
                    "task_id": req.task_id,
                    "status": task.status,
                })
        except Exception:
            pass


class InjectRequest(BaseModel):
    """对话任务运行中插入消息请求。"""
    task_id: str
    message: str


@router.post("/agent/inject")
async def agent_inject(req: InjectRequest):
    """任务运行中插入消息：非阻塞入队，下一思考边界即被 Agent 识别。

    对齐 Claude Code 的中途补充消息行为：不打断当前 LLM 思考/工具执行，
    由 chat_with_agent 在下一个循环边界 drain 并随下次 LLM 调用一起识别。

    - 有活跃对话循环：入队成功，返回 {"queued": true}
    - 无活跃循环（任务 idle 或非对话模式）：返回 {"queued": false}，
      前端应回退为正常的阻塞式对话发送
    """
    from hemlock.services.agent_service import _active_agents, _active_subagent_inboxes
    tracker = _active_agents.get(req.task_id)
    if tracker is not None and getattr(tracker, "_chat_active", False):
        # 广播语义：同一句用户消息同时进主 Agent inbox 与当前活跃子 Agent inbox。
        # 主 Agent 下一边界识别；子 Agent（若有）在其自身循环顶部 drain 并调整方向。
        tracker.enqueue_user_message(req.message)
        sub_inbox = _active_subagent_inboxes.get(req.task_id)
        if sub_inbox is not None:
            try:
                sub_inbox.put_nowait(req.message)
            except asyncio.QueueFull:
                pass  # 队列满则不阻塞（子 Agent 循环下一边界仍会读到已有消息）
        return {"queued": True, "task_id": req.task_id}
    return {"queued": False, "task_id": req.task_id, "reason": "not_running"}


class CreateChatTaskRequest(BaseModel):
    """创建对话式 Agent 任务。"""
    project_id: str | None = None
    goal: str = ""


@router.post("/agent/conversations", response_model=TaskResponse)
async def create_conversation(req: CreateChatTaskRequest, db: Session = Depends(get_db)):
    """创建一个新的对话式 Agent 任务（不自动启动，等待用户发消息）。

    创建 task，状态为 running（表示对话进行中），但不调用 run_agent_task；
    steps 初始化为包含一条 system 消息说明任务目标。
    """
    goal = (req.goal or "").strip()
    task = agent_service.create_task(db, goal=goal, project_id=req.project_id)

    # 初始化 steps：包含一条 system 消息说明任务目标
    initial_steps = [{
        "type": "system_message",
        "data": f"测试任务已创建。任务目标：{goal}" if goal else "测试任务已创建。请发送指令开始安全测试。",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]
    task.steps = json.dumps(initial_steps, ensure_ascii=False, default=str)
    db.commit()
    db.refresh(task)

    return _task_to_response(task)


@router.delete("/agent/conversations/{task_id}")
async def delete_conversation(task_id: str, db: Session = Depends(get_db)):
    """删除对话任务及其关联数据（steps、findings）。"""
    task = agent_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="conversation not found")

    # 删除关联的 findings
    for finding in task.findings:
        db.delete(finding)

    db.delete(task)
    db.commit()
    return {"status": "deleted"}


# ===== 漏洞管理 =====

@router.get("/findings", response_model=list[FindingResponse])
async def list_findings(
    project_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """查询漏洞列表。"""
    return agent_service.list_findings(db, project_id=project_id, status=status)


@router.get("/findings/{finding_id}", response_model=FindingDetailResponse)
async def get_finding(finding_id: str, db: Session = Depends(get_db)):
    """获取漏洞详情（含证据链）。"""
    finding = agent_service.get_finding(db, finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="finding not found")

    evidence = None
    if finding.evidence_id:
        ev = agent_service.get_evidence(db, finding.evidence_id)
        if ev:
            evidence = EvidenceResponse.model_validate(ev)

    return FindingDetailResponse(
        id=finding.id,
        project_id=finding.project_id,
        vuln_type=finding.vuln_type,
        severity=finding.severity,
        status=finding.status,
        title=finding.title,
        detail=finding.detail,
        evidence_id=finding.evidence_id,
        task_id=finding.task_id,
        created_at=finding.created_at,
        updated_at=finding.updated_at,
        evidence=evidence,
    )


@router.patch("/findings/{finding_id}/status", response_model=FindingResponse)
async def update_finding_status(finding_id: str, req: UpdateFindingStatusRequest, db: Session = Depends(get_db)):
    """更新漏洞状态。"""
    valid_statuses = {"pending", "confirmed", "false_positive", "closed"}
    if req.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"invalid status, must be one of {valid_statuses}")

    finding = agent_service.update_finding_status(db, finding_id, req.status)
    if not finding:
        raise HTTPException(status_code=404, detail="finding not found")
    return finding


# ===== 漏洞复测 =====

@router.post("/findings/{finding_id}/retest", response_model=RetestResponse)
async def retest_finding_api(finding_id: str, db: Session = Depends(get_db)):
    """复测漏洞：重放证据请求，AI 验证漏洞是否已修复。"""
    result = await retest_finding(db, finding_id)
    if "error" in result:
        return RetestResponse(
            finding_id=finding_id,
            retest_status="error",
            error=result["error"],
        )
    return RetestResponse(**result)


# ===== 证据 =====

@router.get("/evidence/{evidence_id}", response_model=EvidenceResponse)
async def get_evidence(evidence_id: str, db: Session = Depends(get_db)):
    """获取证据包。"""
    ev = agent_service.get_evidence(db, evidence_id)
    if not ev:
        raise HTTPException(status_code=404, detail="evidence not found")
    return ev


# ===== 渗透测试策略 =====

@router.get("/strategies", response_model=list[StrategyResponse])
async def list_strategies():
    """列出所有渗透测试策略。"""
    strategies = get_all_strategies()
    return [
        StrategyResponse(
            name=s.name,
            vuln_type=s.vuln_type,
            owasp_category=s.owasp_category,
            description=s.description,
            recon_steps=s.recon_steps,
            test_methods=s.test_methods,
            verification=s.verification,
            common_payloads=s.common_payloads,
        )
        for s in strategies
    ]


# ===== Payload 库 =====

@router.get("/agent/presets", response_model=list[PresetResponse])
async def list_presets():
    """列出所有测试策略预设。用户创建任务时可选择预设来指导 Agent 测试方向。"""
    return get_all_presets()


@router.get("/agent/skills")
async def list_agent_skills():
    """列出所有可用技能（Agent 工具调用入口）。

    修复 P11：Agent 的 call_api 工具可能调用 /agent/skills，
    此端点委托到 skills 路由的同名功能，避免 404。
    """
    from hemlock.core.skill_registry import get_enabled_skills
    skills = get_enabled_skills()
    return {"skills": skills}


@router.get("/payloads/scenes")
async def list_payload_scenes():
    """列出所有可用的 payload 场景。"""
    from hemlock.core.payload_library import list_scenes
    return {"scenes": list_scenes()}


@router.get("/payloads")
async def get_payloads_api(
    scene: str,
    level: str | None = None,
    count: int = 20,
):
    """获取 payload 列表。

    Args:
        scene: 场景 sqli/xss/cmd_injection/path_traversal/ssrf/weak_password/open_redirect
        level: 等级 basic/standard/bypass（可选）
        count: 返回数量
    """
    from hemlock.core.payload_library import get_payloads as _get_payloads
    payloads = _get_payloads(scene=scene, level=level, count=count)
    return {"scene": scene, "count": len(payloads), "payloads": payloads}


# ===== 会话管理 =====

@router.get("/sessions/{session_id}", response_model=SessionInfoResponse)
async def get_session(session_id: str):
    """获取 Agent 会话信息。"""
    info = get_session_info(session_id)
    if not info:
        raise HTTPException(status_code=404, detail="session not found")
    return SessionInfoResponse(**info)


@router.get("/sessions")
async def list_sessions():
    """列出所有活跃会话。"""
    return session_manager.list_sessions()


# ===== 报告生成 =====

@router.post("/reports")
async def generate_report_api(req: ReportRequest, db: Session = Depends(get_db)):
    """生成渗透测试报告。

    支持 html/markdown/json 三种格式。
    HTML 格式直接返回可查看的页面，其他格式返回纯文本。
    """
    report = generate_report(db, project_id=req.project_id, task_id=req.task_id, fmt=req.fmt)

    if req.fmt == "html":
        return HTMLResponse(content=report)
    elif req.fmt == "json":
        return PlainTextResponse(content=report, media_type="application/json")
    else:
        return PlainTextResponse(content=report, media_type="text/markdown")


@router.get("/reports")
async def generate_report_get(
    project_id: str | None = None,
    task_id: str | None = None,
    fmt: str = "html",
    db: Session = Depends(get_db),
):
    """GET 方式生成报告（便于浏览器直接访问）。"""
    report = generate_report(db, project_id=project_id, task_id=task_id, fmt=fmt)

    if fmt == "html":
        return HTMLResponse(content=report)
    elif fmt == "json":
        return PlainTextResponse(content=report, media_type="application/json")
    else:
        return PlainTextResponse(content=report, media_type="text/markdown")


# ===== 统计信息 =====

@router.get("/agent/stats")
async def get_agent_stats():
    """获取 Agent 运行统计：Token 用量、活跃任务数等。"""
    from hemlock.core.llm_client import get_token_usage
    from hemlock.services.agent_service import _active_agents

    return {
        "token_usage": get_token_usage(),
        "active_agents": len(_active_agents),
        "active_task_ids": list(_active_agents.keys()),
    }


# ===== 辅助函数 =====

def _task_to_response(task) -> TaskResponse:
    """将 Task 模型转换为响应，解析 steps JSON。"""
    steps_data = []
    try:
        raw_steps = json.loads(task.steps or "[]")
        for s in raw_steps:
            steps_data.append(StepData(
                type=s.get("type", "unknown"),
                data=s.get("data", ""),
                timestamp=s.get("timestamp", ""),
            ))
    except (json.JSONDecodeError, TypeError):
        pass

    return TaskResponse(
        id=task.id,
        goal=task.goal,
        status=task.status,
        steps=steps_data,
        started_at=task.started_at,
        ended_at=task.ended_at,
    )
