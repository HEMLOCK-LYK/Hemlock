"""AttackChain / AttackNode 模型：攻击链。

把「从入口资产一步步走到拿数据」的因果路径固化下来——SRC 报告的故事线、
挖洞进度的结构化载体。来源：Agent 干活时从 task.steps 自动抽取（draft），
Agent / 用户确认后转 confirmed。设计稿见 docs/attack-chain-design.md。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, Integer, Float, DateTime, ForeignKey

from hemlock.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _chain_uuid() -> str:
    return f"chn_{uuid.uuid4().hex[:24]}"


def _node_uuid() -> str:
    return f"and_{uuid.uuid4().hex[:24]}"


class AttackChain(Base):
    __tablename__ = "attack_chains"

    id = Column(String, primary_key=True, default=_chain_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)
    task_id = Column(String, nullable=True, index=True)  # 来源任务（可空：手工创建的链不挂任务）
    name = Column(String(300), default="")               # 如 "OA 系统 → SQL 注入 → 拖库"
    status = Column(String(16), default="draft")         # draft / confirmed / failed
    # 边不单独建表：拓扑是顺序生长的，单 JSON 列足够（设计稿定稿）
    # 格式：[{"from": and_x, "to": and_y, "label": "利用"}]
    edges = Column(Text, default="[]")
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class AttackNode(Base):
    __tablename__ = "attack_nodes"

    id = Column(String, primary_key=True, default=_node_uuid)
    chain_id = Column(String, ForeignKey("attack_chains.id"), nullable=False, index=True)
    # asset / vuln / credential / action / loot
    node_type = Column(String(16), nullable=False)
    title = Column(String(300), default="")   # 如 "SQL 注入点：/api/search 的 q 参数"
    detail = Column(Text, default="")         # JSON 文本：node_type 相关的结构化字段
    # 关联 task.steps 的步骤序号（依据可追溯 + 自动构建的幂等去重键）
    source_step = Column(Integer, nullable=True)
    flow_id = Column(String, nullable=True)   # 关联证据流量
    x = Column(Float, default=0.0)            # 画布坐标（第二期 React Flow 用）
    y = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
