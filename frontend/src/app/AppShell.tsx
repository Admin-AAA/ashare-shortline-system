import { RefreshCcw, SunMoon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import ControlCenterPage from "../features/control/ControlCenterPage";
import DashboardPage from "../features/dashboard/DashboardPage";
import PresetMatrixPage from "../features/matrix/PresetMatrixPage";
import StockMatrixPage from "../features/stock-matrix/StockMatrixPage";
import { useThemePreference } from "../shared/theme/useThemePreference";
import { NAV_ITEMS, type CockpitSection } from "./nav";

const SECTION_PATHS: Record<CockpitSection, string> = {
  dashboard: "/",
  run: "/run",
  pool: "/pool",
  data: "/data",
  history: "/history",
  backtest: "/backtest",
  validation: "/validation",
  matrix: "/matrix",
  "stock-matrix": "/stock-matrix",
  control: "/control",
};

function sectionFromPath(pathname: string): CockpitSection {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  const match = NAV_ITEMS.find((item) => SECTION_PATHS[item.section] === normalized);
  return match?.section ?? "dashboard";
}

export default function AppShell() {
  const [section, setSection] = useState<CockpitSection>(() => sectionFromPath(window.location.pathname));
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const mainRef = useRef<HTMLElement | null>(null);
  const { theme, toggleTheme } = useThemePreference();
  const active = NAV_ITEMS.find((item) => item.section === section) ?? NAV_ITEMS[0];

  const resetMainScroll = useCallback(() => {
    mainRef.current?.scrollTo({ top: 0, behavior: "auto" });
    window.scrollTo({ top: 0, behavior: "auto" });
  }, []);

  const navigateToSection = useCallback((nextSection: CockpitSection, mode: "push" | "replace" = "push") => {
    setSection(nextSection);
    const nextPath = SECTION_PATHS[nextSection];
    if (window.location.pathname !== nextPath) {
      window.history[mode === "replace" ? "replaceState" : "pushState"]({ section: nextSection }, "", nextPath);
    }
    resetMainScroll();
  }, [resetMainScroll]);

  useEffect(() => {
    function handleNavigate(event: Event) {
      const nextSection = (event as CustomEvent<{ section?: CockpitSection }>).detail?.section;
      if (nextSection && NAV_ITEMS.some((item) => item.section === nextSection)) {
        navigateToSection(nextSection);
      }
    }
    window.addEventListener("ashare:navigate", handleNavigate);
    return () => window.removeEventListener("ashare:navigate", handleNavigate);
  }, [navigateToSection]);

  useEffect(() => {
    function handlePopState() {
      setSection(sectionFromPath(window.location.pathname));
      resetMainScroll();
    }
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [resetMainScroll]);

  useEffect(() => {
    resetMainScroll();
  }, [resetMainScroll, section]);

  const refreshCurrentView = useCallback(() => {
    setRefreshing(true);
    setRefreshSignal((value) => value + 1);
    window.setTimeout(() => setRefreshing(false), 1100);
  }, []);

  useEffect(() => {
    window.addEventListener("ashare:refresh-current-view", refreshCurrentView);
    return () => window.removeEventListener("ashare:refresh-current-view", refreshCurrentView);
  }, [refreshCurrentView]);

  return (
    <div className="cockpit-shell">
      <aside className="cockpit-sidebar">
        <div className="cockpit-brand">
          <div className="brand-mark">A</div>
          <div>
            <strong>短线交易驾驶舱</strong>
            <span>Live Decision System</span>
          </div>
        </div>
        <nav className="cockpit-nav" aria-label="驾驶舱导航">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.section}
              className={item.section === section ? "active" : ""}
              aria-pressed={item.section === section}
              onClick={() => navigateToSection(item.section)}
              type="button"
            >
              {item.icon}
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
      </aside>
      <main className="cockpit-main" ref={mainRef}>
        <header className="cockpit-topbar">
          <div>
            <h1>{active.label}</h1>
            <p>{active.subtitle}</p>
          </div>
          <div className="topbar-actions">
            <button type="button" onClick={refreshCurrentView} title="刷新当前视图" disabled={refreshing}>
              <RefreshCcw size={15} className={refreshing ? "spin" : ""} />
              {refreshing ? "刷新中" : "刷新"}
            </button>
            <button type="button" onClick={toggleTheme} title="切换主题">
              <SunMoon size={15} />
              {theme === "dark" ? "深色" : "浅色"}
            </button>
          </div>
        </header>
        {section === "control" ? (
          <ControlCenterPage refreshSignal={refreshSignal} />
        ) : section === "matrix" ? (
          <PresetMatrixPage refreshSignal={refreshSignal} />
        ) : section === "stock-matrix" ? (
          <StockMatrixPage refreshSignal={refreshSignal} />
        ) : (
          <DashboardPage section={section} refreshSignal={refreshSignal} />
        )}
      </main>
    </div>
  );
}
