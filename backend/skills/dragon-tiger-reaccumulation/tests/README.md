# 历史回归验证

测试使用本地 MySQL 中的历史日线、龙虎榜与历史股票身份，核对未来数据隔离、信号日龙虎榜隔离、缺失日线拒绝生成信号，以及策略默认停用。

```bash
backend/.venv/bin/python -m pytest backend/tests/test_dragon_tiger_reaccumulation.py -q
```

真实历史样本包括 2025-02-05 的 000737、2026-01-19 的 603333、2026-08-04 的 002300。测试只查询数据，不写入数据库。
