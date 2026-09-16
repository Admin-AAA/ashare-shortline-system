"""Versioned production selection policy and its public evaluation interface."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from app.engine.backtest import canonical_fingerprint


_POLICY_METADATA_FIELDS = frozenset({"policy_id", "version"})


@dataclass(frozen=True)
class ProductionSelectionPolicy:
    """All production rules that can remove an otherwise scored candidate."""

    policy_id: str = "production-selection"
    version: int = 1
    board_universe: str | None = None
    excluded_20cm_buckets: tuple[str, ...] = (
        "daily-stock-analysis-skill",
        "chao-gu-yang-jia-skill",
    )
    # 2026-08-02 用户决策：不保留 regime hard_veto（8/1 引入后 7 月 14 天被
    # 999 线全拦，候选最高分 88.7~100 零产出）。关闭后结构性强势/震荡日按
    # 普通分数线评估；如需恢复置 True（改 JSON 即生效，无需改代码）。
    structural_strong_hard_veto: bool = False
    oscillation_hard_veto: bool = False
    strong_score_penalty: float = 8.0
    strong_support_ma5_penalty: float = 6.0
    weak_low_chg20_threshold: float = 15.0
    weak_low_chg20_penalty: float = 12.0
    panic_limit_down_threshold: int = 100
    panic_score_penalty: float = 12.0
    market_base_score_adjustments: tuple[tuple[str, float], ...] = (
        ("强牛", 6.0), ("偏多", 0.0), ("震荡", 0.0), ("弱势", 2.0),
    )
    sentiment_score_adjustments: tuple[tuple[str, float], ...] = (
        ("高潮", 5.0), ("活跃", 3.0), ("平淡", 0.0), ("冰点", -3.0),
    )
    climax_nonweak_extra: float = 8.0
    score_line_minimum: float = 35.0
    score_line_maximum: float = 120.0
    # 弱势 regime 分数线封顶（2026-08-02 修复）：7 月弱市 formal 0 产出根因之一为
    # FORMAL_SCORE_LINE_FLOOR=72 + 弱势 +2 调整把线抬到 74，而弱市 gate 分普遍
    # 偏低（<58）——降线到 60 档让弱市反转信号有产出（诊断：弱势·高潮 78.1% WR）。
    # None = 不封顶（保持历史行为）。
    weak_regime_score_cap: float | None = 60.0
    dynamic_soft_floor_enabled: bool = True
    dynamic_soft_floor_offset: float = 1.0
    dynamic_soft_floor_base_discount: float = 8.0
    dynamic_soft_floor_max_gap: float = 20.0
    dynamic_soft_floor_disabled_regimes: tuple[str, ...] = ("震荡",)
    output_limit_enabled: bool = True
    daily_output_limit: int = 10
    climax_output_divisor: int = 2
    weak_ice_output_limit: int = 2
    # regime×情绪 组合的分数线硬覆盖（L-060）：优先级高于 market_base/sentiment/
    # climax 调整叠加。键为 "regime·sentiment"（如 "强牛·高潮"），值为固定分数线。
    # 用于精细放行已验证正期望且能恢复活跃度的组合；具体阈值由版本化 artifact 持有，
    # 而保持负期望组合（偏多·高潮85）的拦截——单参数叠加无法表达这种二维区分。
    regime_sentiment_line_overrides: tuple[tuple[str, float], ...] = ()

    # L-093 强牛 regime × 高特质波动横截面风险否决（默认关闭，仅 v3 打开）：
    # 在强牛（可配 regime）当日，把全市场 ivol20 分位 ≥ 阈值的候选卡硬剔除、由下一名
    # 自然递补（不空仓、不整日 abstain，区别于 L-080 指数趋势择时门控）。卡片须由选择
    # 咽喉预先附加 ivol20_pct（app.services.risk_features）；缺该字段的卡不参与否决
    # （保守：数据不全不动既有行为）。三窗口 canonical Δcohort 全正，详见 L-093。
    strong_high_ivol_veto_enabled: bool = False
    strong_high_ivol_regimes: tuple[str, ...] = ("强牛",)
    strong_high_ivol_pct_threshold: float = 0.80

    # 通用横截面尾部否决（本轮新增，默认关闭；与 strong_high_ivol_veto 同构但参数化）：
    # 在指定 regime 当日，把卡片上已持久化的因子全市场分位（extra_tail_veto_pct）落在
    # 尾部（direction=high 取 ≥thr；low 取 ≤thr）的候选硬剔除、由下一名自然递补。
    # 卡片分位由选择咽喉（live adapters / 回测 policy_experiment）预附；缺字段保守放行。
    # 具体启用哪个因子由版本化 artifact 声明，便于把"已过 canonical 三窗"的因子升为生产
    # 而不改代码分支。机制与等价性证明见 docs/lessons L-108；本轮用它做的三窗证伪见 L-106/L-107。
    extra_tail_veto_enabled: bool = False
    extra_tail_veto_field: str = "extra_tail_veto_pct"
    extra_tail_veto_regimes: tuple[str, ...] = ("强牛",)
    extra_tail_veto_direction: str = "high"
    extra_tail_veto_pct_threshold: float = 0.80

    # L-109 弱势 regime × 低特质下行半波动横截面风险否决（默认关闭，仅 v4 打开）：
    # 与 L-093 同构但方向相反、regime 不同——在弱势（可配 regime）当日，把全市场 ivol_dn20
    # 分位 <= 阈值的候选卡硬剔除、由下一名自然递补。机制：弱势市无承接盘，"残差下行半波动
    # 极低"的安静票缺反转弹性、易被止损/隔夜跳空砍掉（被砍卡三窗均收益 -0.9~-1.4%）。
    # 卡片须由选择咽喉预先附加 ivol_dn20_pct；缺该字段的卡不参与否决（保守）。详见 L-109。
    weak_low_ivoldn20_veto_enabled: bool = False
    weak_low_ivoldn20_regimes: tuple[str, ...] = ("弱势",)
    weak_low_ivoldn20_pct_threshold: float = 0.25

    # v5（L-117）：共识闸门 —— 只保留"至少 N 个非否决策略背书"的候选，且仅在指定市场基态生效。
    # 默认 0 = 关闭，保证 v1-v4 行为与指纹语义不被隐式改变。
    consensus_min_signals: int = 0
    consensus_regimes: tuple[str, ...] = ()

    # v6（L-118）：强牛·高潮日只保留分数最高的 N 张（0 = 关闭）。
    # 依据：W3 的亏损集中在强牛·高潮日（多个 −5.66% 跳空止损）；把高潮日的输出从
    # 现有 divisor 机制（10→5）进一步收到 3 张，三窗 Δcohort +0.015/+0.419/−0.119 全为正
    # 且三窗均过 evaluate_challenger（W2 CI 下界 +0.064、逐月同号 0.86）。
    strong_climax_top_n: int = 0

    # v8（L-121）：高特质波动 + 无均线支撑 → 剔除（把 DSA 的"回撤特征"迁移成跨策略条件）。
    # 依据：W2 里 DSA 相关卡的共同点是"有 MA 支撑"（sup5 0.37 vs 无 DSA 0.16）；
    # W3 里亏损卡集中在高 ivol(0.80) + 高位 + 无支撑，而赢家是低位低波动有支撑。
    # 面板三端口径：W1 WR 60.0%/PF1.420、W3 WR 59.0%/PF1.446/Σ+53.9（W2 让价）。
    no_support_high_ivol_veto_enabled: bool = False
    no_support_high_ivol_pct_threshold: float = 0.70

    # v9（L-122）：配额槽 —— 给"指定技能当日分最高的那张卡"加分，把它抬进 top-N。
    # 依据（用户 2026-09-16）：单策略口径下 trend-pullback W1 WR 61.5%(n148)/W2 65.0%(n80,PF1.73)、
    # overnight W1 PF2.11/W3 PF2.68、sector-relative W1 WR 59.9%(n514)，但组合里每窗只被选中
    # 19/19/3、0/0/0、8/8/1 笔——它们的卡几乎从不赢 top-N 排序。逐技能信号权重对**单信号卡**
    # 完全无效（w 在 Σ(score·w)/Σw 里约掉，实测 sigw_tp15 与基线逐位相同），所以只有直接抬分
    # 才能把它们的最高分卡拉进输出。面板三窗（配额 overnight +20）：W1 Δcohort +0.187
    # CI[+0.028,+0.359]、W3 +0.251、W2 −0.090，三窗均过 evaluate_challenger。
    # 形如 (("overnight-holding-method", 20.0),)；默认空 = 关闭。
    slot_reserve: tuple[tuple[str, float], ...] = ()

    # v11（L-124）：高换手闸门 —— turnover_rate ≥ 阈值的候选卡直接剔除（None = 关闭）。
    # 依据（2026-09-16 第 23 轮）：在**已选中**样本上按换手分层，[8,12) 这一档在三个窗口
    # 都是低质量区（W1 53% vs 60%、W2 47% vs 54-59%、W3 50% vs 61-65%）——是本轮唯一
    # 三窗符号一致的横截面特征。面板口径 turnover<8：W2 WR 54.2→55.9%、PF 1.115→1.186、
    # Σ +132→+201；W3 五项全改善（WR 55.1→55.9%、avg +0.449→+0.652、PF 1.196→1.294）。
    # 缺 turnover_rate 的卡片保守放行（与 v8/L-121 同一纪律）。
    high_turnover_veto_pct: float | None = None

    # v13（L-126）：反向共识闸门 —— 只保留"背书数 ≤ N"的候选（None = 关闭）。
    # 依据（2026-09-16 第 23 轮）：`≥3 背书` 是 W1/W2 的强正信号、W3 的强负信号
    # （已选样本 W3：<3 背书 WR 62% avg +1.57 vs ≥3 背书 WR 45% avg −1.31）。
    # 因果门控（regime/情绪/涨停家数/ivol/分数差）全部无效 → 只能用"已结算的近 K 日
    # 表现"决定当前该用哪一侧；v10 是正向侧、本字段是反向侧，二者由逐日政策切换驱动。
    consensus_max_signals: int | None = None

    # v15（L-127）：大成交额闸门 —— amount_yi ≥ 阈值的候选卡直接剔除（None = 关闭）。
    # 依据（2026-09-16 第 24 轮末尾/V2 口径端到端分层）：剔除 V2 选中样本里 amount_yi 最高 20% 的单子，
    # 三窗胜率同向改善（W2 59.6→61.9%、W1 +1.0pp、W3 +2.2pp）——是本轮唯一在**端到端样本**上
    # 三窗同号的特征。与既有 DSA 自身 universe（`amount_yi < 15`，理由"大成交额是机构博弈盘"）同源，
    # 只是推广到全部技能。缺 amount_yi 的卡片保守放行。
    high_amount_veto_yi: float | None = None

    # v19（L-126）：启用"自适应共识闸门"——由 `app/services/consensus_edge.py` 按
    # **已结算的近 K 日多背书边际**逐日选择政策版本（v19 自身 = 中性分支）。
    # 未启用（False）时，`resolve_adaptive_selection_policy` 原样返回传入政策，逐位不变。
    adaptive_consensus_enabled: bool = False

    # v16（L-129）：高位闸门 —— pos60（60 日价格位置分位）≥ 阈值的候选卡剔除（None = 关闭）。
    # 依据：在"v15 中性配置"的端到端选中样本上按 pos60 分层，剔除最高 20%（pos60≥87）后
    # 三窗胜率同向改善（W2 +2.3pp、W1 +1.6pp、W3 +0.1pp）——与换手/成交额闸门同类的
    # 横截面规则，不依赖任何自适应统计量。缺 pos60 的卡片保守放行。
    high_pos60_veto: float | None = None

    # v23（L-129）：20 日涨幅闸门 —— chg20 ≥ 阈值的候选卡剔除（None = 关闭）。
    # 依据：在 v15 中性配置的端到端样本上按 chg20 分层，剔除最高 30%（chg20≥21.6）后
    # W2 WR +2.6pp、W3 +4.2pp、W1 +0.5pp（三窗同号），与 DSA 既有 universe 的
    # "chg20>50 且当日涨>2% = overextended_chase" 同源，只是阈值收紧到全部技能。
    high_chg20_veto: float | None = None

    # v24（L-129）：60 日涨幅闸门 —— chg60 ≥ 阈值的候选卡剔除（None = 关闭）。
    # 与 chg20 同族但覆盖不同形态（缓慢累积型 vs 急拉型）：在 v15 样本上剔除 chg60 最高 30%
    # 的 W2 WR +4.0pp、W1 −0.6pp、W3 +0.3pp，与 chg20 闸门叠加后用于 W2 的收口。
    high_chg60_veto: float | None = None

    # v25（L-129）：近期涨停闸门 —— limit_up_days20 ≥ 该值的候选卡剔除（None = 关闭）。
    # 依据：v15 样本上剔除"近 20 日出现过涨停"的卡：W2 WR +2.2pp、W1 −0.7pp、W3 +0.3pp。
    # 与 chg20 闸门叠加用于 W2/W3 收口（不追已涨停的票，降低 T+1 追板风险）。
    max_limit_up_days20: int | None = None

    # v26（L-129）："平滑上涨"闸门 —— max_dd60 ≥ 阈值（60 日最深回撤较浅）的候选卡剔除（None = 关闭）。
    # 依据：v15 样本上剔除 max_dd60≥−15.61（回撤最浅的一档）后三窗 WR 同向改善
    # （W2 +2.8pp、W1 +1.0pp、W3 +2.0pp）——"一路平滑新高"的票在 T+1 短线里胜率更差。
    max_dd60_veto: float | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "board_universe": self.board_universe,
            "excluded_20cm_buckets": list(self.excluded_20cm_buckets),
            "structural_strong_hard_veto": self.structural_strong_hard_veto,
            "oscillation_hard_veto": self.oscillation_hard_veto,
            "strong_score_penalty": self.strong_score_penalty,
            "strong_support_ma5_penalty": self.strong_support_ma5_penalty,
            "weak_low_chg20_threshold": self.weak_low_chg20_threshold,
            "weak_low_chg20_penalty": self.weak_low_chg20_penalty,
            "panic_limit_down_threshold": self.panic_limit_down_threshold,
            "panic_score_penalty": self.panic_score_penalty,
            "market_base_score_adjustments": [list(item) for item in self.market_base_score_adjustments],
            "sentiment_score_adjustments": [list(item) for item in self.sentiment_score_adjustments],
            "climax_nonweak_extra": self.climax_nonweak_extra,
            "score_line_minimum": self.score_line_minimum,
            "score_line_maximum": self.score_line_maximum,
            "weak_regime_score_cap": self.weak_regime_score_cap,
            "dynamic_soft_floor_enabled": self.dynamic_soft_floor_enabled,
            "dynamic_soft_floor_offset": self.dynamic_soft_floor_offset,
            "dynamic_soft_floor_base_discount": self.dynamic_soft_floor_base_discount,
            "dynamic_soft_floor_max_gap": self.dynamic_soft_floor_max_gap,
            "dynamic_soft_floor_disabled_regimes": list(self.dynamic_soft_floor_disabled_regimes),
            "output_limit_enabled": self.output_limit_enabled,
            "daily_output_limit": self.daily_output_limit,
            "climax_output_divisor": self.climax_output_divisor,
            "weak_ice_output_limit": self.weak_ice_output_limit,
            "regime_sentiment_line_overrides": [list(item) for item in self.regime_sentiment_line_overrides],
            "strong_high_ivol_veto_enabled": self.strong_high_ivol_veto_enabled,
            "strong_high_ivol_regimes": list(self.strong_high_ivol_regimes),
            "strong_high_ivol_pct_threshold": self.strong_high_ivol_pct_threshold,
            "extra_tail_veto_enabled": self.extra_tail_veto_enabled,
            "extra_tail_veto_field": self.extra_tail_veto_field,
            "extra_tail_veto_regimes": list(self.extra_tail_veto_regimes),
            "extra_tail_veto_direction": self.extra_tail_veto_direction,
            "extra_tail_veto_pct_threshold": self.extra_tail_veto_pct_threshold,
            "weak_low_ivoldn20_veto_enabled": self.weak_low_ivoldn20_veto_enabled,
            "weak_low_ivoldn20_regimes": list(self.weak_low_ivoldn20_regimes),
            "weak_low_ivoldn20_pct_threshold": self.weak_low_ivoldn20_pct_threshold,
            "consensus_min_signals": self.consensus_min_signals,
            "consensus_regimes": list(self.consensus_regimes),
            "strong_climax_top_n": self.strong_climax_top_n,
            "no_support_high_ivol_veto_enabled": self.no_support_high_ivol_veto_enabled,
            "no_support_high_ivol_pct_threshold": self.no_support_high_ivol_pct_threshold,
            "slot_reserve": [list(item) for item in self.slot_reserve],
            "high_turnover_veto_pct": self.high_turnover_veto_pct,
            "consensus_max_signals": self.consensus_max_signals,
            "high_amount_veto_yi": self.high_amount_veto_yi,
            "adaptive_consensus_enabled": self.adaptive_consensus_enabled,
            "high_pos60_veto": self.high_pos60_veto,
            "high_chg20_veto": self.high_chg20_veto,
            "high_chg60_veto": self.high_chg60_veto,
            "max_limit_up_days20": self.max_limit_up_days20,
            "max_dd60_veto": self.max_dd60_veto,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())

    def with_rule_patch(self, rule_id: str, value: Any) -> "ProductionSelectionPolicy":
        aliases = {
            "board_universe": "board_universe",
            "excluded_strategy_buckets": "excluded_20cm_buckets",
            "structural_strong_hard_veto": "structural_strong_hard_veto",
            "oscillation_hard_veto": "oscillation_hard_veto",
        }
        field_name = aliases.get(str(rule_id), str(rule_id))
        if (
            field_name in _POLICY_METADATA_FIELDS
            or field_name not in PATCHABLE_PRODUCTION_RULE_IDS
        ):
            raise ValueError(f"unsupported production selection rule: {rule_id}")
        tuple_string_fields = {
            "excluded_20cm_buckets",
            "dynamic_soft_floor_disabled_regimes",
            "strong_high_ivol_regimes",
            "extra_tail_veto_regimes",
            "weak_low_ivoldn20_regimes",
        }
        if field_name in tuple_string_fields:
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{rule_id} patch must be a list")
            value = tuple(str(item) for item in value)
        if field_name in {"market_base_score_adjustments", "sentiment_score_adjustments", "regime_sentiment_line_overrides"}:
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{rule_id} patch must be a list")
            value = tuple((str(key), float(amount)) for key, amount in value)
        return replace(self, version=self.version + 1, **{field_name: value})


# Explicitly document and constrain the fields that a one-rule experiment may
# change.  This prevents a caller from using a policy patch as an escape hatch
# for identity/fingerprint metadata or future non-selection fields.
PATCHABLE_PRODUCTION_RULE_IDS = frozenset(
    field_name
    for field_name in ProductionSelectionPolicy.__dataclass_fields__
    if field_name not in _POLICY_METADATA_FIELDS
)


_POLICY_ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "policies"


def load_production_selection_policy(
    policy_id: str = "production-selection",
    version: int = 26,
) -> ProductionSelectionPolicy:
    """Load the repository-owned artifact used by production and backtests.

    v1 保留以复现历史；v2 = L-070 偏多/震荡/弱势·高潮硬排除（双窗口因果重放）；
    v3 = v2 全部规则 + L-093 强牛×高特质波动横截面风险否决（三窗口 canonical Δcohort 全正
    +0.075/+0.143/+0.363，生产代码同构复核，见 L-093）；
    v4 为当前默认 = v3 全部规则 + L-109 弱势×低特质下行半波动横截面风险否决（三窗口
    canonical Δcohort 全正 +0.056/+0.116/+0.077，合并月 block CI 下界 >0、逐月同号 18/21，
    被砍卡三窗均收益 -0.9~-1.4%，见 L-109）。
    v5 为当前默认 = v4 全部规则 + 共识闸门（L-117：弱势/震荡 regime 下只保留至少 2 个非否决策略
    背书的候选，三窗口全管线重放 Δcohort +0.105/+0.070/+0.159，三窗均过 evaluate_challenger；
    强牛/偏多不动，避免削掉强趋势窗口的动量收益）。
    v6 为当前默认 = v5 全部规则 + 强牛·高潮日输出上限（strong_climax_top_n=3，L-118；
    三窗 Δcohort 全正、W2 配对 CI 下界 >0、逐月同号 0.86）。
    v8 = v7 + "高 ivol 且无均线支撑"硬剔除（L-121）：端到端 W3 avg/PF/sum 改善但**胜率下降**
    （51.0% vs v7 54.5%），而胜率是当前最紧的约束，因此 v8 保留为可显式加载的候选、**不作为默认**。
    v7 = v6 的同一旋钮收到 2 张（近期窗口优先：端到端 W3 五项全改善
    avg +0.4237 / WR 54.5% / PF 1.186 / sum +28.0 / MDD −15.69，W1 亦改善至 +0.6079/PF 1.336，
    W2 让价 WR −2.0pp、MDD −1.31 但 avg/sum/PF 均改善）。
    需要严格复现旧口径时显式传 version=1/2/3/4/5/6。
    """
    if policy_id != "production-selection" or version not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26):
        raise ValueError(f"unknown production selection policy: {policy_id}@{version}")
    path = _POLICY_ARTIFACT_DIR / f"production_selection.v{version}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("production selection policy artifact must be an object")
    payload["excluded_20cm_buckets"] = tuple(payload.get("excluded_20cm_buckets") or ())
    payload["market_base_score_adjustments"] = tuple(
        (str(key), float(value)) for key, value in payload.get("market_base_score_adjustments") or ()
    )
    payload["sentiment_score_adjustments"] = tuple(
        (str(key), float(value)) for key, value in payload.get("sentiment_score_adjustments") or ()
    )
    payload["dynamic_soft_floor_disabled_regimes"] = tuple(
        str(value) for value in payload.get("dynamic_soft_floor_disabled_regimes") or ()
    )
    payload["regime_sentiment_line_overrides"] = tuple(
        (str(key), float(value)) for key, value in payload.get("regime_sentiment_line_overrides") or ()
    )
    payload["strong_high_ivol_regimes"] = tuple(
        str(value) for value in payload.get("strong_high_ivol_regimes") or ("强牛",)
    )
    payload["extra_tail_veto_regimes"] = tuple(
        str(value) for value in payload.get("extra_tail_veto_regimes") or ("强牛",)
    )
    payload["weak_low_ivoldn20_regimes"] = tuple(
        str(value) for value in payload.get("weak_low_ivoldn20_regimes") or ("弱势",)
    )
    payload["consensus_regimes"] = tuple(
        str(value) for value in payload.get("consensus_regimes") or ()
    )
    payload["consensus_min_signals"] = int(payload.get("consensus_min_signals") or 0)
    payload["strong_climax_top_n"] = int(payload.get("strong_climax_top_n") or 0)
    payload["no_support_high_ivol_veto_enabled"] = bool(
        payload.get("no_support_high_ivol_veto_enabled", False)
    )
    payload["no_support_high_ivol_pct_threshold"] = float(
        payload.get("no_support_high_ivol_pct_threshold") or 0.70
    )
    payload["slot_reserve"] = tuple(
        (str(sid), float(points)) for sid, points in payload.get("slot_reserve") or ()
    )
    _htv = payload.get("high_turnover_veto_pct", None)
    payload["high_turnover_veto_pct"] = None if _htv is None else float(_htv)
    _cms = payload.get("consensus_max_signals", None)
    payload["consensus_max_signals"] = None if _cms in (None, 0, "") else int(_cms)
    _hav = payload.get("high_amount_veto_yi", None)
    payload["high_amount_veto_yi"] = None if _hav in (None, 0, "") else float(_hav)
    payload["adaptive_consensus_enabled"] = bool(payload.get("adaptive_consensus_enabled", False))
    _hpv = payload.get("high_pos60_veto", None)
    payload["high_pos60_veto"] = None if _hpv in (None, 0, "") else float(_hpv)
    _hcv = payload.get("high_chg20_veto", None)
    payload["high_chg20_veto"] = None if _hcv in (None, 0, "") else float(_hcv)
    _h6v = payload.get("high_chg60_veto", None)
    payload["high_chg60_veto"] = None if _h6v in (None, 0, "") else float(_h6v)
    _mlu = payload.get("max_limit_up_days20", None)
    payload["max_limit_up_days20"] = None if _mlu in (None, "",) else int(_mlu)
    _ddv = payload.get("max_dd60_veto", None)
    payload["max_dd60_veto"] = None if _ddv in (None, 0, "") else float(_ddv)
    policy = ProductionSelectionPolicy(**payload)
    if policy.policy_id != policy_id or policy.version != version:
        raise ValueError("production selection policy artifact identity mismatch")
    return policy


DEFAULT_PRODUCTION_SELECTION_POLICY = load_production_selection_policy()


@dataclass(frozen=True)
class SelectionPolicyContext:
    score_line: float
    output_count: int
    market_snapshot: Mapping[str, Any]
    risk_template: str = "balanced"
    display_limit: int | None = None
    same_day_industry_counts: Mapping[str, int] | None = None
    same_day_recommendation_count: int = 0


@dataclass(frozen=True)
class PolicyVerdictStep:
    rule_id: str
    action: str
    reason: str
    before_score_line: float | None = None
    after_score_line: float | None = None
    eliminated: bool = False


@dataclass(frozen=True)
class CandidateVerdict:
    code: str
    selected: bool
    drop_owner: str | None
    steps: tuple[PolicyVerdictStep, ...]


@dataclass(frozen=True)
class PolicyEvaluationResult:
    recommendations: tuple[Mapping[str, Any], ...]
    watchlist: tuple[Mapping[str, Any], ...]
    degraded_watchlist: tuple[Mapping[str, Any], ...]
    verdicts: tuple[CandidateVerdict, ...]
    policy_fingerprint: str
    verdict_summary: Mapping[str, int] = field(default_factory=dict)


def _verdict_for(
    card: Mapping[str, Any],
    recommended_codes: frozenset[str],
    base_score_line: float,
) -> CandidateVerdict:
    code = str(card.get("code") or "")
    meta = card.get("score_line_meta") or {}
    drop_owner = card.get("policy_drop_owner")
    steps = [
        PolicyVerdictStep(
            rule_id=str(item.get("rule_id") or "unknown"),
            action=str(item.get("action") or "observe"),
            reason=str(item.get("reason") or ""),
            before_score_line=item.get("before_score_line"),
            after_score_line=item.get("after_score_line"),
            eliminated=bool(item.get("eliminated") or item.get("action") == "drop"),
        )
        for item in (card.get("policy_verdict_steps") or [])
    ]
    if not drop_owner and meta.get("regime_struct_strong_hard_veto"):
        drop_owner = "structural_strong_hard_veto"
    if not drop_owner and (
        meta.get("regime_oscillation_hard_veto")
        or card.get("_regime_oscillation_hard_veto")
    ):
        drop_owner = "oscillation_hard_veto"
    if not drop_owner and code not in recommended_codes:
        drop_owner = (
            "portfolio_output_limit" if meta.get("passed") else "score_line"
        )
    effective = meta.get("effective_score_line")
    trace_rules = (
        ("strong_support_ma5_penalty", meta.get("regime_support_ma5_penalty")),
        ("strong_score_penalty", meta.get("regime_strong_penalty")),
        ("weak_low_chg20_penalty", meta.get("regime_weak_low_chg20_penalty")),
        ("panic_score_penalty", meta.get("panic_day_penalty")),
    )
    traced = {step.rule_id for step in steps}
    for rule_id, amount in trace_rules:
        if amount and rule_id not in traced:
            steps.append(
                PolicyVerdictStep(
                    rule_id=rule_id,
                    action="raise_score_line",
                    reason=f"effective score line +{float(amount):g}",
                    before_score_line=base_score_line,
                    after_score_line=float(effective) if effective is not None else None,
                )
            )
    if not steps or code in recommended_codes or drop_owner in {
        "score_line",
        "structural_strong_hard_veto",
        "oscillation_hard_veto",
    }:
        action = "select" if code in recommended_codes else "drop"
        steps.append(
            PolicyVerdictStep(
                rule_id=str(drop_owner or "score_line"),
                action=action,
                reason=str((meta.get("block_reason") or drop_owner or "selected")),
                before_score_line=float(meta.get("base_score_line") or base_score_line),
                after_score_line=float(effective) if effective is not None else None,
                eliminated=action == "drop",
            )
        )
    if isinstance(card, dict):
        if drop_owner:
            card["policy_drop_owner"] = str(drop_owner)
        card["policy_verdict_steps"] = [
            {
                "rule_id": step.rule_id,
                "action": step.action,
                "reason": step.reason,
                "before_score_line": step.before_score_line,
                "after_score_line": step.after_score_line,
                "eliminated": step.eliminated,
            }
            for step in steps
        ]
    return CandidateVerdict(
        code=code,
        selected=code in recommended_codes,
        drop_owner=str(drop_owner) if drop_owner else None,
        steps=tuple(steps),
    )


def evaluate_selection_policy(
    cards: list[dict[str, Any]],
    context: SelectionPolicyContext,
    policy: ProductionSelectionPolicy = DEFAULT_PRODUCTION_SELECTION_POLICY,
) -> PolicyEvaluationResult:
    """Evaluate every scored card once and return recommendations plus full verdicts."""

    from app.engine.aggregator import apply_pool_rules

    raw = apply_pool_rules(
        cards,
        output_count=context.output_count,
        score_line=context.score_line,
        market_snapshot=dict(context.market_snapshot),
        risk_template=context.risk_template,
        display_limit=context.display_limit,
        return_watchlist=True,
        same_day_industry_counts=(
            dict(context.same_day_industry_counts)
            if context.same_day_industry_counts is not None else None
        ),
        same_day_recommendation_count=context.same_day_recommendation_count,
        board_universe=policy.board_universe,
        selection_policy=policy,
    )
    recommendations = tuple(raw.get("recommendations") or ())
    selected_codes = frozenset(str(card.get("code") or "") for card in recommendations)
    verdicts = tuple(
        _verdict_for(card, selected_codes, context.score_line) for card in cards
    )
    summary: dict[str, int] = {}
    for item in verdicts:
        owner = item.drop_owner or "selected"
        summary[owner] = summary.get(owner, 0) + 1
    return PolicyEvaluationResult(
        recommendations=recommendations,
        watchlist=tuple(raw.get("watchlist") or ()),
        degraded_watchlist=tuple(raw.get("degraded_watchlist") or ()),
        verdicts=verdicts,
        policy_fingerprint=policy.fingerprint,
        verdict_summary=MappingProxyType(summary),
    )


__all__ = [
    "CandidateVerdict",
    "DEFAULT_PRODUCTION_SELECTION_POLICY",
    "PolicyEvaluationResult",
    "PolicyVerdictStep",
    "PATCHABLE_PRODUCTION_RULE_IDS",
    "ProductionSelectionPolicy",
    "SelectionPolicyContext",
    "evaluate_selection_policy",
    "load_production_selection_policy",
]
