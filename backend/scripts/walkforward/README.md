# Walk-forward 研究工具（2026-10-13 round1）

在 `backend/` 目录下运行，依赖 stage5 面板 `output/research/stage5/panel6/entry_panel_WX.json`：

1. `python scripts/walkforward/precompute.py`：生成每日在各分数线下的合格卡 → `.cache/wf/eligible.json`
2. `python scripts/walkforward/bars.py`：只读拉取候选的后续日 K → `.cache/wf/bars.json`
3. `python scripts/walkforward/wf_selection.py`：分数线、上限、元标签重排/扩票/过滤的季度滚动 walk-forward
4. `python scripts/walkforward/wf_exit.py`：出场参数网格的季度滚动 walk-forward（用生产 `simulate`，基线可逐笔复现 net_by_code）

折：2025Q3 ~ 2026Q3（训练段为测试季度之前的全部数据，留 6 天禁区）。2026Q3 带 * 号，表示已被看过、受污染；2026-10-10 之后的数据留作最终检验。
