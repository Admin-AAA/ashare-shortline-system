# 三窗 WR70 优化归档（2026-09-20）

本轮没有修改生产策略、策略版本、默认政策或执行政策。五轮方向全部归档为 `FALSIFIED`，因此没有执行顺序回放、生产发布、服务更新或页面产物更新。

新增三个只读研究入口：

- `research_wr70_entry_guard.py`：策略分组、卡片质量和市场日级保护筛查。
- `research_wr70_exit_screen.py`：canonical pure execution 的止盈与 trailing 研究。
- `research_wr70_baseline_compare.py`：生产 outcome 路径与 pure execution 的逐笔差异检查。

新增回归测试覆盖空成交窗口与 execution v5 基线身份。研究输出保存在 `backend/output/wr70/`，最终指标和报告保存在 `output/`。
