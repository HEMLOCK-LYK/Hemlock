"""拦截器控制 API：开关配置、挂起队列查询、Forward/Drop。"""

from fastapi import APIRouter
from pydantic import BaseModel

from hemlock.core.proxy_controller import proxy_controller
from hemlock.services.intercept_controller import intercept_controller

router = APIRouter(prefix="/intercept", tags=["intercept"])


class InterceptStatus(BaseModel):
    enabled: bool
    intercept_requests: bool
    intercept_responses: bool
    pending_count: int
    proxy_running: bool = False


class InterceptConfig(BaseModel):
    enabled: bool | None = None
    intercept_requests: bool | None = None
    intercept_responses: bool | None = None


class InterceptItem(BaseModel):
    id: str
    kind: str  # "request" | "response"
    method: str
    url: str
    status: int | None
    queued_at: float
    raw: str


class InterceptPendingList(BaseModel):
    total: int
    items: list[InterceptItem]


class InterceptAction(BaseModel):
    id: str
    raw: str | None = None  # forward 时携带编辑后的完整原始包；缺省=原样放行


class InterceptActionResult(BaseModel):
    ok: bool
    remaining: int = 0
    error: str = ""  # "not_found" / "already_resolved" / "proxy_not_running"


def _proxy_running() -> bool:
    try:
        return proxy_controller.status()["running"]
    except Exception:
        return False


def _to_status(s: dict) -> InterceptStatus:
    return InterceptStatus(
        enabled=s["enabled"],
        intercept_requests=s["intercept_requests"],
        intercept_responses=s["intercept_responses"],
        pending_count=s["pending_count"],
        proxy_running=_proxy_running(),
    )


@router.get("/status", response_model=InterceptStatus)
async def get_intercept_status():
    """获取拦截器状态。"""
    return _to_status(intercept_controller.get_status())


@router.put("/config", response_model=InterceptStatus)
async def set_intercept_config(cfg: InterceptConfig):
    """更新拦截开关（enabled / intercept_requests / intercept_responses，缺省不改变）。"""
    return _to_status(
        intercept_controller.update_config(
            enabled=cfg.enabled,
            intercept_requests=cfg.intercept_requests,
            intercept_responses=cfg.intercept_responses,
        )
    )


@router.get("/pending", response_model=InterceptPendingList)
async def get_pending():
    """获取当前被拦且等待处理的队列。"""
    return intercept_controller.pending_list()


@router.post("/forward", response_model=InterceptActionResult)
async def forward(action: InterceptAction):
    """放行被拦的 flow；携带 raw 时应用编辑后的原始包。"""
    return intercept_controller.forward(action.id, action.raw)


@router.post("/drop", response_model=InterceptActionResult)
async def drop(action: InterceptAction):
    """丢弃被拦的 flow。"""
    return intercept_controller.drop(action.id)
