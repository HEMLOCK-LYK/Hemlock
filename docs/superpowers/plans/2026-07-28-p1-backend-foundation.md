# Hemlock P1 实现计划 · 手工测试基座

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 Hemlock 后端核心——mitmproxy 抓包代理 + SQLite 流量存储 + FastAPI 统一工具 API + LLM 客户端，产出可独立运行的后端服务，前端可联调。

**Architecture:** 三层——mitmproxy addon 捕获流量入库 / FastAPI 提供统一工具 API（REST + WebSocket）/ LLM 客户端封装云端 API 调用。代理与核心解耦，通过 SQLite 共享数据。

**Tech Stack:** Python 3.10 / FastAPI / SQLAlchemy + SQLite / mitmproxy / httpx / OpenAI SDK / pytest

---

## Task 1: 初始化 monorepo 与后端工程骨架

**Files:**
- Create: `hemlock/backend/pyproject.toml`
- Create: `hemlock/backend/hemlock/__init__.py`
- Create: `hemlock/backend/hemlock/core/config.py`
- Create: `hemlock/backend/hemlock/main.py`
- Create: `hemlock/.gitignore`
- Create: `hemlock/README.md`

- [ ] **Step 1:** 初始化 git 仓库与目录结构
- [ ] **Step 2:** 编写 `pyproject.toml`（依赖：fastapi, uvicorn, sqlalchemy, mitmproxy, httpx, openai, pydantic-settings, pytest, pytest-asyncio, httpx 测试客户端）
- [ ] **Step 3:** 编写 `core/config.py`（Pydantic Settings：DB 路径、代理端口、LLM 配置）
- [ ] **Step 4:** 编写 `main.py`（FastAPI app 工厂，CORS，健康检查端点）
- [ ] **Step 5:** 创建虚拟环境，安装依赖，启动服务验证 `/health` 返回 200
- [ ] **Step 6:** git init + 首次提交

## Task 2: 数据模型与数据库初始化

**Files:**
- Create: `hemlock/backend/hemlock/models/__init__.py`
- Create: `hemlock/backend/hemlock/models/base.py`
- Create: `hemlock/backend/hemlock/models/project.py`
- Create: `hemlock/backend/hemlock/models/flow.py`
- Create: `hemlock/backend/hemlock/models/finding.py`
- Create: `hemlock/backend/hemlock/models/evidence.py`
- Create: `hemlock/backend/hemlock/models/task.py`
- Create: `hemlock/backend/hemlock/core/database.py`
- Test: `hemlock/backend/tests/test_models.py`

- [ ] **Step 1:** 编写 `base.py`（declarative base）与 `database.py`（engine、session、init_db）
- [ ] **Step 2:** 编写 Project / Flow / Finding / Evidence / Task 模型，含字段与关系
- [ ] **Step 3:** 编写测试：创建项目→添加 flow→添加 finding+evidence，查询验证关系
- [ ] **Step 4:** 运行测试通过
- [ ] **Step 5:** 提交

## Task 3: mitmproxy addon 流量捕获入库

**Files:**
- Create: `hemlock/proxy-addon/hemlock_addon/__init__.py`
- Create: `hemlock/proxy-addon/hemlock_addon/capture.py`
- Create: `hemlock/proxy-addon/hemlock_addon/runner.py`
- Test: `hemlock/backend/tests/test_capture.py`

- [ ] **Step 1:** 编写 `capture.py`：mitmproxy addon，hook `response` 事件，把请求响应写入 SQLite（source=proxy）
- [ ] **Step 2:** 编写 `runner.py`：用 mitmproxy 的 DumpMaster 或 cmd 启动代理的封装
- [ ] **Step 3:** 测试：mock 一个 flow 对象，调用 addon 的 response 处理，验证数据库写入一条记录
- [ ] **Step 4:** 运行测试通过
- [ ] **Step 5:** 提交

## Task 4: 流量查询 API

**Files:**
- Create: `hemlock/backend/hemlock/api/__init__.py`
- Create: `hemlock/backend/hemlock/api/flows.py`
- Create: `hemlock/backend/hemlock/schemas/flow.py`
- Create: `hemlock/backend/hemlock/services/flow_service.py`
- Test: `hemlock/backend/tests/test_flows_api.py`

- [ ] **Step 1:** 编写 `schemas/flow.py`（FlowResponse、FlowListResponse pydantic 模型）
- [ ] **Step 2:** 编写 `flow_service.py`（查询/分页/过滤/单条详情）
- [ ] **Step 3:** 编写 `api/flows.py`（GET /api/flows、GET /api/flows/{id}、POST /api/flows/{id}/replay）
- [ ] **Step 4:** 注册路由到 main.py
- [ ] **Step 5:** 测试：插入测试数据，调用 API 验证列表、分页、过滤、详情、重放
- [ ] **Step 6:** 运行测试通过
- [ ] **Step 7:** 提交

## Task 5: 请求发送与 Repeater API

**Files:**
- Create: `hemlock/backend/hemlock/api/request.py`
- Create: `hemlock/backend/hemlock/schemas/request.py`
- Create: `hemlock/backend/hemlock/services/request_service.py`
- Test: `hemlock/backend/tests/test_request_api.py`

- [ ] **Step 1:** 编写 request schema（SendRequestRequest：method/url/headers/body）
- [ ] **Step 2:** 编写 request_service：用 httpx 发送请求，响应存入 flow（source=repeater）
- [ ] **Step 3:** 编写 api/request.py（POST /api/request/send 返回响应 + flow_id）
- [ ] **Step 4:** 测试：mock httpx，验证请求发送与入库
- [ ] **Step 5:** 提交

## Task 6: Intruder 爆破 API

**Files:**
- Create: `hemlock/backend/hemlock/api/intruder.py`
- Create: `hemlock/backend/hemlock/schemas/intruder.py`
- Create: `hemlock/backend/hemlock/services/intruder_service.py`
- Test: `hemlock/backend/tests/test_intruder_api.py`

- [ ] **Step 1:** 编写 intruder schema（IntruderConfig：base_request、positions、payloads、concurrency）
- [ ] **Step 2:** 编写 intruder_service：并发替换标记位发送请求，结果存 flow（source=intruder），返回汇总
- [ ] **Step 3:** 编写 api/intruder.py（POST /api/intruder/run、GET /api/intruder/{id}/result）
- [ ] **Step 4:** 测试：mock httpx，验证多 payload 替换与并发结果
- [ ] **Step 5:** 提交

## Task 7: LLM 客户端与 AI 辅助 API

**Files:**
- Create: `hemlock/backend/hemlock/core/llm_client.py`
- Create: `hemlock/backend/hemlock/api/ai.py`
- Create: `hemlock/backend/hemlock/schemas/ai.py`
- Create: `hemlock/backend/hemlock/services/ai_service.py`
- Test: `hemlock/backend/tests/test_ai_api.py`

- [ ] **Step 1:** 编写 llm_client.py：封装 OpenAI 兼容接口（支持 base_url 切换 OpenAI/Claude/DeepSeek），function calling 支持
- [ ] **Step 2:** 编写 ai_service：analyze_flow（分析指定流量给建议）、generate_payloads（根据场景生成 payload 字典）
- [ ] **Step 3:** 编写 api/ai.py（POST /api/ai/analyze、POST /api/ai/payloads）
- [ ] **Step 4:** 测试：mock LLM 返回，验证分析结果结构与 payload 列表
- [ ] **Step 5:** 提交

## Task 8: WebSocket 实时事件推送

**Files:**
- Create: `hemlock/backend/hemlock/api/ws.py`
- Create: `hemlock/backend/hemlock/services/event_bus.py`
- Test: `hemlock/backend/tests/test_ws.py`

- [ ] **Step 1:** 编写 event_bus（进程内 pub/sub，新流量/Agent 进度/漏洞事件）
- [ ] **Step 2:** mitmproxy addon 捕获流量后发布事件
- [ ] **Step 3:** 编写 ws.py（WS /ws/events 推送事件 JSON）
- [ ] **Step 4:** 测试：连接 WS，触发流量写入，验证收到事件
- [ ] **Step 5:** 提交

## Task 9: 目标范围锁定与安全中间件

**Files:**
- Create: `hemlock/backend/hemlock/core/scope.py`
- Create: `hemlock/backend/hemlock/core/rate_limit.py`
- Test: `hemlock/backend/tests/test_scope.py`

- [ ] **Step 1:** 编写 scope.py：校验请求 URL 是否在项目授权域内
- [ ] **Step 2:** mitmproxy addon 与 request_service 调用 scope 校验，越界拒绝
- [ ] **Step 3:** 编写 rate_limit.py：令牌桶限速（默认 10 req/s）
- [ ] **Step 4:** 测试：越界请求被拒、超速被节流
- [ ] **Step 5:** 提交

## Task 10: 后端启动入口与集成验证

**Files:**
- Modify: `hemlock/backend/hemlock/main.py`
- Create: `hemlock/backend/hemlock/cli.py`
- Create: `hemlock/backend/tests/test_integration.py`

- [ ] **Step 1:** 编写 cli.py：一键启动（初始化 DB + 启动 mitmproxy 代理 + 启动 FastAPI）
- [ ] **Step 2:** 集成测试：启动服务→配置浏览器代理→访问测试站点→验证流量入库→API 查询→AI 分析
- [ ] **Step 3:** 文档：README 记录启动方式与配置
- [ ] **Step 4:** 提交并打 tag v0.1.0-backend

---

## 后续（P1 前端，待 Rust 安装后）

- Task 11: Tauri v2 + React + TypeScript 工程初始化
- Task 12: API 客户端 + 状态管理（Zustand）
- Task 13: 流量列表视图（TanStack Table + 过滤）
- Task 14: Repeater 视图（请求编辑器 + 响应渲染）
- Task 15: Intruder 视图（标记位 + payload + 结果）
- Task 16: Target 地图视图
- Task 17: Tauri sidecar 打包 Python 后端
