import client from "./client";

/** 攻击链节点类型：asset 资产 / vuln 漏洞 / credential 凭据 / action 动作 / loot 获取 */
export type AttackNodeType = "asset" | "vuln" | "credential" | "action" | "loot";

export interface AttackNodeData {
  id: string;
  node_type: AttackNodeType | string;
  title: string;
  detail: Record<string, unknown>;
  source_step: number | null;
  flow_id: string | null;
  x: number;
  y: number;
}

export interface AttackChainSummary {
  id: string;
  task_id: string | null;
  name: string;
  status: "draft" | "confirmed" | "failed" | string;
  node_count: number;
  updated_at: string | null;
}

export interface AttackChainDetail extends AttackChainSummary {
  nodes: AttackNodeData[];
  edges: { from: string; to: string; label: string }[];
}

/** 列出攻击链（按项目隔离） */
export async function listAttackChains(projectId?: string): Promise<AttackChainSummary[]> {
  const { data } = await client.get<AttackChainSummary[]>("/attack-chain", {
    params: projectId ? { project_id: projectId } : {},
  });
  return data;
}

/** 获取链详情（节点 + 边） */
export async function getAttackChain(chainId: string): Promise<AttackChainDetail> {
  const { data } = await client.get<AttackChainDetail>(`/attack-chain/${chainId}`);
  return data;
}

/** 拖拽节点后持久化坐标 */
export async function saveNodePosition(
  chainId: string,
  nodeId: string,
  x: number,
  y: number
): Promise<void> {
  await client.put(`/attack-chain/${chainId}/nodes/${nodeId}/position`, { x, y });
}

/** 画布连线 → 加边（同向同边幂等） */
export async function addChainEdge(
  chainId: string,
  source: string,
  target: string,
  label = ""
): Promise<void> {
  await client.post(`/attack-chain/${chainId}/edges`, { source, target, label });
}

/** 删除一条边（按方向匹配） */
export async function deleteChainEdge(
  chainId: string,
  source: string,
  target: string
): Promise<void> {
  await client.delete(`/attack-chain/${chainId}/edges`, {
    data: { source, target },
  });
}

/** 更新链状态：draft / confirmed / failed */
export async function updateChainStatus(
  chainId: string,
  status: string
): Promise<void> {
  await client.put(`/attack-chain/${chainId}/status`, { status });
}

/** 节点类型的中文标签与配色（与 index.css 的 .ac-node-* 配合） */
export const ATTACK_NODE_META: Record<string, { label: string; cssClass: string }> = {
  asset: { label: "资产", cssClass: "asset" },
  vuln: { label: "漏洞", cssClass: "vuln" },
  credential: { label: "凭据", cssClass: "credential" },
  action: { label: "动作", cssClass: "action" },
  loot: { label: "获取", cssClass: "loot" },
};
