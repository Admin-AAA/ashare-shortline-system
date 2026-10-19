import { ClipboardList, type LucideIcon, Layers, Settings2, Sun } from "lucide-react";

/** v4 信息架构：4 个一级入口 + 全局周期切换（短/中/长）。
 * 旧路由全部保留为合法 URL，归属到新的一级入口下（见 SECTION_OF_PATH），
 * 页面内部的旧链接（/records?tab=…、/history?strategy_id=…）因此无需改动。 */
export type Section = "today" | "review" | "strategies" | "system";

export type Horizon = "short" | "mid" | "long";
export const HORIZONS: { id: Horizon; label: string }[] = [
  { id: "short", label: "短线" },
  { id: "mid", label: "中线" },
  { id: "long", label: "长线" },
];

export type NavItem = { section: Section; label: string; icon: LucideIcon; path: string };
export const NAV_ITEMS: NavItem[] = [
  { section: "today", label: "今日", icon: Sun, path: "/" },
  { section: "review", label: "复盘", icon: ClipboardList, path: "/review" },
  { section: "strategies", label: "策略", icon: Layers, path: "/strategies" },
  { section: "system", label: "系统", icon: Settings2, path: "/config" },
];

/** 一级入口内的子视图（tab）。path 即旧路由，保证旧书签/内部跳转可用。 */
export const SUB_TABS: Record<Section, { path: string; label: string; shortOnly?: boolean }[]> = {
  today: [
    { path: "/pool", label: "今日出票" },
    { path: "/positions", label: "持仓 / 待卖" },
    { path: "/dashboard", label: "驾驶舱" },
    { path: "/run", label: "运行详情" },
  ],
  review: [
    { path: "/review", label: "按批次" },
    { path: "/history", label: "交易明细" },
    { path: "/records", label: "运行记录" },
  ],
  strategies: [],
  system: [
    { path: "/config", label: "配置" },
    { path: "/metrics", label: "诊断 / 研究" },
  ],
};

export function normalizePath(pathname: string): string {
  return pathname.replace(/\/+$/, "") || "/";
}

export function sectionFromPath(pathname: string): Section {
  const p = normalizePath(pathname);
  if (p === "/review" || p === "/history" || p === "/records") return "review";
  if (p === "/strategies" || p.startsWith("/strategies/")) return "strategies";
  if (p === "/config" || p === "/system" || p === "/metrics" || p === "/data-health" || p === "/backtest") return "system";
  return "today";
}

/** 需要重定向的旧路由 → 新路由（保留查询参数） */
export function redirectFor(pathname: string, search: string): { to: string; horizon?: Horizon } | null {
  const p = normalizePath(pathname);
  if (p === "/mid") return { to: "/", horizon: "mid" };
  if (p === "/long") return { to: "/", horizon: "long" };
  if (p === "/system" || p === "/backtest") return { to: `/config${search}` };
  if (p === "/data-health") return { to: "/config?tab=data-health" };
  if (p === "/today") return { to: `/${search}` };
  return null;
}

export function navMatches(section: Section, pathname: string): boolean {
  return sectionFromPath(pathname) === section;
}

export function queryParam(key: string): string | null {
  return new URLSearchParams(window.location.search).get(key);
}

export function buildPath(base: string, params?: Record<string, string | number | null | undefined>): string {
  if (!params) return base;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v != null && v !== "") qs.set(k, String(v));
  }
  const str = qs.toString();
  return str ? `${base}?${str}` : base;
}
