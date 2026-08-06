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
    """单条 FlowTag 规则。"""

    __slots__ = ("name", "f_regex", "s_regex", "format", "color", "scope", "enabled", "sensitive", "_compiled")

    def __init__(self, name: str, f_regex: str, s_regex: str = "", format: str = "{0}",
                 color: str = "gray", scope: str = "any", enabled: bool = True,
                 sensitive: bool = True):
        self.name = name
        self.f_regex = f_regex
        self.s_regex = s_regex
        self.format = format
        self.color = color
        self.scope = scope
        self.enabled = enabled
        self.sensitive = sensitive
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
        """加载规则文件：优先用户规则，否则默认规则。"""
        with self._lock:
            self._rules.clear()
            rules_file = USER_RULES_FILE if USER_RULES_FILE.exists() else DEFAULT_RULES_FILE
            if not rules_file.exists():
                logger.warning(f"FlowTag 规则文件不存在: {rules_file}")
                self._loaded = True
                return

            try:
                data = json.loads(rules_file.read_text(encoding="utf-8"))
                for item in data:
                    try:
                        rule = FlowTagRule.from_dict(item)
                        self._rules.append(rule)
                    except Exception as e:
                        logger.warning(f"FlowTag 规则加载失败: {e}")
                logger.info(f"FlowTag 引擎已加载 {len(self._rules)} 条规则")
            except Exception as e:
                logger.error(f"FlowTag 规则文件解析失败: {e}")
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
        if not self._loaded:
            self.load_rules()

        tags = []
        colors = []
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
                    # 去重
                    unique = set()
                    for m in matches:
                        if isinstance(m, tuple):
                            unique.add(m[0] if m[0] else (m[1] if len(m) > 1 else ""))
                        else:
                            unique.add(m)
                    unique.discard("")
                    if unique:
                        count = len(unique)
                        tag_name = f"{rule.name}" if count == 1 else f"{rule.name}({count})"
                        tags.append(tag_name)
                        colors.append(rule.color)
                        # 取最高优先级颜色
                        if COLOR_PRIORITY.get(rule.color, 9) < COLOR_PRIORITY.get(best_color, 9):
                            best_color = rule.color
            except Exception as e:
                logger.debug(f"FlowTag 规则 '{rule.name}' 匹配异常: {e}")

        tags_str = ",".join(tags) if tags else ""
        colors_str = ",".join(colors) if colors else ""
        return tags_str, colors_str


# 全局单例
flowtag_engine = FlowTagEngine()


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
