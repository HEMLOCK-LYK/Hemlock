"""数据模型测试。"""

import pytest
from hemlock.core.database import init_db, SessionLocal
from hemlock.models.project import Project
from hemlock.models.flow import Flow
from hemlock.models.finding import Finding, FINDING_STATUS_PENDING
from hemlock.models.evidence import Evidence


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """使用临时数据库。"""
    monkeypatch.setattr(
        "hemlock.core.config.settings.db_path",
        str(tmp_path / "test.db"),
    )
    # 重新初始化 engine
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


def test_create_project_and_flow(db):
    """项目与流量的关系。"""
    project = Project(name="测试项目", scope="example.com")
    db.add(project)
    db.commit()

    flow = Flow(
        project_id=project.id,
        method="GET",
        url="https://example.com/api/users",
        req_headers="Host: example.com",
        resp_status=200,
        source="proxy",
    )
    db.add(flow)
    db.commit()

    db.refresh(project)
    assert len(project.flows) == 1
    assert project.flows[0].method == "GET"
    assert project.flows[0].source == "proxy"


def test_finding_with_evidence(db):
    """漏洞与证据的关系。"""
    project = Project(name="漏洞测试", scope="target.com")
    db.add(project)
    db.commit()

    evidence = Evidence(
        request="GET /vuln?id=1' HTTP/1.1",
        response="HTTP/1.1 500 SQL error",
        indicator="SQL 语法错误泄露",
        ai_reasoning="单引号触发数据库报错",
    )
    db.add(evidence)
    db.commit()

    finding = Finding(
        project_id=project.id,
        vuln_type="SQL_INJECTION",
        severity="high",
        status=FINDING_STATUS_PENDING,
        title="基于错误的 SQL 注入",
        detail="参数 id 存在 SQL 注入",
        evidence_id=evidence.id,
    )
    db.add(finding)
    db.commit()

    db.refresh(finding)
    assert finding.evidence.indicator == "SQL 语法错误泄露"
    assert finding.status == FINDING_STATUS_PENDING
    db.refresh(project)
    assert len(project.findings) == 1


def test_flow_source_filter(db):
    """流量来源过滤。"""
    project = Project(name="来源测试", scope="a.com")
    db.add(project)
    db.commit()

    for source in ["proxy", "agent", "repeater", "intruder"]:
        db.add(Flow(project_id=project.id, method="GET", url=f"https://a.com/{source}", source=source))
    db.commit()

    proxy_flows = db.query(Flow).filter(Flow.source == "proxy").all()
    assert len(proxy_flows) == 1
    assert proxy_flows[0].url.endswith("/proxy")
