"""mitmproxy addon：捕获 HTTP 流量入库。

完整保留请求/响应的所有头部（包括 Host 等），
存储格式使用 \n 换行，与前端显示一致。
自动检测心跳包/杂包并打上 is_noise 标记。
提取 Burp Suite 风格的拆分字段（host/path/mime/title 等）。
集成 FlowTag 正则匹配引擎，标注特殊数据包。
"""

from mitmproxy import http
from sqlalchemy.orm import Session

from hemlock.core.database import SessionLocal
from hemlock.core.active_project import get_active_project
from hemlock.models.flow import Flow
from hemlock.services.event_bus import event_bus
from hemlock.services.flow_filter import check_noise
from hemlock.services.flowtag_engine import (
    flowtag_engine,
    extract_url_fields,
    extract_mime_type,
    extract_title,
    get_resp_length,
)


def _headers_to_str(headers) -> str:
    """把 mitmproxy Headers 转为可读字符串，保留所有头部。"""
    return "\n".join(f"{k}: {v}" for k, v in headers.items())


class CaptureAddon:
    """mitmproxy addon：拦截响应，把请求响应写入 SQLite。

    每次捕获流量时动态读取当前活跃项目 ID，
    确保流量正确关联到用户选中的项目。
    完整保留所有请求/响应头部（包括 Host）。
    自动检测心跳包/杂包，打上 is_noise 标记（不拦截）。
    提取 host/path/mime/title 等拆分字段。
    集成 FlowTag 正则匹配，标注特殊数据包。
    """

    def response(self, flow: http.HTTPFlow) -> None:
        """响应完成后，把完整流量写入数据库。"""
        # 被拦截器 Drop 的 flow 不入库（响应级 Drop 由拦截 addon 打标记）
        if flow.metadata.get("hemlock_dropped"):
            return
        try:
            db: Session = SessionLocal()
            try:
                # 动态获取当前活跃项目 ID
                project_id = get_active_project()

                # 完整保留所有请求头部
                req_headers_str = _headers_to_str(flow.request.headers)
                resp_headers_str = ""
                resp_body_str = ""
                resp_status = None
                if flow.response:
                    resp_headers_str = _headers_to_str(flow.response.headers)
                    resp_body_str = flow.response.get_text() or ""
                    resp_status = flow.response.status_code

                # 计算请求-响应耗时（毫秒）
                # mitmproxy 的 timestamp_start / timestamp_end 为单调时钟秒数
                duration_ms = None
                if flow.response and flow.request.timestamp_start and flow.response.timestamp_end:
                    duration_ms = round(
                        (flow.response.timestamp_end - flow.request.timestamp_start) * 1000
                    )

                # 提取 URL 拆分字段
                url = flow.request.pretty_url
                host, scheme, path, extension = extract_url_fields(url)

                # 提取响应元数据
                mime_type = extract_mime_type(resp_headers_str)
                title = extract_title(resp_body_str, mime_type)
                resp_length = get_resp_length(resp_body_str) if resp_body_str else (
                    len(flow.response.raw_content) if flow.response and flow.response.raw_content else 0
                )

                # FlowTag 正则匹配
                tags, tag_colors = flowtag_engine.match(
                    req_headers_str,
                    flow.request.get_text() or "",
                    resp_headers_str,
                    resp_body_str,
                )

                # 自动检测是否为杂包
                # 说明：AI Agent 的请求使用 trust_env=False 直连目标、不经过代理，
                # 因此不会出现在这里，无需特殊的自定义头白名单。
                is_noise, noise_reason = check_noise(
                    flow.request.pretty_url,
                    flow.request.method,
                    resp_status,
                    resp_body_str,
                )

                flow_record = Flow(
                    project_id=project_id,
                    method=flow.request.method,
                    url=url,
                    req_headers=req_headers_str,
                    req_body=flow.request.get_text() or "",
                    resp_status=resp_status,
                    resp_headers=resp_headers_str,
                    resp_body=resp_body_str,
                    # 拆分字段
                    host=host,
                    scheme=scheme,
                    path=path,
                    resp_length=resp_length,
                    mime_type=mime_type,
                    extension=extension,
                    title=title,
                    duration_ms=duration_ms,
                    # FlowTag 标签
                    tags=tags,
                    tag_colors=tag_colors,
                    # 元数据
                    source="proxy",
                    is_noise=is_noise,
                    noise_reason=noise_reason,
                )
                db.add(flow_record)
                db.commit()
                db.refresh(flow_record)

                # 发布实时事件
                event_bus.publish("flow", {
                    "event": "new_flow",
                    "flow_id": flow_record.id,
                    "method": flow_record.method,
                    "url": flow_record.url,
                    "status": flow_record.resp_status,
                    "project_id": project_id,
                    "is_noise": is_noise,
                    "tags": tags,
                })
            finally:
                db.close()
        except Exception:
            # addon 内异常不能影响代理本身
            import traceback
            traceback.print_exc()


# mitmproxy 加载入口
addon = CaptureAddon
