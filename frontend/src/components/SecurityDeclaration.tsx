import { useState } from "react";

/**
 * SecurityDeclaration — 启动安全声明弹窗
 *
 * 用户首次打开软件（或重置声明后）必须同意安全声明才能使用。
 * 接受状态存储在 localStorage，可在设置中重置。
 */

const STORAGE_KEY = "hemlock_security_accepted";

/** 检查用户是否已接受安全声明 */
export function isSecurityAccepted(): boolean {
  return localStorage.getItem(STORAGE_KEY) === "true";
}

/** 重置安全声明状态（设置页面调用，下次启动重新弹出） */
export function resetSecurityDeclaration() {
  localStorage.removeItem(STORAGE_KEY);
}

/** 关闭应用窗口（兼容 Tauri 和浏览器环境） */
async function closeApp() {
  try {
    // Tauri v2 环境
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().close();
  } catch {
    // 非 Tauri 环境（浏览器开发模式）回退
    window.close();
  }
}

export function SecurityDeclaration({ onAccept }: { onAccept: () => void }) {
  const [checked, setChecked] = useState(false);

  const handleAccept = () => {
    if (!checked) return;
    localStorage.setItem(STORAGE_KEY, "true");
    onAccept();
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 10000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0, 0, 0, 0.6)",
        backdropFilter: "blur(4px)",
        animation: "fadeIn 0.3s ease-out",
      }}
    >
      <div
        style={{
          width: "560px",
          maxHeight: "80vh",
          background: "#ffffff",
          border: "1px solid #e0e0e0",
          borderRadius: "12px",
          boxShadow: "0 16px 48px rgba(0, 0, 0, 0.15)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          animation: "slideUp 0.4s ease-out",
        }}
      >
        {/* 标题栏 */}
        <div
          style={{
            padding: "20px 24px 16px",
            borderBottom: "1px solid #e0e0e0",
            display: "flex",
            alignItems: "center",
            gap: "10px",
          }}
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" style={{ flexShrink: 0 }}>
            <path
              d="M12 2L3 7v6c0 5.5 3.8 10.7 9 12 5.2-1.3 9-6.5 9-12V7l-9-5z"
              stroke="#2f80ed"
              strokeWidth="2"
              strokeLinejoin="round"
            />
            <path
              d="M12 8v4M12 16h.01"
              stroke="#2f80ed"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
          <h2 style={{ fontSize: "16px", fontWeight: 600, color: "#1a1a2e" }}>
            安全声明 & 使用协议
          </h2>
        </div>

        {/* 声明内容 */}
        <div
          style={{
            padding: "16px 24px",
            overflowY: "auto",
            flex: 1,
            fontSize: "13px",
            lineHeight: "1.7",
            color: "#444",
          }}
        >
          <p style={{ marginBottom: "12px" }}>
            <strong style={{ color: "#1a1a2e" }}>Hemlock</strong>{" "}
            是一款集 AI Agent 与流量捕获于一体的本地安全测试工具，仅供安全研究和授权测试使用。
          </p>

          <p style={{ marginBottom: "8px", fontWeight: 600, color: "#1a1a2e" }}>
            使用前请阅读以下条款：
          </p>

          <ol style={{ paddingLeft: "20px", marginBottom: "12px" }}>
            <li style={{ marginBottom: "6px" }}>
              本软件仅用于<strong>授权范围内的安全测试</strong>。用户必须获得目标系统所有者的书面授权后方可进行测试。
            </li>
            <li style={{ marginBottom: "6px" }}>
              用户不得利用本软件对<strong>未经授权的目标</strong>进行扫描、攻击或数据窃取。
            </li>
            <li style={{ marginBottom: "6px" }}>
              本软件的 AI Agent 功能可能自动发送测试请求，用户应<strong>全程监督 Agent 行为</strong>，敏感操作需手动确认。
            </li>
            <li style={{ marginBottom: "6px" }}>
              用户需遵守所在地区的<strong>网络安全法律法规</strong>（包括但不限于《网络安全法》《数据安全法》《个人信息保护法》）。
            </li>
            <li style={{ marginBottom: "6px" }}>
              开发者不对用户使用本软件造成的任何法律后果承担责任，<strong>全部责任由用户自行承担</strong>。
            </li>
            <li style={{ marginBottom: "6px" }}>
              测试产生的所有数据（流量、漏洞报告等）存储在本地，用户应妥善保管，防止泄露。
            </li>
          </ol>

          <div
            style={{
              padding: "10px 12px",
              background: "#f0f5ff",
              borderRadius: "6px",
              border: "1px solid #d0dfff",
              fontSize: "12px",
              color: "#555",
            }}
          >
            点击"同意并继续"即表示您已阅读、理解并同意遵守以上全部条款。如果您不同意，请关闭本软件。
          </div>
        </div>

        {/* 底部操作 */}
        <div
          style={{
            padding: "12px 24px 16px",
            borderTop: "1px solid #e0e0e0",
            display: "flex",
            flexDirection: "column",
            gap: "10px",
          }}
        >
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              cursor: "pointer",
              fontSize: "13px",
              color: "#444",
              userSelect: "none",
            }}
          >
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => setChecked(e.target.checked)}
              style={{ cursor: "pointer", width: "14px", height: "14px" }}
            />
            我已阅读并理解以上安全声明，同意遵守全部条款
          </label>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
            <button
              onClick={() => closeApp()}
              style={{
                padding: "8px 16px",
                fontSize: "13px",
                border: "1px solid #d0d0d0",
                borderRadius: "6px",
                background: "transparent",
                color: "#666",
                cursor: "pointer",
              }}
            >
              不同意，退出
            </button>
            <button
              onClick={handleAccept}
              disabled={!checked}
              style={{
                padding: "8px 20px",
                fontSize: "13px",
                fontWeight: 600,
                border: "none",
                borderRadius: "6px",
                background: checked ? "#2f80ed" : "#e0e0e0",
                color: checked ? "#ffffff" : "#999",
                cursor: checked ? "pointer" : "not-allowed",
                opacity: 1,
                transition: "background 0.2s ease, color 0.2s ease",
              }}
            >
              同意并继续
            </button>
          </div>
        </div>
      </div>

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes slideUp {
          from {
            opacity: 0;
            transform: translateY(20px) scale(0.98);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
      `}</style>
    </div>
  );
}
