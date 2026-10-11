# -*- coding: utf-8 -*-
"""攻击链自动构建 + 三工具测试（docs/attack-chain-design.md 第一期）。

核心性质：
- 幂等：同一 steps 重复构建不产生重复节点（按 source_step 去重）
- credential 脱敏硬要求：入库一律只存掩码（前4+****+后4 / 文本模式掩码）
- 时间顺序主干连线，Agent 手工补的边不被重建洗掉
- 构建失败不影响主流程
"""
import json

import pytest

from hemlock.models.attack_chain import AttackChain, AttackNode
from hemlock.services.attack_chain_builder import (
    build_from_steps,
    ensure_chain,
    export_chain_markdown,
    mask_credential,
    mask_credential_text,
    render_chain_text,
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


@pytest.fixture
def env(db):
    """项目 + 任务 + agent，返回 (db, agent, task)。"""
    from hemlock.models.project import Project
    from hemlock.models.task import Task
    from hemlock.services.agent_service import AgentOrchestrator

    project = Project(name="p")
    db.add(project)
    db.commit()
    db.refresh(project)
    task = Task(goal="测试", project_id=project.id, status="idle", steps="[]")
    db.add(task)
    db.commit()
    db.refresh(task)
    agent = AgentOrchestrator(db, task)
    agent.session_id = "s"
    agent.context = None
    return db, agent, task


def _step(step_type: str, data) -> dict:
    return {
        "type": step_type,
        "data": data if isinstance(data, str) else json.dumps(data, ensure_ascii=False),
    }


def _tool_step(tool: str, args: dict, result=None) -> dict:
    return _step("tool_call", {"tool": tool, "args": args, "result": result if result is not None else {}})


# ===== 脱敏 =====

def test_mask_credential():
    assert mask_credential("S3cr3tPassword!") == "S3cr****ord!"
    assert mask_credential("short") == "****"
    assert mask_credential("") == "****"
    assert mask_credential("12345678") == "****"  # 8 位 = keep*2，全掩码


def test_mask_credential_text():
    out = mask_credential_text("登录成功 password=Admin@123 已保存")
    assert "Admin@123" not in out
    assert "password=Admi****@123" in out  # 前4 + **** + 后4
    assert "my_secret_key123" not in mask_credential_text("密码：my_secret_key123")


# ===== 自动构建 =====

def test_build_from_steps_rules(env):
    """recon→asset / report_finding→vuln / authenticate→credential / extract_data→loot。"""
    db, agent, task = env
    task.steps = json.dumps([
        _step("user_message", "开始测试"),
        _tool_step("http_probe", {"url": "http://target.local/"}),
        _tool_step("verify_vulnerability", {"vuln_type": "SQLI", "url": "http://target.local/a"}, {"verified": False}),
        _tool_step("report_finding", {"vuln_type": "SQLI", "title": "注入", "severity": "high", "flow_id": "flw_1"}, {"finding_id": "f1"}),
        _tool_step("authenticate", {"login_url": "http://target.local/login", "username": "admin", "password": "P@ssw0rd123"}, {"success": True}),
        _tool_step("verify_vulnerability", {"vuln_type": "WEAK_PASSWORD", "url": "http://target.local/admin"}, {"verified": True, "confidence": 0.9}),
        _tool_step("extract_data", {"flow_id": "flw_1", "pattern": "email", "var_name": "emails"}),
    ], ensure_ascii=False)
    db.commit()

    chain = build_from_steps(db, task)
    assert chain is not None
    nodes = db.query(AttackNode).filter(AttackNode.chain_id == chain.id).order_by(AttackNode.source_step).all()

    types = [n.node_type for n in nodes]
    assert types == ["asset", "vuln", "credential", "vuln", "loot"]
    # 未验证通过的不成节点（第一个 verify verified=False）
    # credential 脱敏硬要求
    cred = next(n for n in nodes if n.node_type == "credential")
    detail = json.loads(cred.detail)
    assert "P@ssw0rd123" not in json.dumps(detail)
    assert detail["password"] == "P@ss****d123"  # 11 字符：前4+****+后4
    assert detail["username"] == "****"  # admin 5 字符 ≤ 8：全掩码
    # 证据流量挂载
    vuln = nodes[1]
    assert vuln.flow_id == "flw_1"
    # 主干边按时间顺序
    edges = json.loads(chain.edges)
    assert len(edges) == len(nodes) - 1


def test_build_idempotent(env):
    """重复构建不产生重复节点、不重复连边（source_step 幂等键）。"""
    db, agent, task = env
    task.steps = json.dumps([
        _tool_step("http_probe", {"url": "http://x/"}),
        _tool_step("extract_data", {"flow_id": "f", "pattern": "p", "var_name": "v"}),
    ], ensure_ascii=False)
    db.commit()

    chain1 = build_from_steps(db, task)
    assert chain1 is not None
    n1 = db.query(AttackNode).filter(AttackNode.chain_id == chain1.id).count()
    e1 = len(json.loads(chain1.edges))

    chain2 = build_from_steps(db, task)
    assert chain2.id == chain1.id  # 同任务复用同一条 draft 链
    n2 = db.query(AttackNode).filter(AttackNode.chain_id == chain2.id).count()
    e2 = len(json.loads(chain2.edges))
    assert (n1, e1) == (n2, e2) == (2, 1)


def test_build_manual_edges_survive_rebuild(env):
    """Agent 手工补的语义边在后续自动重建中保留。"""
    db, agent, task = env
    task.steps = json.dumps([
        _tool_step("authenticate", {"login_url": "http://x/login", "username": "admin", "password": "longpassword123"}, {"success": True}),
        _tool_step("extract_data", {"flow_id": "f", "pattern": "p", "var_name": "v"}),
    ], ensure_ascii=False)
    db.commit()
    chain = build_from_steps(db, task)
    nodes = db.query(AttackNode).filter(AttackNode.chain_id == chain.id).order_by(AttackNode.source_step).all()
    assert [n.node_type for n in nodes] == ["credential", "loot"]

    # 手工补一条「credential → loot 的另一种语义」边？主干已有该边，改成反向语义边
    edges = json.loads(chain.edges)
    edges.append({"from": nodes[1].id, "to": nodes[0].id, "label": "凭据来源"})
    chain.edges = json.dumps(edges, ensure_ascii=False)
    db.commit()

    # 新增步骤触发重建
    steps = json.loads(task.steps)
    steps.append(_tool_step("http_probe", {"url": "http://x/admin"}))
    task.steps = json.dumps(steps, ensure_ascii=False)
    db.commit()
    build_from_steps(db, task)

    edges2 = json.loads(chain.edges)
    labels = [e.get("label") for e in edges2]
    assert "凭据来源" in labels  # 手工边幸存


def test_build_empty_steps_no_chain(env):
    """无可抽取内容时不留空链。"""
    db, agent, task = env
    task.steps = json.dumps([_step("user_message", "你好"), _step("assistant_message", "好的")])
    db.commit()
    assert build_from_steps(db, task) is None
    assert db.query(AttackChain).count() == 0


def test_build_bad_steps_json(env):
    """steps JSON 损坏时不抛异常（构建失败不影响主流程）。"""
    db, agent, task = env
    task.steps = "{broken json"
    db.commit()
    assert build_from_steps(db, task) is None


# ===== Agent 三工具 =====

def test_view_and_export_tools(env):
    """view_attack_chain 增量构建 + export_attack_chain 出 Markdown。"""
    db, agent, task = env
    task.steps = json.dumps([
        _tool_step("http_probe", {"url": "http://target.local/"}),
        _tool_step("report_finding", {"vuln_type": "SQLI", "title": "注入点", "flow_id": "flw_9"}, {}),
    ], ensure_ascii=False)
    db.commit()

    r = agent._tool_view_attack_chain({})
    assert r["total_nodes"] == 2
    assert "资产" in r["text"] and "漏洞" in r["text"]
    # 幂等：再 view 不增节点
    r2 = agent._tool_view_attack_chain({})
    assert r2["total_nodes"] == 2

    ex = agent._tool_export_attack_chain({})
    assert "## 攻击路径" in ex["markdown"]
    assert "flw_9" in ex["markdown"]
    assert ex["saved_to"].endswith("attack_chain.md")


def test_update_add_node_masks_credential(env):
    """update_attack_chain add_node：credential 节点 detail 被强制脱敏。"""
    db, agent, task = env
    r = agent._tool_update_attack_chain({
        "action": "add_node",
        "node_type": "credential",
        "title": "用默认口令登录后台",
        "detail": "admin 登录 http://x/login，password=SuperSecret999",
    })
    assert r["ok"] is True
    assert "SuperSecret999" not in r["text"]  # 返回的文本视图也不含明文

    node = db.query(AttackNode).order_by(AttackNode.created_at.desc()).first()
    assert node.node_type == "credential"
    assert "SuperSecret999" not in (node.detail or "")


def test_update_add_edge_by_index(env):
    """add_edge 用 view 输出中的节点序号连线。"""
    db, agent, task = env
    task.steps = json.dumps([
        _tool_step("http_probe", {"url": "http://x/"}),
        _tool_step("extract_data", {"flow_id": "f", "pattern": "p", "var_name": "v"}),
    ], ensure_ascii=False)
    db.commit()

    r = agent._tool_update_attack_chain({"action": "add_edge", "from_index": 2, "to_index": 1, "label": "凭据来源"})
    assert r["ok"] is True
    chain = db.query(AttackChain).first()
    labels = [e.get("label") for e in json.loads(chain.edges)]
    assert "凭据来源" in labels

    bad = agent._tool_update_attack_chain({"action": "add_edge", "from_index": 9, "to_index": 1})
    assert "error" in bad


def test_update_confirm(env):
    db, agent, task = env
    agent._tool_update_attack_chain({
        "action": "add_node", "node_type": "vuln", "title": "SQL 注入",
    })
    r = agent._tool_update_attack_chain({"action": "confirm"})
    assert r["ok"] is True and r["status"] == "confirmed"
    # confirm 后任务再构建会用新 draft 链（ensure_chain 只找 draft）
    chain = ensure_chain(db, task.project_id, task.id)
    assert chain.status == "draft"
    assert chain.id != r["chain_id"]


def test_chain_tools_registered():
    """三工具注册：核心组 + view/export 对指挥官只读可用。"""
    from hemlock.services.agent_service import AGENT_TOOLS, COORDINATOR_READONLY_TOOLS, OPTIONAL_TOOL_NAMES

    names = {t["function"]["name"] for t in AGENT_TOOLS}
    for t in ("view_attack_chain", "update_attack_chain", "export_attack_chain"):
        assert t in names
        assert t not in OPTIONAL_TOOL_NAMES
    assert "view_attack_chain" in COORDINATOR_READONLY_TOOLS
    assert "update_attack_chain" not in COORDINATOR_READONLY_TOOLS


# ===== 危害串联增强（2026-10-10 用户设计对齐）=====

def test_report_finding_node_carries_finding_id(env):
    """report_finding 的 result 带 finding_id 时入节点 detail（报告联动/前端跳转用）。"""
    db, agent, task = env
    task.steps = json.dumps([
        _tool_step("report_finding", {"vuln_type": "SQLI", "title": "注入", "flow_id": "flw_1"}, {"finding_id": "fnd_abc"}),
    ], ensure_ascii=False)
    db.commit()
    chain = build_from_steps(db, task)
    node = db.query(AttackNode).filter(AttackNode.chain_id == chain.id).first()
    detail = json.loads(node.detail)
    assert detail["finding_id"] == "fnd_abc"
    assert detail["severity"] == "medium"


def test_vuln_to_vuln_trunk_label_is_upgrade(env):
    """洞与洞串联（XSS→CSRF 式危害升级）：主干边标签是「升级」。"""
    db, agent, task = env
    task.steps = json.dumps([
        _tool_step("report_finding", {"vuln_type": "XSS", "title": "存储型XSS", "flow_id": "f1"}, {}),
        _tool_step("report_finding", {"vuln_type": "CSRF", "title": "CSRF绑定操作", "flow_id": "f2"}, {}),
    ], ensure_ascii=False)
    db.commit()
    chain = build_from_steps(db, task)
    edges = json.loads(chain.edges)
    assert len(edges) == 1
    assert edges[0]["label"] == "升级"


def test_add_node_after_index_inserts_middle(env):
    """补录中间动作环节：SQL注入 → [补]写webshell → RCE，after_index 插到中间。

    关键性质：借用宿主 source_step 的手工节点不能挡掉该步骤的自动抽取
    （后续 build 仍幂等），且新步骤触发的重建保持插入后的顺序。
    """
    db, agent, task = env
    task.steps = json.dumps([
        _tool_step("http_probe", {"url": "http://x/"}),
        _tool_step("report_finding", {"vuln_type": "SQLI", "title": "注入点", "flow_id": "f1"}, {}),
        _tool_step("verify_vulnerability", {"vuln_type": "RCE", "url": "http://x/upload"}, {"verified": True}),
    ], ensure_ascii=False)
    db.commit()

    # Agent 补「写 webshell」到第 2 个节点（SQL注入）之后
    r = agent._tool_update_attack_chain({
        "action": "add_node",
        "node_type": "action",
        "title": "写入 webshell 到 /upload/shell.jsp",
        "after_index": 2,
    })
    assert r["ok"] is True

    chain = db.query(AttackChain).first()
    nodes = db.query(AttackNode).filter(AttackNode.chain_id == chain.id).order_by(
        AttackNode.source_step.asc().nullslast(), AttackNode.created_at.asc()
    ).all()
    types = [n.node_type for n in nodes]
    assert types == ["asset", "vuln", "action", "vuln"]  # 插中间，不是排链尾

    # 之后的自动构建（新步骤/任务结束）保持顺序、不产生重复
    build_from_steps(db, task)
    nodes2 = db.query(AttackNode).filter(AttackNode.chain_id == chain.id).order_by(
        AttackNode.source_step.asc().nullslast(), AttackNode.created_at.asc()
    ).all()
    assert [n.node_type for n in nodes2] == types
    assert len(nodes2) == len(nodes)
    # 主干边完整覆盖 4 节点
    assert len(json.loads(chain.edges)) == 3


def test_add_node_after_index_out_of_range(env):
    """after_index 越界时回退链尾追加（不报错——宁可追加不失败）。"""
    db, agent, task = env
    agent._tool_update_attack_chain({"action": "add_node", "node_type": "vuln", "title": "A"})
    r = agent._tool_update_attack_chain({
        "action": "add_node", "node_type": "loot", "title": "B", "after_index": 99,
    })
    assert r["ok"] is True
    node = db.query(AttackNode).filter(AttackNode.title == "B").first()
    assert node.source_step is None  # 回退为链尾手工节点


def test_remove_node_cascades_edges_and_rebuilds_trunk(env):
    """删节点级联清边 + 剩余节点重连主干。"""
    db, agent, task = env
    task.steps = json.dumps([
        _tool_step("http_probe", {"url": "http://x/"}),
        _tool_step("report_finding", {"vuln_type": "SQLI", "title": "注入", "flow_id": "f1"}, {}),
        _tool_step("extract_data", {"flow_id": "f1", "pattern": "p", "var_name": "v"}),
    ], ensure_ascii=False)
    db.commit()
    build_from_steps(db, task)
    chain = db.query(AttackChain).first()
    assert chain is not None
    assert len(json.loads(chain.edges)) == 2

    r = agent._tool_update_attack_chain({"action": "remove_node", "node_index": 2})
    assert r["ok"] is True
    remaining = db.query(AttackNode).filter(AttackNode.chain_id == chain.id).all()
    assert len(remaining) == 2
    edges = json.loads(chain.edges)
    assert len(edges) == 1  # asset → loot 直接相连，无悬空引用

    bad = agent._tool_update_attack_chain({"action": "remove_node", "node_index": 9})
    assert "error" in bad


def test_remove_edge_manual_kept_vs_trunk_rebuilt(env):
    """删手工边真删；删主干边会被下次自动构建重建（返回 note 说明）。"""
    db, agent, task = env
    task.steps = json.dumps([
        _tool_step("http_probe", {"url": "http://x/"}),
        _tool_step("extract_data", {"flow_id": "f", "pattern": "p", "var_name": "v"}),
    ], ensure_ascii=False)
    db.commit()
    build_from_steps(db, task)
    chain = db.query(AttackChain).first()
    assert chain is not None

    # 主干边 1→2 是时间序主干：删掉返回 note
    r = agent._tool_update_attack_chain({"action": "remove_edge", "from_index": 1, "to_index": 2})
    assert r.get("ok") is True and "note" in r
    build_from_steps(db, task)  # 触发增量构建
    chain = db.query(AttackChain).first()
    assert len(json.loads(chain.edges)) == 1  # 主干重建

    # 手工边 2→1：删掉不会回来
    agent._tool_update_attack_chain({"action": "add_edge", "from_index": 2, "to_index": 1, "label": "反向"})
    r2 = agent._tool_update_attack_chain({"action": "remove_edge", "from_index": 2, "to_index": 1})
    assert r2.get("ok") is True and "note" not in r2
    labels = [e.get("label") for e in json.loads(chain.edges)]
    assert "反向" not in labels
    # 重复删：报错（没有边可删）
    r3 = agent._tool_update_attack_chain({"action": "remove_edge", "from_index": 2, "to_index": 1})
    assert "error" in r3


def test_report_includes_confirmed_chain(db):
    """报告集成：confirmed 链进 HTML/Markdown 报告；draft 不进；task 维度取该任务的链。"""
    from hemlock.models.project import Project
    from hemlock.models.task import Task
    from hemlock.services.report_service import generate_report

    project = Project(name="rp")
    db.add(project)
    db.commit()
    db.refresh(project)
    task = Task(goal="g", project_id=project.id, status="done", steps="[]")
    db.add(task)
    db.commit()
    db.refresh(task)

    chain = AttackChain(
        project_id=project.id, task_id=task.id, name="SQLi到RCE链", status="confirmed"
    )
    db.add(chain)
    db.commit()
    db.refresh(chain)
    n1 = AttackNode(
        chain_id=chain.id,
        node_type="vuln",
        title="SQL注入点",
        detail=json.dumps({"url": "http://x/a?id=1"}, ensure_ascii=False),
        source_step=1,
        flow_id="flow-1",
    )
    n2 = AttackNode(chain_id=chain.id, node_type="action", title="写入webshell", source_step=2)
    db.add_all([n1, n2])
    db.commit()
    chain.edges = json.dumps(
        [{"from": n1.id, "to": n2.id, "label": "升级"}], ensure_ascii=False
    )
    db.commit()

    draft = AttackChain(project_id=project.id, task_id=task.id, name="草稿链", status="draft")
    db.add(draft)
    db.commit()

    html = generate_report(db, project_id=project.id, fmt="html")
    assert "攻击路径" in html
    assert "SQLi到RCE链" in html
    assert "SQL注入点" in html and "写入webshell" in html
    assert "升级" in html  # 相邻箭头带边标签
    assert "http://x/a?id=1" in html and "flow-1" in html
    assert "草稿链" not in html

    md = generate_report(db, project_id=project.id, fmt="markdown")
    assert "## 攻击路径：SQLi到RCE链" in md
    assert "SQL注入点" in md
    assert "草稿链" not in md

    # task 维度报告：取该任务的链
    html_task = generate_report(db, task_id=task.id, fmt="html")
    assert "SQLi到RCE链" in html_task

    # 无范围（不传 task/project）：不倾泻全库
    html_all = generate_report(db, fmt="html")
    assert "攻击路径" not in html_all


def test_report_chain_without_nodes_skipped(db):
    """空链（无节点）不出现在报告里，也不至于让整段攻击路径消失。"""
    from hemlock.models.project import Project
    from hemlock.services.report_service import generate_report

    project = Project(name="rp2")
    db.add(project)
    db.commit()
    db.refresh(project)
    empty = AttackChain(project_id=project.id, task_id=None, name="空链", status="confirmed")
    db.add(empty)
    full = AttackChain(project_id=project.id, task_id=None, name="有料链", status="confirmed")
    db.add(full)
    db.commit()
    db.refresh(full)
    db.add(
        AttackNode(chain_id=full.id, node_type="asset", title="目标站", source_step=1)
    )
    db.commit()

    html = generate_report(db, project_id=project.id, fmt="html")
    assert "有料链" in html and "目标站" in html
    assert "空链" not in html
