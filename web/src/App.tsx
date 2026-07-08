import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, type Config } from "./api";
import Upload from "./pages/Upload";
import Review from "./pages/Review";
import Standards from "./pages/Standards";
import Report from "./pages/Report";

export default function App() {
  const [cfg, setCfg] = useState<Config | null>(null);
  useEffect(() => {
    api.config().then(setCfg).catch(() => setCfg(null));
  }, []);

  return (
    <>
      <header className="top">
        <h1>内容审核台</h1>
        <nav>
          <NavLink to="/upload" className={({ isActive }) => (isActive ? "active" : "")}>供应商上传</NavLink>
          <NavLink to="/review" className={({ isActive }) => (isActive ? "active" : "")}>审核台</NavLink>
          <NavLink to="/standards" className={({ isActive }) => (isActive ? "active" : "")}>标准管理</NavLink>
          <NavLink to="/report" className={({ isActive }) => (isActive ? "active" : "")}>报告</NavLink>
        </nav>
        {cfg && (
          <div className="cfg">
            <span>模型：{cfg.reviewer}{cfg.model ? ` · ${cfg.model}` : ""}</span>
            <span className={`dot ${cfg.key_set ? "on" : "off"}`} title={cfg.key_set ? "已配置 key" : "未配置 ONEAPI_KEY"} />
          </div>
        )}
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/review" replace />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/review" element={<Review />} />
          <Route path="/standards" element={<Standards />} />
          <Route path="/report" element={<Report />} />
        </Routes>
      </main>
    </>
  );
}
