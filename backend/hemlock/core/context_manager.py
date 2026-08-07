"""上下文管理系统：三级记忆架构 + 工具输出蒸馏 + Token 预算管理。

设计参考 Claude Code 的三层压缩机制（MicroCompact → Session Memory → AutoCompact），
针对渗透测试场景做领域专用优化。

核心组件：
- TokenEstimator: 本地 Token 估算（字符数近似，无需 tiktoken）
- ToolOutputDistiller: 安全工具输出蒸馏（95%+ 压缩率）
- FindingRegistry: 漏洞发现注册表（结构化摘要）
- WarmMemoryBuilder: 温记忆构建器（目标画像 + 发现注册表 + 阶段进度）
- ContextAssembler: 上下文组装器（整合三级记忆，管理 Token 预算）

三级记忆模型：
- 热记忆 (Hot)  ~4K tokens: 最近对话原文 + 最近工具输出
- 温记忆 (Warm) ~8K tokens: 发现摘要 + 阶段进度 + 目标画像（自动生成）
- 冷记忆 (Cold) 无上限: 全量 task.steps（原始数据，按需检索）
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ===== 常量 =====

# 热记忆预算（tokens）：保留最近对话和工具输出的原文
HOT_BUDGET_TOKENS = 4000

# 温记忆预算（tokens）：结构化摘要
WARM_BUDGET_TOKENS = 8000

# 安全余量（tokens）：防止估算误差导致超限
SAFETY_MARGIN_TOKENS = 2000

# 单条工具结果蒸馏阈值（字符数）：超过此值才蒸馏，短结果保留原文
DISTILL_THRESHOLD_CHARS = 500

# 中文/英文混合文本的近似字符/token 比率
CHARS_PER_TOKEN = 3.5


# ===== Token 估算器 =====

class TokenEstimator:
    """本地 Token 估算器：使用字符数近似，无需 tiktoken 依赖。

    经验值：英文 ~4 字符/token，中文 ~1.5 字符/token，混合 ~3.5 字符/token。
    在预算管理场景下精度足够（误差 ±20% 在可接受范围内）。
    """

    @staticmethod
    def estimate(text: str | None) -> int:
        """估算单段文本的 token 数。"""
        if not text:
            return 0
        return max(1, int(len(text) / CHARS_PER_TOKEN))

    @staticmethod
    def estimate_messages(messages: list[dict]) -> int:
        """估算消息列表的总 token 数（含元数据开销）。"""
        total = 0
        for msg in messages:
            # content 可能是 None（assistant with tool_calls）
            content = msg.get("content")
            if content:
                total += TokenEstimator.estimate(content)

            # tool_calls 的 arguments 也消耗 token
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    args_str = fn.get("arguments", "")
                    total += TokenEstimator.estimate(args_str)
                    total += TokenEstimator.estimate(fn.get("name", ""))

            # tool 角色消息的 tool_call_id 开销
            if msg.get("tool_call_id"):
                total += 2

        # 每条消息的 role + 结构化开销 ~4 tokens
        return total + len(messages) * 4


# ===== 工具输出蒸馏器 =====

class ToolOutputDistiller:
    """工具输出蒸馏器：将原始工具输出压缩为关键信息摘要。

    针对渗透测试工具的领域专用压缩，保留关键信息（状态码、参数、发现），
    去掉原始数据（完整 HTML、冗长 JSON 等）。

    ⚠️ 设计原则（用户强调，2026-08-06）：
    影响漏洞判断的数据【绝不蒸馏成无信息摘要】——响应体、错误信息、反射内容、
    命令输出、验证证据、命中的 payload/凭据等 Agent 要据此判断漏洞的东西，
    一律【截断保留真实内容】（保留足够长的原文片段）。
    注意：HTML 响应体【不能一概当噪音】——XSS 反射判断依赖 HTML 本身
    （payload 是否未编码反射、反射在 script 标签/事件属性/HTML 注释等何种上下文）。
    只对真正的大体积非判断数据（超长无关尾部、海量同构 JSON 数组）做语义摘要。
    宁可多花 token，不可让 Agent 睁眼瞎。

    压缩率目标：95%+（3000 tokens → 30 tokens）
    """

    # 可蒸馏的工具列表（一次性消费型，输出大且旧后无用）
    DISTILLABLE_TOOLS = frozenset({
        "send_request", "send_session_request", "send_raw_request",
        "analyze_flow", "get_flow_detail", "quick_test_param",
        "run_intruder", "generate_payloads", "get_payload_list",
        "read_file", "call_api", "authenticate",
        "verify_vulnerability", "compare_responses",
        "encode_decode", "run_request_chain",
    })

    # 不蒸馏的工具（输出本身就是摘要或关键信息，蒸馏会丢失 Agent 必需的数据）
    # 注：run_script/run_command 的 stdout 是执行目的，蒸馏成 "[tool] N items, XXXB" 会让 Agent 看不到输出
    #     cve_lookup/web_search 的结构化结果（CVE 列表/搜索条目）被蒸馏成计数会失去意义
    NON_DISTILLABLE_TOOLS = frozenset({
        "report_finding", "list_reported_findings",
        "get_test_coverage", "get_strategy",
        "save_memory", "write_file",
        "send_to_repeater",
        "run_script", "run_command",
        "cve_lookup", "web_search",
    })

    @classmethod
    def should_distill(cls, tool_name: str, result_str: str) -> bool:
        """判断是否需要蒸馏此工具输出。

        只对「有专属蒸馏器」的 DISTILLABLE_TOOLS 蒸馏；未知工具一律不蒸馏——
        通用蒸馏会把结构化结果（cve_lookup 的 CVE 列表、web_search 条目、
        analyze_js 的接口、http_probe 的技术栈、run_script 的 stdout 等）
        压成 "[tool] N items, XXXB"，Agent 拿不到内容。
        """
        if tool_name in cls.NON_DISTILLABLE_TOOLS:
            return False
        if tool_name not in cls.DISTILLABLE_TOOLS:
            return False  # 未知工具：不蒸馏（输出通常是 Agent 必需的结构化数据）
        return len(result_str) > DISTILL_THRESHOLD_CHARS

    @classmethod
    def distill(cls, tool_name: str, tool_args: dict, tool_result: dict | str) -> str:
        """将工具输出蒸馏为一行摘要。

        Args:
            tool_name: 工具名称
            tool_args: 工具参数
            tool_result: 工具结果（dict 或 str）

        Returns:
            蒸馏后的摘要字符串（通常 1-2 行）
        """
        # 确保 tool_result 是 dict
        if isinstance(tool_result, str):
            try:
                tool_result = json.loads(tool_result)
            except (json.JSONDecodeError, TypeError):
                return cls._distill_generic(tool_name, {"raw": tool_result[:200]})

        if not isinstance(tool_result, dict):
            return cls._distill_generic(tool_name, tool_result)

        # 有错误的工具结果：保留错误信息（通常很短）
        if tool_result.get("error"):
            return f"[{tool_name}] error: {str(tool_result['error'])[:200]}"

        # 按工具类型蒸馏
        distillers = {
            "send_request": cls._distill_http_request,
            "send_session_request": cls._distill_http_request,
            "send_raw_request": cls._distill_http_request,
            "authenticate": cls._distill_http_request,
            "analyze_flow": cls._distill_flow_analysis,
            "get_flow_detail": cls._distill_flow_detail,
            "quick_test_param": cls._distill_quick_test,
            "run_intruder": cls._distill_intruder,
            "generate_payloads": cls._distill_payloads,
            "get_payload_list": cls._distill_payload_list,
            "read_file": cls._distill_file_read,
            "call_api": cls._distill_call_api,
            "verify_vulnerability": cls._distill_verify,
            "compare_responses": cls._distill_compare,
            "run_request_chain": cls._distill_request_chain,
        }

        distiller = distillers.get(tool_name, cls._distill_generic)
        try:
            return distiller(tool_args, tool_result)
        except Exception as e:
            logger.debug(f"蒸馏 {tool_name} 失败，回退到通用: {e}")
            return cls._distill_generic(tool_name, tool_result)

    # ===== 具体蒸馏器 =====

    @staticmethod
    def _distill_http_request(args: dict, result: dict) -> str:
        """蒸馏 HTTP 请求/响应工具输出。

        保留：方法、URL、状态码、flow_id（关键引用，供 get_flow_detail/report_finding）、
             Server、Set-Cookie、响应体大小
        去掉：完整响应头、完整响应体
        """
        method = args.get("method", "GET")
        url = args.get("url", "")
        # 简化 URL：只保留 path
        if "://" in url:
            url = "/" + "/".join(url.split("/", 3)[3:])

        status = result.get("status_code", result.get("status", "?"))
        # 保留 flow_id：Agent 需要它调用 get_flow_detail / report_finding
        flow_id = result.get("flow_id", "")
        headers = result.get("headers", {})
        if isinstance(headers, str):
            headers_dict = {}
            for line in headers.split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers_dict[k.strip().lower()] = v.strip()
            headers = headers_dict

        # 提取关键头（大小写不敏感）
        parts = []
        server = headers.get("server", headers.get("Server", ""))
        if server:
            parts.append(server)
        cookie = headers.get("set-cookie", headers.get("Set-Cookie", ""))
        if cookie:
            cookie_name = cookie.split("=")[0][:20] if "=" in cookie else cookie[:20]
            parts.append(f"Set-Cookie:{cookie_name}")
        content_type = headers.get("content-type", headers.get("Content-Type", ""))
        if content_type:
            parts.append(content_type.split(";")[0])

        # 响应体：保留大小 + 开头片段（漏洞特征——SQL 报错 / XSS 反射 / 命令回显——都在 body 里）
        # 修复：原实现只留 body 大小，Agent 对每个请求看不到响应体内容 = 睁眼瞎。
        # 截断真实内容（保留前 _HTTP_BODY_PREVIEW 字符），而非蒸馏成"无信息"。
        body = result.get("body", result.get("response_body", ""))
        body_size = len(body) if body else 0
        if body_size > 0:
            if body_size < 1024:
                parts.append(f"body:{body_size}B")
            else:
                parts.append(f"body:{body_size / 1024:.1f}KB")

        detail = ", ".join(parts) if parts else "no details"
        flow_tag = f" flow={flow_id}" if flow_id else ""

        # body 预览（截断真实内容，Agent 靠它判断反射/报错/特征；HTML 可能是 XSS 判断标准，给足上下文）
        if isinstance(body, str) and body.strip():
            body_snippet = body.strip().replace("\n", " ")[:2000]
            return f"[{method} {url}] {status}{flow_tag} → {detail}\nbody: {body_snippet}"

        return f"[{method} {url}] {status}{flow_tag} → {detail}"

    @staticmethod
    def _distill_flow_analysis(args: dict, result: dict) -> str:
        """蒸馏流量分析结果。"""
        flow_id = result.get("flow_id", result.get("id", "?"))
        method = result.get("method", "?")
        path = result.get("path", result.get("url", ""))
        if "://" in path:
            path = "/" + "/".join(path.split("/", 3)[3:])
        status = result.get("status_code", result.get("status", "?"))

        # 参数
        params = result.get("params", {})
        if isinstance(params, dict) and params:
            param_str = ", ".join(list(params.keys())[:5])
        else:
            param_str = ""

        parts = [f"Flow #{flow_id}: {method} {path} {status}"]
        if param_str:
            parts.append(f"params:{param_str}")
        return ", ".join(parts)

    @staticmethod
    def _distill_flow_detail(args: dict, result: dict) -> str:
        """蒸馏流量详情。

        与通用 flow 分析不同：get_flow_detail 的语义是"取完整详情供漏洞判断"，
        蒸馏不能只留方法/路径/状态码——否则 Agent 拿不到响应体里的漏洞特征
        （SQL 报错、反射 XSS、命令输出等）会导致误判。
        因此额外保留响应头关键字段 + 响应体开头片段（漏洞特征通常出现在此处）。
        """
        flow_id = result.get("flow_id", result.get("id", "?"))
        method = result.get("method", "?")
        path = result.get("url", "")
        if "://" in path:
            path = "/" + "/".join(path.split("/", 3)[3:])
        status = result.get("status_code", result.get("status", "?"))

        parts = [f"Flow #{flow_id}: {method} {path} {status}"]

        # 响应头关键字段（content-type / server / set-cookie 等）
        resp_headers = result.get("resp_headers", result.get("headers", ""))
        if isinstance(resp_headers, str):
            for line in resp_headers.split("\n"):
                low = line.lower()
                if any(k in low for k in ("content-type", "server", "set-cookie", "location", "x-powered-by")):
                    parts.append(line.strip()[:120])
                    if len(parts) >= 5:
                        break

        # 响应体开头片段（漏洞特征：SQL 报错 / 反射 XSS / 命令回显；分析页面源码/JS 也需要足够长）
        resp_body = result.get("resp_body", result.get("body", result.get("response_body", "")))
        if isinstance(resp_body, str) and resp_body.strip():
            snippet = resp_body.strip().replace("\n", " ")[:3000]
            parts.append(f"body_start: {snippet}")

        return " | ".join(parts)

    @staticmethod
    def _distill_quick_test(args: dict, result: dict) -> str:
        """蒸馏快速参数测试结果。"""
        vuln_type = args.get("vuln_type", args.get("test_type", "?"))
        url = args.get("url", "")
        if "://" in url:
            url = "/" + "/".join(url.split("/", 3)[3:])
        # 参数名：兼容 param / parameter / param_name 三种命名
        param = args.get("param", args.get("parameter", args.get("param_name", "")))
        # 从结果里回退取实际测试 URL（蒸馏器拿不到时返回空参数名，避免 ?= 假象）
        test_url = result.get("test_url", "")
        if test_url and "://" in test_url:
            test_url = "/" + "/".join(test_url.split("/", 3)[3:])

        # 检测是否有漏洞
        vulnerable = result.get("vulnerable", result.get("is_vulnerable", False))
        display_url = test_url or (f"{url}?{param}=" if param else url)
        if vulnerable:
            payload = result.get("payload", result.get("evidence", ""))
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload, ensure_ascii=False)[:300]
            else:
                payload = str(payload)[:300]
            # 命中时的异常信号（响应变化特征）对漏洞判断关键，保留
            signal = result.get("signal", result.get("detail", ""))
            signal_str = f", signal:{str(signal)[:150]}" if signal else ""
            return f"[{vuln_type} test {display_url}] VULNERABLE, payload:{payload}{signal_str}"
        else:
            return f"[{vuln_type} test {display_url}] not vulnerable"

    @staticmethod
    def _distill_intruder(args: dict, result: dict) -> str:
        """蒸馏爆破结果。

        不能只留 found/total —— Agent 需要知道具体哪个 payload/凭据命中，
        否则"知道有洞但用不了"（如弱口令爆破命中了但不知账号密码）。
        因此保留所有命中的 payload + 状态码（最多 20 个）。
        """
        total = result.get("total_attempts", result.get("total", 0))
        results = result.get("results", [])
        found = result.get("successful", result.get("found", 0))
        if not found and isinstance(results, list):
            found = sum(1 for r in results if not r.get("error") and r.get("status") not in (401, 403, 404, 400))

        # 命中明细：payload + 状态码 + flow_id（Agent 凭此报告漏洞 / 继续利用）
        hits = []
        if isinstance(results, list):
            for r in results:
                if r.get("error"):
                    continue
                st = r.get("status")
                # 401/403/404/400 视为未命中；其余视为命中（含 200/302/500 等）
                if st in (401, 403, 404, 400):
                    continue
                hits.append(f"{r.get('payload', '?')}->{st}({r.get('flow_id', '')})")
                if len(hits) >= 20:
                    break

        if hits:
            return f"[run_intruder] {found}/{total} hits: " + ", ".join(hits)
        return f"[run_intruder] {found}/{total} hits"

    @staticmethod
    def _distill_payloads(args: dict, result: dict) -> str:
        """蒸馏 Payload 生成结果。"""
        vuln_type = args.get("vuln_type", "?")
        payloads = result.get("payloads", [])
        if isinstance(payloads, list):
            count = len(payloads)
            sample = payloads[0][:50] if payloads else ""
            return f"[generate_payloads {vuln_type}] {count} payloads, sample:{sample}"
        return f"[generate_payloads {vuln_type}] generated"

    @staticmethod
    def _distill_payload_list(args: dict, result: dict) -> str:
        """蒸馏 Payload 列表。"""
        vuln_type = args.get("vuln_type", "?")
        payloads = result.get("payloads", [])
        count = len(payloads) if isinstance(payloads, list) else 0
        return f"[get_payload_list {vuln_type}] {count} payloads available"

    @staticmethod
    def _distill_file_read(args: dict, result: dict) -> str:
        """蒸馏文件读取结果。"""
        filename = args.get("filename", args.get("path", "?"))
        content = result.get("content", "")
        size = len(content) if content else 0

        # 提取关键段落名（YAML/INI 风格）
        key_sections = []
        if content:
            for line in content.split("\n")[:200]:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    if stripped.endswith(":") or "=" in stripped:
                        section = stripped.split(":")[0].split("=")[0].strip()
                        if section and len(section) < 30:
                            key_sections.append(section)
            key_sections = key_sections[:8]

        size_str = f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"
        sections_str = ", ".join(key_sections) if key_sections else "none"
        return f"[read_file {filename}] {size_str}, sections: {sections_str}"

    @staticmethod
    def _distill_call_api(args: dict, result: dict) -> str:
        """蒸馏 API 调用结果。

        修复：call_api 返回的字段是 data（解析后的 JSON）与 resp_length，不是 body/response。
        原实现读 body 恒为空 → 显示 body:0B，Agent 看不到平台数据内容。
        保留 data 片段：Agent 常通过 call_api 读取平台数据（如 /findings、/flows）评估测试进展。
        """
        import json as _json
        status = result.get("status_code", result.get("status", "?"))
        data = result.get("data")
        body = result.get("body", result.get("response", ""))
        # call_api 的实际返回：data（JSON）或 {"raw": text}；resp_length 为响应体大小
        if data is not None:
            if isinstance(data, str):
                snippet = data.strip().replace("\n", " ")[:300]
                return f"[call_api] {status}, body:{len(data)}B, preview: {snippet}"
            try:
                data_str = _json.dumps(data, ensure_ascii=False)[:300]
            except Exception:
                data_str = str(data)[:300]
            resp_len = result.get("resp_length", len(data_str))
            return f"[call_api] {status}, body:{resp_len}B, preview: {data_str}"
        if isinstance(body, str) and body.strip():
            snippet = body.strip().replace("\n", " ")[:300]
            return f"[call_api] {status}, body:{len(body)}B, preview: {snippet}"
        return f"[call_api] {status}, body:{result.get('resp_length', 0)}B"

    @staticmethod
    def _distill_verify(args: dict, result: dict) -> str:
        """蒸馏漏洞验证结果（保留关键判定 + 证据）。

        原则：影响漏洞判断的数据不蒸馏成"无信息"。验证结论(confirmed/method/evidence)
        是 Agent 决定是否 report_finding 的依据，证据要保留足够长。
        """
        confirmed = result.get("confirmed", result.get("is_vulnerable", False))
        reason = result.get("reason", result.get("evidence", result.get("details", "")))
        if isinstance(reason, (dict, list)):
            reason = json.dumps(reason, ensure_ascii=False)[:300]
        else:
            reason = str(reason)[:300]
        status = "CONFIRMED" if confirmed else "FALSE_POSITIVE"
        # 附带验证方法（布尔盲注/XSS反射/时间盲注…），Agent 判断可信度用
        method = result.get("method", "")
        method_str = f", method:{method}" if method else ""
        return f"[verify_vulnerability] {status}{method_str}, reason:{reason}"

    @staticmethod
    def _distill_compare(args: dict, result: dict) -> str:
        """蒸馏响应对比结果。"""
        diff_found = result.get("difference_found", result.get("has_diff", False))
        similarity = result.get("similarity", "")
        return f"[compare_responses] diff:{diff_found}, similarity:{similarity}"

    @staticmethod
    def _distill_request_chain(args: dict, result: dict) -> str:
        """蒸馏请求链结果。"""
        steps = result.get("steps", result.get("results", []))
        count = len(steps) if isinstance(steps, list) else 0
        return f"[run_request_chain] {count} steps executed"

    @staticmethod
    def _distill_generic(tool_name: str, result: Any) -> str:
        """通用蒸馏器：提取关键信息。"""
        if isinstance(result, dict):
            # 提取常见字段
            if result.get("error"):
                return f"[{tool_name}] error: {str(result['error'])[:150]}"
            items = result.get("items", result.get("results", result.get("data", [])))
            if isinstance(items, list):
                return f"[{tool_name}] {len(items)} items, {len(json.dumps(result, ensure_ascii=False))}B"
            return f"[{tool_name}] {len(json.dumps(result, ensure_ascii=False))}B"
        elif isinstance(result, str):
            return f"[{tool_name}] {len(result)}B: {result[:100]}"
        else:
            return f"[{tool_name}] completed"


# ===== 发现注册表 =====

@dataclass
class FindingEntry:
    """单个漏洞发现条目。"""
    finding_id: str          # F-001, F-002...
    vuln_type: str           # SQL_INJECTION, XSS...
    endpoint: str            # /api/user?id=
    severity: str            # critical / high / medium / low / info
    status: str              # pending / confirmed / false_positive / closed
    title: str = ""          # 简短标题
    flow_id: str = ""        # 证据流量 ID（供 get_flow_detail / report_finding 引用）


class FindingRegistry:
    """漏洞发现注册表：结构化存储所有发现，每条仅 1-2 行。

    替代原始的 findings 列表（每条可能占数百 tokens 的详细描述），
    压缩为每条 ~30 tokens 的结构化摘要。
    """

    def __init__(self):
        self._findings: list[FindingEntry] = []
        self._counter = 0

    def add(
        self,
        vuln_type: str,
        endpoint: str,
        severity: str = "medium",
        status: str = "pending",
        title: str = "",
        finding_id: str | None = None,
        flow_id: str = "",
    ) -> str:
        """添加一个发现，返回 finding_id。"""
        if finding_id is None:
            self._counter += 1
            finding_id = f"F-{self._counter:03d}"
        entry = FindingEntry(
            finding_id=finding_id,
            vuln_type=vuln_type,
            endpoint=endpoint[:80],
            severity=severity,
            status=status,
            title=title[:60],
            flow_id=flow_id,
        )
        self._findings.append(entry)
        return finding_id

    def update_status(self, finding_id: str, status: str) -> None:
        """更新发现状态。"""
        for f in self._findings:
            if f.finding_id == finding_id:
                f.status = status
                break

    def build_summary(self) -> str:
        """构建发现注册表摘要文本。"""
        if not self._findings:
            return ""

        lines = [f"## 发现注册表（{len(self._findings)} 个）"]
        for f in self._findings:
            flow_tag = f" flow={f.flow_id}" if f.flow_id else ""
            lines.append(
                f"{f.finding_id} | {f.vuln_type} | {f.endpoint} | "
                f"{f.severity} | {f.status}{flow_tag}"
            )
        return "\n".join(lines)

    @property
    def count(self) -> int:
        return len(self._findings)

    @classmethod
    def from_pentest_context(cls, pentest_context: Any) -> "FindingRegistry":
        """从 PentestContext 的 findings 列表构建注册表。"""
        registry = cls()
        if not pentest_context or not hasattr(pentest_context, "findings"):
            return registry

        for i, f in enumerate(pentest_context.findings, 1):
            registry.add(
                vuln_type=f.get("vuln_type", "UNKNOWN"),
                endpoint=f.get("endpoint", ""),
                severity=f.get("severity", "info"),
                status="confirmed" if f.get("severity") in ("critical", "high") else "pending",
                title=f.get("title", ""),
                finding_id=f.get("finding_id") or f"F-{i:03d}",  # 优先保留真实 finding_id
                flow_id=f.get("flow_id", ""),
            )
        return registry


# ===== 温记忆构建器 =====

class WarmMemoryBuilder:
    """温记忆构建器：整合目标画像、发现注册表、阶段进度。

    温记忆是自动生成的结构化摘要层，注入 system prompt 末尾。
    包含三类信息：
    1. 目标画像（技术栈、认证状态、已知端点）
    2. 发现注册表（所有漏洞发现的 1 行摘要）
    3. 阶段进度（当前测试阶段、覆盖度、未测试参数）
    """

    @staticmethod
    def build(pentest_context: Any | None = None) -> str:
        """构建温记忆文本。

        Args:
            pentest_context: PentestContext 实例（可能为 None）

        Returns:
            温记忆文本（注入 system prompt 末尾），可能为空字符串
        """
        if not pentest_context:
            return ""

        sections = []

        # 1. 目标画像
        target_profile = WarmMemoryBuilder._build_target_profile(pentest_context)
        if target_profile:
            sections.append(target_profile)

        # 2. 发现注册表
        registry = FindingRegistry.from_pentest_context(pentest_context)
        if registry.count > 0:
            sections.append(registry.build_summary())

        # 3. 阶段进度
        stage_progress = WarmMemoryBuilder._build_stage_progress(pentest_context)
        if stage_progress:
            sections.append(stage_progress)

        if not sections:
            return ""

        return "\n\n## 测试上下文摘要（自动生成）\n" + "\n\n".join(sections)

    @staticmethod
    def _build_target_profile(ctx: Any) -> str:
        """构建目标画像。"""
        lines = ["### 目标画像"]

        # 技术栈
        tech = ctx.tech_stack
        tech_parts = []
        if tech.server:
            tech_parts.append(f"Server: {tech.server}")
        if tech.framework:
            tech_parts.append(f"框架: {tech.framework}")
        if tech.language:
            tech_parts.append(f"语言: {tech.language}")
        if tech.cms:
            tech_parts.append(f"CMS: {tech.cms}")
        if tech_parts:
            lines.append(f"技术栈: {', '.join(tech_parts)}")

        # 认证状态
        lines.append(f"认证: {'已认证' if ctx.authenticated else '未认证'}")

        # 已知端点（最多 10 个）
        endpoint_count = len(ctx.endpoints)
        lines.append(f"已知端点: {endpoint_count} 个")
        if endpoint_count > 0 and endpoint_count <= 20:
            for key, ep in list(ctx.endpoints.items())[:10]:
                param_count = len(ep.params)
                auth_tag = " [需认证]" if ep.requires_auth else ""
                param_tag = f" ({param_count} 参数)" if param_count else ""
                lines.append(f"  {ep.method} {ep.path}{auth_tag}{param_tag}")

        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _build_stage_progress(ctx: Any) -> str:
        """构建阶段进度摘要。"""
        stats = ctx.get_coverage_stats()
        lines = ["### 阶段进度"]

        phase_map = {
            "recon": "侦察",
            "testing": "漏洞测试",
            "verifying": "验证",
            "reporting": "报告",
            "done": "完成",
        }
        phase_label = phase_map.get(ctx.phase, ctx.phase)
        lines.append(f"当前阶段: {phase_label}")

        # 覆盖度
        total_params = stats.get("total_params", 0)
        tested_params = stats.get("tested_params", 0)
        coverage = stats.get("param_coverage", "0%")
        lines.append(f"参数覆盖度: {coverage}（{tested_params}/{total_params}）")

        # 漏洞统计
        total_findings = stats.get("total_findings", 0)
        if total_findings > 0:
            sev = stats.get("findings_by_severity", {})
            lines.append(
                f"漏洞: {total_findings} 个 "
                f"(严重:{sev.get('critical', 0)} 高危:{sev.get('high', 0)} "
                f"中危:{sev.get('medium', 0)} 低危:{sev.get('low', 0)})"
            )

        # 请求数
        total_requests = stats.get("total_requests", 0)
        if total_requests > 0:
            lines.append(f"已发送请求: {total_requests}")

        # 未测试参数提示
        untested = ctx.get_untested_params()
        if untested:
            lines.append(f"未测试参数: {len(untested)} 个")
            # 列出前 5 个
            for item in untested[:5]:
                lines.append(
                    f"  - {item['method']} {item['endpoint']} [{item['param_name']}]"
                )

        return "\n".join(lines) if len(lines) > 1 else ""


# ===== 上下文组装器 =====

class ContextAssembler:
    """上下文组装器：管理三级记忆，按 Token 预算组装 LLM 消息。

    核心策略：
    1. system prompt 原样保留 + 温记忆注入末尾
    2. 从消息末尾向前扫描，保留最近消息原文（热记忆窗口）
    3. 超出热记忆窗口的旧工具输出，蒸馏为一行摘要
    4. 当热记忆超预算时，最旧的消息被蒸馏替换

    对外接口：
    - assemble_messages(): 组装完整消息列表（替代 _build_chat_messages 的输出）
    - compress_messages(): 压缩已有消息列表（用于对话循环中）
    """

    def __init__(
        self,
        hot_budget: int = HOT_BUDGET_TOKENS,
        warm_budget: int = WARM_BUDGET_TOKENS,
    ):
        self.hot_budget = hot_budget
        self.warm_budget = warm_budget

    def assemble(
        self,
        system_prompt: str,
        messages: list[dict],
        pentest_context: Any | None = None,
    ) -> list[dict]:
        """组装发送给 LLM 的完整消息列表。

        缓存友好设计（对齐 Claude Code）：
        - system prompt 保持完全稳定（不注入随测试进度变化的温记忆），
          使 OpenAI 兼容 API 的自动前缀缓存能在连续回合命中整个 system + 历史前缀。
        - 测试进度（温记忆）由 Agent 通过 get_test_coverage / get_strategy 工具按需获取，
          自主模式下由 run() 每 5 步以 user 消息追加（append-only，不破坏前缀）。

        Args:
            system_prompt: 基础系统提示词
            messages: 从 task.steps 恢复的原始消息列表（含 system 消息）
            pentest_context: 保留参数（温记忆不再注入 system，由工具按需读取）

        Returns:
            压缩后的消息列表，保持 OpenAI API 兼容格式
        """
        # 分离 system 消息和对话消息
        system_msgs = []
        conv_msgs = []
        for msg in messages:
            if msg.get("role") == "system":
                system_msgs.append(msg)
            else:
                conv_msgs.append(msg)

        # 保留 system 消息原样（可多条：稳定段 + 动态段），不合并——
        # 合并会破坏缓存友好的拆分结构（稳定段在前、动态段在后），
        # 使技能/记忆变化作废整个 system 前缀缓存。
        if not system_msgs:
            system_msgs = [{"role": "system", "content": system_prompt}]

        # 压缩对话消息（热记忆管理）
        compressed_conv = self._compress_conv_messages(conv_msgs)

        # 组装最终消息列表
        result = list(system_msgs)
        result.extend(compressed_conv)

        return result

    def compress_messages(
        self,
        messages: list[dict],
        pentest_context: Any | None = None,
    ) -> list[dict]:
        """压缩已有消息列表（用于对话循环中，防止 messages 无限增长）。

        与 assemble() 相同，保留所有 system 消息（稳定段 + 动态段）原样不动，
        只压缩对话部分。

        缓存友好：system 消息保持原样（不重注入温记忆），确保连续回合的
        请求前缀稳定，命中 OpenAI 兼容 API 的自动前缀缓存。

        Args:
            messages: 当前消息列表（含 system 消息）
            pentest_context: 保留参数（温记忆不再注入 system）

        Returns:
            压缩后的消息列表
        """
        if len(messages) <= 2:
            return messages

        # 分离 system 和对话消息（保留所有 system 消息：稳定段 + 动态段，不合并）
        system_msgs = [m for m in messages if m.get("role") == "system"]
        conv_msgs = [m for m in messages if m.get("role") != "system"]

        # 压缩对话消息
        compressed_conv = self._compress_conv_messages(conv_msgs)

        result = list(system_msgs)
        result.extend(compressed_conv)
        return result

    def _compress_conv_messages(self, conv_msgs: list[dict]) -> list[dict]:
        """压缩对话消息列表。

        策略：
        1. 从末尾向前扫描，累计 token 直到达到 HOT_BUDGET
        2. 窗口内的消息保留原文
        3. 窗口外的 tool 消息蒸馏为摘要
        4. 窗口外的 user/assistant 消息保留（通常较短）
        """
        if not conv_msgs:
            return []

        # 从末尾向前计算热记忆窗口
        hot_tokens = 0
        hot_start_idx = len(conv_msgs)

        for i in range(len(conv_msgs) - 1, -1, -1):
            msg_tokens = TokenEstimator.estimate_messages([conv_msgs[i]])
            if hot_tokens + msg_tokens > self.hot_budget and i < len(conv_msgs) - 1:
                hot_start_idx = i + 1
                break
            hot_tokens += msg_tokens
            hot_start_idx = i

        # 构建压缩后的消息列表
        result: list[dict] = []

        # 窗口外的消息：蒸馏工具输出，保留 user/assistant
        for i in range(hot_start_idx):
            msg = conv_msgs[i]

            if msg.get("role") == "tool":
                # 蒸馏工具输出
                tool_call_id = msg.get("tool_call_id", "")
                content = msg.get("content", "")

                # 尝试解析工具名和参数（需要从前面的 assistant 消息推断）
                distilled = self._distill_tool_message(content, conv_msgs, i)
                result.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": distilled,
                })

            elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                # 带工具调用的 assistant 消息：保留 tool_calls 结构（必须与 tool 消息配对）
                # 但可以去掉 content（中间思考通常不需要保留）
                result.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": msg["tool_calls"],
                })

            else:
                # user / 纯文本 assistant：保留原文
                result.append(msg)

        # 窗口内的消息：保留原文
        for i in range(hot_start_idx, len(conv_msgs)):
            result.append(conv_msgs[i])

        # 添加分隔标记（告诉 LLM 之前的工具输出已压缩）
        # 用 user 角色而非 system：system 消息必须在消息开头，中途插入会破坏缓存前缀与 OpenAI 消息规范
        if hot_start_idx > 0:
            # 找到第一个非 system 消息的位置
            for i, msg in enumerate(result):
                if msg.get("role") != "system":
                    result.insert(i, {
                        "role": "user",
                        "content": "[上下文管理] 之前的工具调用结果已压缩为摘要，完整数据保存在数据库中。如需查看某个工具的完整输出，请使用 get_flow_detail 或 read_file 工具。",
                    })
                    break

        return result

    def _distill_tool_message(
        self,
        content: str,
        conv_msgs: list[dict],
        msg_idx: int,
    ) -> str:
        """蒸馏单条 tool 消息的内容。

        通过查找前面的 assistant 消息中的 tool_calls 来获取工具名和参数。
        """
        # 解析 tool result
        try:
            tool_result = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            # 非 JSON 内容，直接截断
            if len(content) > 200:
                return content[:200] + "...[truncated]"
            return content

        # 查找对应的 tool_call（在前面最近的 assistant with tool_calls 消息中）
        tool_name = "unknown"
        tool_args: dict = {}

        tool_call_id = conv_msgs[msg_idx].get("tool_call_id", "")
        for j in range(msg_idx - 1, -1, -1):
            prev_msg = conv_msgs[j]
            if prev_msg.get("role") == "assistant" and prev_msg.get("tool_calls"):
                for tc in prev_msg["tool_calls"]:
                    if isinstance(tc, dict) and tc.get("id") == tool_call_id:
                        fn = tc.get("function", {})
                        tool_name = fn.get("name", "unknown")
                        try:
                            tool_args = json.loads(fn.get("arguments", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            tool_args = {}
                        break
                if tool_name != "unknown":
                    break

        # 检查是否需要蒸馏
        result_str = json.dumps(tool_result, ensure_ascii=False, default=str)
        if not ToolOutputDistiller.should_distill(tool_name, result_str):
            # 短结果保留原文
            return content

        # 蒸馏
        distilled = ToolOutputDistiller.distill(tool_name, tool_args, tool_result)
        return distilled

    def get_stats(self, messages: list[dict]) -> dict:
        """获取消息列表的统计信息（用于调试和日志）。"""
        total_tokens = TokenEstimator.estimate_messages(messages)

        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        tool_tokens = sum(
            TokenEstimator.estimate(m.get("content", ""))
            for m in tool_msgs
        )

        system_tokens = sum(
            TokenEstimator.estimate(m.get("content", ""))
            for m in messages
            if m.get("role") == "system"
        )

        return {
            "total_tokens": total_tokens,
            "total_messages": len(messages),
            "tool_messages": len(tool_msgs),
            "tool_tokens": tool_tokens,
            "system_tokens": system_tokens,
            "conversation_tokens": total_tokens - system_tokens,
            "hot_budget": self.hot_budget,
            "warm_budget": self.warm_budget,
        }
