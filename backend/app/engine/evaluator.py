import ast
import logging
from typing import Any

logger = logging.getLogger(__name__)


class UnsafeExpression(ValueError):
    pass


# 策略 DSL 表达式的合法字段白名单：``_eval`` 只允许引用本集合内的字段名。
#
# 任务 2.2 起，因子框架声明的 ``output_fields`` 会由
# ``app.services.factors.inject_factor_output_fields``（经 ``bootstrap_factor_allowlist``
# 在因子注册完成后调用）**自动并入**本集合——新增因子无需再手工来此登记字段。
# 注入为就地并集（只增不删、幂等），故不会影响下方已有条目。
#
# 下方硬编码条目暂时保留：现有 6 因子要到任务 2.3 才迁入因子框架，届时这些字段
# 将由注入机制提供，可清理与之重复的硬编码项；在迁移前删除会打断策略对这些字段
# 名的引用（变成谁都不提供的空窗），故清理留到 2.3 之后再做。
ALLOWED_NAMES = {
    # L-187：策略 yaml 已引用但此前不在白名单 → UnsafeExpression 被吞掉、条件恒为 False（静默失效）。
    "pledge_ratio",
    "price",
    "current_price",
    "yesterday_high",
    "yesterday_low",
    "dtr_data_ready",
    "dtr_event_count",
    "dtr_limit_count",
    "dtr_event_age",
    "dtr_volume_contraction",
    "dtr_range_pct",
    "dtr_support_ratio",
    "dtr_event_return_pct",
    "dtr_restart_volume",
    "dtr_close_location",
    "dtr_near_range_high",
    "amount_yi",
    "announcement_negative_count",
    "avg_cost",
    "bias_ma5",
    "bias_ma10",
    "bias_ma20",
    "bias_ma60",
    "breakout20",
    "breakout60",
    "capital_flow_score",
    "chg20",
    "chg60",
    "chip_concentration",
    "chip_health_score",
    "capital_resonance_score",
    "capital_resonance_signal",
    "cost_ratio",
    "dragon_tiger_recent_count",
    "dragon_tiger_is_on_list",
    "dragon_tiger_buy_dispersion",
    "dragon_tiger_inst_buy_ratio",
    "dragon_tiger_net_buy_ratio",
    "dragon_tiger_net_amount_yi",
    "dragon_tiger_institution_net_buy_yi",
    "hsgt_net_buy",
    "hsgt_rank",
    "volume_ratio_vs_20d",
    "dist20",
    "dist60",
    "dsa_bias_score",
    "dsa_macd_score",
    "dsa_rsi_score",
    "dsa_signal_score",
    "dsa_support_score",
    "dsa_trend_score",
    "dsa_volume_score",
    "eff20",
    "data_completeness_score",
    "evidence_quality_score",
    "fundamental_score",
    "free_mv_yi",
    "gross_margin",
    "industry_capacity_like_count",
    "industry_positive_excess20_count",
    "inflow_5d_yi",
    "inflow_10d_yi",
    "intelligence_score",
    "is_20cm_stock",
    "is_limit_up",
    "is_main_theme",
    "is_shortline_leader_candidate",
    "limit_up_consecutive_days",
    "limit_up_count",
    "limit_down_count",
    "limit_up_days20",
    "limit_up_fd_amount_yi",
    "limit_up_overheat_level",
    "limit_up_overheat_score",
    "limit_up_open_times",
    "limit_up_turnover_ratio",
    "market_sentiment_phase",
    "market_regime",
    "market_sample_count",
    "is_st",
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "main_net_inflow_yi",
    "main_net_inflow_ratio",
    "max_dd20",
    "max_dd60",
    "net_profit_yoy",
    "news_risk_count",
    "off_high60_pct",
    "off_high_pct",
    "pct",
    "pe_ttm",
    "positive_catalyst_count",
    "pos20",
    "pos60",
    "pos120",
    "profit_ratio",
    "primary_excess20",
    "primary_excess60",
    "resistance_distance_pct",
    "revenue_yoy",
    "roe",
    "rsi_6",
    "rsi_12",
    "rsi_24",
    "sector_fund_rank",
    "sector_ladder_completeness_score",
    "sector_ladder_height",
    "sector_ladder_level",
    "sector_ladder_limit_up_count",
    "shortline_leader_score",
    "support_ma5",
    "support_ma10",
    "twenty_cm_risk_gate",
    "twenty_cm_risk_score",
    "turnover_rate",
    "upper_shadow_count20",
    "vol5_20",
    "volume_breakout_signal",
    "volume_shrink_pullback",
    # ── 新增辅助数据源字段（Tushare 15000 积分接口） ──
    "holder_num_change_pct",       # 股东人数变化率（<0=筹码集中看多）
    "holder_num_decreasing",       # 股东人数减少>3% 的布尔标记
    "holder_trade_net_ratio",      # 股东增减持净变动比例（<0=净减持看空）
    "survey_count",                # 近30天机构调研次数
    "survey_fund_visitors",        # 近30天调研基金访客数
    "block_trade_avg_discount_rate",  # 大宗交易平均折价率（<0=折价卖方急售看空）
    "is_broken_limit_up",          # 当日是否炸板（情绪转弱看空）
}


# AST 缓存：规则表达式不随股票变化，解析一次即可复用
_ast_cache: dict[str, ast.AST] = {}


class _Missing:
    """字段缺失哨兵。

    universe/prefilter 规则在 strict_missing 模式下，缺失字段返回 _Missing()，
    任何数值比较均判定为 False（include 规则缺失→拒绝，exclude 规则缺失→不排除）。
    根因：原先 _coerce(None)=0.0，使 `dist20 >= 0` 在 dist20 缺失时变成 0.0>=0=True
    静默放行死猫跳（000636 dist20=-11.163 仍被 overnight 推荐致 -5.68% 止损）。
    """

    __slots__ = ()

    def __lt__(self, other): return False
    def __le__(self, other): return False
    def __gt__(self, other): return False
    def __ge__(self, other): return False
    def __eq__(self, other): return other is self
    def __ne__(self, other): return other is not self
    def __bool__(self): return False


_MISSING = _Missing()


def evaluate(expression: str, context: dict[str, Any], strict_missing: bool = False) -> bool:
    if not expression:
        return False
    normalized = (
        expression.replace(" AND ", " and ")
        .replace(" OR ", " or ")
        .replace(" NOT ", " not ")
        .replace(" TRUE", " true")
        .replace(" FALSE", " false")
    )
    try:
        node = _ast_cache.get(normalized)
        if node is None:
            node = ast.parse(normalized, mode="eval")
            _ast_cache[normalized] = node
        return bool(_eval(node.body, context, strict_missing))
    except UnsafeExpression:
        return False
    except Exception:
        logger.warning("规则表达式求值异常: %r", expression[:200], exc_info=True)
        return False


def _eval(node: ast.AST, context: dict[str, Any], strict_missing: bool = False) -> Any:
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, context, strict_missing) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise UnsafeExpression("unsupported bool op")

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _eval(node.operand, context, strict_missing)
        if isinstance(node.op, ast.USub):
            return -_eval(node.operand, context, strict_missing)
        if isinstance(node.op, ast.UAdd):
            return +_eval(node.operand, context, strict_missing)
        raise UnsafeExpression("unsupported unary op")

    if isinstance(node, ast.Compare):
        left = _eval(node.left, context, strict_missing)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, context, strict_missing)
            if not _compare(left, op, right):
                return False
            left = right
        return True

    if isinstance(node, ast.Name):
        name = node.id
        if name == "true":
            return True
        if name == "false":
            return False
        if name not in ALLOWED_NAMES:
            raise UnsafeExpression(f"name not allowed: {name}")
        value = context.get(name)
        if strict_missing and value is None:
            return _MISSING
        return _coerce(value)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool, str)):
            return node.value
        raise UnsafeExpression("only numeric, bool and string constants are allowed")

    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        left = _eval(node.left, context, strict_missing)
        right = _eval(node.right, context, strict_missing)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right if right else 0

    raise UnsafeExpression(f"node not allowed: {type(node).__name__}")


def _compare(left: Any, op: ast.cmpop, right: Any) -> bool:
    # 字段缺失哨兵：任何比较均为 False（strict_missing 模式下缺失字段不满足任何条件）
    if isinstance(left, _Missing) or isinstance(right, _Missing):
        return False
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    raise UnsafeExpression("unsupported comparison")


def _coerce(value: Any) -> Any:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
