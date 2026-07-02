import type {
  AccuracyGateSummary,
  AccuracyPolicyAudit,
  MinuteCoverageSummary,
  MinuteGateSummary,
  MinutePrepResult,
} from "../types";

export type BacktestResult = {
  strategy_id?: string;
  strategy_name?: string;
  action?: string;
  minute_gate?: MinutePrepResult["minute_gate"];
  status: string;
  dates_scanned: number;
  dates_succeeded: number;
  dates_failed: number;
  total_recommendations: number;
  total_elapsed_ms?: number;
  labeled_count: number;
  results: Array<{
    trade_date: string;
    status: string;
    candidate_count?: number;
    recommendation_count?: number;
    skill_count?: number;
    elapsed_ms?: number;
    reason?: string;
    error?: string;
    signal_count?: number;
    resolved_trade_count?: number;
    recommendation_accuracy?: number;
    average_net_return?: number;
  }>;
  aggregate_metrics?: {
    total_recommendations: number;
    labeled_count: number;
    triggered_count: number;
    executed_trade_count?: number;
    unexecuted_count?: number;
    trigger_rate?: number;
    success_count: number;
    accuracy_numerator?: number;
    accuracy_denominator?: number;
    trade_accuracy?: number;
    accuracy_basis?: string;
    hit_rate: number;
    recommendation_accuracy?: number;
    avg_return: number;
    avg_executed_return: number;
    average_executed_return?: number;
    average_net_return?: number;
    median_net_return?: number;
    avg_weighted_return?: number;
    avg_close_return?: number;
    avg_max_return?: number;
    avg_max_drawdown: number;
    max_drawdown?: number;
    avg_achievable_return?: number;
    avg_holding_days?: number;
    profit_factor?: number;
    profit_loss_ratio?: number;
    win_loss_ratio?: number;
    trade_count?: number;
    strategy_compound_return?: number;
    positive_month_ratio?: number;
    benchmark_excess_return?: number;
    sample_confidence?: string;
    low_confidence?: boolean;
    flat_count?: number;
    accuracy_policy?: AccuracyPolicyAudit;
    accuracy_gate_summary?: AccuracyGateSummary;
    false_positive_notes?: Array<{
      reason: string;
      count: number;
      examples?: string[];
      category?: string;
      category_label?: string;
      stage?: string;
    }>;
  };
  comparison?: {
    selected_variant: string;
    selected_variant_label: string;
    adoption_reason: string;
    decision_scope?: string;
    decision_scope_label?: string;
    guardrail_notes?: string[];
    decision_metrics?: {
      baseline?: BacktestResult["aggregate_metrics"];
      candidate?: BacktestResult["aggregate_metrics"];
    };
    baseline: BacktestResult["aggregate_metrics"];
    candidate: BacktestResult["aggregate_metrics"];
    walk_forward?: {
      baseline?: WalkForwardAnalysis;
      candidate?: WalkForwardAnalysis;
    };
    time_split?: {
      baseline?: TimeSplitAnalysis;
      candidate?: TimeSplitAnalysis;
    };
    validation_profile?: ValidationProfile;
  };
  false_positive_samples?: Array<{
    trade_date: string;
    code: string;
    name: string;
    entry_time?: string;
    exit_time?: string;
    net_return_pct: number;
    false_positive_reason: string;
    signal_valid_at_recommend_time?: boolean;
    executed?: boolean;
    net_return_positive?: boolean;
    accuracy_failure_label?: string | null;
  }>;
  recommendation_records?: Array<{
    trade_date: string;
    code: string;
    name: string;
    recommendation_time: string;
    entry_time: string;
    entry_price: number;
    exit_date: string;
    exit_time: string;
    exit_price: number;
    gross_return_pct: number;
    net_return_pct: number;
    benchmark_return_pct?: number;
    accurate: boolean;
    signal_valid_at_recommend_time?: boolean;
    executed?: boolean;
    net_return_positive?: boolean;
    accuracy_failure_label?: string | null;
    exit_reason: string;
    false_positive_reason?: string | null;
  }>;
  data_coverage?: {
    signal_date_count: number;
    candidate_count: number;
    minute_ready_candidate_count: number;
    minute_missing_candidate_count: number;
    candidate_minute_coverage_rate: number;
    benchmark_minute_covered_dates: number;
    benchmark_minute_coverage_rate: number;
    missing_minute_examples: string[];
    local_minute_inventory: {
      trade_date_start?: string;
      trade_date_end?: string;
      trade_date_count?: number;
      code_count?: number;
    };
    minute_source_diagnostics?: {
      attempt_count: number;
      by_source: Array<{
        source: string;
        status: string;
        scope?: string;
        attempt_count: number;
        row_count: number;
        example_reason?: string;
      }>;
      successful_sources: string[];
      fallback_used: boolean;
      tushare_stk_mins_status: string;
      tushare_permission_denied: boolean;
      samples?: Array<{
        source: string;
        status: string;
        code?: string;
        reason?: string;
      }>;
    };
  };
  notes?: string[];
  horizon_return_matrix?: Array<{
    strategy_id: string;
    strategy_name: string;
    recommendation_count: number;
    horizons: Record<string, { sample_count: number; average_return_pct: number | null }>;
  }>;
  breakdown?: {
    by_skill: Array<{
      skill_id: string;
      recommendation_count: number;
      triggered_count: number;
      trigger_rate: number;
      success_count: number;
      hit_rate: number;
      avg_score: number;
    }>;
    by_setup: Array<{
      setup_type: string;
      recommendation_count: number;
      triggered_count: number;
      missed_count?: number;
      success_count: number;
      trigger_rate: number;
      hit_rate: number;
      avg_return: number;
    }>;
    by_industry: Array<{
      industry: string;
      recommendation_count: number;
      triggered_count: number;
      trigger_rate: number;
      success_count: number;
      hit_rate: number;
    }>;
    by_market_regime: Array<{
      market_regime: string;
      recommendation_count: number;
      triggered_count: number;
      missed_count?: number;
      success_count: number;
      trigger_rate: number;
      hit_rate: number;
      avg_return: number;
    }>;
    by_score_range: Array<{
      range: string;
      recommendation_count: number;
      triggered_count: number;
      trigger_rate: number;
      success_count: number;
      hit_rate: number;
    }>;
    by_score_line: Array<{
      score_line: number;
      sample_count: number;
      activation_rate: number;
      labeled_count: number;
      triggered_count: number;
      success_count: number;
      trigger_rate: number;
      hit_rate: number;
      avg_return: number;
      avg_drawdown: number;
      avg_close_return: number;
    }>;
  };
};

export type MinuteAction = "preview" | "remediate" | null;

export type BacktestHistoryItem = {
  backtest_id: string;
  skill_id: string;
  strategy_name: string;
  date_from: string;
  date_to: string;
  status: string;
  created_at?: string | null;
  selected_variant?: string | null;
  selected_variant_label?: string | null;
  adoption_reason?: string | null;
  decision_scope?: string | null;
  decision_scope_label?: string | null;
  guardrail_notes?: string[];
  decision_metrics?: {
    baseline?: BacktestResult["aggregate_metrics"];
    candidate?: BacktestResult["aggregate_metrics"];
  };
  validation_profile?: ValidationProfile;
  metrics: {
    trade_count?: number;
    recommendation_accuracy?: number;
    accuracy_numerator?: number;
    accuracy_denominator?: number;
    trade_accuracy?: number;
    executed_trade_count?: number;
    unexecuted_count?: number;
    average_net_return?: number;
    average_executed_return?: number;
    max_drawdown?: number;
    benchmark_excess_return?: number;
    sample_confidence?: string;
    accuracy_policy?: AccuracyPolicyAudit;
    accuracy_gate_summary?: AccuracyGateSummary;
  };
  data_coverage?: {
    candidate_minute_coverage_rate?: number;
    benchmark_minute_coverage_rate?: number;
    tushare_stk_mins_status?: string;
    tushare_permission_denied?: boolean;
    fallback_used?: boolean;
  };
  result?: BacktestResult;
};

export type BacktestHistoryPage = {
  items: BacktestHistoryItem[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  has_prev: boolean;
  has_next: boolean;
};

export type BacktestVersionMatrixItem = {
  backtest_id: string;
  skill_id: string;
  strategy_name: string;
  date_from: string;
  date_to: string;
  created_at?: string | null;
  selected_variant?: string | null;
  selected_variant_label?: string | null;
  decision_scope?: string | null;
  decision_scope_label?: string | null;
  baseline_label?: string | null;
  candidate_label?: string | null;
  baseline_accuracy?: number;
  candidate_accuracy?: number;
  accuracy_delta?: number;
  baseline_accuracy_denominator?: number;
  candidate_accuracy_denominator?: number;
  baseline_trade_accuracy?: number;
  candidate_trade_accuracy?: number;
  trade_accuracy_delta?: number;
  baseline_executed_trade_count?: number;
  candidate_executed_trade_count?: number;
  baseline_unexecuted_count?: number;
  candidate_unexecuted_count?: number;
  baseline_return?: number;
  candidate_return?: number;
  return_delta?: number;
  baseline_drawdown?: number;
  candidate_drawdown?: number;
  drawdown_delta?: number;
  baseline_confidence?: string;
  candidate_confidence?: string;
  validation_profile?: {
    primary_scope?: string | null;
    primary_scope_label?: string | null;
    fallback_reason?: string | null;
  };
  guardrail_notes?: string[];
  adoption_reason?: string | null;
};

export type WalkForwardAnalysis = {
  eligible: boolean;
  window_count: number;
  min_train_dates: number;
  test_aggregate: BacktestResult["aggregate_metrics"];
  windows: Array<{
    train_start: string;
    train_end: string;
    test_start: string;
    test_end: string;
    train: BacktestResult["aggregate_metrics"];
    test: BacktestResult["aggregate_metrics"];
  }>;
  notes?: string[];
};

export type TimeSplitAnalysis = {
  eligible: boolean;
  train_trade_count?: number;
  test_trade_count?: number;
  train_date_range?: { start?: string; end?: string };
  test_date_range?: { start?: string; end?: string };
  train_metrics: BacktestResult["aggregate_metrics"];
  test_metrics: BacktestResult["aggregate_metrics"];
  train_vs_test?: {
    accuracy_diff?: number;
    return_diff?: number;
  };
  notes?: string[];
};

export type ValidationSnapshot = {
  eligible?: boolean;
  window_count?: number;
  date_count?: number;
  trade_count?: number;
  recommendation_accuracy?: number;
  average_net_return?: number;
  max_drawdown?: number;
  sample_confidence?: string;
  notes?: string[];
};

export type ValidationProfile = {
  primary_scope?: string | null;
  primary_scope_label?: string | null;
  fallback_reason?: string | null;
  primary_samples?: {
    baseline_trade_count?: number;
    candidate_trade_count?: number;
    baseline_confidence?: string;
    candidate_confidence?: string;
  };
  walk_forward?: {
    baseline?: ValidationSnapshot;
    candidate?: ValidationSnapshot;
  };
  time_split?: {
    baseline?: TimeSplitAnalysis;
    candidate?: TimeSplitAnalysis;
  };
  aggregate?: {
    baseline?: BacktestResult["aggregate_metrics"];
    candidate?: BacktestResult["aggregate_metrics"];
  };
};
