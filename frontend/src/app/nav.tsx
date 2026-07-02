import { Activity, BarChart3, DatabaseZap, FlaskConical, History, Radar, Search, Settings2, ShieldCheck, Table2 } from "lucide-react";
import type { ReactNode } from "react";

export type CockpitSection = "dashboard" | "run" | "pool" | "data" | "history" | "backtest" | "validation" | "matrix" | "stock-matrix" | "control";

export type NavItem = {
  section: CockpitSection;
  label: string;
  subtitle: string;
  icon: ReactNode;
};

export const NAV_ITEMS: NavItem[] = [
  { section: "dashboard", label: "驾驶舱", subtitle: "市场温度 / 龙头候选 / 风险雷达", icon: <Radar size={17} /> },
  { section: "run", label: "推荐运行", subtitle: "预设 / 阶段 / 心跳", icon: <Activity size={17} /> },
  { section: "pool", label: "候选解释", subtitle: "推荐卡 / 个股解释 / 复盘", icon: <BarChart3 size={17} /> },
  { section: "data", label: "数据健康", subtitle: "数据池 / 数据源 / 调度", icon: <DatabaseZap size={17} /> },
  { section: "history", label: "复盘结果", subtitle: "历史 / 结果 / 统计", icon: <History size={17} /> },
  { section: "backtest", label: "回测", subtitle: "策略 / 收益矩阵 / 版本比较", icon: <FlaskConical size={17} /> },
  { section: "validation", label: "验收安全", subtitle: "dry-run / 批次 / 门禁", icon: <ShieldCheck size={17} /> },
  { section: "matrix", label: "预设矩阵", subtitle: "多预设 / 推荐交集 / 扫描明细", icon: <Table2 size={17} /> },
  { section: "stock-matrix", label: "个股矩阵", subtitle: "全技能分析 / 历史回测 / LLM 审查", icon: <Search size={17} /> },
  { section: "control", label: "控制中心", subtitle: "预设 / 自动任务 / 模型", icon: <Settings2 size={17} /> },
];
