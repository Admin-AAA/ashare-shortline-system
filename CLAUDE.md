# Language Preference

优先使用中文回复。

# Project Development Policy

当前项目按最合适的架构来做，不保留向后兼容；不为了旧接口、旧文件结构或临时实现额外迁就。

- 缺什么数据就补什么数据；本地数据池按增量方式维护，可以为完成系统目标主动补充、更新、校验本地数据。
- 代码结构不合理时可以直接重构，不需要为了保留旧实现而绕路。
- 允许主动修改代码、调整模块边界、重排文件结构、改造数据流和内部契约，只要这样更符合系统长期架构。
- 在任务执行中尽情发挥，尽可能充分分析、实现、验证；不要因为担心上下文或 token 消耗而刻意压缩必要工作。

This project is still in active development. Prioritize the most suitable architecture over backward compatibility, incremental migration, or interface stability.

- Choose the architecture that best fits the product direction and maintainability of the current system.
- Prefer clean module boundaries, coherent data flow, and simple long-term ownership over preserving temporary or legacy structures.
- Refactor boldly when it meaningfully improves the design, even if that changes existing APIs, file layout, or internal contracts.
- Do not optimize for stable iteration history, migration paths, or compatibility with unfinished earlier designs unless explicitly requested.

# Python Testing Policy

When testing this Python project, prefer the project's own virtual environment first.

- Use the virtual environment already configured inside the project whenever available.
- Only fall back to a global Python or another environment if the project does not provide a usable virtual environment.

# Document Font Policy

Do not use `Courier` or `Courier New` in any generated or edited document, including but not limited to `.docx`, `.pdf`, templates, styles, hidden styles, or exported assets.

- Treat `Courier` and `Courier New` as disallowed fonts.
- When generating Word documents, ensure both visible content and internal styles/font tables do not reference `Courier` or `Courier New`.
- Prefer common non-restricted fonts such as `宋体`, `黑体`, `微软雅黑`, or other broadly embeddable document fonts already approved by the user or existing file style.
- If a document contains residual `Courier` or `Courier New` references from templates or software defaults, remove or replace them before delivery.
