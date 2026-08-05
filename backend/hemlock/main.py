"""Hemlock FastAPI 应用工厂。"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from hemlock.core.config import settings
from hemlock.core.database import init_db
from hemlock.core.logging_config import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化日志和数据库。"""
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info(f"启动 {settings.app_name} v0.3.0")
    logger.info(f"API: {settings.api_host}:{settings.api_port} | Proxy: {settings.proxy_host}:{settings.proxy_port}")
    logger.info(f"LLM: {settings.llm_provider} / {settings.llm_model}")
    logger.info(f"Debug 模式: {settings.debug}")

    init_db()
    logger.info("数据库初始化完成")

    # 清理上次服务异常退出遗留的悬挂任务
    try:
        from hemlock.services.agent_service import cleanup_stale_tasks
        cleaned = cleanup_stale_tasks()
        if cleaned:
            logger.info(f"已清理 {cleaned} 个悬挂 Agent 任务")
    except Exception as e:
        logger.warning(f"清理悬挂任务失败（非致命）: {e}")

    # 恢复活跃项目（从持久化文件读取，验证项目仍存在）
    # 临时项目在 init_db 中已被清理，如果上次活跃的是临时项目，会自动清除
    try:
        from hemlock.core.active_project import restore_active_project, set_active_project
        from hemlock.core.database import SessionLocal
        from hemlock.models.project import Project

        restored_id = restore_active_project()
        if restored_id:
            db = SessionLocal()
            try:
                project = db.query(Project).filter(Project.id == restored_id).first()
                if project:
                    logger.info(f"活跃项目已恢复: {project.name} ({restored_id})")
                else:
                    # 项目不存在（可能是临时项目被清理），清除活跃状态
                    set_active_project(None)
                    logger.info("活跃项目已被删除（可能是临时项目清理），已清除活跃状态")
            finally:
                db.close()
        else:
            logger.info("无持久化的活跃项目，等待前端选择")
    except Exception as e:
        logger.warning(f"恢复活跃项目失败（非致命）: {e}")

    # 初始化内置 Agent 角色（基于文件的角色注册表）
    try:
        from hemlock.core.agent_registry import ensure_builtin_roles
        ensure_builtin_roles()
        logger.info("内置 Agent 角色初始化完成")
    except Exception as e:
        logger.warning(f"Agent 角色初始化失败（非致命）: {e}")

    # 初始化内置技能（从 data/builtin_skills/ 复制到用户目录）
    try:
        from hemlock.core.skill_registry import ensure_builtin_skills
        ensure_builtin_skills()
        logger.info("内置技能初始化完成")
    except Exception as e:
        logger.warning(f"内置技能初始化失败（非致命）: {e}")

    # 连接已启用的 MCP 服务器
    try:
        from hemlock.core.database import SessionLocal
        from hemlock.core.mcp_manager import mcp_manager
        mcp_db = SessionLocal()
        await mcp_manager.connect_all_enabled(mcp_db)
        mcp_db.close()
        logger.info("MCP 服务器连接完成")
    except Exception as e:
        logger.warning(f"MCP 服务器连接失败（非致命）: {e}")

    # 启动运行时看门狗：周期清理悬挂/僵尸 Agent 任务（BUG-02 修复）
    try:
        from hemlock.services.agent_service import _watchdog_loop
        watchdog_task = asyncio.create_task(_watchdog_loop())
        app.state.watchdog_task = watchdog_task
        logger.info("Agent 任务看门狗已启动")
    except Exception as e:
        logger.warning(f"看门狗启动失败（非致命）: {e}")

    yield

    # 停止看门狗
    try:
        wt = getattr(app.state, "watchdog_task", None)
        if wt:
            wt.cancel()
    except Exception:
        pass

    # 断开所有 MCP 连接
    try:
        from hemlock.core.mcp_manager import mcp_manager
        await mcp_manager.disconnect_all()
    except Exception:
        pass

    # 停止代理
    try:
        from hemlock.core.proxy_controller import proxy_controller
        proxy_controller.stop()
    except Exception:
        pass

    # 关闭 HTTP 连接池
    from hemlock.services.request_service import close_client
    await close_client()

    logger.info(f"{settings.app_name} 正在关闭...")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """请求日志中间件：记录所有 API 请求的耗时和状态。"""

    async def dispatch(self, request: Request, call_next):
        # 跳过 WebSocket 和静态资源
        if request.url.path.startswith("/ws"):
            return await call_next(request)

        start_time = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start_time) * 1000

        # 只记录 API 请求
        if request.url.path.startswith("/api") or request.url.path == "/health":
            logger = logging.getLogger("hemlock.api")
            logger.debug(
                f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms:.0f}ms)"
            )

        return response


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.3.0",
        lifespan=lifespan,
        # 生产环境关闭文档
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    # ===== 全局异常处理 =====
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger = logging.getLogger("hemlock.api")
        logger.error(
            f"未处理异常: {type(exc).__name__}: {exc} | {request.method} {request.url.path}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "message": str(exc) if settings.debug else "服务器内部错误",
                "type": type(exc).__name__,
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        logger = logging.getLogger("hemlock.api")
        logger.warning(f"参数错误: {exc} | {request.method} {request.url.path}")
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "message": str(exc)},
        )

    # ===== 健康检查 =====
    @app.get("/health")
    async def health():
        """健康检查：返回服务状态和配置信息。"""
        from hemlock.core.llm_client import get_token_usage
        from hemlock.core.config import settings as s

        return {
            "status": "ok",
            "app": s.app_name,
            "version": "0.3.0",
            "debug": s.debug,
            "llm_configured": bool(s.llm_api_key),
            "llm_model": s.llm_model,
            "proxy": f"{s.proxy_host}:{s.proxy_port}",
            "token_usage": get_token_usage(),
        }

    # 注册路由
    _register_routers(app)

    return app


def _register_routers(app: FastAPI) -> None:
    """注册所有 API 路由。每个模块独立导入，缺失时跳过。"""
    logger = logging.getLogger(__name__)
    routers = []
    try:
        from hemlock.api.projects import router as projects_router
        routers.append(("/api", projects_router))
    except ImportError as e:
        logger.warning(f"projects 路由注册失败: {e}")
    try:
        from hemlock.api.flows import router as flows_router
        routers.append(("/api", flows_router))
    except ImportError as e:
        logger.warning(f"flows 路由注册失败: {e}")
    try:
        from hemlock.api.request import router as request_router
        routers.append(("/api", request_router))
    except ImportError as e:
        logger.warning(f"request 路由注册失败: {e}")
    try:
        from hemlock.api.intruder import router as intruder_router
        routers.append(("/api", intruder_router))
    except ImportError as e:
        logger.warning(f"intruder 路由注册失败: {e}")
    try:
        from hemlock.api.ai import router as ai_router
        routers.append(("/api", ai_router))
    except ImportError as e:
        logger.warning(f"ai 路由注册失败: {e}")
    try:
        from hemlock.api.agent import router as agent_router
        routers.append(("/api", agent_router))
    except ImportError as e:
        logger.warning(f"agent 路由注册失败: {e}")
    try:
        from hemlock.api.settings import router as settings_router
        routers.append(("/api", settings_router))
    except ImportError as e:
        logger.warning(f"settings 路由注册失败: {e}")
    try:
        from hemlock.api.skills import router as skills_router
        routers.append(("/api", skills_router))
    except ImportError as e:
        logger.warning(f"skills 路由注册失败: {e}")
    try:
        from hemlock.api.memories import router as memories_router
        routers.append(("/api", memories_router))
    except ImportError as e:
        logger.warning(f"memories 路由注册失败: {e}")
    try:
        from hemlock.api.proxy import router as proxy_router
        routers.append(("/api", proxy_router))
    except ImportError as e:
        logger.warning(f"proxy 路由注册失败: {e}")
    try:
        from hemlock.api.flowtag import router as flowtag_router
        routers.append(("/api", flowtag_router))
    except ImportError as e:
        logger.warning(f"flowtag 路由注册失败: {e}")
    try:
        from hemlock.api.recon import router as recon_router
        routers.append(("/api", recon_router))
    except ImportError as e:
        logger.warning(f"recon 路由注册失败: {e}")
    try:
        from hemlock.api.collab import router as collab_router
        routers.append(("/api", collab_router))
    except ImportError as e:
        logger.warning(f"collab 路由注册失败: {e}")
    try:
        from hemlock.api.llm_providers import router as llm_providers_router
        routers.append(("/api", llm_providers_router))
    except ImportError as e:
        logger.warning(f"llm_providers 路由注册失败: {e}")
    try:
        from hemlock.api.ws import router as ws_router
        routers.append(("", ws_router))
    except ImportError as e:
        logger.warning(f"ws 路由注册失败: {e}")

    for prefix, router in routers:
        app.include_router(router, prefix=prefix)

    logger.info(f"已注册 {len(routers)} 个路由模块")


app = create_app()
