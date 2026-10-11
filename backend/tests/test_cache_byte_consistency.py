# -*- coding: utf-8 -*-
"""字节一致性回归测试：缓存安全性质钉死。

用户核心关切（2026-10-09）：head+tail 截断发生在上下文中是极其危险的缓存行为。
本项目的设计约束：截断/蒸馏只发生在工具结果进入历史的「插入时」，固化进
steps/messages 后字节不再变化；恢复重建走同一个确定性函数
_tool_result_to_llm_content，与插入时逐字节一致——前缀缓存因此可命中。

本测试钉死这条性质：
1. 同一 tool_result 多次调用 _tool_result_to_llm_content 结果逐字节一致（确定性）
2. steps JSON round-trip（落盘 → 读回）后重建 == 插入时序列化（核心性质）
3. read_file / get_flow_detail / run_command 超阈值不蒸馏（读回类工具被蒸馏 =
   Agent 白读再读，同一数据二次注入上下文，token + 缓存推进双重浪费）
4. send_request 的 body 预览（_FLOW_BODY_PREVIEW=8000）序列化后低于
   微压缩阈值（10000）→ 一次请求完整可见，无需二次读取
5. 走蒸馏分支的超大结果同样字节一致（蒸馏是确定性正则/切片，无 LLM）
"""
import json

from hemlock.core.context_manager import ToolOutputDistiller
from hemlock.services.agent_service import (
    _MICRO_COMPACT_THRESHOLD,
    _truncate_output,
    _tool_result_to_llm_content,
)


def _roundtrip(tool_name: str, tool_args: dict, tool_result: dict) -> tuple[str, str]:
    """模拟完整生命周期：插入时序列化 → steps JSON 落盘 → 读回 → 恢复重建。"""
    inserted = _tool_result_to_llm_content(tool_name, tool_args, tool_result)
    # steps 存的是完整原始 tool_result；json.dumps 落盘 → json.loads 读回
    stored = json.loads(json.dumps(tool_result, ensure_ascii=False, default=str))
    rebuilt = _tool_result_to_llm_content(tool_name, tool_args, stored)
    return inserted, rebuilt


# ===== 核心：round-trip 字节一致 =====

def test_run_command_roundtrip_byte_identical():
    """run_command 超长输出：截断插入 == steps round-trip 重建（逐字节）。"""
    stdout_full = "HEAD_MARKER " + "A" * 48000 + "TAIL_MARKER"
    tool_result = {
        "exit_code": 0,
        "stdout": _truncate_output(stdout_full),
        "stderr": "",
        "full_output_file": ".agent_out/1728469200_ab12cd34.log",
        "success": True,
    }
    inserted, rebuilt = _roundtrip("run_command", {"command": "dir /s"}, tool_result)
    assert inserted == rebuilt  # 缓存安全核心性质
    # head+tail 保留 + 省略标记 + 落盘引用
    assert "HEAD_MARKER" in inserted
    assert "TAIL_MARKER" in inserted
    assert "中间省略" in inserted
    assert ".agent_out/" in inserted
    assert len(inserted) > 30000  # 透传（NON_DISTILLABLE），未被蒸馏成一行


def test_send_request_preview_roundtrip_byte_identical():
    """send_request body 预览：插入 == round-trip 重建（逐字节）。"""
    tool_result = {
        "flow_id": "flw_byte1",
        "status": 200,
        "headers": "Content-Type: text/html\r\nSet-Cookie: PHPSESSID=abc" + "H" * 900,
        "body": "SQL_ERROR " + "B" * 7900,
        "duration_ms": 35,
        "resp_length": 120000,
        "flow_id_for_detail": "flw_byte1",
        "truncated": True,
        "hint": "响应已截断（仅预览）",
    }
    inserted, rebuilt = _roundtrip("send_request", {"url": "http://x/a?id=1"}, tool_result)
    assert inserted == rebuilt
    assert "SQL_ERROR" in inserted  # body 预览可见
    assert "flw_byte1" in inserted  # flow_id 引用保留


def test_distilled_send_request_roundtrip_byte_identical():
    """超大 send_request 结果走蒸馏分支：蒸馏同样确定性、round-trip 一致。"""
    tool_result = {
        "flow_id": "flw_huge",
        "status": 200,
        "headers": "Content-Type: application/json",
        "body": json.dumps({"items": [{"id": i, "data": "D" * 200} for i in range(200)]}),
        "resp_length": 999999,
        "truncated": True,
        "hint": "响应已截断（仅预览）",
    }
    # 前置确认确实走蒸馏分支（构造的输入超过微压缩阈值）
    raw = json.dumps(tool_result, ensure_ascii=False, default=str)
    assert len(raw) > _MICRO_COMPACT_THRESHOLD
    assert ToolOutputDistiller.should_distill("send_request", raw)

    inserted, rebuilt = _roundtrip("send_request", {"url": "http://x/list"}, tool_result)
    assert inserted == rebuilt
    assert len(inserted) < len(raw)  # 确实蒸馏了


def test_repeat_calls_deterministic():
    """同一 tool_result 连续多次序列化结果完全一致（无随机性/无时间戳/无 LLM）。"""
    tool_result = {"flow_id": "flw_d", "status": 200, "body": "X" * 9000, "truncated": True}
    outputs = {
        _tool_result_to_llm_content("send_session_request", {"url": "http://x/"}, tool_result)
        for _ in range(5)
    }
    assert len(outputs) == 1


# ===== 读回类工具不蒸馏（防止「白读再读」的二次注入）=====

def test_read_file_never_distilled():
    """read_file 大结果透传：读回全文的工具被蒸馏成一行 = Agent 白读再读。"""
    content = "import os\n" + "# line\n" * 3000
    tool_result = {
        "path": "src/app.py",
        "absolute_path": "/ws/src/app.py",
        "content": content,
        "size": len(content),
        "truncated": False,
    }
    raw = json.dumps(tool_result, ensure_ascii=False, default=str)
    assert len(raw) > _MICRO_COMPACT_THRESHOLD  # 前置：确实超阈值
    assert not ToolOutputDistiller.should_distill("read_file", raw)

    out = _tool_result_to_llm_content("read_file", {"path": "src/app.py"}, tool_result)
    assert out == raw  # 逐字节透传
    assert "import os" in out  # 内容完整可见


def test_get_flow_detail_never_distilled():
    """get_flow_detail 概览透传：自带每字段 10K 封顶，蒸馏反而抹掉概览。"""
    tool_result = {
        "flow_id": "flw_fd",
        "req_headers": "Host: x\r\n" + "Q" * 5000,
        "req_body": "",
        "resp_headers": "Content-Type: text/html",
        "resp_body": "VULN_PROOF " + "R" * 9000,
        "note": "更多内容用 read_flow_content 分页读取",
    }
    raw = json.dumps(tool_result, ensure_ascii=False, default=str)
    assert len(raw) > _MICRO_COMPACT_THRESHOLD
    assert not ToolOutputDistiller.should_distill("get_flow_detail", raw)

    out = _tool_result_to_llm_content("get_flow_detail", {"flow_id": "flw_fd"}, tool_result)
    assert out == raw
    assert "VULN_PROOF" in out


# ===== 阈值联动：body 预览一次可见，不逼二次读取 =====

def test_body_preview_below_micro_compact_threshold():
    """工具层截断后的典型 send_request 结果（body 8000）序列化后 < 微压缩阈值。

    联动约定：_FLOW_BODY_PREVIEW=8000 → JSON 序列化 ~9KB < 10000 → 透传。
    若两常量之一被单独改动导致本断言失败，说明联动被破坏——预览会再次被蒸馏，
    Agent 看不到完整预览就会二次读取（get_flow_detail/read_flow_content），
    同一数据注入两次：正是用户明确要求避免的浪费。
    """
    typical = {
        "flow_id": "flw_typical",
        "status": 200,
        "headers": "H" * 1000,       # _FLOW_HEADERS_PREVIEW 封顶
        "body": "B" * 8000,          # _FLOW_BODY_PREVIEW 封顶
        "duration_ms": 88,
        "resp_length": 500000,
        "flow_id_for_detail": "flw_typical",
        "truncated": True,
        "hint": "响应已截断（仅预览）。需要完整响应体时用 read_flow_content(flow_id, field=\"resp_body\") 分页读取；需要整体概览时用 get_flow_detail(flow_id)",
    }
    raw = json.dumps(typical, ensure_ascii=False, default=str)
    assert len(raw) < _MICRO_COMPACT_THRESHOLD
    out = _tool_result_to_llm_content("send_request", {"url": "http://x/"}, typical)
    assert out == raw  # 未蒸馏，完整透传


def test_truncate_output_deterministic():
    """_truncate_output 确定性：同一输入多次输出逐字节一致（插入时一次性固化）。"""
    text = "S" * 70000
    outs = {_truncate_output(text) for _ in range(3)}
    assert len(outs) == 1
    out = outs.pop()
    assert "中间省略" in out
    assert len(out) <= 30000 + 60  # head(18000) + tail(12000) + 提示行
