from __future__ import annotations

import argparse
import hashlib
import json
import sys
from bisect import bisect_left
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import yaml
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.models.tables import DailyKbar
from app.services.tushare_client import get_pro
from scripts.research_dragon_tiger_reaccumulation import metadata_on, paired_ci
from scripts.research_entry_mode_three_window import _decision
from scripts.research_limit_up_benchmark import (
    FIELDS, attach_labels, load_frame, make_card, research_skill, signal_features, simulate, summarize,
)

RAW_FIELDS = ["trade_date", "ts_code", "exalter", "side", "buy", "sell", "net_buy", "reason"]
CONFIG_PATH = Path(__file__).with_name("dragon_seat_repeat.yaml")


def market_days(start, end):
    with SessionLocal() as db:
        return list(db.scalars(select(DailyKbar.trade_date).where(
            DailyKbar.trade_date >= start, DailyKbar.trade_date <= end,
        ).distinct().order_by(DailyKbar.trade_date)))


def collect(cache, days):
    cache.mkdir(parents=True, exist_ok=True)
    client = get_pro()

    def fetch(day):
        path = cache / f"{day}.json"
        if path.exists():
            return
        frame = client.top_inst(trade_date=day.replace("-", ""))
        if frame.empty or len(frame) >= 10000:
            raise ValueError(f"unverified daily coverage: {day}:{len(frame)}")
        missing = set(RAW_FIELDS) - set(frame.columns)
        if missing:
            raise ValueError(f"missing fields: {day}:{sorted(missing)}")
        payload = {
            "requested_trade_date": day,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source": "TushareProviderRouter.top_inst",
            "published_at": None,
            "rows": json.loads(frame.to_json(orient="records", force_ascii=False)),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, _ in enumerate(pool.map(fetch, days), 1):
            if index % 20 == 0 or index == len(days):
                print(f"席位历史数据 {index}/{len(days)}", flush=True)


def read_raw(cache, days):
    pieces, manifest = [], []
    for day in days:
        path = cache / f"{day}.json"
        payload = json.loads(path.read_text())
        frame = pd.DataFrame(payload["rows"])
        if payload["requested_trade_date"] != day or frame.empty or len(frame) >= 10000:
            raise ValueError(f"invalid cached coverage: {day}")
        if not frame.trade_date.eq(day.replace("-", "")).all():
            raise ValueError(f"unexpected event date: {day}")
        pieces.append(frame)
        manifest.append({"trade_date": day, "rows": len(frame), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return pd.concat(pieces, ignore_index=True), manifest


def normalize_seats(raw):
    frame = raw[RAW_FIELDS].copy()
    if frame[["trade_date", "ts_code", "exalter", "side", "reason"]].isna().any().any():
        raise ValueError("incomplete seat identity")
    for key in ("buy", "sell", "net_buy"):
        frame[key] = pd.to_numeric(frame[key], errors="raise")
    if (frame[["buy", "sell"]] < 0).any().any():
        raise ValueError("negative gross amount")
    complete = frame[["buy", "sell", "net_buy"]].notna().all(axis=1)
    if ((frame.loc[complete, "buy"] - frame.loc[complete, "sell"] - frame.loc[complete, "net_buy"]).abs() > 0.1).any():
        raise ValueError("net amount reconciliation failed")
    if not frame.side.astype(str).isin(["0", "1"]).all():
        raise ValueError("unknown ranking side")
    frame["event_date"] = pd.to_datetime(frame.trade_date, format="%Y%m%d").dt.strftime("%Y-%m-%d")
    frame["code"] = frame.ts_code.str.split(".").str[0]
    reasons = frame.reason.astype(str)
    # 仅使用明确的单日价格、成交量榜单；统计区间无法确认的记录不参与。
    single = reasons.str.contains("日|涨幅|跌幅|振幅|换手率") & ~reasons.str.contains("连续|累计|无价格|无涨跌幅|上市首日")
    named = frame.exalter.str.contains("营业部|分公司") & ~frame.exalter.str.contains("专用|交易单元")
    eligible = frame[single & named].copy()
    identity = ["code", "event_date", "exalter"]
    # 同一席位同时进入两个排行榜或多个单日原因时，金额只计算一次。
    unique = eligible.drop_duplicates(identity + ["buy", "sell"])
    groups = unique.groupby(identity, sort=True)
    counts = groups[["buy", "sell"]].nunique()
    known = counts.buy.eq(1) & counts.sell.eq(1)
    # 买入榜与卖出榜分别披露的金额，仅在两项均明确且唯一时合并。
    clean = groups[["buy", "sell"]].min().loc[known].reset_index()
    clean["net_buy"] = clean.buy - clean.sell
    quality = {
        "raw_rows": len(frame), "single_day_rows": int(single.sum()),
        "named_single_day_rows": len(eligible), "duplicate_ranking_or_reason_rows": len(eligible) - len(unique),
        "missing_buy_rows": int(frame.buy.isna().sum()), "missing_sell_rows": int(frame.sell.isna().sum()),
        "unknown_side_seat_events": int(counts.eq(0).any(axis=1).sum()),
        "conflicting_named_seat_events": int(counts.gt(1).any(axis=1).sum()), "usable_seat_events": len(clean),
        "unique_codes": int(clean.code.nunique()), "publication_timestamp_available": False,
    }
    return clean, quality


def seat_signals(seats, calendar):
    following = dict(zip(calendar[:-1], calendar[1:], strict=True))
    positives = seats[seats.net_buy > 0].copy()
    result = []
    previous = {}
    for (code, exalter), group in seats.groupby(["code", "exalter"]):
        history = group.sort_values("event_date").to_dict("records")
        for before, after in zip(history[:-1], history[1:], strict=True):
            previous[(code, exalter, after["event_date"])] = before
    for (code, exalter), group in positives.groupby(["code", "exalter"]):
        dates = sorted(group.event_date.tolist())
        for index, event_date in enumerate(dates):
            signal_date = following.get(event_date)
            if signal_date is None:
                continue
            start = (pd.Timestamp(signal_date) - pd.DateOffset(months=1)).strftime("%Y-%m-%d")
            first = bisect_left(dates, start)
            observed_dates = dates[first:index + 1]
            prior = previous.get((code, exalter, event_date))
            result.append({
                "code": code, "trade_date": signal_date, "exalter": exalter,
                "event_date": event_date, "positive_dates": observed_dates,
                "repeated": len(observed_dates) >= 2,
                "reversal": bool(prior and prior["event_date"] >= start and prior["net_buy"] < 0),
                "previous_event": prior,
            })
    return pd.DataFrame(result)


def recent_limit_features(frame):
    frame = frame.copy()
    last = frame.trade_date.where(frame.sealed).groupby(frame.code).ffill()
    frame["last_sealed_date"] = last.groupby(frame.code).shift(1)
    start = pd.to_datetime(frame.trade_date) - pd.DateOffset(months=1)
    frame["recent_limit"] = pd.to_datetime(frame.last_sealed_date).ge(start)
    return frame


def institution_signals(raw, calendar):
    following = dict(zip(calendar[:-1], calendar[1:], strict=True))
    reasons = raw.reason.astype(str)
    single = reasons.str.contains("日|涨幅|跌幅|振幅|换手率") & ~reasons.str.contains("连续|累计|无价格|无涨跌幅|上市首日")
    rows = raw[raw.exalter.eq("机构专用") & single]
    result = {}
    for (code, day), part in rows.groupby(["ts_code", "trade_date"]):
        event_date = pd.to_datetime(day, format="%Y%m%d").strftime("%Y-%m-%d")
        signal_date = following.get(event_date)
        complete = part[["buy", "sell"]].notna().all(axis=1).all()
        if signal_date and complete and (part.buy > part.sell).all():
            result[(code.split(".")[0], signal_date)] = part[RAW_FIELDS].to_dict("records")
    return result


def run_research(args, seats, quality, manifest, calendar, raw):
    config = yaml.safe_load(args.config.read_text())
    candidate = args.candidate
    if candidate not in config["strategies"] or candidate == "seat_reference":
        raise ValueError("unknown candidate")
    arms = ["seat_reference", candidate]
    if args.output_count != config["output_count"]:
        raise ValueError("output count mismatch")
    features = seat_signals(seats, calendar)
    frame, metadata = load_frame("2024-12-01", args.end)
    frame = attach_labels(recent_limit_features(signal_features(frame, calendar)))
    snapshots = {}
    for day, part in frame.groupby("trade_date"):
        snapshots[day] = {
            "sample_count": len(part), "up_count": int((part.pct_chg > 0).sum()),
            "down_count": int((part.pct_chg < 0).sum()), "limit_up_count": int(part.sealed.sum()),
            "limit_down_count": int(((part.close - part.down_limit).abs() < 0.005).sum()),
        }
    events = {key: part.to_dict("records") for key, part in features.groupby(["code", "trade_date"])}
    institutions = institution_signals(raw, calendar) if candidate == "institution_agreement" else {}
    keys = features[["code", "trade_date"]].drop_duplicates()
    candidates = frame[frame.valid & frame.recent_limit & ~frame.sealed].merge(keys, on=["code", "trade_date"], validate="one_to_one")
    codes = set(candidates.code)
    bars = {code: [SimpleNamespace(**row) for row in part[list(FIELDS)].to_dict("records")]
            for code, part in frame[frame.code.isin(codes)].groupby("code")}
    windows = {"W1": ("2025-01-15", "2026-01-16"), "W2": ("2026-01-17", "2026-07-30"), "W3": (calendar[-40], calendar[-1])}
    skill = replace(research_skill("seat_reference", config), path=args.config.parent,
                    version="research-20260922", contract_hash=hashlib.sha256(args.config.read_bytes()).hexdigest())
    output = {
        "status": "RESEARCH_ONLY", "candidate": candidate, "rules": config, "source_quality": quality, "source_manifest": manifest,
        "caliber": {
            "evidence": "Path A signal research; production end-to-end replay not completed",
            "output_count": args.output_count, "entry_mode": config["default_entry_mode"],
            "score": "production aggregate final_score; constant declared base 70; ties by code",
            "position": "production _resolve_position_size", "execution": "production _simulate_outcome; T+1 exits; current fees; five observed stock bars",
            "signal_time": "T close; seat event strictly previous market trading day; prior natural month events only",
            "publication": "original published_at unavailable; event_date<T is a conservative research assumption, not historical publication proof",
            "seat_identity": "exact named brokerage outlet; no ultimate investor or holding identity inferred; generic seats excluded",
            "amounts": "yuan; both sides must be known and unique; one-sided net_buy ignored; ranking and reason duplicates counted once",
            "baseline": "same recent-limit and latest positive-seat universe, without repeat condition; not production champion",
            "max_drawdown": "additive signal-day average-return curve, percentage points; account equity drawdown unavailable",
            "capital_utilization": "average allocated fraction per signal; account cash scheduling unavailable",
            "bootstrap": "4000 paired signal-day samples; zero for inactive days; seed 20260921",
            "source_document": "https://tushare.pro/document/2?doc_id=107",
            "reproduce": [f"backend/.venv/bin/python backend/scripts/research_dragon_seat_repeat.py --cache backend/output/dragon-seat/raw --end {args.end} --output-count 6 --config {args.config} --candidate {candidate} --out {args.out}"],
        }, "windows": {},
    }
    for window, (start, end) in windows.items():
        days = [day for day in calendar if start <= day <= end]
        records = {key: [] for key in arms}
        exclusions = Counter()
        for day, part in candidates[candidates.trade_date.between(start, end)].groupby("trade_date"):
            pool = []
            for row in part.to_dict("records"):
                stock_metadata = metadata_on(metadata[row["code"]], day)
                if not stock_metadata or not stock_metadata.get("list_date"):
                    exclusions["metadata_missing"] += 1
                    continue
                if stock_metadata["is_st"] or "ST" in stock_metadata["name"]:
                    exclusions["st"] += 1
                    continue
                if (pd.Timestamp(day) - pd.Timestamp(stock_metadata["list_date"])).days < 60:
                    exclusions["new_listing"] += 1
                    continue
                card = make_card(row, stock_metadata, snapshots[day], skill)
                evidence = events[(row["code"], day)]
                institutional = institutions.get((row["code"], day), [])
                qualified = {
                    "seat_repeat": any(item["repeated"] for item in evidence),
                    "seat_reversal": any(item["reversal"] for item in evidence),
                    "institution_agreement": bool(institutional),
                }[candidate]
                pool.append({"signal_date": day, "code": row["code"], "card": card,
                             "seat_evidence": evidence, "last_sealed_date": row["last_sealed_date"],
                             "qualified": qualified, "institution_evidence": institutional,
                             **{key: bool(row[key]) if pd.notna(row[key]) else None for key in ("next_touch", "next_sealed")}})
            ranked = sorted(pool, key=lambda row: (-row["card"]["final_score"], row["code"]))
            for key in arms:
                selected = [row for row in ranked if key == "seat_reference" or row["qualified"]][:args.output_count]
                for row in selected:
                    record = simulate(row, bars[row["code"]], calendar, config)
                    record.update({field: row[field] for field in ("seat_evidence", "last_sealed_date", "institution_evidence")})
                    records[key].append(record)
        metrics = {key: summarize(items, days) for key, items in records.items()}
        current = metrics[candidate]
        sufficient = current["n"] >= 20
        absolute = sufficient and current["win_rate_pct"] >= 70 and current["profit_factor"] >= 1.2 and current["cumulative_net_return_pct"] > 0
        output["windows"][window] = {
            "date_range": [start, end], "trade_days": len(days), "exclusions": dict(exclusions),
            "strategies": {key: {"metrics": metrics[key], "records": records[key]} for key in records},
            "paired_day_bootstrap": paired_ci(records["seat_reference"], records[candidate], days),
            "relative_evaluate_challenger": _decision(metrics["seat_reference"], current) if current["n"] and metrics["seat_reference"]["n"] else None,
            "sample_sufficient": sufficient, "absolute_target_pass": absolute,
            "gate": "REQUIRES_END_TO_END" if absolute else "FALSIFIED",
            "account_max_drawdown_pct": None, "account_capital_utilization_pct": None,
        }
        print(window, json.dumps(current, ensure_ascii=False), flush=True)
    output["status"] = "FALSIFIED" if any(not value["absolute_target_pass"] for value in output["windows"].values()) else "REQUIRES_END_TO_END"
    output["archived_falsified_directions"] = ([{
        "key": "dragon-seat-repeat-20260922:" + ("named-repeat-net-buy" if candidate == "seat_repeat" else candidate), "reason": "one_or_more_windows_fail_absolute_target",
        "rules_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
    }] if output["status"] == "FALSIFIED" else [])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    if candidate == "seat_repeat":
        write_report(output, args.out.parent / "strategies-deep-opt-report-2026-09-22.md")


def write_report(output, path):
    lines = [
        "# 龙虎榜席位研究 · 2026-09-22", "",
        "第 3 轮状态：FALSIFIED。生产策略、执行版本和选股预设保持不变。", "",
        "## 固定假设与数据", "",
        "最近一个自然月出现过收盘涨停，T 日没有收盘涨停；T 前一交易日的具名营业部净买入，且同一营业部在前一个自然月内另有至少一个净买入日期。假设持续需求能够改善次日涨停率与交易净收益。",
        "",
        "参照组使用相同的近期涨停与前日席位净买入条件，不要求重复日期。两组每日分别经过生产 aggregate 按 final_score 排序，最多选择 6 只；声明基础分为 70，同分按股票代码排序。",
        "",
        "入场方式由 dragon_seat_repeat.yaml 声明为 next_open；成交和退出使用生产 _simulate_outcome，仓位使用 _resolve_position_size，包含现行费用和 T+1 卖出限制。最长观察 5 根股票日线；未完成观察的成交不计入 WR。",
        "",
        "数据来源：[Tushare 龙虎榜席位明细](https://tushare.pro/document/2?doc_id=107)。原始记录按日期保存，JSON 保留逐日 SHA-256。历史范围 2024-12-02 至 2026-09-21，共 440 个交易日，W1 具备完整自然月预热期。",
        "",
        "接口字段 side 表示买入额或卖出额排行榜。单日榜中，同一营业部同时进入两个榜单时只计算一次；两项金额分别披露时，仅在两项都明确且唯一时合并。单侧金额缺失保持未知，不以 net_buy 代替完整净额。连续多日榜、机构专用等无具体身份记录不参与持续性判断。",
        "",
        "原始数据缺少历史发布时间。研究采用 event_date < T 的时间限制，这仍然属于数据可得性的研究假设。营业部名称不能证明同一个最终投资者，也不能证明未上榜期间的持仓。",
        "",
        "## 数据检查", "",
    ]
    for key, value in output["source_quality"].items():
        lines.append(f"- {key}：{value}")
    lines.extend(["", "## 三窗指标", "",
        "Σ 的单位为单笔净收益百分点之和。回撤采用信号日平均净收益累计曲线；覆盖率为已结算成交数除以选中信号数，仓位为每个信号平均分配比例。账户净值回撤和并发资金利用率尚未计算，JSON 明确保留为 null。",
        "",
        "| 窗口 | 交易日 | n | WR % | 平均净收益 % | Σ 百分点 | PF | 信号回撤 百分点 | 平均盈利 % | 平均亏损 % | 覆盖率 % | 平均仓位 % | 配对差值 95% 区间 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for window, result in output["windows"].items():
        m = result["strategies"]["seat_repeat"]["metrics"]
        fields = ["win_rate_pct", "average_net_return_pct", "cumulative_net_return_pct", "profit_factor", "max_drawdown_pct", "average_win_pct", "average_loss_pct", "coverage_pct", "capital_utilization_pct"]
        ci = result["paired_day_bootstrap"]["ci95_pct"]
        lines.append(f"| {window} | {result['trade_days']} | {m['n']} | " + " | ".join(f"{m[key]:.3f}" for key in fields) + f" | [{ci[0]:.3f}, {ci[1]:.3f}] |")
    lines.extend(["", "配对 bootstrap 在相同日期抽样，对比两组每日平均净收益；没有信号的日期按零处理，4000 次，seed=20260921。三个区间均包含零。", "",
        "| 窗口 | 日期 | 触及涨停率 % | 收盘涨停率 % | 收盘涨停次数 / 已知标签 | 收盘涨停率 95% 区间 | 参照组收盘涨停率 % | 原始 evaluate_challenger | 绝对验收 |",
        "|---|---|---:|---:|---:|---|---:|---|---|",
    ])
    for window, result in output["windows"].items():
        m = result["strategies"]["seat_repeat"]["metrics"]["accuracy"]
        sealed = m["next_sealed"]
        ci = sealed["day_cluster_ci95_pct"]
        reference = result["strategies"]["seat_reference"]["metrics"]["accuracy"]["next_sealed"]["rate_pct"]
        decision = result["relative_evaluate_challenger"]["decision"]
        lines.append(f"| {window} | {' ~ '.join(result['date_range'])} | {m['next_touch']['rate_pct']:.2f} | {sealed['rate_pct']:.2f} | {sealed['hits']}/{sealed['n']} | [{ci[0]:.2f}, {ci[1]:.2f}] | {reference:.2f} | {decision} | {result['gate']} |")
    lines.extend(["", "涨停标签使用下一市场交易日的官方涨停价格，缺失标签保留未知。W3 的 11 笔未完成观察成交没有计入交易胜负。W3 原始相对判定通过，但 WR、PF 未达到用户标准，因此综合判定仍为 FALSIFIED。",
        "", "## 发布与后续研究限制", "",
        "本轮只完成路径 A 的独立信号研究，未执行生产全部策略组合的端到端重放。三个窗口已经不满足绝对指标，本规则保持归档，未新建或删除技能，未修改生产页面产物，未重启后端。",
        "",
        "根因证据：同名营业部重复净买入并未形成三窗稳定收益改善；W1、W2 的 WR 均低于参照组，两个窗口收盘涨停率也低于参照组。W3 的净收益改善尚无配对区间排除零的证据。",
        "",
        "当前信息仍无法检验最终投资者持续增仓、盘中主动买入与涨停排队成交。后续方向需要可追溯发布时间的席位事件、历史逐笔或委托数据，且三个窗口都具备覆盖。禁止通过修改本规则的月份、次数或价格阈值重新扫描。",
        "",
        "## 复现", "", "```text", *output["caliber"]["reproduce"],
        "backend/.venv/bin/python -m pytest backend/tests/test_dragon_seat_repeat.py backend/tests/test_limit_up_benchmark.py -q", "```", "",
        "回归使用真实席位披露记录、本地历史日线和逐笔研究产物；覆盖单侧金额、两个排行榜去重、统计区间、未来隔离和指标复算。",
    ])
    for round_number, name, candidate in [(4, "institution-agreement", "institution_agreement"), (5, "seat-reversal", "seat_reversal")]:
        result_path = path.parent / f"{name}-metrics-2026-09-22.json"
        if not result_path.exists():
            continue
        artifact = json.loads(result_path.read_text())
        declared = artifact["rules"]["strategies"][candidate]
        lines.extend(["", f"## 第 {round_number} 轮：{declared['name']}", "", declared["hypothesis"], "", declared["rule"], "",
            "| 窗口 | n | WR % | 平均净收益 % | Σ 百分点 | PF | 信号回撤 百分点 | 平均盈利 % | 平均亏损 % | 覆盖率 % | 平均仓位 % | 配对差值 95% 区间 | 绝对验收 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ])
        for window, result in artifact["windows"].items():
            m = result["strategies"][candidate]["metrics"]
            ci = result["paired_day_bootstrap"]["ci95_pct"]
            lines.append(f"| {window} | {m['n']} | " + " | ".join(f"{m[key]:.3f}" for key in fields) + f" | [{ci[0]:.3f}, {ci[1]:.3f}] | {result['gate']} |")
        lines.extend(["", "| 窗口 | 触及涨停率 % | 收盘涨停率 % | 收盘涨停次数 / 已知标签 | 收盘涨停率 95% 区间 | 原始 evaluate_challenger |",
                      "|---|---:|---:|---:|---|---|"])
        for window, result in artifact["windows"].items():
            accuracy = result["strategies"][candidate]["metrics"]["accuracy"]
            sealed = accuracy["next_sealed"]
            ci = sealed["day_cluster_ci95_pct"]
            lines.append(f"| {window} | {accuracy['next_touch']['rate_pct']:.2f} | {sealed['rate_pct']:.2f} | {sealed['hits']}/{sealed['n']} | [{ci[0]:.2f}, {ci[1]:.2f}] | {result['relative_evaluate_challenger']['decision']} |")
        lines.extend(["", f"产物：{result_path.name}。归档键：{artifact['archived_falsified_directions'][0]['key']}。", "", "```text", *artifact["caliber"]["reproduce"], "```", ""])
    if all((path.parent / f"{name}-metrics-2026-09-22.json").exists() for name in ("institution-agreement", "seat-reversal")):
        lines.extend(["", "## 瓶颈报告与停止条件", "",
            "累计连续 5 轮无 PASS，本次研究结束。第 1 轮研究龙虎榜后整理，第 2 轮研究三个固定涨停形态，第 3 至 5 轮分别检验本报告的三项席位假设；共 7 个方向归档，0 个方向发布。",
            "",
            "阻挡条件：WR≥70%、PF≥1.2、Σ>0 必须在三个窗口同时成立。本次三项席位方向的九组窗口结果中，没有一组同时达到全部条件。账户资金调度、账户净值回撤和完整生产预设重放也尚未形成证据。",
            "",
            "已排除的固定方向：龙虎榜后整理再启动；首板接力；触板未封修复；涨停次日阴线回调；同一营业部重复净买入；机构记录一致净买入；同一营业部净卖出转净买入。",
            "",
            "需要补充：带历史发布时间的席位事件、能够解释身份的账户或席位映射、三个窗口连续覆盖的盘中主动买卖与委托队列数据。现有公开金额无法观测未上榜期间持仓。",
            "",
            "验收标准保持不变，后续是否调整由用户决定。研究代码与归档证据合并不代表策略发布。回归结果及提交状态见同日逐轮记录和最终交付。",
        ])
    path.write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument("--output-count", type=int, choices=[6])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--candidate", choices=["seat_repeat", "institution_agreement", "seat_reversal"], default="seat_repeat")
    args = parser.parse_args()
    if args.render_only:
        write_report(json.loads(args.out.read_text()), args.out.parent / "strategies-deep-opt-report-2026-09-22.md")
        return
    days = market_days("2024-12-01", args.end)
    collect(args.cache, days)
    raw, manifest = read_raw(args.cache, days)
    seats, quality = normalize_seats(raw)
    print(json.dumps({"date_range": [days[0], days[-1]], "trade_days": len(days), "quality": quality}, ensure_ascii=False), flush=True)
    if not args.collect_only:
        if args.out is None or args.output_count is None:
            raise ValueError("research requires out and output-count")
        run_research(args, seats, quality, manifest, days, raw)


if __name__ == "__main__":
    main()
