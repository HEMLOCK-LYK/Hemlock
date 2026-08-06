"""流量 API 测试。"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """创建测试客户端，使用临时数据库。"""
    monkeypatch.setenv("HEMLOCK_DB_PATH", str(tmp_path / "test.db"))

    # 重新加载配置
    from hemlock.core import config, database
    import importlib
    importlib.reload(config)
    monkeypatch.setattr("hemlock.core.config.settings.db_path", str(tmp_path / "test.db"))

    # 重建 engine
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


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_flow_list_empty(client):
    resp = client.get("/api/flows")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_flow_create_and_get(client):
    """通过请求发送 API 创建一条流量，再查询。"""
    # 用 db 直接插入测试数据（流量需关联到项目，API 按 project_id 隔离）
    from hemlock.core.database import SessionLocal
    from hemlock.models.flow import Flow
    from hemlock.models.project import Project

    db = SessionLocal()
    project = Project(name="测试项目")
    db.add(project)
    db.commit()
    db.refresh(project)
    project_id = project.id  # 提前保存为普通变量，避免 session 关闭后访问过期属性
    flow = Flow(project_id=project_id, method="GET", url="https://example.com/test", resp_status=200, source="proxy")
    db.add(flow)
    db.commit()
    db.refresh(flow)
    flow_id = flow.id
    db.close()

    # 查列表（需携带 project_id）
    resp = client.get(f"/api/flows?project_id={project_id}")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # 查详情
    resp = client.get(f"/api/flows/{flow_id}")
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://example.com/test"

    # 不存在的
    resp = client.get("/api/flows/nonexistent")
    assert resp.status_code == 404


def test_flow_filter_by_method(client):
    from hemlock.core.database import SessionLocal
    from hemlock.models.flow import Flow
    from hemlock.models.project import Project

    db = SessionLocal()
    project = Project(name="测试项目")
    db.add(project)
    db.commit()
    db.refresh(project)
    project_id = project.id  # 提前保存为普通变量，避免 session 关闭后访问过期属性
    db.add(Flow(project_id=project_id, method="GET", url="https://a.com/1", source="proxy"))
    db.add(Flow(project_id=project_id, method="POST", url="https://a.com/2", source="proxy"))
    db.commit()
    db.close()

    resp = client.get(f"/api/flows?method=GET&project_id={project_id}")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["method"] == "GET"
