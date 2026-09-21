from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import yaml
from sqlalchemy import and_, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.engine.aggregator import aggregate
from app.engine.skill_loader import StrategySkill
from app.engine.strategy_runner import run_strategy
from app.models.tables import DailyKbar, StkLimit, StockMetadataHistory
from app.services.entry_policy import entry_policy_for_mode
from app.services.feature_builder import market_regime_score
from app.services.outcome_simulation import _simulate_outcome
from scripts.research_dragon_tiger_reaccumulation import metadata_on, paired_ci
from scripts.research_entry_mode_three_window import _decision, _feature_snapshot, _metrics, _recommendation

CONFIG_PATH = Path(__file__).with_name("limit_up_benchmark.yaml")
WINDOWS = {"W1": ("2025-01-15", "2026-01-16"), "W2": ("2026-01-17", "2026-07-30"), "W3": ("2026-08-01", "2026-09-18")}
FIELDS = ("code", "trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate", "ma5", "ma10", "ma20", "ma60")


def load_frame(start="2025-01-01", end="2026-09-18", codes=None):
    query = select(*[getattr(DailyKbar, key) for key in FIELDS], StkLimit.up_limit, StkLimit.down_limit).outerjoin(
        StkLimit, and_(DailyKbar.code == StkLimit.code, DailyKbar.trade_date == StkLimit.trade_date),
    ).where(DailyKbar.trade_date >= start, DailyKbar.trade_date <= end)
    if codes is not None:
        query = query.where(DailyKbar.code.in_(codes))
    with SessionLocal() as db:
        frame = pd.read_sql(query, db.connection())
        history = list(db.scalars(select(StockMetadataHistory).where(
            StockMetadataHistory.effective_from <= end,
            (StockMetadataHistory.effective_to.is_(None)) | (StockMetadataHistory.effective_to >= start),
        )))
    grouped = defaultdict(list)
    for row in history:
        grouped[row.code].append(row)
    return frame.sort_values(["code", "trade_date"]).reset_index(drop=True), grouped


def signal_features(frame, calendar):
    frame = frame.copy()
    if frame.duplicated(["code", "trade_date"]).any():
        raise ValueError("duplicate daily records")
    sequence = {day: index for index, day in enumerate(calendar)}
    frame["day_index"] = frame.trade_date.map(sequence)
    frame["valid"] = (
        frame[["open", "high", "low", "close", "volume", "up_limit", "down_limit"]].notna().all(axis=1)
        & (frame[["open", "high", "low", "close", "volume", "down_limit"]] > 0).all(axis=1)
        & (frame.up_limit > frame.down_limit)
        & (frame.high <= frame.up_limit + 0.005)
        & (frame.low >= frame.down_limit - 0.005)
    )
    frame["sealed"] = frame.valid & ((frame.close - frame.up_limit).abs() < 0.005)
    frame["touched"] = frame.valid & ((frame.high - frame.up_limit).abs() < 0.005)
    groups = frame.groupby("code", sort=False)
    previous_valid = groups.valid.shift(1).eq(True) & groups.day_index.shift(1).eq(frame.day_index - 1)
    previous_sealed = groups.sealed.shift(1).eq(True)
    frame["first_board"] = frame.valid & previous_valid & frame.sealed & ~previous_sealed
    frame["failed_touch"] = frame.valid & frame.touched & ~frame.sealed
    frame["first_pullback"] = (
        frame.valid & previous_valid & previous_sealed & ~frame.touched
        & (frame.close < frame.open) & (frame.close < groups.close.shift(1))
    )
    return frame


def attach_labels(frame):
    frame = frame.copy()
    groups = frame.groupby("code", sort=False)
    known = groups.valid.shift(-1).eq(True) & groups.day_index.shift(-1).eq(frame.day_index + 1)
    frame["next_touch"] = groups.touched.shift(-1).where(known)
    frame["next_sealed"] = groups.sealed.shift(-1).where(known)
    return frame


def research_skill(strategy_id, config):
    name = config["strategies"][strategy_id]["name"]
    contract = {key: config[key] for key in ("default_entry_mode", "scoring", "setups")}
    contract.update({"enabled": False, "require_setup": True})
    return StrategySkill(
        skill_id=strategy_id, name=name, version="research-20260921", path=CONFIG_PATH.parent,
        manifest={"mode": "declarative", "status": "disabled", "capabilities": [], "requirements": []},
        skill_frontmatter={}, skill_body="", skill_hash=None, reference_files=[],
        contract=contract, contract_hash=hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest(), enabled=False,
    )


def make_card(row, metadata, snapshot, skill):
    stock = {key: value for key, value in row.items() if key in FIELDS and pd.notna(value)}
    stock.update(metadata)
    stock.update({
        "price": row["close"], "pct": row["pct_chg"], "amount_yi": row["amount"] / 1e8,
        "exchange": metadata.get("market"), "is_limit_up": bool(row["sealed"]),
        "market_sample_count": snapshot["sample_count"],
        "data_quality": {"missing_fields": ["news", "announcements", "auction", "capital_flow", "seat_identity"]},
    })
    signal = run_strategy(skill, stock, [])
    if signal["hard_veto"]:
        raise ValueError(f"declared benchmark rejected input: {row['code']}:{row['trade_date']}:{signal['veto_reason']}")
    card = aggregate(stock, [signal], market_regime_score(snapshot), 0.0, market_snapshot=snapshot)
    card["bucket"] = skill.skill_id
    card["score_line_meta"] = {"decision_regime": card["market_regime"]}
    return card


def simulate(row, bars, calendar, config):
    card = row["card"]
    snapshot = _feature_snapshot(card)
    # 执行器的日期列表与股票日线逐项对应；停牌期间不移动信号日期。
    dates = [bar.trade_date for bar in bars]
    output = _simulate_outcome(
        _recommendation(row), bars, dates, config["horizon_days"],
        feature_snapshot=snapshot, entry_policy=entry_policy_for_mode(config["default_entry_mode"]),
    )
    record = {key: row[key] for key in ("code", "signal_date", "next_touch", "next_sealed")}
    record.update({"selected": True, "final_score": card["final_score"], "decision_regime": card["score_line_meta"]["decision_regime"]})
    if output is None:
        record.update({"settled": False, "executed": False, "net_return_pct": None, "position_size": 0.0, "exit_reason": "missing_outcome_bars"})
        return record
    record.update({key: output.get(key) for key in (
        "net_return_pct", "position_size", "entry_date", "entry_price", "exit_date", "exit_price", "exit_reason", "entry_policy_id",
    )})
    record["settled"] = output["outcome_label"] in {"success", "failed"} and output.get("net_return_pct") is not None
    record["executed"] = bool(output.get("entry_triggered"))
    return record


def label_metrics(records, days):
    result = {}
    rng = np.random.default_rng(20260921)
    for field in ("next_touch", "next_sealed"):
        observed = [row for row in records if row[field] is not None]
        daily = np.array([[sum(row[field] for row in observed if row["signal_date"] == day), sum(row["signal_date"] == day for row in observed)] for day in days], dtype=float)
        sampled = daily[rng.integers(0, len(days), size=(4000, len(days)))].sum(axis=1)
        ratios = sampled[sampled[:, 1] > 0, 0] / sampled[sampled[:, 1] > 0, 1] * 100
        result[field] = {
            "n": len(observed), "hits": sum(row[field] for row in observed), "unknown": len(records) - len(observed),
            "rate_pct": 100 * sum(row[field] for row in observed) / len(observed) if observed else None,
            "day_cluster_ci95_pct": np.quantile(ratios, [0.025, 0.975]).tolist() if len(ratios) else None,
        }
    return result


def summarize(records, days):
    result = _metrics(records) if any(row["settled"] for row in records) else {"n": 0}
    result["accuracy"] = label_metrics(records, days)
    result["active_day_coverage_pct"] = 100 * len({row["signal_date"] for row in records if row["settled"]}) / len(days)
    result["exit_reasons"] = dict(Counter(row["exit_reason"] for row in records))
    return result


def write_report(output, path):
    lines = [
        "# 涨停形态三窗回测 · 2026-09-21", "",
        "三个固定规则均为 FALSIFIED，未加入选股预设，未修改生产策略。", "",
        "使用全市场历史日线、当日官方涨停价和历史股票属性。T 日收盘形成信号，每日按 aggregate 的 final_score 选取最多 6 只，分数相同按股票代码排序。研究信号统一基础分为 70，未训练排序模型。",
        "",
        "入场方式由研究声明统一指定 next_open，成交与退出调用生产 _simulate_outcome，仓位调用 _resolve_position_size，包含 T+1 卖出限制及现行费用。最长观察 5 根股票日线。未完成观察的交易不计入胜负。",
        "",
        "价格形态独立研究未执行生产全部技能、选股预设和账户资金调度，不能作为生产发布证据。缺少逐笔委托、排队和分钟成交证据，日线模拟具有成交时序限制。",
        "",
        "来源：[BigQuant 反包策略公开讨论](https://bigquant.com/wiki/doc/YmW7dvk3Ae)。该文讨论涨停回调、首阴与高频买卖力量特征。本次规则是独立明确化的形态检验，未复现原文模型或收益声明。",
        "",
        "## 固定规则", "",
    ]
    for key, value in output["rules"]["strategies"].items():
        lines.extend([f"- {value['name']}：{value['rule']} 假设：{value['hypothesis']}"])
    lines.extend(["", "## 指标定义", "",
        "n 为已结算成交笔数，WR 为净收益大于零的笔数占比，PF 为盈利总额除以亏损总额的绝对值。Σ 为单笔净收益百分数之和，不能理解为账户收益率。",
        "",
        "回撤列为信号日平均净收益累计曲线的最大回撤，单位为百分点，累计方式允许超过 100；账户净值回撤尚未计算。覆盖率为已结算成交数除以选中信号数；资金列为独立信号的平均分配仓位，账户并发资金利用率尚未计算。",
        "",
        "配对 bootstrap 按相同信号日期抽样，比较各规则与首板接力的每日平均净收益，4000 次，95% 区间；无信号日期按零处理。首板作为参照，其相对自身差值为零。涨停准确度另按日期整体重采样，分母包含全部次日数据可得的选中信号，不因无法成交而移除。",
    ])
    for window, result in output["windows"].items():
        lines.extend(["", f"## {window} · {' ~ '.join(result['date_range'])} · {result['trade_days']} 个交易日", "",
            "| 规则 | n | WR % | 平均净收益 % | Σ 百分点 | PF | 回撤 百分点 | 平均盈利 % | 平均亏损 % | 覆盖率 % | 分配仓位 % | 配对差值 95% 区间 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ])
        for key, row in result["strategies"].items():
            m = row["metrics"]
            fields = ["win_rate_pct", "average_net_return_pct", "cumulative_net_return_pct", "profit_factor", "max_drawdown_pct", "average_win_pct", "average_loss_pct", "coverage_pct", "capital_utilization_pct"]
            ci = row["paired_day_bootstrap"]["ci95_pct"]
            lines.append(f"| {output['rules']['strategies'][key]['name']} | {m['n']} | " + " | ".join(f"{m[field]:.2f}" for field in fields) + f" | [{ci[0]:.3f}, {ci[1]:.3f}] |")
        lines.extend(["", "| 规则 | 触及涨停次数 / 可判断信号 | 触及涨停率 % | 收盘涨停次数 / 可判断信号 | 收盘涨停率 % | 收盘涨停率 95% 区间 | 综合判定 |", "|---|---:|---:|---:|---:|---|---|"])
        for key, row in result["strategies"].items():
            touch, sealed = (row["metrics"]["accuracy"][field] for field in ("next_touch", "next_sealed"))
            ci = sealed["day_cluster_ci95_pct"]
            lines.append(f"| {output['rules']['strategies'][key]['name']} | {touch['hits']}/{touch['n']} | {touch['rate_pct']:.2f} | {sealed['hits']}/{sealed['n']} | {sealed['rate_pct']:.2f} | [{ci[0]:.2f}, {ci[1]:.2f}] | {row['gate']} |")
        lines.extend(["", f"官方涨跌停价格与日线检查通过 {result['official_limit_valid_rows']} / {result['daily_rows']} 条。"])
    lines.extend(["", "## 发布与归档", "",
        "逐窗调用 evaluate_challenger，原始返回值保存在 JSON。参照为研究首板组，未与生产 champion 做完整重放。综合判定同时要求 n≥20、WR≥70%、PF≥1.2、Σ>0；三个规则均未满足，归档于 JSON 的 archived_falsified_directions。固定规则不再扫描参数。",
        "",
        "W3 使用最近 40 个交易日；W3_given_dates 保留用户给定日期包含的 35 个交易日，两者分别报告。",
        "",
        "账户级回撤与资金利用率、队列成交可得性、席位身份和历史持仓变化仍然缺少证据。价格形态无法证明主力在未来集中买入。",
        "",
        "## 复现", "", "```text", *output["caliber"]["reproduce"],
        "backend/.venv/bin/python -m pytest backend/tests/test_limit_up_benchmark.py -q", "```", "",
    ])
    path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--output-count", type=int, required=True, choices=[6])
    parser.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    if args.render_only:
        write_report(json.loads(args.out.read_text()), args.out.parent / "strategies-deep-opt-report-2026-09-21.md")
        return
    config = yaml.safe_load(CONFIG_PATH.read_text())
    if config["output_count"] != args.output_count:
        raise ValueError("output count mismatch")
    print("读取全市场日线与每日涨停价格", flush=True)
    frame, metadata = load_frame()
    calendar = sorted(frame.trade_date.unique().tolist())
    windows = {**WINDOWS, "W3_given_dates": WINDOWS["W3"]}
    windows["W3"] = (calendar[-40], calendar[-1])
    frame = attach_labels(signal_features(frame, calendar))
    print("数据读取完成", len(frame), len(calendar), calendar[-1], flush=True)
    snapshots = {}
    for day, part in frame.groupby("trade_date"):
        snapshots[day] = {"sample_count": len(part), "up_count": int((part.pct_chg > 0).sum()), "down_count": int((part.pct_chg < 0).sum()), "limit_up_count": int(part.sealed.sum()), "limit_down_count": int(((part.close - part.down_limit).abs() < 0.005).sum())}
    strategy_ids = list(config["strategies"])
    candidates = frame[frame[strategy_ids].any(axis=1)]
    codes = set(candidates.code)
    bars_by_code = {code: [SimpleNamespace(**row) for row in group[list(FIELDS)].to_dict("records")] for code, group in frame[frame.code.isin(codes)].groupby("code")}
    output = {
        "status": "RESEARCH_ONLY", "rules": config,
        "caliber": {
            "evidence": "Path A strategy signal backtest; no production end-to-end replay",
            "production_end_to_end": False, "output_count": 6, "signal_time": "T close", "entry_mode": config["default_entry_mode"],
            "score": "single declared constant-score signal through production aggregate; rank final_score then code",
            "execution": "production _simulate_outcome, current policy, T+1 exit restriction, costs; five observed stock bars",
            "position": "production _resolve_position_size", "capital_utilization": "mean allocated fraction per selected signal; portfolio concurrency not modelled",
            "drawdown": "additive signal-day mean net return drawdown; not account equity drawdown",
            "accuracy": "next market day high/close equals stored official up_limit within half a price tick; missing unknown",
            "eligibility": "all A share daily records; point-in-time non-ST metadata; listed at least 60 calendar days; positive volume and valid official daily limits",
            "baseline": "first_board same-day Top6; descriptive reference, not deployed champion",
            "bootstrap": "4000 paired signal-day samples for net return difference; day-cluster resampling for accuracy; seed 20260921",
            "missing_evidence": ["historical_order_queue", "minute_execution", "portfolio_cash_account", "news", "auction", "capital_flow", "seat_identity"],
            "source_type": "canonical pattern definitions; not a reproduction of any publisher's claimed returns",
            "w3_note": "W3 uses the latest 40 observed market trading days; W3_given_dates preserves the specified 35-day interval separately",
            "reproduce": ["backend/.venv/bin/python backend/scripts/research_limit_up_benchmark.py --output-count 6 --out output/limit-up-benchmark-metrics-2026-09-21.json"],
        }, "windows": {},
    }
    skills = {key: research_skill(key, config) for key in strategy_ids}
    for window, (start, end) in windows.items():
        days = [day for day in calendar if start <= day <= end]
        subset = frame[frame.trade_date.between(start, end)]
        records = {key: [] for key in strategy_ids}
        exclusions = Counter()
        for day, group in candidates[candidates.trade_date.between(start, end)].groupby("trade_date"):
            pools = {key: [] for key in strategy_ids}
            for row in group.to_dict("records"):
                stock_metadata = metadata_on(metadata[row["code"]], day)
                if not stock_metadata or not stock_metadata.get("list_date"):
                    exclusions["missing_metadata"] += 1
                    continue
                if stock_metadata["is_st"] or "ST" in stock_metadata["name"]:
                    exclusions["st"] += 1
                    continue
                if (pd.Timestamp(day) - pd.Timestamp(stock_metadata["list_date"])).days < 60:
                    exclusions["new_listing"] += 1
                    continue
                for strategy_id in strategy_ids:
                    if not row[strategy_id]:
                        continue
                    card = make_card(row, stock_metadata, snapshots[day], skills[strategy_id])
                    pools[strategy_id].append({"signal_date": day, "code": row["code"], "card": card, **{field: bool(row[field]) if pd.notna(row[field]) else None for field in ("next_touch", "next_sealed")}})
            for strategy_id, pool in pools.items():
                for row in sorted(pool, key=lambda value: (-value["card"]["final_score"], value["code"]))[:args.output_count]:
                    records[strategy_id].append(simulate(row, bars_by_code[row["code"]], calendar, config))
        metrics = {key: summarize(rows, days) for key, rows in records.items()}
        result = {"date_range": [start, end], "trade_days": len(days), "daily_rows": len(subset), "official_limit_valid_rows": int(subset.valid.sum()), "exclusions": dict(exclusions), "strategies": {}}
        for key, values in metrics.items():
            absolute = values["n"] >= 20 and values["win_rate_pct"] >= 70 and values["profit_factor"] >= 1.2 and values["cumulative_net_return_pct"] > 0
            result["strategies"][key] = {
                "metrics": values, "paired_day_bootstrap": paired_ci(records["first_board"], records[key], days),
                "relative_evaluate_challenger": _decision(metrics["first_board"], values) if values["n"] and metrics["first_board"]["n"] else None,
                "hard_target_pass": absolute, "gate": "FALSIFIED" if not absolute else "REQUIRES_END_TO_END", "records": records[key],
            }
            print(window, key, values["n"], values.get("win_rate_pct"), values["accuracy"], flush=True)
        output["windows"][window] = result
    output["archived_falsified_directions"] = [{"key": f"limit-up-benchmark-20260921:{key}", "decision": "FALSIFIED", "rules_sha256": hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest(), "reason": "one_or_more_windows_fail_absolute_target", "windows": {window: value["strategies"][key]["gate"] for window, value in output["windows"].items()}} for key in strategy_ids if any(not value["strategies"][key]["hard_target_pass"] for value in output["windows"].values())]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    write_report(output, args.out.parent / "strategies-deep-opt-report-2026-09-21.md")


if __name__ == "__main__":
    main()
