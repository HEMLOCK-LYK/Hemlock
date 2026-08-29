"""数据库引擎与会话管理。"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base

from hemlock.core.config import settings

engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db() -> None:
    """初始化数据库：创建所有表 + 轻量级迁移。"""
    # 导入模型以确保它们被注册到 Base.metadata
    from hemlock.models import project, flow, finding, evidence, task, mcp_server, llm_provider  # noqa: F401

    Base.metadata.create_all(bind=engine)

    # 轻量级迁移：为已有表添加新列（SQLite ALTER TABLE）
    _migrate_add_columns()


def _migrate_add_columns() -> None:
    """为已有表添加新列（如果列不存在）。SQLite 不支持 IF NOT EXISTS，需要手动检查。"""
    with engine.connect() as conn:
        # 检查 projects 表是否有 is_temp 列
        result = conn.execute(text("PRAGMA table_info(projects)"))
        columns = [row[1] for row in result]
        if "is_temp" not in columns:
            conn.execute(text("ALTER TABLE projects ADD COLUMN is_temp BOOLEAN DEFAULT 0 NOT NULL"))
            conn.commit()
            # 确保迁移后清理旧临时项目（首次迁移不会有临时项目，但保持安全）
        # 启动时清理所有临时项目（先删除关联的 tasks/findings/evidence，避免孤儿记录）
        conn.execute(text("DELETE FROM evidences WHERE id IN (SELECT evidence_id FROM findings WHERE task_id IN (SELECT id FROM tasks WHERE project_id IN (SELECT id FROM projects WHERE is_temp = 1)))"))
        conn.execute(text("DELETE FROM findings WHERE task_id IN (SELECT id FROM tasks WHERE project_id IN (SELECT id FROM projects WHERE is_temp = 1))"))
        conn.execute(text("DELETE FROM tasks WHERE project_id IN (SELECT id FROM projects WHERE is_temp = 1)"))
        conn.execute(text("DELETE FROM flows WHERE project_id IN (SELECT id FROM projects WHERE is_temp = 1)"))
        conn.execute(text("DELETE FROM projects WHERE is_temp = 1"))
        conn.commit()

        # 检查 flows 表是否有 is_noise / noise_reason 列
        result = conn.execute(text("PRAGMA table_info(flows)"))
        flow_columns = [row[1] for row in result]
        if "is_noise" not in flow_columns:
            conn.execute(text("ALTER TABLE flows ADD COLUMN is_noise BOOLEAN DEFAULT 0 NOT NULL"))
            conn.commit()
        if "noise_reason" not in flow_columns:
            conn.execute(text("ALTER TABLE flows ADD COLUMN noise_reason VARCHAR(64) DEFAULT ''"))
            conn.commit()

        # 新增拆分字段和元数据字段（Burp Suite 风格）
        new_flow_cols = {
            "host": "VARCHAR(255) DEFAULT ''",
            "scheme": "VARCHAR(10) DEFAULT ''",
            "path": "TEXT DEFAULT ''",
            "resp_length": "INTEGER",
            "mime_type": "VARCHAR(100) DEFAULT ''",
            "extension": "VARCHAR(20) DEFAULT ''",
            "title": "VARCHAR(500) DEFAULT ''",
            "tags": "VARCHAR(500) DEFAULT ''",
            "tag_colors": "VARCHAR(200) DEFAULT ''",
            "duration_ms": "INTEGER",  # 请求-响应耗时（毫秒）
        }
        # 重新获取列名（上面可能已变更）
        result = conn.execute(text("PRAGMA table_info(flows)"))
        flow_columns = [row[1] for row in result]
        for col_name, col_def in new_flow_cols.items():
            if col_name not in flow_columns:
                conn.execute(text(f"ALTER TABLE flows ADD COLUMN {col_name} {col_def}"))
                conn.commit()
        # 为 host 添加索引（如果不存在）
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_flows_host ON flows (host)"))
        conn.commit()

        # 检查 tasks 表是否有 checkpoint 相关列（messages / checkpoint）
        result = conn.execute(text("PRAGMA table_info(tasks)"))
        task_columns = [row[1] for row in result]
        if "messages" not in task_columns:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN messages TEXT DEFAULT '[]'"))
            conn.commit()
        if "checkpoint" not in task_columns:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN checkpoint TEXT DEFAULT '{}'"))
            conn.commit()
        if "memory_snapshot" not in task_columns:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN memory_snapshot TEXT"))
            conn.commit()

        # 检查 llm_providers 表是否有 context_window 列（上下文窗口，tokens，空=兜底）
        result = conn.execute(text("PRAGMA table_info(llm_providers)"))
        provider_columns = [row[1] for row in result]
        if "context_window" not in provider_columns:
            conn.execute(text("ALTER TABLE llm_providers ADD COLUMN context_window INTEGER"))
            conn.commit()

        # 检查 llm_providers 表是否有 protocol 列（接口协议：openai / anthropic）
        if "protocol" not in provider_columns:
            conn.execute(text("ALTER TABLE llm_providers ADD COLUMN protocol VARCHAR(20) DEFAULT 'openai' NOT NULL"))
            conn.commit()


def get_db():
    """FastAPI 依赖：提供数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
