"""记忆管理器 - 基于 Markdown 文件的跨会话长期记忆（Claude Code 风格）。

设计理念：
- 记忆就是 ~/.hemlock/memories/ 目录下的 .md 文件
- 文件名 = 记忆名称（如 waf-encoding-bypass.md → name="waf-encoding-bypass"）
- 文件用 YAML frontmatter 存储元数据（type, description, trigger）
- frontmatter 之后的正文 = 记忆内容（content）
- 文件存在即生效，删除文件即移除——无需数据库

目录结构（两大类）：
    ~/.hemlock/memories/
    ├── global/              # 长期记忆 — 经验（user/feedback/reference 类型）
    │   ├── waf-encoding-bypass.md
    │   └── user-prefers-minimal-payloads.md
    ├── kb/                  # 长期记忆 — 知识库（按库分目录，库名/库类型由使用者或
    │   │                   #   Agent 自定，如 绕waf实战技巧 / nday利用指南 / 0day库）
    │   ├── waf-bypass/
    │   │   ├── _meta.json   # 库描述（name/description/created_at）
    │   │   └── double-url-encode.md
    │   └── nday-guide/
    │       └── shiro-cve-2016-4437.md
    └── projects/            # 项目记忆（project 类型，仅本项目内注入）
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

import json
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
KB_DIR = MEMORIES_DIR / "kb"
PROJECTS_DIR = MEMORIES_DIR / "projects"

# 记忆类型
MEMORY_TYPE_USER = "user"
MEMORY_TYPE_FEEDBACK = "feedback"
MEMORY_TYPE_PROJECT = "project"
MEMORY_TYPE_REFERENCE = "reference"
MEMORY_TYPE_KB = "kb"  # 知识库条目（kb/ 目录，库 = 用户自定义分类）

MEMORY_TYPES = (MEMORY_TYPE_USER, MEMORY_TYPE_FEEDBACK, MEMORY_TYPE_PROJECT, MEMORY_TYPE_REFERENCE, MEMORY_TYPE_KB)

# 全局类型（存在 global/ 目录）
GLOBAL_TYPES = {MEMORY_TYPE_USER, MEMORY_TYPE_FEEDBACK, MEMORY_TYPE_REFERENCE}

# 知识库快照单库最大列出条目数（超过只列最近更新的，其余靠 get_memory 检索）
KB_SNAPSHOT_MAX_ENTRIES = 100


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
    kb: str | None = None  # 知识库条目所属库名（type=kb 时非空）
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
    KB_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def _entry_dir(mem_type: str, project_id: str | None = None, kb: str | None = None) -> Path:
    """根据记忆类型返回条目所在目录。

    - 经验类型（user/feedback/reference）→ global/
    - 知识库条目（kb）→ kb/{库名}/（库不存在时抛 ValueError，建库走 create_kb）
    - 项目记忆（project）→ projects/{project_id}/
    """
    _ensure_dirs()
    if mem_type == MEMORY_TYPE_KB:
        if not kb:
            raise ValueError("知识库条目必须指定知识库名（kb）")
        safe_kb = _safe_name(kb)
        kb_path = KB_DIR / safe_kb
        if not kb_path.is_dir():
            raise ValueError(f"知识库不存在: {kb}（可先创建）")
        return kb_path
    if mem_type in GLOBAL_TYPES:
        return GLOBAL_DIR
    # project 类型 → projects/{project_id}/
    pid = project_id or "_shared"
    proj_dir = PROJECTS_DIR / pid
    proj_dir.mkdir(parents=True, exist_ok=True)
    return proj_dir


# 向后兼容：旧调用方（_get_dir_for_type）语义保留
def _get_dir_for_type(mem_type: str, project_id: str | None = None) -> Path:
    return _entry_dir(mem_type, project_id)


def _scan_dir(directory: Path, project_id: str | None = None, kb: str | None = None) -> list[MemoryFile]:
    """扫描目录下所有 .md 文件，返回解析后的记忆列表。"""
    if not directory.exists():
        return []
    memories = []
    for path in sorted(directory.glob("*.md")):
        mem = _parse_memory_file(path)
        if mem:
            if project_id:
                mem.project_id = project_id
            if kb:
                mem.kb = kb
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
            # kb 归属从路径推导（kb/{库名}/{条目}.md），get_memory 按名查找时也能带上
            kb=(path.parent.name if path.parent.parent == KB_DIR else None),
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
        "kb": mem.kb,
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
    kb: str | None = None,
) -> MemoryFile:
    """创建记忆 .md 文件。

    Args:
        db: 保留参数兼容已有调用方，实际不使用。
        name: 唯一名称（文件名）
        description: 一句话描述
        type: 记忆类型 (user/feedback/project/reference/kb)
        trigger_list: 触发关键词列表
        content: 记忆正文
        project_id: 关联项目 ID（project 类型用）
        kb: 知识库名（type=kb 时必填；传入 kb 时 type 自动置为 kb）

    Returns:
        创建的 MemoryFile 对象

    Raises:
        ValueError: 名称已存在或类型非法
    """
    if kb:
        type = MEMORY_TYPE_KB
    if type not in MEMORY_TYPES:
        raise ValueError(f"非法的记忆类型 '{type}'，允许: {', '.join(MEMORY_TYPES)}")
    if type == MEMORY_TYPE_KB and not kb:
        raise ValueError("知识库条目必须指定知识库名（kb）")

    safe = _safe_name(name)
    target_dir = _entry_dir(type, project_id, kb)
    file_path = target_dir / f"{safe}.md"

    if file_path.exists():
        raise ValueError(f"记忆 '{safe}' 已存在")

    if trigger_list is None:
        trigger_list = []

    file_content = _build_file_content(type, description, trigger_list, content)
    file_path.write_text(file_content, encoding="utf-8")
    logger.info(f"创建记忆文件: {safe}.md (type={type}, kb={kb or '-'})")

    mem = _parse_memory_file(file_path)
    if mem:
        mem.project_id = project_id if type == MEMORY_TYPE_PROJECT else None
        mem.kb = kb if type == MEMORY_TYPE_KB else None
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
    # kb 归属：显式传 kb 以新值为准；显式把 type 改出 kb 时清除旧库归属
    new_kb = kwargs["kb"] if "kb" in kwargs else old_mem.kb
    if "type" in kwargs and kwargs["type"] != MEMORY_TYPE_KB:
        new_kb = kwargs.get("kb")
    if new_kb:
        new_type = MEMORY_TYPE_KB
    if new_type == MEMORY_TYPE_KB and not new_kb:
        raise ValueError("知识库条目必须指定知识库名（kb）")

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
    target_dir = _entry_dir(new_type, new_project_id, new_kb)
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
        mem.kb = new_kb if new_type == MEMORY_TYPE_KB else None
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
    """在所有目录中查找记忆文件（global / kb 各库 / projects 各项目）。"""
    safe = _safe_name(name)
    # 先查 global
    path = GLOBAL_DIR / f"{safe}.md"
    if path.exists():
        return path
    # 再查知识库各库目录
    if KB_DIR.exists():
        for kb_dir in KB_DIR.iterdir():
            if kb_dir.is_dir():
                p = kb_dir / f"{safe}.md"
                if p.exists():
                    return p
    # 最后查 projects 下所有子目录
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
    kb_filter: str | None = None,
) -> list[MemoryFile]:
    """列出记忆，支持按类型、知识库和项目过滤。

    kb_filter 指定时只返回该库的条目；type_filter=kb 时返回所有库的条目。
    """
    _ensure_dirs()
    memories = []

    # 知识库条目（kb/{库}/*.md）——始终扫描，库内按 kb_filter 过滤
    if KB_DIR.exists():
        for kb_dir in sorted(KB_DIR.iterdir()):
            if not kb_dir.is_dir():
                continue
            if kb_filter and kb_dir.name != kb_filter:
                continue
            for mem in _scan_dir(kb_dir, kb=kb_dir.name):
                if type_filter and mem.type != type_filter:
                    continue
                memories.append(mem)

    # 经验（global）
    if not kb_filter:
        for mem in _scan_dir(GLOBAL_DIR):
            if type_filter and mem.type != type_filter:
                continue
            memories.append(mem)

    # 项目记忆（projects）
    if not kb_filter and PROJECTS_DIR.exists():
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


# ===== 知识库管理 =====

def _kb_meta_path(kb_name: str) -> Path:
    return KB_DIR / _safe_name(kb_name) / "_meta.json"


def _read_kb_meta(kb_name: str) -> dict[str, Any]:
    """读取库元信息（缺失时返回空描述）。"""
    meta_path = _kb_meta_path(kb_name)
    try:
        if meta_path.exists():
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def list_kbs(db: Any = None) -> list[dict[str, Any]]:
    """列出所有知识库（按名称排序，确定性输出）。

    返回: [{name, description, entry_count, updated_at}]
    """
    _ensure_dirs()
    kbs = []
    if not KB_DIR.exists():
        return kbs
    for kb_dir in sorted(KB_DIR.iterdir()):
        if not kb_dir.is_dir():
            continue
        entries = _scan_dir(kb_dir, kb=kb_dir.name)
        meta = _read_kb_meta(kb_dir.name)
        # 库更新时间 = 库内条目最新 mtime（无条目时取目录 mtime）
        if entries:
            updated = max(e.updated_at or "" for e in entries)
        else:
            updated = datetime.fromtimestamp(kb_dir.stat().st_mtime, tz=timezone.utc).isoformat()
        kbs.append({
            "name": kb_dir.name,
            "description": meta.get("description", ""),
            "entry_count": len(entries),
            "updated_at": updated,
        })
    return kbs


def create_kb(name: str, description: str = "") -> dict[str, Any]:
    """创建知识库（kb/{安全化名}/ 目录 + _meta.json）。

    Raises:
        ValueError: 名称非法或已存在
    """
    _ensure_dirs()
    safe = _safe_name(name)
    kb_path = KB_DIR / safe
    if kb_path.exists():
        raise ValueError(f"知识库 '{safe}' 已存在")
    kb_path.mkdir(parents=True)
    meta = {
        "name": safe,
        "description": description.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (kb_path / "_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"创建知识库: {safe}")
    return {"name": safe, "description": meta["description"], "entry_count": 0}


def delete_kb(name: str) -> bool:
    """删除知识库（连同库内全部条目）。"""
    safe = _safe_name(name)
    kb_path = KB_DIR / safe
    if not kb_path.is_dir():
        raise ValueError(f"知识库不存在: {safe}")
    import shutil
    shutil.rmtree(kb_path)
    logger.info(f"删除知识库: {safe}")
    return True


def search_kb_entries(
    db: Any = None,
    kb: str = "",
    query: str = "",
    n_results: int = 20,
) -> list[dict[str, Any]]:
    """在指定知识库内按子串检索条目（匹配 name/description/trigger/content）。

    供 Agent 在快照未列出所需条目时按需发现（get_memory 工具的 kb 检索模式）。
    确定性排序：命中字段权重（name > description > trigger > content）+ 名称排序。
    """
    if not kb:
        return []
    kb_name = _safe_name(kb)
    kb_dir = KB_DIR / kb_name
    if not kb_dir.is_dir():
        return []

    q = query.strip().lower()
    results: list[tuple[int, str, dict[str, Any]]] = []
    for mem in _scan_dir(kb_dir, kb=kb_name):
        item = _memory_to_dict(mem)
        if not q:
            item["match_field"] = "all"
            results.append((4, mem.name, item))
            continue
        content_l = (mem.content or "").lower()
        if q in mem.name.lower():
            weight = 4
            item["match_field"] = "name"
        elif q in (mem.description or "").lower():
            weight = 3
            item["match_field"] = "description"
        elif any(q in t.lower() for t in mem.trigger):
            weight = 2
            item["match_field"] = "trigger"
        elif q in content_l:
            weight = 1
            item["match_field"] = "content"
        else:
            continue
        results.append((weight, mem.name, item))

    results.sort(key=lambda x: (-x[0], x[1]))
    return [r[2] for r in results[:max(1, n_results)]]


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
    """构建对话开始时的记忆快照（两大类：经验 + 知识库）。

    分级过滤：
    - 经验：global 目录（user/feedback/reference）+ 指定 project 目录
      （project_id 为空时不加载项目级记忆，避免跨项目串扰）
    - 知识库：kb/ 下所有库的条目索引（跨项目长期积累，日积月累）

    快照在对话开始时生成一次并固化到 task.memory_snapshot，期间不可变——
    中途 save_memory 不回流，保证快照消息逐字节稳定、可被前缀缓存命中。
    知识库条目单库超过 KB_SNAPSHOT_MAX_ENTRIES 时只列最近更新的，其余靠
    get_memory 的 kb 检索模式按需发现。

    Returns:
        格式化快照文本，无任何记忆时返回空字符串
    """
    if project_id:
        memories = list_memories(db, project_id=project_id)
    else:
        _ensure_dirs()
        memories = _scan_dir(GLOBAL_DIR)

    sections: list[str] = []
    if memories:
        sections.append(
            "## 长期记忆（对话开始时的快照，跨会话保留）\n"
            "以下是本对话开始时已沉淀的经验索引。快照在对话开始后不再更新，"
            "与当前任务有关时用 get_memory 工具读取其完整内容：\n"
            + format_memory_for_prompt(memories)
        )

    # 知识库索引（每个库一段：库名 + 描述 + 条目列表）
    kbs = list_kbs(db)
    if kbs:
        lines = [
            "## 知识库索引（长期积累的实战知识，跨会话保留）\n"
            "以下是使用者与 Agent 日积月累记录的知识库。条目相关知识可用 "
            "get_memory 按名称读取全文；需要检索时传 kb + query 做库内搜索；"
            "沉淀新知识用 save_memory 并指定 kb：\n"
        ]
        for kb in kbs:
            kb_name = kb["name"]
            desc = f"—— {kb['description']}" if kb["description"] else ""
            entries = [m for m in list_memories(db, kb_filter=kb_name)]
            # 最近更新的排前面（确定性：updated_at 相同再按名称排序）
            entries.sort(key=lambda m: (m.updated_at or "", m.name), reverse=True)
            header = f"### {kb_name}（{len(entries)} 条）{desc}"
            lines.append(header)
            if not entries:
                lines.append("（暂无条目）")
            else:
                for e in entries[:KB_SNAPSHOT_MAX_ENTRIES]:
                    d = e.description or ""
                    if len(d) > 80:
                        d = d[:80].rstrip() + "…"
                    lines.append(f"- **{e.name}** {d}")
                if len(entries) > KB_SNAPSHOT_MAX_ENTRIES:
                    lines.append(
                        f"（仅显示最近更新的 {KB_SNAPSHOT_MAX_ENTRIES} 条，共 {len(entries)} 条，"
                        f"其余用 get_memory 传 kb+query 检索）"
                    )
            lines.append("")
        sections.append("\n".join(lines).rstrip())

    return "\n\n".join(sections)


def search_by_trigger(db: Any = None, text: str = "", n_results: int = 5) -> list[dict[str, Any]]:
    """根据输入文本检索记忆，返回相关记忆。

    匹配字段：trigger 关键词 > name > description > content（子串匹配，
    中文子串、英文整词）。结果按命中字段权重 + 匹配数排序。
    """
    memories = list_memories(db)
    results = []
    text_lower = text.lower().strip()
    if not text_lower:
        return results

    for mem in memories:
        matched = []
        weight = 0
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
            weight = 4 + len(matched)
        elif text_lower in mem.name.lower():
            weight = 3
            matched.append(mem.name)
        elif text_lower in (mem.description or "").lower():
            weight = 2
            matched.append("description")
        elif text_lower in (mem.content or "").lower():
            weight = 1
            matched.append("content")

        if matched:
            item = _memory_to_dict(mem)
            item["matched_triggers"] = matched
            item["match_count"] = weight
            results.append(item)

    results.sort(key=lambda x: x["match_count"], reverse=True)
    return results[:n_results]
