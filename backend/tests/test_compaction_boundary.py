"""分界线机制测试：compaction 固化 + 线性恢复 + 上下文窗口兜底。

核心验证点：
- compaction 步骤展开压缩消息，并跳过其覆盖的普通步骤（历史段不重复）
- 多次推进后嵌套 compaction 恢复正确（每个 compaction 只跳自己的覆盖区）
- 有 compaction 时 _build_chat_messages 不再全量重算（历史固化）
- _get_context_window 优先 provider 字段，空时按服务商兜底
"""

import json

import pytest

from hemlock.core.database import init_db, SessionLocal
from hemlock.models.llm_provider import LLMProvider
from hemlock.models.task import Task
from hemlock.services.agent_service import (
    _build_chat_messages,
    _get_context_window,
    _linear_restore_steps,
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


def _step(step_type: str, data, ts: str = "2026-08-09T00:00:00+00:00") -> dict:
    return {"type": step_type, "data": data, "timestamp": ts}


def _compaction(messages, method="llm", skip=0) -> dict:
    return _step("compaction", {
        "messages": messages,
        "method": method,
        "boundary_step_count": skip,
    })


def test_linear_restore_expands_compaction_and_skips_covered_steps():
    """compaction 展开压缩消息；被覆盖的原始步骤已物理替换（不重复出现）。

    真实推进时 _advance_compaction_boundary 把追加区整体替换为 compaction，
    原始步骤存于 original_steps 副本，LLM 恢复只用压缩后的 messages。
    """
    steps = [
        _compaction(
            [{"role": "user", "content": "[上下文压缩] 早期对话摘要..."}],
            method="llm",
            skip=2,  # 记录覆盖数（boundary_step_count）
        ),
        _step("user_message", "继续"),  # 推进后的新步骤（被覆盖的"你好/开始测试"已被替换删除）
    ]

    msgs = _linear_restore_steps(steps)

    roles = [m["role"] for m in msgs]
    contents = [m.get("content") for m in msgs]
    assert roles == ["user", "user"]  # 摘要 + 继续
    assert "早期对话摘要" in contents[0]
    # 被压缩的 2 步不出现（物理已替换，只留 original_steps 副本）
    assert "你好" not in contents
    assert "开始测试" not in contents
    assert "继续" in contents[1]


def test_linear_restore_nested_compactions():
    """多次推进后嵌套 compaction：压缩消息线性展开，无覆盖步骤残留。"""
    steps = [
        _compaction([{"role": "user", "content": "[C1] 摘要1"}], skip=2),
        _compaction(
            [{"role": "user", "content": "[C2] 摘要2"}, {"role": "user", "content": "[C2b] 摘要2b"}],
            skip=4,
        ),
        _step("user_message", "E"),  # 第二次推进后的新步骤
    ]

    msgs = _linear_restore_steps(steps)

    contents = [m.get("content") for m in msgs]
    assert contents == ["[C1] 摘要1", "[C2] 摘要2", "[C2b] 摘要2b", "E"]


def test_build_chat_messages_uses_compaction_no_recompute(db):
    """有 compaction 时 _build_chat_messages 线性展开压缩消息，不重复原文。"""
    steps = [
        _compaction(
            [{"role": "user", "content": "[上下文压缩] 已压缩早期步骤"}],
            method="llm",
            skip=2,  # 覆盖"任务开始 + tool_call"（推进时已替换删除）
        ),
        _step("user_message", "最新指令"),
    ]
    task = Task(id="tsk_test1", project_id="proj1", steps=json.dumps(steps, ensure_ascii=False))
    db.add(task)
    db.commit()

    messages = _build_chat_messages(steps, task, db)

    # 第一个是稳定段 system
    assert messages[0]["role"] == "system"
    contents = [m.get("content") for m in messages if m.get("content")]
    # 压缩摘要注入
    assert any("已压缩早期步骤" in c for c in contents)
    # 被压缩的原始 tool_call 细节不出现（历史固化不重算）
    full = json.dumps(contents, ensure_ascii=False)
    assert "send_request" not in full
    assert "最新指令" in full
    # 不存在第二个全量压缩标记（assemble 只在无 compaction 时跑）
    # 消息条数应远小于原始展开（压缩生效）
    assert len(messages) <= 5


def test_build_chat_messages_without_compaction_keeps_original(db):
    """无 compaction（首次累积期）时保留原文，压缩由兜底 assemble 处理。"""
    steps = [
        _step("user_message", "问题1"),
        _step("assistant_message", "回复1"),
        _step("user_message", "问题2"),
        _step("assistant_message", "回复2"),
        _step("user_message", "问题3"),
        _step("assistant_message", "回复3"),
    ]
    task = Task(id="tsk_test2", project_id="proj1", steps=json.dumps(steps, ensure_ascii=False))
    db.add(task)
    db.commit()

    messages = _build_chat_messages(steps, task, db)

    contents = [m.get("content") for m in messages if m.get("content")]
    # 6 条对话消息 > 6 时走 assemble 兜底压缩，但仍保留关键原文
    assert any("问题1" in c for c in contents)
    assert any("问题3" in c for c in contents)


def test_get_context_window_prefers_provider_field(db):
    """provider.context_window 优先。"""
    p = LLMProvider(
        id="p1", name="DeepSeek", provider="deepseek",
        api_key="sk-test", base_url="https://api.deepseek.com/v1",
        model="deepseek-v4-flash", context_window=1_000_000, is_default=True,
    )
    db.add(p)
    db.commit()

    assert _get_context_window(db) == 1_000_000


def test_get_context_window_fallback_by_type(db):
    """context_window 为空时按服务商兜底：deepseek → 1M，其余 → 128K。"""
    p = LLMProvider(
        id="p2", name="OpenAI", provider="openai",
        api_key="sk-test", base_url="https://api.openai.com/v1",
        model="gpt-4o", context_window=None, is_default=True,
    )
    db.add(p)
    db.commit()

    assert _get_context_window(db) == 131072
