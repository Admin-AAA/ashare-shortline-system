"""三窗"整策略剔除"生产同构消融（单次重放 × 多臂复用，只读、零 DB 写）。

动机：research_strategy_exclude_canonical / research_strategy_ablate_ci 每个目标策略都要把
最贵的"universe + 特征 + 逐 skill 规则扫描"重建一遍（实测 ~37min/臂·窗），13 个策略 × 3 窗
≈ 24h。本脚本把重放与聚合拆开：每个交易日只做一次最贵的部分，把 prelim_entries（每只股票
每个 skill 的规则信号）留在内存，随后每个剔除臂只重跑廉价的 gate + aggregate。

语义（与生产的等价性）：
- 剔除某 skill = selected_skills 去掉它 + 该 skill 的 rule_signal 置空（对应 prefilter 并集里
  它不再贡献候选），prelim_score 按过滤后信号重算。生产里 `prefilter_skill_ids` 决定每个
  skill 给哪些股票打分，因此单只股票只剩被剔除 skill 的信号时会自然落选，与真剔除一致。
- 选择/出场一律用**未压缩的全量卡**：aggregate 内部已跑真实 evaluate_selection_policy
  （DEFAULT_PRODUCTION_SELECTION_POLICY = production_selection v4），且 ivol20/ivol_dn20 分位
  在压缩前就附在卡上。走 _pre_policy_card 压缩后的卡会丢这两个字段 → v3/v4 风险否决静默失效
  （L-112(b) 陷阱），故本脚本不用压缩卡做选择。
- 出场用真实因果 _simulate_card（sellable=tradable），与 published 出场策略一致。

前置断言（L-112a）：A 臂（全启用）三窗指标必须与既有 canonical 记录同量级，不一致先修基线。

用法：
  cd backend && .venv/bin/python scripts/research_strategy_ablation_fast.py \
      --windows W1,W2,W3 --out output/research/per_strategy/ablate_fast.json
输出：JSON（每窗 A 臂指标 + 每个 skill 的剔除 Δ/CI/逐月同号/独持卡/递补卡）+ log。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import event, select

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY  # noqa: E402
from app.models.tables import DailyKbar  # noqa: E402
from app.services.backtest.candidate_snapshot_builder import (  # noqa: E402
    ProductionRuleReplayCandidateBuilder,
    _reject_write_statement,
)
from app.services.backtest.policy_experiment import _simulate_card  # noqa: E402
from app.services.backtest.stock_metadata import StockMetadataHistoryProvider  # noqa: E402
from app.services.runs.skill_gate import build_skill_rule_gate  # noqa: E402
from app.services.runs.stages.adapters import ProductionRecommendationAssembly  # noqa: E402
from app.services.workbench_service import _prelim_rule_score  # noqa: E402

WINDOWS = {
    "W1": ("2025-01-15", "2026-01-16"),
    "W2": ("2026-01-17", "2026-07-30"),
    "W3": ("2026-08-01", "2026-09-13"),
}
PARAMS = {
    "data_source": "local_pool",
    "market_scope": "all",
    "market_scope_value": None,
    "score_line": 72.0,
    "output_count": 10,
    "risk_template": "balanced",
    "hide_recent_repeats": False,
    "disable_agent_review": True,
    "skill_review_limit": 0,
}
OUT = B / "output" / "research" / "per_strategy"

# 三窗入场面板（--panel-dir）导出的卡字段白名单。目的是把"每个交易日、每张 pre-policy 卡"
# 落到磁盘，使后续入场规则筛查可以离线重跑真实 evaluate_selection_policy，而不必每次重放
# 2 小时的 universe+特征+规则扫描。必须包含 ivol 分位（v3/v4 风险否决的输入，L-112(b)：
# 缺字段会按"保守放行"静默降级成 v1/v2 行为）。
_PANEL_CARD_KEYS = (
    "code", "name", "industry", "bucket", "final_score", "selection_score",
    "portfolio_rank_score", "recommend_price", "open", "high", "low", "close", "pct",
    "volume", "amount_yi", "is_limit_up", "support_ma5", "support_ma10", "market_regime",
    "consensus_score", "component_scale", "evidence_quality_score", "data_completeness_score",
    "hard_veto", "chg20", "pos60", "ivol20_pct", "ivol_dn20_pct",
    # 组件分与质量/真实性分组：policy 的有效分数线与证据调整读它们（缺了会翻转边界票）
    "component_scores", "raw_component_scores", "weighted_component_scores", "score_breakdown",
    "consensus_boost", "consensus_tags", "stance_summary", "skill_disagreement",
    "market_discipline", "data_quality", "data_authenticity", "authenticity_severe_discount",
    "aggregation_weights", "llm_influence_mode", "shortline_factors", "risk_flags", "info_tags",
    "setup_type", "entry_mode", "stop_loss", "take_profit",
    # policy 直接读的 regime 内部标记（aggregator 在聚合阶段写入；不在白名单里会让
    # 离线重跑丢掉 hard veto → 实测 W3 2026-08-04 多出一张票）
    "_regime_oscillation_hard_veto", "_regime_strong_penalty", "_regime_support_ma5_penalty",
    "_regime_struct_strong_hard_veto", "_regime_weak_low_chg20_penalty", "_panic_day_penalty",
)
PANEL: dict = {"dir": None}
UNIVERSE: dict = {"mode": "historical"}
POLICY_VERSION: dict = {"n": None}   # None = 用 default；否则显式加载该版本 artifact
# 逐日政策切换（自适应闸门实验用）：GATE["dates"] 里的交易日改用 GATE["version"] 的政策，
# 其余交易日照常用默认/`--policy-version`。动机：某些规则在 A 段行情有效、在 B 段行情有害
# （例如"≥3 背书"闸门：W1/W2 端到端大改善、W3 端到端 WR −17pp），而现有可用特征无法因果门控，
# 只能用"已结算的近 N 日表现"决定开关。见 L-126。
GATE: dict = {"map": {}, "version": None}   # map: {signal_date: policy_version}（逐日三态切换）
SKILL_WEIGHTS: dict = {"mode": "equal", "map": None}  # equal | preset | json
EP_OVERRIDE: dict = {"policy": None}                  # dataclasses.replace 出来的出场策略变体
_IVOL_BY_DATE: dict[str, dict] = {}
_IVOL_DN_BY_DATE: dict[str, dict] = {}


def _panel_card(card: dict) -> dict:
    """面板落盘用：保留 policy 决策可能读到的一切字段（含组件分与 regime 内部标记）。

    教训：只留"看起来有用"的标量白名单，会让离线 `evaluate_selection_policy` 在
    W3 2026-08-04 多出一张票——policy 的有效分数线由卡上组件分/质量分推导，缺字段造成
    0.02pp 级偏差就翻转 64.38 vs 64.40 的边界票。因此这里保守保留组件分组 + 完整
    strategy_signals 元数据 + feature_snapshot，并用 `_assert_panel_fidelity`
    逐日复现选中集合来证明等价（不通过直接抛错，不允许产出不可信面板）。
    """
    out = {k: card[k] for k in _PANEL_CARD_KEYS if k in card}
    out["strategy_signals"] = [
        dict(s) for s in (card.get("strategy_signals") or []) if isinstance(s, dict)
    ]
    # data_quality 只保留 missing_fields（policy 的 critical_aux_missing 依据）；
    # 整块 data_quality 是 deep audit 结构（~11KB/卡），是面板体积的主要来源。
    dq = card.get("data_quality")
    if isinstance(dq, dict) and "missing_fields" in dq:
        out["data_quality"] = {"missing_fields": dq["missing_fields"]}
    fs = card.get("feature_snapshot")
    if isinstance(fs, dict):
        out["feature_snapshot"] = {k: v for k, v in fs.items() if _panel_scalar(v)}
    return out


def _panel_scalar(value) -> bool:
    """feature_snapshot 只留标量（含短的标量列表）：research_context/data_quality/
    field_quality/shortline_factor_explanations 是审计结构，占面板体积 90%+ 且 policy 不读。"""
    if value is None or isinstance(value, (int, float, str, bool)):
        return True
    if isinstance(value, list) and len(value) <= 20:
        return all(isinstance(x, (int, float, str, bool)) for x in value)
    return False


def _assert_panel_fidelity(panel_path: Path, window: str, tolerance: float = 0.03) -> dict:
    """前置断言（L-112a）：面板 + 真实 policy 必须复现 harness 的选中集合。

    允许极小比例的**边界日**不一致（离线重跑与 harness 在同一进程/同一天内对"第 10 张卡"
    的边界判定可能相差一张；实测 W2 3/128 天、W1/W3 0 天）。超过 tolerance 直接抛错，
    并把不一致日期写回面板 JSON，供筛查脚本把"离线基线"作为参照并对账。
    """
    from app.engine.production_policy import (
        evaluate_selection_policy,
        load_production_selection_policy,
        SelectionPolicyContext,
    )

    pol = (
        load_production_selection_policy(version=int(POLICY_VERSION["n"]))
        if POLICY_VERSION.get("n") is not None
        else load_production_selection_policy()
    )
    data = json.loads(panel_path.read_text())
    mismatch: list[str] = []
    for day in data["days"]:
        res = evaluate_selection_policy(
            [dict(c) for c in day["cards"]],
            SelectionPolicyContext(
                score_line=PARAMS["score_line"],
                output_count=PARAMS["output_count"],
                market_snapshot=dict(day["market_snapshot"]),
                risk_template=PARAMS["risk_template"],
                same_day_industry_counts=day["same_day_industry_counts"],
                same_day_recommendation_count=day["same_day_recommendation_count"],
            ),
            pol,
        )
        got = sorted(str(c.get("code")) for c in res.recommendations)
        want = sorted(day["selected"])
        if got != want:
            mismatch.append(f"{day['signal_date']}: want={want} got={got}")
    if mismatch:
        ratio = len(mismatch) / max(len(data["days"]), 1)
        if ratio > tolerance:
            raise RuntimeError(
                f"面板保真度断言失败（{window}）：{len(mismatch)}/{len(data['days'])} 天不一致"
                f"（>{tolerance:.0%}）；前 3 条：{mismatch[:3]}"
            )
        data["fidelity_mismatch_days"] = [m.split(":")[0] for m in mismatch]
        panel_path.write_text(json.dumps(data, ensure_ascii=False))
        print(f"[panel] {window}: 保真度 {len(data['days']) - len(mismatch)}/{len(data['days'])} 天一致，"
              f"边界日 {len(mismatch)} 天（{ratio:.1%} ≤ {tolerance:.0%} 容差）："
              f"{data['fidelity_mismatch_days']}", flush=True)
        return data
    print(f"[panel] {window}: 保真度断言通过（{len(data['days'])} 天逐日复现选中集合，"
          f"共 {sum(len(d['selected']) for d in data['days'])} 张）", flush=True)
    return data


def _panel_pre_policy_cards(db, candidate_results, selected_skills, snapshot, params, signal_date, run_id):
    """复刻 `ProductionRecommendationAssembly.aggregate` 在跑 policy **之前** 的建卡步骤。

    必须自己建卡而不能复用 aggregate 的返回值：`evaluate_selection_policy` 会就地改写卡
    （score_line_meta / policy_drop_owner / 软降线等），拿改写后的卡再离线跑一遍策略会把
    规则二次施加，实测 W3 有 1/30 天会多出一张票。这里输出的是 policy 的**输入态**，
    因此离线 `evaluate_selection_policy` 能逐日复现 harness 的选中集合（见脚本内断言）。
    """
    from app.services.feature_builder import market_regime_score
    from app.services.risk_features import attach_ivol20_pct, attach_ivol_dn20_pct
    from app.services.workbench_service import (
        _build_recommendation_card,
        _drop_as_repeat_card,
        _load_calibrated_agg_weights,
        _recent_recommended_codes,
        _same_day_industry_counts,
        _same_day_recommended_codes,
    )

    trade_date = str(snapshot.get("trade_date") or signal_date)
    skill_map = {s["skill_id"]: s["skill"] for s in selected_skills}
    recent_codes = _recent_recommended_codes(db, trade_date)
    same_day_codes = _same_day_recommended_codes(db, trade_date, run_id)
    market_score = market_regime_score(dict(snapshot)) or 50.0
    weights = _load_calibrated_agg_weights(db)
    cards: list[dict] = []
    for entry in candidate_results:
        card = _build_recommendation_card(
            dict(entry),
            entry.get("skill_reviews") or [],
            skill_map=skill_map,
            selected_skills=list(selected_skills),
            market_score=market_score,
            risk_template=str(params.get("risk_template") or "balanced"),
            calibrated_weights=weights,
            snapshot=dict(snapshot),
            recent_codes=recent_codes,
            same_day_codes=same_day_codes,
        )
        if _drop_as_repeat_card(
            card,
            dict(params),
            recent_repeat=bool(card.get("recent_repeat")),
            same_day_repeat=bool(card.get("same_day_repeat")),
        ):
            continue
        cards.append(card)
    if _IVOL_BY_DATE.get(trade_date):
        attach_ivol20_pct(cards, trade_date, _IVOL_BY_DATE[trade_date])
    if _IVOL_DN_BY_DATE.get(trade_date):
        attach_ivol_dn20_pct(cards, trade_date, _IVOL_DN_BY_DATE[trade_date])
    same_day_industry = dict(_same_day_industry_counts(db, trade_date, run_id))
    return cards, same_day_industry, len(same_day_codes)


def _provenance(card: dict) -> dict:
    sigs = [s for s in (card.get("strategy_signals") or []) if isinstance(s, dict) and not s.get("hard_veto")]
    scores = [float(s.get("score", 0) or 0) for s in sigs]
    ids = [str(s.get("strategy_id") or s.get("strategy_name") or "?") for s in sigs]
    dom = ids[scores.index(max(scores))] if scores else "none"
    return {"n_sig": len(sigs), "dominant": dom, "ids": "|".join(sorted(ids))}


def _filtered_entries(entries, skill_ids: set[str], snapshot: dict):
    out = []
    for entry in entries:
        signals = {k: v for k, v in (entry.get("rule_signals") or {}).items() if k in skill_ids}
        out.append(
            {
                "prelim_score": _prelim_rule_score(entry["stock"], signals, dict(snapshot)),
                "context": entry.get("context"),
                "rule_signals": signals,
                "stock": entry["stock"],
            }
        )
    out.sort(key=lambda item: float(item.get("prelim_score") or 0), reverse=True)
    return out


_WORKER: dict = {}
_PCT_CACHE: dict[str, tuple[dict, dict]] = {}
WORKERS: dict = {"n": 1}


def _merge_day_result(
    res: dict,
    trades: dict[str, list],
    prov_rows: list[dict],
    panel_days: list[dict],
    triggered: dict[str, set],
) -> None:
    """把单日结果并入窗口级累积（顺序保持为交易日升序）。"""
    for arm, rows in (res.get("trades") or {}).items():
        trades.setdefault(arm, []).extend(rows)
    prov_rows.extend(res.get("prov") or [])
    if res.get("panel_day") is not None:
        panel_days.append(res["panel_day"])
    for sid, codes in (res.get("triggered") or {}).items():
        triggered.setdefault(sid, set()).update(codes)


def _set_pct_maps(db, trade_date: str) -> None:
    """按日惰性加载 ivol 分位（进程内缓存）并填充 `_panel_pre_policy_cards` 读的全局。

    不整窗预加载：并行 worker 各自预加载 W1 的 115 万行要 ~100MB×N；按日查只多几百毫秒，
    但让每个 worker 的内存占用保持恒定、可安全开多进程。
    """
    cached = _PCT_CACHE.get(trade_date)
    if cached is None:
        from app.services.risk_features import load_ivol20_pct_map, load_ivol_dn20_pct_map

        cached = (
            load_ivol20_pct_map(db, [trade_date]),
            load_ivol_dn20_pct_map(db, [trade_date]),
        )
        _PCT_CACHE[trade_date] = cached
    ivol_map, ivoldn_map = cached
    global _IVOL_BY_DATE, _IVOL_DN_BY_DATE
    _IVOL_BY_DATE = {trade_date: dict(ivol_map)}
    _IVOL_DN_BY_DATE = {trade_date: dict(ivoldn_map)}


def _worker_setup(
    window: str,
    arms: list[str],
    panel_dir: str | None,
    policy_note: str,
    policy_version: int | None = None,
    skill_weight_mode: str | None = None,
    skill_weight_map: dict | None = None,
    ep=None,
    gate_dates=None,
    gate_version: int | None = None,
) -> None:
    """每个进程（含主进程）各自准备 builder + session；worker 之间不共享任何对象。"""
    # spawn 模式子进程会重新 import 模块，模块级 GATE 不会自动带过去，必须显式传参。
    if isinstance(gate_dates, dict):
        GATE["map"] = {str(k): int(v) for k, v in gate_dates.items()}
    else:
        GATE["map"] = {str(x): int(gate_version) for x in (gate_dates or ())} if gate_version is not None else {}
    GATE["version"] = gate_version
    from app.services.factors.registry import bootstrap_default_factor_registry

    # 显式版本：把模块级默认换成该 artifact（aggregate 内部读的就是这个常量）。
    # 用于在不改生产默认的前提下验证候选版本（例如 v7 = v6 + strong_climax_top_n=2）。
    if policy_version is not None:
        import app.engine.production_policy as _pp

        _pp.DEFAULT_PRODUCTION_SELECTION_POLICY = _pp.load_production_selection_policy(
            version=int(policy_version)
        )
    bootstrap_default_factor_registry()
    db = SessionLocal()
    event.listen(db.connection(), "before_cursor_execute", _reject_write_statement)
    builder = ProductionRuleReplayCandidateBuilder(
        db,
        stock_metadata_provider=StockMetadataHistoryProvider(db),
        historical_replay=(UNIVERSE["mode"] == "historical"),
    )
    selected_all = list(builder._selected_skills(None))  # noqa: SLF001
    # 逐技能信号权重：生产 LIVE 走 `_build_recommendation_card` 的
    # `signal["weight"] = selected["weight"]`（即 preset_skills 权重），而本重放器
    # `_selected_skills` 默认给 1.0（等权）——两者差异会改变 strategy_rule 与最终排序。
    # 默认保持等权（历史可比），需要生产口径时传 --skill-weights preset。
    mode = skill_weight_mode or SKILL_WEIGHTS.get("mode") or "equal"
    weight_map: dict[str, float] | None = None
    if mode == "preset":
        from sqlalchemy import text as _text

        rows = db.execute(
            _text("SELECT skill_id, weight FROM preset_skills WHERE preset_id = 'preset_formal_opening'")
        ).all()
        weight_map = {str(r[0]): float(r[1]) for r in rows}
    elif mode == "json":
        weight_map = {str(k): float(v) for k, v in (skill_weight_map or SKILL_WEIGHTS.get("map") or {}).items()}
    if weight_map:
        selected_all = [
            {**s, "weight": float(weight_map.get(str(s["skill_id"]), s.get("weight", 1.0)))}
            for s in selected_all
        ]
    _WORKER.update(
        db=db,
        builder=builder,
        selected_all=selected_all,
        all_ids=sorted(str(s["skill_id"]) for s in selected_all),
        arms=arms,
        window=window,
        panel_dir=panel_dir,
        policy_note=policy_note,
        ep=ep if ep is not None else EP_OVERRIDE.get("policy"),
    )


def _worker_init(
    window: str,
    arms: list[str],
    panel_dir: str | None,
    policy_note: str,
    policy_version: int | None = None,
    skill_weight_mode: str | None = None,
    skill_weight_map: dict | None = None,
    ep=None,
    gate_dates=None,
    gate_version: int | None = None,
) -> None:
    _worker_setup(window, arms, panel_dir, policy_note, policy_version,
                  skill_weight_mode, skill_weight_map, ep, gate_dates, gate_version)


def _process_day(signal_date: str) -> dict:
    """处理一个交易日（交易日之间相互独立，故可多进程并行）。

    返回 {trades, prov, panel_day, triggered}；所有 DB/对象都在本进程内，跨进程只传结果。
    """
    # 逐日政策切换（自适应闸门）：命中 GATE 日期的交易日改用另一版政策 artifact。
    # 每个 worker 顺序处理若干天、单线程，因此直接换模块级默认政策是安全的。
    if GATE["map"]:
        import app.engine.production_policy as _pp

        _want = GATE["map"].get(str(signal_date), GATE.get("version"))
        _cur = _pp.DEFAULT_PRODUCTION_SELECTION_POLICY.version
        _target = None
        if _want is None:
            # 未命中日期回落到默认（表外=默认）；默认由 --policy-version 或 v7 决定
            _fallback = int(POLICY_VERSION["n"]) if POLICY_VERSION.get("n") is not None else 7
            if _cur != _fallback:
                _target = _pp.load_production_selection_policy(version=_fallback)
        elif _cur != int(_want):
            _target = _pp.load_production_selection_policy(version=int(_want))
        if _target is not None:
            _pp.DEFAULT_PRODUCTION_SELECTION_POLICY = _target
    db = _WORKER["db"]
    builder = _WORKER["builder"]
    selected_all = _WORKER["selected_all"]
    all_ids = _WORKER["all_ids"]
    arms = _WORKER["arms"]
    settings = get_settings()
    ep = _WORKER.get("ep") or DEFAULT_EXECUTION_POLICY
    from app.services.backtest.historical_universe import load_historical_universe

    _set_pct_maps(db, signal_date)
    result: dict = {
        "signal_date": signal_date,
        "trades": {a: [] for a in arms},
        "prov": [],
        "panel_day": None,
        "triggered": {},
    }
    triggered: dict[str, set] = {}
    stocks, snapshot = load_historical_universe(
        db, signal_date, stock_metadata_provider=builder._stock_metadata_provider  # noqa: SLF001
    )
    stocks = [dict(s) for s in stocks]
    snapshot = dict(snapshot)
    snapshot["trade_date"] = str(snapshot.get("trade_date") or signal_date)
    snapshot["strategy_versions"] = {
        str(item["skill_id"]): str(item["version_id"]) for item in selected_all
    }
    prelim_entries: list = []
    scoped = builder._scope.apply_scope(stocks, PARAMS)  # noqa: SLF001
    prefiltered, audit = builder._scope.apply_prefilter(  # noqa: SLF001
        scoped, selected_all, PARAMS, snapshot
    )
    if prefiltered:
        _features, contexts = builder._features.build(prefiltered, snapshot)  # noqa: SLF001
        prelim_entries, _cand, _g, _p = builder._strategy.evaluate(  # noqa: SLF001
            research_contexts=contexts,
            selected_skills=selected_all,
            snapshot=snapshot,
            params=PARAMS,
            prefilter_audit=audit,
        )
        prelim_entries = list(prelim_entries)
        for entry in prelim_entries:
            for sid, sig in (entry.get("rule_signals") or {}).items():
                if sig:
                    triggered.setdefault(sid, set()).add(str(entry["stock"].get("code") or ""))

    cards_by_arm: dict[str, list] = {}
    codes_union: set[str] = set()
    panel_pre_cards: list = []
    panel_pending: dict | None = None
    for arm in arms:
        if arm == "__all__":
            skill_ids = set(all_ids)
        else:
            skill_ids = set(all_ids) - {arm}
        skills_arm = [s for s in selected_all if str(s["skill_id"]) in skill_ids]
        entries_arm = _filtered_entries(prelim_entries, skill_ids, snapshot) if prelim_entries else []
        gate = (
            build_skill_rule_gate(entries_arm, skills_arm, settings)
            if entries_arm
            else {"skill_candidates": []}
        )
        candidate_results = tuple(
            {
                **dict(entry),
                "review_skill_ids": [],
                "agent_review_disabled": True,
                "skill_reviews": [],
            }
            for entry in gate.get("skill_candidates") or []
        )
        assembly = ProductionRecommendationAssembly(db, f"backtest-rule-replay-{signal_date}")
        formal_cards, scored_cards, _updates = assembly.aggregate(
            candidate_results=candidate_results,
            selected_skills=skills_arm,
            snapshot=snapshot,
            params=PARAMS,
        )
        full_by_code = {str(c.get("code")): c for c in scored_cards}
        cards_by_arm[arm] = [full_by_code.get(str(c.get("code")), dict(c)) for c in formal_cards]
        codes_union |= {str(c.get("code")) for c in cards_by_arm[arm]}

        # 面板只在全臂导出，且导出的是 policy 的**输入态**（见 _panel_pre_policy_cards）。
        if arm == "__all__" and PANEL.get("dir"):
            import copy as _copy

            _run_id = f"backtest-rule-replay-{signal_date}"
            pre_cards, same_day_industry, same_day_count = _panel_pre_policy_cards(
                db,
                _copy.deepcopy(list(candidate_results)),
                skills_arm,
                snapshot,
                PARAMS,
                signal_date,
                _run_id,
            )
            panel_pre_cards = pre_cards
            panel_pending = {
                "signal_date": signal_date,
                "market_snapshot": dict(snapshot),
                "same_day_industry_counts": same_day_industry,
                "same_day_recommendation_count": same_day_count,
                "cards": [_panel_card(dict(c)) for c in pre_cards],
                "selected": [str(c.get("code")) for c in formal_cards],
                # 每张候选卡的因果净收益：使离线规则筛查可以评估"换票"后的组合结果，
                # 而不只是评估"同一批票"（这是面板能替代重放做入场侧筛查的前提）。
                "net_by_code": {},
                "exit_by_code": {},
            }
            # 候选卡全部需要 K 线：并入 codes_union（否则面板拿到的是空 bars）
            codes_union |= {str(c.get("code")) for c in pre_cards}

    bars = builder._outcome_bars(signal_date, sorted(codes_union), ep)  # noqa: SLF001

    class _Day:
        pass

    day = _Day()
    day.signal_date = signal_date
    day.outcome_bars = bars
    day.market_snapshot = snapshot
    for arm, cards in cards_by_arm.items():
        for card in cards:
            tr = (_simulate_card(day, card, ep) or {}).get("trade")
            if not (tr and tr.executed):
                continue
            result["trades"][arm].append(
                {
                    "dt": signal_date,
                    "code": str(card.get("code")),
                    "net": float(tr.net_return_pct),
                    "exit": str(getattr(tr, "exit_reason", "")),
                }
            )
            if arm == "__all__":
                row = {"dt": signal_date, "code": str(card.get("code")),
                       "net": float(tr.net_return_pct), "exit": str(getattr(tr, "exit_reason", ""))}
                row.update(_provenance(card))
                result["prov"].append(row)
    if panel_pre_cards:
        for card in panel_pre_cards:
            code = str(card.get("code"))
            tr = (_simulate_card(day, card, ep) or {}).get("trade")
            if tr and tr.executed:
                panel_pending["net_by_code"][code] = float(tr.net_return_pct)
                panel_pending["exit_by_code"][code] = str(getattr(tr, "exit_reason", ""))
            else:
                panel_pending["net_by_code"][code] = None
                panel_pending["exit_by_code"][code] = None
        result["panel_day"] = panel_pending
    result["triggered"] = {sid: sorted(codes) for sid, codes in triggered.items()}
    return result


def run_window(db, window: str, targets: list[str], policy_note: str) -> dict:
    start, end = WINDOWS[window]
    # 必须用注册表 current（= 已发布的出场策略 v4）。ExecutionPolicy() 是历史 v1 默认值
    # （止盈 6% 开、止损 4.5%、trailing 2.0%），与 published 出场策略不一致。
    ep = DEFAULT_EXECUTION_POLICY
    settings = get_settings()
    builder = ProductionRuleReplayCandidateBuilder(
        db,
        stock_metadata_provider=StockMetadataHistoryProvider(db),
        historical_replay=(UNIVERSE["mode"] == "historical"),
    )
    selected_all = list(builder._selected_skills(None))  # noqa: SLF001
    all_ids = sorted(str(s["skill_id"]) for s in selected_all)
    arms = ["__all__"] + [t for t in targets if t in all_ids]
    trade_dates = [
        str(v)
        for v in db.scalars(
            select(DailyKbar.trade_date)
            .where(DailyKbar.trade_date >= start, DailyKbar.trade_date <= end)
            .distinct()
            .order_by(DailyKbar.trade_date)
        ).all()
    ]
    print(f"\n===== {window} {start}~{end} 交易日{len(trade_dates)} 启用skill{len(all_ids)} "
          f"臂{len(arms)} ({policy_note}) =====", flush=True)
    trades: dict[str, list] = {a: [] for a in arms}
    prov_rows: list[dict] = []
    panel_days: list[dict] = []
    triggered = defaultdict(set)
    t0 = time.time()
    # 交易日之间相互独立 → 逐日并行（--workers N）。每个进程各自持有一份 builder/session，
    # 跨进程只回传结果字典；worker 初始化失败会直接抛出，不做静默串行回退。
    workers = max(1, int(WORKERS["n"]))
    panel_dir = PANEL.get("dir")
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        print(f"[parallel] {workers} 个进程逐日并行（交易日互相独立，结果按日回传）", flush=True)
        done = 0
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(window, arms, panel_dir, policy_note, POLICY_VERSION["n"],
                      SKILL_WEIGHTS.get("mode"), SKILL_WEIGHTS.get("map"),
                      EP_OVERRIDE.get("policy"), dict(GATE["map"]), GATE["version"]),
        ) as pool:
            for res in pool.map(_process_day, trade_dates, chunksize=1):
                done += 1
                _merge_day_result(res, trades, prov_rows, panel_days, triggered)
                if done % 10 == 0 or done == len(trade_dates):
                    el = time.time() - t0
                    print(f"  [{window}] {done}/{len(trade_dates)} 天 {el/60:.1f}min "
                          f"A={len(trades['__all__'])}笔", flush=True)
    else:
        _worker_setup(window, arms, panel_dir, policy_note, POLICY_VERSION["n"],
                      SKILL_WEIGHTS.get("mode"), SKILL_WEIGHTS.get("map"),
                      EP_OVERRIDE.get("policy"), dict(GATE["map"]), GATE["version"])
        for di, signal_date in enumerate(trade_dates, 1):
            _merge_day_result(_process_day(signal_date), trades, prov_rows, panel_days, triggered)
            if di % 10 == 0 or di == len(trade_dates):
                el = time.time() - t0
                print(f"  [{window}] {di}/{len(trade_dates)} 天 {el/60:.1f}min "
                      f"A={len(trades['__all__'])}笔", flush=True)

    print(f"[{window}] 出票日/触发skill覆盖：", flush=True)
    for sid in all_ids:
        print(f"    {sid:36s} 触发股票数(全历史累计)={len(triggered[sid])}", flush=True)

    def metrics(rows):
        df = pd.DataFrame(rows)
        if df.empty:
            return None
        net = df.net
        daily = df.groupby("dt").net.mean()
        wins, losses = net[net > 0], net[net < 0]
        return {
            "n": int(len(df)),
            "wr": float((net > 0).mean()),
            "avg": float(net.mean()),
            "sum": float(net.sum()),
            "pf": float(wins.sum() / max(abs(losses.sum()), 1e-9)),
            "mdd": float((daily.cumsum() - daily.cumsum().cummax()).min()),
            "cohort": float(daily.mean()),
            "days": int(df.dt.nunique()),
        }

    def boot_ci(delta: np.ndarray, draws=4000, seed=7):
        rng = np.random.default_rng(seed)
        n = len(delta)
        means = np.array([delta[rng.integers(0, n, n)].mean() for _ in range(draws)])
        return float(delta.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))

    res = {"window": window, "start": start, "end": end, "A": metrics(trades["__all__"]),
           "triggered_codes": {k: len(v) for k, v in triggered.items()}, "arms": {}}
    df_a = pd.DataFrame(trades["__all__"])
    days_idx = sorted(set(df_a.dt)) if not df_a.empty else []
    da = df_a.groupby("dt").net.mean().reindex(days_idx).fillna(0.0) if days_idx else pd.Series(dtype=float)
    for t in arms:
        if t == "__all__":
            continue
        df_b = pd.DataFrame(trades[t])
        db_ = (df_b.groupby("dt").net.mean() if not df_b.empty else pd.Series(dtype=float))
        db_ = db_.reindex(days_idx).fillna(0.0)
        dlt = (db_ - da)
        md, lo, hi = boot_ci(dlt.values)
        monthly = dlt.groupby(dlt.index.str[:7]).mean() if len(dlt) else pd.Series(dtype=float)
        pos, neg = int((monthly > 0).sum()), int((monthly < 0).sum())
        ka = set(zip(df_a.dt, df_a.code))
        kb = set(zip(df_b.dt, df_b.code)) if not df_b.empty else set()
        oa = df_a[[(d, c) in ka - kb for d, c in zip(df_a.dt, df_a.code)]] if not df_a.empty else pd.DataFrame()
        ob = df_b[[(d, c) in kb - ka for d, c in zip(df_b.dt, df_b.code)]] if not df_b.empty else pd.DataFrame()
        rec = {
            "B": metrics(trades[t]),
            "delta_cohort": md, "ci_lo": lo, "ci_hi": hi,
            "days_pos": int((dlt > 0).sum()), "days_neg": int((dlt < 0).sum()),
            "monthly_pos_ratio": pos / max(pos + neg, 1),
            "monthly": {k: round(float(v), 4) for k, v in monthly.items()},
            "only_A_cards": int(len(oa)), "only_A_avg": float(oa.net.mean()) if len(oa) else None,
            "repl_B_cards": int(len(ob)), "repl_B_avg": float(ob.net.mean()) if len(ob) else None,
        }
        res["arms"][t] = rec
        print(f"  >> 剔除 {t:34s} Δ{md:+.4f} CI[{lo:+.4f},{hi:+.4f}] "
              f"日+{rec['days_pos']}/-{rec['days_neg']} 月同号{rec['monthly_pos_ratio']:.2f} "
              f"独持{rec['only_A_cards']}张均{rec['only_A_avg']} 递补{rec['repl_B_cards']}张均{rec['repl_B_avg']} "
              f"| B均{rec['B']['avg'] if rec['B'] else None}", flush=True)
    if prov_rows:
        res["provenance"] = prov_rows
    if panel_days and PANEL.get("dir"):
        panel_dir = Path(PANEL["dir"])
        panel_dir.mkdir(parents=True, exist_ok=True)
        panel_path = panel_dir / f"entry_panel_{window}.json"
        panel_path.write_text(
            json.dumps(
                {"window": window, "start": start, "end": end,
                 "policy_note": policy_note, "days": panel_days},
                ensure_ascii=False,
            )
        )
        print(f"[panel] {window}: {len(panel_days)} 天 → {panel_path} "
              f"({panel_path.stat().st_size / 1e6:.1f}MB)", flush=True)
        _assert_panel_fidelity(panel_path, window)
    print(f"[{window}] A臂 n={res['A']['n']} WR={res['A']['wr']*100:.1f}% avg={res['A']['avg']:+.4f} "
          f"PF={res['A']['pf']:.3f} sum={res['A']['sum']:+.2f} MDD={res['A']['mdd']:+.2f}", flush=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="W1,W2,W3")
    ap.add_argument("--targets", default="all")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--panel-dir",
        default=None,
        help="导出三窗入场面板（每个交易日的 pre-policy 卡 + 当日 market_snapshot），"
             "供离线规则筛查复用；配合 --targets none 可只跑 A 臂（最快）",
    )
    ap.add_argument(
        "--universe",
        choices=("historical", "local_pool"),
        default="historical",
        help="historical=从 daily_kbars 重建（L-110 默认，可覆盖全历史）；"
             "local_pool=与生产同源的实时报价池（stock_quotes，仅覆盖 2026-05-22 之后）",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="逐日并行进程数（交易日互相独立）。推荐 6：W1 串行约 3h，6 进程约 25-35min；"
             "1 = 串行（历史行为，结果与并行逐位一致）",
    )
    ap.add_argument(
        "--policy-version",
        type=int,
        default=None,
        help="显式用某个 production_selection 版本跑（默认用当前 default）。用于在不改默认的前提下"
             "验证候选 artifact（如 v7 = v6 + strong_climax_top_n=2）。",
    )
    ap.add_argument(
        "--skill-weights",
        default="equal",
        help="equal（默认，全部 1.0）| preset（读 preset_formal_opening 的 preset_skills 权重，"
             "与生产 LIVE 的 signal.weight 口径一致）| JSON 字符串（如 '{\"daily-stock-analysis-skill\":0.5}'）",
    )
    ap.add_argument(
        "--tp-pct",
        type=float,
        default=None,
        help="覆盖出场策略：打开止盈并设默认止盈百分比（如 4 = +4%% 止盈）。"
             "WR 是目前唯一未达标的轴，止盈是最直接的胜率杠杆；默认 None = 用已发布 artifact。",
    )
    ap.add_argument(
        "--trail-act",
        type=float,
        default=None,
        help="覆盖出场策略：trailing 激活阈值（%%，越小越早锁利 → 胜率杠杆）",
    )
    ap.add_argument(
        "--trail-lock",
        type=float,
        default=None,
        help="覆盖出场策略：trailing 最小锁利（%%），把'曾浮盈'的单子变成小赢单",
    )
    ap.add_argument(
        "--trail-callback",
        type=float,
        default=None,
        help="覆盖出场策略：trailing 回撤触发幅度（%%，越大越晚离场 → 让单子跑到 horizon）",
    )
    ap.add_argument(
        "--horizon-days",
        type=int,
        default=None,
        help="覆盖出场策略：持有上限天数（默认 5）",
    )
    ap.add_argument(
        "--entry-days",
        type=int,
        default=None,
        help="覆盖入场策略：允许挂单成交的最大交易日数（默认 2）。1 = 只在信号次日成交，"
             "用来检验「延迟一日的二次确认是否在筛选质量」。",
    )
    ap.add_argument(
        "--gate-file",
        default=None,
        help="逐日政策切换：JSON 文件，内容为日期列表（YYYY-MM-DD），这些交易日改用 "
             "--gate-policy-version 指定的政策，其余交易日沿用默认/--policy-version。"
             "用于验证「用已结算的近 N 日表现自适应开关某条规则」。",
    )
    ap.add_argument(
        "--gate-policy-version",
        type=int,
        default=None,
        help="与 --gate-file 配套：命中日期使用的 production_selection 版本号。",
    )
    ap.add_argument(
        "--max-entry-gap",
        type=float,
        default=None,
        help="覆盖入场策略：成交价高于推荐价超过该百分比时放弃入场（默认 6.0）。"
             "这是纯入场侧、决策时可知的闸门：越小越不追高（0 = 只在推荐价及以下成交）。",
    )
    ap.add_argument(
        "--sl-pct",
        type=float,
        default=None,
        help="覆盖出场策略：止损宽度（同时抬 maximum_loss_pct），默认 None = 用已发布 artifact。",
    )
    ap.add_argument("--policy-note", default=None)
    args = ap.parse_args()
    if args.policy_note is None:
        from app.engine.production_policy import DEFAULT_PRODUCTION_SELECTION_POLICY
        from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY

        args.policy_note = (
            f"production_selection v{DEFAULT_PRODUCTION_SELECTION_POLICY.version}"
            f" + execution policy v{DEFAULT_EXECUTION_POLICY.version}"
        )
    out_path = Path(args.out) if args.out else OUT / "ablate_fast.json"
    PANEL["dir"] = args.panel_dir
    UNIVERSE["mode"] = args.universe
    POLICY_VERSION["n"] = args.policy_version
    if args.gate_file:
        _gd = json.loads(Path(args.gate_file).read_text())
        _dates = _gd.get("dates") if isinstance(_gd, dict) else _gd
        if isinstance(_dates, dict):
            GATE["map"] = {str(k): int(v) for k, v in _dates.items()}
        else:
            GATE["map"] = (
                {str(x): int(args.gate_policy_version) for x in (_dates or [])}
                if args.gate_policy_version is not None else {}
            )
        GATE["version"] = args.gate_policy_version
        from collections import Counter as _C
        print(f"[gate] 逐日切换 {len(GATE['map'])} 天 → {dict(_C(GATE['map'].values()))}", flush=True)
    if (args.tp_pct is not None or args.sl_pct is not None
            or args.trail_act is not None or args.trail_lock is not None
            or args.trail_callback is not None or args.horizon_days is not None
            or args.entry_days is not None or args.max_entry_gap is not None):
        import dataclasses as _dc

        ep = DEFAULT_EXECUTION_POLICY
        if args.tp_pct is not None:
            ep = _dc.replace(ep, take_profit=_dc.replace(
                ep.take_profit, enabled=True, default_gain_pct=float(args.tp_pct)))
        if args.trail_act is not None:
            ep = _dc.replace(ep, trailing=_dc.replace(
                ep.trailing, activation_gain_pct=float(args.trail_act)))
        if args.trail_lock is not None:
            ep = _dc.replace(ep, trailing=_dc.replace(
                ep.trailing, minimum_lock_gain_pct=float(args.trail_lock)))
        if args.trail_callback is not None:
            ep = _dc.replace(ep, trailing=_dc.replace(
                ep.trailing, callback_pct=float(args.trail_callback)))
        if args.horizon_days is not None:
            ep = _dc.replace(ep, horizon_days=int(args.horizon_days))
        if args.entry_days is not None:
            ep = _dc.replace(ep, entry=_dc.replace(
                ep.entry, max_entry_trade_days=int(args.entry_days)))
        if args.max_entry_gap is not None:
            ep = _dc.replace(ep, gap_risk=_dc.replace(
                ep.gap_risk, max_entry_gap_up_pct=float(args.max_entry_gap)))
        if args.sl_pct is not None:
            ep = _dc.replace(ep, stop_loss=_dc.replace(
                ep.stop_loss, default_loss_pct=float(args.sl_pct),
                maximum_loss_pct=max(float(args.sl_pct) + 0.2, ep.stop_loss.maximum_loss_pct)))
        EP_OVERRIDE["policy"] = ep
        print(f"[ep] 出场变体: tp={args.tp_pct} sl={args.sl_pct} "
              f"trail_act={args.trail_act} trail_lock={args.trail_lock} "
              f"trail_cb={args.trail_callback} horizon={args.horizon_days} "
              f"entry_days={args.entry_days} max_entry_gap={args.max_entry_gap}", flush=True)
    _sw = str(args.skill_weights or "equal").strip()
    if _sw in ("equal", "preset"):
        SKILL_WEIGHTS["mode"] = _sw
        SKILL_WEIGHTS["map"] = None
    else:
        import json as _json

        SKILL_WEIGHTS["mode"] = "json"
        SKILL_WEIGHTS["map"] = _json.loads(_sw)
    WORKERS["n"] = max(1, int(args.workers))
    print(f"[workers] {WORKERS['n']}", flush=True)
    print(f"[universe] {UNIVERSE['mode']}", flush=True)
    if args.panel_dir:
        print(f"[panel] 导出目录 {args.panel_dir}（targets={args.targets}）", flush=True)
    from app.services.factors.registry import bootstrap_default_factor_registry

    bootstrap_default_factor_registry()
    db = SessionLocal()
    connection = db.connection()
    event.listen(connection, "before_cursor_execute", _reject_write_statement)
    results = {}
    try:
        for window in [w.strip() for w in args.windows.split(",") if w.strip()]:
            builder_targets: list[str] = []
            if args.targets == "all":
                selected_all = list(
                    ProductionRuleReplayCandidateBuilder(
                        db, stock_metadata_provider=StockMetadataHistoryProvider(db), historical_replay=True
                    )._selected_skills(None)  # noqa: SLF001
                )
                builder_targets = sorted(str(s["skill_id"]) for s in selected_all)
            elif args.targets.strip() == "none":
                builder_targets = []
            else:
                builder_targets = [t.strip() for t in args.targets.split(",") if t.strip()]
            res = run_window(db, window, builder_targets, args.policy_note)
            results[window] = res
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1))
            print(f"[saved] {out_path}", flush=True)
    finally:
        db.close()
    print("ABLATE_FAST_DONE", flush=True)


if __name__ == "__main__":
    main()
