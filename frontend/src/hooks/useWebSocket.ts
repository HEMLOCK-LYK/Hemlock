import { useEffect, useRef, useState, useCallback } from "react";
import type { WSEvent } from "../types";
import { WS_BASE } from "../api/client";

type EventCallback = (event: WSEvent) => void;

/**
 * WebSocket 实时事件连接 hook。
 * 自动重连、心跳处理。
 * 支持 Tauri 生产模式（绝对 URL）和开发模式（相对 URL）。
 *
 * 关键：使用 closedManually 标志防止组件卸载后的重连。
 * React StrictMode 开发模式下会 mount→unmount→remount，
 * 如果不阻止卸载后的重连，会导致多个 WebSocket 连接并存，
 * 每条事件被重复投递（如思维链 delta 翻倍）。
 */
export function useWebSocket(onEvent: EventCallback) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  // 标记连接是否为主动关闭（组件卸载时设置）
  const closedManuallyRef = useRef(false);

  const connect = useCallback(() => {
    closedManuallyRef.current = false;
    const wsUrl = `${WS_BASE}/events`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
      };

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data) as WSEvent;
          onEventRef.current(data);
        } catch {
          // ignore parse errors
        }
      };

      ws.onclose = () => {
        setConnected(false);
        // 只在非主动关闭时重连（避免组件卸载后残留连接）
        if (!closedManuallyRef.current) {
          reconnectTimer.current = setTimeout(() => connect(), 3000);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      setConnected(false);
      if (!closedManuallyRef.current) {
        reconnectTimer.current = setTimeout(() => connect(), 3000);
      }
    }
  }, []);

  useEffect(() => {
    connect();
    return () => {
      // 标记为主动关闭，阻止 onclose 中的重连逻辑
      closedManuallyRef.current = true;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // 移除 onclose 监听器，防止触发重连
        wsRef.current.close();
      }
      wsRef.current = null;
    };
  }, [connect]);

  return { connected };
}
