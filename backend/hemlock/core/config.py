"""Hemlock 全局配置。"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_data_dir() -> Path:
    """获取数据目录。

    优先级：
    1. 环境变量 HEMLOCK_DATA_DIR（由 Tauri sidecar 传入应用数据目录）
    2. %APPDATA%/Hemlock（Windows 惯例）
    3. ~/.hemlock（回退）
    """
    env_dir = os.environ.get("HEMLOCK_DATA_DIR")
    if env_dir:
        p = Path(env_dir)
    elif os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            p = Path(appdata) / "Hemlock"
        else:
            p = Path.home() / ".hemlock"
    else:
        p = Path.home() / ".hemlock"
    p.mkdir(parents=True, exist_ok=True)
    return p


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HEMLOCK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 基础
    app_name: str = "Hemlock"
    debug: bool = True

    # 数据库 — 默认使用统一数据目录
    db_path: str = str(get_data_dir() / "hemlock.db")

    # FastAPI
    api_host: str = "127.0.0.1"
    api_port: int = 9876

    # mitmproxy
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8080
    # 上游代理（格式：http://host:port 或 http://user:pass@host:port），空则不使用
    upstream_proxy: str = ""
    # 授权目标范围（逗号分隔的域名/通配符），空则允许全部
    target_scope: str = ""

    # 限速
    rate_limit_per_second: int = 10

    # 拦截器
    # 被拦 flow 等待人工处理的最长秒数，超时自动放行（不编辑）
    intercept_timeout: int = 300

    # LLM
    llm_provider: str = "deepseek"  # openai / deepseek / claude
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-v4-flash"
    # 模型分层路由：根据任务复杂度自动选择模型
    # 轻量模型：用于简单任务（标题生成、意图识别等），为空则回退到 llm_model
    llm_model_light: str = ""
    # 强力模型：用于复杂推理（漏洞分析、利用链推导等），为空则回退到 llm_model
    llm_model_powerful: str = ""
    # 轻量模型对应的 Provider ID（智能路由时使用该 Provider 的 API Key 和 Base URL）
    llm_provider_light: str = ""
    # 强力模型对应的 Provider ID
    llm_provider_powerful: str = ""
    # 是否启用智能模型路由
    model_routing_enabled: bool = False
    # 是否注入 cache_control（仅对 claude- 前缀模型生效，用于 Claude 原生 prompt 缓存）
    # DeepSeek/OpenAI 等使用自动前缀缓存，不需要也不接受此字段，默认关闭
    llm_cache_control_enabled: bool = False

    # Agent 模式
    auto_pentest: bool = True  # 一键自动化渗透测试开关
    # LLM 辅助漏洞验证开关（开启后规则验证不确定时调用 LLM 分析，消耗额外 token）
    llm_assisted_verify_enabled: bool = False
    # 敏感操作确认开关（开启后 Agent 执行敏感操作需用户手动批准）
    sensitive_op_confirm: bool = True

    @property
    def db_url(self) -> str:
        db = Path(self.db_path)
        db.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db.as_posix()}"

    @property
    def scope_list(self) -> list[str]:
        return [s.strip() for s in self.target_scope.split(",") if s.strip()]

    def update_settings(self, **kwargs):
        """动态更新配置：运行时覆盖单例字段值（用于设置 API）。

        仅更新已存在且非 None 的字段。
        """
        for key, value in kwargs.items():
            if hasattr(self, key) and value is not None:
                setattr(self, key, value)


settings = Settings()
