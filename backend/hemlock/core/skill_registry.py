"""Skill 注册表 - 基于 Markdown 文件的技能管理（Claude Code 风格）。

设计理念：
- 技能就是 ~/.hemlock/skills/ 目录下的 .md 文件或子目录
- 平铺文件：文件名 = 技能名称（如 cve-search.md → name="cve-search"）
- 子目录形式：目录名 = 技能名称，SKILL.md 为主文件（支持补充文档）
- 支持 YAML frontmatter（name/description），兼容无 frontmatter 的文件
- 文件存在即启用，删除文件即移除——无需数据库，无需开关

内置技能通过 ensure_builtin_skills() 在应用启动时从 data/builtin_skills/
复制到用户目录，已存在的文件不覆盖（保留用户自定义修改）。

用户可直接用任意编辑器创建/编辑 .md 文件，保存即生效。
"""

import logging
import shutil
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
    logger.warning("PyYAML 未安装，技能 frontmatter 将降级为手动解析")

# 技能文件目录（用户目录）
SKILLS_DIR = get_data_dir() / "skills"

# 内置技能目录（随应用打包）
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "data" / "builtin_skills"


@dataclass
class SkillFile:
    """从 .md 文件解析出的技能对象（duck-type 兼容旧 Skill 模型）。

    Attributes:
        name: 技能标识（平铺文件去 .md，子目录用目录名）
        label: 显示名称（H1 标题）
        description: 简短描述（注入 system prompt）
        instructions: 完整文件内容（invoke_skill 激活时加载）
        file_path: 技能文件路径
        category: 分类
        enabled: 是否启用（文件存在即启用）
        is_builtin: 是否内置技能
    """

    name: str
    label: str
    description: str
    instructions: str  # 完整文件内容
    file_path: Path
    category: str = "custom"
    enabled: bool = True  # 文件存在即启用
    is_builtin: bool = False

    @property
    def id(self) -> str:
        """兼容旧 API：id 就是 name。"""
        return self.name


def _ensure_skills_dir() -> None:
    """确保技能目录存在。"""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """解析 Markdown 文件中的 YAML frontmatter 与正文。

    支持 PyYAML 优先解析，不可用时降级为手动键值对解析。
    无 frontmatter 时返回空字典和原始内容。

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
        meta[key] = value
    return meta, body


def _parse_skill_file(path: Path) -> SkillFile | None:
    """解析单个技能文件。

    解析规则：
    - YAML frontmatter 优先提取 name/description（如果有）
    - 无 frontmatter 时：文件名(去.md) = name，H1 = label，首段 = description
    - 子目录中的 SKILL.md：name 用父目录名
    - 完整文件内容 = instructions

    Args:
        path: .md 文件路径

    Returns:
        SkillFile 对象，解析失败返回 None
    """
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return None

        # 确定 name：子目录 SKILL.md 用目录名，平铺文件用文件名
        if path.name == "SKILL.md" and path.parent != SKILLS_DIR:
            name = path.parent.name
        else:
            name = path.stem

        label = name
        description = ""

        # 1. 解析 frontmatter
        meta, body = _parse_frontmatter(content)
        if meta:
            name = str(meta.get("name") or name)
            description = str(meta.get("description") or "")

        # 2. 从 body 提取 H1 作为 label
        lines = body.split("\n")
        idx = 0
        while idx < len(lines):
            line = lines[idx].strip()
            if line.startswith("# "):
                label = line[2:].strip()
                idx += 1
                break
            elif line:  # 遇到非空非 H1 行，跳过 H1 提取
                break
            idx += 1

        # 3. 如果 frontmatter 没有提供 description，从 body 提取首段
        if not description:
            while idx < len(lines):
                line = lines[idx].strip()
                if not line:
                    idx += 1
                    continue
                if line.startswith("#"):
                    idx += 1
                    continue
                description = line
                break

        return SkillFile(
            name=name,
            label=label,
            description=description,
            instructions=content,
            file_path=path,
        )
    except Exception as e:
        logger.warning(f"解析技能文件失败 {path}: {e}")
        return None


def get_enabled_skills(db: Any = None) -> list[SkillFile]:
    """获取所有已启用的技能。

    扫描两种格式：
    1. 平铺 .md 文件：~/.hemlock/skills/*.md
    2. 子目录形式：~/.hemlock/skills/*/SKILL.md（支持补充文档）

    Args:
        db: 保留参数兼容已有调用方，实际不使用。

    Returns:
        SkillFile 列表，按 name 排序
    """
    _ensure_skills_dir()
    skills = []
    seen_names = set()

    # 1. 扫描平铺的 .md 文件
    for path in sorted(SKILLS_DIR.glob("*.md")):
        skill = _parse_skill_file(path)
        if skill and skill.name not in seen_names:
            skills.append(skill)
            seen_names.add(skill.name)

    # 2. 扫描子目录中的 SKILL.md
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        skill = _parse_skill_file(path)
        if skill and skill.name not in seen_names:
            skills.append(skill)
            seen_names.add(skill.name)

    return skills


def get_all_skills(db: Any = None) -> list[SkillFile]:
    """获取所有技能（文件方案中等同于 get_enabled_skills）。"""
    return get_enabled_skills(db)


def get_skill_by_name(db: Any, name: str) -> SkillFile | None:
    """按名称获取技能。

    查找顺序：
    1. 平铺文件：~/.hemlock/skills/{name}.md
    2. 子目录形式：~/.hemlock/skills/{name}/SKILL.md

    Args:
        db: 保留参数兼容已有调用方，实际不使用。
        name: 技能名称

    Returns:
        SkillFile 对象，不存在则返回 None
    """
    _ensure_skills_dir()

    # 1. 查找平铺文件
    path = SKILLS_DIR / f"{name}.md"
    if path.exists():
        return _parse_skill_file(path)

    # 2. 查找子目录
    path = SKILLS_DIR / name / "SKILL.md"
    if path.exists():
        return _parse_skill_file(path)

    return None


def get_skill_instructions(db: Any, name: str) -> str | None:
    """获取技能的完整指令（渐进式披露：仅在激活时调用）。

    Args:
        db: 保留参数兼容已有调用方，实际不使用。
        name: 技能名称

    Returns:
        完整指令文本（Markdown），不存在则返回 None
    """
    skill = get_skill_by_name(db, name)
    if not skill:
        return None
    return skill.instructions


def build_skills_prompt(db: Any = None) -> str:
    """构建注入系统提示词的技能索引（仅 name + 简短描述）。

    遵循渐进式披露原则：只注入元数据索引，不注入完整指令；
    完整指令在 Agent 调用 invoke_skill 时才加载。

    Token 优化：技能描述截断到 _SKILL_DESC_MAX 字符，避免技能库
    （98+ 个内置技能）的全量描述注入导致 system prompt 膨胀
    （原实现约 8K tokens/回合）。

    Args:
        db: 保留参数兼容已有调用方，实际不使用。

    Returns:
        格式化的技能索引文本，无技能时返回空字符串
    """
    # 技能描述截断长度（字符）：索引足够判断技能用途，细节留给 invoke_skill
    _SKILL_DESC_MAX = 90

    skills = get_enabled_skills(db)
    if not skills:
        return ""

    # 确定性排序：技能顺序固定后，技能集合不变时索引字符串逐字节一致，
    # 缓存前缀（Anthropic 显式断点 / DeepSeek-OpenAI 自动前缀）才能命中。
    # 顺序不固定的话，内容没变也会因排列不同而让缓存作废。
    skills = sorted(skills, key=lambda s: s.name)

    lines = ["\n\n## 可用技能 (Skills)\n以下技能已启用，可通过 invoke_skill 工具调用（名称+用途索引）：\n"]
    for s in skills:
        desc = s.description or ""
        if len(desc) > _SKILL_DESC_MAX:
            desc = desc[:_SKILL_DESC_MAX].rstrip() + "…"
        lines.append(f"- **{s.name}**: {desc}")
    return "\n".join(lines)


def create_skill_file(name: str, content: str) -> SkillFile:
    """创建技能 .md 文件（平铺形式）。

    Args:
        name: 技能名称（文件名，不含 .md）
        content: 完整 Markdown 内容

    Returns:
        创建的 SkillFile 对象

    Raises:
        ValueError: 文件已存在
    """
    _ensure_skills_dir()
    # 安全检查：name 只允许字母数字横线下划线
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_")
    if not safe_name:
        raise ValueError("技能名称无效")

    path = SKILLS_DIR / f"{safe_name}.md"
    if path.exists():
        raise ValueError(f"技能 '{safe_name}' 已存在")

    # 同时检查子目录是否已存在
    dir_path = SKILLS_DIR / safe_name
    if dir_path.exists():
        raise ValueError(f"技能 '{safe_name}' 已存在（目录形式）")

    path.write_text(content, encoding="utf-8")
    logger.info(f"创建技能文件: {safe_name}.md")
    return _parse_skill_file(path)  # type: ignore


def update_skill_file(name: str, content: str) -> SkillFile:
    """更新技能文件内容。

    支持平铺文件和子目录中的 SKILL.md。

    Args:
        name: 技能名称
        content: 新的完整 Markdown 内容

    Returns:
        更新后的 SkillFile 对象

    Raises:
        ValueError: 技能不存在
    """
    # 1. 查找平铺文件
    path = SKILLS_DIR / f"{name}.md"
    if path.exists():
        path.write_text(content, encoding="utf-8")
        logger.info(f"更新技能文件: {name}.md")
        return _parse_skill_file(path)  # type: ignore

    # 2. 查找子目录中的 SKILL.md
    path = SKILLS_DIR / name / "SKILL.md"
    if path.exists():
        path.write_text(content, encoding="utf-8")
        logger.info(f"更新技能文件: {name}/SKILL.md")
        return _parse_skill_file(path)  # type: ignore

    raise ValueError(f"技能 '{name}' 不存在")


def delete_skill_file(name: str) -> None:
    """删除技能文件或目录。

    支持平铺文件和子目录形式（删除整个目录，含补充文档）。

    Args:
        name: 技能名称

    Raises:
        ValueError: 技能不存在
    """
    # 1. 查找平铺文件
    path = SKILLS_DIR / f"{name}.md"
    if path.exists():
        path.unlink()
        logger.info(f"删除技能文件: {name}.md")
        return

    # 2. 查找子目录
    dir_path = SKILLS_DIR / name
    if dir_path.is_dir():
        shutil.rmtree(dir_path)
        logger.info(f"删除技能目录: {name}/")
        return

    raise ValueError(f"技能 '{name}' 不存在")


def ensure_builtin_skills() -> None:
    """将内置技能从 data/builtin_skills/ 复制到用户目录 ~/.hemlock/skills/。

    - 已存在的文件/目录不会被覆盖，保留用户的自定义修改
    - 支持文件和目录两种形式
    - 应在应用启动时调用
    """
    _ensure_skills_dir()

    if not BUILTIN_SKILLS_DIR.exists():
        logger.warning(f"内置技能目录不存在: {BUILTIN_SKILLS_DIR}")
        return

    copied = 0
    for item in BUILTIN_SKILLS_DIR.iterdir():
        target = SKILLS_DIR / item.name
        if target.exists():
            continue  # 不覆盖

        try:
            if item.is_dir():
                shutil.copytree(item, target)
                copied += 1
            elif item.is_file() and item.suffix == ".md":
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                copied += 1
        except Exception as e:
            logger.warning(f"复制内置技能失败 {item.name}: {e}")

    if copied > 0:
        logger.info(f"内置技能初始化完成，复制了 {copied} 个技能")


# invoke_skill 工具的 schema 定义（传给 LLM 的 tool definition）
INVOKE_SKILL_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "invoke_skill",
        "description": "调用一个已启用的技能获取详细操作指导。当你需要某个特定安全测试领域的专业指导时调用此工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "要调用的技能名称，从可用技能列表中选择",
                }
            },
            "required": ["skill_name"],
        },
    },
}
