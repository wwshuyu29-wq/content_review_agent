import { useState, type FormEvent } from "react";
import { useAuth } from "../AuthContext";

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password) return;
    setError(null);
    setSubmitting(true);
    try {
      await login(username.trim(), password);
      // Hard navigate so the authenticated shell mounts fresh.
      window.location.assign("/review");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
      setSubmitting(false);
    }
  }

  return (
    <div className="login-shell">
      <form className="card login-card" onSubmit={handleSubmit}>
        <h1>内容审核台</h1>
        <p className="small">登录后进入审核工作台</p>
        <div className="field">
          <label htmlFor="username">用户名</label>
          <input
            id="username"
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            required
          />
        </div>
        <div className="field">
          <label htmlFor="password">密码</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>
        {error && <div className="msg err">{error}</div>}
        <button type="submit" className="btn btn-primary login-submit" disabled={submitting}>
          {submitting ? "登录中…" : "登录"}
        </button>
      </form>
    </div>
  );
}
