import { useEffect, useState } from "react";
import { BarChart3, FileText, LogOut, ShieldCheck, UploadCloud } from "lucide-react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, type Config } from "./api";
import { AuthProvider, useAuth } from "./AuthContext";
import baiduMapLogo from "./assets/baidu-map-logo.png";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import Upload from "./pages/Upload";
import Review from "./pages/Review";
import Report from "./pages/Report";

function AuthedApp() {
  const { user, logout } = useAuth();
  const [cfg, setCfg] = useState<Config | null>(null);
  useEffect(() => {
    api.config().then(setCfg).catch(() => setCfg(null));
  }, []);

  const navItems = [
    { to: "/dashboard", label: "概览", icon: BarChart3 },
    { to: "/upload", label: "上传", icon: UploadCloud },
    { to: "/review", label: "审核台", icon: ShieldCheck },
    { to: "/report", label: "报告", icon: FileText },
  ];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <img className="brand-logo" src={baiduMapLogo} alt="百度地图" />
          <div>
            <strong>百度地图</strong>
            <span>内容审核台</span>
          </div>
        </div>
        <nav className="sidebar-nav" aria-label="主导航">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "active" : "")}>
                <span aria-hidden="true"><Icon size={15} strokeWidth={2.1} /></span>
                {item.label}
              </NavLink>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <span>工作台</span>
          <strong>{user?.display_name || "admin"}</strong>
        </div>
      </aside>
      <main className="content-shell">
        <div className="shell-actions">
          {cfg && (
            <span>模型：{cfg.reviewer}{cfg.model ? ` · ${cfg.model}` : ""}</span>
          )}
          {cfg && <span className={`dot ${cfg.key_set ? "on" : "off"}`} title={cfg.key_set ? "已配置个人 key" : "未配置个人 One API key"} />}
          <button type="button" className="btn btn-ghost btn-logout" onClick={() => { void logout(); }}>
            <LogOut size={14} strokeWidth={2.2} aria-hidden="true" />
            <span>退出</span>
          </button>
        </div>
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/login" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/review" element={<Review />} />
          <Route path="/report" element={<Report />} />
        </Routes>
      </main>
    </div>
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
