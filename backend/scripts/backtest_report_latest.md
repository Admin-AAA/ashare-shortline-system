# 成本感知回测报告 (Cost-Aware Backtest)

- 样本(原始 outcome 行): 1594
- 样本(去重后 (trade_date,code)): 291
- 单笔交易成本: 0.1% (佣金万2.5双边+印花税0.05%卖出)

## 核心指标 (净收益=executed_return-成本)

| 指标 | 值 | 目标 |
|------|-----|------|
| 胜率(净) | 66.0% | ≥50% |
| 平均净收益 | +3.03% | — |
| 盈亏比(净) | 1.98 | ≥1.4 |
| Sharpe(每笔) | 0.517 | — |
| 最大亏损(净) | -7.17% | — |
| 平均持仓天数 | 4.8 | — |

## 出场分布

- take_profit_hit: 122 (41.9%)
- trailing_stop_hit: 117 (40.2%)
- stop_loss_hit: 44 (15.1%)
- take_profit_gap: 7 (2.4%)
- stop_loss_gap: 1 (0.3%)

## 策略归因 (按平均净收益排序)

| 策略 | 命中数 | 净胜率 | 平均净收益 |
|------|--------|--------|------------|
| sector-relative-strength-leader | 23 | 73.9% | +5.73% |
| chao-gu-yang-jia-skill | 29 | 79.3% | +4.41% |
| chen-xiaoqun-skill | 24 | 70.8% | +3.22% |
| daily-stock-analysis-skill | 214 | 67.3% | +2.96% |
| overnight-holding-method | 8 | 50.0% | +2.28% |
| high-range-breakout-momentum | 10 | 30.0% | +0.92% |
| trend-pullback-continuation | 3 | 33.3% | -1.54% |
| limit-up-reseal-relay | 5 | 20.0% | -1.82% |

## 相位信息量检验 (T5 一致性)

- 各相位胜率极差: 19.0pp
- 相位是否携带信息量: 是

| 相位 | 样本数 | 净胜率 | 平均净收益 |
|------|--------|--------|------------|
| 高潮 | 175 | 62.9% | +2.90% |
| 活跃 | 103 | 72.8% | +3.27% |
| unknown | 13 | 53.8% | +2.90% |
