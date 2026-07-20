"""归因 no_follow_through 真残留毒区（18 笔，均净 -4.65%）的入场特征。

no_follow_through 定义（self_check._classify_failure_mode_inline）：
  max_return <= 0.5 且 executed_return < 0 —— 买入后从未涨过 0.5%，一路阴跌止损。
  属「买入即套牢」，exit 侧无解（从没涨，trailing 不激活），只能 entry 侧过滤。

复用 self_check 的覆盖口径找出未被规则覆盖的失败笔，对其做 no_follow_through 归因，
并对比同期 success 样本的同口径特征分布，找可分界的入场特征。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from statistics import median

import pymysql

sys.path.insert(0, "/Users/wangao/Desktop/ashare-shortline-system/backend")
sys.path.insert(0, "/Users/wangao/Desktop/ashare-shortline-system/scripts")

from sqlalchemy import create_engine, text

import self_check as sc  # noqa: E402

ENGINE = create_engine(
    "mysql+pymysql://aowang:1279990918@127.0.0.1:3306/ashare_workbench?charset=utf8mb4"
)

FEATS = ["chg20", "pos60", "rsi_12", "vol5_20", "turnover_rate", "off_high_pct",
         "amount_yi", "max_dd20", "pct", "final_score"]
CATS = ["setup_type", "bucket", "market_regime", "market_sentiment_phase", "industry"]


def _fs(row):
    return json.loads(row[3]) if row[3] else {}


def _mode(fs, exit_reason, executed_return, max_return):
    return sc._classify_failure_mode_inline(
        exit_reason=exit_reason,
        executed_return=executed_return,
        max_return=max_return,
        rsi_12=fs.get("rsi_12"),
        chg20=fs.get("chg20"),
        pos60=fs.get("pos60"),
        setup_type=fs.get("setup_type"),
    )


def _dist(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return "n=0"
    s = sorted(vals)
    n = len(s)
    return (f"n={n} min={s[0]:.2f} p25={s[n//4]:.2f} med={median(s):.2f} "
            f"p75={s[3*n//4]:.2f} max={s[-1]:.2f}")


def main():
    skills = sc._load_skills_once()
    with ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.recommendation_id, o.outcome_label, o.executed_return,
                   r.feature_snapshot_json, r.skill_breakdown_json,
                   o.exit_reason, o.max_return
            FROM recommendation_outcomes o
            JOIN recommendations r ON r.recommendation_id = o.recommendation_id
            JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
            WHERE o.outcome_label IN ('success', 'failed')
              AND r.recommendation_id LIKE 'rec_%'
        """)).fetchall()

    uncovered = sc._collect_uncovered_failures(rows, skills)
    nft = []
    success = []
    for row in rows:
        label = row[1]
        fs = _fs(row)
        if label == "success":
            success.append(fs)
    for u in uncovered:
        mode = _mode(u["fs"], u["exit_reason"], u["executed_return"], u["max_return"])
        if mode == "no_follow_through":
            nft.append(u)

    print(f"未覆盖失败 {len(uncovered)} 笔，no_follow_through {len(nft)} 笔，success {len(success)} 笔\n")

    print("=== 数值特征分布：no_follow_through vs success ===")
    for f in FEATS:
        nft_vals = [u["fs"].get(f) for u in nft]
        suc_vals = [s.get(f) for s in success]
        print(f"\n[{f}]")
        print(f"  NFT : {_dist(nft_vals)}")
        print(f"  SUCC: {_dist(suc_vals)}")

    print("\n\n=== 类别特征分布：no_follow_through ===")
    for c in CATS:
        cnt = Counter(str(u["fs"].get(c)) for u in nft)
        succ_cnt = Counter(str(s.get(c)) for s in success)
        print(f"\n[{c}] NFT: {dict(cnt.most_common(8))}")
        print(f"       SUCC top: {dict(succ_cnt.most_common(5))}")

    print("\n\n=== 18 笔 NFT 明细 ===")
    for u in nft:
        fs = u["fs"]
        print(f"  {u['rid'][:22]} regime={fs.get('market_regime')} sent={fs.get('market_sentiment_phase')} "
              f"setup={fs.get('setup_type')} chg20={fs.get('chg20')} pos60={fs.get('pos60')} "
              f"rsi={fs.get('rsi_12')} vol5_20={fs.get('vol5_20')} turn={fs.get('turnover_rate')} "
              f"max_ret={u['max_return']} ret={round(u['executed_return'],2)}")


if __name__ == "__main__":
    main()
