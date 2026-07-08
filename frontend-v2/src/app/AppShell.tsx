import { Activity, Moon, RefreshCw, Sun } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import AuthGate from "./AuthGate";
import { NAV_ITEMS, navMatches, type Section } from "./nav";
import { useUrlRoute } from "./useUrlRoute";
import { useThemePreference } from "../shared/theme/useThemePreference";
import { WaveBackground } from "../components/WaveBackground";

const HomeScreenPage = lazy(() => import("../pages/HomeScreenPage").then((m) => ({ default: m.HomePage })));
const HomePage = lazy(() => import("../pages/HomePage").then((m) => ({ default: m.HomePage })));
const PoolPage = lazy(() => import("../pages/PoolPage").then((m) => ({ default: m.PoolPage })));
const HistoryPage = lazy(() => import("../pages/HistoryPage").then((m) => ({ default: m.HistoryPage })));
const RecordsPage = lazy(() => import("../pages/RecordsPage").then((m) => ({ default: m.RecordsPage })));
const MatrixPage = lazy(() => import("../pages/MatrixPage").then((m) => ({ default: m.MatrixPage })));
const MetricsPage = lazy(() => import("../pages/MetricsPage").then((m) => ({ default: m.MetricsPage })));
const StrategiesPage = lazy(() => import("../pages/StrategiesPage").then((m) => ({ default: m.StrategiesPage })));
const ConfigPage = lazy(() => import("../pages/ConfigPage").then((m) => ({ default: m.ConfigPage })));
const RunDetailPage = lazy(() => import("../pages/RunDetailPage").then((m) => ({ default: m.RunDetailPage })));

function SectionView({ section }: { section: Section }) {
  switch (section) {
    case "home":
      return <HomeScreenPage />;
    case "dashboard":
      return <HomePage />;
    case "pool":
      return <PoolPage />;
    case "history":
      return <HistoryPage />;
    case "records":
      return <RecordsPage />;
    case "matrix":
      return <MatrixPage />;
    case "metrics":
      return <MetricsPage />;
    case "strategies":
      return <StrategiesPage />;
    case "config":
      return <ConfigPage />;
  }
}

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
  const { pathname, section, navigate, navigateSection } = useUrlRoute();
  const { theme, toggleTheme } = useThemePreference();
  const [refreshSignal, setRefreshSignal] = useState(0);

  // /run 子路由单独走 RunDetailPage；/data-health 已并入配置页 tab，重定向过去
  const isRunDetail = pathname === "/run";

  useEffect(() => {
    if (pathname === "/data-health") {
      navigate("/config?tab=data-health", { replace: true });
    }
  }, [pathname, navigate]);

  const handleRefresh = () => setRefreshSignal((n) => n + 1);

  return (
    <AuthGate>
      <WaveBackground theme={theme} />
      <div className="app-shell">
        <header className="topbar">
          <div
            className="topbar__brand"
            onClick={() => navigate("/")}
            role="button"
            tabIndex={0}
          >
            <span className="topbar__brand-badge">A</span>
            <span>短线驾驶舱</span>
          </div>

          <nav className="topbar__nav">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const active = navMatches(item.section, pathname);
              return (
                <button
                  key={item.section}
                  className={`topbar__nav-item${active ? " topbar__nav-item--active" : ""}`}
                  onClick={() => navigateSection(item.section)}
                >
                  <Icon size={16} />
                  {item.label}
                </button>
              );
            })}
          </nav>

          <div className="topbar__actions">
            <RunCtaButton onClick={() => navigate("/run")} />
            <button
              className="icon-button"
              onClick={handleRefresh}
              title="刷新当前页"
              aria-label="刷新"
            >
              <RefreshCw size={16} />
            </button>
            <button
              className="icon-button"
              onClick={toggleTheme}
              title={theme === "dark" ? "切换到浅色" : "切换到深色"}
              aria-label="切换主题"
            >
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
          </div>
        </header>

        <main className="app-main">
          {isRunDetail ? (
            <Suspense fallback={<PageFallback />}>
              <RunDetailPage refreshSignal={refreshSignal} />
            </Suspense>
          ) : (
            <Suspense fallback={<PageFallback />}>
              <SectionView section={section} />
            </Suspense>
          )}
        </main>
      </div>
    </AuthGate>
  );
}

/** 顶部栏的运行推荐主 CTA —— 简化版：直接跳到运行页。
 * 后续可扩展为下拉选择预设 + 确认面板（见报告 §6.2） */
function RunCtaButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="run-cta" onClick={onClick}>
      <Activity size={15} />
      运行推荐
    </button>
  );
}
