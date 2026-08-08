"""LLM Provider 模型 - 支持多组 API Key 配置。"""
from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer
from sqlalchemy.sql import func
from hemlock.core.database import Base


class LLMProvider(Base):
    """LLM API Key 配置（支持多组）。"""
    __tablename__ = "llm_providers"

    id = Column(String(36), primary_key=True)
    name = Column(String(100), nullable=False)          # 用户起的名字，如 "DeepSeek-V3"
    provider = Column(String(50), nullable=False, default="openai")  # openai/deepseek/anthropic/gemini/...
    api_key = Column(Text, nullable=False)               # API Key
    base_url = Column(String(500), default="https://api.openai.com/v1")
    model = Column(String(100), default="gpt-4o-mini")   # 默认模型名
    context_window = Column(Integer, nullable=True)      # 上下文窗口（tokens），空=走兜底（1M/128K）
    is_default = Column(Boolean, default=False)           # 是否默认（只能有一个）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
