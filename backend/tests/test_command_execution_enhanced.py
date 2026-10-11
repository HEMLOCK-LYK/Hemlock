"""命令执行增强回归测试（对齐 Claude Code 终端体验）。

覆盖：
- _truncate_output head+tail 截断（头保留起始信息、尾保留结果，中间省略）
- _StreamCollector 流式收集：EOF 收割、内存上限滑动窗口、取消后 partial 可取、
  on_chunk 回调异常不影响收集
- run_command 超时不再丢弃输出（超时前已产出的部分输出保留）
- run_command 超长输出 head+tail 截断 + 完整落盘 full_output_file
- run_command 输出流式推送（event=command_output 经 event_bus）
- 后台命令完成后自动注入通知到 inbox（无需轮询 check_background）
- 任务取消后后台完成不再注入（停止就是停止）
- run_script 超时保留部分输出
"""

import asyncio
import sys
from pathlib import Path

import pytest

from hemlock.services.agent_service import (
    AgentOrchestrator,
    _StreamCollector,
    _truncate_output,
)
from hemlock.services.event_bus import event_bus


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """使用临时数据库与隔离的工作区目录。"""
    monkeypatch.setattr(
        "hemlock.core.config.settings.db_path",
        str(tmp_path / "test.db"),
    )
    import hemlock.core.config as config
    from hemlock.core import database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path / "data")
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    database.engine = engine
    database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    database.Base.metadata.create_all(bind=engine)
    db = database.SessionLocal()
    yield db
    db.close()


def _make_agent(db) -> tuple[AgentOrchestrator, Path]:
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
    return agent, agent._get_workspace()


def _py(code: str) -> str:
    """构建系统 Python 执行代码片段的 shell 命令。"""
    return f'"{sys.executable}" -c "{code}"'


# ===== _truncate_output =====

def test_truncate_output_head_tail():
    """超长文本保留头尾、中间省略提示；短文本原样返回。"""
    short = "hello world"
    assert _truncate_output(short) == short

    text = "H" * 6000 + "M" * 20000 + "T" * 6000
    out = _truncate_output(text, max_chars=10000, tail_chars=6000)
    assert out.startswith("H" * 100)  # 头部保留
    assert out.endswith("T" * 100)  # 尾部保留
    assert "中间省略" in out
    assert "M" * 1000 not in out  # 中间被截
    assert len(out) <= 10000 + 60  # 提示语占位（51 字符左右）


# ===== _StreamCollector =====

async def test_stream_collector_eof_and_cap():
    """读到 EOF 收割全部；超 cap 滑动窗口保留头尾各半。"""
    reader = asyncio.StreamReader()
    reader.feed_data(b"A" * 50)
    reader.feed_data(b"B" * 50)
    reader.feed_eof()
    col = _StreamCollector(reader, cap=64)
    await col.wait()

    assert len(col.partial()) == 64
    assert col.dropped == 36
    assert col.partial() == b"A" * 32 + b"B" * 32  # 头尾各半


async def test_stream_collector_on_chunk_and_cancel():
    """on_chunk 逐段回调（异常不影响收集）；取消后 partial 仍可取。"""
    reader = asyncio.StreamReader()
    chunks: list[bytes] = []

    def bad_cb(chunk: bytes) -> None:
        chunks.append(chunk)
        raise RuntimeError("回调异常不应影响收集")

    reader.feed_data(b"hello")
    reader.feed_eof()
    col = _StreamCollector(reader, on_chunk=bad_cb)
    await col.wait()
    assert chunks == [b"hello"]
    assert col.partial() == b"hello"

    # 未到 EOF 就取消：已收集数据保留
    reader2 = asyncio.StreamReader()
    reader2.feed_data(b"partial-data")
    col2 = _StreamCollector(reader2, cap=1000)
    await asyncio.sleep(0.05)  # 让读取任务消费已喂的数据
    col2._task.cancel()
    await col2.wait()
    assert col2.partial() == b"partial-data"


# ===== run_command 超时保留部分输出 =====

async def test_run_command_timeout_keeps_partial_output(db):
    """超时终止时已产出的输出保留（旧版直接丢弃只返回提示文案）。"""
    agent, _ = _make_agent(db)
    cmd = _py("print('PARTIAL_MARK', flush=True); import time; time.sleep(30)")
    result = await agent._tool_run_command({"command": cmd, "timeout": 2})

    assert "超时" in result["error"]
    assert "PARTIAL_MARK" in result["stdout"]
    assert result["success"] is False
    assert "run_in_background" in result["hint"]


async def test_run_command_normal(db):
    """正常短命令：退出码 0、输出完整、无截断标记。"""
    agent, _ = _make_agent(db)
    result = await agent._tool_run_command({
        "command": _py("print('normal-ok')"), "timeout": 30,
    })
    assert result["success"] is True
    assert result["exit_code"] == 0
    assert "normal-ok" in result["stdout"]
    assert "full_output_file" not in result
    assert "中间省略" not in result["stdout"]


async def test_run_command_truncates_and_saves_full(db):
    """超长输出：head+tail 截断展示 + 完整落盘 full_output_file。"""
    agent, _ = _make_agent(db)
    cmd = _py("print('A' * 50000, end='')")
    result = await agent._tool_run_command({"command": cmd, "timeout": 60})

    assert result["success"] is True
    assert "中间省略" in result["stdout"]
    assert len(result["stdout"]) < 40000  # 展示层已截断（head 18000 + tail 12000 + 提示，对齐 _OUTPUT_MAX=30000）

    full_file = result.get("full_output_file")
    assert full_file
    content = Path(full_file).read_text(encoding="utf-8")
    assert len(content) == 50000  # 落盘完整
    assert content.startswith("A")


# ===== 流式推送 =====

async def test_run_command_streams_output_events(db):
    """输出经 event_bus 逐段推送（event=command_output），前端实时显示的数据源。"""
    agent, _ = _make_agent(db)
    events: list[dict] = []
    event_bus.subscribe("agent", events.append)
    try:
        result = await agent._tool_run_command({
            "command": _py("print('STREAM_MARK', flush=True)"), "timeout": 30,
        })
    finally:
        event_bus.unsubscribe("agent", events.append)

    assert result["success"] is True
    deltas = [e for e in events if e.get("event") == "command_output"]
    assert deltas, "应推送至少一条 command_output 事件"
    assert "STREAM_MARK" in "".join(e["delta"] for e in deltas)
    assert all(e["task_id"] == agent.task.id for e in deltas)


# ===== 后台完成自动通知 =====

async def test_background_completion_notifies_inbox(db):
    """后台命令完成 → watcher 自动注入通知到 inbox（无需轮询）。"""
    agent, ws = _make_agent(db)
    result = await agent._run_command_background(_py("print('bg-done-mark')"), str(ws), 0)
    bg_id = result["background_id"]

    msgs: list[str] = []
    for _ in range(150):  # 最多等 15s（Python 进程启动 ~1s）
        msgs = agent.drain_inbox()
        if msgs:
            break
        await asyncio.sleep(0.1)

    assert any("[后台命令完成]" in m and bg_id in m for m in msgs)
    assert any("bg-done-mark" in m for m in msgs)
    assert agent._bg_procs[bg_id]["exit_code"] == 0


async def test_background_no_notify_after_cancel(db):
    """任务已取消：后台完成不再注入（停止就是停止）。"""
    agent, ws = _make_agent(db)
    agent.cancel()  # 置 _cancelled，watcher 应跳过注入

    result = await agent._run_command_background(_py("print('x')"), str(ws), 0)
    bg_id = result["background_id"]

    for _ in range(150):
        await asyncio.sleep(0.1)
        if agent._bg_procs[bg_id].get("exit_code") is not None:
            break

    await asyncio.sleep(0.2)  # 给 watcher 收尾机会
    assert agent.drain_inbox() == []


# ===== run_script 超时保留部分输出 =====

async def test_run_script_timeout_keeps_partial_output(db):
    agent, _ = _make_agent(db)
    code = "print('SCRIPT_PARTIAL', flush=True); import time; time.sleep(30)"
    result = await agent._tool_run_script({"code": code, "timeout": 2})

    assert "超时" in result["error"]
    assert "SCRIPT_PARTIAL" in result["stdout"]
    assert result["success"] is False
