"""§306：把 midterm_backtest_metrics.json 中 6 个中线策略的 recent_12m / phases / bench / excess 换成修正 ST 口径（§278 r25_new）。

只读 output/research/s278/r25_new.json 与 s282/quality_mid_long_v282.json，只改展示指标，不碰选股逻辑。
刷新前断言：R12/N3 笔数与每笔、8 段每笔均与 §282 quality 一致，不一致就中止。
用法：cd backend && .venv/bin/python scripts/midterm/s306_refresh_metrics.py [--dry]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
METRICS = BACKEND / "app/services/midterm_backtest_metrics.json"
R25 = BACKEND / "output/research/s278/r25_new.json"
QUAL = BACKEND / "output/research/s282/quality_mid_long_v282.json"

# 策略 id -> r25_new.json 里的键（低价小市值周度本来就是修正口径，不在此列）
MAPPING = {
    "mid-small-cap-weekly": "SCW",
    "mid-incentive-option": "INC_opt_h20",
    "mid-earnings-accel": "EA_h20",
    "mid-forecast-beat": "F_any_h20",
    "mid-repurchase-plan": "RP_r05",
    "mid-block-trade-premium": "BT_prem_h20",
}
KEYS = ["n", "win", "avg", "pf", "days", "tot", "mdd", "bench", "excess", "ex_win"]
WINDOW = "2025-10-01~2026-10-16"
GENERATED = "2026-10-25"

NOTE_COMMON = "成本0.3%/笔；开盘涨停不买、跌停封死顺延卖出；"
NOTES = {
    "mid-small-cap-weekly": NOTE_COMMON + "最大回撤按逐日盯市（20个等额仓位）计算。回测按 20 个仓位名额（每日新开数量有上限）计算，实盘推荐不设该上限（每周按规则选市值最小 10 只）。以下数字为 ST 修正后口径（§306 刷新）：2019–2026 共8个阶段全部为正（每笔 +5.17 / +2.59 / +1.14 / +4.17 / +1.72 / +4.03 / +3.52 / +4.64，阶段顺序同上表）；最近12个月 83 笔 +4.64%/笔 PF2.81 回撤 −6.2%（超额 +2.91），近3年 250 笔 +4.07%/笔 PF2.16 回撤 −16.9%（超额 +1.29）；同市值对照 7/8 段跑赢（2025-01~09 跑输 0.63）。旧版 2021 年 −2.5%（50笔）是研究用价格数据 2021-05-26~12-31 缺失（每天只有约5只股票）造成的假象，补全后 2021 年 111 笔 每笔 +1.14% PF1.40 回撤 −9.3%",
    "mid-incentive-option": NOTE_COMMON + "回撤按逐日盯市。bench=同日同市值五分位流动性合格全体等权每笔收益，excess=每笔超额。按 2026-10-18 新权重预注册标准采纳；以下数字为 ST 修正后口径（§306 刷新）：最近12个月 123 笔 +4.53%/笔 胜率54.5% PF1.95 回撤 −9.2%（超额 +5.10）、2024-01~2026-10 连续 284 笔 +5.17%/笔 PF2.26 总 +73.4% 回撤 −8.4%（超额 +4.58），2024 +2.64、2025年1–9月 +8.64；7/8 阶段跑赢对照（2021 跑输 0.78）。平均盈利约 +17% 对平均亏损约 −8.6~−10.4%，最长连亏 10 笔。老阶段最差：2023-04~12 −0.36%/笔（回撤 −7.0%，唯一为负）、2021 +0.62、2020 +2.09、2022-04~2023-03 +0.67（其余老阶段只是小幅为正）。与现行中线持有期重叠 0~2.4%（近三年）。同批全部草案版（不限期权）当时也达标但近三年每笔 +3.38%（采纳时旧口径数字）较低未选。",
    "mid-earnings-accel": NOTE_COMMON + "回撤按逐日盯市。按 2026-10-18 新权重（近12个月与2024–2026为主、老阶段只查是否灾难性亏损）预注册标准采纳；以下数字为 ST 修正后口径（§306 刷新）：最近12个月 93 笔 +5.78%/笔 PF2.40 回撤 −9.5%、2024-01~2026-10 连续 248 笔 +3.09%/笔 PF1.72 总 +38.4% 回撤 −24.2%，2024 +0.13、2025年1–9月 +0.28；最差老阶段 2022-04~2023-03 −4.00%/笔（回撤 −28.7%），2023-04~12 −1.48%/笔，未触及 −5%/−40% 灾难线。",
    "mid-forecast-beat": NOTE_COMMON + "回撤按逐日盯市。bench=同日同市值五分位流动性合格全体等权每笔收益，excess=每笔超额。按 2026-10-18 新权重预注册标准采纳；以下数字为 ST 修正后口径（§306 刷新）：最近12个月 138 笔 +3.70%/笔 PF1.85 回撤 −10.3%（超额 +3.58）、2024-01~2026-10 连续 334 笔 +2.88%/笔 PF1.61 总 +48.1% 回撤 −18.8%（超额 +2.63），2024 −0.35（全年小亏）、2025年1–9月 +6.59；6/8 阶段跑赢对照（2020、2023-04~12 跑输）。胜率约47%、平均盈利约 +16% 对平均亏损约 −8.5~−9.9%，靠盈亏比；近3年最长连亏 12 笔。风险：2023-04~12 每笔 −4.84%（PF0.29，回撤 −25.3%），接近 −5% 风控线；信号集中在 1/4/7/10 月预告季。§73 曾测过相近口径（含修正公告、加涨幅过滤），本次不是完全独立的样本外检验。与 mid-earnings-accel 持有期重叠仅 1.5%~2.4%（近三年）。",
    "mid-repurchase-plan": NOTE_COMMON + "回撤按逐日盯市。§100 按预注册标准采纳（替换原不限金额版）；以下数字为 ST 修正后口径（§306 刷新）：最近12个月 194 笔 +1.35%/笔 PF1.32（刚好压在 1.3 线上）回撤 −14.0%（超额 +1.17）；2024-01~2026-10 连续 505 笔 +2.55%/笔 PF1.68 回撤 −19.5%（超额 +0.74）。采纳时对比的原不限金额版数字是旧口径（原版 224 笔 +0.75% PF1.17；2024-01~2026-10 600 笔 +1.85% PF1.44 回撤 −22.8%）。老阶段 2020、2021 每笔低于原版（修正后 +1.19/+1.07，原版 +3.04/+2.96），2023-04~12 小亏 −1.19%/笔。同市值对照 6/8 段跑赢（2019、2021 跑输）。",
    "mid-block-trade-premium": NOTE_COMMON + "回撤按逐日盯市。bench=同日同市值五分位流动性合格全体等权每笔收益，excess=每笔超额。按预注册标准采纳；以下数字为 ST 修正后口径（§306 刷新）：最近12个月 100 笔 +1.27%/笔 胜率45.0% PF1.31 回撤 −10.9%（超额 +0.71）、2024-01~2026-10 连续 381 笔 +2.21%/笔 PF1.49 总 +42.1% 回撤 −21.0%（超额 +0.75），2024 +1.89、2025年1–9月 +3.95；6/8 阶段跑赢对照（2020、2022-04~2023-03 跑输）。平均盈利约 +12% 对平均亏损约 −7.4~−9.5%，最长连亏 11 笔（2022-04~2023-03 19 笔）。老阶段最差：2022-04~2023-03 −0.84%/笔（回撤 −23.8%），2023-04~12 −0.74%/笔。注意：优势温和，2026 年 1–10 月入场 74 笔 −0.21%/笔（胜率 37.8%，采纳时的统计，未重算），最近12个月的正收益主要来自 2025 年 10–12 月；上线后需跟踪。与现行中线/长线持有期重叠 0~2.5%。时点：大宗交易约 21:00 发布，需晚间（21:00 后）出票，次日开盘买。",
}


def _pick(src: dict) -> dict:
    return {k: src[k] for k in KEYS if k in src}


def main(dry: bool = False) -> None:
    metrics = json.loads(METRICS.read_text())
    r25 = json.loads(R25.read_text())
    qual = json.loads(QUAL.read_text())
    report = {}
    for sid, jk in MAPPING.items():
        src = r25[jk]
        q = qual[sid]
        # 断言：R12/N3 与 §282 quality 一致；各段每笔与 quality.phase_avg 一致
        r12 = next(v for k, v in src.items() if k.startswith("R12"))
        n3 = next(v for k, v in src.items() if k.startswith("N3"))
        assert int(r12["n"]) == q["R12"]["n"] and abs(r12["avg"] - q["R12"]["avg"]) < 0.011, (sid, "R12")
        assert int(n3["n"]) == q["N3"]["n"] and abs(n3["avg"] - q["N3"]["avg"]) < 0.011, (sid, "N3")
        for pk, v in src.items():
            tag = pk.split()[0]
            if tag in q.get("phases", {}):
                assert abs(v["avg"] - q["phases"][tag]) < 0.011, (sid, tag, v["avg"], q["phases"][tag])
        old = metrics[sid]
        phases = {pk: _pick(v) for pk, v in src.items()}
        report[sid] = {"R12 old": old["recent_12m"].get("avg"), "R12 new": r12["avg"],
                       "phases": len(phases), "had_N3": any(k.startswith("N3") for k in old["phases"])}
        old["recent_12m"] = _pick(r12)
        old["phases"] = phases
        old["window"] = WINDOW
        old["generated"] = GENERATED
        old["note"] = NOTES[sid]
    # 低价小市值周度：只删「不设观察档」（数据本来就是修正口径）
    lp = metrics["mid-lowprice-smallcap-weekly"]
    lp["note"] = lp["note"].replace("，不设观察档。", "。").replace("不设观察档，", "").replace("不设观察档", "")
    assert "观察档" not in lp["note"]
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if not dry:
        METRICS.write_text(json.dumps(metrics, ensure_ascii=False, indent=1))
        print("written", METRICS)


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
