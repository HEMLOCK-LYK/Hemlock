"""记忆管理器 - 基于 Markdown 文件的跨会话长期记忆（Claude Code 风格）。

设计理念：
- 记忆就是 ~/.hemlock/memories/ 目录下的 .md 文件
- 文件名 = 记忆名称（如 waf-encoding-bypass.md → name="waf-encoding-bypass"）
- 文件用 YAML frontmatter 存储元数据（type, description, trigger）
- frontmatter 之后的正文 = 记忆内容（content）
- 文件存在即生效，删除文件即移除——无需数据库

目录结构：
    ~/.hemlock/memories/
    ├── global/              # 全局记忆（user/feedback/reference 类型）
    │   ├── waf-encoding-bypass.md
    │   └── user-prefers-minimal-payloads.md
    └── projects/            # 项目级记忆（project 类型）
        └── proj_abc123/
            └── target-api-rate-limit.md

文件格式：
    ---
    type: feedback
    description: WAF 基于关键字拦截 UNION SELECT，双层 URL 编码可绕过
    trigger: WAF拦截, SQL注入被拦, 403 Forbidden
    ---
    WAF 基于关键字拦截 UNION SELECT，双层 URL 编码或内联注释可绕过。

    **Why:** 明文 payload 暴露扫描行为并中断测试。
    **How to apply:** 先发无害探针确认 WAF，再尝试双层编码或内联注释绕过。
    [[sql-injection-strategy]]
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hemlock.core.config import get_data_dir

logger = logging.getLogger(__name__)

# 记忆文件根目录
MEMORIES_DIR = get_data_dir() / "memories"
GLOBAL_DIR = MEMORIES_DIR / "global"
PROJECTS_DIR = MEMORIES_DIR / "projects"

# 记忆类型
MEMORY_TYPE_USER = "user"
MEMORY_TYPE_FEEDBACK = "feedback"
MEMORY_TYPE_PROJECT = "project"
MEMORY_TYPE_REFERENCE = "reference"

MEMORY_TYPES = (MEMORY_TYPE_USER, MEMORY_TYPE_FEEDBACK, MEMORY_TYPE_PROJECT, MEMORY_TYPE_REFERENCE)

# 全局类型（存在 global/ 目录）
GLOBAL_TYPES = {MEMORY_TYPE_USER, MEMORY_TYPE_FEEDBACK, MEMORY_TYPE_REFERENCE}


@dataclass
class MemoryFile:
    """从 .md 文件解析出的记忆对象。"""

    name: str
    description: str
    type: str
    trigger: list[str] = field(default_factory=list)
    content: str = ""
    file_path: Path = None  # type: ignore
    project_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def id(self) -> str:
        """兼容旧 API：id 就是 name。"""
        return self.name


# ===== 目录管理 =====

def _ensure_dirs() -> None:
    """确保记忆目录结构存在。"""
    GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def _get_dir_for_type(mem_type: str, project_id: str | None = None) -> Path:
    """根据记忆类型返回对应目录。"""
    _ensure_dirs()
    if mem_type in GLOBAL_TYPES:
        return GLOBAL_DIR
    # project 类型 → projects/{project_id}/
    pid = project_id or "_shared"
    proj_dir = PROJECTS_DIR / pid
    proj_dir.mkdir(parents=True, exist_ok=True)
    return proj_dir


def _scan_dir(directory: Path, project_id: str | None = None) -> list[MemoryFile]:
    """扫描目录下所有 .md 文件，返回解析后的记忆列表。"""
    if not directory.exists():
        return []
    memories = []
    for path in sorted(directory.glob("*.md")):
        mem = _parse_memory_file(path)
        if mem:
            if project_id:
                mem.project_id = project_id
            memories.append(mem)
    return memories


# ===== Frontmatter 解析 =====

def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """解析 YAML frontmatter，返回 (元数据字典, 正文内容)。

    格式：
        ---
        type: feedback
        description: 描述文本
        trigger: 关键词1, 关键词2
        ---
        正文内容...
    """
    meta = {}
    body = content

    # 匹配开头的 --- ... --- 块
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
    if fm_match:
        fm_block = fm_match.group(1)
        body = fm_match.group(2).strip()

        for line in fm_block.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()

    return meta, body


def _parse_memory_file(path: Path) -> MemoryFile | None:
    """解析单个 .md 记忆文件。"""
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return None

        meta, body = _parse_frontmatter(raw)

        name = path.stem
        mem_type = meta.get("type", "feedback")
        if mem_type not in MEMORY_TYPES:
            mem_type = "feedback"

        description = meta.get("description", "")

        # trigger 解析：逗号分隔
        trigger_raw = meta.get("trigger", "")
        trigger = [t.strip() for t in trigger_raw.split(",") if t.strip()]

        # 文件修改时间作为 created/updated
        stat = path.stat()
        created = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()
        updated = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

        return MemoryFile(
            name=name,
            description=description,
            type=mem_type,
            trigger=trigger,
            content=body,
            file_path=path,
            created_at=created,
            updated_at=updated,
        )
    except Exception as e:
        logger.warning(f"解析记忆文件失败 {path}: {e}")
        return None


def _build_file_content(
    mem_type: str,
    description: str,
    trigger: list[str],
    content: str,
) -> str:
    """组装完整的 .md 文件内容。"""
    trigger_str = ", ".join(trigger) if trigger else ""
    lines = [
        "---",
        f"type: {mem_type}",
        f"description: {description}",
        f"trigger: {trigger_str}",
        "---",
        "",
        content,
    ]
    return "\n".join(lines)


def _safe_name(name: str) -> str:
    """安全化文件名：只允许字母数字横线下划线。"""
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("记忆名称无效")
    return safe


# ===== 公共 API =====

def _memory_to_dict(mem: MemoryFile) -> dict[str, Any]:
    """将 MemoryFile 转为字典（兼容旧 API）。"""
    return {
        "id": mem.name,
        "name": mem.name,
        "description": mem.description,
        "type": mem.type,
        "trigger": mem.trigger,
        "content": mem.content,
        "project_id": mem.project_id,
        "created_at": mem.created_at,
        "updated_at": mem.updated_at,
    }


def create_memory(
    db: Any = None,
    name: str = "",
    description: str = "",
    type: str = "feedback",
    trigger_list: list[str] | None = None,
    content: str = "",
    project_id: str | None = None,
) -> MemoryFile:
    """创建记忆 .md 文件。

    Args:
        db: 保留参数兼容已有调用方，实际不使用。
        name: 唯一名称（文件名）
        description: 一句话描述
        type: 记忆类型 (user/feedback/project/reference)
        trigger_list: 触发关键词列表
        content: 记忆正文
        project_id: 关联项目 ID（project 类型用）

    Returns:
        创建的 MemoryFile 对象

    Raises:
        ValueError: 名称已存在或类型非法
    """
    if type not in MEMORY_TYPES:
        raise ValueError(f"非法的记忆类型 '{type}'，允许: {', '.join(MEMORY_TYPES)}")

    safe = _safe_name(name)
    target_dir = _get_dir_for_type(type, project_id)
    file_path = target_dir / f"{safe}.md"

    if file_path.exists():
        raise ValueError(f"记忆 '{safe}' 已存在")

    if trigger_list is None:
        trigger_list = []

    file_content = _build_file_content(type, description, trigger_list, content)
    file_path.write_text(file_content, encoding="utf-8")
    logger.info(f"创建记忆文件: {safe}.md (type={type})")

    mem = _parse_memory_file(file_path)
    if mem:
        mem.project_id = project_id if type == MEMORY_TYPE_PROJECT else None
    return mem  # type: ignore


def update_memory(db: Any = None, memory_id: str = "", **kwargs) -> MemoryFile:
    """更新记忆 .md 文件。

    Args:
        db: 保留参数兼容已有调用方，实际不使用。
        memory_id: 记忆名称（文件名）
        **kwargs: 可更新字段 - name, description, type, trigger_list, content, project_id

    Returns:
        更新后的 MemoryFile 对象

    Raises:
        ValueError: 记忆不存在
    """
    # 查找现有文件
    existing = _find_memory_file(memory_id)
    if not existing:
        raise ValueError(f"记忆不存在: {memory_id}")

    old_mem = _parse_memory_file(existing)
    if not old_mem:
        raise ValueError(f"记忆文件解析失败: {memory_id}")

    # 合并更新
    new_type = kwargs.get("type", old_mem.type)
    new_description = kwargs.get("description", old_mem.description)
    new_content = kwargs.get("content", old_mem.content)
    new_project_id = kwargs.get("project_id", old_mem.project_id)

    if "trigger_list" in kwargs:
        new_trigger = kwargs["trigger_list"]
    elif "trigger" in kwargs and isinstance(kwargs["trigger"], list):
        new_trigger = kwargs["trigger"]
    else:
        new_trigger = old_mem.trigger

    # 如果 name 或 type 变了，需要移动文件
    new_name = kwargs.get("name", memory_id)
    new_safe = _safe_name(new_name)

    # 确定目标目录
    target_dir = _get_dir_for_type(new_type, new_project_id)
    new_path = target_dir / f"{new_safe}.md"

    # 写入新内容
    file_content = _build_file_content(new_type, new_description, new_trigger, new_content)
    new_path.write_text(file_content, encoding="utf-8")

    # 如果路径变了，删除旧文件
    if new_path != existing:
        existing.unlink()

    logger.info(f"更新记忆文件: {new_safe}.md")
    mem = _parse_memory_file(new_path)
    if mem:
        mem.project_id = new_project_id if new_type == MEMORY_TYPE_PROJECT else None
    return mem  # type: ignore


def delete_memory(db: Any = None, memory_id: str = "") -> bool:
    """删除记忆 .md 文件。

    Raises:
        ValueError: 记忆不存在
    """
    path = _find_memory_file(memory_id)
    if not path:
        raise ValueError(f"记忆不存在: {memory_id}")

    path.unlink()
    logger.info(f"删除记忆文件: {memory_id}.md")
    return True


def get_memory(db: Any = None, memory_id: str = "") -> MemoryFile | None:
    """获取单条记忆。"""
    path = _find_memory_file(memory_id)
    if not path:
        return None
    return _parse_memory_file(path)


def _find_memory_file(name: str) -> Path | None:
    """在所有目录中查找记忆文件。"""
    safe = _safe_name(name)
    # 先查 global
    path = GLOBAL_DIR / f"{safe}.md"
    if path.exists():
        return path
    # 再查 projects 下所有子目录
    if PROJECTS_DIR.exists():
        for proj_dir in PROJECTS_DIR.iterdir():
            if proj_dir.is_dir():
                p = proj_dir / f"{safe}.md"
                if p.exists():
                    return p
    return None


def list_memories(
    db: Any = None,
    type_filter: str | None = None,
    project_id: str | None = None,
) -> list[MemoryFile]:
    """列出记忆，支持按类型和项目过滤。"""
    _ensure_dirs()
    memories = []

    # 扫描 global 目录
    for mem in _scan_dir(GLOBAL_DIR):
        if type_filter and mem.type != type_filter:
            continue
        memories.append(mem)

    # 扫描 projects 目录
    if PROJECTS_DIR.exists():
        for proj_dir in sorted(PROJECTS_DIR.iterdir()):
            if not proj_dir.is_dir():
                continue
            # 如果指定了 project_id，只扫对应目录
            if project_id and proj_dir.name != project_id:
                continue
            for mem in _scan_dir(proj_dir, project_id=proj_dir.name):
                if type_filter and mem.type != type_filter:
                    continue
                memories.append(mem)

    # 按名称排序
    memories.sort(key=lambda m: m.name)
    return memories


def get_memory_index(db: Any = None) -> list[dict[str, Any]]:
    """返回所有记忆的摘要索引（name + description + trigger）。"""
    memories = list_memories(db)
    return [_memory_to_dict(m) for m in memories]


def format_memory_for_prompt(memories: list) -> str:
    """将记忆列表格式化为可注入 system prompt 的索引（不含正文）。

    索引机制（Token 优化）：只注入 name + description + type + trigger，
    不注入完整正文——记忆库会随 Agent 的 save_memory 持续累积，
    全量加载正文会让 system prompt 无限膨胀。
    Agent 需要某条记忆的完整内容时，调用 get_memory 工具按需读取。
    """
    if not memories:
        return ""

    # 确定性排序：记忆集合不变时索引字符串逐字节一致，缓存前缀才能命中。
    # （list_memories 已按 name 排序，这里兜底兼容直接传入未排序列表的调用方）
    def _mem_name(m) -> str:
        return m.get("name", "") if isinstance(m, dict) else getattr(m, "name", "")
    memories = sorted(memories, key=_mem_name)

    lines = [
        "\n\n## 长期记忆索引 (Agent Memories Index)\n"
        "以下是跨会话保留的记忆索引。相关记忆与你当前任务有关时，"
        "用 get_memory 工具读取其完整内容：\n"
    ]
    for mem in memories:
        # 兼容 dict 和 MemoryFile 对象
        if isinstance(mem, dict):
            name = mem.get("name", "")
            description = mem.get("description", "")
            mem_type = mem.get("type", "")
            trigger = mem.get("trigger", [])
        else:
            name = mem.name
            description = mem.description
            mem_type = mem.type
            trigger = mem.trigger

        desc = description or ""
        if len(desc) > 100:
            desc = desc[:100].rstrip() + "…"
        lines.append(f"- **{name}** [{mem_type}] {desc}")
        if trigger:
            lines.append(f"   触发词: {', '.join(trigger[:6])}")

    return "\n".join(lines)


def build_memories_prompt(
    db: Any = None,
    text: str | None = None,
    n_results: int = 5,
    project_id: str | None = None,
) -> str:
    """构建注入系统提示词的记忆文本（全量加载）。

    分级过滤：加载 global 目录 + 指定 project 目录（project_id 为空时不加载
    任何项目级记忆，避免跨项目记忆串扰当前任务）。记忆过多时由 LLM 自行判断
    相关性，正文按需经 get_memory 工具读取。

    Args:
        db: 保留参数兼容已有调用方，实际不使用。
        text: 保留参数兼容已有调用方，实际不使用。
        n_results: 保留参数兼容已有调用方，实际不使用。
        project_id: 当前项目 ID，仅加载该项目 + global 的记忆；
                    为空时不加载任何项目级记忆（避免跨项目串扰）。

    Returns:
        格式化的记忆提示词文本，无记忆时返回空字符串
    """
    if project_id:
        memories = list_memories(db, project_id=project_id)
    else:
        # 不指定项目：只加载 global（list_memories(None) 是全量，语义不同）
        _ensure_dirs()
        memories = _scan_dir(GLOBAL_DIR)
    if not memories:
        return ""

    return format_memory_for_prompt(memories)


def build_memory_snapshot(db: Any = None, project_id: str | None = None) -> str:
    """构建对话开始时的记忆快照（分级过滤：仅 global + 当前 project）。

    快照在对话开始时生成一次并固化到 task.memory_snapshot，期间不可变——
    中途 save_memory 不回流，保证快照消息逐字节稳定、可被前缀缓存命中。

    Returns:
        格式化快照文本，无记忆时返回空字符串
    """
    if project_id:
        memories = list_memories(db, project_id=project_id)
    else:
        _ensure_dirs()
        memories = _scan_dir(GLOBAL_DIR)
    if not memories:
        return ""

    index = format_memory_for_prompt(memories)
    return (
        "## 长期记忆（对话开始时的快照，跨会话保留）\n"
        "以下是本对话开始时已沉淀的经验索引。快照在对话开始后不再更新，"
        "与当前任务有关时用 get_memory 工具读取其完整内容：\n"
        + index
    )


def search_by_trigger(db: Any = None, text: str = "", n_results: int = 5) -> list[dict[str, Any]]:
    """根据输入文本匹配 trigger 关键词，返回相关记忆。

    保留此函数兼容 API，但实际匹配由 LLM 在全量加载后自行完成。
    """
    memories = list_memories(db)
    results = []

    for mem in memories:
        if not mem.trigger:
            continue
        matched = []
        text_lower = text.lower()
        for kw in mem.trigger:
            kw_lower = kw.lower()
            # 中文子串匹配，英文整词匹配
            has_cjk = any(0x4E00 <= ord(c) <= 0x9FFF for c in kw)
            if has_cjk:
                if kw_lower in text_lower:
                    matched.append(kw)
            else:
                pattern = r'\b' + re.escape(kw_lower) + r'\b'
                if re.search(pattern, text_lower):
                    matched.append(kw)

        if matched:
            item = _memory_to_dict(mem)
            item["matched_triggers"] = matched
            item["match_count"] = len(matched)
            results.append(item)

    results.sort(key=lambda x: x["match_count"], reverse=True)
    return results[:n_results]
