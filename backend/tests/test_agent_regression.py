"""Agent 回归测试：锁定 round4 报告涉及的 BUG 修复。

覆盖：
- BUG-01: send_* 工具 Flow() 构造（enrich_flow_fields 含 is_noise 不再冲突）
- BUG-02: 悬挂任务运行时清理（cleanup_zombie_tasks）
- BUG-04: /request/send dict headers 兼容
- BUG-07: auto_verify 规则验证始终执行（llm_assisted_verify_enabled=false 也跑规则）
"""

import asyncio
import os
import threading
import http.server

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """创建测试客户端，使用临时数据库。"""
    monkeypatch.setenv("HEMLOCK_DB_PATH", str(tmp_path / "test.db"))

    from hemlock.core import config, database
    import importlib
    importlib.reload(config)
    monkeypatch.setattr("hemlock.core.config.settings.db_path", str(tmp_path / "test.db"))

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    database.engine = engine
    database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    database.Base.metadata.create_all(bind=engine)

    from hemlock.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def local_target():
    """本地目标服务器（固定响应体，供 send 工具测试）。"""
    body = "<html><title>target</title>hello</html>"

    class Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            self._reply()

        def do_POST(self):
            self._reply()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


# ===== BUG-01: send_* 工具 Flow() 构造 =====

def test_send_request_tool_works(tmp_path, monkeypatch, local_target):
    """BUG-01：send_request 工具不再抛 Flow() is_noise TypeError，且 DB 落库完整。"""
    monkeypatch.setenv("HEMLOCK_DB_PATH", str(tmp_path / "test.db"))
    from hemlock.core import config, database
    import importlib
    importlib.reload(config)
    monkeypatch.setattr("hemlock.core.config.settings.db_path", str(tmp_path / "test.db"))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    database.engine = engine
    database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    import hemlock.models  # noqa: F401
    database.Base.metadata.create_all(bind=engine)

    from hemlock.core.database import SessionLocal
    from hemlock.models.project import Project
    from hemlock.models.task import Task
    from hemlock.models.flow import Flow
    from hemlock.services.agent_service import AgentOrchestrator

    db = SessionLocal()
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

    async def run():
        result = await agent._execute_tool(
            "send_request",
            {"method": "GET", "url": f"{local_target}/x", "headers": "X-T: 1", "body": ""},
        )
        return result

    result = asyncio.run(run())

    assert "flow_id" in result, f"send_request 失败: {result}"
    flow = db.query(Flow).filter(Flow.id == result["flow_id"]).first()
    assert flow is not None
    assert flow.resp_body == "<html><title>target</title>hello</html>"
    db.close()


# ===== BUG-04: /request/send dict headers =====

def test_request_send_dict_headers(client, local_target):
    """BUG-04：headers 传 dict 应返回 200 而非 422。"""
    project = client.post("/api/projects", json={"name": "p"}).json()
    resp = client.post(
        "/api/request/send",
        json={
            "method": "GET",
            "url": f"{local_target}/dict",
            "headers": {"X-Test": "abc", "Content-Type": "text/plain"},
            "project_id": project["id"],
        },
    )
    assert resp.status_code == 200, resp.json()
    assert "flow_id" in resp.json()


# ===== BUG-02: 悬挂任务清理 =====

def test_cleanup_zombie_tasks(tmp_path, monkeypatch):
    """BUG-02：running/paused 但无活跃编排器的任务被标记为 failed。"""
    monkeypatch.setenv("HEMLOCK_DB_PATH", str(tmp_path / "test.db"))
    from hemlock.core import config, database
    import importlib
    importlib.reload(config)
    monkeypatch.setattr("hemlock.core.config.settings.db_path", str(tmp_path / "test.db"))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    database.engine = engine
    database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    import hemlock.models  # noqa: F401
    database.Base.metadata.create_all(bind=engine)

    from hemlock.core.database import SessionLocal
    from hemlock.models.task import Task, TASK_STATUS_RUNNING, TASK_STATUS_FAILED
    from hemlock.services import agent_service

    # agent_service 在模块加载时捕获了 SessionLocal 引用，需同步指向测试库
    monkeypatch.setattr(agent_service, "SessionLocal", SessionLocal)

    db = SessionLocal()
    zombie = Task(goal="z", status=TASK_STATUS_RUNNING, steps="[]")  # 无活跃编排器
    db.add(zombie)
    db.commit()
    db.refresh(zombie)
    zombie_id = zombie.id
    db.close()

    cleaned = agent_service.cleanup_zombie_tasks()
    assert cleaned >= 1

    db = SessionLocal()
    task = db.query(Task).filter(Task.id == zombie_id).first()
    assert task.status == TASK_STATUS_FAILED
    db.close()


# ===== BUG-07: auto_verify 规则始终执行 =====

def test_auto_verify_runs_rules_when_llm_assist_disabled(tmp_path, monkeypatch):
    """BUG-07：llm_assisted_verify_enabled=false 时规则验证仍执行，不返回 ai_judgment。"""
    monkeypatch.setenv("HEMLOCK_DB_PATH", str(tmp_path / "test.db"))
    from hemlock.core import config
    import importlib
    importlib.reload(config)
    monkeypatch.setattr("hemlock.core.config.settings.llm_assisted_verify_enabled", False)

    from hemlock.core.vuln_verifier import auto_verify

    async def run():
        result = await auto_verify(
            vuln_type="XSS",
            method="GET",
            url="http://x.test/?q=1",
            param_name="q",
            param_value="<script>alert(1)</script>",
            resp_body="<html><script>alert(1)</script></html>",
        )
        return result

    result = asyncio.run(run())
    # 规则验证执行：XSS 反射命中
    assert result.verified is True
    assert result.method == "xss_reflection"
    assert result.method != "ai_judgment"


# ===== 上下文管理：缓存友好 + 微压缩 =====

def test_deterministic_boundary_compact_shape(tmp_path, monkeypatch):
    """确定性边界压缩：system 原样保留 + 单条 boundary + 最近原文，不改写历史消息。"""
    from hemlock.services.agent_service import _deterministic_boundary_compact

    base = "稳定系统提示"
    old_msgs = [
        {"role": "user", "content": f"历史消息 {i} " + "x" * 300}
        for i in range(20)
    ]
    recent_msgs = [
        {"role": "user", "content": "最近的问题"},
        {"role": "assistant", "content": "最近的回答"},
    ]
    messages = [{"role": "system", "content": base}, *old_msgs, *recent_msgs]

    result, ctype = _deterministic_boundary_compact(messages, keep_recent=4)
    assert ctype == "deterministic"
    # 结构：system 原样 + 1 条 boundary + keep_recent 条原文
    assert result[0]["content"] == base  # system 逐字节不变
    assert len(result) == 1 + 1 + 4
    assert "[上下文压缩]" in result[1]["content"]
    # 最近原文逐字节保留（不蒸馏改写）
    assert result[-2:] == recent_msgs


def test_micro_compact_preserves_flow_id(tmp_path, monkeypatch):
    """微压缩：工具级截断的结果透传保留 flow_id；超大结果才蒸馏。"""
    from hemlock.services.agent_service import _tool_result_to_llm_content

    # ~8KB 级别结果（body 预览 _FLOW_BODY_PREVIEW=8000 截断后）→ 透传，保留 flow_id 与 body 预览
    medium = {
        "flow_id": "flw_regression",
        "status": 200,
        "body": "X" * 8000,
        "headers": "Content-Type: text/html",
        "url": "http://x/a",
    }
    out = _tool_result_to_llm_content("send_request", {"method": "GET"}, medium)
    assert "flw_regression" in out
    assert "XXX" in out  # body 预览透传

    # 超大 call_api 响应 → 蒸馏（长度大幅下降）
    huge = {"status_code": 200, "data": {"items": [{"k": "v" * 100} for _ in range(300)]}}
    out2 = _tool_result_to_llm_content("call_api", {"method": "GET"}, huge)
    assert len(out2) < len(str(huge))
    assert "200" in out2


# ===== 发现工具：analyze_js / fuzz_paths =====

def test_analyze_js_extracts_endpoints(tmp_path, monkeypatch, local_target):
    """analyze_js：从 JS 中提取真实接口，过滤 CSS/图片噪音。"""
    from hemlock.services.discovery_service import analyze_js_page, extract_js_endpoints

    js = '''
    const base = "/api/v1";
    fetch("/api/users?id=1", {method: "GET"});
    axios.post("/api/login", {user, pass});
    let u = { url: "/admin/users" };
    import "./style.css";
    let img = "/logo.png";
    '''
    eps = extract_js_endpoints(js, "http://example.com/", "example.com")
    paths = {e["endpoint"] for e in eps}
    assert "/api/users?id=1" in paths
    assert "/api/login" in paths
    assert "/admin/users" in paths
    assert all("css" not in e and "png" not in e for e in paths)  # 噪音被过滤


def test_fuzz_paths_reports_hits(tmp_path, monkeypatch):
    """fuzz_paths：报告非 404 命中，过滤 404。"""
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/admin"):
                body = b"<html>FOUND</html>"
                self.send_response(200)
            else:
                body = b"not found"
                self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    from hemlock.services.discovery_service import fuzz_paths

    async def run():
        return await fuzz_paths(base, paths=["/", "/admin", "/definitely-not-exist-xyz"])

    result = asyncio.run(run())
    found_paths = [f["path"] for f in result["findings"]]
    assert "/" in found_paths
    assert "/admin" in found_paths
    assert "/definitely-not-exist-xyz" not in found_paths  # 404 被过滤
    srv.shutdown()
