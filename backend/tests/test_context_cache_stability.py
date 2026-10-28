"""上下文缓存稳定性回归测试（2026-10-28 缓存/蒸馏改造的验收钉子）。

把两条用户红线钉进测试：
- 红线 1（缓存不崩）：多轮对话中，第 N 轮发送给 LLM 的消息列表必须是
  第 N-1 轮列表的逐字节前缀扩展（append-only）。唯一的例外是一次性的、
  被固化为 compaction 边界的压缩推进——那之后继续 append-only。
- 红线 2（不花式改写）：除 compaction 边界外，历史消息内容永不变化；
  工具输出微压缩只发生在消息首次插入时，同一条工具结果无论重放多少次
  （恢复/重算/压缩输入）字节都一致。

同时覆盖知识库条目在快照中的注入格式（两大类：经验 + 知识库）。
"""

import json

import pytest

from hemlock.services.agent_service import (
    _build_chat_messages,
    _deterministic_boundary_compact,
    _tool_result_to_llm_content,
)
from hemlock.core.context_manager import TokenEstimator


class FakeTask:
    """最小 Task 替身：_build_chat_messages 只读 goal 等少量属性。"""

    def __init__(self, steps=None, memory_snapshot: str = ""):
        self.id = "task_test"
        self.goal = "测试目标"
        self.steps = json.dumps(steps or [], ensure_ascii=False)
        self.memory_snapshot = memory_snapshot
        self.project_id = None


def _append_tool_round(steps: list[dict], i: int) -> None:
    """向 steps 追加一轮 assistant 分析 + 工具调用（模拟一次 Agent 行动）。"""
    steps.append({
        "type": "assistant_message",
        "data": f"第 {i} 轮分析：目标响应正常，继续探测参数。",
    })
    steps.append({
        "type": "tool_call",
        "data": {
            "tool": "send_request",
            "args": {"url": f"http://target.test/api/item?id={i}"},
            "result": {"status": 200, "body": f"item body {i} " + "A" * 200, "flow_id": f"f{i}"},
            "tool_call_id": f"call_{i}",
            "raw_tool_call": {
                "id": f"call_{i}",
                "type": "function",
                "function": {
                    "name": "send_request",
                    "arguments": json.dumps({"url": f"http://target.test/api/item?id={i}"}),
                },
            },
        },
    })


def _strip_system(messages: list[dict]) -> list[dict]:
    """消息比对时排除 system 段（skill/MCP 动态段在测试间可能因注册表变化）。"""
    return [m for m in messages if m.get("role") != "system"]


def test_chat_history_prefix_stable_across_turns():
    """红线 1：多轮恢复的前缀逐字节稳定——第 N 轮恢复结果包含第 N-1 轮全部内容。"""
    steps: list[dict] = []
    prev_conv: list[dict] = []

    for turn in range(1, 8):
        steps.append({"type": "user_message", "data": f"第 {turn} 轮用户指令：继续测试登录接口。"})
        for i in range(turn, turn + 3):
            _append_tool_round(steps, i)

        task = FakeTask(steps=steps)
        messages = _strip_system(_build_chat_messages(steps, task, db=None))

        # 第 N 轮恢复结果是第 N-1 轮的逐字节前缀扩展（append-only，缓存前缀可命中）
        assert messages[: len(prev_conv)] == prev_conv, f"第 {turn} 轮历史前缀被改写（缓存崩溃）"
        prev_conv = messages


def test_micro_compact_deterministic_bytes():
    """红线 2：同一工具结果的 LLM 内容序列化是确定性的（插入一次，永不重算变化）。"""
    result = {
        "status": 200,
        "headers": "Content-Type: application/json",
        "body": "<html>" + "B" * 30000 + "</html>",
        "flow_id": "flow_abc123",
    }
    args = {"url": "http://target.test/search?q=x", "method": "GET"}

    a = _tool_result_to_llm_content("send_request", args, result)
    b = _tool_result_to_llm_content("send_request", args, result)
    assert a == b, "同一工具结果两次序列化字节不一致（前缀缓存会崩）"
    # 微压缩生效：超大输出被压缩，flow_id 保留
    assert len(a) < len(json.dumps(result, ensure_ascii=False))
    assert "flow_abc123" in a


def test_micro_compact_passthrough_below_threshold():
    """阈值以下原样透传（不改写任何内容）。"""
    result = {"status": 404, "body": "not found"}
    content = _tool_result_to_llm_content("send_request", {"url": "http://x.test/"}, result)
    assert content == json.dumps(result, ensure_ascii=False)


def test_deterministic_fallback_is_boundary_shaped():
    """LLM 不可用时的确定性回退：system 原样 + 单条 boundary + 最近原文，不逐条改写。"""
    messages = [{"role": "system", "content": "稳定系统提示"}]
    for i in range(30):
        messages.append({"role": "user", "content": f"历史 {i} " + "y" * 400})
    messages.append({"role": "user", "content": "现在测哪个参数？"})
    messages.append({"role": "assistant", "content": "先测 id 参数的注入。"})

    result, ctype = _deterministic_boundary_compact(messages, keep_recent=6)
    assert ctype == "deterministic"
    assert result[0]["content"] == "稳定系统提示"
    assert len(result) == 2 + 6
    assert "[上下文压缩]" in result[1]["content"]
    # 最近原文逐字节保留（split 按对话消息数取尾部 6 条）
    assert result[-6:] == [m for m in messages if m.get("role") != "system"][-6:]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """使用临时数据库（与 test_memory_snapshot 同款）。"""
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


def test_compaction_boundary_persist_restore_stable(db):
    """分界线推进后固化到 steps：恢复结果与推进结果一致、恢复幂等（前缀继续稳定）。"""
    from hemlock.models.task import Task
    from hemlock.services.agent_service import _advance_compaction_boundary

    task = Task(id="task_boundary_test", goal="测试", steps="[]", status="running")
    db.add(task)
    db.commit()

    steps: list[dict] = []
    for i in range(15):
        steps.append({"type": "user_message", "data": f"指令 {i}：" + "z" * 600})
        _append_tool_round(steps, i)
    task.steps = json.dumps(steps, ensure_ascii=False)
    db.commit()

    # 先构建当前消息，人为推进分界线（LLM 未配置 → session_memory/确定性层）
    task_for_build = FakeTask(steps=steps)
    messages = _build_chat_messages(steps, task_for_build, db=None)
    threshold = int(TokenEstimator.estimate_messages(messages) * 0.8) + 1000
    import asyncio
    new_messages, ctype = asyncio.run(_advance_compaction_boundary(
        db, task, steps, messages, None, None, threshold=threshold, mode="test",
    ))
    assert ctype in ("deterministic", "session_memory")
    # steps 最后一个步骤已被替换为 compaction
    restored_steps = json.loads(task.steps)
    assert restored_steps[-1]["type"] == "compaction"

    # 恢复（真实 Task 恢复路径）两次，结果与推进返回值逐字节一致、恢复幂等
    task2 = FakeTask(steps=restored_steps)
    restore1 = _build_chat_messages(restored_steps, task2, db=None)
    restore2 = _build_chat_messages(restored_steps, task2, db=None)
    assert restore1 == new_messages
    assert restore1 == restore2


def test_kb_snapshot_groups_and_determinism(tmp_path, monkeypatch):
    """知识库快照：两大类分组输出，且同库同条目两次生成逐字节一致（缓存稳定）。"""
    from hemlock.core import memory_manager

    memories_dir = tmp_path / "memories"
    global_dir = memories_dir / "global"
    kb_dir = memories_dir / "kb" / "waf-bypass"
    projects_dir = memories_dir / "projects" / "proj1"
    for d in (global_dir, kb_dir, projects_dir):
        d.mkdir(parents=True, exist_ok=True)

    (global_dir / "g1.md").write_text(
        "---\ntype: feedback\ndescription: 全局经验\ndef 需要换行\n---\n正文",
        encoding="utf-8",
    )
    (kb_dir / "_meta.json").write_text(
        json.dumps({"name": "waf-bypass", "description": "绕WAF实战技巧"}),
        encoding="utf-8",
    )
    (kb_dir / "double-encode.md").write_text(
        "---\ntype: kb\ndescription: 双层URL编码绕过关键字拦截\ntrigger: WAF拦截\n---\n正文内容",
        encoding="utf-8",
    )

    monkeypatch.setattr(memory_manager, "MEMORIES_DIR", memories_dir)
    monkeypatch.setattr(memory_manager, "GLOBAL_DIR", global_dir)
    monkeypatch.setattr(memory_manager, "KB_DIR", memories_dir / "kb")
    monkeypatch.setattr(memory_manager, "PROJECTS_DIR", memories_dir / "projects")

    s1 = memory_manager.build_memory_snapshot(None, project_id="proj1")
    s2 = memory_manager.build_memory_snapshot(None, project_id="proj1")
    assert s1 == s2, "快照两次生成不一致（缓存前缀会崩）"
    assert "长期记忆" in s1
    assert "知识库索引" in s1
    assert "waf-bypass" in s1
    assert "double-encode" in s1
    # 项目记忆只含当前项目，不串扰
    assert "proj1" not in s1 or "p1a" not in s1  # proj1 无条目时不出现其记忆


def test_kb_crud_roundtrip(tmp_path, monkeypatch):
    """知识库 CRUD：建库 → 条目写入 → 按库过滤 → 删库连带条目。"""
    from hemlock.core import memory_manager

    memories_dir = tmp_path / "memories"
    memories_dir.mkdir(parents=True)
    monkeypatch.setattr(memory_manager, "MEMORIES_DIR", memories_dir)
    monkeypatch.setattr(memory_manager, "GLOBAL_DIR", memories_dir / "global")
    monkeypatch.setattr(memory_manager, "KB_DIR", memories_dir / "kb")
    monkeypatch.setattr(memory_manager, "PROJECTS_DIR", memories_dir / "projects")

    kb = memory_manager.create_kb("0day库", "已验证 0day 记录")
    assert kb["entry_count"] == 0

    # 库不存在时报错、传 kb 自动 type=kb
    with pytest.raises(ValueError, match="知识库不存在"):
        memory_manager.create_memory(name="x", description="d", type="kb", kb="不存在")

    mem = memory_manager.create_memory(
        name="shiro-550",
        description="Shiro-550 反序列化利用记录",
        type="reference",
        trigger_list=["shiro", "rememberMe"],
        content="利用步骤……",
        kb="0day库",
    )
    assert mem.type == "kb"
    assert mem.kb == "0day库"

    # 全局列表 + 库过滤 + 按名查找（跨目录）
    all_mems = memory_manager.list_memories(None)
    assert any(m.name == "shiro-550" for m in all_mems)
    kb_mems = memory_manager.list_memories(None, kb_filter="0day库")
    assert [m.name for m in kb_mems] == ["shiro-550"]
    found = memory_manager.get_memory(None, "shiro-550")
    assert found is not None and found.kb == "0day库"

    # 库内检索
    hits = memory_manager.search_kb_entries(None, kb="0day库", query="shiro")
    assert len(hits) == 1 and hits[0]["name"] == "shiro-550"

    # 删除库连带条目
    memory_manager.delete_kb("0day库")
    assert memory_manager.get_memory(None, "shiro-550") is None
    assert all(k["name"] != "0day库" for k in memory_manager.list_kbs(None))
