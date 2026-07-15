"""短线共享因子的 ``Factor`` 实现（任务 2.3）。

把 ``shortline_factors.py`` 原先 6 个硬编码顺序调用的 ``_apply_*_factor`` 迁移为因子
框架下的一等因子实现：每个因子声明 ``output_fields`` / ``depends_on`` / ``metadata``，
``compute(stock, ctx) -> dict`` 只返回自己新增的字段、绝不原地改 stock；跨股聚合
（板块梯队统计 / 情绪相位 / 主线行业集合）由调用方一次算好经 ``FactorContext`` 注入。

依赖图（拓扑序执行，同层按注册序 tie-break，复刻迁移前的字段计算顺序）：

    market_context ┐
    sector_ladder  ├─→ leader
    capital_resonance ┘
    limit_up_overheat   (独立)
    twenty_cm_risk      (独立)
    dragon_tiger        (独立)

``leader`` 读取 ``is_main_theme``（market_context 提供）、``sector_ladder_completeness_score``
（sector_ladder 提供）、``capital_resonance_score``（capital_resonance 提供），故显式
``depends_on`` 三者，保证上游先算、其输出先 merge 回工作 stock。其余因子彼此无依赖，
它们之间的相对顺序不影响结果（各因子只写自己的 output_fields，无写-写冲突）。

注册与白名单闭环：内置因子类只在本模块声明。生产代码必须显式调用
``bootstrap_default_factor_registry()``，它会按下方 ``BUILTIN_FACTORS`` 注册、完成依赖/
输出归属校验、生成冻结 ExecutionPlan，最后才注入 evaluator 白名单；模块 import 不会
修改全局 Registry，新增因子也无需手工维护白名单。

行为基线（需求 3.5 / 1.1）：迁移后相同输入下各 output 字段值与迁移前逐字段一致，由
``tests/test_shortline_factors_equivalence.py`` 的等价对拍守护。20cm 识别复用
``engine/board_rules.is_twenty_cm_stock``（任务 1.3 已收口的唯一实现）。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.engine.board_rules import is_twenty_cm_stock

from .base import BaseFactor, FactorContext


# ── 数值 / 分级助手（与迁移前 shortline_factors 逐字一致）────────────────────
def _num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _level(score: float, thresholds: list[tuple[float, str]], default: str) -> str:
    for threshold, label in thresholds:
        if score >= threshold:
            return label
    return default


def sector_key(stock: Mapping[str, Any]) -> str:
    """个股归属的板块键：概念板块优先，回退行业，再回退「未分类」。"""
    return str(stock.get("concept_sector_name") or stock.get("industry") or "未分类")


# ── 跨股聚合构造器（由 apply_shared_shortline_factors 一次算好放进 ctx）────────
def compute_sector_stats(stocks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按板块汇总首/二/高板、涨停数、连板高度、强势股、正流入数与最优热度排名。

    与迁移前 ``_sector_stats`` 逐字一致：在任何因子写回之前，基于原始输入字段扫全量
    股票算一次。读取的都是输入字段（is_limit_up / pct / limit_up_consecutive_days /
    main_net_inflow_yi / concept_sector_heat_rank），与因子 output_fields 不相交，故先后
    顺序不影响结果。
    """
    stats: dict[str, dict[str, Any]] = {}
    for stock in stocks:
        sector = sector_key(stock)
        row = stats.setdefault(
            sector,
            {
                "stock_count": 0,
                "limit_up_count": 0,
                "first_board_count": 0,
                "second_board_count": 0,
                "high_board_count": 0,
                "height": 0,
                "strong_count": 0,
                "positive_flow_count": 0,
                "best_heat_rank": 999,
            },
        )
        row["stock_count"] += 1
        pct = _num(stock.get("pct"))
        boards = int(_num(stock.get("limit_up_consecutive_days"), 0))
        is_limit_up = bool(stock.get("is_limit_up")) or boards > 0
        if is_limit_up:
            row["limit_up_count"] += 1
            row["height"] = max(row["height"], max(1, boards))
            if boards <= 1:
                row["first_board_count"] += 1
            elif boards == 2:
                row["second_board_count"] += 1
            else:
                row["high_board_count"] += 1
        if pct >= 3:
            row["strong_count"] += 1
        if _num(stock.get("main_net_inflow_yi")) > 0:
            row["positive_flow_count"] += 1
        rank = int(_num(stock.get("concept_sector_heat_rank"), 999))
        row["best_heat_rank"] = min(row["best_heat_rank"], rank)
    return stats


def market_sentiment_phase(snapshot: Mapping[str, Any]) -> str:
    """市场情绪相位：高潮 / 活跃 / 平淡 / 冰点（与迁移前 ``_market_sentiment_phase`` 一致）。"""
    sample = max(1, int(_num(snapshot.get("sample_count"), 1)))
    limit_up = _num(snapshot.get("limit_up_count"))
    limit_down = _num(snapshot.get("limit_down_count"))
    lu_rate = limit_up / sample
    ld_rate = limit_down / sample
    if lu_rate > 0.012 and (ld_rate == 0 or lu_rate / max(ld_rate, 0.0001) > 3):
        return "高潮"
    if lu_rate > 0.005:
        return "活跃"
    if lu_rate >= 0.001:
        return "平淡"
    return "冰点"


def main_industries_of(snapshot: Mapping[str, Any]) -> set[str]:
    """主线行业/题材集合（与迁移前一致：去空白后取字符串集合）。"""
    return {str(v) for v in snapshot.get("main_industries", []) if str(v).strip()}


# ── 因子实现 ────────────────────────────────────────────────────────────
class MarketContextFactor(BaseFactor):
    """市场上下文：标注个股是否处于主线题材、当日市场情绪相位。

    ``is_main_theme`` 被 ``leader`` 读取，故本因子必须先于 leader 执行（leader
    depends_on 本因子）。主线判定需要跨股的 ``main_industries`` 集合与预算好的
    ``sentiment_phase``，均由调用方注入 ctx.aggregates。
    """

    id = "market_context"
    output_fields = ("is_main_theme", "market_sentiment_phase")
    depends_on = ()
    metadata = {
        "logic": "结合主线行业/概念命中与市场涨停相位，标注个股是否处于主线题材及当日情绪相位。",
        "data_sources": ["行情快照 main_industries", "行业/概念板块字段", "市场涨停/跌停计数"],
        "degradation": "未分类行业强制判为非主线；缺快照时相位退化为冰点。",
    }

    def compute(self, stock: Mapping[str, Any], ctx: FactorContext) -> dict[str, Any]:
        main_industries = ctx.aggregate("main_industries") or set()
        is_main = bool(
            stock.get("industry") in main_industries
            or stock.get("concept_sector_name") in main_industries
        )
        return {
            "is_main_theme": is_main and stock.get("industry") != "未分类",
            "market_sentiment_phase": ctx.aggregate("sentiment_phase"),
        }


class SectorLadderFactor(BaseFactor):
    """板块梯队完整度：按同板块首/二/高板、涨停数、强势股与题材热度计分。"""

    id = "sector_ladder"
    output_fields = (
        "sector_ladder_completeness_score",
        "sector_ladder_level",
        "sector_ladder_limit_up_count",
        "sector_ladder_height",
    )
    depends_on = ()
    metadata = {
        "logic": "按同板块首板、二板、中高标、涨停数、强势股数量和题材热度衡量梯队完整度。",
        "data_sources": ["Tushare/本地涨停池", "本地概念板块池", "行情与行业字段"],
        "degradation": "缺少概念板块时退化为行业分组；缺少板块信息时输出 0 分和缺失标签。",
        # 解释规格：fields 是策划子集（只列打分与分级，不含明细计数），故显式声明。
        "explanation": {
            "key": "sector_ladder",
            "order": 1,
            "fields": ["sector_ladder_completeness_score", "sector_ladder_level"],
            "value_field": "sector_ladder_level",
            "score_field": "sector_ladder_completeness_score",
        },
    }

    def compute(self, stock: Mapping[str, Any], ctx: FactorContext) -> dict[str, Any]:
        sector = sector_key(stock)
        stats = ctx.aggregate("sector_stats", {}).get(sector, {})
        if not sector or sector == "未分类":
            return {
                "sector_ladder_completeness_score": 0.0,
                "sector_ladder_level": "缺失",
                "sector_ladder_limit_up_count": 0,
                "sector_ladder_height": 0,
            }

        score = 0.0
        score += min(30.0, _num(stats.get("limit_up_count")) * 8)
        score += 15.0 if _num(stats.get("first_board_count")) > 0 else 0.0
        score += 20.0 if _num(stats.get("second_board_count")) > 0 else 0.0
        score += 20.0 if _num(stats.get("high_board_count")) > 0 else 0.0
        score += 10.0 if _num(stats.get("strong_count")) >= 3 else 0.0
        score += 8.0 if int(_num(stats.get("best_heat_rank"), 999)) <= 5 else 0.0
        score = round(min(100.0, score), 2)
        return {
            "sector_ladder_completeness_score": score,
            "sector_ladder_level": _level(score, [(75, "完整"), (50, "展开"), (25, "初现")], "不完整"),
            "sector_ladder_limit_up_count": int(_num(stats.get("limit_up_count"))),
            "sector_ladder_height": int(_num(stats.get("height"))),
        }


class CapitalResonanceFactor(BaseFactor):
    """资金共振：主力净流入/超大单/散户方向/板块资金排名/同板块资金扩散计分。

    有资金流数据走 ``capital_flow`` 口径，缺失则回退到涨幅+量比+成交额的
    ``price_volume_fallback`` 口径，并在 source 中标明。
    """

    id = "capital_resonance"
    output_fields = (
        "capital_resonance_score",
        "capital_resonance_signal",
        "capital_resonance_source",
    )
    depends_on = ()
    metadata = {
        "logic": "按个股主力净流入、超大/大单方向、散户方向、板块资金排名和同板块资金扩散计分。",
        "data_sources": ["Tushare/本地资金流池", "行情成交额与量比回退"],
        "degradation": "缺少资金流时用涨幅、量比、成交额作可回测回退，并在解释中标明回退来源。",
        # 解释规格：fields 只列打分与信号（不含 source），source 走 extra；故显式声明。
        "explanation": {
            "key": "capital_resonance",
            "order": 2,
            "fields": ["capital_resonance_score", "capital_resonance_signal"],
            "value_field": "capital_resonance_signal",
            "score_field": "capital_resonance_score",
            "extra": {"source": "capital_resonance_source"},
        },
    }

    def compute(self, stock: Mapping[str, Any], ctx: FactorContext) -> dict[str, Any]:
        stats = ctx.aggregate("sector_stats", {}).get(sector_key(stock), {})
        data_quality = stock.get("data_quality") or {}
        missing = set(data_quality.get("missing_fields") or [])
        has_flow = "capital_flow" not in missing or data_quality.get("capital_flow_checked")

        if has_flow:
            main = _num(stock.get("main_net_inflow_yi"))
            ratio = _num(stock.get("main_net_inflow_ratio"))
            super_large = _num(stock.get("super_large_net_inflow_yi"))
            large = _num(stock.get("large_net_inflow_yi"))
            small = _num(stock.get("small_net_inflow_yi"))
            rank = int(_num(stock.get("sector_fund_rank"), 99))
            score = 45.0
            score += min(22.0, max(-18.0, main * 4))
            score += 10.0 if ratio >= 8 else 5.0 if ratio >= 3 else 0.0
            score += 14.0 if super_large > 0 and large >= 0 else 6.0 if super_large > 0 else 0.0
            score += 8.0 if small < 0 and main > 0 else 0.0
            score += 12.0 if rank <= 5 else 6.0 if rank <= 15 else -6.0
            score += 8.0 if _num(stats.get("positive_flow_count")) >= 2 else 0.0
            source = "capital_flow"
        else:
            pct = _num(stock.get("pct"))
            vol = _num(stock.get("vol5_20"), 1.0)
            amount = _num(stock.get("amount_yi"))
            score = 45.0
            score += 16.0 if pct >= 3 and vol >= 1.5 else 8.0 if pct > 1 and vol >= 1.2 else 0.0
            score += 10.0 if amount >= 5 else 4.0 if amount >= 3 else -6.0
            source = "price_volume_fallback"

        score = round(max(0.0, min(100.0, score)), 2)
        return {
            "capital_resonance_score": score,
            "capital_resonance_signal": _level(
                score,
                [(75, "主力共振"), (60, "资金配合"), (45, "中性")],
                "资金背离",
            ),
            "capital_resonance_source": source,
        }


class LimitUpOverheatFactor(BaseFactor):
    """涨停情绪过热：按市场涨停率、跌停压力、连板高度、涨幅位置与封板分歧计分。"""

    id = "limit_up_overheat"
    output_fields = ("limit_up_overheat_score", "limit_up_overheat_level")
    depends_on = ()
    metadata = {
        "logic": "按市场涨停率、跌停压力、个股连板高度、涨幅位置和封板分歧计分，分数越高代表涨停情绪越过热。",
        "data_sources": ["Tushare/本地涨停池", "行情快照", "日 K 线派生字段"],
        "degradation": "缺少涨停明细时退化为市场涨停率、个股涨幅和位置估算；无法确认时给中性偏低风险。",
        # 解释规格：order=0 复刻历史顶层首位；fields 等于 output_fields。
        "explanation": {
            "key": "limit_up_overheat",
            "order": 0,
            "fields": ["limit_up_overheat_score", "limit_up_overheat_level"],
            "value_field": "limit_up_overheat_level",
            "score_field": "limit_up_overheat_score",
        },
    }

    def compute(self, stock: Mapping[str, Any], ctx: FactorContext) -> dict[str, Any]:
        snapshot = ctx.snapshot
        sample = max(1, int(_num(snapshot.get("sample_count"), 1)))
        limit_up = _num(snapshot.get("limit_up_count"))
        limit_down = _num(snapshot.get("limit_down_count"))
        lu_rate = limit_up / sample
        boards = int(_num(stock.get("limit_up_consecutive_days"), 0))
        score = 0.0
        score += 28.0 if lu_rate >= 0.018 else 20.0 if lu_rate >= 0.012 else 10.0 if lu_rate >= 0.006 else 0.0
        score += 12.0 if limit_down >= max(5.0, limit_up) else 0.0
        score += 30.0 if boards >= 5 else 22.0 if boards >= 3 else 10.0 if boards >= 2 else 0.0
        score += 14.0 if bool(stock.get("is_limit_up")) or _num(stock.get("pct")) >= 8 else 0.0
        score += 12.0 if _num(stock.get("pos60")) > 95 and _num(stock.get("dist60")) > 30 else 0.0
        score += 8.0 if int(_num(stock.get("limit_up_open_times"))) >= 8 else 0.0
        score = round(min(100.0, score), 2)
        return {
            "limit_up_overheat_score": score,
            "limit_up_overheat_level": _level(score, [(70, "高"), (45, "中"), (20, "低")], "正常"),
        }


class TwentyCmRiskFactor(BaseFactor):
    """20cm 风险闸门：识别创业/科创/北交高波动标的，按涨幅/换手/流动性/位置给闸门。

    20cm 识别复用 ``engine/board_rules.is_twenty_cm_stock``（任务 1.3 收口的唯一实现）。
    """

    id = "twenty_cm_risk"
    output_fields = (
        "is_20cm_stock",
        "twenty_cm_risk_score",
        "twenty_cm_risk_gate",
        "twenty_cm_risk_reason",
    )
    depends_on = ()
    metadata = {
        "logic": "识别创业板、科创板、北交所等 20cm 标的，并按涨幅、换手、流动性、位置和封板状态给风险闸门。",
        "data_sources": ["证券代码/交易所/市场板块", "行情快照", "日 K 线派生字段"],
        "degradation": "缺少市场板块时用代码前缀推断；无法识别则按非 20cm 处理但保留原因。",
        # 解释规格：fields 等于 output_fields；reason 走 extra。
        "explanation": {
            "key": "twenty_cm_risk",
            "order": 3,
            "fields": [
                "is_20cm_stock",
                "twenty_cm_risk_score",
                "twenty_cm_risk_gate",
                "twenty_cm_risk_reason",
            ],
            "value_field": "twenty_cm_risk_gate",
            "score_field": "twenty_cm_risk_score",
            "extra": {"reason": "twenty_cm_risk_reason"},
        },
    }

    def compute(self, stock: Mapping[str, Any], ctx: FactorContext) -> dict[str, Any]:
        is_20cm, basis = is_twenty_cm_stock(stock)
        if not is_20cm:
            return {
                "is_20cm_stock": is_20cm,
                "twenty_cm_risk_score": 0.0,
                "twenty_cm_risk_gate": "not_applicable",
                "twenty_cm_risk_reason": basis,
            }

        score = 15.0
        reasons: list[str] = [basis]
        if _num(stock.get("pct")) >= 12:
            score += 25.0
            reasons.append("当日涨幅进入 20cm 高波动区")
        if bool(stock.get("is_limit_up")):
            score += 20.0
            reasons.append("20cm 涨停后追高成交约束更强")
        if _num(stock.get("turnover_rate")) >= 18:
            score += 18.0
            reasons.append("换手过高")
        if _num(stock.get("amount_yi")) < 3:
            score += 12.0
            reasons.append("成交额不足")
        if _num(stock.get("pos60")) > 95 and _num(stock.get("dist60")) > 30:
            score += 15.0
            reasons.append("中期位置过高")
        score = round(min(100.0, score), 2)
        return {
            "is_20cm_stock": is_20cm,
            "twenty_cm_risk_score": score,
            "twenty_cm_risk_gate": "block" if score >= 70 else "caution" if score >= 35 else "pass",
            "twenty_cm_risk_reason": "；".join(reasons),
        }


class LeaderFactor(BaseFactor):
    """龙头候选评分：度量「龙头属性」（主线/板块阶梯/资金共振/涨停/突破）。

    显式依赖 market_context（提供 is_main_theme）、sector_ladder（提供
    sector_ladder_completeness_score）、capital_resonance（提供 capital_resonance_score），
    驱动据此保证三者先算、其输出先 merge，复刻迁移前 leader 读上游字段的行为。
    """

    id = "leader"
    output_fields = ("shortline_leader_score", "is_shortline_leader_candidate")
    depends_on = ("market_context", "sector_ladder", "capital_resonance")
    metadata = {
        "logic": "度量龙头属性（主线/板块阶梯/资金共振/涨停/突破/中期强势），用于选股逻辑而非胜率预测。",
        "data_sources": ["market_context/sector_ladder/capital_resonance 上游因子输出", "行情突破与位置派生字段"],
        "degradation": "上游字段缺失时按 0 计入对应项，不阻断评分。",
    }

    def compute(self, stock: Mapping[str, Any], ctx: FactorContext) -> dict[str, Any]:
        # 龙头候选评分：度量"龙头属性"（主线/板块阶梯/资金共振/涨停/突破），用于选股逻辑，非胜率预测。
        # 权重设计基于因果（主力资金行为），非 point-biserial 优化。
        # 诊断（第99轮 T3，148 笔去重样本）：
        #   - shortline_leader_score 对胜率 point-biserial=0.046（无预测力）
        #   - capital_resonance/sector_ladder 占 52% 权重但分桶胜率无单调关系（CR: 0→63.6%/1-50→70.5%/>=50→66.7%）
        #   - 阈值 68 未作为硬门槛（133/148 笔 leader_score<60 仍被推荐，胜率 69.9%）
        # 结论：权重调整对胜率影响有限，胜率主要由 trailing stop + 策略 universe 决定。
        # 调整权重前必须回测验证（见 alpha-decay-false-positive 教训），避免破坏选股逻辑。
        score = 0.0
        # is_main_theme 占 24 分（最大权重），但 95.7% 的推荐都是 True（第105轮 T1 衰减复检）。
        # 衰减 38.8%（point-biserial 0.125→-0.077），但近期 False 样本仅 5 笔（不可信）。
        # True 胜率前期 72.1%→近期 61.6%（降 10.5%，市场环境变化），False 样本太少无法判定。
        # 95.7% 都是 True 说明该因子几乎无区分度（只排除 4.3%），24 分等于普遍提分。
        # 盈亏比维度（第106轮 T2，按第92轮教训看盈亏比不只看胜率）：
        #   True 盈亏比 1.68（avg_loss 3.5%）vs False 盈亏比 3.85（avg_loss 1.74%）
        #   False 盈亏比更高——非主线股票亏损更可控（但 False 仅 18 笔，不显著）
        #   印证第92轮教训：胜率高的因子盈亏比可能反而低
        # 未来优化方向：降低 is_main_theme 权重，把分数让给有区分度的因子（需回测验证）。
        score += 24.0 if stock.get("is_main_theme") else 0.0
        score += min(28.0, _num(stock.get("sector_ladder_completeness_score")) * 0.28)
        score += min(24.0, _num(stock.get("capital_resonance_score")) * 0.24)
        score += 16.0 if bool(stock.get("is_limit_up")) or _num(stock.get("pct")) >= 5 else 0.0
        score += 8.0 if bool(stock.get("breakout20")) else 0.0
        # breakout60 加入评分（T3 第82轮）：近期预测力增强（+0.023→+0.190），
        # 6-7月 breakout60=True WR=85.7% avg+5.20%（vs False 54.3%）
        # 反事实验证：加 8 分新增 41 笔推荐 WR=58.5% 净=+46.57%（0 移除，纯增量）
        score += 8.0 if bool(stock.get("breakout60")) else 0.0
        # 中期强势因子（T1 第83轮）：dist60>=9.4 表示价格站上60日线 9.4%+，中期趋势确立
        # 数据验证（去重 282 笔）：dist60>=9.4 WR=66.0% avg+2.28%（vs <9.4 的 44.0%/+0.36%）
        # 高潮阶段尤佳：dist60>=9.4 WR=75.0% avg+4.26%（vs <9.4 的 40.7%）
        # 近期增强：5月 r=-0.106 → 6-7月 r=+0.347
        # 反事实验证（扣费0.10%/笔）：加 6 分新增 53 笔 WR=66.0% 净=+115.72%（0 移除）
        # 正交性：与 breakout60 重叠仅 6/58，与 leader 现有项相关 |r|<0.32（独立 alpha）
        # 因果：60日线是机构中期成本线，站上方 9%+ = 中期趋势确立且资金浮盈，主力不轻易放弃
        score += 6.0 if _num(stock.get("dist60")) >= 9.4 else 0.0
        score = round(min(100.0, score), 2)
        return {
            "shortline_leader_score": score,
            "is_shortline_leader_candidate": score >= 68,
        }


class DragonTigerFactor(BaseFactor):
    """龙虎榜聪明钱：买方席位分散度 + 机构净买占比（净买额为 0 或未上榜时归零）。"""

    id = "dragon_tiger"
    output_fields = ("dragon_tiger_buy_dispersion", "dragon_tiger_inst_buy_ratio")
    depends_on = ()
    metadata = {
        "logic": "按龙虎榜买方席位数量判定资金分散度，用机构净买占净买额比例度量聪明钱强度。",
        "data_sources": ["Tushare/本地龙虎榜池"],
        "degradation": "未上榜时输出「未上榜」且比例为 0；净买额为 0 时比例归零。",
    }

    def compute(self, stock: Mapping[str, Any], ctx: FactorContext) -> dict[str, Any]:
        seats = int(_num(stock.get("dragon_tiger_buy_seat_count"), 0))
        net_amt = _num(stock.get("dragon_tiger_net_amount_yi"))
        inst_net = _num(stock.get("dragon_tiger_institution_net_buy_yi"))
        if not stock.get("dragon_tiger_is_on_list"):
            return {
                "dragon_tiger_buy_dispersion": "未上榜",
                "dragon_tiger_inst_buy_ratio": 0.0,
            }
        if seats and seats <= 2:
            dispersion = "集中"
        elif seats and seats <= 5:
            dispersion = "一般"
        else:
            dispersion = "分散"
        return {
            "dragon_tiger_buy_dispersion": dispersion,
            "dragon_tiger_inst_buy_ratio": round(inst_net / abs(net_amt), 2) if net_amt else 0.0,
        }


# 内置因子在 ``bootstrap_default_factor_registry`` 中显式注册、验证并冻结；模块导入
# 只声明实现，不修改进程级 Registry，避免隐式 import side effect。
BUILTIN_FACTORS = (
    MarketContextFactor,
    SectorLadderFactor,
    CapitalResonanceFactor,
    LimitUpOverheatFactor,
    TwentyCmRiskFactor,
    LeaderFactor,
    DragonTigerFactor,
)
