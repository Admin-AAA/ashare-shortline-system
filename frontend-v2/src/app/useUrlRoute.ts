import { useCallback, useEffect, useState } from "react";
import { buildPath, sectionFromPath, type Section } from "./nav";

type NavigateOptions = {
  params?: Record<string, string | number | null | undefined>;
  replace?: boolean;
};

/**
 * URL 驱动的路由 hook。
 *
 * - section / pathname 来自 window.location，popstate 同步
 * - navigate 写入 history + 触发 state 更新
 * - query params 直接从 URLSearchParams 读取
 *
 * 关键修复：导航（含仅 query 参数变化）必须强制重渲染。
 * 旧实现只在 pathname 变化时通过 setPathname 触发，导致同 pathname
 * 下 query 参数切换（如 /records 的 列表→详情→单股分析）不重渲染，
 * 表现为「URL 变了、页面不动，手动刷新才生效」。
 * 这里用 version 计数，每次 navigation / popstate 都 +1 强制重渲染。
 */
export function useUrlRoute() {
  // pathname 仅用于派生 section（主导航高亮 / 页面切换）
  const [pathname, setPathname] = useState(() => window.location.pathname);
  // version 用于强制重渲染：任何 URL 变化（pathname 或 query）都 +1
  const [version, setVersion] = useState(0);

  const sync = useCallback(() => {
    setPathname(window.location.pathname);
    setVersion((v) => v + 1);
  }, []);

  useEffect(() => {
    const onPop = () => sync();
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [sync]);

  const navigate = useCallback(
    (path: string, opts?: NavigateOptions) => {
      const target = opts?.params ? buildPath(path, opts.params) : path;
      if (opts?.replace) {
        window.history.replaceState({}, "", target);
      } else {
        window.history.pushState({}, "", target);
      }
      sync();
      // 滚动到顶部，模拟新页面进入
      window.scrollTo({ top: 0, behavior: "instant" });
    },
    [sync]
  );

  const navigateSection = useCallback(
    (section: Section, opts?: NavigateOptions) => {
      const path = section === "dashboard" ? "/dashboard" : `/${section}`;
      navigate(path, opts);
    },
    [navigate]
  );

  const section: Section = sectionFromPath(pathname);

  // query params：每次渲染都从最新 window.location 读取
  const search = typeof window !== "undefined" ? window.location.search : "";
  const params = new URLSearchParams(search);

  return { pathname, section, params, navigate, navigateSection, version };
}
