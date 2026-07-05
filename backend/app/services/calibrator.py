import json
import math
import random
from itertools import product
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.aggregator import AGG_WEIGHT_KEYS, COMPONENT_KEYS, DEFAULT_AGG_WEIGHTS
from app.models.tables import Recommendation, RecommendationOutcome
from app.services.outcome_service import is_accurate_recommendation


DEFAULT_DSA_WEIGHTS = {
    "dsa_w_trend": 1.0,
    "dsa_w_bias": 1.0,
    "dsa_w_volume": 1.0,
    "dsa_w_support": 1.0,
    "dsa_w_macd": 1.0,
    "dsa_w_rsi": 1.0,
}
# 校准诊断（第109轮 T3，1584 笔样本，时间分割验证）:
#   全样本 point-biserial: trend=-0.038/-0.084(两半都负), volume=-0.103/-0.144(两半都负)
#   → trend/volume 移除安全（两半都负相关，无预测力）
#   bias/support/macd/rsi 前期/近期矛盾（bias: +0.183→-0.040, support: +0.298→-0.071, rsi: -0.016→+0.188）
#   → 按 alpha-decay-false-positive 教训，矛盾时不调整
# 未来优化：可安全设 trend/volume 权重=0，但需反事实验证推荐排序是否改善

DSA_WEIGHT_KEYS = [
    "dsa_w_trend",
    "dsa_w_bias",
    "dsa_w_volume",
    "dsa_w_support",
    "dsa_w_macd",
    "dsa_w_rsi",
]

DSA_SCORE_KEYS = [
    "dsa_trend_score",
    "dsa_bias_score",
    "dsa_volume_score",
    "dsa_support_score",
    "dsa_macd_score",
    "dsa_rsi_score",
]

RANDOM_SAMPLE_COUNT = 5000
WEIGHT_MIN = 0.02
WEIGHT_MAX = 0.50
TOP_PERCENTILE = 0.2


def _load_labeled_samples(db: Session) -> list[dict[str, Any]]:
    """Load all recommendations that have outcomes labeled.

    Includes trade_date for time-based splitting and close_return for
    enhanced composite scoring.
    """
    stmt = (
        select(Recommendation, RecommendationOutcome)
        .join(RecommendationOutcome, Recommendation.recommendation_id == RecommendationOutcome.recommendation_id)
        .where(
            RecommendationOutcome.outcome_label.in_(["success", "failed"]),
            RecommendationOutcome.entry_triggered.is_(True),
        )
    )
    rows = db.execute(stmt).all()
    samples = []
    for rec, outcome in rows:
        try:
            snapshot = json.loads(rec.feature_snapshot_json or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        component_scores = snapshot.get("component_scores")
        if not component_scores:
            continue
        samples.append({
            "recommendation_id": rec.recommendation_id,
            "code": rec.code,
            "trade_date": rec.trade_date,
            "component_scores": component_scores,
            "label": 1 if is_accurate_recommendation(outcome) else 0,
            "close_return": float(outcome.executed_return if outcome.executed_return is not None else (outcome.close_return or 0)),
        })
    return samples


def _roc_auc(scores: list[float], labels: list[int]) -> float:
    """Compute ROC AUC given predicted scores and binary labels."""
    paired = sorted(zip(scores, labels), key=lambda x: x[0], reverse=True)
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    auc = 0.0
    fp = 0
    tp = 0
    fp_prev = 0
    tp_prev = 0
    prev_score = None
    for score, label in paired:
        if score != prev_score:
            auc += (fp - fp_prev) * (tp + tp_prev) / 2.0
            prev_score = score
            fp_prev = fp
            tp_prev = tp
        if label == 1:
            tp += 1
        else:
            fp += 1
    auc += (fp - fp_prev) * (tp + tp_prev) / 2.0
    return auc / (n_pos * n_neg) if n_pos * n_neg > 0 else 0.5


def _composite_score(scores: list[float], labels: list[int], close_returns: list[float] | None = None) -> float:
    """Composite metric for calibration: Win Rate, optionally boosted by returns.

    - Computes the win rate of the top 20% predictions.
    - When close_returns is provided, the metric becomes:
        Win Rate x (1 + Avg Close Return of the top 20% picks).

    Returns a score in [0.0, ...). Higher is better.
    """
    n = len(scores)
    if n < 5:
        return 0.0

    threshold_index = max(0, int(n * TOP_PERCENTILE) - 1)
    threshold = sorted(scores, reverse=True)[threshold_index]

    predictions = [1 if s >= threshold else 0 for s in scores]
    tp = sum(1 for p, l in zip(predictions, labels) if p == 1 and l == 1)
    fp = sum(1 for p, l in zip(predictions, labels) if p == 1 and l == 0)

    denom = max(1, tp + fp)
    win_rate = tp / denom

    if close_returns is not None and len(close_returns) == n:
        picked_returns = [
            r for s, r in zip(scores, close_returns) if s >= threshold
        ]
        avg_return = sum(picked_returns) / max(1, len(picked_returns))
        return win_rate * (1.0 + avg_return)

    return win_rate


def _generate_weight_grid(n_components: int, steps: int = 5) -> list[list[float]]:
    """Generate weight combinations that sum to 1.0 using a step-based grid.

    Uses equal-step increments for the first n-1 weights, with the last
    weight determined by the constraint. Only keeps valid combinations
    where all weights are in [WEIGHT_MIN, WEIGHT_MAX].

    Kept as a fallback when the data sample count is too low for random search.
    """
    step_size = 1.0 / steps
    candidates = [round(i * step_size, 3) for i in range(steps + 1)]
    grids = []
    for combo in product(candidates, repeat=n_components - 1):
        remainder = round(1.0 - sum(combo), 3)
        if remainder < WEIGHT_MIN or remainder > WEIGHT_MAX:
            continue
        weights = list(combo) + [remainder]
        if all(WEIGHT_MIN <= w <= WEIGHT_MAX for w in weights) and abs(sum(weights) - 1.0) < 0.001:
            grids.append(weights)
    return grids


def _generate_weight_random(n_components: int, n_samples: int = RANDOM_SAMPLE_COUNT) -> list[list[float]]:
    """Generate weight combinations by random sampling then normalizing.

    Each weight is drawn from Uniform(WEIGHT_MIN, WEIGHT_MAX), then
    all weights are normalized so the sum equals 1.0.
    """
    grids = []
    seen = set()
    rng = random.Random(42)  # fixed seed for reproducibility
    max_attempts = n_samples * 3  # avoid infinite loop
    attempts = 0
    while len(grids) < n_samples and attempts < max_attempts:
        raw = [rng.uniform(WEIGHT_MIN, WEIGHT_MAX) for _ in range(n_components)]
        total = sum(raw)
        weights = [round(w / total, 6) for w in raw]
        # re-normalize to exactly 1.0
        delta = 1.0 - sum(weights)
        weights[0] = round(weights[0] + delta, 6)
        # verify bounds
        if all(WEIGHT_MIN - 0.001 <= w <= WEIGHT_MAX + 0.001 for w in weights):
            key = tuple(round(w, 4) for w in weights)
            if key not in seen:
                seen.add(key)
                grids.append(weights)
        attempts += 1
    return grids


def _split_by_date(samples: list[dict[str, Any]], train_ratio: float = 0.7) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split samples into training and test sets by trade_date order.

    Sorts samples by trade_date ascending, then takes the first
    train_ratio fraction for training and the remainder for testing.
    """
    sorted_samples = sorted(samples, key=lambda s: s.get("trade_date", ""))
    split_index = int(len(sorted_samples) * train_ratio)
    train = sorted_samples[:split_index]
    test = sorted_samples[split_index:]
    return train, test


def calibrate_aggregation_weights(db: Session, steps: int = 5) -> dict[str, Any]:
    """Calibrate aggregation weights using Random Search to maximize composite score.

    Splits data by trade_date into train/test. Searches weight space via
    random sampling (default 5000 combinations), evaluating against the
    composite metric (Win Rate x (1 + Avg Close Return)). Falls back to
    grid search when labeled samples are below 50.

    Returns dict with optimal_weights, composite_score, roc_auc,
    sample counts, and default comparison.
    """
    samples = _load_labeled_samples(db)
    n_total = len(samples)

    if n_total < 10:
        return {
            "optimal_weights": dict(DEFAULT_AGG_WEIGHTS),
            "composite_score": 0.0,
            "roc_auc": 0.0,
            "train_sample_count": 0,
            "test_sample_count": 0,
            "total_sample_count": n_total,
            "error": "Insufficient labeled samples (need >= 10)",
            "default_weights": dict(DEFAULT_AGG_WEIGHTS),
        }

    # Time-based train/test split
    train_samples, test_samples = _split_by_date(samples, train_ratio=0.7)

    n_train = len(train_samples)
    n_test = len(test_samples)

    train_labels = [s["label"] for s in train_samples]
    train_close_returns = [s["close_return"] for s in train_samples]
    train_matrix = []
    for s in train_samples:
        cs = s["component_scores"]
        train_matrix.append([float(cs.get(k, 0)) for k in COMPONENT_KEYS])

    test_labels = [s["label"] for s in test_samples]
    test_close_returns = [s["close_return"] for s in test_samples]
    test_matrix = []
    for s in test_samples:
        cs = s["component_scores"]
        test_matrix.append([float(cs.get(k, 0)) for k in COMPONENT_KEYS])

    # Compute default weights performance for comparison
    if n_train > 0:
        default_train_scores = [
            sum(DEFAULT_AGG_WEIGHTS[k] * train_matrix[i][j]
                for j, k in enumerate(AGG_WEIGHT_KEYS))
            for i in range(n_train)
        ]
        default_train_composite = _composite_score(default_train_scores, train_labels, train_close_returns)
    else:
        default_train_composite = 0.0

    if n_test > 0:
        default_test_scores = [
            sum(DEFAULT_AGG_WEIGHTS[k] * test_matrix[i][j]
                for j, k in enumerate(AGG_WEIGHT_KEYS))
            for i in range(n_test)
        ]
        default_test_composite = _composite_score(default_test_scores, test_labels, test_close_returns)
        default_test_auc = _roc_auc(default_test_scores, test_labels)
    else:
        default_test_composite = 0.0
        default_test_auc = 0.0

    n_components = len(COMPONENT_KEYS)

    # Choose search strategy: random for sufficient data, grid as fallback
    if n_train >= 50:
        weight_grid = _generate_weight_random(n_components, n_samples=RANDOM_SAMPLE_COUNT)
        search_method = "random"
    else:
        weight_grid = _generate_weight_grid(n_components, steps=steps)
        search_method = "grid"

    best_auc = 0.0
    best_weights = list(DEFAULT_AGG_WEIGHTS.values())

    for weights in weight_grid:
        train_scores = [
            sum(w * train_matrix[i][j] for j, w in enumerate(weights))
            for i in range(n_train)
        ]
        train_auc = _roc_auc(train_scores, train_labels)
        if train_auc > best_auc:
            best_auc = train_auc
            best_weights = weights

    optimal = {key: round(w, 4) for key, w in zip(AGG_WEIGHT_KEYS, best_weights)}

    # Evaluate optimal weights on test set
    test_optimal_composite = 0.0
    test_optimal_auc = 0.0
    if n_test > 0:
        test_optimal_scores = [
            sum(optimal[k] * test_matrix[i][j] for j, k in enumerate(AGG_WEIGHT_KEYS))
            for i in range(n_test)
        ]
        test_optimal_composite = round(_composite_score(
            test_optimal_scores, test_labels, test_close_returns
        ), 4)
        test_optimal_auc = round(_roc_auc(test_optimal_scores, test_labels), 4)

    return {
        "optimal_weights": optimal,
        "train_roc_auc": round(best_auc, 4),
        "test_composite_score": test_optimal_composite,
        "test_roc_auc": test_optimal_auc,
        "train_sample_count": n_train,
        "test_sample_count": n_test,
        "total_sample_count": n_total,
        "search_method": search_method,
        "search_iterations": len(weight_grid),
        "default_weights": dict(DEFAULT_AGG_WEIGHTS),
        "default_train_composite_score": round(default_train_composite, 4),
        "default_test_composite_score": round(default_test_composite, 4),
        "default_test_roc_auc": round(default_test_auc, 4),
    }


def _point_biserial_corr(scores: list[float], labels: list[int]) -> float:
    """Compute point-biserial correlation coefficient."""
    n = len(scores)
    if n < 2:
        return 0.0
    n1 = sum(labels)
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        return 0.0
    mean1 = sum(s for s, l in zip(scores, labels) if l == 1) / n1
    mean0 = sum(s for s, l in zip(scores, labels) if l == 0) / n0
    mean_all = sum(scores) / n
    sd = math.sqrt(sum((s - mean_all) ** 2 for s in scores) / n)
    if sd == 0:
        return 0.0
    return (mean1 - mean0) / sd * math.sqrt(n1 * n0 / (n * n))


def calibrate_dsa_weights(db: Session) -> dict[str, Any]:
    """Calibrate DSA sub-score weights using point-biserial correlation with success.

    Returns dict with optimal_weights, correlations, and sample_count.
    """
    stmt = (
        select(Recommendation, RecommendationOutcome)
        .join(RecommendationOutcome, Recommendation.recommendation_id == RecommendationOutcome.recommendation_id)
        .where(
            RecommendationOutcome.outcome_label.in_(["success", "failed"]),
            RecommendationOutcome.entry_triggered.is_(True),
        )
    )
    rows = db.execute(stmt).all()

    dsa_data = {key: [] for key in DSA_SCORE_KEYS}
    labels = []

    for rec, outcome in rows:
        try:
            snapshot = json.loads(rec.feature_snapshot_json or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        has_dsa = False
        for key in DSA_SCORE_KEYS:
            if key in snapshot:
                dsa_data[key].append(float(snapshot[key]))
                has_dsa = True
        if has_dsa:
            labels.append(1 if is_accurate_recommendation(outcome) else 0)
        else:
            for key in DSA_SCORE_KEYS:
                if dsa_data[key]:
                    dsa_data[key].pop()

    if len(labels) < 10:
        return {
            "optimal_weights": dict(DEFAULT_DSA_WEIGHTS),
            "correlations": {},
            "sample_count": len(labels),
            "error": "Insufficient labeled samples (need >= 10)",
            "default_weights": dict(DEFAULT_DSA_WEIGHTS),
        }

    correlations = {}
    for key in DSA_SCORE_KEYS:
        scores = dsa_data[key]
        if len(scores) == len(labels) and set(scores) != {0}:
            correlations[key] = round(_point_biserial_corr(scores, labels), 4)
        else:
            correlations[key] = 0.0

    # Convert correlations to weights: normalized positive correlations
    positive_corrs = {k: max(0.0, v) for k, v in correlations.items()}
    total = sum(positive_corrs.values())
    if total > 0:
        optimal = {f"dsa_w_{k.split('_')[1]}": round(v / total, 4) for k, v in positive_corrs.items()}
    else:
        optimal = dict(DEFAULT_DSA_WEIGHTS)

    return {
        "optimal_weights": optimal,
        "correlations": {f"dsa_w_{k.split('_')[1]}": v for k, v in correlations.items()},
        "sample_count": len(labels),
        "default_weights": dict(DEFAULT_DSA_WEIGHTS),
    }


def walk_forward_calibrate(db: Session, n_windows: int = 3) -> dict[str, Any]:
    """Walk-forward calibration across rolling time windows.

    Splits labeled samples into n_windows chronological folds, performs
    calibration on expanding or rolling windows, and returns per-window
    optimal weights together with average performance metrics.

    Args:
        db: Database session.
        n_windows: Number of chronological windows (default 3).

    Returns:
        dict with keys: n_windows, windows (list of per-window results),
        avg_test_composite_score, avg_test_roc_auc.
    """
    samples = _load_labeled_samples(db)
    sorted_samples = sorted(samples, key=lambda s: s.get("trade_date", ""))

    if len(sorted_samples) < 10 * n_windows:
        return {
            "n_windows": n_windows,
            "windows": [],
            "avg_test_composite_score": 0.0,
            "avg_test_roc_auc": 0.0,
            "error": f"Insufficient samples for {n_windows} windows (need >= {10 * n_windows})",
        }

    window_size = len(sorted_samples) // n_windows
    windows = []
    composite_scores = []
    roc_aucs = []

    n_components = len(COMPONENT_KEYS)

    for w in range(n_windows):
        train_end = (w + 1) * window_size
        if train_end >= len(sorted_samples):
            break
        train = sorted_samples[:train_end]
        test_start = train_end
        test_end = min(test_start + window_size, len(sorted_samples))
        test = sorted_samples[test_start:test_end]

        if len(train) < 10 or len(test) < 5:
            continue

        # Search for best weights on training set
        train_labels = [s["label"] for s in train]
        train_returns = [s["close_return"] for s in train]
        train_matrix = []
        for s in train:
            cs = s["component_scores"]
            train_matrix.append([float(cs.get(k, 0)) for k in COMPONENT_KEYS])

        if len(train) >= 50:
            weight_grid = _generate_weight_random(n_components)
        else:
            weight_grid = _generate_weight_grid(n_components)

        best_auc = 0.0
        best_weights = list(DEFAULT_AGG_WEIGHTS.values())
        for weights in weight_grid:
            train_scores = [
                sum(w * train_matrix[i][j] for j, w in enumerate(weights))
                for i in range(len(train))
            ]
            train_auc = _roc_auc(train_scores, train_labels)
            if train_auc > best_auc:
                best_auc = train_auc
                best_weights = weights

        optimal_w = {key: round(w, 4) for key, w in zip(AGG_WEIGHT_KEYS, best_weights)}

        # Evaluate on test window
        test_labels = [s["label"] for s in test]
        test_returns = [s["close_return"] for s in test]
        test_matrix = []
        for s in test:
            cs = s["component_scores"]
            test_matrix.append([float(cs.get(k, 0)) for k in COMPONENT_KEYS])

        test_scores = [
            sum(optimal_w[k] * test_matrix[i][j] for j, k in enumerate(AGG_WEIGHT_KEYS))
            for i in range(len(test))
        ]
        test_comp = round(_composite_score(test_scores, test_labels, test_returns), 4)
        test_auc = round(_roc_auc(test_scores, test_labels), 4)

        windows.append({
            "window": w + 1,
            "train_sample_count": len(train),
            "test_sample_count": len(test),
            "optimal_weights": optimal_w,
            "test_composite_score": test_comp,
            "test_roc_auc": test_auc,
        })
        composite_scores.append(test_comp)
        roc_aucs.append(test_auc)

    return {
        "n_windows": n_windows,
        "windows": windows,
        "avg_test_composite_score": round(sum(composite_scores) / max(1, len(composite_scores)), 4),
        "avg_test_roc_auc": round(sum(roc_aucs) / max(1, len(roc_aucs)), 4),
    }
