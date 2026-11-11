import { useEffect, useState } from "react";

/**
 * 输入框引导提示开关 — 全局单例。
 *
 * 用户要求：关闭后**所有**输入框的使用引导话术都消失（AI 聊天框、筛选器、
 * 流量详情搜索、协作目标等），只保留最短的字段提示；不只是单个输入框。
 *
 * 由 App 启动时从 /api/settings 同步一次，设置页保存时即时更新。
 * 组件用 useHints() 订阅，开关切换全应用即时生效。
 */

let hintsEnabled = true;
const listeners = new Set<(v: boolean) => void>();

export function setHintsEnabled(v: boolean) {
  if (v === hintsEnabled) return;
  hintsEnabled = v;
  listeners.forEach((l) => l(v));
}

export function getHintsEnabled() {
  return hintsEnabled;
}

/** 订阅引导开关状态（全应用即时生效） */
export function useHints(): boolean {
  const [v, setV] = useState(hintsEnabled);
  useEffect(() => {
    listeners.add(setV);
    return () => {
      listeners.delete(setV);
    };
  }, []);
  return v;
}
