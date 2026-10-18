import { useCallback, useEffect, useState } from "react";
import { buildPath, sectionFromPath, type Section } from "./nav";

type NavigateOptions = {
  params?: Record<string, string | number | null | undefined>;
  replace?: boolean;
};

/**
 * URL 驱动的路由 hook（全局共享 store 版本）。
 *
 * 设计要点：
 * - 所有调用 useUrlRoute() 的组件共享同一份「当前 URL」订阅（模块级 listeners）。
 * - 任一组件调用 navigate，或浏览器前进/后退（popstate），都会 emitChange →
 *   通知【所有】订阅者（含做路由决策的父组件，如 RecordsPage）强制重渲染。
 *
 * 旧实现的致命缺陷：
 * - 每个组件各自持有独立的 useState 实例（version / pathname）。
 * - 点击子组件里的链接 → 只 bump 了子组件那份 state → 父组件（负责路由切换）不重渲染
 *   → 表现为「URL 变了、页面不动、手动刷新才生效」「返回按钮点不动」。
 * 现在用模块级 store + 订阅，跨组件导航也 100% 生效。
 */

// ── 模块级共享状态 ──
let globalPathname = typeof window !== "undefined" ? window.location.pathname : "/";
const listeners = new Set<() => void>();

function emitChange() {
  globalPathname = window.location.pathname;
  listeners.forEach((l) => l());
}

// 浏览器前进/后退：全局只注册一次
if (typeof window !== "undefined") {
  window.addEventListener("popstate", emitChange);
}

export function useUrlRoute() {
  // forceTick 仅用于触发本组件重渲染；最新 URL 始终从 window.location 读取
  const [, forceTick] = useState(0);

  useEffect(() => {
    const listener = () => forceTick((t) => t + 1);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  const navigate = useCallback(
    (path: string, opts?: NavigateOptions) => {
      const target = opts?.params ? buildPath(path, opts.params) : path;
      if (opts?.replace) {
        window.history.replaceState({}, "", target);
      } else {
        window.history.pushState({}, "", target);
      }
      emitChange();
      // 滚动到顶部，模拟新页面进入
      window.scrollTo({ top: 0, behavior: "instant" });
    },
    [],
  );

  const navigateSection = useCallback(
    (section: Section | string, opts?: NavigateOptions) => {
      const map: Record<string, string> = { today: "/", dashboard: "/dashboard", pool: "/pool", history: "/history", records: "/records", metrics: "/metrics", strategies: "/strategies", config: "/config", review: "/review", system: "/config" };
      const path = map[section] ?? `/${section}`;
      navigate(path, opts);
    },
    [navigate],
  );

  const pathname = globalPathname;
  const section: Section = sectionFromPath(pathname);

  // query params：每次渲染都从最新 window.location 读取
  const search = typeof window !== "undefined" ? window.location.search : "";
  const params = new URLSearchParams(search);

  return { pathname, section, params, navigate, navigateSection };
}
