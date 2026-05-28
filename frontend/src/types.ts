export type RunMode = "formal" | "experiment";
export type SkillMode = "formal" | "experiment" | "disabled";
export type ScheduledTaskType = "data_sync" | "recommendation_run" | "pool_refresh" | "data_backfill";

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
  run_mode: RunMode;
  market_scope: string;
  market_scope_value?: string | null;
  risk_template: string;
  score_line: number;
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
  run_mode: RunMode;
  trade_date: string;
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
  hit_rate: number;
  result_summary: {
    top_codes?: string[];
    avg_score?: number;
    data_source_mode?: string;
    data_source?: string;
    quote_source?: string;
    kline_sources?: Record<string, number>;
    agent_runtime?: string;
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
  heartbeat_stale: boolean;
  resumable: boolean;
};

export type DashboardDataHealth = {
  status: string;
  can_run_local_recommendation: boolean;
  quote_total: number;
  active_quote_count: number;
  eligible_quote_count: number;
  bar_coverage_pct: number;
  eligible_bar_coverage_pct: number;
  missing_bar_count: number;
  stale_bar_count: number;
  short_history_count: number;
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

export type DashboardOverview = {
  market: MarketTemperature;
  recommendations: CockpitRecommendation[];
  risk_radar: RiskRadar;
  current_run: RunProgress;
  data_health: DashboardDataHealth;
  task_health: TaskHealthSummary;
  validation: ValidationSummary;
  quality_scorecard: QualityScorecard;
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
  error?: string;
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

export type MinuteGateDateSummary = {
  trade_date: string;
  status: string;
  candidate_count?: number;
  minute_ready_candidate_count?: number;
  minute_missing_candidate_count?: number;
  benchmark_minute_covered?: boolean;
  missing_minute_codes?: string[];
};

export type MinuteGateSummary = {
  passed: boolean;
  reason: string;
  coverage_rate?: number;
  benchmark_coverage_rate?: number;
  candidate_count?: number;
  minute_ready_candidate_count?: number;
  missing_pair_count?: number;
  missing_pair_sample_count?: number;
  benchmark_required_dates?: string[];
  benchmark_missing_dates?: string[];
  missing_pairs?: Array<{ trade_date: string; code: string }>;
  missing_codes_by_date?: Record<string, string[]>;
  date_summaries?: MinuteGateDateSummary[];
  notes?: string[];
};

export type MinuteCoverageSummary = {
  signal_date_count?: number;
  candidate_count?: number;
  minute_ready_candidate_count?: number;
  minute_missing_candidate_count?: number;
  candidate_minute_coverage_rate?: number;
  benchmark_minute_covered_dates?: number;
  benchmark_minute_coverage_rate?: number;
  missing_minute_examples?: string[];
  local_minute_inventory?: {
    trade_date_start?: string;
    trade_date_end?: string;
    trade_date_count?: number;
    code_count?: number;
    code_count_scope?: string;
    trade_date_count_scope?: string;
    inventory_status?: string;
    inventory_error?: string;
  };
  minute_source_diagnostics?: {
    attempt_count?: number;
    by_source?: Array<{
      source: string;
      status: string;
      scope?: string;
      attempt_count?: number;
      row_count?: number;
      example_reason?: string;
    }>;
    successful_sources?: string[];
    fallback_used?: boolean;
    tushare_stk_mins_status?: string;
    tushare_permission_denied?: boolean;
    samples?: Array<{
      source: string;
      status: string;
      code?: string;
      reason?: string;
    }>;
  };
};

export type MinuteRemediationAction = {
  table?: string;
  scope?: string;
  trade_date?: string;
  trade_date_start?: string;
  trade_date_end?: string;
  requested_code_count?: number;
  requested_date_count?: number;
  row_count?: number;
  status?: string;
};

export type MinutePrepResult = {
  status: string;
  strategy_window?: {
    start_date: string;
    end_date: string;
  };
  minute_gate?: MinuteGateSummary;
  data_coverage?: MinuteCoverageSummary;
  before?: MinutePrepResult;
  after?: MinutePrepResult;
  actions?: MinuteRemediationAction[];
  comparison?: {
    candidate_coverage_rate_delta?: number;
    benchmark_coverage_rate_delta?: number;
    gate_passed_before?: boolean;
    gate_passed_after?: boolean;
    target_missing_pair_count?: number;
    sampled_missing_pair_count?: number;
    resolved_missing_pair_count?: number;
    unresolved_missing_pair_count?: number;
    sample_resolved_missing_pair_count?: number;
    sample_unresolved_missing_pair_count?: number;
    benchmark_target_missing_date_count?: number;
    benchmark_resolved_missing_date_count?: number;
    benchmark_unresolved_missing_date_count?: number;
    target_missing_unit_count?: number;
    resolved_missing_unit_count?: number;
    unresolved_missing_unit_count?: number;
    remediation_coverage_rate?: number;
  };
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

export type HomeBootstrap = {
  presets: Preset[];
  recent_runs: {
    formal: RunSummary | null;
    experiment: RunSummary | null;
  };
  market_snapshot: MarketSnapshot;
  skill_summary: {
    formal_count: number;
    experiment_count: number;
    disabled_count: number;
  };
};

export type HomeInitialState = {
  home: HomeBootstrap;
  presets: Preset[];
  runs: RunSummary[];
  task_runs: ScheduledTaskRun[];
  skills: SkillSummary[];
  data_sources: DataSourceHealthRow[];
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

export type DataSourceType = "quote" | "kline";

export type DataSourceStatus = "healthy" | "degraded" | "disabled" | "untested";

export type DataSourceHealthRow = {
  source: string;
  source_type: DataSourceType;
  enabled: boolean;
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
  status: DataSourceStatus;
  updated_at?: string | null;
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
    stale_count: number;
    min_bars: number;
    coverage_count?: number;
    coverage_rate?: number;
    latest_trade_date?: string | null;
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

export type RecommendationCard = {
  recommendation_id: string;
  run_id: string;
  trade_date: string;
  recommended_at: string;
  code: string;
  name: string;
  industry?: string | null;
  recommend_price: number;
  stop_loss?: number | null;
  take_profit?: number | null;
  final_score: number;
  consensus_score: number;
  setup_type?: string | null;
  entry_rule?: string | null;
  invalid_rule?: string | null;
  rationale?: string | null;
  risk_tags: string[];
  info_tags: string[];
  feature_snapshot: FeatureSnapshot;
  market_context: MarketSnapshot;
  skill_breakdown: SkillSignal[];
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
  mode: SkillMode;
  enabled: boolean;
  has_executable_contract: boolean;
  has_agent_contract?: boolean;
  agent_runtime?: string;
  reference_count?: number;
  manifest: Record<string, unknown>;
  assigned_presets: string[];
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
  mode: SkillMode;
  enabled: boolean;
  current_version: string;
  has_executable_contract: boolean;
  has_agent_contract?: boolean;
  agent_runtime?: string;
  reference_files?: SkillReferenceFile[];
  manifest: Record<string, unknown>;
  skill_md: string;
  errors: string[];
  assigned_presets: Array<{ preset_id: string; name: string }>;
  performance: {
    formal: SkillPerformance;
    experiment: SkillPerformance;
  };
};

export type StatsOverview = {
  mode: RunMode;
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

export type SkillStatsRow = {
  skill_id: string;
  name: string;
  current_mode: string;
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
  run_mode: RunMode;
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

export type PrecisionAtKRow = {
  k: number;
  sample_runs: number;
  evaluated_count: number;
  success_count: number;
  precision: number;
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

/** 9-KPI 评估指标（来自 /api/metrics/overview） */
export type MetricsOverview = {
  mode: "formal" | "experiment";
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
};
