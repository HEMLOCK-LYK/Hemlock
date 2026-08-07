"""子 Agent 中途消息广播测试：/api/agent/inject 广播到主 Agent + 活跃子 Agent。

覆盖：
- _subagent_inbox_lifecycle 生命周期：进入注册、退出清理（正常/异常路径）
- /api/agent/inject 广播路由：主 Agent inbox + 当前活跃子 Agent inbox 同时收到
"""

import asyncio

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


class FakeTracker:
    """伪装 AgentOrchestrator：记录 enqueue_user_message 调用。"""

    def __init__(self):
        self._chat_active = True
        self.inbox = asyncio.Queue()
        self.received: list[str] = []

    def enqueue_user_message(self, message: str) -> None:
        self.received.append(message)
        self.inbox.put_nowait(message)


@pytest.mark.asyncio
async def test_subagent_inbox_lifecycle_register_cleanup():
    """inbox 生命周期：进入注册、退出清理（含异常路径）。"""
    from hemlock.services.agent_service import (
        _active_subagent_inboxes,
        _subagent_inbox_lifecycle,
    )

    parent_task_id = "task-lifecycle"

    with _subagent_inbox_lifecycle(parent_task_id) as inbox:
        assert _active_subagent_inboxes.get(parent_task_id) is inbox
        inbox.put_nowait("hello")

    # 正常退出 → 已清理
    assert parent_task_id not in _active_subagent_inboxes

    # 异常路径 → 同样清理
    with pytest.raises(RuntimeError):
        with _subagent_inbox_lifecycle(parent_task_id) as inbox2:
            assert _active_subagent_inboxes.get(parent_task_id) is inbox2
            raise RuntimeError("boom")
    assert parent_task_id not in _active_subagent_inboxes


def test_inject_broadcasts_to_main_and_subagent(client):
    """/api/agent/inject 广播：主 Agent inbox 与活跃子 Agent inbox 同时收到消息。"""
    from hemlock.services import agent_service

    task_id = "task-broadcast"
    tracker = FakeTracker()
    agent_service._active_agents[task_id] = tracker

    sub_inbox = asyncio.Queue()
    agent_service._active_subagent_inboxes[task_id] = sub_inbox

    try:
        resp = client.post("/api/agent/inject", json={"task_id": task_id, "message": "帮我优先测登录接口"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["queued"] is True

        # 主 Agent inbox 收到
        assert tracker.received == ["帮我优先测登录接口"]
        # 活跃子 Agent inbox 收到（广播语义）
        assert sub_inbox.get_nowait() == "帮我优先测登录接口"
    finally:
        agent_service._active_agents.pop(task_id, None)
        agent_service._active_subagent_inboxes.pop(task_id, None)


def test_inject_not_running_does_not_broadcast(client):
    """无活跃循环：inject 返回 queued=false，不广播。"""
    from hemlock.services import agent_service

    task_id = "task-idle"
    resp = client.post("/api/agent/inject", json={"task_id": task_id, "message": "hi"})
    assert resp.status_code == 200
    assert resp.json()["queued"] is False


def test_inject_broadcasts_even_without_active_subagent(client):
    """主 Agent 活跃但无子 Agent 在跑：消息只进主 Agent，不报错。"""
    from hemlock.services import agent_service

    task_id = "task-main-only"
    tracker = FakeTracker()
    agent_service._active_agents[task_id] = tracker

    try:
        resp = client.post("/api/agent/inject", json={"task_id": task_id, "message": "继续"})
        assert resp.status_code == 200
        assert resp.json()["queued"] is True
        assert tracker.received == ["继续"]
    finally:
        agent_service._active_agents.pop(task_id, None)
