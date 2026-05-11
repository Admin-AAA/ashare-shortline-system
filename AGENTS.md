# Agent Team Model Policy

When spawning or coordinating an agent team, prefer `gpt-5.5` for every agent whenever it is available.

- Apply this to all sub-agents created for team workflows.
- Use another model only when `gpt-5.5` is unavailable, unsuitable for a specific task, or the user explicitly requests a different one.

# Python Testing Policy

When testing a Python project, prefer the project's own virtual environment first.

- Use the virtual environment already configured inside the project whenever available.
- Only fall back to a global Python or another environment if the project does not provide a usable virtual environment.

# Architecture Policy

当前项目按最合适的架构来做，不保留向后兼容；不为了旧接口、旧文件结构或临时实现额外迁就。

The project is still in active development, so prioritize the most suitable architecture over backward compatibility, incremental migration, or interface stability.

- Choose the architecture that best fits the product direction and maintainability of the current system.
- Prefer clean module boundaries, coherent data flow, and simple long-term ownership over preserving temporary or legacy structures.
- Refactor boldly when it meaningfully improves the design, even if that changes existing APIs, file layout, or internal contracts.
- Do not optimize for stable iteration history, migration paths, or compatibility with unfinished earlier designs unless the user explicitly asks for it.

# Document Font Policy

Do not use `Courier` or `Courier New` in any generated or edited document, including but not limited to `.docx`, `.pdf`, templates, styles, hidden styles, or exported assets.

- Treat `Courier` and `Courier New` as disallowed fonts.
- When generating Word documents, ensure both visible content and internal styles/font tables do not reference `Courier` or `Courier New`.
- Prefer common non-restricted fonts such as `宋体`, `黑体`, `微软雅黑`, or other broadly embeddable document fonts already approved by the user or existing file style.
- If a document contains residual `Courier` or `Courier New` references from templates or software defaults, remove or replace them before delivery.
