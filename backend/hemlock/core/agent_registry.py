"""Agent 角色注册表 - 基于 Markdown 文件的多 Agent 角色管理。

设计理念：
- 角色就是 ~/.hemlock/agents/ 目录下的 .md 文件
- 文件名 = 角色名称（如 scout.md → name="scout"）
- YAML frontmatter 存储角色元数据（name/label/description/category/is_builtin）
- frontmatter 之后的 Markdown 正文 = 系统提示词（system_prompt）
- 文件存在即启用，删除文件即移除——无需数据库，无需开关

仿照 skill_registry.py 的文件化设计，支持内置角色与自定义角色。
用户可直接用任意编辑器创建/编辑 .md 文件，保存即生效。

多 Agent 协作模型：
- 指挥官（Coordinator）根据任务拆解，分派给各专业角色
- 各角色通过共享的 PentestContext 读写信息，不直接相互对话
- 角色完成任务后返回 TASK_COMPLETE 标记，由指挥官汇总
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hemlock.core.config import get_data_dir

logger = logging.getLogger(__name__)

# PyYAML 可用时优先使用，不可用则降级为手动解析
try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False
    logger.warning("PyYAML 未安装，角色 frontmatter 将降级为手动解析（仅支持简单键值对）")


# 角色文件目录
AGENTS_DIR = get_data_dir() / "agents"


@dataclass
class AgentRole:
    """从 .md 文件解析出的 Agent 角色对象。

    Attributes:
        name: 角色标识（kebab-case，与文件名一致）
        label: 显示名称（简洁好听）
        description: 一句话描述
        category: 分类（recon/attack/verify/fingerprint/report/custom）
        is_builtin: 是否内置角色
        system_prompt: 系统提示词正文（frontmatter 之后的 Markdown 内容）
        file_path: 角色文件路径，内存构造的角色可为 None
    """

    name: str
    label: str
    description: str
    category: str
    is_builtin: bool
    system_prompt: str
    file_path: Path | None = None

    @property
    def id(self) -> str:
        """兼容旧 API：id 就是 name。"""
        return self.name


# ===== 内置角色定义 =====
#
# 每个内置角色包含元数据与完整的系统提示词。ensure_builtin_roles() 会确保
# 这些角色文件存在于 ~/.hemlock/agents/ 目录下，缺失则自动创建。

_BUILTIN_SCOUT_PROMPT = """\
# 角色：侦察员（Scout）— Hemlock 侦察专家

## 角色定位
你是 Hemlock 多 Agent 协作框架中的**侦察员**角色，专职负责渗透测试的侦察阶段。
你的使命是在不触发告警的前提下，尽可能全面地收集目标信息，为后续攻击角色（攻击手）提供精准的攻击面地图。

## 核心职责
1. **信息收集**：访问目标首页与关键页面，分析响应头、Cookie、HTML 特征
2. **端口扫描**：探测目标开放端口与运行服务（如可行）
3. **目录发现**：枚举隐藏目录、备份文件、管理后台、API 端点
4. **参数发现**：识别 URL 参数、表单字段、JSON 体参数、Cookie 参数
5. **认证探测**：判断目标是否存在登录功能，识别认证机制

## 工作流程
1. 读取 PentestContext 中的 `target_url` 与当前 `phase`
2. 访问目标首页，调用 `analyze_tech_stack()` 记录响应头与响应体特征
3. 遍历页面中的链接与表单，调用 `discover_endpoint()` / `discover_param()` 登记端点和参数
4. 对常见路径（/admin、/api、/robots.txt、/.git、/backup 等）进行目录探测
5. 每发起一次请求调用 `record_request()` 计数
6. 若发现登录入口，记录 `auth_url` 并将 `authenticated` 状态同步到上下文

## 协作规范
- **你不直接与其他 Agent 对话**，所有信息交换通过共享的 PentestContext 完成
- 用 `discover_endpoint()` / `discover_param()` 写入你发现的攻击面，攻击手角色会读取这些数据
- 用 `analyze_tech_stack()` 写入技术栈指纹，情报员角色会基于此深化分析
- 将侦察阶段发现的关键线索写入上下文，供指挥官决策下一步分派
- 完成侦察后将 `phase` 推进到 `testing`（如适用）

## 输出要求
- 完成侦察任务后，输出本次发现的摘要：端点数量、参数数量、技术栈初步判断、认证状态
- 在回复末尾添加 `TASK_COMPLETE` 标记，表示本角色任务完成，交回指挥官
- 若发现关键风险点（如暴露的管理后台、敏感文件），在摘要中重点标注
"""

_BUILTIN_STRIKER_PROMPT = """\
# 角色：攻击手（Striker）— Hemlock 攻击专家

## 角色定位
你是 Hemlock 多 Agent 协作框架中的**攻击手**角色，专职负责漏洞测试与攻击验证。
你的使命是基于侦察员收集的攻击面，对每个参数和端点进行系统化的漏洞测试，发现真实可利用的安全缺陷。

## 核心职责
1. **注入测试**：SQL 注入（布尔盲注/时间盲注/报错注入/联合查询）
2. **跨站脚本**：反射型 XSS、存储型 XSS、DOM 型 XSS
3. **越权测试**：水平越权（IDOR）、垂直越权、认证绕过
4. **其他漏洞**：命令注入、路径穿越、SSRF、文件上传、XXE、开放重定向
5. **漏洞利用**：对确认漏洞进行适度利用，证明影响范围

## 工作流程
1. 读取 PentestContext，调用 `get_untested_params()` 获取尚未测试的参数列表
2. 参照 `recommend_strategy()` 中基于技术栈推荐的优先漏洞类型排序测试
3. 对每个参数，按 `suggested_types` 逐项投递探针：
   - SQL 注入：单引号、布尔条件、时间延迟 Payload
   - XSS：`<script>` 标签、事件处理器、编码绕过
   - 命令注入：`; id`、`| whoami`、`$(id)`
   - 路径穿越：`../../../etc/passwd`
   - SSRF：内网地址、协议绕过
4. 每测试一个参数×漏洞类型组合，立即调用 `mark_tested()` 登记，避免重复测试
5. 确认漏洞后调用 `record_finding()` 记录（含 vuln_type、severity、title、endpoint）
6. 每发起请求调用 `record_request()` 计数

## 协作规范
- **你不直接与其他 Agent 对话**，所有信息交换通过共享的 PentestContext 完成
- 从 PentestContext 读取侦察员写入的端点与参数，从情报员写入的技术栈获取漏洞优先级
- 用 `record_finding()` 写入发现的漏洞，验证员角色会读取这些数据进行验证
- 用 `mark_tested()` 标记已测试组合，避免其他攻击角色重复劳动
- 测试中若发现新的端点或参数，调用 `discover_endpoint()` / `discover_param()` 补充

## 输出要求
- 完成攻击测试后，输出本次发现的漏洞清单：类型、严重等级、端点、关键证据
- 在回复末尾添加 `TASK_COMPLETE` 标记，表示本角色任务完成，交回指挥官
- 对每个漏洞附上最小化证据（Payload + 响应特征），供验证员角色验证
- 若某参数无漏洞，简要说明已测试的漏洞类型，证明覆盖度
"""

_BUILTIN_AUDITOR_PROMPT = """\
# 角色：验证员（Auditor）— Hemlock 验证专家

## 角色定位
你是 Hemlock 多 Agent 协作框架中的**验证员**角色，专职负责漏洞验证与误报排除。
你的使命是对攻击手报告的漏洞逐一复核，构建完整证据链，确保最终报告中的每个漏洞都真实可信、影响明确。

## 核心职责
1. **对比验证**：对已报告漏洞重新发送 Payload，确认可稳定复现
2. **误报排除**：识别因 WAF、缓存、随机内容导致的假阳性
3. **证据链构建**：为每个确认漏洞整理「请求 → 响应 → 影响」完整证据链
4. **影响评估**：评估漏洞的实际危害范围与可利用性
5. **边界确认**：验证漏洞是否在授权范围内，是否影响非目标资产

## 工作流程
1. 读取 PentestContext 中的 `findings` 列表，获取待验证漏洞
2. 逐一复核每个漏洞：
   - 重新发送原始 Payload，确认响应特征一致
   - 发送变形 Payload（编码/大小写/分隔符变化），测试 WAF 绕过可能性
   - 发送无害对照请求，排除随机噪声导致的误判
3. 构建证据链：记录原始请求、响应片段、判定依据
4. 更新漏洞状态：confirmed（已确认）/ false_positive（误报）/ needs_review（待定）
5. 对确认漏洞补充影响描述与修复建议

## 协作规范
- **你不直接与其他 Agent 对话**，所有信息交换通过共享的 PentestContext 完成
- 从 PentestContext 的 `findings` 读取攻击手报告的漏洞，进行独立复核
- 验证结果写回 PentestContext（更新 finding 的验证状态与证据链字段）
- 若发现误报，标注并说明判定依据；若发现新漏洞，调用 `record_finding()` 补充
- 将验证阶段完成后把 `phase` 推进到 `reporting`（如适用）

## 输出要求
- 完成验证后，输出验证结论：确认漏洞数、排除误报数、待定漏洞数
- 对每个确认漏洞给出完整证据链：Payload、响应特征、影响范围、修复建议
- 在回复末尾添加 `TASK_COMPLETE` 标记，表示本角色任务完成，交回指挥官
- 对排除的误报说明判定依据，保留可追溯性
"""

_BUILTIN_RADAR_PROMPT = """\
# 角色：情报员（Radar）— Hemlock 指纹识别专家

## 角色定位
你是 Hemlock 多 Agent 协作框架中的**情报员**角色，专职负责指纹识别、技术栈分析与 CVE 检索。
你的使命是精准识别目标使用的技术组件及其版本，并关联已知漏洞，为攻击角色提供定向打击情报。

## 核心职责
1. **指纹识别**：通过响应头、Cookie、HTML 特征、JS 文件、错误页面识别组件
2. **技术栈分析**：确定 Server、框架、编程语言、数据库、CMS、操作系统
3. **版本精确化**：通过版本路径探测、favicon 哈希、默认页面确定具体版本
4. **CVE 搜索**：对识别到的组件+版本检索已知漏洞（N-Day）
5. **攻击面映射**：将技术栈信息转化为可操作的漏洞测试优先级

## 工作流程
1. 读取 PentestContext 中的 `tech_stack`，获取侦察员初步识别的技术栈信息
2. 深化指纹分析：
   - 检查响应头：Server、X-Powered-By、X-AspNet-Version、Set-Cookie 特征
   - 检查 HTML：meta generator、特定路径（/wp-admin、/administrator）、JS 框架特征
   - 探测版本路径：/server-status、/actuator/info、/elmah.axd 等框架特征端点
   - 分析错误页面：触发 404/500 观察框架默认错误页
3. 调用 `analyze_tech_stack()` 将识别结果写入 PentestContext
4. 对识别到的「组件 + 版本」组合，**使用 `invoke_skill` 工具调用 `cve-search` 技能检索已知 CVE**：
   - 调用方式：invoke_skill(skill_name="cve-search")
   - 获取 CVE 搜索的详细操作指导后，按指导执行检索
   - 将检索到的 CVE 信息整理为漏洞线索
5. 对高危 CVE 调用 `record_finding()` 登记到 PentestContext

## invoke_skill 调用指引
- 当你识别到具体组件版本（如 Apache 2.4.49、Spring Framework 5.3.x）时，
  调用 `invoke_skill` 工具，参数 `skill_name` 设为 `cve-search`
- 该技能会返回 CVE 检索的详细操作指导，按指导完成检索流程
- 将检索结果中的 CVE 编号、漏洞类型、严重等级、利用条件整理记录
- 对存在公开 PoC 的高危 CVE 重点标注，提示攻击手角色优先测试

## 协作规范
- **你不直接与其他 Agent 对话**，所有信息交换通过共享的 PentestContext 完成
- 用 `analyze_tech_stack()` 写入技术栈指纹，攻击手角色会据此调整漏洞测试优先级
- 将检索到的高危 CVE 调用 `record_finding()` 写入，验证员角色会据此验证
- 在 PentestContext 的 `tech_stack.headers_hints` 中记录特征响应头，供全队参考
- 技术栈识别完成后，攻击手角色的 `recommend_strategy()` 会自动据此推荐测试方向

## 输出要求
- 完成指纹识别后，输出技术栈摘要：Server、框架、语言、CMS、版本、操作系统
- 输出关联 CVE 清单：CVE 编号、漏洞类型、严重等级、利用条件、是否有公开 PoC
- 在回复末尾添加 `TASK_COMPLETE` 标记，表示本角色任务完成，交回指挥官
- 对每个识别到的组件标注置信度（确定/推测）与识别依据
"""

_BUILTIN_SCRIBE_PROMPT = """\
# 角色：报告员（Scribe）— Hemlock 报告专家

## 角色定位
你是 Hemlock 多 Agent 协作框架中的**报告员**角色，专职负责漏洞汇总与报告生成。
你的使命是将各角色在 PentestContext 中积累的所有发现、证据、统计数据整合为结构化、可读、可执行的渗透测试报告。

## 核心职责
1. **漏洞汇总**：整合 PentestContext 中所有已确认的漏洞发现
2. **证据整理**：为每个漏洞编排完整的证据链（请求、响应、影响）
3. **统计呈现**：呈现测试覆盖度、参数覆盖度、漏洞分布等统计指标
4. **风险评估**：按 CVSS 或业务影响对漏洞进行风险评级与优先级排序
5. **报告输出**：生成结构化、格式化的最终渗透测试报告

## 工作流程
1. 读取 PentestContext，调用 `get_coverage_stats()` 获取测试统计
2. 遍历 `findings` 列表，按严重等级（critical > high > medium > low > info）排序
3. 读取 `tech_stack` 与 `endpoints`，呈现目标画像与攻击面
4. 对每个漏洞整理：
   - 漏洞标题、类型、严重等级
   - 受影响端点与参数
   - 完整证据链（Payload + 响应特征）
   - 影响描述与修复建议
5. 汇总测试覆盖度统计：端点数、参数数、覆盖率、请求数
6. 生成修复建议优先级清单

## 协作规范
- **你不直接与其他 Agent 对话**，所有信息交换通过共享的 PentestContext 完成
- 从 PentestContext 的 `findings` 读取攻击手发现、验证员验证的漏洞数据
- 从 `tech_stack` 读取情报员识别的技术栈信息，写入报告的目标画像章节
- 从 `get_coverage_stats()` 读取测试统计，呈现报告的测试覆盖度章节
- 报告生成后将 `phase` 推进到 `done`（如适用）

## 输出要求
- 输出完整的渗透测试报告，包含以下章节：
  1. **执行摘要**：目标信息、测试范围、总体风险评级
  2. **目标画像**：技术栈、认证机制、攻击面概览
  3. **漏洞详情**：按严重等级排序，每项含证据链与修复建议
  4. **测试统计**：覆盖度、请求数、漏洞分布
  5. **修复建议**：按优先级排序的修复清单
- 在回复末尾添加 `TASK_COMPLETE` 标记，表示本角色任务完成，交回指挥官
- 报告语言简洁专业，证据客观可追溯，建议具体可执行
"""

# 内置角色清单：(name, label, description, category, system_prompt)
_BUILTIN_ROLES: list[dict[str, Any]] = [
    {
        "name": "scout",
        "label": "侦察员",
        "description": "信息收集、端口扫描、目录发现",
        "category": "recon",
        "system_prompt": _BUILTIN_SCOUT_PROMPT,
    },
    {
        "name": "striker",
        "label": "攻击手",
        "description": "漏洞测试、注入、XSS、越权",
        "category": "attack",
        "system_prompt": _BUILTIN_STRIKER_PROMPT,
    },
    {
        "name": "auditor",
        "label": "验证员",
        "description": "对比验证、误报排除、证据链",
        "category": "verify",
        "system_prompt": _BUILTIN_AUDITOR_PROMPT,
    },
    {
        "name": "radar",
        "label": "情报员",
        "description": "指纹识别、技术栈分析、CVE 搜索",
        "category": "fingerprint",
        "system_prompt": _BUILTIN_RADAR_PROMPT,
    },
    {
        "name": "scribe",
        "label": "报告员",
        "description": "汇总报告、格式化输出",
        "category": "report",
        "system_prompt": _BUILTIN_SCRIBE_PROMPT,
    },
]


def _format_role_file(
    name: str,
    label: str,
    description: str,
    category: str,
    is_builtin: bool,
    system_prompt: str,
) -> str:
    """将角色元数据与系统提示词格式化为带 YAML frontmatter 的 Markdown 文本。

    Args:
        name: 角色标识（kebab-case）
        label: 显示名称
        description: 一句话描述
        category: 分类
        is_builtin: 是否内置角色
        system_prompt: 系统提示词正文

    Returns:
        完整的 Markdown 文件内容（frontmatter + 正文）
    """
    frontmatter = (
        "---\n"
        f"name: {name}\n"
        f"label: {label}\n"
        f"description: {description}\n"
        f"category: {category}\n"
        f"is_builtin: {str(is_builtin).lower()}\n"
        "---\n\n"
    )
    return frontmatter + system_prompt.strip() + "\n"


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """解析 Markdown 文件中的 YAML frontmatter 与正文。

    支持 PyYAML 优先解析，不可用时降级为手动键值对解析。

    Args:
        content: 文件完整文本

    Returns:
        二元组 (元数据字典, 正文内容)。无 frontmatter 时元数据为空字典。
    """
    if not content.startswith("---"):
        return {}, content

    # 定位 frontmatter 结束分隔符（第二个 ---）
    end_marker = content.find("\n---", 3)
    if end_marker == -1:
        return {}, content

    frontmatter_str = content[3:end_marker].strip()
    # 跳过结束分隔符及其换行
    body_start = end_marker + 4
    if body_start < len(content) and content[body_start] == "\n":
        body_start += 1
    body = content[body_start:].strip()

    # 优先使用 PyYAML 解析
    if _HAS_YAML:
        try:
            meta = yaml.safe_load(frontmatter_str)
            if not isinstance(meta, dict):
                meta = {}
            return meta, body
        except yaml.YAMLError as e:
            logger.warning(f"YAML frontmatter 解析失败，降级手动解析: {e}")

    # 降级：手动解析简单 key: value（支持行内 # 注释）
    meta: dict[str, Any] = {}
    for line in frontmatter_str.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # 去除行内注释（YAML 注释格式为 " #"）
        if " #" in value:
            value = value.split(" #")[0].strip()
        # 布尔值转换
        low = value.lower()
        if low in ("true", "yes"):
            value = True
        elif low in ("false", "no"):
            value = False
        meta[key] = value
    return meta, body


def ensure_builtin_roles() -> None:
    """检查内置角色文件是否存在，不存在则自动创建。

    内置角色共 5 个：
    - scout（侦察员）：信息收集、端口扫描、目录发现
    - striker（攻击手）：漏洞测试、注入、XSS、越权
    - auditor（验证员）：对比验证、误报排除、证据链
    - radar（情报员）：指纹识别、技术栈分析、CVE 搜索
    - scribe（报告员）：汇总报告、格式化输出

    本函数应在应用启动时调用，确保内置角色始终可用。
    已存在的同名文件不会被覆盖，以保留用户的自定义修改。
    """
    _ensure_agents_dir()
    for role in _BUILTIN_ROLES:
        path = AGENTS_DIR / f"{role['name']}.md"
        if path.exists():
            continue
        content = _format_role_file(
            name=role["name"],
            label=role["label"],
            description=role["description"],
            category=role["category"],
            is_builtin=True,
            system_prompt=role["system_prompt"],
        )
        path.write_text(content, encoding="utf-8")
        logger.info(f"创建内置角色文件: {role['name']}.md")


def _ensure_agents_dir() -> None:
    """确保角色文件目录 ~/.hemlock/agents/ 存在。"""
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)


def _parse_agent_file(path: Path) -> AgentRole | None:
    """解析单个角色 .md 文件。

    解析规则：
    - YAML frontmatter 提取 name/label/description/category/is_builtin
    - frontmatter 之后的 Markdown 正文作为 system_prompt
    - 若 frontmatter 缺失字段，使用合理默认值补全

    Args:
        path: .md 文件路径

    Returns:
        AgentRole 对象，解析失败返回 None
    """
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return None

        meta, body = _parse_frontmatter(content)

        name = str(meta.get("name") or path.stem)
        label = str(meta.get("label") or name)
        description = str(meta.get("description") or "")
        category = str(meta.get("category") or "custom")
        is_builtin_raw = meta.get("is_builtin", False)
        is_builtin = bool(is_builtin_raw) if not isinstance(is_builtin_raw, str) else (
            is_builtin_raw.strip().lower() in ("true", "yes", "1")
        )
        system_prompt = body

        return AgentRole(
            name=name,
            label=label,
            description=description,
            category=category,
            is_builtin=is_builtin,
            system_prompt=system_prompt,
            file_path=path,
        )
    except Exception as e:
        logger.warning(f"解析角色文件失败 {path}: {e}")
        return None


def get_all_roles(db: Any = None) -> list[AgentRole]:
    """获取所有角色（目录下所有 .md 文件）。

    Args:
        db: 保留参数兼容已有调用方，实际不使用。

    Returns:
        AgentRole 列表，按角色名称排序
    """
    _ensure_agents_dir()
    roles: list[AgentRole] = []
    for path in sorted(AGENTS_DIR.glob("*.md")):
        role = _parse_agent_file(path)
        if role:
            roles.append(role)
    return roles


def get_role_by_name(db: Any, name: str) -> AgentRole | None:
    """按名称获取角色。

    Args:
        db: 保留参数兼容已有调用方，实际不使用。
        name: 角色名称（文件名，不含 .md）

    Returns:
        AgentRole 对象，不存在则返回 None
    """
    _ensure_agents_dir()
    path = AGENTS_DIR / f"{name}.md"
    if not path.exists():
        return None
    return _parse_agent_file(path)


def create_role_file(
    name: str,
    label: str,
    description: str,
    category: str,
    system_prompt: str,
) -> AgentRole:
    """创建自定义角色 .md 文件。

    Args:
        name: 角色标识（kebab-case，仅允许字母数字横线下划线）
        label: 显示名称
        description: 一句话描述
        category: 分类（recon/attack/verify/fingerprint/report/custom）
        system_prompt: 系统提示词正文

    Returns:
        创建的 AgentRole 对象

    Raises:
        ValueError: 角色名称无效或文件已存在
    """
    _ensure_agents_dir()
    # 安全检查：name 只允许字母数字横线下划线
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_")
    if not safe_name:
        raise ValueError("角色名称无效")

    path = AGENTS_DIR / f"{safe_name}.md"
    if path.exists():
        raise ValueError(f"角色 '{safe_name}' 已存在")

    content = _format_role_file(
        name=safe_name,
        label=label,
        description=description,
        category=category,
        is_builtin=False,
        system_prompt=system_prompt,
    )
    path.write_text(content, encoding="utf-8")
    logger.info(f"创建角色文件: {safe_name}.md")
    role = _parse_agent_file(path)
    assert role is not None  # 刚写入的文件一定能解析
    return role


def update_role_file(
    name: str,
    label: str,
    description: str,
    category: str,
    system_prompt: str,
) -> AgentRole:
    """更新角色 .md 文件内容。

    保留原文件的 is_builtin 标记，避免自定义角色被误标为内置角色，
    同时内置角色的更新也保持其内置属性。

    Args:
        name: 角色名称
        label: 显示名称
        description: 一句话描述
        category: 分类
        system_prompt: 系统提示词正文

    Returns:
        更新后的 AgentRole 对象

    Raises:
        ValueError: 角色文件不存在
    """
    path = AGENTS_DIR / f"{name}.md"
    if not path.exists():
        raise ValueError(f"角色 '{name}' 不存在")

    # 保留原 is_builtin 标记
    existing = _parse_agent_file(path)
    is_builtin = existing.is_builtin if existing else False

    content = _format_role_file(
        name=name,
        label=label,
        description=description,
        category=category,
        is_builtin=is_builtin,
        system_prompt=system_prompt,
    )
    path.write_text(content, encoding="utf-8")
    logger.info(f"更新角色文件: {name}.md")
    role = _parse_agent_file(path)
    assert role is not None
    return role


def delete_role_file(name: str) -> None:
    """删除角色 .md 文件。

    内置角色不允许删除，以保护框架核心功能的完整性。

    Args:
        name: 角色名称

    Raises:
        ValueError: 角色文件不存在，或该角色为内置角色（不允许删除）
    """
    path = AGENTS_DIR / f"{name}.md"
    if not path.exists():
        raise ValueError(f"角色 '{name}' 不存在")

    role = _parse_agent_file(path)
    if role and role.is_builtin:
        raise ValueError(f"内置角色 '{name}' 不允许删除")

    path.unlink()
    logger.info(f"删除角色文件: {name}.md")


def build_roles_prompt(db: Any = None) -> str:
    """构建注入指挥官系统提示词的角色列表。

    遵循渐进式披露原则：只注入角色的元数据（名称、描述、分类），
    不注入完整系统提示词。完整提示词在角色被分派激活时才加载。

    Args:
        db: 保留参数兼容已有调用方，实际不使用。

    Returns:
        格式化的角色列表文本，无角色时返回空字符串
    """
    roles = get_all_roles(db)
    if not roles:
        return ""

    lines = [
        "\n\n## 可用角色 (Agent Roles)\n",
        "以下角色可在多 Agent 协作中分派，指挥官根据任务需求选择合适角色：\n",
    ]
    for role in roles:
        builtin_tag = " [内置]" if role.is_builtin else ""
        lines.append(
            f"- **{role.name}**（{role.label}）[{role.category}]{builtin_tag}: {role.description}"
        )
    return "\n".join(lines)
