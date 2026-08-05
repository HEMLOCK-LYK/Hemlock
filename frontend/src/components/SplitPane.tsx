import { useState, useRef, useCallback, useEffect } from "react";
import type { ReactNode } from "react";

/**
 * SplitPane — 可拖拽调节的分隔面板组件
 *
 * 支持水平和垂直两种方向，拖拽分隔条调节两侧面板大小。
 * Burp Suite 风格：细分隔条，hover 高亮。
 *
 * Props:
 * - direction: "horizontal"（左右分割） | "vertical"（上下分割）
 * - initialSplit: 初始分割比例（0-1），默认 0.5
 * - minSize: 最小面板尺寸（px），默认 100
 * - first: 第一个面板内容
 * - second: 第二个面板内容
 * - storageKey: 持久化分割比例到 localStorage 的 key
 */

interface SplitPaneProps {
  direction: "horizontal" | "vertical";
  initialSplit?: number;
  minSize?: number;
  first: ReactNode;
  second: ReactNode;
  storageKey?: string;
  firstHeader?: ReactNode;
  secondHeader?: ReactNode;
}

export function SplitPane({
  direction,
  initialSplit = 0.5,
  minSize = 100,
  first,
  second,
  storageKey,
  firstHeader,
  secondHeader,
}: SplitPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [split, setSplit] = useState(() => {
    if (storageKey) {
      try {
        const saved = localStorage.getItem(storageKey);
        if (saved) {
          const v = parseFloat(saved);
          if (!isNaN(v) && v > 0.05 && v < 0.95) {
            // 根据容器实际尺寸约束比例（防止 localStorage 中的值导致面板过小）
            // 此时无法获取容器尺寸，使用安全范围 0.15-0.85
            return Math.max(0.15, Math.min(0.85, v));
          }
        }
      } catch { /* ignore */ }
    }
    return initialSplit;
  });
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{ startPos: number; startSize: number; containerSize: number } | null>(null);

  // 挂载后根据实际容器尺寸约束分割比例（防止 localStorage 中的值导致面板过小）
  const hasAdjusted = useRef(false);
  useEffect(() => {
    if (hasAdjusted.current) return;
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const containerSize = direction === "horizontal" ? rect.width : rect.height;
    if (containerSize === 0) return; // 容器还没渲染好
    hasAdjusted.current = true;
    const minRatio = minSize / containerSize;
    const maxRatio = 1 - minRatio;
    if (split < minRatio || split > maxRatio) {
      setSplit(Math.max(minRatio, Math.min(maxRatio, split)));
    }
  }, [split, direction, minSize]);

  // 持久化
  useEffect(() => {
    if (storageKey) {
      try {
        localStorage.setItem(storageKey, String(split));
      } catch { /* ignore */ }
    }
  }, [split, storageKey]);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    dragRef.current = {
      startPos: direction === "horizontal" ? e.clientX : e.clientY,
      startSize: split,
      containerSize: direction === "horizontal" ? rect.width : rect.height,
    };
    setDragging(true);
  }, [direction, split]);

  useEffect(() => {
    if (!dragging) return;

    const onMouseMove = (e: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const currentPos = direction === "horizontal" ? e.clientX : e.clientY;
      const delta = currentPos - d.startPos;
      const deltaRatio = delta / d.containerSize;
      let newSplit = d.startSize + deltaRatio;
      // 限制范围
      const minRatio = minSize / d.containerSize;
      const maxRatio = 1 - minRatio;
      newSplit = Math.max(minRatio, Math.min(maxRatio, newSplit));
      setSplit(newSplit);
    };

    const onMouseUp = () => {
      dragRef.current = null;
      setDragging(false);
    };

    document.body.style.cursor = direction === "horizontal" ? "col-resize" : "row-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    return () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };
  }, [dragging, direction, minSize]);

  const isHorizontal = direction === "horizontal";

  return (
    <div
      ref={containerRef}
      style={{
        display: "flex",
        flexDirection: isHorizontal ? "row" : "column",
        height: "100%",
        width: "100%",
        overflow: "hidden",
        position: "relative",
      }}
    >
      {/* 第一个面板 */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          minHeight: 0,
          minWidth: 0,
          ...(isHorizontal
            ? { width: `${split * 100}%` }
            : { height: `${split * 100}%` }),
        }}
      >
        {firstHeader && (
          <div
            style={{
              flexShrink: 0,
              borderBottom: isHorizontal ? "none" : "1px solid var(--border-default)",
              borderRight: isHorizontal ? "1px solid var(--border-default)" : "none",
              background: "var(--bg-secondary)",
            }}
          >
            {firstHeader}
          </div>
        )}
        <div style={{ flex: 1, overflow: "hidden", display: "flex", minHeight: 0, minWidth: 0 }}>
          {first}
        </div>
      </div>

      {/* 分隔条 — 7px 宽命中区域，中心 1px 视觉线 */}
      <div
        onMouseDown={onMouseDown}
        style={{
          flexShrink: 0,
          cursor: isHorizontal ? "col-resize" : "row-resize",
          background: "transparent",
          position: "relative",
          zIndex: 5,
          ...(isHorizontal
            ? { width: 7, height: "100%" }
            : { height: 7, width: "100%" }),
        }}
        className="split-pane-divider"
      >
        {/* 中心视觉线 */}
        <div
          style={{
            position: "absolute",
            background: dragging ? "var(--accent-blue)" : "var(--border-default)",
            transition: dragging ? "none" : "background 0.15s",
            ...(isHorizontal
              ? { left: 3, top: 0, bottom: 0, width: 1 }
              : { top: 3, left: 0, right: 0, height: 1 }),
          }}
          className="split-pane-divider-line"
        />
      </div>

      {/* 第二个面板 */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          flex: 1,
          minHeight: 0,
          minWidth: 0,
        }}
      >
        {secondHeader && (
          <div
            style={{
              flexShrink: 0,
              borderBottom: isHorizontal ? "none" : "1px solid var(--border-default)",
              background: "var(--bg-secondary)",
            }}
          >
            {secondHeader}
          </div>
        )}
        <div style={{ flex: 1, overflow: "hidden", display: "flex", minHeight: 0, minWidth: 0 }}>
          {second}
        </div>
      </div>

      <style>{`
        .split-pane-divider:hover .split-pane-divider-line {
          background: var(--accent-blue) !important;
        }
      `}</style>
    </div>
  );
}

export default SplitPane;
