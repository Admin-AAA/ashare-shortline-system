"""Canonical outcome simulation adapter.

The service converts ORM-shaped recommendations and bars into the canonical
execution-core contract, then projects the result for ``RecommendationOutcome``
persistence. Entry selection, T+1 exits, trailing state, gap fills, costs and
position caps are owned by the current canonical policy/core; no legacy exit
implementation or historical execution compatibility path lives here.
"""
from __future__ import annotations

import json
from bisect import bisect_left
from datetime import time, timedelta
from typing import Any

from app.engine.board_rules import is_one_word_limit_down_bar, limit_pct_for_code
from app.engine.execution_core import (
    CausalEntryBar,
    CausalEntryConfig,
    CausalExitConfig,
    CausalExitResult,
    ExecutionBar,
    compose_position_caps,
    resolve_causal_entry,
    resolve_causal_exit,
)
from app.engine.execution_policy import (
    DEFAULT_EXECUTION_POLICY,
    ExecutionPolicy,
    PositionCapComposition,
)
from app.engine.trading_costs import is_net_profitable, net_return_after_cost
from app.models.tables import DailyKbar, Recommendation
from app.services.entry_policy import (
    EntryPolicy,
    entry_policy_for_mode,
)

# A 股 T+1 交收：买入当日不能卖出。推荐时点决定入场日与最早可离场日：
# - 盘后推荐（recommended_at 落在非交易时段）：推荐价≈当日收盘，T+1 才能买入 → 离场最早 T+2
# - 盘中推荐（recommended_at 落在交易时段）：推荐价为盘中实时价，T 当天可成交 → 离场最早 T+1
# recommended_at 以 UTC 存储（datetime.utcnow），北京时间 09:30–15:00 = UTC 01:30–07:00。
_INTRADAY_UTC_START = time(1, 30)
_INTRADAY_UTC_END = time(7, 0)


def _is_intraday_recommendation(rec: Recommendation) -> bool:
    """推荐是否在交易时段内发出（盘中推荐）。

    盘中推荐可当日按推荐价成交（T 入场、T+1 离场）；盘后推荐只能 T+1 入场、T+2 离场。
    recommended_at 缺失时按盘后处理（保守：延后离场，不虚构当日可卖）。
    """
    recommended_at = getattr(rec, "recommended_at", None)
    if recommended_at is None:
        return False
    t = recommended_at.time() if hasattr(recommended_at, "time") else None
    if t is None:
        return False
    trade_date = str(getattr(rec, "trade_date", "") or "")
    beijing_date = (recommended_at + timedelta(hours=8)).date().isoformat()
    if trade_date and beijing_date != trade_date:
        return False
    return _INTRADAY_UTC_START <= t <= _INTRADAY_UTC_END
from app.services.execution_constraints import (
    entry_execution_block_reason_from_bar,
    exit_execution_block_reason_from_bar,
    no_liquidity_reason_from_bar,
)

# ── 模拟引擎配置常量 ─────────────────────────────────────────────────────
_EXECUTION_RUNTIME = DEFAULT_EXECUTION_POLICY.runtime_constant_projection()
ENTRY_LOOKAHEAD_DAYS = int(_EXECUTION_RUNTIME["ENTRY_LOOKAHEAD_DAYS"])
ENTRY_SLIPPAGE_BPS = float(_EXECUTION_RUNTIME["ENTRY_SLIPPAGE_BPS"])
EXIT_SLIPPAGE_BPS = float(_EXECUTION_RUNTIME["EXIT_SLIPPAGE_BPS"])
SUCCESS_RETURN_PCT = 3.0
DEFAULT_STOP_LOSS_PCT = float(_EXECUTION_RUNTIME["DEFAULT_STOP_LOSS_PCT"])
DEFAULT_TAKE_PROFIT_PCT = float(_EXECUTION_RUNTIME["DEFAULT_TAKE_PROFIT_PCT"])
# 2026-09-15 补：结算路径原先只读数值、不读 enabled，会把"关掉止盈/止损/trailing"
# 静默忽略（execution_policy v3 发布时发现的真实缺口）。
STOP_LOSS_ENABLED = bool(_EXECUTION_RUNTIME.get("STOP_LOSS_ENABLED", True))
TAKE_PROFIT_ENABLED = bool(_EXECUTION_RUNTIME.get("TAKE_PROFIT_ENABLED", True))
TRAILING_ENABLED = bool(_EXECUTION_RUNTIME.get("TRAILING_ENABLED", True))
                                 # avg peak 4.27%，33/65 峰值在 3-5%，TP 下修捕获更多止盈窗口
MAX_STOP_LOSS_PCT = float(_EXECUTION_RUNTIME["MAX_STOP_LOSS_PCT"])
                                 # 第164轮 D 项修复：原 -9% 在实践中只对"高开入场"场景生效
                                 # （正常入场止损都在 -5.7% 以内），且让该场景亏损从 -5.7% 放大到 -9%
                                 # 反事实验证：184 formal 样本仅长裕集团(entry=85>>rec_price 79.32 高开
                                 # 接力)1 票受影响，min -9.18%→-5.79%(达标基线-5.8%)，盈亏比 1.44→1.49，0 误伤
                                 # 因果：高开入场已透支上涨空间，止损不该比正常入场更宽；-9% 反向放大尾部风险
MAX_TAKE_PROFIT_PCT = float(_EXECUTION_RUNTIME["MAX_TAKE_PROFIT_PCT"])
STOP_LOSS_GAP_CAP_PCT = float(_EXECUTION_RUNTIME["STOP_LOSS_GAP_CAP_PCT"])
MAX_ENTRY_GAP_PCT = float(_EXECUTION_RUNTIME["MAX_ENTRY_GAP_PCT"])

# Canonical trailing constants are immutable projections of the current execution policy.
TRAILING_STOP_ACTIVATE_PCT = float(_EXECUTION_RUNTIME["TRAILING_STOP_ACTIVATE_PCT"])
TRAILING_STOP_CALLBACK_PCT = float(_EXECUTION_RUNTIME["TRAILING_STOP_CALLBACK_PCT"])
TRAILING_STOP_MIN_LOCK_PCT = float(_EXECUTION_RUNTIME["TRAILING_STOP_MIN_LOCK_PCT"])
TRAILING_STOP_PROFIT_LOCK_RATIO = float(_EXECUTION_RUNTIME["TRAILING_STOP_PROFIT_LOCK_RATIO"])

OUTCOME_PENDING = "pending"
OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_MISSED = "missed"
# 临时态：尚未触发终局退出且观察期未走满。当前收盘只作临时标记，
# 不计入 EXECUTED_OUTCOME_LABELS（不算已结算），避免中间态污染成败口径。
OUTCOME_PROVISIONAL = "provisional"
EXECUTED_OUTCOME_LABELS = {OUTCOME_SUCCESS, OUTCOME_FAILED}


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return {} if default is None else default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {} if default is None else default


def _simulate_outcome(
    rec: Recommendation,
    bars: list[DailyKbar],
    dates: list[str],
    horizon_days: int,
    feature_snapshot: dict[str, Any] | None = None,
    *,
    pre_resolved_entry: dict[str, Any] | None = None,
    exit_resolver: Any = None,
    entry_policy: EntryPolicy | None = None,
) -> dict[str, Any] | None:
    """模拟单笔推荐 outcome。

    exit_resolver 默认 None → 用生产单路径因果利润地板 `_resolve_exit`。
    传入自定义解析器（签名同 _resolve_exit：holding_bars, stop_loss, take_profit,
    *, code, entry_price）可替换退出逻辑，用于动态退出状态机反事实验证。
    entry_policy 未显式传入时，按推荐快照中的策略 entry_mode 选择；旧快照缺失时
    由持久化 bucket 做确定性迁移，其余策略回退到原有目标价触碰口径。
    入场时序、holding_bars 起点、executed_entry_price（含滑点）、除权重算等全部
    复用生产路径，只替换 exit 环节。
    """
    if not bars:
        return None

    signal_index = bisect_left(dates, str(rec.trade_date))
    signal_bar = bars[signal_index] if signal_index < len(bars) and dates[signal_index] == str(rec.trade_date) else None
    future_start = signal_index + 1 if signal_bar is not None else signal_index
    future_bars = bars[future_start:]
    if not future_bars:
        return None

    feature_snapshot = feature_snapshot if feature_snapshot is not None else _loads(getattr(rec, "feature_snapshot_json", None), {})
    # LIVE formal 持久化不把 final_score 写入 feature_snapshot_json，但它落在
    # Recommendation.final_score 列。仓位策略依赖 final_score，缺失时注入，
    # 使 _resolve_position_size 的 score-bucket 逻辑对 LIVE 与回测一致生效。
    if "final_score" not in feature_snapshot:
        rec_final_score = _as_float(getattr(rec, "final_score", None))
        if rec_final_score > 0:
            feature_snapshot = {**feature_snapshot, "final_score": rec_final_score}
    selected_entry_policy = entry_policy or entry_policy_for_mode(
        feature_snapshot.get("entry_mode"),
        legacy_strategy_id=getattr(rec, "bucket", None),
    )
    static_metrics = _static_window_metrics(rec, signal_bar, future_bars, horizon_days)

    recommendation_block_reason = _recommendation_execution_block_reason(feature_snapshot)
    if recommendation_block_reason:
        return {
            **static_metrics,
            "entry_policy_id": selected_entry_policy.policy_id,
            "entry_triggered": False,
            "entry_date": None,
            "entry_price": None,
            "exit_date": None,
            "exit_price": None,
            "executed_return": 0.0,
            "net_return_pct": None,
            "weighted_return": 0.0,
            "position_size": _resolve_position_size(feature_snapshot),
            "holding_days": 0,
            "canonical_exit_evidence": False,
            "intraday_exit_evidence": None,
            "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
            "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
            "stop_loss_price": _resolve_stop_loss_price(None, feature_snapshot),
            "take_profit_price": _resolve_take_profit_price(None, feature_snapshot),
            "exit_reason": "recommendation_execution_blocked",
            "hit_target": False,
            "hit_invalid": False,
            "outcome_label": OUTCOME_MISSED,
            "outcome_label_legacy": OUTCOME_MISSED,
            "outcome_reason": f"{recommendation_block_reason}；推荐时已判定不可真实成交，静态窗口结果仅作旁路参考",
        }

    # entry 解析：BREAKDOWN_HORIZON_DAYS=[3,5,10] 都 ≥ ENTRY_LOOKAHEAD_DAYS=2，
    # entry_window=future_bars[:2] 与 horizon_days 无关 → 3 horizon 可共享一次解析。
    # pre_resolved_entry 由 _compute_horizon_metric_records 预解析传入，省 2/3 _resolve_entry 调用（占模拟 77%）。
    entry_result = pre_resolved_entry if pre_resolved_entry is not None else _resolve_entry(
        rec, feature_snapshot, future_bars, horizon_days, signal_bar=signal_bar,
        entry_policy=selected_entry_policy,
    )
    position_size = _resolve_position_size(feature_snapshot)

    if not entry_result.get("triggered"):
        entry_reason = str(entry_result.get("reason") or "未触发买点，未建仓")
        exit_reason = "gap_up_blocked" if entry_reason == "gap_up_blocked" else "not_triggered"
        if exit_reason == "gap_up_blocked":
            observed_gap = float(entry_result.get("observed_gap_pct") or 0.0)
            outcome_reason = (
                f"跳空高开 {observed_gap:.1f}% > {MAX_ENTRY_GAP_PCT:.0f}% 阈值，放弃入场"
            )
        else:
            outcome_reason = f"{entry_reason}；静态窗口结果仅作旁路参考"
        return {
            **static_metrics,
            "entry_policy_id": selected_entry_policy.policy_id,
            "entry_triggered": False,
            "entry_date": None,
            "entry_price": None,
            "exit_date": None,
            "exit_price": None,
            "executed_return": 0.0,
            "net_return_pct": None,
            "weighted_return": 0.0,
            "position_size": position_size,
            "holding_days": 0,
            "canonical_exit_evidence": False,
            "intraday_exit_evidence": None,
            "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
            "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
            "stop_loss_price": _resolve_stop_loss_price(None, feature_snapshot),
            "take_profit_price": _resolve_take_profit_price(None, feature_snapshot),
            "exit_reason": exit_reason,
            "hit_target": False,
            "hit_invalid": False,
            "outcome_label": OUTCOME_MISSED,
            "outcome_label_legacy": OUTCOME_MISSED,
            "outcome_reason": outcome_reason,
        }

    raw_entry_price = float(entry_result["price"])
    entry_date = str(entry_result["date"])

    recommend_price = _as_float(rec.recommend_price) or 0

    executed_entry_price = round(float(entry_result["executed_price"]), 4)
    stop_loss_price = _resolve_stop_loss_price(raw_entry_price, feature_snapshot)
    take_profit_price = _resolve_take_profit_price(
        raw_entry_price, feature_snapshot, recommend_price=recommend_price
    )

    # 持仓 K 线：从入场日（含）起的 future bars，取 horizon_days 根。
    # 用 entry_date 在 bars 里定位起点，而非 entry_index——盘中推荐时入场日可能是
    # signal_bar（T 当天），entry_index 相对 entry_window 的语义与 future_bars 不同步，
    # 按 entry_date 定位最稳。入场日是 signal_bar 时从 signal_index 起，否则从 future_start 起。
    if signal_bar is not None and entry_date == str(signal_bar.trade_date):
        holding_start = signal_index
    else:
        holding_start = future_start
        # 在 future_bars 里找 entry_date 对应的偏移
        for _i, _b in enumerate(future_bars):
            if str(_b.trade_date) == entry_date:
                holding_start = future_start + _i
                break
    holding_bars = bars[holding_start : holding_start + max(1, horizon_days)]
    if not holding_bars:
        return None

    if exit_resolver is None:
        exit_result = _resolve_exit_result(
            holding_bars,
            stop_loss_price,
            take_profit_price,
            code=str(rec.code),
            entry_price=executed_entry_price,
        )
        if exit_result is None:
            last = holding_bars[-1]
            exit_price = _as_float(last.close)
            exit_date = str(last.trade_date)
            exit_reason = "horizon_close_t1_locked"
            canonical_exit_evidence = False
            intraday_exit_evidence = None
        else:
            exit_price = exit_result.raw_price
            exit_date = exit_result.trade_date
            exit_reason = exit_result.reason
            canonical_exit_evidence = True
            intraday_exit_evidence = (
                exit_result.intraday_exit_evidence.canonical_payload()
            )
    else:
        exit_price, exit_date, exit_reason = exit_resolver(
            holding_bars,
            stop_loss_price,
            take_profit_price,
            code=str(rec.code),
            entry_price=executed_entry_price,
        )
        canonical_exit_evidence = False
        intraday_exit_evidence = None
    incomplete_horizon = (
        len(holding_bars) < max(1, horizon_days)
        and exit_reason
        in {
            "horizon_close",
            "horizon_close_t1_locked",
            "horizon_close_after_locked_limit_down",
            "limit_down_locked_horizon_mark",
        }
    )
    if incomplete_horizon:
        exit_reason = "horizon_incomplete"
    # 真实持仓期统计：min/max_return/close_return 只算 entry→exit 之间的 bar，而非整个 horizon。
    # 旧实现用 future_bars[:horizon]（_static_window_metrics 在 entry/exit 解析前调用），
    # 把止损/止盈后的 bar 也计入 → stop_loss_hit @ -5.7% 的票 min_return 算到 horizon 末 -34.83%
    # （688636 实证，154 笔 clean≥72 outcome 的 min_return/max_drawdown 被虚构回撤污染）。
    # exit_date 对应 holding_bars 的 bar index；盘中触发（take_profit_hit 在 high、stop_loss_hit
    # 在 low）当日 high/low 都计入（盘中确实到了该价位）。horizon_close 则用整个 holding_bars。
    exit_bar_count = len(holding_bars)  # 默认整个持仓期（horizon_close 等未提前 exit 的场景）
    if exit_date:
        for _i, _b in enumerate(holding_bars):
            if str(_b.trade_date) == str(exit_date):
                exit_bar_count = _i + 1
                break
    held_metrics = _compute_window_returns(executed_entry_price, holding_bars[:exit_bar_count])
    if held_metrics is not None:
        # 用真实持仓期统计覆盖旁路 static_metrics；achievable_return 语义同（持仓期可达成收益）
        static_metrics = {**static_metrics, **held_metrics}
    executed_exit_price = round(exit_price * (1 - EXIT_SLIPPAGE_BPS / 10000.0), 4)
    executed_return = round(((executed_exit_price - executed_entry_price) / executed_entry_price) * 100, 2)

    # 除权/复权检测：当 holding_bars 中存在除权日（open/prev_close 跳变 > 20% 但 pct_chg 在正常范围），
    # 用 pct_chg 链重新计算收益。除权日的价格跳变是除权调整，不是真实暴跌。
    # 不依赖 abs(executed_return) > 20 门槛——STOP_LOSS_GAP_CAP 可能已把 return cap 到 -15%，
    # 但除权日的虚假跳空仍需修正（如 688012 2026-05-29 除权：raw -32% → 真实 -5.47%）。
    # 涨跌停幅度按板块判定（创业/科创 20%、北交 30%、主板 10%）——旧实现只认科创板（68 前缀），
    # 把创业板（300/301）当 10cm，致 300065 这类 20cm 创业板涨停日 pct_chg=20.0% 被误判为
    # 「pct_chg 字段未除权」，触发 pct_chg 链重算把 take_profit_hit +4.79% 覆盖成 -19.28%。
    limit_pct = limit_pct_for_code(str(rec.code or ""))
    has_ex_right_day = False
    pct_chg_unreliable = False
    for i in range(1, len(holding_bars)):
        prev_close = _as_float(holding_bars[i - 1].close)
        cur_open = _as_float(holding_bars[i].open)
        cur_pct = _as_float(holding_bars[i].pct_chg)
        if prev_close > 0 and cur_open > 0:
            price_jump = (cur_open / prev_close - 1) * 100
            # 价格跳变 > 20% 但 pct_chg 字段 < limit*0.6 → 除权日（pct_chg 已调整，价格序列有跳变）
            if abs(price_jump) > 20 and abs(cur_pct) < limit_pct * 0.6:
                has_ex_right_day = True
            # 价格跳变正常但 pct_chg 远超板块涨停幅度 → pct_chg 字段未除权（数据错误）。
            # 用 limit_pct + 容差：涨停日 pct_chg 因四舍五入略超 limit_pct（如 20cm 板 20.0083%），
            # 这是正常涨停而非数据错误；只有 pct_chg 大幅超过涨停幅度（+2pp 容差）才算未除权。
            if abs(cur_pct) > limit_pct + 2.0 and abs(price_jump) < limit_pct * 0.6:
                pct_chg_unreliable = True
    max_daily_pct = max(abs(_as_float(bar.pct_chg)) for bar in holding_bars) if holding_bars else 0
    if has_ex_right_day or pct_chg_unreliable or (abs(executed_return) > 20 and max_daily_pct < limit_pct * 0.6):
        cum = 1.0
        for bar in holding_bars:
            cum *= (1 + _as_float(bar.pct_chg) / 100.0)
        executed_return = round((cum - 1) * 100, 2)
        executed_exit_price = round(executed_entry_price * (1 + executed_return / 100.0), 4)

    weighted_return = round(executed_return * position_size, 2)
    net_return_pct = net_return_after_cost(executed_return)
    hit_invalid = exit_reason.startswith("stop_loss")
    hit_target = exit_reason.startswith("take_profit") or executed_return >= SUCCESS_RETURN_PCT
    # 未触发终局退出且观察期未走满时，当前 executed_return 只是临时标记，
    # 后续交易日数据到位后会重新结算。
    # 不进 success/failed 口径，避免中间态被计入胜负统计或前端误读为最终结论。
    is_provisional = incomplete_horizon
    # outcome_label 与 accuracy policy 对齐：扣费滑点后净收益 > 0 即为准确推荐
    # SUCCESS_RETURN_PCT (3%) 是"优质推荐"阈值，不作为 outcome 硬门槛
    # executed_return 已含滑点但未扣佣金+印花税（_ADDITIONAL_COST_PCT，佣金万1双边+印花税0.05%卖出），
    # 阈值必须覆盖这部分成本，否则 0~0.07% 的微利交易被标 success 但实际亏损
    if is_provisional:
        outcome_label = OUTCOME_PROVISIONAL
    else:
        outcome_label = OUTCOME_SUCCESS if (is_net_profitable(executed_return) and not hit_invalid) else OUTCOME_FAILED

    return {
        **static_metrics,
        "entry_policy_id": selected_entry_policy.policy_id,
        "entry_triggered": True,
        "entry_date": entry_date,
        "entry_price": round(executed_entry_price, 2),
        "exit_date": exit_date,
        "exit_price": round(executed_exit_price, 2),
        "executed_return": executed_return,
        "net_return_pct": net_return_pct if not is_provisional else None,
        "weighted_return": weighted_return,
        "position_size": position_size,
        "holding_days": exit_bar_count,
        "canonical_exit_evidence": canonical_exit_evidence and not is_provisional,
        "intraday_exit_evidence": (
            intraday_exit_evidence if not is_provisional else None
        ),
        "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
        "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
        "stop_loss_price": round(stop_loss_price, 2),
        "take_profit_price": round(take_profit_price, 2),
        "exit_reason": exit_reason,
        "hit_target": hit_target,
        "hit_invalid": hit_invalid,
        "outcome_label": outcome_label,
        # 临时态时 legacy 口径同样不可用（窗口仅入场日 1 根），与主 label 保持一致
        "outcome_label_legacy": outcome_label if is_provisional else static_metrics.get("outcome_label_legacy", OUTCOME_FAILED),
        "outcome_reason": _build_outcome_reason(
            exit_reason, executed_return, static_metrics["close_return"],
            max_return=static_metrics.get("max_return"), max_drawdown=static_metrics.get("max_drawdown"),
        ),
    }


def _bar_tradeable(bar: DailyKbar) -> bool:
    """bar 是否为真实交易日（OHLC 均 > 0）。

    backfill 对停牌日写入 OHLC=0 的占位行，这类 bar 不参与 max/min_return 统计——
    low=0 会被当成真实最低点，算出 min_return=-100% 的虚假最大回撤。
    """
    return (
        _as_float(getattr(bar, "open", 0)) > 0
        and _as_float(getattr(bar, "high", 0)) > 0
        and _as_float(getattr(bar, "low", 0)) > 0
        and _as_float(getattr(bar, "close", 0)) > 0
    )


def _compute_window_returns(baseline_entry: float, bars: list[DailyKbar]) -> dict[str, float] | None:
    """对一组 bar 计算 max/min/close_return/achievable_return/max_drawdown（相对 baseline_entry）。

    纯函数，供 _static_window_metrics（未触发 entry 的旁路统计）与主路径（entry→exit 真实持仓期）
    共用。过滤停牌占位行（OHLC=0，_bar_tradeable）；bars 为空或全停牌返回 None。
    """
    tradeable = [bar for bar in bars if _bar_tradeable(bar)]
    if not tradeable or baseline_entry <= 0:
        return None
    returns = [((_as_float(bar.high) - baseline_entry) / baseline_entry) * 100 for bar in tradeable]
    lows = [((_as_float(bar.low) - baseline_entry) / baseline_entry) * 100 for bar in tradeable]
    close_return = ((_as_float(tradeable[-1].close) - baseline_entry) / baseline_entry) * 100
    max_return = max(returns) if returns else 0.0
    min_return = min(lows) if lows else 0.0
    achievable_return = (close_return + min(max_return, close_return * 1.5)) / 2.0
    return {
        "max_return": round(max_return, 2),
        "min_return": round(min_return, 2),
        "close_return": round(close_return, 2),
        "achievable_return": round(achievable_return, 2),
        "max_drawdown": round(min_return, 2),
    }


def _static_window_metrics(
    rec: Recommendation,
    signal_bar: DailyKbar | None,
    future_bars: list[DailyKbar],
    horizon_days: int,
) -> dict[str, Any]:
    window = future_bars[: max(1, horizon_days)]
    baseline_entry = _as_float(rec.recommend_price)
    if baseline_entry <= 0:
        baseline_entry = _as_float(signal_bar.close if signal_bar else None)
    if baseline_entry <= 0 and window:
        baseline_entry = _as_float(window[0].open) or _as_float(window[0].close)
    if baseline_entry <= 0 and window:
        baseline_entry = max(0.01, _as_float(window[0].close))
    # 过滤停牌占位行（OHLC 全 0）：backfill 对停牌日写入 0 占位行，若纳入 max/min_return
    # 会把 low=0 当真实最低点 → min_return=-100%（2026-06-15 全市场 1149/5508 停牌占位实证）。
    # 入场/出场逻辑已由 no_liquidity_reason_from_bar 拦截停牌日，此处统计口径需对齐。
    metrics = _compute_window_returns(baseline_entry, window)
    if metrics is None:
        # 窗口内全部停牌（极罕见）：max/min/close 都按 0 计，避免 -100% 噪声
        return {
            "max_return": 0.0,
            "min_return": 0.0,
            "close_return": 0.0,
            "achievable_return": 0.0,
            "max_drawdown": 0.0,
            "outcome_label_legacy": OUTCOME_MISSED,
        }
    # legacy_label 用窗口统计（旁路参考，主路径会被 entry→exit 截断后重算覆盖）
    legacy_label = OUTCOME_SUCCESS if (metrics["max_return"] >= 5 and metrics["min_return"] > -4) else OUTCOME_FAILED
    return {
        **metrics,
        "outcome_label_legacy": legacy_label,
    }


def _resolve_entry(
    rec: Recommendation,
    feature_snapshot: dict[str, Any],
    future_bars: list[DailyKbar],
    horizon_days: int,
    *,
    signal_bar: DailyKbar | None = None,
    entry_policy: EntryPolicy | None = None,
) -> dict[str, Any]:
    """Translate ORM-shaped inputs to the canonical entry-core contract."""
    del signal_bar  # Recommendation timestamp owns intraday causality.
    selected_entry_policy = entry_policy or entry_policy_for_mode(
        feature_snapshot.get("entry_mode"),
        legacy_strategy_id=getattr(rec, "bucket", None),
    )
    candidate_bars = tuple(
        CausalEntryBar(
            code=str(getattr(bar, "code", "") or rec.code),
            trade_date=str(bar.trade_date),
            open=_as_float(bar.open),
            low=_as_float(bar.low),
            close=_as_float(bar.close),
            block_reason=_entry_execution_block_reason(str(rec.code), bar),
        )
        for bar in future_bars
    )
    target_prices = tuple(
        value
        for value in (
            _as_float(feature_snapshot.get("ideal_buy")),
            _as_float(feature_snapshot.get("secondary_buy")),
        )
        if value > 0
    )
    decision = resolve_causal_entry(
        candidate_bars,
        CausalEntryConfig(
            policy_id=selected_entry_policy.policy_id,
            price_source=selected_entry_policy.price_source,
            signal_date=str(
                getattr(rec, "trade_date", "")
                or (candidate_bars[0].trade_date if candidate_bars else "unknown")
            ),
            recommendation_price=_as_float(getattr(rec, "recommend_price", None)),
            intraday=_is_intraday_recommendation(rec),
            target_prices=target_prices,
            max_entry_trade_days=selected_entry_policy.max_entry_trade_days,
            horizon_days=horizon_days,
            allow_target_touch=selected_entry_policy.allow_target_touch,
            fallback_to_open_when_targets_miss=(
                selected_entry_policy.fallback_to_open_when_targets_miss
            ),
            entry_slippage_bps=ENTRY_SLIPPAGE_BPS,
            max_entry_gap_pct=MAX_ENTRY_GAP_PCT,
        ),
    )
    return {
        "triggered": decision.triggered,
        "bar_index": decision.bar_index,
        "price": decision.raw_price,
        "executed_price": decision.executed_price,
        "date": decision.trade_date,
        "reason": decision.reason,
        "observed_gap_pct": decision.observed_gap_pct,
    }


def _trailing_stop_return(
    observed_high_return: float,
    trailing_policy: str,
    profit_lock_ratio: float,
    *,
    callback_pct: float | None = None,
    min_lock_pct: float | None = None,
) -> float:
    if trailing_policy == "profit_floor":
        effective_min_lock = min_lock_pct if min_lock_pct is not None else TRAILING_STOP_MIN_LOCK_PCT
        return max(effective_min_lock, observed_high_return * profit_lock_ratio)
    cb = TRAILING_STOP_CALLBACK_PCT if callback_pct is None else callback_pct
    return observed_high_return - cb


def _resolve_exit_causal_result(
    holding_bars: list[DailyKbar],
    stop_loss_price: float,
    take_profit_price: float,
    *,
    code: str = "",
    entry_price: float = 0.0,
    dynamic_trailing: bool = True,
    trailing_policy: str = "peak_callback",
    profit_lock_ratio: float = 0.25,
    activate_pct: float | None = None,
    min_lock_pct: float | None = None,
    trailing_callback: float | None = None,
) -> CausalExitResult | None:
    """Translate ORM-shaped bars to the typed canonical exit contract."""
    if dynamic_trailing and trailing_policy not in {
        "peak_callback",
        "callback",
        "profit_floor",
    }:
        raise ValueError(f"unknown trailing_policy: {trailing_policy}")
    core_bars = tuple(
        ExecutionBar(
            code=str(getattr(bar, "code", "") or code),
            trade_date=str(bar.trade_date),
            open=_as_float(bar.open),
            high=_as_float(bar.high),
            low=_as_float(bar.low),
            close=_as_float(bar.close),
            tradable=True,
            sellable=_exit_execution_block_reason(code, bar) is None,
        )
        for bar in holding_bars
    )
    return resolve_causal_exit(
        core_bars,
        CausalExitConfig(
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            entry_price=entry_price,
            t_plus_one=True,
            dynamic_trailing=dynamic_trailing,
            trailing_mode=trailing_policy,
            profit_lock_ratio=profit_lock_ratio,
            activation_gain_pct=(
                TRAILING_STOP_ACTIVATE_PCT
                if activate_pct is None
                else activate_pct
            ),
            callback_pct=(
                TRAILING_STOP_CALLBACK_PCT
                if trailing_callback is None
                else trailing_callback
            ),
            minimum_lock_gain_pct=(
                TRAILING_STOP_MIN_LOCK_PCT
                if min_lock_pct is None
                else min_lock_pct
            ),
            stop_gap_loss_cap_pct=abs(STOP_LOSS_GAP_CAP_PCT),
            stop_gap_fill_mode=DEFAULT_EXECUTION_POLICY.gap_risk.stop_gap_fill_mode,
            take_profit_gap_fill_mode=(
                DEFAULT_EXECUTION_POLICY.gap_risk.take_profit_gap_fill_mode
            ),
            intraday_exit_priority=DEFAULT_EXECUTION_POLICY.intraday_exit_priority,
        ),
    )


def _resolve_exit_causal(
    holding_bars: list[DailyKbar],
    stop_loss_price: float,
    take_profit_price: float,
    *,
    code: str = "",
    entry_price: float = 0.0,
    dynamic_trailing: bool = True,
    trailing_policy: str = "peak_callback",
    profit_lock_ratio: float = 0.25,
    activate_pct: float | None = None,
    min_lock_pct: float | None = None,
    trailing_callback: float | None = None,
) -> tuple[float, str, str]:
    """Tuple projection for research counterfactual resolvers."""
    result = _resolve_exit_causal_result(
        holding_bars,
        stop_loss_price,
        take_profit_price,
        code=code,
        entry_price=entry_price,
        dynamic_trailing=dynamic_trailing,
        trailing_policy=trailing_policy,
        profit_lock_ratio=profit_lock_ratio,
        activate_pct=activate_pct,
        min_lock_pct=min_lock_pct,
        trailing_callback=trailing_callback,
    )
    if result is None:
        if not holding_bars:
            return 0.0, "", "horizon_close"
        last = holding_bars[-1]
        return (
            _as_float(last.close),
            str(last.trade_date),
            "horizon_close_t1_locked",
        )
    return result.raw_price, result.trade_date, result.reason


def _resolve_exit_result(
    holding_bars: list[DailyKbar],
    stop_loss_price: float,
    take_profit_price: float,
    *,
    code: str = "",
    entry_price: float = 0.0,
) -> CausalExitResult | None:
    return _resolve_exit_causal_result(
        holding_bars,
        stop_loss_price,
        take_profit_price,
        code=code,
        entry_price=entry_price,
        dynamic_trailing=TRAILING_ENABLED,
        trailing_policy="profit_floor",
        profit_lock_ratio=TRAILING_STOP_PROFIT_LOCK_RATIO,
    )


def _resolve_exit(
    holding_bars: list[DailyKbar],
    stop_loss_price: float,
    take_profit_price: float,
    *,
    code: str = "",
    entry_price: float = 0.0,
) -> tuple[float, str, str]:
    """Tuple projection of the production exit for research callers."""
    result = _resolve_exit_result(
        holding_bars,
        stop_loss_price,
        take_profit_price,
        code=code,
        entry_price=entry_price,
    )
    if result is None:
        if not holding_bars:
            return 0.0, "", "horizon_close"
        last = holding_bars[-1]
        return (
            _as_float(last.close),
            str(last.trade_date),
            "horizon_close_t1_locked",
        )
    return result.raw_price, result.trade_date, result.reason


def _resolve_stop_loss_price(raw_entry_price: float | None, feature_snapshot: dict[str, Any]) -> float:
    """Resolve a stop price using the canonical default and maximum loss bounds."""
    if not STOP_LOSS_ENABLED:
        return 0.0  # 策略显式关闭止损：不设停止价，由其他出场条件决定
    raw_stop = _as_float(feature_snapshot.get("stop_loss"))
    if raw_entry_price is None or raw_entry_price <= 0:
        return round(raw_stop, 2) if raw_stop > 0 else 0.0
    max_stop = raw_entry_price * (1 + DEFAULT_STOP_LOSS_PCT / 100.0)
    if raw_stop <= 0 or raw_stop >= raw_entry_price:
        return round(max_stop, 2)
    min_stop = raw_entry_price * (1 + MAX_STOP_LOSS_PCT / 100.0)
    normalized = min(raw_stop, max_stop)   # 不紧于 -5.5%
    normalized = max(normalized, min_stop) # 不宽于 -5.7%（高开入场止损不该比正常入场更宽）
    return round(normalized, 2)


def _resolve_take_profit_price(
    raw_entry_price: float | None,
    feature_snapshot: dict[str, Any],
    recommend_price: float | None = None,
) -> float:
    """Resolve take profit relative to actual entry and canonical policy bounds.

    When the actual entry gaps away from the recommendation price, preserve the
    recommendation's implied target ratio before applying the versioned minimum
    and maximum target bounds.
    """
    raw_target = _as_float(feature_snapshot.get("take_profit"))
    if raw_entry_price is None or raw_entry_price <= 0:
        return round(raw_target, 2) if raw_target > 0 else 0.0
    if not TAKE_PROFIT_ENABLED:
        return 1e308  # 策略显式关闭止盈：等价"永不触发"（与金标准执行核一致）
    fallback = raw_entry_price * (1 + DEFAULT_TAKE_PROFIT_PCT / 100.0)
    if raw_target <= 0:
        return round(fallback, 2)
    # 跳空导致实际入场价偏离推荐价：按推荐时隐含的止盈比例重算，保证盈亏比设计不被破坏
    if recommend_price and recommend_price > 0:
        implied_tp_pct = raw_target / recommend_price - 1.0
        raw_target = raw_entry_price * (1 + implied_tp_pct)
    max_tp = raw_entry_price * (1 + MAX_TAKE_PROFIT_PCT / 100.0)
    normalized = min(raw_target, max_tp)    # 不高于 +15%
    normalized = max(normalized, fallback)  # 不低于 +5%
    return round(normalized, 2)


def _resolve_position_size(
    feature_snapshot: dict[str, Any],
    execution_policy: ExecutionPolicy = DEFAULT_EXECUTION_POLICY,
) -> float:
    """Resolve final size as the minimum of every applicable cap.

    Current v2 always uses ``MIN_ALL_APPLICABLE``.  The historical weak-regime
    exception is reachable only by explicitly supplying the registered v1
    policy for shadow/replay compatibility; display metadata cannot enable it.
    """
    position = execution_policy.position
    final_score = _as_float(feature_snapshot.get("final_score"))
    score_size = (
        position.high_score_cap
        if final_score >= position.high_score_threshold
        else position.default_fraction
    )

    discipline = feature_snapshot.get("market_discipline")
    market_regime = (
        str(discipline.get("market_regime") or "")
        if isinstance(discipline, dict)
        else ""
    )
    if (
        position.cap_composition
        is PositionCapComposition.LEGACY_WEAK_REGIME_BYPASS
        and market_regime.startswith(position.weak_regime_prefix)
    ):
        return round(score_size, 4)

    caps: dict[str, Any] = {
        "score_cap": score_size,
        **{
            name: getattr(position, name)
            for name in position._OPTIONAL_CAP_FIELDS
        },
    }

    def add_fraction(name: str, raw: Any, *, percent: bool = False) -> None:
        if raw is None or raw == "":
            return
        if percent and isinstance(raw, (int, float)) and not isinstance(raw, bool):
            caps[name] = float(raw) / 100.0
        else:
            caps[name] = raw

    if isinstance(discipline, dict):
        if position.market_regime_cap_enabled:
            add_fraction(
                "market_discipline_cap",
                discipline.get("max_position_pct"),
                percent=True,
            )
            add_fraction(
                "market_regime_cap",
                discipline.get("market_position_cap_pct"),
                percent=True,
            )
        add_fraction(
            "risk_template_cap",
            discipline.get("risk_template_cap_pct"),
            percent=True,
        )
    elif position.market_regime_cap_enabled:
        add_fraction(
            "market_discipline_cap",
            feature_snapshot.get("max_position_pct"),
            percent=True,
        )

    risk_template = str(feature_snapshot.get("risk_template") or "").lower()
    if risk_template:
        template_cap = {
            "conservative": 0.45,
            "balanced": 0.65,
            "aggressive": 0.80,
        }.get(risk_template)
        if template_cap is not None:
            caps["risk_template_declared_cap"] = template_cap

    for cap_name in position._OPTIONAL_CAP_FIELDS:
        add_fraction(
            f"runtime_{cap_name}",
            feature_snapshot.get(cap_name),
        )
        add_fraction(
            f"runtime_{cap_name}_pct",
            feature_snapshot.get(f"{cap_name}_pct"),
            percent=True,
        )

    for container_name in ("applicable_position_caps", "position_caps"):
        container = feature_snapshot.get(container_name)
        if not isinstance(container, dict):
            continue
        for raw_name, raw_value in container.items():
            cap_name = str(raw_name)
            add_fraction(
                f"{container_name}.{cap_name}",
                raw_value,
                percent=cap_name.endswith("_pct"),
            )

    decision = compose_position_caps(position.default_fraction, caps)
    return round(decision.approved_size, 4)


def _entry_execution_block_reason(code: str, bar: DailyKbar) -> str | None:
    return entry_execution_block_reason_from_bar(code, bar)


def _exit_execution_block_reason(code: str, bar: DailyKbar) -> str | None:
    return exit_execution_block_reason_from_bar(code, bar)


def _recommendation_execution_block_reason(feature_snapshot: dict[str, Any]) -> str | None:
    execution_viability = feature_snapshot.get("execution_viability")
    if not isinstance(execution_viability, dict):
        return None
    if str(execution_viability.get("status") or "").lower() != "blocked":
        return None
    return str(
        execution_viability.get("block_reason")
        or execution_viability.get("label")
        or "推荐时执行可成交性判定为不可成交"
    )


def _has_no_liquidity(bar: DailyKbar) -> bool:
    return no_liquidity_reason_from_bar(bar, side="entry") is not None


def _is_one_word_limit_down(code: str, bar: DailyKbar) -> bool:
    return is_one_word_limit_down_bar(code, bar)


def _build_outcome_reason(
    exit_reason: str,
    executed_return: float,
    close_return: float,
    *,
    max_return: float | None = None,
    max_drawdown: float | None = None,
) -> str:
    label = {
        "stop_loss_gap": "开盘跳空止损",
        "take_profit_gap": "开盘跳空止盈",
        "stop_loss_intraday_both_hit": "同日同时触发止盈止损，按保守止损处理",
        "stop_loss_hit": "盘中触发止损",
        "take_profit_hit": "盘中触发止盈",
        "trailing_stop_hit": "盘中触发移动保护",
        "trailing_stop_gap": "开盘跳空触发移动保护",
        "horizon_close": "持有到观察期结束",
        "horizon_incomplete": "观察期尚未走完，当前结果仅为临时标记",
        "horizon_close_t1_locked": "数据仅至入场日，结果待今日更新（买入当日受限，无可卖 bar）",
        "limit_down_locked_horizon_mark": "观察期内一字跌停或无流动性，按期末价格保守标记",
        "horizon_close_after_locked_limit_down": "出现一字跌停或无流动性后递延至观察期末",
    }.get(exit_reason, exit_reason)
    parts = [label, f"执行收益 {executed_return:.2f}%", f"静态收盘收益 {close_return:.2f}%"]
    if max_return is not None and max_return > 3:
        parts.append(f"持有期最高浮盈 {max_return:.1f}%")
    # 利润回吐标记：horizon_close 且峰值>=5% 但最终收益 < 峰值-3%
    # 诊断价值：识别 trailing stop 潜力交易（T2 第69轮发现 76 笔 horizon_close 峰值 7.76% 但收益仅 0.88%）
    if (
        exit_reason == "horizon_close"
        and max_return is not None
        and max_return >= 5.0
        and executed_return < max_return - 3.0
    ):
        giveback = max_return - executed_return
        parts.append(f"利润回吐 {giveback:.1f}%（trailing stop 潜力）")
    if max_drawdown is not None and max_drawdown < -3:
        parts.append(f"最大回撤 {max_drawdown:.1f}%")
    return "；".join(parts)
