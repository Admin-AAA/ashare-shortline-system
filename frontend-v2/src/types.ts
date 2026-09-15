export type ScheduledTaskType =
  | "data_sync"
  | "recommendation_run"
  | "preset_matrix_run"
  | "pool_refresh"
  | "data_backfill"
  | "formal_safety_net";

export type PresetSkill = {
  skill_id: string;
  name: string;
  weight: number;
  sort_order: number;
};

export type Preset = {
  preset_id: string;
  name: string;
  description?: string | null;
  skip_aux_refresh: boolean;
  market_scope: string;
  market_scope_value?: string | null;
  risk_template: string;
  score_line: number;
  output_count: number;
  data_source: string;
  hide_recent_repeats: boolean;
  status: string;
  skills: PresetSkill[];
};

export type ScheduledTask = {
  task_id: string;
  name: string;
  task_type: ScheduledTaskType;
  cron_expression: string;
  trigger_time?: string | null;
  payload: Record<string, unknown>;
  enabled: boolean;
  is_running: boolean;
  last_run_at?: string | null;
  last_status?: string | null;
  last_error?: string | null;
  updated_at?: string | null;
};

export type ScheduledTaskRun = {
  task_run_id: string;
  task_id: string;
  task_name: string;
  task_type: ScheduledTaskType;
  trigger_source: string;
  started_at?: string | null;
  finished_at?: string | null;
  status: string;
  summary?: string | null;
  summary_text: string;
  error_message?: string | null;
  duration_seconds?: number | null;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  related_run_id?: string | null;
};

export type ScheduledTaskPayload = {
  task_id: string;
  name: string;
  task_type: ScheduledTaskType;
  trigger_time?: string;
  cron_expression?: string;
  payload?: Record<string, unknown>;
  enabled: boolean;
};

export type RunSummary = {
  run_id: string;
  run_name: string;
  preset_id?: string | null;
  preset_name: string;
  trade_date: string;
  run_mode?: string;
  /** 评估范围：live_forward=正式推荐（实盘），historical_replay=历史回放/补跑 */
  evaluation_scope?: string;
  started_at?: string | null;
  finished_at?: string | null;
  market_scope: string;
  market_scope_value?: string | null;
  risk_template: string;
  score_line: number;
  universe_count: number;
  recommended_count: number;
  status: string;
  error_message?: string | null;
  heartbeat_at?: string | null;
  heartbeat_age_seconds?: number | null;
  heartbeat_stale_seconds?: number;
  is_stale?: boolean;
  progress_phase?: string | null;
  progress_current?: number;
  progress_total?: number;
  progress_pct?: number | null;
  progress_message?: string | null;
  interrupted_at?: string | null;
  checkpoint_phase?: string | null;
  skill_count: number;
  /** 聚合收益率（推荐股票 1/3/5 日 + 至今均值，百分比），由 records 列表接口补算 */
  returns?: {
    return_1d: number | null;
    return_3d: number | null;
    return_5d: number | null;
    return_to_date?: number | null;
  } | null;
  hit_rate: number;
  result_summary: {
    top_codes?: string[];
    avg_score?: number;
    opportunity?: string;
    no_opportunity_reason?: string;
    data_source_mode?: string;
    data_source?: string;
    quote_source?: string;
    kline_sources?: Record<string, number>;
    agent_runtime?: string;
    matrix_batch_id?: string;
    matrix_batch_name?: string;
    matrix_run?: boolean;
    main_industries?: string[];
    unified_quote_total?: number;
    base_filter_eligible_count?: number;
    daily_kline_coverage_count?: number;
    filtered_kline_coverage_count?: number;
    actual_scan_count?: number;
    skill_input_count?: number;
    demo_limited?: boolean;
    scan_limited?: boolean;
    skill_limited?: boolean;
    skip_reasons?: Record<string, unknown>;
    pipeline?: {
      unified_quote_total?: number;
      base_filter_eligible_count?: number;
      daily_kline_coverage_count?: number;
      filtered_kline_coverage_count?: number;
      actual_scan_count?: number;
      skill_input_count?: number;
      skill_review_success_count?: number;
      skill_agent_call_count?: number;
      skill_rule_candidate_count?: number;
      skill_gate_candidate_count?: number;
      market_candidate_count?: number;
      lookahead_multiplier?: number;
      lookahead_count?: number;
      skill_review_limit?: number;
      skill_review_concurrency?: number;
      demo_limited?: boolean;
      scan_limited?: boolean;
      skill_limited?: boolean;
      skip_reasons?: Record<string, unknown>;
      auxiliary_data?: Record<string, unknown>;
      skill_gate?: {
        mode?: string;
        min_score?: number;
        min_per_skill?: number;
        min_total?: number;
        max_total?: number;
        quality_tiers?: Record<string, string>;
        per_skill?: Record<string, {
          scanned: number;
          rule_eligible: number;
          gate_pass: number;
          llm_reviewed: number;
          skipped_hard_veto: number;
          skipped_low_score: number;
          skipped_no_rule: number;
          high_quality_count: number;
          mid_quality_count: number;
          dynamic_max_per_skill: number;
        }>;
      };
    };
  };
};

export type DataQualitySummary = {
  quote_status: string;
  quote_source_count: number;
  kline_status: string;
  kline_days: number;
  auxiliary_coverage: number;
  missing_fields: string[];
};

export type MarketTemperature = {
  trade_date: string;
  sample_count: number;
  up_count: number;
  down_count: number;
  limit_up_count: number;
  limit_down_count: number;
  up_ratio: number;
  heat_score: number;
  regime: string;
  main_industries: string[];
};

export type CockpitRecommendation = {
  recommendation_id: string;
  run_id: string;
  trade_date: string;
  recommended_at?: string | null;
  code: string;
  name: string;
  industry?: string | null;
  final_score: number;
  score_gap?: number | null;
  bucket: string;
  setup_type?: string | null;
  rationale: string;
  risk_level: "low" | "medium" | "high" | "unknown";
  risk_tags: string[];
  info_tags: string[];
  status: string;
  watch_status: string;
  consensus_label: string;
  data_quality: DataQualitySummary;
  preset_name?: string | null;
  preset_id?: string | null;
  run_name?: string | null;
  ideal_buy?: number | null;
  secondary_buy?: number | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  recommend_price?: number | null;
  entry_rule?: string | null;
  invalid_rule?: string | null;
  degraded_reason?: string | null;
  review_status?: "verified" | "partial" | "degraded" | "rule_only" | null;
  market_context?: {
    main_industries?: string[];
    breadth?: Record<string, unknown>;
  };
  skill_breakdown?: SkillBreakdownItem[];
  /** LLM 审查顶层快捷字段（B 项：候选池卡片不展开即可见 LLM 态度） */
  llm_stance?: string | null;
  llm_delta?: number | null;
};

export type SkillBreakdownItem = {
  strategy_name: string;
  score: number;
  phase: string;
  setup_match: string;
  confidence: number;
  evidence: string[];
  hard_veto: boolean;
  veto_reason: string;
};

/** 单只股票已结算推荐的历史表现（后端 /api/recommendations/outcome-summary 返回） */
export type OutcomeSummary = {
  settled: number; // 已结算（success/failed）推荐数
  wins: number; // 其中成功数
  win_rate: number; // 胜率 %
  avg_return: number; // 平均净收益 %
};

/** 近 N 个已结算交易日 formal 推荐聚合表现；零推荐/pending 日期不占窗口 */
export type WinRateSummary = {
  window_trade_days: number;
  trade_dates: string[];
  sample_count: number; // 已结算推荐数（未结算不计入分母）
  success_count: number;
  win_rate: number | null; // 胜率 %，无样本时为 null（诚实不虚构）
  avg_return: number | null; // 平均净收益 %
  profit_loss_ratio: number | null; // 盈亏比
  max_loss?: number | null; // 窗口内单笔最差收益 %（D 项「最大亏损」口径，与 self_check 一致）
  // 净成本口径（扣 0.07%/笔：佣金万1双边 + 印花税卖出）—— D 项目标定义为扣费后须达标
  net_win_rate?: number | null; // 净胜率 %
  net_avg_return?: number | null; // 净平均收益 %
  net_profit_loss_ratio?: number | null; // 净盈亏比
  // 去污染反事实（主口径不变）：剔除 pure 低线(<72)+所有 soft 票 后净 PL（T39 对齐 self_check）
  // soft floor 票不属于「当前正式门槛下硬过线」——07-16 的 13 笔 "clean" 全是 soft@震荡
  clean_sample_count?: number; // clean 样本数（excl soft + low line）；=0 表示近 N 日全部受污染
  clean_net_profit_loss_ratio?: number | null; // clean PL；null 表示无 clean 样本
  pollution_n?: number; // 污染独立 (code,date) 数（pure 低线 + 所有 soft）
  pollution_pl_delta?: number | null; // clean_pl - raw_pl，>0 表示污染拖累 PL
};

// LLM 审查层健康度（驾驶舱状态条「AI 审查」徽章数据源 — 北极星 B 项可见性缺口修复）
// 以 llm_review_cache 最近命中时间为新鲜度代理：缓存只在 LLM 审查【成功】时写入，
// key 失效 / provider 故障时审查静默停摆 → 推荐缺 LLM delta 而用户无感知。此类型让该状态可见。
export type LlmCallHealthRun = {
  trade_date: string;
  success: number;
  failed: number;
  rate: number; // 成功率（0-100）
  unhealthy: boolean; // 成功率 <80%
  top_reasons: { reason: string; count: number }[]; // 失败原因 top3
  sample_errors: string[]; // 失败样本错误文本（最多3个，截120字）
};

export type LlmCallHealth = {
  runs: LlmCallHealthRun[];
  any_unhealthy: boolean; // 最近5个 run 是否有低成功率
};

export type LlmReviewHealth = {
  last_review_at: string | null; // 最近一次成功 LLM 审查时间（ISO）
  recent_count_24h: number; // 近 24h 成功审查次数
  total_entries: number; // 缓存总条数
  model: string | null; // 最近一次审查使用的模型
  status: "ok" | "stale" | "never"; // 审查层健康度（缓存新鲜度代理）
  checked_at: string | null;
  call_health: LlmCallHealth; // 最近5个 formal run 的 skill_review 调用成功率 + 失败原因
};

/** 模型路由可用性检查结果（/api/llm/route-health） */
export type LlmRouteCircuit = {
  model_id: string;
  consecutive_failures: number;
  blocked: boolean; // 熔断中
  blocked_until: number | null;
} | null;

export type LlmRouteHealthItem = {
  route_id: string;
  name: string;
  base_url: string;
  model: string;
  reachable: boolean; // base_url 可达（GET /models 返回 <500）
  status_code: number | null;
  model_count: number; // /models 返回的模型数
  error: string | null;
  circuit: LlmRouteCircuit; // circuit_breaker 状态
};

/** 时态一致性检查结果（/api/admin/temporal-consistency）：验证推荐/回测数据无时间穿越。 */
export type TemporalConsistency = {
  status: "no_data" | "completed" | string;
  run_count?: number;
  recommendation_count?: number;
  check_summary?: {
    total?: number;
    passed?: number;
    warnings?: number;
    failed?: number;
  };
  violations?: Array<Record<string, unknown>>;
};

/** D 项诊断摘要（/api/metrics/d-diagnostics）：真残留毒区 + 伪活跃 + phase 信息量，与 self_check 同源。 */
/** 资金流因子预测力分析 —— /api/metrics/capital-flow-factor 返回。
 * T1 新因子研发，待前瞻验证候选（D 路径穷尽时不直接加 strategy.yaml）。 */
export type CapitalFlowFactorFactor = {
  field: string;
  name: string;
  n: number;
  r_pb: number | null;
  wr_pos: number | null;
  wr_neg: number | null;
  delta_pp: number | null;
  note: string | null;
};

export type CapitalFlowFactorResponse = {
  sample_count: number;
  factors: CapitalFlowFactorFactor[];
  best_factor: {
    field: string;
    name: string;
    n: number;
    r_pb: number;
    guard: {
      support: number;
      independent_days: number;
      top_day: string;
      top_day_ratio: number;
      trustworthy: boolean;
      reason: string;
    };
  } | null;
  note: string;
};

export type DDiagnostics = {
  residual_n: number;
  modes: Array<{ mode: string; n: number; avg_return: number }>;
  pseudo_active: {
    weak_active_n: number;
    pseudo_n: number;
    still_active_n: number;
    days: Array<{
      trade_date: string;
      n: number;
      pseudo: number;
      new_sent?: string;
      lu?: number;
      ld?: number;
    }>;
    note?: string | null;
  } | null;
  /** T35：residual triage（污染/伪活跃/单日簇/稀疏） */
  triage?: {
    n: number;
    pure_pollution: number;
    pseudo_active: number;
    soft_allowed?: number;
    day_clustered: number;
    sparse: number;
    ban_n?: number;
    clustered_days?: string[];
    sparse_n?: number;
    /** T36：稀疏明细（跨日不可合并加 entry） */
    sparse_items?: Array<{
      rid?: string;
      trade_date?: string;
      code?: string;
      mode?: string;
      regime?: string;
      sentiment?: string;
      ret?: number | null;
      score?: number | null;
      soft_applied?: boolean;
      note?: string;
    }>;
    /** soft@允许档失败明细（禁当 sparse entry） */
    soft_allowed_items?: Array<{
      rid?: string;
      trade_date?: string;
      code?: string;
      mode?: string;
      regime?: string;
      sentiment?: string;
      ret?: number | null;
      note?: string;
    }>;
    /** 稀疏同日（未达 day_cluster n≥4）仍禁合并 entry */
    sparse_same_day?: string | null;
    note?: string | null;
    actionable?: boolean;
  } | null;
  /** phase/sentiment 信息量（T5→T7：self_check 每轮可见后指标页一屏同步） */
  phase_information?: {
    n: number;
    overall_winrate: number;
    regime_chi2: number;
    regime_p: number;
    regime_significant: boolean;
    sentiment_chi2: number;
    sentiment_p: number;
    sentiment_significant: boolean;
    notable_low_buckets: Array<{
      dim?: string;
      name?: string;
      n?: number;
      winrate?: number;
      net_plr?: number;
      avg_ret?: number;
    }>;
    note?: string | null;
  } | null;
  forward_epoch?: string | null;
  note?: string | null;
};

/** D 项净成本 + 板块(20cm/10cm)分解（与核心 KPI 共用正式实盘口径）。 */
export type DNetBreakdown = {
  window_days: number | null;
  sample_count: number;
  net: {
    sample_count: number;
    win_rate: number | null;
    net_win_rate: number | null;
    avg_return: number | null;
    net_avg_return: number | null;
    profit_loss_ratio: number | null;
    net_profit_loss_ratio: number | null;
    max_loss: number | null;
  } | null;
  board: {
    "20cm": {
      n: number;
      win_rate: number;
      net_win_rate: number;
      avg_return: number;
      net_avg_return: number;
      profit_loss_ratio: number | null;
      net_profit_loss_ratio: number | null;
    } | null;
    "10cm": {
      n: number;
      win_rate: number;
      net_win_rate: number;
      avg_return: number;
      net_avg_return: number;
      profit_loss_ratio: number | null;
      net_profit_loss_ratio: number | null;
    } | null;
  };
  date_range?: DateRangeMeta;
};

export type RiskRadar = {
  high: number;
  medium: number;
  info: number;
  tags: string[];
};

export type RunProgress = {
  run_id?: string | null;
  status: string;
  phase?: string | null;
  progress_pct?: number | null;
  message: string;
  error_message?: string | null;
  heartbeat_stale: boolean;
  resumable: boolean;
};

export type DashboardDataHealth = {
  status: string;
  can_run_local_recommendation: boolean;
  quote_total: number;
  active_quote_count: number;
  eligible_quote_count: number;
  checked_candidate_count?: number;
  ready_candidate_count?: number;
  example_count?: number;
  example_limit?: number;
  scope_note?: string;
  bar_coverage_pct: number;
  eligible_bar_coverage_pct: number;
  missing_bar_count: number;
  stale_bar_count: number;
  short_history_count: number;
  immature_history_count?: number;
  total_short_history_count?: number;
  messages: string[];
  examples: DashboardDataHealthExample[];
};

export type DashboardDataHealthExample =
  | string
  | {
      code?: string | null;
      name?: string | null;
      reason?: string | null;
      bar_count?: number | null;
      latest_bar_date?: string | null;
      quote_date?: string | null;
    };

export type TaskHealthSummary = {
  enabled_count?: number;
  running_count: number;
  failed_count: number;
  last_errors?: string[];
};

export type QualityScorecardDimension = {
  label: string;
  score: number;
  status: string;
  highlights: string[];
  metrics: Record<string, unknown>;
};

export type QualityScorecard = {
  overall_score: number;
  overall_status: string;
  highlights: string[];
  dimensions: Record<string, QualityScorecardDimension>;
};

export type ValidationSummary = {
  last_status: string;
  alerts: {
    high?: number;
    medium?: number;
    low?: number;
    messages?: string[];
  };
  quality_gate?: {
    overall_score?: number;
    overall_status?: string;
    validation_score?: number;
    validation_status?: string;
    execution_score?: number;
    execution_status?: string;
    highlights?: string[];
  };
};

export type DashboardHardeningEvidence = {
  run_id: string;
  evidence_section: "LIVE_FORWARD" | "HISTORICAL_RECONSTRUCTION" | "RESEARCH";
  evidence_class: string;
  actual_generated_at: string | null;
  business_date: string;
  signal_date: string;
  trade_date: string;
  reconstruction_label: string | null;
  data_quality_decision: string;
  data_quality_reasons: string[];
  risk_gate: string;
  risk_reasons: string[];
  last_updated_at: string | null;
  status: string;
};

export type DashboardHardening = {
  generated_at: string;
  last_updated_at: string | null;
  stale_threshold_seconds: number;
  stale: boolean;
  current_run: DashboardHardeningEvidence | null;
  current_label: string;
  today_live_completed: boolean;
  data_quality_decision: string;
  risk_gate: string;
  evidence: Record<"LIVE_FORWARD" | "HISTORICAL_RECONSTRUCTION" | "RESEARCH", DashboardHardeningEvidence[]>;
  observability?: {
    registered: string[];
    conditions: Array<{ kind: string; threshold: number | null; enabled: boolean; severity: string }>;
    active_alerts: Array<{ kind: string; severity: string; reason_code: string }>;
  };
};

export type DashboardOverview = {
  market: MarketTemperature;
  recommendations: CockpitRecommendation[];
  risk_radar: RiskRadar;
  current_run: RunProgress;
  data_health: DashboardDataHealth;
  task_health: TaskHealthSummary;
  validation: ValidationSummary;
  quality_scorecard: QualityScorecard;
  hardening?: DashboardHardening;
};

export type DataHealthTableDiagnostic = {
  label?: string;
  table?: string;
  total_rows?: number;
  min_date?: string | null;
  max_date?: string | null;
  critical?: boolean;
  error?: string | null;
};

export type RecentTradeCoverageTable = {
  label?: string;
  critical?: boolean;
  covered_count?: number;
  expected_count?: number;
  coverage_rate?: number;
  missing_dates?: string[];
};

export type DataHealthDiagnostic = {
  score?: number;
  reference_trade_date?: string | null;
  reference_trade_date_source?: string | null;
  reference_trade_date_note?: string | null;
    tables?: Record<string, DataHealthTableDiagnostic>;
  lag_days?: Record<string, number>;
  issues?: Array<{ severity: string; message: string }>;
  data_sources?: DataSourceHealthSummary;
  data_domains?: DataDomainCoverageSummary;
  recommendation_summary?: {
    total?: number;
    by_run_id?: Record<string, number>;
    latest_trade_date?: string | null;
    labeled_count?: number;
  };
  recent_trade_day_coverage?: {
    window?: number;
    trade_days?: string[];
    summary?: {
      max_missing_days?: number;
      high_impact_tables?: string[];
      warning_tables?: string[];
    };
    tables?: Record<string, RecentTradeCoverageTable>;
  };
};

export type DataHealthRemediationSnapshot = {
  score?: number;
  issue_count?: number;
  diagnosis_failed?: boolean;
  diagnosis_skipped?: boolean;
  diagnosis_stage?: string;
  error_type?: string;
  error?: string | null;
  recent_summary?: {
    max_missing_days?: number;
    high_impact_tables?: string[];
    warning_tables?: string[];
  };
};

export type DataHealthRemediationAction = {
  table?: string;
  scope?: string;
  trade_date?: string;
  trade_date_start?: string;
  trade_date_end?: string;
  status?: string;
  row_count?: number;
  requested_code_count?: number;
  requested_date_count?: number;
  code_limit?: number;
  error?: string;
  error_type?: string;
  rollback_hint?: string;
};

export type DataHealthRemediationOperationLog = {
  table?: string;
  status?: string;
  target?: string;
  row_count?: number;
  rollback_hint?: string;
  error?: string;
  error_type?: string;
};

export type DataHealthRollbackGuidance = {
  mode?: string;
  message?: string;
  keys?: string[];
};

export type DataHealthRemediationTableDelta = {
  before_lag_days?: number | null;
  after_lag_days?: number | null;
  lag_days_delta?: number | null;
  before_missing_days?: number;
  after_missing_days?: number;
  target_missing_day_count?: number;
  resolved_missing_day_count?: number;
  unresolved_missing_day_count?: number;
  remediation_coverage_rate?: number;
  before_missing_dates?: string[];
  after_missing_dates?: string[];
  resolved_missing_dates?: string[];
  unresolved_missing_dates?: string[];
};

export type DataHealthRemediationResult = {
  status: string;
  before?: DataHealthRemediationSnapshot;
  after?: DataHealthRemediationSnapshot;
  actions?: DataHealthRemediationAction[];
  operation_log?: DataHealthRemediationOperationLog[];
  rollback_guidance?: DataHealthRollbackGuidance;
  action_summary?: {
    planned_count?: number;
    completed_count?: number;
    empty_count?: number;
    skipped_count?: number;
    failed_count?: number;
  };
  provider_activity?: {
    used_providers?: string[];
    route_policy?: string;
    minute_scope?: string;
    providers?: Array<{
      source?: string;
      provider_name?: string;
      request_count?: number;
      success_count?: number;
      failure_count?: number;
      empty_count?: number;
      last_api?: string | null;
      status?: string | null;
      breaker_state?: string | null;
    }>;
  };
  comparison?: {
    status?: string;
    reason?: string;
    message?: string;
    error_type?: string;
    error?: string;
    issue_count_delta?: number;
    score_delta?: number;
    max_missing_days_delta?: number;
    target_missing_day_count?: number;
    resolved_missing_day_count?: number;
    unresolved_missing_day_count?: number;
    remediation_coverage_rate?: number;
    tables_fully_remediated?: string[];
    tables_still_missing?: string[];
    table_deltas?: Record<string, DataHealthRemediationTableDelta>;
  };
  remaining_issues?: Array<{ severity: string; message: string }>;
};


export type PoolRefreshResult = {
  refreshed: number;
  meta: {
    status: string;
    count: number;
    sources: string[];
    source_count: number;
    cache_age_seconds: number;
    errors: string[];
  };
};

export type LocalPoolKlineGapCounts = {
  candidate_count: number;
  checked_count: number;
  ready_count: number;
  missing_count: number;
  short_history_count: number;
  immature_history_count?: number;
  stale_count: number;
  gap_count: number;
  latest_allowed_bar_date?: string | null;
};

export type LocalPoolKlineGapOperation = {
  code?: string | null;
  name?: string | null;
  reason?: string | null;
  status?: string | null;
  row_count?: number;
  bar_count_before?: number;
  latest_bar_date_before?: string | null;
  latest_trade_date?: string | null;
  quote_date?: string | null;
  error?: string | null;
  error_type?: string | null;
};

export type LocalPoolGapSyncResult = {
  status: string;
  dry_run: boolean;
  days: number;
  min_bars: number;
  code_limit: number;
  effective_code_limit?: number;
  target_count: number;
  processed_count: number;
  succeeded_count: number;
  unresolved_count: number;
  failed_count: number;
  row_count: number;
  before: LocalPoolKlineGapCounts;
  after: LocalPoolKlineGapCounts;
  comparison?: {
    before_candidate_count?: number;
    after_candidate_count?: number;
    before_ready_count?: number;
    after_ready_count?: number;
    ready_count_delta?: number;
    before_gap_count?: number;
    after_gap_count?: number;
    gap_count_delta?: number;
    target_gap_count?: number;
    resolved_target_count?: number;
    unresolved_target_count?: number;
    failed_target_count?: number;
    candidate_coverage_rate_before?: number;
    candidate_coverage_rate_after?: number;
    candidate_coverage_rate_delta?: number;
    remediation_coverage_rate?: number;
  };
  sync_summary?: {
    total_log_count?: number;
    visible_log_count?: number;
    status_counts?: Record<string, number>;
    failure_reasons?: Array<{ error_type?: string; message?: string; count?: number }>;
  };
  operation_log?: LocalPoolKlineGapOperation[];
};

export type HomeBootstrap = {
  presets: Preset[];
  recent_runs: {
    latest: RunSummary | null;
  };
  market_snapshot: MarketSnapshot;
  skill_summary: {
    enabled_count: number;
    disabled_count: number;
  };
};

export type MarketSnapshot = {
  snapshot_id: string;
  trade_date: string;
  sample_count: number;
  up_count: number;
  down_count: number;
  limit_up_count: number;
  limit_down_count: number;
  main_industries: string[];
  breadth: Record<string, unknown>;
};

// 后端 list_data_sources 实际返回 quote/kline/news/dragon_tiger/limit_up/concept_sector/capital_flow 等
// 多种 source_type，不限于 quote/kline——用 string 覆盖全集，避免新增类型时漏改
export type DataSourceType = string;

export type DataSourceStatus = "healthy" | "degraded" | "disabled" | "untested";

export type DataSourceHealthRow = {
  source: string;
  source_type: DataSourceType;
  provider_name?: string;
  layer?: string;
  enabled: boolean;
  configured?: boolean;
  priority: number;
  note?: string | null;
  success_count: number;
  failure_count: number;
  empty_count: number;
  success_rate?: number | null;
  latency_ms?: number | null;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  last_error?: string | null;
  last_api?: string | null;
  status: DataSourceStatus;
  breaker_state?: string | null;
  open_circuits?: string[];
  auth_mode?: string | null;
  rate_limit_per_minute?: number | null;
  api_rate_limits?: Record<string, number>;
  max_concurrency?: number | null;
  health_scope?: string | null;
  updated_at?: string | null;
};

/** 数据健康页「数据源接入」卡片用的行（source_type 覆盖全部类型，不限于 quote/kline）。 */
export type DiagnosticDataSource = {
  source: string;
  source_type: string;
  provider_name?: string;
  layer?: string;
  enabled: boolean;
  configured?: boolean;
  priority: number;
  note?: string | null;
  success_count: number;
  failure_count: number;
  empty_count: number;
  success_rate?: number | null;
  latency_ms?: number | null;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  last_error?: string | null;
  last_api?: string | null;
  status: string;
  breaker_state?: string | null;
  rate_limit_per_minute?: number | null;
  updated_at?: string | null;
};

export type DataSourceHealthSummary = {
  sources?: DiagnosticDataSource[];
  working_count?: number;
  broken_count?: number;
  untested_count?: number;
  disabled_count?: number;
  total?: number;
  working_sources?: DiagnosticDataSource[];
  broken_sources?: DiagnosticDataSource[];
  untested_sources?: DiagnosticDataSource[];
  disabled_sources?: DiagnosticDataSource[];
  score_penalty?: number;
  score_policy?: string;
  provider_redundancy?: {
    configured_count?: number;
    healthy_count?: number;
    degraded_count?: number;
    fallback_available?: boolean;
    score_penalty?: number;
  };
};

/** 数据域（指标级）覆盖明细 —— 对应后端 _data_domain_coverage 单个域。 */
export type DataDomainCoverage = {
  key: string;
  label: string;
  category: string;
  status: "covered" | "stale" | "empty" | "error";
  total_rows: number | null;
  total_rows_estimated?: boolean;
  max_date: string | null;
  critical: boolean;
  fresh_window_days: number | null;
  gap_days: number | null;
  note?: string | null;
};

/** 单个类别下的数据域分组。 */
export type DataDomainCategory = {
  category: string;
  domains: DataDomainCoverage[];
  covered_count: number;
  stale_count: number;
  empty_count: number;
};

/** 数据域覆盖汇总 —— 数据健康页「数据域接入覆盖」卡片消费。 */
export type DataDomainCoverageSummary = {
  categories: DataDomainCategory[];
  total: number;
  covered_count: number;
  stale_count: number;
  empty_count: number;
  coverage_rate: number;
  score_penalty: number;
  reference_trade_date: string | null;
  empty_domains?: DataDomainCoverage[];
  stale_domains?: DataDomainCoverage[];
};

export type ModelRoute = {
  route_id: string;
  name: string;
  provider: string;
  base_url: string;
  model: string;
  priority: number;
  enabled: boolean;
  uses_default_key: boolean;
  default_key_available: boolean;
  has_api_key: boolean;
  key_source: string;
  api_key_masked: string;
  // 独立配置参数（为空时使用系统默认值）
  max_tokens?: number | null;
  temperature?: number | null;
  timeout_seconds?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ModelRouteTestResult = {
  ok: boolean;
  route_id?: string;
  route_name?: string;
  model?: string;
  base_url?: string;
  key_source?: string;
  latency_ms?: number;
  message: string;
};

export type DataSourceProvider = {
  provider_name: string;
  source: string;
  source_type: string;
  layer?: string;
  configured: boolean;
  enabled: boolean;
  priority: number;
  note?: string | null;
  apis?: string[]; // 该源承接的数据类型/在路由链中的角色
  status: string; // healthy / degraded / untested / disabled
  success_count?: number;
  failure_count?: number;
  empty_count?: number;
  success_rate?: number | null;
  latency_ms?: number | null;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  last_error?: string | null;
  last_api?: string | null;
  auth_mode?: string;
  rate_limit_per_minute?: number;
  breaker_state?: string;
};

export type DataSourceProvidersResponse = {
  providers: DataSourceProvider[];
};

export type ProviderTestResult = {
  provider_name: string;
  ok: boolean;
  latency_ms?: number | null;
  status: string;
  error?: string | null;
};

export type PoolSourceSummary = {
  source: string;
  row_count: number;
  code_count: number;
  latest_trade_date?: string | null;
  latest_fetched_at?: string | null;
};

export type UnifiedQuotePoolRow = {
  code: string;
  trade_date: string;
  source_count: number;
  quality_score: number;
  freshness_status: string;
  fetched_at?: string | null;
  quote: Record<string, unknown>;
  source_trace: Record<string, unknown>;
};

export type DailyBarPoolRow = {
  code: string;
  bar_count: number;
  first_trade_date?: string | null;
  latest_trade_date?: string | null;
};

export type DailyIndicatorRow = {
  code: string;
  indicator_count: number;
  first_trade_date?: string | null;
  latest_trade_date?: string | null;
};

export type LocalPoolReadiness = {
  status: "ready" | "warning" | "blocked";
  can_run_local_recommendation: boolean;
  has_missing_data: boolean;
  full_market_mode?: boolean;
  required_candidate_count: number;
  quote: {
    available_count: number;
    candidate_count: number;
    base_filter_eligible_count?: number;
    unified_quote_total?: number;
    filter_summary?: CandidateFilterSummary;
    latest_trade_date?: string | null;
    active_sources: string[];
  };
  kline: {
    checked_count: number;
    ready_count: number;
    missing_count: number;
    short_history_count?: number;
    immature_history_count?: number;
    stale_count: number;
    min_bars: number;
    coverage_count?: number;
    coverage_rate?: number;
    latest_trade_date?: string | null;
    example_count?: number;
    example_limit?: number;
    examples: Array<{
      code?: string | null;
      name?: string | null;
      reason: string;
      bar_count: number;
      latest_bar_date?: string | null;
      quote_date?: string | null;
    }>;
  };
  scan?: {
    configured_candidate_count: number;
    lookahead_multiplier: number;
    lookahead_count: number;
    actual_scan_count: number;
    skill_input_count: number;
    agent_review_input_count?: number;
    scan_limited?: boolean;
    demo_limited: boolean;
  };
  messages: string[];
};

export type LocalPoolStatus = {
  summary: {
    raw_quote_count: number;
    raw_bar_count: number;
    unified_quote_count: number;
    active_unified_quote_count: number;
    eligible_quote_count?: number;
    candidate_filter?: CandidateFilterSummary;
    daily_bar_count: number;
    full_market_daily_bar_count?: number;
    active_daily_bar_count?: number;
    eligible_daily_bar_count?: number;
    daily_bar_coverage_rate?: number;
    eligible_daily_bar_coverage_rate?: number;
    daily_bar_missing_count?: number;
    daily_bar_stale_count?: number;
    daily_bar_short_history_count?: number;
    daily_bar_immature_history_count?: number;
    daily_bar_total_short_history_count?: number;
    actual_recommend_scan_count?: number;
    skill_input_count?: number;
    daily_bar_row_count?: number;
    daily_indicator_count: number;
    indicator_coverage_pct: number;
    latest_quote_date?: string | null;
    latest_bar_date?: string | null;
    coverage_trade_date?: string | null;
    latest_unified_at?: string | null;
  };
  quote_sources: PoolSourceSummary[];
  kline_sources: PoolSourceSummary[];
  recommendation_readiness: LocalPoolReadiness;
  unified_quotes: UnifiedQuotePoolRow[];
  unified_quotes_total: number;
  daily_bars: DailyBarPoolRow[];
  daily_bars_total: number;
};

export type CandidateFilterSummary = {
  total_count: number;
  base_eligible_count: number;
  excluded_count: number;
  high_liquidity_count: number;
  low_liquidity_count: number;
  exclusion_counts: Record<string, number>;
  high_liquidity_threshold: {
    amount_yi: number;
    turnover_rate: number;
  };
};

export type SkillSignal = {
  strategy_id: string;
  strategy_name: string;
  version_id: string;
  phase: string;
  score: number;
  confidence: number;
  setup_match: string;
  entry_rule: string;
  invalid_rule: string;
  entry_mode: "target_touch" | "next_open";
  setup_quality: number;
  hard_veto: boolean;
  veto_reason: string;
  risk_flags: string[];
  evidence: string[];
  weight?: number;
  skill_mode?: string;
  runtime?: string;
  agent_skill_review?: AgentSkillReview;
};

export type AgentSkillReview = {
  skill_id?: string;
  skill_name?: string;
  skill_hash?: string | null;
  runtime?: string;
  runtime_note?: string;
  stance?: string;
  score_delta?: number;
  confidence?: number;
  lenses?: string[];
  supports?: string[];
  risks?: string[];
  summary?: string;
  market_regime_judgment?: string;
  action?: string;
  position_range?: string;
  entry_style?: string;
  invalidation_conditions?: string[];
  next_day_plan?: string[];
  instruction_digest?: {
    frontmatter?: Record<string, unknown>;
    principles?: string[];
    body_hash?: string | null;
    reference_files?: SkillReferenceFile[];
    prompt_chars?: number;
  };
  prompt_contract?: Record<string, unknown>;
  score?: number;
  hallucination_flags?: string[];
  hallucination_severity?: string;
};

export type MarketDiscipline = {
  market_regime: string;
  regime_note: string;
  estimated_win_rate: number;
  position_label: string;
  max_position_pct: number;
  action: string;
  market_position_cap_pct: number;
  risk_reward: {
    downside_pct: number;
    upside_pct: number;
    rr_ratio: number;
  };
  risk_flags: string[];
};

export type SkillDisagreement = {
  disagreement_level: string;
  score_variance: number;
  stance_conflict: boolean;
  stance_distribution: Record<string, number>;
};

export type FeatureSnapshot = {
  entry_mode?: "target_touch" | "next_open";
  // Price & MA
  price?: number;
  ma5?: number;
  ma10?: number;
  ma20?: number;
  ma60?: number;
  bias_ma5?: number;
  bias_ma10?: number;
  bias_ma20?: number;
  // Key levels
  yesterday_high?: number;
  yesterday_low?: number;
  platform_high_20d?: number;
  platform_high_60d?: number;
  high_20d?: number;
  low_20d?: number;
  off_high_pct?: number;
  off_high60_pct?: number;
  // Trend
  trend_status?: string;
  ma_alignment?: string;
  trend_strength?: number;
  breakout20?: number;
  breakout60?: number;
  // Volume
  volume_ratio_5d?: number;
  volume_status?: string;
  volume_trend?: string;
  turnover_rate?: number;
  upper_shadow_count20?: number;
  // Technical
  macd_status?: string;
  macd_dif?: number;
  macd_dea?: number;
  macd_bar?: number;
  rsi_12?: number;
  rsi_6?: number;
  rsi_24?: number;
  rsi_status?: string;
  // Support/Resistance
  support_ma5?: boolean;
  support_ma10?: boolean;
  support_levels?: number[];
  resistance_levels?: number[];
  resistance_distance_pct?: number;
  // Buy signal
  buy_signal?: string;
  signal_reasons?: string[];
  risk_factors?: string[];
  ideal_buy?: number | null;
  secondary_buy?: number | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  suggested_position?: string;
  operation_advice?: string;
  confidence_level?: string;
  // Risk
  max_dd20?: number;
  max_dd60?: number;
  limit_up_days20?: number;
  announcement_negative_count?: number;
  news_risk_count?: number;
  // Data quality
  data_source?: string;
  quote_source?: string;
  kline_source?: string;
  latest_trade_date?: string;
  data_quality?: Record<string, unknown>;
  // Misc
  [key: string]: unknown;
};

export type KeyPriceLevels = Pick<
  FeatureSnapshot,
  | "ma5"
  | "ma10"
  | "ma20"
  | "ma60"
  | "yesterday_high"
  | "yesterday_low"
  | "high_20d"
  | "low_20d"
  | "support_levels"
  | "resistance_levels"
> & {
  platform_high_20d?: number | null;
  platform_high_60d?: number | null;
  high_60d?: number | null;
};

export type RecommendationCard = {
  recommendation_id: string;
  run_id: string;
  trade_date: string;
  evidence_class?: string | null;
  actual_generated_at?: string | null;
  reconstruction_batch_id?: string | null;
  recommended_at: string;
  code: string;
  name: string;
  industry?: string | null;
  recommend_price: number;
  ideal_buy?: number | null;
  secondary_buy?: number | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  final_score: number;
  consensus_score: number;
  bucket?: string | null;
  setup_type?: string | null;
  entry_rule?: string | null;
  invalid_rule?: string | null;
  entry_mode?: "target_touch" | "next_open";
  /** 历史列表保留的轻量关键价位，用来把规则里的“支撑/平台”落到具体价格。 */
  key_levels?: KeyPriceLevels;
  rationale?: string | null;
  /** 后端按风险标签统一归类，供候选池低/中/高风险筛选使用。 */
  risk_level?: "low" | "medium" | "high" | "unknown";
  risk_tags: string[];
  info_tags: string[];
  degraded_reason?: string | null;
  feature_snapshot: FeatureSnapshot;
  market_context: MarketSnapshot;
  skill_breakdown: SkillSignal[];
  /** LLM 审查顶层快捷字段（B 项：候选池卡片不展开即可见 LLM 态度） */
  llm_stance?: string | null;
  llm_delta?: number | null;
  score_breakdown?: {
    raw_scores?: Record<string, number>;
    weighted_scores?: Record<string, number>;
    weights?: Record<string, number>;
    consensus_boost?: number;
    quality_cap?: number;
    final_score?: number;
  };
  score_line_meta?: {
    base_score_line?: number;
    market_base?: string;
    market_sentiment?: string;
    market_adjustment?: number;
    risk_template_adjustment?: number;
    evidence_adjustment?: number;
    execution_adjustment?: number;
    execution_status?: string;
    execution_blocked?: boolean;
    block_reason?: string | null;
    effective_score_line?: number;
    passed?: boolean;
    reasons?: string[];
    /** soft floor 动态降线是否放行了该票（历史污染诊断用） */
    dynamic_soft_floor_applied?: boolean | number | string;
    dynamic_soft_floor?: number;
    soft_floor_gap?: number;
    soft_floor_reason?: string;
    degraded_agent_unavailable?: boolean | number | string;
    panic_day_penalty?: number;
    regime_weak_low_chg20_penalty?: number;
    regime_oscillation_low_pos_penalty?: number;
    regime_support_ma5_penalty?: number;
  };
  execution_viability?: {
    status?: "tradable" | "risky" | "blocked" | string;
    label?: string;
    block_reason?: string | null;
    risk_flags?: string[];
    score_line_adjustment?: number;
    details?: {
      is_limit_up?: boolean;
      open_times?: number;
      first_time?: string | null;
      last_time?: string | null;
      fd_amount_yi?: number;
      turnover_ratio?: number;
      [key: string]: unknown;
    };
  };
  status: string;
  recent_repeat: boolean;
  watch_status: string;
  watch_note?: string | null;
  execution_version?: string | null;
  strategy_versions?: Array<{ strategy_id: string; name: string; version_id: string }>;
  aggregation_weights?: Record<string, number>;
  market_regime?: string;
  skill_disagreement?: SkillDisagreement;
  market_discipline?: MarketDiscipline;
};

export type RecommendationOutcome = {
  horizon_days: number;
  entry_triggered: boolean;
  entry_date?: string | null;
  entry_price?: number | null;
  exit_date?: string | null;
  exit_price?: number | null;
  executed_return?: number;
  weighted_return?: number;
  position_size?: number;
  holding_days?: number;
  entry_slippage_bps?: number;
  exit_slippage_bps?: number;
  stop_loss_price?: number | null;
  take_profit_price?: number | null;
  exit_reason?: string | null;
  max_return: number;
  min_return: number;
  close_return: number;
  achievable_return?: number;
  max_drawdown: number;
  hit_target: boolean;
  hit_invalid: boolean;
  outcome_label: string;
  outcome_label_raw?: string | null;
  outcome_label_legacy?: string;
  outcome_reason?: string | null;
  label_source: string;
  labeled_at?: string | null;
};

export type TimelinePoint = {
  label: string;
  trade_date: string;
  close_return: number;
  max_return: number;
  max_drawdown: number;
};

export type AgentLog = {
  agent_name: string;
  output: Record<string, unknown>;
  created_at?: string | null;
};

export type ReviewNote = {
  user_label?: string | null;
  failure_reason?: string | null;
  review_text?: string | null;
  updated_at?: string | null;
};

export type RecommendationHistoryRow = {
  recommendation_id: string;
  run_id: string;
  trade_date: string;
  final_score: number;
  status: string;
};

export type ExecutionConditionSummary = {
  passed?: boolean;
  status?: "passed" | "failed" | string;
  scope?: string;
  code?: string;
  label?: string;
  expression?: string;
  observed?: Record<string, unknown>;
};

export type RecommendationExecutionState = {
  latest_decision_code: "buy" | "wait" | "cancel" | "expired" | string;
  latest_decision_label: string;
  latest_reason?: string | null;
  latest_checked_at: string;
  latest_quote_time?: string | null;
  latest_data_as_of?: string | null;
  latest_price?: number | null;
  latest_strategy_id?: string | null;
  latest_setup_match?: string | null;
  condition_summary?: ExecutionConditionSummary;
  ever_buy_confirmed: boolean;
  first_buy_confirmed_at?: string | null;
  first_buy_confirmed_price?: number | null;
  cancelled_before_entry: boolean;
  first_cancelled_at?: string | null;
  first_cancelled_price?: number | null;
  first_cancel_reason?: string | null;
};

export type CancelObservationPoint = {
  trade_date: string;
  close: number;
  net_return_pct: number;
};

export type CancelObservation = {
  status: "pending" | "partial" | "complete" | string;
  reference_at?: string | null;
  reference_price?: number | null;
  cost_pct: number;
  horizons: {
    "1d": CancelObservationPoint | null;
    "3d": CancelObservationPoint | null;
    "5d": CancelObservationPoint | null;
  };
};

export type RecommendationHistoryResponse = {
  items: (RecommendationCard & {
    outcome: RecommendationOutcome | null;
    execution_state: RecommendationExecutionState | null;
    cancel_observation: CancelObservation | null;
  })[];
  total: number;
  offset: number;
  limit: number;
  facets: {
    setup_types: string[];
  };
  summary: {
    trade_count: number;
    win_count: number;
    win_rate: number | null;
    average_net_return: number | null;
    total_net_return: number;
    additional_cost_pct: number;
    cancel_observation: {
      cancelled_count: number;
      observed_1d_count: number;
      average_1d_return: number | null;
      observed_3d_count: number;
      average_3d_return: number | null;
      observed_5d_count: number;
      average_5d_return: number | null;
      avoided_loss_count_5d: number;
      avoided_loss_rate_5d: number | null;
      missed_gain_count_5d: number;
    };
  };
};

export type RecommendationHistoryQuery = {
  code?: string;
  label?: "success" | "failed" | "missed" | "pending" | "provisional";
  limit?: number;
  offset?: number;
};

export type RealtimePnlItem = {
  recommendation_id: string;
  code: string;
  trade_date: string;
  recommend_price: number;
  status: "holding" | "watching" | "waiting" | "unavailable";
  reason?: string;
  // holding（已入场）
  entry_price?: number;
  entry_date?: string;
  now_price?: number;
  pnl_pct?: number;
  // watching（未触发买点）
  nearest_buy_target?: number;
  distance_to_target_pct?: number | null;
  // waiting（盘后推荐，尚未到下一交易日入场）
  // 共有
  day_high?: number;
  day_low?: number;
  pre_close?: number;
};

export type RealtimePnlResponse = {
  computed_at: string;
  cached: boolean;
  items: RealtimePnlItem[];
};

export type RecommendationDetail = RecommendationCard & {
  run: RunSummary | null;
  outcome: RecommendationOutcome | null;
  timeline: TimelinePoint[];
  agent_logs: AgentLog[];
  review_note?: ReviewNote;
  history: RecommendationHistoryRow[];
  replay_summary?: {
    recommend_time?: string | null;
    symbol?: string | null;
    name?: string | null;
    entry_time?: string | null;
    entry_price?: number | null;
    exit_time?: string | null;
    exit_price?: number | null;
    net_return_pct?: number | null;
    trigger_conditions?: string[];
    false_positive_reason?: string | null;
    false_positive_category?: string | null;
    false_positive_category_label?: string | null;
    false_positive_stage?: string | null;
    review_failure_reason?: string | null;
    user_label?: string | null;
    outcome_label?: string | null;
    outcome_reason?: string | null;
    exit_reason?: string | null;
    holding_days?: number | null;
    effective_score_line?: number | null;
    final_score?: number | null;
    signal_features?: Record<string, unknown>;
  };
  /** 该推荐所属策略的历史战绩（北极星 B 项：一屏可见同策略胜率） */
  strategy_perf?: StrategyPerfSummary | null;
  strategy_perf_status?: "ready" | "pending_backtest" | "unavailable";
};

export type ExecutionDecision = {
  code: "buy" | "wait" | "cancel" | "expired" | "not_ready" | "hold" | "reduce" | "exit" | "observe" | string;
  label: string;
  reason: string;
};

export type ExecutionConfirmation = {
  status: "ok" | "not_ready" | "error" | string;
  recommendation_id: string;
  code?: string;
  name?: string;
  strategy_id?: string;
  strategy_name?: string;
  checked_at?: string;
  data_as_of?: string;
  quote_time?: string | null;
  snapshot_generated_at?: string | null;
  data_source?: string | null;
  fresh?: boolean;
  signal_age_trade_days?: number;
  current_price?: number;
  message?: string;
  entry_decision?: ExecutionDecision;
  holding_decision?: ExecutionDecision;
  original?: {
    trade_date?: string;
    recommend_price?: number;
    stop_loss?: number | null;
    entry_rule?: string | null;
    invalid_rule?: string | null;
  };
  market?: {
    regime?: string | null;
    up_count?: number;
    down_count?: number;
    limit_up_count?: number;
    limit_down_count?: number;
    main_industries?: string[];
  };
  strategy_signal?: {
    phase?: string;
    score?: number;
    setup_match?: string;
    entry_rule?: string;
    invalid_rule?: string;
    hard_veto?: boolean;
    veto_reason?: string;
    risk_flags?: string[];
    evidence?: string[];
    prefilter_ok?: boolean;
    prefilter_reason?: string;
  };
  agent_review?: {
    status?: string;
    stance?: string;
    action?: string;
    confidence?: number;
    summary?: string;
    evidence?: string[];
    risks?: string[];
    invalidation_conditions?: string[];
    next_day_plan?: string[];
    error?: string;
  } | null;
};

/** 单策略历史表现摘要 —— strategy_perf_summary 轻量 SQL 输出 */
export type StrategyPerfSummary = {
  strategy_id: string;
  source: "canonical_backtest";
  strategy_version: string;
  profile_fingerprint: string;
  backtest_id: string;
  portfolio_model: "capped_daily_exit_cohort_v1";
  sample_count: number;
  labeled_count: number;
  success_count: number;
  hit_rate: number | null;
  avg_return: number | null;
  profit_loss_ratio: number | null;
  total_return_pct: number;
  max_drawdown_pct: number;
  monthly_returns: StrategyMonthlyReturn[];
  date_from: string;
  date_to: string;
  generated_at: string | null;
  window_days: number | null;
};

/** 批量次日买入确认的单条结果（执行规则单元格顶部状态标签用） */
export type ExecutionConfirmationItem = {
  recommendation_id: string;
  code: string;
  trade_date: string;
  status: "ok" | "error" | "not_ready" | string;
  fresh?: boolean;
  data_as_of?: string;
  quote_time?: string | null;
  data_source?: string | null;
  current_price?: number;
  entry_decision?: ExecutionDecision;
  holding_decision?: ExecutionDecision;
  condition_summary?: ExecutionConditionSummary;
  strategy_signal?: {
    phase?: string;
    score?: number;
    setup_match?: string;
    hard_veto?: boolean;
    risk_flags?: string[];
  };
  message?: string;
};

export type ExecutionConfirmationSummary = {
  requested_count: number;
  eligible_count: number;
  settled_skipped_count: number;
  expired_count: number;
  success_count: number;
  error_count: number;
};

export type ExecutionConfirmationResponse = {
  computed_at: string;
  data_as_of?: string;
  cached?: boolean;
  error?: string;
  summary: ExecutionConfirmationSummary;
  items: ExecutionConfirmationItem[];
  errors: { recommendation_id: string; code: string; message: string }[];
  pnl_items: RealtimePnlItem[];
};

export type ReplayAnalysisRow = {
  recommendation_id: string;
  run_id: string;
  trade_date: string;
  recommend_time?: string | null;
  symbol?: string | null;
  name?: string | null;
  entry_time?: string | null;
  entry_price?: number | null;
  exit_time?: string | null;
  exit_price?: number | null;
  net_return_pct?: number | null;
  holding_days?: number | null;
  outcome_label?: string | null;
  user_label?: string | null;
  is_false_positive: boolean;
  false_positive_reason?: string | null;
  false_positive_category?: string | null;
  false_positive_category_label?: string | null;
  false_positive_stage?: string | null;
  review_failure_reason?: string | null;
  outcome_reason?: string | null;
  exit_reason?: string | null;
  trigger_conditions?: string[];
  trigger_condition_text?: string;
  effective_score_line?: number | null;
  final_score?: number | null;
  market_regime?: string | null;
  trend_status?: string | null;
  concept_sector_name?: string | null;
  signal_features?: Record<string, unknown>;
};

export type ReplayAnalysisSummary = {
  total_count: number;
  false_positive_count: number;
  false_positive_rate: number;
  average_net_return: number;
  reason_counts: Array<{ reason: string; count: number }>;
  category_counts: Array<{ category: string; category_label: string; stage: string; count: number }>;
  stage_counts: Array<{ stage: string; count: number }>;
  label_counts: Array<{ label: string; count: number }>;
  market_regime_counts: Array<{ market_regime: string; count: number }>;
};

export type ReplayAnalysisResult = {
  run: RunSummary | null;
  rows: ReplayAnalysisRow[];
  summary: ReplayAnalysisSummary;
};

export type RunResult = {
  run: RunSummary;
  recommendations: RecommendationCard[];
  queued?: boolean;
};

export type FunnelStageSummary = {
  stage: string;
  label: string;
  input_count: number;
  pass_count: number;
  filtered_count: number;
  reason_counts?: Record<string, number>;
};

export type FunnelTraceStep = {
  stage: string;
  label?: string;
  passed: boolean;
  reason?: string;
  final_score?: number;
  effective_score_line?: number;
  score_gap?: number | null;
  evidence_adjustment?: number;
  adjustment_reasons?: string[];
  best_skill_id?: string | null;
  best_gate_score?: number | null;
  prelim_score?: number | null;
  review_skill_ids?: string[];
  skill_pass_ids?: string[];
  prefilter_source?: string | null;
  amount_yi?: number | null;
  turnover_rate?: number | null;
  pct?: number | null;
};

export type FunnelAuditRow = {
  code: string;
  name?: string | null;
  industry?: string | null;
  price?: number | null;
  pct?: number | null;
  amount_yi?: number | null;
  turnover_rate?: number | null;
  final_stage: string;
  final_status: string;
  final_reason: string;
  trace?: FunnelTraceStep[];
  trace_summary?: string;
  prefilter?: Record<string, unknown>;
  rule_gate?: {
    passed?: boolean;
    reason?: string;
    reason_key?: string;
    prelim_score?: number;
    best_skill_id?: string | null;
    best_gate_score?: number | null;
    review_selected?: boolean;
    review_skill_ids?: string[];
    gate_scores?: Record<string, number>;
    gate_reasons?: Record<string, string>;
    quality_tier?: Record<string, string>;
  };
  score_line?: {
    final_score?: number;
    effective_score_line?: number;
    score_gap?: number | null;
    passed?: boolean;
    reason?: string;
    evidence_quality_score?: number;
    data_completeness_score?: number;
    score_breakdown?: Record<string, unknown>;
    score_line_meta?: Record<string, unknown>;
    risk_tags?: string[];
    info_tags?: string[];
    setup_type?: string | null;
    data_authenticity?: number;
    data_quality?: Record<string, unknown>;
    raw_component_scores?: Record<string, number>;
    weighted_component_scores?: Record<string, number>;
    aggregation_weights?: Record<string, number>;
    component_scale?: number;
    consensus_boost?: number;
    consensus_tags?: string[];
    quality_cap?: number;
    market_regime?: string | null;
    market_discipline?: Record<string, unknown>;
    skill_disagreement?: Record<string, unknown>;
    strategy_signal_count?: number;
  };
};

export type RunFunnelAudit = {
  run: RunSummary;
  stage_summary: FunnelStageSummary[];
  distributions: Record<string, Record<string, number>>;
  reason_counts: Record<string, Record<string, number>>;
  rows: FunnelAuditRow[];
  empty_reason?: string | null;
};

export type RunDetail = RunSummary & {
  can_resume: boolean;
  resume_block_code?: string | null;
  resume_block_reason?: string | null;
  params: Record<string, unknown>;
  market_snapshot: MarketSnapshot;
  result_summary: Record<string, unknown>;
};

export type SkillSummary = {
  skill_id: string;
  name: string;
  source_type: string;
  source_url?: string | null;
  installed_path: string;
  current_version: string;
  enabled: boolean;
  has_executable_contract: boolean;
  has_agent_contract?: boolean;
  agent_runtime?: string;
  has_backtest_adapter?: boolean;
  backtest_adapter_actions?: string[];
  reference_count?: number;
  manifest: Record<string, unknown>;
  assigned_presets: string[];
};

export type StrategyMonthlySignalMetrics = {
  month: string;
  raw_signal_count: number;
  selected_signal_count: number;
  executed_trade_count: number;
  success_count: number;
  hit_rate: number | null;
  avg_return: number | null;
};

/** 历史表现摘要仍使用账户口径；策略画像页不读取此类型。 */
export type StrategyMonthlyReturn = {
  month: string;
  return_pct: number;
  executed_trade_count: number;
};

export type StrategyWindowPerformance = {
  window_days: number;
  date_from: string;
  date_to: string;
  raw_signal_count: number;
  selected_signal_count: number;
  selected_signal_days: number;
  sample_count: number;
  success_count: number;
  loss_count: number;
  hit_rate: number | null;
  avg_return: number | null;
  profit_loss_ratio: number | null;
  monthly_metrics: StrategyMonthlySignalMetrics[];
};

/** 当前策略版本最近半年 canonical 回测画像。 */
export type StrategyPerformance = {
  source: "canonical_backtest";
  strategy_version: string;
  profile_fingerprint: string;
  backtest_id: string;
  selection_policy: "daily_score_top_n";
  daily_selection_limit: 10;
  selection_tiebreaker: "score_desc_code_asc_trade_id_asc";
  raw_signal_count: number;
  selected_signal_count: number;
  selected_signal_days: number;
  sample_count: number;
  labeled_count: number;
  triggered_count: number;
  missed_count: number;
  success_count: number;
  trigger_rate: number | null;
  hit_rate: number | null;
  avg_score: number | null;
  avg_return: number | null;
  profit_loss_ratio: number | null;
  window_metrics?: Record<string, StrategyWindowPerformance>;
  monthly_metrics: StrategyMonthlySignalMetrics[];
  date_from: string;
  date_to: string;
  /** 当前目标信号窗口（最新可完全结算信号日锚定，随交易日漂移） */
  target_date_from?: string;
  target_date_to?: string;
  /** true=信号窗口已向后滚动复用同版本最新完成画像（date_from/to 为真实窗口）；版本仍不跨混 */
  window_rolled?: boolean;
  generated_at: string | null;
  /** 统计可信度：样本数 < 20 时为 false（胜率属噪声，前端标「样本不足·统计参考」） */
  reliable: boolean;
  /** 近 30 天 formal+experiment 曝光（与 self_check E 项 / 补跑判定一致） */
  exposure_30d?: number;
  /** 曝光样本下限（默认 20） */
  exposure_floor?: number;
  /** 最新 completed experiment 门线明细（仅曝光不足策略） */
  last_exp_gate?: {
    effective_line?: number | null;
    max_candidate_score?: number | null;
    base_score_line?: number | null;
    gate_blocked?: boolean;
    /** 恐慌日（ld>100）有效线抬高属设计内，勿当补跑 bug */
    panic_day?: boolean;
    limit_down_count?: number | null;
    limit_up_count?: number | null;
    regime?: string | null;
    trade_date?: string | null;
  };
};

/** 策略清单页单行（策略元数据 + 表现，单次请求返回） */
export type StrategyListItem = {
  skill_id: string;
  name: string;
  description: string;
  mode: string;
  enabled: boolean;
  source_type: string | null;
  assigned_presets: string[];
  has_backtest_adapter: boolean;
  /** 近 30 天曝光（performance 为空时仍返回，供补跑状态可见） */
  exposure_30d?: number;
  exposure_floor?: number;
  last_exp_gate?: StrategyPerformance["last_exp_gate"];
  performance_status: "ready" | "pending_backtest" | "unavailable";
  current_strategy_version: string | null;
  performance: StrategyPerformance | null;
  /** 三窗回测收益（path A 诊断口径，单策略组合）：与 canonical 画像分开呈现 */
  backtest_returns?: StrategyBacktestReturns | null;
};

/** 单个窗口的逐笔回测统计（口径见 StrategyBacktestReturns.caliber） */
export type StrategyBacktestWindow = {
  n: number;
  win_rate_pct: number;
  avg_net_pct: number;
  sum_net_pct: number;
  /** 窗口内无亏损笔或样本<5 时为 null（不做伪精度展示） */
  profit_factor: number | null;
  profit_factor_note?: string | null;
  max_drawdown_pct: number;
  trade_days: number;
  delta_cohort_vs_combined: number | null;
  ci95: [number, number] | null;
  month_same_sign_ratio: number | null;
  challenger: string | null;
};

/** 三窗回测收益聚合（后端 /api/strategies 的 backtest_returns 字段） */
export type StrategyBacktestReturns = {
  windows: Record<string, StrategyBacktestWindow | null>;
  windows_with_trades: number;
  passes: number;
  avg_net_pct_mean: number | null;
  all_windows_positive: boolean;
  any_window_positive: boolean;
  combined: Record<
    string,
    {
      n: number;
      win_rate_pct: number;
      avg_net_pct: number;
      sum_net_pct: number;
      profit_factor: number;
      max_drawdown_pct: number;
      trade_days: number;
    }
  >;
  caliber: {
    label?: string;
    description?: string;
    combined_label?: string;
    selection_policy?: string;
    execution_policy?: string;
    score_line?: number;
    output_count?: number;
    caveat?: string;
    reproduce?: string;
    source_artifact?: string;
  };
  windows_meta: Record<string, { from: string; to: string; label: string }>;
  generated_at: string;
  schema_version: number;
};

export type StrategyVersionUsage = {
  version_id: string;
  declared_version: string;
  yaml_hash: string;
  created_at: string | null;
  snapshot_available: boolean;
  content_hash: string | null;
  is_current: boolean;
  run_count: number;
  recommendation_count: number;
  latest_run_at: string | null;
};

export type StrategyVersionChange = {
  path: string;
  change_type: "added" | "removed" | "changed";
  before: unknown;
  after: unknown;
};

export type StrategyArtifactSnapshot = {
  schema_version: number;
  strategy_id: string;
  name: string;
  mode: string;
  status: string;
  declared_version: string;
  effective_version: string;
  manifest: Record<string, unknown>;
  contract: Record<string, unknown> | null;
  skill_frontmatter: Record<string, unknown>;
  skill_body: string;
  skill_hash: string | null;
  reference_files: Array<Record<string, unknown>>;
};

export type StrategyCenterDetail = {
  strategy_id: string;
  name: string;
  description: string;
  mode: string;
  status: string;
  enabled: boolean;
  current_version_id: string;
  selected_version_id: string;
  selected_is_current: boolean;
  effective_version: string;
  registry_updated_at: string | null;
  snapshot: StrategyArtifactSnapshot | null;
  snapshot_available: boolean;
  timeline: StrategyVersionUsage[];
  diff: {
    base_version_id: string | null;
    target_version_id: string;
    changes: StrategyVersionChange[];
  };
  performance_status: "ready" | "pending_backtest" | "selected_version_not_current";
  performance: StrategyPerformance | null;
};

export type SkillReferenceFile = {
  path: string;
  size?: number;
  sha256?: string;
};

export type SkillPerformance = {
  sample_count: number;
  triggered_count?: number;
  trigger_rate?: number;
  hit_rate: number;
  avg_return: number;
  avg_score: number;
  failure_reasons: Array<{ reason: string; count: number }>;
};

export type SkillDetail = {
  skill_id: string;
  name: string;
  enabled: boolean;
  current_version: string;
  has_executable_contract: boolean;
  has_agent_contract?: boolean;
  agent_runtime?: string;
  has_backtest_adapter?: boolean;
  backtest_adapter_actions?: string[];
  reference_files?: SkillReferenceFile[];
  manifest: Record<string, unknown>;
  skill_md: string;
  errors: string[];
  assigned_presets: Array<{ preset_id: string; name: string }>;
  performance: SkillPerformance;
};

export type StatsOverview = {
  run_count: number;
  recommendation_count: number;
  labeled_count: number;
  triggered_count?: number;
  success_count: number;
  failed_count: number;
  missed_count?: number;
  pending_count: number;
  watch_count: number;
  reviewed_count: number;
  hit_rate: number;
  trigger_rate?: number;
  avg_return: number;
  avg_executed_return?: number;
  avg_weighted_return?: number;
  avg_close_return?: number;
  avg_max_return: number;
  avg_drawdown: number;
  avg_score: number;
  latest_run_at?: string | null;
};

/** 因子 Alpha 衰减检测 —— /api/stats/alpha-decay 返回。
 * 对比因子在早期 vs 近期的 point-biserial 相关性 + 胜率边际，识别衰减中/改善中/稳定的因子。 */
export type AlphaDecayFactor = {
  factor: string;
  early_r: number;
  recent_r: number;
  delta: number;
  decay_pct: number;
  status: "decaying" | "improving" | "stable";
  early_n: number;
  recent_n: number;
  early_win_edge: number;
  recent_win_edge: number;
  early_true_wr: number;
  recent_true_wr: number;
  early_false_wr: number;
  recent_false_wr: number;
  early_true_n: number;
  recent_true_n: number;
};

export type AlphaDecayResponse = {
  total: number;
  early_period?: string;
  recent_period?: string;
  factors: AlphaDecayFactor[];
  error?: string;
  date_range: DateRangeMeta;
};

export type SkillStatsRow = {
  skill_id: string;
  name: string;
  sample_count: number;
  run_count: number;
  labeled_count: number;
  triggered_count?: number;
  failed_count?: number;
  missed_count?: number;
  pending_count?: number;
  success_count: number;
  hit_rate: number;
  trigger_rate?: number;
  avg_signal_score: number;
  avg_final_score: number;
  avg_return: number;
  last_seen_at?: string | null;
  top_market_regimes?: Array<{ name: string; count: number }>;
  top_industries?: Array<{ name: string; count: number }>;
  top_setups?: Array<{ name: string; count: number }>;
};

export type RunTarget = "preset" | "single-skill";

export type PresetDraft = {
  name: string;
  description: string;
  skip_aux_refresh?: boolean;
  market_scope: string;
  market_scope_value: string;
  risk_template: string;
  score_line: number;
  data_source: string;
  hide_recent_repeats: boolean;
};

export type PresetSkillDraft = {
  skill_id: string;
  weight: number;
  sort_order: number;
};

export type PresetStatsRow = {
  preset_id?: string | null;
  preset_name: string;
  run_count: number;
  recommendation_count: number;
  skill_count: number;
  labeled_count: number;
  triggered_count?: number;
  failed_count?: number;
  missed_count?: number;
  pending_count?: number;
  success_count: number;
  hit_rate: number;
  trigger_rate?: number;
  avg_return: number;
  avg_score: number;
  last_run_at?: string | null;
};

/** 指标时间窗口元信息（后端返回，供前端展示窗口与结算截止说明） */
export type DateRangeMeta = {
  window_days: number | null;
  start_date: string | null;
  end_date: string | null;
  settle_lag_days: number;
  settle_cutoff: string | null;
  total_in_window: number;
  labeled_in_window: number;
  pending_excluded: number;
};

export type EvaluationKpiFields = {
  trade_count?: number;
  recommendation_accuracy?: number;
  average_net_return?: number;
  median_net_return?: number;
  profit_loss_ratio?: number;
  max_drawdown?: number;
  positive_month_ratio?: number;
  benchmark_excess_return?: number;
  sample_confidence?: "low" | "normal" | string;
  accuracy_numerator?: number;
  accuracy_denominator?: number;
  accuracy_basis?: string;
  horizon_metrics?: Record<string, EvaluationHorizonMetrics>;
  date_range?: DateRangeMeta;
};

export type EvaluationHorizonMetrics = {
  horizon_days: number;
  sample_count: number;
  triggered_count: number;
  success_count: number;
  recommendation_accuracy: number;
  hit_rate: number;
  average_net_return: number;
  average_executed_return: number;
  max_drawdown: number;
  sample_confidence?: "low" | "normal" | string;
  accuracy_numerator?: number;
  accuracy_denominator?: number;
};

export type SetupStatsRow = {
  setup_type: string;
  sample_count: number;
  labeled_count?: number;
  triggered_count?: number;
  missed_count?: number;
  success_count: number;
  hit_rate: number;
  trigger_rate?: number;
  avg_score: number;
  avg_return: number;
  avg_drawdown?: number;
} & EvaluationKpiFields;

export type StrategyStatsRow = {
  strategy_id: string;
  strategy_name?: string | null;
  current_mode?: string;
  sample_count: number;
  labeled_count?: number;
  triggered_count?: number;
  missed_count?: number;
  success_count: number;
  hit_rate: number;
  trigger_rate?: number;
  avg_score: number;
  avg_return: number;
} & EvaluationKpiFields;

export type PrecisionAtKRow = {
  k: number;
  sample_runs: number;
  evaluated_count: number;
  success_count: number;
  precision: number;
  date_range?: DateRangeMeta;
};

export type ScoreLineStatsRow = {
  score_line: number;
  sample_count: number;
  activation_rate: number;
  labeled_count: number;
  triggered_count: number;
  missed_count?: number;
  success_count: number;
  trigger_rate: number;
  hit_rate: number;
  avg_return: number;
  avg_drawdown: number;
  avg_close_return: number;
} & EvaluationKpiFields;

export type AccuracyGateSummary = {
  total_recommendations: number;
  signal_valid_count: number;
  signal_invalid_count: number;
  executable_count: number;
  unexecutable_count: number;
  net_positive_count: number;
  net_non_positive_count: number;
  manual_override_count: number;
  legacy_accuracy_label_mismatch_count?: number;
};

export type AccuracyPolicyAudit = {
  definition: string;
  required_conditions: string[];
  denominator: number;
  accurate_count: number;
  strict_three_gate_accurate_count: number;
  reported_accuracy: number;
  strict_three_gate_accuracy: number;
  matches_reported_accuracy: boolean;
  sample_confidence: "low" | "normal";
  gate_counts: AccuracyGateSummary;
  funnel: Array<{
    stage: string;
    label: string;
    count: number;
    failed_count: number;
    pass_rate: number;
  }>;
  failure_breakdown: Array<{
    gate: string;
    label: string;
    count: number;
  }>;
};

export type HistoricalReconstructionCohort = {
  batch_id: string;
  window_start: string;
  window_end: string;
  expected_trade_date_count: number;
  completed_trade_date_count: number;
  completed_at: string;
  cohort_identity_hash: string;
  request_template_fingerprint: string;
  strategy_policy_fingerprint: string;
  execution_policy_fingerprint: string;
  code_revision: string;
};

/** 9-KPI 评估指标（来自 /api/metrics/overview） */
export type MetricsOverview = {
  total_recommendations: number;
  labeled_count: number;
  triggered_count: number;
  executed_trade_count: number;
  unexecuted_count: number;
  trigger_rate: number;
  missed_count: number;
  pending_count: number;
  success_count: number;
  failed_count: number;
  accuracy_numerator: number;
  accuracy_denominator: number;
  trade_accuracy: number;
  accuracy_basis: string;
  hit_rate: number;
  avg_return: number;
  avg_executed_return: number;
  average_executed_return: number;
  avg_weighted_return: number;
  avg_close_return: number;
  avg_mfe: number;
  avg_mae: number;
  avg_holding_days: number;
  /** 9-KPI 标准字段 */
  recommendation_accuracy: number;
  average_net_return: number;
  median_net_return: number;
  profit_loss_ratio: number;
  max_drawdown: number;
  positive_month_ratio: number;
  benchmark_excess_return: number;
  sample_confidence: "low" | "normal";
  strategy_compound_return: number;
  benchmark_compound_return: number;
  flat_count: number;
  false_positive_notes: Array<{
    reason: string;
    count: number;
    examples: string[];
    category?: string;
    category_label?: string;
    stage?: string;
  }>;
  false_positive_category_counts: Array<{
    category: string;
    category_label: string;
    stage: string;
    count: number;
  }>;
  accuracy_gate_summary: AccuracyGateSummary;
  accuracy_policy: AccuracyPolicyAudit;
  historical_reconstruction_cohort?: HistoricalReconstructionCohort | null;
  date_range?: DateRangeMeta;
};

// ── 单股票矩阵分析 ──

export type SkillMatrixRow = {
  skill_id: string;
  skill_name: string;
  rule_score: number;
  rule_phase: string;
  rule_evidence: string[];
  rule_risk_flags: string[];
  hard_veto: boolean;
  veto_reason: string;
  setup_match: string;
  entry_rule: string;
  invalid_rule: string;
  llm_review?: {
    stance: string;
    score: number;
    score_delta: number;
    confidence: number;
    supports: string[];
    risks: string[];
    summary: string;
    action: string;
    position_range: string;
    hallucination_check?: { severity: string; flags: string[] };
    error?: string;
    status?: string;
  } | null;
};

export type StockMatrixAnalysisResult = {
  status: string;
  code: string;
  name: string;
  trade_date: string;
  price: number;
  dsa_profile: Record<string, unknown>;
  skill_results: SkillMatrixRow[];
  aggregated: {
    final_score: number;
    consensus_score: number;
    component_scores: Record<string, number>;
    risk_flags: string[];
    consensus_tags: string[];
    skill_disagreement: { disagreement_level: string; score_variance: number; stance_conflict: string };
    stance_summary: Array<{ skill_name: string; stance: string; score: number; confidence: number }>;
  };
  elapsed_ms: number;
  message?: string;
};

export type SkillBacktestTrade = {
  trade_date: string;
  entry_date: string;
  entry_price: number;
  exit_date: string;
  exit_price: number;
  net_return: number;
  exit_reason: string;
  max_return: number;
  max_drawdown: number;
};

export type SkillBacktestPerformance = {
  skill_id: string;
  skill_name: string;
  signal_count: number;
  triggered_count: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  avg_return: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  max_drawdown: number;
  trades: SkillBacktestTrade[];
};

export type StockMatrixBacktestResult = {
  status: string;
  code: string;
  name: string;
  start_date: string;
  end_date: string;
  dates_scanned: number;
  horizon_days: number;
  per_skill_performance: SkillBacktestPerformance[];
  aggregate_metrics: {
    total_signals: number;
    total_trades: number;
    overall_win_rate: number;
    overall_avg_return: number;
    best_skill_id: string;
    worst_skill_id: string;
  };
  elapsed_ms: number;
  message?: string;
};

// 三规则过滤效果监控
export type FilterEffectiveness = {
  count: number;
  filtered_count: number;
  filter_rate_pct: number;
  kept: {
    count: number;
    net_win_rate: number;
    net_avg_return: number;
    net_profit_factor: number;
  };
  filtered_by_rule: Record<string, {
    count: number;
    win_rate: number;
    avg_return: number;
  }>;
  date_range?: DateRangeMeta;
};

export type StockMatrixHistoryItem = {
  id: number;
  code: string;
  trade_date: string;
  analysis_type: string;
  final_score: number | null;
  win_rate_est: number | null;
  avg_return_est: number | null;
  skill_count: number;
  elapsed_ms: number | null;
  created_at: string | null;
};

export type StockMatrixHistoryResult = {
  code: string;
  page: number;
  page_size: number;
  total: number;
  items: StockMatrixHistoryItem[];
};

/** 推荐股票的 1/3/5日 + 至今收益率（除权安全，pct_chg 链式累乘） */
export type RecommendationReturns = {
  return_1d: number | null;
  return_3d: number | null;
  return_5d: number | null;
  return_to_date: number | null;
  latest_date: string | null;
  latest_close: number | null;
  available_bars: number;
  as_of_date?: string | null;
};

/** 记录中心：单次推荐运行的详情 + 推荐股票（含收益率） */
export type RunRecordDetail = {
  run: RunDetail;
  recommendations: Array<RecommendationCard & { returns: RecommendationReturns }>;
  as_of_date: string;
};

/** 记录中心：矩阵历史列表（跨所有 code，按 created_at 倒序） */
export type MatrixRecordsResponse = {
  page: number;
  page_size: number;
  total: number;
  items: StockMatrixHistoryItem[];
};

/** 记录中心：单条矩阵记录详情 */
export type MatrixRecordDetail = StockMatrixHistoryItem & {
  result: Record<string, unknown>;
  base_price: number;
  returns: RecommendationReturns;
};

/** 单股票评分：基础短线策略特征评分（后端 /api/runs/analyze-stock） */
export type SingleStockScoring = {
  dsa_signal_score: number | null;
  dsa_trend_score: number | null;
  dsa_bias_score: number | null;
  dsa_volume_score: number | null;
  dsa_support_score: number | null;
  dsa_macd_score: number | null;
  dsa_rsi_score: number | null;
  risk_score: number | null;
};

/** 单股票评分：买卖点规则 */
export type SingleStockEntryRules = {
  ideal_buy: number | null;
  secondary_buy: number | null;
  stop_loss: number | null;
  take_profit: number | null;
};

/** 单股票评分：预设模式下单个 skill 的审查结果 */
export type SingleStockSkillReview = {
  skill_id: string;
  skill_name: string;
  status: string;
  error?: string | null;
  score?: number | null;
  stance?: string | null;
  confidence?: number | null;
  summary?: string | null;
  action?: string | null;
  supports?: string[];
  risks?: string[];
};

/** 单股票评分：LLM agent 分析结果（可选，消耗 API 额度）
 *  支持两种模式：
 *  - 单 skill 模式：score/stance/summary 等扁平字段
 *  - 预设多 skill 模式：final_score/component_scores/skill_reviews[] 聚合字段
 */
export type SingleStockLlmAnalysis = {
  status: string;
  elapsed_ms?: number;
  // 单 skill 模式字段
  skill_id?: string;
  score?: number | null;
  score_delta?: number | null;
  rule_score?: number | null;
  llm_adjustment?: number | null;
  stance?: string | null;
  confidence?: number | null;
  summary?: string | null;
  action?: string | null;
  market_regime_judgment?: string | null;
  supports?: string[];
  risks?: string[];
  // 预设多 skill 模式字段
  preset_id?: string;
  preset_name?: string;
  risk_template?: string;
  final_score?: number | null;
  component_scores?: Record<string, number>;
  agent_review_score?: number | null;
  skill_reviews?: SingleStockSkillReview[];
  // 公共字段
  error?: string | null;
  total_tokens_est?: number;
  total_tokens_real?: number;
  total_cache_hit_tokens?: number;
  prompt_cache_active?: boolean;
};

/** 单股票评分：完整返回结果 */
export type SingleStockAnalysis = {
  status: string;
  code: string;
  name: string;
  trade_date: string;
  bar_count: number;
  elapsed_ms: number;
  price: number | null;
  final_score: number | null;
  recommended?: boolean;
  pipeline_decision?: string;
  pipeline?: {
    decision: string;
    recommended: boolean;
    preset_id?: string | null;
    preset_name?: string | null;
    score_line: number;
    selected_skill_count: number;
    skill_gate_passed: boolean;
    reviewed: boolean;
  };
  dsa_profile: {
    macd_status: string | null;
    macd_dif: number | null;
    macd_dea: number | null;
    rsi_12: number | null;
    bias_ma5: number | null;
    bias_ma10: number | null;
    trend_status: string | null;
    volume_status: string | null;
  };
  scoring: SingleStockScoring;
  entry_rules: SingleStockEntryRules;
  llm_analysis?: SingleStockLlmAnalysis;
  message?: string;
};
