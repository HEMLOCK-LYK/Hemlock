"""MCP 服务器管理器 - 管理外部 MCP 服务器连接和工具发现。

支持两种传输方式：
- stdio：启动本地进程（如 npx @modelcontextprotocol/server-sqlite）
- http：连接远程 HTTP 端点

工具发现后动态注入到 LLM 的工具列表中，与内置工具并列。
"""

import json
import logging
import asyncio
from typing import Any

from sqlalchemy.orm import Session

from hemlock.models.mcp_server import McpServer

logger = logging.getLogger(__name__)


class McpConnection:
    """单个 MCP 服务器连接实例。"""

    def __init__(self, server: McpServer):
        self.server = server
        self.tools: list[dict[str, Any]] = []
        self._process: Any = None
        self._connected = False

    async def connect(self) -> bool:
        """连接到 MCP 服务器，发现可用工具。"""
        try:
            if self.server.transport == "stdio":
                return await self._connect_stdio()
            elif self.server.transport == "http":
                return await self._connect_http()
            else:
                logger.error(f"未知的传输方式: {self.server.transport}")
                return False
        except Exception as e:
            logger.error(f"MCP 服务器 {self.server.name} 连接失败: {e}")
            self.server.status = "error"
            self.server.last_error = str(e)
            return False

    async def _connect_stdio(self) -> bool:
        """通过 stdio 启动子进程连接 MCP 服务器。"""
        args = json.loads(self.server.args or "[]")
        env_vars = json.loads(self.server.env or "{}")

        try:
            self._process = await asyncio.create_subprocess_exec(
                self.server.command,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**dict(__import__("os").environ), **env_vars},
            )
            # 发送 initialize 请求
            await self._send_request({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "hemlock", "version": "0.2.0"},
                },
            })

            # 发送 tools/list 请求
            resp = await self._send_request({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            })

            if resp and "result" in resp and "tools" in resp["result"]:
                self.tools = resp["result"]["tools"]
                self._connected = True
                self.server.status = "connected"
                self.server.tool_count = len(self.tools)
                self.server.last_error = ""
                logger.info(f"MCP 服务器 {self.server.name} 连接成功，发现 {len(self.tools)} 个工具")
                return True
            else:
                self.server.status = "error"
                self.server.last_error = "tools/list 响应格式错误"
                return False

        except Exception as e:
            self.server.status = "error"
            self.server.last_error = f"stdio 连接失败: {e}"
            logger.error(f"MCP stdio 连接失败: {e}")
            return False

    async def _connect_http(self) -> bool:
        """通过 HTTP 连接 MCP 服务器。"""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                # 发送 initialize 请求
                init_resp = await client.post(self.server.url, json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "hemlock", "version": "0.2.0"},
                    },
                })
                # 发送 tools/list 请求
                tools_resp = await client.post(self.server.url, json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                })

                data = tools_resp.json()
                if "result" in data and "tools" in data["result"]:
                    self.tools = data["result"]["tools"]
                    self._connected = True
                    self.server.status = "connected"
                    self.server.tool_count = len(self.tools)
                    self.server.last_error = ""
                    logger.info(f"MCP HTTP 服务器 {self.server.name} 连接成功，发现 {len(self.tools)} 个工具")
                    return True
                else:
                    self.server.status = "error"
                    self.server.last_error = "HTTP 响应格式错误"
                    return False

        except Exception as e:
            self.server.status = "error"
            self.server.last_error = f"HTTP 连接失败: {e}"
            logger.error(f"MCP HTTP 连接失败: {e}")
            return False

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """调用 MCP 服务器上的工具。"""
        if not self._connected:
            return {"error": "服务器未连接"}

        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        try:
            if self.server.transport == "stdio":
                resp = await self._send_request(req)
            else:
                import httpx
                async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                    resp = await client.post(self.server.url, json=req)

            if resp and "result" in resp:
                return resp["result"]
            elif resp and "error" in resp:
                return {"error": resp["error"].get("message", "MCP 工具调用错误")}
            return {"error": "未知响应"}
        except Exception as e:
            return {"error": f"工具调用失败: {e}"}

    async def _send_request(self, request: dict) -> dict | None:
        """通过 stdio 发送 JSON-RPC 请求并等待响应。"""
        if not self._process or self._process.returncode is not None:
            return None

        data = json.dumps(request) + "\n"
        self._process.stdin.write(data.encode())
        await self._process.stdin.drain()

        # 读取响应行
        line = await asyncio.wait_for(self._process.stdout.readline(), timeout=15)
        if line:
            return json.loads(line.decode())
        return None

    async def disconnect(self):
        """断开连接。"""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            await self._process.wait()
        self._process = None
        self._connected = False
        self.tools = []
        self.server.status = "disconnected"
        self.server.tool_count = 0


class McpManager:
    """MCP 服务器管理器：管理所有连接、工具发现和动态注入。"""

    def __init__(self):
        self._connections: dict[str, McpConnection] = {}

    async def connect_server(self, db: Session, server: McpServer) -> bool:
        """连接指定的 MCP 服务器。"""
        conn = McpConnection(server)
        success = await conn.connect()
        db.commit()

        if success:
            self._connections[server.name] = conn
        return success

    async def disconnect_server(self, db: Session, server: McpServer):
        """断开 MCP 服务器连接。"""
        conn = self._connections.pop(server.name, None)
        if conn:
            await conn.disconnect()
        server.status = "disconnected"
        server.tool_count = 0
        db.commit()

    async def connect_all_enabled(self, db: Session):
        """启动时连接所有已启用的 MCP 服务器。"""
        servers = db.query(McpServer).filter(McpServer.enabled == True).all()
        for server in servers:
            await self.connect_server(db, server)

    async def disconnect_all(self):
        """关闭所有连接。"""
        for conn in list(self._connections.values()):
            await conn.disconnect()
        self._connections.clear()

    def get_enabled_tools(self, db: Session) -> list[dict[str, Any]]:
        """获取所有已连接 MCP 服务器的工具列表（LLM tool schema 格式）。

        将 MCP tool 定义转换为 OpenAI function calling 格式。
        """
        tools: list[dict[str, Any]] = []
        for name, conn in self._connections.items():
            if not conn._connected:
                continue
            for tool in conn.tools:
                # MCP tool -> OpenAI function format
                fn_name = f"mcp_{name}_{tool.get('name', 'unknown')}"
                params = tool.get("inputSchema", {"type": "object", "properties": {}})
                tools.append({
                    "type": "function",
                    "function": {
                        "name": fn_name,
                        "description": f"[MCP:{name}] {tool.get('description', '')}",
                        "parameters": params,
                    },
                })
        return tools

    def get_connection(self, server_name: str) -> McpConnection | None:
        """获取指定服务器的连接实例。"""
        return self._connections.get(server_name)

    def get_status_list(self, db: Session) -> list[dict]:
        """获取所有 MCP 服务器的状态摘要。"""
        servers = db.query(McpServer).order_by(McpServer.created_at).all()
        result = []
        for s in servers:
            result.append({
                "id": s.id,
                "name": s.name,
                "transport": s.transport,
                "enabled": s.enabled,
                "status": s.status,
                "tool_count": s.tool_count,
                "last_error": s.last_error,
                "command": s.command,
                "url": s.url,
            })
        return result


# 全局单例
mcp_manager = McpManager()
