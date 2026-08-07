"""LLM 客户端：封装 OpenAI 兼容接口，支持云端 API。

特性：
- 自动重试（指数退避）：应对 429 限速 / 5xx 服务端错误
- 超时控制：防止长时间挂起
- 结构化错误：统一异常类型，便于上层处理
- Token 用量追踪：记录消耗，辅助成本控制
"""

import logging
import time
from typing import Any, Callable, TYPE_CHECKING

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


def _retry_api_call(fn: Any, operation: str = "LLM API") -> Any:
    """带指数退避的重试包装器。

    仅对可重试的错误（网络超时、限速、服务端 5xx）进行重试。
    对 4xx 客户端错误（如认证失败、参数错误）直接抛出。
    """
    last_error: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            return fn()
        except _RETRYABLE_ERRORS as e:
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
