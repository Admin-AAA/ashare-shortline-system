import { Moon, PlayCircle, RefreshCw, Search, Sun } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import AuthGate from "./AuthGate";
import { HORIZONS, NAV_ITEMS, SUB_TABS, normalizePath, redirectFor, sectionFromPath } from "./nav";
import { useUrlRoute } from "./useUrlRoute";
import { setHorizon, useHorizon } from "./useHorizon";
import { useThemePreference } from "../shared/theme/useThemePreference";
import { MarketBar } from "../ui/MarketBar";
import { CommandPalette } from "../ui/CommandPalette";
import { RunConfirmPanel } from "../ui/RunConfirmPanel";

const HomePage = lazy(() => import("../pages/HomePage").then((m) => ({ default: m.HomePage })));
const PositionsPage = lazy(() => import("../pages/PositionsPage").then((m) => ({ default: m.PositionsPage })));
const PoolPage = lazy(() => import("../pages/PoolPage").then((m) => ({ default: m.PoolPage })));
const HistoryPage = lazy(() => import("../pages/HistoryPage").then((m) => ({ default: m.HistoryPage })));
const RecordsPage = lazy(() => import("../pages/RecordsPage").then((m) => ({ default: m.RecordsPage })));
const MetricsPage = lazy(() => import("../pages/MetricsPage").then((m) => ({ default: m.MetricsPage })));
const StrategiesPage = lazy(() => import("../pages/StrategiesPage").then((m) => ({ default: m.StrategiesPage })));
const StrategyDetailPage = lazy(() => import("../pages/StrategyDetailPage").then((m) => ({ default: m.StrategyDetailPage })));
const ConfigPage = lazy(() => import("../pages/ConfigPage").then((m) => ({ default: m.ConfigPage })));
const HorizonPage = lazy(() => import("../pages/HorizonPage").then((m) => ({ default: m.HorizonPage })));
const RunDetailPage = lazy(() => import("../pages/RunDetailPage").then((m) => ({ default: m.RunDetailPage })));
const BatchReviewPage = lazy(() => import("../pages/BatchReviewPage").then((m) => ({ default: m.BatchReviewPage })));

function PageFallback() {
  return (
    <div className="panel">
      <div className="skeleton" style={{ height: 28, width: 180, marginBottom: "var(--space-4)" }} />
      <div className="skeleton" style={{ height: 16, width: "100%", marginBottom: "var(--space-2)" }} />
      <div className="skeleton" style={{ height: 16, width: "92%" }} />
    </div>
  );
}

export function AppShell() {
  const { pathname, navigate } = useUrlRoute();
  const { theme, toggleTheme } = useThemePreference();
  const [horizon] = useHorizon();
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [cmdk, setCmdk] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const p = normalizePath(pathname);
  const section = sectionFromPath(p);

  useEffect(() => {
    const r = redirectFor(pathname, window.location.search);
    if (r) {
      if (r.horizon) setHorizon(r.horizon);
      navigate(r.to, { replace: true });
    }
  }, [pathname, navigate]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmdk(true); }
      else if (e.key === "Escape") { setCmdk(false); setRunOpen(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const openRun = useCallback(() => { setCmdk(false); setRunOpen(true); }, []);
  const tabs = SUB_TABS[section].filter((t) => !(horizon !== "short" && section !== "system"));
  const activeTab = (tp: string) => (tp === "/pool" ? p === "/" || p === "/pool" : p === tp);

  let content: React.ReactNode;
  if (horizon !== "short" && section !== "system") {
    content = section === "strategies" || section === "review" || section === "today"
      ? <HorizonPage horizon={horizon} />
      : null;
  } else if (p === "/run") content = <RunDetailPage refreshSignal={refreshSignal} />;
  else if (p.startsWith("/strategies/")) content = <StrategyDetailPage refreshSignal={refreshSignal} />;
  else switch (p) {
    case "/dashboard": content = <HomePage />; break;
    case "/positions": content = <PositionsPage />; break;
    case "/review": content = <BatchReviewPage />; break;
    case "/history": content = <HistoryPage />; break;
    case "/records": content = <RecordsPage />; break;
    case "/metrics": content = <MetricsPage />; break;
    case "/strategies": content = <StrategiesPage />; break;
    case "/config": content = <ConfigPage />; break;
    default: content = <PoolPage />;
  }

  return (
    <AuthGate>
      <div className="app-shell">
        <header className="topbar">
          <div className="topbar__brand" onClick={() => navigate("/")} role="button" tabIndex={0}>
            <span className="topbar__brand-badge">A</span>
            <span>A股策略系统</span>
          </div>
          <div className="hzseg" role="tablist" aria-label="策略周期">
            {HORIZONS.map((h) => (
              <button key={h.id} role="tab" aria-selected={horizon === h.id} className={horizon === h.id ? "on" : ""} onClick={() => setHorizon(h.id)}>{h.label}</button>
            ))}
          </div>
          <nav className="topbar__nav">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const active = section === item.section;
              return (
                <button key={item.section} className={`topbar__nav-item${active ? " topbar__nav-item--active" : ""}`} onClick={() => navigate(item.path)}>
                  <Icon size={16} />{item.label}
                </button>
              );
            })}
          </nav>
          <div className="topbar__actions">
            <button className="search-btn" onClick={() => setCmdk(true)}><Search size={14} /><span>搜索股票 / 页面</span><kbd>⌘K</kbd></button>
            <button className="run-cta" onClick={openRun}><PlayCircle size={15} />运行推荐</button>
            <button className="icon-button" onClick={() => setRefreshSignal((n) => n + 1)} title="刷新当前页" aria-label="刷新"><RefreshCw size={16} /></button>
            <button className="icon-button" onClick={toggleTheme} title={theme === "dark" ? "切换到浅色" : "切换到深色"} aria-label="切换主题">
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
          </div>
        </header>
        <MarketBar />
        <main className="app-main">
          {tabs.length > 0 && (
            <div className="subtabs">
              {tabs.map((t) => <button key={t.path} className={activeTab(t.path) ? "on" : ""} onClick={() => navigate(t.path)}>{t.label}</button>)}
            </div>
          )}
          <Suspense fallback={<PageFallback />}>{content}</Suspense>
        </main>
      </div>
      {cmdk && <CommandPalette onClose={() => setCmdk(false)} onRun={openRun} onToggleTheme={toggleTheme} />}
      {runOpen && <RunConfirmPanel onClose={() => setRunOpen(false)} />}
    </AuthGate>
  );
}
