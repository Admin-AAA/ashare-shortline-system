"""验证 calibrator 的 code 过滤查询在 MySQL 9.6 下可执行（REGEXP 语法）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, func

from app.db.session import get_db
from app.models.tables import Recommendation, RecommendationOutcome


def main() -> None:
    db = next(get_db())
    try:
        # 与 calibrator._load_labeled_samples 相同的过滤条件
        stmt = (
            select(func.count())
            .select_from(Recommendation)
            .join(
                RecommendationOutcome,
                Recommendation.recommendation_id == RecommendationOutcome.recommendation_id,
            )
            .where(
                RecommendationOutcome.outcome_label.in_(["success", "failed"]),
                RecommendationOutcome.entry_triggered.is_(True),
                Recommendation.code.regexp_match(r"^(?:[03468][0-9]{5}|92[0-9]{4})$"),
            )
        )
        n_valid = db.execute(stmt).scalar()
        print(f"合法 code 样本数（regexp 过滤后）: {n_valid}")

        # 对比：不过滤时
        stmt2 = (
            select(func.count())
            .select_from(Recommendation)
            .join(
                RecommendationOutcome,
                Recommendation.recommendation_id == RecommendationOutcome.recommendation_id,
            )
            .where(
                RecommendationOutcome.outcome_label.in_(["success", "failed"]),
                RecommendationOutcome.entry_triggered.is_(True),
            )
        )
        n_all = db.execute(stmt2).scalar()
        print(f"未过滤样本数: {n_all}（差 {n_all - n_valid} 条为非法 code，应为 2 条 diag）")

        # 列出被排除的 code
        stmt3 = (
            select(Recommendation.code, Recommendation.name, RecommendationOutcome.outcome_label)
            .join(
                RecommendationOutcome,
                Recommendation.recommendation_id == RecommendationOutcome.recommendation_id,
            )
            .where(
                RecommendationOutcome.outcome_label.in_(["success", "failed"]),
                RecommendationOutcome.entry_triggered.is_(True),
            )
        )
        for code, name, label in db.execute(stmt3):
            if not (len(str(code)) == 6 and str(code).isdigit()):
                print(f"  被排除: code={code} name={name} label={label}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
