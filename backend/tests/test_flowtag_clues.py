"""FlowTag 检测化回归测试。

覆盖：
- FlowTagRule severity/clue_type 字段（向后兼容：缺省 info）
- match_detailed：标签行为与旧版一致，检测规则命中额外产出结构化线索
- match() 兼容：返回两元组不变
- collect_tag_clues：从 tags 非空流量实时推导线索（含证据片段、等级过滤、去重）
- _tool_list_tag_clues：Agent 工具集成（项目隔离、summary 汇总）
- load_rules 合并：老用户规则文件缺 severity 字段时从内置规则补齐
"""

import json

import pytest

from hemlock.services import flowtag_engine as fe_mod
from hemlock.services.flowtag_engine import FlowTagRule, FlowTagEngine


def _make_engine(rules: list[FlowTagRule]) -> FlowTagEngine:
    """构造已加载指定规则的引擎实例（绕过文件加载，测试隔离）。"""
    engine = FlowTagEngine()
    engine._rules = rules
    engine._loaded = True
    return engine


def _shiro_rule(**kwargs) -> FlowTagRule:
    return FlowTagRule(
        name="Shiro", f_regex="(=deleteMe|rememberMe=)", color="green",
        scope="any header", severity="high", clue_type="组件攻击面", **kwargs,
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


# ===== 规则字段 =====

def test_rule_severity_fields():
    """severity/clue_type 显式传入生效；非法值与缺省回退 info。"""
    rule = _shiro_rule()
    assert rule.severity == "high" and rule.clue_type == "组件攻击面"

    legacy = FlowTagRule(name="Nginx", f_regex="(nginx/[\\d.]+)")
    assert legacy.severity == "info" and legacy.clue_type == ""

    invalid = FlowTagRule(name="X", f_regex="(x)", severity="critical")
    assert invalid.severity == "info"  # 非法等级回退

    assert _shiro_rule().to_dict()["severity"] == "high"
    assert "severity" in FlowTagRule.from_dict({"name": "y", "f_regex": "y"}).to_dict()


# ===== 匹配与线索生成 =====

def test_match_detailed_returns_clues():
    """检测规则命中产出线索（含证据片段）；info 规则只标注不出线索。"""
    engine = _make_engine([
        _shiro_rule(),
        FlowTagRule(name="Tomcat", f_regex="(Apache Tomcat)", color="orange",
                    scope="response body", severity="info"),
    ])
    req = "Cookie: rememberMe=deleteMe\r\nHost: x"
    resp = "<title>Apache Tomcat</title>"

    # 参数顺序：(req_headers, req_body, resp_headers, resp_body)
    tags, colors, clues = engine.match_detailed(req, "", "", resp)

    assert "Shiro" in tags and "Tomcat" in tags
    assert len(clues) == 1  # Tomcat 是 info，不出线索
    clue = clues[0]
    assert clue["rule_name"] == "Shiro"
    assert clue["severity"] == "high"
    assert clue["clue_type"] == "组件攻击面"
    assert clue["evidence"] == "rememberMe="  # 正则交替分支捕获的片段
    assert clue["scope"] == "any header"


def test_match_backward_compat():
    """match() 保持两元组返回（现有调用点不受影响）。"""
    engine = _make_engine([_shiro_rule()])
    result = engine.match("Cookie: rememberMe=1", "", "", "")
    assert isinstance(result, tuple) and len(result) == 2
    assert result[0] == "Shiro"
    assert result[1] == "green"


def test_match_tag_count_suffix_consistent():
    """多次命中带计数后缀，线索按基名归类。"""
    engine = _make_engine([
        FlowTagRule(name="Email", f_regex="([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,5})",
                    severity="medium", clue_type="敏感信息泄漏"),
    ])
    body = "contact: a@x.com, backup: b@y.org"
    tags, _colors, clues = engine.match_detailed("", "", "", body)
    assert tags == "Email(2)"  # 标签串（多次命中带计数）
    assert len(clues) == 1
    assert clues[0]["evidence"] == "a@x.com"  # 首个命中作证据


# ===== collect_tag_clues =====

def test_collect_tag_clues_from_flows(db):
    """从 tags 非空流量推导线索：等级排序、证据提取、去重。"""
    from datetime import datetime, timezone
    from hemlock.models.flow import Flow
    from hemlock.models.project import Project

    project = Project(name="p")
    db.add(project)
    db.commit()
    db.refresh(project)

    ts = datetime.now(timezone.utc)
    db.add_all([
        Flow(project_id=project.id, method="GET", url="http://x/a", host="x",
             req_headers="Cookie: rememberMe=deleteMe", req_body="",
             resp_status=200, resp_headers="Server: Tomcat", resp_body="ok",
             tags="Shiro(1)", tag_colors="green", source="proxy",
             timestamp=ts),
        Flow(project_id=project.id, method="POST", url="http://x/b", host="x",
             req_headers="Host: x", req_body="u=1",
             resp_status=500, resp_headers="", resp_body="MySQLSyntaxErrorException",
             tags="SQL Injection(1)", tag_colors="red", source="proxy",
             timestamp=ts),
        Flow(project_id=project.id, method="GET", url="http://x/c", host="x",
             req_headers="Host: x", req_body="",
             resp_status=200, resp_headers="", resp_body="<html>normal</html>",
             tags="", source="proxy", timestamp=ts),  # 无标签不参与
    ])
    db.commit()

    engine = _make_engine([
        _shiro_rule(),
        FlowTagRule(name="SQL Injection",
                    f_regex="(SQL syntax.*MySQL|MySQLSyntaxErrorException)",
                    color="red", scope="response body",
                    severity="high", clue_type="疑似漏洞"),
    ])
    monkey_engine = pytest.MonkeyPatch()
    monkey_engine.setattr(fe_mod, "flowtag_engine", engine)
    try:
        clues = fe_mod.collect_tag_clues(db, project.id)
    finally:
        monkey_engine.undo()

    assert len(clues) == 2
    assert all(c["severity"] == "high" for c in clues)
    by_rule = {c["rule_name"]: c for c in clues}
    assert "rememberMe=" in by_rule["Shiro"]["evidence"]
    assert by_rule["Shiro"]["flow_id"] and by_rule["Shiro"]["url"] == "http://x/a"
    assert "MySQLSyntaxErrorException" in by_rule["SQL Injection"]["evidence"]


def test_collect_tag_clues_severity_filter_and_isolation(db):
    """min_severity 过滤；项目隔离（其他项目的流量不出线索）。"""
    from hemlock.models.flow import Flow
    from hemlock.models.project import Project

    p1 = Project(name="p1")
    p2 = Project(name="p2")
    db.add_all([p1, p2])
    db.commit()
    db.refresh(p1)
    db.refresh(p2)

    db.add_all([
        Flow(project_id=p1.id, method="GET", url="http://x/1", host="x",
             req_headers="Host: x", req_body="", resp_status=200,
             resp_headers="", resp_body="ok", tags="Shiro(1)", source="proxy"),
        Flow(project_id=p2.id, method="GET", url="http://y/2", host="y",
             req_headers="Host: y", req_body="", resp_status=200,
             resp_headers="", resp_body="ok", tags="Shiro(1)", source="proxy"),
    ])
    db.commit()

    engine = _make_engine([
        _shiro_rule(),
        FlowTagRule(name="JWT", f_regex="(eyJ[A-Za-z0-9_-]{10,})", color="green",
                    scope="any", severity="low", clue_type="信息收集"),
    ])
    mp = pytest.MonkeyPatch()
    mp.setattr(fe_mod, "flowtag_engine", engine)
    try:
        # 项目隔离：只出 p1 的
        clues = fe_mod.collect_tag_clues(db, p1.id)
        assert len(clues) == 1 and clues[0]["host"] == "x"

        # high 过滤：JWT(low) 不出；同流量多标签按 severity 保留达标项
        db.add(Flow(project_id=p1.id, method="GET", url="http://x/3", host="x",
                    req_headers="Auth: Bearer eyJhbGciOiJIUzI1NiJ9", req_body="",
                    resp_status=200, resp_headers="", resp_body="ok",
                    tags="JWT(1),Shiro(1)", source="proxy"))
        db.commit()
        high_only = fe_mod.collect_tag_clues(db, p1.id, min_severity="high")
        assert [c["rule_name"] for c in high_only] == ["Shiro", "Shiro"]
        assert all(c["severity"] == "high" for c in high_only)
    finally:
        mp.undo()


# ===== Agent 工具集成 =====

def test_tool_list_tag_clues(db):
    """_tool_list_tag_clues：项目隔离 + summary 汇总 + 提示语。"""
    from hemlock.models.flow import Flow
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

    db.add(Flow(project_id=project.id, method="GET", url="http://x/a",
                req_headers="Cookie: rememberMe=deleteMe", req_body="",
                resp_status=200, resp_headers="", resp_body="ok",
                tags="Shiro(1)", source="proxy"))
    db.commit()

    agent = AgentOrchestrator(db, task)
    agent.session_id = "test_session"
    agent.context = None

    engine = _make_engine([_shiro_rule()])
    mp = pytest.MonkeyPatch()
    mp.setattr(fe_mod, "flowtag_engine", engine)
    try:
        result = agent._tool_list_tag_clues({"min_severity": "high"})
    finally:
        mp.undo()

    assert result["total"] == 1
    assert result["summary"]["high"] == 1
    assert result["clues"][0]["rule_name"] == "Shiro"
    assert "verify_vulnerability" in result["hint"]

    # 无项目任务报错
    task2 = Task(goal="g", project_id=None, status="idle", steps="[]")
    db.add(task2)
    db.commit()
    db.refresh(task2)
    agent2 = AgentOrchestrator(db, task2)
    agent2.session_id = "test_session"
    assert "error" in agent2._tool_list_tag_clues({})


def test_tool_schema_registered():
    """list_tag_clues 是核心工具（始终注入）且进指挥官只读集。"""
    from hemlock.services.agent_service import (
        AGENT_TOOLS,
        COORDINATOR_READONLY_TOOLS,
        OPTIONAL_TOOL_NAMES,
    )

    names = {t["function"]["name"] for t in AGENT_TOOLS}
    assert "list_tag_clues" in names
    assert "list_tag_clues" not in OPTIONAL_TOOL_NAMES  # 不在按需组
    assert "list_tag_clues" in COORDINATOR_READONLY_TOOLS


# ===== 老用户规则合并 =====

def test_load_rules_merges_severity_from_builtin(tmp_path, monkeypatch):
    """用户规则文件同名规则缺 severity 时从内置规则补齐。"""
    import json as _json
    from pathlib import Path

    user_file = tmp_path / "user_rules.json"
    user_file.write_text(_json.dumps([
        # 老格式：无 severity/clue_type 字段
        {"name": "Shiro", "f_regex": "(=deleteMe|rememberMe=)", "color": "green"},
    ]), encoding="utf-8")

    monkeypatch.setattr(fe_mod, "USER_RULES_FILE", user_file)

    engine = FlowTagEngine()
    engine.load_rules()

    shiro = next(r for r in engine.rules if r.name == "Shiro")
    # 用户文件没写 severity，从内置规则补齐为 high
    assert shiro.severity == "high"
    assert shiro.clue_type == "组件攻击面"
