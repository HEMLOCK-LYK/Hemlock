"""上下文管理优化回归测试（对齐 Claude Code 的上下文策略）。

覆盖：
- 无 compaction 时 _build_chat_messages 原样展开：31 条对话消息逐字节保留，
  工具输出原文不再被热窗口蒸馏（压缩统一由运行循环分界线推进）
- 分界线推进保留最近 10 条原文（keep_recent=10，刚拿到的证据不进摘要）
- session_memory 战术要点提取（Agent 关键结论不因压缩丢失）
- TokenEstimator CJK/ASCII 分段估算（中文消耗高于英文，宁高勿低）
- read_flow_content 分页读取完整流量内容（大响应体不再"想看全看不到"）
- send_request 统一返回结构（truncated/hint/resp_length，Flow 表落库全量）
"""

import asyncio
import json

import pytest

from hemlock.core.context_manager import TokenEstimator
from hemlock.services.agent_service import (
    AgentOrchestrator,
    _advance_compaction_boundary,
    _build_chat_messages,
    _build_session_memory,
    _linear_restore_steps,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """使用临时数据库。"""
    monkeypatch.setattr(
        "hemlock.core.config.settings.db_path",
        str(tmp_path / "test.db"),
    )
    from hemlock.core import database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    database.engine = engine
    database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    database.Base.metadata.create_all(bind=engine)
    db = database.SessionLocal()
    yield db
    db.close()


def _step(step_type: str, data, ts: str = "2026-10-09T00:00:00+00:00") -> dict:
    return {"type": step_type, "data": data, "timestamp": ts}


# ===== 恢复阶段不压缩 =====

def test_build_chat_messages_no_compaction_keeps_all_verbatim(db):
    """无 compaction：恢复阶段不做压缩重算，对话消息逐字节保留。

    15×2 条对话 + 1 个 tool_call 步骤（展开为 assistant(tool_calls) +
    tool(结果) 两条消息）= 32 条。旧版按 4K 热窗口滑动蒸馏窗口外的
    工具输出（历史前缀被改写，缓存命中率归零 + 原文丢失）；新版恢复即原文。
    """
    from hemlock.models.task import Task

    # 模拟 send_request 工具层截断后的典型结果（~4KB，低于微压缩阈值 10000）
    body = "SQL_ERROR_MARKER " + "X" * 4000
    tool_data = {
        "tool": "send_request",
        "args": {"method": "GET", "url": "http://x/?id=1"},
        "result": {"flow_id": "flw_1", "status": 200, "body": body},
        "tool_call_id": "call_1",
        "raw_tool_call": {
            "id": "call_1", "type": "function",
            "function": {"name": "send_request", "arguments": "{}"},
        },
    }
    steps = []
    for i in range(15):
        steps.append(_step("user_message", f"用户消息{i:02d} " + "q" * 180))
        if i == 2:
            steps.append(_step("tool_call", json.dumps(tool_data, ensure_ascii=False)))
        steps.append(_step("assistant_message", f"回复{i:02d} " + "r" * 180))

    task = Task(id="tsk_ctx1", project_id="proj1", steps=json.dumps(steps, ensure_ascii=False))
    db.add(task)
    db.commit()

    messages = _build_chat_messages(steps, task, db)

    conv = [m for m in messages if m.get("role") != "system"]
    assert len(conv) == 32  # 全部消息原样恢复，无压缩（tool_call 步骤拆 2 条）
    tool_msgs = [m for m in conv if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    full = json.dumps(messages, ensure_ascii=False)
    assert body in full  # 工具输出原文逐字节保留（未被蒸馏）
    assert "SQL_ERROR_MARKER" in full


# ===== 分界线推进保留最近原文 =====

def test_advance_boundary_keeps_recent_verbatim(db):
    """分界线推进：最近 10 条原文保留在压缩结果里，旧区进摘要。

    旧版 keep_recent=0 把刚拿到的工具输出也压成摘要，Agent 下一轮
    要用细节只能重新 get_flow_detail；新版对齐 Claude Code compact。
    """
    from hemlock.core.pentest_context import PentestContext
    from hemlock.models.project import Project
    from hemlock.models.task import Task

    project = Project(name="p")
    db.add(project)
    db.commit()
    db.refresh(project)
    task = Task(goal="g", project_id=project.id, status="idle", steps="[]")
    db.add(task)
    db.commit()
    db.refresh(task)

    # 24 条对话消息（12 轮），旧区（前 14 条）足够长（>800 字符）触发压缩
    steps = []
    for i in range(1, 13):
        steps.append(_step("user_message", f"用户消息{i:02d} " + "x" * 100))
        steps.append(_step("assistant_message", f"回复{i:02d} " + "y" * 100))
    task.steps = json.dumps(steps, ensure_ascii=False)
    db.commit()

    messages = _linear_restore_steps(steps)

    # PentestContext 带发现 → session_memory 免费层可用（≥60 字符）
    ctx = PentestContext("http://example.com")
    ctx.record_finding(vuln_type="SQLi", severity="high", title="t", endpoint="/a")

    new_messages, ctype = asyncio.run(_advance_compaction_boundary(
        db, task, steps, messages, ctx, None, threshold=100, mode="test",
    ))

    assert ctype == "session_memory"
    # 追加区整体替换为单个 compaction 步骤并落库
    assert len(steps) == 1 and steps[0]["type"] == "compaction"
    assert json.loads(task.steps)[0]["type"] == "compaction"
    # 固化内容 = [boundary 摘要] + 最近 10 条原文
    comp_msgs = steps[0]["data"]["messages"]
    assert len(comp_msgs) == 11
    assert len(new_messages) == 11  # 无 system 输入 → 摘要 + 原文
    # 最近 10 条原文逐字节保留（刚拿到的证据下一轮直接可用）
    assert "回复12" in new_messages[-1]["content"]
    assert "y" * 100 in new_messages[-1]["content"]
    # 旧区只进摘要，不保留原文（回复01 属于前 14 条旧区）
    assert "回复01" not in json.dumps(new_messages, ensure_ascii=False)
    # 恢复路径与返回消息一致（线性展开 compaction 不重算）
    assert _linear_restore_steps(steps) == new_messages


# ===== session_memory 战术要点 =====

def test_session_memory_extracts_tactical_points():
    """战术要点：assistant 消息中带信号词的行被提取，普通叙述不提取。

    Agent 的关键结论（WAF 特性 / 绕过经验 / 注入确认）不在 PentestContext
    结构化数据里，不提取的话压缩后即丢。
    """
    old_text = "\n".join([
        "[assistant] 目标存在 WAF，union select 被拦截，改用报错注入继续测试 " + "a" * 50,
        "[assistant] 页面返回正常内容没有发现异常 " + "b" * 80,
        "[user] 继续测 admin 接口",
        "[assistant] 发现 id 参数存在数字型注入，已确认可出数据 " + "c" * 60,
    ])
    sm = _build_session_memory(None, old_text)
    assert "近期战术要点" in sm
    assert "报错注入" in sm  # 带"拦截/WAF"信号词 → 提取
    assert "数字型注入" in sm  # 带"发现/确认"信号词 → 提取
    assert "近期用户指令" in sm
    assert "- 继续测 admin 接口" in sm


# ===== TokenEstimator 分段估算 =====

def test_token_estimator_cjk_vs_ascii():
    """CJK 与非 CJK 分段估算：同字符数中文消耗更高（宁高勿低）。"""
    est = TokenEstimator.estimate
    assert est("") == 0
    assert est(None) == 0
    # 纯中文 350 字符：350/1.5 ≈ 233 tokens
    assert est("中" * 350) == 233
    # 纯 ASCII 350 字符：350/3.5 = 100 tokens
    assert est("a" * 350) == 100
    # 同字符数，中文估算必须高于英文（旧版 3.5 一刀切会低估中文）
    assert est("中" * 350) > est("a" * 350)
    # 混合文本介于两者之间
    mixed = "中" * 175 + "a" * 175
    assert est("a" * 350) < est(mixed) < est("中" * 350)


# ===== read_flow_content 分页读取 =====

def test_read_flow_content_pagination(db):
    """分页读取流量完整内容：offset/limit 切片正确，has_more/next_offset 准确。"""
    from hemlock.models.flow import Flow
    from hemlock.models.project import Project
    from hemlock.models.task import Task

    project = Project(name="p")
    db.add(project)
    db.commit()
    db.refresh(project)
    task = Task(goal="g", project_id=project.id, status="idle", steps="[]")
    db.add(task)
    db.commit()
    db.refresh(task)

    agent = AgentOrchestrator(db, task)
    agent.session_id = "test_session"
    agent.context = None

    body = "".join(str(i % 10) for i in range(25000))
    flow = Flow(
        project_id=project.id, method="GET", url="http://x/",
        req_headers="Host: x", req_body="", resp_status=200,
        resp_headers="Content-Type: text/html", resp_body=body, source="agent",
    )
    db.add(flow)
    db.commit()
    db.refresh(flow)

    r1 = agent._tool_read_flow_content({"flow_id": flow.id, "field": "resp_body", "offset": 0, "limit": 10000})
    assert r1["total_length"] == 25000
    assert r1["content"] == body[:10000]
    assert r1["has_more"] is True
    assert r1["next_offset"] == 10000

    r2 = agent._tool_read_flow_content({"flow_id": flow.id, "field": "resp_body", "offset": 10000, "limit": 10000})
    assert r2["content"] == body[10000:20000]
    assert r2["has_more"] is True

    r3 = agent._tool_read_flow_content({"flow_id": flow.id, "field": "resp_body", "offset": 20000, "limit": 10000})
    assert r3["content"] == body[20000:]
    assert r3["has_more"] is False
    assert "next_offset" not in r3

    # 参数校验
    assert "error" in agent._tool_read_flow_content({"flow_id": flow.id, "field": "nope"})
    assert "error" in agent._tool_read_flow_content({"flow_id": "flw_missing"})
    # 空字段
    r_empty = agent._tool_read_flow_content({"flow_id": flow.id, "field": "req_body"})
    assert r_empty["total_length"] == 0 and r_empty["content"] == ""


def test_read_flow_content_schema_registered():
    """read_flow_content 是核心工具（始终注入，不进按需组）。"""
    from hemlock.services.agent_service import AGENT_TOOLS, OPTIONAL_TOOL_NAMES

    names = {t["function"]["name"] for t in AGENT_TOOLS}
    assert "read_flow_content" in names
    assert "read_flow_content" not in OPTIONAL_TOOL_NAMES


# ===== 读取放宽：需要细节时一次读全（用户定调 2026-10-09）=====

def _mk_agent_with_flow(db, body: str):
    """构造 agent + 带 resp_body 的 flow，返回 (agent, flow)。"""
    from hemlock.models.flow import Flow
    from hemlock.models.project import Project
    from hemlock.models.task import Task

    project = Project(name="p")
    db.add(project)
    db.commit()
    db.refresh(project)
    task = Task(goal="g", project_id=project.id, status="idle", steps="[]")
    db.add(task)
    db.commit()
    db.refresh(task)

    agent = AgentOrchestrator(db, task)
    agent.session_id = "test_session"
    agent.context = None

    flow = Flow(
        project_id=project.id, method="GET", url="http://x/api",
        req_headers="Host: x", req_body="", resp_status=200,
        resp_headers="Content-Type: text/html", resp_body=body, source="agent",
    )
    db.add(flow)
    db.commit()
    db.refresh(flow)
    return agent, flow


def test_read_flow_content_read_full_by_default(db):
    """不传 limit 默认一次读全（_FLOW_READ_CHUNK=100K）：50K 字段一页读完。"""
    body = "D" * 50000
    agent, flow = _mk_agent_with_flow(db, body)

    r = agent._tool_read_flow_content({"flow_id": flow.id, "field": "resp_body"})
    assert r["content"] == body  # 全量，无分段
    assert r["has_more"] is False
    assert "next_offset" not in r


def test_read_flow_content_oversized_field_paged(db):
    """超大字段（>100K）默认读前 100K + next_offset 续读；显式 limit 仍生效。"""
    body = "E" * 120000
    agent, flow = _mk_agent_with_flow(db, body)

    r1 = agent._tool_read_flow_content({"flow_id": flow.id, "field": "resp_body"})
    assert len(r1["content"]) == 100000  # _FLOW_READ_CHUNK
    assert r1["has_more"] is True
    assert r1["next_offset"] == 100000

    r2 = agent._tool_read_flow_content({"flow_id": flow.id, "field": "resp_body", "offset": 100000})
    assert r2["content"] == body[100000:]
    assert r2["has_more"] is False

    # 显式 limit 仍生效（分段是可选项，不是被逼的）
    r3 = agent._tool_read_flow_content({"flow_id": flow.id, "field": "resp_body", "limit": 500})
    assert len(r3["content"]) == 500


def test_get_flow_detail_form_view(db):
    """get_flow_detail 表单化：字段长度 + 短预览（800）+ 引导读全。"""
    body = "VULN_PROOF " + "F" * 4900
    agent, flow = _mk_agent_with_flow(db, body)

    r = agent._tool_get_flow_detail({"flow_id": flow.id})
    assert r["resp_status"] == 200
    assert r["field_lengths"]["resp_body"] == len(body)  # 总长度单列
    assert r["resp_body"] == body[:800]  # 预览 800 字符
    assert "read_flow_content" in r["note"]


# ===== list_flows：人眼视角的流量列表（分页/过滤/杂包默认排除）=====

def test_list_flows_pagination_and_filters(db):
    """list_flows：分页翻页、杂包默认排除、search 过滤。"""
    from hemlock.models.flow import Flow
    from hemlock.models.project import Project
    from hemlock.models.task import Task

    project = Project(name="p")
    db.add(project)
    db.commit()
    db.refresh(project)
    task = Task(goal="g", project_id=project.id, status="idle", steps="[]")
    db.add(task)
    db.commit()
    db.refresh(task)

    agent = AgentOrchestrator(db, task)
    agent.session_id = "test_session"
    agent.context = None

    rows = []
    for i in range(25):
        rows.append(Flow(
            project_id=project.id, method="GET", url=f"http://x/item/{i}",
            host="x", resp_status=200, resp_length=100 + i,
            source="proxy",
        ))
    # 杂包与不匹配 search 的流量：不应出现在默认视图
    rows.append(Flow(project_id=project.id, method="GET", url="http://x/heartbeat", host="x", is_noise=True, source="proxy"))
    rows.append(Flow(project_id=project.id, method="GET", url="http://other/y", host="other", source="proxy"))
    db.add_all(rows)
    db.commit()

    r = agent._tool_list_flows({"page": 1, "page_size": 10})
    assert r["total"] == 26  # 仅杂包（is_noise=True）被默认排除；other 域名是合法流量
    assert r["total_pages"] == 3
    assert len(r["flows"]) == 10
    assert r["next_page"] == 2
    assert all(not f["url"].endswith("/heartbeat") for f in r["flows"])

    # host 过滤：只看 x 域名
    r_host = agent._tool_list_flows({"host": "x", "page_size": 100})
    assert r_host["total"] == 25

    r2 = agent._tool_list_flows({"page": 3, "page_size": 10})
    assert len(r2["flows"]) == 6  # 26 - 20 = 6
    assert "next_page" not in r2

    # search 过滤 + 包含杂包
    r3 = agent._tool_list_flows({"search": "heartbeat", "include_noise": True})
    assert r3["total"] == 1
    assert r3["flows"][0]["flow_id"]


def test_list_flows_schema_registered():
    """list_flows 注册为核心工具且对指挥官可读。"""
    from hemlock.services.agent_service import AGENT_TOOLS, COORDINATOR_READONLY_TOOLS, OPTIONAL_TOOL_NAMES

    names = {t["function"]["name"] for t in AGENT_TOOLS}
    assert "list_flows" in names
    assert "list_flows" not in OPTIONAL_TOOL_NAMES
    assert "list_flows" in COORDINATOR_READONLY_TOOLS


# ===== send_request 统一返回结构 =====

def test_persist_flow_and_result_unified(db):
    """统一封装：截断标记 + 完整长度 + 新提示语；Flow 表落库全量。"""
    from hemlock.models.flow import Flow
    from hemlock.models.project import Project
    from hemlock.models.task import Task

    project = Project(name="p")
    db.add(project)
    db.commit()
    db.refresh(project)
    task = Task(goal="g", project_id=project.id, status="idle", steps="[]")
    db.add(task)
    db.commit()
    db.refresh(task)

    agent = AgentOrchestrator(db, task)
    agent.session_id = "test_session"
    agent.context = None

    resp = {
        "status": 200,
        "headers": "Content-Type: text/html",
        "body": "B" * 10000,
        "req_headers": "Host: x",
        "duration_ms": 12,
    }
    result = agent._persist_flow_and_result(
        resp, method="GET", url="http://x/", req_headers="Host: x", req_body="", source="agent",
    )

    assert result["truncated"] is True
    assert len(result["body"]) == 8000
    assert result["resp_length"] == 10000
    assert "read_flow_content" in result["hint"]
    assert result["flow_id"]

    flow = db.query(Flow).filter(Flow.id == result["flow_id"]).first()
    assert flow is not None
    assert flow.resp_body == "B" * 10000  # 落库全量
