import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, onUnauthorized, setCsrfToken, type AuthUser } from "./api";

interface AuthState {
  user: AuthUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const redirecting = useRef(false);

  const clearSession = useCallback(() => {
    setCsrfToken(null);
    setUser(null);
    // Avoid stacking multiple redirects when several 401s fire in the same tick.
    if (!redirecting.current && window.location.pathname !== "/login") {
      redirecting.current = true;
      window.location.assign("/login");
    }
  }, []);

  useEffect(() => onUnauthorized(clearSession), [clearSession]);

  useEffect(() => {
    let cancelled = false;
    api.me()
      .then((res) => {
        if (cancelled) return;
        setCsrfToken(res.csrf_token);
        setUser(res.user);
      })
      .catch(() => {
        if (cancelled) return;
        setCsrfToken(null);
        setUser(null);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res = await api.login(username, password);
    setCsrfToken(res.csrf_token);
    setUser(res.user);
  }, []);

  const logout = useCallback(async () => {
    try { await api.logout(); } catch { /* Session may already be invalid; clear locally regardless. */ }
    setCsrfToken(null);
    setUser(null);
  }, []);

  const value = useMemo<AuthState>(() => ({ user, loading, login, logout }), [user, loading, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (ctx === undefined) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
