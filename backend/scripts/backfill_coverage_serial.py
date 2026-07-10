#!/usr/bin/env python3
"""
串行历史覆盖度补跑脚本
对低覆盖策略在历史多日上逐个跑，等完成再提下一个，避免并发导致 MySQL 临时文件写满。
"""
import sys
import os
import time
import requests

BASE_URL = "http://localhost:8765"

# skill_id -> 策略中文名（与 backend/skills/*/strategy.yaml 的 name 字段保持一致）
# records 页展示 run_name，用英文 skill_id 拼接会让人看不出是什么策略，务必用中文名。
STRATEGY_NAMES = {
    "small-cap-thematic-momentum": "小盘题材动量",
    "chip-single-peak-breakout": "筹码单峰突破",
    "trend-pullback-continuation": "强趋势缩量回踩",
    "limit-up-reseal-relay": "涨停回封接力",
    "high-range-breakout-momentum": "阶段新高放量突破",
    "dragon-tiger-smart-money": "龙虎榜聪明钱接力",
    "chao-gu-yang-jia-skill": "炒股养家大局观",
    "chen-xiaoqun-skill": "陈小群龙头战法",
    "daily-stock-analysis-skill": "基础短线策略",
    "overnight-holding-method": "隔夜持股法",
    "sector-relative-strength-leader": "主线相对强度龙头",
}

LOW_COVERAGE = [
    "small-cap-thematic-momentum",
    "chip-single-peak-breakout",
    "trend-pullback-continuation",
    "limit-up-reseal-relay",
    "high-range-breakout-momentum",
    "dragon-tiger-smart-money",
]

# 只用有 stock_quotes 数据的日期
DATES = [
    "2026-07-09",
    "2026-07-08",
    "2026-07-07",
    "2026-07-06",
    "2026-07-03",
    "2026-07-02",
    "2026-07-01",
]

def submit_run(skill_id: str, trade_date: str) -> str | None:
    strategy_name = STRATEGY_NAMES.get(skill_id, skill_id)
    payload = {
        "single_skill_id": skill_id,
        "run_name": f"历史覆盖度补跑 {strategy_name} {trade_date}",
        "run_mode": "experiment",
        "data_source": "local_pool",
        "trade_date_override": trade_date,
        "score_line": 60,
        "skip_aux_refresh": True,
        "disable_agent_review": True,
        "output_count": 10,
    }
    try:
        r = requests.post(f"{BASE_URL}/api/runs", json=payload, timeout=15)
        r.raise_for_status()
        return r.json().get("run", {}).get("run_id")
    except Exception as e:
        print(f"  提交失败: {e}")
        return None


def wait_run(run_id: str, timeout_s: int = 300) -> tuple[str, int]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(8)
        try:
            r = requests.get(f"{BASE_URL}/api/runs/{run_id}", timeout=10)
            run = r.json()  # GET /api/runs/{id} 直接返回 run 对象
            st = run.get("status", "?")
            rec = int(run.get("recommended_count") or 0)
            if st in ("completed", "failed", "interrupted"):
                return st, rec
        except Exception:
            pass
    return "timeout", 0


def main():
    total_submitted = 0
    total_rec = 0
    results = []

    for trade_date in DATES:
        for skill_id in LOW_COVERAGE:
            print(f"  提交: {trade_date} [{skill_id}] ...", end=" ", flush=True)
            run_id = submit_run(skill_id, trade_date)
            if not run_id:
                print("ERR")
                continue
            total_submitted += 1
            st, rec = wait_run(run_id)
            print(f"→ {st} rec={rec}")
            total_rec += rec
            results.append({"date": trade_date, "skill": skill_id, "run_id": run_id, "status": st, "rec": rec})
            # 每次跑完稍微喘气
            time.sleep(2)

    print(f"\n=== 完成 ===")
    print(f"提交: {total_submitted} | 总推荐: {total_rec}")

    # 按策略汇总
    from collections import defaultdict
    by_skill: dict = defaultdict(lambda: {"rec": 0, "ok": 0, "fail": 0})
    for r in results:
        s = by_skill[r["skill"]]
        s["rec"] += r["rec"]
        if r["status"] == "completed":
            s["ok"] += 1
        else:
            s["fail"] += 1
    print()
    print(f"{'策略':<40} {'完成':>5} {'失败':>5} {'总推荐':>8}")
    print("-" * 65)
    for sk, v in sorted(by_skill.items()):
        print(f"{sk:<40} {v['ok']:>5} {v['fail']:>5} {v['rec']:>8}")


if __name__ == "__main__":
    main()
