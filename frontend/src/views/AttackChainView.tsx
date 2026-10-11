import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ReactFlow,
  Background,
  Controls,
  MarkerType,
  MiniMap,
  Panel,
  useNodesState,
  useEdgesState,
} from "@xyflow/react";
import type { Node, Edge, NodeProps, Connection, Edge as RFEdge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Workflow,
  Globe,
  Bug,
  KeyRound,
  Zap,
  Package,
  CheckCircle2,
  RotateCcw,
  RefreshCw,
  X,
  ExternalLink,
} from "lucide-react";
import {
  listAttackChains,
  getAttackChain,
  saveNodePosition,
  addChainEdge,
  deleteChainEdge,
  updateChainStatus,
  ATTACK_NODE_META,
} from "../api/attackChain";
import type { AttackChainSummary, AttackChainDetail, AttackNodeData } from "../api/attackChain";
import { useProject } from "../context/ProjectContext";
import { EmptyState, Spinner } from "../components/Common";

/** 节点类型 → 图标（与配色元数据分离，组件里映射） */
const NODE_ICONS: Record<string, typeof Globe> = {
  asset: Globe,
  vuln: Bug,
  credential: KeyRound,
  action: Zap,
  loot: Package,
};

const STATUS_META: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: "var(--accent-yellow)" },
  confirmed: { label: "已确认", color: "var(--accent-green)" },
  failed: { label: "已失败", color: "var(--text-muted)" },
};

/** SVG 的 fill / marker 属性不解析 CSS 变量——需要色值的地方运行时解析 */
function cssColor(name: string, fallback: string): string {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  } catch {
    return fallback;
  }
}

interface AttackNodeCardData extends Record<string, unknown> {
  title: string;
  node_type: string;
  hasFlow: boolean;
  target?: string;
  severity?: string;
}
type AttackRFNode = Node<AttackNodeCardData, "attack">;

interface ChainLabelData extends Record<string, unknown> {
  name: string;
  status: string;
  count: number;
  chainId: string;
}
type ChainLabelRFNode = Node<ChainLabelData, "chainLabel">;

/** severity 徽章配色（vuln 节点） */
const SEVERITY_CLASS: Record<string, string> = {
  critical: "critical",
  high: "high",
  medium: "medium",
  low: "low",
};

/** 从节点 detail 提取目标摘要（host + path）——画布上一眼看出这一步打的是哪。
    asset/vuln 取 url，凭据取 login_url，请求链取第一跳；没有目标的节点返回空。 */
function nodeTarget(detail: Record<string, unknown>): string {
  const url =
    typeof detail.url === "string" && detail.url
      ? detail.url
      : typeof detail.login_url === "string"
        ? detail.login_url
        : "";
  const raw =
    url || (Array.isArray(detail.urls) && typeof detail.urls[0] === "string" ? detail.urls[0] : "");
  if (!raw) return "";
  try {
    const u = new URL(raw);
    return `${u.host}${u.pathname === "/" ? "" : u.pathname}`;
  } catch {
    return raw.slice(0, 48);
  }
}

/** 自定义节点卡片：左侧色条标识类型 + 类型徽标 + severity + 标题 + 目标摘要 + 证据流量指示 */
function AttackNodeCard({ data, selected }: NodeProps<AttackRFNode>) {
  const meta = ATTACK_NODE_META[data.node_type] ?? { label: data.node_type, cssClass: "asset" };
  const Icon = NODE_ICONS[data.node_type] ?? Globe;
  const sevClass = data.severity ? SEVERITY_CLASS[data.severity] : undefined;
  return (
    <div className={`ac-node ${meta.cssClass} ${selected ? "selected" : ""}`}>
      <div className="ac-node-type">
        <Icon size={11} />
        <span>{meta.label}</span>
        {sevClass && <span className={`ac-node-sev ${sevClass}`}>{data.severity}</span>}
      </div>
      <div className="ac-node-title" title={data.title}>
        {data.title || "（无标题）"}
      </div>
      {data.target && (
        <div className="ac-node-target" title={data.target}>
          {data.target}
        </div>
      )}
      {data.hasFlow && (
        <div className="ac-node-flow">
          <ExternalLink size={10} />
          <span>有证据流量</span>
        </div>
      )}
    </div>
  );
}

const nodeTypes = { attack: AttackNodeCard, chainLabel: ChainLabelCard };

/** 总览模式：链名标签卡片（非数据节点），点击进入该链的单链编辑视图 */
function ChainLabelCard({ data }: NodeProps<ChainLabelRFNode>) {
  const meta = STATUS_META[data.status] ?? STATUS_META.draft;
  return (
    <div className="ac-chain-label">
      <Workflow size={12} />
      <span className="ac-chain-label-name" title={data.name}>
        {data.name || "未命名链"}
      </span>
      <span className="ac-chain-label-status" style={{ color: meta.color }}>
        {meta.label}
      </span>
      <span className="ac-chain-label-count">{data.count} 节点</span>
    </div>
  );
}

/** 横向主干 DAG 布局（脊柱-肋骨式）：主干 = 全图最长攻击路径，从左到右横穿中线；
    分支按前驱纵向重心上下铺开——打到同一漏洞的多条路径上下并列后在漏洞节点汇合。
    未拖过的节点（x=y=0）自动排布，拖过的保持用户坐标。 */
function dagLayout(
  nodes: AttackNodeData[],
  edges: { from: string; to: string }[]
): Record<string, { x: number; y: number }> {
  const idSet = new Set(nodes.map((n) => n.id));
  const adj = new Map<string, string[]>();
  const radj = new Map<string, string[]>();
  const indeg = new Map<string, number>();
  for (const n of nodes) {
    adj.set(n.id, []);
    radj.set(n.id, []);
    indeg.set(n.id, 0);
  }
  for (const e of edges) {
    if (!idSet.has(e.from) || !idSet.has(e.to) || e.from === e.to) continue;
    adj.get(e.from)!.push(e.to);
    radj.get(e.to)!.push(e.from);
    indeg.set(e.to, (indeg.get(e.to) ?? 0) + 1);
  }

  // Kahn 拓扑序 + 最长路径分层：层号 → 横向列
  const depth = new Map<string, number>(nodes.map((n) => [n.id, 0]));
  const topo: string[] = [];
  const queue = nodes.filter((n) => (indeg.get(n.id) ?? 0) === 0).map((n) => n.id);
  const deg = new Map(indeg);
  while (queue.length > 0) {
    const cur = queue.shift()!;
    topo.push(cur);
    for (const nxt of adj.get(cur) ?? []) {
      depth.set(nxt, Math.max(depth.get(nxt) ?? 0, (depth.get(cur) ?? 0) + 1));
      deg.set(nxt, (deg.get(nxt) ?? 0) - 1);
      if ((deg.get(nxt) ?? 0) === 0) queue.push(nxt);
    }
  }
  // 成环节点拓扑处理不到（入度永远 > 0）：按时间序给递增深度兜底，避免叠在原点
  let fallbackDepth = Math.max(0, ...depth.values()) + 1;
  for (const n of nodes) {
    if ((deg.get(n.id) ?? 0) > 0) {
      depth.set(n.id, fallbackDepth++);
    }
  }

  const X_GAP = 300;
  const Y_GAP = 140;

  // 主干 = 全图最长路径（拓扑逆序 DP：从每个节点出发的最长剩余链），横穿 y=0
  const longest = new Map<string, number>();
  const bestNext = new Map<string, string>();
  for (let i = topo.length - 1; i >= 0; i--) {
    const id = topo[i];
    let best = 0;
    let next = "";
    for (const s of adj.get(id) ?? []) {
      const v = (longest.get(s) ?? 0) + 1;
      if (v > best) {
        best = v;
        next = s;
      }
    }
    longest.set(id, best);
    if (next) bestNext.set(id, next);
  }
  const trunk = new Set<string>();
  if (nodes.length > 0) {
    let cur = nodes[0].id;
    for (const n of nodes) {
      if ((longest.get(n.id) ?? 0) > (longest.get(cur) ?? 0)) cur = n.id;
    }
    while (cur && !trunk.has(cur)) {
      trunk.add(cur);
      cur = bestNext.get(cur) ?? "";
    }
  }

  const layers = new Map<number, string[]>();
  for (const n of nodes) {
    const d = depth.get(n.id) ?? 0;
    if (!layers.has(d)) layers.set(d, []);
    layers.get(d)!.push(n.id);
  }

  const positions: Record<string, { x: number; y: number }> = {};
  const depths = [...layers.keys()].sort((a, b) => a - b);
  for (const d of depths) {
    const group = layers.get(d)!;
    const x = d * X_GAP;
    for (const id of group) {
      if (trunk.has(id)) positions[id] = { x, y: 0 };
    }
    // 其余节点按前驱纵向重心排序、就近认领空槽——分支嵌套自然向外长，少交叉
    const rest = group.filter((id) => !trunk.has(id));
    const baryOf = (id: string) => {
      const preds = radj.get(id) ?? [];
      const ys = preds
        .map((p) => positions[p]?.y)
        .filter((v): v is number => typeof v === "number");
      return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : 0;
    };
    rest.sort((a, b) => baryOf(a) - baryOf(b));
    const used = new Set<number>(group.some((id) => trunk.has(id)) ? [0] : []);
    for (const id of rest) {
      const want = Math.round(baryOf(id) / Y_GAP);
      let slot = want;
      if (used.has(slot)) {
        for (let r = 1; r <= group.length + 1; r++) {
          if (!used.has(want - r)) {
            slot = want - r;
            break;
          }
          if (!used.has(want + r)) {
            slot = want + r;
            break;
          }
        }
      }
      used.add(slot);
      positions[id] = { x, y: slot * Y_GAP };
    }
  }
  return positions;
}

function detailToFlow(detail: AttackChainDetail): { nodes: AttackRFNode[]; edges: Edge[] } {
  const unpositioned = detail.nodes.filter((n) => n.x === 0 && n.y === 0);
  const autoPositions = dagLayout(
    unpositioned,
    detail.edges
  );
  const positions: Record<string, { x: number; y: number }> = {};
  for (const n of detail.nodes) {
    positions[n.id] = n.x === 0 && n.y === 0 ? autoPositions[n.id] : { x: n.x, y: n.y };
  }
  const nodes: AttackRFNode[] = detail.nodes.map((n) => ({
    id: n.id,
    type: "attack",
    position: positions[n.id],
    deletable: false,
    data: {
      title: n.title,
      node_type: n.node_type,
      hasFlow: Boolean(n.flow_id),
      target: nodeTarget(n.detail),
      severity: typeof n.detail.severity === "string" ? n.detail.severity : undefined,
    },
  }));
  const edges: Edge[] = detail.edges
    .filter((e) => e.from && e.to)
    .map((e) => ({
      id: `${e.from}->${e.to}`,
      source: e.from,
      target: e.to,
      label: e.label || undefined,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed },
      labelShowBg: true,
      labelBgPadding: [4, 2],
      labelBgStyle: { fill: "var(--bg-secondary)" },
      labelBgBorderRadius: 3,
      style: { strokeWidth: 2 },
    }));
  return { nodes, edges };
}

/** 总览布局：每条链一条横向泳道（链名标签在左，主干节点从左到右），链与链上下排列；
    跨链关联（同 flow_id 的「同流量」+ 同 URL 的「同目标」）以纵向虚线相连——
    横向看链内步骤流向，纵向看链间衔接。数据模型不动——边仍存各链 edges 列，
    关联边是纯视图产物，不落库。 */
function buildOverviewFlow(details: AttackChainDetail[]): {
  nodes: (AttackRFNode | ChainLabelRFNode)[];
  edges: Edge[];
} {
  const X_GAP = 300; // 泳道内横向步长
  const ROW_GAP = 180; // 链与链的行距
  const LABEL_W = 260; // 链名卡片到首节点的横向间隔
  const nodes: (AttackRFNode | ChainLabelRFNode)[] = [];
  const edges: Edge[] = [];

  details.forEach((chain, ri) => {
    const y = ri * ROW_GAP;
    nodes.push({
      id: `label:${chain.id}`,
      type: "chainLabel",
      position: { x: 0, y },
      deletable: false,
      draggable: false,
      data: {
        name: chain.name,
        status: chain.status,
        count: chain.nodes.length,
        chainId: chain.id,
      },
    });
    chain.nodes.forEach((n, ni) => {
      nodes.push({
        id: n.id,
        type: "attack",
        position: { x: LABEL_W + ni * X_GAP, y },
        deletable: false,
        draggable: false,
        data: {
          title: n.title,
          node_type: n.node_type,
          hasFlow: Boolean(n.flow_id),
          target: nodeTarget(n.detail),
          severity: typeof n.detail.severity === "string" ? n.detail.severity : undefined,
        },
      });
    });
    for (const e of chain.edges) {
      if (!e.from || !e.to) continue;
      edges.push({
        id: `${e.from}->${e.to}`,
        source: e.from,
        target: e.to,
        label: e.label || undefined,
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed },
        labelShowBg: true,
        labelBgPadding: [4, 2],
        labelBgStyle: { fill: "var(--bg-secondary)" },
        labelBgBorderRadius: 3,
        style: { strokeWidth: 2 },
      });
    }
  });

  interface XMember {
    chainIdx: number;
    x: number;
    id: string;
  }
  /** 跨链关联通用逻辑：按 key 分组成员，同 key 出现在 ≥2 条链 → 按链序连虚线。
      linked 记录已连过的节点对，同一对节点不重复连（同流量优先于同目标） */
  const crossLink = (
    groups: Map<string, XMember[]>,
    prefix: string,
    label: string,
    linked: Set<string>
  ) => {
    for (const [key, members] of groups) {
      if (new Set(members.map((m) => m.chainIdx)).size < 2) continue;
      members.sort((a, b) => a.chainIdx - b.chainIdx || a.x - b.x);
      for (let i = 1; i < members.length; i++) {
        const a = members[i - 1];
        const b = members[i];
        if (a.chainIdx === b.chainIdx) continue; // 同链内不连
        const pair = `${a.id}->${b.id}`;
        if (linked.has(pair)) continue;
        linked.add(pair);
        edges.push({
          id: `${prefix}:${key}:${pair}`,
          source: a.id,
          target: b.id,
          label,
          type: "smoothstep",
          markerEnd: { type: MarkerType.ArrowClosed, color: cssColor("--accent-purple", "#bc8cff") },
          labelShowBg: true,
          labelBgPadding: [4, 2],
          labelBgStyle: { fill: "var(--bg-secondary)", color: "var(--accent-purple)" },
          labelBgBorderRadius: 3,
          style: {
            stroke: "var(--accent-purple)",
            strokeWidth: 1.5,
            strokeDasharray: "5 4",
          },
        });
      }
    }
  };

  // 同流量衔接：同 flow_id（同一证据流量出现在多条链）
  const byFlow = new Map<string, XMember[]>();
  details.forEach((chain, ci) => {
    chain.nodes.forEach((n, ni) => {
      if (!n.flow_id) return;
      const list = byFlow.get(n.flow_id) ?? [];
      list.push({ chainIdx: ci, x: LABEL_W + ni * X_GAP, id: n.id });
      byFlow.set(n.flow_id, list);
    });
  });

  // 同目标关联：不同链打过同一入口（host + path 相同）——
  // 没有共享证据包、但打的是同一目标的链也能纵向看出来
  const byUrl = new Map<string, XMember[]>();
  details.forEach((chain, ci) => {
    chain.nodes.forEach((n, ni) => {
      const t = nodeTarget(n.detail);
      if (!t) return;
      const list = byUrl.get(t) ?? [];
      list.push({ chainIdx: ci, x: LABEL_W + ni * X_GAP, id: n.id });
      byUrl.set(t, list);
    });
  });

  const linked = new Set<string>();
  crossLink(byFlow, "xlink", "同流量", linked);
  crossLink(byUrl, "xurl", "同目标", linked);

  return { nodes, edges };
}

export function AttackChainView() {
  const { currentProjectId } = useProject();
  const navigate = useNavigate();

  const [viewMode, setViewMode] = useState<"single" | "overview">("single");
  const [chains, setChains] = useState<AttackChainSummary[]>([]);
  const [activeChainId, setActiveChainId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AttackChainDetail | null>(null);
  const [overview, setOverview] = useState<AttackChainDetail[]>([]);
  const [loading, setLoading] = useState(false);
  const [nodes, setNodes, onNodesChange] = useNodesState<AttackRFNode | ChainLabelRFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const activeChain = chains.find((c) => c.id === activeChainId) ?? null;
  // 抽屉数据源：单链取 detail，总览按节点 id 反查所属链（节点 id 全局唯一）
  const selectedDetail =
    viewMode === "overview"
      ? (overview.find((c) => c.nodes.some((n) => n.id === selectedNodeId)) ?? null)
      : detail;
  const selectedNode = selectedDetail?.nodes.find((n) => n.id === selectedNodeId) ?? null;

  const loadChains = useCallback(
    async (preferId?: string) => {
      if (!currentProjectId) {
        setChains([]);
        setActiveChainId(null);
        return;
      }
      setLoading(true);
      try {
        const list = await listAttackChains(currentProjectId);
        setChains(list);
        setActiveChainId((prev) => {
          const target = preferId ?? prev;
          if (target && list.some((c) => c.id === target)) return target;
          return list.length > 0 ? list[0].id : null;
        });
      } catch (e) {
        alert(`加载攻击链失败：${e instanceof Error ? e.message : String(e)}`);
      } finally {
        setLoading(false);
      }
    },
    [currentProjectId]
  );

  const loadDetail = useCallback(async (chainId: string) => {
    setLoading(true);
    try {
      const d = await getAttackChain(chainId);
      setDetail(d);
      const { nodes: rfNodes, edges: rfEdges } = detailToFlow(d);
      setNodes(rfNodes);
      setEdges(rfEdges);
      setSelectedNodeId(null);
    } catch (e) {
      alert(`加载链详情失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [setNodes, setEdges]);

  const loadOverview = useCallback(async () => {
    if (!currentProjectId) return;
    setLoading(true);
    try {
      const list = await listAttackChains(currentProjectId);
      setChains(list);
      const details = await Promise.all(list.map((c) => getAttackChain(c.id)));
      setOverview(details);
      const { nodes: rfNodes, edges: rfEdges } = buildOverviewFlow(details);
      setNodes(rfNodes);
      setEdges(rfEdges);
      setSelectedNodeId(null);
    } catch (e) {
      alert(`加载总览失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [currentProjectId, setNodes, setEdges]);

  useEffect(() => {
    loadChains();
  }, [loadChains]);

  useEffect(() => {
    if (viewMode !== "single") return; // 总览模式画布由 loadOverview 接管
    if (activeChainId) {
      loadDetail(activeChainId);
    } else {
      setDetail(null);
      setNodes([]);
      setEdges([]);
    }
  }, [activeChainId, viewMode, loadDetail, setNodes, setEdges]);

  useEffect(() => {
    if (viewMode === "overview") {
      loadOverview();
    }
  }, [viewMode, loadOverview]);

  /** 拖拽结束持久化坐标（只存被拖的那个节点；总览模式不可拖，不会触发） */
  const handleNodeDragStop = useCallback(
    (_: unknown, node: AttackRFNode | ChainLabelRFNode) => {
      if (!activeChainId || node.type !== "attack") return;
      saveNodePosition(activeChainId, node.id, node.position.x, node.position.y).catch((e) =>
        alert(`保存坐标失败：${e instanceof Error ? e.message : String(e)}`)
      );
    },
    [activeChainId]
  );

  /** 节点点击：总览里点链名卡片切进该链；普通节点开抽屉 */
  const handleNodeClick = useCallback((_: unknown, node: AttackRFNode | ChainLabelRFNode) => {
    if (node.type === "chainLabel") {
      setViewMode("single");
      setActiveChainId(node.data.chainId);
      return;
    }
    setSelectedNodeId(node.id);
  }, []);

  /** 连线 → 可选填依据标签 → 加边 */
  const handleConnect = useCallback(
    async (conn: Connection) => {
      if (!activeChainId || !conn.source || !conn.target) return;
      const label = window.prompt("连边依据（可留空）", "") ?? null;
      if (label === null) return; // 用户取消
      try {
        await addChainEdge(activeChainId, conn.source, conn.target, label.trim());
        setEdges((prev) => {
          const id = `${conn.source}->${conn.target}`;
          if (prev.some((e) => e.id === id)) return prev;
          return [
            ...prev,
            {
              id,
              source: conn.source!,
              target: conn.target!,
              label: label.trim() || undefined,
              type: "smoothstep",
              markerEnd: { type: MarkerType.ArrowClosed },
              labelShowBg: true,
              labelBgPadding: [4, 2],
              labelBgStyle: { fill: "var(--bg-secondary)" },
              labelBgBorderRadius: 3,
              style: { strokeWidth: 2 },
            },
          ];
        });
      } catch (e) {
        alert(`加边失败：${e instanceof Error ? e.message : String(e)}`);
      }
    },
    [activeChainId, setEdges]
  );

  /** 键盘删除选中的边（节点已设 deletable:false 不会被删） */
  const handleEdgesDelete = useCallback(
    (deleted: RFEdge[]) => {
      if (!activeChainId || deleted.length === 0) return;
      Promise.all(
        deleted.map((e) => deleteChainEdge(activeChainId, e.source, e.target))
      ).catch((e) => {
        alert(`删边失败：${e instanceof Error ? e.message : String(e)}`);
        if (activeChainId) loadDetail(activeChainId);
      });
    },
    [activeChainId, loadDetail]
  );

  /** 确认 / 撤回链 */
  const handleToggleStatus = useCallback(async () => {
    if (!activeChain) return;
    const next = activeChain.status === "confirmed" ? "draft" : "confirmed";
    try {
      await updateChainStatus(activeChain.id, next);
      setChains((prev) => prev.map((c) => (c.id === activeChain.id ? { ...c, status: next } : c)));
      setDetail((prev) => (prev && prev.id === activeChain.id ? { ...prev, status: next } : prev));
    } catch (e) {
      alert(`更新状态失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }, [activeChain]);

  const handleRefresh = useCallback(() => {
    if (viewMode === "overview") loadOverview();
    else loadChains();
  }, [viewMode, loadOverview, loadChains]);

  const selectedEdges = useMemo(() => {
    if (!selectedNode || !selectedDetail) return [];
    return selectedDetail.edges.filter(
      (e) => e.from === selectedNode.id || e.to === selectedNode.id
    );
  }, [selectedNode, selectedDetail]);

  const statusMeta = activeChain ? STATUS_META[activeChain.status] ?? STATUS_META.draft : null;

  if (!currentProjectId) {
    return (
      <div className="flex flex-col h-full">
        <div className="view-header">
          <div className="view-title">攻击链</div>
        </div>
        <EmptyState icon={<Workflow size={32} />} message="请先选择项目" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="view-header">
        <div className="view-title">攻击链</div>
        <div className="view-actions">
          <div className="ac-mode-toggle">
            <button
              className={viewMode === "single" ? "active" : ""}
              onClick={() => setViewMode("single")}
            >
              单链
            </button>
            <button
              className={viewMode === "overview" ? "active" : ""}
              onClick={() => setViewMode("overview")}
            >
              总览
            </button>
          </div>
          {viewMode === "single" && (
            <>
              <select
                className="input ac-chain-select"
                value={activeChainId ?? ""}
                onChange={(e) => setActiveChainId(e.target.value || null)}
              >
                {chains.length === 0 && <option value="">（暂无攻击链）</option>}
                {chains.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name || "未命名链"}（{c.node_count} 节点）
                  </option>
                ))}
              </select>
              {statusMeta && activeChain && (
                <span className="ac-status-badge" style={{ color: statusMeta.color, borderColor: statusMeta.color }}>
                  {statusMeta.label}
                </span>
              )}
              {activeChain && (
                <button
                  className="btn btn-sm"
                  onClick={handleToggleStatus}
                  title={activeChain.status === "confirmed" ? "撤回为草稿" : "确认为最终版（进报告）"}
                >
                  <CheckCircle2 size={12} />
                  {activeChain.status === "confirmed" ? "撤回确认" : "确认此链"}
                </button>
              )}
            </>
          )}
          <button className="btn btn-sm" onClick={handleRefresh} title="刷新">
            <RefreshCw size={12} />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="ac-loading">
          <Spinner size={20} />
        </div>
      ) : chains.length === 0 ? (
        <EmptyState
          icon={<Workflow size={32} />}
          message="暂无攻击链——AI 测试任务结束后会自动从操作步骤构建，也可在对话中让 Agent 用 update_attack_chain 补链"
        />
      ) : (
        <div className="ac-body">
          <div className="ac-canvas">
            <ReactFlow
              key={viewMode}
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              nodeTypes={nodeTypes}
              onNodeDragStop={viewMode === "single" ? handleNodeDragStop : undefined}
              onConnect={viewMode === "single" ? handleConnect : undefined}
              onEdgesDelete={viewMode === "single" ? handleEdgesDelete : undefined}
              onNodeClick={handleNodeClick}
              onPaneClick={() => setSelectedNodeId(null)}
              nodesDraggable={viewMode === "single"}
              nodesConnectable={viewMode === "single"}
              fitView
              fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
              minZoom={0.5}
              maxZoom={1.75}
              deleteKeyCode={viewMode === "single" ? ["Backspace", "Delete"] : null}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={18} size={1} />
              <Controls showInteractive={false} />
              <MiniMap
                pannable
                zoomable
                nodeStrokeWidth={3}
                className="ac-minimap"
                nodeColor={(n) => {
                  if (n.type === "chainLabel") return cssColor("--bg-tertiary", "#1c2128");
                  const t = (n.data as AttackNodeCardData).node_type;
                  if (t === "vuln") return cssColor("--accent-red", "#f85149");
                  if (t === "credential") return cssColor("--accent-orange", "#db6d28");
                  if (t === "action") return cssColor("--accent-blue", "#58a6ff");
                  if (t === "loot") return cssColor("--accent-green", "#3fb950");
                  return cssColor("--text-muted", "#6e7681");
                }}
              />
              <Panel position="bottom-left" className="ac-legend">
                <div className="ac-legend-row">
                  {(["asset", "vuln", "credential", "action", "loot"] as const).map((t) => {
                    const Icon = NODE_ICONS[t];
                    return (
                      <span key={t} className={`ac-legend-item ${t}`}>
                        <Icon size={10} />
                        {ATTACK_NODE_META[t].label}
                      </span>
                    );
                  })}
                </div>
                <div className="ac-legend-row">
                  {viewMode === "overview"
                    ? "虚线 = 跨链关联（同流量 / 同目标）· 点击链名卡片进入该链编辑"
                    : "横向为主线（最长攻击路径），分支上下展开 · 拖节点边缘可连线"}
                </div>
              </Panel>
            </ReactFlow>
            {viewMode === "single" && detail && detail.nodes.length > 0 && (
              <div className="ac-hint">
                拖动节点调整布局 · 拖节点边缘连线（可填依据） · 选中边按 Delete 删除
              </div>
            )}
          </div>

          {selectedNode && (
            <div className="ac-drawer">
              <div className="ac-drawer-header">
                <span>节点详情</span>
                <button className="ac-drawer-close" onClick={() => setSelectedNodeId(null)}>
                  <X size={14} />
                </button>
              </div>
              <div className="ac-drawer-body">
                <div className="ac-drawer-title">{selectedNode.title}</div>
                {viewMode === "overview" && selectedDetail && (
                  <div className="ac-drawer-section">
                    <div className="ac-drawer-kv">
                      <span className="ac-drawer-key">所属链</span>
                      <span className="ac-drawer-value">{selectedDetail.name || "未命名链"}</span>
                    </div>
                  </div>
                )}
                {Object.entries(selectedNode.detail)
                  .filter(([k, v]) => !k.startsWith("_") && v !== "" && v != null).length >
                  0 && (
                  <div className="ac-drawer-section">
                    {Object.entries(selectedNode.detail)
                      .filter(([k, v]) => !k.startsWith("_") && v !== "" && v != null)
                      .map(([k, v]) => (
                        <div key={k} className="ac-drawer-kv">
                          <span className="ac-drawer-key">{k}</span>
                          <span className="ac-drawer-value">
                            {typeof v === "object" ? JSON.stringify(v) : String(v)}
                          </span>
                        </div>
                      ))}
                  </div>
                )}
                <div className="ac-drawer-section">
                  {selectedNode.source_step != null && (
                    <div className="ac-drawer-kv">
                      <span className="ac-drawer-key">来源步骤</span>
                      <span className="ac-drawer-value">#{selectedNode.source_step}</span>
                    </div>
                  )}
                  {selectedNode.flow_id && (
                    <div className="ac-drawer-kv">
                      <span className="ac-drawer-key">证据流量</span>
                      <button
                        className="ac-flow-link"
                        onClick={() => navigate(`/flows/${selectedNode.flow_id}`)}
                      >
                        查看数据包 <ExternalLink size={11} />
                      </button>
                    </div>
                  )}
                </div>
                {selectedEdges.length > 0 && (
                  <div className="ac-drawer-section">
                    <div className="ac-drawer-label">关联连边</div>
                    {selectedEdges.map((e) => {
                      const dir = e.from === selectedNode.id ? "→" : "←";
                      const otherId = e.from === selectedNode.id ? e.to : e.from;
                      const other = selectedDetail?.nodes.find((n) => n.id === otherId);
                      return (
                        <div key={`${e.from}->${e.to}`} className="ac-drawer-kv">
                          <span className="ac-drawer-value">
                            {dir} {other?.title || otherId.slice(0, 12)}
                            {e.label ? `（${e.label}）` : ""}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
                <div className="ac-drawer-note">
                  节点由 AI 测试过程自动抽取，凭据已脱敏；补链/改标题请在 AI 对话中让 Agent 调用 update_attack_chain。
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
