"""拦截器端到端检查：真实 mitmproxy + FastAPI + 真实 HTTP 客户端。

用法：在 backend 目录运行 `.venv/Scripts/python.exe e2e_intercept_check.py`。
（非 pytest 收集对象，使用独立临时数据目录，不污染真实环境）

覆盖场景：
  1. 请求拦截 + 编辑 raw 后 Forward → 上游收到修改后的请求，客户端得到响应，flow 入库
  2. Drop → 客户端报错，flow 不入库
  3. 响应拦截 + 编辑响应后 Forward → 客户端收到修改后的响应，入库为编辑后内容
"""
import os
import sys
import json
import time
import tempfile
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

TMP = tempfile.mkdtemp(prefix="hemlock_e2e_")
os.environ["HEMLOCK_DATA_DIR"] = TMP
os.environ["HEMLOCK_DB_PATH"] = os.path.join(TMP, "e2e.db")

from hemlock.core.config import settings

settings.proxy_port = 8234  # 避开默认 8080（用户可能正在运行 app）
settings.intercept_timeout = 15

from hemlock.main import create_app
from fastapi.testclient import TestClient

PROXY = ("127.0.0.1", settings.proxy_port)
TARGET_PORT = 9009


class Handler(BaseHTTPRequestHandler):
    """回显请求路径与请求头，便于断言上游真正收到的内容。"""

    def do_GET(self):
        body = json.dumps({"path": self.path, "headers": dict(self.headers)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_target():
    server = HTTPServer(("127.0.0.1", TARGET_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def http_get(url, timeout=10):
    proxy = urllib.request.ProxyHandler({"http": f"http://{PROXY[0]}:{PROXY[1]}"})
    opener = urllib.request.build_opener(proxy)
    try:
        with opener.open(url, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return None, str(e)


results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def wait_pending(client, want, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = client.get("/api/intercept/pending").json()
        if d["total"] >= want:
            return d
        time.sleep(0.2)
    return client.get("/api/intercept/pending").json()


def flow_detail(client, item):
    return client.get(f"/api/flows/{item['id']}").json()


def main():
    server = start_target()
    app = create_app()
    with TestClient(app) as c:
        # ---- 准备：项目 + 活跃 + 代理 ----
        p = c.post("/api/projects", json={"name": "e2e-intercept"}).json()
        pid = p["id"]
        c.post("/api/projects/active", json={"project_id": pid})
        r = c.post("/api/proxy/start").json()
        check("proxy start", r.get("running") is True, str(r))

        # ===== 场景 1：请求拦截 + 编辑 Forward =====
        c.put("/api/intercept/config", json={"enabled": True, "intercept_requests": True, "intercept_responses": False})
        st = c.get("/api/intercept/status").json()
        check("intercept enabled", st["enabled"] and st["intercept_requests"])

        got = {}
        threading.Thread(target=lambda: got.setdefault("resp", http_get(f"http://127.0.0.1:{TARGET_PORT}/e2e?a=1"))).start()
        pend = wait_pending(c, 1)
        check("request pending", pend["total"] == 1)
        item = pend["items"][0]
        check("pending kind request", item["kind"] == "request")
        check("pending raw non-empty", bool(item["raw"]))

        edited = f"GET /e2e-modified HTTP/1.1\nHost: 127.0.0.1:{TARGET_PORT}\nX-E2E: 1\n\n"
        fwd = c.post("/api/intercept/forward", json={"id": item["id"], "raw": edited}).json()
        check("forward ok", fwd.get("ok") is True, str(fwd))
        for _ in range(50):
            if got.get("resp"):
                break
            time.sleep(0.1)
        status, body = got.get("resp", (None, ""))
        check("client got 200", status == 200, f"status={status} body={body[:100]}")
        try:
            echo = json.loads(body)
            check("upstream saw edited path", echo.get("path") == "/e2e-modified", body[:120])
            check("upstream saw X-E2E header", echo.get("headers", {}).get("X-E2E") == "1", str(echo.get("headers"))[:100])
        except Exception as ex:
            check("parse echo", False, str(ex))

        time.sleep(0.5)
        flows = c.get("/api/flows", params={"project_id": pid, "page_size": 50}).json()
        f0 = flows["items"][0] if flows["items"] else None
        check("flow captured", f0 is not None)
        if f0:
            check("flow path edited", f0["path"] == "/e2e-modified", f0["path"])
            fd = flow_detail(c, f0)
            check("flow req_headers have X-E2E", "X-E2E: 1" in fd["req_headers"], fd["req_headers"][:200])

        # ===== 场景 2：Drop =====
        got2 = {}
        threading.Thread(target=lambda: got2.setdefault("resp", http_get(f"http://127.0.0.1:{TARGET_PORT}/e2e-drop"))).start()
        pend2 = wait_pending(c, 1)
        check("second pending", pend2["total"] == 1)
        dr = c.post("/api/intercept/drop", json={"id": pend2["items"][0]["id"]}).json()
        check("drop ok", dr.get("ok") is True, str(dr))
        for _ in range(50):
            if got2.get("resp"):
                break
            time.sleep(0.1)
        status2, body2 = got2.get("resp", (None, ""))
        check("dropped client errors", status2 is None, f"status={status2} body={body2[:80]}")

        time.sleep(0.5)
        flows2 = c.get("/api/flows", params={"project_id": pid, "page_size": 50}).json()
        check("dropped flow not captured", flows2["total"] == flows["total"], f"before={flows['total']} after={flows2['total']}")

        # ===== 场景 3：响应拦截 + 编辑 Forward =====
        c.put("/api/intercept/config", json={"enabled": True, "intercept_requests": False, "intercept_responses": True})
        got3 = {}
        threading.Thread(target=lambda: got3.setdefault("resp", http_get(f"http://127.0.0.1:{TARGET_PORT}/e2e-resp"))).start()
        pend3 = wait_pending(c, 1)
        check("response pending", pend3["total"] == 1 and pend3["items"][0]["kind"] == "response",
              pend3["items"][0]["kind"] if pend3["items"] else "none")
        ritem = pend3["items"][0]
        rlines = ritem["raw"].split("\n")
        rlines[0] = "HTTP/1.1 418 I'm a teapot"
        rlines.insert(1, "X-Modified: 1")
        redited = "\n".join(rlines)
        rfwd = c.post("/api/intercept/forward", json={"id": ritem["id"], "raw": redited}).json()
        check("response forward ok", rfwd.get("ok") is True, str(rfwd))
        for _ in range(50):
            if got3.get("resp"):
                break
            time.sleep(0.1)
        status3, body3 = got3.get("resp", (None, ""))
        check("client got 418", status3 == 418, f"status={status3} body={body3[:100]}")

        time.sleep(0.5)
        flows3 = c.get("/api/flows", params={"project_id": pid, "page_size": 50}).json()
        latest = flows3["items"][0] if flows3["items"] else None
        check("resp flow captured 418", latest is not None and latest["resp_status"] == 418,
              f"status={latest['resp_status'] if latest else None}")
        if latest:
            ld = flow_detail(c, latest)
            check("resp flow has X-Modified", "X-Modified: 1" in ld["resp_headers"], ld["resp_headers"][:200])

        # ===== 清理 =====
        c.post("/api/proxy/stop")
        c.post("/api/projects/active", json={"project_id": None})
        c.delete(f"/api/projects/{pid}")
        print("after stop, pending =", c.get("/api/intercept/pending").json()["total"])

    server.shutdown()

    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    print(f"\n==== {len(results) - len(failed)}/{len(results)} passed ====")
    if failed:
        for name, _, detail in failed:
            print("  FAIL:", name, detail)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
