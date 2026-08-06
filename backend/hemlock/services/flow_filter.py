"""流量过滤器：自动检测和过滤心跳包/杂包。

设计思路（多因子混合方案 + 主机级分析）：
1. 自动检测层：在 capture addon 中实时分析流量特征，
   判断是否为心跳包/杂包，打上 is_noise 标记但不拦截。
2. 用户管理层：用户可一键开关过滤、查看黑名单、手动添加/移除。

多因子检测算法（解决多种杂包场景）：

  场景1：访问页面触发 1 关键包 + 5 不同杂包（JS/CSS/追踪/统计）
    → 静态规则捕获 JS/CSS/ico
    → 追踪域名规则捕获 google-analytics/hotjar/百度统计等
    → 主机级频率分析捕获未知追踪服务（同一host多个不同URL）
    → 突发保护确保关键包不被误杀

  场景2：服务器定期向同一URL发送心跳包
    → 响应指纹：相同响应重复 3+ 次 → 杂包
    → 频率追踪：60秒窗口内 >= 20 次 → 杂包
    → 响应特征：204/304 或 < 50B → 杂包

  场景3：追踪服务用多个不同URL向同一host发请求
    → 每个URL只出现1-2次，per-URL频率无法捕获
    → 主机级频率：同一host 60秒内 >= 30次请求 → 杂包
    → "抽样排序"策略：定期分析流量样本，按host请求数排序

检测因子优先级（短路评估）：
  1. 静态规则：OPTIONS / 心跳URL / 静态资源 / 追踪域名 → 立即判定
  2. URL级多因子：突发保护 + 响应指纹 + 响应特征 + 频率
  3. 主机级频率：同一host请求过多 → 追踪服务判定

性能设计：
- 纯内存操作，O(1) 平均时间复杂度
- MD5 仅取响应体前 500 字节计算
- 每 120 秒清理过期数据，最多追踪 5000 个 URL + 500 个 host
- deque 滑动窗口自动淘汰旧时间戳
"""

import hashlib
import re
import time
from collections import defaultdict, deque, Counter
from urllib.parse import urlparse

# ===== 静态规则：已知的心跳包/杂包 URL 模式 =====

# 心跳/健康检查关键词（URL path 中出现即判定）
HEARTBEAT_PATTERNS = re.compile(
    r"/(heartbeat|keepalive|keep-alive|ping|pong|health|healthz|health-check|"
    r"alive|heartbeat\.js|_ping|metrics|sse|eventsource)",
    re.IGNORECASE,
)

# 静态资源后缀（高频但无安全价值的流量）
STATIC_ASSET_PATTERN = re.compile(
    r"\.(js|css|png|jpe?g|gif|svg|ico|woff2?|ttf|eot|map|webp|avif)(\?|$)",
    re.IGNORECASE,
)

# 杂包路径
NOISE_PATHS = re.compile(
    r"(/favicon\.ico|/robots\.txt|/sitemap\.xml|/manifest\.json|"
    r"/sw\.js|/service-worker|/workbox|/.well-known/)",
    re.IGNORECASE,
)

# 已知追踪/统计服务域名（命中即判定为杂包）
TRACKING_DOMAIN_PATTERNS = re.compile(
    r"(google-analytics\.com|googletagmanager\.com|doubleclick\.net|"
    r"adservice\.google\.|adsystem\.|"
    r"facebook\.net|connect\.facebook\.net|pixel\.facebook\.com|"
    r"hotjar\.com|clarity\.ms|segment\.io|mixpanel\.com|"
    r"amplitude\.com|fullstory\.com|sentry\.io|"
    r"bat\.bing\.com|hm\.baidu\.com|"
    r"cnzz\.com|umeng\.co|umeng\.com|"
    r"plausible\.io|matomo\.|"
    r"sentry-cdn\.com|newrelic\.com|nr-data\.net|"
    r"cloudflareinsights\.com|cdn\.cookielaw\.org|"
    r"quantserve\.com|scorecardresearch\.com|"
    r"optimizely\.com|crazyegg\.com|kissmetrics\.com|"
    r"intercom\.io|zendesk\.com|launchdarkly\.com|"
    r"unpkg\.com|cdnjs\.cloudflare\.com|jsdelivr\.net)",
    re.IGNORECASE,
)

# OPTIONS 预检请求通常无安全价值
OPTIONS_METHOD = "OPTIONS"

# ===== URL级多因子参数 =====

# 突发保护：前 N 次请求永不标记为杂包
BURST_PROTECTION_COUNT = 3

# 频率追踪窗口（秒）
FREQUENCY_WINDOW = 60

# 突发保护后的持续频率阈值：窗口内总请求 >= 此值 → 高频
# 设为 20：正常 API 调用极少在 60 秒内命中同一 URL 20 次，
# 只有轮询/心跳类场景才会达到此频率。
FREQUENCY_THRESHOLD = 20

# 响应指纹：同一 URL 相同响应体（MD5 前500字节）出现 >= 此值 → 轮询杂包
# 工作原理：对每个 URL 的响应体前 500 字节计算 MD5，
# 在 fingerprints dict 中计数。如果某个指纹出现 >= 3 次，
# 说明服务器对同一请求反复返回相同响应 → 心跳/轮询行为。
FINGERPRINT_THRESHOLD = 3

# 每个URL最多追踪的响应指纹数量
MAX_FINGERPRINTS_PER_KEY = 5

# 微小响应阈值（字节）
TINY_RESPONSE_SIZE = 50

# 无内容响应状态码
NO_CONTENT_STATUS = {204, 304}

# ===== 主机级频率参数 =====

# 主机级频率窗口（秒）
HOST_WINDOW = 60

# 主机级突发保护：前 N 次请求到同一host永不杂包
HOST_BURST_PROTECTION = 5

# 主机级频率阈值：60秒内同一host >= 此值 → 追踪服务杂包
# 设为 30：正常浏览一个网站通常不会在 60 秒内对同一 host
# 发起 30 次请求，只有追踪/统计服务（多个不同 URL 打同一 host）才会达到。
HOST_FREQUENCY_THRESHOLD = 30

# 主机级最多追踪的host数量
MAX_TRACKED_HOSTS = 500

# ===== 通用参数 =====

CLEANUP_INTERVAL = 120
MAX_TRACKED_KEYS = 5000


def _extract_url_key(url: str) -> str:
    """提取 URL 的唯一标识：host + path（不含 query 参数）。"""
    try:
        parsed = urlparse(url)
        return f"{parsed.netloc}{parsed.path}"
    except Exception:
        return url


def _extract_host(url: str) -> str:
    """提取 URL 的 host 部分。"""
    try:
        return urlparse(url).netloc
    except Exception:
        return ""


def is_static_noise(url: str, method: str) -> bool:
    """静态规则判断：是否为心跳包/杂包（不依赖历史数据）。"""
    if method.upper() == OPTIONS_METHOD:
        return True
    if HEARTBEAT_PATTERNS.search(url):
        return True
    if STATIC_ASSET_PATTERN.search(url):
        return True
    if NOISE_PATHS.search(url):
        return True
    if TRACKING_DOMAIN_PATTERNS.search(url):
        return True
    return False


def _fingerprint(resp_body: str) -> str:
    """计算响应体指纹：MD5(前500字节)。"""
    return hashlib.md5(resp_body[:500].encode("utf-8", errors="ignore")).hexdigest()


class _UrlEntry:
    """每个 URL 的追踪数据。"""

    __slots__ = ("timestamps", "fingerprints")

    def __init__(self):
        self.timestamps: deque = deque()
        self.fingerprints: dict[str, int] = defaultdict(int)


class _HostEntry:
    """每个 host 的追踪数据。"""

    __slots__ = ("timestamps", "url_keys")

    def __init__(self):
        self.timestamps: deque = deque()
        # 记录该host下的不同URL key，用于区分API(少路径多请求) vs 追踪(多路径)
        self.url_keys: set[str] = set()


class NoiseTracker:
    """多因子杂包追踪器。

    三层检测：
    1. URL级：突发保护 + 响应指纹 + 响应特征 + 频率
    2. 主机级：同一host请求过多 → 追踪服务
    3. 抽样排序：定期分析流量样本，识别高频host

    性能：所有操作 O(1) 均摊，不影响抓包性能。
    """

    def __init__(self):
        # URL级追踪
        self._url_data: dict[str, _UrlEntry] = defaultdict(_UrlEntry)
        self._url_flagged: dict[str, str] = {}  # key -> reason

        # 主机级追踪
        self._host_data: dict[str, _HostEntry] = defaultdict(_HostEntry)
        self._host_flagged: dict[str, str] = {}  # host -> reason

        self._last_cleanup = time.monotonic()

    def check(
        self,
        url: str,
        url_key: str,
        host: str,
        resp_status: int | None = None,
        resp_body: str = "",
    ) -> tuple[bool, str]:
        """检查请求是否为杂包，返回 (is_noise, reason)。

        三层检测：
        1. URL级多因子（突发保护 + 指纹 + 特征 + 频率）
        2. 主机级频率（同一host请求过多 → 追踪服务）
        """
        now = time.monotonic()

        # 定期清理
        if now - self._last_cleanup > CLEANUP_INTERVAL:
            self._cleanup(now)
            self._last_cleanup = now

        # === 第一层：URL级检测 ===
        is_noise, reason = self._check_url(url_key, resp_status, resp_body, now)
        if is_noise:
            # URL级已判定，但仍需记录主机级数据
            self._record_host(host, url_key, now)
            return True, reason

        # === 第二层：主机级检测 ===
        # 如果URL级未判定，检查是否为高频追踪host
        is_noise, reason = self._check_host(host, url_key, now)
        return is_noise, reason

    def _check_url(
        self,
        url_key: str,
        resp_status: int | None,
        resp_body: str,
        now: float,
    ) -> tuple[bool, str]:
        """URL级多因子检测。"""

        # 已标记的 URL，直接返回
        if url_key in self._url_flagged:
            entry = self._url_data[url_key]
            self._trim_url_window(entry, now)
            entry.timestamps.append(now)
            return True, self._url_flagged[url_key]

        entry = self._url_data[url_key]
        self._trim_url_window(entry, now)
        entry.timestamps.append(now)
        count = len(entry.timestamps)

        # 因子1：突发保护 — 前 N 次请求永不杂包
        if count <= BURST_PROTECTION_COUNT:
            if resp_body:
                fp = _fingerprint(resp_body)
                entry.fingerprints[fp] += 1
                self._trim_fingerprints(entry)
            return False, ""

        # === 突发保护期过后 ===

        # 因子2：无内容响应状态码
        if resp_status and resp_status in NO_CONTENT_STATUS:
            reason = f"无内容响应({resp_status})"
            self._url_flagged[url_key] = reason
            return True, reason

        # 因子3：响应指纹 — 相同响应重复
        if resp_body:
            fp = _fingerprint(resp_body)
            entry.fingerprints[fp] += 1
            self._trim_fingerprints(entry)

            max_fp = max(entry.fingerprints.values()) if entry.fingerprints else 0
            if max_fp >= FINGERPRINT_THRESHOLD:
                reason = "相同响应重复"
                self._url_flagged[url_key] = reason
                return True, reason

        # 因子4：微小响应
        if resp_body is not None and 0 < len(resp_body) < TINY_RESPONSE_SIZE:
            reason = "微小响应"
            self._url_flagged[url_key] = reason
            return True, reason

        # 因子5：持续高频
        if count >= FREQUENCY_THRESHOLD:
            reason = "高频重复"
            self._url_flagged[url_key] = reason
            return True, reason

        return False, ""

    def _check_host(
        self,
        host: str,
        url_key: str,
        now: float,
    ) -> tuple[bool, str]:
        """主机级频率检测：捕获追踪服务（同host多不同URL）。

        策略：
        - 前 HOST_BURST_PROTECTION 次请求到同一host → 永不杂包
        - 60秒窗口内同一host请求 >= HOST_FREQUENCY_THRESHOLD(30) → 杂包
        - 已标记的host → 直接返回杂包
        """
        if not host:
            return False, ""

        # 已标记的 host
        if host in self._host_flagged:
            entry = self._host_data[host]
            self._trim_host_window(entry, now)
            entry.timestamps.append(now)
            entry.url_keys.add(url_key)
            return True, self._host_flagged[host]

        entry = self._host_data[host]
        self._trim_host_window(entry, now)
        entry.timestamps.append(now)
        entry.url_keys.add(url_key)

        count = len(entry.timestamps)

        # 突发保护
        if count <= HOST_BURST_PROTECTION:
            return False, ""

        # 主机级高频
        if count >= HOST_FREQUENCY_THRESHOLD:
            reason = "主机高频(追踪服务)"
            self._host_flagged[host] = reason
            return True, reason

        return False, ""

    def _record_host(self, host: str, url_key: str, now: float) -> None:
        """记录主机级数据（即使URL级已判定也要记录）。"""
        if not host:
            return
        entry = self._host_data[host]
        self._trim_host_window(entry, now)
        entry.timestamps.append(now)
        entry.url_keys.add(url_key)

    def _trim_url_window(self, entry: _UrlEntry, now: float) -> None:
        cutoff = now - FREQUENCY_WINDOW
        while entry.timestamps and entry.timestamps[0] < cutoff:
            entry.timestamps.popleft()

    def _trim_host_window(self, entry: _HostEntry, now: float) -> None:
        cutoff = now - HOST_WINDOW
        while entry.timestamps and entry.timestamps[0] < cutoff:
            entry.timestamps.popleft()

    def _trim_fingerprints(self, entry: _UrlEntry) -> None:
        if len(entry.fingerprints) > MAX_FINGERPRINTS_PER_KEY:
            sorted_fps = sorted(entry.fingerprints.items(), key=lambda x: -x[1])
            entry.fingerprints = dict(sorted_fps[:MAX_FINGERPRINTS_PER_KEY])

    def _cleanup(self, now: float) -> None:
        """清理过期数据。"""
        # URL级清理
        url_cutoff = now - FREQUENCY_WINDOW
        expired_urls = []
        for key, entry in self._url_data.items():
            self._trim_url_window(entry, now)
            if not entry.timestamps:
                expired_urls.append(key)
        for key in expired_urls:
            del self._url_data[key]
            self._url_flagged.pop(key, None)

        if len(self._url_data) > MAX_TRACKED_KEYS:
            sorted_keys = sorted(
                self._url_data.keys(),
                key=lambda k: self._url_data[k].timestamps[0] if self._url_data[k].timestamps else 0,
            )
            for key in sorted_keys[: len(self._url_data) - MAX_TRACKED_KEYS]:
                del self._url_data[key]
                self._url_flagged.pop(key, None)

        # 主机级清理
        host_cutoff = now - HOST_WINDOW
        expired_hosts = []
        for host, entry in self._host_data.items():
            self._trim_host_window(entry, now)
            if not entry.timestamps:
                expired_hosts.append(host)
        for host in expired_hosts:
            del self._host_data[host]
            self._host_flagged.pop(host, None)

        if len(self._host_data) > MAX_TRACKED_HOSTS:
            sorted_hosts = sorted(
                self._host_data.keys(),
                key=lambda k: self._host_data[k].timestamps[0] if self._host_data[k].timestamps else 0,
            )
            for host in sorted_hosts[: len(self._host_data) - MAX_TRACKED_HOSTS]:
                del self._host_data[host]
                self._host_flagged.pop(host, None)

    def sample_and_sort(self, limit: int = 20) -> list[dict]:
        """抽样排序分析：返回当前窗口内请求最多的 URL 和 host。

        实现"抽样一部分数据包来检测，然后数量排序"策略。
        用户可查看此列表，手动将高频项加入黑名单。

        Returns:
            [{"type": "url"|"host", "key": str, "count": int, "is_flagged": bool}, ...]
        """
        now = time.monotonic()
        results = []

        # URL级排序
        for key, entry in self._url_data.items():
            self._trim_url_window(entry, now)
            count = len(entry.timestamps)
            if count > 1:
                results.append({
                    "type": "url",
                    "key": key,
                    "count": count,
                    "is_flagged": key in self._url_flagged,
                })

        # 主机级排序
        for host, entry in self._host_data.items():
            self._trim_host_window(entry, now)
            count = len(entry.timestamps)
            if count > 1:
                results.append({
                    "type": "host",
                    "key": host,
                    "count": count,
                    "is_flagged": host in self._host_flagged,
                    "url_variety": len(entry.url_keys),
                })

        # 按请求数降序排序
        results.sort(key=lambda x: x["count"], reverse=True)
        return results[:limit]

    def get_flagged(self) -> dict:
        """返回所有已标记的杂包 URL 和 host。"""
        return {
            "urls": [{"key": k, "reason": v} for k, v in self._url_flagged.items()],
            "hosts": [{"host": k, "reason": v} for k, v in self._host_flagged.items()],
        }

    def reset(self) -> None:
        """重置所有数据。"""
        self._url_data.clear()
        self._url_flagged.clear()
        self._host_data.clear()
        self._host_flagged.clear()


# 全局追踪器单例
_tracker = NoiseTracker()


def check_noise(
    url: str,
    method: str,
    resp_status: int | None = None,
    resp_body: str = "",
) -> tuple[bool, str]:
    """判断流量是否为杂包，返回 (is_noise, reason)。

    检测顺序（短路评估，命中即返回）：
    1. 静态规则：OPTIONS / 心跳URL / 静态资源 / 追踪域名 / 杂包路径 → 直接判定
    2. URL级多因子：突发保护 + 响应指纹 + 响应特征 + 频率
    3. 主机级频率：同一host请求过多 → 追踪服务

    Args:
        url: 完整 URL
        method: HTTP 方法
        resp_status: 响应状态码（None 表示无响应）
        resp_body: 响应体文本（空字符串表示无响应体）

    Returns:
        (is_noise, reason): 是否杂包 + 原因
    """
    # 1. 静态规则（立即判定，不依赖历史数据）
    if method.upper() == OPTIONS_METHOD:
        return True, "OPTIONS 预检请求"
    if HEARTBEAT_PATTERNS.search(url):
        return True, "心跳/健康检查"
    if STATIC_ASSET_PATTERN.search(url):
        return True, "静态资源"
    if TRACKING_DOMAIN_PATTERNS.search(url):
        return True, "追踪/统计服务"
    if NOISE_PATHS.search(url):
        return True, "杂包路径"

    # 2+3. 多因子追踪（URL级 + 主机级）
    url_key = _extract_url_key(url)
    host = _extract_host(url)
    return _tracker.check(url, url_key, host, resp_status, resp_body)


def get_noise_analysis(limit: int = 20) -> list[dict]:
    """获取抽样排序分析结果。

    实现"抽取一部分数据包来检测，然后数量排序"策略。
    返回当前窗口内请求最多的 URL 和 host，供用户查看和管理。
    """
    return _tracker.sample_and_sort(limit)


def get_flagged_targets() -> dict:
    """获取所有已标记的杂包目标。"""
    return _tracker.get_flagged()
