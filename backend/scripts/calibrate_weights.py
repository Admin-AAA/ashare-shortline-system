#!/usr/bin/env python3
"""校准权重：用 recommendation_outcomes 数据优化 aggregator 和 DSA 权重。

用法:
    python scripts/calibrate_weights.py              # 仅显示结果
    python scripts/calibrate_weights.py --apply      # 写入 calibration_config 表
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import get_db
from app.models.tables import CalibrationConfig
from app.services.calibrator import calibrate_aggregation_weights, calibrate_dsa_weights


def main():
    parser = argparse.ArgumentParser(description="校准聚合权重")
    parser.add_argument("--apply", action="store_true", help="写入 calibration_config 表")
    args = parser.parse_args()

    db = next(get_db())
    try:
        print("=== 聚合权重校准 ===")
        agg = calibrate_aggregation_weights(db, steps=5)
        print(f"样本量: {agg.get('total_sample_count', 0)}")
        print(f"默认 ROC AUC: {agg.get('default_test_roc_auc', 0):.4f}")
        print(f"校准 ROC AUC: {agg.get('test_roc_auc', 0):.4f}")
        print(f"最优权重:")
        for k, v in agg.get("optimal_weights", {}).items():
            default_v = agg.get("default_weights", {}).get(k, 0)
            delta = v - default_v
            print(f"  {k}: {default_v:.4f} → {v:.4f} ({delta:+.4f})")

        print("\n=== DSA 子分数权重校准 ===")
        dsa = calibrate_dsa_weights(db)
        print(f"样本量: {dsa.get('sample_count', 0)}")
        print(f"最优权重:")
        for k, v in dsa.get("optimal_weights", {}).items():
            corr = dsa.get("correlations", {}).get(k, 0)
            print(f"  {k}: {v:.4f} (r={corr:+.4f})")

        if args.apply:
            print("\n写入 calibration_config ...")
            from datetime import datetime
            now = datetime.now()
            all_weights = {}
            all_weights.update(agg.get("optimal_weights", {}))
            all_weights.update(dsa.get("optimal_weights", {}))
            for k, v in all_weights.items():
                db.merge(CalibrationConfig(key=k, value=v, updated_at=now))
            db.commit()
            print(f"已写入 {len(all_weights)} 条配置")
        else:
            print("\n提示: 加 --apply 可写入数据库")
    finally:
        db.close()


if __name__ == "__main__":
    main()
