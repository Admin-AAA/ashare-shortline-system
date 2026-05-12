export type RunMode = "formal" | "experiment";
export type SkillMode = "formal" | "experiment" | "disabled";

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
  output_count: number;
  bucket_limits: Record<string, number>;
  data_source: string;
  hide_recent_repeats: boolean;
  status: string;
  skills: PresetSkill[];
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
  output_count: number;
  universe_count: number;
  recommended_count: number;
  status: string;
  error_message?: string | null;
  skill_count: number;
  hit_rate: number;
  result_summary: {
    bucket_counts?: Record<string, number>;
    top_codes?: string[];
    avg_score?: number;
    data_source_mode?: string;
    data_source?: string;
    quote_source?: string;
    kline_sources?: Record<string, number>;
    agent_runtime?: string;
    main_industries?: string[];
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
  required_candidate_count: number;
  quote: {
    available_count: number;
    candidate_count: number;
    latest_trade_date?: string | null;
    active_sources: string[];
  };
  kline: {
    checked_count: number;
    ready_count: number;
    missing_count: number;
    stale_count: number;
    min_bars: number;
    examples: Array<{
      code?: string | null;
      name?: string | null;
      reason: string;
      bar_count: number;
      latest_bar_date?: string | null;
      quote_date?: string | null;
    }>;
  };
  messages: string[];
};

export type LocalPoolStatus = {
  summary: {
    raw_quote_count: number;
    raw_bar_count: number;
    unified_quote_count: number;
    active_unified_quote_count: number;
    daily_bar_count: number;
    daily_bar_row_count?: number;
    daily_indicator_count: number;
    indicator_coverage_pct: number;
    latest_quote_date?: string | null;
    latest_bar_date?: string | null;
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

export type SkillSignal = {
  strategy_id: string;
  strategy_name: string;
  version_id: string;
  phase: string;
  score: number;
  confidence: number;
  bucket_preference: string;
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
  instruction_digest?: {
    frontmatter?: Record<string, unknown>;
    principles?: string[];
    body_hash?: string | null;
    reference_files?: SkillReferenceFile[];
    prompt_chars?: number;
  };
  prompt_contract?: Record<string, unknown>;
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
  bucket: string;
  final_score: number;
  consensus_score: number;
  setup_type?: string | null;
  entry_rule?: string | null;
  invalid_rule?: string | null;
  rationale?: string | null;
  risk_tags: string[];
  feature_snapshot: FeatureSnapshot;
  market_context: MarketSnapshot;
  skill_breakdown: SkillSignal[];
  status: string;
  recent_repeat: boolean;
  watch_status: string;
  watch_note?: string | null;
};

export type RecommendationOutcome = {
  horizon_days: number;
  entry_triggered: boolean;
  entry_date?: string | null;
  entry_price?: number | null;
  max_return: number;
  min_return: number;
  close_return: number;
  max_drawdown: number;
  hit_target: boolean;
  hit_invalid: boolean;
  outcome_label: string;
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
  bucket: string;
  status: string;
};

export type RecommendationDetail = RecommendationCard & {
  run: RunSummary | null;
  outcome: RecommendationOutcome | null;
  timeline: TimelinePoint[];
  agent_logs: AgentLog[];
  review_note?: ReviewNote;
  history: RecommendationHistoryRow[];
};

export type RunResult = {
  run: RunSummary;
  recommendations: RecommendationCard[];
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
  success_count: number;
  failed_count: number;
  pending_count: number;
  watch_count: number;
  reviewed_count: number;
  hit_rate: number;
  avg_return: number;
  avg_max_return: number;
  avg_drawdown: number;
  avg_score: number;
  latest_run_at?: string | null;
  top_buckets: Array<{ bucket: string; count: number }>;
};

export type SkillStatsRow = {
  skill_id: string;
  name: string;
  current_mode: string;
  sample_count: number;
  run_count: number;
  labeled_count: number;
  success_count: number;
  hit_rate: number;
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
  output_count: number;
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
  success_count: number;
  hit_rate: number;
  avg_return: number;
  avg_score: number;
  last_run_at?: string | null;
};
