import { RefreshCcw, SunMoon } from "lucide-react";
import { useState } from "react";
import DashboardPage from "../features/dashboard/DashboardPage";
import { useThemePreference } from "../shared/theme/useThemePreference";
import { NAV_ITEMS, type CockpitSection } from "./nav";

export default function AppShell() {
  const [section, setSection] = useState<CockpitSection>("dashboard");
  const { theme, toggleTheme } = useThemePreference();
  const active = NAV_ITEMS.find((item) => item.section === section) ?? NAV_ITEMS[0];

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
              onClick={() => setSection(item.section)}
              type="button"
            >
              {item.icon}
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
      </aside>
      <main className="cockpit-main">
        <header className="cockpit-topbar">
          <div>
            <h1>{active.label}</h1>
            <p>{active.subtitle}</p>
          </div>
          <div className="topbar-actions">
            <button type="button" onClick={() => window.location.reload()} title="刷新">
              <RefreshCcw size={15} />
              刷新
            </button>
            <button type="button" onClick={toggleTheme} title="切换主题">
              <SunMoon size={15} />
              {theme === "dark" ? "深色" : "浅色"}
            </button>
          </div>
        </header>
        <DashboardPage compactMode={section === "dashboard" ? undefined : section} />
      </main>
    </div>
  );
}
