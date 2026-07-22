/** 风险模板与数据源的中文显示映射——跨组件共享，避免重复定义。 */

export const RISK_TEMPLATE_MAP: Record<string, string> = {
  balanced: "平衡型",
  aggressive: "激进型",
  conservative: "保守型",
};

export const DATA_SOURCE_MAP: Record<string, string> = {
  local_pool_preferred: "仅用本地池",
  auto: "本地池+盘中实时",
  // 后端规范化后的实际存储值，显示时复用 auto 标签
  local_pool_realtime: "本地池+盘中实时",
};
