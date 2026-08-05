import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App";
import { ThemeProvider } from "./context/ThemeContext";
import { ProjectProvider } from "./context/ProjectContext";
import { PacketProvider } from "./context/PacketContext";
import "./index.css";

// 窗口显示由 Rust 端控制（lib.rs setup 中延迟 150ms 调用 window.show()）
// 不依赖前端 JS，避免模块加载失败导致窗口永远不可见

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashRouter>
      <ThemeProvider>
        <ProjectProvider>
          <PacketProvider>
            <App />
          </PacketProvider>
        </ProjectProvider>
      </ThemeProvider>
    </HashRouter>
  </StrictMode>
);
