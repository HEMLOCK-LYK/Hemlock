"""拦截器 API 测试。"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """创建测试客户端，使用临时数据库（并重置拦截器全局状态）。"""
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

    from hemlock.services.intercept_controller import intercept_controller
    intercept_controller.reset()
    intercept_controller.update_config(
        enabled=False, intercept_requests=False, intercept_responses=False
    )

    from hemlock.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_intercept_status_default_off(client):
    resp = client.get("/api/intercept/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["intercept_requests"] is False
    assert data["intercept_responses"] is False
    assert data["pending_count"] == 0
    assert data["proxy_running"] is False


def test_intercept_config_update(client):
    resp = client.put("/api/intercept/config", json={"enabled": True, "intercept_requests": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["intercept_requests"] is True

    resp = client.get("/api/intercept/status")
    assert resp.json()["enabled"] is True


def test_intercept_forward_proxy_not_running(client):
    resp = client.post(
        "/api/intercept/forward",
        json={"id": "int_1", "raw": "GET / HTTP/1.1\nHost: a.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "proxy_not_running"


def test_intercept_drop_proxy_not_running(client):
    resp = client.post("/api/intercept/drop", json={"id": "int_1"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_intercept_pending_empty(client):
    resp = client.get("/api/intercept/pending")
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "items": []}
