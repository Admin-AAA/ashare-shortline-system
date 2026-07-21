/**
 * soft floor 展示语义 —— 与 backend aggregator 对齐。
 *
 * 背景（self-improve T13/T14）：震荡 regime soft floor 净负（23 笔 WR 30.4%），
 * aggregator 已禁新产出；历史票仍落库。前端在「股票分明细」一屏标出 soft 与是否污染，
 * 避免运营者把 soft@震荡 当正常过线票读。
 *
 * 弱势·高潮 soft 是反转信号（历史净正）→ 只标「软降线」，不标污染。
 * 强牛·高潮降级 soft 是 A 项兜底 → 只标「软降线」。
 */
export const BANNED_SOFT_MARKET_BASES = ["震荡"] as const;

export type SoftFloorMeta = {
  dynamic_soft_floor_applied?: boolean | number | string | null;
  dynamic_soft_floor?: number | null;
  soft_floor_gap?: number | null;
  soft_floor_reason?: string | null;
  market_base?: string | null;
  market_sentiment?: string | null;
  degraded_agent_unavailable?: boolean | number | string | null;
  effective_score_line?: number | null;
  base_score_line?: number | null;
};

function truthy(v: unknown): boolean {
  if (v === true || v === 1) return true;
  if (typeof v === "string") return ["1", "true", "yes"].includes(v.toLowerCase());
  return false;
}

/** 该票是否经 soft floor 放行。 */
export function isSoftFloorApplied(meta: SoftFloorMeta | null | undefined): boolean {
  if (!meta) return false;
  return truthy(meta.dynamic_soft_floor_applied);
}

/** soft@已禁 regime（当前=震荡）→ 历史污染，新 run 不应再出现。 */
export function isBannedSoftFloor(meta: SoftFloorMeta | null | undefined): boolean {
  if (!isSoftFloorApplied(meta)) return false;
  const base = String(meta?.market_base || "");
  return (BANNED_SOFT_MARKET_BASES as readonly string[]).includes(base);
}

const REASON_LABEL: Record<string, string> = {
  near_market_line: "近市场线",
  per_card_penalty: "个股惩罚挡光",
  degraded_wide_gap: "降级宽距兜底",
};

/** soft floor 展示文案。 */
export function describeSoftFloor(meta: SoftFloorMeta | null | undefined): {
  applied: boolean;
  banned: boolean;
  badge: string;
  title: string;
  line: string;
} {
  if (!isSoftFloorApplied(meta)) {
    return { applied: false, banned: false, badge: "", title: "", line: "" };
  }
  const base = meta?.market_base || "?";
  const sent = meta?.market_sentiment || "?";
  const reasonKey = meta?.soft_floor_reason || "";
  const reason = REASON_LABEL[reasonKey] || reasonKey || "动态软降线";
  const banned = isBannedSoftFloor(meta);
  const floor = meta?.dynamic_soft_floor;
  const floorText = floor != null && Number.isFinite(Number(floor)) ? Number(floor).toFixed(1) : "—";
  const regime = `${base}·${sent}`;

  if (banned) {
    return {
      applied: true,
      banned: true,
      badge: "软降·污染",
      title:
        `soft@${regime} 历史污染：有效线被压到 ${floorText}（${reason}）。` +
        `aggregator 已禁「震荡」soft，新 formal 不再产出；` +
        `D 项近10日 PL 偏低先查 soft 污染，勿当策略退化加 entry。`,
      line: `soft@${regime} 线 ${floorText} · ${reason} · 历史污染`,
    };
  }
  return {
    applied: true,
    banned: false,
    badge: "软降线",
    title: `soft@${regime} 动态软降线 ${floorText}（${reason}）——非硬过线，注意样本质量。`,
    line: `soft@${regime} 线 ${floorText} · ${reason}`,
  };
}
