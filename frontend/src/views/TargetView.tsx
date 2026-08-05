import { useState, useEffect } from "react";
import { Plus, Trash2, Target, Shield, AlertTriangle } from "lucide-react";

const STORAGE_KEY = "hemlock_target_scope";

export function TargetView() {
  const [patterns, setPatterns] = useState<string[]>([]);
  const [newPattern, setNewPattern] = useState("");

  // 从 localStorage 加载（后端 scope API 尚未实现，先本地存储）
  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      try {
        setPatterns(JSON.parse(saved));
      } catch {
        // ignore
      }
    } else {
      // 默认示例
      setPatterns(["*.example.com", "10.0.0.0/8", "192.168.1.0/24"]);
    }
  }, []);

  const savePatterns = (newPatterns: string[]) => {
    setPatterns(newPatterns);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(newPatterns));
  };

  const handleAdd = () => {
    if (!newPattern.trim()) return;
    if (patterns.includes(newPattern.trim())) return;
    savePatterns([...patterns, newPattern.trim()]);
    setNewPattern("");
  };

  const handleRemove = (pattern: string) => {
    savePatterns(patterns.filter((p) => p !== pattern));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleAdd();
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="view-header">
        <span className="view-title">目标范围管理</span>
      </div>

      <div className="scroll-container" style={{ padding: 16 }}>
        {/* 安全警告 */}
        <div
          className="card mb-4"
          style={{
            borderColor: "var(--accent-yellow)",
            background: "rgba(210,153,34,0.06)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 12,
            }}
          >
            <AlertTriangle
              size={20}
              style={{ color: "var(--accent-yellow)", flexShrink: 0, marginTop: 2 }}
            />
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>
                授权范围声明
              </div>
              <div className="text-xs text-secondary">
                仅对已获得书面授权的目标进行安全测试。所有请求将根据此范围列表进行校验，
                超出范围的请求将被拒绝。支持通配符（如 *.example.com）和 CIDR 表示法。
              </div>
            </div>
          </div>
        </div>

        {/* 添加范围 */}
        <div
          style={{
            display: "flex",
            gap: 8,
            marginBottom: 16,
          }}
        >
          <input
            className="input"
            style={{ flex: 1 }}
            placeholder="*.target.com 或 10.0.0.0/8"
            value={newPattern}
            onChange={(e) => setNewPattern(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button className="btn btn-primary" onClick={handleAdd}>
            <Plus size={14} /> 添加
          </button>
        </div>

        {/* 范围列表 */}
        <div style={{ marginBottom: 24 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 12,
            }}
          >
            <Shield size={16} style={{ color: "var(--accent-green)" }} />
            <span className="text-sm" style={{ fontWeight: 600 }}>
              已授权目标 ({patterns.length})
            </span>
          </div>

          {patterns.length === 0 ? (
            <div
              className="card"
              style={{ textAlign: "center", color: "var(--text-muted)" }}
            >
              <Target size={32} style={{ opacity: 0.3, marginBottom: 8 }} />
              <div>暂无授权目标，请添加</div>
            </div>
          ) : (
            patterns.map((p) => (
              <div key={p} className="scope-item">
                <span className="scope-pattern">{p}</span>
                <button
                  className="btn btn-sm"
                  onClick={() => handleRemove(p)}
                  style={{ color: "var(--accent-red)" }}
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))
          )}
        </div>

        {/* 使用说明 */}
        <div className="card">
          <div className="text-sm" style={{ fontWeight: 600, marginBottom: 12 }}>
            使用说明
          </div>
          <div className="text-xs text-secondary" style={{ lineHeight: 1.8 }}>
            <div>
              <strong>通配符:</strong> <code>*.example.com</code> 匹配 example.com 的所有子域名
            </div>
            <div>
              <strong>CIDR:</strong> <code>10.0.0.0/8</code> 匹配 10.x.x.x 网段
            </div>
            <div>
              <strong>精确域名:</strong> <code>api.target.com</code> 仅匹配该域名
            </div>
            <div>
              <strong>注意:</strong> 空列表表示允许所有目标（危险！仅用于测试环境）
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
