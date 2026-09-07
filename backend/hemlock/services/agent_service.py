"""Agent 编排器：AI 自主渗透测试执行引擎。

工作流：
1. 规划：AI 根据目标 + 策略库生成测试计划
2. 执行：逐步执行，每步调用工具 API
3. 分析：AI 分析每步结果，决定下一步操作
4. 证据链：发现漏洞时自动收集请求响应快照
5. 确认：敏感操作（如批量爆破）暂停等待人工确认

工具定义（AI 可调用）：
- send_request: 发送单个 HTTP 请求
- send_session_request: 使用会话状态发送请求（自动携带 Cookie/Token）
- authenticate: 执行登录认证，建立会话
- extract_data: 从响应中提取数据（正则/JSON path）
- set_session_var: 设置会话变量
- analyze_flow: 分析流量安全风险
- generate_payloads: 生成 payload 字典
- run_intruder: 批量攻击（敏感操作，需人工确认）
- compare_responses: 比较两个响应的差异
- verify_vulnerability: 漏洞验证（混合模式=规则+LLM辅助 / 纯AI判断模式，由设置开关控制）
- llm_analyze_evidence: LLM 辅助证据分析（规则验证不确定时使用，仅混合模式有效）
- get_test_coverage: 获取当前测试覆盖度和未测试参数
- report_finding: 报告漏洞（必须附带证据）
- run_request_chain: 多步请求链编排（认证链/越权链/API链）
- list_reported_findings: 查看已报告漏洞列表（避免重复测试）
- send_raw_request: 直接构造并发送原始 HTTP 请求包（类似 Repeater）
- encode_decode: 编码/解码工具（URL/Base64/Hex/HTML/Unicode）
- get_flow_detail: 查看指定流量的完整请求/响应详情
- list_recon_reports: 列出所有已生成的侦察报告
- get_recon_report: 读取指定侦察报告的完整内容
- generate_pentest_report: 生成完整渗透测试报告（HTML/Markdown/JSON）

渗透测试高度适配设计：
- 自动追踪已发现的端点和参数，计算测试覆盖度
- 自动识别技术栈（Server/框架/语言/CMS），辅助策略选择
- 漏洞报告前自动验证，减少误报
- 上下文注入：每 5 步将测试进度摘要注入 AI 对话
"""

import json
import sys
import asyncio
import logging
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from sqlalchemy.orm import Session

from hemlock.core.database import SessionLocal
from hemlock.core.llm_client import chat, chat_with_retry, chat_stream_with_retry, get_token_usage
from hemlock.core.config import settings
from hemlock.core.scope import check_scope
from hemlock.core.pentest_strategies import build_strategy_prompt
from hemlock.core.vuln_verifier import auto_verify, llm_assisted_verify, VerificationResult
from hemlock.core.payload_library import get_payloads, get_recommended_payloads, list_scenes
from hemlock.core.pentest_context import (
    PentestContext,
    create_context,
    get_context,
    remove_context,
)
from hemlock.core.context_manager import (
    ContextAssembler,
    TokenEstimator,
    ToolOutputDistiller,
    HOT_BUDGET_TOKENS,
)
from hemlock.services import ai_service, intruder_service, recon_service
from hemlock.services.event_bus import event_bus
from hemlock.services.flowtag_engine import enrich_flow_fields
from hemlock.services.session_service import (
    session_manager,
    send_request_with_session,
    authenticate as session_authenticate,
    set_session_header,
    set_session_variable,
    extract_data_from_response,
    get_session_info,
)
from hemlock.models.task import Task, TASK_STATUS_IDLE, TASK_STATUS_RUNNING, TASK_STATUS_PAUSED, TASK_STATUS_DONE, TASK_STATUS_FAILED
from hemlock.models.finding import Finding, FINDING_STATUS_PENDING
from hemlock.models.evidence import Evidence
from hemlock.services.request_service import send_raw_request
from hemlock.models.flow import Flow
from hemlock.core.agent_registry import AgentRole, get_role_by_name, get_all_roles, ensure_builtin_roles, build_roles_prompt
from hemlock.models.llm_provider import LLMProvider

logger = logging.getLogger(__name__)


# ===== Agent 工具定义 =====
AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "send_request",
            "description": "发送一个无状态 HTTP 请求到目标。返回响应状态、头部、正文和 flow_id。适用于不需要会话状态的测试。生成的 flow 可被用户右键转发到 Repeater（多标签页）进行手动重放或转发到 Intruder 进行批量测试。默认直连目标；需要经上游代理访问目标时传 use_proxy=true。",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "HTTP 方法: GET/POST/PUT/DELETE/PATCH"},
                    "url": {"type": "string", "description": "完整 URL，必须在授权范围内"},
                    "headers": {"type": "string", "description": "请求头，每行一个 Key: Value"},
                    "body": {"type": "string", "description": "请求体（POST/PUT 时使用）"},
                    "use_proxy": {"type": "boolean", "description": "true 时经上游代理发送（需在设置页已配置；默认 false 直连）"},
                },
                "required": ["method", "url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_session_request",
            "description": "使用会话状态发送 HTTP 请求，自动携带 Cookie 和认证 Token。适用于需要登录态的测试（如越权测试、认证后功能测试）。默认直连目标；需要经上游代理访问目标时传 use_proxy=true。",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "HTTP 方法: GET/POST/PUT/DELETE/PATCH"},
                    "url": {"type": "string", "description": "完整 URL，必须在授权范围内"},
                    "headers": {"type": "string", "description": "额外请求头（会与 session header 合并）"},
                    "body": {"type": "string", "description": "请求体。可用 {{变量名}} 引用会话变量"},
                    "use_proxy": {"type": "boolean", "description": "true 时经上游代理发送（需在设置页已配置；默认 false 直连）"},
                },
                "required": ["method", "url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "authenticate",
            "description": "执行登录认证，建立会话。自动获取 CSRF token、管理 Cookie。认证后可用 send_session_request 进行需要登录的测试。",
            "parameters": {
                "type": "object",
                "properties": {
                    "login_url": {"type": "string", "description": "登录端点 URL"},
                    "method": {"type": "string", "description": "HTTP 方法，通常 POST", "default": "POST"},
                    "username": {"type": "string", "description": "用户名"},
                    "password": {"type": "string", "description": "密码"},
                    "extra_fields": {"type": "object", "description": "额外表单字段，如 {\"csrf_token\": \"xxx\"}"},
                    "success_indicator": {"type": "string", "description": "认证成功标志：状态码（如200）或响应中包含的字符串。留空则自动判断"},
                    "headers": {"type": "string", "description": "额外请求头"},
                },
                "required": ["login_url", "username", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_data",
            "description": "从指定流量的响应中提取数据。支持正则表达式和 JSON path。提取的数据存储为会话变量，可在后续请求中使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string", "description": "要提取数据的流量 ID"},
                    "pattern": {"type": "string", "description": "正则表达式或 JSON path（如 data.user.id）"},
                    "extract_type": {"type": "string", "enum": ["regex", "json"], "description": "提取方式", "default": "regex"},
                    "var_name": {"type": "string", "description": "存储为会话变量的名称，后续可用 {{var_name}} 引用"},
                    "group": {"type": "integer", "description": "正则分组号（0=整体匹配）", "default": 0},
                },
                "required": ["flow_id", "pattern", "var_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_session_var",
            "description": "手动设置会话变量，可在后续请求的 body 或 URL 中用 {{变量名}} 引用。用于请求串联场景。",
            "parameters": {
                "type": "object",
                "properties": {
                    "var_name": {"type": "string", "description": "变量名"},
                    "var_value": {"type": "string", "description": "变量值"},
                },
                "required": ["var_name", "var_value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_flow",
            "description": "分析指定流量的安全风险。返回风险等级、分析说明和建议。用于 AI 辅助判断响应中是否存在漏洞特征。",
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string", "description": "要分析的流量 ID"},
                    "question": {"type": "string", "description": "自定义分析问题（可选）"},
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_payloads",
            "description": "生成安全测试 payload 字典。支持 sqli/xss/fuzz/weak_password/waf_bypass 场景。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene": {
                        "type": "string",
                        "enum": ["sqli", "xss", "fuzz", "weak_password", "waf_bypass"],
                        "description": "payload 场景",
                    },
                    "context": {"type": "string", "description": "目标上下文（技术栈、参数等）"},
                    "count": {"type": "integer", "description": "生成数量", "default": 20},
                },
                "required": ["scene"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_intruder",
            "description": "对目标 URL 的指定参数进行批量 payload 攻击测试。这是敏感操作，需要人工确认后执行。返回每个 payload 的响应状态和长度。适用于 SQLi/XSS/弱口令等批量测试。每个结果都关联 flow_id，用户可右键将感兴趣的结果转发到 Repeater（多标签页）进行进一步分析。",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "HTTP 方法: GET/POST"},
                    "url": {"type": "string", "description": "目标 URL，含占位符 §§ 标记插入位置"},
                    "headers": {"type": "string", "description": "请求头"},
                    "body": {"type": "string", "description": "请求体，含 §payload§ 占位符"},
                    "payloads": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要测试的 payload 列表",
                    },
                    "marker": {"type": "string", "description": "payload 占位符标记", "default": "§"},
                    "use_session": {"type": "boolean", "description": "是否使用会话状态发送", "default": False},
                },
                "required": ["method", "url", "payloads"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_responses",
            "description": "比较两个流量的响应差异。用于布尔盲注验证（对比 AND 1=1 和 AND 1=2 的响应差异）和越权测试（对比不同用户的响应）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id_1": {"type": "string", "description": "第一个流量 ID"},
                    "flow_id_2": {"type": "string", "description": "第二个流量 ID"},
                },
                "required": ["flow_id_1", "flow_id_2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_vulnerability",
            "description": "自动验证疑似漏洞(减少误报)：SQL注入/ORDER BY注入/XSS/命令注入/路径穿越/SSRF/CORS/开放重定向，自动发对比请求。report_finding 前建议先调用。ORDER BY 位置用 SQL_INJECTION_ORDER_BY 类型。",
            "parameters": {
                "type": "object",
                "properties": {
                    "vuln_type": {
                        "type": "string",
                        "description": "漏洞类型: SQL_INJECTION/SQL_INJECTION_ORDER_BY/XSS/COMMAND_INJECTION/PATH_TRAVERSAL/SSRF/CORS_MISCONFIG/OPEN_REDIRECT/IDOR",
                    },
                    "method": {"type": "string", "description": "HTTP 方法"},
                    "url": {"type": "string", "description": "目标 URL"},
                    "param_name": {"type": "string", "description": "参数名（SQL 注入验证需要）"},
                    "param_value": {"type": "string", "description": "参数原始值"},
                    "headers": {"type": "string", "description": "请求头"},
                    "body": {"type": "string", "description": "请求体"},
                    "resp_body": {"type": "string", "description": "之前测试的响应体（用于 XSS 反射验证和错误注入验证）"},
                    "use_session": {"type": "boolean", "description": "是否使用会话状态发送", "default": False},
                },
                "required": ["vuln_type", "method", "url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "llm_analyze_evidence",
            "description": "使用 LLM 分析漏洞证据数据。当 verify_vulnerability 返回 verified=false 但 confidence>=0.2（不确定）时，或遇到规则不支持的新型漏洞时调用此工具。LLM 会分析原始请求/响应数据、响应差异、时间延迟等证据，判断规则可能遗漏的漏洞。也可用于分析自定义漏洞类型或不寻常的响应模式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "vuln_type": {"type": "string", "description": "疑似漏洞类型（如 SQL_INJECTION/XSS/COMMAND_INJECTION 或自定义类型）"},
                    "url": {"type": "string", "description": "目标 URL"},
                    "method": {"type": "string", "description": "HTTP 方法"},
                    "param_name": {"type": "string", "description": "测试参数名"},
                    "param_value": {"type": "string", "description": "参数原始值"},
                    "headers": {"type": "string", "description": "请求头"},
                    "body": {"type": "string", "description": "请求体"},
                    "resp_body": {"type": "string", "description": "测试响应体（关键证据）"},
                    "evidence": {"type": "string", "description": "verify_vulnerability 返回的 evidence JSON 字符串（如有）"},
                    "observation": {"type": "string", "description": "你对异常的观察描述（如'注入单引号后响应长度变化了200字节但相似度仍高'）"},
                },
                "required": ["vuln_type", "url", "method"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_test_coverage",
            "description": "获取当前测试覆盖度统计，包括已发现端点、已测试参数、未测试参数列表和下一步建议。调用此工具了解测试进度，确保不遗漏参数。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_payload_list",
            "description": "从内置 Payload 库获取标准化测试 payload。支持场景: sqli/xss/cmd_injection/path_traversal/ssrf/weak_password/open_redirect。payload 分 basic/standard/bypass 三个等级。系统会根据已知技术栈自动筛选最优 payload。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene": {
                        "type": "string",
                        "description": "payload 场景: sqli, xss, cmd_injection, path_traversal, ssrf, weak_password, open_redirect",
                    },
                    "level": {
                        "type": "string",
                        "enum": ["basic", "standard", "bypass"],
                        "description": "payload 等级筛选（可选，默认全部）",
                    },
                    "count": {"type": "integer", "description": "返回数量", "default": 15},
                },
                "required": ["scene"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quick_test_param",
            "description": "快速测试单个参数：自动从 Payload 库选择基础 payload，逐个发送并返回每个 payload 的响应状态和特征。用于快速探测参数是否存在漏洞，发现异常后再用 verify_vulnerability 确认。注意：这是批量操作，会发送多个请求。",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "HTTP 方法"},
                    "url": {"type": "string", "description": "目标 URL"},
                    "param_name": {"type": "string", "description": "要测试的参数名"},
                    "param_value": {"type": "string", "description": "参数原始值"},
                    "scene": {
                        "type": "string",
                        "description": "测试场景: sqli/xss/cmd_injection/path_traversal/ssrf",
                    },
                    "headers": {"type": "string", "description": "请求头"},
                    "body": {"type": "string", "description": "请求体"},
                    "use_session": {"type": "boolean", "description": "是否使用会话", "default": False},
                    "count": {"type": "integer", "description": "测试 payload 数量", "default": 5},
                },
                "required": ["method", "url", "param_name", "param_value", "scene"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_strategy",
            "description": "获取系统基于当前测试上下文（技术栈、已发现参数、测试进度）推荐的下一步测试策略。返回推荐的漏洞类型优先级和目标参数列表。建议每 10 步调用一次，优化测试效率。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_finding",
            "description": "报告发现的漏洞。必须附带证据（flow_id）和判定指标。报告后漏洞会进入待确认状态。建议先调用 verify_vulnerability 验证漏洞真实性。",
            "parameters": {
                "type": "object",
                "properties": {
                    "vuln_type": {"type": "string", "description": "漏洞类型: SQL_INJECTION/XSS/IDOR/SSRF/COMMAND_INJECTION/XXE/PATH_TRAVERSAL/AUTH_BYPASS/FILE_UPLOAD/CORS_MISCONFIG/..."},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low", "info"],
                        "description": "严重程度",
                    },
                    "title": {"type": "string", "description": "漏洞标题"},
                    "detail": {"type": "string", "description": "漏洞详情：包括发现过程、利用方式、影响范围"},
                    "flow_id": {"type": "string", "description": "证明漏洞的流量 ID（原始请求响应）"},
                    "indicator": {"type": "string", "description": "判定指标：为什么认为是漏洞（如响应中出现数据库错误、状态码变化等）"},
                },
                "required": ["vuln_type", "severity", "title", "flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_request_chain",
            "description": (
                "执行多步请求链：按顺序发送多个 HTTP 请求，每步可从上一步响应中提取数据（正则或 JSON path）"
                "注入到下一步请求中。适用于渗透测试中的请求串联场景："
                "登录→提取token→带token测试越权、获取列表→提取ID→访问详情→修改ID测试IDOR、"
                "获取CSRF token→带token提交表单等。所有请求共享会话状态（Cookie/Token）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": "请求链步骤列表，按顺序执行",
                        "items": {
                            "type": "object",
                            "properties": {
                                "method": {"type": "string", "description": "HTTP 方法: GET/POST/PUT/DELETE"},
                                "url": {"type": "string", "description": "目标 URL，可用 {{变量名}} 引用上一步提取的变量"},
                                "headers": {"type": "string", "description": "请求头，可用 {{变量名}} 引用变量"},
                                "body": {"type": "string", "description": "请求体，可用 {{变量名}} 引用变量"},
                                "extract": {
                                    "type": "array",
                                    "description": "从本步响应中提取数据，供后续步骤使用",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "var_name": {"type": "string", "description": "变量名，后续步骤用 {{var_name}} 引用"},
                                            "pattern": {"type": "string", "description": "正则表达式或 JSON path（如 data.token）"},
                                            "extract_type": {"type": "string", "enum": ["regex", "json"], "default": "regex"},
                                            "group": {"type": "integer", "default": 0, "description": "正则分组号"},
                                        },
                                        "required": ["var_name", "pattern"],
                                    },
                                },
                                "label": {"type": "string", "description": "步骤标签（可选，用于日志识别）"},
                            },
                            "required": ["method", "url"],
                        },
                    },
                    "use_session": {"type": "boolean", "description": "是否使用会话状态发送（默认 true）", "default": True},
                },
                "required": ["steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reported_findings",
            "description": (
                "查看当前任务已报告的漏洞列表。用于避免重复测试同一参数的同一漏洞类型，"
                "以及查看已发现漏洞的详情以规划利用链测试。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_raw_request",
            "description": (
                "直接构造并发送原始 HTTP 请求包（类似 Burp Suite Repeater）。"
                "传入完整的 HTTP 请求文本，系统自动解析请求行、Host 头和请求体后发送。"
                "适用于需要精确控制完整请求内容的场景，如构造畸形请求、修改 Host 头、"
                "测试 HTTP 请求走私等。比 send_request 更灵活，可完全控制每个字节。"
                "生成的 flow 可被用户右键转发到 Repeater / Intruder 做进一步测试。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "raw_request": {
                        "type": "string",
                        "description": (
                            "完整的原始 HTTP 请求文本，格式如:\n"
                            "GET /api/users HTTP/1.1\n"
                            "Host: example.com\n"
                            "Authorization: Bearer xxx\n"
                            "\n"
                            "{\"key\": \"value\"}"
                        ),
                    },
                    "use_session": {
                        "type": "boolean",
                        "description": "是否携带当前会话的 Cookie/Token。默认 false。",
                    },
                    "scheme": {
                        "type": "string",
                        "enum": ["http", "https"],
                        "description": "目标协议，默认 https。从 Host 头无法判断时使用此值。",
                    },
                    "use_proxy": {
                        "type": "boolean",
                        "description": "true 时经上游代理发送（需在设置页已配置；默认 false 直连）",
                    },
                },
                "required": ["raw_request"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "encode_decode",
            "description": (
                "编码/解码工具，支持 URL、Base64、Hex、HTML 实体、Unicode 转义等格式互转。"
                "用于构造 payload、解码响应内容、绕过 WAF 等场景。"
                "例如：将 payload 进行 URL 编码以绕过过滤，或将 Base64 响应解码查看明文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["encode", "decode"],
                        "description": "encode=编码, decode=解码",
                    },
                    "encoding_type": {
                        "type": "string",
                        "enum": ["url", "base64", "hex", "html", "unicode"],
                        "description": "编码类型: url(URL编码), base64(Base64), hex(十六进制), html(HTML实体编码), unicode(Unicode转义)",
                    },
                    "input": {
                        "type": "string",
                        "description": "要编码或解码的输入字符串",
                    },
                },
                "required": ["operation", "encoding_type", "input"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_flow_detail",
            "description": (
                "查看指定流量的完整请求和响应详情。通过 flow_id 获取原始的请求头、请求体、"
                "响应状态码、响应头和响应体。适用于回顾之前发送的请求结果、检查完整响应内容。"
                "重要提示：如果用户直接粘贴了外部工具（如 Burp Suite）的原始数据包，"
                "请先用 send_request 工具发送该请求生成平台流量，再用返回的 flow_id 调用本工具。"
                "本工具只能查看平台已记录的流量，无法直接解析外部原始数据包。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {
                        "type": "string",
                        "description": "流量 ID（send_request/send_session_request/run_intruder 等工具返回的 flow_id 字符串，如 'flw_xxx'）",
                    },
                },
                "required": ["flow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_flowtag_rules",
            "description": (
                "查看当前所有 FlowTag 正则匹配规则。FlowTag 规则用于自动标注特殊数据包"
                "（如检测到 Shiro 指纹、JWT、Swagger 等会在流量中添加标签）。"
                "返回规则名称、正则表达式、颜色、匹配范围、是否启用等信息。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_flowtag_rule",
            "description": (
                "添加一条 FlowTag 正则匹配规则。新规则会立即生效，后续捕获的流量将自动匹配。"
                "适用于 Agent 发现特定指纹或漏洞特征后，添加规则以便后续自动识别。"
                "例如：发现目标使用特定框架，添加规则自动标注该框架的流量。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "规则名称（如 'CustomFramework'）"},
                    "f_regex": {"type": "string", "description": "正则表达式（Python re 语法）"},
                    "color": {
                        "type": "string",
                        "enum": ["red", "orange", "yellow", "green", "cyan", "blue", "pink", "magenta", "gray"],
                        "description": "标签颜色：red=高危, orange=中危, yellow=低危, green=指纹, cyan=信息, blue=提示, gray=常规",
                        "default": "gray",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["any", "request", "response", "request header", "request body", "response header", "response body", "any header", "any body"],
                        "description": "匹配范围",
                        "default": "any",
                    },
                    "sensitive": {"type": "boolean", "description": "是否大小写敏感", "default": True},
                },
                "required": ["name", "f_regex"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_flowtag_rule",
            "description": (
                "删除指定名称的 FlowTag 正则匹配规则。"
                "适用于移除误报规则或不再需要的规则。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要删除的规则名称"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_api",
            "description": (
                "调用 Hemlock 平台任意 API 端点（/api 前缀自动补）。"
                "可操作流量(/flows)、代理(/proxy/*)、项目(/projects)、设置(/settings)、"
                "FlowTag(/flowtag/rules)、信息收集(/recon/*)、漏洞(/findings)。"
                "例: GET /flows、POST /proxy/start、POST /request/send、GET /findings。"
                "DELETE 与 PATCH /settings 为敏感操作需确认。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                        "description": "HTTP 方法",
                    },
                    "path": {
                        "type": "string",
                        "description": "API 路径（不含 /api 前缀），如 /flows、/proxy/start、/flows/123",
                    },
                    "body": {
                        "type": "object",
                        "description": "请求体 JSON（POST/PUT/PATCH 时使用）。如 {\"port\": 8080}",
                    },
                    "query_params": {
                        "type": "object",
                        "description": "URL 查询参数。如 {\"limit\": 50, \"project_id\": \"xxx\"}",
                    },
                },
                "required": ["method", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_repeater",
            "description": (
                "构造一个 HTTP 请求数据包并发送到 Repeater（重放器），用户可以在 Repeater 中查看、修改并发送该请求。\n"
                "适用于敏感操作（如注入 payload、RCE 测试）或需要用户手动审查和发送的关键请求。\n"
                "调用后前端会自动切换到 Repeater 页面并加载该数据包。\n"
                "注意：此工具不会自动发送请求，只是将构造好的数据包放入 Repeater 供用户操作。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "HTTP 方法: GET/POST/PUT/DELETE/PATCH",
                    },
                    "url": {
                        "type": "string",
                        "description": "完整 URL，如 https://example.com/api/login",
                    },
                    "headers": {
                        "type": "string",
                        "description": "请求头，每行一个 Key: Value",
                    },
                    "body": {
                        "type": "string",
                        "description": "请求体内容",
                    },
                    "note": {
                        "type": "string",
                        "description": "给用户的说明（可选），解释为什么要构造这个请求以及用户应该注意什么",
                    },
                },
                "required": ["method", "url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "执行系统终端命令（如 nmap、curl、whois、dig、nslookup、dirb 等）。\n"
                "适用于：端口扫描、DNS 查询、WHOIS 查询、目录爆破、文件操作等需要系统工具的场景。\n"
                "工作目录持久化：cwd 参数生效后，后续调用默认沿用同一目录（初始为工作区）；"
                "返回值中的 cwd 即当前目录，写入文件前先确认它在哪。\n"
                "长任务（全端口扫描、大字典爆破、慢速爬虫）传 run_in_background=true 立即返回，"
                "用 check_background 轮询状态与输出，不要让对话干等。\n"
                "注意：运行环境是 Windows(cmd)，无 grep/head/sed/awk/tail 等 Unix 命令（会报'不是内部或外部命令'）；"
                "文本处理用 Python(run_script) 或 node 替代，如 run_script 执行 'import re;print(...)'。\n"
                "此为敏感操作，需用户确认后执行。危险命令（rm -rf、format、shutdown 等）会被拦截。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的完整命令，如 'nmap -sV -p 1-1000 192.168.1.1' 或 'curl -I https://example.com'",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时时间（秒），默认 30，最大 600（弱口令爆破等长任务建议调大或改用 run_in_background）",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "本次命令的工作目录（相对当前目录或工作区内的绝对路径），并持久化为后续默认目录",
                    },
                    "run_in_background": {
                        "type": "boolean",
                        "description": "true 时转入后台执行，立即返回 background_id 与日志路径（配合 check_background 使用）",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_script",
            "description": (
                "执行 Python 脚本代码并返回输出。\n"
                "适用于：数据解析、自定义漏洞验证脚本、加密/解密计算、响应数据分析等需要编程能力的场景。\n"
                "脚本在独立子进程中运行，支持 import 标准库。\n"
                "注意：此为敏感操作，需用户确认后执行。超时默认 30 秒。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python 脚本代码，print() 输出会被捕获返回",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时时间（秒），默认 30，最大 120",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "将内容写入文件，保存在工作区目录下。\n"
                "适用于：保存脚本、记录测试结果、导出数据等。\n"
                "文件路径相对于工作区目录，不支持绝对路径和 .. 上溯。\n"
                "写入成功后返回值中的 absolute_path 即为真实磁盘路径，可直接告知用户。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对工作区），如 'scripts/exploit.py' 或 'results/scan.txt'",
                    },
                    "content": {
                        "type": "string",
                        "description": "文件内容",
                    },
                    "append": {
                        "type": "boolean",
                        "description": "是否追加写入（默认 false，覆盖写入）",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取工作区目录下的文件内容。\n"
                "适用于：读取之前保存的脚本、测试结果、配置文件等。\n"
                "文件路径相对于工作区目录，不支持绝对路径和 .. 上溯。最大读取 100KB。\n"
                "大文件用 offset（起始行号，从 1 开始）+ limit（行数）分页读取；"
                "二进制文件会被拒收并提示改用 run_command/run_script。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对工作区），如 'scripts/exploit.py'",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（从 1 开始），默认 1",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "读取的行数，不传则读到文件尾（受 100KB 上限）",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "列出工作区目录内容，用于查看目录结构与确认文件落盘结果。\n"
                "支持 glob 过滤（如 '*.md'、'报告/*'）与 recursive 递归列举（最多返回 200 项）。\n"
                "路径相对于工作区目录，返回相对路径可直接用于 read_file/write_file。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径（相对工作区），默认 '.' 即工作区根目录",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "glob 过滤模式，如 '*.log'、'种子*'，不传则列出全部",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "是否递归列举子目录（默认 false，只列一层）",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_background",
            "description": (
                "查询或终止 run_command 的后台任务（run_in_background=true 启动时返回 background_id）。\n"
                "action=status（默认）返回运行状态、输出尾部 output_tail；action=kill 强制终止进程。\n"
                "任务结束后后台句柄不再保留，但日志文件仍在磁盘，可按 log_path 用 read_file 读取。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "background_id": {
                        "type": "string",
                        "description": "run_command 后台启动时返回的 background_id",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["status", "kill"],
                        "description": "status=查询状态与输出尾部（默认），kill=终止进程",
                    },
                },
                "required": ["background_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": (
                "用内置浏览器（本机 Chrome/Edge 无头实例）打开 URL，执行页面 JS 后返回渲染结果："
                "最终 URL、标题、可见文本、前 100 个链接、控制台错误。\n"
                "适用于：SPA/动态渲染页面（send_request 拿不到 JS 生成内容时）、JS 挑战、"
                "需要真实浏览器环境的登录流程。\n"
                "同一任务复用同一标签页，登录态跨调用保持；浏览器全局共享一个进程，首次调用需数秒启动。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要打开的 URL（http/https）",
                    },
                    "wait_seconds": {
                        "type": "integer",
                        "description": "load 事件后的额外等待秒数（JS 异步渲染重的页面建议 2-4），默认 0，最大 15",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_evaluate",
            "description": (
                "在当前浏览器页面内执行 JavaScript 并返回结果（支持 async IIFE 与 Promise）。\n"
                "用于：点击/填表/提交表单、提取页面数据、读取 localStorage/sessionStorage、"
                "调用页面同源 API（自动带 Cookie）。执行前需先 browser_navigate 到目标页。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "JS 表达式，如 `document.querySelector('#login').click()` 或 async IIFE",
                    },
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": (
                "对当前浏览器页面截图，保存到工作区 screenshots/ 目录，返回绝对路径。\n"
                "用于：漏洞证据留存、渲染结果确认。full_page=true 时截整页。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "文件名（不含扩展名，非法字符自动替换），默认时间戳命名",
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": "是否截取整页（默认 false，仅视口）",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_cookies",
            "description": (
                "返回当前浏览器页面可见的 Cookies（name/value/domain/httpOnly/secure）。\n"
                "用于：确认登录态是否生效、会话属性核查。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": (
                "重置当前任务的浏览器标签页（跳转空白页并释放会话引用，浏览器进程保留给其他任务复用）。"
                "仅在需要重新开始浏览会话时调用；应用退出会自动回收浏览器进程。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "将一条经验保存到长期记忆库，让未来的会话能从中受益。\n"
                "适用于：踩坑教训、被纠正的方法论、使用者偏好、项目约束等值得跨会话复用的经验。\n"
                "每条记忆必须包含 name（唯一标识）、description（一句话描述）、type（记忆类型）、trigger（关键词）。\n"
                "feedback 和 project 类型必须包含 Why 和 How to apply。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "记忆唯一名称（英文短横线连接，如 'waf-encoding-bypass'）",
                    },
                    "description": {
                        "type": "string",
                        "description": "一句话描述这条记忆是什么",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["user", "feedback", "project", "reference"],
                        "description": "user=使用者偏好, feedback=踩坑纠正, project=项目约束, reference=外部资源",
                    },
                    "trigger": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "关键词列表，便于用户在前端搜索和分类管理",
                    },
                    "content": {
                        "type": "string",
                        "description": "记忆正文。feedback/project 类型必须包含 **Why:** 和 **How to apply:** 部分",
                    },
                },
                "required": ["name", "description", "type", "trigger", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_memory",
            "description": (
                "读取指定长期记忆的完整内容。记忆索引（名称+描述+触发词）已注入系统提示，"
                "当某条记忆与当前任务相关、需要完整正文（如踩坑教训、项目约束、使用偏好）时调用此工具。"
                "用于按需加载记忆正文，避免每次对话全量注入记忆导致 Token 消耗过大。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "记忆名称（从记忆索引中获取，如 waf-encoding-bypass）",
                    },
                },
                "required": ["memory_id"],
            },
        },
    },
    # ===== 外部情报工具（N-day 漏洞情报）=====
    {
        "type": "function",
        "function": {
            "name": "cve_lookup",
            "description": (
                "按组件+版本查询已知漏洞（CVE/N-day）。需先 load_tools(\"discovery\") 加载后可用。"
                "仅在已确认具体组件+版本（高置信度指纹，如响应头/页面明确标识 nginx 1.18.0、"
                "Apache 2.4.49）时调用；大多数测试靠推理+现有工具即可，不需要联网。"
                "返回 CVE 编号/严重度/描述/PoC 线索，命中后结合 web_search 查利用细节、"
                "verify_vulnerability 实测。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "组件名（如 nginx / apache / wordpress / log4j / spring）",
                    },
                    "version": {
                        "type": "string",
                        "description": "组件版本号（如 1.18.0 / 2.4.49）；未知可留空查该组件高危",
                    },
                },
                "required": ["component"],
            },
        },
    },
    # ===== 攻击面发现工具（发现驱动：让 Agent 自己找该测什么）=====
    {
        "type": "function",
        "function": {
            "name": "analyze_js",
            "description": (
                "抓目标页面引用的 JS 文件，提取隐藏的 API 接口/参数（SPA 接口大多藏 JS 里）。"
                "返回接口清单后用 quick_test_param/send_request 逐个体检。测试前强烈建议先调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "目标页面 URL（如 https://example.com 或 https://example.com/app），须在授权范围内",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fuzz_paths",
            "description": (
                "对目标站点的常见路径进行字典爆破（发现驱动核心工具）。\n"
                "用内置字典探测 /admin、/api、/.git、/backup、/swagger 等隐藏路径，"
                "发现 JS 中未暴露的管理后台、备份文件、敏感配置。\n"
                "只返回非 404 的命中结果（含状态码和响应大小）。\n"
                "发现命中后用 send_request 确认，或用 quick_test_param 测试参数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "目标基础 URL，如 https://example.com，须在授权范围内",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "自定义路径列表（可选，如 ['/admin', '/.git']，默认使用内置字典）",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "联网搜索公开信息（DuckDuckGo，免费无 Key）。用于查组件已知漏洞利用、"
                "最新安全公告、漏洞 PoC 细节、配置绕过技巧、目标背景信息等。"
                "返回标题/摘要/URL。与 cve_lookup 互补：cve_lookup 查结构化 CVE，"
                "web_search 查非结构化情报与最新动态。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如 'nginx 1.18.0 CVE exploit'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    # ===== 信息收集工具（从 recon_agent_service 合并）=====
    {
        "type": "function",
        "function": {
            "name": "recon_search",
            "description": (
                "多引擎并行资产搜索：fofa/quake/hunter/zoomeye/alpha(需Key)、"
                "chaos(需Key)、crtsh/hackertarget/dns(免费)。"
                "测绘引擎用各自语法(如 domain=\"example.com\")，子域名/DNS 引擎直接给域名。"
                "结果自动存 Markdown 报告。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "搜索查询语句。对于空间测绘引擎使用各自语法"
                            "（如 FOFA: domain=\"example.com\"）；"
                            "对于子域名/DNS 引擎直接输入域名（如 example.com）。"
                        ),
                    },
                    "engines": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["fofa", "quake", "hunter", "zoomeye", "alpha", "chaos", "crtsh", "hackertarget", "dns"],
                        },
                        "description": "要使用的引擎列表，支持多引擎并行搜索。",
                    },
                    "size": {
                        "type": "integer",
                        "description": "每个引擎返回的最大结果数，默认 100",
                        "default": 100,
                    },
                },
                "required": ["query", "engines"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "subdomain_enum",
            "description": (
                "子域名枚举：使用 crt.sh（证书透明度日志）、HackerTarget、Chaos 等多个免费源"
                "自动发现目标的子域名。只需输入主域名，自动并行查询所有源并去重。"
                "如果配置了 Chaos API Key，会额外使用 Chaos 进行查询。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "主域名，如 example.com",
                    },
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dns_lookup",
            "description": (
                "DNS 记录查询：查询指定域名的 DNS 记录（A、CNAME、MX、TXT、NS）。"
                "通过 Google DNS over HTTPS 查询，免费且快速。"
                "用于了解目标域名解析情况、发现邮件服务器、CDN 配置等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "要查询的域名，如 example.com",
                    },
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_probe",
            "description": (
                "HTTP 探测：向指定 URL 发送 HTTP 请求，获取响应状态码、响应头、"
                "页面标题和响应体摘要。用于：\n"
                "- 识别目标网站技术栈（Server、X-Powered-By 等响应头）\n"
                "- 检查常见路径（/robots.txt、/sitemap.xml、/.git/ 等）\n"
                "- 探测开放的服务和端口\n"
                "- 获取页面标题和基本信息"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要探测的 URL，如 https://example.com 或 https://example.com/robots.txt",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["GET", "HEAD", "POST"],
                        "description": "HTTP 方法，默认 GET",
                        "default": "GET",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recon_reports",
            "description": (
                "列出所有已生成的侦察报告（信息收集报告）。"
                "recon_search 工具执行后会自动生成 Markdown 格式的侦察报告，"
                "可用此工具查看历史报告列表，了解之前的信息收集成果。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recon_report",
            "description": (
                "读取指定侦察报告的完整内容。先用 list_recon_reports 获取报告文件名，"
                "再传入 filename 参数读取完整报告内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "报告文件名（从 list_recon_reports 获取）",
                    },
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_pentest_report",
            "description": (
                "生成完整的渗透测试报告，汇总当前项目的所有漏洞发现、证据链和测试任务。"
                "支持 HTML、Markdown、JSON 三种格式。报告包含漏洞统计、严重程度分布、"
                "每个漏洞的详细证据（请求/响应数据包）和 AI 分析。"
                "建议在测试完成后调用此工具生成最终报告。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["html", "markdown", "json"],
                        "description": "报告格式，默认 html",
                        "default": "html",
                    },
                },
            },
        },
    },
]

# 子 Agent 工具：启动专职子 Agent 分工（主 Agent 与指挥官均可用，子 Agent 自身禁用防止递归）
SPAWN_SUBAGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "spawn_subagent",
        "description": (
            "启动一个子 Agent 执行特定角色的分工任务。"
            "适合把独立、聚焦、多步骤的活儿委托出去：如让 scout 做一次完整的攻击面收集、"
            "让 radar 做技术栈识别与 CVE 关联、让 auditor 复核一批可疑结果、"
            "让 scribe 汇总生成本阶段报告。子 Agent 使用角色专属提示词与独立上下文运行，"
            "共享当前测试上下文（PentestContext），发现会自动记录，完成后返回执行摘要。"
            "不要把所有工作都交给子 Agent——简单、单步的操作直接自己做更高效。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": (
                        "（可选）专业角色名称：scout(侦察员:信息收集/端点发现)、"
                        "striker(攻击手:漏洞测试/Payload执行)、"
                        "auditor(验证员:漏洞复核/误报排除)、"
                        "radar(情报员:技术栈识别/CVE关联)、"
                        "scribe(报告员:报告生成/发现汇总)。"
                        "不填或任务与角色不完全匹配时，子 Agent 作为通用执行单元按任务目标直接执行"
                    ),
                },
                "goal": {
                    "type": "string",
                    "description": "子 Agent 的具体任务目标，应清晰描述要完成的工作（以目标为准，角色只是参考）",
                },
            },
            "required": ["goal"],
        },
    },
}

# 信息收集工具已合并回主 Agent，通过 system_prompt_override 切换资产收集角色


# ===== 统一工具注册 =====

# ===== 工具分组（按需加载，对齐技能/记忆的"索引+按需"模式）=====
# 核心工具始终注入（HTTP 主循环必需）；发现/文件类工具按需通过 load_tools 加载，
# 显著减少每次请求注入的工具 token（避免"用户说一句话就全量塞 41 个工具"）。
DISCOVERY_TOOL_NAMES = frozenset({
    "analyze_js", "fuzz_paths", "http_probe", "dns_lookup",
    "subdomain_enum", "recon_search", "list_recon_reports", "get_recon_report",
    "web_search", "cve_lookup",
})
FILE_TOOL_NAMES = frozenset({
    "run_command", "run_script", "read_file", "write_file",
    "list_files", "check_background",
})
BROWSER_TOOL_NAMES = frozenset({
    "browser_navigate", "browser_evaluate", "browser_screenshot",
    "browser_cookies", "browser_close",
})
# 组名 → 工具名集合
TOOL_GROUPS: dict[str, frozenset[str]] = {
    "discovery": DISCOVERY_TOOL_NAMES,
    "file": FILE_TOOL_NAMES,
    "browser": BROWSER_TOOL_NAMES,
}
# 组名 → 展示用工具列表（load_tools 返回值告知 Agent 已加载了什么）
GROUP_TOOL_LISTS = {
    "discovery": "analyze_js / fuzz_paths / web_search / cve_lookup / http_probe / dns_lookup / subdomain_enum / recon_search / recon报告",
    "file": "run_command / run_script / read_file / write_file / list_files / check_background",
    "browser": "browser_navigate / browser_evaluate / browser_screenshot / browser_cookies / browser_close",
}
# 所有按需加载的工具名（用于过滤核心）
OPTIONAL_TOOL_NAMES = DISCOVERY_TOOL_NAMES | FILE_TOOL_NAMES | BROWSER_TOOL_NAMES

# 指挥官（协作模式编排者）只读监控工具集。
# 指挥官是总调度者，不直接执行测试（发请求 / 爆破 / 文件 / 情报侦察都委派给子 Agent）。
# 收敛工具定义的好处：
# - 防止指挥官越权直接攻击（与 COORDINATOR_PROMPT「不直接发 HTTP 请求」对齐）
# - 消除工具定义与执行层冲突（SENSITIVE_TOOLS 会被执行层挡住，白费模型调用）
# - 省 Token（指挥官每次请求不再全量携带 43 个工具定义）
COORDINATOR_READONLY_TOOLS = frozenset({
    "get_test_coverage",
    "list_reported_findings",
    "get_flow_detail",
    "get_strategy",
    "get_payload_list",
    "llm_analyze_evidence",
    "get_memory",
    "list_recon_reports",
    "get_recon_report",
    "list_flowtag_rules",
})

# load_tools 工具：始终注入，Agent 需要时加载工具组
LOAD_TOOLS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "load_tools",
        "description": (
            "按需加载工具组（减少每次请求的 Token 开销）。可用组：\n"
            "- discovery：攻击面发现与情报（analyze_js 提取JS接口 / fuzz_paths 路径爆破 / "
            "web_search 联网搜索 / cve_lookup 查CVE / http_probe 探测 / dns_lookup / "
            "subdomain_enum / recon_search 资产搜索 / 侦察报告）\n"
            "- file：终端与文件（run_command 执行命令支持后台运行与持久工作目录 / run_script 执行Python / "
            "read_file / write_file / list_files 列目录 / check_background 查询后台命令）\n"
            "- browser：内置浏览器（browser_navigate 打开页面渲染JS / browser_evaluate 页面内执行JS / "
            "browser_screenshot 截图 / browser_cookies 查Cookie / browser_close 关闭），适用于SPA动态页面、"
            "JS挑战、需要真实浏览器环境渲染或截图取证的场景\n"
            "需要用到某组工具时先调用本工具，该组工具下一轮即刻可用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "enum": ["discovery", "file", "browser"],
                    "description": "要加载的工具组名",
                }
            },
            "required": ["group"],
        },
    },
}

# 实战文档库加载工具（文档库存在即注入；报告格式一篇全局可用，其余面向 SRC 模式）
LOAD_SRC_DOC_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "load_src_doc",
        "description": (
            "加载内置实战文档库中的一篇，返回全文。\n"
            "写漏洞报告前先加载 'vuln-report-format'，按其中的报告格式与定级标准产出报告（所有模式通用）；\n"
            "其余规则与漏洞知识库（'dig-scope-workflow'、'打穿短表'、'idor-test' 等）属 SRC 漏洞挖掘模式，\n"
            "加载哪些、何时加载遵循系统提示中 SRC 模式的索引表：对得上再开，禁止通读。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "文档名或相对路径（不带 .md 亦可）",
                },
            },
            "required": ["name"],
        },
    },
}


def build_agent_tools(
    db: Session | None = None,
    include_coordinator_tools: bool = False,
    loaded_groups: set[str] | None = None,
    coordinator_mode: bool = False,
    exclude_sensitive: bool = False,
) -> list[dict]:
    """构建 Agent 可用的完整工具列表（统一入口）。

    合并多类工具到统一 OpenAI function calling 格式：
    1. 内置工具 (AGENT_TOOLS)——按 loaded_groups 过滤：核心工具始终注入，
       按需组（discovery/file）仅在 loaded_groups 中时才注入
    2. load_tools 工具（始终注入，用于按需加载工具组）
    3. 指挥官专用工具（include_coordinator_tools=True 时追加 SPAWN_SUBAGENT_TOOL）
    4. 已启用的 Skills -> invoke_skill 工具（渐进式披露）
    5. 已连接的 MCP 服务器工具 -> mcp_{server}_{tool} 命名

    Args:
        db: 数据库会话，用于查询已启用的 Skill 和 MCP 服务器。
            为 None 时仅返回内置工具。
        include_coordinator_tools: 是否包含指挥官专用工具（spawn_subagent）。
        loaded_groups: 已加载的按需工具组集合（如 {"discovery"}）。
            为 None 时加载全部工具（兼容旧调用：子 Agent/指挥官/测试）。
        coordinator_mode: 指挥官模式。仅返回只读监控工具 + spawn_subagent，
            不注入 load_tools / invoke_skill / MCP（执行向工具不进入编排视野）。
        exclude_sensitive: 剔除 SENSITIVE_TOOLS（run_intruder/run_command/run_script）。
            子 Agent 无人工确认通道，这些工具执行时会被挡，剔除后模型不再试调。

    Returns:
        OpenAI function calling 格式的工具列表。
    """
    if coordinator_mode:
        # 指挥官模式：只读监控 + spawn_subagent，直接返回（不走下方动态注入）
        tools = [
            t for t in AGENT_TOOLS
            if t["function"]["name"] in COORDINATOR_READONLY_TOOLS
        ]
        if include_coordinator_tools:
            tools.append(SPAWN_SUBAGENT_TOOL)
        return tools

    if loaded_groups is None:
        tools = list(AGENT_TOOLS)
    else:
        loaded_names: set[str] = set()
        for g in loaded_groups:
            loaded_names |= TOOL_GROUPS.get(g, set())
        tools = [
            t for t in AGENT_TOOLS
            if t["function"]["name"] not in OPTIONAL_TOOL_NAMES
            or t["function"]["name"] in loaded_names
        ]

    # 子 Agent / 对话等无人工确认通道的场景，剔除敏感工具定义（执行层保护仍在）
    if exclude_sensitive:
        sensitive = AgentOrchestrator.SENSITIVE_TOOLS
        tools = [t for t in tools if t["function"]["name"] not in sensitive]

    # load_tools 始终可用（核心工具，用于按需加载工具组）
    tools.append(LOAD_TOOLS_TOOL_SCHEMA)

    # 实战文档库：存在即注入 load_src_doc（报告格式一篇全模式可用；
    # 其余文档面向 SRC 模式）。独立于 skill 体系（前端技能列表不可见）。
    if _src_pack_available():
        tools.append(LOAD_SRC_DOC_TOOL_SCHEMA)

    # 指挥官专用工具（spawn_subagent）——子 Agent 不可使用，防止递归
    if include_coordinator_tools:
        tools.append(SPAWN_SUBAGENT_TOOL)

    if not db:
        return tools

    # 动态追加 invoke_skill（仅当有已启用 skill）
    try:
        from hemlock.core.skill_registry import (
            INVOKE_SKILL_TOOL_SCHEMA,
            get_enabled_skills,
        )
        if get_enabled_skills(db):
            tools.append(INVOKE_SKILL_TOOL_SCHEMA)
    except Exception as e:
        logger.debug(f"Skill 工具注册跳过: {e}")

    # 动态追加 MCP 工具
    try:
        from hemlock.core.mcp_manager import mcp_manager
        mcp_tools = mcp_manager.get_enabled_tools(db)
        if mcp_tools:
            tools.extend(mcp_tools)
    except Exception as e:
        logger.debug(f"MCP 工具注册跳过: {e}")

    return tools


SYSTEM_PROMPT = """你是 Hemlock 的 AI 安全测试 Agent，辅助用户完成 Web 安全测试任务。

## 交互原则
1. **任务驱动**：先理解用户意图再行动；意图不明或没有明确目标（URL/IP）时先询问，不要直接开测。
2. **简洁行动，总结收尾**：调用工具时一句话说明；详细分析放最终回复。
3. **测试纪律**：证据链驱动（报漏洞必须带 flow_id 证据）；先验证后报告（verify_vulnerability 三态：verified→报告 / confidence<0.2→排除 / confidence>=0.2→不确定，用 llm_analyze_evidence 或换 payload）；先侦察后攻击；定期 list_reported_findings 避免重复测试。
4. **技能使用（辅助，非必须）**：技能是参考加速器，不是流程前提。场景与「可用技能」匹配时，可先 invoke_skill 加载指令作为参考；没有匹配技能或不确定时，直接按自身推理 + 现有工具执行，不要因为没有技能而卡住或停止。
5. **合规红线**：禁止内网横向/业务中断/后门/删除；SQL 注入只取库名/版本/用户名不深入；写入操作先确认；所有请求须在授权范围。

## 方法论（详细策略已注入下方策略库，此处为执行流程）
工具按需加载：核心工具始终可用；发现/情报类（analyze_js/fuzz_paths/web_search/cve_lookup/http_probe/recon_search 等）、文件类（run_command/read_file 等）与浏览器类（browser_navigate/browser_evaluate 等，SPA 动态页面/截图取证时用）需先调用 load_tools(组名) 加载后可用。
给目标并要求测试时，按三阶段推进：
1. **侦察**：先 analyze_js / fuzz_paths / http_probe 发现攻击面（接口/参数/技术栈），对参数投探针收集异常信号。
2. **深测**：构建系统画像（Swagger/API 清单），梳理权限边界，逆向推演开发者易错点，生成 P0/P1/P2 推论逐条验证。
3. **验证**：每推论执行后反馈 {命中?, 新发现, 衍生问题}；命中→verify_vulnerability→report_finding；不确定→llm_analyze_evidence。
   定期 get_test_coverage 查覆盖度、list_reported_findings 避免重复。

## 情报工具纪律（cve_lookup / web_search 谨慎使用）
- 情报工具是**验证假设**用的，不是**替代思考**用的：先基于已识别技术栈自行推理高危点，再决定是否查证。
- `cve_lookup` / `web_search` 在 **discovery 按需组**——需要时先 `load_tools("discovery")` 加载才能用；仅在**确认具体组件+版本**（高置信度指纹）时调用，大多数漏洞测试靠内置技能知识 + 推理即可，不需要联网。
- `web_search` 仅在需要**利用细节或最新公开情报**时才调用；不要对每个组件/端点/参数都联网搜索。
- 典型节奏：本地推理 + 现有工具（quick_test_param/verify_vulnerability）先测 → 确有 N-day 疑点时才查证 → 命中再深挖利用。
- 网络行为：情报请求**默认直连**，直连失败（被墙/不可达）会自动借用用户配置的「上游代理」重试一次。

## 请求链 / 联动
- run_request_chain 编排多步（认证链/越权链/API链）；可疑响应提示用户可转发 Repeater/Intruder。

## 子 Agent 分工（spawn_subagent）
- 遇到**独立、聚焦、多步骤**的工作可 spawn 专职子 Agent 委托执行：scout 收集攻击面、radar 技术栈+CVE 关联、auditor 复核可疑结果、scribe 汇总结报告。
- 子 Agent 共享测试上下文、发现自动记录，完成后返回摘要。**只委托值得委托的活**——简单/单步操作直接自己做，避免为小事开子 Agent 浪费 token。
- 子 Agent 是提高效率的手段，不是必须流程；不确定是否需要时，默认自己干。
- **角色可选不强制**：spawn_subagent 可省略 role（通用执行单元），或按任务类型选最接近的专业角色；无论是否指定角色，子 Agent 都以 goal 任务目标为准执行，不会被角色定位限制。

## 记忆
- save_memory 沉淀跨会话经验（4 类：user/feedback/project/reference；feedback/project 含 **Why** + **How to apply**，用 [[名称]] 关联）；get_memory 按需读完整正文。写前查重。

## 输出与终止
- 中间步骤一句话；最终回复结构化总结（发现+证据+建议）；测试完成回复 "TASK_COMPLETE"。
- 终止条件：覆盖率≥80% 且推论验证完 / 连续 3 轮无新发现 / 用户要求停止。
- 异常：超时/报错重试 1 次；验证不确定用 llm_analyze_evidence；认证失败检查凭据后询问。
"""

# 注入策略库到系统提示词
SYSTEM_PROMPT += build_strategy_prompt()

# 自动化渗透测试模式追加提示
AUTO_PENTEST_PROMPT = """

## 当前模式：自动化渗透测试
用户给出目标 URL 时：确认目标后立即开始侦察，无需反复询问；按 侦察→深测→验证 推进，每阶段简要汇报；发现漏洞立即验证报告；敏感操作暂停询问；测完输出总结报告。
"""


# ===== SRC 漏洞挖掘模式（独立文档库，不进 skill 体系） =====
# 文档库位于 data/src_pack（gitignore 不入库，打包时随 data 目录进 exe）。
# 与 skill_registry 完全隔离：不进用户技能目录、不出现在前端技能列表、
# 不参与 build_skills_prompt —— 仅通过 load_src_doc 工具按需读全文。
_SRC_PACK_DIR = Path(__file__).parent.parent / "data" / "src_pack"
_SRC_PROMPT_CACHE: str | None = None


def _src_pack_available() -> bool:
    """文档库是否可用（存在 SRC_PROMPT.md 入口）。"""
    if _SRC_PROMPT_CACHE:
        return True
    return (_SRC_PACK_DIR / "SRC_PROMPT.md").is_file()


def _load_src_prompt() -> str:
    """读取 SRC 主控提示词（src_pack/SRC_PROMPT.md），模块级缓存。

    注入稳定段（system_content 追加），缓存后逐字节稳定，跨请求缓存前缀可命中；
    文件变更需重启后端生效（提示词不是热改内容）。缺失返回空串。
    """
    global _SRC_PROMPT_CACHE
    if _SRC_PROMPT_CACHE is None:
        try:
            _SRC_PROMPT_CACHE = (_SRC_PACK_DIR / "SRC_PROMPT.md").read_text(encoding="utf-8")
        except OSError:
            _SRC_PROMPT_CACHE = ""
    return _SRC_PROMPT_CACHE


# ===== 多 Agent 协作：指挥官系统提示词 =====

COORDINATOR_PROMPT = """你是 Hemlock 多 Agent 协作的 **指挥官（Coordinator）**，负责编排多个专用 Agent 协同完成渗透测试任务。

## 角色定位
你不是直接执行测试的 Agent，而是任务的总调度者。你的核心价值在于：
- 分析任务，拆解为可分配的子任务
- 选择合适的角色 Agent 执行各子任务
- 汇总各角色的执行结果，做出全局决策
- 确保测试覆盖完整、漏洞验证充分、报告质量达标

## 可用角色
通过 spawn_subagent 工具启动以下角色 Agent：
- **scout（侦察员）**：信息收集、端点发现、参数枚举、认证探测
- **striker（攻击手）**：漏洞测试、Payload 执行、利用链推导、漏洞报告
- **auditor（验证员）**：漏洞复核、误报排除、证据链完善、影响评估
- **radar（情报员）**：技术栈识别、指纹采集、CVE 关联、WAF 检测
- **scribe（报告员）**：报告生成、发现汇总、风险评级、修复建议

## 工作流程
1. **分析任务**：理解用户目标，确定测试范围和优先级
2. **选择角色**：根据任务类型选择需要启动的角色 Agent
3. **分发任务**：通过 spawn_subagent 分配具体任务目标给各角色
4. **收集结果**：读取子 Agent 返回的执行摘要，整合到全局视图
5. **决定下一步**：根据结果决定是否需要补充测试、验证或生成报告
6. **汇总报告**：所有测试完成后，生成最终总结

## spawn_subagent 使用指南
调用 spawn_subagent 时需提供：
- **role**：角色名称（scout/striker/auditor/radar/scribe）
- **goal**：清晰、具体的任务目标描述

注意事项：
- 每个子 Agent 自主运行，完成后返回执行摘要
- 子 Agent 共享同一个 PentestContext，其发现（端点、参数、漏洞）会自动记录
- 你可以通过 get_test_coverage 和 list_reported_findings 查看所有角色的累计成果
- 子 Agent 不能调用 spawn_subagent（防止递归）
- 同一时间只运行一个子 Agent，前一个完成后才能启动下一个

## 协作规范
- **上下文共享**：所有子 Agent 通过 PentestContext 共享信息。侦察员发现的端点会自动对攻击手可见，攻击手报告的漏洞会自动对验证员可见。
- **指挥官决策**：你读取 PentestContext 的状态做全局决策，不直接发 HTTP 请求
- **避免重复**：启动子 Agent 前先用 get_test_coverage 检查已有成果，避免重复工作
- **结果整合**：每个子 Agent 返回后，分析其摘要，决定是否需要其他角色补充

## 角色选择策略
根据任务类型选择角色（可组合）：

| 任务类型 | 推荐角色组合 | 说明 |
|---------|------------|------|
| 全面安全测试 | radar → scout → striker → auditor → scribe | 标准全流程 |
| 快速漏洞扫描 | scout → striker → scribe | 精简流程 |
| 已知目标深入测试 | striker → auditor → scribe | 跳过侦察 |
| 仅技术栈识别 | radar | 单角色 |
| 仅报告生成 | scribe | 汇总已有结果 |
| 漏洞复核 | auditor | 验证已有发现 |

选择原则：
- 侦察先行：未知目标先启动 scout 或 radar 收集信息
- 按需组合：不必每次都用全部角色，根据任务规模选择
- 验证收尾：攻击手发现漏洞后，建议启动 auditor 复核
- 报告必备：最后必须启动 scribe 生成报告

## 何时启用协作模式
协作模式适用于：
- 复杂的多阶段渗透测试任务
- 需要不同专业能力协同的测试
- 用户明确要求多 Agent 协作
- 单一 Agent 难以覆盖的综合性安全评估

不适用于：
- 简单的单次请求测试
- 用户只想快速查看某个端点
- 目标范围不明确的探索性任务

## 任务终止条件
回复 "TASK_COMPLETE" 需满足以下任一条件：
- 所有计划的角色任务已执行完毕，报告已生成
- 用户明确要求停止
- 连续 2 轮无新发现且无新增攻击面

## 输出要求
- 调用 spawn_subagent 时，简要说明为什么选择这个角色
- 收到子 Agent 结果后，简要总结关键发现
- 最终回复应包含：测试概览、关键发现、风险评级、后续建议
"""


class AgentOrchestrator:
    """AI Agent 编排器：管理任务生命周期，驱动 AI 逐步执行。"""

    # 需要人工确认的敏感工具
    SENSITIVE_TOOLS = {"run_intruder", "run_command", "run_script"}
    # 敏感操作确认超时（秒），超时自动拒绝
    CONFIRM_TIMEOUT = 300

    def __init__(self, db: Session, task: Task, provider_id: str | None = None):
        self.db = db
        self.task = task
        self.provider_id = provider_id  # LLM Provider ID（用于漏洞验证 LLM 辅助分析）
        self.steps: list[dict] = json.loads(task.steps or "[]")
        self.max_steps = 40  # 安全限制
        self._cancelled = False
        # 对话模式中途插入的用户消息队列（/agent/inject 非阻塞入队，循环顶部 drain）
        self.inbox: asyncio.Queue = asyncio.Queue()
        # 标记当前是否为活跃的对话循环（chat_with_agent），供 /agent/inject 判断
        self._chat_active = False
        self._confirm_event: asyncio.Event | None = None
        self._confirm_approved: bool = False
        # 每个 Agent 任务维护一个会话
        self.session_id = f"agent_{task.id}"
        # 渗透测试上下文追踪
        self.context: PentestContext | None = None
        # 上下文注入间隔（步数）
        self._context_inject_interval = 5
        # 已加载的按需工具组（discovery/file），随 load_tools 调用增长
        self.loaded_groups: set[str] = set()
        # 命令执行持久工作目录（run_command cwd 参数；跨调用沿用，初始为 workspace）
        self._cwd: Path | None = None
        # 后台命令注册表：background_id -> {"proc", "log_path", "command"}
        self._bg_procs: dict[str, dict] = {}
        # 内置浏览器会话（惰性创建；任务内复用同一标签页保持登录态）
        self._browser = None

    def cancel(self):
        """立即终止：置停止标志 + 丢弃积压的插入消息 + 唤醒确认等待。

        丢弃 inbox 保证用户点停止后，队列中积压的插入消息不会被当作
        待执行任务继续处理（对齐 Claude Code：停止就是停止）。
        """
        self._cancelled = True
        self.clear_inbox()
        if self._confirm_event:
            self._confirm_event.set()
        # 终止本任务启动的后台命令（run_command run_in_background），
        # 避免"用户已停止测试但扫描进程还在跑"
        for entry in list(self._bg_procs.values()):
            try:
                proc = entry.get("proc")
                if proc and proc.returncode is None:
                    proc.terminate()
            except Exception:
                pass

    # ===== 对话模式中途插入消息 =====
    def enqueue_user_message(self, message: str) -> None:
        """将用户中途插入的消息放入队列（非阻塞）。"""
        try:
            self.inbox.put_nowait(message)
        except Exception:
            logger.debug("enqueue_user_message 失败（队列异常）", exc_info=True)

    def drain_inbox(self) -> list[str]:
        """取出全部积压的插入消息（用于循环顶部一次性处理）。"""
        msgs: list[str] = []
        while True:
            try:
                msgs.append(self.inbox.get_nowait())
            except asyncio.QueueEmpty:
                break
        return msgs

    def clear_inbox(self) -> None:
        """清空队列（停止时调用，丢弃所有未处理消息）。"""
        while True:
            try:
                self.inbox.get_nowait()
            except asyncio.QueueEmpty:
                break

    def approve(self):
        self._confirm_approved = True
        if self._confirm_event:
            self._confirm_event.set()

    def reject(self):
        self._confirm_approved = False
        if self._confirm_event:
            self._confirm_event.set()

    async def run(self, goal: str, target_url: str) -> str:
        """执行 Agent 任务。返回最终状态。"""
        self._emit("agent", {"event": "task_started", "task_id": self.task.id, "goal": goal, "target": target_url})
        # 启动即置为 running：①前端正确显示执行中 ②崩溃时被 run_agent_task 标记 failed ③看门狗能接管
        self._update_task(TASK_STATUS_RUNNING, "任务已开始")

        # 创建会话和渗透测试上下文
        session_manager.create_session(self.session_id)
        self.context = create_context(self.task.id, target_url)

        # ===== checkpoint 恢复（resume 支持，对齐 Claude Code 会话持久化）=====
        # 上次运行中断（崩溃/取消）时，task.messages 已保存对话历史、
        # task.checkpoint 已保存 PentestContext 快照，重新运行同一任务时自动续跑。
        start_step = 0
        restored = False
        try:
            saved_msgs = json.loads(self.task.messages or "[]") if self.task.messages else []
            saved_ckpt = json.loads(self.task.checkpoint or "{}") if self.task.checkpoint else {}
            if saved_msgs and len(saved_msgs) >= 2 and saved_ckpt.get("pentest_context"):
                self.context = PentestContext.from_dict(saved_ckpt["pentest_context"])
                messages = saved_msgs
                start_step = int(saved_ckpt.get("step", 0))
                restored = True
                logger.info(
                    f"任务 {self.task.id} 从 checkpoint 恢复（step={start_step}, 消息数={len(messages)}）"
                )
        except Exception as e:
            logger.warning(f"checkpoint 恢复失败，从头开始（非致命）: {e}")

        if not restored:
            # system 缓存友好拆分（对齐 chat 模式）：稳定段在前（缓存命中），
            # 动态段（技能索引）在后，变化不影响稳定前缀。记忆快照独立注入。
            messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
            dynamic_content = ""
            # 注入技能索引（与 chat 模式一致，渐进式披露：仅 name + description）
            try:
                from hemlock.core.skill_registry import build_skills_prompt
                skills_prompt = build_skills_prompt(self.db)
                if skills_prompt:
                    dynamic_content += skills_prompt
            except Exception:
                pass
            if dynamic_content:
                messages.append({"role": "system", "content": dynamic_content})
            messages.append({"role": "user", "content": f"任务目标: {goal}\n目标 URL: {target_url}\n请开始安全测试。先进行信息收集，再根据发现选择合适的测试策略。"})

        # 记忆快照：独立 system 消息（对话开始时固化，期间不可变，中途 save_memory 不回流）
        try:
            snapshot_generated = _inject_memory_snapshot(messages, self.db, self.task)
            if snapshot_generated:
                self.db.commit()
        except Exception as e:
            logger.debug(f"注入记忆快照失败（非致命）: {e}")

        consecutive_errors = 0  # 连续错误计数
        max_consecutive_errors = 3  # 最大连续错误数

        for step_num in range(start_step, self.max_steps):
            if self._cancelled:
                self._update_task(TASK_STATUS_PAUSED, "用户取消")
                return TASK_STATUS_PAUSED

            try:
                if not _has_llm(self.db):
                    self._add_step("error", "未配置 LLM API Key，Agent 无法运行")
                    self._update_task(TASK_STATUS_FAILED, "No LLM configured")
                    return TASK_STATUS_FAILED

                # 定期注入测试上下文和策略推荐
                if step_num > 0 and step_num % self._context_inject_interval == 0 and self.context:
                    context_prompt = self.context.build_context_prompt()
                    strategy = self.context.recommend_strategy()
                    strategy_hint = ""
                    if strategy.get("target_params"):
                        top_targets = strategy["target_params"][:3]
                        strategy_hint = "\n\n### 系统推荐测试目标\n"
                        for t in top_targets:
                            strategy_hint += (
                                f"- {t['method']} {t['endpoint']} 参数 [{t['param']}]"
                                f" → 测试: {', '.join(t['suggested_types'])}\n"
                            )
                    messages.append({
                        "role": "user",
                        "content": f"[系统提示] 以下是当前测试进度，请参考决定下一步操作：{context_prompt}{strategy_hint}",
                    })

                    # ===== 分界线机制：追加区超预算才推进压缩分界线 =====
                    # 每 5 步（注入上下文时）检查；有 compaction 时历史固化，
                    # messages 总 token ≈ 追加区 token，超窗口 50% 才推进。
                    current_tokens = TokenEstimator.estimate_messages(messages)
                    threshold = int(_get_context_window(self.db, self.provider_id) * 0.5)
                    if current_tokens > threshold:
                        before_tokens = current_tokens
                        # 推进分界线：压缩追加区 → 固化 compaction → 追加区归零
                        messages, _ctype = await _advance_compaction_boundary(
                            self.db, self.task, self.steps, messages, self.context,
                            self.provider_id, threshold=threshold, mode="run",
                        )
                        if _ctype != "none":
                            after_tokens = TokenEstimator.estimate_messages(messages)
                            if before_tokens > after_tokens:
                                logger.info(
                                    f"自主模式分界线推进({_ctype}): {before_tokens} → {after_tokens} tokens "
                                    f"(节省 {before_tokens - after_tokens}, step={step_num})"
                                )

                # 智能模型路由：根据复杂度选择模型
                _routed_model, _routed_pid = _select_model_for_chat(
                    user_message=self.task.goal or "",
                    tool_call_count=step_num,
                    has_findings=len(self.context.findings) > 0 if self.context else False,
                )
                # 统一工具注册：内置 + Skills + MCP
                _active_tools = build_agent_tools(self.db, loaded_groups=self.loaded_groups)
                # 用 asyncio.to_thread 包装同步 LLM 调用，避免阻塞事件循环
                # 看门狗：限制单次 LLM 调用总时长，防止线程假死导致任务永久 running
                llm_watchdog_timeout = 300  # 秒
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            chat_with_retry, messages,
                            tools=_active_tools, temperature=0.3,
                            model=_routed_model, provider_id=_routed_pid or self.provider_id, db=self.db,
                        ),
                        timeout=llm_watchdog_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.error(f"LLM 调用看门狗超时（>{llm_watchdog_timeout}s），任务 {self.task.id} 标记失败")
                    self._add_step("error", f"LLM 调用超时（>{llm_watchdog_timeout}s），已终止任务")
                    self._update_task(TASK_STATUS_FAILED, "LLM watchdog timeout")
                    return TASK_STATUS_FAILED

                if result is None:
                    # LLM 多次返回空响应
                    self._add_step("error", "LLM 连续返回空响应，无法继续")
                    self._update_task(TASK_STATUS_FAILED, "LLM empty response")
                    return TASK_STATUS_FAILED

                content = result.get("content", "")
                tool_calls = result.get("tool_calls")
                reasoning_content = result.get("reasoning_content", "") or ""

                # 推理内容发布为 thinking 事件
                if reasoning_content:
                    self._add_step("thinking", reasoning_content)
                    self._emit("agent", {
                        "event": "thinking",
                        "task_id": self.task.id,
                        "content": reasoning_content,
                    })

                # 重置连续错误计数
                consecutive_errors = 0

                if content and "TASK_COMPLETE" in content:
                    self._add_step("complete", content)
                    self._update_task(TASK_STATUS_DONE, content)
                    self._emit("agent", {"event": "task_complete", "task_id": self.task.id, "summary": content})
                    return TASK_STATUS_DONE

                if tool_calls:
                    for tc in tool_calls:
                        fn_name = tc["function"]["name"]
                        try:
                            fn_args = json.loads(tc["function"]["arguments"])
                        except json.JSONDecodeError as e:
                            logger.warning(f"工具参数 JSON 解析失败: {e}")
                            fn_args = {}

                        self._emit("agent", {
                            "event": "tool_call",
                            "task_id": self.task.id,
                            "step": step_num + 1,
                            "tool": fn_name,
                            "args": fn_args,
                        })

                        # 检查范围
                        url_to_check = fn_args.get("url") or fn_args.get("login_url", "")
                        if url_to_check and not check_scope(url_to_check):
                            tool_result = {"error": "URL 超出授权范围"}
                        elif fn_name in self.SENSITIVE_TOOLS and settings.sensitive_op_confirm:
                            approved = await self._wait_for_confirmation(fn_name, fn_args, step_num + 1)
                            if not approved:
                                tool_result = {"error": "用户拒绝执行此敏感操作"}
                            elif self._cancelled:
                                tool_result = {"error": "任务已取消"}
                            else:
                                tool_result = await self._execute_tool(fn_name, fn_args)
                        else:
                            tool_result = await self._execute_tool(fn_name, fn_args)

                        # 自动追踪上下文
                        if self.context:
                            self._auto_track_context(fn_name, fn_args, tool_result)

                        self._add_step("tool_call", {
                            "step": step_num + 1,
                            "tool": fn_name,
                            "args": fn_args,
                            "result": tool_result,
                        })

                        messages.append({"role": "assistant", "content": None, "tool_calls": [tc]})
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": _tool_result_to_llm_content(fn_name, fn_args, tool_result),
                        })

                        self._emit("agent", {
                            "event": "tool_result",
                            "task_id": self.task.id,
                            "step": step_num + 1,
                            "tool": fn_name,
                            "result": tool_result,
                        })
                else:
                    self._add_step("thinking", content)
                    self._emit("agent", {
                        "event": "thinking",
                        "task_id": self.task.id,
                        "content": content,
                    })
                    messages.append({"role": "assistant", "content": content})

                # 周期保存 checkpoint（每 3 步 + 非恢复运行），崩溃后可恢复
                if not restored or step_num % 3 == 0:
                    self._save_checkpoint(messages, step_num)

            except Exception as e:
                consecutive_errors += 1
                error_msg = f"步骤 {step_num + 1} 执行失败: {type(e).__name__}: {e}"
                logger.error(error_msg, exc_info=True)
                self._add_step("error", error_msg)
                self._emit("agent", {"event": "step_error", "task_id": self.task.id, "step": step_num + 1, "error": error_msg})

                if consecutive_errors >= max_consecutive_errors:
                    self._update_task(TASK_STATUS_FAILED, f"连续 {max_consecutive_errors} 次错误: {e}")
                    return TASK_STATUS_FAILED

                # 非致命错误，继续下一步
                messages.append({
                    "role": "user",
                    "content": f"上一步操作出错: {error_msg}。请分析原因并调整策略继续测试。",
                })

        # 达到最大步数
        msg = f"达到最大步数限制 ({self.max_steps})"
        self._add_step("limit", msg)
        self._update_task(TASK_STATUS_DONE, msg)

        # 输出 Token 用量统计
        usage = get_token_usage()
        logger.info(f"任务 {self.task.id} 完成，Token 用量: {usage}")
        self._emit("agent", {"event": "task_stats", "task_id": self.task.id, "token_usage": usage})

        return TASK_STATUS_DONE

    async def _execute_tool(self, name: str, args: dict) -> dict:
        """执行 AI 调用的工具。

        统一分发入口，按前缀/名称路由到对应处理器：
        - invoke_skill → Skill 渐进式披露
        - mcp_{server}_{tool} → MCP 服务器工具转发
        - 内置工具名 → _tool_* 方法

        所有工具调用均被 try/except 隔离，异常不会冒泡到任务循环。
        """
        try:
            return await self._dispatch_tool(name, args)
        except Exception as e:
            logger.error(f"工具 {name} 执行异常: {type(e).__name__}: {e}", exc_info=True)
            return {"error": f"工具执行失败: {type(e).__name__}: {e}"}

    async def _dispatch_tool(self, name: str, args: dict) -> dict:
        """实际工具分发逻辑（由 _execute_tool 调用，已包裹异常隔离）。"""
        # ===== Skill 调用：渐进式披露 =====
        if name == "invoke_skill":
            return await self._dispatch_invoke_skill(args)

        # ===== MCP 工具：转发到对应 MCP 服务器 =====
        if name.startswith("mcp_"):
            return await self._dispatch_mcp_tool(name, args)

        # ===== 内置工具 =====
        if name == "send_request":
            return await self._tool_send_request(args)
        elif name == "send_session_request":
            return await self._tool_send_session_request(args)
        elif name == "authenticate":
            return await self._tool_authenticate(args)
        elif name == "extract_data":
            return self._tool_extract_data(args)
        elif name == "set_session_var":
            return self._tool_set_session_var(args)
        elif name == "analyze_flow":
            return self._tool_analyze_flow(args)
        elif name == "generate_payloads":
            return self._tool_generate_payloads(args)
        elif name == "run_intruder":
            return await self._tool_run_intruder(args)
        elif name == "compare_responses":
            return self._tool_compare_responses(args)
        elif name == "verify_vulnerability":
            return await self._tool_verify_vulnerability(args)
        elif name == "llm_analyze_evidence":
            return await self._tool_llm_analyze_evidence(args)
        elif name == "get_test_coverage":
            return self._tool_get_coverage(args)
        elif name == "get_payload_list":
            return self._tool_get_payload_list(args)
        elif name == "quick_test_param":
            return await self._tool_quick_test_param(args)
        elif name == "get_strategy":
            return self._tool_get_strategy(args)
        elif name == "report_finding":
            return self._tool_report_finding(args)
        elif name == "run_request_chain":
            return await self._tool_run_request_chain(args)
        elif name == "list_reported_findings":
            return self._tool_list_reported_findings(args)
        elif name == "send_raw_request":
            return await self._tool_send_raw_request(args)
        elif name == "encode_decode":
            return self._tool_encode_decode(args)
        elif name == "get_flow_detail":
            return self._tool_get_flow_detail(args)
        elif name == "list_flowtag_rules":
            return self._tool_list_flowtag_rules(args)
        elif name == "add_flowtag_rule":
            return self._tool_add_flowtag_rule(args)
        elif name == "delete_flowtag_rule":
            return self._tool_delete_flowtag_rule(args)
        elif name == "call_api":
            return await self._tool_call_api(args)
        elif name == "send_to_repeater":
            return await self._tool_send_to_repeater(args)
        elif name == "run_command":
            return await self._tool_run_command(args)
        elif name == "run_script":
            return await self._tool_run_script(args)
        elif name == "write_file":
            return self._tool_write_file(args)
        elif name == "read_file":
            return self._tool_read_file(args)
        elif name == "list_files":
            return self._tool_list_files(args)
        elif name == "check_background":
            return await self._tool_check_background(args)
        elif name == "browser_navigate":
            return await self._tool_browser_navigate(args)
        elif name == "browser_evaluate":
            return await self._tool_browser_evaluate(args)
        elif name == "browser_screenshot":
            return await self._tool_browser_screenshot(args)
        elif name == "browser_cookies":
            return await self._tool_browser_cookies(args)
        elif name == "browser_close":
            return await self._tool_browser_close(args)
        elif name == "save_memory":
            return self._tool_save_memory(args)
        elif name == "get_memory":
            return self._tool_get_memory(args)
        elif name == "load_tools":
            return self._tool_load_tools(args)
        elif name == "load_src_doc":
            return self._tool_load_src_doc(args)
        elif name == "analyze_js":
            return await self._tool_analyze_js(args)
        elif name == "fuzz_paths":
            return await self._tool_fuzz_paths(args)
        elif name == "cve_lookup":
            return await self._tool_cve_lookup(args)
        elif name == "web_search":
            return await self._tool_web_search(args)
        elif name == "recon_search":
            return await self._tool_recon_search(args)
        elif name == "subdomain_enum":
            return await self._tool_subdomain_enum(args)
        elif name == "dns_lookup":
            return await self._tool_dns_lookup(args)
        elif name == "http_probe":
            return await self._tool_http_probe(args)
        elif name == "list_recon_reports":
            return self._tool_list_recon_reports(args)
        elif name == "get_recon_report":
            return self._tool_get_recon_report(args)
        elif name == "generate_pentest_report":
            return self._tool_generate_pentest_report(args)
        elif name == "spawn_subagent":
            # 子 Agent 能力：主 Agent / 指挥官可启动专职子 Agent 执行分工任务
            return await self._dispatch_spawn_subagent(args)
        return {"error": f"Unknown tool: {name}"}

    async def _dispatch_spawn_subagent(self, args: dict) -> dict:
        """启动子 Agent 执行分工任务（共享上下文，发现自动记录）。

        复用 run_subagent：子 Agent 使用角色专属 system prompt + 自己的工具循环，
        完成后返回执行摘要与新增发现。子 Agent 事件（subagent_*）通过 event_bus
        实时推送给前端渲染嵌套卡片。
        """
        sub_role = (args.get("role") or "").strip()
        sub_goal = (args.get("goal") or "").strip()
        if not sub_goal:
            return {"error": "spawn_subagent 需要 goal 参数"}
        # role 可选：不填 / 填了不存在 → run_subagent 内部回退为通用执行单元
        return await run_subagent(
            role_name=sub_role,
            goal=sub_goal,
            context=self.context,
            db=self.db,
            parent_task_id=self.task.id,
            provider_id=self.provider_id,
            # 停止信号传播：用户点停止 → self._cancelled → 子 Agent 下一个边界终止
            should_stop=lambda: self._cancelled,
        )

    async def _dispatch_invoke_skill(self, args: dict) -> dict:
        """处理 invoke_skill 工具调用（渐进式披露）。"""
        try:
            from hemlock.core.skill_registry import get_skill_instructions

            skill_name = args.get("skill_name", "")
            instructions = get_skill_instructions(self.db, skill_name)
            if instructions:
                return {
                    "instructions": instructions,
                    "message": f"技能 '{skill_name}' 已加载，请按照指令执行",
                }
            return {"error": f"技能 '{skill_name}' 不存在或未启用"}
        except Exception as e:
            logger.error(f"invoke_skill 失败: {e}", exc_info=True)
            return {"error": f"技能加载失败: {e}"}

    async def _dispatch_mcp_tool(self, name: str, args: dict) -> dict:
        """处理 mcp_ 前缀工具调用，转发到对应 MCP 服务器。

        工具名格式: mcp_{server_name}_{tool_name}
        """
        try:
            from hemlock.core.mcp_manager import mcp_manager

            for conn_name, conn in mcp_manager._connections.items():
                prefix = f"mcp_{conn_name}_"
                if name.startswith(prefix):
                    real_tool_name = name[len(prefix):]
                    if not conn._connected:
                        return {"error": f"MCP 服务器 '{conn_name}' 未连接"}
                    result = await conn.call_tool(real_tool_name, args)
                    logger.debug(f"MCP 工具调用: {name} -> {conn_name}.{real_tool_name}")
                    return result
            return {"error": f"未找到匹配的 MCP 服务器 for tool: {name}"}
        except Exception as e:
            logger.error(f"MCP 工具调用失败: {name} - {e}", exc_info=True)
            return {"error": f"MCP 工具调用失败: {e}"}

    async def _wait_for_confirmation(self, tool_name: str, args: dict, step: int) -> bool:
        """暂停任务，等待人工确认敏感操作。"""
        from hemlock.core.logging_config import log_sensitive_action

        self._confirm_event = asyncio.Event()
        self._confirm_approved = False

        self._update_task(TASK_STATUS_PAUSED, f"等待确认: {tool_name}")

        log_sensitive_action(
            action="sensitive_tool_requested",
            task_id=self.task.id,
            tool=tool_name,
            args=args,
        )

        self._emit("agent", {
            "event": "confirmation_required",
            "task_id": self.task.id,
            "step": step,
            "tool": tool_name,
            "args": args,
            "message": f"Agent 请求执行敏感操作 [{tool_name}]，请确认是否允许。",
        })

        self._add_step("confirmation_required", {
            "step": step,
            "tool": tool_name,
            "args": args,
        })

        try:
            await asyncio.wait_for(self._confirm_event.wait(), timeout=self.CONFIRM_TIMEOUT)
        except asyncio.TimeoutError:
            self._emit("agent", {
                "event": "confirmation_timeout",
                "task_id": self.task.id,
                "message": f"确认超时（{self.CONFIRM_TIMEOUT}s），自动拒绝",
            })
            self._confirm_approved = False

        self._confirm_event = None
        if not self._cancelled:
            self._update_task(TASK_STATUS_RUNNING, "已恢复运行")

        log_sensitive_action(
            action="sensitive_tool_result",
            task_id=self.task.id,
            tool=tool_name,
            approved=self._confirm_approved,
        )

        return self._confirm_approved

    # ===== 工具实现 =====

    async def _tool_send_request(self, args: dict) -> dict:
        """发送无状态 HTTP 请求。"""
        method = args.get("method", "GET")
        url = args["url"]
        headers = args.get("headers", "")
        body = args.get("body", "")

        resp = await send_raw_request(
            method=method,
            url=url,
            headers_str=headers,
            body=body,
            use_proxy=bool(args.get("use_proxy")),
        )

        _rh = resp.get("headers", "")
        _rb = resp.get("body", "")
        _rqh = resp.get("req_headers", headers)
        flow = Flow(
            project_id=self.task.project_id,
            method=method.upper(),
            url=url,
            req_headers=_rqh,
            req_body=body,
            resp_status=resp.get("status"),
            resp_headers=_rh,
            resp_body=_rb,
            source="agent",
            duration_ms=resp.get("duration_ms"),
            **enrich_flow_fields(url, _rqh, body, _rh, _rb, resp.get("status")),
        )
        self.db.add(flow)
        self.db.commit()
        self.db.refresh(flow)

        event_bus.publish("flow", {"event": "new_flow", "flow_id": flow.id, "url": url, "source": "agent"})

        return {
            "flow_id": flow.id,
            "status": resp.get("status"),
            "headers": (resp.get("headers", "") or "")[:1000],
            "body": (resp.get("body", "") or "")[:3000],
            "duration_ms": resp.get("duration_ms"),
            "flow_id_for_detail": flow.id,
            "truncated": len(resp.get("body", "") or "") > 3000 or len(resp.get("headers", "") or "") > 1000,
            "hint": "响应已截断以节省 Token。如需查看完整请求/响应详情，使用 get_flow_detail 工具并传入 flow_id",
        }

    async def _tool_send_session_request(self, args: dict) -> dict:
        """使用会话状态发送 HTTP 请求。"""
        method = args.get("method", "GET")
        url = args["url"]
        headers = args.get("headers", "")
        body = args.get("body", "")

        resp = await send_request_with_session(
            session_id=self.session_id,
            method=method,
            url=url,
            headers_str=headers,
            body=body,
            use_proxy=bool(args.get("use_proxy")),
        )

        _rh = resp.get("headers", "")
        _rb = resp.get("body", "")
        _rqh = resp.get("req_headers", headers)
        flow = Flow(
            project_id=self.task.project_id,
            method=method.upper(),
            url=url,
            req_headers=_rqh,
            req_body=body,
            resp_status=resp.get("status"),
            resp_headers=_rh,
            resp_body=_rb,
            source="agent_session",
            duration_ms=resp.get("duration_ms"),
            **enrich_flow_fields(url, _rqh, body, _rh, _rb, resp.get("status")),
        )
        self.db.add(flow)
        self.db.commit()
        self.db.refresh(flow)

        event_bus.publish("flow", {"event": "new_flow", "flow_id": flow.id, "url": url, "source": "agent_session"})

        return {
            "flow_id": flow.id,
            "status": resp.get("status"),
            "headers": (resp.get("headers", "") or "")[:1000],
            "body": (resp.get("body", "") or "")[:3000],
            "duration_ms": resp.get("duration_ms"),
            "flow_id_for_detail": flow.id,
            "truncated": len(resp.get("body", "") or "") > 3000 or len(resp.get("headers", "") or "") > 1000,
            "hint": "响应已截断以节省 Token。如需查看完整请求/响应详情，使用 get_flow_detail 工具并传入 flow_id",
        }

    async def _tool_authenticate(self, args: dict) -> dict:
        """执行认证流程。"""
        login_url = args["login_url"]
        method = args.get("method", "POST")
        username = args["username"]
        password = args["password"]
        extra_fields = args.get("extra_fields", {})
        success_indicator = args.get("success_indicator", "")
        headers = args.get("headers", "")

        credentials = {"username": username, "password": password}
        credentials.update(extra_fields)

        result = await session_authenticate(
            session_id=self.session_id,
            login_url=login_url,
            method=method,
            credentials=credentials,
            headers_str=headers,
            success_indicator=success_indicator,
        )

        # 记录认证请求为流量
        _auth_body = "&".join(f"{k}={v}" for k, v in credentials.items())
        _auth_rh = ""
        _auth_rb = result.get("message", "")
        _auth_rqh = result.get("req_headers", headers)
        flow = Flow(
            project_id=self.task.project_id,
            method=method.upper(),
            url=login_url,
            req_headers=_auth_rqh,
            req_body=_auth_body,
            resp_status=result.get("status"),
            resp_headers=_auth_rh,
            resp_body=_auth_rb,
            source="agent_auth",
            duration_ms=result.get("duration_ms"),
            **enrich_flow_fields(login_url, _auth_rqh, _auth_body, _auth_rh, _auth_rb, result.get("status")),
        )
        self.db.add(flow)
        self.db.commit()
        self.db.refresh(flow)

        cookies = result.get("cookies", [])
        csrf_tokens = result.get("csrf_tokens", [])
        is_success = result["success"]
        status_code = result["status"]

        # 校验：如果返回 200 但没有任何 cookie/Token，可能是假成功
        warnings = []
        if is_success and status_code == 200 and not cookies and not csrf_tokens:
            warnings.append(
                "认证返回 200 但未捕获到任何 Cookie 或 Token。"
                "可能原因：1) 响应体中无 Set-Cookie 头；2) 认证端点返回了错误页面但状态码仍为 200；"
                "3) 认证成功但会话通过其他方式维持（如 JWT body 字段）。"
                "建议：检查 flow_id 对应的响应内容确认是否真正登录成功，"
                "或使用 send_session_request 验证会话是否有效。"
            )

        return {
            "success": is_success,
            "message": result["message"],
            "status": status_code,
            "session_cookies": cookies,
            "csrf_tokens": csrf_tokens,
            "flow_id": flow.id,
            "warnings": warnings,
        }

    def _tool_extract_data(self, args: dict) -> dict:
        """从流量响应中提取数据。"""
        flow_id = args["flow_id"]
        pattern = args["pattern"]
        extract_type = args.get("extract_type", "regex")
        var_name = args["var_name"]
        group = args.get("group", 0)

        flow = self.db.query(Flow).filter(Flow.id == flow_id).first()
        if not flow:
            return {"error": "flow not found"}

        value = extract_data_from_response(
            body=flow.resp_body or "",
            pattern=pattern,
            extract_type=extract_type,
            group=group,
        )

        if value is None:
            return {"success": False, "message": "未找到匹配的数据"}

        # 存储为会话变量
        set_session_variable(self.session_id, var_name, value)

        return {
            "success": True,
            "var_name": var_name,
            "value": value[:200],
            "message": f"已提取数据并存储为会话变量 {{{{{var_name}}}}}",
        }

    def _tool_set_session_var(self, args: dict) -> dict:
        """设置会话变量。"""
        var_name = args["var_name"]
        var_value = args["var_value"]
        set_session_variable(self.session_id, var_name, var_value)
        return {"success": True, "var_name": var_name, "message": f"变量 {{{{{var_name}}}}} 已设置"}

    def _tool_analyze_flow(self, args: dict) -> dict:
        """分析流量。"""
        flow_id = args["flow_id"]
        result = ai_service.analyze_flow(self.db, flow_id, args.get("question"))
        return result

    def _tool_generate_payloads(self, args: dict) -> dict:
        """生成 payload。"""
        return ai_service.generate_payloads(
            scene=args["scene"],
            context=args.get("context", ""),
            count=args.get("count", 20),
            db=self.db,
        )

    async def _tool_run_intruder(self, args: dict) -> dict:
        """批量攻击测试（敏感操作，已通过确认）。"""
        method = args.get("method", "GET")
        url = args["url"]
        headers = args.get("headers", "")
        body = args.get("body", "")
        payloads = args["payloads"]
        marker = args.get("marker", "§")
        use_session = args.get("use_session", False)

        results = []
        for i, payload in enumerate(payloads):
            if self._cancelled:
                break

            req_url = url.replace(f"{marker}{marker}", payload) if f"{marker}{marker}" in url else url
            req_body = body.replace(f"{marker}payload{marker}", payload) if f"{marker}payload{marker}" in body else body
            req_headers = headers.replace(f"{marker}payload{marker}", payload) if f"{marker}payload{marker}" in headers else headers

            await asyncio.sleep(1.0 / max(settings.rate_limit_per_second, 1))

            try:
                if use_session:
                    resp = await send_request_with_session(
                        self.session_id, method, req_url, req_headers, req_body
                    )
                else:
                    resp = await send_raw_request(method=method, url=req_url, headers_str=req_headers, body=req_body)

                _rh = resp.get("headers", "")
                _rb = resp.get("body", "")
                flow = Flow(
                    project_id=self.task.project_id,
                    method=method.upper(),
                    url=req_url,
                    req_headers=req_headers,
                    req_body=req_body,
                    resp_status=resp.get("status"),
                    resp_headers=_rh,
                    resp_body=_rb,
                    source="agent_intruder",
                    duration_ms=resp.get("duration_ms"),
                    **enrich_flow_fields(req_url, req_headers, req_body, _rh, _rb, resp.get("status")),
                )
                self.db.add(flow)
                self.db.commit()
                self.db.refresh(flow)

                results.append({
                    "payload": payload,
                    "status": resp.get("status"),
                    "length": len(resp.get("body", "")),
                    "flow_id": flow.id,
                })

                self._emit("intruder", {
                    "event": "intruder_result",
                    "task_id": self.task.id,
                    "payload": payload,
                    "status": resp.get("status"),
                    "length": len(resp.get("body", "")),
                    "flow_id": flow.id,
                })
            except Exception as e:
                results.append({"payload": payload, "error": str(e)})

        return {
            "total": len(results),
            "results": results,
        }

    def _tool_compare_responses(self, args: dict) -> dict:
        """比较两个流量响应的差异。"""
        flow1 = self.db.query(Flow).filter(Flow.id == args["flow_id_1"]).first()
        flow2 = self.db.query(Flow).filter(Flow.id == args["flow_id_2"]).first()

        if not flow1:
            return {"error": f"flow {args['flow_id_1']} not found"}
        if not flow2:
            return {"error": f"flow {args['flow_id_2']} not found"}

        body1 = flow1.resp_body or ""
        body2 = flow2.resp_body or ""

        # 基本对比指标
        len_diff = abs(len(body1) - len(body2))
        status_diff = (flow1.resp_status or 0) != (flow2.resp_status or 0)
        similarity = _calculate_similarity(body1, body2)

        return {
            "flow_1": {
                "id": flow1.id,
                "status": flow1.resp_status,
                "length": len(body1),
            },
            "flow_2": {
                "id": flow2.id,
                "status": flow2.resp_status,
                "length": len(body2),
            },
            "status_different": status_diff,
            "length_difference": len_diff,
            "similarity": f"{similarity:.1f}%",
            "analysis": (
                f"状态码{'不同' if status_diff else '相同'}（{flow1.resp_status} vs {flow2.resp_status}），"
                f"响应长度差异 {len_diff} 字节，相似度 {similarity:.1f}%。"
                f"{'响应存在显著差异，可能存在漏洞。' if similarity < 95 else '响应高度相似。'}"
            ),
        }

    async def _tool_verify_vulnerability(self, args: dict) -> dict:
        """自动验证疑似漏洞，减少误报。

        三态语义：
        - verified=True → 确认漏洞存在
        - verified=False, confidence<0.2 → 确认漏洞不存在（强证据排除）
        - verified=False, confidence>=0.2 → 不确定（没撞上规则不代表没有）
        
        验证流程：规则验证 → LLM 辅助验证（不确定时） → 返回带 evidence 的结果
        """
        vuln_type = args.get("vuln_type", "").upper()
        method = args.get("method", "GET")
        url = args.get("url", "")
        param_name = args.get("param_name", "")
        param_value = args.get("param_value", "")
        headers = args.get("headers", "")
        body = args.get("body", "")
        resp_body = args.get("resp_body", "")
        use_session = args.get("use_session", False)

        session_id = self.session_id if use_session else None

        try:
            result = await auto_verify(
                vuln_type=vuln_type,
                method=method,
                url=url,
                param_name=param_name,
                param_value=param_value,
                headers=headers,
                body=body,
                resp_body=resp_body,
                session_id=session_id,
                provider_id=self.provider_id,
                db=self.db,
            )

            self._emit("agent", {
                "event": "verification_result",
                "task_id": self.task.id,
                "vuln_type": vuln_type,
                "verified": result.verified,
                "confidence": result.confidence,
                "method": result.method,
            })

            return {
                "verified": result.verified,
                "confidence": round(result.confidence, 2),
                "method": result.method,
                "details": result.details,
                "false_positive_risk": result.false_positive_risk,
                "evidence": result.evidence,
                "recommendation": (
                    "漏洞已验证，建议调用 report_finding 报告" if result.verified
                    else "规则验证已跳过（纯AI判断模式），请基于 evidence 中的原始证据自行判断，确认漏洞后直接调用 report_finding 报告" if result.method == "ai_judgment"
                    # 三态语义：低置信度才建议排除
                    else "漏洞未通过验证，建议检查或排除误报" if result.confidence < 0.2
                    # 中高置信度不确定结果，保留报告可能性
                    else "验证结果不确定，建议进一步手动测试或调用 llm_analyze_evidence 分析原始数据"
                ),
            }
        except Exception as e:
            logger.error(f"漏洞验证失败: {e}", exc_info=True)
            return {"error": f"验证过程出错: {e}", "verified": False}

    async def _tool_llm_analyze_evidence(self, args: dict) -> dict:
        """LLM 辅助证据分析：使用 LLM 分析漏洞证据，补充规则验证的盲区。

        适用场景：
        - verify_vulnerability 返回不确定结果（confidence >= 0.2 但 verified=False）
        - 遇到规则不支持的漏洞类型
        - 观察到异常响应模式但规则未识别
        """
        import json as _json

        vuln_type = args.get("vuln_type", "")
        url = args.get("url", "")
        method = args.get("method", "GET")
        param_name = args.get("param_name", "")
        param_value = args.get("param_value", "")
        headers = args.get("headers", "")
        body = args.get("body", "")
        resp_body = args.get("resp_body", "")
        evidence_str = args.get("evidence", "")
        observation = args.get("observation", "")

        # 构造一个伪 VerificationResult 传给 llm_assisted_verify
        try:
            evidence_dict = _json.loads(evidence_str) if evidence_str else {}
        except _json.JSONDecodeError:
            evidence_dict = {}

        # 如果有观察描述，加入 evidence
        if observation:
            evidence_dict["agent_observation"] = observation

        pseudo_result = VerificationResult(
            verified=False,
            confidence=0.3,
            method="manual_analysis",
            details=f"Agent 请求 LLM 分析 {vuln_type} 证据",
            false_positive_risk="medium",
            evidence=evidence_dict,
        )

        try:
            result = await llm_assisted_verify(
                rule_result=pseudo_result,
                vuln_type=vuln_type,
                method=method,
                url=url,
                param_name=param_name,
                param_value=param_value,
                headers=headers,
                body=body,
                resp_body=resp_body,
                provider_id=self.provider_id,
                db=self.db,
            )

            return {
                "verdict": "likely_vulnerable" if result.verified else "inconclusive" if result.confidence >= 0.2 else "likely_safe",
                "confidence": round(result.confidence, 2),
                "method": result.method,
                "analysis": result.details,
                "evidence": result.evidence,
                "recommendation": (
                    "LLM 判断漏洞可能存在，建议调用 report_finding 报告" if result.verified
                    else "LLM 无法确定，建议尝试不同 payload 或手动验证" if result.confidence >= 0.2
                    else "LLM 判断漏洞可能不存在"
                ),
            }
        except Exception as e:
            logger.error(f"LLM 证据分析失败: {e}", exc_info=True)
            return {"error": f"LLM 分析失败: {e}"}

    def _tool_get_coverage(self, args: dict) -> dict:
        """获取当前测试覆盖度统计。"""
        if not self.context:
            return {"error": "上下文未初始化"}

        stats = self.context.get_coverage_stats()
        untested = self.context.get_untested_params()

        return {
            "phase": stats["phase"],
            "total_endpoints": stats["total_endpoints"],
            "total_params": stats["total_params"],
            "tested_params": stats["tested_params"],
            "param_coverage": stats["param_coverage"],
            "total_findings": stats["total_findings"],
            "findings_by_severity": stats["findings_by_severity"],
            "total_requests": stats["total_requests"],
            "tech_stack": stats["tech_stack"],
            "authenticated": stats["authenticated"],
            "untested_params": untested[:20],  # 最多返回 20 个
            "untested_count": len(untested),
        }

    def _tool_get_payload_list(self, args: dict) -> dict:
        """从内置 Payload 库获取 payload。"""
        scene = args.get("scene", "")
        level = args.get("level")
        count = args.get("count", 15)

        # 构建技术栈上下文
        tech_stack = {}
        if self.context and self.context.tech_stack:
            ts = self.context.tech_stack
            tech_stack = {
                "server": ts.server,
                "framework": ts.framework,
                "language": ts.language,
                "database": ts.database,
                "os": ts.os,
                "cms": ts.cms,
            }

        payloads = get_recommended_payloads(
            scene=scene,
            tech_stack=tech_stack,
            count=count,
        )

        return {
            "scene": scene,
            "count": len(payloads),
            "payloads": payloads,
            "available_scenes": list_scenes(),
        }

    async def _tool_quick_test_param(self, args: dict) -> dict:
        """快速测试参数：自动选择 payload 发送请求。"""
        method = args.get("method", "GET")
        url = args["url"]
        param_name = args["param_name"]
        param_value = args["param_value"]
        scene = args.get("scene", "sqli")
        headers = args.get("headers", "")
        body = args.get("body", "")
        use_session = args.get("use_session", False)
        count = args.get("count", 5)

        # 获取技术栈上下文
        tech_stack = {}
        if self.context and self.context.tech_stack:
            ts = self.context.tech_stack
            tech_stack = {
                "server": ts.server,
                "framework": ts.framework,
                "language": ts.language,
            }

        payloads = get_recommended_payloads(
            scene=scene,
            tech_stack=tech_stack,
            param_type=param_name,
            count=count,
        )

        if not payloads:
            return {"error": f"场景 {scene} 无可用 payload"}

        results = []
        parsed = urlparse(url)

        # 先发送基线请求（使用原始参数值），用于后续异常比对
        baseline_len = None
        baseline_status = None
        try:
            if use_session:
                baseline_resp = await send_request_with_session(
                    self.session_id, method, url, headers, body
                )
            else:
                baseline_resp = await send_raw_request(method=method, url=url, headers_str=headers, body=body)
            baseline_len = len(baseline_resp.get("body", ""))
            baseline_status = baseline_resp.get("status")
        except Exception:
            pass  # 基线获取失败不阻断后续测试

        for item in payloads:
            if self._cancelled:
                break

            payload = item["payload"]

            # 注入 payload 到参数
            params = parse_qs(parsed.query, keep_blank_values=True)
            if param_name in params:
                params[param_name] = [payload]
                new_query = urlencode(params, doseq=True)
                test_url = urlunparse(parsed._replace(query=new_query))
                test_body = body
            elif body and param_name in body:
                test_url = url
                test_body = body.replace(f"{param_name}={param_value}", f"{param_name}={payload}", 1)
            else:
                test_url = url
                test_body = body

            await asyncio.sleep(1.0 / max(settings.rate_limit_per_second, 1))

            try:
                if use_session:
                    resp = await send_request_with_session(
                        self.session_id, method, test_url, headers, test_body
                    )
                else:
                    resp = await send_raw_request(method=method, url=test_url, headers_str=headers, body=test_body)

                # 记录流量
                _rh = resp.get("headers", "")
                _rb = resp.get("body", "")
                _rqh = resp.get("req_headers", headers)
                flow = Flow(
                    project_id=self.task.project_id,
                    method=method.upper(),
                    url=test_url,
                    req_headers=_rqh,
                    req_body=test_body,
                    resp_status=resp.get("status"),
                    resp_headers=_rh,
                    resp_body=_rb,
                    source="agent_quicktest",
                    duration_ms=resp.get("duration_ms"),
                    **enrich_flow_fields(test_url, _rqh, test_body, _rh, _rb, resp.get("status")),
                )
                self.db.add(flow)
                self.db.commit()
                self.db.refresh(flow)

                resp_body = resp.get("body", "")
                status = resp.get("status")
                resp_len = len(resp_body)

                # 检测异常特征（与基线对比，而非与 param_value 长度对比）
                anomalies = []
                if status == 500:
                    anomalies.append("HTTP 500 内部错误")
                if any(err in resp_body.lower() for err in ["sql syntax", "error", "exception", "traceback"]):
                    anomalies.append("响应包含错误信息")
                if status and status not in (200, 301, 302, 403, 404):
                    anomalies.append(f"异常状态码 {status}")
                # 响应长度异常：与基线对比而非与 param_value 对比
                if baseline_len is not None and abs(resp_len - baseline_len) > 1000:
                    anomalies.append(f"响应长度异常: {resp_len}（基线: {baseline_len}，差异: {resp_len - baseline_len}）")
                elif baseline_len is None and resp_len > 5000:
                    anomalies.append(f"响应长度较大: {resp_len}（无基线对比）")

                results.append({
                    "payload": payload,
                    "test_url": test_url,
                    "status": status,
                    "length": resp_len,
                    "flow_id": flow.id,
                    "anomalies": anomalies,
                    "resp_preview": resp_body[:200],
                })

                self._emit("agent", {
                    "event": "quick_test_result",
                    "task_id": self.task.id,
                    "payload": payload,
                    "test_url": test_url,
                    "status": status,
                    "anomalies": anomalies,
                    "flow_id": flow.id,
                })

            except Exception as e:
                results.append({"payload": payload, "error": str(e)})

        # 标记参数已测试
        if self.context:
            vuln_type_map = {
                "sqli": "SQL_INJECTION",
                "xss": "XSS",
                "cmd_injection": "COMMAND_INJECTION",
                "path_traversal": "PATH_TRAVERSAL",
                "ssrf": "SSRF",
            }
            mapped_type = vuln_type_map.get(scene, scene.upper())
            path = parsed.path or "/"
            self.context.mark_tested(path, param_name, mapped_type)

        return {
            "scene": scene,
            "param_name": param_name,
            "total": len(results),
            "results": results,
            "anomaly_count": sum(1 for r in results if r.get("anomalies")),
        }

    def _tool_get_strategy(self, args: dict) -> dict:
        """获取策略推荐。"""
        if not self.context:
            return {"error": "上下文未初始化"}

        return self.context.recommend_strategy()

    def _tool_list_flowtag_rules(self, args: dict) -> dict:
        """列出所有 FlowTag 规则。"""
        from hemlock.services.flowtag_engine import flowtag_engine
        rules = flowtag_engine.get_rules_dict()
        return {
            "total": len(rules),
            "rules": rules,
        }

    def _tool_add_flowtag_rule(self, args: dict) -> dict:
        """添加 FlowTag 规则。"""
        from hemlock.services.flowtag_engine import flowtag_engine
        rules = flowtag_engine.get_rules_dict()

        # 检查重名
        name = args.get("name", "").strip()
        if not name:
            return {"error": "规则名称不能为空"}
        if any(r["name"] == name for r in rules):
            return {"error": f"规则 '{name}' 已存在"}

        new_rule = {
            "name": name,
            "f_regex": args.get("f_regex", ""),
            "s_regex": "",
            "format": "{0}",
            "color": args.get("color", "gray"),
            "scope": args.get("scope", "any"),
            "enabled": True,
            "sensitive": args.get("sensitive", True),
        }
        rules.append(new_rule)
        flowtag_engine.save_rules(rules)
        return {"status": "ok", "rule": new_rule, "message": f"已添加 FlowTag 规则: {name}"}

    def _tool_delete_flowtag_rule(self, args: dict) -> dict:
        """删除 FlowTag 规则。"""
        from hemlock.services.flowtag_engine import flowtag_engine
        rules = flowtag_engine.get_rules_dict()
        name = args.get("name", "").strip()

        filtered = [r for r in rules if r["name"] != name]
        if len(filtered) == len(rules):
            return {"error": f"未找到规则: {name}"}

        flowtag_engine.save_rules(filtered)
        return {"status": "ok", "message": f"已删除 FlowTag 规则: {name}"}

    async def _tool_call_api(self, args: dict) -> dict:
        """调用 Hemlock 平台 API，让 Agent 操作软件中的所有功能。

        通过 httpx 内部调用后端 API，自动注入 project_id。
        敏感操作（DELETE /projects/*, PATCH /settings）需要人工确认。
        """
        import httpx

        method = args.get("method", "GET").upper()
        path = args.get("path", "").strip()
        body = args.get("body", {})
        query_params = args.get("query_params", {})

        if not path:
            return {"error": "path 不能为空"}

        # 标准化 path：移除多余的 /api 前缀（用户可能误加）
        if path.startswith("/api/"):
            path = path[4:]
        elif path.startswith("api/"):
            path = "/" + path[4:]
        if not path.startswith("/"):
            path = "/" + path

        # 敏感操作检测：需要人工确认的操作
        sensitive_patterns = [
            (method == "DELETE" and path.startswith("/projects/"), "删除项目"),
            (method == "DELETE" and path == "/flows", "清空所有流量"),
            (method == "DELETE" and path.startswith("/flows/"), "删除流量"),
            (method == "PATCH" and path == "/settings", "修改系统设置"),
            (method == "DELETE" and path.startswith("/recon/keys/"), "删除 API Key"),
            (method == "DELETE" and path.startswith("/skills/mcp/"), "删除 MCP 服务器"),
            (method == "DELETE" and path.startswith("/skills/"), "删除 Skill"),
        ]

        for pattern, desc in sensitive_patterns:
            if pattern:
                if not settings.sensitive_op_confirm:
                    break  # 用户关闭了敏感操作确认，跳过
                # 需要人工确认
                step_num = len(self.steps) + 1
                approved = await self._wait_for_confirmation(
                    f"call_api({method} {path})",
                    {"method": method, "path": path, "body": body, "reason": desc},
                    step_num,
                )
                if not approved:
                    return {"error": f"操作被拒绝: {desc}"}
                break

        # 需要 project_id 的端点：未提供时 GET 会返回空列表（严格项目隔离），
        # 这对 Agent 是陷阱——它看不到"这是空的"还是"缺项目参数"。
        project_aware_prefixes = (
            "/flows", "/findings", "/agent/tasks",
            "/agent/conversations", "/recon/",
        )
        needs_project = any(path.startswith(p) for p in project_aware_prefixes)

        # 自动注入 project_id（如果端点支持且未提供）
        if self.task.project_id and "project_id" not in query_params and needs_project:
            query_params["project_id"] = self.task.project_id

        # 端点需要 project_id 但任务未绑定项目且调用方未提供：
        # 提前拦截并给出明确提示，避免静默返回空列表误导 Agent
        if (
            needs_project
            and "project_id" not in query_params
            and method in ("GET", "POST")
        ):
            return {
                "error": (
                    f"端点 {path} 需要 project_id 参数（严格项目隔离），但当前任务未绑定项目。"
                    f"请先创建/切换项目，或改用其它工具（如 send_request 直接访问目标，"
                    f"或用 list_projects 查看可用项目）。"
                ),
                "hint": "project_id is required for this endpoint",
            }

        # 构建完整 URL
        base_url = f"http://127.0.0.1:{settings.api_port}"
        url = f"{base_url}/api{path}"

        try:
            # trust_env=False：不使用系统代理，直连后端 API
            # 避免请求经过 mitmproxy 代理导致响应体丢失或死循环
            async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    params=query_params if query_params else None,
                    json=body if body is not None else None,
                )

            # 解析响应
            resp_text = resp.text
            if not resp_text:
                # 响应体为空：返回明确的空响应标记，而非空 dict
                return {
                    "status_code": resp.status_code,
                    "data": None,
                    "success": 200 <= resp.status_code < 300,
                    "warning": "响应体为空（0 bytes）",
                }

            try:
                resp_data = resp.json()
            except Exception:
                resp_data = {"raw": resp_text[:2000]}

            return {
                "status_code": resp.status_code,
                "data": resp_data,
                "success": 200 <= resp.status_code < 300,
                "resp_length": len(resp_text),
            }

        except httpx.ConnectError:
            return {"error": "无法连接到后端 API（后端可能未启动）"}
        except httpx.TimeoutException:
            return {"error": "API 请求超时（30s）"}
        except Exception as e:
            return {"error": f"API 调用失败: {type(e).__name__}: {e}"}

    async def _tool_send_to_repeater(self, args: dict) -> dict:
        """构造 HTTP 请求数据包并发送到 Repeater，供用户手动审查和发送。

        通过 WebSocket 事件将数据包推送到前端，前端自动切换到 Repeater 页面并加载数据包。
        不会自动发送请求，只是将构造好的数据包放入 Repeater。
        """
        method = args.get("method", "GET").upper()
        url = args.get("url", "").strip()
        headers = args.get("headers", "")
        body = args.get("body", "")
        note = args.get("note", "")

        if not url:
            return {"error": "url 不能为空"}

        # 通过 WebSocket 事件推送到前端
        event_bus.publish("agent", {
            "event": "send_to_repeater",
            "task_id": self.task.id,
            "packet": {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body,
            },
            "note": note,
        })

        return {
            "success": True,
            "message": f"已将 {method} {url} 发送到 Repeater，用户可在 Repeater 中查看和发送",
            "note": note,
        }

    def _auto_track_context(self, tool_name: str, args: dict, result: dict) -> None:
        """自动从工具调用结果中追踪测试上下文。"""
        if not self.context:
            return

        self.context.record_request()

        # 从 send_request / send_session_request 结果中自动发现端点和参数
        if tool_name in ("send_request", "send_session_request") and "error" not in result:
            url = args.get("url", "")
            if url:
                parsed = urlparse(url)
                path = parsed.path or "/"

                # 记录端点
                method = args.get("method", "GET").upper()
                self.context.discover_endpoint(path, method)

                # 记录查询参数
                query_params = parse_qs(parsed.query, keep_blank_values=True)
                for param_name, param_values in query_params.items():
                    self.context.discover_param(
                        path, method, param_name, "query",
                        param_values[0] if param_values else "",
                    )

                # 记录 body 参数
                body = args.get("body", "")
                if body:
                    if "=" in body and "&" in body:
                        for pair in body.split("&"):
                            if "=" in pair:
                                k, v = pair.split("=", 1)
                                self.context.discover_param(path, method, k, "body", v)

                # 从响应头分析技术栈
                resp_headers = result.get("headers", "")
                resp_body = result.get("body", "")
                if resp_headers:
                    self.context.analyze_tech_stack(resp_headers, resp_body)

        # 从 authenticate 结果中更新认证状态
        elif tool_name == "authenticate" and result.get("success"):
            self.context.authenticated = True
            self.context.auth_url = args.get("login_url", "")
            self.context.phase = "testing"

        # 从 report_finding 结果中记录漏洞
        elif tool_name == "report_finding" and "finding_id" in result:
            self.context.record_finding(
                vuln_type=args.get("vuln_type", ""),
                severity=args.get("severity", "medium"),
                title=args.get("title", ""),
                endpoint=urlparse(args.get("url", "")).path if args.get("url") else "",
                finding_id=result.get("finding_id", ""),
                flow_id=args.get("flow_id", ""),
            )

        # 从 verify_vulnerability 结果中标记已测试
        elif tool_name == "verify_vulnerability" or tool_name == "llm_analyze_evidence":
            url = args.get("url", "")
            param_name = args.get("param_name", "")
            vuln_type = args.get("vuln_type", "")
            if url and param_name and vuln_type:
                path = urlparse(url).path or "/"
                self.context.mark_tested(path, param_name, vuln_type)

    def _tool_report_finding(self, args: dict) -> dict:
        """报告漏洞，自动收集证据链。"""
        from hemlock.core.logging_config import log_finding_reported

        flow_id = args["flow_id"]
        flow = self.db.query(Flow).filter(Flow.id == flow_id).first()
        if not flow:
            return {"error": "flow not found"}

        evidence = Evidence(
            request=f"{flow.method} {flow.url}\n{flow.req_headers}\n\n{flow.req_body}",
            response=f"HTTP {flow.resp_status}\n{flow.resp_headers}\n\n{flow.resp_body}",
            indicator=args.get("indicator", ""),
            ai_reasoning=args.get("detail", ""),
            replayable=True,
        )
        self.db.add(evidence)
        self.db.flush()

        finding = Finding(
            project_id=self.task.project_id,
            task_id=self.task.id,
            vuln_type=args["vuln_type"],
            severity=args.get("severity", "medium"),
            status=FINDING_STATUS_PENDING,
            title=args["title"],
            detail=args.get("detail", ""),
            evidence_id=evidence.id,
        )
        self.db.add(finding)
        self.db.commit()
        self.db.refresh(finding)

        # 安全日志
        log_finding_reported(
            finding_id=finding.id,
            vuln_type=args["vuln_type"],
            severity=args.get("severity", "medium"),
            title=args["title"],
            task_id=self.task.id,
        )

        event_bus.publish("finding", {
            "event": "new_finding",
            "finding_id": finding.id,
            "vuln_type": finding.vuln_type,
            "severity": finding.severity,
            "title": finding.title,
        })

        return {"finding_id": finding.id, "status": "reported"}

    async def _tool_run_request_chain(self, args: dict) -> dict:
        """执行多步请求链：按顺序发送请求，每步可提取数据注入下一步。

        渗透测试场景：
        - 认证链：GET 登录页提取 CSRF → POST 登录 → 提取 token
        - 越权链：用户A登录 → 获取资源ID → 用户B登录 → 访问用户A资源
        - API链：GET 列表 → 提取对象ID → DELETE 对象 → GET 验证
        """
        steps = args.get("steps", [])
        use_session = args.get("use_session", True)

        if not steps:
            return {"error": "请求链步骤不能为空"}

        if len(steps) > 10:
            return {"error": "请求链最多 10 步"}

        results = []
        chain_vars: dict[str, str] = {}  # 链内提取的变量

        for i, step in enumerate(steps):
            if self._cancelled:
                results.append({"step": i + 1, "error": "任务已取消"})
                break

            method = step.get("method", "GET")
            url = step.get("url", "")
            headers = step.get("headers", "")
            body = step.get("body", "")
            label = step.get("label", f"步骤 {i + 1}")
            extract_rules = step.get("extract", [])

            # 变量替换：将 {{变量名}} 替换为链内变量值
            for var_name, var_value in chain_vars.items():
                placeholder = f"{{{{{var_name}}}}}"
                url = url.replace(placeholder, var_value)
                headers = headers.replace(placeholder, var_value)
                body = body.replace(placeholder, var_value)

            await asyncio.sleep(1.0 / max(settings.rate_limit_per_second, 1))

            try:
                if use_session:
                    resp = await send_request_with_session(
                        self.session_id, method, url, headers, body
                    )
                else:
                    resp = await send_raw_request(method=method, url=url, headers_str=headers, body=body)

                # 记录流量
                _rh = resp.get("headers", "")
                _rb = resp.get("body", "")
                _rqh = resp.get("req_headers", headers)
                flow = Flow(
                    project_id=self.task.project_id,
                    method=method.upper(),
                    url=url,
                    req_headers=_rqh,
                    req_body=body,
                    resp_status=resp.get("status"),
                    resp_headers=_rh,
                    resp_body=_rb,
                    source="agent_chain",
                    duration_ms=resp.get("duration_ms"),
                    **enrich_flow_fields(url, _rqh, body, _rh, _rb, resp.get("status")),
                )
                self.db.add(flow)
                self.db.commit()
                self.db.refresh(flow)

                resp_body = resp.get("body", "")
                resp_headers = resp.get("headers", "")

                # 从响应中提取数据
                extracted = {}
                for rule in extract_rules:
                    var_name = rule.get("var_name", "")
                    pattern = rule.get("pattern", "")
                    extract_type = rule.get("extract_type", "regex")
                    group = rule.get("group", 0)

                    if not var_name or not pattern:
                        continue

                    value = extract_data_from_response(
                        body=resp_body,
                        pattern=pattern,
                        extract_type=extract_type,
                        group=group,
                    )

                    if value is not None:
                        chain_vars[var_name] = value
                        set_session_variable(self.session_id, var_name, value)
                        extracted[var_name] = value[:200]
                    else:
                        # 提取失败时返回明确错误，而非静默空值
                        extracted[var_name] = None
                        extracted[f"{var_name}__error"] = (
                            f"提取规则未匹配到数据: type={extract_type}, pattern={pattern}, "
                            f"resp_length={len(resp_body)}。请检查正则表达式或 JSON path 是否正确。"
                        )

                # 检查是否有提取规则但全部失败
                extract_failures = [
                    k for k, v in extracted.items()
                    if v is None and not k.endswith("__error")
                ]

                step_result = {
                    "step": i + 1,
                    "label": label,
                    "method": method,
                    "url": url[:200],
                    "status": resp.get("status"),
                    "length": len(resp_body),
                    "flow_id": flow.id,
                    "extracted": extracted,
                    "extract_warnings": extract_failures if extract_failures else [],
                }
                results.append(step_result)

                self._emit("agent", {
                    "event": "chain_step",
                    "task_id": self.task.id,
                    "step": i + 1,
                    "label": label,
                    "status": resp.get("status"),
                    "flow_id": flow.id,
                })

                # 自动追踪上下文
                if self.context:
                    self.context.record_request()
                    parsed = urlparse(url)
                    path = parsed.path or "/"
                    self.context.discover_endpoint(path, method.upper())
                    query_params = parse_qs(parsed.query, keep_blank_values=True)
                    for param_name, param_values in query_params.items():
                        self.context.discover_param(
                            path, method.upper(), param_name, "query",
                            param_values[0] if param_values else "",
                        )
                    if resp_headers:
                        self.context.analyze_tech_stack(resp_headers, resp_body)

            except Exception as e:
                results.append({
                    "step": i + 1,
                    "label": label,
                    "error": str(e),
                })
                logger.error(f"请求链步骤 {i + 1} 失败: {e}", exc_info=True)
                break

        return {
            "total_steps": len(steps),
            "completed": len(results),
            "results": results,
            "vars": {k: v[:100] for k, v in chain_vars.items()},
        }

    def _tool_list_reported_findings(self, args: dict) -> dict:
        """查看当前任务已报告的漏洞列表，避免重复测试。"""
        findings = self.db.query(Finding).filter(
            Finding.task_id == self.task.id
        ).order_by(Finding.created_at.desc()).all()

        return {
            "total": len(findings),
            "findings": [
                {
                    "id": f.id,
                    "vuln_type": f.vuln_type,
                    "severity": f.severity,
                    "title": f.title,
                    "status": f.status,
                    "created_at": f.created_at.isoformat() if f.created_at else "",
                }
                for f in findings
            ],
        }

    # ===== 终端控制 & 代码执行工具 =====

    # 危险命令黑名单（匹配则拒绝执行）
    _DANGEROUS_PATTERNS = [
        "rm -rf /", "rm -rf ~", "rm -rf *", "rmdir /s", "del /f /s /q C:",
        "format ", "shutdown", "mkfs", "dd if=", "chmod 777 /",
        ":(){:|:&};:", "fork bomb", "shutdown /s", "shutdown /r",
    ]

    @staticmethod
    def _get_workspace() -> Path:
        """获取 Agent 工作区目录，不存在则创建。

        工作区路径与数据库同目录（数据目录/workspace），
        确保所有应用数据集中在同一位置，便于用户查找。
        """
        from hemlock.core.config import get_data_dir
        ws = get_data_dir() / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        return ws.resolve()

    @staticmethod
    def _safe_path(rel_path: str) -> Path | None:
        """将相对路径安全解析到工作区内，防止目录穿越。返回 None 表示不安全。

        修复 BUG-06: 原实现未对 ws 做 resolve()，导致在含符号链接/
        junction 的 Windows 环境下 str(target).startswith(str(ws)) 判定失败，
        使合法路径被误拒。现统一 resolve 后用 is_relative_to 判定。
        """
        ws = AgentOrchestrator._get_workspace()
        try:
            target = (ws / rel_path).resolve()
            # Python 3.9+ is_relative_to，比 startswith 字符串匹配更可靠
            if not target.is_relative_to(ws):
                return None
            return target
        except Exception:
            return None

    def _resolve_cwd(self, cwd_arg: str | None) -> tuple[Path, str | None]:
        """解析命令工作目录：相对路径基于当前持久 cwd（默认工作区），绝对路径须在工作区内。

        返回 (解析后的目录, 错误消息)；成功时错误消息为 None。
        工作区外禁止执行命令（与文件工具同一安全边界）。
        """
        ws = self._get_workspace()
        if not cwd_arg or not cwd_arg.strip():
            return (self._cwd or ws), None
        raw = cwd_arg.strip()
        try:
            p = Path(raw)
            base = self._cwd or ws
            target = (p if p.is_absolute() else base / p).resolve()
            if not target.is_relative_to(ws):
                return (ws, f"路径越出工作区（命令执行仅限工作区内）: {raw}")
            if not target.exists():
                return (ws, f"目录不存在: {raw}")
            if not target.is_dir():
                return (ws, f"不是目录: {raw}")
            return (target, None)
        except Exception:
            return (ws, f"无效路径: {raw}")

    @staticmethod
    def _subprocess_proxy_env() -> dict[str, str] | None:
        """构建子进程环境变量：配置了上游代理时注入 HTTP(S)_PROXY。

        让 curl/nmap 等命令的网络流量与全局代理策略一致（对齐 Claude Code：
        Agent 知道系统网络出口并默认使用）。NO_PROXY 排除本机回环，
        避免访问本地服务（含 mitmproxy 自身）被代理截胡。
        返回 None 表示使用当前环境（未配置代理）。
        """
        upstream = (getattr(settings, "upstream_proxy", "") or "").strip()
        if not upstream:
            return None
        if not upstream.startswith(("http://", "https://", "socks5://", "socks4://")):
            upstream = f"http://{upstream}"
        import os
        env = os.environ.copy()
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                    "http_proxy", "https_proxy", "all_proxy"):
            env[key] = upstream
        no_proxy = "localhost,127.0.0.1,::1"
        env["NO_PROXY"] = no_proxy
        env["no_proxy"] = no_proxy
        return env

    async def _tool_run_command(self, args: dict) -> dict:
        """执行系统终端命令。

        在独立子进程中执行，支持 Windows CREATE_NO_WINDOW 标志。
        危险命令会被拦截，输出截断到 10000 字符。
        命令在 Agent 工作区（用户安装目录/workspace）下执行，
        生成的输出文件落盘到用户目录而非打包只读目录（BUG 修复）。

        对齐 Claude Code 体验：
        - cwd 持久化（self._cwd），跨调用沿用；返回值始终带当前 cwd 与 workspace 绝对路径
        - run_in_background=true 转后台，立即返回 background_id + 日志路径，check_background 轮询
        - 超时上限 300s -> 600s（长扫描/爆破场景）
        """
        command = args.get("command", "").strip()
        if not command:
            return {"error": "command 参数不能为空"}

        timeout = min(args.get("timeout", 30), 600)

        # 危险命令检查
        cmd_lower = command.lower()
        for pattern in self._DANGEROUS_PATTERNS:
            if pattern in cmd_lower:
                return {"error": f"危险命令已被拦截：包含 '{pattern}'"}

        # 工作目录：解析并持久化（后续调用默认沿用）
        workdir_path, cwd_err = self._resolve_cwd(args.get("cwd"))
        if cwd_err:
            return {"error": cwd_err, "workspace": str(self._get_workspace())}
        self._cwd = workdir_path
        workdir = str(workdir_path)

        try:
            import os

            # Windows: CREATE_NO_WINDOW 避免弹出控制台窗口
            creationflags = 0x08000000 if os.name == "nt" else 0

            # 后台执行：输出落盘到 .agent_bg/{id}.log，立即返回
            if args.get("run_in_background"):
                return await self._run_command_background(command, workdir, creationflags)

            proxy_env = self._subprocess_proxy_env()

            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
                env=proxy_env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "error": f"命令执行超时（{timeout}s）",
                    "command": command,
                    "cwd": workdir,
                    "workspace": str(self._get_workspace()),
                    "partial_output": "进程已被终止；长任务建议 run_in_background=true",
                }

            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")

            # 截断输出
            max_output = 10000
            if len(stdout_text) > max_output:
                stdout_text = stdout_text[:max_output] + f"\n... [输出已截断，共 {len(stdout_text)} 字符]"
            if len(stderr_text) > max_output:
                stderr_text = stderr_text[:max_output] + f"\n... [错误输出已截断，共 {len(stderr_text)} 字符]"

            result = {
                "command": command,
                "exit_code": proc.returncode,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "cwd": workdir,
                "workspace": str(self._get_workspace()),
                "success": proc.returncode == 0,
            }
            if proxy_env:
                result["proxy"] = "命令网络流量已默认走上游代理（localhost 除外）"
            return result

        except Exception as e:
            return {"error": f"命令执行失败: {type(e).__name__}: {e}", "command": command}

    async def _run_command_background(
        self, command: str, workdir: str, creationflags: int
    ) -> dict:
        """后台执行命令：stdout/stderr 合并落盘到 .agent_bg/{id}.log，立即返回。

        进程注册到 self._bg_procs，用 check_background 查询/终止。
        任务被 cancel() 时会统一终止所有仍在运行的后台进程。
        """
        import uuid

        bg_id = uuid.uuid4().hex[:8]
        log_path = self._get_workspace() / ".agent_bg" / f"{bg_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, "ab") as log_f:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=workdir,
                stdout=log_f,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=creationflags,
                env=self._subprocess_proxy_env(),
            )

        self._bg_procs[bg_id] = {
            "proc": proc,
            "log_path": log_path,
            "command": command,
        }
        return {
            "background_id": bg_id,
            "command": command,
            "cwd": workdir,
            "workspace": str(self._get_workspace()),
            "log_path": str(log_path),
            "message": (
                "命令已转入后台执行。用 check_background(background_id) 查询状态与输出；"
                "日志文件也可直接 read_file 读取。"
            ),
        }

    async def _tool_check_background(self, args: dict) -> dict:
        """查询/终止后台命令（配合 run_command run_in_background=true）。"""
        bg_id = (args.get("background_id") or "").strip()
        action = (args.get("action") or "status").strip().lower()
        entry = self._bg_procs.get(bg_id)
        if not entry:
            return {
                "error": f"后台任务不存在: {bg_id}",
                "known_ids": sorted(self._bg_procs.keys()),
                "hint": (
                    "background_id 由 run_command(run_in_background=true) 返回；"
                    "任务结束后句柄不再保留，可直接 read_file 读取当时的 log_path 日志。"
                ),
            }

        proc: asyncio.subprocess.Process = entry["proc"]
        log_path: Path = entry["log_path"]

        if action == "kill":
            if proc.returncode is not None:
                return {
                    "background_id": bg_id,
                    "killed": False,
                    "exit_code": proc.returncode,
                    "message": "进程已退出，无需终止",
                    "log_path": str(log_path),
                }
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            except ProcessLookupError:
                pass
            return {
                "background_id": bg_id,
                "killed": True,
                "command": entry["command"],
                "log_path": str(log_path),
                "workspace": str(self._get_workspace()),
            }

        # status：进程可能已退出但 returncode 尚未收割（后台启动后未 await 过），
        # 用近似零超时的 wait 收割一次
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                pass

        running = proc.returncode is None
        result = {
            "background_id": bg_id,
            "command": entry["command"],
            "running": running,
            "output_tail": self._tail_file(log_path),
            "log_path": str(log_path),
            "workspace": str(self._get_workspace()),
        }
        if not running:
            result["exit_code"] = proc.returncode
        return result

    @staticmethod
    def _tail_file(path: Path, max_chars: int = 4000) -> str:
        """读取文件尾部若干字符（后台命令输出预览）。文件不存在返回空串。"""
        try:
            data = path.read_bytes()[-max_chars * 2:]
            text = data.decode("utf-8", errors="replace")
            if len(text) > max_chars:
                text = text[-max_chars:]
            return text
        except Exception:
            return ""

    # ===== 内置浏览器工具（CDP 驱动本机 Chrome/Edge，全局可用）=====

    def _get_browser(self):
        """惰性获取本任务的浏览器会话（任务内跨调用复用同一标签页）。"""
        if self._browser is None:
            from hemlock.core.browser_driver import get_browser_session
            self._browser = get_browser_session()
        return self._browser

    async def _tool_browser_navigate(self, args: dict) -> dict:
        url = (args.get("url") or "").strip()
        if not url:
            return {"error": "url 参数不能为空"}
        try:
            wait = float(args.get("wait_seconds") or 0)
        except (TypeError, ValueError):
            wait = 0
        return await self._get_browser().navigate(url, wait_seconds=wait)

    async def _tool_browser_evaluate(self, args: dict) -> dict:
        return await self._get_browser().evaluate(args.get("expression") or "")

    async def _tool_browser_screenshot(self, args: dict) -> dict:
        return await self._get_browser().screenshot(
            name=(args.get("name") or "").strip(),
            full_page=bool(args.get("full_page")),
        )

    async def _tool_browser_cookies(self, args: dict) -> dict:
        return await self._get_browser().cookies()

    async def _tool_browser_close(self, args: dict) -> dict:
        """重置本任务的浏览器标签页（浏览器进程全局共享，不在此处杀进程）。"""
        session = self._browser
        if session is None:
            return {"success": True, "message": "本任务尚未使用浏览器"}
        try:
            await session.navigate("about:blank")
        except Exception:
            pass
        try:
            await session._reset_tab()
        except Exception:
            pass
        self._browser = None
        return {"success": True, "message": "浏览器标签页已重置"}

    def _resolve_python_command(self) -> list[str] | None:
        """解析用于执行脚本的 Python 命令（argv 前缀）。

        - 开发模式（非 frozen）：sys.executable 就是真实解释器，直接用。
        - PyInstaller 打包（frozen）：sys.executable 是 hemlock-backend.exe，
          需从 PATH 探测系统 python / py（Windows）或 python3（类 Unix）。
        返回可直接执行脚本的 argv 前缀（如 ["python"] 或 ["py", "-3"]），
        找不到返回 None。
        """
        if not getattr(sys, "frozen", False):
            return [sys.executable]

        import shutil

        # 打包环境：探测系统 Python（按优先级）
        # 优先 python / python3（可直接执行脚本）；py launcher 需 -3 前缀作兜底
        for cand in ["python", "python3"]:
            found = shutil.which(cand)
            if found:
                return [found]
        py_launcher = shutil.which("py")
        if py_launcher:
            return [py_launcher, "-3"]
        return None

    async def _tool_run_script(self, args: dict) -> dict:
        """执行 Python 脚本代码并返回输出。

        将代码写入临时文件，在独立子进程中执行，捕获 stdout/stderr。
        脚本在 Agent 工作区（用户安装目录/workspace）下执行，
        脚本产生的文件落盘到用户目录而非打包只读目录（BUG 修复）。
        """
        code = args.get("code", "").strip()
        if not code:
            return {"error": "code 参数不能为空"}

        timeout = min(args.get("timeout", 30), 120)

        import tempfile
        import os

        # 写入临时脚本文件
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            script_path = f.name

        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = 0x08000000  # CREATE_NO_WINDOW

            # 工作目录 = 用户安装目录下的 workspace（不存在则自动创建）
            workdir = str(self._get_workspace())

            # 解析 Python 解释器命令（BUG 修复）：
            # PyInstaller 打包后 sys.executable 指向 hemlock-backend.exe 主程序，
            # 直接用它执行脚本会报 "unrecognized arguments"。
            # 需探测系统 Python 解释器；开发模式下 sys.executable 即真实解释器。
            py_cmd = self._resolve_python_command()
            if not py_cmd:
                return {
                    "error": (
                        "未找到可用的 Python 解释器。打包环境下需要系统安装 Python "
                        "（需在 PATH 中），开发模式则自动使用当前解释器。"
                    )
                }

            proc = await asyncio.create_subprocess_exec(
                *py_cmd, script_path,
                cwd=workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags if os.name == "nt" else 0,
                env=self._subprocess_proxy_env(),
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "error": f"脚本执行超时（{timeout}s）",
                    "partial_output": "进程已被终止",
                }

            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")

            # 截断输出
            max_output = 10000
            if len(stdout_text) > max_output:
                stdout_text = stdout_text[:max_output] + f"\n... [输出已截断]"
            if len(stderr_text) > max_output:
                stderr_text = stderr_text[:max_output] + f"\n... [错误输出已截断]"

            return {
                "exit_code": proc.returncode,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "success": proc.returncode == 0,
            }

        except Exception as e:
            return {"error": f"脚本执行失败: {type(e).__name__}: {e}"}
        finally:
            try:
                os.unlink(script_path)
            except Exception:
                pass

    def _tool_write_file(self, args: dict) -> dict:
        """将内容写入工作区文件。"""
        rel_path = args.get("path", "").strip()
        content = args.get("content", "")
        append = args.get("append", False)

        if not rel_path:
            return {"error": "path 参数不能为空"}

        target = self._safe_path(rel_path)
        if target is None:
            return {"error": f"不安全的路径: {rel_path}（仅允许工作区内相对路径）"}

        # 文件大小限制
        if len(content) > 1024 * 1024:  # 1MB
            return {"error": "文件内容过大（最大 1MB）"}

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with open(target, mode, encoding="utf-8") as f:
                f.write(content)

            return {
                "path": rel_path,
                "absolute_path": str(target),
                "size": len(content),
                "mode": "append" if append else "overwrite",
                "success": True,
            }
        except Exception as e:
            return {"error": f"写入文件失败: {type(e).__name__}: {e}"}

    def _tool_read_file(self, args: dict) -> dict:
        """读取工作区文件内容。

        对齐 Claude Code 体验：offset/limit 按行分页读大文件；
        二进制文件拒收并提示改用 run_command/run_script；
        返回值带 absolute_path，Agent 与用户都能确认文件真实位置。
        """
        rel_path = args.get("path", "").strip()
        if not rel_path:
            return {"error": "path 参数不能为空"}

        target = self._safe_path(rel_path)
        if target is None:
            return {"error": f"不安全的路径: {rel_path}（仅允许工作区内相对路径）"}

        if not target.exists():
            return {"error": f"文件不存在: {rel_path}"}
        if target.is_dir():
            return {"error": f"这是目录，请用 list_files 查看: {rel_path}"}

        try:
            offset = max(int(args.get("offset") or 1), 1)
        except (TypeError, ValueError):
            offset = 1
        limit = args.get("limit")
        try:
            limit = max(int(limit), 1) if limit else None
        except (TypeError, ValueError):
            limit = None

        try:
            size = target.stat().st_size

            # 二进制检测：头部出现 NUL 字节即按二进制拒收
            with open(target, "rb") as f:
                if b"\x00" in f.read(8192):
                    return {
                        "error": f"二进制文件，无法按文本读取: {rel_path}",
                        "size": size,
                        "hint": "用 run_command 或 run_script 处理二进制文件",
                    }

            content = ""
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                if offset > 1 or limit is not None:
                    import itertools
                    f_iter = f
                    if offset > 1:
                        f_iter = itertools.islice(f_iter, offset - 1, None)
                    if limit is not None:
                        f_iter = itertools.islice(f_iter, limit)
                    content = "".join(f_iter)
                else:
                    content = f.read(100 * 1024)

            truncated = False
            if len(content) > 100 * 1024:
                content = content[:100 * 1024]
                truncated = True

            result = {
                "path": rel_path,
                "absolute_path": str(target),
                "content": content,
                "size": size,
                "truncated": truncated,
            }
            if offset > 1:
                result["offset"] = offset
            if limit is not None:
                result["limit"] = limit
            if truncated:
                result["hint"] = "内容已截断到 100KB；继续读取用 offset/limit 分页"
            return result
        except Exception as e:
            return {"error": f"读取文件失败: {type(e).__name__}: {e}"}

    def _tool_list_files(self, args: dict) -> dict:
        """列出工作区目录内容（文件管理：看结构、确认落盘结果）。

        支持 glob 过滤与递归；返回相对路径可直接喂给 read_file/write_file。
        """
        rel_dir = (args.get("path") or ".").strip()
        pattern = (args.get("pattern") or "").strip()
        recursive = bool(args.get("recursive"))
        ws = self._get_workspace()

        base = self._safe_path(rel_dir)
        if base is None:
            return {"error": f"不安全的路径: {rel_dir}（仅允许工作区内）", "workspace": str(ws)}
        if not base.exists():
            return {"error": f"目录不存在: {rel_dir}", "workspace": str(ws)}
        if not base.is_dir():
            return {"error": f"不是目录: {rel_dir}", "workspace": str(ws)}

        from datetime import datetime

        entries: list[dict] = []
        limit_entries = 200
        try:
            if recursive:
                it = base.rglob(pattern or "*")
            elif pattern:
                it = base.glob(pattern)
            else:
                it = base.iterdir()
            for p in it:
                try:
                    stat = p.stat()
                    entries.append({
                        "path": p.relative_to(ws).as_posix(),
                        "type": "dir" if p.is_dir() else "file",
                        "size": stat.st_size if p.is_file() else None,
                        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    })
                except OSError:
                    continue
                if len(entries) >= limit_entries:
                    break
        except Exception as e:
            return {"error": f"列目录失败: {type(e).__name__}: {e}", "workspace": str(ws)}

        entries.sort(key=lambda e: (e["type"] != "dir", e["path"]))
        return {
            "path": rel_dir,
            "workspace": str(ws),
            "count": len(entries),
            "entries": entries,
            **({"note": f"已达 {limit_entries} 项上限，结果可能不完整；可用 pattern 收窄"} if len(entries) >= limit_entries else {}),
        }

    # ===== 记忆系统工具 =====

    def _tool_save_memory(self, args: dict) -> dict:
        """将一条经验保存到长期记忆库。"""
        name = args.get("name", "").strip()
        description = args.get("description", "").strip()
        mem_type = args.get("type", "").strip()
        trigger_list = args.get("trigger", [])
        content = args.get("content", "").strip()

        if not name or not description or not mem_type or not content:
            return {"error": "name, description, type, content 均不能为空"}

        if mem_type not in ("user", "feedback", "project", "reference"):
            return {"error": f"type 必须是 user/feedback/project/reference，收到: {mem_type}"}

        try:
            from hemlock.core.memory_manager import create_memory, get_memory_index, _memory_to_dict

            memory = create_memory(
                db=None,  # 文件方案，不需要数据库
                name=name,
                description=description,
                type=mem_type,
                trigger_list=trigger_list,
                content=content,
                project_id=self.task.project_id if mem_type == "project" else None,
            )

            # 返回当前记忆库索引概览
            index = get_memory_index(None)
            from hemlock.core.memory_manager import MEMORIES_DIR
            return {
                "success": True,
                "memory": _memory_to_dict(memory),
                "total_memories": len(index),
                "note": f"记忆已保存到 {MEMORIES_DIR}，未来会话中会自动加载",
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"save_memory 失败: {e}", exc_info=True)
            return {"error": f"保存记忆失败: {type(e).__name__}: {e}"}

    def _tool_get_memory(self, args: dict) -> dict:
        """读取指定长期记忆的完整内容（索引机制：正文按需加载）。"""
        memory_id = args.get("memory_id", "").strip()
        if not memory_id:
            return {"error": "memory_id 不能为空，请从记忆索引中选择"}

        try:
            from hemlock.core.memory_manager import get_memory, _memory_to_dict
            mem = get_memory(None, memory_id)
            if not mem:
                return {
                    "error": f"记忆不存在: {memory_id}",
                    "hint": "请从系统提示中的「长期记忆索引」选择正确的记忆名称",
                }
            return {
                "success": True,
                "memory": _memory_to_dict(mem),
                "message": f"记忆 '{memory_id}' 完整内容已返回",
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"get_memory 失败: {e}", exc_info=True)
            return {"error": f"读取记忆失败: {type(e).__name__}: {e}"}

    # ===== 攻击面发现工具（发现驱动）=====

    def _tool_load_tools(self, args: dict) -> dict:
        """按需加载工具组（减少每次请求注入的工具 token）。"""
        group = (args.get("group") or "").strip()
        if group not in TOOL_GROUPS:
            return {
                "error": f"未知工具组: {group}，可用组: {', '.join(TOOL_GROUPS)}",
            }
        # 加入已加载集合 → 下一轮 LLM 调用的工具数组将包含该组工具
        self.loaded_groups.add(group)
        message = f"工具组 '{group}' 已加载，现在可以调用：{GROUP_TOOL_LISTS.get(group, '')}"
        if group == "browser":
            # 人机协同知情：有头模式下用户看得见浏览器、可随时手动操作
            if getattr(settings, "browser_headed", False):
                message += (
                    "。当前浏览器窗口对用户可见且共用：用户可能已手动登录、过验证码或打开页面，"
                    "调用前先用 browser_cookies/browser_evaluate 确认当前页面状态；"
                    "遇到验证码、滑块、扫码登录等无法自动完成的操作时，提示用户在浏览器窗口中手动完成，"
                    "稍等后继续（不要反复重试触发风控）。"
                )
            else:
                message += (
                    "。当前为无头模式，用户看不到浏览器。遇到验证码、滑块、扫码登录等无法自动完成的操作时，"
                    "如实告知用户并停止重试。"
                )
        return {
            "success": True,
            "group": group,
            "loaded_tools": GROUP_TOOL_LISTS.get(group, ""),
            "message": message,
        }

    def _tool_load_src_doc(self, args: dict) -> dict:
        """读取 SRC 文档库一篇全文（路径严格限定在 src_pack 内，防穿越）。"""
        name = (args.get("name") or "").strip().strip("/").strip()
        if not name:
            return {"error": "name 参数不能为空"}
        if not _src_pack_available():
            return {"error": "SRC 文档库不可用（src_pack 未安装）"}

        base = _SRC_PACK_DIR.resolve()
        name_noext = name[:-3] if name.endswith(".md") else name

        # 候选：显式相对路径（含 / 或 \）按路径解析（知识库文档在 skills/ 下，一并尝试）；
        # 纯名字在常见子目录中查找
        candidates: list[Path] = []
        if "/" in name_noext or "\\" in name_noext:
            candidates.append(base / (name_noext + ".md"))
            candidates.append(base / name_noext)
            candidates.append(base / "skills" / (name_noext + ".md"))
            candidates.append(base / "skills" / name_noext)
        else:
            for sub in (".", "rules", "skills", "skills/知识库"):
                candidates.append(base / sub / (name_noext + ".md"))

        for cand in candidates:
            try:
                resolved = cand.resolve()
                if not resolved.is_relative_to(base) or not resolved.is_file():
                    continue
                text = resolved.read_text(encoding="utf-8", errors="replace")
                truncated = False
                if len(text) > 200_000:
                    text = text[:200_000] + "\n... [文档过长已截断]"
                    truncated = True
                return {
                    "name": name,
                    "path": resolved.relative_to(base).as_posix(),
                    "content": text,
                    "truncated": truncated,
                }
            except OSError:
                continue

        return {
            "error": f"文档不存在: {name}",
            "hint": "用规则/知识库中的文件名加载（不带 .md 亦可），如 dig-scope-workflow、idor-test",
        }

    async def _tool_analyze_js(self, args: dict) -> dict:
        """分析 JS 文件提取接口（发现驱动核心）。"""
        url = (args.get("url") or "").strip()
        if not url:
            return {"error": "url 不能为空"}
        if not check_scope(url):
            return {"error": f"URL 超出授权范围: {url}"}

        try:
            from hemlock.services.discovery_service import analyze_js_page
            result = await analyze_js_page(url)
            if result.get("error"):
                return {"error": result["error"]}

            eps = result.get("endpoints", [])
            summary = "\n".join(f"- {e['endpoint']} ({e['source']})" for e in eps[:30])
            return {
                "status": "ok",
                "page_url": url,
                "js_files": result.get("js_files_ok", 0),
                "total_endpoints": result.get("total", 0),
                "endpoints": eps,
                "errors": result.get("errors", []),
                "summary": (
                    f"从 {result.get('js_files_ok', 0)} 个 JS 文件提取到 {result.get('total', 0)} 个接口:\n{summary}"
                    if eps else "未从 JS 中提取到接口"
                ),
                "hint": "对每个接口用 quick_test_param 测试参数，或用 send_request 访问确认",
            }
        except Exception as e:
            logger.error(f"analyze_js 失败: {e}", exc_info=True)
            return {"error": f"JS 分析失败: {type(e).__name__}: {e}"}

    async def _tool_fuzz_paths(self, args: dict) -> dict:
        """路径字典爆破（发现驱动）。"""
        url = (args.get("url") or "").strip()
        if not url:
            return {"error": "url 不能为空"}
        paths = args.get("paths") or None

        try:
            from hemlock.services.discovery_service import fuzz_paths
            result = await fuzz_paths(url, paths=paths)
            if result.get("error"):
                return {"error": result["error"]}

            findings = result.get("findings", [])
            tested_paths = result.get("tested_paths", [])
            summary = "\n".join(f"- {f['path']} → {f['status']} ({f['length']}B)" for f in findings[:30])
            # 自定义 paths 时附上全部测试结果（含 404），Agent 能确认实际测了什么
            tested_log = ""
            if tested_paths and (paths or not findings):
                tested_log = "\n测试明细:\n" + "\n".join(
                    f"- {tp['path']} → {tp['status']} ({tp['length']}B)"
                    for tp in tested_paths[:50]
                )
            return {
                "status": "ok",
                "base_url": url,
                "tested": result.get("tested", 0),
                "found": result.get("found", 0),
                "findings": findings,
                "tested_paths": tested_paths,
                "summary": (
                    f"爆破 {result.get('tested', 0)} 个路径，命中 {result.get('found', 0)} 个:\n{summary}{tested_log}"
                    if findings else f"测试了 {result.get('tested', 0)} 个路径，全部 404 或失败:{tested_log}"
                ),
                "hint": "对命中的路径用 send_request 确认内容，或用 quick_test_param 测试参数",
            }
        except Exception as e:
            logger.error(f"fuzz_paths 失败: {e}", exc_info=True)
            return {"error": f"路径爆破失败: {type(e).__name__}: {e}"}

    async def _tool_cve_lookup(self, args: dict) -> dict:
        """N-day 漏洞情报：组件+版本 → 已知 CVE。"""
        component = (args.get("component") or "").strip()
        if not component:
            return {"error": "component 不能为空，如 nginx / apache / wordpress"}
        version = (args.get("version") or "").strip()

        try:
            from hemlock.services.intel_service import cve_lookup
            result = await cve_lookup(component, version)
            if result.get("error"):
                return {"error": result["error"]}

            cves = result.get("cves", [])
            lines = [
                f"- {c['id']} | {c['severity']}(CVSS {c['cvss']}) | PoC:{'有' if c['has_poc'] else '无'} | {c['summary']}"
                for c in cves[:15]
            ]
            return {
                "status": "ok",
                "component": component,
                "version": version or "未知",
                "source": result.get("source"),
                "total": result.get("total", 0),
                "cves": cves,
                "summary": (
                    f"[cve_lookup] {component} {version or ''} 命中 {result.get('total', 0)} 个已知漏洞:\n"
                    + "\n".join(lines)
                    if cves else f"[cve_lookup] {component} {version or ''} 未查到已收录 CVE"
                ),
                "note": result.get("note", ""),
                "hint": "对 PoC=有 的高危 CVE，用 web_search 查利用细节，再用 send_request/verify_vulnerability 实测",
            }
        except Exception as e:
            logger.error(f"cve_lookup 失败: {e}", exc_info=True)
            return {"error": f"漏洞情报查询失败: {type(e).__name__}: {e}"}

    async def _tool_web_search(self, args: dict) -> dict:
        """联网搜索公开信息。"""
        query = (args.get("query") or "").strip()
        if not query:
            return {"error": "query 不能为空"}

        try:
            from hemlock.services.intel_service import web_search
            result = await web_search(query)
            if result.get("error"):
                return {"error": result["error"]}

            items = result.get("results", [])
            lines = [
                f"- {it['title']} | {it['snippet']} | {it['url']}"
                for it in items[:10]
            ]
            return {
                "status": "ok",
                "query": query,
                "total": result.get("total", 0),
                "results": items,
                "summary": (
                    f"[web_search] 「{query}」 返回 {result.get('total', 0)} 条:\n" + "\n".join(lines)
                    if items else f"[web_search] 「{query}」 无结果"
                ),
                "note": result.get("note", ""),
                "hint": "对高价值结果，可用 send_request 抓取页面正文进一步分析",
            }
        except Exception as e:
            logger.error(f"web_search 失败: {e}", exc_info=True)
            return {"error": f"联网搜索失败: {type(e).__name__}: {e}"}

    # ===== 信息收集工具（从 recon_agent_service 合并）=====

    async def _tool_recon_search(self, args: dict) -> dict:
        """多引擎并行搜索。"""
        query = args.get("query", "").strip()
        if not query:
            return {"error": "搜索内容不能为空"}
        engines = args.get("engines", [])
        if not engines:
            return {"error": "请至少选择一个引擎"}
        size = args.get("size", 100)
        result = await recon_service.multi_search(engines, query, size)

        summary_parts = []
        for engine, data in result.get("results", {}).items():
            engine_name = recon_service.ENGINES.get(engine, {}).get("name", engine)
            total = data.get("total", 0)
            items = data.get("results", [])
            summary_parts.append(f"\n### {engine_name}（{total} 条）")
            for item in items[:15]:
                if "ip" in item:
                    summary_parts.append(
                        f"- {item.get('ip', '')}:{item.get('port', '')} "
                        f"| {item.get('title', '') or item.get('hostname', '') or item.get('domain', '')}"
                    )
                elif "subdomain" in item or "domain" in item:
                    summary_parts.append(f"- {item.get('subdomain', '') or item.get('domain', '')}")
                elif "type" in item:
                    summary_parts.append(f"- {item.get('type', '')}: {item.get('value', '')}")

        errors = result.get("errors", [])
        if errors:
            summary_parts.append("\n### ⚠️ 错误")
            for err in errors:
                summary_parts.append(f"- {err}")

        return {
            "status": "ok",
            "engines_used": result.get("engines", []),
            "total_engines": result.get("total_engines", 0),
            "errors": errors,
            "report_file": result.get("report_file", ""),
            "summary": "\n".join(summary_parts) if summary_parts else "无结果",
            "message": (
                f"搜索完成：{result.get('total_engines', 0)} 个引擎返回结果"
                + (f"，{len(errors)} 个引擎失败" if errors else "")
                + f"。报告已保存: {result.get('report_file', '')}"
            ),
        }

    async def _tool_subdomain_enum(self, args: dict) -> dict:
        """子域名枚举：并行调用 crt.sh + HackerTarget + Chaos。"""
        domain = args.get("domain", "").strip()
        if not domain:
            return {"error": "域名不能为空"}

        engines = ["crtsh", "hackertarget"]
        if recon_service.has_key("chaos"):
            engines.append("chaos")

        result = await recon_service.multi_search(engines, domain, 500)

        all_subdomains = set()
        for engine, data in result.get("results", {}).items():
            for item in data.get("results", []):
                sub = item.get("subdomain") or item.get("domain", "")
                if sub:
                    all_subdomains.add(sub.lower())

        sorted_subs = sorted(all_subdomains)
        summary = "\n".join(f"- {s}" for s in sorted_subs[:50])

        return {
            "status": "ok",
            "domain": domain,
            "total_subdomains": len(sorted_subs),
            "subdomains": sorted_subs[:200],
            "report_file": result.get("report_file", ""),
            "summary": f"发现 {len(sorted_subs)} 个子域名:\n{summary}" if sorted_subs else "未发现子域名",
            "errors": result.get("errors", []),
            "message": f"子域名枚举完成：发现 {len(sorted_subs)} 个子域名",
        }

    async def _tool_dns_lookup(self, args: dict) -> dict:
        """DNS 记录查询。"""
        domain = args.get("domain", "").strip()
        if not domain:
            return {"error": "域名不能为空"}

        result = await recon_service._search_dns(domain, 100)
        items = result.get("results", [])

        summary = "\n".join(f"- {item.get('type', '')}: {item.get('name', '')} -> {item.get('value', '')}" for item in items)

        return {
            "status": "ok",
            "domain": domain,
            "total_records": len(items),
            "records": items,
            "summary": f"DNS 记录（{len(items)} 条）:\n{summary}" if items else "未找到 DNS 记录",
            "message": f"DNS 查询完成：{len(items)} 条记录",
        }

    async def _tool_http_probe(self, args: dict) -> dict:
        """HTTP 探测：发送请求并分析响应。"""
        url = args.get("url", "").strip()
        if not url:
            return {"error": "URL 不能为空"}
        method = args.get("method", "GET").upper()

        try:
            resp = await send_raw_request(method=method, url=url, headers_str="", body="")
        except Exception as e:
            return {"error": f"请求失败: {e}"}

        status = resp.get("status", 0)
        headers_str = resp.get("headers", "")
        body = resp.get("body", "")

        header_lines = headers_str.split("\n") if headers_str else []
        tech_info = {}
        for line in header_lines:
            lower = line.lower()
            if lower.startswith("server:"):
                tech_info["server"] = line.split(":", 1)[1].strip()
            elif lower.startswith("x-powered-by:"):
                tech_info["x_powered_by"] = line.split(":", 1)[1].strip()
            elif lower.startswith("set-cookie:"):
                tech_info["cookie"] = line.split(":", 1)[1].strip()[:100]
            elif lower.startswith("content-type:"):
                tech_info["content_type"] = line.split(":", 1)[1].strip()

        title = ""
        if body:
            import re
            title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
            if title_match:
                title = title_match.group(1).strip()[:200]

        # 保存为 flow
        flow_id = None
        if self.task and self.task.project_id:
            # BUG-09 修复：使用 finally 确保数据库会话始终关闭，防止连接泄漏
            db = SessionLocal()
            try:
                meta = enrich_flow_fields(
                    url=url,
                    req_headers="",
                    req_body="",
                    resp_headers=headers_str,
                    resp_body=body,
                    resp_status=status,
                )
                flow = Flow(
                    project_id=self.task.project_id,
                    method=method,
                    url=url,
                    req_headers="",
                    req_body="",
                    resp_status=status,
                    resp_headers=headers_str,
                    resp_body=body[:50000],
                    source="recon",
                    duration_ms=resp.get("duration_ms"),
                    **meta,
                )
                db.add(flow)
                db.commit()
                db.refresh(flow)
                flow_id = flow.id
            except Exception as e:
                logger.warning(f"保存 recon flow 失败: {e}")
            finally:
                db.close()

        summary_parts = [f"HTTP {method} {url} -> {status}"]
        if title:
            summary_parts.append(f"标题: {title}")
        for k, v in tech_info.items():
            summary_parts.append(f"{k}: {v}")
        summary_parts.append(f"响应大小: {len(body)} bytes")

        return {
            "status": "ok",
            "url": url,
            "http_status": status,
            "title": title,
            "tech_info": tech_info,
            "headers": headers_str[:2000],
            "body_preview": body[:2000],
            "body_size": len(body),
            "flow_id": flow_id,
            "summary": "\n".join(summary_parts),
            "message": f"HTTP 探测完成: {url} -> {status}",
        }

    # ===== 报告相关工具 =====

    def _tool_list_recon_reports(self, args: dict) -> dict:
        """列出所有已生成的侦察报告。"""
        try:
            from hemlock.services.recon_service import list_reports
            reports = list_reports()
            return {
                "status": "ok",
                "count": len(reports),
                "reports": reports,
                "message": f"共 {len(reports)} 份侦察报告" if reports else "暂无侦察报告",
            }
        except Exception as e:
            logger.error(f"列出侦察报告失败: {e}", exc_info=True)
            return {"error": f"列出报告失败: {e}"}

    def _tool_get_recon_report(self, args: dict) -> dict:
        """读取指定侦察报告的完整内容。"""
        filename = args.get("filename", "").strip()
        if not filename:
            return {"error": "filename 不能为空，请先调用 list_recon_reports 获取报告列表"}

        try:
            from hemlock.services.recon_service import get_report_content
            content = get_report_content(filename)
            if content is None:
                return {"error": f"报告不存在或文件名无效: {filename}"}
            return {
                "status": "ok",
                "filename": filename,
                "content": content,
                "size": len(content),
                "message": f"报告 {filename} 共 {len(content)} 字符",
            }
        except Exception as e:
            logger.error(f"读取侦察报告失败: {e}", exc_info=True)
            return {"error": f"读取报告失败: {e}"}

    def _tool_generate_pentest_report(self, args: dict) -> dict:
        """生成完整渗透测试报告。"""
        fmt = args.get("format", "html").lower()
        if fmt not in ("html", "markdown", "json"):
            fmt = "html"

        try:
            from hemlock.services.report_service import generate_report
            db = SessionLocal()
            try:
                project_id = self.task.project_id if self.task else None
                task_id = self.task.id if self.task else None
                report_content = generate_report(
                    db=db,
                    project_id=project_id,
                    task_id=task_id,
                    fmt=fmt,
                )
                return {
                    "status": "ok",
                    "format": fmt,
                    "size": len(report_content),
                    "content_preview": report_content[:3000] if len(report_content) > 3000 else report_content,
                    "full_content_length": len(report_content),
                    "message": (
                        f"渗透测试报告已生成（{fmt} 格式，{len(report_content)} 字符）。"
                        f"完整报告可在前端「漏洞管理」页面点击「导出报告」按钮查看和下载。"
                    ),
                    "note": "content_preview 为报告前 3000 字符预览，完整报告请通过前端导出。",
                }
            finally:
                db.close()
        except Exception as e:
            logger.error(f"生成渗透测试报告失败: {e}", exc_info=True)
            return {"error": f"生成报告失败: {e}"}

    # ===== 辅助方法 =====

    async def _tool_send_raw_request(self, args: dict) -> dict:
        """发送原始 HTTP 请求包（类似 Burp Suite Repeater）。

        解析 raw_request 文本，提取 method / Host / path / headers / body，
        构造完整 URL 后发送。支持可选的会话状态注入。
        """
        raw = args.get("raw_request", "").strip()
        if not raw:
            return {"error": "raw_request 不能为空"}

        use_session = args.get("use_session", False)
        scheme = args.get("scheme", "https")

        # 解析原始请求
        parsed = _parse_raw_http(raw, scheme)
        if parsed is None:
            return {"error": "无法解析原始请求，请确保格式正确（请求行 + Host 头）"}

        method = parsed["method"]
        url = parsed["url"]
        headers = parsed["headers"]
        body = parsed["body"]

        # 授权范围检查
        if not check_scope(url):
            return {"error": f"URL 超出授权范围: {url}"}

        if use_session:
            resp = await send_request_with_session(
                session_id=self.session_id,
                method=method,
                url=url,
                headers_str=headers,
                body=body,
                use_proxy=bool(args.get("use_proxy")),
            )
            source = "agent_raw_session"
        else:
            resp = await send_raw_request(
                method=method,
                url=url,
                headers_str=headers,
                body=body,
                use_proxy=bool(args.get("use_proxy")),
            )
            source = "agent_raw"

        _rh = resp.get("headers", "")
        _rb = resp.get("body", "")
        _rqh = resp.get("req_headers", headers)
        flow = Flow(
            project_id=self.task.project_id,
            method=method.upper(),
            url=url,
            req_headers=_rqh,
            req_body=body,
            resp_status=resp.get("status"),
            resp_headers=_rh,
            resp_body=_rb,
            source=source,
            duration_ms=resp.get("duration_ms"),
            **enrich_flow_fields(url, _rqh, body, _rh, _rb, resp.get("status")),
        )
        self.db.add(flow)
        self.db.commit()
        self.db.refresh(flow)

        event_bus.publish("flow", {"event": "new_flow", "flow_id": flow.id, "url": url, "source": source})

        return {
            "flow_id": flow.id,
            "status": resp.get("status"),
            "headers": resp.get("headers", "")[:500],
            "body": resp.get("body", "")[:2000],
        }

    def _tool_encode_decode(self, args: dict) -> dict:
        """编码/解码工具，支持 URL / Base64 / Hex / HTML / Unicode。"""
        operation = args.get("operation", "encode")
        encoding_type = args.get("encoding_type", "url")
        input_str = args.get("input", "")

        if not input_str:
            return {"error": "input 不能为空"}

        try:
            if encoding_type == "url":
                if operation == "encode":
                    result = _url_encode(input_str)
                else:
                    result = _url_decode(input_str)
            elif encoding_type == "base64":
                if operation == "encode":
                    import base64
                    result = base64.b64encode(input_str.encode("utf-8")).decode("ascii")
                else:
                    import base64
                    result = base64.b64decode(input_str).decode("utf-8", errors="replace")
            elif encoding_type == "hex":
                if operation == "encode":
                    result = input_str.encode("utf-8").hex()
                else:
                    result = bytes.fromhex(input_str.replace(" ", "").replace("\n", "")).decode("utf-8", errors="replace")
            elif encoding_type == "html":
                if operation == "encode":
                    result = _html_encode(input_str)
                else:
                    result = _html_decode(input_str)
            elif encoding_type == "unicode":
                if operation == "encode":
                    result = input_str.encode("unicode_escape").decode("ascii")
                else:
                    result = input_str.encode("utf-8").decode("unicode_escape")
            else:
                return {"error": f"不支持的编码类型: {encoding_type}"}

            return {
                "operation": operation,
                "encoding_type": encoding_type,
                "input": input_str[:200],
                "output": result,
            }
        except Exception as e:
            return {"error": f"编码/解码失败: {type(e).__name__}: {e}"}

    def _tool_get_flow_detail(self, args: dict) -> dict:
        """查看指定流量的完整请求和响应详情。"""
        flow_id = args.get("flow_id")
        if flow_id is None:
            return {"error": "flow_id 不能为空。请使用 send_request 等工具返回的 flow_id（如 'flw_xxx' 格式）。"}

        # 兼容整数和字符串类型的 flow_id
        # 如果传入的是纯数字，也尝试作为整数查询
        flow = self.db.query(Flow).filter(Flow.id == str(flow_id)).first()
        if not flow and isinstance(flow_id, (int, float)):
            flow = self.db.query(Flow).filter(Flow.id == str(int(flow_id))).first()
        if not flow:
            # 尝试模糊匹配（如用户只传了数字部分）
            flow_id_str = str(flow_id).strip()
            if flow_id_str.isdigit():
                flow = self.db.query(Flow).filter(Flow.id.like(f"%{flow_id_str}")).first()
        if not flow:
            return {
                "error": f"流量 ID '{flow_id}' 不存在。",
                "hint": "请确认使用的是 send_request/run_intruder 等工具返回的 flow_id。"
                        "如果用户粘贴了外部数据包，请先用 send_request 发送请求生成平台流量。",
            }

        # 大内容截断以节省 Token（完整数据仍在数据库中，可按需分片读取）
        _CAP = 10000
        req_headers = (flow.req_headers or "")[:_CAP]
        req_body = (flow.req_body or "")[:_CAP]
        resp_headers = (flow.resp_headers or "")[:_CAP]
        resp_body = (flow.resp_body or "")[:_CAP]
        truncated = any(
            len(x) > _CAP
            for x in (flow.req_headers or "", flow.req_body or "", flow.resp_headers or "", flow.resp_body or "")
        )

        return {
            "flow_id": flow.id,
            "method": flow.method,
            "url": flow.url,
            "req_headers": req_headers,
            "req_body": req_body,
            "resp_status": flow.resp_status,
            "resp_headers": resp_headers,
            "resp_body": resp_body,
            "source": flow.source or "",
            "created_at": flow.timestamp.isoformat() if flow.timestamp else "",
            "truncated": truncated,
            "note": "内容已截断至 10000 字符以节省 Token，完整数据保留在数据库中",
        }

    # ===== 辅助方法 =====

    def _save_checkpoint(self, messages: list[dict], step_num: int) -> None:
        """保存 checkpoint：消息历史 + PentestContext 快照（供崩溃后恢复）。"""
        try:
            self.task.messages = json.dumps(messages, ensure_ascii=False, default=str)
            if self.context:
                self.task.checkpoint = json.dumps({
                    "step": step_num,
                    "pentest_context": self.context.to_dict(),
                }, ensure_ascii=False)
            self.db.commit()
        except Exception as e:
            logger.debug(f"保存 checkpoint 失败（非致命）: {e}")

    def _add_step(self, step_type: str, data: Any) -> None:
        step = {
            "type": step_type,
            "data": data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.steps.append(step)
        self.task.steps = json.dumps(self.steps, ensure_ascii=False, default=str)
        self.db.commit()

    def _update_task(self, status: str, message: str = "") -> None:
        self.task.status = status
        if status in (TASK_STATUS_DONE, TASK_STATUS_FAILED):
            self.task.ended_at = datetime.now(timezone.utc)
        self.db.commit()

    def _emit(self, channel: str, data: dict) -> None:
        event_bus.publish(channel, data)


# 微压缩阈值（字符）：工具结果超过此值才在插入消息时蒸馏。
# 工具级截断已把 send_request 等结果限制在 ~5KB 内，直接透传保留
# flow_id/关键信息给 LLM；仅真正超大的输出（如 call_api 大响应）在此蒸馏。
_MICRO_COMPACT_THRESHOLD = 6000


def _tool_result_to_llm_content(tool_name: str, tool_args: dict, tool_result: Any) -> str:
    """将工具结果序列化为传给 LLM 的消息内容，超大时蒸馏（微压缩）。

    对齐 Claude Code 的 Microcompact：超大工具输出在插入消息时即蒸馏，
    避免原始大输出堆积在上下文里（也避免等到超预算才批量压缩）。
    阈值取 _MICRO_COMPACT_THRESHOLD（6000 字符），低于阈值原样透传，
    保留 flow_id 等关键引用，供 Agent 调 get_flow_detail / report_finding。
    完整结果仍保存在 task.steps 与数据库中，前端展示与数据复用不受影响。
    """
    from hemlock.core.context_manager import ToolOutputDistiller

    result_str = json.dumps(tool_result, ensure_ascii=False, default=str)
    # 尊重 should_distill：NON_DISTILLABLE_TOOLS（run_script/run_command/cve_lookup/web_search 等）
    # 即使输出超过阈值也透传完整内容（它们的输出是 Agent 必需的，蒸馏成计数会失去意义）
    if len(result_str) > _MICRO_COMPACT_THRESHOLD and ToolOutputDistiller.should_distill(tool_name, result_str):
        try:
            distilled = ToolOutputDistiller.distill(tool_name, tool_args, tool_result)
            if distilled:
                return distilled
        except Exception:
            pass
    return result_str


# ===== LLM 驱动的自动压缩（AutoCompact，对齐 Claude Code 泄漏源码中的 compact.ts）=====
#
# 设计要点（学习自 Claude Code 源码）：
# - 用 LLM 生成"结构化进展摘要"接管旧上下文，而不是简单丢弃 → 保留漏洞/flow_id/进度
# - <analysis>/<summary> 两段式：思考过程不进摘要
# - 摘要调用不带 tools + 系统提示明确"只用文本"，防止摘要时误调工具
# - 熔断器：连续失败 3 次停止 LLM 压缩，回退确定性压缩（避免浪费 API 调用）
# - 工具对完整性：切分时保证 assistant(tool_calls) 与其 tool 结果不拆开（API 不变量）
# - 完整历史仍在 task.steps / 数据库中，压缩只影响发送给 LLM 的内容

_AUTOCOMPACT_PROMPT = """你是一位安全测试会话压缩器。请将以下 AI 安全测试 Agent 的早期对话压缩成一份信息完整的进展摘要，供 Agent 在后续工作中参考。

先输出 <analysis> 分析哪些信息有长期价值，再输出 <summary> 作为最终摘要。

<analysis>（思考过程，仅供分析，不进入摘要）：
- 区分"有长期价值的信息"（目标/画像/漏洞/进度/待办/引用）与"可舍弃的过程内容"

<summary> 必须覆盖：
1. 目标与授权：目标 URL、范围
2. 目标画像：技术栈、认证状态、已发现端点与参数
3. 漏洞发现：每个漏洞的 vuln_type / severity / title / flow_id / finding_id / 验证状态（confirmed / 排除 / 待定）
4. 测试进度：已完成哪些测试及结论（命中 / 排除 / 不确定），当前所处阶段
5. 会话状态：是否已认证、认证 URL、会话变量名
6. 待办与未验证线索：计划中的下一步、未测试参数、不确定的发现
7. 关键引用：flow_id、finding_id、payload、会话变量等后续仍需使用的标识符

要求：
- 中文、分点结构化、不超过 500 字
- 不确定的结论标注「待验证」
- 只保留有决策价值的信息
- 必须完整保留所有 flow_id / finding_id（Agent 后续要靠它们查证据、报漏洞）"""

# AutoCompact 熔断：每个任务连续失败达到该次数后，停止尝试 LLM 压缩
_AUTOCOMPACT_MAX_CONSECUTIVE_FAILURES = 3
# 每个任务键（task id 或会话键）的连续失败计数
_autocompact_failure_counts: dict[str, int] = {}


def _split_for_autocompact(messages: list[dict], keep_recent: int) -> tuple[list[dict], list[dict], list[dict]]:
    """将消息分为 (system, old, recent)，保证不拆开 assistant(tool_calls) 与其 tool 结果。

    OpenAI API 不变量：assistant 消息若带 tool_calls，其后必须紧跟对应数量的 tool 结果。
    若切分点落在 tool 结果上（组被拆开），前移切分点让整组归入 recent。
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    conv = [m for m in messages if m.get("role") != "system"]
    if len(conv) <= keep_recent:
        return system_msgs, [], conv

    split = len(conv) - keep_recent
    # split 若指向 tool 消息 → 该组被拆开，前移使其完整归入 recent
    while split > 0 and split < len(conv) and conv[split].get("role") == "tool":
        split -= 1
    return system_msgs, conv[:split], conv[split:]


def _messages_to_compact_text(messages: list[dict]) -> str:
    """将旧消息转为可读文本，供压缩器理解（含工具调用/结果/对话）。"""
    lines = []
    for m in messages:
        role = m.get("role", "?")
        if role == "tool":
            lines.append(f"[工具结果] {str(m.get('content') or '')[:500]}")
        elif m.get("tool_calls"):
            for tc in m.get("tool_calls", []):
                fn = tc.get("function", {})
                lines.append(f"[工具调用] {fn.get('name', '?')}: {fn.get('arguments', '')[:300]}")
            content = m.get("content")
            if content:
                lines.append(f"[助手分析] {str(content)[:500]}")
        else:
            lines.append(f"[{role}] {str(m.get('content') or '')[:500]}")
    return "\n".join(lines)


def _build_session_memory(pentest_context: Any, old_text: str = "") -> str:
    """构建 Session Memory（免费压缩层，对齐 Claude Code sessionMemory.ts）。

    PentestContext 已被 _auto_track_context 持续更新（端点/参数/漏洞/技术栈/进度），
    WarmMemoryBuilder 可零成本生成结构化进展摘要，无需再调 LLM。
    附加上近期用户指令，保证用户意图不丢。
    """
    from hemlock.core.context_manager import WarmMemoryBuilder

    parts = []
    if pentest_context:
        sm = WarmMemoryBuilder.build(pentest_context)
        if sm:
            # 去掉"自动生成"标题，包装为进展摘要
            sm = sm.replace("## 测试上下文摘要（自动生成）", "## 测试进展（Session Memory）").strip()
            parts.append(sm)
    if old_text:
        user_lines = [l for l in old_text.split("\n") if l.startswith("[user] ")]
        if user_lines:
            parts.append("### 近期用户指令\n" + "\n".join(l.replace("[user] ", "- ") for l in user_lines[-3:]))
    return "\n\n".join(parts)


async def _auto_compact_messages(
    messages: list[dict],
    db: Session | None = None,
    provider_id: str | None = None,
    key: str = "default",
    keep_recent: int = 10,
    pentest_context: Any = None,
) -> tuple[list[dict], str]:
    """自动压缩（AutoCompact，对齐 Claude Code 的渐进升级）。

    压缩层按成本递进（Claude Code compact.ts 优先级链）：
    1. Session Memory（免费）：PentestContext 已预提取的结构化进展 → 直接用，不调 LLM
    2. LLM 摘要（贵）：Session Memory 不足时，用 LLM 生成完整进展摘要
    3. 确定性压缩：LLM 失败/未配置时回退

    返回 (新消息列表, 压缩方式)：'session_memory' / 'llm' / 'deterministic' / 'none'。
    """
    from hemlock.core.context_manager import ContextAssembler
    import re as _re

    system_msgs, old, recent = _split_for_autocompact(messages, keep_recent)
    if not old:
        return messages, "none"

    old_text = _messages_to_compact_text(old)
    if len(old_text) < 800:
        return messages, "none"  # 太短不值得压缩

    # ===== 第 1 层：Session Memory（免费）=====
    session_memory = _build_session_memory(pentest_context, old_text)
    if session_memory and len(session_memory) >= 60:
        boundary = {
            "role": "user",
            "content": (
                "[上下文压缩] 早期对话已压缩为以下测试进展，请基于它继续决策。"
                "需要细节时用 get_flow_detail / list_reported_findings / get_memory 等工具查完整数据。\n"
                "=== 进展摘要 ===\n"
                f"{session_memory}\n"
                "=== 摘要结束 ==="
            ),
        }
        return system_msgs + [boundary] + recent, "session_memory"

    # ===== 第 2 层：LLM 摘要（贵）=====
    # 未配置 LLM 或熔断触发：直接回退确定性压缩，避免白跑 API 调用
    if not _has_llm(db) or _autocompact_failure_counts.get(key, 0) >= _AUTOCOMPACT_MAX_CONSECUTIVE_FAILURES:
        asm = ContextAssembler()
        return asm.compress_messages(messages), "deterministic"

    try:
        result = await asyncio.to_thread(
            chat,
            [
                # NO_TOOLS：摘要调用不带工具，且系统提示明确只用文本，防止摘要时误调工具
                {"role": "system", "content": "你是会话压缩器。CRITICAL: 只用文本回复，不调用任何工具，不输出 markdown 代码块。"},
                {"role": "user", "content": _AUTOCOMPACT_PROMPT + "\n\n=== 待压缩对话 ===\n" + old_text[:60000]},
            ],
            temperature=0.2,
            provider_id=provider_id,
            db=db,
        )
        raw = (result.get("content") or "").strip()
        # 提取 <summary> 部分（无 <analysis>/<summary> 包裹时退化为整体）
        summary = raw
        m = _re.search(r"<summary>(.*?)</summary>", raw, _re.DOTALL)
        if m:
            summary = m.group(1).strip()
        if not summary:
            return messages, "none"

        _autocompact_failure_counts[key] = 0  # 成功，重置熔断计数

        boundary = {
            "role": "user",
            "content": (
                "[上下文压缩] 早期对话已被 LLM 压缩为进展摘要，请基于摘要继续决策。"
                "需要细节时用 get_flow_detail / list_reported_findings / get_memory 等工具查完整数据。\n"
                "=== 进展摘要 ===\n"
                f"{summary}\n"
                "=== 摘要结束 ==="
            ),
        }
        new_messages = system_msgs + [boundary] + recent
        return new_messages, "llm"
    except Exception as e:
        logger.warning(f"LLM 自动压缩失败（{type(e).__name__}: {e}），回退确定性压缩")
        _autocompact_failure_counts[key] = _autocompact_failure_counts.get(key, 0) + 1
        asm = ContextAssembler()
        return asm.compress_messages(messages), "deterministic"


async def _advance_compaction_boundary(
    db: Session | None,
    task: Task,
    steps: list[dict],
    messages: list[dict],
    pentest_context: Any,
    provider_id: str | None = None,
    threshold: int = 0,
    mode: str = "chat",
) -> tuple[list[dict], str]:
    """推进分界线：把追加区压缩固化为 compaction 步骤（分界线机制核心）。

    分界线语义：
    - 历史 compaction 已固化到 task.steps，恢复时线性展开、不重算
    - 追加区（最后一个 compaction 之后的对话）原文保留 → 前缀稳定命中缓存
    - 仅当追加区超预算（threshold = context_window × 50%）才推进分界线：
      压缩追加区 → 固化为新 compaction 步骤 → 追加区归零重新积累

    压缩复用 _auto_compact_messages 三层（session_memory → LLM → deterministic），
    压缩后的消息列表原样存进 compaction.data.messages（DB 同时保留原始步骤
    供前端展示）。恢复时直接展开压缩消息，不再全量重算。

    Returns:
        (新消息列表, 压缩方式)：'session_memory' / 'llm' / 'deterministic' / 'none'
    """
    # 追加区 = 最后一个 compaction 之后的全部步骤
    last_comp = -1
    for _i, _st in enumerate(steps):
        if _st.get("type") == "compaction":
            last_comp = _i
    append_steps = steps[last_comp + 1:]
    if len(append_steps) < 4:
        return messages, "none"

    # 追加区 → 对话消息（不含 system），过短不值得压缩
    append_msgs = _linear_restore_steps(append_steps)
    if len(append_msgs) < 4:
        return messages, "none"
    if len(_messages_to_compact_text(append_msgs)) < 800:
        return messages, "none"

    # AutoCompact 压缩追加区（keep_recent=0：整段进 boundary，固化映射简单）
    comp_msgs, ctype = await _auto_compact_messages(
        append_msgs,
        db=db,
        provider_id=provider_id,
        key=f"boundary_{task.id}",
        keep_recent=0,
        pentest_context=pentest_context,
    )
    if ctype == "none" or not comp_msgs:
        return messages, "none"

    # 固化：把追加区整体替换为单个 compaction 步骤（压缩消息 + 原始步骤副本）。
    # 原始步骤存进 original_steps 供前端展示/调试（落库完整），LLM 恢复只用
    # messages——覆盖步骤物理移除后线性扫描不再重复，历史段逐字节稳定命中缓存。
    comp_step = {
        "type": "compaction",
        "data": {
            "messages": comp_msgs,
            "method": ctype,
            "original_steps": append_steps,
            "boundary_step_count": len(append_steps),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
    steps[last_comp + 1:] = [comp_step]
    task.steps = json.dumps(steps, ensure_ascii=False, default=str)
    db.commit()

    logger.info(
        f"分界线推进({mode}): {ctype} 压缩 {len(append_steps)} 步追加区 → compaction "
        f"(threshold={threshold}, task={task.id})"
    )

    # 返回压缩后的完整消息：system 前缀 + 所有 compaction 展开（追加区已归零）
    system_msgs = [m for m in messages if m.get("role") == "system"]
    return system_msgs + _linear_restore_steps(steps), ctype


def _calculate_similarity(s1: str, s2: str) -> float:
    """计算两个字符串的相似度百分比（基于编辑距离）。"""
    if not s1 and not s2:
        return 100.0
    if not s1 or not s2:
        return 0.0

    # 对于长字符串，使用采样对比提高性能
    if len(s1) > 5000 or len(s2) > 5000:
        s1_sample = s1[:5000]
        s2_sample = s2[:5000]
    else:
        s1_sample = s1
        s2_sample = s2

    # 简化：使用长度差异 + 字符级对比
    max_len = max(len(s1_sample), len(s2_sample))
    if max_len == 0:
        return 100.0

    # 快速相似度：统计相同位置相同字符的比例
    min_len = min(len(s1_sample), len(s2_sample))
    same = sum(1 for i in range(min_len) if s1_sample[i] == s2_sample[i])
    similarity = (same / max_len) * 100
    return similarity


def _has_llm(db: Session | None = None) -> bool:
    """检查是否配置了 LLM API Key。

    优先检查 settings.llm_api_key（.env 配置），
    如果没有则检查数据库中是否有任意 Provider（含默认 Provider）。
    """
    if settings.llm_api_key:
        return True
    if db:
        try:
            provider = db.query(LLMProvider).first()
            if provider:
                return True
        except Exception:
            pass
    return False


def _get_context_window(db: Session | None = None, provider_id: str | None = None) -> int:
    """获取当前 Provider 的上下文窗口（tokens）。

    provider.context_window 优先；为空时按服务商兜底：deepseek 类 1M，
    其余 128K（多数旧模型的常见窗口）。返回值下限 32K，避免触发阈值过小。
    """
    provider = None
    try:
        if db:
            q = db.query(LLMProvider)
            if provider_id:
                provider = q.filter(LLMProvider.id == provider_id).first()
            if provider is None:
                provider = q.filter(LLMProvider.is_default == True).first()
            if provider is None:
                provider = q.first()
    except Exception:
        provider = None
    if provider and provider.context_window:
        return max(int(provider.context_window), 32768)
    ptype = (provider.provider or "").lower() if provider else ""
    if ptype == "deepseek":
        return 1_000_000
    return 131072


# ===== 原始 HTTP 请求解析 + 编解码辅助函数 =====

def _parse_raw_http(raw: str, scheme: str = "https") -> dict | None:
    """解析原始 HTTP 请求文本，返回 {method, url, headers, body}。

    格式:
        METHOD /path HTTP/1.1
        Host: example.com
        Other-Header: value

        body content
    """
    text = raw.replace("\r\n", "\n")
    # 用第一个空行分割 headers 区和 body
    split_idx = text.find("\n\n")
    header_section = text[:split_idx] if split_idx >= 0 else text
    body = text[split_idx + 2:] if split_idx >= 0 else ""

    lines = header_section.split("\n")
    request_line = lines[0].strip() if lines else ""
    parts = request_line.split()
    if len(parts) < 2:
        return None

    method = parts[0]
    path = parts[1]

    # 解析头部
    header_lines = lines[1:]
    host = ""
    for line in header_lines:
        lower = line.lower().strip()
        if lower.startswith("host:"):
            host = line.split(":", 1)[1].strip()
            break

    if not host:
        return None

    url = f"{scheme}://{host}{path}"
    headers = "\n".join(l.strip() for l in header_lines if l.strip())

    return {"method": method, "url": url, "headers": headers, "body": body}


def _url_encode(s: str) -> str:
    """URL 编码（全字符编码，类似 Burp Suite 的 URL-encode all characters）。"""
    from urllib.parse import quote
    return quote(s, safe="")


def _url_decode(s: str) -> str:
    """URL 解码。"""
    from urllib.parse import unquote
    return unquote(s)


def _html_encode(s: str) -> str:
    """HTML 实体编码（十进制格式）。"""
    return "".join(f"&#{ord(c)};" for c in s)


def _html_decode(s: str) -> str:
    """HTML 实体解码。"""
    import html
    return html.unescape(s)


# ===== 任务管理 =====

def create_task(db: Session, goal: str, project_id: str | None = None) -> Task:
    """创建新的 Agent 任务，初始状态为 idle（待开始）。"""
    task = Task(
        project_id=project_id,
        goal=goal,
        status=TASK_STATUS_IDLE,
        steps="[]",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_task(db: Session, task_id: str) -> Task | None:
    return db.query(Task).filter(Task.id == task_id).first()


def list_tasks(db: Session, limit: int = 50, project_id: str | None = None) -> list[Task]:
    """列出 Agent 任务，支持按 project_id 过滤。"""
    q = db.query(Task)
    if project_id:
        q = q.filter(Task.project_id == project_id)
    return q.order_by(Task.started_at.desc()).limit(limit).all()


# ===== 后台任务执行 =====

_active_agents: dict[str, AgentOrchestrator] = {}

# 当前活跃子 Agent 的中途消息 inbox（key: parent_task_id -> asyncio.Queue）。
# 子 Agent 与主 Agent 各自维护独立 inbox；串行模式下同一时间只有一个活跃子 Agent，
# 用 parent_task_id 作 key 足够。用户中途消息经 /agent/inject 广播到两侧。
_active_subagent_inboxes: dict[str, asyncio.Queue] = {}


@contextmanager
def _subagent_inbox_lifecycle(parent_task_id: str):
    """子 Agent 中途消息 inbox 生命周期：进入注册、退出清理。

    /agent/inject 广播用户消息到当前活跃子 Agent 时，通过 _active_subagent_inboxes
    找到对应 queue；子 Agent 循环顶部 drain 注入自身上下文。无论正常结束还是被取消，
    退出 with 块即清理注册，不留脏数据。
    """
    inbox: asyncio.Queue = asyncio.Queue()
    _active_subagent_inboxes[parent_task_id] = inbox
    try:
        yield inbox
    finally:
        _active_subagent_inboxes.pop(parent_task_id, None)


async def run_agent_task(task_id: str, goal: str, target_url: str) -> str:
    """在后台运行 Agent 任务。"""
    db = SessionLocal()
    try:
        task = get_task(db, task_id)
        if not task:
            return TASK_STATUS_FAILED

        agent = AgentOrchestrator(db, task)
        _active_agents[task_id] = agent

        status = await agent.run(goal, target_url)

        # 清理上下文
        remove_context(task_id)

        if task_id in _active_agents:
            del _active_agents[task_id]

        return status
    except asyncio.CancelledError:
        # 任务被取消（如事件循环关闭 / asyncio.create_task 被外部取消）：
        # CancelledError 是 BaseException 而非 Exception，必须单独处理，
        # 否则任务会永久停留在 running/paused 状态形成僵尸任务（BUG-02）
        logger.warning(f"Agent 任务 {task_id} 被取消，标记为失败")
        try:
            task = get_task(db, task_id)
            if task and task.status in (TASK_STATUS_RUNNING, TASK_STATUS_PAUSED):
                task.status = TASK_STATUS_FAILED
                task.ended_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            pass
        raise
    except Exception as e:
        logger.error(f"Agent 任务 {task_id} 发生未捕获异常: {type(e).__name__}: {e}", exc_info=True)
        # 确保任务标记为失败，避免永久悬挂
        # BUG-08 修复：同时检查 RUNNING 和 PAUSED 状态，
        # 如果任务在等待确认（paused）时发生异常，也需要标记为失败
        try:
            task = get_task(db, task_id)
            if task and task.status in (TASK_STATUS_RUNNING, TASK_STATUS_PAUSED):
                task.status = TASK_STATUS_FAILED
                task.ended_at = datetime.now(timezone.utc)
                db.commit()
                logger.info(f"Agent 任务 {task_id} 已标记为失败（异常隔离）")
        except Exception as inner_e:
            logger.error(f"标记任务 {task_id} 为失败时出错: {inner_e}")
        return TASK_STATUS_FAILED
    finally:
        if task_id in _active_agents:
            del _active_agents[task_id]
        # 任务结束广播终止事件（BUG 修复）：run() 的部分终止路径（取消/无LLM/超时/空响应/达上限）
        # 不发 task_complete/task_error，导致前端永远显示"工作中"。此处按最终状态统一补发。
        try:
            final_task = get_task(db, task_id)
            final_status = final_task.status if final_task else TASK_STATUS_FAILED
            if final_status == TASK_STATUS_DONE:
                event_bus.publish("agent", {
                    "event": "task_complete",
                    "task_id": task_id,
                    "summary": (goal or "")[:200],
                })
            elif final_status == TASK_STATUS_PAUSED:
                event_bus.publish("agent", {
                    "event": "task_error",
                    "task_id": task_id,
                    "status": final_status,
                    "error": "任务已取消/暂停",
                })
            else:
                event_bus.publish("agent", {
                    "event": "task_error",
                    "task_id": task_id,
                    "status": final_status,
                    "error": "任务未正常完成",
                })
        except Exception:
            pass
        db.close()


def cancel_agent_task(task_id: str) -> bool:
    agent = _active_agents.get(task_id)
    if agent:
        agent.cancel()
        return True
    return False


def cleanup_stale_tasks() -> int:
    """启动时清理悬挂任务：将所有 running/paused 状态的任务标记为 failed。

    服务重启后，之前运行中或等待确认（paused）的任务不会再有线程执行，
    必须标记为失败，否则前端会永远等待。
    返回清理的任务数量。

    BUG-07 修复：原实现仅清理 running 状态，遗漏了 paused（等待确认）状态。
    如果服务在用户确认敏感操作期间崩溃，paused 任务将永久悬挂。
    """
    db = SessionLocal()
    try:
        stale = db.query(Task).filter(
            Task.status.in_([TASK_STATUS_RUNNING, TASK_STATUS_PAUSED])
        ).all()
        for task in stale:
            task.status = TASK_STATUS_FAILED
            task.ended_at = datetime.now(timezone.utc)
        if stale:
            db.commit()
            logger.info(f"清理了 {len(stale)} 个悬挂任务（running/paused → failed）")
        return len(stale)
    except Exception as e:
        logger.warning(f"清理悬挂任务失败（非致命）: {e}")
        return 0
    finally:
        db.close()


def cleanup_zombie_tasks() -> int:
    """运行时清理僵尸任务：DB 中 running/paused 但无活跃编排器的任务标记为 failed。

    信号来源：_active_agents 持有每个运行中任务（自主模式 + 对话模式）的编排器实例。
    DB 状态为 running/paused 但不在 _active_agents 中的任务，说明编排器已死亡
    （协程被取消、事件循环关闭、异常未被捕获等），必须标记失败，否则前端永远等待。

    返回清理的任务数量。
    """
    db = SessionLocal()
    try:
        stale = db.query(Task).filter(
            Task.status.in_([TASK_STATUS_RUNNING, TASK_STATUS_PAUSED])
        ).all()
        cleaned = 0
        for task in stale:
            if task.id not in _active_agents:
                task.status = TASK_STATUS_FAILED
                task.ended_at = datetime.now(timezone.utc)
                cleaned += 1
        if cleaned:
            db.commit()
            logger.info(f"看门狗清理了 {cleaned} 个僵尸任务（无活跃编排器）")
        return cleaned
    except Exception as e:
        logger.warning(f"看门狗清理失败（非致命）: {e}")
        return 0
    finally:
        db.close()


async def _watchdog_loop(interval: float = 60.0):
    """运行时看门狗协程：周期检查并清理悬挂/僵尸 Agent 任务。

    在应用 lifespan 中以 asyncio.create_task 启动，随应用生命周期运行。
    """
    while True:
        await asyncio.sleep(interval)
        try:
            cleaned = cleanup_zombie_tasks()
            if cleaned:
                logger.info(f"看门狗周期清理完成，清理 {cleaned} 个僵尸任务")
        except Exception as e:
            logger.debug(f"看门狗周期检查失败（非致命）: {e}")


def approve_agent_task(task_id: str) -> bool:
    agent = _active_agents.get(task_id)
    if agent:
        agent.approve()
        return True
    return False


def reject_agent_task(task_id: str) -> bool:
    agent = _active_agents.get(task_id)
    if agent:
        agent.reject()
        return True
    return False


# ===== 漏洞查询 =====

def list_findings(db: Session, project_id: str | None = None, status: str | None = None) -> list[Finding]:
    query = db.query(Finding)
    if project_id:
        query = query.filter(Finding.project_id == project_id)
    if status:
        query = query.filter(Finding.status == status)
    return query.order_by(Finding.created_at.desc()).all()


def get_finding(db: Session, finding_id: str) -> Finding | None:
    return db.query(Finding).filter(Finding.id == finding_id).first()


def get_evidence(db: Session, evidence_id: str) -> Evidence | None:
    return db.query(Evidence).filter(Evidence.id == evidence_id).first()


def update_finding_status(db: Session, finding_id: str, status: str) -> Finding | None:
    finding = db.query(Finding).filter(Finding.id == finding_id).first()
    if finding:
        finding.status = status
        finding.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(finding)
    return finding


# ===== 对话式 Agent（Codex 风格）=====
#
# 将 Agent 从“黑盒自主循环”改为“对话驱动”模式：
# 用户发消息 -> AI 回复（可调用工具）-> 工具结果展示 -> 等待用户下一条消息。
#
# 对话历史存储在 task.steps JSON 数组中：
#   - {"type": "user_message", "data": "用户消息", "timestamp": "..."}
#   - {"type": "assistant_message", "data": "AI 回复文本", "timestamp": "..."}
#   - {"type": "tool_call", "data": {"tool": "...", "args": {...}, "result": "...",
#                                    "tool_call_id": "...", "raw_tool_call": {...}}, "timestamp": "..."}
#   - {"type": "system_message", "data": "...", "timestamp": "..."}  # 仅作记录，不参与 LLM 历史

# 单次对话最大工具调用数，防止 LLM 无限循环调用工具
# 安全测试是"侦察→验证→报告"的连续循环，15 次过低会切断工作流
# 设为高上限：不再因为单次对话内工具调用较多就中断测试流程。
# 仅作为防止死循环的最后兜底（正常测试流程远达不到此数）。
MAX_CHAT_TOOL_CALLS = 200
# 对话模式单次 LLM 调用看门狗（秒）：防止 LLM 挂起/超长导致"无法停止"
# 超时后该轮结束并返回错误提示（线程在后台结束，任务可继续/停止）
# 300s：本地推理模型（如 Qwen3 思考模式）+ 长上下文时单次调用常超 180s，
# 看门狗过短会把进行中的安全测试直接判死，用户侧表现为"测试自动结束"
_CHAT_LLM_WATCHDOG = 300
# 推理模型（如本地 Qwen3 思考模式）"只输出思考、无最终内容/工具调用"的最大纠错次数。
# 达到上限后不再死循环，改用兜底回复结束本轮。
MAX_REASONING_ONLY_RETRY = 1


def _parse_step_data(data: Any) -> Any:
    """解析 step.data：若为 JSON 字符串则解析为 dict，否则原样返回。"""
    if isinstance(data, str):
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return data
    return data


def _add_chat_step(db: Session, task: Task, steps: list[dict], step_type: str, data: Any) -> None:
    """追加一条对话步骤并持久化到 task.steps。

    与 AgentOrchestrator._add_step 保持一致的存储格式：
    data 为字符串时原样存储，否则 JSON 序列化。
    """
    step = {
        "type": step_type,
        "data": data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    steps.append(step)
    task.steps = json.dumps(steps, ensure_ascii=False, default=str)
    db.commit()


def _linear_restore_steps(steps: list[dict]) -> list[dict]:
    """线性展开 steps → 对话消息列表（不含 system）。

    分界线机制核心：遇到 compaction 步骤时直接展开其固化后的压缩消息
    （data.messages），并跳过 boundary_step_count 个被压缩的普通步骤——
    历史段逐字节稳定、不再全量重算，前缀缓存持续命中。
    """
    messages: list[dict] = []
    i = 0
    n = len(steps)
    while i < n:
        step = steps[i]
        stype = step.get("type", "")

        if stype == "user_message":
            content = step.get("data", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str)
            messages.append({"role": "user", "content": content})
            i += 1

        elif stype == "assistant_message":
            content = step.get("data", "") or ""
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str)
            # 检查是否紧随 tool_call 组（同一个 assistant 回合）
            if i + 1 < n and steps[i + 1].get("type") == "tool_call":
                i += 1
                group: list[dict] = []
                while i < n and steps[i].get("type") == "tool_call":
                    group.append(_parse_step_data(steps[i].get("data", {})))
                    i += 1
                _append_tool_group(messages, group, assistant_content=content)
            else:
                messages.append({"role": "assistant", "content": content})
                i += 1

        elif stype == "tool_call":
            # 没有前置 assistant_message 的 tool_call 组（assistant 无文本内容）
            group = []
            while i < n and steps[i].get("type") == "tool_call":
                group.append(_parse_step_data(steps[i].get("data", {})))
                i += 1
            _append_tool_group(messages, group, assistant_content="")

        elif stype == "compaction":
            # 分界线：展开固化后的压缩消息（覆盖的原始步骤已在推进时替换删除，
            # 原始步骤存于 original_steps 副本仅供前端展示/调试，LLM 恢复只用 messages）
            data = _parse_step_data(step.get("data", {}))
            if not isinstance(data, dict):
                data = {}
            comp_msgs = data.get("messages")
            if isinstance(comp_msgs, list) and comp_msgs:
                for _cm in comp_msgs:
                    if isinstance(_cm, dict):
                        messages.append(_cm)
            else:
                # 兼容旧格式：仅存 summary 文本
                summary = data.get("summary", "")
                if summary:
                    messages.append({"role": "user", "content": summary})
            i += 1

        else:
            # system_message / thinking / error / complete / subagent_* 等：跳过
            i += 1

    return messages


def _inject_memory_snapshot(messages: list[dict], db: Session | None, task: Task) -> bool:
    """注入对话级记忆快照（独立 system 消息，动态段之后、对话历史之前）。

    快照 = 对话开始时固化的记忆索引（分级过滤：仅 global + 当前 project）。
    惰性生成：task.memory_snapshot 为空时首次生成并存库，之后每轮复用存储值，
    中途 save_memory 不回流 → 快照消息逐字节稳定、可被前缀缓存命中。

    Returns:
        True 表示本次惰性生成了新快照（调用方应 commit 持久化）。
    """
    snapshot = task.memory_snapshot or ""
    generated = False
    if not snapshot and db:
        try:
            from hemlock.core.memory_manager import build_memory_snapshot
            snapshot = build_memory_snapshot(db, task.project_id)
            if snapshot:
                task.memory_snapshot = snapshot
                generated = True
        except Exception as e:
            logger.debug(f"生成记忆快照失败（非致命）: {e}")
            return False
    if not snapshot:
        return False

    # 查重：checkpoint 恢复时 messages 可能已含上次注入的快照，避免重复插入
    for _m in messages:
        if _m.get("role") == "system" and _m.get("content") == snapshot:
            return generated

    # 插入位置：所有 system 消息（稳定段 + 动态段）之后、对话历史之前
    insert_at = len(messages)
    for _i, _m in enumerate(messages):
        if _m.get("role") == "system":
            insert_at = _i + 1
    messages.insert(insert_at, {"role": "system", "content": snapshot})
    return generated


def _build_chat_messages(steps: list[dict], task: Task, db: Session | None = None, system_prompt_override: str | None = None, pentest_context: PentestContext | None = None) -> list[dict]:
    """从 task.steps 恢复 LLM 对话历史（messages 格式），并执行上下文压缩。

    映射规则：
    - user_message      -> role:user
    - assistant_message -> role:assistant（若紧随 tool_call 组，则合并为带 tool_calls 的 assistant 消息）
    - tool_call         -> role:tool（连续的 tool_call 归属于同一个 assistant 回合）
    - system_message / thinking / error / complete 等 -> 跳过（system_message 仅作记录，目标已注入 system 内容）

    恢复 tool_call 时需要 tool_call_id 与原始 tool_call 对象，以保证 OpenAI 消息序列
    （assistant.tool_calls -> tool.tool_call_id）配对完整。
    """
    # ===== system 缓存友好拆分：稳定段 + 动态段 =====
    # system 拆成两条消息，是前缀缓存命中的关键：
    #   messages[0] = 稳定段（SYSTEM_PROMPT + AUTO_PENTEST，或 override）——
    #                 跨请求逐字节一致，Anthropic 显式断点 / DeepSeek-OpenAI 自动前缀缓存都命中它。
    #   messages[1] = 动态段（技能索引 + MCP 列表 + 长期记忆）——随库变化，
    #                 位于缓存断点之后，改动不影响稳定前缀（越靠前的越不能改）。
    # 补充：task.goal 不注入 system（会让每个任务前缀不同 → 跨任务缓存失效），
    #       移出后所有任务的稳定段一致，缓存可跨任务命中。
    system_content = system_prompt_override if system_prompt_override else SYSTEM_PROMPT
    # 自动化渗透测试模式提示
    if getattr(settings, "auto_pentest", True):
        system_content += AUTO_PENTEST_PROMPT
    # SRC 漏洞挖掘模式提示（稳定段：文件内容缓存后逐字节稳定，缓存前缀可命中）
    if getattr(settings, "src_hunting", False):
        system_content += _load_src_prompt()
    messages: list[dict] = [{"role": "system", "content": system_content}]

    # 动态段：动态注入已启用的 Skill 列表（渐进式披露：仅 name + description）
    # 注意：Skill 注入不依赖 auto_pentest，始终启用
    dynamic_content = ""
    if db:
        try:
            from hemlock.core.skill_registry import build_skills_prompt
            skills_prompt = build_skills_prompt(db)
            if skills_prompt:
                dynamic_content += skills_prompt
        except Exception:
            pass

        # 动态注入已连接的 MCP 工具列表（按名字确定性排序：配置不变时
        # 动态段逐字节稳定，缓存前缀可命中；具体工具定义走 build_agent_tools，
        # 不进 system——技能/MCP 只向 system 注入索引，正文按需经工具加载）
        try:
            from hemlock.core.mcp_manager import mcp_manager
            mcp_tools = mcp_manager.get_enabled_tools(db)
            if mcp_tools:
                mcp_info = "\n\n## 可用 MCP 工具\n以下 MCP 服务器工具已连接，可直接调用：\n"
                for t in sorted(
                    mcp_tools,
                    key=lambda x: (x.get("function", {}).get("name", "") or ""),
                ):
                    fn = t.get("function", {})
                    mcp_info += f"- **{fn.get('name', '?')}**: {fn.get('description', '')}\n"
                dynamic_content += mcp_info
        except Exception:
            pass
    if dynamic_content:
        messages.append({"role": "system", "content": dynamic_content})

    # ===== 网络出口知情（配置了上游代理时）=====
    # 告知 Agent 三条路径的代理策略：目标请求默认直连可显式走代理、
    # 情报工具自动兜底、命令行子进程默认走代理。不依赖 db，独立于动态段。
    upstream = (getattr(settings, "upstream_proxy", "") or "").strip()
    if upstream:
        messages.append({
            "role": "system",
            "content": (
                "## 网络出口\n"
                f"当前已配置上游代理：{upstream}\n"
                "- send_request / send_session_request / send_raw_request 默认直连目标；"
                "需要经代理访问目标时传 use_proxy=true（未配置代理时会明确报错）。\n"
                "- cve_lookup / web_search 直连失败会自动改走该代理重试。\n"
                "- run_command / run_script 子进程已默认注入 HTTP_PROXY/HTTPS_PROXY"
                "（curl 等自动走代理；localhost/127.0.0.1 不走）。\n"
                "- 内置浏览器（browser 组）启动时跟随该代理（localhost 自动绕过）。"
            ),
        })

    # ===== 分界线机制：线性展开（历史 compaction 固化，不重算）=====
    # 遇到 compaction 步骤直接展开其压缩结果，历史段逐字节稳定 → 前缀缓存命中。
    messages.extend(_linear_restore_steps(steps))

    # 上下文压缩兜底：仅当 steps 中没有 compaction（首次累积期 / 旧对话）时，
    # 用 ContextAssembler 全量重算防膨胀；已有 compaction 时历史已固化，
    # 追加区超预算由 _advance_compaction_boundary 推进分界线，这里不再重算。
    has_compaction = any(step.get("type") == "compaction" for step in steps)
    if not has_compaction:
        if pentest_context is None:
            pentest_context = get_context(task.id)
        conv_msg_count = sum(1 for m in messages if m.get("role") != "system")
        if conv_msg_count > 6:
            assembler = ContextAssembler()
            original_tokens = TokenEstimator.estimate_messages(messages)
            messages = assembler.assemble(
                system_prompt=messages[0].get("content", "") if messages else "",
                messages=messages,
                pentest_context=pentest_context,
            )
            compressed_tokens = TokenEstimator.estimate_messages(messages)
            if original_tokens > compressed_tokens:
                logger.info(
                    f"上下文压缩: {original_tokens} → {compressed_tokens} tokens "
                    f"(节省 {original_tokens - compressed_tokens}, {conv_msg_count} 条对话消息)"
                )

    return messages


def _append_tool_group(messages: list[dict], group: list[dict], assistant_content: str) -> None:
    """将一组连续的 tool_call 步骤还原为 LLM 消息序列。

    优先使用存储的 raw_tool_call 还原原生 assistant.tool_calls + tool 消息；
    若为旧格式（无 raw_tool_call），则降级为文本描述，保证历史可读。
    """
    tool_calls = [g["raw_tool_call"] for g in group if g.get("raw_tool_call")]
    if tool_calls:
        messages.append({
            "role": "assistant",
            "content": assistant_content or None,
            "tool_calls": tool_calls,
        })
        for g in group:
            messages.append({
                "role": "tool",
                "tool_call_id": g.get("tool_call_id", ""),
                "content": _tool_result_to_llm_content(
                    g.get("tool", "unknown"), g.get("args") or {}, g.get("result")
                ),
            })
    else:
        # 旧格式降级：将工具调用转为 assistant 文本描述
        parts = [assistant_content] if assistant_content else []
        for g in group:
            parts.append(
                f"[调用工具 {g.get('tool')}] 参数: "
                f"{json.dumps(g.get('args'), ensure_ascii=False, default=str)}\n"
                f"结果: {json.dumps(g.get('result'), ensure_ascii=False, default=str)}"
            )
        messages.append({"role": "assistant", "content": "\n".join(parts)})


def _record_chat_tool_call(
    db: Session,
    task: Task,
    steps: list[dict],
    tool_call_records: list[dict],
    tc: dict,
    fn_name: str,
    fn_args: dict,
    tool_result: dict,
    timestamp: str,
) -> None:
    """记录一次工具调用：写入 task.steps 并追加到返回记录列表（供前端渲染）。"""
    tool_call_records.append({
        "tool": fn_name,
        "args": fn_args,
        "result": tool_result,
        "timestamp": timestamp,
    })

    # 存储足够信息以便恢复 LLM 对话历史（tool_call_id + raw_tool_call）
    step_data = {
        "tool": fn_name,
        "args": fn_args,
        "result": tool_result,
        "tool_call_id": tc.get("id", ""),
        "raw_tool_call": tc,
    }
    _add_chat_step(db, task, steps, "tool_call", step_data)


async def _execute_tool_standalone(
    task_id: str,
    name: str,
    args: dict,
    db: Session,
    context: PentestContext | None,
    provider_id: str | None = None,
    loaded_groups: set[str] | None = None,
) -> dict:
    """独立工具执行函数：复用 AgentOrchestrator._execute_tool 的逻辑，不依赖外部 self。

    内部创建临时 AgentOrchestrator 实例以复用全部 _tool_* 实现，
    保证工具执行逻辑与自主模式完全一致。

    loaded_groups: 传入调用方（对话循环 tracker）的已加载工具组集合，
        使 load_tools 修改的是调用方的集合（从而影响下一轮工具数组）。
    """
    task = get_task(db, task_id)
    if not task:
        return {"error": "task not found"}

    agent = AgentOrchestrator(db, task, provider_id=provider_id)
    # 注入上下文，供 get_test_coverage / get_strategy / get_payload_list 等工具使用
    agent.context = context
    if loaded_groups is not None:
        agent.loaded_groups = loaded_groups

    return await agent._execute_tool(name, args)


# ===== 智能模型路由 =====

def _get_light_model() -> tuple[str | None, str | None]:
    """返回 (轻量模型名称, Provider ID)，未配置则返回 (None, None)（回退到标准模型）。"""
    pid = getattr(settings, "llm_provider_light", "") or None
    model = getattr(settings, "llm_model_light", "") or None
    return model, pid


def _get_powerful_model() -> tuple[str | None, str | None]:
    """返回 (强力模型名称, Provider ID)，未配置则返回 (None, None)（回退到标准模型）。"""
    pid = getattr(settings, "llm_provider_powerful", "") or None
    model = getattr(settings, "llm_model_powerful", "") or None
    return model, pid


def _is_routing_enabled() -> bool:
    """检查是否启用了智能模型路由。"""
    return getattr(settings, "model_routing_enabled", False)


# 复杂任务关键词：出现这些词时倾向使用强力模型
_COMPLEX_KEYWORDS = frozenset({
    "分析", "漏洞", "利用链", "绕过", "提权", "注入", "越权",
    "验证", "exploit", "payload", "bypass", "privilege",
    "深入", "完整", "总结", "报告", "rce", "ssrf", "xss",
    "sql", "sqli", "命令执行", "文件包含", "反序列化",
    "cve", "knowledge", "知识库", "已知漏洞",
})

# 简单任务关键词：出现这些词时倾向使用轻量模型
_SIMPLE_KEYWORDS = frozenset({
    "列表", "查看", "状态", "帮助", "什么", "怎么", "如何",
    "list", "status", "help", "what", "how", "show",
})


def _select_model_for_chat(
    user_message: str = "",
    tool_call_count: int = 0,
    current_tool_names: list[str] | None = None,
    has_findings: bool = False,
) -> tuple[str | None, str | None]:
    """根据任务复杂度选择模型（Codex 风格智能路由）。

    返回 (模型名称, Provider ID)，或 (None, None)（使用默认标准模型）。
    仅在 model_routing_enabled=True 时生效；未配置对应层级模型时回退到标准模型。

    路由策略：
    - 轻量模型：简单问答、列表查看、状态查询（无复杂关键词 + 无工具调用）
    - 强力模型：漏洞分析、利用链推导、复杂推理（有发现/复杂关键词/高工具调用数）
    - 标准模型：其他常规场景
    """
    if not _is_routing_enabled():
        return (None, None)

    msg_lower = user_message.lower()

    # 1. 复杂任务 → 强力模型（优先判断，避免简单关键词覆盖复杂场景）
    powerful_model, powerful_pid = _get_powerful_model()
    if powerful_model or powerful_pid:
        if has_findings:
            return (powerful_model, powerful_pid)
        if any(kw in msg_lower for kw in _COMPLEX_KEYWORDS):
            return (powerful_model, powerful_pid)
        if tool_call_count >= 8:
            return (powerful_model, powerful_pid)
        # 当正在调用验证/报告类工具时，使用强力模型
        if current_tool_names:
            complex_tools = {"verify_vulnerability", "llm_analyze_evidence", "report_finding", "run_request_chain", "run_command", "run_script"}
            if any(t in complex_tools for t in current_tool_names):
                return (powerful_model, powerful_pid)

    # 2. 简单任务 → 轻量模型
    if tool_call_count == 0:
        if any(kw in msg_lower for kw in _SIMPLE_KEYWORDS):
            if not any(kw in msg_lower for kw in _COMPLEX_KEYWORDS):
                light_model, light_pid = _get_light_model()
                if light_model or light_pid:
                    return (light_model, light_pid)

    # 3. 默认 → 标准模型
    return (None, None)


def _generate_conversation_title(
    user_message: str,
    db: Session | None = None,
    provider_id: str | None = None,
) -> str:
    """用 LLM 根据用户首条消息生成简短对话标题（5-15字）。

    如果 LLM 不可用或调用失败，回退到截取前 20 字符。
    必须传入 db 和 provider_id 以使用数据库中配置的 Provider，
    否则回退到 settings.llm_api_key（生产环境可能为空，导致超时）。
    """
    if not _has_llm(db):
        fallback = user_message.strip().replace("\n", " ")[:20]
        return fallback + ("..." if len(user_message.strip()) > 20 else "")

    try:
        from hemlock.core.llm_client import chat_json

        title_msg = [
            {
                "role": "system",
                "content": (
                    "你是对话标题生成器。根据用户的首条消息，生成一个简短、准确的对话标题。"
                    "要求：5-15个中文字符，不要加引号或前缀。"
                    '返回JSON格式: {"title": "标题文本"}'
                    "例如：用户说'帮我测试 example.com 的SQL注入' -> {\"title\": \"SQL注入测试\"}"
                    "用户说'分析这个登录接口的安全性' -> {\"title\": \"登录接口安全分析\"}"
                    "用户说'请分析以下HTTP数据包' -> {\"title\": \"HTTP数据包分析\"}"
                ),
            },
            {"role": "user", "content": user_message[:500]},
        ]
        result = chat_json(
            title_msg,
            temperature=0.3,
            model=_get_light_model()[0],
            provider_id=provider_id,
            db=db,
        )
        # chat_json 返回字符串
        import json as _json
        try:
            parsed = _json.loads(result)
            title = parsed.get("title", "").strip()
        except (_json.JSONDecodeError, AttributeError):
            title = result.strip().strip('"').strip("'")

        if title and len(title) <= 30:
            return title
        # 回退
        fallback = user_message.strip().replace("\n", " ")[:20]
        return fallback + ("..." if len(user_message.strip()) > 20 else "")
    except Exception as e:
        logger.warning(f"LLM 生成对话标题失败，使用回退: {e}")
        fallback = user_message.strip().replace("\n", " ")[:20]
        return fallback + ("..." if len(user_message.strip()) > 20 else "")


async def chat_with_agent(task_id: str, user_message: str, db: Session, system_prompt_override: str | None = None, provider_id: str | None = None) -> dict:
    """对话式 Agent 交互 - Codex 风格。

    流程：
    1. 加载任务，从 steps 中恢复对话历史（messages 格式）
    2. 追加用户消息
    3. 调用 LLM (with AGENT_TOOLS)
    4. 如果有 tool_calls，逐个执行工具（最多执行 15 个工具调用防止无限循环）
    5. 将工具结果反馈给 LLM，获取下一步回复
    6. 重复 4-5 直到 LLM 返回纯文本无 tool_calls
    7. 保存对话历史到 task.steps
    8. 返回 {"reply": "AI文本回复", "tool_calls": [工具调用记录列表]}

    重要：
    - 从 task.steps 中恢复对话历史时，将 user_message 转为 role:user，
      assistant_message 转为 role:assistant，tool_call 转为 role:tool
    - 使用 llm_client.chat_with_retry() 调用 LLM
    - 使用 AgentOrchestrator._execute_tool() 的逻辑执行工具（_execute_tool_standalone）
    - 敏感工具（run_intruder）跳过人工确认，直接返回需要确认的提示（对话模式下由前端处理）
    - 每个工具调用通过 event_bus 发布事件
    - 使用 PentestContext 追踪上下文
    - system_prompt_override: 自定义系统提示词（如资产收集角色），为 None 时使用默认 SYSTEM_PROMPT
    - provider_id: 指定使用的 LLM Provider ID，为 None 时使用数据库默认 Provider
    """
    task = get_task(db, task_id)
    if not task:
        return {"reply": "任务不存在，请先创建测试任务。", "tool_calls": []}

    if not _has_llm(db):
        task.status = TASK_STATUS_IDLE
        db.commit()
        return {"reply": "未配置 LLM API Key，Agent 无法运行。", "tool_calls": []}

    # 确保会话存在（跨多轮对话保持 Cookie / Token）
    session_id = f"agent_{task.id}"
    if not session_manager.get_session(session_id):
        session_manager.create_session(session_id)

    # 确保 PentestContext 存在（跨多轮对话保持测试进度）
    context = get_context(task_id)
    if not context:
        context = create_context(task_id, task.goal or "")

    # 创建一个编排器实例用于上下文追踪（复用 _auto_track_context，与自主模式逻辑一致）
    tracker = AgentOrchestrator(db, task, provider_id=provider_id)
    tracker.context = context
    # 注册到 _active_agents，使 cancel_agent_task() 能中断对话循环
    _active_agents[task_id] = tracker
    # 标记为活跃对话循环，供 /agent/inject 判断是否可入队插入消息
    tracker._chat_active = True

    # 1. 从 task.steps 恢复对话历史（传入 db 以注入 Skill 列表，传入 context 用于上下文压缩）
    steps: list[dict] = json.loads(task.steps or "[]")
    messages = _build_chat_messages(steps, task, db, system_prompt_override, pentest_context=context)

    # 1.5 注入记忆快照（独立 system 消息：对话开始时固化，期间不可变）
    # 注意：快照是独立消息放在动态段之后、对话历史之前——
    # 不追加进动态段（避免每轮记忆变化让动态段前缀缓存作废），
    # 快照自身惰性生成一次、中途 save_memory 不回流，逐字节稳定可被缓存命中。
    try:
        snapshot_generated = _inject_memory_snapshot(messages, db, task)
        if snapshot_generated:
            db.commit()
        elif task.memory_snapshot:
            logger.info("对话模式注入记忆快照")
    except Exception as e:
        logger.debug(f"注入记忆快照失败（非致命）: {e}")

    # 2. 追加用户消息
    messages.append({"role": "user", "content": user_message})
    _add_chat_step(db, task, steps, "user_message", user_message)

    # 标记任务为运行中
    task.status = TASK_STATUS_RUNNING
    db.commit()

    # 如果对话目标为空（新建对话时未设置），用 LLM 生成简短标题
    # 必须用 asyncio.to_thread 包装，避免同步 LLM 调用阻塞事件循环
    # 导致其他 API 请求全部超时
    # 必须传入 provider_id 和 db，确保使用数据库中的 Provider 而非空的 settings
    if not task.goal:
        task.goal = await asyncio.to_thread(
            _generate_conversation_title, user_message, db, provider_id
        )
        db.commit()

    tool_call_records: list[dict] = []
    total_tool_calls = 0
    tool_calls = None  # 初始化，第一次循环时 result 尚未返回
    reasoning_only_nudges = 0  # 连续"仅思考无回复"的纠错计数，避免死循环

    event_bus.publish("agent", {
        "event": "chat_message",
        "task_id": task_id,
        "role": "user",
        "content": user_message,
    })

    # 处理中途插入的用户消息：追加到 messages/steps 并发布事件，
    # 随下一次 LLM 调用一起被 agent 识别（对齐 Claude Code 的下一个思考边界即时识别）。
    def _inject_pending(pending: list[str]) -> None:
        for injected in pending:
            messages.append({"role": "user", "content": injected})
            _add_chat_step(db, task, steps, "user_message", injected)
            event_bus.publish("agent", {
                "event": "chat_message",
                "task_id": task_id,
                "role": "user",
                "content": injected,
            })

    # 3-6. LLM 循环：调用 -> 执行工具 -> 反馈 -> 直到纯文本回复
    while True:
        # 处理中途插入的用户消息（每个循环边界即时识别，无需等整个回合结束）
        # 必须在取消检查之前：取消时 inbox 已被 clear_inbox 清空，此处通常为空
        _inject_pending(tracker.drain_inbox())

        # 用户取消检查
        if tracker._cancelled:
            reply = "（用户已中断对话）"
            _add_chat_step(db, task, steps, "assistant_message", reply)
            task.status = TASK_STATUS_IDLE
            db.commit()
            event_bus.publish("agent", {
                "event": "chat_message",
                "task_id": task_id,
                "role": "assistant",
                "content": reply,
            })
            # 清理注册
            _active_agents.pop(task_id, None)
            return {"reply": reply, "tool_calls": tool_call_records}

        # 统一工具注册：内置 + Skills + MCP + spawn_subagent
        # include_coordinator_tools=True：开放子 Agent 能力，主 Agent 可 spawn 专职子 Agent
        # 达高上限才禁用工具强制总结（兜底防死循环；正常测试流程不受影响）
        active_tools = (
            build_agent_tools(db, include_coordinator_tools=True, loaded_groups=tracker.loaded_groups)
            if total_tool_calls < MAX_CHAT_TOOL_CALLS else None
        )

        try:
            # 智能模型路由：根据复杂度选择模型
            _current_tool_names = [
                tc["function"]["name"] for tc in (tool_calls or [])
                if isinstance(tc, dict) and "function" in tc
            ]
            _has_findings = any(
                rec.get("tool") == "report_finding"
                for rec in tool_call_records
            )
            _routed_model, _routed_pid = _select_model_for_chat(
                user_message=user_message,
                tool_call_count=total_tool_calls,
                current_tool_names=_current_tool_names,
                has_findings=_has_findings,
            )
            if _routed_model:
                logger.debug(f"模型路由: 使用 {_routed_model} (tool_calls={total_tool_calls})")

            # 流式回调：实时推送 thinking_delta 事件
            # content_delta 不推送，最终回复通过 HTTP 响应返回，避免前端重复显示
            def _on_reasoning(delta: str):
                event_bus.publish("agent", {
                    "event": "thinking_delta",
                    "task_id": task_id,
                    "delta": delta,
                })

            # 使用 asyncio.to_thread 包装同步 streaming LLM 调用，避免阻塞事件循环
            # streaming 模式下，reasoning_content 通过回调实时推送给前端
            # should_stop：用户点停止时逐 chunk 中断流式读取，立即终止"继续思考"
            # 看门狗：单次 LLM 调用超时（_CHAT_LLM_WATCHDOG）即终止本轮，防止挂起导致"无法停止"
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    chat_stream_with_retry,
                    messages, tools=active_tools, temperature=0.3,
                    model=_routed_model, provider_id=_routed_pid or provider_id, db=db,
                    on_reasoning=_on_reasoning,
                    should_stop=lambda: tracker._cancelled,
                ),
                timeout=_CHAT_LLM_WATCHDOG,
            )
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}", exc_info=True)
            # 持久化本轮插入但未处理的消息，避免丢失（下一轮仍可被识别）
            _inject_pending(tracker.drain_inbox())
            reply = f"LLM 调用失败: {type(e).__name__}: {e}。请稍后重试。"
            _add_chat_step(db, task, steps, "assistant_message", reply)
            task.status = TASK_STATUS_IDLE
            db.commit()
            event_bus.publish("agent", {
                "event": "chat_message",
                "task_id": task_id,
                "role": "assistant",
                "content": reply,
            })
            _active_agents.pop(task_id, None)
            return {"reply": reply, "tool_calls": tool_call_records}

        if result is None:
            # 持久化本轮插入但未处理的消息，避免丢失
            _inject_pending(tracker.drain_inbox())
            reply = "LLM 连续返回空响应，请稍后重试或调整问题。"
            _add_chat_step(db, task, steps, "assistant_message", reply)
            task.status = TASK_STATUS_IDLE
            db.commit()
            event_bus.publish("agent", {
                "event": "chat_message",
                "task_id": task_id,
                "role": "assistant",
                "content": reply,
            })
            _active_agents.pop(task_id, None)
            return {"reply": reply, "tool_calls": tool_call_records}

        content = result.get("content", "") or ""
        tool_calls = result.get("tool_calls")
        reasoning_content = result.get("reasoning_content", "") or ""

        # 用户点停止后：不执行本轮的 tool_calls，立即结束本轮（LLM 调用已完成）
        if tracker._cancelled:
            _active_agents.pop(task_id, None)
            task.status = TASK_STATUS_IDLE
            db.commit()
            event_bus.publish("agent", {
                "event": "chat_message",
                "task_id": task_id,
                "role": "assistant",
                "content": "已停止",
            })
            return {"reply": "已停止", "tool_calls": tool_call_records}

        # 推理内容（通过 streaming 已实时推送，这里持久化完整内容）
        # 通知前端 thinking 流结束
        if reasoning_content:
            _add_chat_step(db, task, steps, "thinking", reasoning_content)
            event_bus.publish("agent", {
                "event": "thinking_done",
                "task_id": task_id,
                "content": reasoning_content,
            })

        # 纯文本回复（或工具已被禁用）-> 结束本轮对话
        if not tool_calls:
            # 关键：若本次 LLM 调用期间用户插入了新消息，不结束本轮，
            # continue 回到循环顶部优先识别插入消息（即时度：下一思考边界而非回合结束）。
            # 竞态兜底：empty() 与 drain 之间可能刚入队，再 drain 一次防止消息丢失。
            if not tracker.inbox.empty():
                continue
            pending = tracker.drain_inbox()
            if pending:
                _inject_pending(pending)
                continue
            # 推理模型（如本地 Qwen3 思考模式）可能只输出 reasoning 而无最终内容/工具调用，
            # 直接结束会显示 "(AI 暂无回复内容)"。给一次纠错机会让其输出最终回答或调用工具；
            # 纠错仍失败则用说明性兜底回复，避免空占位（思考流已通过 thinking 事件实时展示）。
            if not content.strip() and reasoning_content.strip():
                if reasoning_only_nudges < MAX_REASONING_ONLY_RETRY:
                    reasoning_only_nudges += 1
                    messages.append({
                        "role": "user",
                        "content": "你上一条回复只包含思考过程，没有输出任何最终内容，也没有调用工具。"
                                   "请直接输出对用户有用的最终回答，或调用工具继续执行测试。不要只输出思考。",
                    })
                    continue
                reply = "（模型本轮仅输出思考过程，未给出最终回答或调用工具。已展示上方思考内容，请重试或换用非思考模式模型。）"
            else:
                reply = content
            messages.append({"role": "assistant", "content": reply})
            _add_chat_step(db, task, steps, "assistant_message", reply)
            task.status = TASK_STATUS_IDLE
            db.commit()
            event_bus.publish("agent", {
                "event": "chat_message",
                "task_id": task_id,
                "role": "assistant",
                "content": reply,
            })
            _active_agents.pop(task_id, None)
            return {"reply": reply, "tool_calls": tool_call_records}

        # 有工具调用：记录 assistant 消息（含 tool_calls）
        messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": tool_calls,
        })
        # 中间思考内容（伴随 tool_calls 的 content）标记为 thinking，不作为正式消息展示
        if content:
            _add_chat_step(db, task, steps, "thinking", content)
            event_bus.publish("agent", {
                "event": "thinking",
                "task_id": task_id,
                "content": content,
            })

        # 4-5. 逐个执行工具
        for tc in tool_calls:
            # 用户取消检查（工具执行前）
            if tracker._cancelled:
                break

            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError as e:
                logger.warning(f"工具参数 JSON 解析失败: {e}")
                fn_args = {}

            now_ts = datetime.now(timezone.utc).isoformat()

            # 工具调用上限检查（超过则不再执行，反馈提示给 LLM）
            # 高上限兜底：正常情况下测试流程不会达到；若达到，提示总结而不是报错中断
            if total_tool_calls >= MAX_CHAT_TOOL_CALLS:
                tool_result = {
                    "error": (
                        f"本次对话已执行大量工具调用（{MAX_CHAT_TOOL_CALLS} 次），"
                        f"建议基于已有结果总结，或发送新消息继续测试。"
                    )
                }
                _record_chat_tool_call(
                    db, task, steps, tool_call_records,
                    tc, fn_name, fn_args, tool_result, now_ts,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": _tool_result_to_llm_content(fn_name, fn_args, tool_result),
                })
                continue

            total_tool_calls += 1
            event_bus.publish("agent", {
                "event": "tool_call",
                "task_id": task_id,
                "tool": fn_name,
                "args": fn_args,
            })

            # 敏感工具：对话模式下直接执行（本地工具，用户已掌控操作）
            # 自主模式仍需确认（run_agent_task 中独立处理）
            if fn_name in AgentOrchestrator.SENSITIVE_TOOLS:
                # 发布敏感工具执行通知（让用户知晓，但不阻塞等待确认）
                event_bus.publish("agent", {
                    "event": "sensitive_tool_exec",
                    "task_id": task_id,
                    "tool": fn_name,
                    "args": fn_args,
                })
                try:
                    tool_result = await _execute_tool_standalone(
                        task_id, fn_name, fn_args, db, context,
                        provider_id=provider_id, loaded_groups=tracker.loaded_groups,
                    )
                except Exception as e:
                    logger.error(f"敏感工具 {fn_name} 执行失败: {e}", exc_info=True)
                    tool_result = {
                        "error": f"工具执行失败: {type(e).__name__}: {e}"
                    }
            else:
                # 统一分发：内置 + invoke_skill + mcp_ 前缀均由 _execute_tool 处理
                url_to_check = fn_args.get("url") or fn_args.get("login_url", "")
                if url_to_check and not check_scope(url_to_check):
                    tool_result = {"error": "URL 超出授权范围"}
                else:
                    try:
                        tool_result = await _execute_tool_standalone(
                            task_id, fn_name, fn_args, db, context,
                            provider_id=provider_id, loaded_groups=tracker.loaded_groups,
                        )
                    except Exception as e:
                        logger.error(f"工具 {fn_name} 执行失败: {e}", exc_info=True)
                        tool_result = {
                            "error": f"工具执行失败: {type(e).__name__}: {e}"
                        }

            # 自动追踪上下文（与 AgentOrchestrator.run 中的 _auto_track_context 一致）
            tracker._auto_track_context(fn_name, fn_args, tool_result)

            # spawn_subagent：run_subagent 已把子 Agent 内部步骤（subagent_*）写入 DB，
            # 重新加载 steps 避免本次 _record_chat_tool_call 覆盖丢失
            if fn_name == "spawn_subagent":
                steps = json.loads(task.steps or "[]")

            # 记录工具调用（steps + 返回列表）
            _record_chat_tool_call(
                db, task, steps, tool_call_records,
                tc, fn_name, fn_args, tool_result, now_ts,
            )

            # 反馈工具结果给 LLM
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": _tool_result_to_llm_content(fn_name, fn_args, tool_result),
            })

            event_bus.publish("agent", {
                "event": "tool_result",
                "task_id": task_id,
                "tool": fn_name,
                "result": tool_result,
            })

        # ===== 分界线机制：追加区超预算才推进压缩分界线 =====
        # 有 compaction 步骤时历史段已固化（恢复线性展开、不重算，前缀稳定命中缓存）；
        # messages 总 token ≈ 追加区 token（历史只剩小摘要），超窗口 50% 才推进。
        if total_tool_calls > 0 and total_tool_calls % 10 == 0:
            context_window = _get_context_window(db, provider_id)
            threshold = int(context_window * 0.5)
            current_tokens = TokenEstimator.estimate_messages(messages)
            if current_tokens > threshold:
                before_tokens = current_tokens
                # 推进分界线：压缩追加区 → 固化 compaction → 追加区归零重新积累
                messages, _ctype = await _advance_compaction_boundary(
                    db, task, steps, messages, context, provider_id,
                    threshold=threshold, mode="chat",
                )
                if _ctype != "none":
                    after_tokens = TokenEstimator.estimate_messages(messages)
                    if before_tokens > after_tokens:
                        logger.info(
                            f"对话循环分界线推进({_ctype}): {before_tokens} → {after_tokens} tokens "
                            f"(节省 {before_tokens - after_tokens}, tool_calls={total_tool_calls})"
                        )

        # 继续循环，让 LLM 处理工具结果并决定下一步


# ===== 多 Agent 协作：子 Agent 与指挥官 =====

# 子 Agent 最大执行步数（防止无限循环；高上限兜底，正常协作流程不会达到）
MAX_SUBAGENT_STEPS = 200
# 指挥官最大执行步数（包含 spawn_subagent 的轮次）
MAX_COORDINATOR_STEPS = 200


@contextmanager
def _override_llm_provider(provider_id: str | None, model: str | None, db: Session):
    """临时切换 LLM Provider 配置，退出后自动恢复。

    用于多 Agent 协作中为不同角色指定不同的 LLM Provider/模型。
    当 provider_id 指定时，切换 api_key / base_url 到对应 Provider；
    当 model 指定时，覆盖当前模型名。

    注意：此方法临时修改全局 settings。在指挥官模式下子 Agent 串行执行
    （同一时间仅一个子 Agent 运行），满足此约束。若未来支持并行子 Agent，
    需改为 per-call 客户端注入而非全局 settings 替换。

    Args:
        provider_id: LLM Provider ID，为 None 时使用默认配置。
        model: 模型名称，为 None 时使用 Provider 默认模型或全局默认。
        db: 数据库会话，用于查询 Provider 配置。
    """
    if not provider_id and not model:
        yield
        return

    old_api_key = settings.llm_api_key
    old_base_url = settings.llm_base_url
    old_model = settings.llm_model
    swapped = False

    if provider_id:
        try:
            provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
        except Exception as e:
            logger.warning(f"查询 Provider 失败: {e}")
            provider = None
        if provider:
            settings.llm_api_key = provider.api_key
            settings.llm_base_url = provider.base_url
            if not model:
                settings.llm_model = provider.model
            swapped = True
            logger.debug(f"切换 LLM Provider: {provider.name} (model={model or provider.model})")
        else:
            logger.warning(f"Provider 不存在: {provider_id}，使用默认配置")

    if model:
        settings.llm_model = model
        swapped = True

    try:
        yield
    finally:
        if swapped:
            settings.llm_api_key = old_api_key
            settings.llm_base_url = old_base_url
            settings.llm_model = old_model


# 子 Agent「任务优先原则」：角色是专业背景参考，不是任务范围限制。
# 角色 prompt 是按领域写的"岗位说明书"（如 scout 只写侦察），若主 Agent 下发的
# 任务与其不一致（如给 scout 派攻击验证），子 Agent 会走样或拒绝。注入本段使
# 任务目标成为最高优先级，角色专长只是"做这件事更熟练"。
_SUBAGENT_TASK_FIRST = """\
## 任务优先原则
你的唯一目标是完成主 Agent 下发的任务目标（goal）。角色定位是你的专业背景与工作方法参考，
帮助你更熟练地完成任务，但**不是任务范围的限制**。

- 若任务目标与角色常见职责不一致，一律以任务目标为准，按任务要求执行
- 不要因"这不是本角色常做的"而拒绝、推迟或擅自换任务
- 也不要擅自扩大范围：任务没要求的不做，避免越权或浪费

请严格执行任务目标，完成后回复 TASK_COMPLETE 并给出执行摘要。
"""

# 通用子 Agent 提示词：未指定角色（spawn_subagent 不填 role）或角色不存在时使用。
# 不带领域限定，完全以任务目标为准；需要专业方法时通过技能/策略库按需获取。
_GENERIC_SUBAGENT_PROMPT = """\
# 子 Agent — 通用执行单元
你是主 Agent 委派的执行助手，不限定专业领域。

## 执行原则
- 直接按任务目标要求完成工作，任务没说的不要擅自扩大范围
- 优先使用可用的内置工具（send_request、run_intruder、verify_vulnerability、report_finding、run_command、analyze_js、fuzz_paths、cve_lookup 等）执行
- 需要特定测试方法时，通过 invoke_skill 加载相关技能指令，或参考测试策略
- 每完成一步简要记录进展；发现漏洞立即 verify + report_finding（带证据）
- 完成后回复 TASK_COMPLETE 并给出执行摘要
"""


async def run_subagent(
    role_name: str,
    goal: str,
    context: PentestContext,
    db: Session,
    parent_task_id: str,
    provider_id: str | None = None,
    model: str | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """启动一个子 Agent 执行特定角色任务。

    子 Agent 的特点：
    - 使用角色专属系统提示词（来自 agent_registry）
    - 共享父任务的 PentestContext（读写）
    - 不能调用 spawn_subagent（防止递归）
    - 有独立的 message 历史（不持久化到 task.steps）
    - 执行完毕后返回摘要给指挥官

    Args:
        role_name: 角色名称（scout/striker/auditor/radar/scribe）。
        goal: 子 Agent 的具体任务目标。
        context: 共享的渗透测试上下文（PentestContext）。
        db: 数据库会话。
        parent_task_id: 父任务 ID，用于工具执行时关联任务。
        provider_id: 指定 LLM Provider，为 None 时用默认配置。
        model: 指定模型名，为 None 时用 Provider 默认或全局默认。
        should_stop: 轮询的停止回调（通常引用父 tracker._cancelled）。
            返回 True 时在下一个循环/工具边界立即终止子 Agent，
            使父任务的"停止"信号传播进子 Agent（否则子 Agent 会跑满 200 步）。

    Returns:
        {"role": str, "role_display": str, "summary": str,
         "findings": list[dict], "status": str}
    """
    # 确保内置角色文件存在（基于文件的角色注册表）
    ensure_builtin_roles()
    # role 未指定 → 归一化为 "generic"，保证事件/持久化/前端去重的 role 标识一致
    role_name = role_name or "generic"
    role = get_role_by_name(db, role_name)
    if not role:
        # 角色不存在 → 回退为通用执行单元（不强制选角色，任务目标优先）
        role = AgentRole(
            name=role_name,
            label="通用执行",
            description="未指定专职角色的通用执行单元",
            category="custom",
            is_builtin=False,
            system_prompt=_GENERIC_SUBAGENT_PROMPT,
            file_path=None,
        )

    if not _has_llm(db):
        return {
            "role": role_name,
            "summary": "未配置 LLM API Key，子 Agent 无法运行",
            "findings": [],
            "status": "error",
        }

    # 子 Agent 工具：内置 + Skills + MCP，排除 spawn_subagent（防止递归）。
    # exclude_sensitive：run_intruder/run_command/run_script 无人工确认通道，
    # 执行时必被挡，直接从定义剔除，避免模型试调浪费。
    sub_tools = build_agent_tools(db, include_coordinator_tools=False, exclude_sensitive=True)

    # 构建消息历史：任务优先原则 + 角色 system_prompt + 上下文摘要 + 任务目标
    # 任务优先原则置顶，优先级最高：防止角色"岗位说明书"与主 Agent 下发任务冲突
    system_content = _SUBAGENT_TASK_FIRST + "\n" + role.system_prompt
    if context:
        context_prompt = context.build_context_prompt()
        system_content += f"\n\n## 当前测试上下文\n目标 URL: {context.target_url}\n{context_prompt}"

    messages: list[dict] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": (
            f"任务目标: {goal}\n"
            f"目标 URL: {context.target_url if context else '未知'}\n"
            f"请开始执行任务。"
        )},
    ]

    # 创建 tracker 用于上下文追踪（复用 AgentOrchestrator 的 _auto_track_context）
    task = get_task(db, parent_task_id)
    tracker = AgentOrchestrator(db, task, provider_id=provider_id) if task else None
    if tracker:
        tracker.context = context

    # 子 Agent 内部步骤持久化目标：父任务 steps。
    # subagent_* 类型步骤会被 _build_chat_messages 跳过，不污染 LLM 历史重建；
    # 但会被前端 stepsToMessages 还原为嵌套卡片，刷新后工具调用细节不丢失。
    parent_steps: list[dict] = json.loads(task.steps or "[]") if task else []

    # 确保会话存在（子 Agent 共享父任务会话，保持 Cookie/Token）
    session_id = f"agent_{parent_task_id}"
    if not session_manager.get_session(session_id):
        session_manager.create_session(session_id)

    # 发布子 Agent 启动事件
    if task:
        _add_chat_step(db, task, parent_steps, "subagent_started",
                       {"role": role_name, "role_display": role.label, "goal": goal})
    event_bus.publish("agent", {
        "event": "subagent_started",
        "task_id": parent_task_id,
        "parent_task_id": parent_task_id,
        "role": role_name,
        "role_display": role.label,
        "goal": goal,
    })

    findings_before = len(context.findings) if context else 0
    final_content = ""
    cancelled = False

    # 子 Agent 中途消息 inbox：用户广播消息下一边界注入（广播语义 = 主 Agent + 子 Agent 都听到）
    with _override_llm_provider(provider_id, model, db), \
         _subagent_inbox_lifecycle(parent_task_id) as subagent_inbox:
        for step_num in range(MAX_SUBAGENT_STEPS):
            # 父任务已停止 → 立即终止子 Agent（下一个循环边界）
            if should_stop is not None and should_stop():
                cancelled = True
                final_content = f"{role.label} 已取消"
                break

            # 用户中途消息广播：drain 子 Agent inbox，注入上下文让子 Agent 调整方向。
            # 用户消息已在主会话乐观显示（前端 handleSend 的 opt-user），子 Agent 的
            # 响应通过卡片内思考链 / 工具调用实时反馈，这里无需再发额外显示事件。
            if not subagent_inbox.empty():
                drained_msgs = []
                while not subagent_inbox.empty():
                    drained_msgs.append(subagent_inbox.get_nowait())
                for msg in drained_msgs:
                    messages.append({
                        "role": "user",
                        "content": f"[用户中途指示] {msg}",
                    })
            try:
                # 流式 LLM 调用（与主 Agent chat_with_agent 体验对齐）：
                # - reasoning 逐 chunk 实时推送 subagent_thinking_delta → 前端卡片内实时显示思考，
                #   不再一次性等待完整响应（修复"子 Agent 开始工作后无任何反馈"）
                # - should_stop 逐 chunk 检查 → 点停止立即中断流式读取（修复"停止无效"）
                # - 看门狗超时兜底防 LLM 挂起（修复"卡住"）
                def _on_reasoning(delta: str):
                    event_bus.publish("agent", {
                        "event": "subagent_thinking_delta",
                        "task_id": parent_task_id,
                        "parent_task_id": parent_task_id,
                        "role": role_name,
                        "role_display": role.label,
                        "delta": delta,
                    })

                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        chat_stream_with_retry,
                        messages, tools=sub_tools, temperature=0.3, db=db,
                        on_reasoning=_on_reasoning,
                        should_stop=should_stop,
                    ),
                    timeout=_CHAT_LLM_WATCHDOG,
                )

                # LLM 调用期间被停止 → 立即终止
                if should_stop is not None and should_stop():
                    cancelled = True
                    final_content = f"{role.label} 已取消"
                    break

                if result is None:
                    final_content = f"{role.label} 连续返回空响应，提前结束"
                    break

                content = result.get("content", "") or ""
                tool_calls = result.get("tool_calls")
                reasoning_content = result.get("reasoning_content", "") or ""

                # 推理内容持久化为 subagent_thinking 步骤（完整内容，流式段已通过 delta 实时推送，
                # 前端用 done 事件覆盖流式段）。_build_chat_messages 会跳过该类型，不污染 LLM 历史。
                if reasoning_content:
                    if task:
                        _add_chat_step(db, task, parent_steps, "subagent_thinking",
                                       {"role": role_name, "role_display": role.label,
                                        "content": reasoning_content})
                    event_bus.publish("agent", {
                        "event": "subagent_thinking_done",
                        "task_id": parent_task_id,
                        "parent_task_id": parent_task_id,
                        "role": role_name,
                        "role_display": role.label,
                        "content": reasoning_content,
                    })

                # 子 Agent 返回 TASK_COMPLETE → 结束
                if content and "TASK_COMPLETE" in content:
                    final_content = content
                    messages.append({"role": "assistant", "content": content})
                    break

                # 无工具调用 → 纯文本回复，结束
                if not tool_calls:
                    final_content = content if content else f"{role.label} 执行完毕"
                    messages.append({"role": "assistant", "content": content})
                    break

                # 有工具调用：记录 assistant 消息
                messages.append({
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": tool_calls,
                })
                # 伴随工具调用的思考内容持久化 + 发布 done（完整内容，前端覆盖流式段）
                if content:
                    if task:
                        _add_chat_step(db, task, parent_steps, "subagent_thinking",
                                       {"role": role_name, "role_display": role.label,
                                        "content": content})
                    event_bus.publish("agent", {
                        "event": "subagent_thinking_done",
                        "task_id": parent_task_id,
                        "parent_task_id": parent_task_id,
                        "role": role_name,
                        "role_display": role.label,
                        "content": content,
                    })

                # 逐个执行工具
                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    try:
                        fn_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError as e:
                        logger.warning(f"子 Agent 工具参数解析失败: {e}")
                        fn_args = {}

                    # 安全检查：子 Agent 不应调用 spawn_subagent（防止递归）
                    if fn_name == "spawn_subagent":
                        tool_result = {"error": "子 Agent 不能调用 spawn_subagent（防止递归）"}
                    # 敏感工具：子 Agent 自主模式下跳过（无人工确认通道）
                    elif fn_name in AgentOrchestrator.SENSITIVE_TOOLS:
                        tool_result = {"error": "敏感操作需人工确认，子 Agent 模式下跳过"}
                    else:
                        # 范围检查
                        url_to_check = fn_args.get("url") or fn_args.get("login_url", "")
                        if url_to_check and not check_scope(url_to_check):
                            tool_result = {"error": "URL 超出授权范围"}
                        else:
                            try:
                                tool_result = await _execute_tool_standalone(
                                    parent_task_id, fn_name, fn_args, db, context,
                                    provider_id=provider_id,
                                )
                            except Exception as e:
                                logger.error(
                                    f"子 Agent [{role_name}] 工具 {fn_name} 执行失败: {e}",
                                    exc_info=True,
                                )
                                tool_result = {"error": f"工具执行失败: {type(e).__name__}: {e}"}

                    # 自动追踪上下文（与 chat_with_agent 一致）
                    if tracker:
                        tracker._auto_track_context(fn_name, fn_args, tool_result)

                    # 发布工具调用与结果事件（带 parent_task_id 和 role 标识）
                    if task:
                        _add_chat_step(db, task, parent_steps, "subagent_tool_call",
                                       {"role": role_name, "role_display": role.label,
                                        "step": step_num + 1, "tool": fn_name, "args": fn_args})
                    event_bus.publish("agent", {
                        "event": "subagent_tool_call",
                        "task_id": parent_task_id,
                        "parent_task_id": parent_task_id,
                        "role": role_name,
                        "role_display": role.label,
                        "step": step_num + 1,
                        "tool": fn_name,
                        "args": fn_args,
                    })
                    if task:
                        _add_chat_step(db, task, parent_steps, "subagent_tool_result",
                                       {"role": role_name, "role_display": role.label,
                                        "step": step_num + 1, "tool": fn_name, "result": tool_result})
                    event_bus.publish("agent", {
                        "event": "subagent_tool_result",
                        "task_id": parent_task_id,
                        "parent_task_id": parent_task_id,
                        "role": role_name,
                        "role_display": role.label,
                        "step": step_num + 1,
                        "tool": fn_name,
                        "result": tool_result,
                    })

                    # 反馈工具结果给 LLM
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": _tool_result_to_llm_content(fn_name, fn_args, tool_result),
                    })

                    # 父任务停止 → 不再执行剩余工具调用，交给外层循环顶部终止
                    if should_stop is not None and should_stop():
                        break

                # ===== 上下文更新：每 5 步重注入最新测试上下文 =====
                # 启动时注入的 context_prompt 是快照，跑久会过期（子 Agent 自己也在
                # 用工具发现新端点/参数/漏洞，指挥官/主 Agent 之后的决策需要最新进度）。
                if step_num > 0 and step_num % 5 == 0 and context:
                    fresh_prompt = context.build_context_prompt()
                    messages.append({
                        "role": "user",
                        "content": f"[系统提示] 以下是当前测试进度更新，请参考决定下一步操作：{fresh_prompt}",
                    })

                # ===== 上下文管理：子 Agent 动态压缩 =====
                # 每 5 步检查 messages 大小，超预算时压缩（与 chat_with_agent 一致）
                if step_num > 0 and step_num % 5 == 0:
                    current_tokens = TokenEstimator.estimate_messages(messages)
                    if current_tokens > HOT_BUDGET_TOKENS + 2000:
                        assembler = ContextAssembler()
                        before_tokens = current_tokens
                        messages = assembler.compress_messages(messages, pentest_context=context)
                        after_tokens = TokenEstimator.estimate_messages(messages)
                        if before_tokens > after_tokens:
                            logger.info(
                                f"子 Agent [{role_name}] 动态压缩: {before_tokens} → {after_tokens} tokens "
                                f"(节省 {before_tokens - after_tokens}, step={step_num})"
                            )

            except Exception as e:
                logger.error(
                    f"子 Agent [{role_name}] 步骤 {step_num + 1} 失败: {e}",
                    exc_info=True,
                )
                messages.append({
                    "role": "user",
                    "content": f"上一步操作出错: {e}。请分析原因并调整策略继续。",
                })

    if not final_content:
        final_content = f"{role.label} 执行完毕（达到最大步数 {MAX_SUBAGENT_STEPS}）"

    # 收集执行期间新发现的漏洞
    new_findings = []
    if context:
        new_findings = context.findings[findings_before:]

    # 发布子 Agent 完成事件
    if task:
        _add_chat_step(db, task, parent_steps, "subagent_complete",
                       {"role": role_name, "role_display": role.label,
                        "summary": final_content, "new_findings_count": len(new_findings),
                        "cancelled": cancelled})
    event_bus.publish("agent", {
        "event": "subagent_complete",
        "task_id": parent_task_id,
        "parent_task_id": parent_task_id,
        "role": role_name,
        "role_display": role.label,
        "summary": final_content,
        "new_findings_count": len(new_findings),
        "cancelled": cancelled,
    })

    logger.info(
        f"子 Agent [{role_name}] 完成 | 步数={step_num + 1} | "
        f"新发现={len(new_findings)} | 摘要前200字={final_content[:200]}"
    )

    return {
        "role": role_name,
        "role_display": role.label,
        "summary": final_content,
        "findings": new_findings,
        "status": "cancelled" if cancelled else "complete",
    }


def _mark_task_failed(db: Session, task_id: str) -> None:
    """将 running/paused 状态的任务标记为失败（取消/异常兜底）。"""
    try:
        task = get_task(db, task_id)
        if task and task.status in (TASK_STATUS_RUNNING, TASK_STATUS_PAUSED):
            task.status = TASK_STATUS_FAILED
            task.ended_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(f"协作任务 {task_id} 已标记为失败")
    except Exception as e:
        logger.warning(f"标记协作任务 {task_id} 为失败时出错: {e}")


def _publish_coordinator_finished(task_id: str, status: str) -> None:
    """补发协作任务终止事件（前端 runningTasks 对账清除）。

    正常结束由 run_coordinator_task 尾部调用；任务被取消/异常终止时由
    run_coordinator_in_background 兜底调用，保证任何路径前端都能清除"工作中"。
    """
    event_bus.publish("agent", {
        "event": "coordinator_finished",
        "task_id": task_id,
        "status": status,
        "token_usage": get_token_usage(),
    })


async def run_coordinator_task(
    task_id: str,
    goal: str,
    target_url: str,
    roles: list[str],
    role_providers: dict[str, str],
    db: Session,
) -> str:
    """运行指挥官 Agent，编排多 Agent 协作。

    指挥官使用 COORDINATOR_PROMPT 系统提示词，拥有 spawn_subagent 工具，
    通过 ReAct 循环分析任务、分发子任务、收集结果、生成报告。

    指挥官本身不直接发送 HTTP 请求（由子 Agent 执行），但可以使用
    get_test_coverage / list_reported_findings 等只读工具查看协作进度。

    Args:
        task_id: 任务 ID。
        goal: 总体任务目标。
        target_url: 测试目标 URL。
        roles: 参与协作的角色列表（如 ["scout", "striker", "scribe"]）。
        role_providers: 角色到 Provider ID 的映射，未指定的角色用默认 Provider。
        db: 数据库会话。

    Returns:
        任务最终状态（done / failed）。
    """
    task = get_task(db, task_id)
    if not task:
        return TASK_STATUS_FAILED

    if not _has_llm(db):
        task.status = TASK_STATUS_FAILED
        db.commit()
        return TASK_STATUS_FAILED

    # 确保会话和上下文存在
    session_id = f"agent_{task_id}"
    if not session_manager.get_session(session_id):
        session_manager.create_session(session_id)

    context = get_context(task_id)
    if not context:
        context = create_context(task_id, target_url)

    # 创建 tracker 用于上下文追踪和取消控制
    tracker = AgentOrchestrator(db, task)
    tracker.context = context
    _active_agents[task_id] = tracker

    # 标记任务运行中
    task.status = TASK_STATUS_RUNNING
    db.commit()

    steps: list[dict] = json.loads(task.steps or "[]")

    # 确保内置角色文件存在（基于文件的角色注册表）
    ensure_builtin_roles()

    # 构建指挥官系统提示词 + 任务信息
    system_content = COORDINATOR_PROMPT
    # 注入动态角色列表（渐进式披露：仅 name/label/description/category）
    roles_prompt = build_roles_prompt(db)
    if roles_prompt:
        system_content += roles_prompt
    roles_desc = ", ".join(roles) if roles else "全部可用角色"
    system_content += (
        f"\n\n## 当前任务\n"
        f"目标 URL: {target_url}\n"
        f"任务目标: {goal}\n"
        f"参与角色: {roles_desc}\n"
        f"注意：请按照角色选择策略，依次 spawn 对应角色的子 Agent 执行任务。"
    )

    messages: list[dict] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": (
            f"请开始编排多 Agent 协作任务。\n"
            f"目标 URL: {target_url}\n"
            f"任务目标: {goal}\n"
            f"可用角色: {roles_desc}\n"
            f"请分析任务，选择合适的角色并依次启动子 Agent。"
        )},
    ]

    _add_chat_step(db, task, steps, "system_message",
                   f"协作模式启动 | 角色: {roles_desc} | 目标: {target_url}")

    event_bus.publish("agent", {
        "event": "coordinator_started",
        "task_id": task_id,
        "goal": goal,
        "target_url": target_url,
        "roles": roles,
    })

    consecutive_errors = 0
    max_consecutive_errors = 3
    final_status = TASK_STATUS_DONE

    # 指挥官工具集：只读监控 + spawn_subagent（coordinator_mode 收敛）。
    # 提到循环外一次构建——指挥官无 load_tools / MCP，工具列表运行期静态。
    active_tools = build_agent_tools(db, include_coordinator_tools=True, coordinator_mode=True)

    for step_num in range(MAX_COORDINATOR_STEPS):
        # 用户取消检查
        if tracker._cancelled:
            _add_chat_step(db, task, steps, "system_message", "用户取消协作任务")
            task.status = TASK_STATUS_PAUSED
            db.commit()
            final_status = TASK_STATUS_PAUSED
            break

        try:
            # 流式 LLM 调用（对齐主 Agent）：reasoning 实时推送，停止逐 chunk 生效，
            # 看门狗超时兜底防挂起
            def _on_coordinator_reasoning(delta: str):
                event_bus.publish("agent", {
                    "event": "thinking_delta",
                    "task_id": task_id,
                    "delta": delta,
                })

            result = await asyncio.wait_for(
                asyncio.to_thread(
                    chat_stream_with_retry, messages,
                    tools=active_tools, temperature=0.3, db=db,
                    on_reasoning=_on_coordinator_reasoning,
                    should_stop=lambda: tracker._cancelled,
                ),
                timeout=_CHAT_LLM_WATCHDOG,
            )

            # LLM 调用期间被停止 → 不再处理返回内容，交给循环顶部处理取消
            if tracker._cancelled:
                continue

            if result is None:
                _add_chat_step(db, task, steps, "error", "指挥官 LLM 连续返回空响应")
                final_status = TASK_STATUS_FAILED
                break

            content = result.get("content", "") or ""
            tool_calls = result.get("tool_calls")
            reasoning_content = result.get("reasoning_content", "") or ""

            # 推理内容持久化 + 发布 thinking_done（流式段已通过 thinking_delta 实时推送，done 覆盖）
            if reasoning_content:
                _add_chat_step(db, task, steps, "thinking", reasoning_content)
                event_bus.publish("agent", {
                    "event": "thinking_done",
                    "task_id": task_id,
                    "content": reasoning_content,
                })

            # 重置连续错误计数
            consecutive_errors = 0

            # 指挥官返回 TASK_COMPLETE → 结束
            if content and "TASK_COMPLETE" in content:
                _add_chat_step(db, task, steps, "complete", content)
                _add_chat_step(db, task, steps, "assistant_message", content)
                event_bus.publish("agent", {
                    "event": "coordinator_complete",
                    "task_id": task_id,
                    "summary": content,
                })
                break

            # 无工具调用 → 纯文本回复
            if not tool_calls:
                reply = content if content else "(指挥官暂无回复)"
                _add_chat_step(db, task, steps, "assistant_message", reply)
                messages.append({"role": "assistant", "content": reply})
                event_bus.publish("agent", {
                    "event": "chat_message",
                    "task_id": task_id,
                    "role": "assistant",
                    "content": reply,
                })
                # 纯文本无 TASK_COMPLETE，注入提示让指挥官继续
                messages.append({
                    "role": "user",
                    "content": "请继续编排任务。如果所有工作已完成，请回复 TASK_COMPLETE 并给出最终总结。",
                })
                continue

            # 有工具调用：记录 assistant 消息
            messages.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": tool_calls,
            })
            if content:
                _add_chat_step(db, task, steps, "thinking", content)
                event_bus.publish("agent", {
                    "event": "thinking",
                    "task_id": task_id,
                    "content": content,
                })

            # 逐个执行工具
            for tc in tool_calls:
                if tracker._cancelled:
                    break

                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    fn_args = {}

                now_ts = datetime.now(timezone.utc).isoformat()

                if fn_name == "spawn_subagent":
                    # ===== 指挥官专用：启动子 Agent =====
                    sub_role = fn_args.get("role", "")
                    sub_goal = fn_args.get("goal", "")
                    if not sub_goal:
                        tool_result = {"error": "spawn_subagent 需要 goal 参数"}
                    else:
                        # role 可选：run_subagent 内部对空/不存在角色回退通用执行单元
                        if roles and sub_role and sub_role not in roles:
                            logger.info(f"角色 {sub_role} 不在初始列表 {roles} 中，但仍允许启动")
                        sub_provider_id = role_providers.get(sub_role)
                        tool_result = await run_subagent(
                            role_name=sub_role,
                            goal=sub_goal,
                            context=context,
                            db=db,
                            parent_task_id=task_id,
                            provider_id=sub_provider_id,
                            should_stop=lambda: tracker._cancelled,
                        )

                    # 重新加载 steps：run_subagent 已把子 Agent 内部步骤（subagent_*）写入
                    # task.steps，本地 steps 已过期。不重载会把这批步骤覆盖丢失
                    # （与 chat_with_agent spawn 分支的处理一致）。
                    steps = json.loads(task.steps or "[]")

                    _record_chat_tool_call(
                        db, task, steps, [], tc, fn_name, fn_args, tool_result, now_ts,
                    )

                elif fn_name in AgentOrchestrator.SENSITIVE_TOOLS:
                    # 敏感工具：指挥官模式下跳过
                    tool_result = {
                        "error": "敏感操作需人工确认，指挥官模式下跳过。"
                                 "请通过子 Agent 执行此操作。",
                    }
                    _record_chat_tool_call(
                        db, task, steps, [], tc, fn_name, fn_args, tool_result, now_ts,
                    )

                else:
                    # 其他内置工具（get_test_coverage / list_reported_findings 等）
                    url_to_check = fn_args.get("url") or fn_args.get("login_url", "")
                    if url_to_check and not check_scope(url_to_check):
                        tool_result = {"error": "URL 超出授权范围"}
                    else:
                        try:
                            tool_result = await _execute_tool_standalone(
                                task_id, fn_name, fn_args, db, context,
                            )
                        except Exception as e:
                            logger.error(f"指挥官工具 {fn_name} 执行失败: {e}", exc_info=True)
                            tool_result = {"error": f"工具执行失败: {type(e).__name__}: {e}"}

                    # 自动追踪上下文
                    tracker._auto_track_context(fn_name, fn_args, tool_result)
                    _record_chat_tool_call(
                        db, task, steps, [], tc, fn_name, fn_args, tool_result, now_ts,
                    )

                # 发布工具事件
                event_bus.publish("agent", {
                    "event": "tool_call",
                    "task_id": task_id,
                    "tool": fn_name,
                    "args": fn_args,
                })
                event_bus.publish("agent", {
                    "event": "tool_result",
                    "task_id": task_id,
                    "tool": fn_name,
                    "result": tool_result,
                })

                # 反馈工具结果给 LLM
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": _tool_result_to_llm_content(fn_name, fn_args, tool_result),
                })

            # ===== 上下文管理：指挥官动态压缩 =====
            # 每 5 步检查 messages 大小，超预算时压缩
            if step_num > 0 and step_num % 5 == 0:
                current_tokens = TokenEstimator.estimate_messages(messages)
                if current_tokens > HOT_BUDGET_TOKENS + 2000:
                    assembler = ContextAssembler()
                    before_tokens = current_tokens
                    messages = assembler.compress_messages(messages, pentest_context=context)
                    after_tokens = TokenEstimator.estimate_messages(messages)
                    if before_tokens > after_tokens:
                        logger.info(
                            f"指挥官动态压缩: {before_tokens} → {after_tokens} tokens "
                            f"(节省 {before_tokens - after_tokens}, step={step_num})"
                        )

        except Exception as e:
            consecutive_errors += 1
            error_msg = f"指挥官步骤 {step_num + 1} 失败: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            _add_chat_step(db, task, steps, "error", error_msg)

            if consecutive_errors >= max_consecutive_errors:
                _add_chat_step(db, task, steps, "error",
                               f"连续 {max_consecutive_errors} 次错误，终止协作")
                final_status = TASK_STATUS_FAILED
                break

            messages.append({
                "role": "user",
                "content": f"上一步操作出错: {error_msg}。请调整策略继续。",
            })

    # 达到最大步数或正常结束
    if final_status == TASK_STATUS_DONE and step_num + 1 >= MAX_COORDINATOR_STEPS:
        _add_chat_step(db, task, steps, "limit",
                       f"指挥官达到最大步数限制 ({MAX_COORDINATOR_STEPS})")

    # 更新任务状态
    task.status = final_status
    task.ended_at = datetime.now(timezone.utc)
    db.commit()

    # 输出 Token 用量统计
    usage = get_token_usage()
    logger.info(f"协作任务 {task_id} 完成 | 状态={final_status} | Token={usage}")

    _publish_coordinator_finished(task_id, final_status)

    # 清理注册
    _active_agents.pop(task_id, None)

    return final_status


async def run_coordinator_in_background(
    task_id: str,
    goal: str,
    target_url: str,
    roles: list[str],
    role_providers: dict[str, str],
) -> str:
    """在后台运行指挥官 Agent 任务（供 API 端点 asyncio.create_task 调用）。

    自行管理数据库会话生命周期，与 run_agent_task 模式一致。
    取消/异常兜底：run_coordinator_task 内部的 except Exception 接不住
    asyncio.CancelledError（BaseException），若不在此处理，任务会跳过它的
    收尾逻辑（状态更新 / coordinator_finished / _active_agents 清理），
    形成僵尸任务（task 停留 running 且 _active_agents 持有泄漏的 tracker，
    看门狗因 tracker 仍在册也清不掉）。
    """
    db = SessionLocal()
    try:
        status = await run_coordinator_task(
            task_id=task_id,
            goal=goal,
            target_url=target_url,
            roles=roles,
            role_providers=role_providers,
            db=db,
        )
        return status
    except asyncio.CancelledError:
        # 任务被取消（事件循环关闭 / create_task 被外部取消）
        logger.warning(f"协作任务 {task_id} 被取消，标记为失败")
        _mark_task_failed(db, task_id)
        _publish_coordinator_finished(task_id, TASK_STATUS_FAILED)
        raise
    except Exception as e:
        logger.error(
            f"协作任务 {task_id} 发生未捕获异常: {type(e).__name__}: {e}",
            exc_info=True,
        )
        _mark_task_failed(db, task_id)
        _publish_coordinator_finished(task_id, TASK_STATUS_FAILED)
        return TASK_STATUS_FAILED
    finally:
        # 清理上下文与注册（正常/取消/异常都执行，杜绝泄漏）
        remove_context(task_id)
        if task_id in _active_agents:
            _active_agents.pop(task_id, None)
        db.close()
