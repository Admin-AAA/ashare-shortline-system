import { FlaskConical, RefreshCcw, SunMoon, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ErrorBoundary } from "../components/ErrorBoundary";
import StarfieldBackground from "../components/StarfieldBackground";
import ControlCenterPage from "../features/control/ControlCenterPage";
import DashboardPage from "../features/dashboard/DashboardPage";
import PresetMatrixPage from "../features/matrix/PresetMatrixPage";
import StockMatrixPage from "../features/stock-matrix/StockMatrixPage";
import { useThemePreference } from "../shared/theme/useThemePreference";
import {
  groupOfSection,
  NAV_GROUPS,
  NAV_ITEMS,
  type CockpitSection,
  type NavGroupKey,
} from "./nav";

const SECTION_PATHS: Record<CockpitSection, string> = {
  home: "/",
  dashboard: "/dashboard",
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
  return match?.section ?? "home";
}

export default function AppShell() {
  const [section, setSection] = useState<CockpitSection>(() => sectionFromPath(window.location.pathname));
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [testDrawerOpen, setTestDrawerOpen] = useState(false);
  const mainRef = useRef<HTMLElement | null>(null);
  const { theme, toggleTheme } = useThemePreference();
  const activeGroup = groupOfSection(section);

  const resetMainScroll = useCallback(() => {
    mainRef.current?.scrollTo({ top: 0, behavior: "auto" });
    window.scrollTo({ top: 0, behavior: "auto" });
  }, []);

  const navigateToSection = useCallback(
    (nextSection: CockpitSection, mode: "push" | "replace" = "push") => {
      setSection(nextSection);
      const nextPath = SECTION_PATHS[nextSection];
      if (window.location.pathname !== nextPath) {
        window.history[mode === "replace" ? "replaceState" : "pushState"](
          { section: nextSection },
          "",
          nextPath,
        );
      }
      resetMainScroll();
    },
    [resetMainScroll],
  );

  const goToGroup = useCallback(
    (key: NavGroupKey) => {
      const group = NAV_GROUPS.find((g) => g.key === key);
      if (!group) return;
      if (!group.sections.includes(section)) {
        navigateToSection(group.sections[0]);
      }
    },
    [navigateToSection, section],
  );

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

  const activeItem = NAV_ITEMS.find((item) => item.section === section) ?? NAV_ITEMS[0];
  const activeGroupDef = NAV_GROUPS.find((g) => g.key === activeGroup);
  const testGroupDef = NAV_GROUPS.find((g) => g.key === "test");

  return (
    <div className="cosmic-shell" data-active-group={activeGroup}>
      <StarfieldBackground />

      <header className="cosmic-topbar">
        <button
          type="button"
          className="cosmic-brand"
          onClick={() => navigateToSection("home")}
          title="回到首页"
        >
          <div className="brand-mark">A</div>
          <div className="brand-text">
            <strong>短线交易驾驶舱</strong>
            <span>Live Decision System</span>
          </div>
        </button>

        <nav className="cosmic-mainnav" aria-label="主导航">
          {NAV_GROUPS.filter((g) => g.key !== "test").map((g) => (
            <button
              key={g.key}
              type="button"
              className={activeGroup === g.key ? "active" : ""}
              aria-pressed={activeGroup === g.key}
              onClick={() => goToGroup(g.key)}
            >
              <span className="mainnav-label">{g.label}</span>
              <span className="mainnav-hint">{g.hint}</span>
            </button>
          ))}
        </nav>

        <div className="cosmic-actions">
          <button
            type="button"
            className={`test-trigger ${activeGroup === "test" ? "active" : ""}`}
            onClick={() => setTestDrawerOpen(true)}
            title="配置窗口：驾驶舱 / 运行 / 验收 / 控制"
          >
            <FlaskConical size={15} />
            <span>配置窗口</span>
          </button>
          <button
            type="button"
            onClick={refreshCurrentView}
            title="刷新当前视图"
            disabled={refreshing}
          >
            <RefreshCcw size={15} className={refreshing ? "spin" : ""} />
            <span>{refreshing ? "刷新中" : "刷新"}</span>
          </button>
          <button type="button" onClick={toggleTheme} title="切换主题">
            <SunMoon size={15} />
            <span>{theme === "dark" ? "深色" : "浅色"}</span>
          </button>
        </div>
      </header>

      {section !== "home" && (
        <div className="cosmic-subnav" aria-label="子导航">
          <div className="subnav-title">
            <strong>{activeGroupDef?.label}</strong>
            <span>{activeItem.label} · {activeItem.subtitle}</span>
          </div>
          <div className="subnav-items">
            {activeGroupDef?.sections.map((s) => {
              const item = NAV_ITEMS.find((n) => n.section === s);
              if (!item) return null;
              return (
                <button
                  key={s}
                  type="button"
                  className={section === s ? "active" : ""}
                  aria-pressed={section === s}
                  onClick={() => navigateToSection(s)}
                >
                  {item.icon}
                  <span>{item.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      <main className="cosmic-main" ref={mainRef}>
        {section === "home" ? (
          <section className="cosmic-home">
            <div className="home-inner">
              <h1 className="home-title">短线交易驾驶舱</h1>
              <p className="home-subtitle">于数据海洋之上，凝视纵深，捕捉信号。</p>
              <div className="home-entries">
                <button
                  type="button"
                  className="home-entry"
                  onClick={() => goToGroup("matrix")}
                >
                  <span className="entry-label">矩阵推荐</span>
                  <span className="entry-hint">单条 · 全市场</span>
                </button>
                <button
                  type="button"
                  className="home-entry"
                  onClick={() => goToGroup("strategy")}
                >
                  <span className="entry-label">策略</span>
                  <span className="entry-hint">回测 · 复盘 · 健康 · 解释</span>
                </button>
              </div>
            </div>
          </section>
        ) : (
          <section className="cosmic-content-card">
            <ErrorBoundary>
              {section === "control" ? (
                <ControlCenterPage refreshSignal={refreshSignal} />
              ) : section === "matrix" ? (
                <PresetMatrixPage refreshSignal={refreshSignal} />
              ) : section === "stock-matrix" ? (
                <StockMatrixPage refreshSignal={refreshSignal} />
              ) : (
                <DashboardPage section={section} refreshSignal={refreshSignal} />
              )}
            </ErrorBoundary>
          </section>
        )}
      </main>

      {testDrawerOpen && testGroupDef ? (
        <div
          className="cosmic-drawer-overlay"
          onClick={() => setTestDrawerOpen(false)}
          role="presentation"
        >
          <aside
            className="cosmic-drawer"
            onClick={(event) => event.stopPropagation()}
            role="dialog"
            aria-label="配置窗口"
          >
            <div className="drawer-head">
              <div>
                <strong>配置窗口</strong>
                <span>驾驶舱 / 运行 / 验收 / 控制</span>
              </div>
              <button
                type="button"
                className="drawer-close"
                onClick={() => setTestDrawerOpen(false)}
                aria-label="关闭"
              >
                <X size={18} />
              </button>
            </div>
            <nav className="drawer-nav">
              {testGroupDef.sections.map((s) => {
                const item = NAV_ITEMS.find((n) => n.section === s);
                if (!item) return null;
                return (
                  <button
                    key={s}
                    type="button"
                    className={section === s ? "active" : ""}
                    onClick={() => {
                      navigateToSection(s);
                      setTestDrawerOpen(false);
                    }}
                  >
                    {item.icon}
                    <div className="drawer-item-text">
                      <strong>{item.label}</strong>
                      <span>{item.subtitle}</span>
                    </div>
                  </button>
                );
              })}
            </nav>
          </aside>
        </div>
      ) : null}
    </div>
  );
}
