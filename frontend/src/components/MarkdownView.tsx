import { memo, useState } from "react";
import type { ReactNode } from "react";
import { Copy, Check } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

// ===== 代码块（Typora/Codex 风格 — 语言标签 + 复制按钮）=====

const CodeBlockWrapper = memo(function CodeBlockWrapper({ children }: { children: ReactNode }) {
  const [copied, setCopied] = useState(false);

  // 从子级 code 元素中提取语言和代码文本
  const codeEl = Array.isArray(children) ? children[0] : children;
  const props = ((codeEl as React.ReactElement)?.props ?? {}) as { className?: string; children?: ReactNode };
  const className: string = props.className || "";
  const lang = className.replace(/hljs\s+language-|language-/g, "") || "text";
  const rawChildren = props.children;
  const codeText = typeof rawChildren === "string"
    ? rawChildren
    : Array.isArray(rawChildren)
      ? rawChildren.join("")
      : String(rawChildren ?? "");

  const handleCopy = () => {
    navigator.clipboard.writeText(codeText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="md-codeblock">
      <div className="md-codeblock-header">
        <span className="md-codeblock-lang">{lang}</span>
        <button className="md-codeblock-copy" onClick={handleCopy}>
          {copied ? <Check size={11} /> : <Copy size={11} />}
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre className="md-pre">{children}</pre>
    </div>
  );
});

// ===== Markdown 渲染（与 Agent 消息渲染同款配置）=====

interface MarkdownViewProps {
  /** Markdown 源文本 */
  content: string;
  /** 容器 class，默认 agent-chat-msg-markdown（含完整 markdown + 语法高亮样式） */
  className?: string;
}

/** 统一 markdown 渲染组件：GFM 表格、代码高亮 + 复制、ASCII 框线等宽。 */
const MarkdownView = memo(function MarkdownView({ content, className = "agent-chat-msg-markdown" }: MarkdownViewProps) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          pre: ({ children }) => <CodeBlockWrapper>{children}</CodeBlockWrapper>,
          code: ({ className, children, ...props }) => {
            const isInline = !className;
            return isInline ? (
              <code className="md-code-inline" {...props}>{children}</code>
            ) : (
              <code className={className} {...props}>{children}</code>
            );
          },
          table: ({ children }) => <table className="md-table">{children}</table>,
          th: ({ children }) => <th className="md-th">{children}</th>,
          td: ({ children }) => <td className="md-td">{children}</td>,
          // 检测包含 ASCII 框线字符的段落，使用等宽字体保持对齐
          p: ({ children, ...props }) => {
            const text = typeof children === "string" ? children : "";
            const hasBoxChars = /[│┌┐└┘├┤┬┴┼─━┃┏┓┗┛┣┫┳┻╋═║╔╗╚╝╠╣╦╩╬↑↓←→]/.test(text);
            return hasBoxChars ? (
              <p className="md-ascii-art" {...props}>{children}</p>
            ) : (
              <p {...props}>{children}</p>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

export { MarkdownView, CodeBlockWrapper };
