import { useCallback, useSyncExternalStore } from "react";
import type { Horizon } from "./nav";

/** 全局周期（短/中/长）：URL ?h= 优先，其次 localStorage，默认短线。切换时同步写回两处。 */
const KEY = "ashare.horizon";
const valid = (v: unknown): v is Horizon => v === "short" || v === "mid" || v === "long";

function initial(): Horizon {
  if (typeof window === "undefined") return "short";
  const q = new URLSearchParams(window.location.search).get("h");
  if (valid(q)) return q;
  try {
    const s = window.localStorage.getItem(KEY);
    if (valid(s)) return s;
  } catch {
    /* ignore */
  }
  return "short";
}

let current: Horizon = initial();
const listeners = new Set<() => void>();

function apply(h: Horizon) {
  if (typeof document !== "undefined") document.documentElement.dataset.h = h;
}
apply(current);

export function setHorizon(h: Horizon) {
  current = h;
  apply(h);
  try {
    window.localStorage.setItem(KEY, h);
  } catch {
    /* ignore */
  }
  const url = new URL(window.location.href);
  if (h === "short") url.searchParams.delete("h");
  else url.searchParams.set("h", h);
  window.history.replaceState({}, "", url.toString());
  listeners.forEach((l) => l());
}

export function useHorizon(): [Horizon, (h: Horizon) => void] {
  const h = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => current,
    () => current,
  );
  return [h, useCallback((v: Horizon) => setHorizon(v), [])];
}
