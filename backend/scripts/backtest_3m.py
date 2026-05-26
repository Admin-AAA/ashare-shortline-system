#!/usr/bin/env python3
"""3个月回测 — 直接调 service，纯本地数据，不做参数对比"""
import sys, logging, json, time
sys.path.insert(0, '.')
from app.db.session import SessionLocal
from app.services.overnight_holding_service import (
    run_overnight_holding_backtest,
    _MINUTE_FREQ_PRIORITY,
    _FORCE_LOCAL_ONLY,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

logger.info("FREQ_PRIORITY=%s FORCE_LOCAL=%s", _MINUTE_FREQ_PRIORITY, _FORCE_LOCAL_ONLY)

db = SessionLocal()
t0 = time.perf_counter()
result = run_overnight_holding_backtest(db, start_date='2026-03-01', end_date='2026-05-25')
elapsed = time.perf_counter() - t0

metrics = result.get('metrics', {})
print(json.dumps({
    'elapsed_s': round(elapsed, 1),
    'trade_count': metrics.get('trade_count'),
    'avg_net_return': metrics.get('avg_net_return'),
    'med_net_return': metrics.get('med_net_return'),
    'profit_loss_ratio': metrics.get('profit_loss_ratio'),
    'max_drawdown': metrics.get('max_drawdown'),
    'recommendation_accuracy': metrics.get('recommendation_accuracy'),
    'positive_month_ratio': metrics.get('positive_month_ratio'),
    'benchmark_excess': metrics.get('benchmark_excess_return'),
    'date_range': result.get('date_range'),
    'signal_dates': result.get('signal_dates'),
    'source_summary': {s.get('source'): s.get('row_count') for s in (result.get('minute_source_trace') or [])[:50]},
}, indent=2, ensure_ascii=False))
db.close()
