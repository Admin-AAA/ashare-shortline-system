# 新策略上线：自动回填最近 3 个月回放记录

状态：**已实现、默认关闭**（`REPLAY_AUTO_BACKFILL_ENABLED=0`）。全量重建完成后由 A股交易系统owner 打开。

## 中线 / 长线（midterm_service.STRATEGIES 新增一条后）
```
cd backend
REPLAY_AUTO_BACKFILL_ENABLED=1 .venv/bin/python scripts/rebuild_replay_picks.py --onboard <strategy_id> --execute
```
- 复用生产 `generate_picks`，只跑该策略，最近 3 个月逐交易日顺序生成（持仓去重与实盘一致）。
- 批次 JSON 带 `"origin": "replay"`、`replayed_at`；无 origin = 实盘。已存在的同名文件不覆盖。
- 不带 `--execute` 为试跑（临时目录，不写生产）。输出里 `no_data` 必须为空，否则多半是数据源限流，换时段重跑。
- 避开 16:35–18:35；不与全量 pytest / 其他大回测同时跑。

## 短线
短线回放按整个正式 preset 跑，新策略加入 preset 后执行：
```
.venv/bin/python scripts/replay_all_days.py --start <today-3个月> --end <最近交易日> --attempt-tag onboard-<strategy_id>
```
记录为 `evaluation_scope=historical_replay / evidence_class=historical_reconstruction`，与 live_forward 区分。

## 上线检查单（追加）
1. 回测达标 → 2. 加入 STRATEGIES / preset → 3. import 检查 + 定向测试 → 4. **运行上面的回填** → 5. push + 安全重启。
