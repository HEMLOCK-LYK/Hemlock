import { useState, useRef, useCallback, useEffect } from "react";
import type { ReactNode } from "react";
import { X, Maximize2, Minimize2 } from "lucide-react";

/**
 * FlowWindow — 可拖拽、可关闭的浮动小窗组件
 *
 * Burp Suite 风格：双击数据包后弹出的独立详情窗口。
 * - 标题栏可拖拽移动窗口
 * - 右上角关闭/最大化按钮
 * - 可调整窗口大小（右下角拖拽手柄）
 * - 点击外部遮罩不关闭（需手动关闭）
 *
 * Props:
 * - title: 窗口标题
 * - onClose: 关闭回调
 * - children: 窗口内容
 * - initialX / initialY: 初始位置
 * - initialWidth / initialHeight: 初始尺寸
 */

interface FlowWindowProps {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  initialX?: number;
  initialY?: number;
  initialWidth?: number;
  initialHeight?: number;
}

export function FlowWindow({
  title,
  onClose,
  children,
  initialX,
  initialY,
  initialWidth = 900,
  initialHeight = 600,
}: FlowWindowProps) {
  const windowRef = useRef<HTMLDivElement>(null);

  // 窗口位置和尺寸
  const [pos, setPos] = useState(() => ({
    x: initialX ?? Math.max(20, (window.innerWidth - initialWidth) / 2),
    y: initialY ?? Math.max(20, (window.innerHeight - initialHeight) / 2),
  }));
  const [size, setSize] = useState({
    width: initialWidth,
    height: initialHeight,
  });
  const [maximized, setMaximized] = useState(false);

  // 拖拽状态
  const dragRef = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  // 调整大小状态
  const resizeRef = useRef<{ startX: number; startY: number; origW: number; origH: number } | null>(null);
  const [resizing, setResizing] = useState(false);

  // 拖拽标题栏
  const onTitleMouseDown = useCallback((e: React.MouseEvent) => {
    if (maximized) return;
    e.preventDefault();
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      origX: pos.x,
      origY: pos.y,
    };
    setDragging(true);
  }, [pos.x, pos.y, maximized]);

  useEffect(() => {
    if (!dragging) return;

    const onMouseMove = (e: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const deltaX = e.clientX - d.startX;
      const deltaY = e.clientY - d.startY;
      let newX = d.origX + deltaX;
      let newY = d.origY + deltaY;
      // 限制在视口范围内
      newX = Math.max(0, Math.min(newX, window.innerWidth - 100));
      newY = Math.max(0, Math.min(newY, window.innerHeight - 40));
      setPos({ x: newX, y: newY });
    };

    const onMouseUp = () => {
      dragRef.current = null;
      setDragging(false);
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    return () => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };
  }, [dragging]);

  // 调整大小
  const onResizeMouseDown = useCallback((e: React.MouseEvent) => {
    if (maximized) return;
    e.preventDefault();
    e.stopPropagation();
    resizeRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      origW: size.width,
      origH: size.height,
    };
    setResizing(true);
  }, [size.width, size.height, maximized]);

  useEffect(() => {
    if (!resizing) return;

    const onMouseMove = (e: MouseEvent) => {
      const r = resizeRef.current;
      if (!r) return;
      const deltaW = e.clientX - r.startX;
      const deltaH = e.clientY - r.startY;
      const newW = Math.max(400, Math.min(r.origW + deltaW, window.innerWidth - pos.x - 10));
      const newH = Math.max(300, Math.min(r.origH + deltaH, window.innerHeight - pos.y - 10));
      setSize({ width: newW, height: newH });
    };

    const onMouseUp = () => {
      resizeRef.current = null;
      setResizing(false);
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    return () => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };
  }, [resizing, pos.x, pos.y]);

  // Esc 关闭
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", handleEsc);
    return () => document.removeEventListener("keydown", handleEsc);
  }, [onClose]);

  const windowStyle: React.CSSProperties = maximized
    ? {
        position: "fixed",
        top: 0,
        left: 0,
        width: "100vw",
        height: "100vh",
        zIndex: 9999,
        display: "flex",
        flexDirection: "column",
        background: "var(--bg-primary)",
        boxShadow: "0 20px 60px rgba(0,0,0,0.4)",
      }
    : {
        position: "fixed",
        top: pos.y,
        left: pos.x,
        width: size.width,
        height: size.height,
        zIndex: 9999,
        display: "flex",
        flexDirection: "column",
        background: "var(--bg-primary)",
        borderRadius: 6,
        boxShadow: "0 12px 40px rgba(0,0,0,0.35)",
        overflow: "hidden",
      };

  return (
    <div ref={windowRef} style={windowStyle}>
      {/* 标题栏 */}
      <div
        onMouseDown={onTitleMouseDown}
        onDoubleClick={() => setMaximized((v) => !v)}
        style={{
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "6px 12px",
          background: "var(--bg-secondary)",
          borderBottom: "1px solid var(--border-default)",
          cursor: maximized ? "default" : "move",
          userSelect: "none",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flex: 1 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {title}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
          <button
            onClick={() => setMaximized((v) => !v)}
            title={maximized ? "还原" : "最大化"}
            style={{
              padding: "3px",
              border: "none",
              background: "transparent",
              cursor: "pointer",
              color: "var(--text-muted)",
              display: "flex",
              alignItems: "center",
            }}
          >
            {maximized ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
          <button
            onClick={onClose}
            title="关闭 (Esc)"
            style={{
              padding: "3px",
              border: "none",
              background: "transparent",
              cursor: "pointer",
              color: "var(--text-muted)",
              display: "flex",
              alignItems: "center",
            }}
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* 内容区 */}
      <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", minHeight: 0 }}>
        {children}
      </div>

      {/* 调整大小手柄（右下角） */}
      {!maximized && (
        <div
          onMouseDown={onResizeMouseDown}
          style={{
            position: "absolute",
            bottom: 0,
            right: 0,
            width: 16,
            height: 16,
            cursor: "nwse-resize",
            background:
              "linear-gradient(135deg, transparent 50%, var(--text-muted) 50%, var(--text-muted) 60%, transparent 60%, transparent 70%, var(--text-muted) 70%, var(--text-muted) 80%, transparent 80%)",
            opacity: 0.5,
          }}
        />
      )}
    </div>
  );
}

export default FlowWindow;
