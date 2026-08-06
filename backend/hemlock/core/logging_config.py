"""日志系统配置：统一日志格式、级别、文件轮转。

日志策略：
- 控制台输出：开发模式（debug 级别），生产模式（info 级别）
- 文件输出：按日轮转，保留 7 天，单文件最大 10MB
- 模块级过滤：Agent 相关模块使用 debug 级别，其他使用 info
- 安全日志：敏感操作和漏洞报告单独记录到 security.log
"""

import logging
import logging.handlers
import sys

from hemlock.core.config import get_data_dir, settings

# 日志目录
LOG_DIR = get_data_dir() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 日志格式
_DETAILED_FORMAT = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_SIMPLE_FORMAT = logging.Formatter(
    "%(levelname)-8s | %(name)-20s | %(message)s",
)


def setup_logging(debug: bool | None = None) -> None:
    """初始化全局日志配置。

    Args:
        debug: 是否启用 debug 模式。None 则从配置读取。
    """
    if debug is None:
        debug = settings.debug

    level = logging.DEBUG if debug else logging.INFO

    # 根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 清除已有 handlers（避免重复配置）
    root_logger.handlers.clear()

    # ===== 控制台输出 =====
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(_SIMPLE_FORMAT if not debug else _DETAILED_FORMAT)
    root_logger.addHandler(console_handler)

    # ===== 文件输出（按日轮转） =====
    file_handler = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / "hemlock.log",
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # 文件始终记录 debug 级别
    file_handler.setFormatter(_DETAILED_FORMAT)
    root_logger.addHandler(file_handler)

    # ===== 安全日志（单独文件） =====
    security_handler = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / "security.log",
        when="midnight",
        interval=1,
        backupCount=30,  # 安全日志保留 30 天
        encoding="utf-8",
    )
    security_handler.setLevel(logging.INFO)
    security_handler.setFormatter(_DETAILED_FORMAT)

    security_logger = logging.getLogger("hemlock.security")
    security_logger.addHandler(security_handler)
    security_logger.propagate = True  # 同时输出到控制台和主日志

    # ===== 模块级配置 =====
    # Agent 相关模块使用 debug 级别
    for module in [
        "hemlock.services.agent_service",
        "hemlock.core.llm_client",
        "hemlock.core.vuln_verifier",
        "hemlock.core.pentest_context",
    ]:
        logging.getLogger(module).setLevel(logging.DEBUG)

    # 第三方库降低日志级别
    for module in ["httpx", "httpcore", "openai", "urllib3"]:
        logging.getLogger(module).setLevel(logging.WARNING)

    logging.info(f"日志系统已初始化（级别: {'DEBUG' if debug else 'INFO'}，目录: {LOG_DIR}）")


def get_security_logger() -> logging.Logger:
    """获取安全日志器，用于记录敏感操作和漏洞。"""
    return logging.getLogger("hemlock.security")


def log_sensitive_action(
    action: str,
    task_id: str = "",
    tool: str = "",
    args: dict | None = None,
    approved: bool | None = None,
) -> None:
    """记录敏感操作日志。"""
    logger = get_security_logger()
    status = "approved" if approved else "rejected" if approved is False else "requested"
    logger.info(
        f"[SECURITY] 敏感操作 {status} | action={action} task={task_id} tool={tool} "
        f"args={args or {}}"
    )


def log_finding_reported(
    finding_id: str,
    vuln_type: str,
    severity: str,
    title: str,
    task_id: str = "",
) -> None:
    """记录漏洞报告日志。"""
    logger = get_security_logger()
    logger.info(
        f"[SECURITY] 漏洞报告 | finding={finding_id} type={vuln_type} severity={severity} "
        f"task={task_id} title={title}"
    )
