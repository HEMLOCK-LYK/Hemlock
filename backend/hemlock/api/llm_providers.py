"""LLM Provider 管理 API。

提供多组 API Key 的增删改查功能：
- 支持多个 LLM 服务商配置（OpenAI / DeepSeek / Anthropic / Gemini 等）
- 每组配置包含 name / provider / api_key / base_url / model
- 支持设置默认 Provider（同一时间仅一个）
- api_key 在响应中始终脱敏，不返回明文
"""
import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hemlock.core.database import get_db
from hemlock.models.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm-providers", tags=["llm-providers"])


# ===== Pydantic 请求 / 响应模型 =====
class ProviderCreateRequest(BaseModel):
    """创建 Provider 请求。"""
    name: str
    provider: str = "openai"
    protocol: str = "openai"            # 接口协议：openai（/v1/chat/completions）/ anthropic（/v1/messages）
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    context_window: int | None = None   # 上下文窗口（tokens），空=走兜底
    is_default: bool = False


class ProviderUpdateRequest(BaseModel):
    """更新 Provider 请求。所有字段可选，仅更新传入字段。"""
    name: str | None = None
    provider: str | None = None
    protocol: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    context_window: int | None = None
    is_default: bool | None = None


class ProviderResponse(BaseModel):
    """Provider 响应。api_key 已脱敏，不返回明文。"""
    id: str
    name: str
    provider: str
    protocol: str = "openai"
    base_url: str
    model: str
    context_window: int | None = None
    is_default: bool
    # 注意：不返回 api_key 明文，只返回 masked 版本
    api_key_masked: str


class ProviderListResponse(BaseModel):
    """Provider 列表响应。"""
    providers: list[ProviderResponse]
    total: int


# ===== 辅助函数 =====
def _mask_api_key(key: str) -> str:
    """脱敏 API Key：保留前 4 位和后 4 位，中间用 **** 替代。

    过短的 key（<=8 位）直接返回 ****，避免泄露。
    """
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def _to_response(provider: LLMProvider) -> ProviderResponse:
    """将 ORM 对象转换为响应模型（api_key 脱敏）。"""
    return ProviderResponse(
        id=provider.id,
        name=provider.name,
        provider=provider.provider,
        protocol=getattr(provider, "protocol", None) or "openai",
        base_url=provider.base_url,
        model=provider.model,
        context_window=provider.context_window,
        is_default=provider.is_default,
        api_key_masked=_mask_api_key(provider.api_key),
    )


def _clear_cache(provider_id: str | None = None) -> None:
    """清除 llm_client 中的 Provider 客户端缓存。

    在 Provider 创建/更新/删除时调用，确保客户端使用最新配置。
    """
    try:
        from hemlock.core.llm_client import clear_provider_cache
        clear_provider_cache(provider_id)
    except Exception as e:
        logger.warning(f"清除 Provider 缓存失败（非致命）: {e}")


def _unset_other_defaults(db: Session, exclude_id: str | None = None) -> None:
    """将除 exclude_id 外的所有 Provider 的 is_default 设为 False。"""
    query = db.query(LLMProvider).filter(LLMProvider.is_default == True)
    if exclude_id:
        query = query.filter(LLMProvider.id != exclude_id)
    for p in query.all():
        p.is_default = False


# ===== 路由 =====
@router.get("", response_model=ProviderListResponse)
async def list_providers(db: Session = Depends(get_db)):
    """列出所有 Provider。

    api_key 返回 masked 版本（前4后4，中间 ****），不返回明文。
    """
    providers = db.query(LLMProvider).order_by(LLMProvider.created_at).all()
    return ProviderListResponse(
        providers=[_to_response(p) for p in providers],
        total=len(providers),
    )


@router.post("", response_model=ProviderResponse, status_code=201)
async def create_provider(req: ProviderCreateRequest, db: Session = Depends(get_db)):
    """创建 Provider。

    如果 is_default=True，则自动将其他 Provider 的 is_default 设为 False。
    """
    provider = LLMProvider(
        id=str(uuid.uuid4()),
        name=req.name,
        provider=req.provider,
        protocol=req.protocol if req.protocol in ("openai", "anthropic") else "openai",
        api_key=req.api_key,
        base_url=req.base_url,
        model=req.model,
        context_window=req.context_window,
        is_default=req.is_default,
    )

    if req.is_default:
        _unset_other_defaults(db)

    db.add(provider)
    db.commit()
    db.refresh(provider)

    logger.info(f"创建 Provider: {provider.name} ({provider.provider}) | id={provider.id}")

    return _to_response(provider)


@router.put("/{provider_id}", response_model=ProviderResponse)
async def update_provider(
    provider_id: str,
    req: ProviderUpdateRequest,
    db: Session = Depends(get_db),
):
    """更新 Provider。

    仅更新传入的字段。如果 is_default 设为 True，则自动将其他 Provider 的
    is_default 设为 False。更新后清除该 Provider 的客户端缓存。
    """
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider 不存在: {provider_id}")

    update_data = req.model_dump(exclude_none=True)

    # 处理 is_default 互斥逻辑
    if update_data.get("is_default") is True:
        _unset_other_defaults(db, exclude_id=provider_id)

    for key, value in update_data.items():
        setattr(provider, key, value)

    db.commit()
    db.refresh(provider)

    # 清除该 Provider 的客户端缓存，使下次调用使用新配置
    _clear_cache(provider_id)

    logger.info(f"更新 Provider: {provider.name} | id={provider_id} | 字段: {list(update_data.keys())}")

    return _to_response(provider)


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(provider_id: str, db: Session = Depends(get_db)):
    """删除 Provider。

    如果删除的是默认 Provider，自动将剩余的第一个设为默认。
    删除后清除该 Provider 的客户端缓存。
    """
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider 不存在: {provider_id}")

    was_default = provider.is_default
    db.delete(provider)
    db.commit()

    # 如果删除的是默认 Provider，自动把第一个设为默认
    if was_default:
        first = (
            db.query(LLMProvider)
            .order_by(LLMProvider.created_at)
            .first()
        )
        if first:
            first.is_default = True
            db.commit()
            logger.info(f"默认 Provider 已删除，自动将 '{first.name}' 设为默认")

    # 清除该 Provider 的客户端缓存
    _clear_cache(provider_id)

    logger.info(f"删除 Provider: {provider.name} | id={provider_id}")


@router.post("/{provider_id}/set-default", response_model=ProviderResponse)
async def set_default_provider(provider_id: str, db: Session = Depends(get_db)):
    """设置指定 Provider 为默认。

    同时将其他 Provider 的 is_default 设为 False。
    """
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider 不存在: {provider_id}")

    _unset_other_defaults(db, exclude_id=provider_id)
    provider.is_default = True
    db.commit()
    db.refresh(provider)

    logger.info(f"设置默认 Provider: {provider.name} | id={provider_id}")

    return _to_response(provider)
