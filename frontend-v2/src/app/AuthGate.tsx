import { Loader2, LogIn } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api/client";

type Props = { children: ReactNode };

export default function AuthGate({ children }: Props) {
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    void api.authStatus()
      .then((status) => setAuthenticated(!status.enabled || status.authenticated))
      .catch(() => setError("无法确认访问状态"))
      .finally(() => setReady(true));
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
