import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { MarketState } from "./strategyRules";

/** 市场状态（GET /api/home/bootstrap → market_snapshot），5 分钟刷新 */
// 性能：上次的市场快照存 localStorage，打开页面即显示（stale-while-revalidate），
// initialDataUpdatedAt 用存储时间，过期即立刻后台刷新；返回内容与接口一致。
const BOOT_KEY = "ashare.bootstrap.v1";
type Boot = Awaited<ReturnType<typeof api.bootstrap>>;
function readBoot(): { at: number; data: Boot } | undefined {
  try { const raw = localStorage.getItem(BOOT_KEY); return raw ? JSON.parse(raw) : undefined; } catch { return undefined; }
}
export function useBootstrap() {
  const cached = readBoot();
  return useQuery({
    queryKey: ["home-bootstrap"],
    queryFn: async () => {
      const data = await api.bootstrap();
      try { localStorage.setItem(BOOT_KEY, JSON.stringify({ at: Date.now(), data })); } catch { /* quota */ }
      return data;
    },
    initialData: cached?.data,
    initialDataUpdatedAt: cached?.at,
    refetchInterval: 300_000,
    staleTime: 60_000,
  });
}

export function useMarketState(): { market: MarketState | null; tradeDate?: string; hotTheme?: string; generatedAt?: string } {
  const q = useBootstrap();
  const s = q.data?.market_snapshot;
  if (!s) return { market: null };
  const b = (s.breadth ?? {}) as Record<string, unknown>;
  const upRatio = typeof b.up_ratio === "number" ? b.up_ratio : s.sample_count ? s.up_count / s.sample_count : null;
  return {
    market: { regime: String(b.regime ?? "—"), upRatio, limitUp: s.limit_up_count, limitDown: s.limit_down_count },
    tradeDate: s.trade_date,
    hotTheme: typeof b.hot_theme === "string" ? b.hot_theme : undefined,
    generatedAt: typeof b.generated_at === "string" ? b.generated_at : undefined,
  };
}
