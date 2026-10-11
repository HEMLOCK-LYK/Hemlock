"""FlowTag 正则匹配引擎。

参考 Burp Suite FlowTag 插件的匹配逻辑，对 HTTP 请求/响应进行正则匹配，
命中时返回标签名称和颜色，用于在流量列表中标注特殊数据包。

规则格式（JSON）：
{
  "name": "Shiro",
  "f_regex": "(=deleteMe|rememberMe=)",
  "s_regex": "",
  "format": "{0}",
  "color": "green",
  "scope": "any header",       // any / request / response / request header / request body / response header / response body / any header / any body
  "enabled": true,
  "sensitive": true
}
"""

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from hemlock.core.config import get_data_dir

logger = logging.getLogger(__name__)

# 规则文件路径
RULES_DIR = Path(__file__).parent.parent / "data"
DEFAULT_RULES_FILE = RULES_DIR / "flowtag_rules.json"
USER_RULES_FILE = get_data_dir() / "flowtag_rules.json"

# 颜色 → CSS 颜色映射
COLOR_MAP = {
    "red": "#f0636c",
    "orange": "#e59400",
    "yellow": "#d4b106",
    "green": "#6aab73",
    "cyan": "#17a2b8",
    "blue": "#4ea1e6",
    "pink": "#e83e8c",
    "magenta": "#b06acb",
    "gray": "#7f7f7f",
    "none": "",
}

# 颜色优先级（数字越小优先级越高，用于多规则命中时取最高优先级颜色）
COLOR_PRIORITY = {
    "red": 0, "orange": 1, "yellow": 2, "green": 3,
    "cyan": 4, "blue": 5, "pink": 6, "magenta": 7,
    "gray": 8, "none": 9,
}


class FlowTagRule:
    """单条 FlowTag 规则。

    severity / clue_type 为检测化扩展字段（向后兼容，旧规则文件缺省时为
    info / 空字符串）：severity 非空且非 info 的规则命中时会进入
    collect_tag_clues 的结构化线索视图，供 Agent 审查消费。
    """

    __slots__ = ("name", "f_regex", "s_regex", "format", "color", "scope", "enabled", "sensitive",
                 "severity", "clue_type", "_compiled")

    def __init__(self, name: str, f_regex: str, s_regex: str = "", format: str = "{0}",
                 color: str = "gray", scope: str = "any", enabled: bool = True,
                 sensitive: bool = True, severity: str = "info", clue_type: str = ""):
        self.name = name
        self.f_regex = f_regex
        self.s_regex = s_regex
        self.format = format
        self.color = color
        self.scope = scope
        self.enabled = enabled
        self.sensitive = sensitive
        self.severity = severity if severity in ("high", "medium", "low", "info") else "info"
        self.clue_type = clue_type
        self._compiled: Optional[re.Pattern] = None
        self._compile()

    def _compile(self):
        """预编译正则表达式。"""
        flags = 0 if self.sensitive else re.IGNORECASE
        try:
            self._compiled = re.compile(self.f_regex, flags)
        except re.error as e:
            logger.warning(f"FlowTag 规则 '{self.name}' 正则编译失败: {e}")
            self._compiled = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "f_regex": self.f_regex,
            "s_regex": self.s_regex,
            "format": self.format,
            "color": self.color,
            "scope": self.scope,
            "enabled": self.enabled,
            "sensitive": self.sensitive,
            "severity": self.severity,
            "clue_type": self.clue_type,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FlowTagRule":
        return cls(
            name=d.get("name", ""),
            f_regex=d.get("f_regex", ""),
            s_regex=d.get("s_regex", ""),
            format=d.get("format", "{0}"),
            color=d.get("color", "gray"),
            scope=d.get("scope", "any"),
            enabled=d.get("enabled", True),
            sensitive=d.get("sensitive", True),
            severity=d.get("severity", "info"),
            clue_type=d.get("clue_type", ""),
        )


class FlowTagEngine:
    """FlowTag 正则匹配引擎单例。"""

    def __init__(self):
        self._rules: list[FlowTagRule] = []
        self._lock = threading.Lock()
        self._loaded = False

    @property
    def rules(self) -> list[FlowTagRule]:
        return self._rules

    def load_rules(self):
        """加载规则：内置文件为常驻基底，用户文件按名覆盖，用户独有的规则追加尾部。

        内置规则是常驻检测集——新增内置规则（如登录态标注）自动到达所有用户，
        包括已保存过自定义规则的老用户；用户的定制（改正则/禁用）按名覆盖保留。
        想关掉某条内置规则用 enabled=false，直接删除会在下次加载时复活。
        """
        with self._lock:
            self._rules.clear()

            base: list[dict] = []
            if DEFAULT_RULES_FILE.exists():
                try:
                    base = [
                        item
                        for item in json.loads(DEFAULT_RULES_FILE.read_text(encoding="utf-8"))
                        if isinstance(item, dict) and item.get("name")
                    ]
                except Exception as e:
                    logger.debug(f"内置规则读取失败（忽略）: {e}")

            user_rules: list[dict] = []
            if USER_RULES_FILE.exists():
                try:
                    user_rules = [
                        item
                        for item in json.loads(USER_RULES_FILE.read_text(encoding="utf-8"))
                        if isinstance(item, dict) and item.get("name")
                    ]
                except Exception as e:
                    logger.debug(f"用户规则读取失败（忽略）: {e}")

            user_map = {item["name"]: item for item in user_rules}
            merged: list[dict] = []
            for item in base:
                user = user_map.get(item["name"])
                merged.append({**item, **user} if user is not None else item)
            base_names = {item["name"] for item in base}
            merged.extend(item for item in user_rules if item["name"] not in base_names)

            for item in merged:
                try:
                    self._rules.append(FlowTagRule.from_dict(item))
                except Exception as e:
                    logger.warning(f"FlowTag 规则加载失败: {item.get('name')} {e}")
            logger.info(f"FlowTag 引擎已加载 {len(self._rules)} 条规则（内置基底 {len(base)} 条）")
            self._loaded = True

    def save_rules(self, rules_data: list[dict]):
        """保存规则到用户规则文件。"""
        USER_RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        USER_RULES_FILE.write_text(
            json.dumps(rules_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        self.load_rules()

    def get_rules_dict(self) -> list[dict]:
        """返回当前规则的字典列表。"""
        if not self._loaded:
            self.load_rules()
        return [r.to_dict() for r in self._rules]

    def _select_content(self, scope: str, req_headers: str, req_body: str,
                        resp_headers: str, resp_body: str) -> str:
        """根据 scope 选择匹配内容。"""
        # 合并请求和响应
        full_request = f"{req_headers}\r\n\r\n{req_body}" if req_body else req_headers
        full_response = f"{resp_headers}\r\n\r\n{resp_body}" if resp_body else resp_headers

        scope_map = {
            "any": full_request + "\r\n" + full_response,
            "request": full_request,
            "response": full_response,
            "request header": req_headers,
            "request body": req_body,
            "response header": resp_headers,
            "response body": resp_body,
            "any header": req_headers + "\r\n" + resp_headers,
            "any body": req_body + "\r\n" + resp_body,
        }
        return scope_map.get(scope, full_request + "\r\n" + full_response)

    def match(self, req_headers: str, req_body: str,
              resp_headers: str, resp_body: str) -> tuple[str, str]:
        """对一条流量执行所有启用的规则匹配。

        Returns:
            (tags, tag_colors) — 逗号分隔的标签名和颜色
        """
        tags, colors, _clues = self.match_detailed(req_headers, req_body, resp_headers, resp_body)
        return tags, colors

    def match_detailed(self, req_headers: str, req_body: str,
                       resp_headers: str, resp_body: str) -> tuple[str, str, list[dict]]:
        """匹配并收集结构化线索（检测化）。

        在 match() 基础上，对 severity 非空且非 info 的规则命中额外生成
        线索 dict（rule_name/severity/clue_type/evidence/scope），
        供 collect_tag_clues / Agent 审查消费。标签行为与旧版完全一致。

        Returns:
            (tags, tag_colors, clues)
        """
        if not self._loaded:
            self.load_rules()

        tags = []
        colors = []
        clues = []
        best_color = "none"

        for rule in self._rules:
            if not rule.enabled or not rule._compiled:
                continue

            content = self._select_content(
                rule.scope, req_headers, req_body, resp_headers, resp_body
            )
            if not content:
                continue

            try:
                matches = rule._compiled.findall(content)
                if matches:
                    # 去重（保持出现顺序，首个命中作为线索证据片段）
                    unique = []
                    for m in matches:
                        if isinstance(m, tuple):
                            val = m[0] if m[0] else (m[1] if len(m) > 1 else "")
                        else:
                            val = m
                        if val and val not in unique:
                            unique.append(val)
                    if unique:
                        count = len(unique)
                        tag_name = f"{rule.name}" if count == 1 else f"{rule.name}({count})"
                        tags.append(tag_name)
                        colors.append(rule.color)
                        if rule.severity and rule.severity != "info":
                            clues.append({
                                "rule_name": rule.name,
                                "severity": rule.severity,
                                "clue_type": rule.clue_type or "flowtag",
                                "evidence": unique[0][:200],
                                "scope": rule.scope,
                            })
                        # 取最高优先级颜色
                        if COLOR_PRIORITY.get(rule.color, 9) < COLOR_PRIORITY.get(best_color, 9):
                            best_color = rule.color
            except Exception as e:
                logger.debug(f"FlowTag 规则 '{rule.name}' 匹配异常: {e}")

        tags_str = ",".join(tags) if tags else ""
        colors_str = ",".join(colors) if colors else ""
        return tags_str, colors_str, clues

    def get_detection_rules(self) -> dict[str, FlowTagRule]:
        """返回带检测等级（severity 非 info）的规则映射（按规则名索引）。"""
        if not self._loaded:
            self.load_rules()
        return {r.name: r for r in self._rules if r.severity and r.severity != "info"}


# 全局单例
flowtag_engine = FlowTagEngine()


# severity 过滤排序（数字越小等级越高）
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def collect_tag_clues(
    db,
    project_id: str,
    min_severity: str = "low",
    host: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """从 tags 非空的流量实时推导结构化检测线索（FlowTag 检测化）。

    不落独立表：标签命中时 Flow.tags 已持久化，此处按规则的
    severity/clue_type 元数据把标签升级为线索视图（含证据片段），
    供 Agent 审查消费。天然覆盖历史流量，且无需改动任何写入链路。

    Args:
        db: 数据库会话
        project_id: 项目 ID（必填，项目隔离）
        min_severity: 最低等级过滤（high / medium / low）
        host: 可选，按 host 模糊过滤
        limit: 返回线索条数上限

    Returns:
        线索列表，按等级排序（high 优先），每条含:
        flow_id / method / url / host / rule_name / severity /
        clue_type / evidence / scope / timestamp
    """
    from hemlock.models.flow import Flow
    from sqlalchemy import desc

    if min_severity not in _SEVERITY_ORDER:
        min_severity = "low"
    min_order = _SEVERITY_ORDER[min_severity]

    detection_rules = flowtag_engine.get_detection_rules()
    if not detection_rules:
        return []

    # 只扫 tags 非空的流量；扫描上限保底（超大项目不无限扫）
    q = (
        db.query(Flow)
        .filter(Flow.project_id == project_id)
        .filter(Flow.tags != None, Flow.tags != "")  # noqa: E711,E712
    )
    if host:
        q = q.filter(Flow.host.contains(host))
    flows = q.order_by(desc(Flow.timestamp)).limit(2000).all()

    clues: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()  # (flow_id, rule_name) 去重

    for flow in flows:
        for raw_tag in (flow.tags or "").split(","):
            raw_tag = raw_tag.strip()
            if not raw_tag:
                continue
            # 标签名可能带命中计数后缀 "Shiro(2)"，还原基名
            base_name = raw_tag.rsplit("(", 1)[0].strip()
            rule = detection_rules.get(base_name)
            if not rule:
                continue
            if _SEVERITY_ORDER.get(rule.severity, 3) > min_order:
                continue

            key = (flow.id, base_name)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # 证据片段：对该流量跑一次对应规则取首个命中（截断 200 字符）
            evidence = ""
            try:
                if rule._compiled:
                    content = flowtag_engine._select_content(
                        rule.scope, flow.req_headers or "", flow.req_body or "",
                        flow.resp_headers or "", flow.resp_body or "",
                    )
                    m = rule._compiled.search(content) if content else None
                    if m:
                        evidence = m.group(0)[:200]
            except Exception:
                evidence = ""

            clues.append({
                "flow_id": flow.id,
                "method": flow.method,
                "url": flow.url,
                "host": flow.host,
                "rule_name": base_name,
                "severity": rule.severity,
                "clue_type": rule.clue_type or "flowtag",
                "evidence": evidence,
                "scope": rule.scope,
                "timestamp": flow.timestamp.isoformat() if flow.timestamp else "",
            })
            if len(clues) >= limit:
                clues.sort(key=lambda c: _SEVERITY_ORDER.get(c["severity"], 3))
                return clues

    clues.sort(key=lambda c: _SEVERITY_ORDER.get(c["severity"], 3))
    return clues


def extract_url_fields(url: str) -> tuple[str, str, str, str]:
    """从 URL 提取 host, scheme, path, extension。

    Returns:
        (host, scheme, path, extension)
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        scheme = parsed.scheme or ""
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        # 提取扩展名
        ext = ""
        path_only = parsed.path or ""
        if "." in path_only:
            ext = path_only.rsplit(".", 1)[-1].lower()
            # 限制扩展名长度，避免误判
            if len(ext) > 10 or "/" in ext:
                ext = ""
        return host, scheme, path, ext
    except Exception:
        return "", "", "", ""


def extract_mime_type(resp_headers: str) -> str:
    """从响应头提取 MIME 类型（Content-Type 去除参数）。"""
    if not resp_headers:
        return ""
    for line in resp_headers.split("\n"):
        if line.lower().startswith("content-type:"):
            value = line.split(":", 1)[1].strip()
            # 去除 ;charset=xxx 等参数
            return value.split(";")[0].strip().lower()
    return ""


def extract_title(resp_body: str, mime_type: str = "") -> str:
    """从 HTML 响应体提取 <title>。"""
    if not resp_body:
        return ""
    # 仅对 HTML 内容提取
    if mime_type and "html" not in mime_type and "xml" not in mime_type:
        # 但也检查内容是否像 HTML
        if not resp_body.lstrip().startswith("<"):
            return ""
    # 限制搜索范围以提高性能
    search_text = resp_body[:50000] if len(resp_body) > 50000 else resp_body
    match = re.search(r"<title[^>]*>(.*?)</title>", search_text, re.IGNORECASE | re.DOTALL)
    if match:
        title = match.group(1).strip()
        # 清理 HTML 实体和多余空白
        title = re.sub(r"\s+", " ", title)
        return title[:200] if len(title) > 200 else title
    return ""


def get_resp_length(resp_body: str) -> int:
    """获取响应体字节长度。"""
    if not resp_body:
        return 0
    try:
        return len(resp_body.encode("utf-8"))
    except Exception:
        return len(resp_body)


def enrich_flow_fields(
    url: str,
    req_headers: str,
    req_body: str,
    resp_headers: str,
    resp_body: str,
    resp_status: int | None = None,
) -> dict:
    """提取流量的所有元数据字段，返回 kwargs dict 供 Flow() 构造使用。

    统一入口：Repeater / Intruder / Retest / Agent 创建 Flow 时调用此函数，
    确保 host/scheme/path/mime_type/resp_length/tags 等字段全部填充，
    与代理捕获的流量保持一致。

    Returns:
        dict with keys: host, scheme, path, resp_length, mime_type,
                        extension, title, tags, tag_colors, is_noise, noise_reason
    """
    from hemlock.services.flow_filter import check_noise

    host, scheme, path, extension = extract_url_fields(url)
    mime_type = extract_mime_type(resp_headers)
    title = extract_title(resp_body, mime_type)
    resp_length = get_resp_length(resp_body) if resp_body else 0
    tags, tag_colors = flowtag_engine.match(req_headers, req_body, resp_headers, resp_body)
    is_noise, noise_reason = check_noise(url, "", resp_status, resp_body)

    return {
        "host": host,
        "scheme": scheme,
        "path": path,
        "extension": extension,
        "mime_type": mime_type,
        "title": title,
        "resp_length": resp_length,
        "tags": tags,
        "tag_colors": tag_colors,
        "is_noise": is_noise,
        "noise_reason": noise_reason,
    }
