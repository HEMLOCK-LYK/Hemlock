"""记忆快照 + 分级过滤测试。

验证点：
- build_memory_snapshot 只含 global + 当前 project（不串扰其他项目）
- _inject_memory_snapshot 惰性生成一次并固化到 task.memory_snapshot，
  之后读取固化值不再重新生成（中途 save_memory 不回流 → 快照稳定）
"""

import pytest

from hemlock.core.database import SessionLocal
from hemlock.models.task import Task
from hemlock.services.agent_service import _inject_memory_snapshot


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


@pytest.fixture()
def memories(tmp_path, monkeypatch):
    """隔离记忆目录并写入测试记忆文件。"""
    from hemlock.core import memory_manager
    global_dir = tmp_path / "global"
    projects_dir = tmp_path / "projects"
    global_dir.mkdir(parents=True, exist_ok=True)
    (projects_dir / "proj1").mkdir(parents=True, exist_ok=True)
    (projects_dir / "proj2").mkdir(parents=True, exist_ok=True)

    def _write(directory, name, mem_type, description, content):
        (directory / f"{name}.md").write_text(
            f"---\ntype: {mem_type}\ndescription: {description}\n---\n{content}",
            encoding="utf-8",
        )

    _write(global_dir, "g1", "feedback", "全局记忆", "全局内容正文")
    _write(projects_dir / "proj1", "p1a", "project", "项目1记忆", "项目1内容正文")
    _write(projects_dir / "proj2", "p2a", "project", "项目2记忆", "项目2内容正文")

    monkeypatch.setattr(memory_manager, "GLOBAL_DIR", global_dir)
    monkeypatch.setattr(memory_manager, "PROJECTS_DIR", projects_dir)
    return memory_manager


def test_build_memory_snapshot_filters_by_project(memories):
    """快照只含 global + 当前 project，不串扰其他项目。"""
    snap = memories.build_memory_snapshot(None, project_id="proj1")
    assert "g1" in snap          # global 记忆包含
    assert "p1a" in snap         # 当前 project 记忆包含
    assert "p2a" not in snap     # 其他 project 不包含
    # 正文不进索引（索引机制：只注入 name+desc）
    assert "全局内容正文" not in snap


def test_build_memory_snapshot_without_project_only_global(memories):
    """不指定 project 时只加载 global（避免跨项目串扰）。"""
    snap = memories.build_memory_snapshot(None, project_id=None)
    assert "g1" in snap
    assert "p1a" not in snap
    assert "p2a" not in snap


def test_inject_memory_snapshot_generates_once_and_freezes(db, memories):
    """惰性生成一次并固化到 task.memory_snapshot，之后读取固化值不重新生成。"""
    task = Task(id="tsk_snap1", project_id="proj1")
    db.add(task)
    db.commit()

    messages = [{"role": "system", "content": "稳定段"}]
    generated = _inject_memory_snapshot(messages, db, task)

    assert generated is True
    assert task.memory_snapshot            # 已固化
    assert "p1a" in task.memory_snapshot   # 快照含当前 project 记忆
    # 快照作为独立 system 消息注入（稳定段之后）
    assert messages[-1]["role"] == "system"
    assert "p1a" in messages[-1]["content"]

    # 再次调用：读取固化值，不重新生成、不重复插入
    messages2 = [{"role": "system", "content": "稳定段"}]
    generated2 = _inject_memory_snapshot(messages2, db, task)
    assert generated2 is False
    assert messages2[-1]["content"] == task.memory_snapshot


def test_inject_memory_snapshot_empty_memories(db, monkeypatch, tmp_path):
    """无记忆时快照为空串，不注入消息。"""
    from hemlock.core import memory_manager
    monkeypatch.setattr(memory_manager, "GLOBAL_DIR", tmp_path / "empty_global")
    monkeypatch.setattr(memory_manager, "PROJECTS_DIR", tmp_path / "empty_projects")

    task = Task(id="tsk_snap2", project_id="proj_x")
    db.add(task)
    db.commit()

    messages = [{"role": "system", "content": "稳定段"}]
    generated = _inject_memory_snapshot(messages, db, task)

    assert generated is False
    assert len(messages) == 1  # 无快照消息注入
