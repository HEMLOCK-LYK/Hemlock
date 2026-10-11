# -*- coding: utf-8 -*-
"""登录态一键迁移（第一期）测试：docs/login-migration-design.md。

核心性质：
- 凭据只进 session_manager 内存，任何返回值/存储不回显完整凭据
- 注入优先级写死：Authorization > Cookie > 显式 token 头
- 登录页判定（401/403、30x 跳 login、密码框特征）迁移验证与被动检测共用
- send_session_request 返回值带 session_stale 让 Agent 感知失效
"""
import pytest

from hemlock.services import session_service
from hemlock.services.session_service import (
    _parse_cookie_string,
    looks_like_login_page,
    migrate_flow_auth_to_session,
    parse_auth_from_flow,
    session_manager,
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


@pytest.fixture(autouse=True)
def clean_sessions():
    """每个测试用干净的内存会话池（session_manager 是进程内单例）。"""
    session_manager._sessions.clear()
    yield
    session_manager._sessions.clear()


def _flow(req_headers: str, host: str = "target.local", url: str = "http://target.local/admin", method: str = "GET"):
    """构造带请求头的假 flow（不需要入库——迁移只读属性）。"""
    class FakeFlow:
        pass
    f = FakeFlow()
    f.id = "flw_test1"
    f.host = host
    f.url = url
    f.method = method
    f.scheme = "http"
    f.req_headers = req_headers
    return f


# ===== 解析 =====

def test_parse_auth_from_flow_all_forms():
    f = _flow("Host: target.local\r\nAuthorization: Bearer abc.def.ghi\r\nCookie: SID=xyz; theme=dark\r\nX-Api-Key: k123")
    p = parse_auth_from_flow(f)
    assert p["auth_header"] == "Bearer abc.def.ghi"
    assert p["cookie"] == "SID=xyz; theme=dark"
    assert p["token_headers"] == {"X-Api-Key": "k123"}


def test_parse_auth_from_flow_no_auth():
    f = _flow("Host: target.local\r\nUser-Agent: Mozilla/5.0")
    p = parse_auth_from_flow(f)
    assert not (p["auth_header"] or p["cookie"] or p["token_headers"])


def test_parse_cookie_string_keeps_value_verbatim():
    """Cookie 值逐字节忠实（引号/转义是站点语义，不 unquote）。"""
    assert _parse_cookie_string('SID="a,b"; k2=v2; empty=') == {"SID": '"a,b"', "k2": "v2", "empty": ""}
    assert _parse_cookie_string("") == {}
    assert _parse_cookie_string("garbage-no-equals") == {}


# ===== 迁移注入 =====

def test_migrate_injects_all_forms_into_memory_session():
    f = _flow("Authorization: Bearer tok123\r\nCookie: SID=s1; user=admin")
    r = migrate_flow_auth_to_session(f, "agent_t1")
    assert r["migrated"] is True
    assert r["migrated_from"] == "flow:flw_test1"

    state = session_manager.get_session("agent_t1")
    assert state is not None
    # 注入成功（内存里有完整凭据——这是它们唯一存在的地方）
    assert state.headers["Authorization"] == "Bearer tok123"
    assert state.cookies == {"SID": "s1", "user": "admin"}
    assert state.authenticated is True
    assert state.stale is False
    # 返回值不回显完整凭据（安全边界）
    assert "Bearer tok123" not in str(r)
    assert "SID=s1" not in str(r)


def test_migrate_no_auth_headers():
    r = migrate_flow_auth_to_session(_flow("Host: x"), "agent_t2")
    assert r["migrated"] is False
    assert "未发现认证信息" in r["error"]


def test_migrate_resets_stale():
    """重新迁移时清掉 stale 标记（旧会话失效后重新导入的场景）。"""
    f = _flow("Cookie: SID=new")
    migrate_flow_auth_to_session(f, "agent_t3")
    state = session_manager.get_session("agent_t3")
    state.stale = True
    migrate_flow_auth_to_session(_flow("Cookie: SID=fresh"), "agent_t3")
    assert state.stale is False


# ===== 登录页判定 =====

def test_looks_like_login_page_rules():
    assert looks_like_login_page(401) is True
    assert looks_like_login_page(403) is True
    assert looks_like_login_page(302, location="http://x/login") is True
    assert looks_like_login_page(302, location="http://x/other") is False
    assert looks_like_login_page(200, body_head='<form><input type="password" name="pwd">') is True
    assert looks_like_login_page(200, body_head="<h1>后台管理首页</h1>") is False
    # 含 login 字样的正常页面不误报（密码框才是强特征）
    assert looks_like_login_page(200, body_head="<a href='/login'>退出登录</a>") is False


@pytest.mark.asyncio
async def test_validate_migrated_session_marks_stale(monkeypatch):
    """主动验证：被踢回登录页 → valid=False 且会话标记 stale。"""
    async def fake_send(session_id, method, url, headers_str="", body="", follow_redirects=True, use_proxy=False):
        return {"status": 302, "headers": "Location: http://target.local/login", "body": ""}
    monkeypatch.setattr(session_service, "send_request_with_session", fake_send)

    migrate_flow_auth_to_session(_flow("Cookie: SID=s"), "agent_t4")
    r = await session_service.validate_migrated_session("agent_t4", "http://target.local/admin")
    assert r["valid"] is False
    assert session_manager.get_session("agent_t4").stale is True


@pytest.mark.asyncio
async def test_validate_migrated_session_ok(monkeypatch):
    async def fake_send(session_id, method, url, **kw):
        return {"status": 200, "headers": "Content-Type: text/html", "body": "<h1>Dashboard</h1>"}
    monkeypatch.setattr(session_service, "send_request_with_session", fake_send)

    migrate_flow_auth_to_session(_flow("Cookie: SID=s"), "agent_t5")
    r = await session_service.validate_migrated_session("agent_t5", "http://target.local/admin")
    assert r["valid"] is True
    assert "200" in r["reason"]


# ===== Agent 感知（send_session_request 被动 stale）=====

@pytest.mark.asyncio
async def test_send_session_request_reports_stale(db, monkeypatch):
    """send_session_request 响应像登录页时：会话标 stale + 返回 session_stale=true。"""
    from hemlock.models.project import Project
    from hemlock.models.task import Task
    from hemlock.services.agent_service import AgentOrchestrator

    project = Project(name="p")
    db.add(project)
    db.commit()
    db.refresh(project)
    task = Task(goal="g", project_id=project.id, status="idle", steps="[]")
    db.add(task)
    db.commit()
    db.refresh(task)
    agent = AgentOrchestrator(db, task)
    agent.session_id = "agent_stale_test"
    agent.context = None

    async def fake_send(session_id, method, url, headers_str="", body="", follow_redirects=True, use_proxy=False):
        return {
            "status": 200,
            "headers": "Content-Type: text/html",
            "body": "<form><input type='password' name='pass'>登录</form>",
            "flow_id": "",
        }
    monkeypatch.setattr("hemlock.services.agent_service.send_request_with_session", fake_send)

    # 先造一个已迁移会话，随后被踢回登录页
    migrate_flow_auth_to_session(_flow("Cookie: SID=old"), agent.session_id)
    result = await agent._tool_send_session_request({"url": "http://target.local/admin"})

    assert result.get("session_stale") is True
    assert "stale_hint" in result
    assert session_manager.get_session(agent.session_id).stale is True


@pytest.mark.asyncio
async def test_send_session_request_normal_no_stale(db, monkeypatch):
    """正常响应不误标 stale。"""
    from hemlock.models.project import Project
    from hemlock.models.task import Task
    from hemlock.services.agent_service import AgentOrchestrator

    project = Project(name="p")
    db.add(project)
    db.commit()
    db.refresh(project)
    task = Task(goal="g", project_id=project.id, status="idle", steps="[]")
    db.add(task)
    db.commit()
    db.refresh(task)
    agent = AgentOrchestrator(db, task)
    agent.session_id = "agent_stale_ok"
    agent.context = None

    async def fake_send(session_id, method, url, **kw):
        return {"status": 200, "headers": "Content-Type: application/json", "body": '{"ok": true}', "flow_id": ""}
    monkeypatch.setattr("hemlock.services.agent_service.send_request_with_session", fake_send)

    migrate_flow_auth_to_session(_flow("Cookie: SID=s"), agent.session_id)
    result = await agent._tool_send_session_request({"url": "http://target.local/api/list"})
    assert "session_stale" not in result
    assert session_manager.get_session(agent.session_id).stale is False


# ===== Agent 工具 migrate_flow_auth（定位→迁移→探测的 Agent 侧闭环）=====

def _agent_env(db):
    from hemlock.models.project import Project
    from hemlock.models.task import Task
    from hemlock.services.agent_service import AgentOrchestrator

    project = Project(name="mig-project")
    db.add(project)
    db.commit()
    db.refresh(project)
    task = Task(goal="迁移测试", project_id=project.id, status="idle", steps="[]")
    db.add(task)
    db.commit()
    db.refresh(task)
    agent = AgentOrchestrator(db, task)
    agent.session_id = "sess_mig"
    agent.context = None
    return agent, project


def _db_flow(db, project_id, req_headers: str):
    from hemlock.models.flow import Flow
    flow = Flow(
        project_id=project_id, method="GET", url="http://target.local/admin",
        host="target.local", req_headers=req_headers, source="proxy",
    )
    db.add(flow)
    db.commit()
    db.refresh(flow)
    return flow


async def test_agent_migrate_tool_success(db, monkeypatch):
    agent, project = _agent_env(db)
    flow = _db_flow(db, project.id, "Host: target.local\r\nCookie: SID=xyz; theme=dark")

    async def fake_validate(session_id, probe_url):
        assert session_id == "sess_mig"
        assert probe_url == "http://target.local/admin"
        return {"valid": True, "status": 200, "reason": "200"}
    monkeypatch.setattr(session_service, "validate_migrated_session", fake_validate)

    r = await agent._tool_migrate_flow_auth({"flow_id": flow.id})
    assert r["migrated"] is True
    assert r["probe"]["valid"] is True
    assert "session_stale" not in r
    assert "SID=xyz" not in str(r)  # 返回值不回显凭据


async def test_agent_migrate_tool_stale_marks_session(db, monkeypatch):
    """探测发现被踢回登录页：session_stale 标记透传给 Agent。"""
    agent, project = _agent_env(db)
    flow = _db_flow(db, project.id, "Host: target.local\r\nCookie: SID=xyz")

    async def fake_validate(session_id, probe_url):
        # 真 validate_migrated_session 会在判定 stale 时标记 session（副作用照做）
        state = session_manager.get_session(session_id)
        if state:
            state.stale = True
        return {"valid": False, "status": 302, "reason": "302 跳转登录页"}
    monkeypatch.setattr(session_service, "validate_migrated_session", fake_validate)

    r = await agent._tool_migrate_flow_auth({"flow_id": flow.id})
    assert r["session_stale"] is True
    assert session_manager.get_session("sess_mig").stale is True


async def test_agent_migrate_tool_flow_not_found(db):
    agent, _ = _agent_env(db)
    r = await agent._tool_migrate_flow_auth({"flow_id": "flw_missing"})
    assert r.get("error") == "flow not found"


async def test_agent_migrate_tool_no_auth_headers(db):
    agent, project = _agent_env(db)
    flow = _db_flow(db, project.id, "Host: target.local\r\nUser-Agent: Mozilla/5.0")
    r = await agent._tool_migrate_flow_auth({"flow_id": flow.id})
    assert r.get("migrated") is False
    assert "error" in r and "hint" in r


def test_migrate_tool_registered():
    from hemlock.services.agent_service import AGENT_TOOLS, COORDINATOR_READONLY_TOOLS, OPTIONAL_TOOL_NAMES

    names = {t["function"]["name"] for t in AGENT_TOOLS}
    assert "migrate_flow_auth" in names
    assert "migrate_flow_auth" not in OPTIONAL_TOOL_NAMES
    assert "migrate_flow_auth" not in COORDINATOR_READONLY_TOOLS  # 写会话状态，不进指挥官只读集
