import { useRef, useMemo, useCallback, useState, useEffect } from "react";
import type { ReactNode } from "react";
import { tokenizePacket, COLOR, getPacketBodyMode } from "../utils/packetTokenize";
import type { Segment } from "../utils/packetTokenize";
import { Search, X, ChevronUp, ChevronDown } from "lucide-react";

/**
 * CodeEditor — 带语法高亮和行号的数据包编辑器/显示器
 *
 * 设计目标：Burp Suite 风格的数据包查看器
 *
 * 只读模式（默认）：
 * - 行号嵌入每行 div（flex 布局），内容 pre-wrap 自动换行
 * - 长行（如 Cookie）在容器边界自然折行，无需横向拖动
 * - 行号固定在行首，折行后不错位
 * - 支持 Ctrl+F 唤出搜索栏，高亮匹配项，上下翻页
 *
 * 可编辑模式：
 * - 独立行号栏 + 透明 textarea overlay（pre 不换行）
 * - 用于 Repeater 等需要编辑请求的场景
 */

// ===== 共享样式常量 =====
const FONT_FAMILY = "var(--font-mono)";
const FONT_SIZE = 12;
const LINE_HEIGHT = 1.6;
const PADDING_TOP = 8;
const PADDING_BOTTOM = 8;
const PADDING_H = 12;

// 只读模式：行号嵌入行内，窄列 + 小字体（Burp Suite 风格）
const GUTTER_WIDTH_READONLY = 20;
const GUTTER_FONT_READONLY = 10;

// 可编辑模式：独立行号栏
const GUTTER_WIDTH_EDITABLE = 36;

// 搜索高亮背景色
const SEARCH_HIGHLIGHT_BG = "rgba(255, 213, 0, 0.35)";
const SEARCH_HIGHLIGHT_ACTIVE_BG = "rgba(255, 145, 0, 0.55)";

interface CodeEditorProps {
  value: string;
  onChange?: (value: string) => void;
  type: "request" | "response";
  editable?: boolean;
  style?: React.CSSProperties;
  /** 外部传入的 textarea ref（可编辑模式下），用于 Intruder 等需要访问选区的场景 */
  externalTextareaRef?: React.RefObject<HTMLTextAreaElement | null>;
  /** 选区变化回调（可编辑模式下），通知父组件是否有选中文本 */
  onSelectionChange?: (hasSelection: boolean) => void;
}

/** 搜索匹配信息 */
interface SearchMatch {
  lineIndex: number;
  startCol: number;
  endCol: number;
}

export function CodeEditor({
  value,
  onChange,
  type,
  editable = false,
  style,
  externalTextareaRef,
  onSelectionChange,
}: CodeEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const preRef = useRef<HTMLDivElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // 搜索状态
  const [showSearch, setShowSearch] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [currentMatchIdx, setCurrentMatchIdx] = useState(0);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // 词法分析
  const lines = useMemo(() => tokenizePacket(value, type), [value, type]);

  // 换行策略：表单数据不换行（同 Burp Suite），JSON/HTML 等可换行
  const shouldWrap = useMemo(() => {
    const bodyMode = getPacketBodyMode(value);
    return bodyMode !== "form";
  }, [value]);

  // 行数
  const lineCount = useMemo(() => {
    return Math.max(value.split("\n").length, 1);
  }, [value]);

  // 搜索匹配结果
  const matches = useMemo((): SearchMatch[] => {
    if (!searchQuery.trim()) return [];
    const results: SearchMatch[] = [];
    const query = searchQuery.toLowerCase();
    const rawLines = value.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    for (let i = 0; i < rawLines.length; i++) {
      const line = rawLines[i].toLowerCase();
      let start = 0;
      while (true) {
        const idx = line.indexOf(query, start);
        if (idx === -1) break;
        results.push({ lineIndex: i, startCol: idx, endCol: idx + query.length });
        start = idx + 1;
      }
    }
    return results;
  }, [searchQuery, value]);

  // 当前匹配索引范围修正
  useEffect(() => {
    if (currentMatchIdx >= matches.length) {
      setCurrentMatchIdx(0);
    }
  }, [matches.length, currentMatchIdx]);

  // Ctrl+F 快捷键
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "f") {
        const container = containerRef.current;
        if (container && container.contains(document.activeElement)) {
          e.preventDefault();
          e.stopPropagation();
          setShowSearch(true);
          setTimeout(() => searchInputRef.current?.focus(), 50);
        }
      }
      if (e.key === "Escape" && showSearch) {
        setShowSearch(false);
        setSearchQuery("");
      }
    };
    const container = containerRef.current;
    if (container) {
      container.addEventListener("keydown", handleKeyDown);
      return () => container.removeEventListener("keydown", handleKeyDown);
    }
  }, [showSearch]);

  // 滚动到当前匹配项
  useEffect(() => {
    if (matches.length === 0) return;
    const match = matches[currentMatchIdx];
    if (!match) return;
    const lineHeight = FONT_SIZE * LINE_HEIGHT;
    const scrollTarget = match.lineIndex * lineHeight;
    const pre = preRef.current;
    if (pre) {
      pre.scrollTop = Math.max(0, scrollTarget - pre.clientHeight / 2);
    }
    const gutter = gutterRef.current;
    if (gutter) {
      gutter.scrollTop = pre?.scrollTop ?? 0;
    }
  }, [currentMatchIdx, matches]);

  // 构建行匹配映射（行号 → 该行上的匹配列表）
  const lineMatchMap = useMemo(() => {
    const map = new Map<number, SearchMatch[]>();
    for (const m of matches) {
      if (!map.has(m.lineIndex)) map.set(m.lineIndex, []);
      map.get(m.lineIndex)!.push(m);
    }
    return map;
  }, [matches]);

  // 判断某个 segment 在某行上是否与搜索匹配重叠，返回是否为当前激活匹配
  function getHighlightType(
    lineIdx: number,
    segStartCol: number,
    segEndCol: number
  ): "none" | "match" | "active" {
    const lineMs = lineMatchMap.get(lineIdx);
    if (!lineMs) return "none";
    for (let i = 0; i < lineMs.length; i++) {
      const m = lineMs[i];
      // 检查重叠
      if (segEndCol > m.startCol && segStartCol < m.endCol) {
        return i === currentMatchIdx ? "active" : "match";
      }
    }
    return "none";
  }

  // 渲染单个 segment（含搜索高亮拆分逻辑）
  function renderSegment(seg: Segment & { startCol: number; endCol: number }, lineIdx: number, j: number): ReactNode {
    const hlType = getHighlightType(lineIdx, seg.startCol, seg.endCol);
    let bg = "transparent";
    if (hlType === "match") bg = SEARCH_HIGHLIGHT_BG;
    else if (hlType === "active") bg = SEARCH_HIGHLIGHT_ACTIVE_BG;
    const fw = seg.bold ? "bold" : "normal";

    // 如果有搜索高亮，需要进一步拆分 segment 以精确高亮匹配部分
    if (hlType !== "none" && searchQuery) {
      const lineMs = lineMatchMap.get(lineIdx) || [];
      const parts: ReactNode[] = [];
      let cursor = seg.startCol;

      for (const m of lineMs) {
        if (m.endCol <= seg.startCol || m.startCol >= seg.endCol) continue;
        const matchStart = Math.max(m.startCol, seg.startCol);
        const matchEnd = Math.min(m.endCol, seg.endCol);

        // 匹配前的文本
        if (matchStart > cursor) {
          parts.push(
            <span key={`pre-${lineIdx}-${j}-${m.startCol}`} style={{ color: seg.color, fontWeight: fw }}>
              {seg.text.slice(cursor - seg.startCol, matchStart - seg.startCol)}
            </span>
          );
        }

        // 匹配的文本
        const isCurrent = lineMs.indexOf(m) === currentMatchIdx - lineMs.filter(x => x.lineIndex < lineIdx).reduce((acc) => acc + 1, 0) ||
          (lineMs.indexOf(m) === 0 && currentMatchIdx === 0);
        parts.push(
          <span
            key={`hl-${lineIdx}-${j}-${m.startCol}`}
            style={{
              color: seg.color,
              fontWeight: fw,
              backgroundColor: isCurrent ? SEARCH_HIGHLIGHT_ACTIVE_BG : SEARCH_HIGHLIGHT_BG,
              borderRadius: 2,
            }}
          >
            {seg.text.slice(matchStart - seg.startCol, matchEnd - seg.startCol)}
          </span>
        );
        cursor = matchEnd;
      }

      // 匹配后的剩余文本
      if (cursor < seg.endCol) {
        parts.push(
          <span key={`post-${lineIdx}-${j}`} style={{ color: seg.color, fontWeight: fw }}>
            {seg.text.slice(cursor - seg.startCol)}
          </span>
        );
      }

      return <span key={`seg-${lineIdx}-${j}`}>{parts}</span>;
    }

    return (
      <span
        key={`seg-${lineIdx}-${j}`}
        style={{ color: seg.color, fontWeight: fw, backgroundColor: bg }}
      >
        {seg.text}
      </span>
    );
  }

  // ===== 只读模式：行号嵌入每行 =====
  const readonlyLines = useMemo((): ReactNode[] => {
    return lines.map((lineSegs: Segment[], i: number) => {
      let colOffset = 0;
      const segsWithCol = lineSegs.map((seg) => {
        const start = colOffset;
        colOffset += seg.text.length;
        return { ...seg, startCol: start, endCol: colOffset };
      });

      const isFirst = i === 0;
      const isLast = i === lines.length - 1;
      return (
        <div
          key={`line-${i}`}
          style={{
            display: "flex",
            flexShrink: 0,
            minHeight: `${FONT_SIZE * LINE_HEIGHT}px`,
          }}
        >
          {/* 行号 — 连续背景列，延伸至顶部和底部 */}
          <div
            style={{
              width: GUTTER_WIDTH_READONLY,
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "flex-end",
              paddingRight: 4,
              fontFamily: FONT_FAMILY,
              fontSize: GUTTER_FONT_READONLY,
              lineHeight: LINE_HEIGHT,
              color: "var(--text-muted)",
              userSelect: "none",
              background: "var(--bg-secondary)",
              borderRight: "1px solid var(--border-default)",
              alignSelf: "stretch",
            }}
          >
            {i + 1}
          </div>
          {/* 内容 */}
          <div
            style={{
              flex: 1,
              minWidth: 0,
              whiteSpace: shouldWrap ? "pre-wrap" : "pre",
              overflowWrap: shouldWrap ? ("break-word" as React.CSSProperties["overflowWrap"]) : undefined,
              padding: `${isFirst ? PADDING_TOP : 0}px ${PADDING_H}px ${isLast ? PADDING_BOTTOM : 0}px ${PADDING_H}px`,
              fontFamily: FONT_FAMILY,
              fontSize: FONT_SIZE,
              lineHeight: LINE_HEIGHT,
            }}
          >
            {segsWithCol.length === 0
              ? "\u00A0"
              : segsWithCol.map((seg, j) => renderSegment(seg, i, j))}
          </div>
        </div>
      );
    });
  }, [lines, searchQuery, lineMatchMap, currentMatchIdx, shouldWrap]);

  // ===== 可编辑模式：独立行号 + pre 不换行 =====
  const editableLines = useMemo((): ReactNode[] => {
    return lines.map((lineSegs: Segment[], i: number) => {
      let colOffset = 0;
      const segsWithCol = lineSegs.map((seg) => {
        const start = colOffset;
        colOffset += seg.text.length;
        return { ...seg, startCol: start, endCol: colOffset };
      });

      return (
        <div
          key={`line-${i}`}
          style={{
            whiteSpace: shouldWrap ? "pre-wrap" : "pre",
            overflowWrap: shouldWrap ? "break-word" : undefined,
            minHeight: `${FONT_SIZE * LINE_HEIGHT}px`,
            position: "relative",
          }}
        >
          {segsWithCol.length === 0
            ? "\u00A0"
            : segsWithCol.map((seg, j) => renderSegment(seg, i, j))}
        </div>
      );
    });
  }, [lines, searchQuery, lineMatchMap, currentMatchIdx, shouldWrap]);

  // 滚动同步（可编辑模式）— 必须用 externalTextareaRef || textareaRef 才能兼容 Intruder
  const handleScroll = useCallback(() => {
    const ta = (externalTextareaRef || textareaRef).current;
    const pre = preRef.current;
    const gutter = gutterRef.current;
    if (ta && pre) {
      pre.scrollTop = ta.scrollTop;
      pre.scrollLeft = ta.scrollLeft;
    }
    if (ta && gutter) {
      gutter.scrollTop = ta.scrollTop;
    }
  }, [externalTextareaRef]);

  const handlePreScroll = useCallback(() => {
    const pre = preRef.current;
    const gutter = gutterRef.current;
    if (pre && gutter) {
      gutter.scrollTop = pre.scrollTop;
    }
  }, []);

  // 搜索导航
  const handleSearchPrev = () => {
    if (matches.length === 0) return;
    setCurrentMatchIdx((prev) => (prev - 1 + matches.length) % matches.length);
  };

  const handleSearchNext = () => {
    if (matches.length === 0) return;
    setCurrentMatchIdx((prev) => (prev + 1) % matches.length);
  };

  // 空内容（只读模式直接渲染空容器；可编辑模式不能提前返回，
  // 否则空标签页没有 textarea，新建标签页将无法输入——见重放器）
  if (!editable && (!value || !value.trim())) {
    return (
      <div
        ref={containerRef}
        style={{
          display: "flex",
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          overflow: "hidden",
          background: "var(--bg-primary)",
          position: "relative",
          ...style,
        }}
      />
    );
  }

  // ===== 搜索栏（两种模式共用） =====
  const searchBar = showSearch && (
    <div
      style={{
        position: "absolute",
        top: 0,
        right: 0,
        zIndex: 10,
        display: "flex",
        alignItems: "center",
        gap: 4,
        padding: "4px 8px",
        background: "var(--bg-secondary)",
        borderBottom: "1px solid var(--border-default)",
        borderLeft: "1px solid var(--border-default)",
        borderBottomLeftRadius: 4,
        fontSize: 12,
      }}
    >
      <Search size={12} style={{ color: "var(--text-muted)" }} />
      <input
        ref={searchInputRef}
        type="text"
        placeholder="搜索..."
        value={searchQuery}
        onChange={(e) => {
          setSearchQuery(e.target.value);
          setCurrentMatchIdx(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (e.shiftKey) handleSearchPrev();
            else handleSearchNext();
          }
        }}
        style={{
          width: 180,
          padding: "2px 6px",
          fontSize: 12,
          background: "var(--bg-primary)",
          border: "1px solid var(--border-default)",
          borderRadius: 3,
          color: "var(--text-primary)",
          outline: "none",
        }}
      />
      {matches.length > 0 && (
        <span style={{ fontSize: 11, color: "var(--text-muted)", whiteSpace: "nowrap", minWidth: 50 }}>
          {currentMatchIdx + 1}/{matches.length}
        </span>
      )}
      <button
        onClick={handleSearchPrev}
        disabled={matches.length === 0}
        title="上一个 (Shift+Enter)"
        style={{
          padding: "2px",
          border: "none",
          background: "transparent",
          cursor: matches.length ? "pointer" : "inherit",
          color: matches.length ? "var(--text-secondary)" : "var(--text-muted)",
          opacity: matches.length ? 1 : 0.4,
        }}
      >
        <ChevronUp size={14} />
      </button>
      <button
        onClick={handleSearchNext}
        disabled={matches.length === 0}
        title="下一个 (Enter)"
        style={{
          padding: "2px",
          border: "none",
          background: "transparent",
          cursor: matches.length ? "pointer" : "inherit",
          color: matches.length ? "var(--text-secondary)" : "var(--text-muted)",
          opacity: matches.length ? 1 : 0.4,
        }}
      >
        <ChevronDown size={14} />
      </button>
      <button
        onClick={() => {
          setShowSearch(false);
          setSearchQuery("");
        }}
        title="关闭 (Esc)"
        style={{
          padding: "2px",
          border: "none",
          background: "transparent",
          cursor: "pointer",
          color: "var(--text-muted)",
        }}
      >
        <X size={14} />
      </button>
    </div>
  );

  // ===== 可编辑模式：独立行号栏 + textarea overlay =====
  if (editable) {
    return (
      <div
        ref={containerRef}
        style={{
          display: "flex",
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          overflow: "hidden",
          background: "var(--bg-primary)",
          position: "relative",
          ...style,
        }}
        tabIndex={0}
      >
        {searchBar}

        {/* 行号栏 */}
        <div
          ref={gutterRef}
          style={{
            width: GUTTER_WIDTH_EDITABLE,
            flexShrink: 0,
            overflow: "hidden",
            background: "var(--bg-secondary)",
            borderRight: "1px solid var(--border-default)",
            paddingTop: PADDING_TOP,
            paddingBottom: PADDING_BOTTOM,
            fontFamily: FONT_FAMILY,
            fontSize: GUTTER_FONT_READONLY,
            lineHeight: LINE_HEIGHT,
            color: "var(--text-muted)",
            textAlign: "right",
            paddingRight: 6,
            userSelect: "none",
          }}
        >
          {Array.from({ length: lineCount }, (_, i) => (
            <div key={i} style={{ minHeight: `${FONT_SIZE * LINE_HEIGHT}px` }}>
              {i + 1}
            </div>
          ))}
        </div>

        {/* 内容区 */}
        <div style={{ position: "relative", flex: 1, overflow: "hidden", minWidth: 0 }}>
          <div
            ref={preRef}
            onScroll={handlePreScroll}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              margin: 0,
              padding: `${PADDING_TOP}px ${PADDING_H}px ${PADDING_BOTTOM}px ${PADDING_H}px`,
              fontFamily: FONT_FAMILY,
              fontSize: FONT_SIZE,
              lineHeight: LINE_HEIGHT,
              whiteSpace: shouldWrap ? "pre-wrap" : "pre",
              overflowWrap: shouldWrap ? "break-word" : undefined,
              overflow: "auto",
              background: "transparent",
              color: COLOR.text,
              pointerEvents: "none",
            }}
          >
            {editableLines}
          </div>

          <textarea
            ref={externalTextareaRef || textareaRef}
            value={value}
            onChange={(e) => onChange?.(e.target.value)}
            onScroll={handleScroll}
            onMouseUp={() => {
              if (onSelectionChange) {
                const ta = (externalTextareaRef || textareaRef).current;
                onSelectionChange(!!ta && ta.selectionStart !== ta.selectionEnd);
              }
            }}
            onKeyUp={() => {
              if (onSelectionChange) {
                const ta = (externalTextareaRef || textareaRef).current;
                onSelectionChange(!!ta && ta.selectionStart !== ta.selectionEnd);
              }
            }}
            spellCheck={false}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              margin: 0,
              padding: `${PADDING_TOP}px ${PADDING_H}px ${PADDING_BOTTOM}px ${PADDING_H}px`,
              fontFamily: FONT_FAMILY,
              fontSize: FONT_SIZE,
              lineHeight: LINE_HEIGHT,
              whiteSpace: shouldWrap ? "pre-wrap" : "pre",
              overflowWrap: shouldWrap ? "break-word" : undefined,
              overflow: "auto",
              background: "transparent",
              color: "transparent",
              caretColor: "var(--text-primary)",
              border: "none",
              outline: "none",
              resize: "none",
              tabSize: 4,
            }}
          />
        </div>
      </div>
    );
  }

  // ===== 只读模式：行号嵌入每行，pre-wrap 自动换行 =====
  // 使用 block 布局（非 flex），避免 flex column + overflow:auto 导致的行重叠问题
  return (
    <div
      ref={containerRef}
      className="text-selectable"
      style={{
        display: "flex",
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        overflow: "hidden",
        background: "var(--bg-primary)",
        position: "relative",
        ...style,
      }}
      tabIndex={0}
    >
      {searchBar}

      <div
        ref={preRef}
        style={{
          margin: 0,
          padding: 0,
          fontFamily: FONT_FAMILY,
          fontSize: FONT_SIZE,
          lineHeight: LINE_HEIGHT,
          overflow: "auto",
          background: "transparent",
          color: COLOR.text,
          flex: 1,
          minWidth: 0,
          minHeight: 0,
        }}
      >
        {readonlyLines}
      </div>
    </div>
  );
}

export default CodeEditor;
