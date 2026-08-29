"""LLM 客户端：封装 OpenAI 兼容接口，支持云端 API。

特性：
- 自动重试（指数退避）：应对 429 限速 / 5xx 服务端错误
- 超时控制：防止长时间挂起
- 结构化错误：统一异常类型，便于上层处理
- Token 用量追踪：记录消耗，辅助成本控制
"""

import json
import logging
import time
import uuid
from typing import Any, Callable, TYPE_CHECKING

import httpx
from openai import (
    OpenAI,
    APIError,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

from hemlock.core.config import settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from hemlock.models.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

# ===== 重试配置 =====
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # 基础延迟（秒）
_RETRY_MAX_DELAY = 30.0  # 最大延迟
_REQUEST_TIMEOUT = 120  # 请求超时（秒）

# 可重试的异常类型
_RETRYABLE_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

class _StopStream(Exception):
    """内部信号：流式请求被用户主动停止（用于重试包装器中提前退出）。"""


# Token 用量统计（进程级）
_token_usage: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "request_count": 0,
}

# Provider 客户端缓存：provider_id -> OpenAI 客户端
_provider_clients: dict[str, OpenAI] = {}


def get_client() -> OpenAI:
    """获取 LLM 客户端。支持 OpenAI / DeepSeek / 任何 OpenAI 兼容接口。"""
    return OpenAI(
        api_key=settings.llm_api_key or "sk-placeholder",
        base_url=settings.llm_base_url,
        timeout=_REQUEST_TIMEOUT,
        max_retries=0,  # 我们自己管理重试
    )


def get_client_for_provider(
    provider_id: str | None = None,
    db: "Session | None" = None,
) -> OpenAI:
    """获取指定 Provider 的 OpenAI 客户端。

    - provider_id 为 None：使用默认 Provider（数据库 is_default=True）
    - provider_id 指定：使用该 Provider
    - 数据库无 Provider 或 db 为 None：回退到 settings 中的配置

    Args:
        provider_id: Provider ID，为 None 时使用默认 Provider
        db: 数据库会话，为 None 时直接回退到配置文件

    Returns:
        OpenAI 客户端实例
    """
    if provider_id is None:
        # 查默认 provider
        if db:
            from hemlock.models.llm_provider import LLMProvider
            provider = db.query(LLMProvider).filter(LLMProvider.is_default == True).first()
            if provider:
                return _get_or_create_client(provider)
    else:
        if db:
            from hemlock.models.llm_provider import LLMProvider
            provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
            if provider:
                return _get_or_create_client(provider)

    # 回退到配置文件
    return get_client()


def _get_or_create_client(provider: "LLMProvider") -> OpenAI:
    """获取或创建 Provider 的客户端（带缓存）。

    使用 provider_id 作为缓存键，避免重复创建客户端。
    缓存的客户端在 Provider 更新/删除时通过 clear_provider_cache 清除。

    Args:
        provider: LLMProvider ORM 对象

    Returns:
        OpenAI 客户端实例
    """
    if provider.id in _provider_clients:
        return _provider_clients[provider.id]
    client = OpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        timeout=_REQUEST_TIMEOUT,
        max_retries=0,  # 我们自己管理重试
    )
    _provider_clients[provider.id] = client
    return client


def clear_provider_cache(provider_id: str | None = None) -> None:
    """清除客户端缓存（Provider 更新/删除时调用）。

    - provider_id 指定：仅清除该 Provider 的缓存
    - provider_id 为 None：清除所有缓存

    Args:
        provider_id: Provider ID，为 None 时清除全部缓存
    """
    if provider_id:
        _provider_clients.pop(provider_id, None)
    else:
        _provider_clients.clear()


def _resolve_client_and_model(
    provider_id: str | None = None,
    db: "Session | None" = None,
    model: str | None = None,
) -> tuple[OpenAI, str]:
    """解析客户端和模型名。

    优先级：显式 model 参数 > Provider 配置的 model > settings.llm_model

    - provider_id 不为 None 或 db 不为 None 时，使用 get_client_for_provider
    - 否则回退到 get_client()（settings 配置）

    Args:
        provider_id: Provider ID，为 None 时使用默认 Provider
        db: 数据库会话
        model: 显式指定的模型名，优先级最高

    Returns:
        (OpenAI 客户端, 模型名) 元组
    """
    use_model = model

    if provider_id is not None or db is not None:
        client = get_client_for_provider(provider_id, db)
        # 尝试从 Provider 获取模型（如果未显式指定）
        if use_model is None and db is not None:
            from hemlock.models.llm_provider import LLMProvider
            if provider_id is not None:
                p = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
            else:
                p = db.query(LLMProvider).filter(LLMProvider.is_default == True).first()
            if p:
                use_model = p.model
    else:
        client = get_client()

    if use_model is None:
        use_model = settings.llm_model

    return client, use_model


def _apply_cache_control(messages: list[dict], model: str | None) -> list[dict]:
    """为 Claude 模型注入 cache_control（Anthropic 原生 prompt 缓存标记）。

    仅当 settings.llm_cache_control_enabled=True 且模型为 claude- 前缀时生效：
    将 system 消息 content 转为 Anthropic 内容块格式并附加 cache_control，
    使 Claude 缓存 system prompt，命中"前缀缓存"降低重复 token 计费。

    只对第一条 system 消息打标——配合 system 的「稳定段 + 动态段」拆分，
    缓存断点落在稳定段末尾，技能/记忆等动态段在断点之后，变化不影响缓存命中。

    DeepSeek/OpenAI/Kimi 等使用自动前缀缓存，不接受此字段，默认关闭不受影响。
    """
    if not getattr(settings, "llm_cache_control_enabled", False):
        return messages
    if not model or not model.startswith("claude-"):
        return messages

    result = list(messages)
    for i, m in enumerate(result):
        if m.get("role") == "system" and isinstance(m.get("content"), str) and m["content"]:
            result[i] = {
                "role": "system",
                "content": [{
                    "type": "text",
                    "text": m["content"],
                    "cache_control": {"type": "ephemeral"},
                }],
            }
            break
    return result


# ===== Anthropic 原生协议（/v1/messages）适配 =====
#
# 部分中转站只暴露 Anthropic 原生接口（/v1/messages），不提供 OpenAI 兼容层。
# Provider 的 protocol 字段为 "anthropic" 时，走本节适配：内部消息仍是 OpenAI
# 格式（agent 全链路不变），在边界处转换为 Anthropic 格式并解析回 OpenAI 格式。

_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_MAX_TOKENS = 8192  # Anthropic Messages API 必填的输出上限

# 共享连接池（无认证头，鉴权按请求携带，避免 Provider 更新后连接池残留旧 key）
_anthropic_http = httpx.Client(timeout=_REQUEST_TIMEOUT)


class _AnthropicError(Exception):
    """Anthropic 接口 4xx 错误（不可重试）。"""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code


class _AnthropicRetryableError(_AnthropicError):
    """Anthropic 接口 429/5xx 错误（可重试）。"""


def _normalize_anthropic_base(base_url: str) -> str:
    """规整 Anthropic Base URL：去尾斜杠与 /v1 后缀，拼路径时统一补 /v1/messages。

    用户可能填 https://relay.com、https://relay.com/ 或 https://relay.com/v1，
    三者最终都请求 https://relay.com/v1/messages。
    """
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base


def _resolve_protocol(provider_id: str | None, db: "Session | None") -> str:
    """解析 Provider 的接口协议（openai / anthropic）。无 Provider 时默认 openai。"""
    if db is None:
        return "openai"
    from hemlock.models.llm_provider import LLMProvider
    if provider_id is not None:
        p = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    else:
        p = db.query(LLMProvider).filter(LLMProvider.is_default == True).first()
    if p is None:
        return "openai"
    return getattr(p, "protocol", None) or "openai"


def _resolve_anthropic_config(
    provider_id: str | None,
    db: "Session | None",
    model: str | None,
) -> dict[str, str]:
    """解析 Anthropic 协议请求所需的 base_url / api_key / model。

    优先级同 OpenAI 路径：Provider 配置 > settings 兜底；显式 model 参数最高。
    """
    base_url = settings.llm_base_url
    api_key = settings.llm_api_key or ""
    use_model = model

    if db is not None:
        from hemlock.models.llm_provider import LLMProvider
        if provider_id is not None:
            p = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
        else:
            p = db.query(LLMProvider).filter(LLMProvider.is_default == True).first()
        if p:
            base_url = p.base_url or base_url
            api_key = p.api_key
            if use_model is None:
                use_model = p.model

    if use_model is None:
        use_model = settings.llm_model

    return {"base_url": base_url, "api_key": api_key, "model": use_model or ""}


def _openai_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    """OpenAI tools 格式 -> Anthropic tools 格式。

    OpenAI:  {"type": "function", "function": {"name", "description", "parameters"}}
    Anthropic: {"name", "description", "input_schema"}
    """
    result = []
    for t in tools:
        fn = t.get("function", {}) if isinstance(t, dict) else {}
        result.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return result


def _anthropic_system_blocks(system_text: str) -> str | list[dict]:
    """构造 system 参数。开启 cache_control 时用内容块格式打缓存标记。"""
    if not getattr(settings, "llm_cache_control_enabled", False):
        return system_text
    return [{
        "type": "text",
        "text": system_text,
        "cache_control": {"type": "ephemeral"},
    }]


def _messages_to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI 消息序列 -> (system 文本, Anthropic messages)。

    转换规则：
    - system -> system 参数（多条合并）
    - assistant.tool_calls -> tool_use 内容块（arguments JSON 字符串解析回对象）
    - role=tool -> user 消息中的 tool_result 块
    - 相邻同角色消息合并（Anthropic 要求 user/assistant 交替）
    """
    system_parts: list[str] = []
    out: list[dict] = []

    def _append(role: str, content) -> None:
        # 合并相邻同角色消息
        if out and out[-1]["role"] == role:
            prev = out[-1]["content"]
            if isinstance(prev, list) and isinstance(content, list):
                prev.extend(content)
            elif isinstance(prev, list):
                prev.append({"type": "text", "text": str(content)})
            elif isinstance(content, list):
                out[-1]["content"] = [{"type": "text", "text": prev}, *content]
            else:
                out[-1]["content"] = f"{prev}\n{content}"
        else:
            out.append({"role": role, "content": content})

    for m in messages:
        role = m.get("role")
        content = m.get("content")

        if role == "system":
            if isinstance(content, list):
                # cache_control 注入后的块格式
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        system_parts.append(blk.get("text", ""))
            elif content:
                system_parts.append(content)

        elif role == "tool":
            _append("user", [{
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id", ""),
                "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
            }])

        elif role == "assistant":
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                raw_args = fn.get("arguments") or "{}"
                try:
                    input_obj = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    input_obj = {"_raw": str(raw_args)}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:16]}",
                    "name": fn.get("name", ""),
                    "input": input_obj,
                })
            if blocks:
                _append("assistant", blocks)

        else:  # user
            if isinstance(content, list):
                texts = [
                    blk.get("text", "") if isinstance(blk, dict) else str(blk)
                    for blk in content
                ]
                content = "\n".join(t for t in texts if t)
            if content:
                _append("user", content)

    # Anthropic 要求首条消息为 user
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": "（继续）"})

    return "\n".join(system_parts), out


def _anthropic_request(config: dict[str, str], body: dict) -> dict:
    """发送 Anthropic Messages API 请求，返回 JSON 响应。

    429/5xx 抛 _AnthropicRetryableError（可重试），其余 4xx 抛 _AnthropicError。
    """
    url = _normalize_anthropic_base(config["base_url"]) + "/v1/messages"
    headers = {
        "x-api-key": config["api_key"],
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    try:
        resp = _anthropic_http.post(url, json=body, headers=headers)
    except httpx.TimeoutException as e:
        raise APITimeoutError(e)  # 复用超时重试与错误信息
    except httpx.TransportError as e:
        raise APIConnectionError(e)

    if resp.status_code >= 400:
        try:
            err_detail = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            err_detail = resp.text
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _AnthropicRetryableError(resp.status_code, err_detail)
        raise _AnthropicError(resp.status_code, err_detail)

    return resp.json()


def _track_usage_anthropic(usage: dict | None) -> None:
    """追踪 Anthropic 格式的 Token 用量。"""
    if not usage:
        return
    try:
        _token_usage["prompt_tokens"] += usage.get("input_tokens", 0) or 0
        _token_usage["completion_tokens"] += usage.get("output_tokens", 0) or 0
        _token_usage["total_tokens"] += (
            (usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0)
        )
        _token_usage["request_count"] += 1
    except Exception:
        pass


def _anthropic_parse_response(data: dict) -> dict[str, Any]:
    """Anthropic 响应 -> OpenAI 风格返回值（content / tool_calls / reasoning_content）。"""
    content_parts: list[str] = []
    reasoning = ""
    tool_calls: list[dict] = []

    for blk in data.get("content", []):
        btype = blk.get("type")
        if btype == "text":
            content_parts.append(blk.get("text", ""))
        elif btype == "thinking":
            reasoning += blk.get("thinking", "")
        elif btype == "tool_use":
            tool_calls.append({
                "id": blk.get("id", ""),
                "type": "function",
                "function": {
                    "name": blk.get("name", ""),
                    "arguments": json.dumps(blk.get("input", {}), ensure_ascii=False),
                },
            })

    _track_usage_anthropic(data.get("usage"))

    return {
        "content": "".join(content_parts),
        "tool_calls": tool_calls or None,
        "reasoning_content": reasoning,
    }


def _anthropic_chat(
    config: dict[str, str],
    messages: list[dict],
    tools: list[dict] | None,
    temperature: float,
) -> dict[str, Any]:
    """Anthropic 协议非流式对话。返回结构与 chat() 一致。"""
    system, msgs = _messages_to_anthropic(messages)
    body: dict[str, Any] = {
        "model": config["model"],
        "max_tokens": _ANTHROPIC_MAX_TOKENS,
        "messages": msgs,
        "temperature": temperature,
    }
    if system:
        body["system"] = _anthropic_system_blocks(system)
    if tools:
        body["tools"] = _openai_tools_to_anthropic(tools)

    resp = _retry_api_call(
        lambda: _anthropic_request(config, body), "chat(anthropic)", _ANTHROPIC_RETRYABLE
    )
    return _anthropic_parse_response(resp)


def _anthropic_chat_json(
    config: dict[str, str],
    messages: list[dict],
    temperature: float,
) -> str:
    """Anthropic 协议 JSON 对话。Anthropic 无 response_format，用 system 提示约束。"""
    msgs = [{"role": "system", "content": "只输出合法 JSON，不要输出任何其他内容。"}, *messages]
    result = _anthropic_chat(config, msgs, None, temperature)
    return result["content"] or "{}"


def _anthropic_stream(
    config: dict[str, str],
    messages: list[dict],
    tools: list[dict] | None,
    temperature: float,
    on_reasoning: "Callable[[str], None] | None" = None,
    on_content: "Callable[[str], None] | None" = None,
    should_stop: "Callable[[], bool] | None" = None,
) -> dict[str, Any]:
    """Anthropic 协议流式对话（SSE）。返回结构与 chat_stream() 一致。"""
    system, msgs = _messages_to_anthropic(messages)
    body: dict[str, Any] = {
        "model": config["model"],
        "max_tokens": _ANTHROPIC_MAX_TOKENS,
        "messages": msgs,
        "temperature": temperature,
        "stream": True,
    }
    if system:
        body["system"] = _anthropic_system_blocks(system)
    if tools:
        body["tools"] = _openai_tools_to_anthropic(tools)

    url = _normalize_anthropic_base(config["base_url"]) + "/v1/messages"
    headers = {
        "x-api-key": config["api_key"],
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    def _call():
        # httpx.stream 返回惰性上下文管理器，真正发请求在 __enter__ 时——
        # 因此这里手动 enter 并检查状态码，让连接错误与 429/5xx 落在重试范围内
        if should_stop is not None and should_stop():
            raise _StopStream()
        ctx = _anthropic_http.stream("POST", url, json=body, headers=headers)
        try:
            resp = ctx.__enter__()
            if resp.status_code >= 400:
                resp.read()
                try:
                    err_detail = resp.json().get("error", {}).get("message", resp.text)
                except Exception:
                    err_detail = resp.text
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise _AnthropicRetryableError(resp.status_code, err_detail)
                raise _AnthropicError(resp.status_code, err_detail)
            return ctx, resp
        except BaseException:
            ctx.__exit__(type(None), None, None)
            raise

    try:
        stream_ctx, resp = _retry_api_call(_call, "chat_stream(anthropic)", _ANTHROPIC_RETRYABLE)
    except _StopStream:
        return {"content": "", "tool_calls": None, "reasoning_content": ""}

    full_reasoning = ""
    full_content = ""
    # block_index -> {"id", "name", "arguments"}（tool_use 块按 index 累积）
    tool_blocks: dict[int, dict] = []
    input_tokens = 0
    output_tokens = 0

    try:
        try:
            for line in resp.iter_lines():
                # 用户停止：立即中断流式读取
                if should_stop is not None and should_stop():
                    break
                if not line or not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[len("data:"):].strip())
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")
                if etype == "message_start":
                    usage = event.get("message", {}).get("usage", {})
                    input_tokens = usage.get("input_tokens", 0) or 0

                elif etype == "content_block_start":
                    blk = event.get("content_block", {})
                    if blk.get("type") == "tool_use":
                        tool_blocks[event.get("index", 0)] = {
                            "id": blk.get("id", ""),
                            "name": blk.get("name", ""),
                            "arguments": "",
                        }

                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    dtype = delta.get("type")
                    if dtype == "text_delta":
                        piece = delta.get("text", "")
                        if piece:
                            full_content += piece
                            if on_content:
                                try:
                                    on_content(piece)
                                except Exception:
                                    pass
                    elif dtype == "thinking_delta":
                        piece = delta.get("thinking", "")
                        if piece:
                            full_reasoning += piece
                            if on_reasoning:
                                try:
                                    on_reasoning(piece)
                                except Exception:
                                    pass
                    elif dtype == "input_json_delta":
                        idx = event.get("index", 0)
                        if idx in tool_blocks:
                            tool_blocks[idx]["arguments"] += delta.get("partial_json", "")

                elif etype == "message_delta":
                    usage = event.get("usage", {})
                    output_tokens = usage.get("output_tokens", 0) or 0

                elif etype == "error":
                    err = event.get("error", {})
                    raise _AnthropicError(500, err.get("message", "stream error"))
        finally:
            # 关闭流式连接（等价于 with 块退出）
            stream_ctx.__exit__(type(None), None, None)
    except httpx.TimeoutException as e:
        raise APITimeoutError(e)
    except httpx.TransportError as e:
        raise APIConnectionError(e)

    _token_usage["prompt_tokens"] += input_tokens
    _token_usage["completion_tokens"] += output_tokens
    _token_usage["total_tokens"] += input_tokens + output_tokens
    _token_usage["request_count"] += 1

    serialized_tool_calls = None
    if tool_blocks:
        serialized_tool_calls = [
            {
                "id": blk["id"],
                "type": "function",
                "function": {
                    "name": blk["name"],
                    "arguments": blk["arguments"] or "{}",
                },
            }
            for _, blk in sorted(tool_blocks.items())
        ]

    return {
        "content": full_content,
        "tool_calls": serialized_tool_calls,
        "reasoning_content": full_reasoning,
    }


_ANTHROPIC_RETRYABLE = (
    _AnthropicRetryableError,
    httpx.TimeoutException,
    httpx.TransportError,
)


def _retry_api_call(fn: Any, operation: str = "LLM API", retryable: tuple = _RETRYABLE_ERRORS) -> Any:
    """带指数退避的重试包装器。

    仅对可重试的错误（网络超时、限速、服务端 5xx）进行重试。
    对 4xx 客户端错误（如认证失败、参数错误）直接抛出。

    retryable: 可重试的异常类型元组。OpenAI 路径用默认值（SDK 异常），
    Anthropic 路径传入自定义异常元组。
    """
    last_error: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            return fn()
        except retryable as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                delay = min(_RETRY_BASE_DELAY * (2 ** attempt), _RETRY_MAX_DELAY)
                logger.warning(
                    f"{operation} 第 {attempt + 1} 次重试（{delay:.1f}s 后）: {type(e).__name__}: {e}"
                )
                time.sleep(delay)
            else:
                logger.error(f"{operation} 重试 {_MAX_RETRIES} 次后仍失败: {e}")
        except APIError as e:
            # 4xx 错误不重试
            logger.error(f"{operation} 不可重试错误 ({getattr(e, 'status_code', '?')}): {e}")
            raise
        except Exception as e:
            logger.error(f"{operation} 未知错误: {type(e).__name__}: {e}")
            raise

    # 重试耗尽
    raise last_error  # type: ignore[misc]


def _track_usage(resp: Any) -> None:
    """追踪 Token 用量。"""
    try:
        usage = resp.usage
        if usage:
            _token_usage["prompt_tokens"] += usage.prompt_tokens or 0
            _token_usage["completion_tokens"] += usage.completion_tokens or 0
            _token_usage["total_tokens"] += usage.total_tokens or 0
            _token_usage["request_count"] += 1
    except Exception:
        pass  # 用量追踪不影响主流程


def get_token_usage() -> dict[str, int]:
    """获取当前 Token 用量统计。"""
    return dict(_token_usage)


def reset_token_usage() -> None:
    """重置 Token 用量统计。"""
    for key in _token_usage:
        _token_usage[key] = 0


def chat(
    messages: list[dict[str, str]],
    tools: list[dict] | None = None,
    temperature: float = 0.2,
    model: str | None = None,
    provider_id: str | None = None,
    db: "Session | None" = None,
) -> dict[str, Any]:
    """调用 LLM 对话。

    返回: {"content": str, "tool_calls": list[dict] | None}
    tool_calls 已序列化为普通字典，便于 JSON 处理。

    参数 model 可指定使用的模型，为 None 时使用 settings.llm_model。
    参数 provider_id 可指定使用的 Provider ID，不为 None 时使用该 Provider 的客户端。
    参数 db 为数据库会话，传入时可从数据库查询 Provider 配置。
    自动重试网络错误和限速，4xx 错误直接抛出。
    """
    # Anthropic 原生协议（/v1/messages）走独立适配
    if _resolve_protocol(provider_id, db) == "anthropic":
        config = _resolve_anthropic_config(provider_id, db, model)
        return _anthropic_chat(config, messages, tools, temperature)

    client, use_model = _resolve_client_and_model(provider_id, db, model)
    messages = _apply_cache_control(messages, use_model)
    kwargs: dict[str, Any] = {
        "model": use_model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    def _call():
        return client.chat.completions.create(**kwargs)

    resp = _retry_api_call(_call, "chat")

    _track_usage(resp)

    msg = resp.choices[0].message

    # 序列化 tool_calls 为普通字典
    raw_tool_calls = msg.tool_calls
    serialized_tool_calls = None
    if raw_tool_calls:
        serialized_tool_calls = []
        for tc in raw_tool_calls:
            serialized_tool_calls.append({
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            })

    # 提取推理内容（DeepSeek-v4 思考模式 / Qwen-QwQ 等模型的思考链条）
    reasoning_content = getattr(msg, "reasoning_content", None) or ""

    return {
        "content": msg.content or "",
        "tool_calls": serialized_tool_calls,
        "reasoning_content": reasoning_content,
    }


def chat_json(
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    model: str | None = None,
    provider_id: str | None = None,
    db: "Session | None" = None,
) -> str:
    """调用 LLM 并要求 JSON 格式输出。

    自动重试网络错误和限速。
    参数 model 可指定使用的模型，为 None 时使用 settings.llm_model。
    参数 provider_id 可指定使用的 Provider ID，不为 None 时使用该 Provider 的客户端。
    参数 db 为数据库会话，传入时可从数据库查询 Provider 配置。
    """
    # Anthropic 原生协议（/v1/messages）走独立适配
    if _resolve_protocol(provider_id, db) == "anthropic":
        config = _resolve_anthropic_config(provider_id, db, model)
        return _anthropic_chat_json(config, messages, temperature)

    client, use_model = _resolve_client_and_model(provider_id, db, model)
    messages = _apply_cache_control(messages, use_model)

    def _call():
        return client.chat.completions.create(
            model=use_model,
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )

    resp = _retry_api_call(_call, "chat_json")

    _track_usage(resp)

    return resp.choices[0].message.content or "{}"


def chat_with_retry(
    messages: list[dict[str, str]],
    tools: list[dict] | None = None,
    temperature: float = 0.2,
    max_retries: int = 2,
    model: str | None = None,
    provider_id: str | None = None,
    db: "Session | None" = None,
    should_stop: "Callable[[], bool] | None" = None,
) -> dict[str, Any] | None:
    """带额外重试的 chat，用于 Agent 主循环中 LLM 返回异常内容时的恢复。

    与 chat() 不同：如果 LLM 返回空内容且无 tool_calls，会重试。
    返回 None 表示重试耗尽仍无法获得有效响应。
    参数 model 可指定使用的模型，为 None 时使用 settings.llm_model。
    参数 provider_id 可指定使用的 Provider ID，不为 None 时使用该 Provider 的客户端。
    参数 db 为数据库会话，传入时可从数据库查询 Provider 配置。
    参数 should_stop: 每次请求前（含重试间隔）轮询的停止回调。返回 True 时
        立即放弃本次调用并返回 None，用于用户点停止时不再发起新的 LLM 请求。
    """
    for attempt in range(max_retries + 1):
        if should_stop is not None and should_stop():
            logger.info("chat_with_retry 已停止（should_stop）")
            return None
        try:
            result = chat(
                messages,
                tools=tools,
                temperature=temperature,
                model=model,
                provider_id=provider_id,
                db=db,
            )
            content = result.get("content", "")
            tool_calls = result.get("tool_calls")
            reasoning_content = result.get("reasoning_content", "")

            # 有效响应：有内容、有工具调用、或有推理内容
            if content.strip() or tool_calls or reasoning_content.strip():
                return result

            if attempt < max_retries:
                logger.warning(f"LLM 返回空响应，重试 {attempt + 1}/{max_retries}")
                # 在消息末尾追加提示
                messages.append({
                    "role": "user",
                    "content": "请继续执行任务。如果你已完成所有测试，请回复 TASK_COMPLETE 并总结发现。",
                })
            else:
                logger.error("LLM 多次返回空响应，放弃重试")
        except Exception as e:
            logger.error(f"chat_with_retry 第 {attempt + 1} 次失败: {e}")
            if attempt >= max_retries:
                raise

    return None


def chat_stream(
    messages: list[dict[str, str]],
    tools: list[dict] | None = None,
    temperature: float = 0.2,
    model: str | None = None,
    provider_id: str | None = None,
    db: "Session | None" = None,
    on_reasoning: "Callable[[str], None] | None" = None,
    on_content: "Callable[[str], None] | None" = None,
    should_stop: "Callable[[], bool] | None" = None,
) -> dict[str, Any]:
    """流式调用 LLM 对话，支持实时回调推理内容和最终回复。

    通过 stream=True 模式调用 API，逐 chunk 接收响应。
    DeepSeek-v4-flash 思考模式等模型会先输出 reasoning_content（思考链），
    再输出 content（最终回复）。

    回调函数在同步上下文中调用，event_bus.publish 也是同步的，安全。

    Args:
        on_reasoning: 收到 reasoning_content 增量时调用
        on_content: 收到 content 增量时调用
        should_stop: 逐 chunk 检查的停止回调。返回 True 时立即中断流式读取
            （用于用户点停止时立刻终止 LLM 思考，不再等生成完毕）。
            在同步线程中被轮询，需保证线程安全（读布尔标志即可）。

    Returns:
        {"content": str, "tool_calls": list[dict] | None, "reasoning_content": str}
    """
    # 发起请求前已请求停止：直接返回空结果（不发起无意义的调用）
    if should_stop is not None and should_stop():
        return {"content": "", "tool_calls": None, "reasoning_content": ""}

    # Anthropic 原生协议（/v1/messages）走独立适配
    if _resolve_protocol(provider_id, db) == "anthropic":
        config = _resolve_anthropic_config(provider_id, db, model)
        return _anthropic_stream(
            config, messages, tools, temperature,
            on_reasoning=on_reasoning, on_content=on_content, should_stop=should_stop,
        )

    client, use_model = _resolve_client_and_model(provider_id, db, model)
    messages = _apply_cache_control(messages, use_model)
    kwargs: dict[str, Any] = {
        "model": use_model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    def _call():
        # 重试间隔内也可能被停止，避免停止后还发起请求
        if should_stop is not None and should_stop():
            raise _StopStream()
        return client.chat.completions.create(**kwargs)

    try:
        resp = _retry_api_call(_call, "chat_stream")
    except _StopStream:
        return {"content": "", "tool_calls": None, "reasoning_content": ""}

    full_reasoning = ""
    full_content = ""
    tool_calls_raw: list[Any] = []

    for chunk in resp:
        # 用户停止：立即中断流式读取（下一个 chunk 边界，通常在百毫秒内）
        if should_stop is not None and should_stop():
            break
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        # 推理内容增量（思考链）
        reasoning_delta = getattr(delta, "reasoning_content", None)
        if reasoning_delta:
            full_reasoning += reasoning_delta
            if on_reasoning:
                try:
                    on_reasoning(reasoning_delta)
                except Exception:
                    pass

        # 最终回复增量
        content_delta = delta.content
        if content_delta:
            full_content += content_delta
            if on_content:
                try:
                    on_content(content_delta)
                except Exception:
                    pass

        # 工具调用增量（累积）
        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                while len(tool_calls_raw) <= idx:
                    tool_calls_raw.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if tc.id:
                    tool_calls_raw[idx]["id"] = tc.id
                if tc.type:
                    tool_calls_raw[idx]["type"] = tc.type
                if tc.function:
                    if tc.function.name:
                        tool_calls_raw[idx]["function"]["name"] += tc.function.name
                    if tc.function.arguments:
                        tool_calls_raw[idx]["function"]["arguments"] += tc.function.arguments

    _track_usage(resp)  # type: ignore[arg-type]

    serialized_tool_calls = tool_calls_raw if tool_calls_raw else None

    return {
        "content": full_content,
        "tool_calls": serialized_tool_calls,
        "reasoning_content": full_reasoning,
    }


def chat_stream_with_retry(
    messages: list[dict[str, str]],
    tools: list[dict] | None = None,
    temperature: float = 0.2,
    max_retries: int = 2,
    model: str | None = None,
    provider_id: str | None = None,
    db: "Session | None" = None,
    on_reasoning: "Callable[[str], None] | None" = None,
    on_content: "Callable[[str], None] | None" = None,
    should_stop: "Callable[[], bool] | None" = None,
) -> dict[str, Any] | None:
    """带额外重试的 chat_stream，用于 Agent 主循环。

    与 chat_stream() 不同：如果 LLM 返回空内容且无 tool_calls，会重试。
    返回 None 表示重试耗尽仍无法获得有效响应。

    should_stop: 逐 chunk 检查的停止回调，用户点停止后立即中断。
    """
    for attempt in range(max_retries + 1):
        # 已停止则不再发起下一次重试
        if should_stop is not None and should_stop():
            return None
        try:
            # 只在第一次尝试时推送 reasoning 增量，避免重试导致重复推送思考链
            # 重试时 on_reasoning=None，reasoning_content 仍会累积在返回值中
            # 前端通过 thinking_done 事件获取完整内容覆盖流式部分
            attempt_on_reasoning = on_reasoning if attempt == 0 else None
            result = chat_stream(
                messages,
                tools=tools,
                temperature=temperature,
                model=model,
                provider_id=provider_id,
                db=db,
                on_reasoning=attempt_on_reasoning,
                on_content=on_content,
                should_stop=should_stop,
            )
            content = result.get("content", "")
            tool_calls = result.get("tool_calls")
            reasoning_content = result.get("reasoning_content", "")

            if content.strip() or tool_calls or reasoning_content.strip():
                return result

            if attempt < max_retries:
                logger.warning(f"LLM 返回空响应，重试 {attempt + 1}/{max_retries}")
                messages.append({
                    "role": "user",
                    "content": "请继续执行任务。如果你已完成所有测试，请回复 TASK_COMPLETE 并总结发现。",
                })
            else:
                logger.error("LLM 多次返回空响应，放弃重试")
        except Exception as e:
            logger.error(f"chat_stream_with_retry 第 {attempt + 1} 次失败: {e}")
            if attempt >= max_retries:
                raise

    return None
