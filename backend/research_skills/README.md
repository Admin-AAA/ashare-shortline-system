# research_skills/

研究原型策略（生产不加载）。`skill_loader.load_skills(settings.skills_dir)` 只读 `skills/`；
仅当回放进程设置 `ASHARE_RESEARCH_EXTRA_SKILLS=<id,...>` 时才把这里点名的目录追加进来，例如：

    ASHARE_RESEARCH_EXTRA_SKILLS=research-ladder-relay python scripts/research_strategy_ablation_fast.py ...

已下线的旧策略（dragon-tiger-reaccumulation、trend-pullback-continuation）与 scripts/archive 已删除，需要时从 git 历史恢复。
