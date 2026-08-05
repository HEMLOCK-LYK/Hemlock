import { useEffect, useState } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { SecurityDeclaration, isSecurityAccepted } from "./components/SecurityDeclaration";
import { FlowList } from "./views/FlowList";
import { FlowDetail } from "./views/FlowDetail";
import { Repeater } from "./views/Repeater";
import { Intruder } from "./views/Intruder";
import { Encoder } from "./views/Encoder";
import { AgentView } from "./views/AgentView";
import { AgentConfig } from "./views/AgentConfig";
import { AgentRoles } from "./views/AgentRoles";
import { ReconView } from "./views/ReconView";
import { LLMProviders } from "./views/LLMProviders";
import { FindingsView } from "./views/FindingsView";
import { FlowTagRules } from "./views/FlowTagRules";
import { Settings } from "./views/Settings";
import { MemoryManager } from "./views/MemoryManager";
import { ProjectSelector } from "./views/ProjectSelector";
import { useProject } from "./context/ProjectContext";

/** 隐藏并移除原生启动屏（加 fade-out 过渡动画） */
function dismissSplash() {
  const splash = document.getElementById("splash-screen");
  if (!splash) return;
  splash.classList.add("fade-out");
  // 等淡出动画结束后彻底移除 DOM
  setTimeout(() => splash.remove(), 600);
}

export default function App() {
  const { currentProject, loading } = useProject();
  const [splashDismissed, setSplashDismissed] = useState(false);
  const [securityAccepted, setSecurityAccepted] = useState(isSecurityAccepted());
  const location = useLocation();

  // 退出/刷新页面时提示保存临时项目
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (currentProject?.is_temp) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [currentProject?.is_temp]);

  // 加载完成：触发启动屏淡出过渡
  useEffect(() => {
    if (!loading && !splashDismissed) {
      dismissSplash();
      setSplashDismissed(true);
    }
  }, [loading, splashDismissed]);

  // 安全声明未接受：显示声明弹窗（最高优先级）
  if (!securityAccepted) {
    return <SecurityDeclaration onAccept={() => setSecurityAccepted(true)} />;
  }

  // 加载中：不渲染任何内容，原生启动屏继续显示
  if (loading) {
    return null;
  }

  // 未选中项目：显示项目选择器
  if (!currentProject) {
    return <ProjectSelector />;
  }

  // 已选中项目：显示主界面
  return (
    <div className="app-layout">
      <Sidebar />
      <div className="main-content">
        <div className="content-area">
          <div key={location.pathname} className="page-transition">
            <Routes location={location}>
              <Route path="/" element={<Navigate to="/flows" replace />} />
              <Route path="/flows" element={<FlowList />} />
              <Route path="/flows/:flowId" element={<FlowDetail />} />
              <Route path="/repeater" element={<Repeater />} />
              <Route path="/intruder" element={<Intruder />} />
              <Route path="/encoder" element={<Encoder />} />
              <Route path="/agent" element={<AgentView />} />
              <Route path="/agent/config" element={<AgentConfig />} />
              <Route path="/agent/roles" element={<AgentRoles />} />
              <Route path="/recon" element={<ReconView />} />
              <Route path="/llm-providers" element={<LLMProviders />} />
              <Route path="/memory" element={<MemoryManager />} />
              <Route path="/findings" element={<FindingsView />} />
              <Route path="/flowtag" element={<FlowTagRules />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </div>
        </div>
        <StatusBar />
      </div>
    </div>
  );
}
