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

# Deployment Sync Policy

后续对当前系统做代码或配置改动时，如果需要推送到 GitHub，也必须同步上传并部署到云端 Ubuntu 服务器。

- 服务器：`139.196.242.53`
- SSH 用户：`root`
- 本机免密 SSH 别名：`ashare-server`
- 云端项目目录：`/opt/ashare-shortline-system`
- 对外访问地址：`http://139.196.242.53:18000`
- 后端服务：`ashare-backend`
- Web 入口：`nginx`
- 部署时同步当前工作区产物到服务器，重启后端服务与 Web 入口，并验证公网地址可访问。
- 不要把服务器密码、API Key、数据库密码等敏感信息写入 GitHub 跟踪文件；需要连接服务器时，从用户提供的信息或安全环境中获取凭据。

# Document Font Policy

Do not use `Courier` or `Courier New` in any generated or edited document, including but not limited to `.docx`, `.pdf`, templates, styles, hidden styles, or exported assets.

- Treat `Courier` and `Courier New` as disallowed fonts.
- When generating Word documents, ensure both visible content and internal styles/font tables do not reference `Courier` or `Courier New`.
- Prefer common non-restricted fonts such as `宋体`, `黑体`, `微软雅黑`, or other broadly embeddable document fonts already approved by the user or existing file style.
- If a document contains residual `Courier` or `Courier New` references from templates or software defaults, remove or replace them before delivery.
