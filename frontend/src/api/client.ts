import axios from "axios";

// 检测是否在 Tauri 生产环境中运行
// 开发模式：Vite 代理转发 /api -> http://127.0.0.1:9876/api
// 生产模式：Tauri WebView 直接请求 http://127.0.0.1:9876/api
const isTauriProduction = () => {
  // Tauri v2 注入 __TAURI_INTERNALS__
  // 在开发模式下，Vite 代理处理 /api 路径，使用相对 URL
  // 在生产模式下，需要绝对 URL
  if (typeof window !== "undefined") {
    return (
      "__TAURI_INTERNALS__" in window ||
      "__TAURI__" in window
    );
  }
  return false;
};

const API_BASE = isTauriProduction()
  ? "http://127.0.0.1:9876/api"
  : "/api";

const WS_BASE = isTauriProduction()
  ? "ws://127.0.0.1:9876/ws"
  : "/ws";

const client = axios.create({
  baseURL: API_BASE,
  timeout: 300000, // 5 分钟：Agent 对话可能涉及多轮 LLM 调用和工具执行
  headers: {
    "Content-Type": "application/json",
  },
});

// 响应拦截器 — 统一错误处理
client.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const status = error.response?.status;
    const msg = error.response?.data?.detail || error.message || "Unknown error";
    // 404 "project not found" 是预期错误：后端重启后内存中的活跃项目 ID 丢失，
    // 或临时项目被清理后前端 localStorage 仍持有旧 ID。
    // ProjectContext 会静默处理此错误（清除旧 ID），无需打印到控制台。
    if (status === 404 && msg === "project not found") {
      return Promise.reject(new Error(msg));
    }
    console.error("[API Error]", msg);
    return Promise.reject(new Error(msg));
  }
);

export { API_BASE, WS_BASE };
export default client;
