import {
  createContext,
  useContext,
  useState,
  useCallback,
  type ReactNode,
} from "react";

/**
 * 跨组件共享的请求数据包，用于「发送到 Repeater / Intruder / AI 助手」。
 *
 * FlowList 右键菜单调用 sendToRepeater / sendToIntruder / sendToAI 写入数据，
 * Repeater / Intruder / AgentView 组件挂载时读取 pendingPacket 并处理。
 */

export interface PendingPacket {
  method: string;
  url: string;
  headers: string;
  body: string;
  // 可选的响应数据（发送给 AI 分析时附带）
  respStatus?: number;
  respHeaders?: string;
  respBody?: string;
}

interface PacketContextValue {
  /** 待接收的请求数据包，目标组件读取后应调用 consume 清空 */
  pendingPacket: PendingPacket | null;
  /** 目标组件标识：repeater / intruder / ai */
  target: "repeater" | "intruder" | "ai" | null;
  /** 发送数据包到 Repeater */
  sendToRepeater: (packet: PendingPacket) => void;
  /** 发送数据包到 Intruder */
  sendToIntruder: (packet: PendingPacket) => void;
  /** 发送数据包到 AI 助手 */
  sendToAI: (packet: PendingPacket) => void;
  /** 消费后清空（防止重复加载） */
  consume: () => void;
}

const PacketContext = createContext<PacketContextValue | null>(null);

export function PacketProvider({ children }: { children: ReactNode }) {
  const [pendingPacket, setPendingPacket] = useState<PendingPacket | null>(null);
  const [target, setTarget] = useState<"repeater" | "intruder" | "ai" | null>(null);

  const sendToRepeater = useCallback((packet: PendingPacket) => {
    setPendingPacket(packet);
    setTarget("repeater");
  }, []);

  const sendToIntruder = useCallback((packet: PendingPacket) => {
    setPendingPacket(packet);
    setTarget("intruder");
  }, []);

  const sendToAI = useCallback((packet: PendingPacket) => {
    setPendingPacket(packet);
    setTarget("ai");
  }, []);

  const consume = useCallback(() => {
    setPendingPacket(null);
    setTarget(null);
  }, []);

  return (
    <PacketContext.Provider
      value={{ pendingPacket, target, sendToRepeater, sendToIntruder, sendToAI, consume }}
    >
      {children}
    </PacketContext.Provider>
  );
}

export function usePacket(): PacketContextValue {
  const ctx = useContext(PacketContext);
  if (!ctx) {
    throw new Error("usePacket 必须在 PacketProvider 内部使用");
  }
  return ctx;
}
