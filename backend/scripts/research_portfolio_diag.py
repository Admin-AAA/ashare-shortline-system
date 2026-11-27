"""组合层与收益集中度诊断（只读，不写库、不改生产代码）。

目的：回答两个系统级问题
1. 现役策略的收益是不是高度依赖少数右尾交易（右尾依赖度）；
2. 把多策略、同日多票按等权拼成组合后，组合回撤/波动比单笔口径差多少
   （即"只报每笔均值 + 单笔 MDD"这个口径掩盖了多少真实风险）。

数据源：backend/output/research/solo_20261026/<skill>/panel/entry_panel_WX.json
        （2026-10-26 真单策略回放，生产 v27 + execution v5.0.0 口径）
        交易日历与同排期基准：本地库 index_daily 的 EWALL.A（只读）。

用法：
  cd backend && .venv/bin/python scripts/research_portfolio_diag.py
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
SOLO = BASE / "output" / "research" / "solo_20261026"
# 2026-11-15 的旧口径结果留在 portfolio_diag_20261115/，这里另起目录，不覆盖证据。
OUT = BASE / "output" / "research" / "portfolio_diag_20261127"
# 面板只有离场原因、没有卖出日：每笔按短线执行政策的最长持有 5 个交易日（horizon_days）近似。
HOLD_DAYS = 5


def load_skill(skill_dir: Path):
    p = skill_dir / "panel" / "entry_panel_WX.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    rows = []  # (signal_date, code, net_pct, exit_reason, final_score)
    skipped = []  # 选中但买不进（net_by_code 为 None）
    for day in d.get("days") or []:
        sd = day.get("signal_date")
        net = day.get("net_by_code") or {}
        exits = day.get("exit_by_code") or {}
        cards = day.get("cards") or []
        scores = {c.get("code"): c.get("final_score") for c in cards}
        inds = {c.get("code"): c.get("industry") for c in cards}
        for code in day.get("selected") or []:
            if code not in net or net[code] is None:
                # 买不进（一字涨停 / 停牌）不计入已实现收益，但单独计数
                skipped.append({"signal_date": sd, "code": code, "reason": "no_fill"})
                continue
            rows.append(
                {
                    "signal_date": sd,
                    "code": code,
                    "net_pct": float(net[code]),
                    "exit": exits.get(code),
                    "score": scores.get(code),
                    "industry": inds.get(code),
                }
            )
    return {"rows": rows, "start": d.get("start"), "end": d.get("end"), "skipped": skipped}


def concentration(rets: list[float]) -> dict:
    """收益集中度：右尾依赖度 = 前 10 笔盈利之和 / 全部盈利之和。"""
    n = len(rets)
    if n == 0:
        return {}
    s = sorted(rets, reverse=True)
    total = sum(rets)
    gross_pos = sum(r for r in rets if r > 0)
    top10 = sum(s[: min(10, n)])
    top5pct = sum(s[: max(1, n // 20)])
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    # trim5：去掉最好与最差各 5% 后的均值
    k = max(1, n // 20)
    trimmed = s[k : n - k] if n - 2 * k > 0 else s
    return {
        "n": n,
        "win_rate_pct": round(100 * len(wins) / n, 2),
        "avg_pct": round(total / n, 4),
        "median_pct": round(statistics.median(rets), 4),
        "std_pct": round(statistics.pstdev(rets), 4) if n > 1 else 0.0,
        "sum_pct": round(total, 3),
        "profit_factor": round(gross_pos / abs(sum(losses)), 3) if losses else None,
        "avg_win_pct": round(statistics.mean(wins), 3) if wins else None,
        "avg_loss_pct": round(statistics.mean(losses), 3) if losses else None,
        "top10_profit_share_pct": round(100 * top10 / gross_pos, 1) if gross_pos > 0 else None,
        "top5pct_profit_share_pct": round(100 * top5pct / gross_pos, 1) if gross_pos > 0 else None,
        "trim5_avg_pct": round(sum(trimmed) / len(trimmed), 4),
        "worst_pct": round(min(rets), 3),
        "best_pct": round(max(rets), 3),
    }


@lru_cache(maxsize=1)
def _calendar() -> tuple[list[str], dict[str, float]]:
    """EWALL.A 交易日历与当日等权涨跌幅（%），只读库。"""
    from app.db.session import SessionLocal
    from app.services.market_benchmark import equal_weight_curve

    with SessionLocal() as db:
        return equal_weight_curve(db)


def day_portfolio(rows: list[dict]) -> dict:
    """账户组合，统一走 app.services.overlap_portfolio（交易日历固定槽位，L-203 2026-11-27 修订）。

    资金等分 5 份，每个信号日用 1 份买当天入选的票（批内每笔一份），T+1 买入、持 5 个交易日后这份收回；
    没有信号的交易日那份留现金，曲线一直走到最后一笔卖出。面板没有卖出日，所有票都按持满 5 日近似：
    多数票实际更早被止损或移动止盈卖出，这只改变收益在窗口内的分布，不改变每笔总收益。
    2026-11-15 修过「5 日收益当日收益复利」的 5 倍放大；2026-11-27 再改掉旧
    return_quality.portfolio_metrics 的三个问题（已删除）：以信号日为时间轴（无信号日被压掉）、
    止于最后一个信号日（最后一批持有期被截掉）、按当天覆盖的批数均分资金（空槽不算现金）。
    """
    from app.services import overlap_portfolio

    cal, pct = _calendar()
    idx = {d: i for i, d in enumerate(cal)}
    picks = []
    for r in rows:
        i = idx.get(r["signal_date"])
        if i is None or i + 1 + HOLD_DAYS >= len(cal):
            continue
        picks.append({
            "signal_date": r["signal_date"], "code": r["code"], "status": "closed", "hold_days": HOLD_DAYS,
            "entry_date": cal[i + 1], "exit_date": cal[i + 1 + HOLD_DAYS],
            # 面板 net_pct 是模拟器净收益，已扣 0.07% 往返税费；build 对 ret_includes_cost=False 的笔会再扣一次，先加回。
            "ret": r["net_pct"] + overlap_portfolio.COST_PCT, "ret_includes_cost": False,
        })
    p = overlap_portfolio.build(picks, cal, pct) if picks else None
    if not p:
        return {}
    a, b = p["A"], p["B"]
    return {
        "trade_days": len({r["signal_date"] for r in rows}),
        "picks": p["eligible_picks"],
        "outside_calendar": len(rows) - len(picks),
        "slots": p["slots"],
        "calendar_days": len(p["curves"]["dates"]),
        "first_day": p["curves"]["dates"][0],
        "last_day": p["curves"]["dates"][-1],
        "cum_pct": a["net_ret"],
        "bench_pct": a["bench_ret"],
        "excess_pct": a["excess_ret"],
        "portfolio_mdd_pct": a["max_drawdown"],
        "avg_invested_pct": a["avg_invested_pct"],
        "avg_positions": a["avg_positions"],
        # 同日同票多策略重复只买一份（L-227 的 B），只在合并多个策略时与上面不同。
        "dedup_cum_pct": b["net_ret"],
        "dedup_mdd_pct": b["max_drawdown"],
    }


def intraday_dispersion(rows: list[dict]) -> dict:
    """日内离散度 vs 全样本离散度：判断同日多票是否高度同质。

    比值接近 1 说明同日票几乎独立；远小于 1 说明同日票共享同一因子/情绪，
    分散化是假的，组合风险显著高于"每笔独立"的假设。
    """
    by_day: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_day[r["signal_date"]].append(r["net_pct"])
    multi = {d: v for d, v in by_day.items() if len(v) >= 2}
    if not multi:
        return {}
    within = statistics.mean([statistics.pstdev(v) for v in multi.values() if len(v) >= 2])
    all_rets = [r["net_pct"] for r in rows]
    overall = statistics.pstdev(all_rets)
    # 同日票两两相关（Pearson，仅在共同日期上）
    return {
        "multi_name_days": len(multi),
        "avg_names_per_multi_day": round(statistics.mean([len(v) for v in multi.values()]), 2),
        "within_day_std_pct": round(within, 3),
        "overall_std_pct": round(overall, 3),
        "dispersion_ratio": round(within / overall, 3) if overall else None,
    }


def overlap_matrix(skills: dict[str, dict]) -> dict:
    """跨策略同日同票重叠：衡量'多策略'到底是不是真分散。"""
    sets: dict[str, set] = {}
    for name, data in skills.items():
        sets[name] = {(r["signal_date"], r["code"]) for r in data["rows"]}
    names = sorted(sets)
    out = {}
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            inter = len(sets[a] & sets[b])
            union = len(sets[a] | sets[b])
            out[f"{a}|{b}"] = {
                "overlap_n": inter,
                "jaccard_pct": round(100 * inter / union, 2) if union else 0,
                "overlap_over_smaller_pct": round(100 * inter / min(len(sets[a]), len(sets[b])), 2)
                if min(len(sets[a]), len(sets[b]))
                else 0,
            }
    return out


def exit_distribution(rows: list[dict]) -> dict:
    c: dict[str, int] = defaultdict(int)
    for r in rows:
        c[r["exit"] or "unknown"] += 1
    n = len(rows)
    return {k: {"n": v, "pct": round(100 * v / n, 1)} for k, v in sorted(c.items(), key=lambda x: -x[1])}


def industry_dedup(rows: list[dict]) -> dict:
    """同日同行业只留 final_score 最高的一只，检验"同日同质"能否靠行业去重缓解。"""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["signal_date"]].append(r)
    kept: list[dict] = []
    dropped = 0
    for d, items in by_day.items():
        best: dict[str, dict] = {}
        for r in items:
            ind = r.get("industry") or "__unknown__"
            cur = best.get(ind)
            if cur is None or (r.get("score") or 0) > (cur.get("score") or 0):
                if cur is not None:
                    dropped += 1
                best[ind] = r
            else:
                dropped += 1
        kept.extend(best.values())
    return {
        "before_n": len(rows),
        "after_n": len(kept),
        "dropped_n": dropped,
        "before": {k: v for k, v in day_portfolio(rows).items() if k != "curve"},
        "after": {k: v for k, v in day_portfolio(kept).items() if k != "curve"},
        "before_avg_pct": round(sum(r["net_pct"] for r in rows) / len(rows), 4) if rows else None,
        "after_avg_pct": round(sum(r["net_pct"] for r in kept) / len(kept), 4) if kept else None,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    skills: dict[str, dict] = {}
    for d in sorted(SOLO.iterdir()):
        if not d.is_dir():
            continue
        data = load_skill(d)
        if data and data["rows"]:
            skills[d.name] = data

    report: dict = {"source": str(SOLO), "window": {}, "skills": {}, "overlap": {}, "combined": {}}
    all_rows: list[dict] = []
    for name, data in skills.items():
        rets = [r["net_pct"] for r in data["rows"]]
        report["window"][name] = {"start": data["start"], "end": data["end"]}
        report["skills"][name] = {
            "concentration": concentration(rets),
            "intraday": intraday_dispersion(data["rows"]),
            "exit_distribution": exit_distribution(data["rows"]),
            "portfolio": {k: v for k, v in day_portfolio(data["rows"]).items() if k != "curve"},
            "industry_dedup": industry_dedup(data["rows"]),
            "no_fill_n": len(data.get("skipped") or []),
        }
        all_rows.extend(data["rows"])

    report["overlap"] = overlap_matrix(skills)
    if all_rows:
        rets = [r["net_pct"] for r in all_rows]
        report["combined"] = {
            "all_skills": {
                "concentration": concentration(rets),
                "intraday": intraday_dispersion(all_rows),
                "portfolio": {k: v for k, v in day_portfolio(all_rows).items() if k != "curve"},
            }
        }
        (OUT / "combined_portfolio.json").write_text(
            json.dumps(day_portfolio(all_rows), ensure_ascii=False, indent=1)
        )

    (OUT / "diag.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

    # ---- 控制台摘要 ----
    print("=" * 78)
    print("A. 逐策略：收益集中度 / 日内同质 / 组合回撤")
    print("=" * 78)
    for name, s in report["skills"].items():
        c, it, pf = s["concentration"], s["intraday"], s["portfolio"]
        print(f"\n[{name}]  {report['window'][name]['start']} ~ {report['window'][name]['end']}")
        print(
            f"  笔数 {c['n']:>4}  胜率 {c['win_rate_pct']:>5.1f}%  每笔均值 {c['avg_pct']:>7.3f}%  "
            f"中位数 {c['median_pct']:>7.3f}%  PF {c['profit_factor']}"
        )
        print(
            f"  前10笔利润占比 {c['top10_profit_share_pct']}%  |  去两端5%均值 {c['trim5_avg_pct']:>7.3f}%  "
            f"(原始 {c['avg_pct']:>7.3f}%)"
        )
        print(
            f"  日内离散/全样本离散 = {it.get('dispersion_ratio')}  "
            f"({it.get('within_day_std_pct')} / {it.get('overall_std_pct')})"
        )
        print(
            f"  组合（交易日历 {pf.get('slots')} 槽，{pf.get('first_day')}~{pf.get('last_day')}）：累计 {pf.get('cum_pct')}%  "
            f"同排期基准 {pf.get('bench_pct')}%  超额 {pf.get('excess_pct')}pp  MDD {pf.get('portfolio_mdd_pct')}%  "
            f"平均投入 {pf.get('avg_invested_pct')}%"
        )
        du = s.get("industry_dedup") or {}
        if du:
            print(
                f"  同行业去重：{du['before_n']}→{du['after_n']} 笔（剔 {du['dropped_n']}）  "
                f"均值 {du['before_avg_pct']}% → {du['after_avg_pct']}%  "
                f"MDD {du['before']['portfolio_mdd_pct']}% → {du['after']['portfolio_mdd_pct']}%"
            )
        print(f"  出场分布: {', '.join(f'{k}={v['pct']}%' for k, v in s['exit_distribution'].items())}")

    print("\n" + "=" * 78)
    print("B. 跨策略同日同票重叠")
    print("=" * 78)
    for k, v in report["overlap"].items():
        print(f"  {k:60s} 重叠 {v['overlap_n']:>3} 票  Jaccard {v['jaccard_pct']:>5.1f}%  较小集占比 {v['overlap_over_smaller_pct']:>5.1f}%")

    if report["combined"]:
        c = report["combined"]["all_skills"]["concentration"]
        pf = report["combined"]["all_skills"]["portfolio"]
        print("\n" + "=" * 78)
        print("C. 全部策略合并（4 个策略同日等权）")
        print("=" * 78)
        print(f"  笔数 {c['n']}  每笔均值 {c['avg_pct']}%  中位数 {c['median_pct']}%  PF {c['profit_factor']}")
        print(f"  前10笔利润占比 {c['top10_profit_share_pct']}%  去两端5%均值 {c['trim5_avg_pct']}%")
        print(
            f"  组合（交易日历 {pf.get('slots')} 槽）：每笔一份 累计 {pf.get('cum_pct')}%  MDD {pf.get('portfolio_mdd_pct')}%  "
            f"超额 {pf.get('excess_pct')}pp  |  同日同票只买一份 累计 {pf.get('dedup_cum_pct')}%  "
            f"MDD {pf.get('dedup_mdd_pct')}%  平均投入 {pf.get('avg_invested_pct')}%"
        )
    print(f"\n输出: {OUT / 'diag.json'}")


if __name__ == "__main__":
    main()
