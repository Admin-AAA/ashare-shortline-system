import type { KeyPriceLevels } from "../types";

export type RuleKeyLevels = KeyPriceLevels;

const KEY_LEVEL_PATTERN = /关键支撑|支撑位|平台|突破位|压力位|昨日高|昨日低|MA(?:5|10|20|60)/i;

export function rulesMentionKeyLevel(entryRule?: string | null, invalidRule?: string | null): boolean {
  return KEY_LEVEL_PATTERN.test(`${entryRule ?? ""} ${invalidRule ?? ""}`);
}

export function ruleKeyLevelLabels(
  entryRule: string | null | undefined,
  invalidRule: string | null | undefined,
  levels: RuleKeyLevels | null | undefined,
): string[] {
  if (!levels) return [];
  const rule = `${entryRule ?? ""} ${invalidRule ?? ""}`;
  const labels: string[] = [];

  const add = (label: string, value: number | null | undefined) => {
    if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return;
    labels.push(`${label} ${value.toFixed(2)}`);
  };

  if (/昨日高/.test(rule)) add("昨高", levels.yesterday_high);
  if (/昨日低/.test(rule)) add("昨低", levels.yesterday_low);

  const mentions20DayPlatform = /20日[^，。；]*平台|20日平台/.test(rule);
  const mentions60DayPlatform = /60日[^，。；]*平台|60日平台/.test(rule);
  const mentionsGenericPlatform = /平台/.test(rule) && !mentions20DayPlatform && !mentions60DayPlatform;

  if (mentions20DayPlatform || mentionsGenericPlatform) {
    if (levels.platform_high_20d != null) add("20日平台", levels.platform_high_20d);
    else add("20日高参考", levels.high_20d);
  }
  if (mentions60DayPlatform) {
    if (levels.platform_high_60d != null) add("60日平台", levels.platform_high_60d);
    else add("60日高参考", levels.high_60d);
  }

  for (const days of [5, 10, 20, 60] as const) {
    if (new RegExp(`MA${days}`, "i").test(rule)) add(`MA${days}`, levels[`ma${days}`]);
  }

  if (/关键支撑|支撑位/.test(rule) && !/MA(?:5|10|20|60)/i.test(rule)) {
    const supports = (levels.support_levels ?? []).filter(
      (value): value is number => typeof value === "number" && Number.isFinite(value) && value > 0,
    );
    if (supports.length > 0) {
      supports.slice(0, 2).forEach((value, index) => add(index === 0 ? "关键支撑" : "次支撑", value));
    } else {
      add("关键支撑参考", levels.ma10 ?? levels.ma20 ?? levels.ma5);
    }
  }

  if (/突破位|压力位/.test(rule) && labels.length === 0) {
    add("压力/突破位", levels.resistance_levels?.[0]);
  }

  return [...new Set(labels)];
}
