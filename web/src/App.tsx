import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, type Config } from "./api";
import { AuthProvider, useAuth } from "./AuthContext";
import Login from "./pages/Login";
import Upload from "./pages/Upload";
import Review from "./pages/Review";
import Standards from "./pages/Standards";
import Report from "./pages/Report";

function AuthedApp() {
  const { user, logout } = useAuth();
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
        <div className="cfg">
          {cfg && (
            <span>模型：{cfg.reviewer}{cfg.model ? ` · ${cfg.model}` : ""}</span>
          )}
          {cfg && <span className={`dot ${cfg.key_set ? "on" : "off"}`} title={cfg.key_set ? "已配置 key" : "未配置 ONEAPI_KEY"} />}
          {user && <span className="user-name">{user.display_name}</span>}
          <button type="button" className="btn btn-ghost btn-logout" onClick={() => { void logout(); }}>退出</button>
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/review" replace />} />
          <Route path="/login" element={<Navigate to="/review" replace />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/review" element={<Review />} />
          <Route path="/standards" element={<Standards />} />
          <Route path="/report" element={<Report />} />
        </Routes>
      </main>
    </>
  );
}

function Gate() {
  const { user, loading } = useAuth();
  if (loading) {
    return <div className="login-shell"><p className="small">加载中…</p></div>;
  }
  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }
  return <AuthedApp />;
}

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}
