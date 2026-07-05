import {
  BarChart3,
  Compass,
  History,
  LayoutGrid,
  LineChart,
  type LucideIcon,
  Settings2,
} from "lucide-react";

/** 频率驱动的六入口导航 — 见 UX-architecture-full-report.md §5.2 */
export type Section =
  | "dashboard" // 🏠 驾驶舱（首页）
  | "pool" // 🎯 候选池
  | "history" // 📊 复盘
  | "matrix" // 📈 矩阵
  | "metrics" // 📐 指标分析
  | "config"; // ⚙️ 配置

export type NavItem = {
  section: Section;
  label: string;
  icon: LucideIcon;
};

export const NAV_ITEMS: NavItem[] = [
  { section: "dashboard", label: "驾驶舱", icon: Compass },
  { section: "pool", label: "候选池", icon: BarChart3 },
  { section: "history", label: "复盘", icon: History },
  { section: "matrix", label: "矩阵", icon: LayoutGrid },
  { section: "metrics", label: "指标", icon: LineChart },
  { section: "config", label: "配置", icon: Settings2 },
];

/** 运行详情通过 /run 访问，但不作为主导航入口 */
export type RoutePath =
  | `/${Section}`
  | "/run"
  | "/stock-matrix"
  | "/preset-matrix"
  | "/backtest"
  | "/data-health";

export const SECTION_PATHS: Record<Section, string> = {
  dashboard: "/dashboard",
  pool: "/pool",
  history: "/history",
  matrix: "/matrix",
  metrics: "/metrics",
  config: "/config",
};

/** 从 URL pathname 反向解析 section（不含 run 等子路由） */
export function sectionFromPath(pathname: string): Section {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  for (const [section, path] of Object.entries(SECTION_PATHS)) {
    if (normalized === path) return section as Section;
  }
  // /run → 不属于主导航，但默认回到驾驶舱
  if (normalized === "/run") return "dashboard";
  return "dashboard";
}

/** 主导航是否匹配当前 path（用于高亮） */
export function navMatches(section: Section, pathname: string): boolean {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  if (normalized === SECTION_PATHS[section]) return true;
  // /run、/stock-matrix 等子路由归到对应主导航
  if (section === "config" && (normalized === "/backtest" || normalized === "/data-health")) {
    return true;
  }
  if (section === "matrix" && (normalized === "/stock-matrix" || normalized === "/preset-matrix")) {
    return true;
  }
  if (section === "dashboard" && normalized === "/run") {
    return true;
  }
  return false;
}

/** 读取 URL 查询参数 */
export function queryParam(key: string): string | null {
  return new URLSearchParams(window.location.search).get(key);
}

/** 构造带查询参数的 URL */
export function buildPath(base: string, params?: Record<string, string | number | null | undefined>): string {
  if (!params) return base;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v != null && v !== "") qs.set(k, String(v));
  }
  const str = qs.toString();
  return str ? `${base}?${str}` : base;
}
