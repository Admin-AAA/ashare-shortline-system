#!/bin/bash
# 一键部署并验证数据同步修复
# 当云端服务器恢复后执行此脚本
set -e

SERVER="ashare-server"
PROJECT="/opt/ashare-shortline-system/backend"
LOCAL="/Users/wangao/Desktop/ashare-shortline-system"

echo "=========================================="
echo "Phase 1: 部署代码修改"
echo "=========================================="

# 部署核心服务文件
scp \
  "$LOCAL/backend/app/services/scheduler_service.py" \
  "$LOCAL/backend/app/services/feature_builder.py" \
  "$LOCAL/backend/app/services/research_context.py" \
  "$LOCAL/backend/app/services/shortline_factors.py" \
  "$SERVER:$PROJECT/app/services/"

# 部署脚本文件
scp \
  "$LOCAL/backend/scripts/backfill_auxiliary.py" \
  "$LOCAL/backend/scripts/backfill_auxiliary_data.py" \
  "$LOCAL/backend/scripts/fix_all_date_formats.py" \
  "$LOCAL/backend/scripts/fix_duplicate_dates.py" \
  "$LOCAL/backend/scripts/sync_minute_kbars_latest.py" \
  "$SERVER:$PROJECT/scripts/"

echo "部署完成"

echo ""
echo "=========================================="
echo "Phase 2: 重启后端服务"
echo "=========================================="
ssh "$SERVER" "systemctl restart ashare-backend && sleep 3 && systemctl status ashare-backend --no-pager | head -5"
echo "服务已重启"

echo ""
echo "=========================================="
echo "Phase 3: 更新调度器 Payload (days: 120 → 20)"
echo "=========================================="
ssh "$SERVER" "mysql -h139.196.242.53 -P4407 -uaowang -p1279990918 ashare_workbench -e \"
  UPDATE scheduled_tasks
  SET payload_json = JSON_REPLACE(payload_json, '\$.days', 20)
  WHERE task_id = 'postclose_tushare_data_sync' AND JSON_EXTRACT(payload_json, '\$.days') = 120;
  SELECT task_id, payload_json FROM scheduled_tasks;
\""
echo "Payload 已优化"

echo ""
echo "=========================================="
echo "Phase 4: 清理异常日期数据 (2026-05-30)"
echo "=========================================="
ssh "$SERVER" "mysql -h139.196.242.53 -P4407 -uaowang -p1279990918 ashare_workbench -e \"
  DELETE FROM daily_kbars WHERE trade_date = '2026-05-30';
  SELECT COUNT(*) as remaining FROM daily_kbars WHERE trade_date = '2026-05-30';
\""
echo "异常数据已清理"

echo ""
echo "=========================================="
echo "Phase 5: 日K缺口补齐 (2026-05-29)"
echo "=========================================="
# 用慢速限流回填，避免打爆 Tushare
for batch in 0 300 600 900 1200; do
  echo "--- Batch offset=$batch, limit=300, rate_limit=10 ---"
  ssh "$SERVER" "cd $PROJECT && .venv/bin/python scripts/backfill_daily_bars.py \
    --days 15 --min-bars 5 --commit-interval 50 \
    --limit 300 --offset $batch --rate-limit 10 --force --reset-progress" || true
done

echo ""
echo "=========================================="
echo "Phase 6: 补齐 minute_kbars 缺口"
echo "=========================================="
ssh "$SERVER" "cd $PROJECT && .venv/bin/python scripts/sync_minute_kbars_latest.py --days 3 --top-n 100" || true

echo ""
echo "=========================================="
echo "Phase 7: 补齐新闻公告"
echo "=========================================="
ssh "$SERVER" "cd $PROJECT && .venv/bin/python scripts/backfill_auxiliary_data.py \
  --skip-concepts --skip-dragon-tiger --skip-capital-flows \
  --skip-tushare-aux --skip-tushare-new \
  --news-top-n 500 --trade-date 2026-05-29" || true

echo ""
echo "=========================================="
echo "Phase 8: 数据健康检查"
echo "=========================================="
ssh "$SERVER" "cd $PROJECT && .venv/bin/python scripts/check_data_health.py"

echo ""
echo "=========================================="
echo "Phase 9: 数据覆盖率检查"
echo "=========================================="
ssh "$SERVER" "mysql -h139.196.242.53 -P4407 -uaowang -p1279990918 ashare_workbench -e \"
SELECT 'daily_kbars' as tbl, MAX(trade_date) as latest, COUNT(DISTINCT code) as codes FROM daily_kbars
UNION ALL SELECT 'stock_quotes', MAX(trade_date), COUNT(DISTINCT code) FROM stock_quotes
UNION ALL SELECT 'limit_up_board', MAX(trade_date), COUNT(DISTINCT code) FROM limit_up_board
UNION ALL SELECT 'concept_sector_stock', MAX(trade_date), COUNT(DISTINCT code) FROM concept_sector_stock
UNION ALL SELECT 'index_daily', MAX(trade_date), COUNT(DISTINCT ts_code) FROM index_daily
UNION ALL SELECT 'capital_flow', MAX(trade_date), COUNT(DISTINCT code) FROM capital_flow
UNION ALL SELECT 'dragon_tiger', MAX(trade_date), COUNT(DISTINCT code) FROM dragon_tiger
UNION ALL SELECT 'news_announcement', MAX(announce_date), COUNT(*) FROM news_announcement
UNION ALL SELECT 'minute_kbars', MAX(trade_date), COUNT(DISTINCT code) FROM minute_kbars;
\""

echo ""
echo "=========================================="
echo "ALL DONE. 现在可以本地运行推荐链路验证。"
echo "=========================================="
