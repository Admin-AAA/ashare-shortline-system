/** 风险模板与数据源的中文显示映射——跨组件共享，避免重复定义。 */

export const RISK_TEMPLATE_MAP: Record<string, string> = {
  balanced: "平衡型",
  aggressive: "激进型",
  conservative: "保守型",
};

export const DATA_SOURCE_MAP: Record<string, string> = {
  local_pool_realtime: "本地池+盘中实时",
  local_pool_preferred: "本地池优先（缓存过期才刷新）",
  local_pool: "仅用本地池",
  auto: "自动（盘中实时/盘后本地）",
};
