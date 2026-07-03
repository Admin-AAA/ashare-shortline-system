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
 * 替代原方案基于 sessionStorage + CustomEvent 的跨页通信。
 */
export function useUrlRoute() {
  const [pathname, setPathname] = useState(() => window.location.pathname);

  useEffect(() => {
    const onPop = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback(
    (path: string, opts?: NavigateOptions) => {
      const target = opts?.params ? buildPath(path, opts.params) : path;
      if (window.location.pathname === path && window.location.search === "") {
        // 仅参数变化时也走 replace，避免历史栈膨胀
      }
      if (opts?.replace) {
        window.history.replaceState({}, "", target);
      } else {
        window.history.pushState({}, "", target);
      }
      setPathname(target.replace(/\?.*$/, ""));
      // 滚动到顶部，模拟新页面进入
      window.scrollTo({ top: 0, behavior: "instant" });
    },
    []
  );

  const navigateSection = useCallback(
    (section: Section, opts?: NavigateOptions) => {
      const path = section === "dashboard" ? "/dashboard" : `/${section}`;
      navigate(path, opts);
    },
    [navigate]
  );

  const section: Section = sectionFromPath(pathname);

  // query params
  const search = typeof window !== "undefined" ? window.location.search : "";
  const params = new URLSearchParams(search);

  return { pathname, section, params, navigate, navigateSection };
}
