import { Loader2, LogIn } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, UNAUTHORIZED_EVENT } from "../api/client";

const AUTH_OK_KEY = "ashare.auth.ok";

type Props = { children: ReactNode };

export default function AuthGate({ children }: Props) {
  // 性能：上次已通过鉴权则先渲染页面（各接口自身仍受鉴权保护），后台再校验；
  // 校验失败或任何接口返回 401 → 回到登录。避免每次打开都被 /api/auth/status 阻塞。
  const remembered = (() => { try { return localStorage.getItem(AUTH_OK_KEY) === "1"; } catch { return false; } })();
  const [ready, setReady] = useState(remembered);
  const [authenticated, setAuthenticatedState] = useState(remembered);
  const setAuthenticated = (ok: boolean) => {
    setAuthenticatedState(ok);
    try { if (ok) localStorage.setItem(AUTH_OK_KEY, "1"); else localStorage.removeItem(AUTH_OK_KEY); } catch { /* ignore */ }
  };
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    void api.authStatus()
      .then((status) => setAuthenticated(!status.enabled || status.authenticated))
      // 网关/后端暂时 5xx 时不要白屏：系统无登录概念，真正需要登录的接口会以 401 触发 UNAUTHORIZED_EVENT。
      .catch(() => { if (!remembered) setAuthenticated(true); })
      .finally(() => setReady(true));
    const onUnauthorized = () => { setAuthenticated(false); setReady(true); };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await api.login(password);
      setAuthenticated(true);
    } catch {
      setError("访问密码不正确");
    } finally {
      setLoading(false);
    }
  }

  if (!ready) {
    return (
      <div className="auth-screen">
        <div className="auth-panel">
          <Loader2 className="spin" size={22} />
          <span>正在进入驾驶舱</span>
        </div>
      </div>
    );
  }

  if (!authenticated) {
    return (
      <div className="auth-screen">
        <form className="auth-panel" onSubmit={submit}>
          <div>
            <div className="auth-title">短线交易驾驶舱</div>
            <div className="auth-subtitle">A-share Shortline Cockpit</div>
          </div>
          <input
            className="auth-input"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-label="访问密码"
          />
          {error ? <div className="auth-error">{error}</div> : null}
          <button className="primary auth-submit" type="submit" disabled={loading || !password}>
            {loading ? <Loader2 className="spin" size={16} /> : <LogIn size={16} />}
            进入
          </button>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}
