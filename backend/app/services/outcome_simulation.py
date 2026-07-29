"""结果模拟引擎（任务 6.1）：给定推荐 + 该股票日K数据，模拟出场景（是否触发入场、
出场价格、退出原因、各类收益率），供 `outcome_service` 写入 `RecommendationOutcome`。

## 迁移说明
本文件是从 `outcome_service.py` 原地搬迁的**权威实现**（module extraction，不改变
任何函数逻辑/参数/返回值/默认值）：`_simulate_outcome`（核心模拟入口）及其依赖的全部
辅助函数——`_static_window_metrics` / `_resolve_entry` / `_resolve_exit` /
`_resolve_exit_base` / `_resolve_exit_with_trailing` / `_resolve_stop_loss_price` /
`_resolve_take_profit_price` / `_resolve_position_size` /
`_entry_execution_block_reason` / `_exit_execution_block_reason` /
`_recommendation_execution_block_reason` / `_entry_not_triggered_reason` /
`_has_no_liquidity` / `_is_one_word_limit_down` / `_build_outcome_reason` /
`_as_float` / `_loads`，以及这套模拟引擎的配置常量（`ENTRY_LOOKAHEAD_DAYS` /
`ENTRY_SLIPPAGE_BPS` / `EXIT_SLIPPAGE_BPS` / `SUCCESS_RETURN_PCT` /
`DEFAULT_STOP_LOSS_PCT` / `DEFAULT_TAKE_PROFIT_PCT` / `MAX_STOP_LOSS_PCT` /
`MAX_TAKE_PROFIT_PCT` / `STOP_LOSS_GAP_CAP_PCT` / `MAX_ENTRY_GAP_PCT` /
`TRAILING_STOP_ACTIVATE_PCT` / `TRAILING_STOP_CALLBACK_PCT` / `OUTCOME_*` 标签常量）。
`outcome_service.py` 改为从本模块 re-export 这些名字（保持
`from app.services.outcome_service import _simulate_outcome` 等既有导入路径不失效，
包括 `stock_backtest_service.py` / `scripts/exit_rules.py` /
`scripts/failure_attribution.py` / `scripts/verify_trailing_stop_params.py` /
`tests/test_outcome_execution_constraints.py` / `tests/test_failure_attribution.py`
等既有调用点），不再保留重复实现。

这些函数构成设计 §8 所称"结果模拟引擎"：**纯计算（in-process）**——只依赖传入的
`bars`/`dates`/`feature_snapshot`，不查询数据库，因此可脱离 DB 独立单测。真正涉及
数据库读写的调度层（`_apply_outcome_simulation` 写 DB、`label_pending_outcomes` /
`recompute_stale_outcomes` 查 DB + 调度模拟 + 写 DB + 加锁）继续留在
`outcome_service.py`，只是内部改为调用本模块的模拟函数——`outcome_service` 因此瘦成
"只读上报 + 调度" 层，模拟逻辑收口到这里。

佣金与印花税口径由 `app.engine.trading_costs` 统一提供，模拟和指标共享同一纯函数。
"""
from __future__ import annotations

import json
from bisect import bisect_left
from datetime import time, timedelta
from typing import Any

from app.engine.board_rules import is_one_word_limit_down_bar, limit_pct_for_code
from app.engine.trading_costs import is_net_profitable
from app.models.tables import DailyKbar, Recommendation
from app.services.entry_policy import (
    CAUSAL_STRATEGY_ENTRY,
    EntryPolicy,
    LEGACY_MIXED_ENTRY,
    NEXT_TRADING_DAY_OPEN,
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
ENTRY_LOOKAHEAD_DAYS = 2
ENTRY_SLIPPAGE_BPS = 10.0
EXIT_SLIPPAGE_BPS = 10.0
SUCCESS_RETURN_PCT = 3.0
DEFAULT_STOP_LOSS_PCT = -5.5  # 回测：止损 -5.5% 盈亏比 1.25→1.64，胜率 54.3%→57.0%
DEFAULT_TAKE_PROFIT_PCT = 5.0  # 回测：horizon_close 65/92 峰值不达 6.5%，TP=5.0% 模拟 WR 57.2%→71.7%（+14.5pp, 22 saved）
                                 # avg peak 4.27%，33/65 峰值在 3-5%，TP 下修捕获更多止盈窗口
MAX_STOP_LOSS_PCT = -5.7  # 止损最宽 -5.7%（与 DEFAULT -5.5% 几乎重合，仅留 0.2pp 缓冲）
                                 # 第164轮 D 项修复：原 -9% 在实践中只对"高开入场"场景生效
                                 # （正常入场止损都在 -5.7% 以内），且让该场景亏损从 -5.7% 放大到 -9%
                                 # 反事实验证：184 formal 样本仅长裕集团(entry=85>>rec_price 79.32 高开
                                 # 接力)1 票受影响，min -9.18%→-5.79%(达标基线-5.8%)，盈亏比 1.44→1.49，0 误伤
                                 # 因果：高开入场已透支上涨空间，止损不该比正常入场更宽；-9% 反向放大尾部风险
MAX_TAKE_PROFIT_PCT = 15.0     # 止盈上限（+15%），防止 feature_builder 设置过高的止盈
STOP_LOSS_GAP_CAP_PCT = -15.0  # 跳空止损最大亏损上限：open 远低于止损价时，模拟"隔夜止损单"的极限（科创板 20cm 跌停场景）
MAX_ENTRY_GAP_PCT = 10.0       # 跳空高开 > 10% 则放弃入场（历史数据：gap>10% 胜率 0%）

# 历史 callback trailing stop：保留给 `_resolve_exit_legacy_noncausal` 做有偏上界对照，
# 不再作为生产默认退出。以下历史结论不能替代 2026-07-27 利润地板的因果验证。
# 回测验证（235 笔 formal 样本，min 时序口径，扣费口径现统一为 0.07%/笔）：
#   胜率 49.4%→66.8%、盈亏比 1.38→1.89、总收益 +232→+685、最大亏损 -15.19%→-7.17%
# 时间分割验证（防过拟合）：前半 盈亏比 1.97→2.40，后半（样本外）1.51→1.67，两半均正向
# 网格搜索结论：activate=2.0 主导（更早锁利避开回吐），callback=0.8 次要；高 activate(4+/5+) 有跳空尾部风险
# 失败归因闭环（第 94 轮）：248 笔 trailing 失败中 22 笔大亏(<-3%)，集中在半导体/通信设备板块
#   反事实验证：半导体差异化 callback(1.5/2.0) 无改善（盈亏比 1.84→1.83→1.81）
#   根因：板块系统性跳空低开，参数调整无法改善，min(open,触发价) 已是保守口径
#   结论：trailing 大亏是市场风险，不要盲目按行业差异化 callback——会破坏整体盈亏比
# 关键约束：trailing 优先级低于 take_profit，故不破坏原有 TP 路径（take_profit_hit 完整保留）
# 时序口径：触发价取 min(open, 触发价)——跳空低开时以 open 成交（更差），max 会虚假高估出场价
TRAILING_STOP_ACTIVATE_PCT = 2.0   # high 达 entry +2% 激活（原 3.0；2.0 让更多交易在亏损前以小赚离场）
TRAILING_STOP_CALLBACK_PCT = 0.8   # 自峰值 high 回撤 0.8% 触发退出（原 1.5；更紧锁利，loss 3.67→3.13）
TRAILING_STOP_MIN_LOCK_PCT = 0.3   # 利润地板最低锁定 +0.3%，覆盖滑点和交易成本后仍保留小幅净利润
TRAILING_STOP_PROFIT_LOCK_RATIO = 0.50  # 生产动态退出锁住前一日最高浮盈的 50%，其余空间留给正常波动

OUTCOME_PENDING = "pending"
OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_MISSED = "missed"
# 临时态：入场后尚无任何可卖 bar（holding_bars 仅入场日 1 根），被迫按入场日收盘临时标记。
# 数据仅至入场日，结果待下一交易日更新。不计入 EXECUTED_OUTCOME_LABELS（不算已结算），
# 故所有 IN ('success','failed') 的胜率/收益统计自动排除，避免中间态污染成败口径。
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
    entry_policy: EntryPolicy = CAUSAL_STRATEGY_ENTRY,
) -> dict[str, Any] | None:
    """模拟单笔推荐 outcome。

    exit_resolver 默认 None → 用生产单路径因果利润地板 `_resolve_exit`。
    传入自定义解析器（签名同 _resolve_exit：holding_bars, stop_loss, take_profit,
    *, code, entry_price）可替换退出逻辑，用于动态退出状态机反事实验证。
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
    static_metrics = _static_window_metrics(rec, signal_bar, future_bars, horizon_days)

    recommendation_block_reason = _recommendation_execution_block_reason(feature_snapshot)
    if recommendation_block_reason:
        return {
            **static_metrics,
            "entry_policy_id": entry_policy.policy_id,
            "entry_triggered": False,
            "entry_date": None,
            "entry_price": None,
            "exit_date": None,
            "exit_price": None,
            "executed_return": 0.0,
            "weighted_return": 0.0,
            "position_size": _resolve_position_size(feature_snapshot),
            "holding_days": 0,
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
        entry_policy=entry_policy,
    )
    position_size = _resolve_position_size(feature_snapshot)

    if not entry_result.get("triggered"):
        return {
            **static_metrics,
            "entry_policy_id": entry_policy.policy_id,
            "entry_triggered": False,
            "entry_date": None,
            "entry_price": None,
            "exit_date": None,
            "exit_price": None,
            "executed_return": 0.0,
            "weighted_return": 0.0,
            "position_size": position_size,
            "holding_days": 0,
            "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
            "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
            "stop_loss_price": _resolve_stop_loss_price(None, feature_snapshot),
            "take_profit_price": _resolve_take_profit_price(None, feature_snapshot),
            "exit_reason": "not_triggered",
            "hit_target": False,
            "hit_invalid": False,
            "outcome_label": OUTCOME_MISSED,
            "outcome_label_legacy": OUTCOME_MISSED,
            "outcome_reason": f"{entry_result.get('reason') or '未触发买点，未建仓'}；静态窗口结果仅作旁路参考",
        }

    raw_entry_price = float(entry_result["price"])
    entry_date = str(entry_result["date"])

    # 跳空高开过滤：gap > MAX_ENTRY_GAP_PCT 则放弃入场
    # 历史数据表明 gap > 10% 的交易胜率为 0%（12/12 全部失败，平均亏损 -4.5%）
    recommend_price = _as_float(rec.recommend_price) or 0
    if recommend_price > 0 and raw_entry_price > 0:
        gap_pct = (raw_entry_price - recommend_price) / recommend_price * 100
        if gap_pct > MAX_ENTRY_GAP_PCT:
            return {
                **static_metrics,
                "entry_policy_id": entry_policy.policy_id,
                "entry_triggered": False,
                "entry_date": None,
                "entry_price": None,
                "exit_date": None,
                "exit_price": None,
                "executed_return": 0.0,
                "weighted_return": 0.0,
                "position_size": _resolve_position_size(feature_snapshot),
                "holding_days": 0,
                "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
                "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
                "stop_loss_price": _resolve_stop_loss_price(None, feature_snapshot),
                "take_profit_price": _resolve_take_profit_price(None, feature_snapshot),
                "exit_reason": "gap_up_blocked",
                "hit_target": False,
                "hit_invalid": False,
                "outcome_label": OUTCOME_MISSED,
                "outcome_label_legacy": OUTCOME_MISSED,
                "outcome_reason": f"跳空高开 {gap_pct:.1f}% > {MAX_ENTRY_GAP_PCT:.0f}% 阈值，放弃入场",
            }

    executed_entry_price = round(raw_entry_price * (1 + ENTRY_SLIPPAGE_BPS / 10000.0), 4)
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

    resolver = exit_resolver if exit_resolver is not None else _resolve_exit
    exit_price, exit_date, exit_reason = resolver(
        holding_bars,
        stop_loss_price,
        take_profit_price,
        code=str(rec.code),
        entry_price=executed_entry_price,
    )
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
    hit_invalid = exit_reason.startswith("stop_loss")
    hit_target = exit_reason.startswith("take_profit") or executed_return >= SUCCESS_RETURN_PCT
    # 临时态：holding_bars 仅入场日 1 根（horizon_close_t1_locked），入场后尚无可卖 bar，
    # 当前 executed_return 只是入场日收盘临时标记，下一交易日数据到位后会重新结算。
    # 不进 success/failed 口径，避免中间态被计入胜负统计或前端误读为最终结论。
    is_provisional = exit_reason == "horizon_close_t1_locked"
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
        "entry_policy_id": entry_policy.policy_id,
        "entry_triggered": True,
        "entry_date": entry_date,
        "entry_price": round(executed_entry_price, 2),
        "exit_date": exit_date,
        "exit_price": round(executed_exit_price, 2),
        "executed_return": executed_return,
        "weighted_return": weighted_return,
        "position_size": position_size,
        "holding_days": len(holding_bars),
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
    entry_policy: EntryPolicy = CAUSAL_STRATEGY_ENTRY,
) -> dict[str, Any]:
    """解析入场日与入场价。

    推荐时点决定入场候选窗口：
    - 盘中推荐（_is_intraday_recommendation）：直接使用生成推荐时保存的实时推荐价；
      全天 signal_bar 的 open/low 可能早于推荐时点，不能作为成交依据。
    - 盘后推荐：signal_bar 不作为候选（盘后只能 T+1 买入），维持 future_bars[:2] 原窗口。

    - 盘后推荐：目标买点（ideal_buy/secondary_buy 的 low 触及）> 次日开盘市价回退。
    """
    if entry_policy == NEXT_TRADING_DAY_OPEN:
        if not future_bars:
            return {"triggered": False, "reason": "下一交易日行情缺失，无法确认成交"}
        bar = future_bars[0]
        block_reason = _entry_execution_block_reason(str(rec.code), bar)
        if block_reason:
            return {"triggered": False, "reason": block_reason}
        raw_entry_price = _as_float(bar.open)
        if raw_entry_price <= 0:
            return {"triggered": False, "reason": "下一交易日开盘价无效，无法真实成交"}
        return {
            "triggered": True,
            "bar_index": 0,
            "price": raw_entry_price,
            "date": str(bar.trade_date),
            "reason": "next_trading_day_open_entry",
        }

    intraday = _is_intraday_recommendation(rec)
    if intraday and entry_policy in {CAUSAL_STRATEGY_ENTRY, LEGACY_MIXED_ENTRY}:
        recommend_price = _as_float(getattr(rec, "recommend_price", None))
        if recommend_price <= 0:
            return {"triggered": False, "reason": "盘中推荐价无效，无法确认成交"}
        return {
            "triggered": True,
            "bar_index": 0,
            "price": recommend_price,
            "date": str(rec.trade_date),
            "reason": "intraday_recommend_price_entry",
        }
    entry_targets = [
        value for value in (
            _as_float(feature_snapshot.get("ideal_buy")),
            _as_float(feature_snapshot.get("secondary_buy")),
        )
        if value > 0
    ]
    lookahead = max(1, min(entry_policy.max_entry_trade_days, horizon_days, len(future_bars)))
    # 盘中推荐：signal_bar 作首候选（T 当天），后接 future_bars 前 lookahead 根
    if intraday and signal_bar is not None:
        entry_window = [signal_bar] + future_bars[:lookahead]
    else:
        entry_window = future_bars[:lookahead]
    blocked_reasons: list[str] = []
    for bar_index, bar in enumerate(entry_window):
        low = _as_float(bar.low)
        bar_open = _as_float(bar.open)
        for target in entry_targets:
            if low <= target:
                block_reason = _entry_execution_block_reason(str(rec.code), bar)
                if block_reason:
                    blocked_reasons.append(block_reason)
                    continue
                return {
                    "triggered": True,
                    "bar_index": bar_index,
                    "price": min(bar_open, target) if bar_open > 0 else target,
                    "date": str(bar.trade_date),
                    "reason": "buy_target_touched",
                }

    if entry_targets:
        if not entry_policy.fallback_to_open_when_targets_miss:
            return {
                "triggered": False,
                "reason": _entry_not_triggered_reason(blocked_reasons, has_targets=True),
            }
        # 买点未在窗口内命中 → 回退到次日开盘市价入场，而不是直接放弃
        for bar_index, bar in enumerate(entry_window):
            block_reason = _entry_execution_block_reason(str(rec.code), bar)
            if block_reason:
                blocked_reasons.append(block_reason)
                continue
            raw_entry_price = _as_float(bar.open) or _as_float(bar.close)
            if raw_entry_price <= 0:
                blocked_reasons.append("入场日价格无效，无法真实成交")
                continue
            return {
                "triggered": True,
                "bar_index": bar_index,
                "price": raw_entry_price,
                "date": str(bar.trade_date),
                "reason": "next_open_fallback_entry（买点未命中，市价入场）",
            }
        return {
            "triggered": False,
            "reason": _entry_not_triggered_reason(blocked_reasons, has_targets=True),
        }

    for bar_index, bar in enumerate(entry_window):
        block_reason = _entry_execution_block_reason(str(rec.code), bar)
        if block_reason:
            blocked_reasons.append(block_reason)
            continue
        raw_entry_price = _as_float(bar.open) or _as_float(bar.close)
        if raw_entry_price <= 0:
            blocked_reasons.append("入场日价格无效，无法真实成交")
            continue
        return {
            "triggered": True,
            "bar_index": bar_index,
            "price": raw_entry_price,
            "date": str(bar.trade_date),
            "reason": "next_open_entry",
        }
    return {
        "triggered": False,
        "reason": _entry_not_triggered_reason(blocked_reasons, has_targets=False),
    }


def _resolve_exit_legacy_noncausal(
    holding_bars: list[DailyKbar],
    stop_loss_price: float,
    take_profit_price: float,
    *,
    code: str = "",
    entry_price: float = 0.0,
) -> tuple[float, str, str]:
    """历史两轮退出逻辑，仅用于有偏上界对照，不得作为生产默认解析器。

    A 股 T+1 交收：买入当日不能卖出。holding_bars[0] 是入场日，**当天不触发任何离场**
    （含开盘跳空止损、盘中 high/low 触及止盈止损），从 holding_bars[1:]（入场日次日起）起扫。
    若仅 1 根 bar（入场日即 horizon 末），强制 horizon_close——持仓但未到可卖日，按入场日收盘标记。

    两轮扫描（在 holding_bars[1:] 上）：
      1. 第一轮用原 stop_loss/take_profit 逻辑找出基准 exit；
         若基准是 take_profit_*（含 both_hit），直接返回——trailing 不抢跑 TP 路径。
      2. 若基准是 stop_loss_*/horizon_close（即 holding 期内未触及 TP），
         第二轮应用 trailing stop 重模拟：high 达 entry+2% 激活，自峰值回撤 0.8% 锁利。

    时序口径：触发价取 min(open, 触发价)，跳空低开时以 open 成交（max 会虚假高估）。
    """
    # T+1 约束：入场日 holding_bars[0] 不参与离场，从次日起扫
    if len(holding_bars) <= 1:
        last = holding_bars[-1]
        return _as_float(last.close or 0), str(last.trade_date), "horizon_close_t1_locked"
    scan_bars = holding_bars[1:]

    # 第一轮：基准 exit（原 stop_loss/take_profit 逻辑）
    base_exit = _resolve_exit_base(scan_bars, stop_loss_price, take_profit_price,
                                    code=code, entry_price=entry_price)
    base_reason = base_exit[2]
    # take_profit 路径不应用 trailing（避免抢跑 TP，回测显示抢跑导致 -1897% 损失）
    if base_reason.startswith("take_profit"):
        return base_exit

    # 第二轮：基准是 stop_loss_*/horizon_close，应用条件 trailing stop
    trailing_exit = _resolve_exit_with_trailing(
        scan_bars, stop_loss_price, take_profit_price,
        code=code, entry_price=entry_price,
    )
    return trailing_exit


def _resolve_exit_base(
    holding_bars: list[DailyKbar],
    stop_loss_price: float,
    take_profit_price: float,
    *,
    code: str = "",
    entry_price: float = 0.0,
) -> tuple[float, str, str]:
    """原 stop_loss/take_profit 退出逻辑（无 trailing）。"""
    locked_limit_down_bars = 0
    for bar in holding_bars:
        if _exit_execution_block_reason(code, bar):
            locked_limit_down_bars += 1
            continue
        bar_open = _as_float(bar.open)
        high = _as_float(bar.high)
        low = _as_float(bar.low)
        if bar_open > 0 and low > 0:
            if bar_open <= stop_loss_price:
                if entry_price > 0:
                    min_exit_price = entry_price * (1 + STOP_LOSS_GAP_CAP_PCT / 100.0)
                    gap_exit = max(bar_open, min_exit_price)
                else:
                    gap_exit = bar_open
                return gap_exit, str(bar.trade_date), "stop_loss_gap"
            if bar_open >= take_profit_price:
                return bar_open, str(bar.trade_date), "take_profit_gap"
        if low <= stop_loss_price and high >= take_profit_price:
            return stop_loss_price, str(bar.trade_date), "stop_loss_intraday_both_hit"
        if low <= stop_loss_price:
            return stop_loss_price, str(bar.trade_date), "stop_loss_hit"
        if high >= take_profit_price:
            return take_profit_price, str(bar.trade_date), "take_profit_hit"

    last_bar = holding_bars[-1]
    if locked_limit_down_bars and locked_limit_down_bars == len(holding_bars):
        return _as_float(last_bar.close), str(last_bar.trade_date), "limit_down_locked_horizon_mark"
    if locked_limit_down_bars:
        return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close_after_locked_limit_down"
    return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close"


def _resolve_exit_with_trailing(
    holding_bars: list[DailyKbar],
    stop_loss_price: float,
    take_profit_price: float,
    *,
    code: str = "",
    entry_price: float = 0.0,
) -> tuple[float, str, str]:
    """应用条件 trailing stop 重模拟。

    调用前已确认基准 exit 非 take_profit_*，故 holding 期内 high 从未达 take_profit_price。
    trailing 在 high≥entry+2% 激活，自峰值 high 回撤 0.8% 锁利。
    时序口径分同 bar / 跨 bar 触发（见触发处注释）：同 bar 触发取触发价（high 先达 hwm
    是激活前提，时序确定）；跨 bar 触发取 min(open, 触发价)（可能跳空低开透单，保守）。
    """
    locked_limit_down_bars = 0
    hwm = 0.0
    trailing_activated = False
    for bar in holding_bars:
        if _exit_execution_block_reason(code, bar):
            locked_limit_down_bars += 1
            continue
        bar_open = _as_float(bar.open)
        high = _as_float(bar.high)
        low = _as_float(bar.low)
        if bar_open > 0 and low > 0:
            # 跳空止损仍按原逻辑（cap 极端亏损）
            if bar_open <= stop_loss_price:
                if entry_price > 0:
                    min_exit_price = entry_price * (1 + STOP_LOSS_GAP_CAP_PCT / 100.0)
                    gap_exit = max(bar_open, min_exit_price)
                else:
                    gap_exit = bar_open
                return gap_exit, str(bar.trade_date), "stop_loss_gap"
        # 更新 trailing hwm；记录 trailing 是否在当前 bar 刚激活（区分同 bar 触发 vs 跨 bar 触发）
        activated_this_bar = False
        if entry_price > 0 and high > 0:
            bar_high_ret = (high / entry_price - 1) * 100
            if bar_high_ret > hwm:
                hwm = bar_high_ret
            if not trailing_activated and hwm >= TRAILING_STOP_ACTIVATE_PCT:
                trailing_activated = True
                activated_this_bar = True
        # 固定止损仍优先于已激活 trailing（low 触及 stop_loss 即返回 stop_loss_hit）。
        # 护栏（第 162 轮反事实验证）：同 bar 内 high 冲高激活 trailing 后 low 触止损，日线
        # 时序无法确定 high/low 谁先到。让 trailing 优先的变体在 191 笔样本上虚假 +11pp 胜率，
        # 但 21 笔改善全来自同 bar 乐观假设（high 先到）——跨 bar 保守口径下 0 笔改善，纯过拟合。
        # 故 stop_loss 优先是保守口径，禁止改 trailing 优先（除非接入分钟级时序数据）。
        # 见 scripts/verify_trailing_priority.py 与 test_resolve_exit_same_bar_high_surge_then_stop_loss_uses_stop_loss。
        if low > 0 and low <= stop_loss_price:
            return stop_loss_price, str(bar.trade_date), "stop_loss_hit"
        # 条件 trailing 触发
        if trailing_activated and entry_price > 0 and low > 0:
            trailing_stop_ret = hwm - TRAILING_STOP_CALLBACK_PCT
            trailing_stop_price = entry_price * (1 + trailing_stop_ret / 100.0)
            if low <= trailing_stop_price:
                # 时序口径分两种情况（因果必然性，不是保守估计）：
                # - 同 bar 触发（trailing 在本 bar 刚激活）：激活前提是 high 先达 hwm，
                #   故时序上 high 必先于 low，价格路径是 high 见顶后回落到触发价成交。
                #   成交价 = 触发价（trailing limit 的本意）。min(open,触发价) 会把平开冲高
                #   回落（open≈entry < 触发价）误判成跳空，以 open 出场，把盈利单变微亏（D 项 28 笔 -0.20% 假失败根因）。
                # - 跨 bar 触发（trailing 之前 bar 已激活，本 bar 才触发）：本 bar 开盘可能跳空低开
                #   穿透触发价，以 open 成交（更差）。维持 min(open, 触发价) 保守口径。
                if activated_this_bar:
                    exit_px = trailing_stop_price
                else:
                    exit_px = min(bar_open, trailing_stop_price) if bar_open > 0 else trailing_stop_price
                return exit_px, str(bar.trade_date), "trailing_stop_hit"

    last_bar = holding_bars[-1]
    if locked_limit_down_bars and locked_limit_down_bars == len(holding_bars):
        return _as_float(last_bar.close), str(last_bar.trade_date), "limit_down_locked_horizon_mark"
    if locked_limit_down_bars:
        return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close_after_locked_limit_down"
    return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close"


def _trailing_stop_return(
    observed_high_return: float,
    trailing_policy: str,
    profit_lock_ratio: float,
) -> float:
    if trailing_policy == "profit_floor":
        return max(TRAILING_STOP_MIN_LOCK_PCT, observed_high_return * profit_lock_ratio)
    return observed_high_return - TRAILING_STOP_CALLBACK_PCT


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
) -> tuple[float, str, str]:
    """单路径因果退出函数（动态退出状态机 MVP）。

    与历史 `_resolve_exit_legacy_noncausal` 的关键差异：trailing 保护价严格按交易日推进，
    **只用截至上一交易日收盘已知的信息**生成下一交易日生效的保护价，当日新形成的最高价
    只能更新下一交易日的保护价，不得反过来触发当日退出（日 K 无法证明当日 high/low 先后顺序）。

    holding_bars[0] 是入场日，当天禁止退出（T+1）。入场日 close 用于初始化次日计划：
    - entry_close_return = (entry_close / entry_price - 1) * 100
    - 若 ≥ TRAILING_STOP_ACTIVATE_PCT(2.0%)，从次日起激活移动保护
    - `peak_callback`：次日保护收益 = 最高浮盈 - callback
    - `profit_floor`：次日保护收益 = max(最低锁利, 最高浮盈 × profit_lock_ratio)
    - 保护价不得低于 hard_stop，也不得低于上一版保护价（单调上移）

    对 holding_bars[1:] 逐日按 Spec §4 的 10 条顺序判断；当 5、6（low 触止损）与 7、8
    （low/high 触 trailing/tp）同日并存时，按 Spec §4.5/§4.6/§4.7 的保守顺序：固定止损 >
    已确定的移动保护价 > 固定止盈。窗口结束未退出按最后一日收盘。

    dynamic_trailing=False 时退化为纯单路径固定止盈止损（与 _resolve_exit_base 在
    holding_bars[1:] 上的语义一致），用于反事实验证的因果基线。
    """
    if dynamic_trailing and trailing_policy not in {"peak_callback", "profit_floor"}:
        raise ValueError(f"unknown trailing_policy: {trailing_policy}")
    if dynamic_trailing and trailing_policy == "profit_floor" and not 0 < profit_lock_ratio <= 1:
        raise ValueError(f"profit_lock_ratio must be in (0, 1], got {profit_lock_ratio}")

    if not holding_bars:
        return 0.0, "", "horizon_close"
    if len(holding_bars) <= 1:
        # 入场日即 horizon 末：T+1 锁仓，按入场日收盘标记
        last = holding_bars[-1]
        return _as_float(last.close or 0), str(last.trade_date), "horizon_close_t1_locked"

    entry_bar = holding_bars[0]
    scan_bars = holding_bars[1:]
    hard_stop = stop_loss_price
    take_profit = take_profit_price

    # ── 入场日收盘初始化次日计划（因果：只用 entry_bar.close，不用 high）──
    trailing_active = False
    observed_high_return = 0.0  # 截至上一交易日收盘已知的最高浮盈（%）
    next_trailing_stop = 0.0    # 下一交易日生效的移动保护价
    if dynamic_trailing and entry_price > 0:
        entry_close = _as_float(entry_bar.close)
        if entry_close > 0:
            entry_close_return = (entry_close / entry_price - 1) * 100
            observed_high_return = max(0.0, entry_close_return)
            if entry_close_return >= TRAILING_STOP_ACTIVATE_PCT:
                trailing_active = True
                stop_return = _trailing_stop_return(entry_close_return, trailing_policy, profit_lock_ratio)
                prot = entry_price * (1 + stop_return / 100.0)
                prot = max(prot, hard_stop)
                next_trailing_stop = prot

    locked_limit_down_bars = 0
    for bar in scan_bars:
        sellable = _exit_execution_block_reason(code, bar) is None
        if not sellable:
            locked_limit_down_bars += 1
        bar_open = _as_float(bar.open)
        high = _as_float(bar.high)
        low = _as_float(bar.low)
        has_ohlc = bar_open > 0 and low > 0 and high > 0

        if sellable and has_ohlc:
            # 2. 开盘不高于固定止损 → stop_loss_gap（隔夜止损单口径）
            if bar_open <= hard_stop:
                if entry_price > 0:
                    min_exit_price = entry_price * (1 + STOP_LOSS_GAP_CAP_PCT / 100.0)
                    gap_exit = max(bar_open, min_exit_price)
                else:
                    gap_exit = bar_open
                return gap_exit, str(bar.trade_date), "stop_loss_gap"
            # 3. 已激活移动保护且开盘不高于保护价 → trailing_stop_gap
            if (
                dynamic_trailing
                and trailing_active
                and next_trailing_stop > 0
                and bar_open <= next_trailing_stop
            ):
                return bar_open, str(bar.trade_date), "trailing_stop_gap"
            # 4. 开盘不低于固定止盈 → take_profit_gap
            if bar_open >= take_profit:
                return bar_open, str(bar.trade_date), "take_profit_gap"
            # 5. 同日 low 触止损且 high 触止盈 → 保守按止损
            if low <= hard_stop and high >= take_profit:
                return hard_stop, str(bar.trade_date), "stop_loss_intraday_both_hit"
            # 6. low 触及固定止损 → stop_loss_hit
            if low <= hard_stop:
                return hard_stop, str(bar.trade_date), "stop_loss_hit"
            # 7. 已存在上一日确定的移动保护价，且 low 触及 → 按保护价退出
            if (
                dynamic_trailing
                and trailing_active
                and next_trailing_stop > 0
                and low <= next_trailing_stop
            ):
                return next_trailing_stop, str(bar.trade_date), "trailing_stop_hit"
            # 8. high 触及固定止盈 → take_profit_hit
            if high >= take_profit:
                return take_profit, str(bar.trade_date), "take_profit_hit"

        # 9. 当日未退出（含不可卖日），收盘后用当日 high 更新下一交易日的计划。
        #    因果关键：当日 high 只更新 next_trailing_stop，绝不反过来触发当日退出。
        if dynamic_trailing and entry_price > 0 and high > 0:
            bar_high_return = (high / entry_price - 1) * 100
            if bar_high_return > observed_high_return:
                observed_high_return = bar_high_return
            if not trailing_active and observed_high_return >= TRAILING_STOP_ACTIVATE_PCT:
                trailing_active = True
            if trailing_active:
                stop_return = _trailing_stop_return(observed_high_return, trailing_policy, profit_lock_ratio)
                prot = entry_price * (1 + stop_return / 100.0)
                prot = max(prot, hard_stop)
                # 保护价单调上移：不得低于上一版
                if prot > next_trailing_stop:
                    next_trailing_stop = prot

    # 10. 持有窗口结束仍未退出 → 最后一日收盘
    last_bar = holding_bars[-1]
    if locked_limit_down_bars and locked_limit_down_bars == len(scan_bars):
        return _as_float(last_bar.close), str(last_bar.trade_date), "limit_down_locked_horizon_mark"
    if locked_limit_down_bars:
        return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close_after_locked_limit_down"
    return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close"


def _resolve_exit(
    holding_bars: list[DailyKbar],
    stop_loss_price: float,
    take_profit_price: float,
    *,
    code: str = "",
    entry_price: float = 0.0,
) -> tuple[float, str, str]:
    """生产退出：单路径因果状态机 + 50% 利润地板。

    保护价只使用上一交易日结束时已知的最高浮盈，并从下一交易日起生效；
    不再根据完整持有期是否最终触及固定止盈来事后选择退出路径。
    """
    return _resolve_exit_causal(
        holding_bars,
        stop_loss_price,
        take_profit_price,
        code=code,
        entry_price=entry_price,
        dynamic_trailing=True,
        trailing_policy="profit_floor",
        profit_lock_ratio=TRAILING_STOP_PROFIT_LOCK_RATIO,
    )


def _resolve_stop_loss_price(raw_entry_price: float | None, feature_snapshot: dict[str, Any]) -> float:
    """解析止损价，加入归一化逻辑防止过紧的止损。

    止损价必须在 [min_stop, max_stop] 范围内：
    - max_stop = entry * (1 + DEFAULT_STOP_LOSS_PCT/100) = 最紧 -5.5%
    - min_stop = entry * (1 + MAX_STOP_LOSS_PCT/100) = 最宽 -5.7%
    """
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
    """解析止盈价，与 _resolve_stop_loss_price 对称：实际入场价偏离推荐价时按比例重算。

    设计止盈比例隐含在推荐时（take_profit / recommend_price − 1）。次日实际入场价因跳空
    偏离推荐价时，旧实现只做上限裁剪、不按比例重算，导致跳空低开后设计的 +5% 止盈目标被
    相对抬高（如 603991 推荐价 168.19、入场价 154.21，止盈 176.60 相对入场变 +14.5% 而非 +5%），
    固定止盈路径失效 → 全推给 trailing 兜底、且被错标 success。
    修复：用隐含比例重算 entry × (1 + implied_tp_pct)，再 clamp 到 [entry×1.05, entry×1.15]。
    """
    raw_target = _as_float(feature_snapshot.get("take_profit"))
    if raw_entry_price is None or raw_entry_price <= 0:
        return round(raw_target, 2) if raw_target > 0 else 0.0
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


def _resolve_position_size(feature_snapshot: dict[str, Any]) -> float:
    discipline = feature_snapshot.get("market_discipline")
    if isinstance(discipline, dict):
        max_position_pct = _as_float(discipline.get("max_position_pct"))
        if max_position_pct > 0:
            return round(min(max_position_pct / 100.0, 1.0), 4)
        label = str(discipline.get("position_label") or "")
    else:
        max_position_pct = _as_float(feature_snapshot.get("max_position_pct"))
        if max_position_pct > 0:
            return round(min(max_position_pct / 100.0, 1.0), 4)
        label = str(feature_snapshot.get("position_label") or "")
    if "大仓" in label:
        return 0.75
    if "中仓" in label:
        return 0.55
    if "小仓" in label:
        return 0.35
    if "观察" in label:
        return 0.15
    return 1.0


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


def _entry_not_triggered_reason(blocked_reasons: list[str], *, has_targets: bool) -> str:
    if blocked_reasons:
        unique = list(dict.fromkeys(blocked_reasons))
        return "；".join(unique[:2])
    return "未触发目标买点，未建仓" if has_targets else "观察窗口内无可执行入场日"


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
