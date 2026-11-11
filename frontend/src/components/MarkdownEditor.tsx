import { MarkdownView } from "./MarkdownView";

interface MarkdownEditorProps {
  value: string;
  onChange: (v: string) => void;
  /** 编辑区最小高度（px），默认 220 */
  minHeight?: number;
  placeholder?: string;
}

/**
 * MarkdownEditor — 编辑 + 实时渲染并排。
 *
 * 用户要求编辑时直接看到渲染效果（不做纯源码模式）：
 * 左侧 textarea 写源码，右侧 MarkdownView 实时渲染，改动即时同步。
 * 弹窗宽度建议 ≥ 880px，否则两栏会挤压。
 */
export function MarkdownEditor({ value, onChange, minHeight = 220, placeholder }: MarkdownEditorProps) {
  const empty = !value.trim();
  return (
    <div style={{ display: "flex", gap: 8, minHeight }}>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        style={{
          flex: 1,
          minWidth: 0,
          minHeight,
          padding: 10,
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          lineHeight: 1.6,
          background: "var(--bg-primary)",
          border: "1px solid var(--border-default)",
          borderRadius: 6,
          color: "var(--text-primary)",
          resize: "vertical",
          outline: "none",
        }}
      />
      <div
        className="text-selectable"
        style={{
          flex: 1,
          minWidth: 0,
          minHeight,
          maxHeight: 480,
          overflow: "auto",
          padding: "8px 10px",
          background: "var(--bg-primary)",
          border: "1px solid var(--border-default)",
          borderRadius: 6,
          fontSize: 12,
        }}
      >
        {empty ? (
          <span style={{ color: "var(--text-muted)", fontSize: 12 }}>（暂无内容，左侧输入后此处实时渲染）</span>
        ) : (
          <MarkdownView content={value} />
        )}
      </div>
    </div>
  );
}

export default MarkdownEditor;
