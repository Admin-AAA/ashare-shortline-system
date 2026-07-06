/** 共享工具函数 —— 跨页面重复的格式化逻辑统一抽取。 */

/** 格式化价格：null/NaN → "—"，否则保留 2 位小数。 */
export function fmtPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(2);
}
