"""mitmproxy addon：捕获 HTTP 流量入库。

完整保留请求/响应的所有头部（包括 Host 等），
存储格式使用 \\n 换行，与前端显示一致。
"""

from mitmproxy import http
from sqlalchemy.orm import Session

from hemlock.core.database import SessionLocal
from hemlock.core.active_project import get_active_project
from hemlock.models.flow import Flow
from hemlock.services.event_bus import event_bus


def _headers_to_str(headers) -> str:
    """把 mitmproxy Headers 转为可读字符串，保留所有头部。

    使用 \\n 换行（与前端显示一致），完整保留 Host、Cookie、
    Content-Type 等所有头部字段。
    """
    return "\n".join(f"{k}: {v}" for k, v in headers.items())


class CaptureAddon:
    """mitmproxy addon：拦截响应，把请求响应写入 SQLite。

    每次捕获流量时动态读取当前活跃项目 ID，
    确保流量正确关联到用户选中的项目。
    完整保留所有请求/响应头部（包括 Host）。
    """

    def response(self, flow: http.HTTPFlow) -> None:
        """响应完成后，把完整流量写入数据库。"""
        import sys
        print(f"[CaptureAddon] response: {flow.request.method} {flow.request.pretty_url}", file=sys.stderr, flush=True)
        try:
            db: Session = SessionLocal()
            try:
                # 动态获取当前活跃项目 ID
                project_id = get_active_project()
                print(f"[CaptureAddon] project_id={project_id}", file=sys.stderr, flush=True)

                # 完整保留所有请求头部（mitmproxy 的 headers 包含 Host 等）
                req_headers_str = _headers_to_str(flow.request.headers)
                resp_headers_str = ""
                if flow.response:
                    resp_headers_str = _headers_to_str(flow.response.headers)

                flow_record = Flow(
                    project_id=project_id,
                    method=flow.request.method,
                    url=flow.request.pretty_url,
                    req_headers=req_headers_str,
                    req_body=flow.request.get_text() or "",
                    resp_status=flow.response.status_code if flow.response else None,
                    resp_headers=resp_headers_str,
                    resp_body=flow.response.get_text() or "" if flow.response else "",
                    source="proxy",
                )
                db.add(flow_record)
                db.commit()
                db.refresh(flow_record)
                print(f"[CaptureAddon] flow saved id={flow_record.id}", file=sys.stderr, flush=True)

                # 发布实时事件
                event_bus.publish("flow", {
                    "event": "new_flow",
                    "flow_id": flow_record.id,
                    "method": flow_record.method,
                    "url": flow_record.url,
                    "status": flow_record.resp_status,
                    "project_id": project_id,
                })
            finally:
                db.close()
        except Exception as e:
            # addon 内异常不能影响代理本身
            import traceback
            print(f"[CaptureAddon] error: {e}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)


# mitmproxy 加载入口
addon = CaptureAddon
