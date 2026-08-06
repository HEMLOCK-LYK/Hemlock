import { useState, useCallback } from "react";
import {
  ArrowUpDown,
  Copy,
  Check,
  AlertCircle,
  Binary,
  Link2,
  Type,
  Braces,
} from "lucide-react";

type CodecType =
  | "url-standard"
  | "url-js"
  | "base64"
  | "base64-url"
  | "unicode-escape"
  | "unicode-html"
  | "hex";

interface CodecDef {
  key: CodecType;
  label: string;
  icon: typeof Binary;
  encode: (input: string) => string;
  decode: (input: string) => string;
}

// ===== 编解码函数 =====

const codecs: CodecDef[] = [
  {
    key: "url-standard",
    label: "URL 编码 (标准)",
    icon: Link2,
    encode: (input) => encodeURIComponent(input),
    decode: (input) => decodeURIComponent(input),
  },
  {
    key: "url-js",
    label: "URL 编码 (JS escape)",
    icon: Link2,
    encode: (input) => {
      // 模拟 JS escape() 行为：不编码 @*/+ 等
      let result = "";
      for (let i = 0; i < input.length; i++) {
        const ch = input.charCodeAt(i);
        if (
          (ch >= 65 && ch <= 90) || // A-Z
          (ch >= 97 && ch <= 122) || // a-z
          (ch >= 48 && ch <= 57) || // 0-9
          ch === 64 || // @
          ch === 42 || // *
          ch === 95 || // _
          ch === 43 || // +
          ch === 45 || // -
          ch === 46 || // .
          ch === 47 // /
        ) {
          result += input[i];
        } else if (ch <= 255) {
          result += "%" + ch.toString(16).toUpperCase().padStart(2, "0");
        } else {
          result += "%u" + ch.toString(16).toUpperCase().padStart(4, "0");
        }
      }
      return result;
    },
    decode: (input) => {
      // 模拟 JS unescape() 行为
      return input.replace(/%u([0-9a-fA-F]{4})/g, (_, hex) =>
        String.fromCharCode(parseInt(hex, 16))
      ).replace(/%([0-9a-fA-F]{2})/g, (_, hex) =>
        String.fromCharCode(parseInt(hex, 16))
      );
    },
  },
  {
    key: "base64",
    label: "Base64",
    icon: Binary,
    encode: (input) => {
      // 支持 UTF-8
      const bytes = new TextEncoder().encode(input);
      let binary = "";
      bytes.forEach((b) => (binary += String.fromCharCode(b)));
      return btoa(binary);
    },
    decode: (input) => {
      const binary = atob(input.trim());
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return new TextDecoder().decode(bytes);
    },
  },
  {
    key: "base64-url",
    label: "Base64URL (URL Safe)",
    icon: Binary,
    encode: (input) => {
      const bytes = new TextEncoder().encode(input);
      let binary = "";
      bytes.forEach((b) => (binary += String.fromCharCode(b)));
      return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    },
    decode: (input) => {
      let str = input.trim().replace(/-/g, "+").replace(/_/g, "/");
      // 补齐 padding
      while (str.length % 4) str += "=";
      const binary = atob(str);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return new TextDecoder().decode(bytes);
    },
  },
  {
    key: "unicode-escape",
    label: "Unicode 转义 (\\uXXXX)",
    icon: Type,
    encode: (input) => {
      let result = "";
      for (let i = 0; i < input.length; i++) {
        const code = input.charCodeAt(i);
        if (code > 127) {
          result += "\\u" + code.toString(16).toUpperCase().padStart(4, "0");
        } else {
          result += input[i];
        }
      }
      return result;
    },
    decode: (input) => {
      return input.replace(/\\u([0-9a-fA-F]{4})/g, (_, hex) =>
        String.fromCharCode(parseInt(hex, 16))
      ).replace(/\\u\{([0-9a-fA-F]+)\}/g, (_, hex) =>
        String.fromCodePoint(parseInt(hex, 16))
      );
    },
  },
  {
    key: "unicode-html",
    label: "HTML 实体编码 (&#x)",
    icon: Type,
    encode: (input) => {
      let result = "";
      for (let i = 0; i < input.length; i++) {
        const code = input.charCodeAt(i);
        if (code > 127) {
          result += "&#x" + code.toString(16).toUpperCase() + ";";
        } else {
          result += input[i];
        }
      }
      return result;
    },
    decode: (input) => {
      return input.replace(/&#x([0-9a-fA-F]+);/g, (_, hex) =>
        String.fromCharCode(parseInt(hex, 16))
      ).replace(/&#(\d+);/g, (_, dec) =>
        String.fromCharCode(parseInt(dec, 10))
      );
    },
  },
  {
    key: "hex",
    label: "Hex 十六进制",
    icon: Braces,
    encode: (input) => {
      const bytes = new TextEncoder().encode(input);
      return Array.from(bytes)
        .map((b) => b.toString(16).toUpperCase().padStart(2, "0"))
        .join(" ");
    },
    decode: (input) => {
      const hexStr = input.trim().replace(/\s+/g, "").replace(/0x/gi, "");
      const bytes = new Uint8Array(hexStr.length / 2);
      for (let i = 0; i < hexStr.length; i += 2) {
        bytes[i / 2] = parseInt(hexStr.substr(i, 2), 16);
      }
      return new TextDecoder().decode(bytes);
    },
  },
];

export function Encoder() {
  const [codecType, setCodecType] = useState<CodecType>("url-standard");
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [mode, setMode] = useState<"encode" | "decode">("encode");

  const currentCodec = codecs.find((c) => c.key === codecType)!;

  const handleTransform = useCallback(
    (direction: "encode" | "decode", text: string) => {
      if (!text) {
        setOutput("");
        setError(null);
        return;
      }
      try {
        const result =
          direction === "encode"
            ? currentCodec.encode(text)
            : currentCodec.decode(text);
        setOutput(result);
        setError(null);
      } catch (e) {
        setOutput("");
        setError(e instanceof Error ? e.message : "转换失败");
      }
    },
    [currentCodec]
  );

  const handleEncode = () => {
    setMode("encode");
    handleTransform("encode", input);
  };

  const handleDecode = () => {
    setMode("decode");
    handleTransform("decode", input);
  };

  const handleSwap = () => {
    if (!output) return;
    setInput(output);
    setOutput("");
    setError(null);
  };

  const handleCopy = () => {
    if (output) {
      navigator.clipboard.writeText(output);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  const handleClear = () => {
    setInput("");
    setOutput("");
    setError(null);
  };

  return (
    <div className="flex flex-col h-full">
      <div className="view-header">
        <span className="view-title">编码解码器</span>
      </div>

      <div className="scroll-container" style={{ padding: 16, maxWidth: 900 }}>
        {/* 编码类型选择 */}
        <div className="card mb-4">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 12,
            }}
          >
            <ArrowUpDown size={16} style={{ color: "var(--accent-purple)" }} />
            <span style={{ fontWeight: 600, fontSize: 14 }}>编码类型</span>
          </div>
          <div className="encoder-type-grid">
            {codecs.map((codec) => {
              const Icon = codec.icon;
              return (
                <div
                  key={codec.key}
                  className={`encoder-type-card ${
                    codecType === codec.key ? "active" : ""
                  }`}
                  onClick={() => {
                    setCodecType(codec.key);
                    setOutput("");
                    setError(null);
                  }}
                >
                  <Icon size={16} style={{ marginBottom: 4, opacity: 0.7 }} />
                  <div style={{ fontSize: 12, fontWeight: 600 }}>
                    {codec.label}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* 输入区 */}
        <div className="card mb-4">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 8,
            }}
          >
            <span className="text-xs text-muted">
              输入 ({mode === "encode" ? "明文" : "编码后"})
            </span>
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn btn-sm" onClick={handleClear}>
                清空
              </button>
            </div>
          </div>
          <textarea
            className="input w-full"
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              if (e.target.value === "") {
                setOutput("");
                setError(null);
              }
            }}
            rows={6}
            style={{
              resize: "vertical",
              fontFamily: "monospace",
              fontSize: 13,
              lineHeight: 1.5,
            }}
          />
        </div>

        {/* 操作按钮 */}
        <div
          style={{
            display: "flex",
            gap: 8,
            marginBottom: 16,
            justifyContent: "center",
          }}
        >
          <button
            className={`btn btn-sm ${mode === "encode" ? "btn-primary" : ""}`}
            onClick={handleEncode}
            disabled={!input}
          >
            <ArrowUpDown size={12} style={{ transform: "rotate(180deg)" }} />
            编码 →
          </button>
          <button className="btn btn-sm" onClick={handleSwap} disabled={!output} title="将输出移至输入">
            <ArrowUpDown size={12} />
            交换 ⇅
          </button>
          <button
            className={`btn btn-sm ${mode === "decode" ? "btn-primary" : ""}`}
            onClick={handleDecode}
            disabled={!input}
          >
            <ArrowUpDown size={12} />
            ← 解码
          </button>
        </div>

        {/* 输出区 */}
        <div className="card mb-4">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 8,
            }}
          >
            <span className="text-xs text-muted">
              输出 ({mode === "encode" ? "编码后" : "明文"})
            </span>
            <button
              className="btn btn-sm"
              onClick={handleCopy}
              disabled={!output}
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? "已复制" : "复制"}
            </button>
          </div>
          {error ? (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: 12,
                background: "rgba(255,107,107,0.1)",
                border: "1px solid var(--accent-red)",
                borderRadius: 6,
                color: "var(--accent-red)",
                fontSize: 13,
              }}
            >
              <AlertCircle size={16} />
              {error}
            </div>
          ) : (
            <textarea
              className="input w-full"
              value={output}
              readOnly
              rows={6}
              style={{
                resize: "vertical",
                fontFamily: "monospace",
                fontSize: 13,
                lineHeight: 1.5,
                background: "var(--bg-primary)",
              }}
            />
          )}
        </div>

        {/* 编码类型说明 */}
        <div
          className="card"
          style={{ borderColor: "var(--border-subtle)" }}
        >
          <div
            style={{
              fontSize: 12,
              color: "var(--text-secondary)",
              lineHeight: 1.7,
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 4, color: "var(--text-primary)" }}>
              {currentCodec.label}
            </div>
            {codecType === "url-standard" && (
              <div>
                标准 URL 编码 (encodeURIComponent)，将特殊字符转换为 %XX 格式。
                适用于 URL 参数值的编码。
              </div>
            )}
            {codecType === "url-js" && (
              <div>
                JS escape() 方式编码，保留 @*/+-._ 不编码，其他非 ASCII 字符使用 %uXXXX 格式。
                常见于旧版 Web 应用和 XSS 绕过场景。
              </div>
            )}
            {codecType === "base64" && (
              <div>
                Base64 编码，将二进制数据转换为可打印 ASCII 字符。支持 UTF-8。
                使用 +、/ 和 = 填充符。
              </div>
            )}
            {codecType === "base64-url" && (
              <div>
                URL 安全的 Base64 编码，将 + 替换为 -，/ 替换为 _，去除 = 填充。
                适用于 JWT、URL 参数等场景。
              </div>
            )}
            {codecType === "unicode-escape" && (
              <div>
                Unicode 转义序列编码，将非 ASCII 字符转换为 \uXXXX 格式。
                常见于 JavaScript 字符串、JSON 中。
              </div>
            )}
            {codecType === "unicode-html" && (
              <div>
                HTML 实体编码，将非 ASCII 字符转换为 &#xHEX; 格式。
                用于 XSS payload 编码绕过。
              </div>
            )}
            {codecType === "hex" && (
              <div>
                十六进制编码，将每个字节转换为两位十六进制，空格分隔。
                常用于二进制数据分析。
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
