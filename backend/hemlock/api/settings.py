"""设置管理 API 路由。

提供 LLM Provider、API Key、代理端口等配置的查询与更新，
设置持久化到 ~/.hemlock/settings.json，跨重启保留。

重要：LLM 相关字段（provider / model / base_url / api_key）优先从数据库
默认 Provider 读取，确保系统配置页面展示的是实际调用时使用的模型。
"""

import json
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hemlock.core.config import settings, get_data_dir
from hemlock.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])

# ===== 设置文件持久化 =====
SETTINGS_DIR = get_data_dir()
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

# 允许通过 API 配置的键
CONFIGURABLE_KEYS = [
    "llm_provider",
    "llm_api_key",
    "llm_base_url",
    "llm_model",
    "llm_model_light",
    "llm_model_powerful",
    "llm_provider_light",
    "llm_provider_powerful",
    "model_routing_enabled",
    "proxy_port",
    "proxy_host",
    "upstream_proxy",
    "api_port",
    "auto_pentest",
    "src_hunting",
    "llm_assisted_verify_enabled",
    "sensitive_op_confirm",
    "browser_headed",
    "compact_limit_tokens",
]

# ===== 预设服务商（仅提供 base_url 快捷填充，模型由用户自行输入）=====
PROVIDER_PRESETS = {
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "base_url": "https://api.anthropic.com/v1",
    },
    "qwen": {
        "name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    },
    "moonshot": {
        "name": "Moonshot Kimi",
        "base_url": "https://api.moonshot.cn/v1",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
    "baichuan": {
        "name": "百川",
        "base_url": "https://api.baichuan-ai.com/v1",
    },
    "ollama": {
        "name": "Ollama (本地)",
        "base_url": "http://localhost:11434/v1",
    },
    "custom": {
        "name": "自定义",
        "base_url": "",
    },
}


# ===== Pydantic 模型 =====
class SettingsResponse(BaseModel):
    """设置查询响应。llm_api_key 已脱敏。"""

    llm_provider: str
    llm_api_key: str
    has_api_key: bool
    llm_base_url: str
    llm_model: str
    llm_model_light: str
    llm_model_powerful: str
    llm_provider_light: str
    llm_provider_powerful: str
    model_routing_enabled: bool
    proxy_port: int
    proxy_host: str
    upstream_proxy: str
    api_port: int
    auto_pentest: bool
    src_hunting: bool
    llm_assisted_verify_enabled: bool
    sensitive_op_confirm: bool
    browser_headed: bool
    compact_limit_tokens: int


class SettingsUpdateRequest(BaseModel):
    """设置更新请求。所有字段可选，仅更新传入字段。"""

    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_model_light: str | None = None
    llm_model_powerful: str | None = None
    llm_provider_light: str | None = None
    llm_provider_powerful: str | None = None
    model_routing_enabled: bool | None = None
    proxy_port: int | None = None
    proxy_host: str | None = None
    upstream_proxy: str | None = None
    api_port: int | None = None
    auto_pentest: bool | None = None
    src_hunting: bool | None = None
    llm_assisted_verify_enabled: bool | None = None
    sensitive_op_confirm: bool | None = None
    browser_headed: bool | None = None
    compact_limit_tokens: int | None = None


class ProviderInfo(BaseModel):
    """单个 provider 信息。"""

    name: str
    base_url: str


class ProvidersResponse(BaseModel):
    """provider 预设列表响应。"""

    providers: dict[str, ProviderInfo]


# ===== 辅助函数 =====
def _mask_api_key(key: str) -> str:
    """脱敏 API Key：保留前后各 4 位，中间用 **** 替代。

    过短的 key（<=8 位）直接返回 ****，避免泄露。
    """
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def _load_file_settings() -> dict:
    """从 ~/.hemlock/settings.json 加载持久化设置。"""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # 仅保留可配置键，过滤未知字段
            return {k: v for k, v in data.items() if k in CONFIGURABLE_KEYS}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"读取设置文件失败: {e}")
    return {}


def _save_file_settings(data: dict) -> None:
    """保存设置到 ~/.hemlock/settings.json。"""
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _get_merged_settings() -> dict:
    """合并当前 settings 单例与文件持久化值（文件值优先）。

    读取顺序：先取 settings 单例当前值，再用文件中的持久化值覆盖。
    """
    merged = {k: getattr(settings, k) for k in CONFIGURABLE_KEYS}
    file_data = _load_file_settings()
    merged.update(file_data)
    return merged


def _get_default_provider_info(db: Session) -> dict | None:
    """从数据库查询默认 LLMProvider，返回其配置信息。

    实际 LLM 调用通过 get_client_for_provider() 使用数据库默认 Provider，
    因此系统配置页面也应展示数据库中的配置，而非 .env 文件中的回退值。

    Returns:
        包含 provider / api_key / base_url / model 的字典，无默认 Provider 时返回 None
    """
    try:
        from hemlock.models.llm_provider import LLMProvider
        provider = db.query(LLMProvider).filter(LLMProvider.is_default == True).first()
        if provider:
            return {
                "llm_provider": provider.provider,
                "llm_api_key": provider.api_key,
                "llm_base_url": provider.base_url,
                "llm_model": provider.model,
            }
    except Exception as e:
        logger.debug(f"查询默认 Provider 失败（可能数据库未初始化）: {e}")
    return None


def _build_settings_response(db: Session | None = None) -> SettingsResponse:
    """根据合并后的设置构建响应（api_key 脱敏）。

    LLM 相关字段优先从数据库默认 Provider 读取（即实际调用时使用的配置），
    数据库无默认 Provider 时回退到文件 / .env 配置。
    """
    merged = _get_merged_settings()

    # 优先使用数据库默认 Provider 的配置（与实际 LLM 调用一致）
    if db is not None:
        db_provider = _get_default_provider_info(db)
        if db_provider:
            merged["llm_provider"] = db_provider["llm_provider"]
            merged["llm_api_key"] = db_provider["llm_api_key"]
            merged["llm_base_url"] = db_provider["llm_base_url"]
            merged["llm_model"] = db_provider["llm_model"]

    raw_key = merged.get("llm_api_key", "")
    return SettingsResponse(
        llm_provider=merged["llm_provider"],
        llm_api_key=_mask_api_key(raw_key),
        has_api_key=bool(raw_key),
        llm_base_url=merged["llm_base_url"],
        llm_model=merged["llm_model"],
        llm_model_light=merged.get("llm_model_light", ""),
        llm_model_powerful=merged.get("llm_model_powerful", ""),
        llm_provider_light=merged.get("llm_provider_light", ""),
        llm_provider_powerful=merged.get("llm_provider_powerful", ""),
        model_routing_enabled=merged.get("model_routing_enabled", False),
        proxy_port=merged["proxy_port"],
        proxy_host=merged["proxy_host"],
        upstream_proxy=merged.get("upstream_proxy", ""),
        api_port=merged["api_port"],
        auto_pentest=merged.get("auto_pentest", True),
        src_hunting=merged.get("src_hunting", False),
        llm_assisted_verify_enabled=merged.get("llm_assisted_verify_enabled", False),
        sensitive_op_confirm=merged.get("sensitive_op_confirm", True),
        browser_headed=merged.get("browser_headed", False),
        compact_limit_tokens=merged.get("compact_limit_tokens", 200_000),
    )


def _apply_persisted_settings() -> None:
    """将文件中的持久化设置应用到 settings 单例（模块加载时调用一次）。

    确保重启后用户保存的 LLM / 代理配置立即生效。
    """
    file_data = _load_file_settings()
    if file_data:
        settings.update_settings(**file_data)
        logger.debug(
            f"已从设置文件加载持久化配置: provider={file_data.get('llm_provider', settings.llm_provider)}"
        )


# ===== 路由 =====
@router.get("/settings", response_model=SettingsResponse)
async def get_settings(db: Session = Depends(get_db)):
    """获取当前设置。

    LLM 相关字段（provider / model / base_url / api_key）优先从数据库默认 Provider 读取，
    确保展示的是实际调用时使用的模型配置。返回时 llm_api_key 已脱敏。
    """
    return _build_settings_response(db)


@router.patch("/settings", response_model=SettingsResponse)
async def update_settings(req: SettingsUpdateRequest, db: Session = Depends(get_db)):
    """更新设置。写入文件持久化，并动态更新 settings 单例。

    注意：LLM provider / model / api_key / base_url 的修改应通过 API Key 管理页面
    （/llm-providers）操作数据库 Provider，此处仅处理代理端口、模型路由等配置。
    """
    update_data = req.model_dump(exclude_none=True)

    # 合并已有文件数据后持久化
    file_data = _load_file_settings()
    file_data.update(update_data)
    _save_file_settings(file_data)

    # 动态更新 settings 单例
    settings.update_settings(**update_data)

    logger.info(
        f"设置已更新: {', '.join(update_data.keys())} | "
        f"provider={settings.llm_provider} model={settings.llm_model}"
    )

    return _build_settings_response(db)


@router.get("/models/providers", response_model=ProvidersResponse)
async def list_providers():
    """返回所有 provider 预设（仅 base_url，模型由用户自行输入）。"""
    return ProvidersResponse(
        providers={k: ProviderInfo(**v) for k, v in PROVIDER_PRESETS.items()}
    )


# 模块加载时应用持久化设置，使保存的配置在重启后立即生效
_apply_persisted_settings()
