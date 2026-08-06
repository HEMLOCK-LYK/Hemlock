import { useEffect, useState } from "react";
import { useWebSocket } from "../hooks/useWebSocket";
import type { WSEvent } from "../types";

export function StatusBar() {
  const [flowCount, setFlowCount] = useState(0);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);

  const handleEvent = (_event: WSEvent) => {
    if (_event.event === "flow") {
      setFlowCount((c) => c + 1);
    }
  };

  const { connected } = useWebSocket(handleEvent);

  // 健康检查
  useEffect(() => {
    const checkHealth = async () => {
      try {
        const resp = await fetch("/health");
        if (resp.ok) {
          setBackendOnline(true);
          return;
        }
      } catch {
        // ignore
      }
      setBackendOnline(false);
    };
    checkHealth();
    const timer = setInterval(checkHealth, 10000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="status-bar">
      <div className="status-bar-item">
        <span
          className={`status-dot ${backendOnline ? "online" : backendOnline === false ? "offline" : "unknown"}`}
        />
        后端: {backendOnline ? "在线" : backendOnline === false ? "离线" : "检测中"}
      </div>
      <div className="status-bar-item">
        <span
          className={`status-dot ${connected ? "online" : "offline"}`}
        />
        WebSocket: {connected ? "已连接" : "断开"}
      </div>
      <div className="status-bar-item">
        流量: {flowCount}
      </div>
      <div style={{ flex: 1 }} />
      <div className="status-bar-item">
        Hemlock v0.3.1
      </div>
    </div>
  );
}
