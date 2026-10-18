/**
 * 统一时间展示：后端 *_at 字段存的是 UTC 且不带时区后缀（naive）。
 * 这里按 UTC 解析（无偏移则补 Z），统一以北京时间（Asia/Shanghai）展示。
 * 纯日期字符串（如 trade_date "2026-10-19"）不做时区换算，原样返回。仅影响展示。
 */
export const BJ_TZ = "Asia/Shanghai";
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const HAS_TZ = /(?:z|[+-]\d{2}:?\d{2})$/i;

export function parseUtc(value?: string | null): Date | null {
  if (!value) return null;
  const raw = String(value).trim();
  if (!raw || DATE_ONLY.test(raw)) return null;
  const iso = raw.replace(" ", "T");
  const d = new Date(HAS_TZ.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function utcMs(value?: string | null): number | null {
  return parseUtc(value)?.getTime() ?? null;
}

function parts(d: Date) {
  const out: Record<string, string> = {};
  for (const p of new Intl.DateTimeFormat("en-CA", { timeZone: BJ_TZ, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(d)) out[p.type] = p.value;
  return out;
}

export type BjFormat = "datetime" | "datetime-sec" | "short" | "time" | "time-sec" | "date";

/** 北京时间格式化。datetime=YYYY-MM-DD HH:mm，short=MM-DD HH:mm，time=HH:mm。 */
export function formatBj(value?: string | null, fmt: BjFormat = "datetime", fallback = "—"): string {
  if (!value) return fallback;
  if (DATE_ONLY.test(String(value).trim())) return String(value).trim();
  const d = parseUtc(value);
  if (!d) return String(value);
  const p = parts(d);
  const date = `${p.year}-${p.month}-${p.day}`;
  switch (fmt) {
    case "date": return date;
    case "short": return `${p.month}-${p.day} ${p.hour}:${p.minute}`;
    case "time": return `${p.hour}:${p.minute}`;
    case "time-sec": return `${p.hour}:${p.minute}:${p.second}`;
    case "datetime-sec": return `${date} ${p.hour}:${p.minute}:${p.second}`;
    default: return `${date} ${p.hour}:${p.minute}`;
  }
}

/** 北京时间日期键 YYYY-MM-DD */
export function bjDateKey(value?: string | null): string | null {
  const d = parseUtc(value);
  return d ? formatBj(value, "date") : null;
}
