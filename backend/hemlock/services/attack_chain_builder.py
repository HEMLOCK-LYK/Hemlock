"""攻击链自动构建器（第一期）。

设计稿（docs/attack-chain-design.md）定稿的规则：
- Agent 干活时不要求显式画链——从 task.steps 的因果序列自动抽取
- 时机：任务结束 + 分界线推进前（原始步骤被压缩覆盖前抢救）各跑一次增量抽取
- 幂等：按 (chain_id, source_step) 去重，重复调用不产生重复节点
- 第一期只做时间顺序主干连线，宁可漏连不误连，漏的让 Agent 用
  update_attack_chain 手动补
- credential 脱敏是硬要求：入库一律只存掩码（前 4 + **** + 后 4），
  完整值只存在于当轮对话上下文

抽取规则（tool_call 步骤 → 节点）：
- recon_search / http_probe / subdomain_enum / dns_lookup → asset
- report_finding → vuln（flow_id 挂证据流量）
- verify_vulnerability（verified=True）→ vuln
- authenticate → credential（username/password 掩码入库）
- run_request_chain → action
- extract_data → loot（var_name + pattern，不含提取到的数据本身）
"""

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from hemlock.models.attack_chain import AttackChain, AttackNode
from hemlock.models.flow import Flow

logger = logging.getLogger("hemlock.attack_chain")

# 工具 → 节点类型 映射
_ASSET_TOOLS = {"recon_search", "http_probe", "subdomain_enum", "dns_lookup"}
_ACTION_TOOLS = {"run_request_chain"}
_LOOT_TOOLS = {"extract_data"}

# credential 掩码：保留前 4 后 4，中间 ****；过短值全掩码
_CRED_KEEP = 4


def mask_credential(value: str) -> str:
    """凭据脱敏：入库一律只存掩码（设计稿硬要求）。"""
    value = value or ""
    if len(value) <= _CRED_KEEP * 2:
        return "****"
    return f"{value[:_CRED_KEEP]}****{value[-_CRED_KEEP:]}"


def _parse_step_data(data: Any) -> dict:
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return data if isinstance(data, dict) else {}


def _flow_url(db: Session, flow_id: str) -> str:
    flow = db.query(Flow).filter(Flow.id == flow_id).first()
    return flow.url if flow else ""


def _extract_node(db: Session, step_index: int, tool: str, args: dict, result: Any) -> dict | None:
    """单个 tool_call 步骤 → 节点描述。返回 None 表示该步骤不成节点。"""
    if not isinstance(result, dict):
        result = {}

    if tool in _ASSET_TOOLS:
        url = args.get("url") or args.get("query") or ""
        if not url:
            return None
        return {
            "node_type": "asset",
            "title": f"资产: {url[:120]}",
            "detail": {"url": url[:500], "method": args.get("method", "GET")},
        }

    if tool == "report_finding":
        vuln_type = args.get("vuln_type", "")
        title = args.get("title", "")
        if not vuln_type and not title:
            return None
        flow_id = args.get("flow_id", "")
        finding_id = ""
        if isinstance(result, dict):
            finding_id = str(result.get("finding_id", "") or "")
        detail = {
            "vuln_type": vuln_type,
            "severity": args.get("severity", "medium"),
            "title": title[:200],
            "url": _flow_url(db, flow_id)[:500],
        }
        if finding_id:
            detail["finding_id"] = finding_id
        return {
            "node_type": "vuln",
            "title": f"漏洞: {vuln_type or title}"[:200],
            "detail": detail,
            "flow_id": flow_id,
        }

    if tool == "verify_vulnerability":
        if not result.get("verified"):
            return None  # 未验证通过的不成节点（宁可漏连不误连）
        vuln_type = args.get("vuln_type", "")
        url = args.get("url", "")
        return {
            "node_type": "vuln",
            "title": f"漏洞(已验证): {vuln_type}"[:200],
            "detail": {
                "vuln_type": vuln_type,
                "confidence": result.get("confidence"),
                "url": (url or "")[:500],
            },
        }

    if tool == "authenticate":
        # 成功与否都记录（失败的认证尝试也是链路信息），凭据一律掩码
        ok = bool(result.get("success"))
        return {
            "node_type": "credential",
            "title": f"凭据{'(有效)' if ok else '(尝试)'}: {mask_credential(args.get('username', ''))}"[:200],
            "detail": {
                "login_url": (args.get("login_url") or "")[:500],
                "username": mask_credential(args.get("username", "")),
                "password": mask_credential(args.get("password", "")),
                "success": ok,
            },
        }

    if tool in _ACTION_TOOLS:
        steps = args.get("steps") or []
        urls = [str(s.get("url", ""))[:200] for s in steps if isinstance(s, dict)][:5]
        return {
            "node_type": "action",
            "title": f"请求链: {len(steps)} 跳"[:200],
            "detail": {"steps_count": len(steps), "urls": urls},
        }

    if tool in _LOOT_TOOLS:
        var_name = args.get("var_name", "")
        if not var_name:
            return None
        return {
            "node_type": "loot",
            "title": f"数据提取: {var_name}"[:200],
            "detail": {"var_name": var_name[:100], "extract_type": args.get("extract_type", "regex")},
            "flow_id": args.get("flow_id", ""),
        }

    return None


# credential 节点 detail 的自由文本脱敏：password/密码 等模式后面的值掩码
_CRED_TEXT_PATTERN = re.compile(
    r"(?i)(password|passwd|pwd|密码|口令)(\s*[=:：]\s*)(\S+)"
)


def mask_credential_text(text: str) -> str:
    """对自由文本中的凭据赋值模式做掩码（credential 节点入库前必经）。"""
    return _CRED_TEXT_PATTERN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{mask_credential(m.group(3))}",
        text or "",
    )


def _edge_label(src_type: str, dst_type: str) -> str:
    """类型转换语义简表（第一期主干连线用）。"""
    pairs = {
        ("asset", "vuln"): "发现",
        ("asset", "credential"): "获取",
        ("vuln", "credential"): "利用",
        ("vuln", "vuln"): "升级",
        ("vuln", "loot"): "提取数据",
        ("vuln", "action"): "深入",
        ("credential", "vuln"): "认证后测试",
        ("credential", "action"): "认证后操作",
        ("credential", "loot"): "认证后提取",
        ("action", "loot"): "提取数据",
        ("action", "vuln"): "发现",
        ("action", "action"): "推进",
    }
    return pairs.get((src_type, dst_type), "推进")


def ensure_chain(db: Session, project_id: str | None, task_id: str) -> AttackChain:
    """拿任务对应的 draft 链（没有则建）。每个任务一条自动链。

    只 flush 不 commit：build_from_steps 需要在「发现无可抽取内容」时
    rollback 掉刚建的空链，commit 由调用方在确认有内容后执行。
    """
    chain = (
        db.query(AttackChain)
        .filter(AttackChain.task_id == task_id, AttackChain.status == "draft")
        .first()
    )
    if chain is None:
        chain = AttackChain(project_id=project_id, task_id=task_id, name="", status="draft")
        db.add(chain)
        db.flush()
        db.refresh(chain)
    return chain


def build_from_steps(db: Session, task) -> AttackChain | None:
    """从 task.steps 增量抽取节点（幂等），按时间顺序连成主干。

    任何异常都不影响主流程（构建失败只记日志）。
    返回构建后的链（无可抽取内容且链上无节点时返回 None）。
    """
    try:
        steps = json.loads(task.steps or "[]")
    except (json.JSONDecodeError, ValueError):
        return None
    if not steps:
        return None

    chain = ensure_chain(db, getattr(task, "project_id", None), task.id)
    # 手工节点（Agent 用 add_node + after_index 插位）会借用宿主节点的 source_step，
    # 不能挡掉该步骤的自动抽取——去重集只收自动构建产生的节点
    existing_steps: set[int] = set()
    for n in (
        db.query(AttackNode)
        .filter(AttackNode.chain_id == chain.id, AttackNode.source_step.isnot(None))
        .all()
    ):
        try:
            d = json.loads(n.detail or "")
        except (json.JSONDecodeError, ValueError):
            d = {}
        if isinstance(d, dict) and d.get("_manual"):
            continue
        existing_steps.add(n.source_step)

    new_nodes: list[AttackNode] = []
    for idx, step in enumerate(steps):
        if idx in existing_steps:
            continue
        if step.get("type") != "tool_call":
            continue
        data = _parse_step_data(step.get("data", {}))
        tool = data.get("tool", "")
        args = data.get("args") or {}
        desc = _extract_node(db, idx, tool, args, data.get("result"))
        if desc is None:
            continue
        node = AttackNode(
            chain_id=chain.id,
            node_type=desc["node_type"],
            title=desc["title"],
            detail=json.dumps(desc.get("detail", {}), ensure_ascii=False),
            source_step=idx,
            flow_id=desc.get("flow_id") or None,
        )
        db.add(node)
        new_nodes.append(node)

    if not new_nodes and not _chain_has_nodes(db, chain.id):
        # 一个节点都没有：不留空链
        db.rollback()
        return None

    # 无新节点也重连一次：Agent/画布删掉的主干边在下次构建时自动修复
    # （内容不变时 SQLAlchemy 不产生 UPDATE，无写放大）
    _rebuild_edges(db, chain)
    db.commit()

    # 链名：首个 asset 标题 → 末个 loot/vuln 标题
    _refresh_chain_name(db, chain)
    return chain


def _chain_has_nodes(db: Session, chain_id: str) -> bool:
    db.flush()  # session 可能是 autoflush=False，先落 pending 再查
    return db.query(AttackNode).filter(AttackNode.chain_id == chain_id).first() is not None


def _ordered_nodes(db: Session, chain_id: str) -> list[AttackNode]:
    db.flush()  # 同上：pending 的新节点必须先 flush 才能被查询看到
    return (
        db.query(AttackNode)
        .filter(AttackNode.chain_id == chain_id)
        .order_by(AttackNode.source_step.asc().nullslast(), AttackNode.created_at.asc())
        .all()
    )


def get_chain_nodes(db: Session, chain_id: str) -> list[AttackNode]:
    """对外（报告/导出）公开：按主干顺序取链节点。"""
    return _ordered_nodes(db, chain_id)


def _rebuild_edges(db: Session, chain: AttackChain) -> None:
    """按 source_step 时间顺序重建主干边，同时保留 Agent 手工补的语义边。

    主干边 = 相邻节点自动连线（幂等重算），带 _auto 标记——插节点/删节点后
    旧主干边被新主干替代，而不是被当成手工边残留（如 SQL注入→RCE 的旧直达边）。
    现有边里无 _auto 标记且不属于新主干的视为手工边，原样保留——Agent 用
    update_attack_chain 补的「凭据依赖」类边不能被洗掉。
    """
    nodes = _ordered_nodes(db, chain.id)
    trunk: set[tuple[str, str]] = set()
    edges: list[dict] = []
    for a, b in zip(nodes, nodes[1:]):
        trunk.add((a.id, b.id))
        edges.append({
            "from": a.id,
            "to": b.id,
            "label": _edge_label(a.node_type, b.node_type),
            "_auto": True,
        })
    try:
        existing = json.loads(chain.edges or "[]")
    except (json.JSONDecodeError, ValueError):
        existing = []
    for e in existing:
        if not isinstance(e, dict):
            continue
        if e.get("_auto"):
            continue  # 旧自动主干边：由新主干替代
        if (e.get("from"), e.get("to")) not in trunk:
            edges.append(e)
    chain.edges = json.dumps(edges, ensure_ascii=False)


def _refresh_chain_name(db: Session, chain: AttackChain) -> None:
    nodes = db.query(AttackNode).filter(AttackNode.chain_id == chain.id).all()
    if not nodes:
        return
    nodes.sort(key=lambda n: n.source_step if n.source_step is not None else 10**9)
    first = nodes[0].title
    last = nodes[-1].title
    name = f"{first[:40]} → {last[:40]}" if first != last else first[:80]
    if chain.name != name:
        chain.name = name
        db.commit()


def render_chain_text(db: Session, chain: AttackChain) -> str:
    """链的文本视图（第一期无画布，Agent 与用户都读这个）。"""
    nodes = _ordered_nodes(db, chain.id)
    if not nodes:
        return "（链上暂无节点）"

    try:
        edges = json.loads(chain.edges or "[]")
    except (json.JSONDecodeError, ValueError):
        edges = []
    edge_map = {(e.get("from"), e.get("to")): e.get("label", "") for e in edges if isinstance(e, dict)}

    type_badge = {"asset": "资产", "vuln": "漏洞", "credential": "凭据", "action": "动作", "loot": "获取"}
    lines = [f"攻击链 [{chain.status}] {chain.name}", ""]
    for i, node in enumerate(nodes):
        badge = type_badge.get(node.node_type, node.node_type)
        lines.append(f"{i + 1}. [{badge}] {node.title}")
        if node.detail:
            try:
                detail = json.loads(node.detail)
                kv = "  ".join(f"{k}={v}" for k, v in detail.items() if v not in ("", None))
                if kv:
                    lines.append(f"   {kv}")
            except (json.JSONDecodeError, ValueError):
                pass
        if node.flow_id:
            lines.append(f"   证据流量: {node.flow_id}")
        if i + 1 < len(nodes):
            label = edge_map.get((node.id, nodes[i + 1].id), "")
            arrow = f" --{label}--> " if label else " --> "
            lines.append(f"   {arrow.strip()}")
    return "\n".join(lines)


def export_chain_markdown(db: Session, chain: AttackChain) -> str:
    """导出 Markdown 攻击路径描述（直接进漏洞报告）。"""
    nodes = _ordered_nodes(db, chain.id)
    type_badge = {"asset": "资产", "vuln": "漏洞", "credential": "凭据", "action": "动作", "loot": "获取"}
    out = [f"## 攻击路径：{chain.name}", ""]
    for node in nodes:
        badge = type_badge.get(node.node_type, node.node_type)
        out.append(f"- **[{badge}]** {node.title}")
        if node.detail:
            try:
                detail = json.loads(node.detail)
                for k, v in detail.items():
                    if v not in ("", None):
                        out.append(f"  - {k}: {v}")
            except (json.JSONDecodeError, ValueError):
                pass
        if node.flow_id:
            out.append(f"  - 证据流量: `{node.flow_id}`")
    return "\n".join(out)
